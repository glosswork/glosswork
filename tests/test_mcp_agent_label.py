"""Agent labels (FR-M5; FR-I6 registry half) and write attribution (DD-4) over MCP."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from mcp.server import MCPServer
from sqlalchemy import text

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.db import Database
from glosswork.repositories.models import AuditEvent
from glosswork.services import ServiceBundle
from tests.conftest import make_actor, seed_second_principal
from tests.mcp_support import error_of, memory_session, seed_task_type, structured


@dataclass
class Seeded:
    services: ServiceBundle
    plain_a: str  # no links either way
    target_b: str  # linked to from child_c.parent
    child_c: str
    deleted_d: str
    comment_id: str
    second_principal: str  # a real principal, for the two grant tools


@pytest.fixture
def seeded(services: ServiceBundle, db: Database) -> Seeded:
    seed_task_type(services)
    a = services.records.create_record(make_actor(), "task", {"title": "A"})
    b = services.records.create_record(make_actor(), "task", {"title": "B"})
    c = services.records.create_record(make_actor(), "task", {"title": "C"})
    d = services.records.create_record(make_actor(), "task", {"title": "D"})
    services.records.link_records(make_actor(), c.key, "parent", [b.key])
    services.records.delete_record(make_actor(), d.key)
    comment = services.comments.add_comment(make_actor(), a.key, "seed comment")
    second_principal = seed_second_principal(db)
    # A pre-existing grant, so revoke_object_type_grant has a row to remove; set
    # tolerates overwriting it, so both grant tools stay independently callable.
    services.access.grant(make_actor(), "task", second_principal, "read")
    return Seeded(services, a.key, b.key, c.key, d.key, comment.id, second_principal)


def _events_since(services: ServiceBundle, cursor: int) -> list[AuditEvent]:
    return services.changes.list_changes_since(make_actor(), cursor=cursor, limit=1000).events


def _cursor(services: ServiceBundle) -> int:
    return services.changes.list_changes_since(make_actor()).next_cursor


# --------------------------------------------------------------- header default


@pytest.mark.anyio
async def test_connection_header_sets_the_default_label(
    mcp_server: MCPServer, seeded: Seeded, pat: dict[str, str]
) -> None:
    before = _cursor(seeded.services)
    async with memory_session(mcp_server, token=pat["write"], agent_label="planner-bot") as c:
        comment = structured(
            await c.call_tool("add_comment", {"record": seeded.plain_a, "body": "via header"})
        )
    events = _events_since(seeded.services, before)
    assert events
    label_ids = {e.agent_label_id for e in events} | {comment["agent_label_id"]}
    assert len(label_ids) == 1
    label = seeded.services.agent_labels.get_label(label_ids.pop())
    assert label.label == "planner-bot"
    assert label.principal_id == BOOTSTRAP_PRINCIPAL_ID


@pytest.mark.anyio
async def test_per_call_agent_parameter_overrides_the_header(
    mcp_server: MCPServer, seeded: Seeded, pat: dict[str, str]
) -> None:
    before = _cursor(seeded.services)
    async with memory_session(mcp_server, token=pat["write"], agent_label="header-label") as c:
        await c.call_tool(
            "update_record",
            {"record": seeded.plain_a, "values": {"title": "A2"}, "agent": "per-call-label"},
        )
    events = _events_since(seeded.services, before)
    assert events
    labels = {seeded.services.agent_labels.get_label(e.agent_label_id).label for e in events}  # type: ignore[arg-type]
    assert labels == {"per-call-label"}


@pytest.mark.anyio
async def test_no_label_anywhere_means_null_agent_label(
    mcp_server: MCPServer, seeded: Seeded, pat: dict[str, str]
) -> None:
    before = _cursor(seeded.services)
    async with memory_session(mcp_server, token=pat["write"]) as c:
        comment = structured(
            await c.call_tool("add_comment", {"record": seeded.plain_a, "body": "anonymous"})
        )
    assert comment["agent_label_id"] is None
    events = _events_since(seeded.services, before)
    assert events and all(e.agent_label_id is None for e in events)


# ------------------------------------------------------------ auto-registration


@pytest.mark.anyio
async def test_auto_registration_counts_uses_and_is_per_principal(
    mcp_server: MCPServer, seeded: Seeded, db: Database, pat: dict[str, str]
) -> None:
    services = seeded.services
    async with memory_session(mcp_server, token=pat["read"], agent_label="counter") as c:
        await c.call_tool("list_object_types", {})
        first = services.agent_labels.list_labels(BOOTSTRAP_PRINCIPAL_ID)
        assert len(first) == 1
        row = first[0]
        assert row.label == "counter"
        assert row.verified is False
        assert row.call_count == 1
        assert row.first_seen_at == row.last_seen_at

        await c.call_tool("list_object_types", {})
        await c.call_tool("get_record", {"record": seeded.plain_a})
    again = services.agent_labels.get_label(row.id)
    assert again.call_count == 3
    assert again.first_seen_at == row.first_seen_at
    assert again.last_seen_at >= row.last_seen_at

    # The same label string under a second principal is an independent row
    # (the FR-C5 pattern: the principal is inserted directly for the test).
    other_principal = str(uuid.uuid4())
    with db.write() as conn:
        conn.execute(
            text(
                "INSERT INTO principals (id, type, display_name, email, is_active, created_at) "
                "VALUES (:id, 'user', 'Other', 'other@example.com', 1, '2026-01-01T00:00:00Z')"
            ),
            {"id": other_principal},
        )
    other = services.agent_labels.register_use(other_principal, "counter")
    assert other.id != row.id
    assert other.call_count == 1
    assert services.agent_labels.get_label(row.id).call_count == 3


@pytest.mark.anyio
async def test_unknown_label_is_never_rejected(
    mcp_server: MCPServer, seeded: Seeded, pat: dict[str, str]
) -> None:
    weird = "Totally Unknown Agent v0.0.1 (typo'd)"
    async with memory_session(mcp_server, token=pat["write"], agent_label=weird) as c:
        result = await c.call_tool("add_comment", {"record": seeded.plain_a, "body": "ok"})
    assert not result.is_error
    labels = [row.label for row in seeded.services.agent_labels.list_labels()]
    assert labels == [weird]


# ------------------------------------------------------------------ attribution

# Every write tool of docs/MCP_TOOLS.md sections 5.2 and 5.3, with the arguments
# that make it succeed against the ``seeded`` fixture.
WRITE_CALLS: dict[str, Callable[[Seeded], dict[str, Any]]] = {
    "create_record": lambda s: {"object_type": "task", "values": {"title": "new"}},
    "update_record": lambda s: {"record": s.plain_a, "values": {"title": "changed"}},
    "delete_record": lambda s: {"record": s.plain_a},
    "restore_record": lambda s: {"record": s.deleted_d},
    "bulk_update_records": lambda s: {
        "object_type": "task",
        "filter": {"field": "title", "op": "eq", "value": "A"},
        "values": {"status": "doing"},
    },
    "link_records": lambda s: {
        "from_record": s.plain_a,
        "field_key": "parent",
        "to_records": [s.target_b],
    },
    "unlink_records": lambda s: {
        "from_record": s.child_c,
        "field_key": "parent",
        "to_records": [s.target_b],
    },
    "add_comment": lambda s: {"record": s.plain_a, "body": "attributed"},
    "update_comment": lambda s: {"comment_id": s.comment_id, "body": "edited"},
    "delete_comment": lambda s: {"comment_id": s.comment_id},
    "create_object_type": lambda s: {
        "key": "note",
        "name": "Note",
        "name_plural": "Notes",
        "description": "A free-form note attached to nothing in particular.",
        "key_prefix": "NOTE",
    },
    "update_object_type": lambda s: {"object_type": "task", "changes": {"name": "Job"}},
    "add_field": lambda s: {
        "object_type": "task",
        "key": "effort",
        "name": "Effort",
        "type": "integer",
        "description": "Estimated effort in ideal days.",
    },
    "update_field": lambda s: {
        "object_type": "task",
        "field_key": "notes",
        "changes": {"description": "Working notes, rewritten."},
    },
    "propose_schema_change": lambda s: {
        "change_type": "delete_field",
        "object_type": "task",
        "field_key": "due",
        "reason": "Nobody fills it in.",
    },
    "create_text_attachment": lambda s: {"filename": "note.txt", "text": "hello"},
    "set_object_type_grant": lambda s: {
        "object_type": "task",
        "principal_id": s.second_principal,
        "level": "write",
    },
    "revoke_object_type_grant": lambda s: {
        "object_type": "task",
        "principal_id": s.second_principal,
    },
}


@pytest.mark.anyio
@pytest.mark.parametrize("tool", sorted(WRITE_CALLS))
async def test_every_mcp_write_is_attributed(
    mcp_server: MCPServer, seeded: Seeded, tool: str, pat: dict[str, str]
) -> None:
    services = seeded.services
    before = _cursor(services)
    async with memory_session(mcp_server, token=pat["admin"], agent_label="auditor") as c:
        result = await c.call_tool(tool, WRITE_CALLS[tool](seeded))
    assert not result.is_error, error_of(result) if result.is_error else None
    events = _events_since(services, before)
    assert events, f"{tool} produced no audit rows"
    request_ids = {e.request_id for e in events}
    assert len(request_ids) == 1, "one tool call is one request id"
    for event in events:
        assert event.surface == "mcp"
        assert event.auth_method == "pat"
        assert event.principal_id == BOOTSTRAP_PRINCIPAL_ID
        assert event.agent_label_id is not None
        assert services.agent_labels.get_label(event.agent_label_id).label == "auditor"


@pytest.mark.anyio
@pytest.mark.parametrize("tool", sorted(WRITE_CALLS))
async def test_every_mcp_write_honors_the_per_call_override(
    mcp_server: MCPServer, seeded: Seeded, tool: str, pat: dict[str, str]
) -> None:
    """Every tool that appends an audit event accepts ``agent``, and a per-call value
    beats the connection header on every row the call appends. Fails for any such tool
    that lacks the parameter: the call is refused before it runs."""
    services = seeded.services
    before = _cursor(services)
    args = {**WRITE_CALLS[tool](seeded), "agent": "per-call-label"}
    async with memory_session(mcp_server, token=pat["admin"], agent_label="header-label") as c:
        result = await c.call_tool(tool, args)
    assert not result.is_error, error_of(result) if result.is_error else None
    events = _events_since(services, before)
    assert events, f"{tool} produced no audit rows"
    labels = {services.agent_labels.get_label(e.agent_label_id).label for e in events}  # type: ignore[arg-type]
    assert labels == {"per-call-label"}, tool


# ------------------------------------------------------ the token's own label

TOKEN_LABEL = "token-borne-bot"


def _labeled_pat(services: ServiceBundle, label: str = TOKEN_LABEL) -> str:
    """A PAT minted for one named tool, carrying that tool's agent label."""
    return services.tokens.mint(
        make_actor(), name=f"for-{label}", scope="admin", agent_label=label
    ).plaintext


