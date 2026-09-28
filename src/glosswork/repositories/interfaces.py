"""Repository interfaces the service layer depends on (DD-2, docs/DATA_MODEL.md
section 12). One SQLite implementation exists; the seam keeps raw SQL below the
service layer so a Postgres driver stays a bounded project.

Connections are passed per call: services own transaction boundaries via
``Database.write`` so one logical write commits atomically.

``AuditRepository`` deliberately exposes no update or delete operation: audit rows
are immutable (FR-D1).
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Connection

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
    KeywordHit,
    LinkRow,
    ObjectType,
    PrincipalRow,
    Proposal,
    RecordRow,
    SavedViewRow,
    SessionRow,
    VectorPool,
)


class SchemaRepository(Protocol):
    def list_object_types(self, conn: Connection) -> list[ObjectType]: ...
    def get_object_type_by_key(self, conn: Connection, key: str) -> ObjectType | None: ...
    def get_object_type_by_id(self, conn: Connection, type_id: str) -> ObjectType | None: ...
    def insert_object_type(self, conn: Connection, row: ObjectType) -> None: ...
    def update_object_type_row(
        self, conn: Connection, type_id: str, changes: dict[str, Any]
    ) -> None: ...
    def allocate_key_seq(self, conn: Connection, type_id: str) -> int: ...
    def list_fields(self, conn: Connection, type_id: str) -> list[FieldDef]: ...
    def get_field(self, conn: Connection, type_id: str, key: str) -> FieldDef | None: ...
    def get_field_by_id(self, conn: Connection, field_id: str) -> FieldDef | None: ...
    def insert_field(self, conn: Connection, row: FieldDef) -> None: ...
    def update_field_row(
        self, conn: Connection, field_id: str, changes: dict[str, Any]
    ) -> None: ...
    def next_field_position(self, conn: Connection, type_id: str) -> int: ...
    def insert_proposal(self, conn: Connection, row: Proposal) -> None: ...
    def get_proposal(self, conn: Connection, proposal_id: str) -> Proposal | None: ...
    def list_proposals(
        self,
        conn: Connection,
        status: str | None = None,
        *,
        type_ids: Collection[str] | None = None,
        after: tuple[str, str] | None = None,
        limit: int | None = None,
    ) -> list[Proposal]: ...
    def count_proposals(
        self,
        conn: Connection,
        status: str | None = None,
        *,
        type_ids: Collection[str] | None = None,
    ) -> int: ...
    def update_proposal_row(
        self, conn: Connection, proposal_id: str, changes: dict[str, Any]
    ) -> None: ...
    def execute_index_ddl(self, conn: Connection, ddl: str) -> None: ...
    def list_index_names(self, conn: Connection, prefix: str) -> list[str]: ...


class RecordRepository(Protocol):
    def insert_record(self, conn: Connection, row: RecordRow) -> None: ...
    def get_record(
        self, conn: Connection, ref: str, include_deleted: bool = False
    ) -> RecordRow | None: ...
    def update_record_row(
        self, conn: Connection, record_id: str, changes: dict[str, Any]
    ) -> None: ...
    def run_query(
        self, conn: Connection, sql: str, params: dict[str, Any]
    ) -> list[tuple[RecordRow, dict[str, Any]]]: ...
    def run_count(self, conn: Connection, sql: str, params: dict[str, Any]) -> int: ...
    def records_for_type(
        self, conn: Connection, type_id: str, include_deleted: bool = False
    ) -> list[RecordRow]: ...
    def records_for_type_page(
        self, conn: Connection, type_id: str, after_id: str | None, limit: int
    ) -> list[RecordRow]: ...
    def find_unique_collision(
        self,
        conn: Connection,
        type_id: str,
        field_key: str,
        value: Any,
        exclude_record_id: str | None = None,
    ) -> str | None: ...
    def remove_field_key_from_type(
        self, conn: Connection, type_id: str, field_key: str
    ) -> None: ...
    def soft_delete_all_for_type(
        self, conn: Connection, type_id: str, deleted_at: str, deleted_by: str
    ) -> None: ...
    def insert_link(self, conn: Connection, row: LinkRow) -> None: ...
    def delete_link_row(self, conn: Connection, link_id: str) -> None: ...
    def get_link(
        self, conn: Connection, field_id: str, from_record_id: str, to_record_id: str
    ) -> LinkRow | None: ...
    def links_from(
        self, conn: Connection, from_record_id: str, field_id: str | None = None
    ) -> list[LinkRow]: ...
    def linked_keys_for_records(
        self, conn: Connection, from_record_ids: list[str], field_id: str
    ) -> dict[str, list[str]]: ...
    def links_to(self, conn: Connection, to_record_id: str) -> list[LinkRow]: ...
    def next_link_position(self, conn: Connection, field_id: str, from_record_id: str) -> int: ...
    def recent_field_values(
        self, conn: Connection, type_id: str, field_key: str, limit: int
    ) -> list[Any]: ...


class CommentRepository(Protocol):
    def insert_comment(self, conn: Connection, row: CommentRow) -> None: ...
    def get_comment(self, conn: Connection, comment_id: str) -> CommentRow | None: ...
    def update_comment_row(
        self, conn: Connection, comment_id: str, changes: dict[str, Any]
    ) -> None: ...
    def list_for_record(
        self,
        conn: Connection,
        record_id: str,
        include_deleted: bool = False,
        after: tuple[str, str] | None = None,
        limit: int | None = None,
    ) -> list[CommentRow]: ...
    def live_stats(self, conn: Connection, record_id: str) -> tuple[int, str | None]: ...


class SavedViewRepository(Protocol):
    """Named, shared, per-object-type views (FR-U3, docs/DATA_MODEL.md section 11)."""

    def insert(self, conn: Connection, row: SavedViewRow) -> None: ...
    def get(self, conn: Connection, view_id: str) -> SavedViewRow | None: ...
    def list_for_type(self, conn: Connection, object_type_id: str) -> list[SavedViewRow]: ...
    def update_row(self, conn: Connection, view_id: str, changes: dict[str, Any]) -> None: ...
    def delete(self, conn: Connection, view_id: str) -> None: ...
    def clear_default_for_type(
        self, conn: Connection, object_type_id: str, except_id: str | None = None
    ) -> None: ...


class AgentLabelRepository(Protocol):
    """Per-principal agent label registry (FR-I6, FR-I7)."""

    def get_label(self, conn: Connection, label_id: str) -> AgentLabelRow | None: ...
    def labels_by_ids(self, conn: Connection, ids: list[str]) -> list[AgentLabelRow]: ...
    def find_label(
        self, conn: Connection, principal_id: str, label: str
    ) -> AgentLabelRow | None: ...
    def insert_label(self, conn: Connection, row: AgentLabelRow) -> None: ...
    def record_use(self, conn: Connection, label_id: str, seen_at: str) -> None: ...
    def list_labels(
        self, conn: Connection, principal_id: str | None = None
    ) -> list[AgentLabelRow]: ...
    def search_labels(
        self,
        conn: Connection,
        q: str | None,
        accessible_type_ids: list[str] | None,
        include_untyped: bool,
        limit: int,
    ) -> list[AgentLabelRow]: ...
    def update_label(
        self,
        conn: Connection,
        label_id: str,
        display_name: str | None,
        description: str | None,
    ) -> None: ...
    def count_labels(self, conn: Connection) -> int: ...


class AttachmentRepository(Protocol):
    def insert_attachment(self, conn: Connection, row: AttachmentRow) -> None: ...
    def get_attachment(self, conn: Connection, attachment_id: str) -> AttachmentRow | None: ...
    def get_attachments(self, conn: Connection, ids: list[str]) -> list[AttachmentRow]: ...


class BlobRepository(Protocol):
    """Content-addressed byte storage on the mounted volume (docs/DATA_MODEL.md
    section 8). Writing the same content twice is idempotent: one file on disk.

    ``delete``, ``iter_hashes`` and ``path_for`` exist because the first three methods
    alone would give the orphan sweep (FR-P2) no way to remove anything and the backup
    artifact (FR-P8, DD-36) no way to enumerate the tree.
    """

    def write(self, sha256: str, content: bytes) -> None: ...
    def read(self, sha256: str) -> bytes: ...
    def open_stream(self, sha256: str) -> Iterator[bytes]: ...
    def delete(self, sha256: str) -> bool: ...
    def iter_hashes(self) -> Iterator[str]: ...
    def path_for(self, sha256: str) -> Path: ...


class BackupRepository(Protocol):
    """The consistent database snapshot behind FR-P8 (DD-36).

    One method, and it exists as a repository at all because DD-2 keeps raw SQL out of
    the service layer: ``VACUUM INTO`` is raw SQL, even though it reads more like an
    operation than a query.
    """

    def vacuum_into(self, raw_connection: Any, destination: Path) -> None: ...


class BlobReferenceRepository(Protocol):
    """Which content hashes the ``attachments`` table still references (FR-P2).

    Separate from :class:`AttachmentRepository` because the orphan sweep asks a
    question no attachment caller asks: not "what does this row point at" but "what
    does *anything* point at". Deduplication is what makes the distinction load
    bearing -- two rows can share one file, so only the absence of every referencing
    row makes a blob deletable.
    """

    def referenced_hashes(self, conn: Connection) -> set[str]: ...
    def reference_count(self, conn: Connection, sha256: str) -> int: ...


class GrantRepository(Protocol):
    """``object_type_grants`` (DD-11, docs/DATA_MODEL.md section 2).

    The **only** module allowed to name that table in SQL (DD-2): the raw
    SQL is here, the authorization decision is in
    :class:`~glosswork.services.access.AccessService`, and nothing else reads either.

    ``list_for_principal`` is what makes ``accessible_type_ids`` a bounded read rather
    than one lookup per object type: it is answered by
    ``ix_object_type_grants_principal`` and the whole map is composed in memory.
    """

    def get(self, conn: Connection, object_type_id: str, principal_id: str) -> GrantRow | None: ...
    def list_for_type(self, conn: Connection, object_type_id: str) -> list[GrantRow]: ...
    def list_for_principal(self, conn: Connection, principal_id: str) -> list[GrantRow]: ...
    def upsert(self, conn: Connection, row: GrantRow) -> GrantRow: ...
    def delete(self, conn: Connection, object_type_id: str, principal_id: str) -> bool: ...


class RecordAttachmentRepository(Protocol):
    """``record_attachments`` (docs/DATA_MODEL.md section 8).

    The materialized reverse index from an attachment to the records referencing it.
    ``attachments`` carries no record id and no object type id, and an attachment
    field's value is a list of attachment ids inside ``records.data``, so without this
    there is no object type to check a level against.
    """

    def replace_for_record(
        self, conn: Connection, record_id: str, refs: dict[str, list[str]]
    ) -> None: ...
    def object_type_ids_for_attachment(self, conn: Connection, attachment_id: str) -> set[str]: ...
    def object_type_ids_for_attachments(
        self, conn: Connection, attachment_ids: list[str]
    ) -> dict[str, set[str]]: ...
    def rows_for_record(self, conn: Connection, record_id: str) -> list[tuple[str, str]]: ...


class AuditRepository(Protocol):
    """Append and read only. Audit rows are never updated or deleted (FR-D1)."""

    def append(self, conn: Connection, events: list[AuditEvent]) -> list[int]: ...
    def for_record(
        self,
        conn: Connection,
        record_id: str,
        field_key: str | None = None,
        limit: int | None = None,
        after_id: int | None = None,
    ) -> list[AuditEvent]: ...
    def record_update_events(self, conn: Connection, record_id: str) -> list[AuditEvent]: ...
    def get_event(self, conn: Connection, event_id: int) -> AuditEvent | None: ...
    def for_request(self, conn: Connection, request_id: str) -> list[AuditEvent]: ...
    def latest_id(self, conn: Connection) -> int: ...
    def since(
        self,
        conn: Connection,
        cursor: int,
        object_type_ids: list[str] | None,
        limit: int,
        accessible_type_ids: list[str] | None = None,
        include_untyped: bool = True,
    ) -> list[AuditEvent]: ...
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
    ) -> list[AuditEvent]: ...


class PrincipalRepository(Protocol):
    """``principals`` (docs/DATA_MODEL.md section 2, FR-I3, FR-I5). Principals are
    deactivated, never deleted: every audit row, record ``created_by``, and comment
    author is a foreign key onto this table."""

    def get(self, conn: Connection, principal_id: str) -> PrincipalRow | None: ...
    def get_by_email(self, conn: Connection, email: str) -> PrincipalRow | None: ...
    def get_by_external(
        self, conn: Connection, auth_provider: str, external_id: str
    ) -> PrincipalRow | None: ...
    def insert(self, conn: Connection, row: PrincipalRow) -> None: ...
    def update_row(self, conn: Connection, principal_id: str, changes: dict[str, Any]) -> None: ...
    def list_principals(
        self, conn: Connection, principal_type: str | None = None, include_inactive: bool = True
    ) -> list[PrincipalRow]: ...
    def count_active_admin_users(self, conn: Connection) -> int: ...
    def count_active_users(self, conn: Connection) -> int: ...
    def count_users(self, conn: Connection) -> int: ...
    def principal_exists(self, conn: Connection, principal_id: str) -> bool: ...
    def principals_by_ids(self, conn: Connection, ids: list[str]) -> list[PrincipalRow]: ...
    def search_principals(
        self,
        conn: Connection,
        q: str | None,
        principal_type: str | None,
        include_inactive: bool,
        limit: int,
    ) -> list[PrincipalRow]: ...
    def find_by_display_name(self, conn: Connection, display_name: str) -> list[PrincipalRow]: ...


class AccessTokenRepository(Protocol):
    """``access_tokens`` (docs/DATA_MODEL.md section 2, FR-I4). Lookup is by the
    sha256 of the presented secret; the plaintext never reaches this layer's storage."""

    def get(self, conn: Connection, token_id: str) -> AccessTokenRow | None: ...
    def get_by_hash(self, conn: Connection, token_hash: str) -> AccessTokenRow | None: ...
    def insert(self, conn: Connection, row: AccessTokenRow) -> None: ...
    def update_row(self, conn: Connection, token_id: str, changes: dict[str, Any]) -> None: ...
    def list_for_principal(self, conn: Connection, principal_id: str) -> list[AccessTokenRow]: ...
    def list_live_by_scope(
        self, conn: Connection, principal_id: str, scopes: list[str], now: str
    ) -> list[AccessTokenRow]: ...
    def touch_last_used(self, conn: Connection, token_id: str, now: str) -> None: ...

    # DD-16. An upload ticket is a row in this table, not a new credential system, so the
    # three methods it needs sit beside the others rather than in a repository of their own.
    # ``list_for_principal`` excludes capability rows; ``list_live_by_scope`` deliberately
    # does not, which is what makes a password reset's revoke-everything reach a ticket.
    def list_capability_tokens(self, conn: Connection) -> list[AccessTokenRow]: ...
    def delete_spent_capability_tokens(self, conn: Connection, now: str) -> int: ...
    def consume_capability(self, conn: Connection, token_id: str, now: str) -> int: ...


