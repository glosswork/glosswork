"""The upload ticket (DD-16).

Bytes cannot ride in a tool call (DD-29), so an upload is an HTTP request,
and an HTTP request needs a credential the agent can actually see. The connection's
bearer belongs to the MCP *client*, and no part of the protocol hands the model its own
credential, so the endpoint mints one narrowed to exactly the thing it is for.

The whole design is affordable because a ticket is **an ``access_tokens`` row**, not a
new credential system: it inherits hashing, ``token_prefix``, expiry enforcement, the
deactivated-principal check and a password reset's revoke-everything for free. This module asserts
the negatives that follow from that; the happy path lives in
``tests/test_endpoint_teaches_itself.py``, where it belongs, because there it is part of
proving the endpoint teaches itself.

Two of these are **meta-tests** in the shape of "``AccessService`` is the only
answerer": exactly one predicate reads a capability for authorization, and exactly one
route accepts one. Without both, "closed by construction" is a comment.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.shared.exceptions import MCPError

from glosswork.actor import ActorContext, Scope
from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.errors import InsufficientScopeError, ValidationFailedError
from glosswork.repositories.models import AccessTokenRow
from glosswork.scopes import route_capabilities, route_scopes
from glosswork.services import ServiceBundle
from glosswork.timeutil import format_datetime, utc_now
from tests.conftest import auth, make_actor, mint_scope_tokens
from tests.mcp_support import http_session

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "glosswork"

BASE_URL = "https://tracker.example.com"
PAYLOAD = b"a small binary payload \x00\x01\x02\n"


# ------------------------------------------------------------------- fixtures


def based_settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        embedding_enabled=False,
        base_url=BASE_URL,
        # See the note in tests/test_endpoint_teaches_itself.py: the published
        # origin is the setting's, and the transport's host allowlist is separate.
        mcp_allowed_hosts="testserver",
        **overrides,
    )


@pytest.fixture
def based_app(tmp_path: Path) -> FastAPI:
    return create_app(based_settings(tmp_path))


@pytest.fixture
def based_client(based_app: FastAPI) -> TestClient:
    """A running deployment with ``GW_BASE_URL`` set, presenting an ``admin`` PAT by
    default so a ticketed request has to override the header rather than add to it."""
    with TestClient(based_app) as test_client:
        tokens = mint_scope_tokens(based_app.state.services)
        test_client.headers["Authorization"] = f"Bearer {tokens['admin']}"
        test_client.scope_tokens = tokens  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture
def based_services(based_client: TestClient, based_app: FastAPI) -> ServiceBundle:
    bundle: ServiceBundle = based_app.state.services
    return bundle


def actor_at(scope: Scope, principal_id: str | None = None) -> ActorContext:
    """A plain PAT actor: what an MCP agent calling the mint tool looks like. No
    capability of its own -- a ticket cannot mint a ticket, because it cannot reach the
    MCP surface at all (asserted below)."""
    base = make_actor()
    return ActorContext(
        principal_id=principal_id or base.principal_id,
        principal_type="service_account",
        agent_label_id=None,
        auth_method="pat",
        surface="mcp",
        request_id=base.request_id,
        scope=scope,
    )


def mint(
    services: ServiceBundle,
    filename: str = "report.pdf",
    content_type: str | None = None,
    actor: ActorContext | None = None,
) -> dict[str, Any]:
    ticket: dict[str, Any] = services.attachments.create_upload_ticket(
        actor or actor_at("write"), filename, content_type
    )
    return ticket


def upload(client: TestClient, ticket: dict[str, Any], content: bytes = PAYLOAD) -> Any:
    return client.post(
        "/api/v1/attachments",
        files={"file": ("whatever.bin", content, "application/octet-stream")},
        headers={"Authorization": ticket["authorization"]},
    )


def log_lines(capfd: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    """The application's structured log lines, which are one JSON object each (FR-P5)."""
    return [
        json.loads(line) for line in capfd.readouterr().out.splitlines() if line.startswith("{")
    ]


