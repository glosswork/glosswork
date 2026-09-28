"""OIDC ID-token validation and role mapping (FR-I1, FR-I2).

This module is the *verifier*: given an ID token, decide whether it is genuine and who
it says the caller is. The browser-facing authorization-code + PKCE redirect, the
callback, and the session cookie live in the OIDC flow and session services, and they
call this.

``JwksSource`` is the seam that makes this testable with no network and no mock
provider process, and it exists for the same reason ``TokenResolver`` does (DD-8):
one narrow protocol with a production implementation and a test implementation, not a
production implementation with a test escape hatch inside it.

- Production: :class:`PyJwkClientJwksSource` wraps ``jwt.PyJWKClient``, whose
  ``jwks_uri`` is discovered from the issuer's ``.well-known/openid-configuration``
  and whose JWK set is cached by the client itself.
- Tests: :class:`StaticJwksSource`, a dict built from a per-session RSA keypair
  generated with ``cryptography``. Nothing listens on a socket and nothing leaves the
  machine, which is the same offline discipline FR-P1 and FR-Q6 already impose.

No ``PyJWTError`` escapes past this module. Each of the four failure modes — bad
signature, wrong issuer, wrong audience, expired — becomes a distinct domain error, so
a caller can tell "your clock is wrong" from "this token is for another application".
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from jwt import PyJWK, PyJWKClient

from glosswork.config import Settings
from glosswork.errors import AuthenticationFailedError

ALGORITHMS = ["RS256"]
DISCOVERY_PATH = "/.well-known/openid-configuration"


class OidcNotConfiguredError(AuthenticationFailedError):
    """``GW_AUTH_MODE`` does not permit OIDC, or the issuer/client id are absent."""


class InvalidIdTokenError(AuthenticationFailedError):
    """The presented ID token failed verification. ``details["reason"]`` names which
    of signature / issuer / audience / expiry, because the operator fix differs."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message, {"reason": reason})
        self.reason = reason


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    """What a verified ID token asserts. Not a principal: JIT provisioning turns this
    into one (``PrincipalService.provision_oidc_principal``)."""

    external_id: str
    email: str | None
    display_name: str
    role: str
    groups: tuple[str, ...]


class JwksSource(Protocol):
    def signing_key(self, kid: str | None) -> PyJWK: ...
    def refresh(self) -> None: ...


class StaticJwksSource:
    """A fixed JWK set. The test implementation, and the reason the OIDC suite needs
    no network: a keypair is generated in-process and its public half handed here."""

    def __init__(self, keys: dict[str, PyJWK]) -> None:
        self._keys = dict(keys)
        self.refresh_count = 0

    def signing_key(self, kid: str | None) -> PyJWK:
        if kid is None or kid not in self._keys:
            raise KeyError(kid)
        return self._keys[kid]

    def refresh(self) -> None:
        self.refresh_count += 1


class PyJwkClientJwksSource:
    """The production implementation. ``jwks_uri`` is discovered from the issuer's
    OpenID configuration document on first use and the JWK set is cached by
    ``PyJWKClient``; ``refresh`` drops that cache so a provider key rotation heals
    without restarting the process."""

    def __init__(self, issuer: str, jwks_uri: str | None = None, timeout: int = 10) -> None:
        self._issuer = issuer.rstrip("/")
        self._jwks_uri = jwks_uri
        self._timeout = timeout
        self._client: PyJWKClient | None = None

    def _discover(self) -> str:
        if self._jwks_uri is not None:
            return self._jwks_uri
        url = self._issuer + DISCOVERY_PATH
        with urllib.request.urlopen(url, timeout=self._timeout) as response:  # noqa: S310
            document = json.load(response)
        jwks_uri = document.get("jwks_uri")
        if not jwks_uri:
            raise OidcNotConfiguredError(
                f"The issuer's OpenID configuration at {url} declares no 'jwks_uri', so "
                "ID-token signatures cannot be verified."
            )
        self._jwks_uri = str(jwks_uri)
        return self._jwks_uri

    def _ensure_client(self) -> PyJWKClient:
        if self._client is None:
            self._client = PyJWKClient(self._discover(), cache_keys=True)
        return self._client

    def signing_key(self, kid: str | None) -> PyJWK:
        if kid is None:
            raise KeyError(kid)
        return self._ensure_client().get_signing_key(kid)

    def refresh(self) -> None:
        self._client = None


