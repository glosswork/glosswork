"""OIDC ID-token validation, role mapping, and JIT provisioning (FR-I1, FR-I2).

Tested against a **locally generated JWKS fixture**, never a real Okta tenant and
never a mock-provider process. A per-session RSA-2048 keypair is
generated with `cryptography`; the JWK set is built from its public half with
`jwt.algorithms.RSAAlgorithm.to_jwk`; tokens are minted here by signing claim sets with
the private half. Nothing listens on a socket and nothing leaves the machine, matching
the offline discipline FR-P1 and FR-Q6 already impose.

The seam that makes that possible — `JwksSource` — is a deliverable, not a test trick:
production wraps `jwt.PyJWKClient`, tests hand over a static dict, and the verifier
cannot tell the difference. `test_the_verifier_makes_no_network_call` is the assertion
that keeps it honest.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from glosswork.config import ConfigError, Settings, load_settings
from glosswork.db import Database
from glosswork.errors import AuthenticationFailedError, ValidationFailedError
from glosswork.services import ServiceBundle, build_services
from glosswork.services.oidc import (
    InvalidIdTokenError,
    OidcNotConfiguredError,
    OidcVerifier,
    StaticJwksSource,
)
from tests.conftest import make_actor

ISSUER = "https://example.okta.com/oauth2/default"
CLIENT_ID = "0oa1glosswork"
ADMIN_GROUP = "glosswork-admins"
KID = "test-key-1"


class Keypair:
    """One RSA-2048 keypair plus the JWK set its public half publishes."""

    def __init__(self, kid: str = KID) -> None:
        self.kid = kid
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(RSAAlgorithm.to_jwk(self.private.public_key()))
        jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})
        self.jwk = jwk

    def public_jwk(self) -> jwt.PyJWK:
        return jwt.PyJWK.from_dict(self.jwk)

    def sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": self.kid})


@pytest.fixture(scope="module")
def keypair() -> Keypair:
    """Generated once per module: RSA-2048 key generation is the slowest thing here
    and nothing in the suite needs a fresh key per test."""
    return Keypair()


@pytest.fixture
def jwks(keypair: Keypair) -> StaticJwksSource:
    return StaticJwksSource({keypair.kid: keypair.public_jwk()})


def oidc_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "auth_mode": "oidc",
        "oidc_issuer": ISSUER,
        "oidc_client_id": CLIENT_ID,
        "oidc_admin_groups": ADMIN_GROUP,
        "embedding_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def verifier(jwks: StaticJwksSource) -> OidcVerifier:
    return OidcVerifier(oidc_settings(), jwks)


def claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "00u1abcdef",
        "exp": int((now + timedelta(minutes=10)).timestamp()),
        "iat": int(now.timestamp()),
        "email": "grace@example.com",
        "name": "Grace Hopper",
        "groups": ["everyone"],
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------ happy path


def test_a_valid_token_verifies_to_an_identity(verifier: OidcVerifier, keypair: Keypair) -> None:
    identity = verifier.verify(keypair.sign(claims()))
    assert identity.external_id == "00u1abcdef"
    assert identity.email == "grace@example.com"
    assert identity.display_name == "Grace Hopper"
    assert identity.role == "member"


def test_the_verifier_makes_no_network_call(
    jwks: StaticJwksSource, keypair: Keypair, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim the `JwksSource` seam exists to make good on. A full verification
    completes with `urllib.request.urlopen` poisoned, so no test in this file can
    quietly start depending on a network the CI box may not have."""
    import urllib.request

    def explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the OIDC verifier attempted a network call")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    verifier = OidcVerifier(oidc_settings(), jwks)
    assert verifier.verify(keypair.sign(claims())).external_id == "00u1abcdef"


# ------------------------------------------------- the four failure modes


def test_a_bad_signature_is_refused(verifier: OidcVerifier) -> None:
    other = Keypair()  # same kid, different key: the signature will not verify
    with pytest.raises(InvalidIdTokenError) as excinfo:
        verifier.verify(other.sign(claims()))
    assert excinfo.value.reason == "signature"
    assert excinfo.value.code == "invalid_credentials"


