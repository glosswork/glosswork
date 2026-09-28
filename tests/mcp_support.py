"""Test-side MCP plumbing for the MCP suite.

Two ways to drive a real ``mcp.Client`` session against the server:

- ``HeaderStampingTransport``: the SDK's in-memory stream transport, plus a wrapper
  on the client's write stream that stamps every inbound message with a request
  context carrying HTTP-style headers. That is exactly what the streamable HTTP
  transport does with the Starlette request, so ``Authorization`` and
  ``X-Agent-Label`` reach the scope middleware and ``ctx.headers`` the same way
  they do in production (DD-5).
- ``http_session``: the real streamable HTTP path, in-process through
  ``httpx2.ASGITransport`` against the FastAPI app with the host lifespan running.

No test imports a tool implementation; everything goes through ``Client``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from types import TracebackType
from typing import Any

import anyio
import httpx2
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import ServerMessageMetadata, SessionMessage
from mcp.types import INVALID_REQUEST

SERVER_SHUTDOWN_GRACE = 2.0


class StampedRequest:
    """Stands in for the transport's request object: the only attribute the server
    reads off it is ``headers``."""

    def __init__(self, headers: Mapping[str, str]) -> None:
        self.headers: dict[str, str] = {k.lower(): v for k, v in headers.items()}


class _StampingWriteStream:
    def __init__(self, inner: Any, headers: Mapping[str, str]) -> None:
        self._inner = inner
        self._headers = headers

    async def send(self, item: SessionMessage) -> None:
        item.metadata = ServerMessageMetadata(request_context=StampedRequest(self._headers))
        await self._inner.send(item)

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> _StampingWriteStream:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()


class HeaderStampingTransport:
    """In-memory client transport whose every message carries ``headers``."""

    def __init__(self, server: MCPServer, headers: Mapping[str, str] | None = None) -> None:
        self._server = server
        self._headers = dict(headers or {})
        self._cm: Any = None

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[tuple[Any, Any]]:
        lowlevel = self._server._lowlevel_server  # noqa: SLF001 - SDK has no public accessor
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            client_read, client_write = client_streams
            server_read, server_write = server_streams
            server_done = anyio.Event()

            async def _run_server() -> None:
                try:
                    await lowlevel.run(
                        server_read, server_write, lowlevel.create_initialization_options()
                    )
                finally:
                    server_done.set()

            async with anyio.create_task_group() as tg:
                tg.start_soon(_run_server)
                try:
                    yield client_read, _StampingWriteStream(client_write, self._headers)
                finally:
                    await client_write.aclose()
                    await server_write.aclose()
                    with anyio.move_on_after(SERVER_SHUTDOWN_GRACE):
                        await server_done.wait()
                    if not server_done.is_set():
                        tg.cancel_scope.cancel()

    async def __aenter__(self) -> tuple[Any, Any]:
        self._cm = self._connect()
        streams: tuple[Any, Any] = await self._cm.__aenter__()
        return streams

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(exc_type, exc_val, exc_tb)
            self._cm = None


def session_headers(token: str | None, agent_label: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if agent_label is not None:
        headers["X-Agent-Label"] = agent_label
    return headers


@asynccontextmanager
async def memory_session(
    server: MCPServer, token: str | None = None, agent_label: str | None = None
) -> AsyncIterator[Client]:
    """A real client session over the stamped in-memory transport."""
    transport = HeaderStampingTransport(server, session_headers(token, agent_label))
    async with Client(transport) as client:
        yield client


@asynccontextmanager
async def http_session(
    app: FastAPI, token: str | None = None, agent_label: str | None = None
) -> AsyncIterator[Client]:
    """A real client session over streamable HTTP, in-process against ``app`` with
    the host lifespan entered (so migrations run and the MCP session manager is up)."""
    async with app.router.lifespan_context(app):
        http_client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://testserver",
            headers=session_headers(token, agent_label),
        )
        async with http_client:
            transport = streamable_http_client("http://testserver/mcp", http_client=http_client)
            async with Client(transport) as client:
                yield client


TASK_TYPE_KEY = "task"

# A small object type covering what the orientation fixture needs
# to provide: a required short_text, a single_select with described options, a
# date, a self-referential relation, plus an un-indexed long_text so the compact
# default projection (FR-M8) has something to omit.
TASK_FIELDS: list[dict[str, Any]] = [
    {
        "key": "title",
        "name": "Title",
        "type": "short_text",
        "description": "Short imperative summary of the work; the record's display label.",
        "required": True,
    },
    {
        "key": "status",
        "name": "Status",
        "type": "single_select",
        "description": "Delivery state. Use 'doing' only for work actively in progress.",
        "config": {
            "options": [
                {"value": "todo", "label": "To do", "description": "Not started."},
                {"value": "doing", "label": "Doing", "description": "Actively in progress."},
                {"value": "done", "label": "Done", "description": "Finished and accepted."},
            ]
        },
    },
    {
        "key": "due",
        "name": "Due date",
        "type": "date",
        "description": "Date the task must be finished by.",
    },
    {
        "key": "parent",
        "name": "Parent task",
        "type": "relation",
        "description": "The larger task this one rolls up into; self-referential.",
        "config": {
            "target_type_key": TASK_TYPE_KEY,
            "cardinality": "one",
            "inverse_field_key": "children",
        },
    },
    {
        "key": "notes",
        "name": "Notes",
        "type": "long_text",
        "description": "Free-form working notes; not indexed, so omitted from compact results.",
    },
]


# The self-referential relation auto-creates its inverse field ``children``
# (FR-L2/FR-L3), so the seeded type carries one more field than TASK_FIELDS lists.
TASK_FIELD_KEYS = {f["key"] for f in TASK_FIELDS} | {"children"}


def seed_task_type(services: Any) -> str:
    """Create the ``task`` type through the service layer and return its key."""
    from tests.conftest import make_actor

    services.schema.create_object_type(
        make_actor(),
        key=TASK_TYPE_KEY,
        name="Task",
        name_plural="Tasks",
        description="A unit of work someone has committed to finish, tracked to completion.",
        key_prefix="TSK",
        fields=TASK_FIELDS,
    )
    return TASK_TYPE_KEY


def tool_names(listing: Any) -> set[str]:
    return {tool.name for tool in listing.tools}


def structured(result: Any) -> dict[str, Any]:
    assert result.structured_content is not None, result
    content: dict[str, Any] = result.structured_content
    return content


def error_of(result: Any) -> dict[str, Any]:
    """The shared error envelope carried by an ``is_error`` tool result."""
    assert result.is_error, result
    envelope: dict[str, Any] = structured(result)["error"]
    return envelope


# --------------------------------------------- a refusal at the session's opening


def reachable_exceptions(exc: BaseException) -> Iterator[BaseException]:
    """Every exception reachable from ``exc``: itself, the members of any exception
    group below it, and each ``__cause__`` and ``__context__`` behind those."""
    seen: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.extend(getattr(current, "exceptions", ()) or ())
        stack.extend(linked for linked in (current.__cause__, current.__context__) if linked)


def mcp_error_in(exc: BaseException, code: int) -> MCPError | None:
    """The first ``MCPError`` carrying ``code`` anywhere in ``exc``'s chain, or ``None``."""
    for found in reachable_exceptions(exc):
        if isinstance(found, MCPError) and found.code == code:
            return found
    return None


