"""The email relay driver (change 9, docs/DEPLOYMENT.md section 5a).

A workspace never sends email itself. It asks the hosting control plane's relay to, with
one ``POST`` per message naming one of two templates and typed fields, so a workspace
cannot send free text. This module is the whole of that client:

- :func:`build_relay_request` is pure. It turns one :class:`RelayMessage` and the
  settings into the exact method, URL, headers and body bytes. The golden requests under
  ``docs/relay/`` are its output for a fixed message, checked byte for byte by
  ``tests/test_relay_definition.py``, and are what the control plane's own tests send.
- :class:`RelaySender` posts that request through an injected ``httpx2.Client`` and maps
  the answer to exactly one :data:`Outcome`. It never retries: a person can ask for
  another code, and an administrator sees an invite's outcome on screen.

No log line, error or exception message here carries a code, the relay token or a
request body. A refusal is read for its field names only.

There is deliberately no other sender: no SMTP driver and no mail library (Q72).
"""

from __future__ import annotations

import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

import httpx2

from glosswork.config import Settings
from glosswork.logging import get_logger
from glosswork.timeutil import format_datetime

#: The whole exchange with the relay, connect to last byte, in seconds. One deadline, not
#: ``httpx2``'s per-phase timeout, which would allow several times this in total.
RELAY_TIMEOUT_SECONDS = 5

#: The most of a relay's answer the workspace reads. A refusal body is a few dozen bytes.
MAX_ANSWER_BYTES = 16_384

#: The ``to`` rule's length bound, the longest address SMTP carries.
MAX_ADDRESS_LENGTH = 254

Template = Literal["sign_in_code", "invite"]
Outcome = Literal["accepted", "refused_credential", "refused_fields", "rate_limited", "unavailable"]

_FORBIDDEN_IN_ADDRESS = re.compile(r'[\s,;<>"]')


def normalize_address(email: str) -> str:
    """An address as the workspace stores it: trimmed and lowercased."""
    return email.strip().lower()


def is_deliverable_address(address: str) -> bool:
    """The ``to`` rule (section 5a): at most 254 characters, exactly one ``@`` with text on
    both sides, no whitespace, and none of ``,;<>"``. Applied to a normalized address.

    Deliberately a floor, not RFC 5322: its job is that ``to`` can only ever name one
    recipient, so a stored address can never become a list at the relay."""
    if not address or len(address) > MAX_ADDRESS_LENGTH:
        return False
    if _FORBIDDEN_IN_ADDRESS.search(address) or address.count("@") != 1:
        return False
    local, _, domain = address.partition("@")
    return bool(local) and bool(domain)


@dataclass(frozen=True, slots=True)
class RelayMessage:
    """One message for the relay. Build it with :meth:`sign_in_code` or :meth:`invite`,
    which are the only two shapes the definition allows."""

    message_id: str
    template: Template
    to: str
    fields: dict[str, str]

    @classmethod
    def sign_in_code(
        cls, to: str, code: str, expires_at: datetime, message_id: str | None = None
    ) -> RelayMessage:
        return cls(
            message_id=message_id or str(uuid.uuid4()),
            template="sign_in_code",
            to=to,
            fields={"code": code, "code_expires_at": format_datetime(expires_at)},
        )

    @classmethod
    def invite(cls, to: str, inviter_name: str, message_id: str | None = None) -> RelayMessage:
        return cls(
            message_id=message_id or str(uuid.uuid4()),
            template="invite",
            to=to,
            fields={"inviter_name": inviter_name},
        )


@dataclass(frozen=True, slots=True)
class RelayRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True, slots=True)
class RelayResult:
    """What one send came to. ``refused_fields`` is non-empty only for a ``422`` whose body
    had exactly the defined ``refused_fields`` shape."""

    outcome: Outcome
    refused_fields: list[str] = field(default_factory=list)


def build_relay_request(message: RelayMessage, settings: Settings) -> RelayRequest:
    """The exact request for ``message`` (docs/DEPLOYMENT.md section 5a). Pure: the same
    message and settings always give the same bytes, which is what makes the golden
    requests checkable."""
    if settings.relay_url is None or settings.relay_token is None:
        raise ValueError("the relay is not configured")
    body = {
        "message_id": message.message_id,
        "template": message.template,
        "to": message.to,
        "fields": dict(message.fields),
    }
    return RelayRequest(
        method="POST",
        url=settings.relay_url,
        headers={
            "Authorization": f"Bearer {settings.relay_token}",
            "Content-Type": "application/json",
        },
        body=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )


