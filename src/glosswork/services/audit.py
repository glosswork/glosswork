"""Audit browsing (FR-U8, DD-21): a read-only service over
``AuditRepository.search``, resolving an ``object_type`` key or a ``record``
key/UUID to an id before delegating, the same way ``ChangeFeedService`` resolves
``object_type_keys`` today. Record-ref resolution uses ``RecordRepository`` rather
than raw SQL here, mirroring how ``RecordService`` itself resolves refs (DD-2: the
only new combined-filter SQL is inside ``AuditRepository.search``)."""

from __future__ import annotations

from dataclasses import dataclass

from glosswork.actor import ActorContext
from glosswork.cursors import cursor_int, decode_cursor, encode_cursor, validate_page_limit
from glosswork.db import Database
from glosswork.errors import NotFoundError, UnknownObjectTypeError
from glosswork.repositories.interfaces import AuditRepository, RecordRepository, SchemaRepository
from glosswork.repositories.models import AuditEvent
from glosswork.services.access import AccessService

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


@dataclass(slots=True)
class AuditSearchResult:
    events: list[AuditEvent]
    next_cursor: str | None


class AuditService:
    def __init__(
        self,
        db: Database,
        audit_repo: AuditRepository,
        schema_repo: SchemaRepository,
        record_repo: RecordRepository,
        access: AccessService,
    ) -> None:
        self._db = db
        self._audit = audit_repo
        self._schema = schema_repo
        self._records = record_repo
        # The same restriction the change feed carries, in SQL.
        self._access = access

    def search(
        self,
        actor: ActorContext,
        record: str | None = None,
        principal_id: str | None = None,
        agent_label_id: str | None = None,
        object_type: str | None = None,
        field_key: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> AuditSearchResult:
        validate_page_limit(limit, MAX_LIMIT, "MAX_LIMIT")
        before_id: int | None = None
        if cursor is not None:
            before_id = cursor_int(decode_cursor(cursor, {"id"})["id"])
        with self._db.read() as conn:
            record_id: str | None = None
            if record is not None:
                row = self._records.get_record(conn, record, include_deleted=True)
                if row is None:
                    raise NotFoundError("record", record)
                record_type = self._schema.get_object_type_by_id(conn, row.object_type_id)
                if record_type is not None:
                    self._access.require_level(conn, actor, record_type, "read")
                record_id = row.id
            object_type_id: str | None = None
            if object_type is not None:
                object_type_row = self._schema.get_object_type_by_key(conn, object_type)
                if object_type_row is None:
                    valid = [t.key for t in self._schema.list_object_types(conn)]
                    raise UnknownObjectTypeError(object_type, valid)
                self._access.require_level(conn, actor, object_type_row, "read")
                object_type_id = object_type_row.id
            accessible: list[str] | None = None
            include_untyped = True
            if not self._access.is_unrestricted(conn, actor):
                accessible = sorted(self._access.accessible_type_ids(conn, actor, "read"))
                include_untyped = False
            events = self._audit.search(
                conn,
                record_id=record_id,
                principal_id=principal_id,
                agent_label_id=agent_label_id,
                object_type_id=object_type_id,
                field_key=field_key,
                since=since,
                until=until,
                limit=limit + 1,
                before_id=before_id,
                accessible_type_ids=accessible,
                include_untyped=include_untyped,
            )
        next_cursor: str | None = None
        if len(events) > limit:
            events = events[:limit]
            assert events[-1].id is not None
            next_cursor = encode_cursor({"id": events[-1].id})
        return AuditSearchResult(events=events, next_cursor=next_cursor)
