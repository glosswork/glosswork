"""Authentication over HTTP: login, logout, the OIDC redirect flow, session cookies,
and CSRF enforcement (FR-A3, FR-I1, DD-9, DD-10).

The headline security assertions for sign-in live here: the forged-write test, the
bearer exemption, the deactivation and demotion parity tests, the precedence test, and
the cookie-attribute assertions against the real ``Set-Cookie`` header rather than a
config object.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import text

from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.cookies import CSRF_HEADER_NAME
from glosswork.services import ServiceBundle
from glosswork.services.oidc import StaticJwksSource
from tests.conftest import auth, make_actor, mint_scope_tokens

PASSWORD = "correct-horse-battery-staple"
ISSUER = "https://example.okta.com/oauth2/default"
CLIENT_ID = "0oa1glosswork"
KID = "test-key-1"


# --------------------------------------------------------------------------- helpers


def _set_cookie(response: Any, name: str) -> str:
    """The raw ``Set-Cookie`` header text for one cookie, so attributes can be
    asserted against the header itself rather than a parsed/normalized view of it."""
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    raise AssertionError(f"no Set-Cookie header for {name!r} in {response.headers}")


@pytest.fixture
def local_login_app(tmp_path: Path) -> FastAPI:
    # `cookie_secure=False`: TestClient talks to "http://testserver", and a Secure
    # cookie is (correctly) never attached to a plain-http request by httpx's own
    # cookie jar, exactly as a real browser would refuse it. The dedicated Secure-
    # attribute test below builds its own app with the (default) true value instead.
    settings = Settings(data_dir=tmp_path / "data", cookie_secure=False, embedding_enabled=False)
    return create_app(settings)


@pytest.fixture
def local_login_client(local_login_app: FastAPI) -> TestClient:
    with TestClient(local_login_app) as client:
        services: ServiceBundle = local_login_app.state.services
        services.principals.create_user(
            make_actor(),
            email="dana@example.com",
            display_name="Dana",
            role="member",
            password=PASSWORD,
        )
        yield client


def _login(client: TestClient, email: str = "dana@example.com", password: str = PASSWORD) -> Any:
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


# ---------------------------------------------------------------------------- modes


def test_auth_modes_reports_each_configured_mode(tmp_path: Path) -> None:
    for mode, expected in (
        ("standalone", {"standalone": True, "oidc": False, "email_code": False}),
        ("both", {"standalone": True, "oidc": True, "email_code": False}),
    ):
        settings = Settings(
            data_dir=tmp_path / mode,
            auth_mode=mode,
            oidc_issuer=ISSUER if mode != "standalone" else None,
            oidc_client_id=CLIENT_ID if mode != "standalone" else None,
            base_url="https://glosswork.example.com" if mode != "standalone" else None,
            embedding_enabled=False,
        )
        with TestClient(create_app(settings)) as client:
            response = client.get("/api/v1/auth/modes")
        assert response.status_code == 200
        assert response.json() == expected


def test_auth_modes_needs_no_credential(local_login_app: FastAPI) -> None:
    with TestClient(local_login_app) as client:
        response = client.get("/api/v1/auth/modes")
    assert response.status_code == 200


# ----------------------------------------------------------------------------- login


def test_login_succeeds_and_returns_me_shaped_body(local_login_client: TestClient) -> None:
    response = _login(local_login_client)
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "dana@example.com"
    assert body["scope"] == "write"
    assert body["auth_method"] == "session"


def test_a_members_session_is_accepted_on_write_and_refused_on_admin_routes(
    local_login_client: TestClient,
) -> None:
    """The companion to the demotion test: a `member` who was never an admin gets the
    same treatment a demoted one does."""
    _login(local_login_client)
    csrf_cookie = local_login_client.cookies.get("gw_csrf")

    write_route = local_login_client.post(
        "/api/v1/access-tokens",
        json={"name": "member-token", "scope": "read"},
        headers={CSRF_HEADER_NAME: csrf_cookie},
    )
    assert write_route.status_code == 201

    admin_route = local_login_client.get("/api/v1/principals")
    assert admin_route.status_code == 403
    assert admin_route.json()["error"]["code"] == "insufficient_scope"


def test_login_sets_session_and_csrf_cookies_with_the_right_attributes(
    local_login_client: TestClient,
) -> None:
    response = _login(local_login_client)
    session_cookie = _set_cookie(response, "gw_session")
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()
    assert "Path=/" in session_cookie
    assert "Domain=" not in session_cookie
    assert "Max-Age" not in session_cookie  # no client-side lifetime (DD-9)

    csrf_cookie = _set_cookie(response, "gw_csrf")
    assert "HttpOnly" not in csrf_cookie  # the SPA must read it with script
    assert "samesite=lax" in csrf_cookie.lower()


def test_login_sets_the_secure_attribute_when_configured(tmp_path: Path) -> None:
    """`GW_COOKIE_SECURE` defaults to true; asserted separately from the rest of the
    attribute test above, which runs with it off so the same client can actually carry
    the cookie back to "http://testserver" — exactly as a real browser would refuse a
    Secure cookie over plain HTTP, which is the point of the default. This test only
    inspects the header on the login response itself, so it never needs the cookie to
    round-trip."""
    settings = Settings(
        data_dir=tmp_path / "data", embedding_enabled=False
    )  # cookie_secure defaults to True
    app = create_app(settings)
    with TestClient(app) as client:
        services: ServiceBundle = app.state.services
        services.principals.create_user(
            make_actor(), email="dana@example.com", display_name="Dana", password=PASSWORD
        )
        response = _login(client)
        assert "Secure" in _set_cookie(response, "gw_session")
        assert "Secure" in _set_cookie(response, "gw_csrf")


def test_login_fails_indistinguishably_for_unknown_email_and_wrong_password(
    local_login_client: TestClient,
) -> None:
    wrong_email = _login(local_login_client, email="nobody@example.com")
    wrong_password = _login(local_login_client, password="not the password")
    assert wrong_email.status_code == wrong_password.status_code == 401
    assert wrong_email.json() == wrong_password.json()
    assert wrong_email.json()["error"]["code"] == "invalid_credentials"


def test_a_cookie_authenticated_request_can_read(local_login_client: TestClient) -> None:
    _login(local_login_client)
    response = local_login_client.get("/api/v1/me")
    assert response.status_code == 200
    assert response.json()["auth_method"] == "session"


def test_login_rotates_the_session_identifier_and_deletes_the_prior_row(
    local_login_client: TestClient,
) -> None:
    first = _login(local_login_client)
    first_session_cookie = local_login_client.cookies.get("gw_session")
    first_csrf_cookie = local_login_client.cookies.get("gw_csrf")
    second = _login(local_login_client)
    second_session_cookie = local_login_client.cookies.get("gw_session")
    second_csrf_cookie = local_login_client.cookies.get("gw_csrf")
    assert first.status_code == second.status_code == 200
    assert first_session_cookie != second_session_cookie
    assert first_csrf_cookie != second_csrf_cookie  # rotated with the session (DD-10)

    replay = local_login_client.get("/api/v1/me", cookies={"gw_session": first_session_cookie})
    assert replay.status_code == 401


# ---------------------------------------------------------------------------- logout


def test_logout_deletes_the_row_and_a_replayed_cookie_is_401(
    local_login_client: TestClient,
) -> None:
    _login(local_login_client)
    session_cookie = local_login_client.cookies.get("gw_session")
    csrf_cookie = local_login_client.cookies.get("gw_csrf")

    logout = local_login_client.delete(
        "/api/v1/auth/session", headers={CSRF_HEADER_NAME: csrf_cookie}
    )
    assert logout.status_code == 204

    replay = local_login_client.get("/api/v1/me", cookies={"gw_session": session_cookie})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "invalid_token"


# --------------------------------------------------------------------------- CSRF


def test_forged_write_with_no_csrf_header_is_rejected_and_nothing_happens(
    local_login_client: TestClient, local_login_app: FastAPI
) -> None:
    """The headline security assertion for sessions: a request shaped exactly as a
    cross-origin forgery — a valid session cookie, a non-safe method, a write route,
    and no X-GW-CSRF header — is rejected, and the write does not happen, and it
    leaves no audit row (rejection happens in ASGI middleware, before the app --
    and therefore before any service or audit write -- ever runs)."""
    _login(local_login_client)
    services: ServiceBundle = local_login_app.state.services
    before = services.principals.list_principals()
    with local_login_app.state.db.read() as conn:
        audit_before = conn.execute(text("SELECT COUNT(*) FROM audit_events")).scalar_one()

    response = local_login_client.post(
        "/api/v1/access-tokens", json={"name": "forged", "scope": "read"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"

    after = services.principals.list_principals()
    assert before == after
    with local_login_app.state.db.read() as conn:
        rows = conn.execute(text("SELECT * FROM access_tokens WHERE name = 'forged'")).all()
        audit_after = conn.execute(text("SELECT COUNT(*) FROM audit_events")).scalar_one()
    assert rows == []
    assert audit_after == audit_before


def test_mismatched_csrf_header_is_rejected(local_login_client: TestClient) -> None:
    _login(local_login_client)
    response = local_login_client.post(
        "/api/v1/access-tokens",
        json={"name": "x", "scope": "read"},
        headers={CSRF_HEADER_NAME: "not-the-real-token"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_correctly_tokened_write_succeeds(local_login_client: TestClient) -> None:
    _login(local_login_client)
    csrf_cookie = local_login_client.cookies.get("gw_csrf")
    response = local_login_client.post(
        "/api/v1/access-tokens",
        json={"name": "legit", "scope": "read"},
        headers={CSRF_HEADER_NAME: csrf_cookie},
    )
    assert response.status_code == 201


def test_bearer_authenticated_write_with_no_csrf_header_succeeds(client: TestClient) -> None:
    """The exemption is itself tested: a PAT-bearing POST with no CSRF header
    succeeds, so a future tightening cannot silently take the agent surface down."""
    response = client.post("/api/v1/access-tokens", json={"name": "agent-token", "scope": "read"})
    assert response.status_code == 201


def test_precedence_bearer_wins_over_a_coexisting_cookie_and_is_csrf_exempt(
    local_login_client: TestClient, local_login_app: FastAPI
) -> None:
    """DD-9's precedence rule, and the condition DD-10's enforcement is keyed on."""
    _login(local_login_client)
    tokens = mint_scope_tokens(local_login_app.state.services)
    response = local_login_client.post(
        "/api/v1/access-tokens",
        json={"name": "via-bearer", "scope": "read"},
        headers=auth(tokens["admin"]),  # no X-GW-CSRF header
    )
    assert response.status_code == 201


def test_no_route_module_enforces_csrf() -> None:
    """Central enforcement, declared once, in `middleware.py` — never in a handler
    (in the same style as `test_no_route_module_reads_scope`).

    `routes/auth.py` legitimately names the `gw_csrf` *cookie* (setting and clearing
    it alongside the session cookie on every credential-issuing response), so the
    pattern targets the enforcement vocabulary specifically — the literal header name
    and a call to `verify_csrf` — not the bare substring `csrf`, which a bare-word
    grep would also catch in that legitimate cookie-name usage.
    """
    routes_dir = Path(__file__).resolve().parents[1] / "src" / "glosswork" / "routes"
    result = subprocess.run(
        ["grep", "-rniE", r"X-GW-CSRF|verify_csrf|CSRF_HEADER_NAME", str(routes_dir)],
        capture_output=True,
        text=True,
    )
    assert result.stdout == "", (
        "A route module references CSRF enforcement directly. Enforcement belongs in "
        f"middleware.py, not a handler:\n{result.stdout}"
    )


def test_no_get_or_head_route_under_api_mutates_except_the_oidc_callback(
    local_login_app: FastAPI,
) -> None:
    """What makes SameSite=Lax sufficient as the first CSRF layer rather than assumed
    sufficient: no GET/HEAD route performs a write, with the OIDC callback as the
    single named exception (it is the one leg of the flow a redirect can reach).

    Scoped to each route's own endpoint function's source via `inspect.getsource`,
    not a whole-file grep: a write-verb call anywhere else in the same module (a POST
    handler sharing the file with a GET one, say) must not make this fail on a route
    that never touches it.
    """
    import inspect
    import re

    from glosswork.scopes import flatten_routes

    write_verb = re.compile(
        r"services\.\w+\.(create|update|delete|revoke|deactivate|mint|issue|"
        r"upload|approve|reject|apply|bulk_update|revert)\w*\("
    )
    allowlisted = {"oidc_callback"}
    offenders: list[tuple[str, str]] = []
    for route in flatten_routes(local_login_app):
        methods = getattr(route, "methods", None) or set()
        endpoint = getattr(route, "endpoint", None)
        if not methods & {"GET", "HEAD"} or endpoint is None:
            continue
        name = getattr(endpoint, "__name__", "")
        if name in allowlisted:
            continue
        try:
            source = inspect.getsource(endpoint)
        except (OSError, TypeError):
            continue
        if write_verb.search(source):
            offenders.append((getattr(route, "path", "?"), name))
    assert offenders == []


# ---------------------------------------------------------------------------- CORS


def test_no_cors_middleware_is_registered(local_login_app: FastAPI) -> None:
    """There is no CORSMiddleware today; adding one with allow_credentials=True and a
    permissive origin would defeat both CSRF layers at once, so the absence is
    asserted rather than left true by accident."""
    middleware_classes = {m.cls.__name__ for m in local_login_app.user_middleware}
    assert "CORSMiddleware" not in middleware_classes


# ------------------------------------------------------------------ audit and parity


def test_cookie_authenticated_write_produces_an_audit_row_with_auth_method_session(
    local_login_client: TestClient, local_login_app: FastAPI
) -> None:
    _login(local_login_client)
    csrf_cookie = local_login_client.cookies.get("gw_csrf")
    response = local_login_client.post(
        "/api/v1/access-tokens",
        json={"name": "audited", "scope": "read"},
        headers={CSRF_HEADER_NAME: csrf_cookie},
    )
    assert response.status_code == 201
    with local_login_app.state.db.read() as conn:
        row = conn.execute(
            text(
                "SELECT auth_method, surface FROM audit_events "
                "WHERE entity_type = 'access_token' AND action = 'create' "
                "ORDER BY id DESC LIMIT 1"
            )
        ).one()
    assert row[0] == "session"
    assert row[1] == "ui"


def test_no_credential_plaintext_appears_in_audit_rows_or_log_lines(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """The token assertion (`test_principal_and_token_mutations_write_audit_rows`),
    extended to the credentials sign-in introduces: the session cookie, the CSRF token,
    and the password used to obtain them must appear in no audit row and no
    structured log line (FR-P5, FR-D4)."""
    settings = Settings(data_dir=tmp_path / "data", cookie_secure=False, embedding_enabled=False)
    app = create_app(settings)
    with TestClient(app) as client:
        services: ServiceBundle = app.state.services
        services.principals.create_user(
            make_actor(),
            email="dana@example.com",
            display_name="Dana",
            password=PASSWORD,
        )
        login = _login(client)
        session_cookie = client.cookies.get("gw_session")
        csrf_cookie = client.cookies.get("gw_csrf")
        assert login.status_code == 200 and session_cookie and csrf_cookie

        client.delete("/api/v1/auth/session", headers={CSRF_HEADER_NAME: csrf_cookie})

        with app.state.db.read() as conn:
            audit_dump = str(conn.execute(text("SELECT * FROM audit_events")).all())

    stdout, _stderr = capfd.readouterr()

    for secret in (PASSWORD, session_cookie, csrf_cookie):
        assert secret not in audit_dump, secret
        assert secret not in stdout, secret


def test_a_pat_write_is_still_auth_method_pat(client: TestClient, app: FastAPI) -> None:
    response = client.post("/api/v1/access-tokens", json={"name": "via-pat", "scope": "read"})
    assert response.status_code == 201
    with app.state.db.read() as conn:
        row = conn.execute(
            text(
                "SELECT auth_method FROM audit_events "
                "WHERE entity_type = 'access_token' AND action = 'create' "
                "ORDER BY id DESC LIMIT 1"
            )
        ).one()
    assert row[0] == "pat"


def test_deactivation_kills_both_a_live_session_and_a_live_pat_on_the_next_request(
    local_login_client: TestClient, local_login_app: FastAPI
) -> None:
    """The deactivation-parity test DD-9 exists for: one test deactivates a principal
    holding both a live session cookie and a live PAT, and asserts *both* are 401 on
    the very next request."""
    services: ServiceBundle = local_login_app.state.services
    _login(local_login_client)
    principal = services.principals.find_by_email("dana@example.com")
    assert principal is not None
    minted = services.tokens.mint(
        make_actor(), name="dana-pat", scope="write", principal_id=principal.id
    )

    services.principals.deactivate_principal(make_actor(), principal.id)

    session_response = local_login_client.get("/api/v1/me")
    assert session_response.status_code == 401
    pat_response = local_login_client.get("/api/v1/me", headers=auth(minted.plaintext))
    assert pat_response.status_code == 401


def test_demoting_an_administrator_mid_session_drops_scope_with_no_relogin(
    tmp_path: Path,
) -> None:
    # The MCP session manager can only be entered once per app instance (see
    # tests/test_mcp_transport.py's own note on this), so everything — seeding the two
    # admins, logging in, and the demotion itself — happens inside one `with
    # TestClient(app)` block rather than a throwaway bootstrap app plus a second one.
    settings = Settings(data_dir=tmp_path / "data", cookie_secure=False, embedding_enabled=False)
    app = create_app(settings)
    with TestClient(app) as client:
        services: ServiceBundle = app.state.services
        # A second admin so demoting the target does not trip the last-admin guard.
        services.principals.create_user(
            make_actor(),
            email="other-admin@example.com",
            display_name="Other Admin",
            role="admin",
            password=PASSWORD,
        )
        target = services.principals.create_user(
            make_actor(),
            email="target-admin@example.com",
            display_name="Target Admin",
            role="admin",
            password=PASSWORD,
        )

        login = _login(client, email="target-admin@example.com")
        assert login.json()["scope"] == "admin"

        services.principals.update_principal(make_actor(), target.id, role="member")

        admin_read = client.get("/api/v1/principals")
        assert admin_read.status_code == 403
        assert admin_read.json()["error"]["code"] == "insufficient_scope"

        write_still_works = client.get("/api/v1/me")
        assert write_still_works.status_code == 200
        assert write_still_works.json()["scope"] == "write"


# ------------------------------------------------------------------------------ oidc


@pytest.fixture(scope="module")
def oidc_keypair() -> Any:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update({"kid": KID, "alg": "RS256", "use": "sig"})
    return private, jwk


def _oidc_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        auth_mode="oidc",
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        base_url="https://glosswork.example.com",
        cookie_secure=False,  # see local_login_app's comment: TestClient is plain http
        embedding_enabled=False,
    )


def _id_token(private: Any, **overrides: Any) -> str:
    import jwt

    now = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "00u1oidcuser",
        "exp": int((now + timedelta(minutes=10)).timestamp()),
        "iat": int(now.timestamp()),
        "email": "grace@example.com",
        "name": "Grace Hopper",
        "groups": [],
    }
    claims.update(overrides)
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": KID})


def test_oidc_start_sets_the_transaction_cookie_and_redirects(tmp_path: Path) -> None:
    settings = _oidc_settings(tmp_path)
    with TestClient(create_app(settings), follow_redirects=False) as client:
        response = client.get("/api/v1/auth/oidc/start")
    assert response.status_code == 302
    assert response.headers["location"].startswith(f"{ISSUER}/v1/authorize?")
    tx_cookie = _set_cookie(response, "gw_oidc_tx")
    assert "HttpOnly" in tx_cookie
    assert "Max-Age=600" in tx_cookie
    assert "Path=/api/v1/auth/oidc" in tx_cookie


def test_oidc_callback_completes_login_with_no_network_egress(
    tmp_path: Path, oidc_keypair: Any
) -> None:
    private, jwk = oidc_keypair
    settings = _oidc_settings(tmp_path)
    jwks_source = StaticJwksSource({KID: __import__("jwt").PyJWK.from_dict(jwk)})

    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        return httpx2.Response(200, json={"id_token": _id_token(private)})

    http_client = httpx2.Client(transport=httpx2.MockTransport(handler))
    app = create_app(settings, jwks_source=jwks_source, oidc_http_client=http_client)

    with TestClient(app, follow_redirects=False) as client:
        start = client.get("/api/v1/auth/oidc/start")
        tx_cookie_header = _set_cookie(start, "gw_oidc_tx")
        tx_value = tx_cookie_header.split(";")[0].split("=", 1)[1]
        state = tx_value.split(":", 1)[0]

        # The transaction cookie is already in the client's jar from `/start`'s
        # Set-Cookie, exactly as a browser would carry it to the callback redirect.
        callback = client.get(
            "/api/v1/auth/oidc/callback", params={"code": "the-code", "state": state}
        )
        assert callback.status_code == 302
        assert calls  # the token endpoint really was called
        session_cookie = _set_cookie(callback, "gw_session")
        assert "HttpOnly" in session_cookie
        tx_cleared = _set_cookie(callback, "gw_oidc_tx")
        assert "Max-Age=0" in tx_cleared or "expires=" in tx_cleared.lower()

        me = client.get("/api/v1/me")
        assert me.status_code == 200
        assert me.json()["email"] == "grace@example.com"
        assert me.json()["auth_method"] == "session"


def test_oidc_callback_refuses_a_mismatched_state_before_exchanging_the_code(
    tmp_path: Path, oidc_keypair: Any
) -> None:
    private, jwk = oidc_keypair
    settings = _oidc_settings(tmp_path)
    jwks_source = StaticJwksSource({KID: __import__("jwt").PyJWK.from_dict(jwk)})

    def handler(request: httpx2.Request) -> httpx2.Response:  # pragma: no cover
        raise AssertionError("the token endpoint must not be reached")

    http_client = httpx2.Client(transport=httpx2.MockTransport(handler))
    app = create_app(settings, jwks_source=jwks_source, oidc_http_client=http_client)

    with TestClient(app, follow_redirects=False) as client:
        client.get("/api/v1/auth/oidc/start")  # sets gw_oidc_tx in the client's jar

        response = client.get(
            "/api/v1/auth/oidc/callback",
            params={"code": "the-code", "state": "not-the-saved-state"},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_oidc_callback_refuses_a_missing_state(tmp_path: Path) -> None:
    settings = _oidc_settings(tmp_path)
    with TestClient(create_app(settings), follow_redirects=False) as client:
        response = client.get("/api/v1/auth/oidc/callback", params={"code": "the-code"})
    assert response.status_code == 401
