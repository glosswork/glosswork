"""Record store: CRUD, version-checked writes, querying, and links
(FR-R1 through FR-R11, FR-L1 through FR-L5, docs/DATA_MODEL.md sections 5-6).
"""

from __future__ import annotations

import difflib
import uuid as uuidlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from glosswork.actor import ActorContext, Scope
from glosswork.auth import level_allows
from glosswork.compiler import compile_query, parse_sort
from glosswork.cursors import cursor_int, decode_cursor, encode_cursor, validate_page_limit
from glosswork.db import Database
from glosswork.errors import (
    NotFoundError,
    RelationBlockedError,
    UnknownFieldError,
    UnknownObjectTypeError,
    ValidationFailedError,
    VersionConflictError,
)
from glosswork.fieldtypes import PSEUDO_FIELDS, validate_value
from glosswork.filters import FilterContext, parse_filter
from glosswork.repositories.interfaces import (
    AgentLabelRepository,
    AttachmentRepository,
    AuditRepository,
    PrincipalRepository,
    RecordAttachmentRepository,
    RecordRepository,
    SchemaRepository,
)
from glosswork.repositories.models import (
    AuditEvent,
    FieldDef,
    LinkRow,
    ObjectType,
    RecordRow,
)
from glosswork.serializers import agent_label_sidecar_doc, principal_sidecar_doc
from glosswork.services.access import AccessService
from glosswork.services.base import display_field, make_event
from glosswork.services.principals import resolve_principal_ref
from glosswork.services.search_index import SearchIndexService
from glosswork.timeutil import format_datetime, utc_now


@dataclass(slots=True)
class QueryResult:
    records: list[dict[str, Any]]
    total_count: int
    next_cursor: str | None
    truncated: bool


@dataclass(slots=True)
class BulkUpdateResult:
    affected_count: int
    sample_keys: list[str]
    dry_run: bool


@dataclass(slots=True)
class HistoryPage:
    events: list[AuditEvent]
    next_cursor: str | None


# Practical upper bound on records touched by one bulk-update call/transaction
# (PRD scale assumptions: low hundreds of thousands of records total).
BULK_UPDATE_MAX_MATCHES = 200_000
BULK_UPDATE_SAMPLE_SIZE = 20
DEFAULT_QUERY_LIMIT = 50
DEFAULT_HISTORY_LIMIT = 100
# Page ceilings (DD-18) for three read paths -- query, history and comments. Checking only
# ``limit < 1`` would answer ``limit=100000000`` with the whole table plus a principals
# sidecar in one response. They are enforced in the *service*, exactly as
# ``services/search.py`` enforces ``MAX_SEARCH_LIMIT``, so REST and MCP inherit one rule
# and neither adapter re-declares it as a schema bound.
#
# 1,000 matches the changes feed's ceiling and sits comfortably above the UI's
# ``TABLE_VIEW_PAGE_SIZE`` of 200, which is the floor any query cap has to
# clear. It is also the page size CSV export walks the table in.
MAX_QUERY_LIMIT = 1000
MAX_HISTORY_LIMIT = 500
# describe_object_type(include_samples=true): example values per field, and how
# many recent records to scan for distinct ones (docs/MCP_TOOLS.md section 5.1).
SAMPLE_VALUES_PER_FIELD = 3
_SAMPLE_SCAN_ROWS = 25


def _resolver_details(exc: ValidationFailedError) -> dict[str, Any]:
    """The resolver's structured details, minus the ``field_key`` the re-raise sets
    itself. ``candidates`` is the one key it currently carries, and it is the whole
    point of refusing to pick under ambiguity: the caller is told which people share the
    name and which email addresses distinguish them."""
    return {k: v for k, v in exc.details.items() if k != "field_key"}


