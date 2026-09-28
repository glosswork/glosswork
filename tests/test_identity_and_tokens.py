"""Principals, service accounts, and personal access tokens (FR-I3, FR-I4, FR-I5).

Covers the lifecycle at the service layer and over REST, the two mint-time ceilings,
the audit rows these mutations produce (`entity_type` `principal` and
`access_token` are in docs/DATA_MODEL.md section 9's enumeration and once had no
producer), and the one property that makes central scope enforcement sound:
demotion must not leave a stale over-scoped token behind.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID, ActorContext
from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.cookies import CSRF_HEADER_NAME
from glosswork.db import Database
from glosswork.errors import (
    InsufficientScopeError,
    NotFoundError,
    TokenRefusedError,
    ValidationFailedError,
)
from glosswork.services import ServiceBundle, build_services
from glosswork.services.principals import role_scope
from glosswork.services.tokens import TOKEN_PREFIX, hash_token
from glosswork.timeutil import format_datetime, utc_now
from tests.conftest import auth, make_actor

PASSWORD = "correct-horse-battery-staple"


def audit_rows(db: Database, entity_type: str) -> list[int]:
    with db.read() as conn:
        rows = conn.execute(
            text("SELECT id FROM audit_events WHERE entity_type = :t ORDER BY id"),
            {"t": entity_type},
        ).all()
    return [int(row[0]) for row in rows]


def audit_dump(db: Database) -> str:
    with db.read() as conn:
        rows = conn.execute(text("SELECT * FROM audit_events")).all()
    return str([tuple(row) for row in rows])


# ------------------------------------------------------- principal lifecycle


def test_create_a_user(services: ServiceBundle) -> None:
    principal = services.principals.create_user(
        make_actor(),
        email="ada@example.com",
        display_name="Ada Lovelace",
        role="member",
        password=PASSWORD,
    )
    assert principal.type == "user"
    assert principal.role == "member"
    assert principal.auth_provider == "local"
    assert principal.is_active


def test_create_a_service_account_requires_a_description(services: ServiceBundle) -> None:
    """Descriptions on agent-facing objects are required across this product (AGENTS.md
    non-negotiable 6). A service account with no stated purpose is exactly the
    credential nobody can later decide whether to revoke."""
    account = services.principals.create_service_account(
        make_actor(),
        display_name="Nightly reconciliation agent",
        description="Reconciles initiative status against the finance export each night.",
    )
    assert account.type == "service_account"
    assert account.email is None
    for blank in ("", "   "):
        with pytest.raises(ValidationFailedError) as excinfo:
            services.principals.create_service_account(
                make_actor(), display_name="Nameless", description=blank
            )
        assert "description" in excinfo.value.message


def test_list_get_rename_and_deactivate(services: ServiceBundle) -> None:
    created = services.principals.create_user(
        make_actor(), email="grace@example.com", display_name="Grace", password=PASSWORD
    )
    assert services.principals.get_principal(created.id).display_name == "Grace"
    renamed = services.principals.update_principal(
        make_actor(), created.id, display_name="Grace Hopper"
    )
    assert renamed.display_name == "Grace Hopper"

    users = services.principals.list_principals("user")
    assert [p.id for p in users] == [created.id]
    assert BOOTSTRAP_PRINCIPAL_ID in {p.id for p in services.principals.list_principals()}

    deactivated = services.principals.deactivate_principal(make_actor(), created.id)
    assert deactivated.is_active is False
    # Deactivated, never deleted: every audit row and record `created_by` still resolves.
    assert services.principals.get_principal(created.id).id == created.id
    assert [p.id for p in services.principals.list_principals("user", include_inactive=False)] == []


def test_an_unknown_principal_is_a_not_found_error(services: ServiceBundle) -> None:
    with pytest.raises(NotFoundError):
        services.principals.get_principal("00000000-0000-4000-8000-00000000dead")


def test_the_last_active_administrator_cannot_be_demoted_or_deactivated(
    services: ServiceBundle,
) -> None:
    """Not a stated requirement, but the alternative is a running deployment nobody can
    administer, recoverable only through the operator CLI. One check is cheaper than
    that support ticket."""
    admin = services.principals.create_user(
        make_actor(),
        email="root@example.com",
        display_name="Root",
        role="admin",
        password=PASSWORD,
    )
    with pytest.raises(ValidationFailedError) as excinfo:
        services.principals.update_principal(make_actor(), admin.id, role="member")
    assert "only active administrator" in excinfo.value.message
    with pytest.raises(ValidationFailedError):
        services.principals.deactivate_principal(make_actor(), admin.id)

    services.principals.create_user(
        make_actor(),
        email="second@example.com",
        display_name="Second",
        role="admin",
        password=PASSWORD,
    )
    assert services.principals.update_principal(make_actor(), admin.id, role="member").role


def test_the_seeded_bootstrap_principal_survives_the_new_migration(db: Database) -> None:
    """Migration 1's principal is the FK target of every audit row, record
    `created_by`, and comment author written before anyone could sign in. Nothing
    deletes or repurposes it, and migration 4 adds a table beside it rather than
    touching it."""
    from glosswork.migrations import MIGRATIONS, applied_migrations, run_migrations

    assert {m.number for m in MIGRATIONS} == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}
    assert applied_migrations(db) == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12}
    assert run_migrations(db) == []  # idempotent
    with db.read() as conn:
        row = (
            conn.execute(
                text("SELECT * FROM principals WHERE id = :id"),
                {"id": BOOTSTRAP_PRINCIPAL_ID},
            )
            .mappings()
            .first()
        )
    assert row is not None
    assert row["type"] == "service_account"
    assert row["role"] == "admin"
    assert row["is_active"] == 1


def test_existing_data_still_reads_after_the_migration(
    db: Database, services: ServiceBundle, sink_type: object
) -> None:
    """Seed data shaped like a pre-sign-in deployment's, then assert history still
    resolves its principal. `run_migrations` already ran in the `db` fixture, so this is the
    "existing deployment upgrades" path rather than a fresh install."""
    actor = make_actor()
    record = services.records.create_record(actor, "artifact", {"title": "Legacy"})
    services.records.update_record(actor, record.key, {"title": "Legacy renamed"})
    services.comments.add_comment(actor, record.key, "A comment from before sign-in existed.")

    history = services.records.get_record_history(actor, record.key)
    assert history
    for event in history:
        assert services.principals.get_principal(event.principal_id).id == BOOTSTRAP_PRINCIPAL_ID
    comments = services.comments.list_comments(actor, record.key)
    assert len(comments) == 1
    assert services.records.get_record(actor, record.key).data["title"] == "Legacy renamed"


def test_role_scope_derivation_is_recorded() -> None:
    """Cookie sessions carry no scope of their own and must derive one from role at the
    edge. Recording it here means a session is a middleware branch, not a redesign of
    the enforcement mechanism."""
    assert role_scope("admin") == "admin"
    assert role_scope("member") == "write"


# --------------------------------------------------------------- minting


def test_mint_returns_the_plaintext_exactly_once_and_stores_only_its_hash(
    db: Database, services: ServiceBundle
) -> None:
    minted = services.tokens.mint(make_actor(), name="my laptop", scope="write")
    plaintext = minted.plaintext
    assert plaintext.startswith(TOKEN_PREFIX)
    assert len(plaintext) == len(TOKEN_PREFIX) + 32

    stored = services.tokens.get_token(minted.row.id)
    assert stored.token_hash == hash_token(plaintext)
    assert stored.token_prefix == plaintext[:8]

    # The plaintext appears in no table.
    with db.read() as conn:
        tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).all()
        for (table,) in tables:
            rows = conn.execute(text(f"SELECT * FROM {table}")).all()  # noqa: S608
            assert plaintext not in str([tuple(r) for r in rows]), table

    # ...and in no subsequent read.
    assert plaintext not in str(services.tokens.list_tokens(make_actor()))
    assert plaintext not in audit_dump(db)


def test_mint_stores_the_agent_label_on_the_token(services: ServiceBundle) -> None:
    """A token is already minted for one named place, so the label belongs on it."""
    minted = services.tokens.mint(
        make_actor(), name="claude desktop", scope="write", agent_label="claude-desktop"
    )
    assert minted.row.agent_label == "claude-desktop"
    assert services.tokens.get_token(minted.row.id).agent_label == "claude-desktop"


def test_mint_without_an_agent_label_leaves_it_none(services: ServiceBundle) -> None:
    minted = services.tokens.mint(make_actor(), name="plain", scope="write")
    assert minted.row.agent_label is None
    assert services.tokens.get_token(minted.row.id).agent_label is None


def test_a_malformed_agent_label_is_refused_at_mint(services: ServiceBundle) -> None:
    """A bad label stored on a token would refuse every later call that
    token made, so it is refused here rather than at use. FR-I6 is untouched -- an
    *unknown* label is still accepted and auto-registered; only a malformed one fails.
    """
    with pytest.raises(ValidationFailedError):
        services.tokens.mint(make_actor(), name="too long", scope="read", agent_label="x" * 201)
    with pytest.raises(ValidationFailedError):
        services.tokens.mint(make_actor(), name="blank", scope="read", agent_label="   ")
    assert services.tokens.list_tokens(make_actor()) == []


def test_the_mint_route_takes_and_returns_the_agent_label(client: TestClient) -> None:
    """The failure shape this closes: the field was accepted with 201 and dropped."""
    response = client.post(
        "/api/v1/access-tokens",
        json={"name": "desktop", "scope": "read", "agent_label": "claude-desktop"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["agent_label"] == "claude-desktop"

    listed = client.get("/api/v1/access-tokens")
    assert listed.status_code == 200, listed.text
    by_name = {row["name"]: row for row in listed.json()["access_tokens"]}
    assert by_name["desktop"]["agent_label"] == "claude-desktop"


def test_a_token_minted_with_no_agent_label_publishes_it_as_null(client: TestClient) -> None:
    response = client.post("/api/v1/access-tokens", json={"name": "plain", "scope": "read"})
    assert response.status_code == 201, response.text
    assert response.json()["agent_label"] is None


def test_a_member_principal_cannot_mint_an_admin_token(services: ServiceBundle) -> None:
    """FR-I4's scope ceiling, at both boundaries."""
    member = services.principals.create_user(
        make_actor(),
        email="member@example.com",
        display_name="Member",
        role="member",
        password=PASSWORD,
    )
    actor = make_actor()
    with pytest.raises(ValidationFailedError) as excinfo:
        services.tokens.mint(actor, name="over", scope="admin", principal_id=member.id)
    assert "'admin' token" in excinfo.value.message
    # ...but write and read are fine.
    assert services.tokens.mint(actor, name="ok", scope="write", principal_id=member.id)

    admin = services.principals.create_user(
        make_actor(),
        email="admin@example.com",
        display_name="Admin",
        role="admin",
        password=PASSWORD,
    )
    assert services.tokens.mint(actor, name="fine", scope="admin", principal_id=admin.id)


