"""The fake relay: the one enforcer of the relay request's definition in this repository.

A workspace with ``GW_RELAY_URL`` and ``GW_RELAY_TOKEN`` set asks the hosting control
plane's relay to send its sign-in codes and invites. ``docs/DEPLOYMENT.md`` section 5a
defines that request. This module is a small ASGI app that accepts exactly that
definition and refuses anything else, so every test that sends through it proves the
driver produces what the definition says rather than what a second copy of it says.

What it enforces, in order:

- the path it was mounted at, and ``POST``;
- ``Authorization: Bearer <token>`` equal to the token it was given (``401`` otherwise);
- ``Content-Type: application/json``;
- the body, through Pydantic models with ``extra="forbid"`` at every level: exactly
  ``message_id``, ``template``, ``to`` and ``fields``; a lowercase version-4 UUID; one
  address under the ``to`` rule; a known template; and exactly that template's fields,
  a code of six ASCII digits and a ``code_expires_at`` in the ``Z`` form with whole
  seconds, at least three and at most thirty minutes ahead of the relay's clock.

A request outside the definition is ``400`` with the reasons, which the workspace maps to
``unavailable``: a test that sees ``400`` here has found a driver defect, not a scripted
answer. A request inside it is ``202``, or whatever the test scripted next; a scripted
``422`` carries exactly the ``refused_fields`` body the definition gives.

Runnable as a process for Playwright::

    uv run python -m tests.fake_relay --port 8791 --token <token>

with a test-only ``GET /sent`` that returns every accepted message, oldest first.
"""

from __future__ import annotations

import argparse
import json
import re
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

#: Where the fake is mounted. The hosted relay's path, so a test URL reads like the real one.
RELAY_PATH = "/v1/relay/send"

#: The relay's own bounds on a code's expiry (the control plane's template models): never
#: less than three minutes left, never more than thirty minutes ahead.
MIN_CODE_LIFETIME = timedelta(minutes=3)
MAX_CODE_LIFETIME = timedelta(minutes=30)

#: The ``to`` rule, as the definition states it: at most 254 characters, exactly one ``@``
#: with text on both sides, no whitespace, none of ``,;<>"``, trimmed and lowercased.
_TO_FORBIDDEN = re.compile(r'[\s,;<>"]')
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
_SIX_DIGITS = re.compile(r"^[0-9]{6}$")
_EXPIRY = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SignInCodeFields(_Strict):
    """The ``sign_in_code`` template's fields."""

    code: str
    code_expires_at: str

    @field_validator("code")
    @classmethod
    def _six_digits(cls, value: str) -> str:
        if not _SIX_DIGITS.fullmatch(value):
            raise ValueError("code must be exactly six ASCII digits")
        return value

    @field_validator("code_expires_at")
    @classmethod
    def _expiry_window(cls, value: str) -> str:
        if not _EXPIRY.fullmatch(value):
            raise ValueError("code_expires_at must be RFC 3339 UTC with a Z and whole seconds")
        moment = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        now = datetime.now(UTC)
        if moment - now < MIN_CODE_LIFETIME:
            raise ValueError("code_expires_at leaves less than three minutes")
        if moment - now > MAX_CODE_LIFETIME:
            raise ValueError("code_expires_at is more than thirty minutes ahead")
        return value


