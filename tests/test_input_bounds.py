"""Every input is bounded, and a bad one is a 4xx (DD-18, DD-19).

The seed probes are a security review's own reproductions, each watched to fail against
the tree before the fix existed:

- an oversized request body read and parsed in full on the unauthenticated login
  route;
- a keyset cursor whose boundary value is an object, and a simple cursor whose id is
  not an integer, both 500s;
- a filter nested past any useful depth, a 500 at roughly 991 frames;
- FastAPI's default 422, which echoes the request body -- including a value in the
  password position -- in FastAPI's own ``{"detail": [...]}`` shape rather than the
  project envelope, and which recurses while doing it.

The attachment, page-size and CSV halves of the same rule live with their own surfaces:
``tests/test_api_attachments.py``, ``tests/test_records.py``,
``tests/test_comments.py``, ``tests/test_mcp_errors.py`` and
``tests/test_csv_import_export.py``.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from glosswork.compiler import SortKey
from glosswork.compiler import decode_cursor as decode_keyset_cursor
from glosswork.config import Settings
from glosswork.cursors import decode_cursor, encode_cursor
from glosswork.errors import ValidationFailedError
from glosswork.filters import MAX_FILTER_DEPTH
from glosswork.middleware import is_body_cap_exempt
from glosswork.services import ServiceBundle
from tests.conftest import make_actor
from tests.mcp_support import seed_task_type


@pytest.fixture
def task_type(services: ServiceBundle) -> str:
    return seed_task_type(services)


def _keyset_cursor(values: list[Any], record_id: str, sort: tuple[SortKey, ...]) -> str:
    """Hand-built, the way a tampering caller builds one: the encoder lives on
    ``CompiledQuery.cursor_for`` and only ever emits values a row produced."""
    payload = {
        "v": values,
        "id": record_id,
        "k": [[s.field_key, s.direction] for s in sort],
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


# ------------------------------------------------------------------ body size cap


def _oversized_json(settings: Settings) -> bytes:
    """A JSON login body one byte past ``GW_MAX_REQUEST_BYTES``."""
    padding = settings.max_request_bytes  # the object wrapper puts it comfortably over
    return json.dumps({"email": "a@example.com", "password": "x" * padding}).encode()


def test_an_oversized_body_is_413_with_the_project_envelope(
    app: FastAPI, client: TestClient
) -> None:
    settings: Settings = app.state.settings
    response = client.post(
        "/api/v1/auth/login",
        content=_oversized_json(settings),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "payload_too_large"
    assert str(settings.max_request_bytes) in json.dumps(error["details"])


def test_an_oversized_login_body_never_reaches_the_limiter_or_argon2(
    app: FastAPI, client: TestClient
) -> None:
    """The refusal happens at the edge, so the route never runs: no rate-limiter
    window is opened and no Argon2id verification is paid for. That is the whole
    point of a body cap on the one unauthenticated write path."""
    settings: Settings = app.state.settings
    services: ServiceBundle = app.state.services
    limiter = services.login_limiter

    client.post(
        "/api/v1/auth/login",
        content=_oversized_json(settings),
        headers={"Content-Type": "application/json"},
    )

    assert limiter._windows == {}  # noqa: SLF001 - asserting the control did not run
    assert limiter._ip_windows == {}  # noqa: SLF001


def test_a_body_without_content_length_is_still_counted(app: FastAPI, client: TestClient) -> None:
    """A chunked upload declares no ``Content-Length``, so the cap has to count the
    bytes as they arrive rather than trusting a header that may be absent or lying."""
    settings: Settings = app.state.settings
    chunk = b"x" * 65536
    total = settings.max_request_bytes + len(chunk)

    def _chunks() -> Any:
        sent = 0
        while sent < total:
            sent += len(chunk)
            yield chunk

    response = client.post(
        "/api/v1/auth/login",
        content=_chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_an_ordinary_body_is_unaffected(client: TestClient) -> None:
    """A scope fence: the cap must not change what a normal request does."""
    response = client.get("/api/v1/object-types")
    assert response.status_code == 200


# ---------------------------------------------------------- the 422 handler (FR-A4)


def test_a_malformed_body_is_the_project_envelope_and_never_echoes_the_input(
    client: TestClient,
) -> None:
    secret = "hunter2-do-not-echo-this"
    response = client.post("/api/v1/auth/login", json={"email": 5, "password": secret})

    assert response.status_code == 422
    body = response.json()
    assert set(body) == {"error"}, "FastAPI's own {'detail': [...]} shape must be gone"
    error = body["error"]
    assert error["code"] == "validation_failed"
    assert secret not in response.text
    # ``loc`` and ``msg`` survive: the caller still learns what to fix.
    errors = error["details"]["errors"]
    assert errors and all({"loc", "msg"} <= set(e) for e in errors)
    assert all("input" not in e for e in errors)


def test_a_deeply_nested_body_is_422_rather_than_a_recursion_error(
    client: TestClient, capsys: Any
) -> None:
    """The review's 2,000-deep JSON body. The recursion was inside FastAPI's own
    ``RequestValidationError`` serializer while it echoed the input, which is why no
    application handler caught it first; the handler that stops the echo stops it.

    The log is asserted as well as the status, because the two could come apart: a
    handler that caught ``RecursionError`` and returned 422 would satisfy the status
    while still paying the recursion and still logging it at ``error``. Nothing here
    catches it -- the handler never serializes the input, so it never happens.
    """
    nested: Any = "leaf"
    for _ in range(2000):
        nested = [nested]
    response = client.post(
        "/api/v1/auth/login",
        content=json.dumps({"email": nested, "password": "x"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    logged = capsys.readouterr()
    assert "RecursionError" not in logged.out + logged.err
    assert "unclassified_exception" not in logged.out + logged.err


# ------------------------------------------------------------------------ cursors


def test_the_simple_decoder_refuses_a_non_scalar_element() -> None:
    cursor = encode_cursor({"id": {"nested": "object"}})
    with pytest.raises(ValidationFailedError) as exc:
        decode_cursor(cursor, {"id"})
    assert "cursor" in exc.value.message.lower()


def test_the_simple_decoder_refuses_a_non_integer_id_before_any_sql(
    services: ServiceBundle, task_type: str
) -> None:
    """``int(...)`` on a tampered cursor was an unhandled ``ValueError`` -> 500."""
    record = services.records.create_record(make_actor(), task_type, {"title": "One"})
    cursor = encode_cursor({"id": "not-an-integer"})
    with pytest.raises(ValidationFailedError):
        services.records.get_record_history_page(make_actor(), record.key, limit=10, cursor=cursor)


def test_the_keyset_decoder_refuses_a_dict_boundary_value() -> None:
    """The review's tampered cursor: sqlite3 refuses to bind a dict, and the log line
    that resulted carried the full SQL text *and* the bound parameters."""
    sort = (SortKey("title", "asc"),)
    cursor = _keyset_cursor([{"a": 1}], "some-record-id", sort)
    with pytest.raises(ValidationFailedError):
        decode_keyset_cursor(cursor, sort)


def test_a_valid_cursor_still_round_trips() -> None:
    """Scope fence: only decoding tightens; encoding is unchanged."""
    payload = {"created_at": "2026-09-04T00:00:00Z", "id": "abc"}
    assert decode_cursor(encode_cursor(payload), {"created_at", "id"}) == payload


# ------------------------------------------------------------------- filter depth


def _nested_filter(depth: int) -> dict[str, Any]:
    """``depth`` filter nodes: ``depth - 1`` ``and`` nodes wrapping one condition.

    The leaf names ``key``, a **pseudo-field**, so the identical tree is legal on both
    walkers' paths: the multi-type search path refuses a user field by its own one-type
    rule (``FILTER_RULE``) and would refuse the tree for the wrong reason.
    """
    node: dict[str, Any] = {"field": "key", "op": "eq", "value": "TSK-001"}
    for _ in range(depth - 1):
        node = {"and": [node]}
    return node


def test_a_filter_at_the_cap_is_accepted(services: ServiceBundle, task_type: str) -> None:
    result = services.records.query_records(
        make_actor(), task_type, filter=_nested_filter(MAX_FILTER_DEPTH)
    )
    assert result.total_count == 0


def test_a_filter_one_past_the_cap_is_refused(services: ServiceBundle, task_type: str) -> None:
    with pytest.raises(ValidationFailedError) as exc:
        services.records.query_records(
            make_actor(), task_type, filter=_nested_filter(MAX_FILTER_DEPTH + 1)
        )
    assert str(MAX_FILTER_DEPTH) in exc.value.message


# ---------------------------------------------------- the two surfaces agree on a 413


def test_the_mcp_413_carries_the_project_envelope_not_the_sdk_plain_text(
    app: FastAPI, client: TestClient
) -> None:
    """Watched to fail against the tree before the fix, where this returned 21 bytes
    of ``Request body too large`` in ``text/plain``: the SDK's own refusal, which had
    been live and unnoticed from the start because ``max_request_body_size`` was never passed
    and the surface took the SDK's 4 MiB default."""
    settings: Settings = app.state.settings
    response = client.post(
        "/mcp",
        content=b"x" * (settings.max_request_bytes + 1),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/json")
    error = response.json()["error"]
    assert error["code"] == "payload_too_large"
    assert error["details"]["limit_name"] == "GW_MAX_REQUEST_BYTES"


def test_one_setting_governs_both_surfaces_body_cap(monkeypatch: Any, tmp_path: Any) -> None:
    """Asserted on the value actually handed to the SDK rather than on
    the source, so the two caps cannot drift into disagreement."""
    import mcp.server as mcp_server_module

    from glosswork.app import create_app

    seen: dict[str, Any] = {}
    original = mcp_server_module.MCPServer.streamable_http_app

    def _spy(self: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return original(self, **kwargs)

    monkeypatch.setattr(mcp_server_module.MCPServer, "streamable_http_app", _spy)
    settings = Settings(
        data_dir=tmp_path / "data", embedding_enabled=False, max_request_bytes=12345
    )
    create_app(settings)
    assert seen["max_request_body_size"] == settings.max_request_bytes == 12345


def test_the_body_cap_exemptions_are_exactly_the_two_routes_that_need_them(
    client: TestClient,
) -> None:
    """Pinned by equality against the application's real route table -- read from
    ``/openapi.json``, as ``tests/test_api_infra.py`` reads it -- the way
    ``AUTH_PUBLIC_PATHS`` is pinned. The import path is parameterized, so the exemption cannot be
    an exact-match frozenset and is a suffix match, which is exactly the kind of rule
    that absorbs a future route silently unless something asserts the matched set."""
    spec = client.get("/openapi.json").json()
    matched = {
        (method.upper(), path)
        for path, operations in spec["paths"].items()
        if is_body_cap_exempt(path)
        for method in operations
    }
    assert matched == {
        ("POST", "/api/v1/attachments"),
        ("POST", "/api/v1/object-types/{object_type_key}/import"),
    }


# -------------------------------- the two filter walkers agree on what they refuse


def test_both_walkers_refuse_the_same_tree_at_the_same_depth(
    services: ServiceBundle, task_type: str
) -> None:
    """The *same* filter tree through ``query_records`` (which walks it with
    ``filters._parse_node``) and through multi-type ``search`` (which first walks it
    with ``search._filter_field_keys``). The two count depth differently -- an
    ``and`` node costs the search walker two levels of recursion where it costs the
    parser one -- so a naive ``> MAX_FILTER_DEPTH`` in both would have refused, on
    search, a filter query accepts. Both count filter nodes instead.
    """
    at_cap = _nested_filter(MAX_FILTER_DEPTH)
    past_cap = _nested_filter(MAX_FILTER_DEPTH + 1)

    services.records.query_records(make_actor(), task_type, filter=at_cap)
    services.search.search(make_actor(), query="anything", filter=at_cap)

    with pytest.raises(ValidationFailedError):
        services.records.query_records(make_actor(), task_type, filter=past_cap)
    with pytest.raises(ValidationFailedError):
        services.search.search(make_actor(), query="anything", filter=past_cap)


def test_a_deep_filter_value_on_the_search_path_is_refused_rather_than_recursed(
    services: ServiceBundle, task_type: str
) -> None:
    """``_filter_field_keys`` descends into a condition's ``value``, which
    ``_parse_node`` never does, so a shallow filter carrying a deeply nested literal
    would otherwise recurse there and nowhere else. It is bounded on its own budget:
    a deep *value* is not a deep filter, and refusing it against the node budget would
    have made a two-element ``between`` count toward nesting."""
    value: Any = "leaf"
    for _ in range(MAX_FILTER_DEPTH + 5):
        value = [value]
    with pytest.raises(ValidationFailedError):
        services.search.search(
            make_actor(),
            query="anything",
            filter={"field": "key", "op": "in", "value": value},
        )
