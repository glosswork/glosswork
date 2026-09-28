"""Personal access tokens (FR-I4, FR-I5, docs/DATA_MODEL.md section 2).

A token is `gw_pat_` plus 32 URL-safe random characters from ``secrets``. Only its
sha256 and its first 8 characters are stored; the plaintext is returned exactly once,
from :meth:`AccessTokenService.mint`, and exists nowhere else — not in a table, not in
a log line, not in an audit row. sha256 rather than a KDF is the right primitive here
and not an inconsistency with Argon2id for passwords: a 32-character random secret has
no low-entropy candidate set to grind, so a slow KDF would buy nothing while making
every request pay for it.

Two ceilings apply at mint time:

- **Role (FR-I4).** A ``member`` principal cannot mint an ``admin`` token; only an
  ``admin`` principal may.
- **The caller's own scope.** A credential cannot mint a token above itself. Without
  this, an administrator's deliberately narrow ``write`` token could mint an ``admin``
  one, which turns every ``write`` credential into a latent ``admin`` credential and
  makes the whole point of scoping a token illusory.

Neither ceiling is sufficient alone, and neither survives a role change on its own,
which is why demotion revokes over-scoped tokens in the same transaction
(:meth:`PrincipalService.update_principal`).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Connection

from glosswork.actor import ActorContext, Scope
from glosswork.auth import VALID_SCOPES, scope_allows
from glosswork.db import Database
from glosswork.errors import (
    InsufficientScopeError,
    NotFoundError,
    ValidationFailedError,
)
from glosswork.repositories.interfaces import (
    AccessTokenRepository,
    AuditRepository,
    PrincipalRepository,
)
from glosswork.repositories.models import AccessTokenRow, PrincipalRow
from glosswork.services.agent_labels import AgentLabelService
from glosswork.services.base import make_event
from glosswork.services.principals import role_scope
from glosswork.timeutil import format_datetime, parse_datetime, utc_now

TOKEN_PREFIX = "gw_pat_"
# DD-16. An upload ticket's own prefix, so a leaked string is identifiable at
# a glance and in a refusal (``token_prefix`` is the first eight characters), without
# anyone having to look the row up. It is still a ``dt_``-shaped secret in the same
# table with the same hashing: only the prefix and the three capability columns differ.
CAPABILITY_TOKEN_PREFIX = "gw_upl_"
TOKEN_SECRET_CHARS = 32
TOKEN_DISPLAY_PREFIX_CHARS = 8

# `last_used_at` is advanced at most once per this interval per token. Every
# authenticated request would otherwise be a write, which on SQLite means every read
# takes the write lock ("does not produce one write each").
LAST_USED_COARSENESS = timedelta(minutes=5)


def hash_token(plaintext: str) -> str:
    """The stored form of a presented secret. The only hashing this module does, and
    it never touches a password (that is Argon2id, in ``services/passwords.py``)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MintedToken:
    """The one and only carrier of a token's plaintext. Returned from ``mint`` and
    never persisted; every read path returns :class:`AccessTokenRow` instead."""

    row: AccessTokenRow
    plaintext: str