class SessionRepository(Protocol):
    """``sessions`` (docs/DATA_MODEL.md section 2, FR-A3, DD-9). Lookup is by
    the sha256 of the presented cookie value; the plaintext never reaches this layer's
    storage, exactly as with :class:`AccessTokenRepository`."""

    def get(self, conn: Connection, session_id: str) -> SessionRow | None: ...
    def get_by_hash(self, conn: Connection, session_hash: str) -> SessionRow | None: ...
    def insert(self, conn: Connection, row: SessionRow) -> None: ...
    def update_row(self, conn: Connection, session_id: str, changes: dict[str, Any]) -> None: ...
    def delete(self, conn: Connection, session_id: str) -> None: ...
    def delete_by_hash(self, conn: Connection, session_hash: str) -> None: ...
    def list_live_for_principal(self, conn: Connection, principal_id: str) -> list[SessionRow]: ...
    def touch_last_seen(self, conn: Connection, session_id: str, now: str) -> None: ...


class SearchRepository(Protocol):
    """Keyword and vector index storage (docs/DATA_MODEL.md section 10, DD-2, DD-31).

    The one module whose implementation may name ``vec0``, ``vec_embeddings``, or
    ``fts_content`` -- grep-backed by ``tests/test_search_repository_isolation.py`` --
    so migrating off either index stays a one-module change (DD-2, made a
    constraint rather than a sentence). Fusion is **not** here: it
    is a pure function in the search service (DD-33). It holds the indexing half, the
    retrieval half (``keyword_search``, ``vector_search``) and the bulk enqueue behind
    the re-index trigger (``enqueue_all``).

    Every method takes a connection, because index maintenance runs inside the domain
    write's own transaction: the audit row, the FTS row, and the queue job commit
    together or not at all (docs/DATA_MODEL.md section 14).
    """

    # -- keyword index -------------------------------------------------------
    def replace_fts_row(self, conn: Connection, source: IndexSource, body: str) -> None: ...
    def delete_fts_row(self, conn: Connection, source: IndexSource) -> None: ...

    # -- vector index --------------------------------------------------------
    def stored_chunk_hashes(self, conn: Connection, source: IndexSource) -> list[str]: ...
    def sync_source_embeddings(
        self,
        conn: Connection,
        source: IndexSource,
        chunks: list[tuple[int, str, str]],
        vectors_by_hash: dict[str, list[float]],
        model_id: str,
        now: str,
    ) -> EmbeddingSyncResult: ...
    def source_rows(
        self, conn: Connection, source: IndexSource, include_vectors: bool = False
    ) -> list[EmbeddingRow]: ...

    # -- purge ---------------------------------------------------------------
    def purge_field(self, conn: Connection, object_type_id: str, field_key: str) -> None: ...
    def purge_object_type(self, conn: Connection, object_type_id: str) -> None: ...

    # -- durable queue -------------------------------------------------------
    def enqueue(self, conn: Connection, source: IndexSource, now: str) -> None: ...
    def claim_jobs(
        self, conn: Connection, limit: int, now: str, backoff_cutoffs: list[str]
    ) -> list[EmbeddingJobRow]: ...
    def reclaim_stale(self, conn: Connection, stale_before: str, now: str) -> int: ...
    def reclaim_all(self, conn: Connection, now: str) -> int: ...
    def release_claimed(self, conn: Connection, jobs: list[EmbeddingJobRow]) -> int: ...
    def complete_job(self, conn: Connection, job_id: int) -> None: ...
    def fail_job(
        self, conn: Connection, job: EmbeddingJobRow, error: str, now: str, max_attempts: int
    ) -> str: ...

    def enqueue_all(self, conn: Connection, now: str) -> int: ...

    # -- retrieval -----------------------------------------------------------
    def keyword_search(
        self, conn: Connection, fts_query: str, object_type_ids: list[str], k: int
    ) -> list[KeywordHit]: ...
    def vector_search(
        self,
        conn: Connection,
        query_vector: list[float],
        object_type_ids: list[str],
        model_id: str,
        k: int,
    ) -> VectorPool: ...

    # -- observation ---------------------------------------------------------
    def counts(self, conn: Connection) -> IndexCounts: ...
    def failed_jobs(self, conn: Connection) -> list[FailedJobRow]: ...
    def stale_chunk_count(self, conn: Connection, model_id: str) -> int: ...
    def object_type_id_for_record(self, conn: Connection, record_id: str) -> str | None: ...


class UsageRepository(Protocol):
    """Operator usage counts and the tool-call counters (DD-39, FR-P10).

    Every method returns numbers, or numbers keyed by a value the caller supplied.
    None of them returns a string a tenant chose.
    """

    def count_records(self, conn: Connection) -> tuple[int, int]: ...
    def count_attachments(self, conn: Connection) -> tuple[int, int, int]: ...
    def count_schema(self, conn: Connection) -> tuple[int, int]: ...
    def sum_agent_label_calls(self, conn: Connection) -> int: ...
    def count_labels_by_harness(
        self, conn: Connection, known: Collection[str]
    ) -> dict[str, int]: ...
    def counting_since(self, conn: Connection) -> str | None: ...
    def read_counters(self, conn: Connection) -> dict[tuple[str, str], int]: ...
    def add_counters(self, conn: Connection, deltas: dict[tuple[str, str], int]) -> None: ...
