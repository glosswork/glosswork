"""Token resolution seam (DD-8, FR-M2, FR-M4, FR-I4).

``TokenResolver`` is the one replaceable seam between "what the caller presented" and
"who the caller is and what scope they hold". Both surfaces consume it: the MCP
adapter builds each tool call's ``ActorContext`` from a ``TokenIdentity``, and
``RequestContextMiddleware`` does the same for every REST request (DD-8). Nothing
about *authorization* lives here — required scope is declared once where a tool or a
route is registered, and compared against ``TokenIdentity.scope`` by the MCP scope
middleware and the REST ``require_scope`` dependency.

The resolver ``create_app`` builds is :class:`PatTokenResolver`, which verifies a real
personal access token. That is the payoff DD-8 was designed for: tool registration, the
``tools/list`` filter, the ``tools/call`` gate, and the REST middleware do not depend on
which resolver is installed; only the instance ``create_app`` builds decides it.

``FixedScopeResolver`` remains. It is a test seam, not an interim credential.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from glosswork.actor import (
    BOOTSTRAP_PRINCIPAL_ID,
    AuthMethod,
    Level,
    PrincipalType,
    Scope,
)
from glosswork.errors import TokenRefusedError
from glosswork.timeutil import format_datetime, utc_now

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from glosswork.services import ServiceBundle

__all__ = [
    "LEVEL_ORDER",
    "SCOPE_ORDER",
    "VALID_SCOPES",
    "FixedScopeResolver",
    "PatTokenResolver",
    "TokenIdentity",
    "TokenRefusedError",
    "TokenResolver",
    "bearer_token",
    "level_allows",
    "min_level",
    "scope_allows",
]

# DD-11. **One ordering, not two.** ``none < read < write < admin`` is the whole
# table; ``SCOPE_ORDER`` is the same table with the deny row removed, *derived* rather
# than restated, so the two can never drift. ``scope_allows`` keeps its exact prior
# behaviour and every one of its callers.
LEVEL_ORDER: dict[Level, int] = {"none": -1, "read": 0, "write": 1, "admin": 2}
SCOPE_ORDER: dict[Scope, int] = {k: v for k, v in LEVEL_ORDER.items() if k != "none"}
# The scope vocabulary, derived from the same table for the same reason. It lives here
# rather than in ``services/tokens.py`` so ``services/principals.py`` can read it
# without importing the token service back (the mint ceiling now imports ``role_scope``
# in the other direction).
VALID_SCOPES: tuple[Scope, ...] = tuple(SCOPE_ORDER)


def scope_allows(actual: Scope, required: Scope) -> bool:
    """``read < write < admin``: a scope satisfies every requirement at or below it.

    The single ordering. The MCP ``tools/call`` gate, the ``tools/list`` filter, and
    the REST ``require_scope`` dependency all call this rather than each comparing
    scopes their own way, so there is never a second ordering.
    """
    return SCOPE_ORDER[actual] >= SCOPE_ORDER[required]


def level_allows(actual: Level, required: Scope) -> bool:
    """The grant axis's comparison, reading the same table (DD-11).

    ``required`` is a :class:`Scope` rather than a :class:`Level` deliberately: no call
    site ever needs "at least ``none``", which is trivially true, so admitting it as a
    requirement would only make an unenforced path look enforced.
    """
    return LEVEL_ORDER[actual] >= LEVEL_ORDER[required]


def min_level(scope: Scope, granted: Level) -> Level:
    """``effective(credential, T) = min(credential.scope, granted(principal, T))``.

    The ceiling in one place. A credential can only ever *narrow*: it
    can never lift ``none`` to anything, and a ``read`` PAT held by the administrator
    of a type still only reads it, which is what preserves the rule that a ``read`` PAT
    is rejected on every write path.
    """
    return scope if LEVEL_ORDER[scope] < LEVEL_ORDER[granted] else granted


@dataclass(frozen=True, slots=True)
class TokenIdentity:
    principal_id: str
    principal_type: PrincipalType
    scope: Scope
    auth_method: AuthMethod
    # DD-16, mirroring :class:`~glosswork.actor.ActorContext`. ``auth_method``
    # stays ``"pat"`` for a capability token, because it is one: the narrowing is a
    # second dimension on the credential ceiling, not a fourth authentication method.
    capability: str | None = None
    credential_id: str | None = None
    # DD-17. The agent label this credential was minted for, read straight
    # off the ``access_tokens`` row the resolver already holds -- no second lookup. It is
    # the third and weakest source in ``actor.resolve_agent_label_id``'s precedence, under
    # the per-call ``agent`` parameter and the ``X-Agent-Label`` header. A session
    # identity leaves it ``None``: a cookie carries no token, so there is nothing to read.
    agent_label: str | None = None


class TokenResolver(Protocol):
    def resolve(self, authorization_header: str | None) -> TokenIdentity: ...


def bearer_token(authorization_header: str | None) -> str | None:
    """The token portion of an ``Authorization: Bearer <token>`` header, or None when
    the header is absent. Any other shape returns the raw header so the resolver
    refuses it with the same message as an unknown token."""
    if authorization_header is None:
        return None
    scheme, _, token = authorization_header.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return authorization_header.strip()
    return token.strip()


_NO_CREDENTIAL_MESSAGE = (
    "This request carried no credential. Send a personal access token as "
    "'Authorization: Bearer gw_pat_...'. An administrator can mint one for you, or "
    "create the first one out of band with 'python -m glosswork.admin mint-token'."
)
_UNKNOWN_TOKEN_MESSAGE = (
    "This deployment cannot verify the presented bearer token. Check that the token "
    "was copied whole and has not been revoked; a token's secret is shown only once, "
    "at mint time, so a lost one is replaced rather than recovered."
)


class PatTokenResolver:
    """Verify a presented personal access token (FR-I4, DD-8).

    Hashes the bearer, looks the hash up in ``access_tokens``, and returns the token's
    principal and scope. Unknown, revoked, expired, or belonging to a deactivated
    principal each raise :class:`TokenRefusedError` with a ``reason`` distinguishing
    them, because the fix differs: an expired token is re-minted, a revoked one was
    deliberately killed, and a deactivated principal is an administrator's decision.

    A **missing** ``Authorization`` header is refused, not promoted. A resolver that
    treated an absent header as full ``admin`` access would make every unauthenticated
    request an administrator's.

    Services are reached through a callable rather than held directly, because the
    resolver is constructed in ``create_app`` before the lifespan builds the service
    bundle — the same lazy-services pattern the MCP server factory already uses.
    """

    def __init__(self, get_services: Callable[[], ServiceBundle]) -> None:
        self._get_services = get_services

    def resolve(self, authorization_header: str | None) -> TokenIdentity:
        token = bearer_token(authorization_header)
        if token is None:
            raise TokenRefusedError(_NO_CREDENTIAL_MESSAGE, reason="missing")
        services = self._get_services()
        row = services.tokens.resolve_secret(token)
        if row is None:
            raise TokenRefusedError(_UNKNOWN_TOKEN_MESSAGE, reason="unknown")
        if row.revoked_at is not None:
            raise TokenRefusedError(
                f"Token {row.token_prefix}... was revoked at {row.revoked_at}. Ask an "
                "administrator to mint a replacement.",
                reason="revoked",
            )
        if row.expires_at is not None and row.expires_at <= format_datetime(utc_now()):
            raise TokenRefusedError(
                f"Token {row.token_prefix}... expired at {row.expires_at}. Mint a "
                "replacement; an expired token is never reactivated.",
                reason="expired",
            )
        principal = services.principals.get_principal_or_none(row.principal_id)
        if principal is None or not principal.is_active:
            raise TokenRefusedError(
                "The principal this token belongs to is deactivated. Ask an "
                "administrator to reactivate it, or use a different credential.",
                reason="inactive_principal",
            )
        if row.consumed_at is not None:
            # DD-16. The resolver checks only that the ticket is unspent; the
            # *spending* is a conditional UPDATE inside the upload's own transaction, so
            # a refused or failed request never burns one.
            raise TokenRefusedError(
                f"Token {row.token_prefix}... has already been used. An upload ticket is "
                "single use; call create_attachment_upload again for a fresh one.",
                reason="consumed",
            )
        services.tokens.note_use(row.id, row.last_used_at)
        principal_type: PrincipalType = "user" if principal.type == "user" else "service_account"
        scope: Scope = row.scope  # type: ignore[assignment]
        return TokenIdentity(
            principal_id=principal.id,
            principal_type=principal_type,
            scope=scope,
            auth_method="pat",
            capability=row.capability,
            credential_id=row.id,
            agent_label=row.agent_label,
        )


class FixedScopeResolver:
    """A stub resolver returning one fixed identity regardless of the header. Exists to
    prove the seam: the MCP suite builds the server against it and asserts the
    corresponding listing, which is what keeps ``PatTokenResolver`` a drop-in (DD-8). It
    is a test seam, not an interim credential."""

    def __init__(
        self,
        scope: Scope,
        principal_id: str = BOOTSTRAP_PRINCIPAL_ID,
    ) -> None:
        self._identity = TokenIdentity(
            principal_id=principal_id,
            principal_type="service_account",
            scope=scope,
            auth_method="pat",
        )

    def resolve(self, authorization_header: str | None) -> TokenIdentity:
        return self._identity
