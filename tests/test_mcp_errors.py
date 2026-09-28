"""Error conventions over MCP (docs/MCP_TOOLS.md section 6, FR-M6): one mapping,
two surfaces.

Every error is produced through a real client call and arrives as a tool result
with ``is_error=true``, the shared envelope under ``structured_content["error"]``,
and the message text in ``content``. ``Client.call_tool`` returns for every case;
nothing below expects an exception.
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
from glosswork.services.comments import MAX_COMMENT_LIMIT
from glosswork.services.records import MAX_HISTORY_LIMIT, MAX_QUERY_LIMIT
from tests.conftest import make_actor
from tests.mcp_support import (
    TASK_FIELD_KEYS,
    error_of,
    memory_session,
    seed_task_type,
    structured,
)


@pytest.fixture
def seeded(services: ServiceBundle) -> ServiceBundle:
    seed_task_type(services)
    return services


async def _call(server: MCPServer, tool: str, args: dict[str, Any], token: str):  # type: ignore[no-untyped-def]
    async with memory_session(server, token=token) as c:
        return await c.call_tool(tool, args)


def _assert_error_shape(result: Any, code: str) -> dict[str, Any]:
    error = error_of(result)
    assert set(error) == {"code", "message", "details"}
    assert error["code"] == code
    assert result.content[0].type == "text"
    assert result.content[0].text == error["message"]
    return error


# ------------------------------------------------------------- REST/MCP parity


@pytest.mark.anyio
async def test_error_envelopes_are_identical_over_rest_and_mcp(
    app: FastAPI, client: TestClient, app_services: ServiceBundle, api_tokens: dict[str, str]
) -> None:
    """The same failing operation over REST and over MCP yields the same
    ``error`` object: both surfaces call ``errors.error_envelope``."""
    seed_task_type(app_services)
    record = app_services.records.create_record(make_actor(), "task", {"title": "parity"})
    server = create_mcp_server(lambda: app_services, PatTokenResolver(lambda: app_services))

    cases: list[tuple[str, Any, str, dict[str, Any]]] = [
        (
            "unknown_field",
            client.post("/api/v1/object-types/task/records", json={"titel": "typo"}),
            "create_record",
            {"object_type": "task", "values": {"titel": "typo"}},
        ),
        (
            "validation_failed",
            client.post("/api/v1/object-types/task/records", json={"status": "bogus"}),
            "create_record",
            {"object_type": "task", "values": {"status": "bogus"}},
        ),
        (
            "version_conflict",
            client.patch(
                f"/api/v1/records/{record.key}",
                json={"values": {"title": "x"}, "expected_version": 99},
            ),
            "update_record",
            {"record": record.key, "values": {"title": "x"}, "expected_version": 99},
        ),
        (
            "not_found",
            client.get("/api/v1/records/TSK-999"),
            "get_record",
            {"record": "TSK-999"},
        ),
    ]
    async with memory_session(server, token=api_tokens["admin"]) as c:
        for code, rest_response, tool, args in cases:
            rest_error = rest_response.json()["error"]
            assert rest_error["code"] == code
            mcp_error = error_of(await c.call_tool(tool, args))
            assert mcp_error == rest_error, code


# --------------------------------------------------------- each documented code


@pytest.mark.anyio
async def test_unknown_object_type_lists_valid_keys(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    result = await _call(
        mcp_server, "describe_object_type", {"object_type": "initiative"}, pat["admin"]
    )
    error = _assert_error_shape(result, "unknown_object_type")
    assert "task" in error["message"]
    assert error["details"]["valid_keys"] == ["task"]
    assert "list_object_types" in error["message"]


@pytest.mark.anyio
async def test_unknown_field_suggests_a_near_miss_and_lists_keys(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    result = await _call(
        mcp_server, "create_record", {"object_type": "task", "values": {"titel": "x"}}, pat["admin"]
    )
    error = _assert_error_shape(result, "unknown_field")
    assert "Did you mean 'title'?" in error["message"]
    assert error["details"]["near_misses"][0] == "title"
    assert set(error["details"]["valid_keys"]) == TASK_FIELD_KEYS
    assert "describe_object_type" in error["message"]


@pytest.mark.anyio
async def test_invalid_operator_lists_valid_operators(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    result = await _call(
        mcp_server,
        "query_records",
        {"object_type": "task", "filter": {"field": "status", "op": "gt", "value": "todo"}},
        pat["admin"],
    )
    error = _assert_error_shape(result, "invalid_operator")
    assert error["details"]["valid_ops"] == ["eq", "neq", "in", "not_in", "is_null", "is_not_null"]
    assert "eq, neq, in, not_in" in error["message"]


@pytest.mark.anyio
async def test_validation_failed_names_the_field_and_lists_options(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    result = await _call(
        mcp_server,
        "create_record",
        {"object_type": "task", "values": {"title": "x", "status": "bogus"}},
        pat["admin"],
    )
    error = _assert_error_shape(result, "validation_failed")
    assert error["details"]["field_key"] == "status"
    assert error["details"]["valid_options"] == ["todo", "doing", "done"]
    assert "todo, doing, done" in error["message"]


@pytest.mark.anyio
async def test_version_conflict_carries_merge_details(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    record = seeded.records.create_record(make_actor(), "task", {"title": "v1"})
    seeded.records.update_record(make_actor(), record.key, {"title": "v2"})
    result = await _call(
        mcp_server,
        "update_record",
        {"record": record.key, "values": {"title": "mine"}, "expected_version": 1},
        pat["admin"],
    )
    error = _assert_error_shape(result, "version_conflict")
    assert error["details"]["current_version"] == 2
    assert error["details"]["conflicting_fields"] == {
        "title": {"your_value": "mine", "current_value": "v2"}
    }
    assert error["details"]["changed_since_your_version"] == ["title"]
    assert "force=true" in error["message"]


@pytest.mark.anyio
async def test_relation_blocked_lists_blocking_keys(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    parent = seeded.records.create_record(make_actor(), "task", {"title": "parent"})
    child = seeded.records.create_record(make_actor(), "task", {"title": "child"})
    seeded.records.link_records(make_actor(), child.key, "parent", [parent.key])
    result = await _call(mcp_server, "delete_record", {"record": parent.key}, pat["admin"])
    error = _assert_error_shape(result, "relation_blocked")
    assert error["details"]["blocking_record_keys"] == [child.key]
    assert child.key in error["message"]


@pytest.mark.anyio
async def test_insufficient_scope_names_the_required_scope(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    result = await _call(
        mcp_server,
        "create_record",
        {"object_type": "task", "values": {"title": "x"}},
        pat["read"],
    )
    error = _assert_error_shape(result, "insufficient_scope")
    assert error["details"]["required_scope"] == "write"
    assert "administrator" in error["message"]


@pytest.mark.anyio
async def test_not_found(mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]) -> None:
    result = await _call(mcp_server, "get_record", {"record": "TSK-404"}, pat["admin"])
    error = _assert_error_shape(result, "not_found")
    assert error["details"] == {"entity": "record", "ref": "TSK-404"}


# ----------------------------------------------------------- requires_approval


@pytest.mark.anyio
async def test_destructive_update_field_is_a_normal_pending_result(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    seeded.records.create_record(make_actor(), "task", {"title": "has data"})
    async with memory_session(mcp_server, token=pat["admin"]) as c:
        result = await c.call_tool(
            "update_field",
            {"object_type": "task", "field_key": "title", "changes": {"type": "integer"}},
        )
    assert not result.is_error
    doc = structured(result)
    assert doc["status"] == "pending_human_approval"
    assert doc["proposal_id"]
    assert doc["impact"]["affected_records"] == 1
    assert "administrator must approve" in doc["message"].lower()
    proposal = seeded.schema.get_proposal(make_actor(), doc["proposal_id"])
    assert proposal.status == "pending"
    _, fields = seeded.schema.get_object_type(make_actor(), "task")
    assert next(f for f in fields if f.key == "title").type == "short_text"  # nothing applied


@pytest.mark.anyio
async def test_propose_schema_change_is_a_normal_pending_result(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    seeded.records.create_record(make_actor(), "task", {"title": "x", "due": "2026-09-09"})
    async with memory_session(mcp_server, token=pat["admin"]) as c:
        result = await c.call_tool(
            "propose_schema_change",
            {"change_type": "delete_field", "object_type": "task", "field_key": "due"},
        )
    assert not result.is_error
    doc = structured(result)
    assert doc["status"] == "pending_human_approval"
    assert doc["change_type"] == "delete_field"
    assert doc["impact"]["affected_records"] == 1
    assert doc["impact"]["non_empty_values"] == 1
    assert "administrator must approve" in doc["message"].lower()
    assert doc["proposal_id"] in doc["message"]


# ------------------------------------------------- protocol-level guarantees


@pytest.mark.anyio
async def test_no_domain_error_reaches_the_client_as_a_protocol_error(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    """For every case above, call_tool returned rather than raised; this sweeps
    them in one session to pin the guarantee explicitly."""
    calls: list[tuple[str, dict[str, Any]]] = [
        ("describe_object_type", {"object_type": "nope"}),
        ("create_record", {"object_type": "task", "values": {"titel": "x"}}),
        ("query_records", {"object_type": "task", "filter": {"field": "status", "op": "gt"}}),
        ("create_record", {"object_type": "task", "values": {"status": "bogus"}}),
        ("get_record", {"record": "TSK-999"}),
        ("delete_comment", {"comment_id": "missing"}),
        ("update_object_type", {"object_type": "task", "changes": {"key": "renamed"}}),
    ]
    async with memory_session(mcp_server, token=pat["admin"]) as c:
        for tool, args in calls:
            result = await c.call_tool(tool, args)
            assert result.is_error, (tool, args)
            assert "error" in structured(result), (tool, args)


@pytest.mark.anyio
async def test_argument_rejected_by_input_schema_is_a_tool_error_not_an_exception(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["admin"]) as c:
        result = await c.call_tool(
            "describe_object_type", {"object_type": "task", "include_samples": "definitely"}
        )
    assert result.is_error
    assert "include_samples" in result.content[0].text  # type: ignore[union-attr]


# ------------------------------------------------------------ caps, on both surfaces


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "args", "max_limit"),
    [
        ("query_records", {"object_type": "task"}, MAX_QUERY_LIMIT),
        ("get_record_history", {"record": "TSK-001"}, MAX_HISTORY_LIMIT),
        ("list_comments", {"record": "TSK-001"}, MAX_COMMENT_LIMIT),
    ],
)
async def test_a_limit_over_the_cap_reaches_mcp_as_the_project_envelope(
    mcp_server: MCPServer,
    seeded: ServiceBundle,
    pat: dict[str, str],
    tool: str,
    args: dict[str, Any],
    max_limit: int,
) -> None:
    """The cap is enforced in the service, so MCP inherits it -- and it
    arrives as the project's ``validation_failed`` envelope naming the cap, not as an
    SDK schema error, which is what an ``le=`` on the parameter would have produced."""
    seeded.records.create_record(make_actor(), "task", {"title": "One"})
    result = await _call(mcp_server, tool, {**args, "limit": max_limit + 1}, pat["admin"])
    error = _assert_error_shape(result, "validation_failed")
    assert error["details"]["max_limit"] == max_limit


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "args", "max_limit"),
    [
        ("query_records", {"object_type": "task"}, MAX_QUERY_LIMIT),
        ("get_record_history", {"record": "TSK-001"}, MAX_HISTORY_LIMIT),
        ("list_comments", {"record": "TSK-001"}, MAX_COMMENT_LIMIT),
    ],
)
async def test_a_limit_at_the_cap_is_accepted_over_mcp(
    mcp_server: MCPServer,
    seeded: ServiceBundle,
    pat: dict[str, str],
    tool: str,
    args: dict[str, Any],
    max_limit: int,
) -> None:
    seeded.records.create_record(make_actor(), "task", {"title": "One"})
    result = await _call(mcp_server, tool, {**args, "limit": max_limit}, pat["admin"])
    assert not result.is_error, result


@pytest.mark.anyio
async def test_no_mcp_parameter_declares_the_bound_as_a_schema_constraint(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    """Asserted on the published schema rather than on the source.

    An ``le=`` would move the refusal into the SDK's own coercion, where
    ``adapter.py`` deliberately leaves the message as the SDK's -- so ``limit=1001``
    would be ``validation_failed`` on REST and an SDK schema error on MCP. The bound
    lives in the description text instead, and that is where an agent reads it.
    """
    async with memory_session(mcp_server, token=pat["admin"]) as client:
        listing = await client.list_tools()
    by_name = {tool.name: tool for tool in listing.tools}
    for tool_name, max_limit in (
        ("query_records", MAX_QUERY_LIMIT),
        ("get_record_history", MAX_HISTORY_LIMIT),
        ("list_comments", MAX_COMMENT_LIMIT),
    ):
        schema = by_name[tool_name].input_schema["properties"]["limit"]
        assert "maximum" not in schema and "exclusiveMaximum" not in schema, tool_name
        assert str(max_limit) in schema["description"], tool_name