async def refusal_opening(
    opening: AbstractAsyncContextManager[Client], code: int = INVALID_REQUEST
) -> MCPError:
    """Open a session this deployment must refuse, and return the refusal it sent.

    The server gates every request method it registers, including ``server/discover``.
    That puts the credential check at the session's opening rather than at the first
    call after the handshake: a client presenting no credential, or one this
    deployment cannot verify, never gets a session at all. A refusal at the opening is
    the stronger outcome, and it is the one a real client meets, so tests assert it here
    rather than opening a session and expecting ``list_tools()`` to raise.

    The refusal has to be looked for rather than read off the exception the caller
    catches, and that is the SDK's doing on one transport:

    - Over streamable HTTP the deployment's own ``-32600`` is the leaf.
    - Over the in-memory transport it is not. ``negotiate_auto`` treats any ``MCPError``
      from ``send_discover`` other than a disjoint-modern ``-32022`` as a reason to fall
      back to ``session.initialize()``; the SDK's own protocol layer then rejects a
      legacy handshake on a modern connection before the deployment is asked, and the
      real refusal survives only as ``__context__``. Both arrive inside an anyio task
      group's ``ExceptionGroup``.

    So this walks groups and chains alike and returns the deployment's own error, which
    keeps every assertion about **what the caller is told** rather than about which
    layer told it. The divergence itself is a defect in the test transport; nothing
    about a real client changes.

    Raises ``AssertionError`` if the session opens, which is what makes a test built on
    this fail the moment the gate is taken away.
    """
    try:
        async with opening:
            pass
    except BaseException as exc:  # noqa: BLE001 - re-raised below unless it is the refusal
        refusal = mcp_error_in(exc, code)
        if refusal is None:
            raise AssertionError(
                f"the session failed, but with no MCP error carrying code {code}"
            ) from exc
        return refusal
    raise AssertionError(
        "the session opened: this deployment did not refuse the credential it was given"
    )


JSON_RPC_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def raw_jsonrpc(
    client: Any, method: str, params: dict[str, Any] | None = None, token: str | None = None
) -> dict[str, Any]:
    """One JSON-RPC request posted straight at ``/mcp``, below any client session.

    The server refuses an uncredentialed session at its opening, so a real
    ``mcp.Client`` cannot be used to ask what one particular method answers
    without a credential: the client never gets far enough to send it. This does,
    because the wire does not care. It is how a per-method refusal stays provable
    per method rather than collapsing into one assertion about a session that will
    not open.

    ``token`` reads exactly the way it does in :func:`session_headers`, so a test can
    send the same credential over both paths and compare what comes back: ``None``
    sends no ``Authorization`` header at all, and any string, the empty one included,
    sends ``Bearer <token>``. The distinction is load-bearing, because the deployment
    answers an absent header and an unverifiable one with different messages.
    """
    default = client.headers.get("Authorization")
    if token is None and default:
        raise AssertionError(
            "this client presents a credential by default, so an absent-header probe would "
            "be sent as that credential and pass while proving nothing: use ``anon_client``"
        )
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    headers = {**JSON_RPC_HEADERS, **session_headers(token, None)}
    payload: dict[str, Any] = client.post("/mcp", json=body, headers=headers).json()
    return payload
