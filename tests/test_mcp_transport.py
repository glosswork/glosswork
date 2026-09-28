"""Transport and mount (FR-M1, FR-P5): the SDK client over real streamable HTTP, in-process
against the application ``create_app()`` returns, with the host lifespan running."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server import MCPServer
from mcp.types import INVALID_REQUEST

import glosswork.app as app_module
from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.mcp_server import GATED_METHODS
from glosswork.services import ServiceBundle
from tests.conftest import make_actor, mint_scope_tokens
from tests.mcp_support import (
    JSON_RPC_HEADERS,
    http_session,
    memory_session,
    refusal_opening,
    seed_task_type,
    structured,
    tool_names,
)
from tests.test_mcp_catalog import ADMIN_TOOLS, READ_TOOLS, WRITE_TOOLS


@pytest.mark.anyio
async def test_streamable_http_lists_tools_and_writes_under_the_header_label(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    # A real write PAT must exist before the streamable HTTP session opens, but
    # the SDK's session manager can only be run once per app instance: the
    # ``app`` fixture's own lifespan is that one run. So mint the token through a
    # throwaway app built over the same data directory, whose lifespan is entered
    # and exited before the app under test ever starts.
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
    bootstrap_app = create_app(settings)
    async with bootstrap_app.router.lifespan_context(bootstrap_app):
        write_token = mint_scope_tokens(bootstrap_app.state.services)["write"]

    app = create_app(settings)
    async with http_session(app, token=write_token, agent_label="http-writer") as client:
        services: ServiceBundle = app.state.services
        seed_task_type(services)
        assert client.protocol_version == "2026-07-28"
        assert tool_names(await client.list_tools()) == READ_TOOLS | WRITE_TOOLS
        created = structured(
            await client.call_tool(
                "create_record", {"object_type": "task", "values": {"title": "over http"}}
            )
        )
        events = services.records.get_record_history(make_actor(), created["key"])
        assert events
        label_ids = {e.agent_label_id for e in events}
        assert len(label_ids) == 1
        label_id = label_ids.pop()
        assert label_id is not None
        assert services.agent_labels.get_label(label_id).label == "http-writer"
        assert {e.surface for e in events} == {"mcp"}

    # The application's JSON access log line for /mcp reports surface "mcp".
    out, _err = capfd.readouterr()
    access_lines = [
        json.loads(line)
        for line in out.splitlines()
        if line.startswith("{") and '"event": "access"' in line
    ]
    mcp_lines = [line for line in access_lines if line["path"] == "/mcp"]
    assert mcp_lines
    assert {line["surface"] for line in mcp_lines} == {"mcp"}
    assert all(line["status"] == 200 for line in mcp_lines)


@pytest.mark.anyio
async def test_unknown_token_over_http_is_refused(app: FastAPI) -> None:
    """Over streamable HTTP the refusal lands at the session's opening, because the gate
    covers ``server/discover`` and that is the first request a modern
    client sends. This is the transport a real client uses, and on it the deployment's
    own message is what the client is handed: no SDK error stands in front of it, which
    is the difference the in-memory harness cannot show.
    """
    refusal = await refusal_opening(http_session(app, token="gw_pat_nope"))
    assert refusal.code == INVALID_REQUEST
    assert "cannot verify the presented bearer token" in refusal.message


def test_rest_and_health_routes_are_unaffected_by_the_mcp_mount(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ok"}  # the body, not the status
    spec = client.get("/openapi.json").json()
    assert spec["openapi"].startswith("3.1")
    assert "/api/v1/object-types" in spec["paths"]
    assert "/mcp" not in spec["paths"]
    assert client.get("/api/v1/object-types").json() == []
    # The MCP path itself is served by the SDK app, not the REST router.
    assert client.get("/mcp/nothing-here").status_code == 404


def test_mcp_path_answers_with_the_sdk_not_a_redirect(client: TestClient) -> None:
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code != 307
    assert response.status_code in (200, 400, 406)


INITIALIZE_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2026-07-28",
        "capabilities": {},
        "clientInfo": {"name": "probe", "version": "0"},
    },
}


def test_the_handshake_itself_requires_a_credential(client: TestClient) -> None:
    """When ``GATED_METHODS`` covered ``tools/list`` and ``tools/call`` and nothing else,
    ``initialize`` ran with no credential and returned the full
    capability set **and** the onboarding instructions -- the orientation document that
    names every object type concept the deployment has. Measured on that tree: HTTP
    200 carrying ``result.instructions``.

    The refusal shape was probed before it was asserted, because the SDK's middleware
    contract says ``initialize`` is observed but not rewritable and does *not* say what
    the streamable-HTTP layer puts on the wire for a vetoed handshake. What it actually
    returns: **HTTP 200 with a JSON-RPC error envelope at code -32600**, the same shape
    a refused ``tools/list`` already had. So the gate keeps one shape across all three
    gated methods, and this test asserts what was measured rather than what was
    expected.
    """
    response = client.post(
        "/mcp", json=INITIALIZE_BODY, headers={**JSON_RPC_HEADERS, "Authorization": ""}
    )
    assert response.status_code == 200
    body = response.json()
    assert "result" not in body, body
    assert body["error"]["code"] == INVALID_REQUEST, body
    # The disclosure this closes: neither the capability set nor the instructions.
    assert "instructions" not in response.text
    assert "list_object_types" not in response.text


def test_the_handshake_still_answers_for_a_valid_token(client: TestClient) -> None:
    """The other direction of the same gate: a real credential gets the handshake it
    always got, instructions included. ``client`` presents an ``admin`` PAT."""
    response = client.post("/mcp", json=INITIALIZE_BODY, headers=JSON_RPC_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert "error" not in body, body
    assert body["result"]["capabilities"]["tools"] is not None
    assert "list_object_types" in body["result"]["instructions"]


def test_a_notification_is_never_gated(client: TestClient) -> None:
    """Notifications carry no ``request_id``, have no response, and must not be gated:
    a compliant client that sends one before it sends a token would see a protocol
    error it could not act on. They take the non-gated early return in
    ``McpAdapter._dispatch`` by construction rather than by name.

    **Fence**: gating the handshake did not change it; it answered 202 before too.
    """
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**JSON_RPC_HEADERS, "Authorization": ""},
    )
    assert response.status_code == 202
    assert response.text == ""


def test_the_sse_stream_endpoint_is_not_served(tmp_path: Path) -> None:
    """``GET /mcp`` is the SSE stream. It could not be gated -- the adapter
    middleware sees JSON-RPC methods, not HTTP verbs -- and it served nothing anyway,
    since this deployment runs ``stateless_http=True`` with ``json_response=True`` and
    never sends a server-initiated message. So an unauthenticated GET returned 200 and
    held the connection open **indefinitely**, there being no idle timeout in stateless
    mode and no ``limit_concurrency`` set. Measured on the ungated tree by watching
    this probe hang rather than fail, which is the failure.

    The app is built here with the SPA fallback deliberately absent, because that
    catch-all changes the answer without changing the decision: with ``web/dist``
    built it absorbs the GET and refuses it as an MCP path with 404, and without it the
    router answers 405. Both are refusals and neither serves a stream, but only the
    router's own answer is a property of *this* change, and asserting the built-frontend
    number would make the test pass or fail on whether someone had run ``npm run build``.
    """
    monkeypatched = pytest.MonkeyPatch()
    monkeypatched.setattr(app_module, "FRONTEND_DIST_DIR", tmp_path / "no-dist")
    try:
        app = create_app(Settings(data_dir=tmp_path / "data", embedding_enabled=False))
        with TestClient(app) as probe:
            response = probe.get("/mcp", headers={"Accept": "text/event-stream"})
    finally:
        monkeypatched.undo()
    assert response.status_code == 405
    assert set(response.headers["allow"].replace(" ", "").split(",")) == {"POST", "DELETE"}


def test_the_sse_stream_is_refused_however_the_frontend_is_built(client: TestClient) -> None:
    """The same property against the ordinary fixture app, whose SPA fallback exists or
    not depending on whether ``web/dist`` was built. Either way ``GET /mcp`` is refused
    and never opens a stream. This is the assertion that holds in every environment;
    the one above pins the router's own answer."""
    response = client.get("/mcp", headers={"Accept": "text/event-stream"})
    assert response.status_code in (404, 405)
    assert "text/event-stream" not in response.headers.get("content-type", "")


