"""DD-11: a type's administrator can permission it, on both surfaces.

Two things are being asserted, and they are different claims.

The first is the **document**: ``AccessService.list_grants_document`` returns the grant
rows plus a ``principals`` sidecar naming every id they mention. The sidecar is not
decoration. A grant row may name a principal the browser's picker cannot see -- a
deactivated colleague, or anyone past the directory route's 200-row cap -- and without a
map built from the rows' own ids such a row renders as a raw UUID. Its cost is asserted
by counting repository calls, the way ``tests/test_principal_sidecar.py`` counts, because
"N+1 but correct" passes every functional assertion.

The second is **who may call it**. The check is on the *level* held on the object type,
never on the system role, and an adapter can still decline to expose it: REST having the
routes while the browser hid its panel behind ``principal.role == "admin"`` and MCP had no
tool at all is the gap these close. The tests below exercise a ``creator`` that holds
``admin`` on the type it defined -- by construction the population the old gate excluded
-- and both halves of DD-11's refusal pair, which mean different things:
``insufficient_scope`` is the credential, ``forbidden`` is the grant.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server import MCPServer

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID, ActorContext, Scope
from glosswork.auth import PatTokenResolver
from glosswork.errors import ForbiddenError
from glosswork.mcp_server import create_mcp_server
from glosswork.repositories.models import PrincipalRow
from glosswork.services import ServiceBundle
from tests.conftest import auth, make_actor
from tests.mcp_support import error_of, memory_session, structured, tool_names
from tests.test_mcp_catalog import ADMIN_TOOLS, READ_TOOLS, WRITE_TOOLS

PASSWORD = "correct-horse-battery-staple"

GRANT_TOOLS = {
    "list_object_type_grants",
    "set_object_type_grant",
    "revoke_object_type_grant",
}

DOCUMENT_KEYS = {"object_type", "default_level", "grants", "principals"}
# Kept in step with ``tests/test_principal_sidecar.py``. ``principal_sidecar_doc``
# is shared with ``AccessService``, so the grants document carries ``type`` too.
SIDECAR_KEYS = {"display_name", "email", "is_active", "type"}


# --------------------------------------------------------------------------- helpers


def seed_type(services: ServiceBundle, key: str, prefix: str, actor: ActorContext) -> Any:
    return services.schema.create_object_type(
        actor,
        key=key,
        name=key.title(),
        name_plural=f"{key.title()}s",
        description=f"Seeded for the delegated-permissions suite ({key}).",
        key_prefix=prefix,
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "What this is called, shown wherever it is listed.",
            }
        ],
    )


def person(services: ServiceBundle, email: str, name: str, role: str = "member") -> PrincipalRow:
    return services.principals.create_user(
        make_actor(), email=email, display_name=name, role=role, password=PASSWORD
    )


def actor_for(principal: PrincipalRow, scope: Scope = "admin") -> ActorContext:
    return ActorContext(
        principal_id=principal.id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id=str(uuid.uuid4()),
        scope=scope,
    )


class CountingPrincipals:
    """Wraps the real repository and counts ``principals_by_ids`` calls, exactly as
    ``tests/test_principal_sidecar.py`` does for the record sidecar."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def principals_by_ids(self, conn: Any, ids: list[str]) -> list[PrincipalRow]:
        self.calls += 1
        return self._inner.principals_by_ids(conn, ids)


# ------------------------------------------------- the document and its sidecar