def test_a_credential_cannot_mint_a_token_above_its_own_scope(
    services: ServiceBundle,
) -> None:
    """The second ceiling. Without it an administrator's deliberately narrow `write`
    token could mint an `admin` one, which would make every `write` credential a latent
    `admin` credential and the point of scoping a token illusory."""
    narrow = ActorContext(
        principal_id=BOOTSTRAP_PRINCIPAL_ID,
        principal_type="service_account",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id="req-1",
        scope="write",
    )
    assert services.tokens.mint(narrow, name="peer", scope="write")
    with pytest.raises(InsufficientScopeError) as excinfo:
        services.tokens.mint(narrow, name="escalate", scope="admin")
    assert excinfo.value.code == "insufficient_scope"
    assert excinfo.value.required_scope == "admin"
    assert excinfo.value.actual_scope == "write"
    # Named by what was attempted, not by a route: the same service method is reached
    # from REST, from MCP, and from the operator CLI (DD-3).
    assert "access token" in excinfo.value.details["operation"]
    assert "path" not in excinfo.value.details


def test_minting_for_another_principal_requires_admin_scope_and_the_admin_role(
    services: ServiceBundle,
) -> None:
    """Delegated minting hands out a credential the caller will not be holding, so it
    is gated twice over. Both gates are exercised here because neither is reachable
    through REST — the route is declared `write`, and an `admin`-scoped caller is the
    only kind that gets this far — so a test is the only thing standing between this
    branch and a silent regression."""
    target = services.principals.create_service_account(
        make_actor(),
        display_name="Delegated agent",
        description="Holds a token minted on its behalf by an administrator.",
    )

    # Gate one: the caller's own credential scope.
    writer = ActorContext(
        principal_id=BOOTSTRAP_PRINCIPAL_ID,
        principal_type="service_account",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id="req-delegate",
        scope="write",
    )
    with pytest.raises(InsufficientScopeError) as excinfo:
        services.tokens.mint(writer, name="delegated", scope="read", principal_id=target.id)
    assert excinfo.value.required_scope == "admin"
    assert excinfo.value.actual_scope == "write"
    assert "another principal" in excinfo.value.details["operation"]
    assert "path" not in excinfo.value.details

    # Gate two: the caller's role. An admin-scoped credential held by a `member`
    # principal still cannot mint on someone else's behalf.
    member = services.principals.create_user(
        make_actor(),
        email="member-minter@example.com",
        display_name="Member Minter",
        role="member",
        password=PASSWORD,
    )
    member_actor = ActorContext(
        principal_id=member.id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id="req-delegate-2",
        scope="admin",
    )
    with pytest.raises(ValidationFailedError) as validation:
        services.tokens.mint(member_actor, name="delegated", scope="read", principal_id=target.id)
    assert "'admin' role" in validation.value.message

    # And the path that should work: admin scope, admin role, another principal.
    assert services.tokens.mint(
        make_actor(), name="delegated", scope="read", principal_id=target.id
    )