def flatten(exc: BaseException) -> list[BaseException]:
    """Every leaf of a possibly nested ``ExceptionGroup``."""
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for child in exc.exceptions for leaf in flatten(child)]
    return [exc]


def capability_rows(services: ServiceBundle) -> list[AccessTokenRow]:
    """Every live capability row, read straight from the repository.

    Deliberately not through ``list_tokens``: it excludes capability rows,
    so a test that looked for one there would be asserting nothing.
    """
    from glosswork.repositories.sqlite import SqliteAccessTokenRepository

    with services.tokens._db.read() as conn:  # noqa: SLF001
        return SqliteAccessTokenRepository().list_capability_tokens(conn)


def only_row(services: ServiceBundle) -> AccessTokenRow:
    rows = capability_rows(services)
    assert len(rows) == 1, rows
    return rows[0]


def set_column(services: ServiceBundle, token_id: str, changes: dict[str, Any]) -> None:
    with services.tokens._db.write() as conn:  # noqa: SLF001
        services.tokens._tokens.update_row(conn, token_id, changes)  # noqa: SLF001


# --------------------------------------------------------- what a ticket may do


def test_a_ticket_is_a_write_credential_bound_to_one_upload(
    based_client: TestClient, based_services: ServiceBundle
) -> None:
    ticket = mint(based_services, "quarterly.pdf", "application/pdf")
    assert ticket["authorization"].startswith("Bearer gw_upl_")
    response = upload(based_client, ticket)
    assert response.status_code == 200, response.text
    body = response.json()
    # The ticket's own filename and content type win over the multipart part's,
    # which is what makes a leaked ticket unrepurposable, and removes the friction a
    # mismatch refusal would add -- the agent names the file at mint and points curl at
    # whatever local path holds the bytes.
    assert body["filename"] == "quarterly.pdf"
    assert body["content_type"] == "application/pdf"
    assert body["byte_size"] == len(PAYLOAD)


def test_a_second_use_of_one_ticket_is_refused(
    based_client: TestClient, based_services: ServiceBundle
) -> None:
    """Consumption is atomic with the insert, so a ticket is spent exactly
    once and two concurrent uses cannot both win."""
    ticket = mint(based_services)
    assert upload(based_client, ticket).status_code == 200
    second = upload(based_client, ticket)
    assert second.status_code == 401, second.text
    # ``invalid_token``, inherited: the ticket is refused at the resolver, beside every
    # other reason a bearer does not verify, so a spent ticket and a revoked PAT read the
    # same way to a client. The message is what distinguishes them.
    assert second.json()["error"]["code"] == "invalid_token"
    assert "already been used" in second.json()["error"]["message"]


def test_a_refused_upload_does_not_burn_the_ticket(tmp_path: Path) -> None:
    """The other half, and the reason consumption happens on success rather than
    on presentation: an over-sized file is the agent's to retry, and a ticket burned by
    the refusal would make the retry impossible."""
    app = create_app(based_settings(tmp_path, max_attachment_bytes=64))
    with TestClient(app) as client:
        services: ServiceBundle = app.state.services
        client.headers["Authorization"] = f"Bearer {mint_scope_tokens(services)['admin']}"
        ticket = mint(services)
        too_big = upload(client, ticket, content=b"x" * 200)
        assert too_big.status_code == 422, too_big.text
        assert too_big.json()["error"]["details"]["setting"] == "GW_MAX_ATTACHMENT_BYTES"
        # The upload caps are unchanged: a ticket buys a credential, never a larger file.
        assert ticket["max_bytes"] == 64
        # Still live -- the same ticket succeeds on a file within the cap.
        assert upload(client, ticket, content=b"y" * 16).status_code == 200


# ------------------------------------------------------ what a ticket may not do