class AccessTokenService:
    def __init__(
        self,
        db: Database,
        token_repo: AccessTokenRepository,
        principal_repo: PrincipalRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._db = db
        self._tokens = token_repo
        self._principals = principal_repo
        self._audit = audit_repo

    # ------------------------------------------------------------------- mint

    def mint(
        self,
        actor: ActorContext,
        *,
        name: str,
        scope: str,
        principal_id: str | None = None,
        expires_at: str | None = None,
        agent_label: str | None = None,
        now: datetime | None = None,
    ) -> MintedToken:
        """Create a token and return its plaintext exactly once.

        ``principal_id`` defaults to the caller's own principal. Minting on behalf of
        another principal — a service account, typically — requires ``admin`` scope and
        an ``admin`` role, because it hands out a credential the caller will not be
        holding and therefore cannot be assumed to be accountable for.
        """
        with self._db.write() as conn:
            return self.mint_in_txn(
                conn,
                actor,
                name=name,
                scope=scope,
                principal_id=principal_id,
                expires_at=expires_at,
                agent_label=agent_label,
                now=now,
            )

    def mint_in_txn(
        self,
        conn: Connection,
        actor: ActorContext,
        *,
        name: str,
        scope: str,
        principal_id: str | None = None,
        expires_at: str | None = None,
        agent_label: str | None = None,
        now: datetime | None = None,
    ) -> MintedToken:
        """:meth:`mint`, on a transaction the caller owns (DD-22).

        Extracted so that the bootstrap claim can check "is this deployment already
        bootstrapped", create the administrator and mint its first token as one
        transaction. A nested ``db.write()`` takes a second connection and deadlocks on
        SQLite's own writer lock, so an atomic sequence has to be composed from methods
        shaped like this one rather than from the public ones.

        Every ceiling :meth:`mint` enforces is enforced here, because this *is* that
        body: the caller gains atomicity, never authority.
        """
        requested_scope = self._require_scope(scope)
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise ValidationFailedError(
                "A token name is required. Name it after where it will be used, so it "
                "can be revoked without guessing.",
                name=name,
            )
        # Validated **at mint**, which is the whole point: a malformed label stored
        # on a token would refuse every later call that token made. The
        # rule is the registry's own, shared rather than restated, and it registers
        # nothing -- ``register_use`` keeps its single call site.
        cleaned_label = (
            AgentLabelService.validate_label(agent_label) if agent_label is not None else None
        )
        normalized_expiry = self._normalize_expiry(expires_at, now)
        target_id = principal_id or actor.principal_id
        ts = format_datetime(now or utc_now())

        owner = self._require_principal(conn, target_id)
        if target_id != actor.principal_id:
            self._require_admin_delegation(conn, actor, "Minting")
        if not owner.is_active:
            raise ValidationFailedError(
                f"Principal {owner.display_name!r} is deactivated; a token minted for "
                "it would not resolve. Reactivate the principal first.",
                principal_id=owner.id,
            )
        self._check_ceilings(actor, owner, requested_scope)

        plaintext = TOKEN_PREFIX + secrets.token_urlsafe(48)[:TOKEN_SECRET_CHARS]
        row = AccessTokenRow(
            id=str(uuid.uuid4()),
            principal_id=owner.id,
            name=cleaned_name,
            token_hash=hash_token(plaintext),
            token_prefix=plaintext[:TOKEN_DISPLAY_PREFIX_CHARS],
            scope=requested_scope,
            expires_at=normalized_expiry,
            last_used_at=None,
            revoked_at=None,
            created_at=ts,
            created_by=actor.principal_id,
            agent_label=cleaned_label,
        )
        self._tokens.insert(conn, row)
        self._audit.append(
            conn,
            [
                make_event(
                    actor,
                    ts,
                    entity_type="access_token",
                    entity_id=row.id,
                    action="create",
                    # The plaintext is deliberately absent. The prefix and scope
                    # are what an administrator needs to recognise the token later;
                    # the secret is what nobody may recover from this row.
                    new_value={
                        "token_prefix": row.token_prefix,
                        "scope": row.scope,
                        "principal_id": row.principal_id,
                        "expires_at": row.expires_at,
                    },
                    note=f"minted token {cleaned_name!r}",
                )
            ],
        )
        return MintedToken(row=row, plaintext=plaintext)

    def mint_capability_token(
        self,
        actor: ActorContext,
        *,
        capability: str,
        capability_data: dict[str, Any],
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> MintedToken:
        """Mint a **capability token**: a credential narrowed to one named operation
        (DD-16).

        It is an ordinary row in this table at ``write`` scope with a short
        ``expires_at``, which is the whole reason the design is affordable. Everything
        that makes a PAT safe is inherited rather than restated: the plaintext is
        returned once and only its sha256 is stored, ``token_prefix`` makes a refusal
        legible, ``PatTokenResolver`` enforces expiry and the deactivated-principal
        check, and the revoke-everything on a password reset or a deactivation already
        reads ``list_live_by_scope``, which does not exclude these rows.

        The narrowing only ever narrows (DD-11): ``write`` is hard-coded rather than
        taken from the caller, so an ``admin`` credential minting a ticket still produces
        a ``write`` one, and :meth:`_check_ceilings` still refuses a caller below that.

        **This method deliberately does not check whether its caller is itself holding a
        ticket.** A capability is read for authorization at exactly one predicate, and
        adding a second reader here is how a default-closed property stops being one. The
        recursion is closed by unreachability instead: ``create_attachment_upload`` exists
        only as an MCP tool, and ``scopes.refuse_capability_credential`` closes the whole
        MCP surface to a ticket, so nothing a ticket can reach arrives here.

        Expired, consumed and revoked tickets are deleted as one is minted, so the table
        does not grow without bound and no scheduled job has to exist to keep that true.
        """
        ts = format_datetime(now or utc_now())
        expiry = format_datetime((now or utc_now()) + timedelta(seconds=ttl_seconds))
        with self._db.write() as conn:
            owner = self._require_principal(conn, actor.principal_id)
            if not owner.is_active:
                raise ValidationFailedError(
                    f"Principal {owner.display_name!r} is deactivated; a ticket minted "
                    "for it would not resolve.",
                    principal_id=owner.id,
                )
            self._check_ceilings(actor, owner, "write")
            self._tokens.delete_spent_capability_tokens(conn, ts)

            plaintext = CAPABILITY_TOKEN_PREFIX + secrets.token_urlsafe(48)[:TOKEN_SECRET_CHARS]
            row = AccessTokenRow(
                id=str(uuid.uuid4()),
                principal_id=owner.id,
                name=f"{capability} ticket",
                token_hash=hash_token(plaintext),
                token_prefix=plaintext[:TOKEN_DISPLAY_PREFIX_CHARS],
                scope="write",
                expires_at=expiry,
                last_used_at=None,
                revoked_at=None,
                created_at=ts,
                created_by=actor.principal_id,
                capability=capability,
                capability_data=json.dumps(capability_data, sort_keys=True),
                consumed_at=None,
            )
            self._tokens.insert(conn, row)
            # No audit event. A ticket is a transient narrowing of the credential the
            # caller already holds, and the act that matters -- the attachment it
            # creates -- appends its own event naming the same principal. One audit row
            # per upload rather than two, and the ``access_token`` create events an
            # administrator reads stay a list of durable credentials.
        return MintedToken(row=row, plaintext=plaintext)

    def consume_capability_in_txn(
        self, conn: Connection, token_id: str, now: datetime | None = None
    ) -> AccessTokenRow:
        """Spend one ticket, **inside the caller's transaction**.

        The ``*_in_txn`` pattern, and mandatory rather than stylistic: a nested
        ``db.write()`` takes a second connection and deadlocks on this codebase's own
        writer lock. The conditional ``UPDATE ... WHERE consumed_at IS NULL`` is what
        makes two concurrent uses of one ticket impossible to both win, and the rowcount
        assertion is what turns a lost race into a refusal rather than a second
        attachment. Because it runs in the attachment insert's transaction, a rollback
        un-spends the ticket.
        """
        row = self._tokens.get(conn, token_id)
        if row is None or row.capability is None:
            raise NotFoundError("upload ticket", token_id)
        spent = self._tokens.consume_capability(conn, token_id, format_datetime(now or utc_now()))
        if spent != 1:
            raise ValidationFailedError(
                "This upload ticket has already been used. An upload ticket is single "
                "use; call create_attachment_upload again for a fresh one.",
                token_prefix=row.token_prefix,
            )
        return row

    # ------------------------------------------------------------ list/revoke

    def list_tokens(
        self, actor: ActorContext, principal_id: str | None = None
    ) -> list[AccessTokenRow]:
        """Every token for one principal. The secret is not in :class:`AccessTokenRow`
        to be leaked: only ``token_prefix``, name, scope, and the timestamps.

        ``principal_id`` defaults to the caller's own principal, and naming somebody
        else's is a **delegation**, not a filter (DD-12). It goes through the
        same :meth:`_require_admin_delegation` the mint path uses, because listing a
        principal's credentials and minting one for it are the two halves of one act:
        the inventory of which service accounts hold never-expiring ``admin`` tokens is
        exactly what a credential's confidentiality is protecting. A route that took
        ``principal_id`` as a free query parameter, over a service that took no actor,
        would let any authenticated ``read`` credential walk the deployment.
        """
        target_id = principal_id or actor.principal_id
        with self._db.read() as conn:
            if target_id != actor.principal_id:
                self._require_admin_delegation(conn, actor, "Listing")
            return self._tokens.list_for_principal(conn, target_id)

    def get_token(self, token_id: str) -> AccessTokenRow:
        with self._db.read() as conn:
            row = self._tokens.get(conn, token_id)
            if row is None:
                raise NotFoundError("access token", token_id)
            return row

    def revoke(
        self, actor: ActorContext, token_id: str, now: datetime | None = None
    ) -> AccessTokenRow:
        """Set ``revoked_at``. The token stops resolving on the next request: the
        resolver reads the row every time and never caches an identity."""
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            row = self._tokens.get(conn, token_id)
            if row is None:
                raise NotFoundError("access token", token_id)
            if row.principal_id != actor.principal_id and not scope_allows(actor.scope, "admin"):
                # Not an ownership check dressed as authorization: a token belonging to
                # someone else is not the caller's to revoke unless they administer the
                # deployment, and pretending it does not exist leaks less than refusing.
                raise NotFoundError("access token", token_id)
            if row.revoked_at is not None:
                return row
            self._tokens.update_row(conn, token_id, {"revoked_at": ts})
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="access_token",
                        entity_id=token_id,
                        action="revoke",
                        field_key="revoked_at",
                        old_value=None,
                        new_value=ts,
                        note=f"revoked token {row.name!r}",
                    )
                ],
            )
            refreshed = self._tokens.get(conn, token_id)
            assert refreshed is not None
        return refreshed

    # -------------------------------------------------------------- resolution

    def resolve_secret(self, plaintext: str, now: datetime | None = None) -> AccessTokenRow | None:
        """The row behind a presented secret, or None when there is no such token.

        Revocation, expiry, and principal state are *not* checked here: the resolver
        needs to tell those apart to produce a message naming the actual problem, and
        collapsing them into None would make every failure read as "unknown token".
        """
        del now
        with self._db.read() as conn:
            return self._tokens.get_by_hash(conn, hash_token(plaintext))

    def note_use(
        self, token_id: str, last_used_at: str | None, now: datetime | None = None
    ) -> None:
        """Advance ``last_used_at``, coarsely (FR-I4).

        Skipped entirely when the stored value is already within
        :data:`LAST_USED_COARSENESS`, so a burst of requests on one token produces one
        write rather than one per request. The field answers "is this token still in
        use", which a five-minute resolution answers just as well as a per-request one.
        """
        moment = now or utc_now()
        if last_used_at is not None:
            try:
                if moment - parse_datetime(last_used_at) < LAST_USED_COARSENESS:
                    return
            except ValueError:
                pass
        with self._db.write() as conn:
            self._tokens.touch_last_used(conn, token_id, format_datetime(moment))

    # ----------------------------------------------------------------- helpers

    def _check_ceilings(self, actor: ActorContext, owner: PrincipalRow, requested: Scope) -> None:
        # Derived from `role_scope`, not from a role string. Reading
        # `owner.role != "admin"` directly would mean a `creator` -- which needs `admin`
        # credential scope to reach the schema routes at all -- could not be minted the
        # token it needs.
        ceiling = role_scope(owner.role)
        if not scope_allows(ceiling, requested):
            raise ValidationFailedError(
                f"A {owner.role!r} principal cannot hold a {requested!r} token; that role "
                f"carries at most the {ceiling!r} scope (FR-I4).",
                scope=requested,
                role=owner.role,
            )
        if not scope_allows(actor.scope, requested):
            raise InsufficientScopeError.for_operation(
                f"Minting a {requested!r} access token", requested, actor.scope
            )

    def _require_admin_delegation(
        self, conn: Connection, actor: ActorContext, operation: str
    ) -> None:
        """May this caller act on **another principal's** credentials at all?

        One definition, two callers: :meth:`mint` and :meth:`list_tokens`.
        ``operation`` is the present participle of what was attempted -- ``"Minting"``
        or ``"Listing"`` -- and is interpolated into both messages, because a refusal
        that tells a caller it may not mint when it tried to list sends it to ask an
        administrator for the wrong thing.
        """
        if not scope_allows(actor.scope, "admin"):
            raise InsufficientScopeError.for_operation(
                f"{operation} an access token for another principal", "admin", actor.scope
            )
        caller = self._principals.get(conn, actor.principal_id)
        if caller is None or caller.role != "admin":
            raise ValidationFailedError(
                f"{operation} a token for another principal requires the 'admin' role.",
                principal_id=actor.principal_id,
            )

    def _require_principal(self, conn: Connection, principal_id: str) -> PrincipalRow:
        row = self._principals.get(conn, principal_id)
        if row is None:
            raise NotFoundError("principal", principal_id)
        return row

    @staticmethod
    def _require_scope(scope: str) -> Scope:
        if scope not in VALID_SCOPES:
            raise ValidationFailedError(
                f"Unknown scope {scope!r}. Valid scopes: {', '.join(VALID_SCOPES)}.",
                scope=scope,
            )
        return scope

    @staticmethod
    def _normalize_expiry(expires_at: str | None, now: datetime | None) -> str | None:
        if expires_at is None:
            return None
        try:
            parsed = parse_datetime(expires_at)
        except ValueError as exc:
            raise ValidationFailedError(
                f"expires_at must be an ISO-8601 UTC timestamp like "
                f"'2027-01-31T00:00:00Z'; got {expires_at!r}.",
                expires_at=expires_at,
            ) from exc
        if parsed <= (now or utc_now()):
            raise ValidationFailedError(
                "expires_at is already in the past; the token would never resolve.",
                expires_at=expires_at,
            )
        return format_datetime(parsed)
