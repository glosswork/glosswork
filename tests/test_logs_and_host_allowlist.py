"""Structured third-party logs (FR-P5) and the MCP ``Host`` allowlist (FR-P7)."""

from __future__ import annotations

import contextlib
import io
import json
import logging
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient

from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.logging import THIRD_PARTY_LOGGERS, configure_logging
from tests.mcp_support import memory_session, session_headers

# ------------------------------------------------------------------ FR-P5 logs


def _non_json_lines(captured: str) -> list[str]:
    """Every emitted line that is not a JSON object. FR-P5 promises the whole stream
    is structured, and one plain-text line is enough to break a shipper's parser for
    everything around it."""
    bad = []
    for line in captured.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            bad.append(line)
            continue
        if not isinstance(parsed, dict):
            bad.append(line)
    return bad


@pytest.mark.parametrize("logger_name", THIRD_PARTY_LOGGERS)
def test_each_named_third_party_logger_emits_json(logger_name: str) -> None:
    """Unconfigured, these reach the root handler with a bare ``%(message)s``
    formatter and interleave plain text into stdout."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        configure_logging("info")
        logging.getLogger(logger_name).warning("a third-party line with no structure")
    captured = buffer.getvalue()

    assert captured.strip(), f"{logger_name} produced no output at all"
    assert _non_json_lines(captured) == []
    record = json.loads(captured.strip().splitlines()[-1])
    # The emitting logger is named, so an operator can tell an httpx line from an SDK
    # line without pattern-matching the message.
    assert record["logger"] == logger_name
    assert record["level"] == "warning"
    assert record["event"] == "a third-party line with no structure"
    assert "timestamp" in record


def test_our_own_events_are_still_json_and_are_not_duplicated() -> None:
    """Routing foreign records through the stdlib handler must not double-emit the
    application's own events, which go straight to stdout via ``PrintLoggerFactory``."""
    from glosswork.logging import get_logger

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        configure_logging("info")
        get_logger("glosswork.test").info("our_event", detail="kept")
    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]

    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "our_event"
    assert json.loads(lines[0])["detail"] == "kept"


def test_configuring_logging_twice_does_not_stack_handlers() -> None:
    """A test suite that builds many apps would otherwise emit each foreign line once
    per app ever created."""
    configure_logging("info")
    configure_logging("info")
    configure_logging("info")
    assert len(logging.getLogger().handlers) == 1


@pytest.mark.anyio
async def test_an_mcp_session_emits_no_plain_text(mcp_server: Any, pat: dict[str, str]) -> None:
    """Exercised over a real ``mcp.Client`` session rather than by logging a synthetic
    line: the SDK's own chatter is what structured logging has to cover."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        configure_logging("debug")
        async with memory_session(mcp_server, token=pat["read"]) as client:
            await client.list_tools()
            await client.call_tool("list_object_types", {})
    assert _non_json_lines(buffer.getvalue()) == []


def test_an_oidc_style_http_fetch_emits_no_plain_text() -> None:
    """``httpx``'s "HTTP Request: ..." line is the other named producer. Driven over
    ``MockTransport`` so this makes no network call, exactly as the OIDC suite
    does."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"id_token": "stub"})

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        configure_logging("debug")
        with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
            client.post("https://idp.example.com/oauth2/v1/token", data={"code": "abc"})
    assert _non_json_lines(buffer.getvalue()) == []


# ------------------------------------------------------------ the Host allowlist


def _mcp_probe(client: TestClient, host: str) -> Any:
    """A minimal MCP POST. Only the transport-security verdict matters here, so the
    body need only be well-formed enough to get past it."""
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={
            **session_headers(None, None),
            "Host": host,
            "Content-Type": "application/json",
        },
    )


@pytest.fixture
def host_locked_app(tmp_path: Any) -> Any:
    settings = Settings(
        data_dir=tmp_path / "data",
        embedding_enabled=False,
        base_url="https://dt.example.com",
        mcp_allowed_hosts="dt.internal:8000",
    )
    return create_app(settings)


def test_a_foreign_host_is_rejected(host_locked_app: Any) -> None:
    with TestClient(host_locked_app) as client:
        response = _mcp_probe(client, "evil.example.net")
    assert response.status_code == 421


def test_the_host_from_base_url_is_accepted(host_locked_app: Any) -> None:
    """Accepted by the transport check, which is all this asserts: the request then
    fails authentication, having carried no bearer token, and that refusal is proof it
    got past the Host gate rather than being turned away by it.

    It is not a 401, though this docstring once said so: an MCP auth refusal is a
    JSON-RPC error at **HTTP 200** (code -32600), because the transport's error shape is
    the SDK's contract rather than HTTP's. Surfacing it as a 401 was considered in a
    security review and deliberately left alone. The ``!= 421`` assertion is the right
    one.
    """
    with TestClient(host_locked_app) as client:
        response = _mcp_probe(client, "dt.example.com")
    assert response.status_code != 421


def test_a_configured_extra_host_is_accepted(host_locked_app: Any) -> None:
    with TestClient(host_locked_app) as client:
        response = _mcp_probe(client, "dt.internal:8000")
    assert response.status_code != 421


def test_an_unconfigured_deployment_leaves_the_check_off(tmp_path: Any) -> None:
    """``Settings.mcp_allowed_host_values`` returning nothing must mean "no Host check",
    not "reject everything": the SDK's validator is an exact-match allowlist, so
    enabling it empty would take the agent surface down on every deployment that never
    set ``GW_BASE_URL`` — which is optional outside OIDC mode."""
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
    assert settings.mcp_allowed_host_values() == []
    with TestClient(create_app(settings)) as client:
        response = _mcp_probe(client, "anything.example.net")
    assert response.status_code != 421