def test_a_ticket_is_refused_on_every_route_but_the_upload(
    based_client: TestClient, based_services: ServiceBundle
) -> None:
    """``enforce_scope`` refuses any actor carrying a
    capability, so every route in the system -- including one added tomorrow -- is
    closed to a ticket without anyone remembering to close it."""
    ticket = mint(based_services)
    header = {"Authorization": ticket["authorization"]}
    for method, path in (
        ("GET", "/api/v1/object-types"),
        ("GET", "/api/v1/principals/directory"),
        ("GET", "/api/v1/changes"),
        ("POST", "/api/v1/search"),
        ("GET", "/api/v1/agent-labels"),
    ):
        response = based_client.request(method, path, headers=header, json={"query": "x"})
        assert response.status_code == 403, (method, path, response.status_code, response.text)
        envelope = response.json()["error"]
        assert envelope["code"] == "insufficient_scope"
        assert "attachment_upload" in envelope["message"], envelope


@pytest.mark.anyio
async def test_a_ticket_cannot_reach_the_mcp_surface(tmp_path: Path) -> None:
    """The hole a route-only rule would leave.

    ``/mcp`` is in ``SCOPE_EXEMPT_PATHS`` by design (DD-8: the adapter runs its own gate
    over the tool catalog, and a second route-level check would be the "two orderings"
    ``scopes.py`` exists to avoid). So a ticket presented there would otherwise resolve
    to an ordinary ``write`` identity and see every write tool -- including the one that
    mints more tickets, which is an unbounded widening. ``McpAdapter.identity`` calls the
    same one predicate ``enforce_scope`` does.
    """
    settings = based_settings(tmp_path)
    bootstrap = create_app(settings)
    async with bootstrap.router.lifespan_context(bootstrap):
        ticket = mint(bootstrap.state.services)
    secret = ticket["authorization"].removeprefix("Bearer ")
    with pytest.raises(BaseException) as caught:
        async with http_session(create_app(settings), token=secret) as session:
            await session.list_tools()
    # The handshake itself is gated, so the refusal arrives from ``initialize``
    # inside the client's own task group and reaches the caller as an exception group.
    # What matters is that it is an ``MCPError`` naming the capability, not that it is
    # unwrapped, so the group is flattened rather than asserted around.
    assert any(
        isinstance(exc, MCPError) and "attachment_upload" in str(exc)
        for exc in flatten(caught.value)
    ), caught.value


def test_a_ticket_never_carries_admin_scope(based_services: ServiceBundle) -> None:
    """A capability narrows the DD-11 ceiling; it grants nothing. Minted
    by an ``admin`` credential, a ticket is still a ``write`` token, so there is no path
    by which it does more than the PAT that minted it."""
    mint(based_services, actor=actor_at("admin"))
    row = only_row(based_services)
    assert row.scope == "write"
    assert row.capability == "attachment_upload"


def test_a_read_credential_cannot_mint_a_ticket(based_services: ServiceBundle) -> None:
    """The mint inherits ``AccessTokenService``'s own ceiling: a credential cannot mint
    a token above itself. The MCP tool is registered at ``write`` scope too, so this is
    defence in depth rather than the only gate."""
    with pytest.raises(InsufficientScopeError):
        mint(based_services, actor=actor_at("read"))


def test_nothing_a_ticket_can_reach_can_mint_another_ticket(
    based_client: TestClient, based_services: ServiceBundle
) -> None:
    """The recursion is closed by **unreachability**, not by a second rule, and this
    records which.

    A ticket carries ``write`` scope, so a service-level "refuse a capability actor" in
    ``AccessTokenService`` would be the only thing standing between one ticket and an
    unbounded supply of them -- and it would be a second reader of the capability, which
    is forbidden precisely because two readers is how a default-closed property
    stops being one. Instead the mint is simply not reachable: it exists only as an MCP
    tool, and the MCP surface refuses a ticket outright (the test above), as does every
    REST route (the test above that). So what is asserted here is the reachability, not
    a rule that is deliberately absent.
    """
    ticket = mint(based_services)
    header = {"Authorization": ticket["authorization"]}
    # There is no REST twin of create_attachment_upload by design (DD-16: a REST caller
    # already holds a PAT that reaches the upload route), so there is no route to try.
    assert not [entry for entry in route_scopes(based_client.app) if "upload" in entry.path]
    # The credential routes are closed to it like every other route...
    assert based_client.request("GET", "/api/v1/access-tokens", headers=header).status_code == 403
    # ...and the one route it does reach creates an attachment, which is not a credential.
    # Spending it last, because the refusal above must be measured on a live ticket.
    assert upload(based_client, ticket).status_code == 200


