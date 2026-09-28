"""Per-object-type access control: the third authorization axis (DD-11).

Three values already existed and answer different questions. A **credential scope**
(``access_tokens.scope``, or ``role_scope(principal.role)`` for a session) says how much
this *credential* may do anywhere. A **system role** (``principals.role``) says whether
this principal is a system administrator and whether it may create object types. What
was missing is which object types a principal may touch, and how, which is what
``object_type_grants.level`` and ``object_types.default_level`` now say.

The composition, and the whole of it::

    granted(principal, T) =
        'admin'                       if principal.role == 'admin'
        grant_level(principal, T)     if a grant row exists   -- may be 'none'
        T.default_level               otherwise

    effective(credential, T) = min(credential.scope, granted(principal, T))

``min`` is by ``auth.LEVEL_ORDER``. **The credential is a ceiling and can only ever
narrow.** Because a credential's scope is one of ``read | write | admin``, it can never
lift ``none`` to anything, and a ``read`` PAT held by the administrator of a type still
only reads it. That is what keeps "a ``read`` PAT is rejected on every write path"
true, and it is why the ceiling shape was chosen
over letting grants override scope.

This is the only place that answers an authorization question about an object type, and
:class:`~glosswork.repositories.sqlite.SqliteGrantRepository` is the only place that
reads the table (DD-2: the raw SQL is in the repository, the decision is in the
service). Two rules follow, both grep-backed by meta-tests:

- **No route handler and no MCP tool calls this** (DD-3). Enforcement
  lives in the services, in the same spirit as the existing test that asserts nothing
  under ``routes/`` reads ``ActorContext.scope``.
- **It never filters internal lookups.** ``SchemaService.get_object_type_by_id`` is used
  by expansion and redaction to *find out* what a caller may not see; filtering it would
  make relation redaction impossible. Enforcement is applied at the
  service entry points the adapters call, and nowhere below them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection

from glosswork.actor import ActorContext, Level, Scope
from glosswork.auth import LEVEL_ORDER, level_allows, min_level
from glosswork.db import Database
from glosswork.errors import (
    ForbiddenError,
    InsufficientScopeError,
    NotFoundError,
    ValidationFailedError,
)
from glosswork.repositories.interfaces import (
    AuditRepository,
    GrantRepository,
    PrincipalRepository,
    SchemaRepository,
)
from glosswork.repositories.models import AttachmentRow, GrantRow, ObjectType
from glosswork.serializers import principal_sidecar_doc
from glosswork.services.base import make_event
from glosswork.timeutil import format_datetime, utc_now

VALID_LEVELS: tuple[Level, ...] = ("none", "read", "write", "admin")

# The roles that may create object types, and therefore the roles a type's `admin`
# grant is exercisable by. Ordered `member < creator < admin`.
CREATOR_ROLES: frozenset[str] = frozenset({"admin", "creator"})


@dataclass(frozen=True)
class GrantListing:
    """Everything one grants screen needs, read in one transaction (DD-11).

    ``principals`` is the same ``principals`` sidecar every document carrying a record
    has: three keys per entry, keyed by principal id, built here by
    :func:`~glosswork.serializers.principal_sidecar_doc` rather than by the envelope,
    exactly as ``RecordService.principal_sidecar`` builds its own (DD-3). One
    place assembles a sidecar and one projection is auditable.

    It is required rather than decorative. A grant row may name a **deactivated**
    principal, or one past the directory route's 200-row cap, and the browser's picker
    reads the directory; without this map such a row would render as a raw UUID. The
    ids here are the rows' own, so the map is exact by construction -- nothing to cap
    and nothing to miss.
    """

    object_type_key: str
    default_level: Level
    grants: list[GrantRow]
    principals: dict[str, dict[str, Any]]


class AccessService:
    def __init__(
        self,
        db: Database,
        grant_repo: GrantRepository,
        schema_repo: SchemaRepository,
        principal_repo: PrincipalRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._db = db
        self._grants = grant_repo
        self._schema = schema_repo
        self._principals = principal_repo
        self._audit = audit_repo

    # ------------------------------------------------------------- the decision

    def granted_level(
        self, conn: Connection, actor: ActorContext, object_type: ObjectType
    ) -> Level:
        """``granted(principal, T)``: the principal's own level, before the ceiling."""
        if self._is_system_admin(conn, actor):
            return "admin"
        row = self._grants.get(conn, object_type.id, actor.principal_id)
        if row is not None:
            return _as_level(row.level)
        return _as_level(object_type.default_level)

    def effective_level(
        self, conn: Connection, actor: ActorContext, object_type: ObjectType
    ) -> Level:
        """``effective(credential, T)``: the grant narrowed by the credential's scope.

        This is the value ``your_access`` carries on the orientation paths -- never the
        raw grant, so an agent holding a ``read`` PAT on a type it
        administers is told ``read``, which is the truth about what it can do now.
        """
        return min_level(actor.scope, self.granted_level(conn, actor, object_type))

    def require_level(
        self, conn: Connection, actor: ActorContext, object_type: ObjectType, required: Scope
    ) -> None:
        """Refuse unless ``effective >= required``, **with the code that is true**.

        ``effective`` is a ``min`` of two things, and which of the two fell short decides
        the error:

        - the *credential's scope* was too low -> ``insufficient_scope``. Its remedy is
          presenting a stronger credential, and it is the same code the route's or tool's
          own ``require_scope`` declaration would have produced had it been declared at
          this level. This is what makes Accept clause 3 true -- a principal granted
          ``admin`` on a type, holding a ``read`` PAT, is told its *token* is too narrow,
          which is the truth.
        - the credential was sufficient and the *principal's grant* was not ->
          ``forbidden``. Its remedy is somebody granting this principal access.

        Reporting ``forbidden`` for the first case would send an agent to ask for a grant
        it already has.
        """
        if not level_allows(actor.scope, required):
            raise InsufficientScopeError.for_operation(
                f"{required.capitalize()} access to object type {object_type.key!r}",
                required,
                actor.scope,
            )
        effective = self.effective_level(conn, actor, object_type)
        if not level_allows(effective, required):
            raise ForbiddenError(object_type.key, effective, required)

    def accessible_type_ids(
        self, conn: Connection, actor: ActorContext, required: Scope
    ) -> set[str]:
        """Every live object type id this actor holds at least ``required`` on.

        Two reads plus a role lookup, not one per type: ``ActorContext`` carries no
        role, so evaluating ``granted()`` needs the ``principals`` row as well as the
        grant rows. Given those, the principal's grants come back on
        ``ix_object_type_grants_principal`` and the live types' ``default_level`` comes
        back with the type list, and the whole map resolves in memory. Every cross-type
        read path calls this once per request.
        """
        types = self._schema.list_object_types(conn)
        if self._is_system_admin(conn, actor):
            ceiling = min_level(actor.scope, "admin")
            return {t.id for t in types} if level_allows(ceiling, required) else set()
        by_type = {
            row.object_type_id: _as_level(row.level)
            for row in self._grants.list_for_principal(conn, actor.principal_id)
        }
        allowed: set[str] = set()
        for object_type in types:
            granted = by_type.get(object_type.id, _as_level(object_type.default_level))
            if level_allows(min_level(actor.scope, granted), required):
                allowed.add(object_type.id)
        return allowed

    def effective_levels(
        self, conn: Connection, actor: ActorContext, types: list[ObjectType]
    ) -> dict[str, Level]:
        """``effective_level`` for many types under the same two reads. Used by
        ``list_object_types``, which would otherwise probe the grant table per type."""
        if self._is_system_admin(conn, actor):
            ceiling = min_level(actor.scope, "admin")
            return {t.id: ceiling for t in types}
        by_type = {
            row.object_type_id: _as_level(row.level)
            for row in self._grants.list_for_principal(conn, actor.principal_id)
        }
        return {
            t.id: min_level(actor.scope, by_type.get(t.id, _as_level(t.default_level)))
            for t in types
        }

    def require_every_type(
        self, conn: Connection, actor: ActorContext, required: Scope, label: str
    ) -> None:
        """Refuse unless the actor holds ``required`` on **every** live object type.

        The fail-closed answer for a subject that names no single type -- today only a
        schema proposal whose ``target_type_id`` is null, which has no producer but is
        nullable in the schema and so must resolve to something rather than to a crash.
        """
        types = self._schema.list_object_types(conn)
        if self.accessible_type_ids(conn, actor, required) != {t.id for t in types}:
            raise ForbiddenError(label, "none", required)

    def is_unrestricted(self, conn: Connection, actor: ActorContext) -> bool:
        """True when this actor sees every type and every untyped audit row.

        Only a ``role == 'admin'`` principal is, and only through a credential that has
        not narrowed it below ``read``. The two audit feeds use this to decide whether to
        apply the SQL restriction at all, and it is the *only* thing that admits rows
        whose ``object_type_id`` is null.
        """
        return self._is_system_admin(conn, actor) and level_allows(actor.scope, "read")

    def require_creator_role(self, conn: Connection, actor: ActorContext) -> None:
        """Creating an object type needs ``role >= creator``.

        This is the one gate on the *role* axis inside the service layer; the twelve
        system routes are gated by ``require_role`` at declaration time.
        """
        principal = self._principals.get(conn, actor.principal_id)
        if principal is None or principal.role not in CREATOR_ROLES:
            role = "unknown" if principal is None else principal.role
            raise ValidationFailedError(
                f"Creating an object type requires the 'creator' or 'admin' role; this "
                f"principal is {role!r}. Ask a system administrator to run "
                "'python -m glosswork.admin set-role --role creator'.",
                role=role,
            )

    # ------------------------------------------------------------ grant management
    #
    # These own their transaction, unlike the four decision helpers above, which take a
    # connection because they run inside another service's read or write. Same split as
    # ``SchemaService._add_field_in_txn`` beside ``add_field``.

    def grant(
        self,
        actor: ActorContext,
        object_type_key: str,
        principal_id: str,
        level: str,
        now: datetime | None = None,
    ) -> GrantRow:
        checked = require_level_value(level)
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            object_type = self._require_type(conn, object_type_key)
            self.require_level(conn, actor, object_type, "admin")
            if self._principals.get(conn, principal_id) is None:
                raise NotFoundError("principal", principal_id)
            existing = self._grants.get(conn, object_type.id, principal_id)
            row = self._grants.upsert(
                conn,
                GrantRow(
                    id=existing.id if existing is not None else str(uuid.uuid4()),
                    object_type_id=object_type.id,
                    principal_id=principal_id,
                    level=checked,
                    created_at=existing.created_at if existing is not None else ts,
                    created_by=existing.created_by if existing is not None else actor.principal_id,
                    updated_at=ts,
                    updated_by=actor.principal_id,
                ),
            )
            self._audit.append(
                conn,
                [
                    self._event(
                        actor,
                        ts,
                        row,
                        "update" if existing is not None else "create",
                        old_value=existing.level if existing is not None else None,
                        new_value=checked,
                    )
                ],
            )
            return row

    def revoke(
        self,
        actor: ActorContext,
        object_type_key: str,
        principal_id: str,
        now: datetime | None = None,
    ) -> None:
        """Remove the grant row, returning the principal to the type's default.

        Deliberately different from granting ``'none'``: revoking restores whatever
        ``default_level`` says, granting ``'none'`` is an explicit deny that survives a
        later widening of the default.
        """
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            object_type = self._require_type(conn, object_type_key)
            self.require_level(conn, actor, object_type, "admin")
            existing = self._grants.get(conn, object_type.id, principal_id)
            if existing is None:
                raise NotFoundError("object type grant", f"{object_type_key}/{principal_id}")
            self._grants.delete(conn, object_type.id, principal_id)
            self._audit.append(
                conn,
                [
                    self._event(
                        actor, ts, existing, "delete", old_value=existing.level, new_value=None
                    )
                ],
            )

    def list_grants_document(self, actor: ActorContext, object_type_key: str) -> GrantListing:
        """The grants screen's whole read, in one transaction.

        The ``admin``-level check is the same one ``grant`` and ``revoke`` apply, and it
        is a check on the *level*, never on the system role: a principal that holds
        ``admin`` on this one type administers this one type's grants, on both adapters.

        Exactly one ``principals_by_ids`` call, for every id the rows mention --
        ``principal_id``, ``created_by`` and ``updated_by`` across all of them -- not one
        per row. ``tests/test_delegated_permissions.py`` counts the calls, because "N+1 but
        correct" passes every functional assertion. No grants means no read at all.
        """
        with self._db.read() as conn:
            object_type = self._require_type(conn, object_type_key)
            self.require_level(conn, actor, object_type, "admin")
            grants = self._grants.list_for_type(conn, object_type.id)
            ids: list[str] = []
            for row in grants:
                for value in (row.principal_id, row.created_by, row.updated_by):
                    if isinstance(value, str) and value:
                        ids.append(value)
            principals: dict[str, dict[str, Any]] = {}
            if ids:
                rows = self._principals.principals_by_ids(conn, list(dict.fromkeys(ids)))
                principals = principal_sidecar_doc(rows)
            return GrantListing(
                object_type_key=object_type.key,
                default_level=_as_level(object_type.default_level),
                grants=grants,
                principals=principals,
            )

    def list_grants(self, actor: ActorContext, object_type_key: str) -> list[GrantRow]:
        """The rows alone, for the operator CLI (``admin.py``'s ``list-grants``), which
        renders ids and needs no names. One implementation of the check and of the
        read."""
        return self.list_grants_document(actor, object_type_key).grants

    def grant_owner_in_txn(
        self,
        conn: Connection,
        actor: ActorContext,
        object_type_id: str,
        ts: str,
    ) -> GrantRow:
        """The creator's own ``admin`` grant, inserted in ``create_object_type``'s own
        transaction.

        Inserted for a ``role == 'admin'`` creator too, where the row is strictly
        redundant. The redundancy is deliberate: it makes "who owns this type" a legible
        query against one table rather than a rule the reader has to know.
        """
        return self._grants.upsert(
            conn,
            GrantRow(
                id=str(uuid.uuid4()),
                object_type_id=object_type_id,
                principal_id=actor.principal_id,
                level="admin",
                created_at=ts,
                created_by=actor.principal_id,
                updated_at=ts,
                updated_by=actor.principal_id,
            ),
        )

    # ------------------------------------------------- attachments

    @staticmethod
    def attachment_readable(
        actor: ActorContext,
        row: AttachmentRow,
        referencing_type_ids: set[str],
        readable_type_ids: set[str],
    ) -> bool:
        """**The attachment read rule, and the only implementation of it** (DD-12).

        An attachment is readable when the caller holds ``read`` on the object type of
        **any** referencing record, **or** is the attachment's ``uploaded_by``.

        The uploader clause is required, not a convenience. Between
        ``POST /api/v1/attachments`` and the record write that attaches the id there is
        no referencing row at all, so without it an uploader could not complete its own
        upload -- it would be unable to read back the row it had just created.

        An attachment has no object type of its own: blobs are shared by content hash,
        so "the owning record" is legitimately zero, one, or many records across several
        object types, and ``record_attachments`` is the materialized reverse index that
        gives the level check something to check against.

        The rule lives here rather than in ``AttachmentService`` because it has a second
        enforcement site -- the record write funnel -- and a rule with two homes is a
        rule that drifts. ``readable_type_ids`` is passed in rather than read here so
        the batch callers keep their single :meth:`accessible_type_ids` read.
        """
        if row.uploaded_by == actor.principal_id:
            return True
        return bool(referencing_type_ids & readable_type_ids)

    def require_attachment_readable(
        self,
        conn: Connection,
        actor: ActorContext,
        row: AttachmentRow,
        referencing_type_ids: set[str],
        readable_type_ids: set[str] | None = None,
    ) -> None:
        """:meth:`attachment_readable`, refused with ``forbidden`` when it is false.

        One refusal for both the read path and the write path, because it is the same
        refusal: a caller that may not read an attachment may not reference one either,
        and a write that could manufacture the reference would otherwise manufacture the
        right along with it.

        There is no single object type to name, so the message names the attachment.
        """
        if readable_type_ids is None:
            readable_type_ids = self.accessible_type_ids(conn, actor, "read")
        if not self.attachment_readable(actor, row, referencing_type_ids, readable_type_ids):
            raise ForbiddenError(f"(attachment {row.id})", "none", "read")

    # ---------------------------------------------------------------- helpers

    def _is_system_admin(self, conn: Connection, actor: ActorContext) -> bool:
        principal = self._principals.get(conn, actor.principal_id)
        return principal is not None and principal.role == "admin"

    def _require_type(self, conn: Connection, key: str) -> ObjectType:
        object_type = self._schema.get_object_type_by_key(conn, key)
        if object_type is None:
            valid = [t.key for t in self._schema.list_object_types(conn)]
            from glosswork.errors import UnknownObjectTypeError

            raise UnknownObjectTypeError(key, valid)
        return object_type

    @staticmethod
    def _event(
        actor: ActorContext,
        ts: str,
        row: GrantRow,
        action: str,
        *,
        old_value: Any,
        new_value: Any,
    ) -> Any:
        """One ``entity_type='object_type_grant'`` audit row. Carries
        ``object_type_id`` so the grant's own history is visible to exactly the people
        who can read the type it is about (the audit read rule)."""
        return make_event(
            actor,
            ts,
            entity_type="object_type_grant",
            entity_id=row.id,
            action=action,
            object_type_id=row.object_type_id,
            field_key="level",
            old_value=old_value,
            new_value=new_value,
            note=f"principal={row.principal_id}",
        )


def _as_level(value: str) -> Level:
    if value not in LEVEL_ORDER:
        raise ValueError(f"unknown access level {value!r}")
    return value


def require_level_value(value: str) -> Level:
    """The level vocabulary, checked with the vocabulary in the message.

    Public because ``SchemaService.update_object_type`` validates
    ``object_types.default_level`` through it: one declaration of what a level may be,
    checked the same way wherever a caller supplies one.
    """
    if value not in VALID_LEVELS:
        raise ValidationFailedError(
            f"Unknown access level {value!r}. Valid levels: {', '.join(VALID_LEVELS)}. "
            "'none' is an explicit deny that overrides the type's default_level; "
            "removing the grant row entirely is 'revoke'.",
            level=value,
        )
    return value