LEAK = "no such table: records_secret_v3 at /srv/gw/data/records.sqlite3"


def _explode(*args: object, **kwargs: object) -> None:
    raise RuntimeError(LEAK)


@pytest.mark.anyio
async def test_an_unclassified_tool_failure_over_http_returns_only_a_request_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second dispatch path. ``McpAdapter.call`` catching only ``GlossworkError`` once
    let everything else fall through to the SDK's catch-all, which puts ``str(e)`` in the
    tool result. Measured on that tree over the in-memory transport, the caller got back
    verbatim:

        Error executing tool list_object_types: no such table: records_secret_v3 at
        /srv/gw/data/records.sqlite3

    The filesystem path in that quotation is **not** the one the original message
    carried: the real one named a former product and has been replaced throughout this
    file with a neutral path of the same shape. Nothing is lost, because detection here
    comes from ``records_secret_v3`` and from the whole string being absent from the
    answer, never from the path's letters -- measured by reintroducing the leak, which
    fails the same four tests with either path.

    A real ``sqlite3.OperationalError`` names real tables and the real database path.
    This is the same probe over streamable HTTP, because a fix at the adapter has to
    hold on whichever transport the caller used -- the leak was never transport-specific
    and neither is the classification.
    """
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
    bootstrap_app = create_app(settings)
    async with bootstrap_app.router.lifespan_context(bootstrap_app):
        read_token = mint_scope_tokens(bootstrap_app.state.services)["read"]

    monkeypatch.setattr(
        "glosswork.services.schema.SchemaService.list_object_types", _explode, raising=True
    )
    app = create_app(settings)
    async with http_session(app, token=read_token) as mcp_client:
        result = await mcp_client.call_tool("list_object_types", {})

    assert result.is_error
    assert LEAK not in str(result.content)
    assert "records_secret_v3" not in str(result.structured_content)
    error = (result.structured_content or {})["error"]
    assert error["code"] == "internal_error"
    assert error["details"]["request_id"]


@pytest.mark.anyio
async def test_an_unclassified_failure_in_the_adapter_middleware_is_classified_too(
    mcp_server: MCPServer, pat: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam ``McpAdapter.call`` cannot see.

    ``call`` wraps a tool body, and every ``adapter.actor(ctx)`` is inside one. But
    ``McpAdapter.middleware`` resolves the caller's identity *before* any tool is
    dispatched, and the SDK's catch-all wraps argument coercion before a tool function
    is entered -- so a lock timeout or a ``sqlite3.OperationalError`` raised while
    resolving the credential escaped ``call`` entirely and reached ``str(e)`` just the
    same. Hence two ``except Exception`` blocks rather than one.

    Note this is the *resolver* raising, not ``agent_labels.register_use``: that runs
    inside ``McpAdapter.actor``, which every tool calls from within its ``run()``
    closure, so it is already covered by ``call``. The identity resolution in the
    middleware is the part that genuinely is not.

    ``TokenRefusedError`` is unaffected -- it is classified, and its ``MCPError`` is
    re-raised untouched, which the adjacent refusal tests pin.

    **The patch is applied inside the session, and nothing else differs.** The gate
    runs on ``server/discover`` as well, so a resolver that explodes from the first
    request onwards takes the session's opening down and this test would never reach the
    ``tools/call`` it is about. Patching after the session is open puts the explosion
    exactly where the seam under test lives: in the middleware's identity resolution on
    a dispatched call. The assertions are the ones this test has always made.
    """
    async with memory_session(mcp_server, token=pat["read"]) as mcp_client:
        monkeypatch.setattr("glosswork.auth.PatTokenResolver.resolve", _explode, raising=True)
        result = await mcp_client.call_tool("list_object_types", {})
    assert result.is_error
    assert LEAK not in str(result.content)
    error = (result.structured_content or {})["error"]
    assert error["code"] == "internal_error"
    assert error["details"]["request_id"]