# ---------------------------------------------------- what kills a ticket early


def test_an_expired_ticket_is_refused(
    based_client: TestClient, based_services: ServiceBundle
) -> None:
    ticket = mint(based_services)
    past = format_datetime(utc_now() - timedelta(seconds=1))
    set_column(based_services, only_row(based_services).id, {"expires_at": past})
    response = upload(based_client, ticket)
    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"].lower()


def test_a_ticket_whose_principal_was_deactivated_is_refused(
    based_client: TestClient, based_services: ServiceBundle
) -> None:
    holder = based_services.principals.create_user(
        make_actor(),
        email="deactivated.holder@example.com",
        display_name="Deactivated Holder",
        password="a-sufficiently-long-password",
    )
    ticket = mint(based_services, actor=actor_at("write", holder.id))
    based_services.principals.deactivate_principal(make_actor(), holder.id)
    response = upload(based_client, ticket)
    assert response.status_code == 401
    # Refused as **revoked**, not as "deactivated", and that is the inherited behaviour
    # rather than a near miss: ``deactivate_principal`` revokes every live token in the
    # same transaction as the flag (FR-I3), so the ticket is already dead by the time the
    # resolver would have reached its principal check. Both paths are live -- a ticket
    # minted before the deactivation is revoked, and one that somehow survived would fail
    # the principal check -- and this asserts the one that actually fires.
    assert "revoked" in response.json()["error"]["message"].lower()


def test_a_password_reset_kills_a_live_ticket(
    based_client: TestClient, based_services: ServiceBundle
) -> None:
    """A password reset's revocation, inherited rather than reimplemented -- and asserted
    rather than assumed.

    A reset revokes every live token in the same transaction as the hash, and a ticket
    is a live token at ``write`` scope, so it is already in that set. This is the
    containment action that matters for a credential which, by design, lands in a
    model's context and therefore in transcripts and logs (DD-16's residual risk).
    """
    holder = based_services.principals.create_user(
        make_actor(),
        email="ticket.holder@example.com",
        display_name="Ticket Holder",
        password="a-sufficiently-long-password",
    )
    ticket = mint(based_services, actor=actor_at("write", holder.id))
    based_services.principals.set_password(
        make_actor(), holder.id, "a-different-sufficiently-long-password"
    )
    response = upload(based_client, ticket)
    assert response.status_code == 401
    assert "revoked" in response.json()["error"]["message"].lower()


# ---------------------------------------------------- minting, and the operator


def test_minting_refuses_when_the_base_url_is_unset(services: ServiceBundle) -> None:
    """A relative URL makes a ticket useless: the model does not see the MCP
    endpoint's own origin either, so a bare path leaves both halves of the request
    missing. The refusal names the setting an operator has to set."""
    with pytest.raises(ValidationFailedError) as caught:
        services.attachments.create_upload_ticket(actor_at("write"), "report.pdf")
    assert "GW_BASE_URL" in caught.value.message


def test_minting_a_ticket_appends_no_audit_row(based_services: ServiceBundle) -> None:
    """The reason ``create_attachment_upload`` is the one write tool with no ``agent``
    parameter (stated in tests/test_mcp_catalog.py): it writes no record and appends no
    audit event a label would attribute. The upload the ticket enables appends its own
    event instead, under whatever credential the agent presents on that HTTP request."""
    cursor = based_services.changes.list_changes_since(make_actor()).next_cursor
    mint(based_services)
    events = based_services.changes.list_changes_since(make_actor(), cursor=cursor).events
    assert events == []


