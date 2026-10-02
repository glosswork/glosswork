"""SQLite implementation of the repository interfaces (DD-1, DD-2).

The only modules that may contain raw SQL are this one, ``sqlexpr`` (index DDL and
json_extract text), and ``compiler`` (filter compilation). Everything above works
with value objects.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Collection
from pathlib import Path
from typing import Any

import sqlite_vec
from sqlalchemy import Connection, text

from glosswork.repositories.models import (
    AccessTokenRow,
    AgentLabelRow,
    AttachmentRow,
    AuditEvent,
    CommentRow,
    EmbeddingJobRow,
    EmbeddingRow,
    EmbeddingSyncResult,
    FailedJobRow,
    FieldDef,
    GrantRow,
    IndexCounts,
    IndexSource,
    InviteRow,
    KeywordHit,
    LinkRow,
    ObjectType,
    PrincipalRow,
    Proposal,
    RecordRow,
    SavedViewRow,
    SessionRow,
    SignInCodeRow,
    VectorHit,
    VectorPool,
)
from glosswork.sqlexpr import UUID_PATTERN, json_field_expr


def _dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _dump_opt(value: Any) -> str | None:
    return None if value is None else _dump(value)


def _load_opt(value: str | None) -> Any:
    return None if value is None else json.loads(value)


def _escape_like(value: str) -> str:
    """Make ``value`` match itself literally under ``LIKE ... ESCAPE '\\'``.

    ``%`` and ``_`` are ``LIKE``'s two wildcards and ``\\`` is the escape character
    chosen at the call site, so all three have to be escaped, the backslash first.
    """
    for char in ("\\", "%", "_"):
        value = value.replace(char, "\\" + char)
    return value


def _set_clause(changes: dict[str, Any], allowed: frozenset[str]) -> str:
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"refusing to update unexpected columns: {sorted(unknown)}")
    return ", ".join(f"{col} = :{col}" for col in changes)


class SqliteSchemaRepository:
    _TYPE_COLUMNS = frozenset(
        {
            "name",
            "name_plural",
            "description",
            "icon",
            "is_deleted",
            "default_level",
            "display_field_key",
            "updated_at",
            "updated_by",
        }
    )
    _FIELD_COLUMNS = frozenset(
        {
            "name",
            "description",
            "type",
            "position",
            "is_required",
            "is_unique",
            "is_indexed",
            "embed",
            "default_value",
            "config",
            "is_deleted",
            "updated_at",
            "updated_by",
        }
    )
    _PROPOSAL_COLUMNS = frozenset(
        {"status", "impact", "snapshot_ref", "decided_at", "decided_by", "decision_note"}
    )

    def list_object_types(self, conn: Connection) -> list[ObjectType]:
        rows = conn.execute(
            text("SELECT * FROM object_types WHERE is_deleted = 0 ORDER BY key")
        ).mappings()
        return [self._object_type(dict(r)) for r in rows]

    def get_object_type_by_key(self, conn: Connection, key: str) -> ObjectType | None:
        row = (
            conn.execute(
                text("SELECT * FROM object_types WHERE key = :key AND is_deleted = 0"),
                {"key": key},
            )
            .mappings()
            .first()
        )
        return None if row is None else self._object_type(dict(row))

    def get_object_type_by_id(self, conn: Connection, type_id: str) -> ObjectType | None:
        row = (
            conn.execute(text("SELECT * FROM object_types WHERE id = :id"), {"id": type_id})
            .mappings()
            .first()
        )
        return None if row is None else self._object_type(dict(row))

    def insert_object_type(self, conn: Connection, row: ObjectType) -> None:
        conn.execute(
            text(
                "INSERT INTO object_types (id, key, name, name_plural, description, "
                "key_prefix, key_counter, icon, is_deleted, default_level, "
                "display_field_key, created_at, "
                "created_by, updated_at, updated_by) VALUES (:id, :key, :name, "
                ":name_plural, :description, :key_prefix, :key_counter, :icon, "
                ":is_deleted, :default_level, :display_field_key, :created_at, "
                ":created_by, :updated_at, "
                ":updated_by)"
            ),
            {
                "id": row.id,
                "key": row.key,
                "name": row.name,
                "name_plural": row.name_plural,
                "description": row.description,
                "key_prefix": row.key_prefix,
                "key_counter": row.key_counter,
                "icon": row.icon,
                "is_deleted": int(row.is_deleted),
                "default_level": row.default_level,
                "display_field_key": row.display_field_key,
                "created_at": row.created_at,
                "created_by": row.created_by,
                "updated_at": row.updated_at,
                "updated_by": row.updated_by,
            },
        )

    def update_object_type_row(
        self, conn: Connection, type_id: str, changes: dict[str, Any]
    ) -> None:
        clause = _set_clause(changes, self._TYPE_COLUMNS)
        conn.execute(
            text(f"UPDATE object_types SET {clause} WHERE id = :__id"),
            {**changes, "__id": type_id},
        )

    def allocate_key_seq(self, conn: Connection, type_id: str) -> int:
        row = conn.execute(
            text(
                "UPDATE object_types SET key_counter = key_counter + 1 "
                "WHERE id = :id RETURNING key_counter"
            ),
            {"id": type_id},
        ).first()
        if row is None:
            raise ValueError(f"object type {type_id} not found for key allocation")
        return int(row[0])

    def list_fields(self, conn: Connection, type_id: str) -> list[FieldDef]:
        rows = conn.execute(
            text(
                "SELECT * FROM fields WHERE object_type_id = :tid AND is_deleted = 0 "
                "ORDER BY position, key"
            ),
            {"tid": type_id},
        ).mappings()
        return [self._field(dict(r)) for r in rows]

    def get_field(self, conn: Connection, type_id: str, key: str) -> FieldDef | None:
        row = (
            conn.execute(
                text(
                    "SELECT * FROM fields WHERE object_type_id = :tid AND key = :key "
                    "AND is_deleted = 0"
                ),
                {"tid": type_id, "key": key},
            )
            .mappings()
            .first()
        )
        return None if row is None else self._field(dict(row))

    def get_field_by_id(self, conn: Connection, field_id: str) -> FieldDef | None:
        row = (
            conn.execute(text("SELECT * FROM fields WHERE id = :id"), {"id": field_id})
            .mappings()
            .first()
        )
        return None if row is None else self._field(dict(row))

    def insert_field(self, conn: Connection, row: FieldDef) -> None:
        conn.execute(
            text(
                "INSERT INTO fields (id, object_type_id, key, name, description, type, "
                "position, is_required, is_unique, is_indexed, embed, default_value, "
                "config, is_deleted, created_at, created_by, updated_at, updated_by) "
                "VALUES (:id, :object_type_id, :key, :name, :description, :type, "
                ":position, :is_required, :is_unique, :is_indexed, :embed, "
                ":default_value, :config, :is_deleted, :created_at, :created_by, "
                ":updated_at, :updated_by)"
            ),
            {
                "id": row.id,
                "object_type_id": row.object_type_id,
                "key": row.key,
                "name": row.name,
                "description": row.description,
                "type": row.type,
                "position": row.position,
                "is_required": int(row.is_required),
                "is_unique": int(row.is_unique),
                "is_indexed": int(row.is_indexed),
                "embed": int(row.embed),
                "default_value": _dump_opt(row.default_value),
                "config": _dump(row.config),
                "is_deleted": int(row.is_deleted),
                "created_at": row.created_at,
                "created_by": row.created_by,
                "updated_at": row.updated_at,
                "updated_by": row.updated_by,
            },
        )

    def update_field_row(self, conn: Connection, field_id: str, changes: dict[str, Any]) -> None:
        payload = dict(changes)
        if "config" in payload:
            payload["config"] = _dump(payload["config"])
        if "default_value" in payload:
            payload["default_value"] = _dump_opt(payload["default_value"])
        for flag in ("is_required", "is_unique", "is_indexed", "embed", "is_deleted"):
            if flag in payload:
                payload[flag] = int(payload[flag])
        clause = _set_clause(payload, self._FIELD_COLUMNS)
        conn.execute(
            text(f"UPDATE fields SET {clause} WHERE id = :__id"),
            {**payload, "__id": field_id},
        )

    def next_field_position(self, conn: Connection, type_id: str) -> int:
        row = conn.execute(
            text(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM fields "
                "WHERE object_type_id = :tid AND is_deleted = 0"
            ),
            {"tid": type_id},
        ).first()
        return int(row[0]) if row is not None else 0

    def insert_proposal(self, conn: Connection, row: Proposal) -> None:
        conn.execute(
            text(
                "INSERT INTO schema_proposals (id, status, change_type, target_type_id, "
                "target_field_id, payload, impact, snapshot_ref, reason, proposed_at, "
                "proposed_by, proposed_agent, decided_at, decided_by, decision_note) "
                "VALUES (:id, :status, :change_type, :target_type_id, :target_field_id, "
                ":payload, :impact, :snapshot_ref, :reason, :proposed_at, :proposed_by, "
                ":proposed_agent, :decided_at, :decided_by, :decision_note)"
            ),
            {
                "id": row.id,
                "status": row.status,
                "change_type": row.change_type,
                "target_type_id": row.target_type_id,
                "target_field_id": row.target_field_id,
                "payload": _dump(row.payload),
                "impact": _dump(row.impact),
                "snapshot_ref": row.snapshot_ref,
                "reason": row.reason,
                "proposed_at": row.proposed_at,
                "proposed_by": row.proposed_by,
                "proposed_agent": row.proposed_agent,
                "decided_at": row.decided_at,
                "decided_by": row.decided_by,
                "decision_note": row.decision_note,
            },
        )

    def get_proposal(self, conn: Connection, proposal_id: str) -> Proposal | None:
        row = (
            conn.execute(text("SELECT * FROM schema_proposals WHERE id = :id"), {"id": proposal_id})
            .mappings()
            .first()
        )
        return None if row is None else self._proposal(dict(row))

    def _proposal_where(
        self,
        status: str | None,
        type_ids: Collection[str] | None,
    ) -> tuple[list[str], dict[str, Any]]:
        """The clauses shared by the page and its count, built once.

        ``type_ids`` is the authorization filter, and it is **in the SQL rather than applied to
        the rows afterwards**. Filtering a page in Python after ``LIMIT`` returns
        short pages and a ``total_count`` that counts proposals the caller may not see, which is
        both a wrong number and a disclosure. An empty collection means "nothing is visible" and
        must produce no rows -- distinct from ``None``, which means "do not filter at all".
        """
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if status is not None:
            clauses.append("status = :status")
            params["status"] = status
        if type_ids is not None:
            ids = list(type_ids)
            if not ids:
                clauses.append("1 = 0")
            else:
                names = [f"t{i}" for i in range(len(ids))]
                clauses.append(f"target_type_id IN ({', '.join(':' + n for n in names)})")
                params.update(dict(zip(names, ids, strict=True)))
        return clauses, params

    def list_proposals(
        self,
        conn: Connection,
        status: str | None = None,
        *,
        type_ids: Collection[str] | None = None,
        after: tuple[str, str] | None = None,
        limit: int | None = None,
    ) -> list[Proposal]:
        """Newest first, keyset-paginated over ``(proposed_at, id)``.

        **The secondary key is load-bearing, not decoration**. ``proposed_at`` is
        second-precision, so ``ORDER BY proposed_at`` alone leaves two proposals raised in the
        same second in no defined order -- which makes "newest first" unassertable and a keyset
        cursor unsound, since a boundary row could be yielded twice or skipped.
        """
        clauses, params = self._proposal_where(status, type_ids)
        if after is not None:
            # Strictly past the boundary, in the same DESC order the sort declares.
            clauses.append("(proposed_at < :a_at OR (proposed_at = :a_at AND id < :a_id))")
            params["a_at"], params["a_id"] = after
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM schema_proposals{where} ORDER BY proposed_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        rows = conn.execute(text(sql), params).mappings()
        return [self._proposal(dict(r)) for r in rows]

    def count_proposals(
        self,
        conn: Connection,
        status: str | None = None,
        *,
        type_ids: Collection[str] | None = None,
    ) -> int:
        """How many the caller could reach in total, under the same filters as the page.

        Separate from the page because the badge needs the total while the page stays bounded,
        and a count taken from a page's length silently caps at the page size.
        """
        clauses, params = self._proposal_where(status, type_ids)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        total = conn.execute(text(f"SELECT COUNT(*) FROM schema_proposals{where}"), params).scalar()
        return int(total or 0)

    def update_proposal_row(
        self, conn: Connection, proposal_id: str, changes: dict[str, Any]
    ) -> None:
        payload = dict(changes)
        if "impact" in payload:
            payload["impact"] = _dump(payload["impact"])
        clause = _set_clause(payload, self._PROPOSAL_COLUMNS)
        conn.execute(
            text(f"UPDATE schema_proposals SET {clause} WHERE id = :__id"),
            {**payload, "__id": proposal_id},
        )

    def execute_index_ddl(self, conn: Connection, ddl: str) -> None:
        conn.exec_driver_sql(ddl)

    def list_index_names(self, conn: Connection, prefix: str) -> list[str]:
        """Index names on ``records`` beginning **literally** with ``prefix``.

        The reconcilers need to know what is actually on disk, not what the schema
        implies should be: an index left behind by a field that was deleted while a
        prior version was running, or by a prior version's naming scheme, is exactly the
        drift a declarative reconcile exists to clear. Reading ``sqlite_master`` is the
        only way to ask.

        **The prefix is escaped, and that is not cosmetic.** In SQL ``LIKE``,
        ``_`` is a single-character wildcard, and every prefix passed here ends in one.
        Unescaped, ``'ix_rec_%'`` matches ``ix_records_type_live`` and
        ``ix_records_updated`` -- both indexes on ``records``, created by migration 1,
        and both depended on by every query scope. The retirement sweep lists by
        exactly that prefix and drops what it finds, so without the escape the first
        start after the upgrade would drop the two base indexes.
        """
        rows = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'records' "
                "AND name LIKE :prefix ESCAPE '\\' ORDER BY name"
            ),
            {"prefix": f"{_escape_like(prefix)}%"},
        ).all()
        return [str(row[0]) for row in rows]

    @staticmethod
    def _object_type(row: dict[str, Any]) -> ObjectType:
        return ObjectType(
            id=row["id"],
            key=row["key"],
            name=row["name"],
            name_plural=row["name_plural"],
            description=row["description"],
            key_prefix=row["key_prefix"],
            key_counter=row["key_counter"],
            icon=row["icon"],
            is_deleted=bool(row["is_deleted"]),
            created_at=row["created_at"],
            created_by=row["created_by"],
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
            default_level=row["default_level"],
            display_field_key=row["display_field_key"],
        )

    @staticmethod
    def _field(row: dict[str, Any]) -> FieldDef:
        return FieldDef(
            id=row["id"],
            object_type_id=row["object_type_id"],
            key=row["key"],
            name=row["name"],
            description=row["description"],
            type=row["type"],
            position=row["position"],
            is_required=bool(row["is_required"]),
            is_unique=bool(row["is_unique"]),
            is_indexed=bool(row["is_indexed"]),
            embed=bool(row["embed"]),
            default_value=_load_opt(row["default_value"]),
            config=json.loads(row["config"]),
            is_deleted=bool(row["is_deleted"]),
            created_at=row["created_at"],
            created_by=row["created_by"],
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
        )

    @staticmethod
    def _proposal(row: dict[str, Any]) -> Proposal:
        return Proposal(
            id=row["id"],
            status=row["status"],
            change_type=row["change_type"],
            target_type_id=row["target_type_id"],
            target_field_id=row["target_field_id"],
            payload=json.loads(row["payload"]),
            impact=json.loads(row["impact"]),
            snapshot_ref=row["snapshot_ref"],
            reason=row["reason"],
            proposed_at=row["proposed_at"],
            proposed_by=row["proposed_by"],
            proposed_agent=row["proposed_agent"],
            decided_at=row["decided_at"],
            decided_by=row["decided_by"],
            decision_note=row["decision_note"],
        )


class SqliteRecordRepository:
    _RECORD_COLUMNS = frozenset(
        {
            "data",
            "version",
            "updated_at",
            "updated_by",
            # ``_set_clause`` raises on any column outside this set, so the write
            # path cannot reach a column until it is named here.
            "updated_by_agent_label_id",
            "deleted_at",
            "deleted_by",
            "comment_count",
            "last_comment_at",
        }
    )

    def insert_record(self, conn: Connection, row: RecordRow) -> None:
        conn.execute(
            text(
                "INSERT INTO records (id, object_type_id, key, key_seq, version, data, "
                "created_at, created_by, updated_at, updated_by, updated_by_agent_label_id, "
                "deleted_at, deleted_by, comment_count, last_comment_at) VALUES "
                "(:id, :object_type_id, :key, :key_seq, :version, :data, :created_at, "
                ":created_by, :updated_at, :updated_by, :updated_by_agent_label_id, "
                ":deleted_at, :deleted_by, :comment_count, :last_comment_at)"
            ),
            {
                "id": row.id,
                "object_type_id": row.object_type_id,
                "key": row.key,
                "key_seq": row.key_seq,
                "version": row.version,
                "data": _dump(row.data),
                "created_at": row.created_at,
                "created_by": row.created_by,
                "updated_at": row.updated_at,
                "updated_by": row.updated_by,
                "updated_by_agent_label_id": row.updated_by_agent_label_id,
                "deleted_at": row.deleted_at,
                "deleted_by": row.deleted_by,
                "comment_count": row.comment_count,
                "last_comment_at": row.last_comment_at,
            },
        )

    def get_record(
        self, conn: Connection, ref: str, include_deleted: bool = False
    ) -> RecordRow | None:
        column = "id" if UUID_PATTERN.match(ref) else "key"
        sql = f"SELECT * FROM records WHERE {column} = :ref"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        row = conn.execute(text(sql), {"ref": ref}).mappings().first()
        return None if row is None else self._record(dict(row))

    def update_record_row(self, conn: Connection, record_id: str, changes: dict[str, Any]) -> None:
        payload = dict(changes)
        if "data" in payload:
            payload["data"] = _dump(payload["data"])
        clause = _set_clause(payload, self._RECORD_COLUMNS)
        conn.execute(
            text(f"UPDATE records SET {clause} WHERE id = :__id"),
            {**payload, "__id": record_id},
        )

    def run_query(
        self, conn: Connection, sql: str, params: dict[str, Any]
    ) -> list[tuple[RecordRow, dict[str, Any]]]:
        results: list[tuple[RecordRow, dict[str, Any]]] = []
        for mapping in conn.execute(text(sql), params).mappings():
            row = dict(mapping)
            extras = {k: v for k, v in row.items() if k.startswith("__")}
            results.append((self._record(row), extras))
        return results

    def run_count(self, conn: Connection, sql: str, params: dict[str, Any]) -> int:
        row = conn.execute(text(sql), params).first()
        return int(row[0]) if row is not None else 0

    def records_for_type(
        self, conn: Connection, type_id: str, include_deleted: bool = False
    ) -> list[RecordRow]:
        sql = "SELECT * FROM records WHERE object_type_id = :tid"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY key_seq"
        rows = conn.execute(text(sql), {"tid": type_id}).mappings()
        return [self._record(dict(r)) for r in rows]

    def records_for_type_page(
        self, conn: Connection, type_id: str, after_id: str | None, limit: int
    ) -> list[RecordRow]:
        """One page of live records of a type, ordered by ``id``.

        Keyset-paged on the primary key rather than offset-paged, because the caller is
        the schema-change fan-out, which commits between pages: rows inserted by other
        writers while it runs would shift an OFFSET window and make it skip records.
        Ordering by ``id`` also means a record created mid-fan-out is either ahead of
        the cursor and gets indexed here, or behind it and was already indexed by its
        own write path, never neither.
        """
        sql = "SELECT * FROM records WHERE object_type_id = :t AND deleted_at IS NULL"
        params: dict[str, Any] = {"t": type_id, "limit": limit}
        if after_id is not None:
            sql += " AND id > :after"
            params["after"] = after_id
        sql += " ORDER BY id LIMIT :limit"
        rows = conn.execute(text(sql), params).mappings().all()
        return [self._record(dict(row)) for row in rows]

    def find_unique_collision(
        self,
        conn: Connection,
        type_id: str,
        field_key: str,
        value: Any,
        exclude_record_id: str | None = None,
    ) -> str | None:
        """Key of a live record of this type already holding ``value`` in the field,
        or None. The unique partial expression index remains the hard enforcement;
        this pre-check exists to produce an error naming the field."""
        sql = (
            f"SELECT key FROM records WHERE object_type_id = :tid AND deleted_at IS NULL "
            f"AND {json_field_expr(field_key)} = :value"
        )
        params: dict[str, Any] = {"tid": type_id, "value": value}
        if exclude_record_id is not None:
            sql += " AND id != :exclude"
            params["exclude"] = exclude_record_id
        row = conn.execute(text(sql), params).first()
        return None if row is None else str(row[0])

    def recent_field_values(
        self, conn: Connection, type_id: str, field_key: str, limit: int
    ) -> list[Any]:
        """Values of one field from the most recently updated live records that
        hold it, newest first. Backs ``describe_object_type(include_samples=true)``;
        the service de-duplicates and truncates."""
        expr = json_field_expr(field_key)
        rows = conn.execute(
            text(
                f"SELECT data FROM records WHERE object_type_id = :tid AND deleted_at IS NULL "
                f"AND {expr} IS NOT NULL ORDER BY updated_at DESC, rowid DESC LIMIT :limit"
            ),
            {"tid": type_id, "limit": limit},
        )
        return [json.loads(r[0])[field_key] for r in rows]

    def remove_field_key_from_type(self, conn: Connection, type_id: str, field_key: str) -> None:
        conn.execute(
            text(
                "UPDATE records SET data = json_remove(data, :path) "
                "WHERE object_type_id = :tid AND "
                f"{json_field_expr(field_key)} IS NOT NULL"
            ),
            {"path": f"$.{field_key}", "tid": type_id},
        )

    def soft_delete_all_for_type(
        self, conn: Connection, type_id: str, deleted_at: str, deleted_by: str
    ) -> None:
        conn.execute(
            text(
                "UPDATE records SET deleted_at = :at, deleted_by = :by "
                "WHERE object_type_id = :tid AND deleted_at IS NULL"
            ),
            {"at": deleted_at, "by": deleted_by, "tid": type_id},
        )

    def insert_link(self, conn: Connection, row: LinkRow) -> None:
        conn.execute(
            text(
                "INSERT INTO record_links (id, field_id, from_record_id, to_record_id, "
                "position, created_at, created_by) VALUES (:id, :field_id, "
                ":from_record_id, :to_record_id, :position, :created_at, :created_by)"
            ),
            {
                "id": row.id,
                "field_id": row.field_id,
                "from_record_id": row.from_record_id,
                "to_record_id": row.to_record_id,
                "position": row.position,
                "created_at": row.created_at,
                "created_by": row.created_by,
            },
        )

    def delete_link_row(self, conn: Connection, link_id: str) -> None:
        conn.execute(text("DELETE FROM record_links WHERE id = :id"), {"id": link_id})

    def get_link(
        self, conn: Connection, field_id: str, from_record_id: str, to_record_id: str
    ) -> LinkRow | None:
        row = (
            conn.execute(
                text(
                    "SELECT * FROM record_links WHERE field_id = :f AND "
                    "from_record_id = :fr AND to_record_id = :to"
                ),
                {"f": field_id, "fr": from_record_id, "to": to_record_id},
            )
            .mappings()
            .first()
        )
        return None if row is None else self._link(dict(row))

    def links_from(
        self, conn: Connection, from_record_id: str, field_id: str | None = None
    ) -> list[LinkRow]:
        sql = "SELECT * FROM record_links WHERE from_record_id = :fr"
        params: dict[str, Any] = {"fr": from_record_id}
        if field_id is not None:
            sql += " AND field_id = :f"
            params["f"] = field_id
        sql += " ORDER BY position, created_at"
        rows = conn.execute(text(sql), params).mappings()
        return [self._link(dict(r)) for r in rows]

    def linked_keys_for_records(
        self, conn: Connection, from_record_ids: list[str], field_id: str
    ) -> dict[str, list[str]]:
        """Target record *keys* per source record, in link order, for one relation field.

        One query for a whole page instead of ``links_from`` per record followed by
        ``get_record`` per link. The join is what collapses the inner
        fan-out: returning ``LinkRow``s here would have made the outer read batched and
        left the per-target fetch exactly as it was, which is the failure this method
        exists to avoid.

        Deleted targets are included, matching ``list_links``, which reads them with
        ``include_deleted=True``: a CSV export names what the link points at, and a link
        to a soft-deleted record is still a link.
        """
        if not from_record_ids:
            return {}
        placeholders = ", ".join(f":r{i}" for i in range(len(from_record_ids)))
        params: dict[str, Any] = {f"r{i}": rid for i, rid in enumerate(from_record_ids)}
        params["f"] = field_id
        rows = conn.execute(
            text(
                "SELECT rl.from_record_id AS src, r.key AS target_key "
                "FROM record_links rl JOIN records r ON r.id = rl.to_record_id "
                f"WHERE rl.field_id = :f AND rl.from_record_id IN ({placeholders}) "
                "ORDER BY rl.position, rl.created_at"
            ),
            params,
        ).mappings()
        grouped: dict[str, list[str]] = {rid: [] for rid in from_record_ids}
        for row in rows:
            grouped[row["src"]].append(row["target_key"])
        return grouped

    def links_to(self, conn: Connection, to_record_id: str) -> list[LinkRow]:
        rows = conn.execute(
            text("SELECT * FROM record_links WHERE to_record_id = :to ORDER BY created_at"),
            {"to": to_record_id},
        ).mappings()
        return [self._link(dict(r)) for r in rows]

    def next_link_position(self, conn: Connection, field_id: str, from_record_id: str) -> int:
        row = conn.execute(
            text(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM record_links "
                "WHERE field_id = :f AND from_record_id = :fr"
            ),
            {"f": field_id, "fr": from_record_id},
        ).first()
        return int(row[0]) if row is not None else 0

    @staticmethod
    def _record(row: dict[str, Any]) -> RecordRow:
        return RecordRow(
            id=row["id"],
            object_type_id=row["object_type_id"],
            key=row["key"],
            key_seq=row["key_seq"],
            version=row["version"],
            data=json.loads(row["data"]),
            created_at=row["created_at"],
            created_by=row["created_by"],
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
            updated_by_agent_label_id=row["updated_by_agent_label_id"],
            deleted_at=row["deleted_at"],
            deleted_by=row["deleted_by"],
            comment_count=row["comment_count"],
            last_comment_at=row["last_comment_at"],
        )

    @staticmethod
    def _link(row: dict[str, Any]) -> LinkRow:
        return LinkRow(
            id=row["id"],
            field_id=row["field_id"],
            from_record_id=row["from_record_id"],
            to_record_id=row["to_record_id"],
            position=row["position"],
            created_at=row["created_at"],
            created_by=row["created_by"],
        )


class SqliteCommentRepository:
    _COMMENT_COLUMNS = frozenset({"body", "updated_at", "edited", "deleted_at", "deleted_by"})

    # DD-25: the author's human label is resolved in the same query that reads the row, so
    # `comment_doc` stays a pure row-to-dict function and one page of comments stays one
    # query. LEFT, so a comment whose author has been deleted still returns.
    _SELECT = (
        "SELECT c.*, p.display_name AS principal_display_name, a.label AS agent_label "
        "FROM comments c "
        "LEFT JOIN principals p ON p.id = c.author_id "
        # The agent label's text, on the same terms. LEFT because most comments have no
        # label at all, not merely because a referent might be gone.
        "LEFT JOIN agent_labels a ON a.id = c.agent_label_id"
    )

    def insert_comment(self, conn: Connection, row: CommentRow) -> None:
        conn.execute(
            text(
                "INSERT INTO comments (id, record_id, body, author_id, agent_label_id, "
                "created_at, updated_at, edited, deleted_at, deleted_by) VALUES "
                "(:id, :record_id, :body, :author_id, :agent_label_id, :created_at, "
                ":updated_at, :edited, :deleted_at, :deleted_by)"
            ),
            {
                "id": row.id,
                "record_id": row.record_id,
                "body": row.body,
                "author_id": row.author_id,
                "agent_label_id": row.agent_label_id,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "edited": int(row.edited),
                "deleted_at": row.deleted_at,
                "deleted_by": row.deleted_by,
            },
        )

    def get_comment(self, conn: Connection, comment_id: str) -> CommentRow | None:
        row = (
            conn.execute(text(f"{self._SELECT} WHERE c.id = :id"), {"id": comment_id})
            .mappings()
            .first()
        )
        return None if row is None else self._comment(dict(row))

    def update_comment_row(
        self, conn: Connection, comment_id: str, changes: dict[str, Any]
    ) -> None:
        payload = dict(changes)
        if "edited" in payload:
            payload["edited"] = int(payload["edited"])
        clause = _set_clause(payload, self._COMMENT_COLUMNS)
        conn.execute(
            text(f"UPDATE comments SET {clause} WHERE id = :__id"),
            {**payload, "__id": comment_id},
        )

    def list_for_record(
        self,
        conn: Connection,
        record_id: str,
        include_deleted: bool = False,
        after: tuple[str, str] | None = None,
        limit: int | None = None,
    ) -> list[CommentRow]:
        sql = f"{self._SELECT} WHERE c.record_id = :rid"
        params: dict[str, Any] = {"rid": record_id}
        if not include_deleted:
            sql += " AND c.deleted_at IS NULL"
        if after is not None:
            # Keyset boundary over the same (created_at, rowid) order used below,
            # resolved from the boundary comment's id so the cursor stays opaque.
            after_created_at, after_id = after
            sql += (
                " AND (c.created_at > :after_created_at OR (c.created_at = :after_created_at "
                "AND c.rowid > (SELECT rowid FROM comments WHERE id = :after_id)))"
            )
            params["after_created_at"] = after_created_at
            params["after_id"] = after_id
        # Tie-break on rowid, not the UUID `id`: timestamps are second-precision
        # (docs/DATA_MODEL.md section 12), so same-second comments are common, and
        # a UUID has no relationship to creation order. `comments` has no `WITHOUT
        # ROWID` clause, so SQLite's implicit rowid tracks true insertion order.
        sql += " ORDER BY c.created_at, c.rowid"
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        rows = conn.execute(text(sql), params).mappings()
        return [self._comment(dict(r)) for r in rows]

    def live_stats(self, conn: Connection, record_id: str) -> tuple[int, str | None]:
        row = conn.execute(
            text(
                "SELECT COUNT(*), MAX(created_at) FROM comments "
                "WHERE record_id = :rid AND deleted_at IS NULL"
            ),
            {"rid": record_id},
        ).first()
        if row is None:
            return 0, None
        return int(row[0]), row[1]

    @staticmethod
    def _comment(row: dict[str, Any]) -> CommentRow:
        return CommentRow(
            id=row["id"],
            record_id=row["record_id"],
            body=row["body"],
            author_id=row["author_id"],
            agent_label_id=row["agent_label_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            edited=bool(row["edited"]),
            deleted_at=row["deleted_at"],
            deleted_by=row["deleted_by"],
            principal_display_name=row.get("principal_display_name"),
            agent_label=row.get("agent_label"),
        )


class SqliteSavedViewRepository:
    """``saved_views`` (docs/DATA_MODEL.md section 11, FR-U3). Views are shared
    across all principals at MVP; the DB's partial unique index on
    ``(object_type_id) WHERE is_default = 1`` is the hard enforcement that at most
    one view per object type is default. ``clear_default_for_type`` exists so a
    service can unset every other default within the same write transaction that
    sets a new one, since the index would otherwise reject a second default row."""

    _VIEW_COLUMNS = frozenset(
        {"name", "description", "mode", "config", "is_default", "updated_at", "updated_by"}
    )

    def insert(self, conn: Connection, row: SavedViewRow) -> None:
        conn.execute(
            text(
                "INSERT INTO saved_views (id, object_type_id, name, description, mode, "
                "config, is_default, created_at, created_by, updated_at, updated_by) "
                "VALUES (:id, :object_type_id, :name, :description, :mode, :config, "
                ":is_default, :created_at, :created_by, :updated_at, :updated_by)"
            ),
            {
                "id": row.id,
                "object_type_id": row.object_type_id,
                "name": row.name,
                "description": row.description,
                "mode": row.mode,
                "config": _dump(row.config),
                "is_default": int(row.is_default),
                "created_at": row.created_at,
                "created_by": row.created_by,
                "updated_at": row.updated_at,
                "updated_by": row.updated_by,
            },
        )

    def get(self, conn: Connection, view_id: str) -> SavedViewRow | None:
        row = (
            conn.execute(text("SELECT * FROM saved_views WHERE id = :id"), {"id": view_id})
            .mappings()
            .first()
        )
        return None if row is None else self._view(dict(row))

    def list_for_type(self, conn: Connection, object_type_id: str) -> list[SavedViewRow]:
        rows = conn.execute(
            text(
                "SELECT * FROM saved_views WHERE object_type_id = :tid ORDER BY created_at, rowid"
            ),
            {"tid": object_type_id},
        ).mappings()
        return [self._view(dict(r)) for r in rows]

    def update_row(self, conn: Connection, view_id: str, changes: dict[str, Any]) -> None:
        payload = dict(changes)
        if "config" in payload:
            payload["config"] = _dump(payload["config"])
        if "is_default" in payload:
            payload["is_default"] = int(payload["is_default"])
        clause = _set_clause(payload, self._VIEW_COLUMNS)
        conn.execute(
            text(f"UPDATE saved_views SET {clause} WHERE id = :__id"),
            {**payload, "__id": view_id},
        )

    def delete(self, conn: Connection, view_id: str) -> None:
        conn.execute(text("DELETE FROM saved_views WHERE id = :id"), {"id": view_id})

    def clear_default_for_type(
        self, conn: Connection, object_type_id: str, except_id: str | None = None
    ) -> None:
        sql = "UPDATE saved_views SET is_default = 0 WHERE object_type_id = :tid"
        params: dict[str, Any] = {"tid": object_type_id}
        if except_id is not None:
            sql += " AND id != :except_id"
            params["except_id"] = except_id
        conn.execute(text(sql), params)

    @staticmethod
    def _view(row: dict[str, Any]) -> SavedViewRow:
        return SavedViewRow(
            id=row["id"],
            object_type_id=row["object_type_id"],
            name=row["name"],
            description=row["description"],
            mode=row["mode"],
            config=json.loads(row["config"]),
            is_default=bool(row["is_default"]),
            created_at=row["created_at"],
            created_by=row["created_by"],
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
        )


class SqliteAgentLabelRepository:
    """``agent_labels`` (docs/DATA_MODEL.md, FR-I6): one row per (principal, label)."""

    def get_label(self, conn: Connection, label_id: str) -> AgentLabelRow | None:
        row = (
            conn.execute(text("SELECT * FROM agent_labels WHERE id = :id"), {"id": label_id})
            .mappings()
            .first()
        )
        return None if row is None else self._label(dict(row))

    def labels_by_ids(self, conn: Connection, ids: list[str]) -> list[AgentLabelRow]:
        """Every label named by ``ids``, in one statement (mirroring
        ``principals_by_ids``).

        The read behind the ``agent_labels`` sidecar: one call per response document, not one
        per record. Before this there was no batched label read at all -- only lookup by id,
        lookup by principal-plus-label, and ``list_labels``, which with no ``principal_id``
        returns every label in the deployment. Building the sidecar on that would have been an
        unbounded read, which DD-18 forbids.

        Unknown ids are simply absent, exactly as in ``principals_by_ids``: an unresolved id is
        a rendering fallback on the client, not an error here.
        """
        if not ids:
            return []
        unique = list(dict.fromkeys(ids))
        placeholders = ", ".join(f":id{i}" for i in range(len(unique)))
        params = {f"id{i}": value for i, value in enumerate(unique)}
        rows = conn.execute(
            text(f"SELECT * FROM agent_labels WHERE id IN ({placeholders})"), params
        ).mappings()
        return [self._label(dict(r)) for r in rows]

    def find_label(self, conn: Connection, principal_id: str, label: str) -> AgentLabelRow | None:
        row = (
            conn.execute(
                text("SELECT * FROM agent_labels WHERE principal_id = :pid AND label = :label"),
                {"pid": principal_id, "label": label},
            )
            .mappings()
            .first()
        )
        return None if row is None else self._label(dict(row))

    def insert_label(self, conn: Connection, row: AgentLabelRow) -> None:
        conn.execute(
            text(
                "INSERT INTO agent_labels (id, principal_id, label, display_name, description, "
                "verified, first_seen_at, last_seen_at, call_count) VALUES (:id, :principal_id, "
                ":label, :display_name, :description, :verified, :first_seen_at, :last_seen_at, "
                ":call_count)"
            ),
            {
                "id": row.id,
                "principal_id": row.principal_id,
                "label": row.label,
                "display_name": row.display_name,
                "description": row.description,
                "verified": int(row.verified),
                "first_seen_at": row.first_seen_at,
                "last_seen_at": row.last_seen_at,
                "call_count": row.call_count,
            },
        )

    def record_use(self, conn: Connection, label_id: str, seen_at: str) -> None:
        conn.execute(
            text(
                "UPDATE agent_labels SET call_count = call_count + 1, last_seen_at = :seen_at "
                "WHERE id = :id"
            ),
            {"id": label_id, "seen_at": seen_at},
        )

    def list_labels(self, conn: Connection, principal_id: str | None = None) -> list[AgentLabelRow]:
        sql = "SELECT * FROM agent_labels"
        params: dict[str, Any] = {}
        if principal_id is not None:
            sql += " WHERE principal_id = :pid"
            params["pid"] = principal_id
        sql += " ORDER BY principal_id, label"
        rows = conn.execute(text(sql), params).mappings()
        return [self._label(dict(r)) for r in rows]

    def search_labels(
        self,
        conn: Connection,
        q: str | None,
        accessible_type_ids: list[str] | None,
        include_untyped: bool,
        limit: int,
    ) -> list[AgentLabelRow]:
        """The directory read: every label appearing on an audit event this caller
        may read, narrowed by ``q`` and bounded by ``limit``.

        **An ``EXISTS`` over ``audit_events``, not a bare list of the table**, and that is
        the whole point. ``list_labels(None)`` is every label in the deployment;
        ``AuditService.search`` narrows a restricted caller to the object types they hold
        ``read`` on, so a directory built on the former would publish label strings whose
        only activity is on types the caller is closed out of. The restriction is
        :func:`audit_access_clause`, the same one the audit search applies, which is what
        makes "the directory shows you no label your own audit search would hide" a
        property of one predicate rather than of two that have to be kept in step.

        **``q`` and ``LIMIT`` go into SQL**, mirroring :meth:`search_principals` and for
        the same reason (DD-2): agent labels auto-register on first use (FR-I6), so the
        table's size is unbounded in a way ``principals`` is not, and loading it to
        truncate in Python would order the truncation by whatever the table felt like.
        """
        params: dict[str, Any] = {"limit": limit}
        access = audit_access_clause(params, accessible_type_ids, include_untyped)
        clauses = [f"EXISTS (SELECT 1 FROM audit_events e WHERE e.agent_label_id = l.id{access})"]
        if q:
            clauses.append("lower(l.label) LIKE :q")
            params["q"] = f"%{q.strip().lower()}%"
        sql = (
            "SELECT l.* FROM agent_labels l WHERE "
            + " AND ".join(clauses)
            + " ORDER BY l.label COLLATE NOCASE, l.id LIMIT :limit"
        )
        rows = conn.execute(text(sql), params).mappings()
        return [self._label(dict(r)) for r in rows]

    def update_label(
        self,
        conn: Connection,
        label_id: str,
        display_name: str | None,
        description: str | None,
    ) -> None:
        conn.execute(
            text(
                "UPDATE agent_labels SET display_name = :display_name, "
                "description = :description, verified = 1 WHERE id = :id"
            ),
            {"id": label_id, "display_name": display_name, "description": description},
        )

    def count_labels(self, conn: Connection) -> int:
        """Every registered agent label, across every principal: ``agents``,
        for the workspace document. A count, not a list: ``list_labels`` with no
        ``principal_id`` returns every label in the deployment, which is exactly the
        unbounded read DD-18 forbids if a caller had to sum its length instead."""
        row = conn.execute(text("SELECT COUNT(*) FROM agent_labels")).first()
        return 0 if row is None else int(row[0])

    @staticmethod
    def _label(row: dict[str, Any]) -> AgentLabelRow:
        return AgentLabelRow(
            id=row["id"],
            principal_id=row["principal_id"],
            label=row["label"],
            display_name=row["display_name"],
            description=row["description"],
            verified=bool(row["verified"]),
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            call_count=int(row["call_count"]),
        )


class SqliteAttachmentRepository:
    def insert_attachment(self, conn: Connection, row: AttachmentRow) -> None:
        conn.execute(
            text(
                "INSERT INTO attachments (id, sha256, filename, content_type, byte_size, "
                "uploaded_at, uploaded_by) VALUES (:id, :sha256, :filename, :content_type, "
                ":byte_size, :uploaded_at, :uploaded_by)"
            ),
            {
                "id": row.id,
                "sha256": row.sha256,
                "filename": row.filename,
                "content_type": row.content_type,
                "byte_size": row.byte_size,
                "uploaded_at": row.uploaded_at,
                "uploaded_by": row.uploaded_by,
            },
        )

    def get_attachment(self, conn: Connection, attachment_id: str) -> AttachmentRow | None:
        row = (
            conn.execute(text("SELECT * FROM attachments WHERE id = :id"), {"id": attachment_id})
            .mappings()
            .first()
        )
        return None if row is None else self._attachment(dict(row))

    def get_attachments(self, conn: Connection, ids: list[str]) -> list[AttachmentRow]:
        if not ids:
            return []
        placeholders = ", ".join(f":a{i}" for i in range(len(ids)))
        rows = conn.execute(
            text(f"SELECT * FROM attachments WHERE id IN ({placeholders})"),
            {f"a{i}": v for i, v in enumerate(ids)},
        ).mappings()
        return [self._attachment(dict(r)) for r in rows]

    @staticmethod
    def _attachment(row: dict[str, Any]) -> AttachmentRow:
        return AttachmentRow(
            id=row["id"],
            sha256=row["sha256"],
            filename=row["filename"],
            content_type=row["content_type"],
            byte_size=row["byte_size"],
            uploaded_at=row["uploaded_at"],
            uploaded_by=row["uploaded_by"],
        )


def audit_access_clause(
    params: dict[str, Any],
    accessible_type_ids: list[str] | None,
    include_untyped: bool,
) -> str:
    """The audit restriction by object type, as SQL rather than as a post-filter.

    ``None`` means unrestricted (a ``role == 'admin'`` principal). Otherwise rows are
    kept only for the named types, plus -- when ``include_untyped`` -- the rows whose
    ``object_type_id`` is null. Every clause it emits is qualified ``e.``, so a caller
    joining ``audit_events`` must alias it ``e``.

    **That null rule is a fail-closed heuristic, not a guarantee.**
    ``audit_events.object_type_id`` is nullable with no foreign key and is populated by
    whichever call site remembers to; ``services/comments.py`` already writes null when a
    record lookup misses. Hiding those rows from everyone but a system administrator errs
    toward withholding, which is the right direction, but it does not make "typed" and
    "about an object type" the same thing.

    **Two readers**, which is why it is a function and not a method: the audit
    search, and the agent-label directory, whose whole safety property is that it shows a
    caller no label their own audit search would hide.
    """
    if accessible_type_ids is None:
        return ""
    if not accessible_type_ids:
        return " AND 0" if not include_untyped else " AND e.object_type_id IS NULL"
    placeholders = ", ".join(f":a{i}" for i in range(len(accessible_type_ids)))
    params.update({f"a{i}": tid for i, tid in enumerate(accessible_type_ids)})
    clause = f"e.object_type_id IN ({placeholders})"
    if include_untyped:
        clause = f"({clause} OR e.object_type_id IS NULL)"
    else:
        clause = f"({clause})"
    return f" AND {clause}"


class SqliteAuditRepository:
    """Append and read only. There is deliberately no update or delete here; audit
    rows are immutable (FR-D1) and application code never issues UPDATE or DELETE
    against ``audit_events``."""

    # DD-25: an audit envelope's foreign ids are resolved to human labels here, in the same
    # query that reads the row — one query per page rather than N service lookups, and the
    # serializers stay pure row-to-dict functions. Both joins are LEFT: an event whose
    # principal or record has since been deleted still returns, with a null label the
    # frontend renders as the raw id it shows today.
    _SELECT = (
        "SELECT e.*, p.display_name AS principal_display_name, r.key AS record_key, "
        "a.label AS agent_label "
        "FROM audit_events e "
        "LEFT JOIN principals p ON p.id = e.principal_id "
        "LEFT JOIN records r ON r.id = e.record_id "
        # The third join, for the same reason as the first two. An event with no agent
        # label is the ordinary case -- every write by a person has none -- so LEFT here is
        # load-bearing in a way it merely guards against elsewhere.
        "LEFT JOIN agent_labels a ON a.id = e.agent_label_id"
    )

    def append(self, conn: Connection, events: list[AuditEvent]) -> list[int]:
        ids: list[int] = []
        for event in events:
            row = conn.execute(
                text(
                    "INSERT INTO audit_events (ts, request_id, principal_id, "
                    "principal_type, agent_label_id, auth_method, surface, entity_type, "
                    "entity_id, record_id, object_type_id, action, field_key, old_value, "
                    "new_value, note) VALUES (:ts, :request_id, :principal_id, "
                    ":principal_type, :agent_label_id, :auth_method, :surface, "
                    ":entity_type, :entity_id, :record_id, :object_type_id, :action, "
                    ":field_key, :old_value, :new_value, :note) RETURNING id"
                ),
                {
                    "ts": event.ts,
                    "request_id": event.request_id,
                    "principal_id": event.principal_id,
                    "principal_type": event.principal_type,
                    "agent_label_id": event.agent_label_id,
                    "auth_method": event.auth_method,
                    "surface": event.surface,
                    "entity_type": event.entity_type,
                    "entity_id": event.entity_id,
                    "record_id": event.record_id,
                    "object_type_id": event.object_type_id,
                    "action": event.action,
                    "field_key": event.field_key,
                    "old_value": _dump_opt(event.old_value),
                    "new_value": _dump_opt(event.new_value),
                    "note": event.note,
                },
            ).first()
            assert row is not None
            event.id = int(row[0])
            ids.append(event.id)
        return ids

    def for_record(
        self,
        conn: Connection,
        record_id: str,
        field_key: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> list[AuditEvent]:
        sql = f"{self._SELECT} WHERE e.record_id = :rid"
        params: dict[str, Any] = {"rid": record_id}
        if field_key is not None:
            sql += " AND e.field_key = :fk"
            params["fk"] = field_key
        if after_id is not None:
            sql += " AND e.id > :after_id"  # keyset boundary: id is the natural order
            params["after_id"] = after_id
        sql += " ORDER BY e.id"
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        rows = conn.execute(text(sql), params).mappings()
        return [self._event(dict(r)) for r in rows]

    def record_update_events(self, conn: Connection, record_id: str) -> list[AuditEvent]:
        rows = conn.execute(
            text(
                f"{self._SELECT} WHERE e.record_id = :rid AND "
                "e.entity_type = 'record' AND e.action = 'update' ORDER BY e.id"
            ),
            {"rid": record_id},
        ).mappings()
        return [self._event(dict(r)) for r in rows]

    def get_event(self, conn: Connection, event_id: int) -> AuditEvent | None:
        row = (
            conn.execute(text(f"{self._SELECT} WHERE e.id = :id"), {"id": event_id})
            .mappings()
            .first()
        )
        return None if row is None else self._event(dict(row))

    def for_request(self, conn: Connection, request_id: str) -> list[AuditEvent]:
        rows = conn.execute(
            text(f"{self._SELECT} WHERE e.request_id = :rid ORDER BY e.id"),
            {"rid": request_id},
        ).mappings()
        return [self._event(dict(r)) for r in rows]

    def latest_id(self, conn: Connection) -> int:
        row = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM audit_events")).first()
        return int(row[0]) if row is not None else 0

    def since(
        self,
        conn: Connection,
        cursor: int,
        object_type_ids: list[str] | None,
        limit: int,
        accessible_type_ids: list[str] | None = None,
        include_untyped: bool = True,
    ) -> list[AuditEvent]:
        """Events after ``cursor``, ordered by id (the change-feed cursor). The
        autoincrement id is monotonic, so a cursor stays valid and resumable
        regardless of writes that land before or after it is issued.

        ``accessible_type_ids`` is the grant restriction and is applied **in SQL**,
        not by filtering the returned page. That is what keeps keyset pagination
        correct: filtering afterwards would return short pages whose ``next_cursor``
        skipped rows the caller never saw, so a page boundary that happens to fall
        inside a run of filtered-out rows would silently truncate the feed.
        """
        sql = f"{self._SELECT} WHERE e.id > :cursor"
        params: dict[str, Any] = {"cursor": cursor, "limit": limit}
        if object_type_ids is not None:
            placeholders = ", ".join(f":t{i}" for i in range(len(object_type_ids)))
            sql += f" AND e.object_type_id IN ({placeholders})"
            params.update({f"t{i}": tid for i, tid in enumerate(object_type_ids)})
        sql += self._access_clause(params, accessible_type_ids, include_untyped)
        sql += " ORDER BY e.id LIMIT :limit"
        rows = conn.execute(text(sql), params).mappings()
        return [self._event(dict(r)) for r in rows]

    @staticmethod
    def _access_clause(
        params: dict[str, Any],
        accessible_type_ids: list[str] | None,
        include_untyped: bool,
    ) -> str:
        """Delegates to :func:`audit_access_clause`, which holds the body.

        Kept as a method because this class's two call sites read better as
        ``self._access_clause(...)``. The body is a function because the agent-label
        directory is a second reader of the same restriction, and a copy of it would have been
        a second implementation of "which audit rows may this caller see".
        """
        return audit_access_clause(params, accessible_type_ids, include_untyped)

    def search(
        self,
        conn: Connection,
        *,
        record_id: str | None = None,
        principal_id: str | None = None,
        agent_label_id: str | None = None,
        object_type_id: str | None = None,
        field_key: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int,
        before_id: int | None = None,
        accessible_type_ids: list[str] | None = None,
        include_untyped: bool = True,
    ) -> list[AuditEvent]:
        """Filtered audit browse (FR-U8, DD-21), newest first, keyset-paginated on
        id. Raw SQL for this combined WHERE clause lives only here (DD-2).

        ``accessible_type_ids`` is the grant restriction, applied in SQL for the same
        pagination reason as in :meth:`since`."""
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": limit}
        if record_id is not None:
            clauses.append("e.record_id = :record_id")
            params["record_id"] = record_id
        if principal_id is not None:
            clauses.append("e.principal_id = :principal_id")
            params["principal_id"] = principal_id
        if agent_label_id is not None:
            clauses.append("e.agent_label_id = :agent_label_id")
            params["agent_label_id"] = agent_label_id
        if object_type_id is not None:
            clauses.append("e.object_type_id = :object_type_id")
            params["object_type_id"] = object_type_id
        if field_key is not None:
            clauses.append("e.field_key = :field_key")
            params["field_key"] = field_key
        if since is not None:
            clauses.append("e.ts >= :since")
            params["since"] = since
        if until is not None:
            clauses.append("e.ts <= :until")
            params["until"] = until
        if before_id is not None:
            clauses.append("e.id < :before_id")
            params["before_id"] = before_id
        access = self._access_clause(params, accessible_type_ids, include_untyped)
        if access:
            clauses.append(access.removeprefix(" AND "))
        sql = self._SELECT
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY e.id DESC LIMIT :limit"
        rows = conn.execute(text(sql), params).mappings()
        return [self._event(dict(r)) for r in rows]

    @staticmethod
    def _event(row: dict[str, Any]) -> AuditEvent:
        return AuditEvent(
            id=row["id"],
            ts=row["ts"],
            request_id=row["request_id"],
            principal_id=row["principal_id"],
            principal_type=row["principal_type"],
            agent_label_id=row["agent_label_id"],
            auth_method=row["auth_method"],
            surface=row["surface"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            record_id=row["record_id"],
            object_type_id=row["object_type_id"],
            action=row["action"],
            field_key=row["field_key"],
            old_value=_load_opt(row["old_value"]),
            new_value=_load_opt(row["new_value"]),
            note=row["note"],
            principal_display_name=row.get("principal_display_name"),
            agent_label=row.get("agent_label"),
            record_key=row.get("record_key"),
        )


_PRINCIPAL_COLUMNS = (
    "id, type, display_name, email, role, auth_provider, external_id, password_hash, "
    "is_active, description, created_at, created_by"
)


class SqlitePrincipalRepository:
    """``principals`` (docs/DATA_MODEL.md section 2, FR-I3)."""

    def get(self, conn: Connection, principal_id: str) -> PrincipalRow | None:
        row = (
            conn.execute(text("SELECT * FROM principals WHERE id = :id"), {"id": principal_id})
            .mappings()
            .first()
        )
        return None if row is None else self._principal(dict(row))

    def get_by_email(self, conn: Connection, email: str) -> PrincipalRow | None:
        row = (
            conn.execute(text("SELECT * FROM principals WHERE email = :email"), {"email": email})
            .mappings()
            .first()
        )
        return None if row is None else self._principal(dict(row))

    def get_by_external(
        self, conn: Connection, auth_provider: str, external_id: str
    ) -> PrincipalRow | None:
        row = (
            conn.execute(
                text(
                    "SELECT * FROM principals WHERE auth_provider = :provider "
                    "AND external_id = :external_id"
                ),
                {"provider": auth_provider, "external_id": external_id},
            )
            .mappings()
            .first()
        )
        return None if row is None else self._principal(dict(row))

    def insert(self, conn: Connection, row: PrincipalRow) -> None:
        conn.execute(
            text(
                f"INSERT INTO principals ({_PRINCIPAL_COLUMNS}) VALUES (:id, :type, "
                ":display_name, :email, :role, :auth_provider, :external_id, :password_hash, "
                ":is_active, :description, :created_at, :created_by)"
            ),
            {
                "id": row.id,
                "type": row.type,
                "display_name": row.display_name,
                "email": row.email,
                "role": row.role,
                "auth_provider": row.auth_provider,
                "external_id": row.external_id,
                "password_hash": row.password_hash,
                "is_active": int(row.is_active),
                "description": row.description,
                "created_at": row.created_at,
                "created_by": row.created_by,
            },
        )

    def update_row(self, conn: Connection, principal_id: str, changes: dict[str, Any]) -> None:
        if not changes:
            return
        assignments = ", ".join(f"{column} = :{column}" for column in changes)
        params: dict[str, Any] = dict(changes)
        params["id"] = principal_id
        conn.execute(text(f"UPDATE principals SET {assignments} WHERE id = :id"), params)

    def list_principals(
        self, conn: Connection, principal_type: str | None = None, include_inactive: bool = True
    ) -> list[PrincipalRow]:
        sql = "SELECT * FROM principals"
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if principal_type is not None:
            clauses.append("type = :type")
            params["type"] = principal_type
        if not include_inactive:
            clauses.append("is_active = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at, id"
        rows = conn.execute(text(sql), params).mappings()
        return [self._principal(dict(r)) for r in rows]

    def principal_exists(self, conn: Connection, principal_id: str) -> bool:
        """Is this id a *live* principal? One boolean SELECT.

        It lives here rather than on :class:`SqliteSchemaRepository`, which is not where
        the ``principals`` table belongs (DD-2). Its one caller is
        ``services/principals.py::resolve_principal_ref``, whose hot path is a bare id on a
        field with the default permissive service-account rule and needs no row. Every
        other question about a principal reference goes through that
        resolver, and ``tests/test_one_principal_resolver.py`` fails if a second caller
        appears.
        """
        row = conn.execute(
            text("SELECT 1 FROM principals WHERE id = :id AND is_active = 1"),
            {"id": principal_id},
        ).first()
        return row is not None

    def principals_by_ids(self, conn: Connection, ids: list[str]) -> list[PrincipalRow]:
        """Every principal named by ``ids``, in one statement.

        The read behind the ``principals`` sidecar: one call per response document, not
        one per record. Unknown ids are simply absent from the result — a referent that
        has been removed is a rendering fallback on the client, not an error here.
        """
        if not ids:
            return []
        unique = list(dict.fromkeys(ids))
        placeholders = ", ".join(f":id{i}" for i in range(len(unique)))
        params = {f"id{i}": value for i, value in enumerate(unique)}
        rows = conn.execute(
            text(f"SELECT * FROM principals WHERE id IN ({placeholders})"), params
        ).mappings()
        return [self._principal(dict(r)) for r in rows]

    def search_principals(
        self,
        conn: Connection,
        q: str | None,
        principal_type: str | None,
        include_inactive: bool,
        limit: int,
    ) -> list[PrincipalRow]:
        """The directory read: a case-insensitive substring of the
        display name **or** the email, active-only unless asked otherwise.

        Every argument is required and none is clamped here: what the bounds *are* is
        service-layer policy (``DIRECTORY_MAX_LIMIT``), and this layer only turns the
        answer into a LIMIT clause (DD-2).
        """
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if q:
            clauses.append("(lower(display_name) LIKE :q OR lower(email) LIKE :q)")
            params["q"] = f"%{q.strip().lower()}%"
        if principal_type is not None:
            clauses.append("type = :type")
            params["type"] = principal_type
        if not include_inactive:
            clauses.append("is_active = 1")
        sql = "SELECT * FROM principals"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY display_name COLLATE NOCASE, id LIMIT :limit"
        params["limit"] = limit
        rows = conn.execute(text(sql), params).mappings()
        return [self._principal(dict(r)) for r in rows]

    def find_by_display_name(self, conn: Connection, display_name: str) -> list[PrincipalRow]:
        """Every principal whose display name equals ``display_name`` case-insensitively.

        A list, never one row: the resolver refuses to pick under ambiguity, so the
        candidate set is what it needs, and narrowing it (inactive, service accounts) is
        the *mode's* job, not this query's.
        """
        rows = conn.execute(
            text("SELECT * FROM principals WHERE lower(display_name) = :name"),
            {"name": display_name.strip().lower()},
        ).mappings()
        return [self._principal(dict(r)) for r in rows]

    def count_active_admin_users(self, conn: Connection) -> int:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM principals WHERE type = 'user' AND role = 'admin' "
                "AND is_active = 1"
            )
        ).first()
        return 0 if row is None else int(row[0])

    def count_active_users(self, conn: Connection) -> int:
        """Active principals of type ``user``: ``people``, for the workspace
        document. A service account is not a person, so it is excluded by the same
        ``type`` filter that admits it to nothing here."""
        row = conn.execute(
            text("SELECT COUNT(*) FROM principals WHERE type = 'user' AND is_active = 1")
        ).first()
        return 0 if row is None else int(row[0])

    def count_users(self, conn: Connection) -> int:
        """Every principal of type ``user``, active or not.

        The one count with no ``is_active`` filter, and the filter's absence is the
        point: it answers "has this deployment ever had a person in it", which is what
        ``POST /api/v1/bootstrap`` refuses a second claim on. Reusing either sibling
        count above would have made "already bootstrapped" depend on whether the only
        administrator happened to be deactivated, reopening a credential-exempt path
        that mints an ``admin`` token. Principals are never hard-deleted, so once this
        is above zero it stays there.
        """
        row = conn.execute(text("SELECT COUNT(*) FROM principals WHERE type = 'user'")).first()
        return 0 if row is None else int(row[0])

    @staticmethod
    def _principal(row: dict[str, Any]) -> PrincipalRow:
        return PrincipalRow(
            id=row["id"],
            type=row["type"],
            display_name=row["display_name"],
            email=row["email"],
            role=row["role"],
            auth_provider=row["auth_provider"],
            external_id=row["external_id"],
            password_hash=row["password_hash"],
            is_active=bool(row["is_active"]),
            description=row["description"],
            created_at=row["created_at"],
            created_by=row["created_by"],
        )


class SqliteAccessTokenRepository:
    """``access_tokens`` (docs/DATA_MODEL.md section 2, FR-I4)."""

    def get(self, conn: Connection, token_id: str) -> AccessTokenRow | None:
        row = (
            conn.execute(text("SELECT * FROM access_tokens WHERE id = :id"), {"id": token_id})
            .mappings()
            .first()
        )
        return None if row is None else self._token(dict(row))

    def get_by_hash(self, conn: Connection, token_hash: str) -> AccessTokenRow | None:
        row = (
            conn.execute(
                text("SELECT * FROM access_tokens WHERE token_hash = :h"), {"h": token_hash}
            )
            .mappings()
            .first()
        )
        return None if row is None else self._token(dict(row))

    def insert(self, conn: Connection, row: AccessTokenRow) -> None:
        conn.execute(
            text(
                "INSERT INTO access_tokens (id, principal_id, name, token_hash, token_prefix, "
                "scope, expires_at, last_used_at, revoked_at, created_at, created_by, "
                "capability, capability_data, consumed_at, agent_label) VALUES "
                "(:id, :principal_id, :name, :token_hash, :token_prefix, :scope, :expires_at, "
                ":last_used_at, :revoked_at, :created_at, :created_by, :capability, "
                ":capability_data, :consumed_at, :agent_label)"
            ),
            {
                "id": row.id,
                "principal_id": row.principal_id,
                "name": row.name,
                "token_hash": row.token_hash,
                "token_prefix": row.token_prefix,
                "scope": row.scope,
                "expires_at": row.expires_at,
                "last_used_at": row.last_used_at,
                "revoked_at": row.revoked_at,
                "created_at": row.created_at,
                "created_by": row.created_by,
                "capability": row.capability,
                "capability_data": row.capability_data,
                "consumed_at": row.consumed_at,
                "agent_label": row.agent_label,
            },
        )

    def update_row(self, conn: Connection, token_id: str, changes: dict[str, Any]) -> None:
        if not changes:
            return
        assignments = ", ".join(f"{column} = :{column}" for column in changes)
        params: dict[str, Any] = dict(changes)
        params["id"] = token_id
        conn.execute(text(f"UPDATE access_tokens SET {assignments} WHERE id = :id"), params)

    def list_for_principal(self, conn: Connection, principal_id: str) -> list[AccessTokenRow]:
        """Every ordinary token for one principal.

        Capability rows are excluded (DD-16): an upload ticket lives for
        minutes, cannot be renamed or re-scoped, and would only ever be noise in a list
        an administrator reads to decide what to revoke. The stated cost is that a live
        ticket cannot be hand-revoked; the containment action that matters -- the
        revoke-everything on a password reset or a deactivation -- reads
        ``list_live_by_scope``, which deliberately does not exclude them.
        """
        rows = conn.execute(
            text(
                "SELECT * FROM access_tokens WHERE principal_id = :pid "
                "AND capability IS NULL ORDER BY created_at, id"
            ),
            {"pid": principal_id},
        ).mappings()
        return [self._token(dict(r)) for r in rows]

    def list_capability_tokens(self, conn: Connection) -> list[AccessTokenRow]:
        """Every live capability row, newest last. Not reachable from any route or
        tool: it exists for the mint-time purge and for the tests that have to look at
        a ticket the operator surface deliberately hides."""
        rows = conn.execute(
            text(
                "SELECT * FROM access_tokens WHERE capability IS NOT NULL "
                "AND consumed_at IS NULL AND revoked_at IS NULL "
                "ORDER BY created_at, id"
            )
        ).mappings()
        return [self._token(dict(r)) for r in rows]

    def delete_spent_capability_tokens(self, conn: Connection, now: str) -> int:
        """Drop consumed, revoked and expired capability rows.

        Opportunistic, run as a ticket is minted, so the table does not grow without
        bound and no scheduled job has to exist to keep that true. Ordinary PATs are
        never touched: the ``capability IS NOT NULL`` clause is what makes this safe to
        run on every mint.
        """
        result = conn.execute(
            text(
                "DELETE FROM access_tokens WHERE capability IS NOT NULL AND ("
                "consumed_at IS NOT NULL OR revoked_at IS NOT NULL "
                "OR (expires_at IS NOT NULL AND expires_at <= :now))"
            ),
            {"now": now},
        )
        return int(result.rowcount or 0)

    def consume_capability(self, conn: Connection, token_id: str, now: str) -> int:
        """Stamp ``consumed_at`` if and only if it is still null, returning the rowcount.

        Conditional rather than a read-then-write, so two concurrent uses of one ticket
        cannot both win: exactly one ``UPDATE`` sees a null and the other matches no
        row. The caller asserts the rowcount is 1 and runs this **inside** the
        transaction that inserts the attachment, so a rollback
        un-spends the ticket.
        """
        result = conn.execute(
            text(
                "UPDATE access_tokens SET consumed_at = :now WHERE id = :id AND consumed_at IS NULL"
            ),
            {"id": token_id, "now": now},
        )
        return int(result.rowcount or 0)

    def list_live_by_scope(
        self, conn: Connection, principal_id: str, scopes: list[str], now: str
    ) -> list[AccessTokenRow]:
        """Unrevoked, unexpired tokens for one principal at any of ``scopes``. Used by
        the demotion path (FR-I4), which must not leave a stale over-scoped token."""
        if not scopes:
            return []
        placeholders = ", ".join(f":s{i}" for i in range(len(scopes)))
        params: dict[str, Any] = {f"s{i}": scope for i, scope in enumerate(scopes)}
        params["pid"] = principal_id
        params["now"] = now
        rows = conn.execute(
            text(
                f"SELECT * FROM access_tokens WHERE principal_id = :pid "
                f"AND scope IN ({placeholders}) AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > :now) ORDER BY created_at, id"
            ),
            params,
        ).mappings()
        return [self._token(dict(r)) for r in rows]

    def touch_last_used(self, conn: Connection, token_id: str, now: str) -> None:
        conn.execute(
            text("UPDATE access_tokens SET last_used_at = :now WHERE id = :id"),
            {"id": token_id, "now": now},
        )

    @staticmethod
    def _token(row: dict[str, Any]) -> AccessTokenRow:
        return AccessTokenRow(
            id=row["id"],
            principal_id=row["principal_id"],
            name=row["name"],
            token_hash=row["token_hash"],
            token_prefix=row["token_prefix"],
            scope=row["scope"],
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
            revoked_at=row["revoked_at"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            capability=row["capability"],
            capability_data=row["capability_data"],
            consumed_at=row["consumed_at"],
            agent_label=row["agent_label"],
        )


class SqliteSessionRepository:
    """``sessions`` (docs/DATA_MODEL.md section 2, FR-A3, DD-9)."""

    def get(self, conn: Connection, session_id: str) -> SessionRow | None:
        row = (
            conn.execute(text("SELECT * FROM sessions WHERE id = :id"), {"id": session_id})
            .mappings()
            .first()
        )
        return None if row is None else self._session(dict(row))

    def get_by_hash(self, conn: Connection, session_hash: str) -> SessionRow | None:
        row = (
            conn.execute(
                text("SELECT * FROM sessions WHERE session_hash = :h"), {"h": session_hash}
            )
            .mappings()
            .first()
        )
        return None if row is None else self._session(dict(row))

    def insert(self, conn: Connection, row: SessionRow) -> None:
        conn.execute(
            text(
                "INSERT INTO sessions (id, principal_id, session_hash, csrf_hash, created_at, "
                "expires_at, last_seen_at, revoked_at) VALUES (:id, :principal_id, "
                ":session_hash, :csrf_hash, :created_at, :expires_at, :last_seen_at, :revoked_at)"
            ),
            {
                "id": row.id,
                "principal_id": row.principal_id,
                "session_hash": row.session_hash,
                "csrf_hash": row.csrf_hash,
                "created_at": row.created_at,
                "expires_at": row.expires_at,
                "last_seen_at": row.last_seen_at,
                "revoked_at": row.revoked_at,
            },
        )

    def update_row(self, conn: Connection, session_id: str, changes: dict[str, Any]) -> None:
        if not changes:
            return
        assignments = ", ".join(f"{column} = :{column}" for column in changes)
        params: dict[str, Any] = dict(changes)
        params["id"] = session_id
        conn.execute(text(f"UPDATE sessions SET {assignments} WHERE id = :id"), params)

    def delete(self, conn: Connection, session_id: str) -> None:
        conn.execute(text("DELETE FROM sessions WHERE id = :id"), {"id": session_id})

    def delete_by_hash(self, conn: Connection, session_hash: str) -> None:
        conn.execute(text("DELETE FROM sessions WHERE session_hash = :h"), {"h": session_hash})

    def list_live_for_principal(self, conn: Connection, principal_id: str) -> list[SessionRow]:
        """Unrevoked sessions for one principal, expired or not: the demotion/deactivation
        cleanup path deletes every row it can still find rather than filtering by expiry,
        which would leave a merely-expired-but-unrevoked row behind for no reason."""
        rows = conn.execute(
            text(
                "SELECT * FROM sessions WHERE principal_id = :pid AND revoked_at IS NULL "
                "ORDER BY created_at, id"
            ),
            {"pid": principal_id},
        ).mappings()
        return [self._session(dict(r)) for r in rows]

    def touch_last_seen(self, conn: Connection, session_id: str, now: str) -> None:
        conn.execute(
            text("UPDATE sessions SET last_seen_at = :now WHERE id = :id"),
            {"id": session_id, "now": now},
        )

    @staticmethod
    def _session(row: dict[str, Any]) -> SessionRow:
        return SessionRow(
            id=row["id"],
            principal_id=row["principal_id"],
            session_hash=row["session_hash"],
            csrf_hash=row["csrf_hash"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_seen_at=row["last_seen_at"],
            revoked_at=row["revoked_at"],
        )


class SqliteSignInCodeRepository:
    """``sign_in_codes`` (docs/DATA_MODEL.md section 2, change 9)."""

    def insert(self, conn: Connection, row: SignInCodeRow) -> None:
        conn.execute(
            text(
                "INSERT INTO sign_in_codes (id, email, code_hash, created_at, expires_at, "
                "attempts, consumed_at, sent) VALUES (:id, :email, :code_hash, :created_at, "
                ":expires_at, :attempts, :consumed_at, :sent)"
            ),
            {
                "id": row.id,
                "email": row.email,
                "code_hash": row.code_hash,
                "created_at": row.created_at,
                "expires_at": row.expires_at,
                "attempts": row.attempts,
                "consumed_at": row.consumed_at,
                "sent": 1 if row.sent else 0,
            },
        )

    def count_for_email_since(self, conn: Connection, email: str, since: str) -> int:
        count = conn.execute(
            text(
                "SELECT COUNT(*) FROM sign_in_codes WHERE email = :email AND created_at >= :since"
            ),
            {"email": email, "since": since},
        ).scalar_one()
        return int(count)

    def count_unsent_since(self, conn: Connection, since: str) -> int:
        count = conn.execute(
            text("SELECT COUNT(*) FROM sign_in_codes WHERE sent = 0 AND created_at >= :since"),
            {"since": since},
        ).scalar_one()
        return int(count)

    def list_live_for_email(self, conn: Connection, email: str, now: str) -> list[SignInCodeRow]:
        """Unconsumed codes for one address that have not expired, oldest first."""
        rows = conn.execute(
            text(
                "SELECT * FROM sign_in_codes WHERE email = :email AND consumed_at IS NULL "
                "AND expires_at > :now ORDER BY created_at, id"
            ),
            {"email": email, "now": now},
        ).mappings()
        return [self._code(dict(r)) for r in rows]

    def update_row(self, conn: Connection, code_id: str, changes: dict[str, Any]) -> None:
        if not changes:
            return
        assignments = ", ".join(f"{column} = :{column}" for column in changes)
        params: dict[str, Any] = dict(changes)
        params["id"] = code_id
        conn.execute(text(f"UPDATE sign_in_codes SET {assignments} WHERE id = :id"), params)

    def delete_created_before(self, conn: Connection, cutoff: str) -> int:
        result = conn.execute(
            text("DELETE FROM sign_in_codes WHERE created_at < :cutoff"), {"cutoff": cutoff}
        )
        return int(result.rowcount or 0)

    def delete_for_email(self, conn: Connection, email: str) -> int:
        result = conn.execute(
            text("DELETE FROM sign_in_codes WHERE email = :email"), {"email": email}
        )
        return int(result.rowcount or 0)

    @staticmethod
    def _code(row: dict[str, Any]) -> SignInCodeRow:
        return SignInCodeRow(
            id=row["id"],
            email=row["email"],
            code_hash=row["code_hash"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            attempts=int(row["attempts"]),
            consumed_at=row["consumed_at"],
            sent=bool(row["sent"]),
        )


class SqliteInviteRepository:
    """``invites`` (docs/DATA_MODEL.md section 2, change 9)."""

    def insert(self, conn: Connection, row: InviteRow) -> None:
        conn.execute(
            text(
                "INSERT INTO invites (id, email, display_name, role, invited_by, created_at, "
                "accepted_at, principal_id, revoked_at, revoked_by) VALUES (:id, :email, "
                ":display_name, :role, :invited_by, :created_at, :accepted_at, :principal_id, "
                ":revoked_at, :revoked_by)"
            ),
            {
                "id": row.id,
                "email": row.email,
                "display_name": row.display_name,
                "role": row.role,
                "invited_by": row.invited_by,
                "created_at": row.created_at,
                "accepted_at": row.accepted_at,
                "principal_id": row.principal_id,
                "revoked_at": row.revoked_at,
                "revoked_by": row.revoked_by,
            },
        )

    def get(self, conn: Connection, invite_id: str) -> InviteRow | None:
        row = (
            conn.execute(text("SELECT * FROM invites WHERE id = :id"), {"id": invite_id})
            .mappings()
            .first()
        )
        return None if row is None else self._invite(dict(row))

    def get_open_by_email(self, conn: Connection, email: str) -> InviteRow | None:
        row = (
            conn.execute(
                text(
                    "SELECT * FROM invites WHERE email = :email AND accepted_at IS NULL "
                    "AND revoked_at IS NULL"
                ),
                {"email": email},
            )
            .mappings()
            .first()
        )
        return None if row is None else self._invite(dict(row))

    def list_open(self, conn: Connection) -> list[InviteRow]:
        """Open invites, newest first."""
        rows = conn.execute(
            text(
                "SELECT * FROM invites WHERE accepted_at IS NULL AND revoked_at IS NULL "
                "ORDER BY created_at DESC, id"
            )
        ).mappings()
        return [self._invite(dict(r)) for r in rows]

    def update_row(self, conn: Connection, invite_id: str, changes: dict[str, Any]) -> None:
        if not changes:
            return
        assignments = ", ".join(f"{column} = :{column}" for column in changes)
        params: dict[str, Any] = dict(changes)
        params["id"] = invite_id
        conn.execute(text(f"UPDATE invites SET {assignments} WHERE id = :id"), params)

    @staticmethod
    def _invite(row: dict[str, Any]) -> InviteRow:
        return InviteRow(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"],
            role=row["role"],
            invited_by=row["invited_by"],
            created_at=row["created_at"],
            accepted_at=row["accepted_at"],
            principal_id=row["principal_id"],
            revoked_at=row["revoked_at"],
            revoked_by=row["revoked_by"],
        )


class SqliteSearchRepository:
    """The only module in the tree that names ``vec0``, ``vec_embeddings``, or
    ``fts_content`` (DD-2; grep-backed).

    Two structural choices worth reading before changing anything here.

    **The FTS row is addressed by rowid, through ``search_sources``.** FTS5 keeps
    ``UNINDEXED`` columns in a content shadow table with no index on them, so
    ``DELETE FROM fts_content WHERE record_id = ?`` is a full scan -- measured at 26 ms
    against 200,000 rows, against 0.007 ms by rowid. A schema change writes or purges
    one FTS row per live record of its type, and the purge stays inside the change's own
    transaction (DD-34), so the scanning form would turn that into tens of minutes
    holding SQLite's single writer lock. ``search_sources`` gives every
    source a stable integer identity used as the ``fts_content`` rowid.

    **An unchanged chunk keeps its row.** ``embeddings.id`` *is* the
    ``vec_embeddings`` rowid, so deleting a source's rows and re-inserting them
    assigns new ids and orphans every reused vector. :meth:`sync_source_embeddings`
    therefore matches new chunks against stored hashes as a multiset and preserves
    matched rows in place.
    """

    # -- source identity -----------------------------------------------------

    @staticmethod
    def _key(source: IndexSource) -> dict[str, Any]:
        return {
            "record_id": source.record_id,
            "source_type": source.source_type,
            "field_key_k": source.field_key or "",
            "comment_id_k": source.comment_id or "",
        }

    _SOURCE_MATCH = (
        "record_id = :record_id AND source_type = :source_type "
        "AND coalesce(field_key, '') = :field_key_k "
        "AND coalesce(comment_id, '') = :comment_id_k"
    )

    def _source_id(self, conn: Connection, source: IndexSource, create: bool = False) -> int | None:
        row = conn.execute(
            text(f"SELECT id FROM search_sources WHERE {self._SOURCE_MATCH}"), self._key(source)
        ).first()
        if row is not None:
            return int(row[0])
        if not create:
            return None
        created = conn.execute(
            text(
                "INSERT INTO search_sources "
                "(record_id, object_type_id, source_type, field_key, comment_id) "
                "VALUES (:record_id, :object_type_id, :source_type, :field_key, :comment_id) "
                "RETURNING id"
            ),
            {
                "record_id": source.record_id,
                "object_type_id": source.object_type_id,
                "source_type": source.source_type,
                "field_key": source.field_key,
                "comment_id": source.comment_id,
            },
        ).scalar_one()
        return int(created)

    # -- keyword index -------------------------------------------------------

    def replace_fts_row(self, conn: Connection, source: IndexSource, body: str) -> None:
        """One ``fts_content`` row per source, holding the whole text.

        Not chunked: FTS5 has no length problem, and a whole-text row gives the best
        ``snippet()`` (docs/DATA_MODEL.md section 10).
        """
        source_id = self._source_id(conn, source, create=True)
        conn.execute(text("DELETE FROM fts_content WHERE rowid = :id"), {"id": source_id})
        conn.execute(
            text(
                "INSERT INTO fts_content "
                "(rowid, body, record_id, source_type, field_key, comment_id) "
                "VALUES (:id, :body, :record_id, :source_type, :field_key, :comment_id)"
            ),
            {
                "id": source_id,
                "body": body,
                "record_id": source.record_id,
                "source_type": source.source_type,
                "field_key": source.field_key,
                "comment_id": source.comment_id,
            },
        )

    def delete_fts_row(self, conn: Connection, source: IndexSource) -> None:
        source_id = self._source_id(conn, source)
        if source_id is None:
            return
        conn.execute(text("DELETE FROM fts_content WHERE rowid = :id"), {"id": source_id})
        conn.execute(text("DELETE FROM search_sources WHERE id = :id"), {"id": source_id})

    # -- vector index --------------------------------------------------------

    def stored_chunk_hashes(self, conn: Connection, source: IndexSource) -> list[str]:
        rows = conn.execute(
            text(
                f"SELECT content_hash FROM embeddings WHERE {self._SOURCE_MATCH} "
                "ORDER BY chunk_index"
            ),
            self._key(source),
        ).all()
        return [row[0] for row in rows]

    def sync_source_embeddings(
        self,
        conn: Connection,
        source: IndexSource,
        chunks: list[tuple[int, str, str]],
        vectors_by_hash: dict[str, list[float]],
        model_id: str,
        now: str,
    ) -> EmbeddingSyncResult:
        """Row-preserving sync of one source's chunks (docs/DATA_MODEL.md section 10).

        ``chunks`` is ``(chunk_index, chunk_text, content_hash)`` in document order.
        ``vectors_by_hash`` need only carry the hashes the caller determined were new;
        one vector per *distinct* hash is enough even when a paragraph repeats, since
        identical text embeds identically.

        Raises :class:`KeyError` if a chunk needs a row and the caller supplied no
        vector for it. That can only happen when the source's stored rows changed
        between the caller's read and this write (a concurrent purge), and failing the
        job so it retries against fresh state is the right answer -- inventing a zero
        vector would poison the index silently.
        """
        stored = conn.execute(
            text(
                f"SELECT id, chunk_index, content_hash FROM embeddings WHERE {self._SOURCE_MATCH} "
                "ORDER BY chunk_index, id"
            ),
            self._key(source),
        ).all()
        # Multiset, not a map: a repeated paragraph gives two chunks the same hash, and
        # N stored rows carrying a hash may satisfy at most N new chunks carrying it.
        available: dict[str, list[tuple[int, int]]] = {}
        for row_id, chunk_index, content_hash in stored:
            available.setdefault(content_hash, []).append((int(row_id), int(chunk_index)))

        reused = 0
        pending_inserts: list[tuple[int, str, str]] = []
        for chunk_index, chunk_text, content_hash in chunks:
            bucket = available.get(content_hash)
            if bucket:
                row_id, stored_index = bucket.pop(0)
                if stored_index != chunk_index:
                    # Only the position moved; the vector is untouched and keeps its
                    # rowid, which is the whole point of this method.
                    conn.execute(
                        text("UPDATE embeddings SET chunk_index = :i WHERE id = :id"),
                        {"i": chunk_index, "id": row_id},
                    )
                reused += 1
            else:
                pending_inserts.append((chunk_index, chunk_text, content_hash))

        orphaned = [row_id for bucket in available.values() for row_id, _ in bucket]
        for row_id in orphaned:
            conn.execute(text("DELETE FROM vec_embeddings WHERE rowid = :id"), {"id": row_id})
            conn.execute(text("DELETE FROM embeddings WHERE id = :id"), {"id": row_id})

        for chunk_index, chunk_text, content_hash in pending_inserts:
            vector = vectors_by_hash[content_hash]
            new_id = conn.execute(
                text(
                    "INSERT INTO embeddings (source_type, record_id, object_type_id, field_key, "
                    "comment_id, chunk_index, chunk_text, content_hash, model_id, created_at) "
                    "VALUES (:source_type, :record_id, :object_type_id, :field_key, :comment_id, "
                    ":chunk_index, :chunk_text, :content_hash, :model_id, :created_at) "
                    "RETURNING id"
                ),
                {
                    "source_type": source.source_type,
                    "record_id": source.record_id,
                    "object_type_id": source.object_type_id,
                    "field_key": source.field_key,
                    "comment_id": source.comment_id,
                    "chunk_index": chunk_index,
                    "chunk_text": chunk_text,
                    "content_hash": content_hash,
                    "model_id": model_id,
                    "created_at": now,
                },
            ).scalar_one()
            # Both partition keys travel with the vector (migration 6): a KNN scoped by
            # object type and model is answered inside those partitions. They are never
            # updated afterwards; a source that changes type or model is purged and
            # re-inserted.
            conn.execute(
                text(
                    "INSERT INTO vec_embeddings (rowid, embedding, object_type_id, model_id) "
                    "VALUES (:id, :v, :object_type_id, :model_id)"
                ),
                {
                    "id": int(new_id),
                    "v": sqlite_vec.serialize_float32(vector),
                    "object_type_id": source.object_type_id,
                    "model_id": model_id,
                },
            )

        return EmbeddingSyncResult(
            inserted=len(pending_inserts), reused=reused, deleted=len(orphaned)
        )

    def source_rows(
        self, conn: Connection, source: IndexSource, include_vectors: bool = False
    ) -> list[EmbeddingRow]:
        """A source's stored chunks, optionally with the vectors themselves.

        ``include_vectors`` exists for the test that asserts an unchanged chunk's
        vector is **byte-identical** across an edit: "the provider was not called" is
        equally true of the broken implementation that re-inserts every row, and only
        reading the stored bytes back discriminates between them.
        """
        rows = conn.execute(
            text(
                "SELECT id, source_type, record_id, object_type_id, field_key, comment_id, "
                "chunk_index, chunk_text, content_hash, model_id, created_at "
                f"FROM embeddings WHERE {self._SOURCE_MATCH} ORDER BY chunk_index, id"
            ),
            self._key(source),
        ).all()
        out: list[EmbeddingRow] = []
        for row in rows:
            vector: list[float] | None = None
            if include_vectors:
                raw = conn.execute(
                    text("SELECT embedding FROM vec_embeddings WHERE rowid = :id"),
                    {"id": int(row[0])},
                ).scalar()
                if raw is not None:
                    vector = list(struct.unpack(f"{len(raw) // 4}f", raw))
            out.append(
                EmbeddingRow(
                    id=int(row[0]),
                    source_type=row[1],
                    record_id=row[2],
                    object_type_id=row[3],
                    field_key=row[4],
                    comment_id=row[5],
                    chunk_index=int(row[6]),
                    chunk_text=row[7],
                    content_hash=row[8],
                    model_id=row[9],
                    created_at=row[10],
                    vector=vector,
                )
            )
        return out

    # -- purge ---------------------------------------------------------------

    def purge_field(self, conn: Connection, object_type_id: str, field_key: str) -> None:
        """Remove every index row for one field of one object type.

        Called when a field is deleted, has its type changed away from text, or has
        its ``embed`` flag turned off. Both indexes go together: ``embed`` gates the
        FTS row as well as the embedding rows, because a term present only in a
        non-opted-in field must not be findable in hybrid mode either
        (docs/DATA_MODEL.md section 10).
        """
        params = {"otid": object_type_id, "fk": field_key}
        self._purge_fts(conn, "object_type_id = :otid AND field_key = :fk", params)
        self._purge_vectors(conn, "object_type_id = :otid AND field_key = :fk", params)
        conn.execute(
            text(
                "DELETE FROM embedding_jobs WHERE field_key = :fk AND record_id IN "
                "(SELECT id FROM records WHERE object_type_id = :otid)"
            ),
            params,
        )

    def purge_object_type(self, conn: Connection, object_type_id: str) -> None:
        params = {"otid": object_type_id}
        self._purge_fts(conn, "object_type_id = :otid", params)
        self._purge_vectors(conn, "object_type_id = :otid", params)
        conn.execute(
            text(
                "DELETE FROM embedding_jobs WHERE record_id IN "
                "(SELECT id FROM records WHERE object_type_id = :otid)"
            ),
            params,
        )

    def _purge_fts(self, conn: Connection, where: str, params: dict[str, Any]) -> None:
        ids = [
            int(row[0])
            for row in conn.execute(
                text(f"SELECT id FROM search_sources WHERE {where}"), params
            ).all()
        ]
        for source_id in ids:
            conn.execute(text("DELETE FROM fts_content WHERE rowid = :id"), {"id": source_id})
            conn.execute(text("DELETE FROM search_sources WHERE id = :id"), {"id": source_id})

    def _purge_vectors(self, conn: Connection, where: str, params: dict[str, Any]) -> None:
        ids = [
            int(row[0])
            for row in conn.execute(text(f"SELECT id FROM embeddings WHERE {where}"), params).all()
        ]
        for row_id in ids:
            conn.execute(text("DELETE FROM vec_embeddings WHERE rowid = :id"), {"id": row_id})
        conn.execute(text(f"DELETE FROM embeddings WHERE {where}"), params)

    # -- durable queue -------------------------------------------------------

    def enqueue(self, conn: Connection, source: IndexSource, now: str) -> None:
        """Queue one source for embedding (docs/DATA_MODEL.md section 10, rules 1 and 4).

        Two things happen in order, and both matter. A prior ``failed`` row for this
        source is deleted first: a new write supersedes a past failure on that content,
        so the status endpoint reports what is true now rather than a permanent record
        of past failures that re-indexing everything is not the right remedy for. Then
        the insert is ``ON CONFLICT DO NOTHING`` against ``ux_jobs_pending_source``, so
        ten edits before the worker runs leave one job, and a job already ``running``
        (which does not occupy the partial index's slot) gets a fresh ``pending``
        sibling rather than being lost.
        """
        conn.execute(
            text(f"DELETE FROM embedding_jobs WHERE {self._SOURCE_MATCH} AND status = 'failed'"),
            self._key(source),
        )
        conn.execute(
            text(
                "INSERT INTO embedding_jobs "
                "(record_id, source_type, field_key, comment_id, status, attempts, "
                " enqueued_at, updated_at) "
                "VALUES (:record_id, :source_type, :field_key, :comment_id, 'pending', 0, "
                ":now, :now) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "record_id": source.record_id,
                "source_type": source.source_type,
                "field_key": source.field_key,
                "comment_id": source.comment_id,
                "now": now,
            },
        )

    def claim_jobs(
        self, conn: Connection, limit: int, now: str, backoff_cutoffs: list[str]
    ) -> list[EmbeddingJobRow]:
        """Claim up to ``limit`` pending jobs in ``id`` order, honoring the backoff.

        The backoff lives **here**, in the claim predicate, not in reclaim: reclaim
        only ever looks at ``running`` rows, so a ``pending`` row is claimed and never
        reclaimed, and a backoff expressed as a reclaim delay is a no-op that lets a
        failing job burn all five attempts in a few seconds (section 10, rule 2).
        ``backoff_cutoffs[n]`` is the newest ``updated_at`` a job with ``n`` attempts
        may still be claimed at; the caller computes them from ``min(300, 2 ** n)``.
        """
        params: dict[str, Any] = {"now": now, "limit": limit}
        branches = []
        for attempt, cutoff in enumerate(backoff_cutoffs):
            params[f"c{attempt}"] = cutoff
            if attempt == len(backoff_cutoffs) - 1:
                branches.append(f"ELSE :c{attempt}")
            else:
                branches.append(f"WHEN attempts = {attempt} THEN :c{attempt}")
        case_expr = "CASE " + " ".join(branches) + " END"
        rows = conn.execute(
            text(
                "UPDATE embedding_jobs SET status = 'running', updated_at = :now "
                "WHERE id IN (SELECT id FROM embedding_jobs "
                f"            WHERE status = 'pending' AND updated_at <= {case_expr} "
                "             ORDER BY id LIMIT :limit) "
                "RETURNING id, record_id, source_type, field_key, comment_id, status, "
                "          attempts, last_error, enqueued_at, updated_at"
            ),
            params,
        ).all()
        return [self._job(row) for row in rows]

    def reclaim_stale(self, conn: Connection, stale_before: str, now: str) -> int:
        """Return ``running`` rows stranded past the timeout to the queue (rule 5).

        This is the **idle poll**'s reclaim. It runs only on the embedding worker's own
        thread, between batches, while that thread holds nothing, so a row it finds
        ``running`` was abandoned by that same process: an ``Exception`` escaping
        ``run_once`` outside ``_process``'s handler (``fail_job``'s own write failing on
        ``busy_timeout``, say) leaves the rest of the batch ``running`` while the thread
        survives, and those rows would otherwise stay stranded for the life of the
        process and ``pending_jobs`` would never reach zero. A ``BaseException`` kills
        the thread instead, so this reclaim never runs for it and only the next
        startup's :meth:`reclaim_all` recovers its rows (DD-35).

        The ten-minute threshold is margin, not protection for a live holder: a pause
        of any length cannot make the worker reclaim its own work, because the pause
        stops the one thread that would do it. A second process on one database would
        reclaim rows the first still holds; that is unsupported, not impossible, and the
        entry point pins one process for that reason.
        """
        return self._reclaim(
            conn,
            "WHERE status = 'running' AND updated_at <= :stale_before",
            {"stale_before": stale_before},
            error="reclaimed after exceeding the running timeout",
            now=now,
        )

    def reclaim_all(self, conn: Connection, now: str) -> int:
        """Return **every** ``running`` row to the queue, whatever its age (DD-35).

        Called once, at worker startup. At the moment a process starts, every
        ``running`` row is the residue of an unclean exit: only the application
        lifespan ever constructs an ``EmbeddingWorker``, and the documented upgrade
        stops the old container before starting the new one. An age threshold there
        just makes a workspace that was killed seconds ago wait ten minutes for content
        it has already been told is indexed, while answering ``/readyz``.

        Still counts an attempt, like the idle poll: this is the kill path, and a source
        that reliably kills the process has to become terminal rather than be retried
        forever. A graceful stop reaches ``release_claimed`` instead and is free.
        """
        return self._reclaim(
            conn,
            "WHERE status = 'running'",
            {},
            error="reclaimed at startup after an unclean exit",
            now=now,
        )

    def _reclaim(
        self,
        conn: Connection,
        where: str,
        params: dict[str, Any],
        *,
        error: str,
        now: str,
    ) -> int:
        rows = conn.execute(
            text(
                "SELECT id, record_id, source_type, field_key, comment_id, status, attempts, "
                f"last_error, enqueued_at, updated_at FROM embedding_jobs {where} ORDER BY id"
            ),
            params,
        ).all()
        for row in rows:
            job = self._job(row)
            # Counted as an attempt on purpose: a source that reliably strands the
            # worker (an input that kills the thread rather than raising) would
            # otherwise be reclaimed forever, and the backoff keys off ``attempts``.
            self._leave_running(
                conn,
                job,
                attempts=job.attempts + 1,
                error=error,
                now=now,
                terminal=False,
            )
        return len(rows)

    def release_claimed(self, conn: Connection, jobs: list[EmbeddingJobRow]) -> int:
        """Return claimed-but-unstarted jobs to ``pending``, charging nothing.

        The stop path, not the failure path: the worker checked its stop event between
        sources and these are the ones it will not reach. ``attempts``, ``last_error``
        and ``updated_at`` are all left exactly as they are, so a workspace that scales
        to zero five times does not mark its own content ``failed``, and a stop writes
        no error where none happened.

        Leaving ``updated_at`` alone leaves it at the claim time, because
        :meth:`claim_jobs` stamps it there, so the claim predicate (rule 2) holds the
        row back by ``min(300, 2 ** attempts)`` seconds: 1 second at ``attempts = 0``
        and 16 at ``attempts = 4``. That is inside every grace period here and is left
        as it is, but a caller that releases and re-claims at one injected moment will
        see nothing and should not read that as a bug.

        Rule 4 governs this transition like the other three. A released row that finds a
        live ``pending`` sibling is deleted and carries its ``attempts`` onto that
        sibling -- dropping them would reset a source's failure count every time a stop
        landed while an edit was queued -- while the sibling keeps its own
        ``last_error``, because releasing is not a failure.
        """
        released = 0
        for job in jobs:
            key = self._key(job.source(""))
            sibling = conn.execute(
                text(
                    f"SELECT id FROM embedding_jobs WHERE {self._SOURCE_MATCH} "
                    "AND status = 'pending' AND id != :id"
                ),
                {**key, "id": job.id},
            ).first()
            if sibling is not None:
                conn.execute(text("DELETE FROM embedding_jobs WHERE id = :id"), {"id": job.id})
                conn.execute(
                    text("UPDATE embedding_jobs SET attempts = :attempts WHERE id = :id"),
                    {"attempts": job.attempts, "id": int(sibling[0])},
                )
            else:
                conn.execute(
                    text(
                        "UPDATE embedding_jobs SET status = 'pending' "
                        "WHERE id = :id AND status = 'running'"
                    ),
                    {"id": job.id},
                )
            released += 1
        return released

    def complete_job(self, conn: Connection, job_id: int) -> None:
        conn.execute(text("DELETE FROM embedding_jobs WHERE id = :id"), {"id": job_id})

    def fail_job(
        self, conn: Connection, job: EmbeddingJobRow, error: str, now: str, max_attempts: int
    ) -> str:
        """Record a failed attempt; returns the resulting status."""
        attempts = job.attempts + 1
        terminal = attempts >= max_attempts
        return self._leave_running(
            conn, job, attempts=attempts, error=error, now=now, terminal=terminal
        )

    def _leave_running(
        self,
        conn: Connection,
        job: EmbeddingJobRow,
        *,
        attempts: int,
        error: str,
        now: str,
        terminal: bool,
    ) -> str:
        """The one place a ``running`` row leaves that state (section 10, rule 4).

        ``ux_jobs_pending_source`` makes ``pending`` a scarce state, so this transition
        has to say what happens when the slot is already taken. A sibling can only
        exist because a write landed while this job was running, which means newer
        content is already queued: the ``running`` row is deleted and its ``attempts``
        and ``last_error`` are carried onto the sibling, so the five-attempt terminal
        state counts the source's failures instead of resetting on every concurrent
        edit -- and so a terminal failure does not leave a ``failed`` row beside a
        ``pending`` one, which is the permanent-failure report rule 1 exists to
        prevent. Only when no sibling exists does the row transition in place.
        """
        key = self._key(job.source(""))
        sibling = conn.execute(
            text(
                f"SELECT id FROM embedding_jobs WHERE {self._SOURCE_MATCH} "
                "AND status = 'pending' AND id != :id"
            ),
            {**key, "id": job.id},
        ).first()
        if sibling is not None:
            conn.execute(text("DELETE FROM embedding_jobs WHERE id = :id"), {"id": job.id})
            conn.execute(
                text(
                    "UPDATE embedding_jobs SET attempts = :attempts, last_error = :error, "
                    "updated_at = :now WHERE id = :id"
                ),
                {"attempts": attempts, "error": error, "now": now, "id": int(sibling[0])},
            )
            return "pending"
        status = "failed" if terminal else "pending"
        conn.execute(
            text(
                "UPDATE embedding_jobs SET status = :status, attempts = :attempts, "
                "last_error = :error, updated_at = :now WHERE id = :id"
            ),
            {"status": status, "attempts": attempts, "error": error, "now": now, "id": job.id},
        )
        return status

    def enqueue_all(self, conn: Connection, now: str) -> int:
        """The bulk enqueue behind the re-index trigger (FR-Q9).

        One ``pending`` job per ``search_sources`` row -- soft-deleted records and
        comments included, because ``restore_record`` enqueues nothing and a
        record deleted before a model swap and restored after it would otherwise
        never be semantically findable again -- inserted with ``ON CONFLICT DO
        NOTHING`` against ``ux_jobs_pending_source`` so a source with a job already
        pending is coalesced rather than duplicated. ``failed`` rows are deleted first
        (rule 1): a full re-index supersedes every past failure. Returns the number of
        jobs actually inserted.
        """
        conn.execute(text("DELETE FROM embedding_jobs WHERE status = 'failed'"))
        result = conn.execute(
            text(
                "INSERT INTO embedding_jobs "
                "(record_id, source_type, field_key, comment_id, status, attempts, "
                " enqueued_at, updated_at) "
                "SELECT record_id, source_type, field_key, comment_id, 'pending', 0, :now, :now "
                "FROM search_sources WHERE true ORDER BY id "
                "ON CONFLICT DO NOTHING"
            ),
            {"now": now},
        )
        return int(result.rowcount)

    # -- retrieval -----------------------------------------------------------

    @staticmethod
    def _type_scope(alias: str, object_type_ids: list[str], params: dict[str, Any]) -> str:
        """``<alias>.object_type_id IN (:t0, :t1, ...)`` with the ids bound, never
        interpolated. The caller guarantees the list is non-empty."""
        names = []
        for index, type_id in enumerate(object_type_ids):
            params[f"t{index}"] = type_id
            names.append(f":t{index}")
        return f"{alias}object_type_id IN ({', '.join(names)})"

    def keyword_search(
        self, conn: Connection, fts_query: str, object_type_ids: list[str], k: int
    ) -> list[KeywordHit]:
        """The keyword arm's pool: ``k`` FTS rows in ``bm25`` order, **scoped and
        live-joined before the limit** (docs/DATA_MODEL.md section 10, rule 1).

        ``fts_content`` is joined to ``search_sources`` (its rowid; that is where
        ``object_type_id`` lives), to ``records`` on ``deleted_at IS NULL``, and, for
        comment sources, to ``comments`` on ``deleted_at IS NULL``, all *before*
        ``ORDER BY bm25(...) LIMIT k``, so a soft-deleted record's rows consume no
        pool slot. ``MATCH`` is written against the unaliased table name: aliasing
        an FTS5 table breaks the operator (measured).
        ``fts_query`` is the constructed phrase query from the search service --
        raw user text never reaches this method.
        """
        if not object_type_ids:
            return []
        params: dict[str, Any] = {"q": fts_query, "k": k}
        scope = self._type_scope("s.", object_type_ids, params)
        rows = conn.execute(
            text(
                "SELECT s.record_id, s.object_type_id, s.source_type, s.field_key, s.comment_id, "
                "       snippet(fts_content, 0, '<em>', '</em>', '…', 32), bm25(fts_content) "
                "FROM fts_content "
                "JOIN search_sources s ON s.id = fts_content.rowid "
                "JOIN records r ON r.id = s.record_id AND r.deleted_at IS NULL "
                "LEFT JOIN comments c ON c.id = s.comment_id "
                f"WHERE fts_content MATCH :q AND {scope} "
                "  AND (s.source_type = 'field' OR c.deleted_at IS NULL) "
                "ORDER BY bm25(fts_content), s.id LIMIT :k"
            ),
            params,
        ).all()
        return [
            KeywordHit(
                source=IndexSource(
                    record_id=row[0],
                    object_type_id=row[1],
                    source_type=row[2],
                    field_key=row[3],
                    comment_id=row[4],
                ),
                snippet=row[5],
                bm25=float(row[6]),
            )
            for row in rows
        ]

    def vector_search(
        self,
        conn: Connection,
        query_vector: list[float],
        object_type_ids: list[str],
        model_id: str,
        k: int,
    ) -> VectorPool:
        """The vector arm's pool: the ``k`` nearest **live** chunk rows by distance,
        from a KNN constrained by both partition keys (docs/DATA_MODEL.md section 10,
        rule 1).

        Two engine facts shape the statement. The KNN runs inside a ``MATERIALIZED``
        CTE because SQLite otherwise flattens the subquery and pushes the outer
        ``LIMIT`` into vec0, which refuses ``LIMIT`` and ``k`` together. And
        ``object_type_id IN (...)`` returns ``k`` rows **per matched partition**, so
        the CTE over-fetches by design and the outer ordering by distance picks the
        global nearest across the in-scope types.

        ``records.deleted_at`` is not visible inside the virtual table, so a
        soft-deleted record's chunks do occupy KNN slots. The live join therefore
        runs over every KNN row and the pool is the first ``k`` live ones; the
        pre-join count comes back as ``knn_rows`` so the service can tell "the pool
        was full and the join emptied it" (widen once, decision 6) from "there was
        nothing more to find". Chunk text is fetched only for the winners.
        """
        if not object_type_ids:
            return VectorPool(hits=[], knn_rows=0)
        params: dict[str, Any] = {
            "q": sqlite_vec.serialize_float32(query_vector),
            "k": k,
            "model_id": model_id,
        }
        scope = self._type_scope("", object_type_ids, params)
        rows = conn.execute(
            text(
                "WITH knn AS MATERIALIZED ("
                "  SELECT rowid AS id, distance FROM vec_embeddings "
                f"  WHERE embedding MATCH :q AND k = :k AND {scope} AND model_id = :model_id"
                ") "
                "SELECT knn.id, knn.distance, e.record_id, e.object_type_id, e.source_type, "
                "       e.field_key, e.comment_id, "
                "       (r.id IS NOT NULL AND (e.source_type = 'field' OR c.deleted_at IS NULL)) "
                "FROM knn "
                "JOIN embeddings e ON e.id = knn.id "
                "LEFT JOIN records r ON r.id = e.record_id AND r.deleted_at IS NULL "
                "LEFT JOIN comments c ON c.id = e.comment_id "
                "ORDER BY knn.distance, knn.id"
            ),
            params,
        ).all()
        winners = [row for row in rows if bool(row[7])][:k]
        if not winners:
            return VectorPool(hits=[], knn_rows=len(rows))
        text_params: dict[str, Any] = {}
        placeholders = []
        for index, row in enumerate(winners):
            text_params[f"i{index}"] = int(row[0])
            placeholders.append(f":i{index}")
        id_list = ", ".join(placeholders)
        chunk_text = {
            int(row[0]): row[1]
            for row in conn.execute(
                text(f"SELECT id, chunk_text FROM embeddings WHERE id IN ({id_list})"),
                text_params,
            ).all()
        }
        hits = [
            VectorHit(
                source=IndexSource(
                    record_id=row[2],
                    object_type_id=row[3],
                    source_type=row[4],
                    field_key=row[5],
                    comment_id=row[6],
                ),
                chunk_text=chunk_text[int(row[0])],
                distance=float(row[1]),
            )
            for row in winners
        ]
        return VectorPool(hits=hits, knn_rows=len(rows))

    @staticmethod
    def _job(row: Any) -> EmbeddingJobRow:
        return EmbeddingJobRow(
            id=int(row[0]),
            record_id=row[1],
            source_type=row[2],
            field_key=row[3],
            comment_id=row[4],
            status=row[5],
            attempts=int(row[6]),
            last_error=row[7],
            enqueued_at=row[8],
            updated_at=row[9],
        )

    # -- observation ---------------------------------------------------------

    def counts(self, conn: Connection) -> IndexCounts:
        by_status = {
            row[0]: int(row[1])
            for row in conn.execute(
                text("SELECT status, count(*) FROM embedding_jobs GROUP BY status")
            ).all()
        }
        chunks = conn.execute(text("SELECT count(*) FROM embeddings")).scalar_one()
        running = by_status.get("running", 0)
        return IndexCounts(
            # "Pending" as an operator means "not yet indexed", which includes the rows
            # a worker is holding right now (docs/DATA_MODEL.md section 10).
            pending_jobs=by_status.get("pending", 0) + running,
            running_jobs=running,
            failed_jobs=by_status.get("failed", 0),
            indexed_chunks=int(chunks),
        )

    def failed_jobs(self, conn: Connection) -> list[FailedJobRow]:
        rows = conn.execute(
            text(
                "SELECT r.key, j.record_id, j.source_type, j.field_key, j.comment_id, "
                "j.attempts, j.last_error, j.updated_at "
                "FROM embedding_jobs j LEFT JOIN records r ON r.id = j.record_id "
                "WHERE j.status = 'failed' ORDER BY j.id"
            )
        ).all()
        return [
            FailedJobRow(
                record_key=row[0],
                record_id=row[1],
                source_type=row[2],
                field_key=row[3],
                comment_id=row[4],
                attempts=int(row[5]),
                last_error=row[6],
                updated_at=row[7],
            )
            for row in rows
        ]

    def stale_chunk_count(self, conn: Connection, model_id: str) -> int:
        count = conn.execute(
            text("SELECT count(*) FROM embeddings WHERE model_id != :model_id"),
            {"model_id": model_id},
        ).scalar_one()
        return int(count)

    def object_type_id_for_record(self, conn: Connection, record_id: str) -> str | None:
        row = conn.execute(
            text("SELECT object_type_id FROM records WHERE id = :id"), {"id": record_id}
        ).first()
        return None if row is None else str(row[0])


class SqliteBackupRepository:
    """``VACUUM INTO``, and nothing else (FR-P8, DD-36).

    DD-36 chose this over copying the database file: a running deployment holds
    uncheckpointed frames in ``-wal``, so copying only the ``.db`` silently drops
    them and copying the three files non-atomically can tear. ``VACUUM INTO`` writes
    one consistent, compacted file while readers and writers proceed, which is FR-P8's
    "without stopping writes" exactly.

    It takes a **DBAPI** connection rather than the SQLAlchemy ``Connection`` every
    other repository here takes, because SQLite refuses ``VACUUM`` inside a
    transaction and both of ``Database``'s context managers open one. See
    ``Database.raw_connection``.
    """

    def vacuum_into(self, raw_connection: Any, destination: Path) -> None:
        """Write a consistent snapshot of the whole database to ``destination``.

        The destination is bound as a parameter rather than interpolated: SQLite
        accepts an expression here, so there is no reason to build the statement by
        string concatenation even though the path is server-chosen and never
        attacker-supplied.

        ``destination`` must not already exist -- SQLite refuses to overwrite -- which
        is the caller's responsibility and is why the service snapshots into a fresh
        uuid-named file.
        """
        cursor = raw_connection.cursor()
        try:
            cursor.execute("VACUUM INTO ?", (str(destination),))
        finally:
            cursor.close()


class SqliteBlobReferenceRepository:
    """Which content hashes the ``attachments`` table still references (FR-P2).

    Separate from :class:`SqliteAttachmentRepository` because the orphan sweep asks a
    question no attachment caller asks: not "what does this row point at" but "what
    does *anything* point at". Deduplication is why the sweep cannot work row by row --
    two rows can share one file, so only the absence of *every* referencing row makes
    a blob deletable.
    """

    def referenced_hashes(self, conn: Connection) -> set[str]:
        rows = conn.execute(text("SELECT DISTINCT sha256 FROM attachments")).all()
        return {str(row[0]) for row in rows}

    def reference_count(self, conn: Connection, sha256: str) -> int:
        return int(
            conn.execute(
                text("SELECT count(*) FROM attachments WHERE sha256 = :sha"), {"sha": sha256}
            ).scalar_one()
        )


class SqliteGrantRepository:
    """``object_type_grants`` (DD-11). The only module that names that table.

    That is a rule rather than a habit: a grep-backed meta-test asserts
    it, for the same reason ``SqliteSearchRepository`` is the only module allowed to
    name ``vec0`` (DD-2).
    """

    def get(self, conn: Connection, object_type_id: str, principal_id: str) -> GrantRow | None:
        row = (
            conn.execute(
                text(
                    "SELECT * FROM object_type_grants "
                    "WHERE object_type_id = :tid AND principal_id = :pid"
                ),
                {"tid": object_type_id, "pid": principal_id},
            )
            .mappings()
            .first()
        )
        return None if row is None else self._grant(dict(row))

    def list_for_type(self, conn: Connection, object_type_id: str) -> list[GrantRow]:
        rows = conn.execute(
            text(
                "SELECT * FROM object_type_grants WHERE object_type_id = :tid "
                "ORDER BY created_at, principal_id"
            ),
            {"tid": object_type_id},
        ).mappings()
        return [self._grant(dict(r)) for r in rows]

    def list_for_principal(self, conn: Connection, principal_id: str) -> list[GrantRow]:
        """Answered by ``ix_object_type_grants_principal``. This is the read that makes
        ``accessible_type_ids`` bounded rather than one probe per object type."""
        rows = conn.execute(
            text("SELECT * FROM object_type_grants WHERE principal_id = :pid"),
            {"pid": principal_id},
        ).mappings()
        return [self._grant(dict(r)) for r in rows]

    def upsert(self, conn: Connection, row: GrantRow) -> GrantRow:
        """Insert, or update the level of the row ``ux_object_type_grants`` collides
        with. ``created_at``/``created_by`` are preserved on an update, so a re-grant
        does not rewrite who first admitted this principal."""
        conn.execute(
            text(
                "INSERT INTO object_type_grants (id, object_type_id, principal_id, level, "
                "created_at, created_by, updated_at, updated_by) VALUES (:id, :object_type_id, "
                ":principal_id, :level, :created_at, :created_by, :updated_at, :updated_by) "
                "ON CONFLICT (object_type_id, principal_id) DO UPDATE SET "
                "level = excluded.level, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by"
            ),
            {
                "id": row.id,
                "object_type_id": row.object_type_id,
                "principal_id": row.principal_id,
                "level": row.level,
                "created_at": row.created_at,
                "created_by": row.created_by,
                "updated_at": row.updated_at,
                "updated_by": row.updated_by,
            },
        )
        stored = self.get(conn, row.object_type_id, row.principal_id)
        assert stored is not None
        return stored

    def delete(self, conn: Connection, object_type_id: str, principal_id: str) -> bool:
        result = conn.execute(
            text(
                "DELETE FROM object_type_grants WHERE object_type_id = :tid AND principal_id = :pid"
            ),
            {"tid": object_type_id, "pid": principal_id},
        )
        return bool(result.rowcount)

    @staticmethod
    def _grant(row: dict[str, Any]) -> GrantRow:
        return GrantRow(
            id=row["id"],
            object_type_id=row["object_type_id"],
            principal_id=row["principal_id"],
            level=row["level"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            updated_at=row["updated_at"],
            updated_by=row["updated_by"],
        )


class SqliteRecordAttachmentRepository:
    """``record_attachments`` (docs/DATA_MODEL.md section 8).

    The reverse index from an attachment to the records that reference it, maintained
    on every write that can change an attachment field's value. It is a *materialized*
    join rather than a scan of ``records.data`` because the read it answers -- "which
    object types is this attachment reachable from" -- happens on every attachment
    download, and the scan form has no index to stand on.
    """

    def replace_for_record(
        self, conn: Connection, record_id: str, refs: dict[str, list[str]]
    ) -> None:
        """Make the stored rows for ``record_id`` exactly ``refs`` (field key -> ids).

        Delete-then-insert rather than a diff: the row count per record is small, and
        the alternative is three statements where this is two with no chance of a
        stale row surviving a rename or a field delete.
        """
        conn.execute(
            text("DELETE FROM record_attachments WHERE record_id = :rid"), {"rid": record_id}
        )
        rows = [
            {"rid": record_id, "aid": attachment_id, "fk": field_key}
            for field_key, ids in sorted(refs.items())
            for attachment_id in dict.fromkeys(ids)
        ]
        if not rows:
            return
        conn.execute(
            text(
                "INSERT OR IGNORE INTO record_attachments (record_id, attachment_id, field_key) "
                "VALUES (:rid, :aid, :fk)"
            ),
            rows,
        )

    def object_type_ids_for_attachment(self, conn: Connection, attachment_id: str) -> set[str]:
        rows = conn.execute(
            text(
                "SELECT DISTINCT r.object_type_id FROM record_attachments ra "
                "JOIN records r ON r.id = ra.record_id WHERE ra.attachment_id = :aid"
            ),
            {"aid": attachment_id},
        ).all()
        return {str(row[0]) for row in rows}

    def object_type_ids_for_attachments(
        self, conn: Connection, attachment_ids: list[str]
    ) -> dict[str, set[str]]:
        if not attachment_ids:
            return {}
        placeholders = ", ".join(f":a{i}" for i in range(len(attachment_ids)))
        params = {f"a{i}": value for i, value in enumerate(attachment_ids)}
        rows = conn.execute(
            text(
                "SELECT DISTINCT ra.attachment_id, r.object_type_id FROM record_attachments ra "
                f"JOIN records r ON r.id = ra.record_id WHERE ra.attachment_id IN ({placeholders})"
            ),
            params,
        ).all()
        out: dict[str, set[str]] = {}
        for attachment_id, type_id in rows:
            out.setdefault(str(attachment_id), set()).add(str(type_id))
        return out

    def rows_for_record(self, conn: Connection, record_id: str) -> list[tuple[str, str]]:
        """``(field_key, attachment_id)`` pairs, for the per-write-path tests."""
        rows = conn.execute(
            text(
                "SELECT field_key, attachment_id FROM record_attachments "
                "WHERE record_id = :rid ORDER BY field_key, attachment_id"
            ),
            {"rid": record_id},
        ).all()
        return [(str(row[0]), str(row[1])) for row in rows]


class SqliteUsageRepository:
    """Every count ``GET /api/v1/usage`` reports, and the counters behind it
    (DD-39, FR-P10).

    One repository rather than a count method added to each of five existing ones,
    because these queries have one consumer and one reason to change: they are an
    operator's view of volume, not a tenant's view of anything. Nothing here returns a
    row, an id, a key or a name that a tenant chose -- every method returns numbers, or,
    in :meth:`count_labels_by_harness`, numbers keyed by a value the **caller** supplied
    from a shipped allowlist.

    Two counts deliberately absent: ``humans_active`` and ``agent_labels_distinct``.
    ``WorkspaceService`` already holds their definition, in the one place it lives, and
    both are already shown to the tenant in the workspace sidebar. Adding a second query
    for them here is how an operator's numbers come to disagree with the tenant's own
    screen with neither being wrong.
    """

    # -- volume ---------------------------------------------------------------

    def count_records(self, conn: Connection) -> tuple[int, int]:
        """``(live, deleted)``. A total, never a breakdown by object type: a type key is
        tenant-chosen, and usage reports "record counts by bucket", not by type."""
        row = conn.execute(
            text(
                "SELECT COUNT(*) FILTER (WHERE deleted_at IS NULL), "
                "COUNT(*) FILTER (WHERE deleted_at IS NOT NULL) FROM records"
            )
        ).first()
        return (0, 0) if row is None else (int(row[0]), int(row[1]))

    def count_attachments(self, conn: Connection) -> tuple[int, int, int]:
        """``(count, logical bytes, stored bytes)``.

        The two byte numbers are reported separately because storage and billing
        disagree about a file uploaded twice, and returning one number would silently
        pick a side: logical is what the workspace thinks it holds, stored is what the
        volume actually carries after deduplication by ``sha256``.
        """
        row = conn.execute(
            text(
                "SELECT COUNT(*), COALESCE(SUM(byte_size), 0), "
                "COALESCE((SELECT SUM(b) FROM "
                "(SELECT byte_size AS b FROM attachments GROUP BY sha256)), 0) "
                "FROM attachments"
            )
        ).first()
        return (0, 0, 0) if row is None else (int(row[0]), int(row[1]), int(row[2]))

    def count_schema(self, conn: Connection) -> tuple[int, int]:
        """``(object types, fields)``, both live. Counts, never keys."""
        row = conn.execute(
            text(
                "SELECT (SELECT COUNT(*) FROM object_types WHERE is_deleted = 0), "
                "(SELECT COUNT(*) FROM fields WHERE is_deleted = 0)"
            )
        ).first()
        return (0, 0) if row is None else (int(row[0]), int(row[1]))

    def sum_agent_label_calls(self, conn: Connection) -> int:
        """Total uses across every registered label. ``agent_labels.call_count`` counts
        uses of a *label*, which is a different question from ``usage_counters``' calls
        of a *tool*; both are reported and neither replaces the other."""
        row = conn.execute(text("SELECT COALESCE(SUM(call_count), 0) FROM agent_labels")).first()
        return 0 if row is None else int(row[0])

    def count_labels_by_harness(self, conn: Connection, known: Collection[str]) -> dict[str, int]:
        """Distinct labels per known harness, plus ``__other__`` for everything else.

        **The tenant's own label strings never leave this method**.
        The caller supplies the allowlist; the ``IN`` clause is what does the matching,
        so every key returned is a value the caller already had. A label that matches
        nothing is counted, never named. Bounded by ``len(known) + 1`` rows whatever the
        workspace has registered, which is what keeps this a bounded read (DD-18).

        Matching is exact after case-folding, never a prefix: ``claude-code-ACME-CORP``
        matches nothing and lands in the residual, rather than being reported as a known
        harness while carrying a customer's name.
        """
        total = conn.execute(text("SELECT COUNT(*) FROM agent_labels")).scalar() or 0
        wanted = sorted({value.casefold() for value in known})
        counts: dict[str, int] = {}
        if wanted:
            placeholders = ", ".join(f":k{i}" for i in range(len(wanted)))
            rows = conn.execute(
                text(
                    f"SELECT lower(label), COUNT(*) FROM agent_labels "
                    f"WHERE lower(label) IN ({placeholders}) GROUP BY lower(label)"
                ),
                {f"k{i}": value for i, value in enumerate(wanted)},
            ).all()
            counts = {str(row[0]): int(row[1]) for row in rows}
        counts["__other__"] = int(total) - sum(counts.values())
        return counts

    # -- counters -------------------------------------------------------------

    def counting_since(self, conn: Connection) -> str | None:
        """When this database began counting, written by migration 11 itself. A hosting
        operator that sees the counters fall reads this to tell a replaced volume from a
        workspace that genuinely went quiet."""
        row = conn.execute(
            text("SELECT value FROM usage_meta WHERE key = 'counting_since'")
        ).first()
        return None if row is None else str(row[0])

    def read_counters(self, conn: Connection) -> dict[tuple[str, str], int]:
        rows = conn.execute(text("SELECT tool_name, error_code, count FROM usage_counters")).all()
        return {(str(row[0]), str(row[1])): int(row[2]) for row in rows}

    def add_counters(self, conn: Connection, deltas: dict[tuple[str, str], int]) -> None:
        """Add each delta to its row, creating the row if it is new.

        An upsert that **adds** rather than sets, so a flush that overlaps another
        process's flush cannot lose the other's counts. One statement per pair inside
        the caller's single transaction: the pairs are bounded by the
        catalog's size times the error-code table's, and this is never the hot path --
        the hot path increments a dictionary in memory and touches no connection at all.
        """
        for (tool_name, error_code), delta in deltas.items():
            conn.execute(
                text(
                    "INSERT INTO usage_counters (tool_name, error_code, count) "
                    "VALUES (:tool, :code, :delta) "
                    "ON CONFLICT(tool_name, error_code) DO UPDATE SET count = count + :delta"
                ),
                {"tool": tool_name, "code": error_code, "delta": int(delta)},
            )
