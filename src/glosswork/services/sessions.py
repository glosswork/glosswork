"""Browser sessions (FR-A3, docs/DATA_MODEL.md section 2, DD-9, DD-10).

Shaped after ``services/tokens.py``'s ``AccessTokenService`` on purpose: a session
cookie value is `secrets.token_urlsafe(32)`, only its sha256 is stored, and the
plaintext is returned exactly once, from :meth:`SessionService.issue`. The CSRF token
(DD-10) is minted alongside it and stored the same way, in the same row, so it is
session-bound rather than a second, independent concept.

``resolve`` answers the identical question ``PatTokenResolver`` answers for a bearer
token — unknown, revoked, expired, or belonging to a deactivated principal each refuse
with the same ``invalid_token`` code a PAT refusal produces, so the two credential types
cannot drift about what a refusal looks like. It additionally enforces the two
server-side lifetime bounds DD-9 requires (absolute and idle) and, unlike a PAT, never
caches scope: it is derived fresh from the principal's *current* role on every call
(``services.principals.role_scope``).

This module is not part of the ``TokenResolver`` seam (``auth.py``): DD-9 is explicit
that widening that protocol to take a whole request would reshape the seam the MCP
adapter depends on for a browser-only concern. ``RequestContextMiddleware`` calls
:meth:`resolve` directly, as a second branch after the bearer path finds nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from glosswork.actor import ActorContext, Scope
from glosswork.auth import TokenIdentity
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.errors import TokenRefusedError
from glosswork.repositories.interfaces import (
    AuditRepository,
    PrincipalRepository,
    SessionRepository,
)
from glosswork.repositories.models import PrincipalRow, SessionRow
from glosswork.services.base import make_event
from glosswork.services.principals import role_scope
from glosswork.timeutil import format_datetime, parse_datetime, utc_now

SESSION_SECRET_BYTES = 32
CSRF_SECRET_BYTES = 32

# ``last_seen_at`` is advanced at most once per this interval per session, mirroring
# ``services/tokens.py``'s ``LAST_USED_COARSENESS``: every authenticated request would
# otherwise be a write, which on SQLite means every read takes the write lock.
LAST_SEEN_COARSENESS = timedelta(minutes=5)


def hash_session_value(plaintext: str) -> str:
    """The stored form of a presented session or CSRF secret. sha256, not a KDF, for
    the same reason ``services/tokens.py``'s ``hash_token`` uses it: a 32-byte random
    secret has no low-entropy candidate set to grind."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MintedSession:
    """The one and only carrier of a session's plaintext cookie and CSRF values.
    Returned from :meth:`SessionService.issue` and never persisted."""

    row: SessionRow
    cookie_value: str
    csrf_value: str