def test_a_wrong_issuer_is_refused(verifier: OidcVerifier, keypair: Keypair) -> None:
    with pytest.raises(InvalidIdTokenError) as excinfo:
        verifier.verify(keypair.sign(claims(iss="https://evil.example.com")))
    assert excinfo.value.reason == "issuer"
    assert "GW_OIDC_ISSUER" in excinfo.value.message


def test_a_wrong_audience_is_refused(verifier: OidcVerifier, keypair: Keypair) -> None:
    with pytest.raises(InvalidIdTokenError) as excinfo:
        verifier.verify(keypair.sign(claims(aud="some-other-application")))
    assert excinfo.value.reason == "audience"
    assert "GW_OIDC_CLIENT_ID" in excinfo.value.message


def test_an_expired_token_is_refused(verifier: OidcVerifier, keypair: Keypair) -> None:
    past = int((datetime.now(UTC) - timedelta(hours=1)).timestamp())
    with pytest.raises(InvalidIdTokenError) as excinfo:
        verifier.verify(keypair.sign(claims(exp=past)))
    assert excinfo.value.reason == "expired"
    assert "clock" in excinfo.value.message


def test_no_raw_pyjwt_error_escapes_the_service_boundary(
    verifier: OidcVerifier, keypair: Keypair
) -> None:
    """Every failure is a domain error carrying a code the adapters already know how
    to shape. A `PyJWTError` reaching a route would become a 500."""
    for token in ("not-a-jwt", "", "a.b.c", keypair.sign(claims(sub=""))):
        with pytest.raises(AuthenticationFailedError):
            verifier.verify(token)


def test_a_token_missing_a_required_claim_is_refused(
    verifier: OidcVerifier, keypair: Keypair
) -> None:
    incomplete = claims()
    del incomplete["exp"]
    with pytest.raises(InvalidIdTokenError):
        verifier.verify(keypair.sign(incomplete))


# ----------------------------------------------------------------- key rotation


def test_an_unknown_kid_refetches_the_jwks_exactly_once_and_then_verifies(
    keypair: Keypair,
) -> None:
    """A provider key rotation heals without a restart. *Exactly* once, deliberately:
    a token forged with an arbitrary kid must not be able to drive an unbounded number
    of fetches against the provider."""
    rotated = Keypair(kid="test-key-2")

    class RotatingSource(StaticJwksSource):
        def __init__(self) -> None:
            super().__init__({keypair.kid: keypair.public_jwk()})

        def refresh(self) -> None:
            super().refresh()
            self._keys[rotated.kid] = rotated.public_jwk()  # noqa: SLF001 - test double

    source = RotatingSource()
    verifier = OidcVerifier(oidc_settings(), source)
    identity = verifier.verify(rotated.sign(claims()))
    assert identity.external_id == "00u1abcdef"
    assert source.refresh_count == 1


def test_a_kid_that_never_appears_fails_after_one_refetch(keypair: Keypair) -> None:
    source = StaticJwksSource({keypair.kid: keypair.public_jwk()})
    verifier = OidcVerifier(oidc_settings(), source)
    ghost = Keypair(kid="never-published")
    with pytest.raises(InvalidIdTokenError) as excinfo:
        verifier.verify(ghost.sign(claims()))
    assert excinfo.value.reason == "unknown_key"
    assert source.refresh_count == 1


# ---------------------------------------------------------------- role mapping


@pytest.mark.parametrize(
    ("group_claim", "expected_role"),
    [
        ([ADMIN_GROUP], "admin"),
        ([ADMIN_GROUP, "everyone"], "admin"),
        ([], "member"),
        (["some-other-group"], "member"),
        (None, "member"),
        ("a-single-string-not-a-list", "member"),
        (ADMIN_GROUP, "admin"),
        (42, "member"),
        ({"nested": "object"}, "member"),
    ],
)
def test_group_claim_to_role_mapping(
    jwks: StaticJwksSource, keypair: Keypair, group_claim: Any, expected_role: str
) -> None:
    """FR-I2. Membership in any `GW_OIDC_ADMIN_GROUPS` entry grants `admin`, everything
    else is `member`. A claim that is absent, a bare string, or an unexpected JSON type
    all mean "no membership was asserted" — never an exception, because failing a login
    over a provider's claim shape would be a misconfiguration presenting as a broken
    product."""
    verifier = OidcVerifier(oidc_settings(), jwks)
    payload = claims()
    if group_claim is None:
        del payload["groups"]
    else:
        payload["groups"] = group_claim
    assert verifier.verify(keypair.sign(payload)).role == expected_role