class InviteFields(_Strict):
    """The ``invite`` template's fields."""

    inviter_name: str

    @field_validator("inviter_name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("inviter_name must be non-empty")
        return value


#: Every template and its fields model. ``tests/test_relay_definition.py`` checks that
#: ``docs/DEPLOYMENT.md`` section 5a names exactly these templates and these fields.
TEMPLATE_FIELDS: dict[str, type[_Strict]] = {
    "sign_in_code": SignInCodeFields,
    "invite": InviteFields,
}


class RelayMessage(_Strict):
    """The request body, top level."""

    message_id: str
    template: Literal["sign_in_code", "invite"]
    to: str
    fields: dict[str, Any]

    @field_validator("message_id")
    @classmethod
    def _uuid4(cls, value: str) -> str:
        if not _UUID4.fullmatch(value):
            raise ValueError("message_id must be a lowercase hyphenated version-4 UUID")
        return value

    @field_validator("to")
    @classmethod
    def _one_address(cls, value: str) -> str:
        if (
            len(value) > 254
            or value != value.strip().lower()
            or _TO_FORBIDDEN.search(value)
            or value.count("@") != 1
        ):
            raise ValueError("to must be one trimmed, lowercased address")
        local, _, domain = value.partition("@")
        if not local or not domain:
            raise ValueError("to must have text on both sides of the @")
        return value


def validate_message(body: Any) -> tuple[RelayMessage, _Strict]:
    """Parse one request body against the definition, or raise ``ValidationError``."""
    message = RelayMessage.model_validate(body)
    fields = TEMPLATE_FIELDS[message.template].model_validate(message.fields)
    return message, fields


@dataclass
class Scripted:
    """One scripted answer. ``refused`` names fields for a ``422``; ``delay`` is seconds
    the relay waits before answering, to prove the workspace does not wait for it."""

    status: int = 202
    refused: list[str] = field(default_factory=list)
    delay: float = 0.0
    body: Any = None


class FakeRelay:
    """The relay's state: its token, the answers a test scripted, and what it accepted."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.sent: list[dict[str, Any]] = []
        self.refused: list[str] = []
        self._script: list[Scripted] = []
        self._default = Scripted()
        self._lock = threading.Lock()
        self.app = Starlette(
            routes=[
                Route(RELAY_PATH, self._send, methods=["POST"]),
                Route("/sent", self._sent, methods=["GET"]),
            ]
        )

    # ---------------------------------------------------------------- scripting

    def script(self, *answers: Scripted) -> None:
        """Answer the next requests with these, in order, then the default again."""
        with self._lock:
            self._script.extend(answers)

    def answer_always(self, answer: Scripted) -> None:
        """Answer every request with this until changed."""
        with self._lock:
            self._default = answer

    def messages(self, template: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [m for m in self.sent if template is None or m["template"] == template]

    def wait_for(self, count: int, timeout: float = 5.0) -> list[dict[str, Any]]:
        """Wait until at least ``count`` messages were accepted, for a server under uvicorn
        where the workspace sends after it has answered."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.messages()) >= count:
                break
            time.sleep(0.02)
        return self.messages()

    # ------------------------------------------------------------------ routes

    async def _send(self, request: Request) -> Response:
        if request.headers.get("authorization") != f"Bearer {self.token}":
            self.refused.append("credential")
            return JSONResponse({"error": {"code": "unauthorized"}}, status_code=401)
        if request.headers.get("content-type") != "application/json":
            self.refused.append("content-type")
            return JSONResponse({"error": {"code": "bad_content_type"}}, status_code=400)
        raw = await request.body()
        try:
            body = json.loads(raw)
            validate_message(body)
        except (ValueError, ValidationError, KeyError) as exc:
            self.refused.append(type(exc).__name__)
            return JSONResponse(
                {"error": {"code": "outside_definition", "reason": str(exc)[:2000]}},
                status_code=400,
            )
        with self._lock:
            answer = self._script.pop(0) if self._script else self._default
        if answer.delay:
            import anyio

            await anyio.sleep(answer.delay)
        if 200 <= answer.status < 300:
            with self._lock:
                self.sent.append(body)
            return JSONResponse({"accepted": True}, status_code=answer.status)
        if answer.status == 422 and answer.body is None:
            return JSONResponse(
                {"error": {"code": "refused_fields", "fields": answer.refused}},
                status_code=422,
            )
        return JSONResponse(
            answer.body if answer.body is not None else {"error": {"code": "scripted"}},
            status_code=answer.status,
        )

    async def _sent(self, request: Request) -> Response:
        del request
        return JSONResponse({"sent": self.messages()})


def new_token() -> str:
    """A relay token for one test run: long enough for the workspace's 32-character floor,
    and made at run time so no literal credential-shaped string sits in the tree."""
    return "relay-" + uuid.uuid4().hex + uuid.uuid4().hex


@dataclass
class LiveRelay:
    relay: FakeRelay
    url: str


@contextmanager
def serve(relay: FakeRelay, port: int = 0) -> Iterator[LiveRelay]:
    """Serve ``relay`` on a loopback port in a thread, for as long as the block runs.

    A real socket rather than a transport seam, so the workspace under test is configured
    with ``GW_RELAY_URL`` exactly as a deployment is."""
    import socket

    import uvicorn

    if port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
    # ``log_config=None``: uvicorn otherwise reconfigures the process's ``uvicorn`` loggers,
    # which other tests assert the shape of.
    config = uvicorn.Config(relay.app, host="127.0.0.1", port=port, log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:  # pragma: no cover - a stuck server is a test failure
            raise RuntimeError("the fake relay did not start")
        time.sleep(0.01)
    try:
        yield LiveRelay(relay=relay, url=f"http://127.0.0.1:{port}{RELAY_PATH}")
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(prog="python -m tests.fake_relay")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)
    relay = FakeRelay(args.token)
    uvicorn.run(relay.app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