def test_expiry_must_be_in_the_future_and_well_formed(services: ServiceBundle) -> None:
    actor = make_actor()
    with pytest.raises(ValidationFailedError) as excinfo:
        services.tokens.mint(actor, name="stale", scope="read", expires_at="2000-01-01T00:00:00Z")
    assert "past" in excinfo.value.message
    with pytest.raises(ValidationFailedError) as excinfo:
        services.tokens.mint(actor, name="garbled", scope="read", expires_at="next tuesday")
    assert "ISO-8601" in excinfo.value.message
    # The millisecond form, which is what `Date.prototype.toISOString()` emits and what the Setup
    # UI once sent. The server has always refused it; this pins that, so a later
    # loosening of `parse_datetime` turns a test red rather than quietly widening what the product
    # accepts at its boundary.
    with pytest.raises(ValidationFailedError) as excinfo:
        services.tokens.mint(
            actor, name="millis", scope="read", expires_at="2027-01-31T00:00:00.000Z"
        )
    assert "ISO-8601" in excinfo.value.message
    future = format_datetime(utc_now() + timedelta(days=30))
    assert services.tokens.mint(actor, name="fine", scope="read", expires_at=future)


def test_a_token_name_is_required(services: ServiceBundle) -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        services.tokens.mint(make_actor(), name="  ", scope="read")
    assert "name" in excinfo.value.message