class OidcVerifier:
    """Validate an ID token and map its group claim onto a role (FR-I1, FR-I2)."""

    def __init__(self, settings: Settings, jwks: JwksSource) -> None:
        self._settings = settings
        self._jwks = jwks

    def verify(self, id_token: str) -> VerifiedIdentity:
        if self._settings.auth_mode == "standalone":
            raise OidcNotConfiguredError(
                "This deployment runs in 'standalone' mode and does not accept OIDC "
                "identities. Set GW_AUTH_MODE to 'oidc' or 'both' to enable them."
            )
        issuer = self._settings.oidc_issuer
        audience = self._settings.oidc_client_id
        if not issuer or not audience:
            raise OidcNotConfiguredError(
                "OIDC is enabled but GW_OIDC_ISSUER and GW_OIDC_CLIENT_ID are not both set."
            )
        claims = self._decode(id_token, issuer, audience, allow_refresh=True)
        return self._identity(claims)

    # ---------------------------------------------------------------- internals

    def _decode(
        self, id_token: str, issuer: str, audience: str, allow_refresh: bool
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise InvalidIdTokenError(
                "The presented ID token is not a well-formed JWT.", "malformed"
            ) from exc
        kid = header.get("kid")
        try:
            key = self._jwks.signing_key(kid)
        except KeyError as exc:
            if allow_refresh:
                # A provider key rotation is the ordinary cause of an unknown kid, so
                # re-fetch the JWK set exactly once before deciding the token is bad.
                # Exactly once: a token forged with an arbitrary kid must not be able
                # to drive an unbounded number of fetches against the provider.
                self._jwks.refresh()
                return self._decode(id_token, issuer, audience, allow_refresh=False)
            raise InvalidIdTokenError(
                f"No signing key with kid {kid!r} is published by {issuer}, even after "
                "refreshing its key set.",
                "unknown_key",
            ) from exc
        try:
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key=key,
                algorithms=ALGORITHMS,
                issuer=issuer,
                audience=audience,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise InvalidIdTokenError(
                "The presented ID token has expired. Sign in again; if this repeats "
                "immediately, check that this host's clock is correct.",
                "expired",
            ) from exc
        except jwt.InvalidAudienceError as exc:
            raise InvalidIdTokenError(
                f"The presented ID token was issued for a different application, not "
                f"{audience!r}. Check GW_OIDC_CLIENT_ID.",
                "audience",
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise InvalidIdTokenError(
                f"The presented ID token was issued by a provider other than {issuer!r}. "
                "Check GW_OIDC_ISSUER.",
                "issuer",
            ) from exc
        except jwt.InvalidSignatureError as exc:
            raise InvalidIdTokenError(
                "The presented ID token's signature does not verify against the "
                "provider's published keys.",
                "signature",
            ) from exc
        except jwt.PyJWTError as exc:
            raise InvalidIdTokenError(
                f"The presented ID token could not be verified: {exc}.", "invalid"
            ) from exc
        return claims

    def _identity(self, claims: dict[str, Any]) -> VerifiedIdentity:
        subject = str(claims.get("sub", "")).strip()
        if not subject:
            raise InvalidIdTokenError(
                "The presented ID token carries no 'sub' claim, so it identifies nobody.",
                "subject",
            )
        groups = self._groups(claims)
        email = claims.get("email")
        display_name = claims.get("name") or claims.get("preferred_username") or email or subject
        return VerifiedIdentity(
            external_id=subject,
            email=str(email) if email else None,
            display_name=str(display_name),
            role=self.map_role(groups),
            groups=groups,
        )

    def _groups(self, claims: dict[str, Any]) -> tuple[str, ...]:
        """The group claim, defensively.

        A provider that omits the claim, sends a single string instead of a list, or
        sends something else entirely all mean the same thing here: no group
        membership was asserted, therefore no admin grant. Never an exception —
        failing a login because a claim had an unexpected JSON type would be a
        provider misconfiguration presenting as a broken product.
        """
        raw = claims.get(self._settings.oidc_group_claim)
        if raw is None:
            return ()
        if isinstance(raw, str):
            return (raw,)
        if isinstance(raw, (list, tuple)):
            return tuple(str(item) for item in raw)
        return ()

    def map_role(self, groups: tuple[str, ...]) -> str:
        """Membership in any ``GW_OIDC_ADMIN_GROUPS`` entry grants ``admin``, then any
        ``GW_OIDC_CREATOR_GROUPS`` entry grants ``creator``, and everything else is
        ``member`` (FR-I2). An unset or empty
        variable maps nobody to that role, which fails safe for both.

        **Admin is checked first, and the order is not arbitrary.** Roles are ordered
        ``member < creator < admin`` (DD-11), so an identity in both an admin group
        and a creator group is an administrator. Resolving the other way would demote an
        administrator as a side effect of adding them to a second group, which is the
        opposite of what an operator adding a group means by it.

        This is the only place a role is derived from claims, and
        ``PrincipalService.provision_oidc_principal`` re-runs it on every login -- which
        is what makes the mapping, rather than an out-of-band ``set-role``, the durable
        way to give an OIDC principal the ``creator`` role.
        """
        if self._matches(self._settings.admin_groups(), groups):
            return "admin"
        if self._matches(self._settings.creator_groups(), groups):
            return "creator"
        return "member"

    @staticmethod
    def _matches(configured: frozenset[str], groups: tuple[str, ...]) -> bool:
        """An empty configured set never matches, so an unset variable grants nothing
        rather than everything. Both group mappings read this, so neither can acquire a
        different notion of "unset" than the other."""
        return bool(configured) and bool(configured.intersection(groups))
