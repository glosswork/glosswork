"""A person changes their own password over `/api/v1/me/password` (FR-I17, DD-13).

The route, `PrincipalService.change_own_password`, the `set_password` race guards and
`me_doc.auth_provider` are each asserted below. An unknown path on a tree with no
`web/dist` built (every backend test run) returns FastAPI's own 404, not the SPA's
`200 text/html` fallback (AGENTS.md's trap), so the assertions read the JSON body
rather than the status code: they measure the same thing whichever fallback a given
tree has.

Fixtures mirror ``tests/test_identity_and_tokens.py``'s ``reset_app``/``reset_client``
(lines ~717-753): a real app, a real ``gw_session`` cookie, no default
``Authorization`` header.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from glosswork.actor import ActorContext
from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.cookies import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from glosswork.db import Database
from glosswork.errors import AuthenticationFailedError, ValidationFailedError
from glosswork.services import ServiceBundle
from glosswork.services.principals import role_scope
from glosswork.services.sessions import hash_session_value
from tests.conftest import auth, make_actor

MEMBER_EMAIL = "pw-member@example.com"
ADMIN_EMAIL = "pw-admin@example.com"
PASSWORD = "correct-horse-battery-staple"
ADMIN_PASSWORD = "another-correct-horse-battery"
NEW_PASSWORD = "brand-new-correct-horse-battery"


def _session_actor(principal_id: str, role: str = "member") -> ActorContext:
    """A session-authenticated actor for a given principal, built by hand exactly as
    ``routes/auth.py::_login_actor`` builds one at the edge (the race tests call the
    service layer directly, so no route builds this one for them)."""
    return ActorContext(
        principal_id=principal_id,
        principal_type="user",
        agent_label_id=None,
        auth_method="session",
        surface="ui",
        request_id=str(uuid.uuid4()),
        scope=role_scope(role),
    )


def _seed_users(app: FastAPI) -> ServiceBundle:
    """One admin and one member, both local password accounts, seeded directly
    through the service layer exactly as ``reset_client`` does."""
    services: ServiceBundle = app.state.services
    services.principals.create_user(
        make_actor(),
        email=ADMIN_EMAIL,
        display_name="PW Admin",
        role="admin",
        auth_provider="local",
        password=ADMIN_PASSWORD,
    )
    services.principals.create_user(
        make_actor(),
        email=MEMBER_EMAIL,
        display_name="PW Member",
        role="member",
        auth_provider="local",
        password=PASSWORD,
    )
    return services


def _login(client: TestClient, email: str, password: str) -> Any:
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def _change_password(client: TestClient, current_password: str, new_password: str) -> Any:
    """POST the new route with the ``X-GW-CSRF`` header a session-authenticated POST
    always needs -- taken from the client's own cookie jar, as ``apiRequest``
    would echo it."""
    return client.post(
        "/api/v1/me/password",
        json={"current_password": current_password, "new_password": new_password},
        headers={CSRF_HEADER_NAME: client.cookies["gw_csrf"]},
    )


@pytest.fixture
def own_password_app(tmp_path: Path) -> FastAPI:
    """``cookie_secure=False`` for the reason ``test_auth_routes.py`` gives: the
    TestClient talks plain http, and httpx's cookie jar correctly refuses to attach a
    Secure cookie to it, exactly as a browser would."""
    return create_app(
        Settings(data_dir=tmp_path / "data", cookie_secure=False, embedding_enabled=False)
    )


@pytest.fixture
def own_password_client(own_password_app: FastAPI) -> Iterator[TestClient]:
    """No default ``Authorization`` header: every request here authenticates with a
    cookie unless a test overrides it per request."""
    with TestClient(own_password_app) as test_client:
        _seed_users(own_password_app)
        yield test_client


# ------------------------------------------------------------------- self change


def test_a_self_change_revokes_every_other_session_and_pat_but_keeps_the_caller(
    own_password_app: FastAPI, own_password_client: TestClient
) -> None:
    """The caller's own session survives; every other session and token is revoked."""
    services: ServiceBundle = own_password_app.state.services
    member = services.principals.find_by_email(MEMBER_EMAIL)
    assert member is not None

    client_a = own_password_client
    assert _login(client_a, MEMBER_EMAIL, PASSWORD).status_code == 200
    client_b = TestClient(own_password_app)
    assert _login(client_b, MEMBER_EMAIL, PASSWORD).status_code == 200
    pat = services.tokens.mint(
        make_actor(), name="member laptop", scope="write", principal_id=member.id
    ).plaintext

    changed = _change_password(client_a, PASSWORD, NEW_PASSWORD)
    assert changed.status_code == 200, changed.text
    assert changed.json()["id"] == member.id

    assert client_a.get("/api/v1/me").status_code == 200
    assert client_b.get("/api/v1/me").status_code == 401

    refused = client_a.get("/api/v1/me", headers=auth(pat))
    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "invalid_token"
    assert refused.json()["error"]["details"]["reason"] == "revoked"

    fresh = TestClient(own_password_app)
    assert _login(fresh, MEMBER_EMAIL, PASSWORD).status_code == 401
    assert _login(fresh, MEMBER_EMAIL, NEW_PASSWORD).status_code == 200