def test_an_unknown_scope_is_rejected(services: ServiceBundle) -> None:
    with pytest.raises(ValidationFailedError) as excinfo:
        services.tokens.mint(make_actor(), name="weird", scope="superuser")
    assert "Valid scopes" in excinfo.value.message


# ----------------------------------------------------------- list and revoke


def test_listing_never_reveals_a_secret(services: ServiceBundle) -> None:
    minted = services.tokens.mint(make_actor(), name="visible", scope="read")
    listed = services.tokens.list_tokens(make_actor())
    row = next(t for t in listed if t.id == minted.row.id)
    assert row.token_prefix == minted.plaintext[:8]
    assert row.name == "visible"
    assert row.scope == "read"
    assert row.revoked_at is None
    assert minted.plaintext not in str(row)


def test_revoking_is_idempotent_and_audited(db: Database, services: ServiceBundle) -> None:
    minted = services.tokens.mint(make_actor(), name="doomed", scope="read")
    first = services.tokens.revoke(make_actor(), minted.row.id)
    assert first.revoked_at is not None
    second = services.tokens.revoke(make_actor(), minted.row.id)
    assert second.revoked_at == first.revoked_at
    assert len(audit_rows(db, "access_token")) >= 2  # one create, one revoke


# --------------------------------------------------- demotion revokes tokens


def test_demotion_revokes_the_principals_admin_tokens_in_the_same_transaction(
    services: ServiceBundle,
) -> None:
    """Without this, FR-I4's ceiling holds only at mint time and central enforcement's
    reliance on `scope` alone becomes unsound."""
    services.principals.create_user(
        make_actor(),
        email="keeper@example.com",
        display_name="Keeper",
        role="admin",
        password=PASSWORD,
    )
    target = services.principals.create_user(
        make_actor(),
        email="demoted@example.com",
        display_name="Demoted",
        role="admin",
        password=PASSWORD,
    )
    admin_token = services.tokens.mint(
        make_actor(), name="powerful", scope="admin", principal_id=target.id
    )
    write_token = services.tokens.mint(
        make_actor(), name="ordinary", scope="write", principal_id=target.id
    )

    services.principals.update_principal(make_actor(), target.id, role="member")

    assert services.tokens.get_token(admin_token.row.id).revoked_at is not None
    # A token at or below the new ceiling is untouched: this is a downgrade of what the
    # principal may hold, not a mass revocation of everything they have.
    assert services.tokens.get_token(write_token.row.id).revoked_at is None