class TestGrantListing:
    def test_the_documents_key_set_is_exactly_four(self, client: TestClient) -> None:
        services: ServiceBundle = client.app.state.services  # type: ignore[attr-defined]
        seed_type(services, "widget", "WID", make_actor())
        body = client.get("/api/v1/object-types/widget/grants").json()
        assert set(body) == DOCUMENT_KEYS

    def test_the_map_names_principal_id_created_by_and_updated_by(
        self, services: ServiceBundle
    ) -> None:
        """Three id columns, not one. Whoever a row is *about* and whoever wrote it are
        different people, and a screen that names only the first cannot say who granted
        what."""
        seed_type(services, "widget", "WID", make_actor())
        granter = person(services, "granter@example.com", "Ada Granter", role="admin")
        subject = person(services, "subject@example.com", "Bo Subject")
        services.access.grant(actor_for(granter), "widget", subject.id, "read")

        listing = services.access.list_grants_document(make_actor(), "widget")
        row = next(g for g in listing.grants if g.principal_id == subject.id)
        assert row.created_by == granter.id
        assert listing.principals[subject.id]["display_name"] == "Bo Subject"
        assert listing.principals[granter.id]["display_name"] == "Ada Granter"
        assert set(listing.principals[subject.id]) == SIDECAR_KEYS
        # The creating principal's own `admin` row, inserted by `create_object_type`.
        assert BOOTSTRAP_PRINCIPAL_ID in listing.principals

    def test_a_deactivated_granted_principal_is_still_named(self, services: ServiceBundle) -> None:
        """The case the sidecar exists for. The browser's picker reads
        the directory, which is active-only, so a departed colleague's row would render as
        a raw UUID if the panel resolved names the same way it offers them."""
        seed_type(services, "widget", "WID", make_actor())
        departed = person(services, "departed@example.com", "Dana Departed")
        services.access.grant(make_actor(), "widget", departed.id, "write")
        services.principals.deactivate_principal(make_actor(), departed.id)

        listing = services.access.list_grants_document(make_actor(), "widget")
        assert listing.principals[departed.id]["display_name"] == "Dana Departed"
        assert listing.principals[departed.id]["is_active"] is False

    def test_exactly_one_repository_call_for_a_listing_of_many_grants(
        self, services: ServiceBundle
    ) -> None:
        """Five grant rows naming six principals is **one** call, not five and not six."""
        seed_type(services, "widget", "WID", make_actor())
        for index in range(5):
            granted = person(services, f"p{index}@example.com", f"Person {index}")
            services.access.grant(make_actor(), "widget", granted.id, "read")

        counting = CountingPrincipals(services.access._principals)  # noqa: SLF001 - test seam
        services.access._principals = counting  # noqa: SLF001
        listing = services.access.list_grants_document(make_actor(), "widget")

        assert len(listing.grants) == 6  # five granted plus the creator's own admin row
        assert counting.calls == 1
        assert len(listing.principals) == 6

    def test_an_empty_grant_list_yields_an_empty_map_and_issues_no_read(
        self, services: ServiceBundle
    ) -> None:
        seed_type(services, "widget", "WID", make_actor())
        # Remove the creator's own row so the list is genuinely empty.
        services.access.revoke(make_actor(), "widget", BOOTSTRAP_PRINCIPAL_ID)

        counting = CountingPrincipals(services.access._principals)  # noqa: SLF001 - test seam
        services.access._principals = counting  # noqa: SLF001
        listing = services.access.list_grants_document(make_actor(), "widget")

        assert listing.grants == []
        assert listing.principals == {}
        assert counting.calls == 0

    def test_list_grants_still_returns_the_rows_alone_for_the_cli(
        self, services: ServiceBundle
    ) -> None:
        """The operator CLI's third caller keeps its signature; the document-shaped read
        sits beside it, and ``list_grants`` delegates."""
        seed_type(services, "widget", "WID", make_actor())
        rows = services.access.list_grants(make_actor(), "widget")
        assert [r.principal_id for r in rows] == [BOOTSTRAP_PRINCIPAL_ID]


# --------------------------------------------------------- catalog and visibility