# -------------------------------------------------------- wrong current password


def test_a_wrong_current_password_is_refused_and_revokes_nothing(
    own_password_app: FastAPI, own_password_client: TestClient
) -> None:
    services: ServiceBundle = own_password_app.state.services
    member = services.principals.find_by_email(MEMBER_EMAIL)
    assert member is not None

    client_a = own_password_client
    assert _login(client_a, MEMBER_EMAIL, PASSWORD).status_code == 200
    client_b = TestClient(own_password_app)
    assert _login(client_b, MEMBER_EMAIL, PASSWORD).status_code == 200
    pat = services.tokens.mint(
        make_actor(), name="member laptop", scope="write", principal_id=member.id
    ).plaintext

    changed = _change_password(client_a, "definitely-the-wrong-password", NEW_PASSWORD)
    assert changed.status_code == 422, changed.text
    error = changed.json()["error"]
    assert error["code"] == "validation_failed"
    assert error["details"]["field_key"] == "current_password"

    fresh = TestClient(own_password_app)
    assert _login(fresh, MEMBER_EMAIL, PASSWORD).status_code == 200
    assert client_b.get("/api/v1/me").status_code == 200
    assert client_a.get("/api/v1/me", headers=auth(pat)).status_code == 200


# ------------------------------------------------------------- bearer credential


def test_a_pat_cannot_change_a_password_even_with_the_right_current_one(
    own_password_app: FastAPI, own_password_client: TestClient
) -> None:
    """A bearer credential is refused before the password is even
    looked at, and the refusal names no method or path (DD-3)."""
    services: ServiceBundle = own_password_app.state.services
    member = services.principals.find_by_email(MEMBER_EMAIL)
    assert member is not None
    pat = services.tokens.mint(
        make_actor(), name="agent token", scope="write", principal_id=member.id
    ).plaintext

    refused = own_password_client.post(
        "/api/v1/me/password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers=auth(pat),
    )
    assert refused.status_code == 403, refused.text
    error = refused.json()["error"]
    assert error["code"] == "insufficient_scope"
    assert error["details"]["required_auth_method"] == "session"
    assert "/api/v1/me/password" not in error["message"]
    assert "POST" not in error["message"]

    fresh = TestClient(own_password_app)
    assert _login(fresh, MEMBER_EMAIL, PASSWORD).status_code == 200


# ---------------------------------------------------------------- OIDC principal


def test_an_oidc_principal_is_refused_by_provider_not_by_a_password_guess(
    own_password_app: FastAPI, own_password_client: TestClient
) -> None:
    """A session issued directly (``services.sessions.issue``, as
    ``tests/test_sessions.py:54`` does) because an OIDC principal has no password to
    log in with over ``/auth/login``."""
    services: ServiceBundle = own_password_app.state.services
    principal = services.principals.create_user(
        make_actor(),
        email="okta-user@example.com",
        display_name="Okta User",
        role="member",
        auth_provider="oidc",
        external_id="sub-1",
    )
    minted = services.sessions.issue(make_actor(), principal)
    client = TestClient(own_password_app)
    client.cookies.set(SESSION_COOKIE_NAME, minted.cookie_value)
    client.cookies.set(CSRF_COOKIE_NAME, minted.csrf_value)

    me = client.get("/api/v1/me")
    assert me.status_code == 200, me.text
    assert me.json()["auth_provider"] == "oidc"

    changed = _change_password(client, "whatever-the-current-password-might-be", NEW_PASSWORD)
    assert changed.status_code == 422, changed.text
    assert changed.json()["error"]["details"]["field_key"] == "current_password"


# ---------------------------------------------------------------- password floor