@pytest.mark.anyio
async def test_token_label_attributes_an_unlabeled_mcp_call(
    mcp_server: MCPServer, seeded: Seeded
) -> None:
    """The MCP half of the same property: a connection that sends no header and no
    per-call ``agent`` is still attributed, because its credential carries the label."""
    services = seeded.services
    # Minted before the cursor: the mint appends its own unlabeled
    # ``access_token:create`` event, which would otherwise sit inside the window below.
    token = _labeled_pat(services)
    before = _cursor(services)
    async with memory_session(mcp_server, token=token) as c:
        comment = structured(
            await c.call_tool("add_comment", {"record": seeded.plain_a, "body": "via token label"})
        )
    events = _events_since(services, before)
    assert events
    label_ids = {e.agent_label_id for e in events} | {comment["agent_label_id"]}
    assert None not in label_ids, "the token's own label left a row unattributed"
    assert len(label_ids) == 1
    assert services.agent_labels.get_label(label_ids.pop()).label == TOKEN_LABEL


@pytest.mark.anyio
async def test_the_header_overrides_the_token_label_on_mcp(
    mcp_server: MCPServer, seeded: Seeded
) -> None:
    services = seeded.services
    token = _labeled_pat(services)
    before = _cursor(services)
    async with memory_session(mcp_server, token=token, agent_label="header-label") as c:
        await c.call_tool("add_comment", {"record": seeded.plain_a, "body": "header wins"})
    events = _events_since(services, before)
    assert events
    labels = {services.agent_labels.get_label(e.agent_label_id).label for e in events}  # type: ignore[arg-type]
    assert labels == {"header-label"}


