"""MCP scope visibility and gating (FR-M4, DD-8). Listing is asserted as set equality:
hidden tools are omitted, not refused."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from mcp.server import MCPServer
from mcp.types import INVALID_REQUEST

from glosswork.auth import FixedScopeResolver, PatTokenResolver, TokenRefusedError
from glosswork.mcp_server import create_mcp_server
from glosswork.services import ServiceBundle
from tests.conftest import make_actor
from tests.mcp_support import (
    error_of,
    memory_session,
    raw_jsonrpc,
    refusal_opening,
    seed_task_type,
    structured,
    tool_names,
)
from tests.test_mcp_catalog import ADMIN_TOOLS, READ_TOOLS, WRITE_TOOLS

# ``describe_capabilities`` is not a member: it mutates nothing and never did. It reads no
# principal, token, grant or record, and returns the platform's own static description.
# It was once in this set only because it was registered at ``admin`` scope, which is the
# thing DD-30 corrects.
SCHEMA_MUTATION_TOOLS = {
    "create_object_type",
    "update_object_type",
    "add_field",
    "update_field",
    "propose_schema_change",
    "list_schema_proposals",
}


@pytest.mark.anyio
async def test_read_token_sees_exactly_the_read_set(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["read"]) as client:
        assert tool_names(await client.list_tools()) == READ_TOOLS


@pytest.mark.anyio
async def test_write_token_sees_read_plus_write_and_no_schema_mutation(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["write"]) as client:
        names = tool_names(await client.list_tools())
    assert names == READ_TOOLS | WRITE_TOOLS
    assert names & SCHEMA_MUTATION_TOOLS == set()


@pytest.mark.anyio
async def test_admin_token_sees_the_whole_catalog(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["admin"]) as client:
        assert tool_names(await client.list_tools()) == READ_TOOLS | WRITE_TOOLS | ADMIN_TOOLS


@pytest.mark.anyio
async def test_listing_is_filtered_per_request_from_one_registration(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """Two sessions against the same server instance, different tokens, different
    lists: one catalog registered once, filtered per request (DD-8)."""
    async with (
        memory_session(mcp_server, token=pat["read"]) as reader,
        memory_session(mcp_server, token=pat["admin"]) as admin,
    ):
        read_names = tool_names(await reader.list_tools())
        admin_names = tool_names(await admin.list_tools())
        read_again = tool_names(await reader.list_tools(cache_mode="bypass"))
    assert read_names == READ_TOOLS
    assert admin_names == READ_TOOLS | WRITE_TOOLS | ADMIN_TOOLS
    assert read_again == READ_TOOLS


@pytest.mark.anyio
async def test_direct_call_of_hidden_tool_is_insufficient_scope_and_changes_nothing(
    mcp_server: MCPServer, services: ServiceBundle, pat: dict[str, str]
) -> None:
    seed_task_type(services)
    _, before = services.schema.get_object_type(make_actor(), "task")
    async with memory_session(mcp_server, token=pat["write"]) as client:
        assert "add_field" not in tool_names(await client.list_tools())
        result = await client.call_tool(
            "add_field",
            {
                "object_type": "task",
                "key": "sneaky",
                "name": "Sneaky",
                "type": "short_text",
                "description": "Should never be created.",
            },
        )
    error = error_of(result)
    assert error["code"] == "insufficient_scope"
    assert error["details"] == {
        "tool_name": "add_field",
        "required_scope": "admin",
        "actual_scope": "write",
    }
    assert "'admin' scope" in error["message"]
    assert "administrator" in error["message"]
    assert result.content[0].text == error["message"]  # type: ignore[union-attr]
    _, after = services.schema.get_object_type(make_actor(), "task")
    assert [f.key for f in after] == [f.key for f in before]


@pytest.mark.anyio
async def test_read_token_cannot_write_even_by_direct_call(
    mcp_server: MCPServer, services: ServiceBundle, pat: dict[str, str]
) -> None:
    seed_task_type(services)
    async with memory_session(mcp_server, token=pat["read"]) as client:
        result = await client.call_tool(
            "create_record", {"object_type": "task", "values": {"title": "nope"}}
        )
    assert error_of(result)["code"] == "insufficient_scope"
    assert services.records.query_records(_admin_actor(), "task").total_count == 0


def _admin_actor():  # type: ignore[no-untyped-def]
    from tests.conftest import make_actor

    return make_actor()


# ----------------------------------------------------------- token resolution


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("read", READ_TOOLS),
        ("write", READ_TOOLS | WRITE_TOOLS),
        ("admin", READ_TOOLS | WRITE_TOOLS | ADMIN_TOOLS),
    ],
)
async def test_real_pat_resolution_over_a_session(
    mcp_server: MCPServer, pat: dict[str, str], scope: str, expected: set[str]
) -> None:
    async with memory_session(mcp_server, token=pat[scope]) as client:
        assert tool_names(await client.list_tools()) == expected


@pytest.mark.anyio
async def test_missing_bearer_is_refused_not_promoted_to_admin(
    mcp_server: MCPServer, anon_client: TestClient
) -> None:
    """The single most consequential auth behavior in ``PatTokenResolver`` (its
    docstring, ``src/glosswork/auth.py``): an absent ``Authorization`` header used
    to resolve to full ``admin`` access under the deleted ``InterimTokenResolver``
    (REST/MCP parity with the once-unauthenticated API). It is now refused before any
    tool dispatch, exactly like an unknown bearer below.

    **The refusal lands at the session's opening**, and this test follows it.
    Gating ``server/discover`` means a credential-less client is refused at the
    session's opening and never reaches ``list_tools`` at all, so the session open is
    the assertion. The half this used to make separately, that ``tools/call`` is
    refused too, is made here on the raw wire, which is the only place a single method
    can still be asked in isolation once the session will not open.
    """
    refusal = await refusal_opening(memory_session(mcp_server, token=None))
    assert refusal.code == INVALID_REQUEST
    assert "carried no credential" in refusal.message

    called = raw_jsonrpc(anon_client, "tools/call", {"name": "list_object_types", "arguments": {}})
    assert "result" not in called, called
    assert called["error"]["code"] == INVALID_REQUEST, called
    assert "carried no credential" in called["error"]["message"], called


@pytest.mark.anyio
@pytest.mark.parametrize("token", ["gw_pat_deadbeef", "not-even-a-token", ""])
async def test_unknown_bearer_is_refused_before_dispatch(
    mcp_server: MCPServer, anon_client: TestClient, token: str
) -> None:
    """Refused at the session's opening, for the reason above, and on
    ``tools/call`` over the raw wire with the same unverifiable token."""
    refusal = await refusal_opening(memory_session(mcp_server, token=token))
    called = raw_jsonrpc(
        anon_client, "tools/call", {"name": "list_object_types", "arguments": {}}, token=token
    )
    assert "result" not in called, called
    assert called["error"]["code"] == INVALID_REQUEST, called
    for message in (refusal.message, called["error"]["message"]):
        # PatTokenResolver's actual unknown-token message (src/glosswork/auth.py
        # _UNKNOWN_TOKEN_MESSAGE): it no longer lists literal accepted tokens, since
        # there are none left to list.
        assert "cannot verify the presented bearer token" in message
        assert "revoked" in message
    assert refusal.code == INVALID_REQUEST


# ------------------------------------------------------------------- the seam


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ("read", READ_TOOLS),
        ("write", READ_TOOLS | WRITE_TOOLS),
        ("admin", READ_TOOLS | WRITE_TOOLS | ADMIN_TOOLS),
    ],
)
async def test_server_built_against_a_stub_resolver_lists_that_scope(
    services: ServiceBundle, scope: str, expected: set[str]
) -> None:
    """The factory takes a TokenResolver; a stub returning a fixed scope drives the
    listing regardless of any header, so PatTokenResolver is a drop-in that
    touches no tool registration (DD-8)."""
    server = create_mcp_server(lambda: services, FixedScopeResolver(scope))  # type: ignore[arg-type]
    async with memory_session(server, token="gw_pat_admin") as client:
        assert tool_names(await client.list_tools()) == expected


def test_pat_resolver_is_the_default_in_the_app_factory() -> None:
    """Documented so the swap point is one line in app.py: ``create_app`` builds a
    ``PatTokenResolver`` whenever ``token_resolver`` is not supplied. Unlike
    the deleted ``InterimTokenResolver``, the default now refuses a missing header
    rather than promoting it to ``admin``; services are never touched for that case,
    so the resolver here needs no real service bundle behind it."""
    from inspect import signature

    from glosswork.app import create_app

    assert "token_resolver" in signature(create_app).parameters

    def _unused_services() -> ServiceBundle:
        raise AssertionError("a missing header is refused before services are touched")

    resolver = PatTokenResolver(_unused_services)
    with pytest.raises(TokenRefusedError) as excinfo:
        resolver.resolve(None)
    assert excinfo.value.reason == "missing"


# ------------------------------------------------------ DD-30: the manual at read


@pytest.mark.anyio
async def test_a_read_token_both_sees_and_can_call_the_capability_document(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """The manual has to be readable at the lowest scope that can act on it. Registered
    at ``admin``, as it once was, the agent that uploads -- which holds ``write`` --
    could neither see it in ``tools/list`` nor call it."""
    async with memory_session(mcp_server, token=pat["read"]) as client:
        assert "describe_capabilities" in tool_names(await client.list_tools())
        result = await client.call_tool("describe_capabilities", {})
    assert not result.is_error, result
    document = structured(result)
    assert document["attachments"]["upload_route"] == "POST /api/v1/attachments"


@pytest.mark.anyio
async def test_the_capability_document_does_not_depend_on_who_asks(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """**A fence**: it could not fail against the tree that registered the manual at
    ``admin``, because there a ``read`` token could not call the tool at all.

    Equality across two callers is what makes the scope drop safe, and it is
    mechanically checkable where "contains nothing sensitive" is not -- a naive scan for
    ``@`` matches the ``@me`` and ``@today`` tokens this document is *required* to
    publish. If a per-caller projection is ever added, this fails.
    """
    async with memory_session(mcp_server, token=pat["read"]) as reader:
        low = structured(await reader.call_tool("describe_capabilities", {}))
    async with memory_session(mcp_server, token=pat["admin"]) as admin:
        high = structured(await admin.call_tool("describe_capabilities", {}))
    assert low == high
    assert set(low) == {
        "search",
        "field_types",
        "system_fields",
        "filter_grammar",
        "date_tokens",
        "key_rules",
        "attachments",
        "limits",
    }


@pytest.mark.anyio
async def test_the_capability_document_is_still_behind_the_credential_gate(
    mcp_server: MCPServer, anon_client: TestClient
) -> None:
    """Lowering the scope must not make the manual public:
    ``GATED_METHODS`` covers every request method the server registers, so a session
    carrying no credential never reaches it, and ``anonymous_actor`` (which is
    ``read`` scope) is never the actor of an MCP call.

    The session does not open without a credential, so the first assertion is
    the opening. ``describe_capabilities`` is still named, on the raw wire, because
    this test is about that one tool and not about sessions in general: the manual it
    returns is the disclosure, and a gate that stopped covering it while still refusing
    the handshake would leave this test green if it only asserted the opening.
    """
    refusal = await refusal_opening(memory_session(mcp_server, token=None))
    assert refusal.code == INVALID_REQUEST
    assert "carried no credential" in refusal.message

    called = raw_jsonrpc(
        anon_client, "tools/call", {"name": "describe_capabilities", "arguments": {}}
    )
    assert "result" not in called, called
    assert called["error"]["code"] == INVALID_REQUEST, called
    assert "carried no credential" in called["error"]["message"], called
    assert "filter_grammar" not in str(called), called