def test_the_group_claim_name_is_configurable(jwks: StaticJwksSource, keypair: Keypair) -> None:
    verifier = OidcVerifier(oidc_settings(oidc_group_claim="roles"), jwks)
    payload = claims(groups=[ADMIN_GROUP], roles=[ADMIN_GROUP])
    assert verifier.verify(keypair.sign(payload)).role == "admin"
    payload = claims(groups=[ADMIN_GROUP], roles=["nope"])
    assert verifier.verify(keypair.sign(payload)).role == "member"


def test_no_admin_groups_configured_maps_everyone_to_member(
    jwks: StaticJwksSource, keypair: Keypair
) -> None:
    """Fails safe: an unset `GW_OIDC_ADMIN_GROUPS` grants nobody `admin` rather than
    everybody."""
    verifier = OidcVerifier(oidc_settings(oidc_admin_groups=None), jwks)
    assert verifier.verify(keypair.sign(claims(groups=[ADMIN_GROUP]))).role == "member"


# ---------------------------------------------------------- JIT provisioning


def test_a_new_subject_provisions_a_principal(
    services: ServiceBundle, jwks: StaticJwksSource, keypair: Keypair
) -> None:
    verifier = OidcVerifier(oidc_settings(), jwks)
    identity = verifier.verify(keypair.sign(claims(groups=[ADMIN_GROUP])))
    principal = services.principals.provision_oidc_principal(
        make_actor(),
        external_id=identity.external_id,
        email=identity.email,
        display_name=identity.display_name,
        role=identity.role,
    )
    assert principal.auth_provider == "oidc"
    assert principal.external_id == "00u1abcdef"
    assert principal.role == "admin"
    assert principal.password_hash is None


def test_a_returning_identity_is_refreshed_from_the_current_claims_not_the_stored_copy(
    services: ServiceBundle, jwks: StaticJwksSource, keypair: Keypair
) -> None:
    """The demotion path. Revoking the Okta admin group has to actually
    demote, which it only does if the provider is treated as authoritative on every
    login rather than the stored row being trusted."""
    verifier = OidcVerifier(oidc_settings(), jwks)
    admin_identity = verifier.verify(keypair.sign(claims(groups=[ADMIN_GROUP])))
    first = services.principals.provision_oidc_principal(
        make_actor(),
        external_id=admin_identity.external_id,
        email=admin_identity.email,
        display_name=admin_identity.display_name,
        role=admin_identity.role,
    )
    assert first.role == "admin"

    # Keep a second admin around so the last-administrator guard is not what demotes.
    services.principals.create_user(
        make_actor(),
        email="other-admin@example.com",
        display_name="Other Admin",
        role="admin",
        password="correct-horse-battery-staple",
    )

    demoted_identity = verifier.verify(
        keypair.sign(claims(groups=["everyone"], name="Grace Hopper Jr"))
    )
    second = services.principals.provision_oidc_principal(
        make_actor(),
        external_id=demoted_identity.external_id,
        email=demoted_identity.email,
        display_name=demoted_identity.display_name,
        role=demoted_identity.role,
    )
    assert second.id == first.id
    assert second.role == "member"
    assert second.display_name == "Grace Hopper Jr"


def test_two_oidc_principals_cannot_share_a_subject(services: ServiceBundle) -> None:
    """`ix_principals_external` is the hard enforcement; this asserts the conflict
    surfaces as a domain error rather than an `IntegrityError` leaking through."""
    services.principals.create_user(
        make_actor(),
        email="one@example.com",
        display_name="One",
        auth_provider="oidc",
        external_id="shared-subject",
    )
    with pytest.raises(ValidationFailedError) as excinfo:
        services.principals.create_user(
            make_actor(),
            email="two@example.com",
            display_name="Two",
            auth_provider="oidc",
            external_id="shared-subject",
        )
    assert excinfo.value.code == "validation_failed"
    assert "shared-subject" in excinfo.value.message


# ------------------------------------------------------------- GW_AUTH_MODE


