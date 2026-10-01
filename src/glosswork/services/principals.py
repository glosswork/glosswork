"""Principals and service accounts (FR-I1, FR-I3, FR-I5, docs/DATA_MODEL.md section 2).

The single actor table. A principal is either a human who can log in or a service
account that only holds tokens. Every ``created_by``, ``updated_by``, comment author,
and audit actor column is a foreign key onto it, which is why principals are
*deactivated*, never deleted.

Three responsibilities live here rather than in an adapter (DD-3):

- lifecycle: create, list, get, rename, change role, deactivate, each audited;
- local password accounts: set and verify a password through Argon2id, with the
  unknown-email and wrong-password branches deliberately indistinguishable;
- OIDC just-in-time provisioning: a verified identity with no matching principal
  creates one, and a returning identity has its role and display name refreshed from
  the *current* claims, so revoking the Okta admin group actually demotes.

Role changes carry one consequence that must not be left to a later sweep: lowering a
principal to ``member`` revokes that principal's ``admin``-scoped tokens **in the same
transaction**. Central scope enforcement compares only ``ActorContext.scope``, so
without this the FR-I4 mint-time ceiling would hold only at mint time.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from glosswork.actor import ActorContext, Scope
from glosswork.auth import VALID_SCOPES, scope_allows
from glosswork.db import Database
from glosswork.errors import (
    AuthenticationFailedError,
    InsufficientScopeError,
    NotFoundError,
    ValidationFailedError,
)
from glosswork.repositories.interfaces import (
    AccessTokenRepository,
    AuditRepository,
    PrincipalRepository,
    SessionRepository,
)
from glosswork.repositories.models import PrincipalRow
from glosswork.services.base import make_event
from glosswork.services.passwords import PasswordService
from glosswork.timeutil import format_datetime, utc_now

Role = Literal["admin", "creator", "member"]
# Ordered ``member < creator < admin``. ``creator`` may define its own
# object types and holds ``admin`` credential scope to reach the schema routes at all;
# what keeps it out of ``/admin/export`` is ``require_role``, not its scope.
VALID_ROLES: frozenset[str] = frozenset({"admin", "creator", "member"})

# The scopes a token may hold that a ``member`` principal may not (FR-I4). Kept as a
# list so the demotion sweep and the mint-time ceiling read from one declaration.
#
# **This is not the gate on anything.** All three sites that compare roles derive from
# :func:`role_scope`, which is the same "one
# ordering, not two" discipline ``auth.LEVEL_ORDER`` applies to levels. The constant
# survives only as the argument to ``list_live_by_scope`` in the deactivation sweep,
# which wants every scope, not a ceiling.
ABOVE_MEMBER_SCOPES: list[str] = ["admin"]

# Directory paging bounds. Policy, so they live in the service layer: the
# repository takes whatever limit it is handed and turns it into a LIMIT clause.
DIRECTORY_DEFAULT_LIMIT = 50
DIRECTORY_MAX_LIMIT = 200

# Deliberately identical for every failure mode of a password login: unknown email,
# wrong password, and deactivated principal all produce this exact message, so the
# response cannot be used to enumerate users.
_LOGIN_FAILED_MESSAGE = (
    "Email or password is incorrect. If you have forgotten your password, ask an "
    "administrator to set a new one."
)


def role_scope(role: str) -> Scope:
    """The scope a role-derived credential carries.

    ``admin`` -> ``admin``, ``creator`` -> ``admin``, anything else -> ``write``.
    Session cookies carry no scope of their own and derive one at the edge through this.

    It is also **the single declaration the three role gates read from**: the last-admin
    guard, the demotion token sweep, and the mint ceiling all compare scopes derived here
    rather than comparing role strings each in their own way. That is what makes adding a
    third role a one-line change here instead of three silent behaviour changes elsewhere.

    A ``creator`` needs ``admin`` credential scope because ``POST /object-types`` and
    every schema-editing route declares ``admin``. What stops a creator reaching
    ``/admin/export`` is ``require_role``, not its scope.
    """
    return "admin" if role in ("admin", "creator") else "write"


# ------------------------------------------------------- the user_ref resolver
#
# Exactly **one** function turns a ``user_ref`` reference into
# a principal id, and it has three callers -- the record write path
# (``services/records.py::_validate_values``), the filter compiler (through the callable
# injected on ``FilterContext``), and CSV import, which inherits it through the first with
# no code of its own. ``tests/test_one_principal_resolver.py`` fails if a fourth appears,
# or if anything outside this module calls ``principal_exists`` directly, which is how a
# write path would bypass the funnel.


def resolve_principal_ref(
    conn: Connection,
    repo: PrincipalRepository,
    value: str,
    *,
    me: str | None,
    allow_inactive: bool = False,
    allow_service_accounts: bool = True,
    stored_value: str | None = None,
) -> str:
    """One ``user_ref`` reference to a principal id. Resolution order, first match wins:

      1. ``@me``                 -> the calling principal
      2. an exact principal id -> itself
      3. an exact email        -> that principal (emails are UNIQUE per the schema)
      4. an exact display name -> that principal, case-insensitively, IF exactly one
                                  candidate matches

    Ordered so an id can never be shadowed by a name, and ``@me`` can never be a display
    name. Ambiguity is an error, never a pick, and is evaluated over whichever candidate
    set the mode considers: ``validation_failed`` naming every candidate with its email.

    The three mode flags are the read/write asymmetry DD-24 records:

    ``allow_inactive``
        ``True`` only for filters. Finding a departed colleague's open work is the
        handover query, and a filter is a read. Every write leaves it ``False``.
    ``allow_service_accounts``
        Read from the field's ``config`` by the caller; ``True`` by default, so a field
        that does not set it accepts service accounts. Never consulted for a
        pseudo-field, which has no config at all.
    ``stored_value``
        The field's currently stored value on an update. An id equal to it
        resolves **even when the principal is inactive**, so re-submitting an unchanged
        value -- the detail card's per-field Edit, a ``bulk_update`` naming the field, and
        CSV re-import of an exported record -- does not brick when someone leaves.
        Setting an inactive principal afresh stays rejected, and resolution by name or by
        email on a write never returns an inactive principal.
    """
    if not isinstance(value, str) or not value:
        raise ValidationFailedError("A user reference must be a non-empty string.")
    reference = value.strip()

    # 1. @me. First, so it can never be shadowed by someone's display name.
    if reference == "@me":
        if me is None:
            raise ValidationFailedError(
                "'@me' has no meaning here: there is no calling principal to resolve it to."
            )
        return me

    # 2. An exact principal id. The hot path -- a live id on a field with the default
    # permissive rule -- is one boolean SELECT and needs no row.
    if allow_service_accounts and repo.principal_exists(conn, reference):
        return reference
    by_id = repo.get(conn, reference)
    if by_id is not None:
        # An id equal to what is already stored resolves regardless of
        # ``is_active``, because re-submitting an unchanged value is not a new assignment.
        if stored_value is not None and by_id.id == stored_value:
            _reject_service_account(by_id, allow_service_accounts)
            return by_id.id
        return _accept(by_id, allow_inactive, allow_service_accounts)

    # 3. An exact email. ``principals.email`` is UNIQUE, so this can never be ambiguous.
    by_email = repo.get_by_email(conn, reference.lower())
    if by_email is not None:
        return _accept(by_email, allow_inactive, allow_service_accounts)

    # 4. An exact display name, case-insensitively, if exactly one candidate matches the
    # mode. Narrowing happens *before* counting: a name shared by one active and one
    # deactivated principal is unambiguous on a write and ambiguous in a filter.
    candidates = repo.find_by_display_name(conn, reference)
    if not allow_service_accounts:
        candidates = [c for c in candidates if c.type != "service_account"]
    if not allow_inactive:
        candidates = [c for c in candidates if c.is_active]
    if len(candidates) == 1:
        return candidates[0].id
    if len(candidates) > 1:
        named = ", ".join(
            f"{c.display_name} <{c.email or 'no email'}>"
            for c in sorted(candidates, key=lambda c: (c.email or "", c.id))
        )
        raise ValidationFailedError(
            f"{reference!r} matches {len(candidates)} principals: {named}. "
            "Use the email address or the principal id instead.",
            candidates=[
                {"id": c.id, "display_name": c.display_name, "email": c.email}
                for c in sorted(candidates, key=lambda c: (c.email or "", c.id))
            ],
        )
    raise ValidationFailedError(
        f"{reference!r} does not name a principal. Give a principal id, an email address, "
        "an exact display name, or '@me'; find_principals lists the directory."
    )


def _accept(row: PrincipalRow, allow_inactive: bool, allow_service_accounts: bool) -> str:
    _reject_service_account(row, allow_service_accounts)
    if not allow_inactive and not row.is_active:
        raise ValidationFailedError(
            f"{row.display_name} is deactivated and cannot be assigned. An id already "
            "stored on the field still saves unchanged."
        )
    return row.id


def _reject_service_account(row: PrincipalRow, allow_service_accounts: bool) -> None:
    if not allow_service_accounts and row.type == "service_account":
        raise ValidationFailedError(
            f"{row.display_name} is a service account, and this field does not accept one "
            "(allow_service_accounts is false)."
        )


class PrincipalService:
    def __init__(
        self,
        db: Database,
        principal_repo: PrincipalRepository,
        token_repo: AccessTokenRepository,
        audit_repo: AuditRepository,
        passwords: PasswordService,
        session_repo: SessionRepository,
    ) -> None:
        self._db = db
        self._principals = principal_repo
        self._tokens = token_repo
        self._audit = audit_repo
        self._passwords = passwords
        self._sessions = session_repo

    @property
    def passwords(self) -> PasswordService:
        return self._passwords

    # ------------------------------------------------------------------ reads

    def get_principal(self, principal_id: str) -> PrincipalRow:
        with self._db.read() as conn:
            return self._require(conn, principal_id)

    def get_principal_or_none(self, principal_id: str) -> PrincipalRow | None:
        """The row or None. ``PatTokenResolver`` uses this on every request: a missing
        principal there is a refusal to shape, not a ``NotFoundError`` to propagate."""
        with self._db.read() as conn:
            return self._principals.get(conn, principal_id)

    def find_by_email(self, email: str) -> PrincipalRow | None:
        with self._db.read() as conn:
            return self._principals.get_by_email(conn, self._normalize_email(email))

    def search_principals(
        self,
        q: str | None = None,
        principal_type: str | None = None,
        include_inactive: bool = False,
        limit: int = DIRECTORY_DEFAULT_LIMIT,
    ) -> list[PrincipalRow]:
        """The directory read behind ``GET /api/v1/principals/directory`` and the
        ``find_principals`` MCP tool.

        Deliberately **not** ``list_principals`` with different defaults: that one is an
        ``admin``-only management read over whole rows, and this one is readable by any
        authenticated principal and is projected down to five keys by
        ``envelopes.principal_directory_doc``. Keeping them separate is what makes the
        narrower projection a property of the route rather than of a caller remembering
        to strip fields.

        Rows come back whole because a repository returns rows; the projection is the
        envelope's job, and a test asserts ``role`` never survives it.
        """
        if principal_type is not None and principal_type not in ("user", "service_account"):
            raise ValidationFailedError(
                f"Unknown principal type {principal_type!r}. Use 'user' or 'service_account'.",
                type=principal_type,
            )
        if limit < 1:
            raise ValidationFailedError("limit must be at least 1.")
        with self._db.read() as conn:
            return self._principals.search_principals(
                conn,
                q=q,
                principal_type=principal_type,
                include_inactive=include_inactive,
                limit=min(limit, DIRECTORY_MAX_LIMIT),
            )

    def principals_by_ids(self, ids: list[str]) -> list[PrincipalRow]:
        """Every principal named by ``ids``, in one read.

        The sidecar's only read. Unknown ids are absent rather than an error: a referent
        that is gone renders as its raw id on the client, which is the fallback
        ``AuditTimeline`` has used for principal display names since DD-25.
        """
        if not ids:
            return []
        with self._db.read() as conn:
            return self._principals.principals_by_ids(conn, ids)

    def list_principals(
        self, principal_type: str | None = None, include_inactive: bool = True
    ) -> list[PrincipalRow]:
        if principal_type is not None and principal_type not in ("user", "service_account"):
            raise ValidationFailedError(
                f"Unknown principal type {principal_type!r}. Use 'user' or 'service_account'.",
                type=principal_type,
            )
        with self._db.read() as conn:
            return self._principals.list_principals(conn, principal_type, include_inactive)

    def active_admin_user_count(self) -> int:
        with self._db.read() as conn:
            return self._principals.count_active_admin_users(conn)

    # ----------------------------------------------------------------- writes

    def create_user(
        self,
        actor: ActorContext,
        *,
        email: str,
        display_name: str,
        role: str = "member",
        auth_provider: str = "local",
        external_id: str | None = None,
        password: str | None = None,
        now: datetime | None = None,
    ) -> PrincipalRow:
        """A human who can log in (FR-I3). ``password`` is optional because an OIDC
        principal never has one; a ``local`` principal without one cannot log in until
        an administrator sets it.

        Split into :meth:`prepare_user` and :meth:`create_user_in_txn` because the first
        administrator's claim (DD-22) needs the check, the create and the mint to be one
        transaction. The order is deliberate: validation and the Argon2 hash happen *before*
        the writer lock is taken, because holding SQLite's single writer across a ~30 ms
        hash would serialize every other write in the deployment behind it.
        """
        row = self.prepare_user(
            actor,
            email=email,
            display_name=display_name,
            role=role,
            auth_provider=auth_provider,
            external_id=external_id,
            password=password,
            now=now,
        )
        with self._db.write() as conn:
            self.create_user_in_txn(conn, actor, row)
        return row

    def prepare_user(
        self,
        actor: ActorContext,
        *,
        email: str,
        display_name: str,
        role: str = "member",
        auth_provider: str = "local",
        external_id: str | None = None,
        password: str | None = None,
        now: datetime | None = None,
    ) -> PrincipalRow:
        """Everything :meth:`create_user` does before it takes the writer lock:
        validate the role, name and email, hash the password, and build the row.

        Public because a caller that needs the insert inside its own transaction still
        must not do the validation or the hashing there, and must not re-implement
        either. Touches no connection.
        """
        self._require_role(role)
        cleaned_name = self._require_text(display_name, "display_name")
        normalized_email = self._normalize_email(self._require_text(email, "email"))
        password_hash = self._passwords.hash(password) if password is not None else None
        ts = format_datetime(now or utc_now())
        return PrincipalRow(
            id=str(uuid.uuid4()),
            type="user",
            display_name=cleaned_name,
            email=normalized_email,
            role=role,
            auth_provider=auth_provider,
            external_id=external_id,
            password_hash=password_hash,
            is_active=True,
            description=None,
            created_at=ts,
            created_by=actor.principal_id,
        )

    def create_user_in_txn(
        self,
        conn: Connection,
        actor: ActorContext,
        row: PrincipalRow,
        *,
        source: str | None = None,
    ) -> PrincipalRow:
        """The insert and its audit row, on a transaction the caller owns (DD-22).

        ``row`` comes from :meth:`prepare_user`, so everything that can be refused has
        been refused already and nothing here can raise a validation error with the
        writer lock held. A nested ``db.write()`` would take a second connection and
        deadlock on that lock, which is why this takes ``conn`` rather than opening one.

        ``source`` joins the audit row's note when the person was made by something other
        than an administrator's direct act: ``invite accepted`` for a first code sign-in.
        """
        self._insert(conn, row)
        details: dict[str, Any] = {"type": "user", "role": row.role}
        if source is not None:
            details["source"] = source
        self._audit.append(
            conn,
            [self._event(actor, row.created_at, row, "create", details)],
        )
        return row

    def reactivate_in_txn(
        self,
        conn: Connection,
        actor: ActorContext,
        principal: PrincipalRow,
        *,
        role: str,
        ts: str,
        source: str,
    ) -> PrincipalRow:
        """Bring a removed person back, on a transaction the caller owns (change 9).

        The one way back from ``deactivate_principal``: an administrator invites the
        removed person's address, and their first code sign-in reactivates this same
        principal with the invite's role, so their history stays theirs. Audited as
        ``principal update`` of ``is_active`` and, when it changes, ``role``, each noted
        with ``source``. Nothing was left to revoke: removal already revoked every token
        and session."""
        self._require_role(role)
        changes: dict[str, Any] = {"is_active": 1}
        note = {"source": source}
        events = [
            self._event(
                actor,
                ts,
                principal,
                "update",
                note,
                field_key="is_active",
                old_value=False,
                new_value=True,
            )
        ]
        if role != principal.role:
            changes["role"] = role
            events.append(
                self._event(
                    actor,
                    ts,
                    principal,
                    "update",
                    note,
                    field_key="role",
                    old_value=principal.role,
                    new_value=role,
                )
            )
        self._principals.update_row(conn, principal.id, changes)
        self._audit.append(conn, events)
        return self._require(conn, principal.id)

    def create_service_account(
        self,
        actor: ActorContext,
        *,
        display_name: str,
        description: str,
        role: str = "member",
        now: datetime | None = None,
    ) -> PrincipalRow:
        """A non-human principal that only holds tokens (FR-I5).

        ``description`` is required and names the account's purpose: descriptions on
        agent-facing objects are required across this product (AGENTS.md
        non-negotiable 6), and a service account with no stated purpose is exactly the
        credential nobody can later decide whether to revoke.
        """
        self._require_role(role)
        cleaned_name = self._require_text(display_name, "display_name")
        cleaned_description = self._require_text(
            description,
            "description",
            hint="Say what this service account is for and who operates it.",
        )
        ts = format_datetime(now or utc_now())
        row = PrincipalRow(
            id=str(uuid.uuid4()),
            type="service_account",
            display_name=cleaned_name,
            email=None,
            role=role,
            auth_provider=None,
            external_id=None,
            password_hash=None,
            is_active=True,
            description=cleaned_description,
            created_at=ts,
            created_by=actor.principal_id,
        )
        with self._db.write() as conn:
            self._insert(conn, row)
            self._audit.append(
                conn,
                [self._event(actor, ts, row, "create", {"type": "service_account", "role": role})],
            )
        return row

    def update_principal(
        self,
        actor: ActorContext,
        principal_id: str,
        *,
        display_name: str | None = None,
        role: str | None = None,
        description: str | None = None,
        now: datetime | None = None,
    ) -> PrincipalRow:
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            existing = self._require(conn, principal_id)
            changes: dict[str, Any] = {}
            events = []
            if display_name is not None:
                cleaned = self._require_text(display_name, "display_name")
                if cleaned != existing.display_name:
                    changes["display_name"] = cleaned
                    events.append(
                        self._event(
                            actor,
                            ts,
                            existing,
                            "update",
                            {"display_name": cleaned},
                            field_key="display_name",
                            old_value=existing.display_name,
                            new_value=cleaned,
                        )
                    )
            if description is not None:
                cleaned_description = self._require_text(description, "description")
                if cleaned_description != existing.description:
                    changes["description"] = cleaned_description
                    events.append(
                        self._event(
                            actor,
                            ts,
                            existing,
                            "update",
                            {},
                            field_key="description",
                            old_value=existing.description,
                            new_value=cleaned_description,
                        )
                    )
            if role is not None:
                self._require_role(role)
                if role != existing.role:
                    changes["role"] = role
                    events.append(
                        self._event(
                            actor,
                            ts,
                            existing,
                            "update",
                            {},
                            field_key="role",
                            old_value=existing.role,
                            new_value=role,
                        )
                    )
            if not changes:
                return existing
            new_role = changes.get("role")
            if new_role is not None and new_role != "admin":
                # **Any** transition away from `admin`, not only one to `member`.
                # Comparing against the literal `"member"` would let a third role value
                # walk the last administrator out of the deployment without the guard
                # firing.
                self._guard_last_admin(conn, existing)
            self._principals.update_row(conn, principal_id, changes)
            if new_role is not None:
                # Same transaction as the role change, deliberately (FR-I4): a PAT
                # minted before a demotion must not still open a route afterwards, and
                # central enforcement reads only the token's scope. Which tokens are now
                # over-scoped is derived from `role_scope(new_role)` rather than from the
                # role string, so `member -> creator` revokes nothing.
                events.extend(self._revoke_over_scoped(conn, actor, existing, new_role, ts))
            self._audit.append(conn, events)
            refreshed = self._require(conn, principal_id)
        return refreshed

    def deactivate_principal(
        self, actor: ActorContext, principal_id: str, now: datetime | None = None
    ) -> PrincipalRow:
        """Set ``is_active = 0`` rather than deleting the row (FR-I3). Takes effect on
        the next request: ``PatTokenResolver`` refuses a token whose principal is
        inactive, so no cached identity survives."""
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            existing = self._require(conn, principal_id)
            if not existing.is_active:
                return existing
            self._guard_last_admin(conn, existing)
            self._principals.update_row(conn, principal_id, {"is_active": 0})
            events = [
                self._event(
                    actor,
                    ts,
                    existing,
                    "update",
                    {},
                    field_key="is_active",
                    old_value=True,
                    new_value=False,
                )
            ]
            events.extend(
                self._revoke_tokens(
                    conn,
                    actor,
                    existing,
                    ts,
                    self._tokens.list_live_by_scope(
                        conn, existing.id, ["read", "write", "admin"], ts
                    ),
                    note="principal deactivated",
                )
            )
            events.extend(
                self._revoke_sessions(conn, actor, existing, ts, note="principal deactivated")
            )
            self._audit.append(conn, events)
            refreshed = self._require(conn, principal_id)
        return refreshed

    def set_password(
        self,
        actor: ActorContext,
        principal_id: str,
        password: str,
        *,
        keep_session_hash: str | None = None,
        expected_password_hash: str | None = None,
        require_kept_session: bool = False,
        now: datetime | None = None,
    ) -> PrincipalRow:
        """Set a local account's password, and **revoke every credential it holds**
        (DD-13).

        A reset is the containment action an operator reaches for after a phished password
        or a stolen cookie, and without revocation it would contain nothing: the attacker's
        session would live to its absolute expiry and any PAT they had minted would live
        forever, because PATs default to no expiry. So the tokens and the sessions go in the
        same transaction as the hash, exactly as they do on ``deactivate_principal``.

        ``keep_session_hash`` spares one session: the one that made the request, when an
        administrator is resetting their **own** password. Revoking it would log the
        caller out on success, which is the pattern the OIDC role-change path already
        avoids by rotating only the principal's *other* devices. The route decides which
        session that is and passes the value down (DD-3); this method never sees a
        cookie, only its sha256.

        ``verify_password``'s transparent rehash-on-login is deliberately **not** this
        method, and revokes nothing: an operator raising the Argon2 cost parameters
        would otherwise sign the whole deployment out one login at a time.

        ``expected_password_hash`` and ``require_kept_session`` are a race guard used
        only by ``change_own_password``, and both default to "off" so this method's
        behaviour for its other callers -- the admin route and the operator CLI -- does
        not depend on them. A self-change verifies the current password and
        then calls this method, and an administrator's containment reset can land in
        the gap between those two steps. When either is set, this method re-checks the
        row it just loaded -- still inside the write transaction and before any
        update -- and refuses with ``validation_failed`` naming ``current_password`` if
        the stored hash no longer matches ``expected_password_hash``, if the principal
        has since been deactivated, or (``require_kept_session``) if no live session
        still carries ``keep_session_hash``. Any of those is what a concurrent
        administrator's reset looks like from in here, and it must win: the self-change
        that loses the race commits nothing.
        """
        ts = format_datetime(now or utc_now())
        password_hash = self._passwords.hash(password)
        with self._db.write() as conn:
            existing = self._require(conn, principal_id)
            if existing.type != "user":
                raise ValidationFailedError(
                    "Only a user principal can have a password; a service account "
                    "authenticates with a personal access token.",
                    principal_id=principal_id,
                )
            if expected_password_hash is not None or require_kept_session:
                self._guard_against_concurrent_change(
                    conn,
                    existing,
                    expected_password_hash=expected_password_hash,
                    require_kept_session=require_kept_session,
                    keep_session_hash=keep_session_hash,
                )
            self._principals.update_row(conn, principal_id, {"password_hash": password_hash})
            # The hash itself is never an audit value: the row records that the
            # password changed, not what it changed to.
            events = [self._event(actor, ts, existing, "update", {}, field_key="password_hash")]
            events.extend(
                self._revoke_tokens(
                    conn,
                    actor,
                    existing,
                    ts,
                    self._tokens.list_live_by_scope(
                        conn, existing.id, ["read", "write", "admin"], ts
                    ),
                    note="password reset",
                )
            )
            events.extend(
                self._revoke_sessions(
                    conn,
                    actor,
                    existing,
                    ts,
                    note="password reset",
                    keep_session_hash=keep_session_hash,
                )
            )
            self._audit.append(conn, events)
            refreshed = self._require(conn, principal_id)
        return refreshed

    def change_own_password(
        self,
        actor: ActorContext,
        current_password: str,
        new_password: str,
        *,
        keep_session_hash: str | None = None,
    ) -> PrincipalRow:
        """A signed-in person changes their own password (FR-I17, DD-13).

        Five steps, in this order, each closing a hole a different order would leave:

        1. Refuse anything but a browser session before computing or verifying a
           single byte: a personal access token handed to an agent must never be able
           to take over the human who minted it. The route has already counted this
           attempt against the rate limiter, the same as any other.
        2. Check ``new_password`` against the one ``PasswordPolicy`` **before**
           verifying ``current_password``: otherwise a stolen-cookie holder could
           use a deliberately too-short new password to learn, from the shape of the
           422 alone, whether a guessed current password was right, and a request
           doomed by the floor would pay for an Argon2id verification it never needed.
        3. Load the caller's own row and refuse anyone who cannot use a local password
           here at all -- not a ``user``, deactivated, provisioned by an identity
           provider, or with no hash set -- before spending a verification on it.
        4. Verify ``current_password`` with :meth:`PasswordService.verify`, which never
           rehashes: rehashing here would derive a fresh hash from the very
           password this call is about to replace, moments before replacing it.
        5. Delegate to :meth:`set_password` (one implementation of a password change)
           with the hash just verified as ``expected_password_hash``
           and ``require_kept_session=True``, so a concurrent administrator's
           containment reset -- landing in the gap between this method's verification
           and its own write -- wins the race rather than being silently overwritten.
        """
        if actor.auth_method != "session":
            raise InsufficientScopeError.for_session_only(
                "Changing your own password", actor.auth_method, actor.scope
            )
        self._passwords.policy.check(new_password)
        with self._db.read() as conn:
            row = self._require(conn, actor.principal_id)
        usable = (
            row.type == "user"
            and row.is_active
            and row.auth_provider == "local"
            and row.password_hash is not None
        )
        if not usable:
            raise ValidationFailedError(
                "This account has no password here; it signs in through your identity provider.",
                field_key="current_password",
            )
        assert row.password_hash is not None
        if not self._passwords.verify(row.password_hash, current_password):
            raise ValidationFailedError(
                "The current password is not correct.", field_key="current_password"
            )
        return self.set_password(
            actor,
            actor.principal_id,
            new_password,
            keep_session_hash=keep_session_hash,
            expected_password_hash=row.password_hash,
            require_kept_session=True,
        )

    # ------------------------------------------------------------ local login

    def verify_password(
        self, email: str, password: str, now: datetime | None = None
    ) -> PrincipalRow:
        """Verify a local-account login (FR-I1).

        Every failure raises the identical :class:`AuthenticationFailedError`, and the
        unknown-email branch performs a real Argon2id verification against a dummy
        hash so it costs the same as the found-principal branch. An enumeration oracle
        made of a timing difference is still an enumeration oracle.
        """
        normalized = self._normalize_email(email) if email else ""
        with self._db.read() as conn:
            principal = self._principals.get_by_email(conn, normalized) if normalized else None
        usable = (
            principal is not None
            and principal.type == "user"
            and principal.is_active
            and principal.password_hash is not None
            and principal.auth_provider == "local"
        )
        if not usable:
            self._passwords.verify_dummy()
            raise AuthenticationFailedError(_LOGIN_FAILED_MESSAGE)
        assert principal is not None and principal.password_hash is not None
        if not self._passwords.verify(principal.password_hash, password):
            raise AuthenticationFailedError(_LOGIN_FAILED_MESSAGE)
        if self._passwords.needs_rehash(principal.password_hash):
            # Transparent upgrade on the owner's next successful login: the cost
            # parameters moved, and this is the only moment the plaintext is available
            # to re-derive under them.
            upgraded = self._passwords.hash(password)
            with self._db.write() as conn:
                self._principals.update_row(conn, principal.id, {"password_hash": upgraded})
            principal.password_hash = upgraded
        return principal

    # -------------------------------------------------- OIDC JIT provisioning

    def provision_oidc_principal(
        self,
        actor: ActorContext,
        *,
        external_id: str,
        email: str | None,
        display_name: str,
        role: str,
        now: datetime | None = None,
    ) -> PrincipalRow:
        """Create or refresh the principal behind a verified OIDC identity (FR-I2).

        A returning identity's role and display name are taken from the *current*
        claims rather than the stored copy: the provider is authoritative, so removing
        someone from the Okta admin group demotes them on their next login instead of
        leaving a stale ``admin`` row behind. That demotion goes through the same path
        as an administrative one, so it revokes over-scoped tokens too.
        """
        self._require_role(role)
        cleaned_name = self._require_text(display_name, "display_name")
        normalized_email = self._normalize_email(email) if email else None
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            existing = self._principals.get_by_external(conn, "oidc", external_id)
            if existing is None:
                row = PrincipalRow(
                    id=str(uuid.uuid4()),
                    type="user",
                    display_name=cleaned_name,
                    email=normalized_email,
                    role=role,
                    auth_provider="oidc",
                    external_id=external_id,
                    password_hash=None,
                    is_active=True,
                    description=None,
                    created_at=ts,
                    created_by=actor.principal_id,
                )
                self._insert(conn, row)
                self._audit.append(
                    conn,
                    [
                        self._event(
                            actor, ts, row, "create", {"type": "user", "auth_provider": "oidc"}
                        )
                    ],
                )
                return row
            if not existing.is_active:
                raise AuthenticationFailedError(
                    "This account has been deactivated in Glosswork. Ask an "
                    "administrator to reactivate it."
                )
            changes: dict[str, Any] = {}
            events = []
            if cleaned_name != existing.display_name:
                changes["display_name"] = cleaned_name
            if normalized_email is not None and normalized_email != existing.email:
                changes["email"] = normalized_email
            if role != existing.role:
                changes["role"] = role
                events.append(
                    self._event(
                        actor,
                        ts,
                        existing,
                        "update",
                        {"source": "oidc_claims"},
                        field_key="role",
                        old_value=existing.role,
                        new_value=role,
                    )
                )
            if changes:
                self._principals.update_row(conn, existing.id, changes)
                if changes.get("role") is not None:
                    # Same derivation as `update_principal`: which tokens
                    # are now over-scoped comes from `role_scope(new_role)`, not from a
                    # role string, so a provider promoting `member -> creator` revokes
                    # nothing.
                    events.extend(
                        self._revoke_over_scoped(conn, actor, existing, changes["role"], ts)
                    )
                    # A role change from the provider is discovered mid-login, right
                    # before this same call chain issues a fresh session (DD-9, "role
                    # change rotates too"): the principal's *other* sessions, on
                    # other devices, are rotated out here rather than left to self-heal
                    # on their own next request. This is different from an
                    # administrator demoting someone else's live session
                    # (`update_principal`, below), which must keep working at the
                    # lower scope with no forced re-login.
                    events.extend(
                        self._revoke_sessions(conn, actor, existing, ts, note="role changed")
                    )
                if events:
                    self._audit.append(conn, events)
            refreshed = self._require(conn, existing.id)
        return refreshed

    # ---------------------------------------------------------------- helpers

    def _revoke_over_scoped(
        self,
        conn: Connection,
        actor: ActorContext,
        principal: PrincipalRow,
        new_role: str,
        ts: str,
    ) -> list[Any]:
        """Revoke exactly the live tokens whose scope the new role no longer supports.

        Derived from :func:`role_scope` rather than from a role string. Revoking every
        ``admin``-scoped PAT whenever the new role is not ``admin`` would strip a freshly
        promoted ``creator`` of the very credential it needs.
        """
        ceiling = role_scope(new_role)
        over_scoped: list[str] = [s for s in VALID_SCOPES if not scope_allows(ceiling, s)]
        if not over_scoped:
            return []
        live = self._tokens.list_live_by_scope(conn, principal.id, over_scoped, ts)
        return self._revoke_tokens(
            conn, actor, principal, ts, live, note="role lowered below the token's scope"
        )

    def _revoke_tokens(
        self,
        conn: Connection,
        actor: ActorContext,
        principal: PrincipalRow,
        ts: str,
        tokens: list[Any],
        note: str,
    ) -> list[Any]:
        events = []
        for token in tokens:
            self._tokens.update_row(conn, token.id, {"revoked_at": ts})
            events.append(
                make_event(
                    actor,
                    ts,
                    entity_type="access_token",
                    entity_id=token.id,
                    action="revoke",
                    field_key="revoked_at",
                    old_value=None,
                    new_value=ts,
                    note=note,
                )
            )
        return events

    def _revoke_sessions(
        self,
        conn: Connection,
        actor: ActorContext,
        principal: PrincipalRow,
        ts: str,
        note: str,
        keep_session_hash: str | None = None,
    ) -> list[Any]:
        """Delete every live session for ``principal``, in the caller's own
        transaction (DD-9). Reaches ``SessionRepository`` directly, exactly as
        ``_revoke_tokens`` reaches ``AccessTokenRepository`` directly, so both the
        token and the session cleanup a deactivation or a role change requires commit
        atomically with the principal write that triggered them rather than through
        ``SessionService``'s own standalone (separately transactioned)
        ``revoke_all_for_principal``.

        ``keep_session_hash`` spares exactly one row, identified by the sha256 the
        ``sessions`` table stores rather than by "the most recent". Two callers --
        ``deactivate_principal`` and the OIDC role-change path -- pass nothing and revoke
        everything."""
        events = []
        for row in self._sessions.list_live_for_principal(conn, principal.id):
            if keep_session_hash is not None and row.session_hash == keep_session_hash:
                continue
            self._sessions.delete(conn, row.id)
            events.append(
                make_event(
                    actor,
                    ts,
                    entity_type="session",
                    entity_id=row.id,
                    action="revoke",
                    note=note,
                )
            )
        return events

    def _guard_against_concurrent_change(
        self,
        conn: Connection,
        existing: PrincipalRow,
        *,
        expected_password_hash: str | None,
        require_kept_session: bool,
        keep_session_hash: str | None,
    ) -> None:
        """Refuse a self-change that lost its race against a concurrent
        administrator's containment reset. Reads exactly the liveness
        ``_revoke_sessions`` uses (``list_live_for_principal``, not ``get_by_hash``,
        which does not filter expired rows), so a session ``keep_session_hash`` names
        counts here only if it would also have survived that same reset.

        Raises the same ``validation_failed`` a wrong current password does, naming
        ``current_password``: from the caller's side the two look identical, because
        the password they verified a moment ago is no longer the one that counts.
        """
        stale = (
            expected_password_hash is not None and existing.password_hash != expected_password_hash
        ) or not existing.is_active
        if not stale and require_kept_session:
            live = self._sessions.list_live_for_principal(conn, existing.id)
            stale = not any(row.session_hash == keep_session_hash for row in live)
        if stale:
            raise ValidationFailedError(
                "Your password was changed by someone else while this request was "
                "in flight. Sign in again.",
                field_key="current_password",
            )

    def _guard_last_admin(self, conn: Connection, principal: PrincipalRow) -> None:
        """Refuse to remove the last way into the deployment.

        Demoting or deactivating the only active admin user would leave a running
        system nobody can administer, recoverable only through the operator CLI. That
        is a foot-gun worth one check, not a support ticket.
        """
        if principal.type != "user" or principal.role != "admin" or not principal.is_active:
            return
        if self._principals.count_active_admin_users(conn) <= 1:
            raise ValidationFailedError(
                "This is the only active administrator. Promote another user to admin "
                "first, or nobody will be able to administer this deployment.",
                principal_id=principal.id,
            )

    def _insert(self, conn: Connection, row: PrincipalRow) -> None:
        try:
            self._principals.insert(conn, row)
        except IntegrityError as exc:
            # ix_principals_external and the unique email index are the hard
            # enforcement; this turns either into a domain error rather than letting a
            # driver exception leak through the adapter.
            raise self._conflict(row, exc) from exc

    @staticmethod
    def _conflict(row: PrincipalRow, exc: IntegrityError) -> ValidationFailedError:
        detail = str(exc.orig) if exc.orig is not None else str(exc)
        if "external_id" in detail or "auth_provider" in detail:
            return ValidationFailedError(
                f"A principal for {row.auth_provider!r} subject {row.external_id!r} already "
                "exists. Two identities from the same provider cannot share a subject.",
                auth_provider=row.auth_provider,
                external_id=row.external_id,
            )
        if "email" in detail:
            return ValidationFailedError(
                f"A principal with email {row.email!r} already exists.", email=row.email
            )
        return ValidationFailedError(
            "That principal conflicts with an existing one.", principal_id=row.id
        )

    @staticmethod
    def _event(
        actor: ActorContext,
        ts: str,
        principal: PrincipalRow,
        action: str,
        note_details: dict[str, Any],
        *,
        field_key: str | None = None,
        old_value: Any = None,
        new_value: Any = None,
    ) -> Any:
        """One ``entity_type='principal'`` audit row (FR-D3, docs/DATA_MODEL.md
        section 9's enumeration). A password hash
        is never an audit value: the row records *that* the password changed."""
        note = ", ".join(f"{k}={v}" for k, v in sorted(note_details.items())) or None
        return make_event(
            actor,
            ts,
            entity_type="principal",
            entity_id=principal.id,
            action=action,
            field_key=field_key,
            old_value=old_value,
            new_value=new_value,
            note=note,
        )

    def _require(self, conn: Connection, principal_id: str) -> PrincipalRow:
        row = self._principals.get(conn, principal_id)
        if row is None:
            raise NotFoundError("principal", principal_id)
        return row

    @staticmethod
    def _require_role(role: str) -> None:
        if role not in VALID_ROLES:
            raise ValidationFailedError(
                f"Unknown role {role!r}. Valid roles: {', '.join(sorted(VALID_ROLES))}.",
                role=role,
            )

    @staticmethod
    def _require_text(value: str, name: str, hint: str = "") -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            suffix = f" {hint}" if hint else ""
            raise ValidationFailedError(
                f"{name} must be a non-empty string.{suffix}", **{name: value}
            )
        return cleaned

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()