def test_a_ticket_is_invisible_to_list_tokens(based_services: ServiceBundle) -> None:
    """Its stated cost: an administrator cannot see or hand-revoke a live
    ticket. Accepted, because a ticket cannot be renamed, re-scoped or usefully revoked
    one at a time -- it expires in minutes -- and the containment action that matters
    already reaches it (the test above)."""
    actor = make_actor()
    before = based_services.tokens.list_tokens(actor)
    mint(based_services, actor=actor_at("write", actor.principal_id))
    assert based_services.tokens.list_tokens(actor) == before
    assert len(capability_rows(based_services)) == 1


def test_minting_purges_spent_and_expired_tickets(
    based_client: TestClient, based_services: ServiceBundle
) -> None:
    """The table does not grow without bound, and the purge is
    opportunistic rather than a scheduled job nobody runs."""
    spent = mint(based_services)
    assert upload(based_client, spent).status_code == 200
    mint(based_services)
    stale = only_row(based_services)
    set_column(
        based_services, stale.id, {"expires_at": format_datetime(utc_now() - timedelta(seconds=1))}
    )
    mint(based_services)
    remaining = capability_rows(based_services)
    assert [row.id for row in remaining] == [row.id for row in remaining if row.id != stale.id]
    assert len(remaining) == 1


def test_a_ticket_filename_is_validated_at_mint(based_services: ServiceBundle) -> None:
    """``AttachmentService.upload`` applies no filename validation at
    all -- a gap noted rather than routed around -- so the mint applies its
    own, and the ``curl`` line shell-quotes every interpolated value on top of that.
    Two independent controls, because a tool result is a string a model may paste into
    a shell.
    """
    for bad in ("", "   ", "../etc/passwd", "a/b.txt", "with\nnewline", "nul\x00byte", "x" * 300):
        with pytest.raises(ValidationFailedError):
            based_services.attachments.create_upload_ticket(actor_at("write"), bad)


def test_the_curl_line_cannot_become_a_second_command(based_services: ServiceBundle) -> None:
    ticket = mint(based_services, "quarterly report; rm -rf ~.pdf".replace("/", "-"))
    words = shlex.split(ticket["curl"])
    assert words[0] == "curl"
    assert "rm" not in words
    assert ";" not in words
    assert ticket["upload_url"] in words


# --------------------------------------------------------- the startup warning