def test_standalone_mode_refuses_an_oidc_identity(jwks: StaticJwksSource, keypair: Keypair) -> None:
    verifier = OidcVerifier(Settings(auth_mode="standalone"), jwks)
    with pytest.raises(OidcNotConfiguredError) as excinfo:
        verifier.verify(keypair.sign(claims()))
    assert "standalone" in excinfo.value.message
    assert "GW_AUTH_MODE" in excinfo.value.message


def test_oidc_mode_refuses_a_local_password_login(
    db: Database, tmp_path: Path, jwks: StaticJwksSource
) -> None:
    services = build_services(db, tmp_path, oidc_settings(data_dir=tmp_path), jwks)
    services.principals.create_user(
        make_actor(),
        email="local@example.com",
        display_name="Local",
        password="correct-horse-battery-staple",
    )
    with pytest.raises(AuthenticationFailedError) as excinfo:
        services.authn.login_with_password("local@example.com", "correct-horse-battery-staple")
    assert "identity provider" in excinfo.value.message


def test_both_mode_accepts_either(
    db: Database, tmp_path: Path, jwks: StaticJwksSource, keypair: Keypair
) -> None:
    services = build_services(
        db, tmp_path, oidc_settings(auth_mode="both", data_dir=tmp_path), jwks
    )
    services.principals.create_user(
        make_actor(),
        email="local@example.com",
        display_name="Local",
        password="correct-horse-battery-staple",
    )
    assert services.authn.login_with_password("local@example.com", "correct-horse-battery-staple")
    principal, identity = services.authn.login_with_id_token(make_actor(), keypair.sign(claims()))
    assert principal.auth_provider == "oidc"
    assert identity.external_id == "00u1abcdef"


def test_config_validation_names_the_missing_variable() -> None:
    """The project convention: fail fast, naming the offending `GW_*` variable rather than
    the pydantic field."""
    import os

    saved = dict(os.environ)
    try:
        os.environ["GW_AUTH_MODE"] = "oidc"
        os.environ.pop("GW_OIDC_ISSUER", None)
        os.environ.pop("GW_OIDC_CLIENT_ID", None)
        with pytest.raises(ConfigError) as excinfo:
            load_settings()
        assert "GW_OIDC_ISSUER" in str(excinfo.value)

        os.environ["GW_OIDC_ISSUER"] = ISSUER
        with pytest.raises(ConfigError) as excinfo:
            load_settings()
        assert "GW_OIDC_CLIENT_ID" in str(excinfo.value)
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_httpx2_mock_transport_is_available(
    jwks: StaticJwksSource,
) -> None:
    """The authorization-code exchange against the provider's token endpoint is
    stubbable with `httpx2.MockTransport`, so the OIDC flow needs no further test
    dependency.
    PKCE itself is `secrets` + `hashlib` from the standard library."""
    import hashlib
    import secrets

    import httpx2

    assert hasattr(httpx2, "MockTransport")
    verifier_bytes = secrets.token_urlsafe(64).encode()
    assert len(hashlib.sha256(verifier_bytes).digest()) == 32
    del jwks


# ------------------------------------------------------------ GW_OIDC_CREATOR_GROUPS

CREATOR_GROUP = "glosswork-creators"


def creator_settings(**overrides: Any) -> Settings:
    """`oidc_settings` with a creator group configured as well as an admin one."""
    base: dict[str, Any] = {"oidc_creator_groups": CREATOR_GROUP}
    base.update(overrides)
    return oidc_settings(**base)


def test_a_creator_group_maps_to_the_creator_role(jwks: StaticJwksSource, keypair: Keypair) -> None:
    """Without a creator group the provider can only ever say `admin` or `member`,
    so a `creator` set by `set-role` would be overwritten at the next login."""
    verifier = OidcVerifier(creator_settings(), jwks)
    assert verifier.verify(keypair.sign(claims(groups=[CREATOR_GROUP]))).role == "creator"


def test_admin_wins_when_an_identity_is_in_both_groups(
    jwks: StaticJwksSource, keypair: Keypair
) -> None:
    """Roles are ordered `member < creator < admin` (DD-11), so the higher wins.
    Resolving the other way would demote an administrator as a side effect of adding
    them to a second group."""
    verifier = OidcVerifier(creator_settings(), jwks)
    both = claims(groups=[ADMIN_GROUP, CREATOR_GROUP])
    assert verifier.verify(keypair.sign(both)).role == "admin"
    # Order within the claim must not decide it either.
    reversed_claim = claims(groups=[CREATOR_GROUP, ADMIN_GROUP])
    assert verifier.verify(keypair.sign(reversed_claim)).role == "admin"


