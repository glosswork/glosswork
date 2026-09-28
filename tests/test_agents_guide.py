"""Enforces the central claim of docs/AGENT_ONBOARDING.md: an agent
with only MCP access, and no bespoke prompt engineering about the schema, can walk the
guide's discovery path and answer a natural-language-shaped question requiring a
filter over a user-defined field. If the guide's discovery path, its claim that
``search`` never returns a full record body, or its tool-name references ever stop
matching the live server, these tests fail.

No test here imports a tool module (the repo-wide convention, tests/mcp_support.py):
every call goes through a real ``mcp.Client`` session.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from mcp.server import MCPServer

from glosswork.auth import PatTokenResolver
from glosswork.mcp_server import create_mcp_server
from glosswork.services import ServiceBundle
from tests.conftest import make_actor, mint_scope_tokens
from tests.mcp_support import memory_session, structured

# Runs in CI's `guards` job on every pipeline, including ones where the behavioural
# suites are skipped, because a change that touches only documentation can break this.
pytestmark = pytest.mark.structural

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_GUIDE = REPO_ROOT / "docs" / "AGENT_ONBOARDING.md"
MCP_TOOLS_DOC = REPO_ROOT / "docs" / "MCP_TOOLS.md"

RISK_TYPE_KEY = "risk"

# A user-defined single_select plus a date field, per the guide's discovery section.
# The option values, labels, and descriptions below are seeding data (allowed to be
# literal: something has to define the schema); the *test body* below reads every
# one of them back out of describe_object_type instead of repeating them here.
RISK_FIELDS: list[dict[str, Any]] = [
    {
        "key": "title",
        "name": "Title",
        "type": "short_text",
        "description": "Short name of the risk.",
        "required": True,
    },
    {
        "key": "severity",
        "name": "Severity",
        "type": "single_select",
        "description": "How severe this risk is if it materializes.",
        "config": {
            "options": [
                {
                    "value": "low",
                    "label": "Low",
                    "description": "Minor impact, easily absorbed.",
                },
                {
                    "value": "medium",
                    "label": "Medium",
                    "description": "Noticeable impact, manageable with attention.",
                },
                {
                    "value": "high",
                    "label": "High",
                    "description": "Severe impact, requires escalation.",
                },
            ]
        },
    },
    {
        "key": "reviewed_on",
        "name": "Reviewed on",
        "type": "date",
        "description": "Date this risk was last reviewed.",
    },
]


def _seed_risks(services: ServiceBundle) -> dict[str, Any]:
    services.schema.create_object_type(
        make_actor(),
        key=RISK_TYPE_KEY,
        name="Risk",
        name_plural="Risks",
        description="A tracked delivery risk, seeded only for the agent guide test.",
        key_prefix="RSK",
        fields=RISK_FIELDS,
    )
    high_one = services.records.create_record(
        make_actor(),
        RISK_TYPE_KEY,
        {"title": "Vendor exit", "severity": "high", "reviewed_on": "2026-08-01"},
    )
    high_two = services.records.create_record(
        make_actor(), RISK_TYPE_KEY, {"title": "Key-person dependency", "severity": "high"}
    )
    services.records.create_record(
        make_actor(), RISK_TYPE_KEY, {"title": "Minor process drift", "severity": "low"}
    )
    return {"high_keys": {high_one.key, high_two.key}}


@pytest.fixture
def seeded_risks(services: ServiceBundle) -> dict[str, Any]:
    return _seed_risks(services)


@pytest.mark.anyio
async def test_discovery_path_derives_a_filter_with_no_hardcoded_schema_knowledge(
    mcp_server: MCPServer, seeded_risks: dict[str, Any], pat: dict[str, str]
) -> None:
    """Answers "which risks are rated at the most severe level?" by walking exactly
    docs/AGENT_ONBOARDING.md section 3's path (describe_capabilities, list_object_types,
    describe_object_type) in one session, then building a query_records filter.

    Discipline: no field key, option value, or operator is a literal anywhere below
    this docstring. Every one is read out of a tool response. Do not "simplify" this
    by inlining "severity", "high", or "eq": that would defeat the point of the test,
    which is that an agent with zero prior schema knowledge can still ask this
    question correctly.
    """
    async with memory_session(mcp_server, token=pat["read"], agent_label="guide-test") as client:
        # 1. Platform-wide grammar and operator catalog, read once. A `read` token
        # walks the whole path, as the guide says (DD-30, docs/MCP_TOOLS.md section 5.1).
        capabilities = structured(await client.call_tool("describe_capabilities", {}))
        assert capabilities["filter_grammar"]["combinators"] == ["and", "or", "not"]

        # 2. What object types exist? Located by the description this test seeded,
        # never by its key.
        types = structured(await client.call_tool("list_object_types", {}))["object_types"]
        target = next(t for t in types if "agent guide test" in t["description"])
        type_key = target["key"]

        # 3. Its full field list: keys, types, descriptions, and per-field operators.
        describe = structured(
            await client.call_tool("describe_object_type", {"object_type": type_key})
        )

        # The field to filter on: whichever declared field is a single_select. Its
        # key comes from the response, not from a literal "severity".
        select_field = next(f for f in describe["fields"] if f["type"] == "single_select")
        field_key = select_field["key"]

        # The option value to filter by: the last option this field declares. Which
        # option that is is a fact about the seed data above, not about this code;
        # the value itself is read out of the response, not typed as "high".
        chosen_option = select_field["options"][-1]
        option_value = chosen_option["value"]
        assert chosen_option["description"]  # every option is agent-facing (PRD non-negotiable 6)

        # The operator: the first one this field declares. describe_object_type's
        # per-type operator order (docs/MCP_TOOLS.md section 4) puts single-value
        # equality first; the point is this code never writes the operator itself.
        operator = select_field["operators"][0]

        # 4. Build and run the filter from nothing but the values above.
        queried = structured(
            await client.call_tool(
                "query_records",
                {
                    "object_type": type_key,
                    "filter": {"field": field_key, "op": operator, "value": option_value},
                },
            )
        )

    assert {r["key"] for r in queried["records"]} == seeded_risks["high_keys"]
    assert queried["total_count"] == len(seeded_risks["high_keys"])
    for record in queried["records"]:
        assert record["data"][field_key] == option_value


# --------------------------------------------------------- search vs. get_record

BRIEF_TYPE_KEY = "brief"

BRIEF_FIELDS: list[dict[str, Any]] = [
    {
        "key": "title",
        "name": "Title",
        "type": "short_text",
        "description": "Title of the brief.",
        "required": True,
    },
    {
        "key": "summary",
        "name": "Summary",
        "type": "long_text",
        "description": "Body of the brief; searchable free text.",
        "embed": True,
    },
]


def _seed_brief(services: ServiceBundle) -> Any:
    services.schema.create_object_type(
        make_actor(),
        key=BRIEF_TYPE_KEY,
        name="Brief",
        name_plural="Briefs",
        description="A short brief, seeded only for the search-vs-get_record test.",
        key_prefix="BRF",
        fields=BRIEF_FIELDS,
    )
    return services.records.create_record(
        make_actor(),
        BRIEF_TYPE_KEY,
        {
            "title": "Payroll run risk",
            "summary": "Notes on the returns handling backlog and who owns it.",
        },
    )


@pytest.fixture
def brief_search(search_services: ServiceBundle) -> tuple[MCPServer, dict[str, str], Any]:
    """An MCP server over the embedding-enabled ``search_services`` bundle (keyword
    rows are maintained on every write even without a running worker)."""
    record = _seed_brief(search_services)
    server = create_mcp_server(lambda: search_services, PatTokenResolver(lambda: search_services))
    return server, mint_scope_tokens(search_services), record


@pytest.mark.anyio
async def test_search_hits_carry_no_full_record_body_but_get_record_does(
    brief_search: tuple[MCPServer, dict[str, str], Any],
) -> None:
    """Enforces docs/AGENT_ONBOARDING.md section 5's claim: a search hit identifies a record
    and where the match was found, but never carries its data document or version;
    following up means calling get_record with the hit's record_key."""
    server, tokens, record = brief_search
    async with memory_session(server, token=tokens["read"]) as client:
        found = structured(
            await client.call_tool(
                "search", {"query": "returns handling backlog", "mode": "keyword"}
            )
        )["results"]
        assert found and found[0]["record_key"] == record.key
        hit = found[0]
        assert "data" not in hit
        assert "version" not in hit
        assert "comments" not in hit

        fetched = structured(await client.call_tool("get_record", {"record": hit["record_key"]}))

    assert fetched["data"]["summary"] == record.data["summary"]
    assert fetched["version"] == 1