# -------------------------------------------------- the Origin allowlist follows Host


def _origin_probe(client: TestClient, host: str, origin: str | None) -> Any:
    """The same minimal MCP POST as ``_mcp_probe``, with an ``Origin`` header. A 403
    is the SDK's transport-security refusal ("Invalid Origin header"); anything else
    means the request got past the origin gate, exactly as ``!= 421`` means it got past
    the Host gate."""
    headers = {
        **session_headers(None, None),
        "Host": host,
        "Content-Type": "application/json",
    }
    if origin is not None:
        headers["Origin"] = origin
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=headers,
    )


def test_the_deployments_own_origin_is_accepted(host_locked_app: Any) -> None:
    """Left unpopulated, ``allowed_origins`` defaults to ``[]`` in the SDK, so setting
    ``GW_BASE_URL`` -- the thing that turns the hardening on -- made the transport refuse a
    request carrying the deployment's **own** ``Origin`` with 403 "Invalid Origin header". A
    same-origin browser client was the one caller the hardening was certain to break.
    Watched to fail, with the field unpopulated, with exactly that 403."""
    with TestClient(host_locked_app) as client:
        response = _origin_probe(client, "dt.example.com", "https://dt.example.com")
    assert response.status_code != 403, response.text


def test_a_foreign_origin_is_still_refused(host_locked_app: Any) -> None:
    """Populating the list is not disabling the check. An origin nobody configured is
    refused, which is the property that makes the test above mean something.

    **Fence**, and honestly so: it passes on the unfixed tree too, where the empty list
    refused *every* origin including the deployment's own. It cannot distinguish the
    two trees by itself and is not counted as a measurement; it earns its place by
    pairing with the test above, which can.
    """
    with TestClient(host_locked_app) as client:
        response = _origin_probe(client, "dt.example.com", "https://evil.example.net")
    assert response.status_code == 403


def test_a_request_with_no_origin_is_accepted(host_locked_app: Any) -> None:
    """``_validate_origin`` returns ``True`` on a falsy origin, so populating the list
    cannot break a non-browser client, which sends no ``Origin`` at all.

    **Fence**: true on the unfixed tree too, by the SDK's own behaviour. It is here
    because it is the assumption the whole change rests on -- if it were false, every
    agent would have broken the moment the list stopped being empty.
    """
    with TestClient(host_locked_app) as client:
        response = _origin_probe(client, "dt.example.com", None)
    assert response.status_code != 403


def test_a_plain_http_base_url_yields_a_plain_http_origin(tmp_path: Any) -> None:
    """The scheme is derived from ``GW_BASE_URL``, not assumed to be ``https``.

    This is the rule a single-scheme derivation gets wrong, and getting it wrong is L9
    again under a new name: an internal deployment served over plain HTTP would have
    its own ``Origin`` refused by the very setting meant to protect it.
    """
    settings = Settings(
        data_dir=tmp_path / "data", embedding_enabled=False, base_url="http://dt.internal:8000"
    )
    assert settings.mcp_allowed_origin_values() == ["http://dt.internal:8000"]
    with TestClient(create_app(settings)) as client:
        response = _origin_probe(client, "dt.internal:8000", "http://dt.internal:8000")
    assert response.status_code != 403, response.text


def test_a_configured_host_yields_both_schemes_and_keeps_a_port_wildcard(tmp_path: Any) -> None:
    """A ``GW_MCP_ALLOWED_HOSTS`` entry carries no scheme, so it cannot imply one and
    yields both forms. A ``:*`` port wildcard survives the derivation because
    ``_validate_origin`` supports the same suffix ``_validate_host`` does -- the SDK
    strips the ``:*`` and prefix-matches, so ``https://dt.internal:*`` accepts
    ``https://dt.internal:9999``."""
    settings = Settings(
        data_dir=tmp_path / "data",
        embedding_enabled=False,
        base_url="https://dt.example.com",
        mcp_allowed_hosts="dt.internal:*",
    )
    assert settings.mcp_allowed_origin_values() == [
        "https://dt.example.com",
        "https://dt.internal:*",
        "http://dt.internal:*",
    ]
    with TestClient(create_app(settings)) as client:
        assert (
            _origin_probe(client, "dt.internal:9999", "http://dt.internal:9999").status_code != 403
        )
        assert (
            _origin_probe(client, "dt.internal:9999", "https://dt.internal:9999").status_code != 403
        )


def test_the_two_allowlists_derive_from_one_setting(tmp_path: Any) -> None:
    """The property DD-15 records: enabling one list cannot disable the other, because
    both come from the same two sources. An empty host list leaves the whole check off,
    and an empty origin list accompanies it rather than contradicting it."""
    unconfigured = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
    assert unconfigured.mcp_allowed_host_values() == []
    assert unconfigured.mcp_allowed_origin_values() == []

    configured = Settings(
        data_dir=tmp_path / "data",
        embedding_enabled=False,
        base_url="https://dt.example.com",
        mcp_allowed_hosts="dt.internal:8000",
    )
    assert configured.mcp_allowed_host_values() == ["dt.example.com", "dt.internal:8000"]
    for host in configured.mcp_allowed_host_values():
        assert any(o.endswith(f"//{host}") for o in configured.mcp_allowed_origin_values()), host