@pytest.mark.anyio
async def test_the_traceback_reaches_the_application_log_and_not_the_caller(
    mcp_server: MCPServer,
    pat: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The other half of "never echo": the detail has to go *somewhere*, or an operator
    cannot debug what a caller can no longer report. It goes to the application log at
    ``error`` with the traceback and the same request id the caller was handed, which
    is what makes the id in the opaque message actionable rather than decorative
    (never at ``info``, and never in the caller's answer).

    Captured with ``capfd`` rather than ``caplog``: this deployment's structlog
    configuration renders JSON to stdout, so nothing reaches the stdlib handler
    ``caplog`` installs, and a ``caplog``-based assertion would pass vacuously on an
    empty record list. The access-log test at the top of this file captures the same
    way for the same reason.
    """
    monkeypatch.setattr(
        "glosswork.services.schema.SchemaService.list_object_types", _explode, raising=True
    )
    async with memory_session(mcp_server, token=pat["read"]) as mcp_client:
        result = await mcp_client.call_tool("list_object_types", {})

    request_id = ((result.structured_content or {})["error"])["details"]["request_id"]
    out, _err = capfd.readouterr()
    logged = [
        json.loads(line)
        for line in out.splitlines()
        if line.startswith("{") and '"mcp_unclassified_exception"' in line
    ]
    assert len(logged) == 1, out
    entry = logged[0]
    assert entry["level"] == "error"
    assert entry["request_id"] == request_id
    # The traceback an operator needs, which the caller is never shown.
    assert LEAK in entry["exception"]
    assert "Traceback (most recent call last)" in entry["exception"]
    assert LEAK not in str(result.content)
    assert LEAK not in str(result.structured_content)


# ------------------------------------------------ every registered method is gated


#: The modern per-request envelope. ``server/discover`` exists only on this wire, and the
#: refusal's **HTTP** status differs between the two wires while its JSON-RPC code does
#: not. Built here rather than imported so the test states the shape it probes.
PROTOCOL_VERSION = "2026-07-28"
_META_VERSION = "io.modelcontextprotocol/protocolVersion"
_META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"


def modern_body(method: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {"_meta": {_META_VERSION: PROTOCOL_VERSION, _META_CAPABILITIES: {}}},
    }


def modern_headers(method: str) -> dict[str, str]:
    return {
        **JSON_RPC_HEADERS,
        "Authorization": "",
        "mcp-protocol-version": PROTOCOL_VERSION,
        "mcp-method": method,
    }


def _registered_request_methods(server: MCPServer) -> set[str]:
    """Every request method this server has a handler for, plus ``initialize``.

    ``initialize`` is added by hand because it cannot be registered:
    ``Server.add_request_handler`` raises ``ValueError`` for it, the SDK's own runner owning
    the handshake. It is gated all the same, so leaving it out would make the set below
    disagree with ``GATED_METHODS`` for a reason that has nothing to do with the property
    under test.

    ``_request_handlers`` is private SDK surface. The precedent is
    ``tests/mcp_support.py``'s ``HeaderStampingTransport``, which reaches
    ``_lowlevel_server`` the same way; the exposure is that an SDK upgrade can rename
    the attribute and turn this test into an error rather than a finding. It is named
    here so that failure is legible when it happens.
    """
    lowlevel = server._lowlevel_server  # noqa: SLF001 - SDK has no public accessor
    handlers = lowlevel._request_handlers  # noqa: SLF001 - SDK has no public accessor
    return {str(method) for method in handlers} | {"initialize"}


def test_every_registered_request_method_is_gated(mcp_server: MCPServer) -> None:
    """The gate is read off the server, not restated.

    This is the third generation of one defect. The first gate named three methods and
    missed the three resource methods; the second added those; the third found
    ``server/discover``,
    ``prompts/list``, ``prompts/get``, ``subscriptions/listen`` and ``ping`` still open.
    Each generation extended a denylist by naming a member, and each generation's test
    pinned the denylist rather than the server, so no test could find the next miss.

    So this one enumerates the server's own registered handlers. A request method that
    joins the server without joining the gate turns it red with no edit to any list.
    It replaces an older test that pinned the gated set by equality against a literal of
    six method names. That test asserted the contradictory outcome and was retired;
    keeping it beside this one would assert two answers at once.

    **No exemption set.** An exemption set is a second denylist, and a denylist by
    enumeration is the defect this ends. ``ping`` costs nothing to gate: it is
    registered but does not route on the streamable HTTP wire, answering 404 ``-32601``
    either way.

    Notifications are not here and must not be: they carry no ``request_id``, have no
    response, and take the non-gated early return by construction. The registry holds
    zero notification handlers today.
    """
    registered = _registered_request_methods(mcp_server)
    ungated = registered - GATED_METHODS
    assert not ungated, (
        f"registered request methods that no credential check covers: {sorted(ungated)}"
    )


@pytest.mark.parametrize("method", sorted(GATED_METHODS - {"initialize"}))
def test_an_uncredentialed_gated_method_discloses_nothing(client: TestClient, method: str) -> None:
    """Parametrized over ``GATED_METHODS`` itself, so a method added to the
    gate is probed without anybody editing a list here.

    The disclosure this closes, measured on the tree before the wider gate: a raw
    ``server/discover`` POST carrying no ``Authorization`` header at all answered HTTP 200
    with the full ``capabilities`` and the complete ``instructions`` string, which is the
    same orientation document ``initialize`` is gated for disclosing. ``prompts/list``
    answered with a ``prompts`` result and the server's own name, which is why the
    server-name assertion is here and not only the two obvious ones.

    ``initialize`` is excluded only because it has its own long-standing test above,
    ``test_the_handshake_itself_requires_a_credential``, which asserts the same three
    things plus the handshake's own shape.
    """
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": {}},
        headers={**JSON_RPC_HEADERS, "Authorization": ""},
    )
    body = response.json()
    assert "result" not in body, body
    assert body["error"]["code"] == INVALID_REQUEST, body
    assert "capabilities" not in response.text
    assert "instructions" not in response.text
    assert "glosswork" not in response.text.lower()


def test_the_refusal_carries_the_documented_shape_on_both_wires(client: TestClient) -> None:
    """``docs/MCP_TOOLS.md`` section 1's refusal shape, asserted as measured.

    The JSON-RPC code is ``-32600`` on both wires. The **HTTP** status is not the same on
    both: the legacy wire answers 200 and the modern per-request envelope answers 400.
    That split is a property of the wire and not of the method, and it held before the
    gate was widened too, which is why an earlier documentation sentence was wrong.
    """
    legacy = client.post(
        "/mcp", json=INITIALIZE_BODY, headers={**JSON_RPC_HEADERS, "Authorization": ""}
    )
    modern = client.post(
        "/mcp", json=modern_body("tools/list"), headers=modern_headers("tools/list")
    )
    assert legacy.status_code == 200, legacy.text
    assert legacy.json()["error"]["code"] == INVALID_REQUEST, legacy.text
    assert modern.status_code == 400, modern.text
    assert modern.json()["error"]["code"] == INVALID_REQUEST, modern.text


@pytest.mark.anyio
async def test_a_valid_credential_is_unaffected_by_the_wider_gate(tmp_path: Path) -> None:
    """Gating the set costs a legitimate client nothing. Same connection, same protocol
    version, same tool count.

    Driven through a real ``mcp.Client`` over streamable HTTP rather than through raw
    JSON-RPC, because what the constraint is about is a session a client can actually
    hold, not a single request's status code.
    """
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
    bootstrap_app = create_app(settings)
    async with bootstrap_app.router.lifespan_context(bootstrap_app):
        admin_token = mint_scope_tokens(bootstrap_app.state.services)["admin"]

    app = create_app(settings)
    async with http_session(app, token=admin_token) as session:
        assert session.protocol_version == PROTOCOL_VERSION
        assert tool_names(await session.list_tools()) == READ_TOOLS | WRITE_TOOLS | ADMIN_TOOLS
