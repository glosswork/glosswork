"""Reads and writes over MCP, in parity with REST (FR-A1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.server import MCPServer

from glosswork.auth import PatTokenResolver
from glosswork.fieldtypes import PSEUDO_FIELDS
from glosswork.filters import MAX_FILTER_DEPTH
from glosswork.mcp_server import create_mcp_server
from glosswork.services import ServiceBundle
from glosswork.services.comments import MAX_COMMENT_LIMIT
from glosswork.services.records import MAX_HISTORY_LIMIT, MAX_QUERY_LIMIT
from tests.conftest import make_actor
from tests.mcp_support import error_of, memory_session, seed_task_type, structured


@pytest.fixture
def seeded(services: ServiceBundle) -> ServiceBundle:
    seed_task_type(services)
    return services


def _today_plus(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%d")


async def _one(server: MCPServer, tool: str, args: dict[str, Any], token: str) -> Any:
    async with memory_session(server, token=token) as c:
        return await c.call_tool(tool, args)


# -------------------------------------------------------------- query_records


@pytest.mark.anyio
async def test_query_passes_every_parameter_through(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    soon = seeded.records.create_record(
        make_actor(), "task", {"title": "Soon", "status": "doing", "due": _today_plus(5)}
    )
    late = seeded.records.create_record(
        make_actor(), "task", {"title": "Late", "status": "doing", "due": _today_plus(90)}
    )
    done = seeded.records.create_record(
        make_actor(), "task", {"title": "Done", "status": "done", "due": _today_plus(1)}
    )
    seeded.comments.add_comment(make_actor(), done.key, "discussed")
    parent = seeded.records.create_record(make_actor(), "task", {"title": "Parent"})
    seeded.records.link_records(make_actor(), soon.key, "parent", [parent.key])
    gone = seeded.records.create_record(make_actor(), "task", {"title": "Gone"})
    seeded.records.delete_record(make_actor(), gone.key)

    async with memory_session(mcp_server, token=pat["read"]) as c:
        # Nested and/or/not over a custom field, a pseudo-field, and a date token.
        nested = structured(
            await c.call_tool(
                "query_records",
                {
                    "object_type": "task",
                    "filter": {
                        "and": [
                            {"field": "status", "op": "in", "value": ["doing", "done"]},
                            {
                                "or": [
                                    {"field": "due", "op": "lte", "value": "@today+30d"},
                                    {"field": "comment_count", "op": "gt", "value": 0},
                                ]
                            },
                            {"not": {"field": "title", "op": "eq", "value": "Done"}},
                        ]
                    },
                    "sort": [{"field": "due", "dir": "desc"}],
                    "fields": ["title", "due"],
                    "expand_relations": ["parent"],
                },
            )
        )
        assert [r["key"] for r in nested["records"]] == [soon.key]
        assert set(nested["records"][0]["data"]) == {"title", "due"}
        assert nested["records"][0]["expand"]["parent"][0]["key"] == parent.key
        assert nested["records"][0]["expand"]["parent"][0]["display"] == "Parent"

        # sort + limit + cursor: two pages of one, in due order desc.
        first = structured(
            await c.call_tool(
                "query_records",
                {
                    "object_type": "task",
                    "filter": {"field": "due", "op": "is_not_null"},
                    "sort": [{"field": "due", "dir": "desc"}],
                    "limit": 2,
                },
            )
        )
        assert [r["key"] for r in first["records"]] == [late.key, soon.key]
        assert first["next_cursor"]
        second = structured(
            await c.call_tool(
                "query_records",
                {
                    "object_type": "task",
                    "filter": {"field": "due", "op": "is_not_null"},
                    "sort": [{"field": "due", "dir": "desc"}],
                    "limit": 2,
                    "cursor": first["next_cursor"],
                },
            )
        )
        assert [r["key"] for r in second["records"]] == [done.key]
        assert second["next_cursor"] is None

        # include_deleted surfaces the soft-deleted record.
        live = structured(await c.call_tool("query_records", {"object_type": "task"}))
        with_deleted = structured(
            await c.call_tool("query_records", {"object_type": "task", "include_deleted": True})
        )
    assert gone.key not in {r["key"] for r in live["records"]}
    assert gone.key in {r["key"] for r in with_deleted["records"]}
    assert with_deleted["total_count"] == live["total_count"] + 1


@pytest.mark.anyio
async def test_fr_m8_compact_default_projection_and_truncation_guidance(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    for i in range(3):
        seeded.records.create_record(
            make_actor(),
            "task",
            {"title": f"T{i}", "status": "todo", "notes": "long working notes " * 5},
        )
    async with memory_session(mcp_server, token=pat["read"]) as c:
        compact = structured(await c.call_tool("query_records", {"object_type": "task"}))
        assert compact["total_count"] == 3
        record = compact["records"][0]
        assert set(record["data"]) == {"title", "status"}  # display + indexed fields
        assert record["key"] and record["created_at"] and record["version"] == 1
        assert compact["truncated"] is True  # notes were omitted
        assert "narrow" in compact["guidance"].lower()
        assert "fields" in compact["guidance"]
        assert "next_cursor" in compact["guidance"]

        everything = structured(
            await c.call_tool("query_records", {"object_type": "task", "fields": "*"})
        )
        assert set(everything["records"][0]["data"]) == {"title", "status", "notes"}
        assert everything["truncated"] is False
        assert "guidance" not in everything

        clipped = structured(
            await c.call_tool("query_records", {"object_type": "task", "fields": "*", "limit": 2})
        )
        assert len(clipped["records"]) == 2
        assert clipped["next_cursor"]
        assert clipped["truncated"] is True
        assert "guidance" in clipped


# ------------------------------------------------------------------ get_record


@pytest.mark.anyio
async def test_get_record_by_key_or_uuid_with_includes_matches_rest(
    client: TestClient, app_services: ServiceBundle, api_tokens: dict[str, str]
) -> None:
    seed_task_type(app_services)
    parent = app_services.records.create_record(make_actor(), "task", {"title": "P"})
    child = app_services.records.create_record(make_actor(), "task", {"title": "C"})
    app_services.records.link_records(make_actor(), child.key, "parent", [parent.key])
    app_services.comments.add_comment(make_actor(), child.key, "hello")
    server = create_mcp_server(lambda: app_services, PatTokenResolver(lambda: app_services))

    includes = ["comments", "links", "history", "attachments"]
    rest = client.get(f"/api/v1/records/{child.key}", params={"include": ",".join(includes)})
    assert rest.status_code == 200
    async with memory_session(server, token=api_tokens["read"]) as c:
        by_key = structured(
            await c.call_tool("get_record", {"record": child.key, "include": includes})
        )
        by_uuid = structured(
            await c.call_tool("get_record", {"record": child.id, "include": includes})
        )
        plain = structured(await c.call_tool("get_record", {"record": child.key}))
    assert by_key == rest.json()
    assert by_uuid == by_key
    # Each entry is {key, id, display}, not only {key, id}.
    assert by_key["links"] == {
        "parent": [{"key": parent.key, "id": parent.id, "display": parent.data.get("title")}],
        "children": [],
    }
    assert [c["body"] for c in by_key["comments"]] == ["hello"]
    assert by_key["history"] and by_key["attachments"] == {}
    assert set(plain) == set(by_key) - set(includes)


# ------------------------------------------------ update / delete / restore


@pytest.mark.anyio
async def test_update_with_expected_version_and_force(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    record = seeded.records.create_record(make_actor(), "task", {"title": "v1"})
    async with memory_session(mcp_server, token=pat["write"]) as c:
        ok = structured(
            await c.call_tool(
                "update_record",
                {"record": record.key, "values": {"title": "v2"}, "expected_version": 1},
            )
        )
        assert ok["version"] == 2 and ok["data"]["title"] == "v2"
        stale = await c.call_tool(
            "update_record",
            {"record": record.key, "values": {"title": "stale"}, "expected_version": 1},
        )
        assert error_of(stale)["code"] == "version_conflict"
        forced = structured(
            await c.call_tool(
                "update_record",
                {
                    "record": record.key,
                    "values": {"title": "forced"},
                    "expected_version": 1,
                    "force": True,
                },
            )
        )
    assert forced["version"] == 3 and forced["data"]["title"] == "forced"


@pytest.mark.anyio
async def test_delete_blocked_unless_forced_then_restore(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    parent = seeded.records.create_record(make_actor(), "task", {"title": "P"})
    child = seeded.records.create_record(make_actor(), "task", {"title": "C"})
    seeded.records.link_records(make_actor(), child.key, "parent", [parent.key])
    async with memory_session(mcp_server, token=pat["write"]) as c:
        blocked = await c.call_tool("delete_record", {"record": parent.key})
        assert error_of(blocked)["details"]["blocking_record_keys"] == [child.key]
        forced = structured(
            await c.call_tool("delete_record", {"record": parent.key, "force": True})
        )
        assert forced["deleted_at"] is not None
        assert seeded.records.list_links(make_actor(), child.key, "parent") == []
        restored = structured(await c.call_tool("restore_record", {"record": parent.key}))
    assert restored["deleted_at"] is None
    assert seeded.records.get_record(make_actor(), parent.key).deleted_at is None


# ------------------------------------------------------------- bulk update


@pytest.mark.anyio
async def test_bulk_update_dry_run_then_apply(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    keys = [
        seeded.records.create_record(make_actor(), "task", {"title": f"T{i}", "status": "todo"}).key
        for i in range(25)
    ]
    args = {
        "object_type": "task",
        "filter": {"field": "status", "op": "eq", "value": "todo"},
        "values": {"status": "doing"},
    }
    async with memory_session(mcp_server, token=pat["write"]) as c:
        dry = structured(await c.call_tool("bulk_update_records", {**args, "dry_run": True}))
        assert dry["affected_count"] == 25 and dry["dry_run"] is True
        assert len(dry["sample_keys"]) == 20 and set(dry["sample_keys"]) <= set(keys)
        assert all(
            r["data"]["status"] == "todo"
            for r in seeded.records.query_records(make_actor(), "task", limit=100).records
        )
        applied = structured(await c.call_tool("bulk_update_records", args))
    assert applied["affected_count"] == 25 and applied["dry_run"] is False
    assert len(applied["sample_keys"]) == 20
    assert all(
        r["data"]["status"] == "doing"
        for r in seeded.records.query_records(make_actor(), "task", limit=100).records
    )


# ------------------------------------------------------------------- links


@pytest.mark.anyio
async def test_link_and_unlink_by_key_or_uuid_keep_inverse_consistent(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    parent = seeded.records.create_record(make_actor(), "task", {"title": "P"})
    a = seeded.records.create_record(make_actor(), "task", {"title": "A"})
    b = seeded.records.create_record(make_actor(), "task", {"title": "B"})
    async with memory_session(mcp_server, token=pat["write"]) as c:
        linked = structured(
            await c.call_tool(
                "link_records",
                {"from_record": a.key, "field_key": "parent", "to_records": [parent.id]},
            )
        )
        assert linked == {"field_key": "parent", "linked": [parent.id]}
        await c.call_tool(
            "link_records",
            {"from_record": b.id, "field_key": "parent", "to_records": [parent.key]},
        )
        assert {r.key for r in seeded.records.list_links(make_actor(), parent.key, "children")} == {
            a.key,
            b.key,
        }
        unlinked = structured(
            await c.call_tool(
                "unlink_records",
                {"from_record": a.id, "field_key": "parent", "to_records": [parent.key]},
            )
        )
    assert unlinked == {"field_key": "parent", "unlinked_count": 1}
    assert {r.key for r in seeded.records.list_links(make_actor(), parent.key, "children")} == {
        b.key
    }
    assert seeded.records.list_links(make_actor(), a.key, "parent") == []


# ---------------------------------------------------------- comments/history


@pytest.mark.anyio
async def test_comment_crud_and_keyset_pagination_yield_each_item_once(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    record = seeded.records.create_record(make_actor(), "task", {"title": "talk"})
    async with memory_session(mcp_server, token=pat["write"]) as c:
        ids = []
        for i in range(5):
            comment = structured(
                await c.call_tool("add_comment", {"record": record.key, "body": f"c{i}"})
            )
            ids.append(comment["id"])
        edited = structured(
            await c.call_tool("update_comment", {"comment_id": ids[0], "body": "c0 edited"})
        )
        assert edited["edited"] is True and edited["body"] == "c0 edited"
        gone = structured(await c.call_tool("delete_comment", {"comment_id": ids[4]}))
        assert gone["deleted_at"] is not None

        seen: list[str] = []
        cursor = None
        pages = 0
        while True:
            args: dict[str, Any] = {"record": record.key, "limit": 2}
            if cursor:
                args["cursor"] = cursor
            page = structured(await c.call_tool("list_comments", args))
            pages += 1
            seen.extend(cm["id"] for cm in page["comments"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
    assert pages == 2
    assert seen == ids[:4]  # every live comment exactly once, in order
    assert len(set(seen)) == len(seen)


@pytest.mark.anyio
async def test_history_pagination_yields_each_event_once_and_filters_by_field(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    record = seeded.records.create_record(make_actor(), "task", {"title": "h", "status": "todo"})
    for i in range(3):
        seeded.records.update_record(make_actor(), record.key, {"title": f"h{i}"})
    all_events = seeded.records.get_record_history(make_actor(), record.key)
    async with memory_session(mcp_server, token=pat["read"]) as c:
        seen: list[int] = []
        cursor = None
        while True:
            args: dict[str, Any] = {"record": record.key, "limit": 2}
            if cursor:
                args["cursor"] = cursor
            page = structured(await c.call_tool("get_record_history", args))
            seen.extend(e["id"] for e in page["events"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        titles_only = structured(
            await c.call_tool("get_record_history", {"record": record.key, "field_key": "title"})
        )
    assert seen == [e.id for e in all_events]
    assert len(all_events) > 2
    assert {e["field_key"] for e in titles_only["events"]} == {"title"}
    assert len(titles_only["events"]) == 4  # create + 3 updates


# ------------------------------------------------------------- change feed


@pytest.mark.anyio
async def test_list_changes_since(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["write"]) as c:
        start = structured(await c.call_tool("list_changes_since", {}))
        assert start["events"] == [] and isinstance(start["next_cursor"], int)
        created = structured(
            await c.call_tool("create_record", {"object_type": "task", "values": {"title": "x"}})
        )
        since = structured(
            await c.call_tool("list_changes_since", {"cursor": start["next_cursor"]})
        )
        ids = [e["id"] for e in since["events"]]
        assert ids == sorted(ids) and ids[0] > start["next_cursor"]
        assert {e["record_id"] for e in since["events"]} == {created["id"]}
        assert since["next_cursor"] == ids[-1]
        scoped = structured(
            await c.call_tool(
                "list_changes_since", {"cursor": start["next_cursor"], "object_types": ["task"]}
            )
        )
        assert [e["id"] for e in scoped["events"]] == ids
        nothing = structured(
            await c.call_tool("list_changes_since", {"cursor": since["next_cursor"]})
        )
    assert nothing["events"] == [] and nothing["next_cursor"] == since["next_cursor"]


# ---------------------------------------------------------------- schema tools


@pytest.mark.anyio
async def test_schema_tools(
    mcp_server: MCPServer, services: ServiceBundle, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["admin"]) as c:
        created = structured(
            await c.call_tool(
                "create_object_type",
                {
                    "key": "risk",
                    "name": "Risk",
                    "name_plural": "Risks",
                    "description": "Something that might go wrong and needs an owner.",
                    "key_prefix": "RSK",
                    "fields": [
                        {
                            "key": "summary",
                            "name": "Summary",
                            "type": "short_text",
                            "description": "One line naming the risk.",
                            "required": True,
                        },
                        {
                            "key": "severity",
                            "name": "Severity",
                            "type": "single_select",
                            "description": "How bad it would be.",
                            "config": {
                                "options": [
                                    {"value": "low", "label": "Low", "description": "Minor."},
                                    {"value": "high", "label": "High", "description": "Major."},
                                ]
                            },
                        },
                    ],
                },
            )
        )
        assert created["key"] == "risk" and created["field_count"] == 2

        for immutable_key in ("key", "key_prefix"):
            immutable = await c.call_tool(
                "update_object_type",
                {"object_type": "risk", "changes": {immutable_key: "hazard"}},
            )
            assert error_of(immutable)["code"] == "validation_failed", immutable_key
            assert "immutable" in error_of(immutable)["message"], immutable_key
            assert immutable_key in error_of(immutable)["message"]
        assert services.schema.get_object_type(make_actor(), "risk")[0].key_prefix == "RSK"
        renamed = structured(
            await c.call_tool(
                "update_object_type", {"object_type": "risk", "changes": {"name": "Hazard"}}
            )
        )
        assert renamed["name"] == "Hazard"

        added = structured(
            await c.call_tool(
                "add_field",
                {
                    "object_type": "risk",
                    "key": "owner_note",
                    "name": "Owner note",
                    "type": "long_text",
                    "description": "Why this owner.",
                    "required": False,
                },
            )
        )
        assert added["status"] == "applied" and added["field"]["key"] == "owner_note"
        assert "contains" in added["field"]["operators"]

        additive = structured(
            await c.call_tool(
                "update_field",
                {"object_type": "risk", "field_key": "summary", "changes": {"name": "Headline"}},
            )
        )
        assert additive["status"] == "applied" and additive["field"]["name"] == "Headline"

        services.records.create_record(make_actor(), "risk", {"summary": "s", "severity": "low"})
        destructive = structured(
            await c.call_tool(
                "update_field",
                {
                    "object_type": "risk",
                    "field_key": "severity",
                    "changes": {
                        "config": {
                            "options": [{"value": "high", "label": "High", "description": "Major."}]
                        }
                    },
                },
            )
        )
        assert destructive["status"] == "pending_human_approval"

        pending = structured(await c.call_tool("list_schema_proposals", {"status": "pending"}))
        assert [p["id"] for p in pending["proposals"]] == [destructive["proposal_id"]]
        approved = structured(await c.call_tool("list_schema_proposals", {"status": "approved"}))
        assert approved["proposals"] == []
        every = structured(await c.call_tool("list_schema_proposals", {}))
        assert len(every["proposals"]) == 1

        caps = structured(await c.call_tool("describe_capabilities", {}))
    types = {t["type"]: t for t in caps["field_types"]}
    assert len(types) == 13
    assert types["relation"]["config_keys"] == [
        "cardinality",
        "inverse_field_key",
        "target_type_key",
    ]
    assert types["single_select"]["operators"] == [
        "eq",
        "neq",
        "in",
        "not_in",
        "is_null",
        "is_not_null",
    ]
    assert all(t["description"] for t in types.values())
    assert set(caps["date_tokens"]["bases"]) == {
        "@today",
        "@now",
        "@start_of_week",
        "@start_of_month",
        "@start_of_quarter",
        "@start_of_year",
    }
    assert caps["date_tokens"]["offset_units"] == {
        "d": "days",
        "w": "weeks",
        "M": "months",
        "y": "years",
        "h": "hours",
        "m": "minutes",
    }
    assert caps["limits"]["bulk_update_sample_keys"] == 20
    assert caps["limits"]["changes_max_limit"] == 1000
    # DD-18: every ceiling an agent can hit is published here, so it can size a
    # page before asking rather than discovering the cap by being refused.
    assert caps["limits"]["query_max_limit"] == MAX_QUERY_LIMIT
    assert caps["limits"]["history_max_limit"] == MAX_HISTORY_LIMIT
    assert caps["limits"]["comment_max_limit"] == MAX_COMMENT_LIMIT
    assert caps["limits"]["filter_max_depth"] == MAX_FILTER_DEPTH
    assert {f["key"] for f in caps["system_fields"]} >= {"key", "comment_count", "last_comment_at"}
    # DD-20: the reserved set an agent must not name a field after, published
    # through the same tool it reads the key pattern from. `describe_capabilities` is
    # MCP-only -- there is no REST route -- so this is the only surface to check.
    assert caps["key_rules"]["reserved_field_keys"] == sorted(PSEUDO_FIELDS)
    assert "created_by_name" in caps["key_rules"]["reserved_field_keys_note"]


@pytest.mark.anyio
async def test_describe_capabilities_publishes_reserved_field_keys(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """DD-20. The set an agent must not name a field after, on the
    adapter an agent actually reads. ``describe_capabilities`` is an MCP tool with no
    REST route, so this is the second and last surface: the service document is pinned
    in ``tests/test_schema_engine.py``, and ``/openapi.json`` does not move, which is
    what keeps the frontend's unchanged types safe rather than lucky.
    """
    caps = structured(await _one(mcp_server, "describe_capabilities", {}, pat["admin"]))
    assert caps["key_rules"]["reserved_field_keys"] == sorted(PSEUDO_FIELDS)
    # Read from the dict, not restated: adding a pseudo-field reserves it here too.
    assert set(caps["key_rules"]["reserved_field_keys"]) == {
        f["key"] for f in caps["system_fields"]
    }