@pytest.mark.anyio
async def test_precedence_the_per_call_agent_beats_the_header_and_the_token_label(
    mcp_server: MCPServer, seeded: Seeded
) -> None:
    """All three sources present on one call, which is the only place the whole FR-M5
    precedence rule is measurable at once."""
    services = seeded.services
    token = _labeled_pat(services)
    before = _cursor(services)
    async with memory_session(mcp_server, token=token, agent_label="header-label") as c:
        await c.call_tool(
            "update_record",
            {"record": seeded.plain_a, "values": {"title": "A3"}, "agent": "per-call-label"},
        )
    events = _events_since(services, before)
    assert events
    labels = {services.agent_labels.get_label(e.agent_label_id).label for e in events}  # type: ignore[arg-type]
    assert labels == {"per-call-label"}


@pytest.mark.anyio
async def test_each_tool_call_gets_a_fresh_request_id(
    mcp_server: MCPServer, seeded: Seeded, pat: dict[str, str]
) -> None:
    services = seeded.services
    before = _cursor(services)
    async with memory_session(mcp_server, token=pat["write"], agent_label="x") as c:
        await c.call_tool("add_comment", {"record": seeded.plain_a, "body": "one"})
        await c.call_tool("add_comment", {"record": seeded.plain_a, "body": "two"})
    events = _events_since(services, before)
    assert len({e.request_id for e in events}) == 2