def test_a_short_new_password_is_refused_before_the_current_password_is_checked(
    own_password_app: FastAPI, own_password_client: TestClient
) -> None:
    """The floor runs before verification, so a wrong current password
    paired with a too-short new one reports the floor, never "not correct" --
    otherwise a short guess would leak whether the current password was right."""
    services: ServiceBundle = own_password_app.state.services
    member = services.principals.find_by_email(MEMBER_EMAIL)
    assert member is not None
    client_a = own_password_client
    assert _login(client_a, MEMBER_EMAIL, PASSWORD).status_code == 200
    client_b = TestClient(own_password_app)
    assert _login(client_b, MEMBER_EMAIL, PASSWORD).status_code == 200
    pat = services.tokens.mint(
        make_actor(), name="member laptop", scope="write", principal_id=member.id
    ).plaintext

    changed = _change_password(client_a, "definitely-the-wrong-password", "x")
    assert changed.status_code == 422, changed.text
    error = changed.json()["error"]
    assert "min_length" in error["details"]
    assert "not correct" not in error["message"]

    assert client_b.get("/api/v1/me").status_code == 200
    assert client_a.get("/api/v1/me", headers=auth(pat)).status_code == 200


# -------------------------------------------------------------- admin reset race


def test_a_concurrent_admin_reset_wins_the_race_over_a_self_change(
    services: ServiceBundle, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An administrator's reset landing mid-change wins, at the service layer.

    Removing ``change_own_password``'s in-transaction guard makes this fail for the
    intended reason, which is what shows the guard is what it measures.
    """
    member = services.principals.create_user(
        make_actor(),
        email="racer@example.com",
        display_name="Racer",
        role="member",
        auth_provider="local",
        password=PASSWORD,
    )
    minted = services.sessions.issue(make_actor(), member)
    keep_hash = hash_session_value(minted.cookie_value)
    admins_password = "an-administrators-reset-password"

    real_verify = services.principals.passwords.verify

    def racing_verify(password_hash: str, password: str) -> bool:
        # Report the true answer, but land a concurrent administrator's reset in the
        # gap between that answer and this method's own write -- exactly what can
        # happen for real between verification and the transaction.
        result = real_verify(password_hash, password)
        if result:
            services.principals.set_password(make_actor(), member.id, admins_password)
        return result

    monkeypatch.setattr(services.principals.passwords, "verify", racing_verify)

    actor = _session_actor(member.id)
    with pytest.raises(ValidationFailedError) as excinfo:
        services.principals.change_own_password(  # type: ignore[attr-defined]
            actor, PASSWORD, NEW_PASSWORD, keep_session_hash=keep_hash
        )
    assert excinfo.value.field_key == "current_password"

    assert services.principals.verify_password("racer@example.com", admins_password).id == member.id
    with pytest.raises(AuthenticationFailedError):
        services.principals.verify_password("racer@example.com", NEW_PASSWORD)

    with db.read() as conn:
        actor_rows = conn.execute(
            text(
                "SELECT principal_id FROM audit_events WHERE entity_type = 'principal' "
                "AND action = 'update' AND field_key = 'password_hash'"
            )
        ).all()
    assert member.id not in {row[0] for row in actor_rows}


# -------------------------------------------------------------------- hash guard


def test_a_concurrent_hash_change_that_keeps_the_session_still_wins_the_race(
    services: ServiceBundle, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hash guard alone.

    The simulated administrator reset in the race test above changes the hash **and**
    deletes every session the member holds, so either guard inside
    ``_guard_against_concurrent_change`` independently catches that race: a mutation
    removing either one alone still leaves that test passing. This test decouples
    the two effects -- the racing write keeps the member's own kept session alive
    (an administrator's ``set_password`` with ``keep_session_hash`` naming it) -- so
    only the hash comparison can be what refuses the self-change.
    """
    member = services.principals.create_user(
        make_actor(),
        email="racer-hash@example.com",
        display_name="Racer Hash",
        role="member",
        auth_provider="local",
        password=PASSWORD,
    )
    minted = services.sessions.issue(make_actor(), member)
    keep_hash = hash_session_value(minted.cookie_value)
    admins_password = "an-administrators-reset-password"

    real_verify = services.principals.passwords.verify

    def racing_verify(password_hash: str, password: str) -> bool:
        # Report the true answer, but land a concurrent administrator's reset in the
        # gap between that answer and this method's own write -- one that spares the
        # member's own session, unlike the first race test's.
        result = real_verify(password_hash, password)
        if result:
            services.principals.set_password(
                make_actor(), member.id, admins_password, keep_session_hash=keep_hash
            )
            # Before trusting the guard under test: confirm the racing write actually
            # kept the session alive, so this test cannot pass because the setup did
            # the wrong thing.
            with db.read() as conn:
                live_hashes = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT session_hash FROM sessions WHERE principal_id = :pid "
                            "AND revoked_at IS NULL"
                        ),
                        {"pid": member.id},
                    ).all()
                }
            assert keep_hash in live_hashes, "setup bug: the kept session was not preserved"
        return result

    monkeypatch.setattr(services.principals.passwords, "verify", racing_verify)

    actor = _session_actor(member.id)
    with pytest.raises(ValidationFailedError) as excinfo:
        services.principals.change_own_password(  # type: ignore[attr-defined]
            actor, PASSWORD, NEW_PASSWORD, keep_session_hash=keep_hash
        )
    assert excinfo.value.field_key == "current_password"

    assert (
        services.principals.verify_password("racer-hash@example.com", admins_password).id
        == member.id
    )
    with pytest.raises(AuthenticationFailedError):
        services.principals.verify_password("racer-hash@example.com", NEW_PASSWORD)


