"""``PatTokenResolver``: verifying a presented credential (DD-8, FR-I4).

What is tested here is the resolver's own behavior — unknown, revoked, expired,
inactive-principal, and *absent* — plus the property DD-8 was designed to deliver:
swapping the resolver changes nothing else on either surface, so the same refusal
reaches a REST client as a 401 envelope and an MCP client as a JSON-RPC error carrying
the same code.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server import MCPServer

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.auth import PatTokenResolver
from glosswork.db import Database
from glosswork.errors import TokenRefusedError
from glosswork.services import ServiceBundle
from glosswork.services.tokens import LAST_USED_COARSENESS
from glosswork.timeutil import format_datetime, utc_now
from tests.conftest import auth, make_actor, mint_scope_tokens
from tests.mcp_support import memory_session, refusal_opening


@pytest.fixture
def resolver(services: ServiceBundle) -> PatTokenResolver:
    return PatTokenResolver(lambda: services)


def _bearer(token: str) -> str:
    return f"Bearer {token}"


# --------------------------------------------------------------- happy path


def test_a_minted_token_resolves_to_its_principal_and_scope(
    services: ServiceBundle, resolver: PatTokenResolver, pat: dict[str, str]
) -> None:
    identity = resolver.resolve(_bearer(pat["write"]))
    assert identity.principal_id == BOOTSTRAP_PRINCIPAL_ID
    assert identity.scope == "write"
    assert identity.auth_method == "pat"
    assert identity.principal_type == "service_account"


def test_scope_comes_from_the_token_not_from_the_role(
    services: ServiceBundle, resolver: PatTokenResolver
) -> None:
    """Scope and role are two different things. The seeded bootstrap
    principal has the `admin` role, yet its `read` token resolves to `read` scope:
    central enforcement compares only the credential's scope, and the role's job is to
    cap what may be minted."""
    minted = services.tokens.mint(make_actor(), name="narrow", scope="read")
    assert resolver.resolve(_bearer(minted.plaintext)).scope == "read"


# ------------------------------------------------------------------ refusals


def test_a_missing_authorization_header_is_refused(resolver: PatTokenResolver) -> None:
    """The most consequential refusal. An absent header must never resolve to the
    bootstrap principal at `admin` scope, which is what an unauthenticated REST API
    would amount to."""
    with pytest.raises(TokenRefusedError) as excinfo:
        resolver.resolve(None)
    assert excinfo.value.reason == "missing"
    assert "gw_pat_" in str(excinfo.value)


def test_an_unknown_token_is_refused(resolver: PatTokenResolver) -> None:
    with pytest.raises(TokenRefusedError) as excinfo:
        resolver.resolve(_bearer("gw_pat_thisisnotarealtokenatallreally"))
    assert excinfo.value.reason == "unknown"


def test_an_old_interim_selector_is_refused_like_any_other_unknown_token(
    resolver: PatTokenResolver,
) -> None:
    """The interim literals an early build accepted are simply unknown strings. Named here
    because an agent harness or a stale bookmark carrying one must fail loudly rather
    than be silently promoted, which is the exact failure DD-8 called out."""
    for legacy in ("gw_pat_read", "gw_pat_write", "gw_pat_admin"):
        with pytest.raises(TokenRefusedError) as excinfo:
            resolver.resolve(_bearer(legacy))
        assert excinfo.value.reason == "unknown"


def test_a_revoked_token_stops_resolving_on_the_next_request(
    services: ServiceBundle, resolver: PatTokenResolver
) -> None:
    minted = services.tokens.mint(make_actor(), name="doomed", scope="write")
    assert resolver.resolve(_bearer(minted.plaintext)).scope == "write"
    services.tokens.revoke(make_actor(), minted.row.id)
    with pytest.raises(TokenRefusedError) as excinfo:
        resolver.resolve(_bearer(minted.plaintext))
    assert excinfo.value.reason == "revoked"


def test_an_expired_token_is_distinguished_from_an_unknown_one(
    db: Database, services: ServiceBundle, resolver: PatTokenResolver
) -> None:
    """The two refusals carry different `reason` values and different messages because
    the fix differs: an expired token is re-minted by its owner, an unknown one means
    the value is wrong."""
    minted = services.tokens.mint(
        make_actor(),
        name="short-lived",
        scope="read",
        expires_at=format_datetime(utc_now() + timedelta(hours=1)),
    )
    assert resolver.resolve(_bearer(minted.plaintext)).scope == "read"
    # Move the stored expiry into the past rather than sleeping for it.
    with db.write() as conn:
        conn.exec_driver_sql(
            "UPDATE access_tokens SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (minted.row.id,),
        )
    with pytest.raises(TokenRefusedError) as excinfo:
        resolver.resolve(_bearer(minted.plaintext))
    assert excinfo.value.reason == "expired"
    assert "expired" in str(excinfo.value).lower()


def test_deactivating_a_principal_takes_effect_on_the_next_request(
    services: ServiceBundle, resolver: PatTokenResolver
) -> None:
    """FR-I3 through the resolver, not by inspection. An inactive principal's PAT
    resolves to a refusal, so there is no cached identity to outlive the decision."""
    owner = services.principals.create_user(
        make_actor(),
        email="temp@example.com",
        display_name="Temp",
        role="member",
        password="correct-horse-battery-staple",
    )
    minted = services.tokens.mint(make_actor(), name="temp", scope="read", principal_id=owner.id)
    assert resolver.resolve(_bearer(minted.plaintext)).principal_id == owner.id
    services.principals.deactivate_principal(make_actor(), owner.id)
    with pytest.raises(TokenRefusedError) as excinfo:
        resolver.resolve(_bearer(minted.plaintext))
    # Deactivation also revokes the principal's live tokens, so either refusal is
    # correct here; the point is that the credential stops working immediately.
    assert excinfo.value.reason in ("inactive_principal", "revoked")


def test_an_inactive_principal_refuses_even_a_live_token(
    db: Database, services: ServiceBundle, resolver: PatTokenResolver
) -> None:
    """The resolver's own `is_active` branch, isolated from the revocation that
    ordinarily accompanies deactivation: a token that was never revoked still stops
    resolving the moment its principal is inactive."""
    owner = services.principals.create_user(
        make_actor(),
        email="ghost@example.com",
        display_name="Ghost",
        role="member",
        password="correct-horse-battery-staple",
    )
    minted = services.tokens.mint(make_actor(), name="ghost", scope="read", principal_id=owner.id)
    with db.write() as conn:
        conn.exec_driver_sql("UPDATE principals SET is_active = 0 WHERE id = ?", (owner.id,))
    with pytest.raises(TokenRefusedError) as excinfo:
        resolver.resolve(_bearer(minted.plaintext))
    assert excinfo.value.reason == "inactive_principal"


# ------------------------------------------------------------ last_used_at


def test_last_used_at_advances_on_use(services: ServiceBundle, resolver: PatTokenResolver) -> None:
    minted = services.tokens.mint(make_actor(), name="tracked", scope="read")
    assert minted.row.last_used_at is None
    resolver.resolve(_bearer(minted.plaintext))
    assert services.tokens.get_token(minted.row.id).last_used_at is not None


def test_a_burst_of_requests_does_not_produce_one_write_each(
    services: ServiceBundle, resolver: PatTokenResolver
) -> None:
    """FR-I4's `last_used_at` must not turn every read into a write. The update is
    coarse: skipped while the stored value is within `LAST_USED_COARSENESS`, so a burst
    writes once. The field answers "is this token still in use", which a five-minute
    resolution answers as well as a per-request one would."""
    minted = services.tokens.mint(make_actor(), name="hot", scope="read")
    for _ in range(20):
        resolver.resolve(_bearer(minted.plaintext))
    first = services.tokens.get_token(minted.row.id).last_used_at
    assert first is not None
    for _ in range(20):
        resolver.resolve(_bearer(minted.plaintext))
    assert services.tokens.get_token(minted.row.id).last_used_at == first
    assert LAST_USED_COARSENESS > timedelta(seconds=0)


# ----------------------------------------------------- one refusal, two surfaces


def test_rest_refuses_an_unauthenticated_request_with_the_shared_envelope(
    app: FastAPI,
) -> None:
    with TestClient(app) as bare:
        response = bare.get("/api/v1/object-types")
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "invalid_token"
    assert error["details"]["reason"] == "missing"
    assert set(error) == {"code", "message", "details"}


def test_rest_refuses_an_unknown_bearer(app: FastAPI) -> None:
    with TestClient(app) as bare:
        response = bare.get("/api/v1/object-types", headers=auth("gw_pat_nope"))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_health_and_readiness_still_answer_without_a_credential(app: FastAPI) -> None:
    """The exemption allowlist is not decoration: a load balancer probes these with no
    credential at all (FR-P4), so requiring one would make the deployment unmonitorable."""
    with TestClient(app) as bare:
        # The body, not the status. The SPA catch-all answers 200 for a route
        # that does not exist, so a status-only assertion cannot tell the two apart.
        assert bare.get("/healthz").json() == {"status": "ok"}
        assert bare.get("/readyz").json() == {"status": "ok"}


@pytest.mark.anyio
async def test_mcp_refuses_the_same_credential_with_the_same_code(
    app: FastAPI, mcp_server: MCPServer
) -> None:
    """Failure is uniform across surfaces: REST answers 401 through the
    shared envelope, MCP refuses before dispatch, and the two carry the same code —
    `errors.TokenRefusedError`, which is where the code lives, is the reason they
    cannot drift."""
    with TestClient(app) as bare:
        rest_code = bare.get("/api/v1/object-types").json()["error"]["code"]
    # The MCP half is refused at the session's opening, not at the first call after it,
    # because ``server/discover`` is gated too. ``refusal_opening`` does the flattening
    # a refusal needs, and adds the ``__context__``
    # hop the SDK's fallback to a legacy handshake puts in front of the real refusal.
    refusal = await refusal_opening(memory_session(mcp_server, token=None))
    assert rest_code == "invalid_token"
    assert TokenRefusedError.code == rest_code
    assert "no credential" in refusal.message.lower()


@pytest.mark.anyio
async def test_an_unclassified_failure_is_uniform_across_surfaces_too(
    app: FastAPI,
    mcp_server: MCPServer,
    pat: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The test above, extended to an unclassified failure.

    A *classified* failure carries the same code on both surfaces. An unclassified one,
    left alone, would carry nothing of the kind: REST would fall through to Starlette's
    ``ServerErrorMiddleware`` and return a 21-byte plain-text body, while MCP would fall
    through to the SDK's catch-all and return ``str(exc)`` verbatim in the tool result --
    so the same bug would be a blank page on one surface and an internals leak on the
    other. Both return the same ``internal_error`` envelope carrying only a request id.

    The raised message here is a security probe: a fake filesystem path and a
    fake table name, asserted **absent** from both answers.
    """
    leak = "no such table: records_secret_v3 at /srv/gw/data/records.sqlite3"

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError(leak)

    monkeypatch.setattr(
        "glosswork.services.schema.SchemaService.list_object_types", explode, raising=True
    )

    with TestClient(app, raise_server_exceptions=False) as bare:
        bare.headers["Authorization"] = f"Bearer {mint_scope_tokens(app.state.services)['admin']}"
        rest = bare.get("/api/v1/object-types")
    assert rest.status_code == 500
    rest_error = rest.json()["error"]
    assert rest_error["code"] == "internal_error"
    assert leak not in rest.text
    assert "records_secret_v3" not in rest.text
    # This response bypasses RequestContextMiddleware's send_wrapper, so the id
    # rides in the body rather than the x-request-id header every other error carries.
    assert "x-request-id" not in {k.lower() for k in rest.headers}
    assert rest_error["details"]["request_id"]

    async with memory_session(mcp_server, token=pat["read"]) as mcp_client:
        result = await mcp_client.call_tool("list_object_types", {})
    assert result.is_error
    mcp_error = (result.structured_content or {})["error"]
    assert mcp_error["code"] == rest_error["code"] == "internal_error"
    assert leak not in str(result.content)
    assert "records_secret_v3" not in str(result.structured_content)
    assert mcp_error["details"]["request_id"]