def test_no_creator_groups_configured_maps_everyone_to_member(
    jwks: StaticJwksSource, keypair: Keypair
) -> None:
    """Fails safe exactly as the admin mapping does: unset grants nobody `creator`."""
    verifier = OidcVerifier(oidc_settings(oidc_creator_groups=None), jwks)
    assert verifier.verify(keypair.sign(claims(groups=[CREATOR_GROUP]))).role == "member"


@pytest.mark.parametrize(
    ("groups", "expected_role"),
    [
        ([ADMIN_GROUP], "admin"),
        ([CREATOR_GROUP], "member"),
        (["everyone"], "member"),
        ([], "member"),
    ],
)
def test_an_unset_creator_variable_leaves_every_outcome_as_if_the_rule_did_not_exist(
    jwks: StaticJwksSource, keypair: Keypair, groups: list[str], expected_role: str
) -> None:
    """Asserted rather than assumed: a deployment that never sets
    `GW_OIDC_CREATOR_GROUPS` behaves exactly as if the setting did not exist. This is
    what makes the setting safe to ship without coordinating with anyone's identity
    provider."""
    verifier = OidcVerifier(oidc_settings(), jwks)
    assert verifier.verify(keypair.sign(claims(groups=groups))).role == expected_role


def test_the_creator_group_is_matched_against_the_configured_claim_name(
    jwks: StaticJwksSource, keypair: Keypair
) -> None:
    """Both group variables read the one `GW_OIDC_GROUP_CLAIM`, so a deployment that
    renames the claim does not have to discover that only half of it moved."""
    verifier = OidcVerifier(creator_settings(oidc_group_claim="roles"), jwks)
    payload = claims(groups=[CREATOR_GROUP], roles=[CREATOR_GROUP])
    assert verifier.verify(keypair.sign(payload)).role == "creator"
    payload = claims(groups=[CREATOR_GROUP], roles=["nope"])
    assert verifier.verify(keypair.sign(payload)).role == "member"


def test_a_creator_group_survives_a_second_login(
    services: ServiceBundle, jwks: StaticJwksSource, keypair: Keypair
) -> None:
    """The assertion that matters most here: the role is re-derived on every login,
    so it only persists because the provider keeps asserting it."""
    verifier = OidcVerifier(creator_settings(), jwks)

    def sign_in(groups: list[str]) -> str:
        identity = verifier.verify(keypair.sign(claims(groups=groups)))
        return services.principals.provision_oidc_principal(
            make_actor(),
            external_id=identity.external_id,
            email=identity.email,
            display_name=identity.display_name,
            role=identity.role,
        ).role

    assert sign_in([CREATOR_GROUP]) == "creator"
    assert sign_in([CREATOR_GROUP]) == "creator"


def test_leaving_the_creator_group_demotes_at_the_next_login(
    services: ServiceBundle, jwks: StaticJwksSource, keypair: Keypair
) -> None:
    """FR-I2's revocation guarantee holds for `creator` exactly as it does for `admin`.
    This is the property the creator group deliberately does not trade away: the
    alternative, making `creator` sticky, would have left a role the identity
    provider could not revoke."""
    verifier = OidcVerifier(creator_settings(), jwks)

    def sign_in(groups: list[str]) -> str:
        identity = verifier.verify(keypair.sign(claims(groups=groups)))
        return services.principals.provision_oidc_principal(
            make_actor(),
            external_id=identity.external_id,
            email=identity.email,
            display_name=identity.display_name,
            role=identity.role,
        ).role

    assert sign_in([CREATOR_GROUP]) == "creator"
    assert sign_in(["everyone"]) == "member"


def test_the_two_group_settings_parse_identically() -> None:
    """One parser, so whitespace, blank entries and unset cannot drift between them."""
    settings = oidc_settings(oidc_admin_groups=" a , ,b ", oidc_creator_groups=" a , ,b ")
    assert settings.admin_groups() == settings.creator_groups() == frozenset({"a", "b"})
    empty = oidc_settings(oidc_admin_groups="", oidc_creator_groups="")
    assert empty.admin_groups() == empty.creator_groups() == frozenset()