# ----------------------------------------------------------------- session guard


def test_a_concurrent_session_revoke_that_keeps_the_hash_still_wins_the_race(
    services: ServiceBundle, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session guard alone.

    The counterpart of the hash-guard test: the racing write revokes the member's kept session
    through ``SessionService.revoke_by_cookie`` -- the existing logout method
    (``routes/auth.py``'s ``DELETE /session`` uses the same one), not a new
    production method -- **without** touching the password hash at all. Only the
    kept-session liveness check can be what refuses the self-change here.
    """
    member = services.principals.create_user(
        make_actor(),
        email="racer-session@example.com",
        display_name="Racer Session",
        role="member",
        auth_provider="local",
        password=PASSWORD,
    )
    original_hash = member.password_hash
    assert original_hash is not None
    minted = services.sessions.issue(make_actor(), member)
    keep_hash = hash_session_value(minted.cookie_value)

    real_verify = services.principals.passwords.verify

    def racing_verify(password_hash: str, password: str) -> bool:
        # Report the true answer, but revoke the member's kept session -- and nothing
        # else -- in the gap between that answer and this method's own write.
        result = real_verify(password_hash, password)
        if result:
            services.sessions.revoke_by_cookie(make_actor(), minted.cookie_value)
            # Before trusting the guard under test: confirm the racing write left the
            # hash untouched, so this test cannot pass because the setup did the
            # wrong thing.
            with db.read() as conn:
                row = conn.execute(
                    text("SELECT password_hash FROM principals WHERE id = :pid"),
                    {"pid": member.id},
                ).one()
            assert row[0] == original_hash, "setup bug: the racing write changed the hash"
        return result

    monkeypatch.setattr(services.principals.passwords, "verify", racing_verify)

    actor = _session_actor(member.id)
    with pytest.raises(ValidationFailedError) as excinfo:
        services.principals.change_own_password(  # type: ignore[attr-defined]
            actor, PASSWORD, NEW_PASSWORD, keep_session_hash=keep_hash
        )
    assert excinfo.value.field_key == "current_password"

    assert (
        services.principals.verify_password("racer-session@example.com", PASSWORD).id == member.id
    )
    with pytest.raises(AuthenticationFailedError):
        services.principals.verify_password("racer-session@example.com", NEW_PASSWORD)


# ---------------------------------------------------------------- account window


@pytest.fixture
def small_pair_app(tmp_path: Path) -> FastAPI:
    """A tiny per-account ceiling, so the fourth wrong current password trips
    the pair window rather than needing ten."""
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            cookie_secure=False,
            embedding_enabled=False,
            login_max_attempts=3,
        )
    )


def test_four_wrong_current_passwords_trip_the_accounts_own_window(
    small_pair_app: FastAPI,
) -> None:
    with TestClient(small_pair_app) as client:
        _seed_users(small_pair_app)
        assert _login(client, MEMBER_EMAIL, PASSWORD).status_code == 200
        for _ in range(3):
            attempt = _change_password(client, "wrong-current-password", NEW_PASSWORD)
            assert attempt.status_code == 422, attempt.text

        refused = _change_password(client, "wrong-current-password", NEW_PASSWORD)
        assert refused.status_code == 429, refused.text
        error = refused.json()["error"]
        assert error["code"] == "rate_limited"
        assert error["details"]["window"] == "account"
        assert "password change" in error["message"]
        assert "login" not in error["message"]


# ----------------------------------------------------------------- source window


@pytest.fixture
def small_source_app(tmp_path: Path) -> FastAPI:
    """``login_ip_max_attempts=4`` admits the one sign-in this scenario needs
    plus three alternating attempts; the fifth call overall is the one the arithmetic
    predicts as refused. The limiter refuses the call that finds the window already at
    its ceiling, so a ceiling of N admits N calls and refuses call N + 1 (measured)."""
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            cookie_secure=False,
            embedding_enabled=False,
            login_ip_max_attempts=4,
        )
    )


def test_login_and_password_change_share_one_source_address_window(
    small_source_app: FastAPI,
) -> None:
    """The two routes' attempts are counted into the same per-address budget,
    so alternating between them still trips on the predicted call -- here, the fifth
    -- and reports the ``source`` window regardless of which route it used."""
    with TestClient(small_source_app) as client:
        _seed_users(small_source_app)
        assert _login(client, MEMBER_EMAIL, PASSWORD).status_code == 200  # attempt 1

        wrong_login = client.post(
            "/api/v1/auth/login", json={"email": MEMBER_EMAIL, "password": "not-it"}
        )
        assert wrong_login.status_code == 401, wrong_login.text  # attempt 2

        wrong_change = _change_password(client, "not-it-either", NEW_PASSWORD)
        assert wrong_change.status_code == 422, wrong_change.text  # attempt 3

        wrong_login_2 = client.post(
            "/api/v1/auth/login", json={"email": MEMBER_EMAIL, "password": "still-not-it"}
        )
        assert wrong_login_2.status_code == 401, wrong_login_2.text  # attempt 4

        refused = _change_password(client, "nope", NEW_PASSWORD)  # attempt 5
        assert refused.status_code == 429, refused.text
        error = refused.json()["error"]
        assert error["code"] == "rate_limited"
        assert error["details"]["window"] == "source"


# ------------------------------------------------------------------- audit trail


def test_a_self_change_leaves_a_legible_audit_trail_and_leaks_no_password(
    own_password_app: FastAPI,
    own_password_client: TestClient,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Uses ``capfd`` rather than ``caplog``: this deployment's structlog
    configuration renders JSON straight to stdout
    (``tests/test_mcp_transport.py:314-331``), so nothing reaches the stdlib handler
    ``caplog`` installs, and a ``caplog``-based assertion here would pass regardless
    of whether a password leaked -- exactly the vacuous-fence trap AGENTS.md warns
    against."""
    services: ServiceBundle = own_password_app.state.services
    member = services.principals.find_by_email(MEMBER_EMAIL)
    assert member is not None

    client_a = own_password_client
    assert _login(client_a, MEMBER_EMAIL, PASSWORD).status_code == 200
    client_b = TestClient(own_password_app)
    assert _login(client_b, MEMBER_EMAIL, PASSWORD).status_code == 200
    services.tokens.mint(make_actor(), name="member laptop", scope="write", principal_id=member.id)

    changed = _change_password(client_a, PASSWORD, NEW_PASSWORD)
    assert changed.status_code == 200, changed.text

    admin_client = TestClient(own_password_app)
    assert _login(admin_client, ADMIN_EMAIL, ADMIN_PASSWORD).status_code == 200
    events = admin_client.get("/api/v1/audit-events")
    assert events.status_code == 200, events.text
    rows = events.json()["events"]

    hash_rows = [
        r
        for r in rows
        if r["entity_type"] == "principal"
        and r["action"] == "update"
        and r["field_key"] == "password_hash"
    ]
    assert len(hash_rows) == 1, rows
    assert hash_rows[0]["principal_id"] == member.id

    token_revokes = [
        r for r in rows if r["entity_type"] == "access_token" and r["action"] == "revoke"
    ]
    session_revokes = [r for r in rows if r["entity_type"] == "session" and r["action"] == "revoke"]
    assert len(token_revokes) == 1, rows
    assert len(session_revokes) == 1, rows

    dump = str(rows)
    assert PASSWORD not in dump
    assert NEW_PASSWORD not in dump
    assert "$argon2id$" not in dump

    out, _err = capfd.readouterr()
    assert PASSWORD not in out
    assert NEW_PASSWORD not in out


# ----------------------------------------------------------------- auth provider


def test_me_reports_a_local_auth_provider_for_a_local_session(
    own_password_client: TestClient,
) -> None:
    assert _login(own_password_client, MEMBER_EMAIL, PASSWORD).status_code == 200
    me = own_password_client.get("/api/v1/me")
    assert me.status_code == 200, me.text
    assert me.json()["auth_provider"] == "local"