# --------------------------------------------------------- the guide-vs-surface walk

TOOL_HEADER_RE = re.compile(r"^#### `([a-z][a-z0-9_]*)\(", re.MULTILINE)
BACKTICK_SPAN_RE = re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)`")


def _catalog_tool_names() -> set[str]:
    """The tool catalog's own names, parsed from docs/MCP_TOOLS.md section 5's
    ``#### `name(...)` `` headers rather than hardcoded here, so this reference set
    cannot itself drift from the catalog it exists to check the guide against."""
    text = MCP_TOOLS_DOC.read_text()
    return set(TOOL_HEADER_RE.findall(text))


@pytest.mark.anyio
async def test_every_tool_the_guide_mentions_exists_on_the_live_server(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """Parses docs/AGENT_ONBOARDING.md for backticked tool names and asserts every one is
    reachable in a real tools/list for an admin session: the mechanical form of "if
    the guide and the tool surface disagree, the test fails."

    Backtick spans are filtered against the catalog's own tool names (not against
    identifier shape alone), so an ordinary backticked word the guide also uses --
    `date`, `read`, `title`, `key` -- can never produce a false failure: none of
    those are tool names, so they are never in ``known`` to begin with.
    """
    known = _catalog_tool_names()
    # 31 once `get_attachment` and `create_text_attachment` were added (29 before them, 26 and
    # 25 earlier). The `resources/read` entry beside them is deliberately not
    # counted: it is a request method, not a tool, and its header carries no `name(...)` span for
    # TOOL_HEADER_RE to match. The literal is the point: it is what makes an accidentally-dropped
    # tool header a failure here rather than a silently smaller set for the sweep below to match
    # against. It is also why a documentation-only edit can fail this suite -- learned the
    # expensive way, which is why the number is bumped in the same change that adds the headers,
    # not after it.
    assert len(known) == 32, "docs/MCP_TOOLS.md's catalog no longer parses to 32 tool headers"

    guide_text = AGENTS_GUIDE.read_text()
    mentioned = {name for name in BACKTICK_SPAN_RE.findall(guide_text) if name in known}
    assert len(mentioned) >= 5, (
        "docs/AGENT_ONBOARDING.md should reference several real tools by name"
    )

    async with memory_session(mcp_server, token=pat["admin"]) as client:
        live_tools = {tool.name for tool in (await client.list_tools()).tools}

    missing = mentioned - live_tools
    assert not missing, (
        f"docs/AGENT_ONBOARDING.md names tools absent from the live server: {missing}"
    )


def test_agents_guide_states_the_agent_label_is_not_a_security_control() -> None:
    """The one prose claim PRD section 10's risk row requires and that no MCP call
    can exercise directly: guard it with a direct text check so it cannot be quietly
    dropped in an edit."""
    text = AGENTS_GUIDE.read_text().lower()
    assert "never a security control" in text or "not a security control" in text
    assert "descriptive metadata" in text