class RecordService:
    def __init__(
        self,
        db: Database,
        schema_repo: SchemaRepository,
        record_repo: RecordRepository,
        audit_repo: AuditRepository,
        access: AccessService,
        attachment_repo: AttachmentRepository | None = None,
        search_index: SearchIndexService | None = None,
        record_attachments: RecordAttachmentRepository | None = None,
        principal_repo: PrincipalRepository | None = None,
        label_repo: AgentLabelRepository | None = None,
    ) -> None:
        self._db = db
        self._schema = schema_repo
        self._records = record_repo
        self._audit = audit_repo
        # DD-11: required, so no entry point has a silent unenforced branch. The
        # level is applied at each entry point rather than inside ``_type_and_fields``,
        # because the required level differs per entry point and those helpers are also
        # reached from internal lookups that must stay unfiltered.
        self._access = access
        self._attachments = attachment_repo
        # The reverse index from an attachment to the records referencing it,
        # maintained on every write that can change ``records.data``. Optional only for
        # the same reason ``attachment_repo`` is; every production bundle passes one.
        self._record_attachments = record_attachments
        # Optional so a constructor call without one keeps working in tests that do not
        # need the search index; every production bundle passes one (FR-Q3).
        self._search_index = search_index
        # The ``user_ref`` resolver's repository, and the sidecar's one read. Optional
        # for the same reason the two above are -- every production bundle passes one --
        # but a ``user_ref`` write with no repository wired is a bug rather than a skipped
        # check, so ``_principals_repo`` raises rather than returning None where it is used.
        self._principals = principal_repo
        # The ``agent_labels`` sidecar's one read. Optional for the same reason
        # every repository above it is; a page written entirely by people never asks for it.
        self._labels = label_repo

    # ------------------------------------------------------------------ reads
    #
    # Every read below takes an ``ActorContext``, first and positional,
    # exactly as the writes do: the third authorization axis is per principal, and these
    # are the entry points the adapters call.

    def get_record(self, actor: ActorContext, ref: str, include_deleted: bool = False) -> RecordRow:
        with self._db.read() as conn:
            record = self._require_record(conn, ref, include_deleted)
            self._require_record_level(conn, actor, record, "read")
            return record

    def get_record_history(
        self, actor: ActorContext, ref: str, field_key: str | None = None
    ) -> list[AuditEvent]:
        with self._db.read() as conn:
            record = self._require_record(conn, ref, include_deleted=True)
            self._require_record_level(conn, actor, record, "read")
            return self._audit.for_record(conn, record.id, field_key)

    def get_record_history_page(
        self,
        actor: ActorContext,
        ref: str,
        field_key: str | None = None,
        limit: int = DEFAULT_HISTORY_LIMIT,
        cursor: str | None = None,
    ) -> HistoryPage:
        """Field-level audit for one record, keyset-paginated over the audit id
        (FR-D5; docs/MCP_TOOLS.md 5.1 ``get_record_history``)."""
        validate_page_limit(limit, MAX_HISTORY_LIMIT, "MAX_HISTORY_LIMIT")
        after_id: int | None = None
        if cursor is not None:
            after_id = cursor_int(decode_cursor(cursor, {"id"})["id"])
        with self._db.read() as conn:
            record = self._require_record(conn, ref, include_deleted=True)
            self._require_record_level(conn, actor, record, "read")
            events = self._audit.for_record(
                conn, record.id, field_key, limit=limit + 1, after_id=after_id
            )
        next_cursor: str | None = None
        if len(events) > limit:
            events = events[:limit]
            assert events[-1].id is not None
            next_cursor = encode_cursor({"id": events[-1].id})
        return HistoryPage(events=events, next_cursor=next_cursor)

    def sample_values(
        self,
        actor: ActorContext,
        object_type_key: str,
        per_field: int = SAMPLE_VALUES_PER_FIELD,
    ) -> dict[str, list[Any]]:
        """Up to ``per_field`` distinct example values per field, drawn from the most
        recently updated live records (``describe_object_type(include_samples=true)``,
        docs/MCP_TOOLS.md 5.1). Fields with no data map to an empty list; relation
        fields have no stored value and always map to an empty list."""
        with self._db.read() as conn:
            object_type, fields_by_key = self._type_and_fields(conn, object_type_key)
            self._access.require_level(conn, actor, object_type, "read")
            samples: dict[str, list[Any]] = {}
            for key, field in fields_by_key.items():
                if field.type == "relation":
                    samples[key] = []
                    continue
                distinct: list[Any] = []
                for value in self._records.recent_field_values(
                    conn, object_type.id, key, _SAMPLE_SCAN_ROWS
                ):
                    if value not in distinct:
                        distinct.append(value)
                    if len(distinct) >= per_field:
                        break
                samples[key] = distinct
            return samples

    def list_links(self, actor: ActorContext, ref: str, field_key: str) -> list[RecordRow]:
        """Linked records for one relation field, in link order.

        Returns rows, so it has no way to *express* a redaction: when the caller cannot
        read the field's target type this is empty rather than a run of placeholders.
        The redacted shape lives on the expansion path --
        :meth:`list_link_summaries` and :meth:`get_record_expansions` -- which is what
        ``get_record`` uses on both surfaces, and which is where two callers with
        different grants must agree on how many links a record has.

        A relation field targets exactly one object type, so the answer here is all or
        nothing rather than per target row.
        """
        with self._db.read() as conn:
            record = self._require_record(conn, ref)
            object_type, fields_by_key = self._type_and_fields_by_id(conn, record.object_type_id)
            self._access.require_level(conn, actor, object_type, "read")
            field = self._require_relation_field(object_type, fields_by_key, field_key)
            if self._may_read_target(conn, actor, field) is None:
                return []
            linked: list[RecordRow] = []
            for link in self._records.links_from(conn, record.id, field.id):
                target = self._records.get_record(conn, link.to_record_id, include_deleted=True)
                if target is not None:
                    linked.append(target)
            return linked

    def linked_keys_for_page(
        self,
        actor: ActorContext,
        object_type_key: str,
        record_ids: list[str],
        field_keys: list[str],
    ) -> dict[str, dict[str, list[str]]]:
        """Target keys for one page of records across several relation fields.

        The batched counterpart to :meth:`list_links`, and it collapses **both** halves
        of that method's fan-out. ``list_links`` is a double N+1: called once per record
        it opens its own connection and re-runs ``_require_record`` and
        ``require_level`` every time, and then fetches each link target with a separate
        ``get_record``. Here one connection, one level check and one
        ``_may_read_target`` per *field* serve the whole page, and the repository
        returns target keys already joined, so the cost is one read per (page, field).

        Hoisting ``_may_read_target`` out of the per-record loop is sound because a
        relation field targets exactly one object type, so the answer cannot vary by
        row. The behaviour it produces is a fence: a field
        whose target type the caller cannot read yields an **empty list** for every
        record, not a redaction marker, exactly as ``list_links`` does -- this path
        returns keys, so it has no way to express a redaction either.
        """
        if not record_ids or not field_keys:
            return {rid: {} for rid in record_ids}
        with self._db.read() as conn:
            object_type, fields_by_key = self._type_and_fields(conn, object_type_key)
            self._access.require_level(conn, actor, object_type, "read")
            by_record: dict[str, dict[str, list[str]]] = {rid: {} for rid in record_ids}
            for field_key in field_keys:
                field = self._require_relation_field(object_type, fields_by_key, field_key)
                if self._may_read_target(conn, actor, field) is None:
                    for rid in record_ids:
                        by_record[rid][field_key] = []
                    continue
                grouped = self._records.linked_keys_for_records(conn, record_ids, field.id)
                for rid in record_ids:
                    by_record[rid][field_key] = grouped.get(rid, [])
            return by_record

    def list_link_summaries(self, actor: ActorContext, ref: str) -> dict[str, list[dict[str, Any]]]:
        """Every relation field of one record, as ``{"key", "id", "display"}`` entries or,
        for a target type the caller cannot read, ``{"redacted": True}``.

        ``display`` is the **target type's** display field value, the same
        value ``_expand_record`` already produces on the other relation path. It is
        resolved once per relation field, not once per link, so the read path's cost is
        unchanged in shape.

        **The link is preserved and the target is redacted, not hidden.** Two callers
        with different grants therefore agree on how many links a record has and
        disagree only on what they can see. Hiding the link instead would have made a
        record that is in fact linked look unlinked, which is a response lying to a
        caller rather than withholding from one. The redacted entry carries no key, no
        id, no title, no object type, and no field values.
        """
        with self._db.read() as conn:
            record = self._require_record(conn, ref)
            object_type, fields_by_key = self._type_and_fields_by_id(conn, record.object_type_id)
            self._access.require_level(conn, actor, object_type, "read")
            summaries: dict[str, list[dict[str, Any]]] = {}
            for field in fields_by_key.values():
                if field.type != "relation":
                    continue
                target_type = self._may_read_target(conn, actor, field)
                readable = target_type is not None
                display = None
                if target_type is not None:
                    target_fields = {
                        f.key: f for f in self._schema.list_fields(conn, target_type.id)
                    }
                    display = display_field(target_fields, target_type.display_field_key)
                entries: list[dict[str, Any]] = []
                for link in self._records.links_from(conn, record.id, field.id):
                    if not readable:
                        # `display` is a title, so a redacted entry gains nothing. The key
                        # is absent, not null.
                        entries.append({"redacted": True})
                        continue
                    target = self._records.get_record(conn, link.to_record_id, include_deleted=True)
                    if target is not None:
                        entries.append(
                            {
                                "key": target.key,
                                "id": target.id,
                                "display": (
                                    target.data.get(display.key) if display is not None else None
                                ),
                            }
                        )
                summaries[field.key] = entries
            return summaries

    def _resolve_readable_ref(self, conn: Connection, actor: ActorContext, ref: str) -> str:
        """Resolve a record reference named **inside a filter** to its id, refusing when
        the caller cannot read that record's type.

        ``linked_to`` / ``linked_to_any`` naming a key in an unreadable type is
        ``forbidden`` rather than a silent empty result, because the caller named the
        thing: an empty answer there would be the filter lying about what matched.
        """
        record = self._require_record(conn, ref, include_deleted=True)
        object_type = self._schema.get_object_type_by_id(conn, record.object_type_id)
        if object_type is not None:
            self._access.require_level(conn, actor, object_type, "read")
        return record.id

    def _may_read_target(
        self, conn: Connection, actor: ActorContext, field: FieldDef
    ) -> ObjectType | None:
        """A relation field's target type when the caller may read it, else ``None``.

        Uses the **unfiltered** repository lookup deliberately: deciding
        to redact requires finding out what the caller may not see, so filtering this
        lookup would make redaction impossible.

        Returns the row rather than a bare ``bool``: ``list_link_summaries`` needs the
        type to resolve ``display_field_key``, and dropping the row here would make the
        caller resolve the same target type a second time. The verdict is
        derived by the caller so the resolution stays at one site.
        """
        target_type = self._schema.get_object_type_by_key(
            conn, str(field.config["target_type_key"])
        )
        if target_type is None:
            return None
        if not level_allows(self._access.effective_level(conn, actor, target_type), "read"):
            return None
        return target_type

    def get_record_expansions(
        self,
        actor: ActorContext,
        ref: str,
        relation_field_keys: list[str],
        expand_fields: list[str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Resolve relation fields one level deep to key, display name, and a
        caller-specified field subset (FR-L6)."""
        with self._db.read() as conn:
            record = self._require_record(conn, ref)
            self._require_record_level(conn, actor, record, "read")
            _, fields_by_key = self._type_and_fields_by_id(conn, record.object_type_id)
            return self._expand_record(
                conn, actor, record, fields_by_key, relation_field_keys, expand_fields
            )

    def query_records(
        self,
        actor: ActorContext,
        object_type_key: str,
        filter: dict[str, Any] | None = None,
        sort: list[dict[str, Any]] | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
        cursor: str | None = None,
        fields: list[str] | str | None = None,
        expand_relations: list[str] | None = None,
        include_deleted: bool = False,
        now: datetime | None = None,
    ) -> QueryResult:
        """The core read path (FR-R5 through FR-R9, FR-R11, FR-M8, FR-L6)."""
        validate_page_limit(limit, MAX_QUERY_LIMIT, "MAX_QUERY_LIMIT")
        with self._db.read() as conn:
            object_type, fields_by_key = self._type_and_fields(conn, object_type_key)
            self._access.require_level(conn, actor, object_type, "read")
            ctx = FilterContext(
                object_type_key=object_type.key,
                fields_by_key=fields_by_key,
                now=(now or utc_now()),
                resolve_record_ref=lambda ref: self._resolve_readable_ref(conn, actor, ref),
                resolve_principal_ref=self._filter_principal_resolver(conn, actor),
            )
            node = parse_filter(filter, ctx)
            sort_keys = parse_sort(sort, fields_by_key, object_type.key)
            used_default_projection = fields is None
            projection = self._validate_projection(fields, fields_by_key, object_type)
            compiled = compile_query(
                object_type, fields_by_key, node, sort_keys, limit, cursor, include_deleted
            )
            rows = self._records.run_query(conn, compiled.sql, compiled.params)
            total = self._records.run_count(conn, compiled.count_sql, compiled.count_params)
            next_cursor: str | None = None
            if len(rows) > limit:
                rows = rows[:limit]
                last_record, last_extras = rows[-1]
                next_cursor = compiled.cursor_for(last_extras, last_record.id)
            records = [self._project(record, projection) for record, _ in rows]
            if expand_relations:
                for record_dict, (record, _) in zip(records, rows, strict=True):
                    record_dict["expand"] = self._expand_record(
                        conn, actor, record, fields_by_key, expand_relations, None
                    )
            truncated = next_cursor is not None or (
                used_default_projection
                and projection is not None
                and any(set(record.data) - set(projection) for record, _ in rows)
            )
            return QueryResult(
                records=records, total_count=total, next_cursor=next_cursor, truncated=truncated
            )

    def bulk_update(
        self,
        actor: ActorContext,
        object_type_key: str,
        values: dict[str, Any],
        filter: dict[str, Any] | None = None,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> BulkUpdateResult:
        """Apply one value patch to every live record matching a filter (FR-R10).

        Uses the same filter grammar as ``query_records``. ``dry_run: true`` reports
        the affected count and up to twenty sample keys without writing anything.
        A live run applies unconditionally (there is no per-record expected_version;
        an agent that needs conflict safety should update records individually).
        Runs as one transaction, so a failure partway through rolls back every
        record touched by the call.
        """
        if not values:
            raise ValidationFailedError("bulk_update requires at least one value to set.")
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            object_type, fields_by_key = self._type_and_fields(conn, object_type_key)
            self._access.require_level(conn, actor, object_type, "write")
            ctx = FilterContext(
                object_type_key=object_type.key,
                fields_by_key=fields_by_key,
                now=(now or utc_now()),
                resolve_record_ref=lambda ref: self._resolve_readable_ref(conn, actor, ref),
                resolve_principal_ref=self._filter_principal_resolver(conn, actor),
            )
            node = parse_filter(filter, ctx)
            compiled = compile_query(
                object_type, fields_by_key, node, (), BULK_UPDATE_MAX_MATCHES, None, False
            )
            rows = self._records.run_query(conn, compiled.sql, compiled.params)
            matched = [record for record, _ in rows][:BULK_UPDATE_MAX_MATCHES]

            if dry_run:
                return BulkUpdateResult(
                    affected_count=len(matched),
                    sample_keys=[r.key for r in matched[:BULK_UPDATE_SAMPLE_SIZE]],
                    dry_run=True,
                )

            cleared = {k for k, v in values.items() if v is None}
            for key in cleared:
                if key not in fields_by_key and key not in PSEUDO_FIELDS:
                    self._raise_unknown_field(key, object_type.key, fields_by_key)
                field = fields_by_key.get(key)
                if field is not None and field.is_required:
                    raise ValidationFailedError(
                        f"Field {key!r} is required and cannot be cleared.", key
                    )
            supplied = {k: v for k, v in values.items() if v is not None}
            validated = self._validate_values(conn, actor, object_type, fields_by_key, supplied)

            updated_keys: list[str] = []
            for record in matched:
                new_data = dict(record.data)
                for key in cleared:
                    new_data.pop(key, None)
                new_data.update(validated)
                changed = [
                    key
                    for key in set(record.data) | set(new_data)
                    if record.data.get(key) != new_data.get(key)
                ]
                if not changed:
                    continue
                self._check_unique(
                    conn,
                    object_type,
                    fields_by_key,
                    {k: new_data[k] for k in changed if k in new_data},
                    exclude_record_id=record.id,
                )
                try:
                    self._records.update_record_row(
                        conn,
                        record.id,
                        {
                            "data": new_data,
                            "version": record.version + 1,
                            "updated_at": ts,
                            "updated_by": actor.principal_id,
                            # Written on every value-changing write, INCLUDING an
                            # unlabeled one. Left to default, an agent's mark would outlive the
                            # agent's involvement and the By column would keep naming it long
                            # after a person took the record over.
                            "updated_by_agent_label_id": actor.agent_label_id,
                        },
                    )
                except IntegrityError as exc:
                    raise ValidationFailedError(
                        f"Record {record.key} violates a uniqueness constraint: {exc.orig}"
                    ) from exc
                self._sync_attachment_refs(conn, record.id, fields_by_key, new_data)
                events = [
                    make_event(
                        actor,
                        ts,
                        entity_type="record",
                        entity_id=record.id,
                        action="update",
                        record_id=record.id,
                        object_type_id=object_type.id,
                        field_key=key,
                        old_value=record.data.get(key),
                        new_value=new_data.get(key),
                    )
                    for key in sorted(changed)
                ]
                self._audit.append(conn, events)
                refreshed = self._records.get_record(conn, record.id)
                if refreshed is not None:
                    self._index(conn, object_type, fields_by_key, refreshed, changed, now)
                updated_keys.append(record.key)
            return BulkUpdateResult(
                affected_count=len(updated_keys),
                sample_keys=updated_keys[:BULK_UPDATE_SAMPLE_SIZE],
                dry_run=False,
            )

    # ----------------------------------------------------------------- writes

    @contextmanager
    def write_batch(self) -> Iterator[Connection]:
        """One transaction spanning many record writes.

        The three ``*_in_txn`` methods below take the connection this yields, so a
        caller that writes N records commits them together or not at all. It exists
        because ``CsvService`` needs that guarantee (FR-E2) and has no ``Database`` of
        its own: record-write transaction boundaries belong to this service, which is
        what ``db.py`` means by "services own transaction boundaries", so the batch
        boundary is published here rather than handing the database around.
        """
        with self._db.write() as conn:
            yield conn

    def create_record(
        self,
        actor: ActorContext,
        object_type_key: str,
        values: dict[str, Any],
        now: datetime | None = None,
    ) -> RecordRow:
        with self._db.write() as conn:
            return self.create_record_in_txn(conn, actor, object_type_key, values, now)

    def create_record_in_txn(
        self,
        conn: Connection,
        actor: ActorContext,
        object_type_key: str,
        values: dict[str, Any],
        now: datetime | None = None,
    ) -> RecordRow:
        """:meth:`create_record`, inside a transaction the caller already opened.

        Exists so a batch can commit or roll back as one: ``CsvService.import_csv``
        writes every planned row through this method under a single ``db.write()``, and
        a failure at any row unwinds the ones before it. Calling :meth:`create_record`
        in that loop would check a *second* connection out of the pool and issue its own
        ``BEGIN IMMEDIATE`` against a database the outer transaction already holds the
        writer lock on, which blocks for ``busy_timeout`` and then fails.
        """
        ts = format_datetime(now or utc_now())
        object_type, fields_by_key = self._type_and_fields(conn, object_type_key)
        self._access.require_level(conn, actor, object_type, "write")
        data = self._validate_values(conn, actor, object_type, fields_by_key, values)
        for field in fields_by_key.values():
            if field.key not in data and field.default_value is not None:
                data[field.key] = field.default_value  # default application
        missing = [
            f.key
            for f in fields_by_key.values()
            if f.is_required and f.type != "relation" and f.key not in data
        ]
        if missing:
            raise ValidationFailedError(
                f"Required field(s) missing: {', '.join(missing)}.",
                missing[0],
            )
        self._check_unique(conn, object_type, fields_by_key, data, exclude_record_id=None)
        seq = self._schema.allocate_key_seq(conn, object_type.id)
        record = RecordRow(
            id=str(uuidlib.uuid4()),
            object_type_id=object_type.id,
            key=f"{object_type.key_prefix}-{seq:03d}",
            key_seq=seq,
            version=1,
            data=data,
            created_at=ts,
            created_by=actor.principal_id,
            updated_at=ts,
            updated_by=actor.principal_id,
            # A create is a value change, so it marks the row.
            updated_by_agent_label_id=actor.agent_label_id,
            deleted_at=None,
            deleted_by=None,
            comment_count=0,
            last_comment_at=None,
        )
        try:
            self._records.insert_record(conn, record)
        except IntegrityError as exc:
            raise ValidationFailedError(
                f"Record violates a uniqueness constraint: {exc.orig}"
            ) from exc
        self._sync_attachment_refs(conn, record.id, fields_by_key, data)
        events = [
            make_event(
                actor,
                ts,
                entity_type="record",
                entity_id=record.id,
                action="create",
                record_id=record.id,
                object_type_id=object_type.id,
                new_value={"key": record.key},
            )
        ]
        for field_key, value in data.items():
            events.append(
                make_event(
                    actor,
                    ts,
                    entity_type="record",
                    entity_id=record.id,
                    action="create",
                    record_id=record.id,
                    object_type_id=object_type.id,
                    field_key=field_key,
                    new_value=value,
                )
            )
        self._audit.append(conn, events)
        self._index(conn, object_type, fields_by_key, record, list(data), now)
        return record

    def update_record(
        self,
        actor: ActorContext,
        ref: str,
        values: dict[str, Any],
        expected_version: int | None = None,
        force: bool = False,
        now: datetime | None = None,
        note: str | None = None,
    ) -> RecordRow:
        """Partial, version-checked update (FR-R4): only supplied keys change; a
        ``None`` value clears the field (absent values are stored by key omission).
        ``note`` is threaded onto the audit rows this call writes; revert
        (DD-21) is this same method called with a computed inverse patch and a
        note referencing what was reverted, not a second write path."""
        with self._db.write() as conn:
            return self.update_record_in_txn(
                conn, actor, ref, values, expected_version, force, now, note
            )

    def update_record_in_txn(
        self,
        conn: Connection,
        actor: ActorContext,
        ref: str,
        values: dict[str, Any],
        expected_version: int | None = None,
        force: bool = False,
        now: datetime | None = None,
        note: str | None = None,
    ) -> RecordRow:
        """:meth:`update_record`, inside a transaction the caller already opened.
        See :meth:`create_record_in_txn` for why a batch needs this."""
        ts = format_datetime(now or utc_now())
        record = self._require_record(conn, ref)
        object_type, fields_by_key = self._type_and_fields_by_id(conn, record.object_type_id)
        self._access.require_level(conn, actor, object_type, "write")
        if expected_version is not None and expected_version != record.version and not force:
            changed_since = self._changed_fields_since(conn, record.id, expected_version)
            conflicting = {
                key: {
                    "your_value": values[key],
                    "current_value": record.data.get(key),
                }
                for key in values
                if key in changed_since
            }
            raise VersionConflictError(
                record.key, record.version, expected_version, conflicting, changed_since
            )

        new_data = dict(record.data)
        cleared = {k for k, v in values.items() if v is None}
        for key in cleared:
            if key not in fields_by_key and key not in PSEUDO_FIELDS:
                self._raise_unknown_field(key, object_type.key, fields_by_key)
            field = fields_by_key.get(key)
            if field is not None and field.is_required:
                raise ValidationFailedError(
                    f"Field {key!r} is required and cannot be cleared.", key
                )
            new_data.pop(key, None)
        supplied = {k: v for k, v in values.items() if v is not None}
        validated = self._validate_values(
            conn, actor, object_type, fields_by_key, supplied, current=record.data
        )
        new_data.update(validated)

        changed = [
            key
            for key in set(record.data) | set(new_data)
            if record.data.get(key) != new_data.get(key)
        ]
        if not changed:
            return record
        self._check_unique(
            conn,
            object_type,
            fields_by_key,
            {k: new_data[k] for k in changed if k in new_data},
            exclude_record_id=record.id,
        )
        try:
            self._records.update_record_row(
                conn,
                record.id,
                {
                    "data": new_data,
                    "version": record.version + 1,
                    "updated_at": ts,
                    "updated_by": actor.principal_id,
                    # See the note at the sibling site in ``write_batch``.
                    "updated_by_agent_label_id": actor.agent_label_id,
                },
            )
        except IntegrityError as exc:
            raise ValidationFailedError(
                f"Record violates a uniqueness constraint: {exc.orig}"
            ) from exc
        self._sync_attachment_refs(conn, record.id, fields_by_key, new_data)
        events = [
            make_event(
                actor,
                ts,
                entity_type="record",
                entity_id=record.id,
                action="update",
                record_id=record.id,
                object_type_id=object_type.id,
                field_key=key,
                old_value=record.data.get(key),
                new_value=new_data.get(key),
                note=note,
            )
            for key in sorted(changed)
        ]
        self._audit.append(conn, events)
        refreshed = self._records.get_record(conn, record.id)
        assert refreshed is not None
        self._index(conn, object_type, fields_by_key, refreshed, changed, now)
        return refreshed

    def revert_field_change(
        self,
        actor: ActorContext,
        event_id: int,
        expected_version: int,
        force: bool = False,
        now: datetime | None = None,
    ) -> RecordRow:
        """Revert one field-update audit event to its ``old_value``, as a new
        forward-audited write reusing ``update_record``'s version check (FR-D6,
        DD-21). Only ``entity_type == 'record'``, ``action == 'update'`` events
        with a ``field_key`` are revertible here: relation link/unlink events,
        comment edits, and a record's own create row are out of scope for this
        path (DD-21 scope boundary)."""
        with self._db.read() as conn:
            event = self._audit.get_event(conn, event_id)
            if event is None:
                raise NotFoundError("audit event", str(event_id))
            if event.entity_type != "record" or event.action != "update" or event.field_key is None:
                raise ValidationFailedError(
                    f"Audit event {event_id} is a {event.entity_type!r} {event.action!r} "
                    "event, not a record field-update. Only entity_type='record', "
                    "action='update' events with a field_key can be reverted through this "
                    "path; relation links, comments, and a record's own create row are not "
                    "revertible here.",
                    event_id=event_id,
                )
        assert event.record_id is not None
        return self.update_record(
            actor,
            event.record_id,
            {event.field_key: event.old_value},
            expected_version,
            force,
            now,
            note=f"revert of event {event_id}",
        )

    def revert_to_version(
        self,
        actor: ActorContext,
        ref: str,
        target_version: int,
        expected_version: int,
        force: bool = False,
        now: datetime | None = None,
    ) -> RecordRow:
        """Revert a record's fields to their state at ``target_version`` by
        grouping its update events by ``request_id`` (one group per version
        increment, since one ``update_record`` call maps one-to-one onto one
        version bump) and keeping only each field's first post-target
        ``old_value`` — that is exactly the field's state at ``target_version``,
        including a field that did not yet exist there being cleared (FR-D6,
        DD-21)."""
        with self._db.read() as conn:
            record = self._require_record(conn, ref)
            if not (1 <= target_version < record.version):
                raise ValidationFailedError(
                    f"target_version must be between 1 and {record.version - 1} for "
                    f"record {record.key} (currently at version {record.version}).",
                    target_version=target_version,
                )
            groups = self._update_event_groups(conn, record.id)
            patch: dict[str, Any] = {}
            for _, group in groups[target_version - 1 :]:
                for event in group:
                    if event.field_key is not None and event.field_key not in patch:
                        patch[event.field_key] = event.old_value
        return self.update_record(
            actor,
            record.id,
            patch,
            expected_version,
            force,
            now,
            note=f"revert to version {target_version}",
        )

    def delete_record(
        self,
        actor: ActorContext,
        ref: str,
        force: bool = False,
        now: datetime | None = None,
    ) -> RecordRow:
        """Soft delete; blocked by inbound links unless forced (FR-L4, FR-R11)."""
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            record = self._require_record(conn, ref)
            self._require_record_level(conn, actor, record, "write")
            inbound = self._records.links_to(conn, record.id)
            if inbound and not force:
                blocking: list[str] = []
                for link in inbound:
                    source = self._records.get_record(
                        conn, link.from_record_id, include_deleted=True
                    )
                    if source is not None and source.key not in blocking:
                        blocking.append(source.key)
                raise RelationBlockedError(record.key, blocking)
            events: list[AuditEvent] = []
            if force:
                for link in [*inbound, *self._records.links_from(conn, record.id)]:
                    self._records.delete_link_row(conn, link.id)
                    events.append(
                        self._link_event(actor, ts, conn, link, "unlink", record.object_type_id)
                    )
            # Deliberately does NOT move ``updated_by`` or
            # ``updated_by_agent_label_id``. ``updated_by`` is the last *value* change
            # (``fieldtypes.py``), and a delete changes no value. Marking it here would leave a
            # row whose person and agent name different writes.
            self._records.update_record_row(
                conn, record.id, {"deleted_at": ts, "deleted_by": actor.principal_id}
            )
            events.append(
                make_event(
                    actor,
                    ts,
                    entity_type="record",
                    entity_id=record.id,
                    action="delete",
                    record_id=record.id,
                    object_type_id=record.object_type_id,
                    old_value={"key": record.key},
                )
            )
            self._audit.append(conn, events)
            refreshed = self._records.get_record(conn, record.id, include_deleted=True)
            assert refreshed is not None
            return refreshed

    def restore_record(
        self, actor: ActorContext, ref: str, now: datetime | None = None
    ) -> RecordRow:
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            record = self._records.get_record(conn, ref, include_deleted=True)
            if record is None:
                raise NotFoundError("record", ref)
            if record.deleted_at is None:
                raise ValidationFailedError(f"Record {record.key} is not deleted.")
            object_type, fields_by_key = self._type_and_fields_by_id(conn, record.object_type_id)
            self._access.require_level(conn, actor, object_type, "write")
            self._check_unique(
                conn, object_type, fields_by_key, record.data, exclude_record_id=record.id
            )
            try:
                # A restore changes no value either; see ``delete_record``.
                self._records.update_record_row(
                    conn, record.id, {"deleted_at": None, "deleted_by": None}
                )
            except IntegrityError as exc:
                raise ValidationFailedError(
                    f"Cannot restore {record.key}: a live record now holds one of its "
                    f"unique values ({exc.orig})."
                ) from exc
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="record",
                        entity_id=record.id,
                        action="restore",
                        record_id=record.id,
                        object_type_id=record.object_type_id,
                        new_value={"key": record.key},
                    )
                ],
            )
            refreshed = self._records.get_record(conn, record.id)
            assert refreshed is not None
            return refreshed

    def link_records(
        self,
        actor: ActorContext,
        from_ref: str,
        field_key: str,
        to_refs: list[str],
        now: datetime | None = None,
    ) -> list[LinkRow]:
        """Create links; cardinality 'one' is enforced here in the service layer
        (FR-L1), and inverse links are maintained automatically (FR-L3)."""
        # Checked before the transaction is opened, which is the order this method has to
        # keep: an
        # empty ``to_refs`` is refused without ever contending for SQLite's single
        # writer lock. It is checked *again* inside ``link_records_in_txn``, because a
        # batch caller reaches that method directly and must be refused identically.
        if not to_refs:
            raise ValidationFailedError("to_records must name at least one record.")
        with self._db.write() as conn:
            return self.link_records_in_txn(conn, actor, from_ref, field_key, to_refs, now)

    def link_records_in_txn(
        self,
        conn: Connection,
        actor: ActorContext,
        from_ref: str,
        field_key: str,
        to_refs: list[str],
        now: datetime | None = None,
    ) -> list[LinkRow]:
        """:meth:`link_records`, inside a transaction the caller already opened.
        See :meth:`create_record_in_txn` for why a batch needs this. The empty-``to_refs``
        guard is deliberately duplicated: :meth:`link_records` checks it *before* opening
        the transaction, and it is repeated here so a caller
        arriving directly with a connection is refused on the same terms."""
        if not to_refs:
            raise ValidationFailedError("to_records must name at least one record.")
        ts = format_datetime(now or utc_now())
        source = self._require_record(conn, from_ref)
        object_type, fields_by_key = self._type_and_fields_by_id(conn, source.object_type_id)
        self._access.require_level(conn, actor, object_type, "write")
        field = self._require_relation_field(object_type, fields_by_key, field_key)
        target_type = self._schema.get_object_type_by_key(
            conn, str(field.config["target_type_key"])
        )
        if target_type is None:
            raise UnknownObjectTypeError(str(field.config["target_type_key"]), [])
        # `write` on the source type and at least `read` on the
        # target's. You may not link a record into a type you cannot see -- doing so
        # would let a caller assert a relationship whose other end it can neither
        # name nor verify, and would put a redacted entry on the far
        # side that the caller itself created.
        self._access.require_level(conn, actor, target_type, "read")
        targets = []
        for ref in to_refs:
            target = self._require_record(conn, ref)
            if target.object_type_id != target_type.id:
                raise ValidationFailedError(
                    f"Record {target.key} is not a {target_type.key}; field "
                    f"{field_key!r} links to {target_type.key} records.",
                    field_key,
                )
            targets.append(target)

        existing = self._records.links_from(conn, source.id, field.id)
        already = {link.to_record_id for link in existing}
        duplicate_keys = [t.key for t in targets if t.id in already]
        if duplicate_keys:
            raise ValidationFailedError(
                f"Already linked via {field_key!r}: {', '.join(duplicate_keys)}.",
                field_key,
            )
        if field.config["cardinality"] == "one" and len(existing) + len(targets) > 1:
            raise ValidationFailedError(
                f"Field {field_key!r} has cardinality 'one' and would end up with "
                f"{len(existing) + len(targets)} links. Unlink the current record "
                "first.",
                field_key,
            )

        inverse = self._inverse_field(conn, field, target_type)
        created: list[LinkRow] = []
        events: list[AuditEvent] = []
        for target in targets:
            link = LinkRow(
                id=str(uuidlib.uuid4()),
                field_id=field.id,
                from_record_id=source.id,
                to_record_id=target.id,
                position=self._records.next_link_position(conn, field.id, source.id),
                created_at=ts,
                created_by=actor.principal_id,
            )
            self._records.insert_link(conn, link)
            created.append(link)
            events.append(self._link_event(actor, ts, conn, link, "link", source.object_type_id))
            if inverse is not None:
                if inverse.config["cardinality"] == "one" and self._records.links_from(
                    conn, target.id, inverse.id
                ):
                    raise ValidationFailedError(
                        f"Inverse field {inverse.key!r} on {target.key} has "
                        "cardinality 'one' and is already linked.",
                        inverse.key,
                    )
                reciprocal = LinkRow(
                    id=str(uuidlib.uuid4()),
                    field_id=inverse.id,
                    from_record_id=target.id,
                    to_record_id=source.id,
                    position=self._records.next_link_position(conn, inverse.id, target.id),
                    created_at=ts,
                    created_by=actor.principal_id,
                )
                self._records.insert_link(conn, reciprocal)
                events.append(
                    self._link_event(actor, ts, conn, reciprocal, "link", target.object_type_id)
                )
        self._audit.append(conn, events)
        return created

    def unlink_records(
        self,
        actor: ActorContext,
        from_ref: str,
        field_key: str,
        to_refs: list[str],
        now: datetime | None = None,
    ) -> int:
        if not to_refs:
            raise ValidationFailedError("to_records must name at least one record.")
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            source = self._require_record(conn, from_ref)
            object_type, fields_by_key = self._type_and_fields_by_id(conn, source.object_type_id)
            self._access.require_level(conn, actor, object_type, "write")
            field = self._require_relation_field(object_type, fields_by_key, field_key)
            target_type = self._schema.get_object_type_by_key(
                conn, str(field.config["target_type_key"])
            )
            inverse = (
                self._inverse_field(conn, field, target_type) if target_type is not None else None
            )
            events: list[AuditEvent] = []
            removed = 0
            for ref in to_refs:
                target = self._require_record(conn, ref, include_deleted=True)
                link = self._records.get_link(conn, field.id, source.id, target.id)
                if link is None:
                    raise NotFoundError("link", f"{source.key} -[{field_key}]-> {target.key}")
                self._records.delete_link_row(conn, link.id)
                events.append(
                    self._link_event(actor, ts, conn, link, "unlink", source.object_type_id)
                )
                removed += 1
                if inverse is not None:
                    reciprocal = self._records.get_link(conn, inverse.id, target.id, source.id)
                    if reciprocal is not None:
                        self._records.delete_link_row(conn, reciprocal.id)
                        events.append(
                            self._link_event(
                                actor, ts, conn, reciprocal, "unlink", target.object_type_id
                            )
                        )
            self._audit.append(conn, events)
            return removed

    # -------------------------------------------------------------- internals

    def _index(
        self,
        conn: Connection,
        object_type: ObjectType,
        fields_by_key: dict[str, FieldDef],
        record: RecordRow,
        keys: list[str],
        now: datetime | None,
    ) -> None:
        """Maintain the search index for the fields this write touched (FR-Q3).

        Called inside the caller's own ``db.write()`` transaction, so the audit row,
        the keyword row, and the queue job commit together or not at all
        (docs/DATA_MODEL.md section 14). ``delete_record`` and ``restore_record``
        deliberately do not call this: soft deletion is excluded at query time, not by
        touching the index (docs/DATA_MODEL.md section 10). Revert reaches it through
        ``update_record`` like any other write (DD-21), so there is no separate hook.
        """
        if self._search_index is None or not keys:
            return
        self._search_index.index_record_fields(conn, object_type, fields_by_key, record, keys, now)

    def require_type_level(
        self, actor: ActorContext, object_type_key: str, required: Scope
    ) -> None:
        """The level check for one object type, by key, in its own read transaction.

        Public because ``CsvService`` is a service over two other services rather than
        over repositories, so it has no connection of its own to check against and must
        not grow one (DD-2). Named without a leading underscore for the same reason
        the access completeness meta-test can see it: a service reaching another
        service's gate is legible, a service reaching a private helper is not.
        """
        with self._db.read() as conn:
            object_type, _ = self._type_and_fields(conn, object_type_key)
            self._access.require_level(conn, actor, object_type, required)

    def _require_record_level(
        self, conn: Connection, actor: ActorContext, record: RecordRow, required: Scope
    ) -> None:
        """The level check for a record, expressed against its object type. A record is
        exactly as visible as its type: there is no field- or record-level access."""
        object_type = self._schema.get_object_type_by_id(conn, record.object_type_id)
        if object_type is None:
            raise NotFoundError("object type", record.object_type_id)
        self._access.require_level(conn, actor, object_type, required)

    def _require_record(
        self, conn: Connection, ref: str, include_deleted: bool = False
    ) -> RecordRow:
        record = self._records.get_record(conn, ref, include_deleted)
        if record is None:
            raise NotFoundError("record", ref)
        return record

    def _type_and_fields(
        self, conn: Connection, object_type_key: str
    ) -> tuple[ObjectType, dict[str, FieldDef]]:
        object_type = self._schema.get_object_type_by_key(conn, object_type_key)
        if object_type is None:
            valid = [t.key for t in self._schema.list_object_types(conn)]
            raise UnknownObjectTypeError(object_type_key, valid)
        fields = self._schema.list_fields(conn, object_type.id)
        return object_type, {f.key: f for f in fields}

    def _type_and_fields_by_id(
        self, conn: Connection, type_id: str
    ) -> tuple[ObjectType, dict[str, FieldDef]]:
        object_type = self._schema.get_object_type_by_id(conn, type_id)
        if object_type is None:
            raise NotFoundError("object type", type_id)
        fields = self._schema.list_fields(conn, object_type.id)
        return object_type, {f.key: f for f in fields}

    def _raise_unknown_field(
        self, field_key: str, object_type_key: str, fields_by_key: dict[str, FieldDef]
    ) -> None:
        valid = sorted(fields_by_key)
        near = difflib.get_close_matches(field_key, valid + sorted(PSEUDO_FIELDS), n=3, cutoff=0.6)
        raise UnknownFieldError(field_key, object_type_key, valid, list(near))

    def _validate_values(
        self,
        conn: Connection,
        actor: ActorContext,
        object_type: ObjectType,
        fields_by_key: dict[str, FieldDef],
        values: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate a value document against the type's field definitions
        (docs/DATA_MODEL.md section 14). Unknown keys are rejected with near-miss
        suggestions; relation keys are redirected to link_records.

        A ``user_ref`` value is **resolved**, not merely checked. The same argument
        ``_check_attachment_refs`` makes below applies here -- this is the one funnel every
        ``user_ref`` value passes through, so it is where ``@me``, an email and an exact
        display name become a principal id, and where
        ``allow_service_accounts`` is enforced. What lands in ``records.data`` is still a
        bare principal id, so no stored shape, index, cursor or coercion rule changes, and
        the audit event records the id rather than whatever the caller typed (DD-4).

        ``current`` is the record's data as stored, passed by ``update_record`` and left
        ``None`` by ``create_record`` and ``bulk_update``. It carries the rule that an id equal
        to what is already on the field re-submits successfully even after that principal
        is deactivated, while assigning an inactive principal afresh stays refused.
        """
        data: dict[str, Any] = {}
        for key, value in values.items():
            field = fields_by_key.get(key)
            if field is None:
                self._raise_unknown_field(key, object_type.key, fields_by_key)
                raise AssertionError("unreachable")
            validated = validate_value(field.type, field.config, key, value)
            if field.type == "user_ref":
                validated = self._resolve_user_ref(conn, actor, key, field, validated, current)
            if field.type == "attachment":
                self._check_attachment_refs(conn, actor, key, field, validated)
            data[key] = validated
        return data

    def _resolve_user_ref(
        self,
        conn: Connection,
        actor: ActorContext,
        key: str,
        field: FieldDef,
        value: str,
        current: dict[str, Any] | None,
    ) -> str:
        """One ``user_ref`` value, through the single resolver.

        The field key is prefixed onto the resolver's message rather than passed into it,
        because the resolver is also the filter compiler's and knows nothing about
        ``fields``; the ``field`` argument on ``ValidationFailedError`` is what a client
        actually branches on and is set here.
        """
        stored = current.get(key) if current is not None else None
        try:
            return resolve_principal_ref(
                conn,
                self._principals_repo(),
                value,
                me=actor.principal_id,
                allow_inactive=False,
                allow_service_accounts=bool(field.config.get("allow_service_accounts", True)),
                stored_value=stored if isinstance(stored, str) else None,
            )
        except ValidationFailedError as exc:
            raise ValidationFailedError(
                f"Field {key!r}: {exc.message}", key, **_resolver_details(exc)
            ) from exc

    def principal_sidecar(
        self,
        actor: ActorContext,
        object_type_ref: str,
        record_docs: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """The ``principals`` map for one response document (DD-25).

        **One read per document, not one per record.** ``object_type_ref`` is an object
        type key or id; the type is resolved once, its ``user_ref`` field keys are read
        once, every referenced id across every record is collected, and exactly one
        ``principals_by_ids`` call issues. ``tests/test_principal_sidecar.py`` counts
        repository calls to hold that, because "N+1 but correct" is the failure mode this
        shape exists to prevent and it is invisible in a passing functional test.

        Why a sidecar rather than a richer value: ``record_doc`` takes a ``RecordRow`` and
        nothing else so ``ExportService`` and ``envelopes`` can share one function,
        and it cannot resolve a principal. DD-25's join-in-the-repository shape does not
        transfer either -- ``comments.author_id`` is a *column*, while a ``user_ref``
        value lives inside ``records.data``, whose ``user_ref`` keys are known only from
        the type's field definitions. So the service assembles it, and the stored value
        stays a bare id.

        ``created_by`` and ``updated_by`` are always collected, so a type with no
        ``user_ref`` field still gets a map naming whoever wrote the record.

        ``actor`` is taken and not read: every caller has already been through
        ``require_level`` for the records it is about to name, and the map carries no
        field a directory read would withhold from the same caller. It
        stays in the signature because every other public entry point on this service
        takes one first and positionally, and an authorization question that
        arrives here later must not be a signature change.
        """
        if not record_docs:
            return {}
        with self._db.read() as conn:
            object_type = self._schema.get_object_type_by_key(conn, object_type_ref)
            if object_type is None:
                object_type = self._schema.get_object_type_by_id(conn, object_type_ref)
            user_ref_keys = (
                [
                    f.key
                    for f in self._schema.list_fields(conn, object_type.id)
                    if f.type == "user_ref"
                ]
                if object_type is not None
                else []
            )
            ids: list[str] = []
            for doc in record_docs:
                for column in ("created_by", "updated_by"):
                    value = doc.get(column)
                    if isinstance(value, str) and value:
                        ids.append(value)
                data = doc.get("data")
                if not isinstance(data, dict):
                    continue
                for key in user_ref_keys:
                    value = data.get(key)
                    if isinstance(value, str) and value:
                        ids.append(value)
            if not ids:
                return {}
            rows = self._principals_repo().principals_by_ids(conn, list(dict.fromkeys(ids)))
        return principal_sidecar_doc(rows)

    def agent_label_sidecar(
        self,
        actor: ActorContext,
        record_docs: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """The ``agent_labels`` map for one response document.

        **One read per document, not one per record**, exactly as ``principal_sidecar`` is,
        and for the same reason: "N+1 but correct" passes every functional test and is
        invisible until it is measured. ``tests/test_record_agent_label.py`` counts the
        repository calls.

        Simpler than its sibling: an agent label is a *column* on the record row, not a value
        inside ``data``, so there is no field catalogue to consult first and no object type to
        resolve. Records whose label is null contribute nothing and a page written entirely by
        people costs no read at all.

        ``actor`` is taken and not read, for the reason ``principal_sidecar`` records: every
        caller has been through ``require_level`` for the records it is about to name, and
        the sidecar's two-key projection settles what the map may carry. It stays in the
        signature so an authorization question arriving here later is not a signature
        change.
        """
        ids = [
            doc["updated_by_agent_label_id"]
            for doc in record_docs
            if isinstance(doc.get("updated_by_agent_label_id"), str)
        ]
        if not ids:
            return {}
        with self._db.read() as conn:
            rows = self._labels_repo().labels_by_ids(conn, list(dict.fromkeys(ids)))
        return agent_label_sidecar_doc(rows)

    def _filter_principal_resolver(
        self, conn: Connection, actor: ActorContext
    ) -> Callable[[str], str]:
        """The ``user_ref`` resolver as the filter compiler consumes it.

        ``allow_inactive=True`` is the whole read/write asymmetry in one argument: a
        filter is a read, and finding a departed colleague's still-open work is the
        handover query. ``allow_service_accounts`` is left at its permissive default and
        no ``stored_value`` is passed, because neither has any meaning for a comparison --
        the pseudo-fields ``created_by`` and ``updated_by`` reach here too and have no
        field config at all.
        """
        return lambda value: resolve_principal_ref(
            conn, self._principals_repo(), value, me=actor.principal_id, allow_inactive=True
        )

    def _labels_repo(self) -> AgentLabelRepository:
        if self._labels is None:  # pragma: no cover - every bundle wires one
            raise RuntimeError(
                "RecordService was built without an agent-label repository, so a record's "
                "agent label cannot be resolved. Pass label_repo= when constructing it."
            )
        return self._labels

    def _principals_repo(self) -> PrincipalRepository:
        if self._principals is None:  # pragma: no cover - every bundle wires one
            raise RuntimeError(
                "RecordService was built without a principal repository, so a user_ref "
                "value cannot be resolved. Pass principal_repo= when constructing it."
            )
        return self._principals

    def _check_attachment_refs(
        self,
        conn: Connection,
        actor: ActorContext,
        key: str,
        field: FieldDef,
        attachment_ids: list[str],
    ) -> None:
        """**The one funnel every attachment field value passes through.**

        Enforces ``max_bytes`` (FR-S3) **and the attachment read rule** over whatever of
        the referenced ids resolve to real ``attachments`` rows. Unlike ``user_ref``, an
        unresolved id is not itself an error: attachment field values are opaque ids
        validated only for shape, and neither the byte-size enforcement nor the
        authorization narrows that contract -- an id that resolves
        to no row is stored, never joined, and authorizes nothing.

        **Why the write path authorizes at all** (DD-12). The read rule makes an
        attachment readable when the caller holds ``read`` on the type of any
        *referencing* record, and ``_sync_attachment_refs`` writes a referencing row for
        every id that resolves. Validating ids "only for shape" would therefore let naming
        an attachment from a type the caller can write manufacture the right to read one
        it cannot: the row the write causes to exist is what makes the download succeed.
        Authority is never inferred from a reference, so a reference is authorized when
        it is written, by the same rule and the same refusal the
        read path uses (:meth:`AccessService.require_attachment_readable`).

        It never returns early when ``max_bytes`` is unset and is never skipped at the
        call site when no attachment repository is wired, because a funnel with two ways
        around it is not one -- and the read rule's join rows fail
        **open** when a path is missed (an attachment with no rows falls back to the
        uploader clause and is invisible to everyone else, which reads as working until
        the wrong person cannot see a file).

        The join rows themselves are **not** written here: this runs during validation,
        before ``create_record`` has a record id and before ``update_record`` knows the
        record's final data. They are written by :meth:`_sync_attachment_refs`,
        immediately after each write of ``records.data``, which is the only place that
        knows what is actually stored.
        """
        if self._attachments is None or self._record_attachments is None:
            # The same two guards that stop any join row being written at all
            # (``_sync_attachment_refs``), so the check fails closed rather than
            # silently authorizing what it cannot look up.
            return
        if not attachment_ids:
            return
        rows = self._attachments.get_attachments(conn, attachment_ids)

        max_bytes = field.config.get("max_bytes")
        if max_bytes is not None:
            total = sum(r.byte_size for r in rows)
            if total > int(max_bytes):
                raise ValidationFailedError(
                    f"Field {key!r} attachments total {total} bytes, exceeding the "
                    f"field's max_bytes of {max_bytes}.",
                    key,
                )

        # The two checks are sequential and independent: a ``max_bytes`` branch that
        # returned early when the field declared no cap, which is most fields, would
        # stop an authorization check placed after it from ever running.
        by_attachment = self._record_attachments.object_type_ids_for_attachments(
            conn, [r.id for r in rows]
        )
        readable = self._access.accessible_type_ids(conn, actor, "read")
        for row in rows:
            self._access.require_attachment_readable(
                conn, actor, row, by_attachment.get(row.id, set()), readable
            )

    def _sync_attachment_refs(
        self,
        conn: Connection,
        record_id: str,
        fields_by_key: dict[str, FieldDef],
        data: dict[str, Any],
    ) -> None:
        """Make ``record_attachments`` match this record's stored data exactly.

        Called immediately after **every** write of ``records.data``: ``create_record``,
        ``update_record``, and ``bulk_update``. CSV import and both revert paths reach it
        through those three rather than around them, which is why they need nothing of
        their own -- and the tests prove that per write path rather than by inspection.

        Reads the *stored* data rather than the supplied patch, so clearing an
        attachment field drops its rows and an update that omits the field keeps them.

        **Ids that resolve to no ``attachments`` row are not written.** Attachment field
        values are opaque ids validated only for shape, and access control does not
        narrow that contract -- but ``record_attachments.attachment_id`` is a real
        foreign key, and a join row for an attachment that does not exist would be
        neither insertable nor useful: there is nothing there to authorize. The stored
        value in ``records.data`` is untouched either way.
        """
        if self._record_attachments is None:
            return
        refs = {
            field.key: [str(v) for v in data.get(field.key) or []]
            for field in fields_by_key.values()
            if field.type == "attachment"
        }
        if self._attachments is not None and any(refs.values()):
            candidates = sorted({i for ids in refs.values() for i in ids})
            known = {row.id for row in self._attachments.get_attachments(conn, candidates)}
            refs = {key: [i for i in ids if i in known] for key, ids in refs.items()}
        elif self._attachments is None:
            refs = dict.fromkeys(refs, [])
        self._record_attachments.replace_for_record(conn, record_id, refs)

    def _check_unique(
        self,
        conn: Connection,
        object_type: ObjectType,
        fields_by_key: dict[str, FieldDef],
        data: dict[str, Any],
        exclude_record_id: str | None,
    ) -> None:
        for field in fields_by_key.values():
            if not field.is_unique or field.key not in data:
                continue
            collision = self._records.find_unique_collision(
                conn, object_type.id, field.key, data[field.key], exclude_record_id
            )
            if collision is not None:
                raise ValidationFailedError(
                    f"Field {field.key!r} must be unique; record {collision} already "
                    f"holds {data[field.key]!r}.",
                    field.key,
                )

    def _update_event_groups(
        self, conn: Connection, record_id: str
    ) -> list[tuple[str, list[AuditEvent]]]:
        """Record field-update events grouped by request_id, one group per prior
        ``update_record`` call. Update calls map one-to-one onto version
        increments, and one call's audit rows share a request_id and consecutive
        ids, so ``groups[v - 1:]`` is exactly the changes made after version
        ``v``, in order (used by both stale-write conflict reporting and
        version revert, DD-21)."""
        events = self._audit.record_update_events(conn, record_id)
        groups: list[tuple[str, list[AuditEvent]]] = []
        for event in events:
            if groups and groups[-1][0] == event.request_id:
                groups[-1][1].append(event)
            else:
                groups.append((event.request_id, [event]))
        return groups

    def _changed_fields_since(
        self, conn: Connection, record_id: str, expected_version: int
    ) -> list[str]:
        """Fields changed by update calls after the caller's version."""
        groups = self._update_event_groups(conn, record_id)
        changed: list[str] = []
        for _, group in groups[max(expected_version - 1, 0) :]:
            for event in group:
                if event.field_key is not None and event.field_key not in changed:
                    changed.append(event.field_key)
        return changed

    def _require_relation_field(
        self,
        object_type: ObjectType,
        fields_by_key: dict[str, FieldDef],
        field_key: str,
    ) -> FieldDef:
        field = fields_by_key.get(field_key)
        if field is None:
            self._raise_unknown_field(field_key, object_type.key, fields_by_key)
            raise AssertionError("unreachable")
        if field.type != "relation":
            raise ValidationFailedError(
                f"Field {field_key!r} is {field.type}, not a relation. Use update_record "
                "for value fields.",
                field_key,
            )
        return field

    def _inverse_field(
        self, conn: Connection, field: FieldDef, target_type: ObjectType
    ) -> FieldDef | None:
        inverse_key = field.config.get("inverse_field_key")
        if inverse_key is None:
            return None
        return self._schema.get_field(conn, target_type.id, str(inverse_key))

    def _link_event(
        self,
        actor: ActorContext,
        ts: str,
        conn: Connection,
        link: LinkRow,
        action: str,
        object_type_id: str,
    ) -> AuditEvent:
        field = self._schema.get_field_by_id(conn, link.field_id)
        to_record = self._records.get_record(conn, link.to_record_id, include_deleted=True)
        payload = {
            "field_key": field.key if field is not None else None,
            "to_record": to_record.key if to_record is not None else link.to_record_id,
        }
        return make_event(
            actor,
            ts,
            entity_type="link",
            entity_id=link.id,
            action=action,
            record_id=link.from_record_id,
            object_type_id=object_type_id,
            field_key=field.key if field is not None else None,
            old_value=payload if action == "unlink" else None,
            new_value=payload if action == "link" else None,
        )

    def _validate_projection(
        self,
        fields: list[str] | str | None,
        fields_by_key: dict[str, FieldDef],
        object_type: ObjectType,
    ) -> list[str] | None:
        """``"*"`` returns every field; a list projects the data document (FR-R6).
        Omitting ``fields`` returns the compact default: key, the type's display
        field, and all indexed fields, to protect the agent's context window
        (FR-M8). Pseudo-fields are always present at the top level of a record."""
        if fields == "*":
            return None
        if fields is None:
            return self._compact_projection(fields_by_key, object_type.display_field_key)
        if isinstance(fields, str):
            raise ValidationFailedError(
                "fields must be a list of field keys, or '*' for everything."
            )
        projection: list[str] = []
        for key in fields:
            if key in PSEUDO_FIELDS:
                continue  # always present on the record envelope
            if key not in fields_by_key:
                self._raise_unknown_field(key, object_type.key, fields_by_key)
            projection.append(key)
        return projection

    def _compact_projection(
        self, fields_by_key: dict[str, FieldDef], display_field_key: str | None = None
    ) -> list[str]:
        """key + the type's display field + every indexed field (FR-M8). Told the chosen
        key: a type whose display field is not its first returns that field
        here, which is the point of choosing one."""
        display = self._display_field(fields_by_key, display_field_key)
        keys: list[str] = [display.key] if display is not None else []
        for f in fields_by_key.values():
            if f.is_indexed and f.key not in keys:
                keys.append(f.key)
        return keys

    @staticmethod
    def _display_field(
        fields_by_key: dict[str, FieldDef], display_field_key: str | None = None
    ) -> FieldDef | None:
        """The shared rule (``services.base.display_field``), kept as a method name so
        its call sites read as they do; the search service uses the helper directly for a
        hit's ``title``. It carries no rule of its own: it can be told the chosen key, and
        it decides nothing."""
        return display_field(fields_by_key, display_field_key)

    def _expand_record(
        self,
        conn: Connection,
        actor: ActorContext,
        record: RecordRow,
        fields_by_key: dict[str, FieldDef],
        relation_field_keys: list[str],
        field_subset: list[str] | None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Resolve relation fields one level deep to key, display name, and an
        optional field subset (FR-L6)."""
        expanded: dict[str, list[dict[str, Any]]] = {}
        for field_key in relation_field_keys:
            object_type = self._schema.get_object_type_by_id(conn, record.object_type_id)
            assert object_type is not None
            field = self._require_relation_field(object_type, fields_by_key, field_key)
            target_type = self._schema.get_object_type_by_key(
                conn, str(field.config["target_type_key"])
            )
            assert target_type is not None
            readable = level_allows(self._access.effective_level(conn, actor, target_type), "read")
            target_fields = {f.key: f for f in self._schema.list_fields(conn, target_type.id)}
            display = self._display_field(target_fields, target_type.display_field_key)
            summaries: list[dict[str, Any]] = []
            for link in self._records.links_from(conn, record.id, field.id):
                if not readable:
                    # The link is preserved, the target is redacted. The
                    # entry carries no key, no id, no title, no object type and no field
                    # values, so the count stays truthful and nothing else leaks.
                    summaries.append({"redacted": True})
                    continue
                target = self._records.get_record(conn, link.to_record_id, include_deleted=True)
                if target is None:
                    continue
                summary: dict[str, Any] = {
                    "key": target.key,
                    "id": target.id,
                    "display": target.data.get(display.key) if display is not None else None,
                }
                if field_subset:
                    summary["fields"] = {
                        k: target.data.get(k) for k in field_subset if k in target_fields
                    }
                summaries.append(summary)
            expanded[field_key] = summaries
        return expanded

    @staticmethod
    def _project(record: RecordRow, projection: list[str] | None) -> dict[str, Any]:
        data = record.data
        if projection is not None:
            data = {k: v for k, v in data.items() if k in projection}
        return {
            "id": record.id,
            "key": record.key,
            "version": record.version,
            "created_at": record.created_at,
            "created_by": record.created_by,
            "updated_at": record.updated_at,
            "updated_by": record.updated_by,
            # This is the SECOND builder of a record document -- ``_project``
            # duplicates ``serializers.record_doc``'s key list because it also narrows ``data``
            # to the projection (FR-M8). A key added to one and not the other is invisible on
            # the write responses and absent from every query result, and the query path is
            # what the ``By`` column reads from.
            "updated_by_agent_label_id": record.updated_by_agent_label_id,
            "deleted_at": record.deleted_at,
            "comment_count": record.comment_count,
            "last_comment_at": record.last_comment_at,
            "data": data,
        }
