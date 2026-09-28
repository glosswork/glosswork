"""Browser sessions (FR-A3, DD-9): the service layer.

Mirrors ``test_identity_and_tokens.py``'s coverage of ``AccessTokenService`` on
purpose — issue/resolve/revoke, the plaintext-shown-once property, and the audit
trail — plus what is new to a session rather than a PAT: two server-side lifetime
bounds, scope derived fresh from the principal's *current* role on every resolution,
and the session-bound CSRF hash DD-10 stores alongside it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from glosswork.config import Settings
from glosswork.db import Database
from glosswork.errors import TokenRefusedError
from glosswork.services import ServiceBundle
from glosswork.services.sessions import LAST_SEEN_COARSENESS, hash_session_value
from glosswork.timeutil import format_datetime, utc_now
from tests.conftest import make_actor

PASSWORD = "correct-horse-battery-staple"


def audit_rows(db: Database, entity_type: str) -> list[int]:
    with db.read() as conn:
        rows = conn.execute(
            text("SELECT id FROM audit_events WHERE entity_type = :t ORDER BY id"),
            {"t": entity_type},
        ).all()
    return [int(row[0]) for row in rows]


@pytest.fixture
def local_user(services: ServiceBundle):
    return services.principals.create_user(
        make_actor(),
        email="dana@example.com",
        display_name="Dana",
        role="member",
        password=PASSWORD,
    )


# --------------------------------------------------------------------------- issue


def test_issue_returns_the_plaintexts_exactly_once_and_stores_only_hashes(
    db: Database, services: ServiceBundle, local_user
) -> None:
    minted = services.sessions.issue(make_actor(), local_user)
    assert len(minted.cookie_value) > 20
    assert len(minted.csrf_value) > 20
    assert minted.row.session_hash == hash_session_value(minted.cookie_value)
    assert minted.row.csrf_hash == hash_session_value(minted.csrf_value)

    with db.read() as conn:
        tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).all()
        for (table,) in tables:
            rows = conn.execute(text(f"SELECT * FROM {table}")).all()  # noqa: S608
            dumped = str([tuple(r) for r in rows])
            assert minted.cookie_value not in dumped, table
            assert minted.csrf_value not in dumped, table


def test_issue_writes_an_audit_row(db: Database, services: ServiceBundle, local_user) -> None:
    before = audit_rows(db, "session")
    services.sessions.issue(make_actor(), local_user, note="local login")
    after = audit_rows(db, "session")
    assert len(after) == len(before) + 1


def test_issue_deletes_the_prior_row_for_the_same_browser(
    services: ServiceBundle, local_user
) -> None:
    first = services.sessions.issue(make_actor(), local_user)
    second = services.sessions.issue(make_actor(), local_user, replacing_cookie=first.cookie_value)
    assert second.cookie_value != first.cookie_value
    with pytest.raises(TokenRefusedError) as excinfo:
        services.sessions.resolve(first.cookie_value)
    assert excinfo.value.reason == "unknown"
    identity = services.sessions.resolve(second.cookie_value)
    assert identity.principal_id == local_user.id


# ------------------------------------------------------------------------- resolve


def test_resolve_a_fresh_session(services: ServiceBundle, local_user) -> None:
    minted = services.sessions.issue(make_actor(), local_user)
    identity = services.sessions.resolve(minted.cookie_value)
    assert identity.principal_id == local_user.id
    assert identity.principal_type == "user"
    assert identity.auth_method == "session"
    assert identity.scope == "write"  # member -> write (role_scope)


def test_resolve_an_unknown_cookie_is_refused(services: ServiceBundle) -> None:
    with pytest.raises(TokenRefusedError) as excinfo:
        services.sessions.resolve("not-a-real-session-value")
    assert excinfo.value.code == "invalid_token"
    assert excinfo.value.reason == "unknown"


def test_resolve_a_revoked_session_is_refused(services: ServiceBundle, local_user) -> None:
    minted = services.sessions.issue(make_actor(), local_user)
    services.sessions.revoke_by_cookie(make_actor(), minted.cookie_value)
    with pytest.raises(TokenRefusedError) as excinfo:
        services.sessions.resolve(minted.cookie_value)
    assert excinfo.value.reason == "unknown"  # the row is deleted, not flagged


def test_resolve_an_expired_session_is_refused(services: ServiceBundle, local_user) -> None:
    now = utc_now()
    minted = services.sessions.issue(make_actor(), local_user, now=now)
    later = now + timedelta(hours=Settings().session_lifetime_hours, minutes=1)
    with pytest.raises(TokenRefusedError) as excinfo:
        services.sessions.resolve(minted.cookie_value, now=later)
    assert excinfo.value.reason == "expired"


def test_resolve_an_idle_session_is_refused(services: ServiceBundle, local_user) -> None:
    now = utc_now()
    minted = services.sessions.issue(make_actor(), local_user, now=now)
    later = now + timedelta(hours=Settings().session_idle_hours, minutes=1)
    with pytest.raises(TokenRefusedError) as excinfo:
        services.sessions.resolve(minted.cookie_value, now=later)
    assert excinfo.value.reason == "idle_expired"


def test_resolve_checks_the_principals_active_flag_directly(
    db: Database, services: ServiceBundle, local_user
) -> None:
    """`resolve` itself refuses an inactive principal's session (DD-9's deactivation
    parity: the same live check `PatTokenResolver` runs), independent of
    `deactivate_principal`'s own session cleanup — which is exercised separately below
    and would otherwise mask this check by deleting the row first."""
    minted = services.sessions.issue(make_actor(), local_user)
    with db.write() as conn:
        conn.execute(
            text("UPDATE principals SET is_active = 0 WHERE id = :id"), {"id": local_user.id}
        )
    with pytest.raises(TokenRefusedError) as excinfo:
        services.sessions.resolve(minted.cookie_value)
    assert excinfo.value.reason == "inactive_principal"


def test_resolve_derives_scope_from_the_current_role_not_a_cached_one(
    services: ServiceBundle, local_user
) -> None:
    """The property DD-9 is built on: nothing about scope is cached in the row."""
    minted = services.sessions.issue(make_actor(), local_user)
    assert services.sessions.resolve(minted.cookie_value).scope == "write"
    services.principals.update_principal(make_actor(), local_user.id, role="admin")
    assert services.sessions.resolve(minted.cookie_value).scope == "admin"


def test_last_seen_at_advances_coarsely(db: Database, services: ServiceBundle, local_user) -> None:
    now = utc_now()
    minted = services.sessions.issue(make_actor(), local_user, now=now)

    services.sessions.resolve(minted.cookie_value, now=now + timedelta(seconds=1))
    with db.read() as conn:
        row = conn.execute(
            text("SELECT last_seen_at FROM sessions WHERE id = :id"), {"id": minted.row.id}
        ).one()
    assert row[0] == format_datetime(now)  # unchanged: within the coarseness window

    later = now + LAST_SEEN_COARSENESS + timedelta(seconds=1)
    services.sessions.resolve(minted.cookie_value, now=later)
    with db.read() as conn:
        row = conn.execute(
            text("SELECT last_seen_at FROM sessions WHERE id = :id"), {"id": minted.row.id}
        ).one()
    assert row[0] == format_datetime(later)


# ----------------------------------------------------------------------------- csrf


def test_verify_csrf_accepts_the_matching_token(services: ServiceBundle, local_user) -> None:
    minted = services.sessions.issue(make_actor(), local_user)
    assert services.sessions.verify_csrf(minted.cookie_value, minted.csrf_value) is True


def test_verify_csrf_rejects_a_mismatched_token(services: ServiceBundle, local_user) -> None:
    minted = services.sessions.issue(make_actor(), local_user)
    assert services.sessions.verify_csrf(minted.cookie_value, "wrong-value") is False


def test_verify_csrf_rejects_a_missing_token(services: ServiceBundle, local_user) -> None:
    minted = services.sessions.issue(make_actor(), local_user)
    assert services.sessions.verify_csrf(minted.cookie_value, None) is False


def test_verify_csrf_uses_hmac_compare_digest(services: ServiceBundle, local_user) -> None:
    """Not `==`: the CSRF hash comparison is timing-safe (DD-10)."""
    import inspect

    source = inspect.getsource(services.sessions.__class__.verify_csrf)
    assert "hmac.compare_digest" in source
    assert " == row.csrf_hash" not in source and "row.csrf_hash ==" not in source


# --------------------------------------------------------------------------- revoke


def test_revoke_by_cookie_deletes_the_row_and_is_idempotent(
    services: ServiceBundle, local_user
) -> None:
    minted = services.sessions.issue(make_actor(), local_user)
    services.sessions.revoke_by_cookie(make_actor(), minted.cookie_value)
    with pytest.raises(TokenRefusedError):
        services.sessions.resolve(minted.cookie_value)
    # A second revoke of the same (now-gone) cookie is a no-op, not an error.
    services.sessions.revoke_by_cookie(make_actor(), minted.cookie_value)


def test_revoke_by_cookie_of_an_unknown_cookie_is_a_no_op(services: ServiceBundle) -> None:
    services.sessions.revoke_by_cookie(make_actor(), "never-issued")


def test_revoke_all_for_principal_deletes_every_live_session_and_audits_each(
    db: Database, services: ServiceBundle, local_user
) -> None:
    first = services.sessions.issue(make_actor(), local_user)
    second = services.sessions.issue(make_actor(), local_user)
    third_owner = services.principals.create_user(
        make_actor(), email="other@example.com", display_name="Other", password=PASSWORD
    )
    other = services.sessions.issue(make_actor(), third_owner)

    before = audit_rows(db, "session")
    services.sessions.revoke_all_for_principal(make_actor(), local_user.id)
    after = audit_rows(db, "session")
    assert len(after) == len(before) + 2  # two of Dana's sessions, not Other's

    for cookie in (first.cookie_value, second.cookie_value):
        with pytest.raises(TokenRefusedError):
            services.sessions.resolve(cookie)
    # Unaffected: a different principal's session.
    assert services.sessions.resolve(other.cookie_value).principal_id == third_owner.id


# -------------------------------------------------------- lifecycle side effects


def test_deactivating_a_principal_revokes_its_sessions_in_the_same_transaction(
    services: ServiceBundle, local_user
) -> None:
    minted = services.sessions.issue(make_actor(), local_user)
    services.principals.deactivate_principal(make_actor(), local_user.id)
    with pytest.raises(TokenRefusedError) as excinfo:
        services.sessions.resolve(minted.cookie_value)
    # The row itself is gone (revoked by deactivation), not merely blocked by the
    # is_active check — both would 401, but this asserts the cleanup actually ran.
    assert excinfo.value.reason == "unknown"


def test_an_admins_own_live_session_survives_a_demotion_by_another_admin(
    services: ServiceBundle,
) -> None:
    """The headline session rule: demoting an administrator mid-session drops them
    to `write` scope with no re-login, so `update_principal`'s demotion path must NOT
    revoke the demoted principal's own session — unlike deactivation, and unlike an
    OIDC re-login that discovers a role change for the identity logging in right now.
    """
    admin_actor = make_actor()
    # A second admin user, so demoting `target` does not trip the
    # last-active-administrator guard (`_guard_last_admin`).
    services.principals.create_user(
        admin_actor, email="other-admin@example.com", display_name="Other Admin", role="admin"
    )
    target = services.principals.create_user(
        admin_actor, email="target-admin@example.com", display_name="Target", role="admin"
    )
    minted = services.sessions.issue(admin_actor, target)
    assert services.sessions.resolve(minted.cookie_value).scope == "admin"

    services.principals.update_principal(admin_actor, target.id, role="member")

    identity = services.sessions.resolve(minted.cookie_value)
    assert identity.scope == "write"
    assert identity.principal_id == target.id