def test_an_admin_pat_minted_before_a_demotion_no_longer_opens_an_admin_route(
    client: TestClient, app_services: ServiceBundle
) -> None:
    """The end-to-end proof, over HTTP, which is where it matters."""
    app_services.principals.create_user(
        make_actor(),
        email="keeper@example.com",
        display_name="Keeper",
        role="admin",
        password=PASSWORD,
    )
    target = app_services.principals.create_user(
        make_actor(),
        email="soon@example.com",
        display_name="Soon",
        role="admin",
        password=PASSWORD,
    )
    minted = app_services.tokens.mint(
        make_actor(), name="before", scope="admin", principal_id=target.id
    )
    header = auth(minted.plaintext)
    assert client.get("/api/v1/principals", headers=header).status_code == 200

    demote = client.patch(f"/api/v1/principals/{target.id}", json={"role": "member"})
    assert demote.status_code == 200, demote.text

    after = client.get("/api/v1/principals", headers=header)
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "invalid_token"


# ----------------------------------------------------------------- audit


def test_principal_and_token_mutations_write_audit_rows(
    db: Database, services: ServiceBundle
) -> None:
    """`entity_type` `principal` and `access_token` are already in
    docs/DATA_MODEL.md section 9's enumeration and have had no producer until now."""
    assert audit_rows(db, "principal") == []
    services.principals.create_user(
        make_actor(),
        email="keeper@example.com",
        display_name="Keeper",
        role="admin",
        password=PASSWORD,
    )
    created = services.principals.create_user(
        make_actor(), email="audited@example.com", display_name="Audited", password=PASSWORD
    )
    services.principals.update_principal(make_actor(), created.id, role="admin")
    services.principals.set_password(make_actor(), created.id, "another-good-password")
    minted = services.tokens.mint(
        make_actor(), name="audited", scope="read", principal_id=created.id
    )
    services.tokens.revoke(make_actor(), minted.row.id)
    services.principals.deactivate_principal(make_actor(), created.id)

    assert len(audit_rows(db, "principal")) >= 5  # 2 creates, role, password, deactivate
    assert len(audit_rows(db, "access_token")) >= 2  # mint, revoke

    body = audit_dump(db)
    assert minted.plaintext not in body
    assert "$argon2id$" not in body
    assert PASSWORD not in body
    assert "another-good-password" not in body


def test_auth_method_is_pat_for_every_token_write(
    db: Database, client: TestClient, app_services: ServiceBundle
) -> None:
    """Every write through a token records `pat`; recorded here rather than leaving the
    enumeration half-covered."""
    created = client.post(
        "/api/v1/principals",
        json={"type": "user", "email": "pat@example.com", "display_name": "Pat"},
    )
    assert created.status_code == 201, created.text
    events = client.get("/api/v1/audit-events", params={"limit": 200}).json()["events"]
    methods = {event["auth_method"] for event in events}
    assert methods == {"pat"}
    del db, app_services


# ------------------------------------------------------------------- REST