class SessionService:
    def __init__(
        self,
        db: Database,
        session_repo: SessionRepository,
        principal_repo: PrincipalRepository,
        audit_repo: AuditRepository,
        settings: Settings,
    ) -> None:
        self._db = db
        self._sessions = session_repo
        self._principals = principal_repo
        self._audit = audit_repo
        self._settings = settings

    # ------------------------------------------------------------------- issue

    def issue(
        self,
        actor: ActorContext,
        principal: PrincipalRow,
        *,
        replacing_cookie: str | None = None,
        note: str | None = None,
        now: datetime | None = None,
    ) -> MintedSession:
        """Create a session and return its plaintext cookie and CSRF values exactly
        once. Every successful authentication calls this (FR-A3): a fresh session
        identifier is issued and, when the browser presented a prior session cookie,
        that row is deleted in the same transaction (DD-9's rotation-on-login)."""
        moment = now or utc_now()
        ts = format_datetime(moment)
        lifetime = timedelta(hours=self._settings.session_lifetime_hours)
        expires_at = format_datetime(moment + lifetime)
        cookie_value = secrets.token_urlsafe(SESSION_SECRET_BYTES)
        csrf_value = secrets.token_urlsafe(CSRF_SECRET_BYTES)
        row = SessionRow(
            id=str(uuid.uuid4()),
            principal_id=principal.id,
            session_hash=hash_session_value(cookie_value),
            csrf_hash=hash_session_value(csrf_value),
            created_at=ts,
            expires_at=expires_at,
            last_seen_at=ts,
            revoked_at=None,
        )
        with self._db.write() as conn:
            if replacing_cookie:
                self._sessions.delete_by_hash(conn, hash_session_value(replacing_cookie))
            self._sessions.insert(conn, row)
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="session",
                        entity_id=row.id,
                        action="create",
                        note=note,
                    )
                ],
            )
        return MintedSession(row=row, cookie_value=cookie_value, csrf_value=csrf_value)

    # ----------------------------------------------------------------- resolve

    def resolve(self, cookie_value: str, now: datetime | None = None) -> TokenIdentity:
        """Verify a presented session cookie (DD-9).

        Refuses with the ``invalid_token`` code for every failure mode — unknown,
        revoked, expired past ``GW_SESSION_LIFETIME_HOURS``, idle past
        ``GW_SESSION_IDLE_HOURS``, or belonging to a deactivated principal — so a
        session and a PAT cannot disagree about what a refusal looks like. Scope is
        derived fresh from the principal's *current* role on every call; nothing about
        it is cached in the row.
        """
        moment = now or utc_now()
        moment_ts = format_datetime(moment)
        with self._db.read() as conn:
            row = self._sessions.get_by_hash(conn, hash_session_value(cookie_value))
        if row is None:
            raise TokenRefusedError(
                "This session cookie is not recognized. Sign in again.", reason="unknown"
            )
        if row.revoked_at is not None:
            raise TokenRefusedError(
                "This session has been signed out. Sign in again.", reason="revoked"
            )
        if row.expires_at <= moment_ts:
            raise TokenRefusedError("This session has expired. Sign in again.", reason="expired")
        if moment - parse_datetime(row.last_seen_at) > timedelta(
            hours=self._settings.session_idle_hours
        ):
            raise TokenRefusedError(
                "This session was idle too long and has expired. Sign in again.",
                reason="idle_expired",
            )
        with self._db.read() as conn:
            principal = self._principals.get(conn, row.principal_id)
        if principal is None or not principal.is_active:
            raise TokenRefusedError(
                "The principal this session belongs to is deactivated. Ask an "
                "administrator to reactivate it, or use a different credential.",
                reason="inactive_principal",
            )
        self._touch(row, moment_ts, moment)
        principal_type = "user" if principal.type == "user" else "service_account"
        scope: Scope = role_scope(principal.role)
        return TokenIdentity(
            principal_id=principal.id,
            principal_type=principal_type,  # type: ignore[arg-type]
            scope=scope,
            auth_method="session",
        )

    def _touch(self, row: SessionRow, moment_ts: str, moment: datetime) -> None:
        try:
            if moment - parse_datetime(row.last_seen_at) < LAST_SEEN_COARSENESS:
                return
        except ValueError:
            pass
        with self._db.write() as conn:
            self._sessions.touch_last_seen(conn, row.id, moment_ts)

    # -------------------------------------------------------------------- csrf

    def verify_csrf(self, cookie_value: str, presented_csrf: str | None) -> bool:
        """Compare a presented ``X-GW-CSRF`` header against the session-bound hash
        (DD-10), using ``hmac.compare_digest`` rather than ``==`` so the comparison
        does not leak timing information about how much of the token matched."""
        if not presented_csrf:
            return False
        with self._db.read() as conn:
            row = self._sessions.get_by_hash(conn, hash_session_value(cookie_value))
        if row is None:
            return False
        return hmac.compare_digest(hash_session_value(presented_csrf), row.csrf_hash)

    # ------------------------------------------------------------------- revoke

    def revoke_by_cookie(
        self, actor: ActorContext, cookie_value: str, now: datetime | None = None
    ) -> None:
        """Logout (FR-A3): deletes the row outright, so a replayed cookie resolves to
        nothing rather than to a merely-flagged one. Silently a no-op for a cookie that
        matches no live row, so logout is safe to call more than once."""
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            row = self._sessions.get_by_hash(conn, hash_session_value(cookie_value))
            if row is None:
                return
            self._sessions.delete(conn, row.id)
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="session",
                        entity_id=row.id,
                        action="revoke",
                        note="logout",
                    )
                ],
            )

    def revoke_all_for_principal(
        self, actor: ActorContext, principal_id: str, now: datetime | None = None
    ) -> None:
        """Every live session for one principal, deleted in a transaction this method
        opens and commits itself.

        ``PrincipalService`` reaches ``SessionRepository`` directly rather than through
        this method when it needs revocation in the *same* transaction as a principal
        write (``deactivate_principal``, and OIDC re-provisioning's role-change branch),
        matching how it already holds ``AccessTokenRepository`` directly for the
        equivalent PAT revocation. This method is the standalone entry point: it exists
        so the capability is independently testable and so a future administrator
        "sign this user out everywhere" control has
        something to call without duplicating the loop."""
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            live = self._sessions.list_live_for_principal(conn, principal_id)
            events = []
            for row in live:
                self._sessions.delete(conn, row.id)
                events.append(
                    make_event(
                        actor,
                        ts,
                        entity_type="session",
                        entity_id=row.id,
                        action="revoke",
                        note="principal deactivated or role changed",
                    )
                )
            if events:
                self._audit.append(conn, events)
