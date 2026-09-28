"""A workspace frozen to read-only (DD-38), over MCP, and parity with REST.

Every session here is the real streamable HTTP path (``http_session``), so the flag is
read from the app's own settings exactly as a deployment reads it, over a data directory
seeded with the flag off (``tests/test_read_only_mode.py``'s ``seed_workspace``).

A refusal is a **tool result**, never a JSON-RPC error (FR-M6): the model
reads a tool result, and the subscribe URL is only useful if the model reads it. Each
assertion is labelled **measured** or **fence**, as in the REST module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from glosswork.app import create_app
from glosswork.scopes import route_scopes
from tests.mcp_support import http_session, tool_names
from tests.test_mcp_agent_label import WRITE_CALLS
from tests.test_mcp_catalog import ADMIN_TOOLS, READ_TOOLS, SECTION_8_PARITY, WRITE_TOOLS
from tests.test_read_only_mode import (
    READ_ONLY_CODE,
    SUBSCRIBE_URL,
    Seeded,
    Workspace,
    is_read_only_refusal,
    seed_workspace,
    settings_for,
    snapshot,
)
from tests.test_rest_scope_enforcement import _request

# Every write tool, with arguments that succeed against the seeded workspace:
# ``WRITE_CALLS`` from ``tests/test_mcp_agent_label.py`` plus the one write tool that
# appends no audit event and so is not in it (DD-17's exemption).
MCP_WRITE_CALLS: dict[str, Any] = {
    **WRITE_CALLS,
    "create_attachment_upload": lambda s: {"filename": "late.bin"},
}


def read_calls(seeded: Seeded) -> dict[str, dict[str, Any]]:
    """Every tool that only reads, including the two ``admin`` reads, with valid
    arguments. ``search`` asks for keyword mode because embedding is off here."""
    return {
        "list_object_types": {},
        "describe_object_type": {"object_type": "task"},
        "query_records": {"object_type": "task"},
        "get_record": {"record": seeded.plain_a},
        "search": {"query": "A", "mode": "keyword"},
        "list_comments": {"record": seeded.plain_a},
        "get_record_history": {"record": seeded.plain_a},
        "list_changes_since": {},
        "find_principals": {},
        "get_attachment": {"attachment_id": seeded.attachment_id},
        "describe_capabilities": {},
        "list_schema_proposals": {},
        "list_object_type_grants": {"object_type": "task"},
    }


def frozen_app(workspace: Workspace, *, read_only: bool = True) -> Any:
    return create_app(
        settings_for(workspace.data_dir, read_only=read_only, subscribe_url=SUBSCRIBE_URL)
    )


def code_of(result: Any) -> str | None:
    """The envelope code of an error result, or None for a success or an error the SDK
    raised without the project's envelope (an argument rejected by the input schema)."""
    if not result.is_error or result.structured_content is None:
        return None
    error = result.structured_content.get("error")
    return error.get("code") if isinstance(error, dict) else None


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return seed_workspace(tmp_path / "data")


# --------------------------------------------------------------------- writes


@pytest.mark.anyio
@pytest.mark.parametrize("tool", sorted(MCP_WRITE_CALLS))
async def test_every_write_tool_is_refused_and_changes_nothing(
    workspace: Workspace, tool: str
) -> None:
    """**Measured.** Without the gate each call returned ``is_error = False`` and the
    snapshot differed. With the flag, each is a tool error carrying ``workspace_read_only`` and the
    subscribe URL, the tool body never runs, and no row changes, including
    no agent label, since the body is where a label registers."""
    before = snapshot(workspace.data_dir)
    async with http_session(
        frozen_app(workspace), token=workspace.tokens["admin"], agent_label="frozen-bot"
    ) as session:
        result = await session.call_tool(tool, MCP_WRITE_CALLS[tool](workspace.seeded))
    after = snapshot(workspace.data_dir)
    # One comparison, as on REST, so neither half is measured only after the other fails.
    assert (bool(result.is_error), code_of(result), after == before) == (
        True,
        READ_ONLY_CODE,
        True,
    ), tool
    envelope = result.structured_content["error"]
    assert envelope["details"]["subscribe_url"] == SUBSCRIBE_URL
    assert envelope["details"]["attempted"] == tool
    assert SUBSCRIBE_URL in result.content[0].text


@pytest.mark.anyio
@pytest.mark.parametrize("tool", sorted(MCP_WRITE_CALLS))
async def test_every_write_tool_succeeds_with_the_flag_off(workspace: Workspace, tool: str) -> None:
    """**Fence.** The same call succeeds and changes rows with the flag off, so the
    refusal above is a refusal of a real write."""
    before = snapshot(workspace.data_dir)
    async with http_session(
        frozen_app(workspace, read_only=False), token=workspace.tokens["admin"]
    ) as session:
        result = await session.call_tool(tool, MCP_WRITE_CALLS[tool](workspace.seeded))
    after = snapshot(workspace.data_dir)
    # The message is the code alone: ``create_attachment_upload``'s result carries a live
    # upload ticket, and a failing assertion must not print a credential.
    assert not result.is_error, (tool, code_of(result))
    assert after != before, f"{tool} is not a write: it changed no row"


