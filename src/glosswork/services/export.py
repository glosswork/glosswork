"""The full-deployment JSON export (FR-E5).

**This is not the backup format.** ``BackupService`` (``services/backup.py``, FR-P8, DD-36)
produces a tar of a ``VACUUM INTO`` database snapshot plus the ``attachments/`` blob tree; its
consumer is *this deployment*, restored in place by an operator procedure. ``ExportService``
produces one JSON document instead: its consumer is a *future backing store* -- a data
warehouse, another Glosswork deployment being seeded, an analyst's notebook -- something
that wants schema, records, comments, relations, saved views, agent labels, and audit history
as portable documents rather than as a SQLite file it cannot open. DD-36 is
explicit that FR-E5 and FR-P8 share the streaming response envelope and the admin scope, and
deliberately do not share a format, because they do not share a consumer.

**Streaming, not materializing.** A 200,000-record deployment's records alone would not fit
comfortably in memory as one Python object graph, and building one and calling ``json.dumps``
once on it would hold the whole document (records, comments, audit history, everything) in
memory *twice* -- once as the graph, once as the rendered string -- before the first byte could
be written to the response. :meth:`ExportService.stream` is a generator instead: it writes the
document's punctuation itself (the opening brace, each top-level key, commas and brackets
between array elements) and calls ``json.dumps`` once per *element*, so memory stays bounded by
the largest single record, comment, or audit event rather than by the deployment's size. The
audit table -- the largest one, and the one most likely to dwarf everything else at scale -- is
additionally paged through :meth:`~glosswork.repositories.interfaces.AuditRepository.since`
in batches, so it is never loaded as one list either.

**One consistent read.** The whole traversal runs inside a single
:meth:`~glosswork.db.Database.read` connection, opened once and held for the lifetime of the
generator (it closes when the generator is exhausted or the caller stops pulling from it). This
is what makes the export a single point-in-time snapshot of the deployment rather than a
sequence of unrelated reads that could each observe a different, concurrently-written state --
the same correctness property ``BackupService`` gets from ``VACUUM INTO``, achieved here by
transaction isolation instead of a file copy, because the artifact is JSON rather than a second
SQLite file.

No audit event is emitted for the export itself: it is a read, and DD-4 requires attribution on
writes, not on reads. One structlog line is emitted on completion instead, exactly as
``backup.py`` logs ``backup_streamed``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator
from typing import Any

from sqlalchemy import Connection

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.logging import get_logger
from glosswork.repositories.interfaces import (
    AgentLabelRepository,
    AuditRepository,
    CommentRepository,
    RecordRepository,
    SavedViewRepository,
    SchemaRepository,
)
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.serializers import (
    agent_label_doc,
    audit_event_doc,
    comment_doc,
    field_doc,
    record_doc,
    saved_view_doc,
)
from glosswork.timeutil import format_datetime, utc_now

logger = get_logger(__name__)

FORMAT_NAME = "glosswork-export"
FORMAT_VERSION = 1

# How many audit rows are pulled into memory at once (module docstring: the audit table is the
# one most likely to dwarf the rest of the deployment). Matches the order of magnitude
# ``AuditRepository.since`` callers already page at elsewhere (``ChangeFeedService``, the
# isolated container proof's re-index drain), not a new constant.
_AUDIT_PAGE_SIZE = 1000


def _object_type_doc(object_type: ObjectType, fields: list[FieldDef]) -> dict[str, Any]:
    """One ``object_types`` row plus its fields, for the export's ``object_types`` array.

    ``envelopes.object_type_summary`` doesn't fit: it takes a ``ServiceBundle`` and an
    ``ActorContext`` to compute a live ``record_count`` via ``RecordService.query_records``,
    which would mean a second read (and a second connection) per type nested inside this
    export's single consistent read, and it omits ``name_plural``, ``icon``, and ``is_deleted``,
    which a full-deployment export needs to describe every type -- including a soft-deleted one
    -- without a second lookup. This is the row's own columns instead, the same shape
    ``record_doc`` and the other moved serializers use for their rows.
    """
    return {
        "key": object_type.key,
        "name": object_type.name,
        "name_plural": object_type.name_plural,
        "description": object_type.description,
        "key_prefix": object_type.key_prefix,
        "icon": object_type.icon,
        "is_deleted": object_type.is_deleted,
        "created_at": object_type.created_at,
        "created_by": object_type.created_by,
        "updated_at": object_type.updated_at,
        "updated_by": object_type.updated_by,
        "fields": [field_doc(f) for f in fields],
    }


def _kv(key: str, value: Any) -> bytes:
    """One ``"key":<json>`` pair for a scalar top-level field, encoded once."""
    return f'"{key}":'.encode() + json.dumps(value).encode("utf-8")


def _array_field(key: str, docs: Iterable[dict[str, Any]]) -> Iterator[bytes]:
    """One ``"key":[...]`` top-level array, streamed element by element.

    ``json.dumps`` runs once per element rather than once over the whole array, which is the
    module docstring's memory-bound property: the largest thing ever held in memory here is one
    element's serialized bytes, not the array.
    """
    yield f'"{key}":['.encode()
    first = True
    for doc in docs:
        if not first:
            yield b","
        first = False
        yield json.dumps(doc).encode("utf-8")
    yield b"]"


class ExportService:
    def __init__(
        self,
        db: Database,
        schema_repo: SchemaRepository,
        record_repo: RecordRepository,
        comment_repo: CommentRepository,
        saved_view_repo: SavedViewRepository,
        label_repo: AgentLabelRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._db = db
        self._schema = schema_repo
        self._records = record_repo
        self._comments = comment_repo
        self._saved_views = saved_view_repo
        self._labels = label_repo
        self._audit = audit_repo

    def stream(self, actor: ActorContext) -> Iterator[bytes]:
        """Yield the full-deployment export as UTF-8 encoded JSON, one document.

        See the module docstring for why this streams, why it is one consistent read, and why
        it is not ``BackupService``'s format.
        """
        counts: dict[str, int] = {
            "object_types": 0,
            "records": 0,
            "links": 0,
            "comments": 0,
            "saved_views": 0,
            "agent_labels": 0,
            "audit": 0,
        }
        exported_at = format_datetime(utc_now())
        started = time.perf_counter()

        with self._db.read() as conn:
            object_types = self._schema.list_object_types(conn)
            fields_by_type_id = {
                ot.id: self._schema.list_fields(conn, ot.id) for ot in object_types
            }
            field_key_by_id = {
                field.id: field.key for fields in fields_by_type_id.values() for field in fields
            }
            counts["object_types"] = len(object_types)

            # Populated as the records array streams out (below), so the links and comments
            # sections that follow it can resolve a uuid to a portable record key without a
            # second pass over ``records_for_type``. Order matters: this dict must be fully
            # populated before the links/comments sections start reading it, which holds because
            # each is a separate ``yield from`` below and a generator's body does not resume past
            # a ``yield from`` until the sub-generator it drives is exhausted.
            record_key_by_id: dict[str, str] = {}

            yield b"{"
            yield _kv("format", FORMAT_NAME)
            yield b","
            yield _kv("format_version", FORMAT_VERSION)
            yield b","
            yield _kv("exported_at", exported_at)
            yield b","
            yield from _array_field(
                "object_types",
                (_object_type_doc(ot, fields_by_type_id[ot.id]) for ot in object_types),
            )
            yield b","
            yield from _array_field(
                "records",
                self._record_docs(conn, object_types, record_key_by_id, counts),
            )
            yield b","
            yield from _array_field(
                "links",
                self._link_docs(conn, record_key_by_id, field_key_by_id, counts),
            )
            yield b","
            yield from _array_field(
                "comments",
                self._comment_docs(conn, record_key_by_id, counts),
            )
            yield b","
            yield from _array_field(
                "saved_views",
                self._saved_view_docs(conn, object_types, counts),
            )
            yield b","
            yield from _array_field("agent_labels", self._agent_label_docs(conn, counts))
            yield b","
            yield from _array_field("audit", self._audit_docs(conn, counts))
            yield b"}"

        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            "export_streamed",
            elapsed_ms=elapsed_ms,
            principal_id=actor.principal_id,
            **counts,
        )

    # ------------------------------------------------------------------ sections

    def _record_docs(
        self,
        conn: Connection,
        object_types: list[ObjectType],
        record_key_by_id: dict[str, str],
        counts: dict[str, int],
    ) -> Iterator[dict[str, Any]]:
        """Every record of every type, including soft-deleted (this is a whole-deployment
        export: a soft-deleted record must survive it), each carrying its object type's key so
        the export is readable without joining back to ``object_types``."""
        for object_type in object_types:
            records = self._records.records_for_type(conn, object_type.id, include_deleted=True)
            for record in records:
                record_key_by_id[record.id] = record.key
                counts["records"] += 1
                doc = record_doc(record)
                doc["object_type_key"] = object_type.key
                yield doc

    def _link_docs(
        self,
        conn: Connection,
        record_key_by_id: dict[str, str],
        field_key_by_id: dict[str, str],
        counts: dict[str, int],
    ) -> Iterator[dict[str, Any]]:
        """Every row of the ``links`` table, once each: ``links_from`` per record returns the
        rows whose ``from_record_id`` is that record, and every row has exactly one
        ``from_record_id``, so iterating every record's outgoing links visits each row exactly
        once. A cardinality-many-both-ways relation stores a reciprocal row on the inverse
        field, which surfaces here as a second, distinct row (its own ``field_key``) rather than
        being collapsed with its pair -- it is a different row of the same table."""
        for from_id, from_key in record_key_by_id.items():
            for link in self._records.links_from(conn, from_id):
                counts["links"] += 1
                yield {
                    "id": link.id,
                    "field_key": field_key_by_id[link.field_id],
                    "from_record_key": from_key,
                    "to_record_key": record_key_by_id[link.to_record_id],
                    "position": link.position,
                    "created_at": link.created_at,
                    "created_by": link.created_by,
                }

    def _comment_docs(
        self,
        conn: Connection,
        record_key_by_id: dict[str, str],
        counts: dict[str, int],
    ) -> Iterator[dict[str, Any]]:
        """Every comment of every record, including soft-deleted, each carrying its
        ``record_key``."""
        for record_id, record_key in record_key_by_id.items():
            for comment in self._comments.list_for_record(conn, record_id, include_deleted=True):
                counts["comments"] += 1
                doc = comment_doc(comment)
                doc["record_key"] = record_key
                yield doc

    def _saved_view_docs(
        self,
        conn: Connection,
        object_types: list[ObjectType],
        counts: dict[str, int],
    ) -> Iterator[dict[str, Any]]:
        for object_type in object_types:
            for view in self._saved_views.list_for_type(conn, object_type.id):
                counts["saved_views"] += 1
                yield saved_view_doc(view)

    def _agent_label_docs(
        self, conn: Connection, counts: dict[str, int]
    ) -> Iterator[dict[str, Any]]:
        for label in self._labels.list_labels(conn, None):
            counts["agent_labels"] += 1
            yield agent_label_doc(label)

    def _audit_docs(self, conn: Connection, counts: dict[str, int]) -> Iterator[dict[str, Any]]:
        """Every audit event, paged through ``since`` rather than loaded as one list (module
        docstring): the audit table is the largest one and the one most likely to dwarf the
        rest of a real deployment."""
        cursor = 0
        while True:
            batch = self._audit.since(conn, cursor, None, _AUDIT_PAGE_SIZE)
            if not batch:
                return
            for event in batch:
                counts["audit"] += 1
                yield audit_event_doc(event)
            next_cursor = batch[-1].id
            assert next_cursor is not None
            cursor = next_cursor