@pytest.mark.anyio
async def test_the_catalog_is_thirty_two_tools_in_three_scopes(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    """The three grant tools, all `admin`, take 26 to 29; `get_attachment` at `read` and
    `create_text_attachment` at `write` take 29 to 31; `create_attachment_upload` at
    `write`, with `describe_capabilities` at `read` rather than `admin` (DD-30), takes 31
    to 32 as 11 read, 12 write, 9 admin. Counted off a
    **live listing**, not off this module's own constants: a literal compared against
    another literal would pass on an unfixed tree and prove nothing."""
    async with memory_session(mcp_server, token=pat["admin"]) as client:
        live = tool_names(await client.list_tools())
    assert len(live) == 32
    assert (len(READ_TOOLS), len(WRITE_TOOLS), len(ADMIN_TOOLS)) == (11, 12, 9)
    assert live == READ_TOOLS | WRITE_TOOLS | ADMIN_TOOLS
    assert GRANT_TOOLS <= ADMIN_TOOLS


@pytest.mark.anyio
@pytest.mark.parametrize("scope", ["read", "write"])
async def test_the_grant_tools_are_invisible_below_admin_scope(
    mcp_server: MCPServer, pat: dict[str, str], scope: str
) -> None:
    """**Fence.** A negative about tools that did not exist on a tree without them, so it
    cannot fail there and is not counted as a measured assertion. It is here because
    managing access is an `admin`-scope act on both surfaces, and a tool registered one
    scope too low would be invisible to every other test in this file."""
    async with memory_session(mcp_server, token=pat[scope]) as client:
        names = tool_names(await client.list_tools())
    assert names & GRANT_TOOLS == set()


@pytest.mark.anyio
async def test_the_grant_tools_are_visible_at_admin_scope(
    mcp_server: MCPServer, pat: dict[str, str]
) -> None:
    async with memory_session(mcp_server, token=pat["admin"]) as client:
        names = tool_names(await client.list_tools())
    assert GRANT_TOOLS <= names


# --------------------------------------------- the creator, over both surfaces


@pytest.fixture
def delegated(app: FastAPI, client: TestClient) -> dict[str, Any]:
    """A `creator` that defined its own object type, and therefore holds `admin` on it
    through ``create_object_type``'s own transaction -- the exact population a system-role
    gate would exclude. Plus a second type it holds only `read` on, and an outsider to
    grant to.
    """
    services: ServiceBundle = app.state.services
    creator = person(services, "creator@example.com", "Cass Creator", role="creator")
    creator_actor = actor_for(creator)
    seed_type(services, "widget", "WID", creator_actor)

    seed_type(services, "gadget", "GAD", make_actor())
    services.access.grant(make_actor(), "gadget", creator.id, "read")

    outsider = person(services, "outsider@example.com", "Otto Outsider")
    creator_admin_pat = services.tokens.mint(
        creator_actor, name="creator-admin", scope="admin"
    ).plaintext
    creator_read_pat = services.tokens.mint(
        creator_actor, name="creator-read", scope="read"
    ).plaintext
    return {
        "services": services,
        "client": client,
        "creator": creator,
        "outsider": outsider,
        "admin_pat": creator_admin_pat,
        "read_pat": creator_read_pat,
        "mcp": create_mcp_server(lambda: services, PatTokenResolver(lambda: services)),
    }


@pytest.mark.anyio
async def test_a_creator_lists_sets_and_revokes_on_its_own_type_over_mcp(
    delegated: dict[str, Any],
) -> None:
    """The whole of DD-11 in one session: the type's administrator administers
    it, holding no system role that would let it touch any other type."""
    outsider_id = delegated["outsider"].id
    async with memory_session(delegated["mcp"], token=delegated["admin_pat"]) as session:
        listed = structured(
            await session.call_tool("list_object_type_grants", {"object_type": "widget"})
        )
        assert set(listed) == DOCUMENT_KEYS
        assert listed["principals"][delegated["creator"].id]["display_name"] == "Cass Creator"

        granted = structured(
            await session.call_tool(
                "set_object_type_grant",
                {"object_type": "widget", "principal_id": outsider_id, "level": "read"},
            )
        )
        assert granted["level"] == "read"

        after_grant = structured(
            await session.call_tool("list_object_type_grants", {"object_type": "widget"})
        )
        assert after_grant["principals"][outsider_id]["display_name"] == "Otto Outsider"

        revoked = structured(
            await session.call_tool(
                "revoke_object_type_grant",
                {"object_type": "widget", "principal_id": outsider_id},
            )
        )
        assert revoked == {"status": "revoked"}

        after_revoke = structured(
            await session.call_tool("list_object_type_grants", {"object_type": "widget"})
        )
        assert outsider_id not in {g["principal_id"] for g in after_revoke["grants"]}


@pytest.mark.anyio
async def test_the_same_creator_is_forbidden_on_a_type_it_only_reads(
    delegated: dict[str, Any],
) -> None:
    """DD-11's grant refusal, `forbidden`. The credential is `admin` scope and reaches the tool; the
    grant on this type is `read`, so the service refuses."""
    async with memory_session(delegated["mcp"], token=delegated["admin_pat"]) as session:
        result = await session.call_tool("list_object_type_grants", {"object_type": "gadget"})
    assert error_of(result)["code"] == "forbidden"


@pytest.mark.anyio
async def test_an_admin_granted_principal_presenting_a_read_pat_gets_insufficient_scope(
    delegated: dict[str, Any],
) -> None:
    """DD-11's credential refusal, `insufficient_scope`, and the ceiling that makes it.
    The same principal holds `admin` on `widget`; the credential caps what it may do
    anywhere, so a `read` PAT cannot even see the tool, and calling it directly is refused
    on the scope, not on the grant."""
    async with memory_session(delegated["mcp"], token=delegated["read_pat"]) as session:
        assert tool_names(await session.list_tools()) & GRANT_TOOLS == set()
        result = await session.call_tool("list_object_type_grants", {"object_type": "widget"})
    assert error_of(result)["code"] == "insufficient_scope"


@pytest.mark.anyio
async def test_mcp_and_rest_return_the_identical_document_for_the_same_actor(
    delegated: dict[str, Any],
) -> None:
    """FR-A1 asserted rather than assumed. Same credential, same principal, both
    surfaces, byte-for-byte the same document."""
    rest = delegated["client"].get(
        "/api/v1/object-types/widget/grants", headers=auth(delegated["admin_pat"])
    )
    assert rest.status_code == 200, rest.text
    async with memory_session(delegated["mcp"], token=delegated["admin_pat"]) as session:
        mcp = structured(
            await session.call_tool("list_object_type_grants", {"object_type": "widget"})
        )
    assert mcp == rest.json()


def test_a_creator_administering_its_own_type_over_rest_too(
    delegated: dict[str, Any],
) -> None:
    """The REST half of the same claim. These routes have always accepted this caller;
    nothing here is new behaviour, and it is asserted so the two surfaces are held to one
    claim."""
    headers = auth(delegated["admin_pat"])
    outsider_id = delegated["outsider"].id
    put = delegated["client"].put(
        f"/api/v1/object-types/widget/grants/{outsider_id}",
        json={"level": "write"},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    body = delegated["client"].get("/api/v1/object-types/widget/grants", headers=headers).json()
    assert body["principals"][outsider_id]["display_name"] == "Otto Outsider"


def test_the_service_refuses_a_listing_below_admin_on_the_type(
    services: ServiceBundle,
) -> None:
    """**Scope fence**: the level check ``list_grants`` applies, still applied by the
    document-shaped read it delegates to. It cannot fail against a tree without that
    read, so it is not counted as a measured assertion."""
    seed_type(services, "widget", "WID", make_actor())
    member = person(services, "member@example.com", "Mo Member")
    services.access.grant(make_actor(), "widget", member.id, "write")
    with pytest.raises(ForbiddenError):
        services.access.list_grants_document(actor_for(member, scope="admin"), "widget")