def test_the_read_tools_above_read_scope_are_exactly_the_two_admin_reads() -> None:
    """**Measured** (fails on import without the gate). Pinned by equality: a new
    admin tool is refused while frozen until somebody names it a read here."""
    from glosswork.mcp_server import READ_TOOLS_ABOVE_READ_SCOPE

    assert READ_TOOLS_ABOVE_READ_SCOPE == frozenset(
        {"list_schema_proposals", "list_object_type_grants"}
    )


def test_every_catalog_tool_is_either_a_read_or_a_write_call_here() -> None:
    """**Fence.** The two tables above cover the whole catalog, so a tool added later is
    either refused or not refused by a test, never by nobody."""
    covered = set(MCP_WRITE_CALLS) | set(read_calls(Seeded("", "", "", "", "", "", "", "", "")))
    assert covered == READ_TOOLS | WRITE_TOOLS | ADMIN_TOOLS


# ---------------------------------------------------------------------- reads


@pytest.mark.anyio
async def test_every_read_tool_still_answers(workspace: Workspace) -> None:
    """**Fence.** Every read tool, including the two ``admin`` reads, answers without error
    while frozen. Watched failing against a deliberate mutation."""
    calls = read_calls(workspace.seeded)
    async with http_session(frozen_app(workspace), token=workspace.tokens["admin"]) as session:
        results = {tool: await session.call_tool(tool, args) for tool, args in calls.items()}
    failed = {tool: result for tool, result in results.items() if result.is_error}
    assert failed == {}, failed


@pytest.mark.anyio
async def test_an_attachment_seeded_before_the_freeze_is_still_readable(
    workspace: Workspace,
) -> None:
    """**Fence.** ``resources/read`` is a request method, not a tool, and is not gated."""
    async with http_session(frozen_app(workspace), token=workspace.tokens["admin"]) as session:
        result = await session.read_resource(f"attachment://{workspace.seeded.attachment_id}")
    (content,) = result.contents
    assert content.text == "seeded bytes"


@pytest.mark.anyio
async def test_tools_list_is_the_same_with_the_flag_on_and_off(workspace: Workspace) -> None:
    """**Fence.** Write tools stay listed and are refused on call, because the
    refusal is where the subscribe URL reaches an agent."""
    token = workspace.tokens["admin"]
    async with http_session(frozen_app(workspace), token=token) as session:
        frozen = tool_names(await session.list_tools())
    async with http_session(frozen_app(workspace, read_only=False), token=token) as session:
        thawed = tool_names(await session.list_tools())
    assert frozen == thawed
    assert frozen == READ_TOOLS | WRITE_TOOLS | ADMIN_TOOLS


# --------------------------------------------------------------------- parity


@pytest.mark.anyio
async def test_a_tool_is_refused_exactly_when_its_rest_twin_is(workspace: Workspace) -> None:
    """**Measured.** For every ``docs/MCP_TOOLS.md`` section 8 row, the tool is refused
    while frozen exactly when its REST twin is, and the refused set is the one the rule
    names: every tool above ``read`` except the named admin reads. Both surfaces are
    driven with arguments that could not succeed (placeholders on REST, none on MCP), so
    what is compared is the gate and nothing behind it.

    Without the gate it fails on the import below; with the import satisfied and no gate, both
    refused sets are empty, which the final assertion also catches."""
    from glosswork.mcp_server import READ_TOOLS_ABOVE_READ_SCOPE

    app = frozen_app(workspace)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {workspace.tokens['admin']}"
        entries = {entry.key: entry for entry in route_scopes(app)}
        rest_refused = {
            tool
            for tool, method, path in SECTION_8_PARITY
            if is_read_only_refusal(_request(client, entries[f"{method} {path}"]))
        }
    async with http_session(frozen_app(workspace), token=workspace.tokens["admin"]) as session:
        mcp_refused = {
            tool
            for tool, _, _ in SECTION_8_PARITY
            if code_of(await session.call_tool(tool, {})) == READ_ONLY_CODE
        }
    drift = rest_refused ^ mcp_refused
    assert drift == set(), f"refused on one surface only: {sorted(drift)}"
    expected = {
        tool for tool, _, _ in SECTION_8_PARITY if tool in WRITE_TOOLS or tool in ADMIN_TOOLS
    } - READ_TOOLS_ABOVE_READ_SCOPE
    assert mcp_refused == expected
