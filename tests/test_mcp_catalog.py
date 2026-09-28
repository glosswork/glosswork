"""The MCP protocol surface and catalog, adapter discipline, and the section 8 parity
walk.

Every assertion about what a client sees goes through a real ``mcp.Client``
session; the structural checks are grep-backed over the source tree.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mcp.server import MCPServer

from glosswork.mcp_server import INSTRUCTIONS, INSTRUCTIONS_BUDGET
from tests.mcp_support import memory_session, tool_names

_ROOT = Path(__file__).resolve().parents[1]
_MCP_PACKAGE = _ROOT / "src" / "glosswork" / "mcp_server"
_TESTS_DIR = Path(__file__).resolve().parent

# docs/MCP_TOOLS.md section 5: tool -> (required parameter names, optional parameter
# names). ``propose_schema_change`` takes ``object_type``
# and ``field_key`` in place of the doc's original ``target`` (recorded in 5.3).
# ``list_changes_since.cursor`` is optional: 5.1 states that passing no cursor
# returns the current cursor, so the doc's unmarked ``cursor`` cannot be required.
CATALOG: dict[str, tuple[set[str], set[str]]] = {
    # 5.1 discovery and read
    "list_object_types": (set(), set()),
    "describe_object_type": ({"object_type"}, {"include_samples"}),
    "query_records": (
        {"object_type"},
        {"filter", "sort", "limit", "cursor", "fields", "expand_relations", "include_deleted"},
    ),
    "get_record": ({"record"}, {"include"}),
    "search": ({"query"}, {"object_types", "filter", "mode", "limit"}),
    "list_comments": ({"record"}, {"limit", "cursor"}),
    "get_record_history": ({"record"}, {"field_key", "limit", "cursor"}),
    "list_changes_since": (set(), {"cursor", "object_types", "limit"}),
    # The directory, over the same service method as
    # GET /api/v1/principals/directory. Every parameter is optional -- calling it with
    # nothing is "who exists".
    "find_principals": (set(), {"query", "type", "include_inactive", "limit"}),
    # DD-29: metadata plus a resource link. The bytes are read through
    # resources/read, which is not a tool and so is not in this table.
    "get_attachment": ({"attachment_id"}, set()),
    # 5.2 write
    "create_record": ({"object_type", "values"}, {"agent"}),
    "update_record": ({"record", "values"}, {"expected_version", "force", "agent"}),
    "delete_record": ({"record"}, {"force", "agent"}),
    "restore_record": ({"record"}, {"agent"}),
    "bulk_update_records": ({"object_type", "filter", "values"}, {"dry_run", "agent"}),
    "link_records": ({"from_record", "field_key", "to_records"}, {"agent"}),
    "unlink_records": ({"from_record", "field_key", "to_records"}, {"agent"}),
    "add_comment": ({"record", "body"}, {"agent"}),
    "update_comment": ({"comment_id", "body"}, {"agent"}),
    "delete_comment": ({"comment_id"}, {"agent"}),
    # DD-29: the one payload allowed inside a tool call, because the
    # model had to emit those characters anyway. Binary goes to REST multipart.
    "create_text_attachment": ({"filename", "text"}, {"content_type", "agent"}),
    # DD-16: mints a single-use credential for the one thing an agent
    # cannot do through a tool call. No ``agent`` parameter: it writes no record and
    # appends no audit event a label would attribute.
    "create_attachment_upload": ({"filename"}, {"content_type"}),
    # 5.3 schema administration
    "create_object_type": (
        {"key", "name", "name_plural", "description", "key_prefix"},
        # `display_field_key` is optional and defaults server-side to the first
        # display-eligible field.
        {"fields", "display_field_key", "agent"},
    ),
    "update_object_type": ({"object_type", "changes"}, {"agent"}),
    "add_field": (
        {"object_type", "key", "name", "type", "description"},
        {"config", "required", "unique", "indexed", "embed", "default", "agent"},
    ),
    "update_field": ({"object_type", "field_key", "changes"}, {"agent"}),
    "propose_schema_change": (
        {"change_type", "object_type"},
        {"field_key", "payload", "reason", "agent"},
    ),
    # DD-18: the list is a bounded page, so it takes the same `limit`/`cursor`
    # pair every other paged tool takes.
    "list_schema_proposals": (set(), {"status", "limit", "cursor"}),
    "describe_capabilities": (set(), set()),
    # DD-11: the grant surface, over the same three service methods as
    # the three REST routes. `principal_id` is an id, never a name (DD-11).
    "list_object_type_grants": ({"object_type"}, set()),
    "set_object_type_grant": ({"object_type", "principal_id", "level"}, {"agent"}),
    "revoke_object_type_grant": ({"object_type", "principal_id"}, {"agent"}),
}

READ_TOOLS = {
    "list_object_types",
    "describe_object_type",
    "query_records",
    "get_record",
    "search",
    "list_comments",
    "get_record_history",
    "list_changes_since",
    "find_principals",
    "get_attachment",
    # DD-30: the manual is at ``read`` scope, because the agent that
    # has to act on it holds ``write`` and the document is static platform
    # description that reads no principal, token, grant or record.
    "describe_capabilities",
}
WRITE_TOOLS = {
    "create_record",
    "update_record",
    "delete_record",
    "restore_record",
    "bulk_update_records",
    "link_records",
    "unlink_records",
    "add_comment",
    "update_comment",
    "delete_comment",
    "create_text_attachment",
    "create_attachment_upload",
}
ADMIN_TOOLS = {
    "create_object_type",
    "update_object_type",
    "add_field",
    "update_field",
    "propose_schema_change",
    "list_schema_proposals",
    "list_object_type_grants",
    "set_object_type_grant",
    "revoke_object_type_grant",
}

# docs/MCP_TOOLS.md section 8: MCP tool -> REST route(s), minus the UI-only rows
# (approval, audit, revert, index status, re-index), which must NOT be tools.
SECTION_8_PARITY: list[tuple[str, str, str]] = [
    ("list_object_types", "GET", "/api/v1/object-types"),
    ("describe_object_type", "GET", "/api/v1/object-types/{key}"),
    ("query_records", "POST", "/api/v1/object-types/{object_type_key}/query"),
    ("get_record", "GET", "/api/v1/records/{ref}"),
    ("create_record", "POST", "/api/v1/object-types/{object_type_key}/records"),
    ("update_record", "PATCH", "/api/v1/records/{ref}"),
    ("delete_record", "DELETE", "/api/v1/records/{ref}"),
    ("bulk_update_records", "POST", "/api/v1/object-types/{object_type_key}/bulk-update"),
    ("link_records", "POST", "/api/v1/records/{ref}/links/{field_key}"),
    ("unlink_records", "DELETE", "/api/v1/records/{ref}/links/{field_key}"),
    ("add_comment", "POST", "/api/v1/records/{ref}/comments"),
    ("update_comment", "PATCH", "/api/v1/comments/{comment_id}"),
    ("delete_comment", "DELETE", "/api/v1/comments/{comment_id}"),
    ("search", "POST", "/api/v1/search"),
    ("get_record_history", "GET", "/api/v1/records/{ref}/history"),
    ("list_changes_since", "GET", "/api/v1/changes"),
    # One service method, two surfaces, one projection.
    ("find_principals", "GET", "/api/v1/principals/directory"),
    ("add_field", "POST", "/api/v1/object-types/{key}/fields"),
    ("update_field", "PATCH", "/api/v1/object-types/{key}/fields/{field_key}"),
    ("propose_schema_change", "POST", "/api/v1/schema-proposals"),
    ("list_schema_proposals", "GET", "/api/v1/schema-proposals"),
    # The three grant rows, which DD-11 puts on both surfaces rather than on REST and the
    # CLI only.
    ("list_object_type_grants", "GET", "/api/v1/object-types/{key}/grants"),
    ("set_object_type_grant", "PUT", "/api/v1/object-types/{key}/grants/{principal_id}"),
    ("revoke_object_type_grant", "DELETE", "/api/v1/object-types/{key}/grants/{principal_id}"),
    # DD-29: the two attachment rows section 8 once omitted entirely, neither as parity nor
    # as a declared exception. The third attachment route, GET
    # /api/v1/attachments/{attachment_id}/download, has no tool by design -- its counterpart
    # is the resources/read template, which is a request method rather than a tool -- and
    # section 8 records it as such.
    ("get_attachment", "GET", "/api/v1/attachments/{attachment_id}"),
    ("create_text_attachment", "POST", "/api/v1/attachments"),
    # Four rows the table was once missing, found the moment the
    # walk stopped iterating only the rows it was handed and started asserting the
    # catalog is covered. Every one has had a REST twin all along; none was a declared
    # exception. That is the same class of gap DD-29 complained of, and it is why
    # MCP_ONLY_TOOLS exists rather than an informal understanding.
    ("create_object_type", "POST", "/api/v1/object-types"),
    ("update_object_type", "PATCH", "/api/v1/object-types/{key}"),
    ("restore_record", "POST", "/api/v1/records/{ref}/restore"),
    ("list_comments", "GET", "/api/v1/records/{ref}/comments"),
]

# The two tools that are deliberately MCP-only, declared rather than
# omitted. ``test_section_8_parity_walk`` iterates only the rows it is given, so before
# this set existed a tool absent from the table was silently unchecked -- the same class
# of gap DD-29 found when three attachment routes were missing from a table whose whole
# purpose is to make drift visible.
#
# ``describe_capabilities`` describes the platform, so there is nothing for a REST twin
# to serve that ``/openapi.json`` does not. ``create_attachment_upload`` mints a
# credential for the upload route; a REST caller already holds a PAT that reaches that
# route, so a twin would exist to serve nobody (DD-16).
MCP_ONLY_TOOLS = {"describe_capabilities", "create_attachment_upload"}


# ------------------------------------------------------------------ discipline


def test_no_test_module_imports_the_mcp_tool_modules() -> None:
    """Test discipline: every assertion goes through Client.list_tools /
    Client.call_tool / Client.read_resource, never a tool function and never the
    server subclass. The permitted imports from the package are the server factory,
    so a server can be built against a stub resolver (DD-8), and ``GATED_METHODS``,
    so the credential gate's method set can be pinned by equality.

    ``GlossworkMcpServer`` is named here rather than covered by the module pattern:
    that pattern is anchored on ``glosswork.mcp_server.`` **with the trailing
    dot**, so ``from glosswork.mcp_server import GlossworkMcpServer`` would pass
    it exactly as the permitted factory import does. Driving the subclass directly
    would bypass the middleware that gates every resource read.
    """
    offenders = []
    for path in _TESTS_DIR.glob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if re.match(r"\s*(from|import)\s+glosswork\.mcp_server\.", line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
            elif re.match(r"\s*(from|import)\s+glosswork\b.*\bGlossworkMcpServer\b", line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, f"tests reach into the MCP package directly: {offenders}"


def test_mcp_package_imports_no_repository_sqlalchemy_or_http_client() -> None:
    """Adapter discipline (DD-3, DD-2): the MCP package calls the services
    directly. It never reaches below them (repositories, SQLAlchemy) and never
    calls its own REST API (httpx, httpx2, TestClient)."""
    forbidden = re.compile(
        r"^\s*(from|import)\s+("
        r"glosswork\.repositories|sqlalchemy|httpx|httpx2|fastapi\.testclient"
        r"|starlette\.testclient)\b"
    )
    offenders = []
    for path in _MCP_PACKAGE.glob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if forbidden.match(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, offenders


def test_mcp_package_contains_no_raw_sql() -> None:
    source = "\n".join(p.read_text() for p in _MCP_PACKAGE.glob("*.py"))
    assert "json_extract" not in source
    assert not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\s+\w+\s+(FROM|INTO|SET)\b", source)


# ---------------------------------------------------------------------- catalog


@pytest.mark.anyio
async def test_catalog_is_complete_with_exactly_the_documented_parameters(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["admin"]) as client:
        listing = await client.list_tools()
    by_name = {tool.name: tool for tool in listing.tools}
    assert set(by_name) == set(CATALOG), (
        f"missing: {set(CATALOG) - set(by_name)}; extra: {set(by_name) - set(CATALOG)}"
    )
    for name, (required, optional) in CATALOG.items():
        schema = by_name[name].input_schema
        assert set(schema.get("properties", {})) == required | optional, name
        assert set(schema.get("required", [])) == required, name


@pytest.mark.anyio
async def test_every_tool_and_parameter_has_a_description(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["admin"]) as client:
        listing = await client.list_tools()
    for tool in listing.tools:
        assert tool.description and tool.description.strip(), tool.name
        for param, spec in tool.input_schema.get("properties", {}).items():
            assert spec.get("description", "").strip(), f"{tool.name}.{param}"


@pytest.mark.anyio
async def test_key_descriptions_say_what_the_checklist_requires(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["admin"]) as client:
        listing = await client.list_tools()
    by_name = {tool.name: tool for tool in listing.tools}

    # ``create_attachment_upload`` is the one write tool with no ``agent``
    # parameter, and deliberately so -- it writes no record and appends no audit event
    # for a label to attribute. The upload it enables does, through whatever credential
    # the agent presents on that HTTP request.
    for name in WRITE_TOOLS - {"create_attachment_upload"}:
        agent = by_name[name].input_schema["properties"]["agent"]["description"].lower()
        assert "not a security boundary" in agent, name
        assert "metadata" in agent, name

    bulk = by_name["bulk_update_records"].description.lower()
    assert "dry-run first" in bulk or "dry_run=true" in bulk

    update = by_name["update_record"].description.lower()
    assert "expected_version" in update
    assert "version_conflict" in update
    assert "force" in update

    describe = by_name["describe_object_type"].description.lower()
    assert "orientation tool" in describe


@pytest.mark.anyio
async def test_every_audit_appending_tool_publishes_an_agent_property(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """The rule made structural: a tool that appends an audit event accepts ``agent``. Read
    from a real ``tools/list`` rather than from the documented ``CATALOG`` table, so a write
    tool shipped without the parameter fails here even before ``CATALOG`` is updated to
    expect it -- and the exempt set is computed from the live schema and pinned by equality,
    so quietly widening it is also caught, not merely a name someone agreed to
    (``create_attachment_upload`` writes no record and appends no audit event a label would
    attribute)."""
    async with memory_session(mcp_server, token=pat["admin"]) as client:
        listing = await client.list_tools()
    by_name = {tool.name: tool for tool in listing.tools}

    # The two admin reads take no ``agent`` by the same rule every other read tool
    # follows: they append no audit rows.
    admin_write_tools = ADMIN_TOOLS - {"list_schema_proposals", "list_object_type_grants"}
    audit_appending_tools = WRITE_TOOLS | admin_write_tools

    missing_agent = {
        name
        for name in audit_appending_tools
        if "agent" not in by_name[name].input_schema.get("properties", {})
    }
    assert missing_agent == {"create_attachment_upload"}, missing_agent


@pytest.mark.anyio
async def test_server_instructions_carry_the_orientation_pattern(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["read"]) as client:
        instructions = client.instructions
    assert instructions is not None
    first = instructions.index("list_object_types")
    second = instructions.index("describe_object_type", first)
    third = instructions.index("query_records", second)
    assert first < second < third
    assert "name the tool to call next" in instructions


def test_instructions_fit_the_delivery_budget() -> None:
    """DD-30. ``instructions`` arrives through a window whose size the server does not
    control and cannot detect: one host delivered exactly 2,048 characters and cut the
    string mid-word. Anything load-bearing past
    the budget is not shipped, so the budget is asserted rather than hoped for.

    This is the assertion that was missing, not merely failing: the two tests that read
    ``instructions`` before this one asserted substrings were *present*, which stays green
    while the delivered copy is cut in half.
    """
    assert len(INSTRUCTIONS) <= INSTRUCTIONS_BUDGET, (
        f"INSTRUCTIONS is {len(INSTRUCTIONS)} characters, over the "
        f"{INSTRUCTIONS_BUDGET}-character delivery budget. Move detail into "
        "describe_capabilities rather than raising the budget (DD-30)."
    )


def test_instructions_name_the_manual_and_the_upload_tool() -> None:
    """DD-30 makes ``instructions`` an *index*: its job is to name the tool that holds
    the manual, and the one tool an agent cannot guess at."""
    assert "describe_capabilities" in INSTRUCTIONS
    assert "create_attachment_upload" in INSTRUCTIONS


@pytest.mark.anyio
async def test_no_approval_tool_at_any_scope(mcp_server: MCPServer, pat: dict[str, str]) -> None:
    async with memory_session(mcp_server, token=pat["admin"]) as client:
        names = tool_names(await client.list_tools())
        assert not {n for n in names if "approve" in n or "reject" in n}
        result = await client.call_tool("approve_schema_change", {"proposal_id": "x"})
    assert result.is_error
    assert "unknown tool" in result.content[0].text.lower()  # type: ignore[union-attr]


# ------------------------------------------------------------------ parity walk


@pytest.mark.anyio
async def test_section_8_parity_walk(
    mcp_server: MCPServer, client: TestClient, pat: dict[str, str]
) -> None:
    """Every section 8 row names a registered tool AND a registered REST route, so
    a tool cannot be added to one surface and forgotten on the other (FR-A1)."""
    async with memory_session(mcp_server, token=pat["admin"]) as session:
        registered_tools = tool_names(await session.list_tools())
    spec = client.get("/openapi.json").json()
    routes = {(m.upper(), path) for path, ops in spec["paths"].items() for m in ops}
    for tool, method, path in SECTION_8_PARITY:
        assert tool in registered_tools, tool
        assert (method, path) in routes, (tool, method, path)
    assert "search" in registered_tools  # the catalog's 25th tool
    assert ("POST", "/api/v1/schema-proposals/{proposal_id}/approve") in routes  # UI/REST only
    # The search-index rows that are REST/UI-only by design (docs/MCP_TOOLS.md section 8): an
    # agent reads index_lag on every search response instead.
    assert ("GET", "/api/v1/admin/search-index") in routes
    assert ("POST", "/api/v1/admin/search-index/reindex") in routes
    assert not {name for name in registered_tools if "index" in name}
    # Every registered tool is either a parity row or a declared
    # MCP-only exception. Without this, a tool added to neither is unchecked.
    covered = {tool for tool, _, _ in SECTION_8_PARITY} | MCP_ONLY_TOOLS
    assert registered_tools <= covered, (
        "tools in neither SECTION_8_PARITY nor MCP_ONLY_TOOLS: "
        f"{sorted(registered_tools - covered)}"
    )
    assert MCP_ONLY_TOOLS <= registered_tools
    assert not (MCP_ONLY_TOOLS & {tool for tool, _, _ in SECTION_8_PARITY})