def test_me_returns_the_principal_and_the_credentials_scope(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """The route that lets the UI avoid hardcoding `CURRENT_PRINCIPAL_ID` and gives
    FR-C5's "am I the author" a real answer. It is backend work, so it lands here
    rather than with its consumer."""
    body = client.get("/api/v1/me", headers=auth(api_tokens["read"])).json()
    assert body["id"] == BOOTSTRAP_PRINCIPAL_ID
    assert body["type"] == "service_account"
    assert body["role"] == "admin"  # from the principal
    assert body["scope"] == "read"  # from the credential — two different things
    assert body["auth_method"] == "pat"

    admin_body = client.get("/api/v1/me", headers=auth(api_tokens["admin"])).json()
    assert admin_body["scope"] == "admin"
    assert admin_body["id"] == body["id"]


def test_the_pat_routes_round_trip_over_http(client: TestClient) -> None:
    created = client.post("/api/v1/access-tokens", json={"name": "ci runner", "scope": "write"})
    assert created.status_code == 201, created.text
    body = created.json()
    plaintext = body["token"]
    assert plaintext.startswith(TOKEN_PREFIX)
    assert body["token_prefix"] == plaintext[:8]
    assert "token_hash" not in body

    listing = client.get("/api/v1/access-tokens").json()["access_tokens"]
    row = next(t for t in listing if t["id"] == body["id"])
    assert "token" not in row and "token_hash" not in row
    assert plaintext not in str(listing)

    # The minted token works...
    assert client.get("/api/v1/object-types", headers=auth(plaintext)).status_code == 200
    # ...until it is revoked.
    assert client.delete(f"/api/v1/access-tokens/{body['id']}").status_code == 200
    assert client.get("/api/v1/object-types", headers=auth(plaintext)).status_code == 401


def test_principal_routes_round_trip_over_http(client: TestClient) -> None:
    created = client.post(
        "/api/v1/principals",
        json={
            "type": "service_account",
            "display_name": "Reporting agent",
            "description": "Builds the weekly operations-team status pack.",
        },
    )
    assert created.status_code == 201, created.text
    principal_id = created.json()["id"]
    assert created.json()["type"] == "service_account"

    assert client.get(f"/api/v1/principals/{principal_id}").status_code == 200
    renamed = client.patch(
        f"/api/v1/principals/{principal_id}", json={"display_name": "Reporting agent v2"}
    )
    assert renamed.json()["display_name"] == "Reporting agent v2"
    assert client.delete(f"/api/v1/principals/{principal_id}").json()["is_active"] is False


def test_creating_a_service_account_without_a_description_is_rejected_over_http(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/principals", json={"type": "service_account", "display_name": "Nameless"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# ------------------------------------------------------ listing is a delegation


def _pat_actor(principal_id: str, scope: str) -> ActorContext:
    """One principal holding one credential, at the service layer. The route's own
    ``require_scope`` is a separate, coarser gate; these cases are about the check
    inside the service, which is where DD-3 puts it."""
    return ActorContext(
        principal_id=principal_id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id=str(uuid.uuid4()),
        scope=scope,  # type: ignore[arg-type]
    )


@pytest.fixture
def three_principals(services: ServiceBundle) -> dict[str, str]:
    """A `member`, a `creator`, and a second system `admin`, each holding one token."""
    ids = {
        "member": services.principals.create_user(
            make_actor(),
            email="listing-member@example.com",
            display_name="Listing Member",
            role="member",
            password=PASSWORD,
        ).id,
        "creator": services.principals.create_user(
            make_actor(),
            email="listing-creator@example.com",
            display_name="Listing Creator",
            role="creator",
            password=PASSWORD,
        ).id,
        "admin": services.principals.create_user(
            make_actor(),
            email="listing-admin@example.com",
            display_name="Listing Admin",
            role="admin",
            password=PASSWORD,
        ).id,
    }
    for label, principal_id in ids.items():
        services.tokens.mint(
            make_actor(), name=f"{label} token", scope="read", principal_id=principal_id
        )
    return ids


def test_a_member_lists_its_own_tokens(
    services: ServiceBundle, three_principals: dict[str, str]
) -> None:
    """**Fence.** A `read` credential still lists its own tokens, with and without the
    redundant `principal_id`, and sees nothing belonging to anyone else."""
    who = _pat_actor(three_principals["member"], "read")
    for argument in (None, three_principals["member"]):
        listed = services.tokens.list_tokens(who, argument)
        assert [t.name for t in listed] == ["member token"]
        assert {t.principal_id for t in listed} == {three_principals["member"]}


def test_a_member_cannot_list_another_principals_tokens(
    services: ServiceBundle, three_principals: dict[str, str]
) -> None:
    """The security review's reproduction: a `read` credential naming a foreign
    `principal_id` used to receive that principal's whole credential inventory."""
    who = _pat_actor(three_principals["member"], "read")
    with pytest.raises(InsufficientScopeError) as excinfo:
        services.tokens.list_tokens(who, three_principals["admin"])
    assert "Listing" in excinfo.value.message
    assert "Minting" not in excinfo.value.message


def test_a_creator_with_an_admin_credential_still_needs_the_admin_role(
    services: ServiceBundle, three_principals: dict[str, str]
) -> None:
    """The role half of the delegation. `role_scope('creator')` is `admin`, so the
    credential ceiling permits the scope and only the role refuses."""
    who = _pat_actor(three_principals["creator"], "admin")
    with pytest.raises(ValidationFailedError) as excinfo:
        services.tokens.list_tokens(who, three_principals["admin"])
    assert "listing" in excinfo.value.message.lower()
    assert "minting" not in excinfo.value.message.lower()


def test_an_admin_lists_another_principals_tokens_only_with_an_admin_credential(
    services: ServiceBundle, three_principals: dict[str, str]
) -> None:
    """Both halves, on the one principal that holds the role: the credential is a
    ceiling and narrows the administrator like anybody else (DD-11)."""
    admin_id = three_principals["admin"]
    listed = services.tokens.list_tokens(_pat_actor(admin_id, "admin"), three_principals["member"])
    assert [t.name for t in listed] == ["member token"]

    with pytest.raises(InsufficientScopeError) as excinfo:
        services.tokens.list_tokens(_pat_actor(admin_id, "read"), three_principals["member"])
    assert "Listing" in excinfo.value.message


def test_the_listing_route_refuses_a_foreign_principal_id_over_http(client: TestClient) -> None:
    """The route passes the actor rather than resolving ownership itself,
    so the refusal arrives with the service's code."""
    member_id = client.post(
        "/api/v1/principals",
        json={
            "type": "service_account",
            "display_name": "Foreign listing target",
            "description": "Holds a token this test tries to enumerate from elsewhere.",
        },
    ).json()["id"]
    minted = client.post(
        "/api/v1/access-tokens",
        json={"name": "narrow reader", "scope": "read", "principal_id": member_id},
    )
    assert minted.status_code == 201, minted.text
    plaintext = minted.json()["token"]

    own = client.get("/api/v1/access-tokens", headers=auth(plaintext))
    assert own.status_code == 200, own.text
    assert [t["name"] for t in own.json()["access_tokens"]] == ["narrow reader"]

    foreign = client.get(
        f"/api/v1/access-tokens?principal_id={BOOTSTRAP_PRINCIPAL_ID}", headers=auth(plaintext)
    )
    assert foreign.status_code == 403, foreign.text
    assert foreign.json()["error"]["code"] == "insufficient_scope"


# ------------------------------------------------ credentials die with the password


NEW_PASSWORD = "another-correct-horse-battery"


@pytest.fixture
def reset_app(tmp_path: Path) -> FastAPI:
    """``cookie_secure=False`` for the reason ``test_auth_routes.py`` gives: the
    TestClient talks plain http, and httpx's cookie jar correctly refuses to attach a
    Secure cookie to it, exactly as a browser would."""
    settings = Settings(data_dir=tmp_path / "data", cookie_secure=False, embedding_enabled=False)
    return create_app(settings)


@pytest.fixture
def reset_client(reset_app: FastAPI) -> Any:
    """One admin and one member, both local password accounts. No default
    ``Authorization`` header: every request here authenticates with a cookie unless it
    says otherwise."""
    with TestClient(reset_app) as test_client:
        services: ServiceBundle = reset_app.state.services
        services.principals.create_user(
            make_actor(),
            email="reset-admin@example.com",
            display_name="Reset Admin",
            role="admin",
            auth_provider="local",
            password=PASSWORD,
        )
        services.principals.create_user(
            make_actor(),
            email="reset-member@example.com",
            display_name="Reset Member",
            role="member",
            auth_provider="local",
            password=PASSWORD,
        )
        yield test_client


def _login(client: TestClient, email: str, password: str = PASSWORD) -> Any:
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_a_reset_kills_the_targets_session_and_every_token_it_held(
    reset_app: FastAPI, reset_client: TestClient
) -> None:
    """The review's reproduction, inverted into an assertion: after the reset an operator
    reaches for, neither the attacker's cookie nor a PAT minted under the account
    still answers."""
    services: ServiceBundle = reset_app.state.services
    member = services.principals.find_by_email("reset-member@example.com")
    assert member is not None
    assert _login(reset_client, "reset-member@example.com").status_code == 200
    pat = services.tokens.mint(
        make_actor(), name="member laptop", scope="write", principal_id=member.id
    ).plaintext
    assert reset_client.get("/api/v1/me").status_code == 200
    assert reset_client.get("/api/v1/me", headers=auth(pat)).status_code == 200

    services.principals.set_password(make_actor(), member.id, NEW_PASSWORD)

    assert reset_client.get("/api/v1/me").status_code == 401
    refused = reset_client.get("/api/v1/me", headers=auth(pat))
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "invalid_token"
    assert refused.json()["error"]["details"]["reason"] == "revoked"


def test_an_admin_self_reset_over_a_session_spares_exactly_that_session(
    reset_app: FastAPI, reset_client: TestClient
) -> None:
    """The self-reset carve-out, asserted at the API against a real ``gw_session`` cookie.
    The session that performed the reset survives; the same account's other device does
    not."""
    services: ServiceBundle = reset_app.state.services
    admin = services.principals.find_by_email("reset-admin@example.com")
    assert admin is not None
    assert _login(reset_client, "reset-admin@example.com").status_code == 200
    other_device = TestClient(reset_app)
    assert _login(other_device, "reset-admin@example.com").status_code == 200
    assert other_device.get("/api/v1/me").status_code == 200

    changed = reset_client.post(
        f"/api/v1/principals/{admin.id}/password",
        json={"password": NEW_PASSWORD},
        headers={CSRF_HEADER_NAME: reset_client.cookies["gw_csrf"]},
    )
    assert changed.status_code == 200, changed.text

    assert reset_client.get("/api/v1/me").status_code == 200
    assert other_device.get("/api/v1/me").status_code == 401


def test_an_admin_resetting_someone_else_over_a_session_spares_nothing_of_theirs(
    reset_app: FastAPI, reset_client: TestClient
) -> None:
    """The carve-out is for a *self*-reset only: resetting another principal's
    password must not spare a session merely because the caller arrived on one."""
    services: ServiceBundle = reset_app.state.services
    member = services.principals.find_by_email("reset-member@example.com")
    assert member is not None
    victim = TestClient(reset_app)
    assert _login(victim, "reset-member@example.com").status_code == 200
    assert _login(reset_client, "reset-admin@example.com").status_code == 200

    changed = reset_client.post(
        f"/api/v1/principals/{member.id}/password",
        json={"password": NEW_PASSWORD},
        headers={CSRF_HEADER_NAME: reset_client.cookies["gw_csrf"]},
    )
    assert changed.status_code == 200, changed.text
    assert victim.get("/api/v1/me").status_code == 401
    assert reset_client.get("/api/v1/me").status_code == 200


def test_a_reset_writes_one_revocation_row_per_credential_with_the_note(
    db: Database, services: ServiceBundle
) -> None:
    """The revocations audit themselves exactly as they do on deactivation, and the
    note says which containment action produced them."""
    user = services.principals.create_user(
        make_actor(),
        email="noted@example.com",
        display_name="Noted",
        role="member",
        password=PASSWORD,
    )
    tokens = [
        services.tokens.mint(make_actor(), name=f"pat-{s}", scope=s, principal_id=user.id)
        for s in ("read", "write")
    ]
    sessions = [services.sessions.issue(make_actor(), user) for _ in range(2)]

    services.principals.set_password(make_actor(), user.id, NEW_PASSWORD)

    for minted in tokens:
        assert services.tokens.get_token(minted.row.id).revoked_at is not None
    for issued in sessions:
        with pytest.raises(TokenRefusedError) as excinfo:
            services.sessions.resolve(issued.cookie_value)
        assert excinfo.value.reason == "unknown"

    with db.read() as conn:
        rows = conn.execute(
            text(
                "SELECT entity_type FROM audit_events "
                "WHERE action = 'revoke' AND note = 'password reset' ORDER BY id"
            )
        ).all()
    assert sorted(str(row[0]) for row in rows) == [
        "access_token",
        "access_token",
        "session",
        "session",
    ]


def test_a_rehash_on_a_successful_login_revokes_nothing(db: Database, tmp_path: Path) -> None:
    """``verify_password`` also rewrites ``password_hash``, and must not revoke: an
    operator raising the Argon2 cost would otherwise sign the deployment out one
    login at a time (section 1)."""
    cheap = build_services(db, tmp_path, Settings(data_dir=tmp_path, embedding_enabled=False))
    user = cheap.principals.create_user(
        make_actor(),
        email="rehashed@example.com",
        display_name="Rehashed",
        role="member",
        auth_provider="local",
        password=PASSWORD,
    )
    minted = cheap.tokens.mint(make_actor(), name="kept", scope="read", principal_id=user.id)
    session = cheap.sessions.issue(make_actor(), user)

    dearer = build_services(
        db,
        tmp_path,
        Settings(data_dir=tmp_path, embedding_enabled=False, argon2_time_cost=4),
    )
    before = dearer.principals.get_principal_or_none(user.id)
    assert before is not None and before.password_hash is not None
    dearer.principals.verify_password("rehashed@example.com", PASSWORD)
    after = dearer.principals.get_principal_or_none(user.id)
    assert after is not None and after.password_hash != before.password_hash  # it rehashed

    assert dearer.tokens.get_token(minted.row.id).revoked_at is None
    assert dearer.sessions.resolve(session.cookie_value).principal_id == user.id


def test_a_service_account_still_cannot_be_given_a_password(services: ServiceBundle) -> None:
    """**Fence.** The type check precedes the write, so no revocation runs for a
    target that could never have had a password."""
    agent = services.principals.create_service_account(
        make_actor(),
        display_name="Reporting agent",
        description="Builds the weekly operations-team status pack.",
    )
    with pytest.raises(ValidationFailedError):
        services.principals.set_password(make_actor(), agent.id, NEW_PASSWORD)
