"""Authentication: turning a presented credential into a principal (FR-I1, FR-I2).

Distinct from ``auth.py``'s ``TokenResolver``, which answers the same question for a
*personal access token* on every request. This service answers it for the two
interactive credentials — a local password and an OIDC ID token — which are presented
once, at login, and which are exchanged for a session cookie.

``GW_AUTH_MODE`` is honored here, in one place, rather than at each call site:
``standalone`` refuses an OIDC identity, ``oidc`` refuses a local password login, and
``both`` accepts either. Refusing in the wrong mode is a configuration statement, not
a credential failure, so it says so.
"""

from __future__ import annotations

from datetime import datetime

from glosswork.actor import ActorContext
from glosswork.config import Settings
from glosswork.errors import AuthenticationFailedError
from glosswork.repositories.models import PrincipalRow
from glosswork.services.oidc import OidcVerifier, VerifiedIdentity
from glosswork.services.principals import PrincipalService


class AuthService:
    def __init__(
        self,
        settings: Settings,
        principals: PrincipalService,
        oidc: OidcVerifier,
    ) -> None:
        self._settings = settings
        self._principals = principals
        self._oidc = oidc

    @property
    def oidc(self) -> OidcVerifier:
        return self._oidc

    def login_with_password(
        self, email: str, password: str, now: datetime | None = None
    ) -> PrincipalRow:
        """Verify a local account (FR-I1).

        Every failure mode raises the identical error from ``PrincipalService``, so
        this method adds only the mode check ahead of it.
        """
        if self._settings.auth_mode == "oidc":
            raise AuthenticationFailedError(
                "This deployment authenticates through its identity provider only. "
                "Sign in there rather than with a local password."
            )
        return self._principals.verify_password(email, password, now=now)

    def login_with_id_token(
        self, actor: ActorContext, id_token: str, now: datetime | None = None
    ) -> tuple[PrincipalRow, VerifiedIdentity]:
        """Verify an OIDC ID token and resolve it to a principal (FR-I1, FR-I2).

        Provisioning is just-in-time and the provider is authoritative on every login:
        a new subject creates a principal, and a returning one has its role and display
        name refreshed from the current claims, so removing someone from the admin
        group demotes them (and revokes their over-scoped tokens) at their next login
        rather than whenever an administrator notices.
        """
        identity = self._oidc.verify(id_token)
        principal = self._principals.provision_oidc_principal(
            actor,
            external_id=identity.external_id,
            email=identity.email,
            display_name=identity.display_name,
            role=identity.role,
            now=now,
        )
        return principal, identity
