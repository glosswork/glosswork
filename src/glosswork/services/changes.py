"""Pull-based change feed (FR-M7): ``list_changes_since(cursor)``.

The audit table's autoincrement id doubles as the change-feed cursor
(docs/DATA_MODEL.md section 9), so the feed and the audit trail agree by
construction and no separate outbox table is needed. Omitting a cursor returns
the current cursor with no events, so a caller can start watching from now
without replaying history.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Connection

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.errors import UnknownObjectTypeError, ValidationFailedError
from glosswork.repositories.interfaces import AuditRepository, SchemaRepository
from glosswork.repositories.models import AuditEvent
from glosswork.services.access import AccessService

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


@dataclass(slots=True)
class ChangesResult:
    events: list[AuditEvent]
    next_cursor: int


class ChangeFeedService:
    def __init__(
        self,
        db: Database,
        schema_repo: SchemaRepository,
        audit_repo: AuditRepository,
        access: AccessService,
    ) -> None:
        self._db = db
        self._schema = schema_repo
        self._audit = audit_repo
        # The feed is the sharpest edge: unrestricted, the lowest credential the system
        # can mint would return the full mutation history -- old and new field values --
        # of every record of every type.
        self._access = access

    def list_changes_since(
        self,
        actor: ActorContext,
        cursor: int | None = None,
        object_type_keys: list[str] | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> ChangesResult:
        if limit < 1 or limit > MAX_LIMIT:
            raise ValidationFailedError(f"limit must be between 1 and {MAX_LIMIT}.")
        with self._db.read() as conn:
            if cursor is None:
                # No history requested: hand back the current cursor so the caller
                # can start watching from now (FR-M7).
                return ChangesResult(events=[], next_cursor=self._audit.latest_id(conn))
            if cursor < 0:
                raise ValidationFailedError("cursor must be a non-negative integer.")
            type_ids: list[str] | None = None
            if object_type_keys is not None:
                type_ids = []
                for key in object_type_keys:
                    object_type = self._schema.get_object_type_by_key(conn, key)
                    if object_type is None:
                        valid = [t.key for t in self._schema.list_object_types(conn)]
                        raise UnknownObjectTypeError(key, valid)
                    # Naming a type explicitly is `forbidden` when you cannot read it,
                    # for the same reason `linked_to` naming an unreadable key is: the
                    # caller named the thing, so silence would be a lie about the filter
                    # rather than a withholding of content.
                    self._access.require_level(conn, actor, object_type, "read")
                    type_ids.append(object_type.id)
            accessible, untyped = self._restriction(conn, actor)
            events = self._audit.since(
                conn,
                cursor,
                type_ids,
                limit,
                accessible_type_ids=accessible,
                include_untyped=untyped,
            )
            next_cursor = events[-1].id if events else cursor
            assert next_cursor is not None
            return ChangesResult(events=events, next_cursor=next_cursor)

    def _restriction(self, conn: Connection, actor: ActorContext) -> tuple[list[str] | None, bool]:
        """``(accessible type ids, may see untyped rows)`` for this actor.

        A ``role == 'admin'`` principal is unrestricted and is the only one that sees
        rows with a null ``object_type_id``. Both halves go into the repository's SQL
        rather than filtering a returned page, so cursor pagination stays correct across
        a boundary where rows are filtered out.
        """
        if self._access.is_unrestricted(conn, actor):
            return None, True
        return sorted(self._access.accessible_type_ids(conn, actor, "read")), False