def render_http(request: RelayRequest) -> bytes:
    """A request as the golden files hold it: the request line, the headers in order, a
    blank line, and the body bytes exactly."""
    lines = [f"{request.method} {request.url}"]
    lines.extend(f"{name}: {value}" for name, value in request.headers.items())
    return ("\n".join(lines) + "\n\n").encode("utf-8") + request.body + b"\n"


def _refused_field_names(raw: bytes) -> list[str]:
    """Field names from exactly ``{"error": {"code": "refused_fields", "fields": [...]}}``,
    or nothing: any other body is treated as unparsed (F7)."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(error, dict) or error.get("code") != "refused_fields":
        return []
    names = error.get("fields")
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        return []
    return [n for n in names if n][:20]


def outcome_for(status: int) -> Outcome:
    if 200 <= status < 300:
        return "accepted"
    if status in (401, 403):
        return "refused_credential"
    if status == 422:
        return "refused_fields"
    if status == 429:
        return "rate_limited"
    return "unavailable"


class RelaySender:
    """Posts messages to the relay and maps each answer to one outcome."""

    def __init__(
        self,
        settings: Settings,
        client: httpx2.Client | None = None,
        timeout_seconds: float = RELAY_TIMEOUT_SECONDS,
    ) -> None:
        self._settings = settings
        self._timeout = timeout_seconds
        self._client = client or httpx2.Client(
            timeout=httpx2.Timeout(timeout_seconds), follow_redirects=False
        )
        self._logger = get_logger(__name__)

    def send(self, message: RelayMessage) -> RelayResult:
        """Send one message. Never raises and never retries.

        The exchange runs on a worker thread so the caller waits at most the deadline in
        total, whatever phase the relay stalls in; a stalled worker ends on the client's
        own timeout."""
        request = build_relay_request(message, self._settings)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="relay-send")
        status: int | None = None
        try:
            status, raw = executor.submit(self._exchange, request).result(timeout=self._timeout)
        except FutureTimeoutError:
            result = RelayResult("unavailable")
            failure: str | None = "timeout"
        except Exception as exc:  # a connection error, or anything else: never raised
            result = RelayResult("unavailable")
            failure = type(exc).__name__
        else:
            failure = None
            outcome = outcome_for(status)
            fields = _refused_field_names(raw) if outcome == "refused_fields" else []
            result = RelayResult(outcome, fields)
        finally:
            executor.shutdown(wait=False)
        self._log(message, result, status, failure)
        return result

    def _exchange(self, request: RelayRequest) -> tuple[int, bytes]:
        with self._client.stream(
            request.method,
            request.url,
            headers=request.headers,
            content=request.body,
            timeout=httpx2.Timeout(self._timeout),
        ) as response:
            raw = b""
            for chunk in response.iter_bytes():
                raw += chunk
                if len(raw) >= MAX_ANSWER_BYTES:
                    break
            return response.status_code, raw[:MAX_ANSWER_BYTES]

    def _log(
        self, message: RelayMessage, result: RelayResult, status: int | None, failure: str | None
    ) -> None:
        """One line per send, naming the message id (a trace id, not secret), the template,
        the outcome, and for a refusal the field names. Never the address's code, the
        token, or a body."""
        values: dict[str, Any] = {
            "message_id": message.message_id,
            "template": message.template,
            "outcome": result.outcome,
            "status": status,
        }
        if result.refused_fields:
            values["refused_fields"] = result.refused_fields
        if failure is not None:
            values["failure"] = failure
        if result.outcome == "accepted":
            self._logger.info("relay_send", **values)
        elif result.outcome == "rate_limited":
            self._logger.warning("relay_send", **values)
        elif result.outcome == "refused_credential":
            self._logger.error(
                "relay_send", hint="the relay refused this workspace's credential", **values
            )
        else:
            self._logger.error("relay_send", **values)
