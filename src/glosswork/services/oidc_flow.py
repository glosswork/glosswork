"""OIDC authorization-code + PKCE flow (FR-I1, DD-10).

Distinct from ``services/oidc.py``'s ``OidcVerifier``, which validates an ID token
once one is in hand; this module is what gets one: it builds the authorization
redirect and exchanges the code the provider returns for an ID token.

FR-I1 names Okta specifically, so this follows Okta's fixed endpoint layout
(``{issuer}/v1/authorize``, ``{issuer}/v1/token``) rather than performing a
``.well-known/openid-configuration`` discovery round trip for them — unlike
``OidcVerifier``, which already does full discovery for ``jwks_uri`` because
``PyJWKClient`` requires it and that value is not derivable from a fixed suffix.

The code exchange is the one network call this module makes, and it is made through an
injected ``httpx2.Client`` — the same DD-8-shaped seam ``JwksSource`` uses for the
verifier — so the test suite verifies it against ``httpx2.MockTransport`` with nothing
listening on a socket.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx2

from glosswork.config import Settings
from glosswork.services.oidc import OidcNotConfiguredError

AUTHORIZE_SUFFIX = "/v1/authorize"
TOKEN_SUFFIX = "/v1/token"

# 32 random bytes -> a 43-character base64url token, within RFC 7636's 43-128 range.
CODE_VERIFIER_BYTES = 32
STATE_BYTES = 32

OIDC_SCOPES = "openid profile email"


def _code_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OidcExchangeFailedError(OidcNotConfiguredError):
    """The provider's token endpoint refused the code, or answered with no
    ``id_token``. Distinct from :class:`~glosswork.services.oidc.InvalidIdTokenError`,
    which is raised once an ID token is in hand and fails *verification*; this is
    raised before one exists at all."""


@dataclass(frozen=True, slots=True)
class OidcTransaction:
    """What ``start`` hands the caller: the URL to redirect the browser to, and the
    two secrets that must survive round-trip to the callback in ``gw_oidc_tx``
    (DD-10) so ``finish`` can validate ``state`` and complete PKCE."""

    authorization_url: str
    state: str
    code_verifier: str


class OidcFlowService:
    def __init__(self, settings: Settings, http_client: httpx2.Client | None = None) -> None:
        self._settings = settings
        self._http = http_client or httpx2.Client(timeout=10.0)

    def start(self, redirect_uri: str) -> OidcTransaction:
        """Build the authorization redirect (FR-I1): a fresh ``state`` and PKCE
        ``code_verifier``/``code_challenge`` pair, generated with ``secrets`` and
        ``hashlib`` exactly as DD-9's session identifiers are."""
        issuer, client_id = self._require_configured()
        state = secrets.token_urlsafe(STATE_BYTES)
        code_verifier = secrets.token_urlsafe(CODE_VERIFIER_BYTES)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": OIDC_SCOPES,
            "state": state,
            "code_challenge": _code_challenge(code_verifier),
            "code_challenge_method": "S256",
        }
        url = f"{issuer}{AUTHORIZE_SUFFIX}?{urlencode(params)}"
        return OidcTransaction(authorization_url=url, state=state, code_verifier=code_verifier)

    def exchange_code(self, code: str, code_verifier: str, redirect_uri: str) -> str:
        """Trade the authorization code for an ID token. Called only after the
        callback route has already compared ``state``: a mismatched or missing state is
        refused before the code is exchanged."""
        issuer, client_id = self._require_configured()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        if self._settings.oidc_client_secret:
            data["client_secret"] = self._settings.oidc_client_secret
        response = self._http.post(f"{issuer}{TOKEN_SUFFIX}", data=data)
        if response.status_code != 200:
            raise OidcExchangeFailedError(
                "The identity provider refused to exchange the authorization code for "
                "a token. Sign in again; if this repeats, check GW_OIDC_CLIENT_SECRET."
            )
        body = response.json()
        id_token = body.get("id_token")
        if not id_token:
            raise OidcExchangeFailedError(
                "The identity provider's token response carried no 'id_token'. Check "
                "that the OIDC scope includes 'openid'."
            )
        return str(id_token)

    def _require_configured(self) -> tuple[str, str]:
        if self._settings.auth_mode == "standalone":
            raise OidcNotConfiguredError(
                "This deployment runs in 'standalone' mode and does not accept OIDC "
                "identities. Set GW_AUTH_MODE to 'oidc' or 'both' to enable them."
            )
        issuer = self._settings.oidc_issuer
        client_id = self._settings.oidc_client_id
        if not issuer or not client_id:
            raise OidcNotConfiguredError(
                "OIDC is enabled but GW_OIDC_ISSUER and GW_OIDC_CLIENT_ID are not both set."
            )
        return issuer, client_id
