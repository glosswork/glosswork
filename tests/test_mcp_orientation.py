"""An MCP client that knows nothing about the schema orients itself with
``list_object_types`` and ``describe_object_type`` alone, then creates, queries,
comments, and reads history.

The orientation test body references no fixture field key as a literal: every key,
option value, and operator it sends came out of ``describe_object_type``.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server import MCPServer

from glosswork.services import ServiceBundle
from tests.conftest import make_actor
from tests.mcp_support import TASK_FIELD_KEYS, memory_session, seed_task_type, structured


@pytest.fixture
def seeded(services: ServiceBundle) -> ServiceBundle:
    seed_task_type(services)
    return services


def _field_of_type(describe: dict[str, Any], field_type: str) -> dict[str, Any]:
    return next(f for f in describe["fields"] if f["type"] == field_type)


@pytest.mark.anyio
async def test_orientation_from_discovery_alone(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["write"], agent_label="orienter") as c:
        # 1. What exists?
        types = structured(await c.call_tool("list_object_types", {}))["object_types"]
        assert len(types) == 1
        type_key = types[0]["key"]
        assert types[0]["description"]

        # 2. What does it look like? Everything below comes from this document.
        describe = structured(await c.call_tool("describe_object_type", {"object_type": type_key}))
        required_text = next(
            f for f in describe["fields"] if f["required"] and f["type"] == "short_text"
        )
        select = _field_of_type(describe, "single_select")
        option = select["options"][1]
        assert option["description"]
        operator = next(op for op in select["operators"] if op == "eq")
        assert operator in select["operators"]

        # 3. Create using only derived keys and an option value.
        created = structured(
            await c.call_tool(
                "create_record",
                {
                    "object_type": type_key,
                    "values": {
                        required_text["key"]: "Learn the schema",
                        select["key"]: option["value"],
                    },
                },
            )
        )
        assert created["key"].startswith(describe["key_prefix"] + "-")
        assert created["version"] == 1

        # 4. Query it back by the user-defined single_select field.
        queried = structured(
            await c.call_tool(
                "query_records",
                {
                    "object_type": type_key,
                    "filter": {"field": select["key"], "op": operator, "value": option["value"]},
                },
            )
        )
        assert [r["key"] for r in queried["records"]] == [created["key"]]
        assert queried["records"][0]["data"][select["key"]] == option["value"]

        # 5. Comment on it.
        comment = structured(
            await c.call_tool(
                "add_comment", {"record": created["key"], "body": "Oriented without a human."}
            )
        )
        assert comment["record_id"] == created["id"]

        # 6. Read its audit history: the field write and the comment, attributed.
        history = structured(await c.call_tool("get_record_history", {"record": created["key"]}))
    events = history["events"]
    field_writes = [e for e in events if e["field_key"] == select["key"]]
    assert field_writes and field_writes[0]["new_value"] == option["value"]
    comment_events = [e for e in events if e["entity_type"] == "comment"]
    assert comment_events and comment_events[0]["new_value"] == "Oriented without a human."
    assert {e["surface"] for e in events} == {"mcp"}
    label_ids = {e["agent_label_id"] for e in events}
    assert len(label_ids) == 1
    label = seeded.agent_labels.get_label(label_ids.pop())
    assert label.label == "orienter"


@pytest.mark.anyio
async def test_describe_object_type_returns_every_documented_element(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    seeded.records.create_record(
        make_actor(), "task", {"title": "Sample one", "status": "todo", "due": "2026-09-01"}
    )
    seeded.records.create_record(make_actor(), "task", {"title": "Sample two", "status": "done"})
    async with memory_session(mcp_server, token=pat["read"]) as c:
        plain = structured(await c.call_tool("describe_object_type", {"object_type": "task"}))
        sampled = structured(
            await c.call_tool(
                "describe_object_type", {"object_type": "task", "include_samples": True}
            )
        )

    for key in ("key", "name", "description", "key_prefix", "record_count"):
        assert key in plain, key
    assert plain["record_count"] == 2
    assert plain["field_count"] == len(TASK_FIELD_KEYS)

    per_field = {"key", "name", "type", "description", "required", "unique", "indexed", "default"}
    for field in plain["fields"]:
        assert per_field <= set(field), field["key"]
        assert field["operators"], field["key"]
        assert "samples" not in field

    status = _field_of_type(plain, "single_select")
    assert [o["value"] for o in status["options"]] == ["todo", "doing", "done"]
    assert all(o["description"] for o in status["options"])
    assert status["operators"] == ["eq", "neq", "in", "not_in", "is_null", "is_not_null"]

    parent = _field_of_type(plain, "relation")
    assert parent["target_type_key"] == "task"
    assert parent["cardinality"] == "one"
    assert parent["inverse_field_key"] == "children"
    assert parent["operators"] == [
        "linked_to",
        "linked_to_any",
        "has_links",
        "has_no_links",
        "is_null",
        "is_not_null",
    ]

    system = {f["key"]: f for f in plain["system_fields"]}
    assert set(system) == {
        "key",
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
        "deleted_at",
        "comment_count",
        "last_comment_at",
    }
    assert all(f["operators"] for f in system.values())

    samples = {f["key"]: f["samples"] for f in sampled["fields"]}
    assert set(samples["title"]) == {"Sample one", "Sample two"}
    assert set(samples["status"]) == {"todo", "done"}
    assert samples["due"] == ["2026-09-01"]
    assert samples["notes"] == []  # no data
    assert samples["parent"] == []  # relations hold no stored value
    assert all(len(v) <= 3 for v in samples.values())


@pytest.mark.anyio
async def test_include_samples_caps_at_three_distinct_values(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    for i in range(6):
        seeded.records.create_record(make_actor(), "task", {"title": f"T{i % 4}"})
    async with memory_session(mcp_server, token=pat["read"]) as c:
        doc = structured(
            await c.call_tool(
                "describe_object_type", {"object_type": "task", "include_samples": True}
            )
        )
    titles = next(f["samples"] for f in doc["fields"] if f["key"] == "title")
    assert len(titles) == 3
    assert len(set(titles)) == 3


@pytest.mark.anyio
async def test_list_object_types_shape(
    mcp_server: MCPServer, seeded: ServiceBundle, pat: dict[str, str]
) -> None:
    seeded.records.create_record(make_actor(), "task", {"title": "one"})
    async with memory_session(mcp_server, token=pat["read"]) as c:
        types = structured(await c.call_tool("list_object_types", {}))["object_types"]
    assert len(types) == 1
    assert set(types[0]) == {
        "key",
        "name",
        "description",
        "key_prefix",
        "record_count",
        "field_count",
        # What makes the MCP surface honest under the rule that the tool catalog stays a
        # pure function of credential scope: this token is `read`-scoped, so the ceiling
        # reports `read` however the grant reads.
        "your_access",
    }
    assert types[0]["your_access"] == "read"
    assert types[0]["record_count"] == 1
    assert types[0]["field_count"] == len(TASK_FIELD_KEYS)
