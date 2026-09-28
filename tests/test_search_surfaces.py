"""``search`` on REST and MCP, ``describe_capabilities``, and the disabled-deployment
codes over the surfaces (FR-A1, FR-M4, FR-M6, docs/MCP_TOOLS.md sections 5.1, 6, 8).

Shape tests run in ``keyword`` mode under the fake provider (its vectors are
hash-seeded, so semantic order is meaningless there); ``tests/test_search_golden.py``
is where semantics are judged.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server import MCPServer

from glosswork.auth import PatTokenResolver
from glosswork.mcp_server import create_mcp_server
from glosswork.services import ServiceBundle
from tests.conftest import auth, make_actor, mint_scope_tokens
from tests.mcp_support import error_of, memory_session, structured
from tests.test_search_service import NOTE_FIELDS


def _seed(services: ServiceBundle) -> dict[str, Any]:
    services.schema.create_object_type(
        make_actor(),
        key="note",
        name="Note",
        name_plural="Notes",
        description="A note used by the search surface tests.",
        key_prefix="NOTE",
        fields=NOTE_FIELDS,
    )
    active = services.records.create_record(
        make_actor(),
        "note",
        {"title": "Pricing north", "body": "pricing matrix", "region": "north"},
    )
    services.records.create_record(
        make_actor(), "note", {"title": "Pricing south", "body": "pricing memos", "region": "south"}
    )
    comment = services.comments.add_comment(make_actor(), active.key, "the pricing binder")
    return {"active": active, "comment": comment}


@pytest.fixture
def seeded_client(
    search_app: FastAPI, search_client: TestClient
) -> tuple[TestClient, dict[str, Any]]:
    return search_client, _seed(search_app.state.services)


@pytest.fixture
def search_mcp(search_services: ServiceBundle) -> tuple[MCPServer, dict[str, str], dict[str, Any]]:
    """An MCP server over the embedding-enabled bundle, with real per-scope PATs."""
    seeded = _seed(search_services)
    server = create_mcp_server(lambda: search_services, PatTokenResolver(lambda: search_services))
    return server, mint_scope_tokens(search_services), seeded


EXPECTED_HIT_KEYS = {
    "record_key",
    "record_id",
    "object_type",
    "title",
    "score",
    "hit_source",
    "snippet",
    "other_matches",
}


# ----------------------------------------------------------------------- REST


def test_rest_search_f1_shape_over_http(seeded_client: tuple[TestClient, dict[str, Any]]) -> None:
    """Golden case F1's shape (query plus a one-type filter) over HTTP, in keyword
    mode: the envelope, the hit keys, the filter intersection, and index_lag."""
    client, seeded = seeded_client
    response = client.post(
        "/api/v1/search",
        json={
            "query": "pricing",
            "object_types": ["note"],
            "filter": {"field": "region", "op": "eq", "value": "north"},
            "mode": "keyword",
            "limit": 10,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"results", "index_lag", "mode_applied"}
    assert body["mode_applied"] == "keyword"
    assert set(body["index_lag"]) == {"pending_jobs", "failed_jobs"}
    assert [hit["record_key"] for hit in body["results"]] == [seeded["active"].key]
    hit = body["results"][0]
    assert set(hit) == EXPECTED_HIT_KEYS
    assert hit["object_type"] == "note" and hit["title"] == "Pricing north"
    assert hit["score"] == 1.0 and hit["other_matches"] == 2
    assert "<em>" in hit["snippet"]
    assert "widened" not in body and "widened" not in hit


def test_rest_search_defaults_and_validation(
    seeded_client: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = seeded_client
    assert (
        client.post("/api/v1/search", json={"query": "pricing", "mode": "keyword"}).status_code
        == 200
    )
    empty = client.post("/api/v1/search", json={"query": "   "})
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "validation_failed"
    too_many = client.post("/api/v1/search", json={"query": "pricing", "limit": 51})
    assert too_many.status_code == 422
    unknown = client.post("/api/v1/search", json={"query": "pricing", "object_types": ["nope"]})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_object_type"
    rule = client.post(
        "/api/v1/search",
        json={"query": "pricing", "filter": {"field": "region", "op": "eq", "value": "north"}},
    )
    assert rule.status_code == 422 and rule.json()["error"]["details"]["rule"] == "one_type"


def test_rest_search_is_reachable_at_read_scope(
    seeded_client: tuple[TestClient, dict[str, Any]],
) -> None:
    client, _ = seeded_client
    tokens: dict[str, str] = client.scope_tokens  # type: ignore[attr-defined]
    response = client.post(
        "/api/v1/search", json={"query": "pricing", "mode": "keyword"}, headers=auth(tokens["read"])
    )
    assert response.status_code == 200


def test_feature_disabled_over_rest_is_409_with_the_settled_details(client: TestClient) -> None:
    """The shared ``client`` fixture runs with embedding disabled (DD-19 as settled)."""
    response = client.post("/api/v1/search", json={"query": "anything", "mode": "semantic"})
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "feature_disabled"
    assert error["details"] == {
        "feature": "semantic_search",
        "setting": "GW_EMBEDDING_ENABLED",
        "use_instead": {"mode": "keyword"},
    }
    degraded = client.post("/api/v1/search", json={"query": "anything", "mode": "hybrid"})
    assert degraded.status_code == 200 and degraded.json()["mode_applied"] == "keyword"


def test_reindex_over_rest_is_feature_disabled_when_embedding_is_off(client: TestClient) -> None:
    response = client.post("/api/v1/admin/search-index/reindex")
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "feature_disabled" and error["details"]["feature"] == "reindex"
    assert "use_instead" not in error["details"]


def test_openapi_documents_search_and_reindex(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert "post" in spec["paths"]["/api/v1/search"]
    assert "post" in spec["paths"]["/api/v1/admin/search-index/reindex"]
    body_schema = spec["components"]["schemas"]["SearchBody"]
    assert set(body_schema["properties"]) == {"query", "object_types", "filter", "mode", "limit"}
    assert body_schema["required"] == ["query"]


# ------------------------------------------------------------------------ MCP


@pytest.mark.anyio
async def test_mcp_search_returns_the_same_envelope_as_rest(
    search_mcp: tuple[MCPServer, dict[str, str], dict[str, Any]],
) -> None:
    server, tokens, seeded = search_mcp
    async with memory_session(server, token=tokens["read"]) as client:
        result = structured(
            await client.call_tool(
                "search",
                {
                    "query": "pricing",
                    "object_types": ["note"],
                    "filter": {"field": "region", "op": "eq", "value": "north"},
                    "mode": "keyword",
                },
            )
        )
    assert set(result) == {"results", "index_lag", "mode_applied"}
    assert [hit["record_key"] for hit in result["results"]] == [seeded["active"].key]
    assert set(result["results"][0]) == EXPECTED_HIT_KEYS
    assert result["results"][0]["other_matches"] == 2


@pytest.mark.anyio
async def test_mcp_search_errors_use_the_shared_envelope(
    search_mcp: tuple[MCPServer, dict[str, str], dict[str, Any]],
) -> None:
    server, tokens, _ = search_mcp
    async with memory_session(server, token=tokens["read"]) as client:
        rule = error_of(
            await client.call_tool(
                "search",
                {"query": "pricing", "filter": {"field": "region", "op": "eq", "value": "north"}},
            )
        )
        assert rule["code"] == "validation_failed" and rule["details"]["rule"] == "one_type"
        empty = error_of(await client.call_tool("search", {"query": " "}))
        assert empty["code"] == "validation_failed"


@pytest.mark.anyio
async def test_mcp_search_feature_disabled_on_a_disabled_deployment(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """``mcp_server`` is built over the disabled ``services`` fixture."""
    async with memory_session(mcp_server, token=pat["read"]) as client:
        refused = error_of(await client.call_tool("search", {"query": "x", "mode": "semantic"}))
        assert refused["code"] == "feature_disabled"
        assert refused["details"]["use_instead"] == {"mode": "keyword"}
        degraded = structured(await client.call_tool("search", {"query": "x", "mode": "hybrid"}))
        assert degraded["mode_applied"] == "keyword"


@pytest.mark.anyio
async def test_mcp_search_docstring_carries_the_fr_m6_copy(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["read"]) as client:
        listing = await client.list_tools()
        instructions = client.instructions
    tool = next(t for t in listing.tools if t.name == "search")
    description = (tool.description or "").lower()
    for phrase in (
        "query_records",
        "keyword",
        "get_record",
        "index_lag",
        "mode_applied",
        "feature_disabled",
    ):
        assert phrase in description, phrase
    filter_doc = tool.input_schema["properties"]["filter"]["description"].lower()
    assert "exactly one type" in filter_doc
    assert instructions is not None and "search" in instructions and "index_lag" in instructions


# ---------------------------------------------------------- capabilities


@pytest.mark.anyio
async def test_describe_capabilities_reports_the_search_block_disabled_and_enabled(
    mcp_server: MCPServer,
    pat: dict[str, str],
    search_mcp: tuple[MCPServer, dict[str, str], dict[str, Any]],
) -> None:
    async with memory_session(mcp_server, token=pat["admin"]) as client:
        disabled = structured(await client.call_tool("describe_capabilities", {}))
    assert set(disabled) == {
        "search",
        "field_types",
        "system_fields",
        "filter_grammar",
        "date_tokens",
        "key_rules",
        # DD-29: the file recipe. Its contents are asserted against the service's
        # own constants in tests/test_attachment_surface.py; this set stays pinned by
        # equality so a block added to the code without being published fails here.
        "attachments",
        "limits",
    }
    # DD-18: the ``limits`` key set is pinned by equality, not by ``>=``, so a cap
    # added to the code without being published here fails rather than passing silently.
    assert set(disabled["limits"]) == {
        "query_default_limit",
        "query_max_limit",
        "history_max_limit",
        "comment_max_limit",
        "filter_max_depth",
        "changes_default_limit",
        "changes_max_limit",
        "bulk_update_max_matches",
        "bulk_update_sample_keys",
        "describe_samples_per_field",
        # DD-18: the proposal list is bounded like every other list an agent can page.
        "proposal_default_limit",
        "proposal_max_limit",
        "agent_label_max_length",
        # The first *setting* published in this document; the caps above are constants.
        "attachment_max_bytes",
        # DD-16: the second setting in this block.
        "upload_ticket_ttl_seconds",
    }
    block = disabled["search"]
    assert set(block) == {
        "modes",
        "default_mode",
        "default_limit",
        "max_limit",
        "max_query_chars",
        "semantic_enabled",
        "filter_rule",
        "fusion",
    }
    assert block["modes"] == ["hybrid", "semantic", "keyword"]
    assert block["default_mode"] == "hybrid"
    assert (block["default_limit"], block["max_limit"], block["max_query_chars"]) == (10, 50, 1000)
    assert block["fusion"] == {"k": 60, "candidate_multiplier": 4, "widen_multiplier": 16}
    assert block["semantic_enabled"] is False
    assert "exactly one type" in block["filter_rule"]

    server, tokens, _ = search_mcp
    async with memory_session(server, token=tokens["admin"]) as client:
        enabled = structured(await client.call_tool("describe_capabilities", {}))
    assert enabled["search"]["semantic_enabled"] is True