def test_startup_warns_once_when_the_base_url_is_unset(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """In the shape of the ``reserved_key_collisions`` report: logged once at
    ``warning`` during lifespan, blocking nothing and failing no probe, because a
    browser-only deployment is entirely unaffected -- the SPA joins relative paths to
    the origin it loaded from. What is degraded is the agent surface."""
    with TestClient(create_app(Settings(data_dir=tmp_path / "d", embedding_enabled=False))) as c:
        # The body, not the status. A status-only assertion passes against a
        # route that does not exist whenever web/dist is built.
        assert c.get("/readyz").json() == {"status": "ok"}
    lines = log_lines(capfd)
    warnings = [line for line in lines if line.get("event") == "base_url_unset"]
    assert len(warnings) == 1, warnings
    assert warnings[0]["level"] == "warning"
    assert warnings[0]["setting"] == "GW_BASE_URL"
    assert "create_attachment_upload" in warnings[0]["hint"]


def test_startup_is_silent_when_the_base_url_is_set(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    with TestClient(create_app(based_settings(tmp_path))) as c:
        assert c.get("/readyz").json() == {"status": "ok"}  # the body
    lines = log_lines(capfd)
    assert not [line for line in lines if line.get("event") == "base_url_unset"]


# --------------------------------------------------------------- the meta-tests


def test_only_the_scope_gate_reads_a_capability_for_authorization() -> None:
    """In the shape of "``AccessService`` is the only answerer".

    A capability is read in exactly two places and both delegate to one predicate in
    ``scopes.py``: the REST dependency chain through ``enforce_scope``, and
    ``McpAdapter.identity``, which closes the ``/mcp`` surface -- scope-exempt by design
    -- to a ticket. If a route handler, a service method or a tool body starts branching
    on a capability, the default-closed property is gone, so the matched set is pinned
    by equality rather than merely bounded.
    """

    # ``actor.capability`` is the ActorContext attribute a handler, a service method or
    # a tool would branch on, and ``identity.capability`` is the TokenIdentity one at the
    # edge. Both are pinned; ``row.capability`` and the ``capability=`` keyword are the
    # storage layer carrying a column and are deliberately not matched, because carrying
    # a value is not reading it to decide anything.
    def files_matching(pattern: str) -> dict[str, list[str]]:
        result = subprocess.run(
            ["grep", "-rn", "--include=*.py", "-E", pattern, str(SRC)],
            capture_output=True,
            text=True,
        )
        found: dict[str, list[str]] = {}
        for line in result.stdout.splitlines():
            if line.strip():
                found.setdefault(Path(line.split(":", 1)[0]).name, []).append(line)
        return found

    reads = files_matching(r"\bactor\.capability\b")
    assert reads, "the grep matched nothing at all, so it is asserting nothing"
    assert set(reads) == {"scopes.py"}, reads

    carried = files_matching(r"\bidentity\.capability\b")
    assert set(carried) == {"adapter.py", "middleware.py"}, carried
    # The adapter's single line delegates to the same predicate rather than restating
    # the rule; the middleware's copies the value onto the actor and decides nothing.
    assert len(carried["adapter.py"]) == 1, carried["adapter.py"]
    assert "refuse_capability_credential" in carried["adapter.py"][0]
    assert len(carried["middleware.py"]) == 1, carried["middleware.py"]
    assert "capability=identity.capability" in carried["middleware.py"][0]


def test_exactly_one_route_accepts_a_capability(app: FastAPI) -> None:
    """Pinned by equality over the real route table, the way the body-cap exemptions are
    pinned. Adding a second value of ``capability`` is a new decision
    with its own blast-radius argument, and this is what makes it deliberate."""
    assert route_capabilities(app) == [("POST", "/api/v1/attachments", "attachment_upload")]


def test_the_capability_route_still_declares_its_scope(app: FastAPI) -> None:
    """``require_capability`` replaces ``require_scope`` on that one route, so it has
    to keep answering the introspection every completeness test walks. A capability is
    a narrowing of the credential ceiling, never a way around declaring one."""
    entry = next(e for e in route_scopes(app) if e.key == "POST /api/v1/attachments")
    assert entry.scope == "write"


def test_the_upload_route_behaves_identically_for_a_plain_pat(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """A **fence**: the plain-PAT path through ``POST /api/v1/attachments`` is the same
    as before capabilities existed, so this cannot fail on its own account. It is here
    because ``require_capability`` replaced that route's
    ``require_scope("write")``, and what needs proving is that nothing else moved."""
    ok = client.post(
        "/api/v1/attachments",
        files={"file": ("note.txt", b"hello", "text/plain")},
        headers=auth(api_tokens["write"]),
    )
    assert ok.status_code == 200
    assert ok.json()["filename"] == "note.txt"
    refused = client.post(
        "/api/v1/attachments",
        files={"file": ("note.txt", b"hello", "text/plain")},
        headers=auth(api_tokens["read"]),
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "insufficient_scope"


def test_the_ticket_prefix_is_distinguishable_from_a_pat() -> None:
    """A leaked ``gw_upl_`` string is identifiable at a glance, and in a refusal
    through ``token_prefix``, without anyone having to look the row up."""
    source = (SRC / "services" / "tokens.py").read_text()
    assert re.search(r'CAPABILITY_TOKEN_PREFIX\s*=\s*"gw_upl_"', source)
