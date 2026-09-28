"""Index maintenance on every write path (FR-Q3, FR-C7, docs/DATA_MODEL.md section 10).

This service is called **inside** the domain write's own ``db.write()`` transaction,
never after it. That is what makes the audit row, the keyword row, and the queue job
commit together or not at all (docs/DATA_MODEL.md section 14): a record update that
rolls back leaves nothing behind claiming it was indexed.

Index maintenance is deliberately **not** a domain write path. ``embeddings``,
``vec_embeddings``, and ``embedding_jobs`` are not audited entities (FR-D3), so nothing here
emits an audit row or carries an ``ActorContext``, and the DD-4 non-negotiable is not being
quietly bent: the *trigger* of a re-index is a domain action by an administrator and is
audited with its actor, while the derived rows this service keeps in step are not. That
trigger is :meth:`SearchIndexService.reindex`: it takes an ``ActorContext`` and writes one
``reindex_requested`` audit row alongside the bulk enqueue, in the same transaction.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime

from sqlalchemy import Connection

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.errors import FeatureDisabledError
from glosswork.logging import get_logger
from glosswork.repositories.interfaces import (
    AuditRepository,
    RecordRepository,
    SearchRepository,
)
from glosswork.repositories.models import (
    CommentRow,
    FailedJobRow,
    FieldDef,
    IndexSource,
    ObjectType,
    RecordRow,
)
from glosswork.services.base import make_event
from glosswork.services.embedding import EmbeddingProvider
from glosswork.timeutil import format_datetime, utc_now

logger = get_logger(__name__)

# The only field types whose text is indexed at all. The ``embed`` flag gates within
# these; it cannot promote a type outside them (docs/DATA_MODEL.md section 10).
INDEXABLE_FIELD_TYPES = frozenset({"short_text", "long_text"})

# How many records one fan-out transaction indexes before committing and letting other
# writers in. Chosen from the measurement rather than by feel: the unbatched
# fan-out over 90,000 records held the writer lock for 22.1 seconds, so 2,000 records
# is roughly half a second of lock per batch, which is comfortably inside the 5,000 ms
# ``busy_timeout`` every other writer is waiting against.
FAN_OUT_BATCH_SIZE = 500

# How long the fan-out pauses between batches, holding no lock at all.
#
# Batching alone did not work, which is worth recording because it is counter-intuitive.
# With 2,000-record batches and no pause, the fan-out still failed ten concurrent writes
# over 21 seconds: SQLite's busy handler retries a blocked ``BEGIN IMMEDIATE`` on its own
# schedule, and a fan-out that releases the write lock and immediately re-takes it leaves
# almost no window for a retry to land in. The lock was nominally released forty-five
# times and practically never available. Committing more often is necessary and is not
# sufficient; the writer has to actually stand back.
#
# 20 ms was measured, not guessed, against four writer threads in a tight loop (a load
# deliberately harsher than any real deployment): 10 ms left one write still failing,
# 20 ms left none, and let 11,071 writes through during the fan-out where the unbatched
# version had allowed 516. The cost is wall clock -- the fan-out over 90,000 records
# goes from 21 s to about 46 s -- which is the right trade for a rare administrative
# action that must not take anyone else's writes down with it.
FAN_OUT_BATCH_PAUSE_S = 0.02

# Fan-outs registered during a schema change, to be run once it has committed.
#
# A context variable rather than a return value threaded through
# ``_apply_field_changes`` and ``_apply_type_change`` and their callers: the work is
# discovered four call levels below the method that owns the transaction, and the only
# thing that needs to cross that distance is "there is deferred work". Same instrument
# the request middleware already uses for log context, and correct under threads
# because each request handler runs in its own context.
_PENDING_FAN_OUTS: ContextVar[list[tuple[ObjectType, FieldDef]] | None] = ContextVar(
    "gw_pending_fan_outs", default=None
)


@contextmanager
def collect_fan_outs() -> Iterator[list[tuple[ObjectType, FieldDef]]]:
    """Collect fan-outs a schema change registers, for the caller to run after commit.

    Used by ``SchemaService``'s two public entry points that can move a field across
    the eligibility line. The list is populated inside the transaction and consumed
    after it, which is the whole point: a fan-out batch opens its own write connection,
    and SQLite allows one writer, so running one inside the still-open schema
    transaction would deadlock against it.
    """
    pending: list[tuple[ObjectType, FieldDef]] = []
    token = _PENDING_FAN_OUTS.set(pending)
    try:
        yield pending
    finally:
        _PENDING_FAN_OUTS.reset(token)


def is_field_eligible(field: FieldDef) -> bool:
    """FR-Q3.

    A field source is eligible when its type is ``short_text`` or ``long_text``
    **and** its ``embed`` flag is true. The rule is the flag, not the type:
    ``services/schema.py`` already accepts an ``embed`` change on a field of any type
    and only *defaults* it from the type, so an administrator turning ``embed`` off on
    a ``long_text`` field must actually turn indexing off for that field, and setting
    it on a ``number`` field must not index anything.

    The flag gates the keyword row as well as the embedding rows. FR-Q3 says "indexed
    content", not "embedded content", and DD-33's golden class (E) negative -- a term
    present only in a non-opted-in field must not be found -- is false in hybrid mode
    if the keyword arm still holds that field's text.
    """
    return field.type in INDEXABLE_FIELD_TYPES and field.embed


@dataclass(slots=True)
class IndexStatus:
    """What ``GET /api/v1/admin/search-index`` reports (FR-Q7, FR-P5)."""

    pending_jobs: int
    running_jobs: int
    indexed_chunks: int
    stale_chunks: int
    embedding_model: str
    semantic_enabled: bool
    failed_jobs: list[FailedJobRow] = dataclass_field(default_factory=list)


class SearchIndexService:
    def __init__(
        self,
        db: Database,
        search_repo: SearchRepository,
        record_repo: RecordRepository,
        *,
        embedding_enabled: bool,
        configured_model: str,
        provider: EmbeddingProvider | None = None,
        audit_repo: AuditRepository | None = None,
    ) -> None:
        self._db = db
        self._search = search_repo
        self._records = record_repo
        # Only the re-index trigger audits; optional so a construction without it in tests
        # keeps working, required by build_services.
        self._audit = audit_repo
        self._embedding_enabled = embedding_enabled
        self._configured_model = configured_model
        self._provider = provider

    @property
    def embedding_enabled(self) -> bool:
        return self._embedding_enabled

    # ------------------------------------------------------- record write paths

    def index_record_fields(
        self,
        conn: Connection,
        object_type: ObjectType,
        fields_by_key: dict[str, FieldDef],
        record: RecordRow,
        keys: list[str],
        now: datetime | None = None,
    ) -> None:
        """Maintain the index for the named fields of one record.

        ``keys`` is what actually changed, so an update that leaves a field alone
        enqueues nothing for it. Callers pass every populated key on create and the
        changed keys on update; a key that is not an eligible field is ignored here
        rather than filtered by every caller.
        """
        ts = format_datetime(now or utc_now())
        for key in keys:
            field = fields_by_key.get(key)
            if field is None or not is_field_eligible(field):
                continue
            source = IndexSource.field(record.id, object_type.id, key)
            value = record.data.get(key)
            self._apply_source(conn, source, value if isinstance(value, str) else "", ts)

    def index_comment(
        self,
        conn: Connection,
        record: RecordRow,
        comment: CommentRow,
        now: datetime | None = None,
    ) -> None:
        """Every comment body is eligible (FR-C7); comments carry no ``embed`` flag."""
        ts = format_datetime(now or utc_now())
        source = IndexSource.comment(record.id, record.object_type_id, comment.id)
        self._apply_source(conn, source, comment.body, ts)

    def _apply_source(self, conn: Connection, source: IndexSource, body: str, ts: str) -> None:
        if body.strip():
            self._search.replace_fts_row(conn, source, body)
        else:
            # A field cleared to empty must stop matching immediately. Empty text
            # produces no chunk and no keyword row (docs/DATA_MODEL.md section 10);
            # the queued job clears the embedding rows the same way, by chunking the
            # now-empty value to nothing.
            self._search.delete_fts_row(conn, source)
        if self._embedding_enabled:
            self._search.enqueue(conn, source, ts)

    # ------------------------------------------------------- schema write paths

    def sync_field_eligibility(
        self,
        conn: Connection,
        object_type: ObjectType,
        field: FieldDef,
        now: datetime | None = None,
    ) -> None:
        """Bring a field's index rows in line with what it is now.

        One method covers every schema change that can move a field across the
        eligibility line: ``embed`` toggled either way, and an approved
        ``change_field_type`` into or out of the text types.

        **The two directions are handled differently, and a measurement is why.** Becoming
        ineligible is a purge: two indexed deletes, fast, and it happens inline in the
        schema change's own transaction so the field stops matching the moment the
        change commits.

        Becoming *eligible* is a fan-out of one keyword row and one queue job per live
        record of the type, which cannot run inline. Measured before DD-34
        bounded it, on the seeded 200,000-record corpus, turning ``embed`` on for a field of the
        90,000-record type held SQLite's single writer lock for **22.1 seconds and
        failed 16 concurrent writes** with "database is locked"; the purge side of
        ``delete_field`` took 7.0 seconds and failed 4. That is not a slow
        administrative action, it is an outage for everyone else writing at the time.

        So an eligible field is *registered* here and fanned out by
        :meth:`fan_out_field` after the schema change commits, in bounded batches that
        release the lock between them. :func:`collect_fan_outs` is how the schema
        service picks the work up; see its docstring for why it is a context variable
        rather than a return value.
        """
        if not is_field_eligible(field):
            self._search.purge_field(conn, object_type.id, field.key)
            return
        pending = _PENDING_FAN_OUTS.get()
        if pending is None:
            # No collector active: fall back to the unbatched behaviour and fan out inline
            # on the caller's own connection. Unbounded, but correct, and it is what an
            # isolated caller (a test, a future one) gets by not opting in. It must not
            # call ``fan_out_field`` here: that opens its own write connection, and
            # SQLite allows one writer, so it would deadlock against the transaction
            # this method was handed.
            ts = format_datetime(now or utc_now())
            for record in self._records.records_for_type(conn, object_type.id):
                value = record.data.get(field.key)
                source = IndexSource.field(record.id, object_type.id, field.key)
                self._apply_source(conn, source, value if isinstance(value, str) else "", ts)
            return
        pending.append((object_type, field))

    def fan_out_field(
        self,
        object_type: ObjectType,
        field: FieldDef,
        now: datetime | None = None,
        batch_size: int = FAN_OUT_BATCH_SIZE,
        pause_s: float = FAN_OUT_BATCH_PAUSE_S,
    ) -> int:
        """Index every live record of ``object_type`` for ``field``, in batches.

        Each batch is its own transaction, so SQLite's single writer lock is released
        between them and other writers interleave instead of queueing behind one
        multi-second hold. Returns the number of records indexed.

        **This is deliberately not atomic with the schema change**, and that is the
        trade made knowingly. Index maintenance is not a domain write path here: it
        emits no audit rows and takes no ``ActorContext``, so there is
        no attribution to tear. A process that dies midway leaves some records indexed
        and some not, which is visible as a reduced ``indexed_chunks`` and is repaired
        by the FR-Q9 re-index button; the alternative, a 22-second window in which
        every other write fails, is worse in every deployment.

        Opens its own connections rather than taking one, because the caller's
        transaction must already be committed before the first batch runs: SQLite
        allows one writer, so a batch opened inside the schema change's transaction
        would deadlock against it.
        """
        ts = format_datetime(now or utc_now())
        last_id: str | None = None
        indexed = 0
        while True:
            with self._db.write() as conn:
                batch = self._records.records_for_type_page(
                    conn, object_type.id, after_id=last_id, limit=batch_size
                )
                for record in batch:
                    value = record.data.get(field.key)
                    source = IndexSource.field(record.id, object_type.id, field.key)
                    self._apply_source(conn, source, value if isinstance(value, str) else "", ts)
                if batch:
                    last_id = batch[-1].id
                    indexed += len(batch)
            if len(batch) < batch_size:
                logger.info(
                    "field_fan_out_complete",
                    object_type=object_type.key,
                    field=field.key,
                    records=indexed,
                    batch_size=batch_size,
                )
                return indexed
            # Stand back, holding nothing, so a writer blocked on BEGIN IMMEDIATE has a
            # window its busy handler can actually retry into. See
            # FAN_OUT_BATCH_PAUSE_S: without this the batching is cosmetic.
            if pause_s:
                time.sleep(pause_s)

    def purge_field(self, conn: Connection, object_type_id: str, field_key: str) -> None:
        """A deleted field's rows go, whatever its ``embed`` flag said."""
        self._search.purge_field(conn, object_type_id, field_key)

    def purge_object_type(self, conn: Connection, object_type_id: str) -> None:
        """A deleted object type takes its records' field rows and comment rows with it."""
        self._search.purge_object_type(conn, object_type_id)

    # ---------------------------------------------------------------- re-index

    def reindex(self, actor: ActorContext, now: datetime | None = None) -> int:
        """Enqueue every ``search_sources`` row for re-embedding (FR-Q9), as one audited
        administrative action.

        Soft-deleted records and comments are included: ``restore_record`` enqueues
        nothing, so after a model swap a record deleted before the re-index and
        restored after it would otherwise never be semantically findable again. The
        ``failed`` rows go first, the insert is ``ON CONFLICT DO NOTHING`` against a
        job already pending, and the ``reindex_requested`` audit row carrying the actor
        and the count commits in the same transaction. Nothing is deleted ahead of
        time: rows are replaced per source as the worker reaches them, which is what
        makes a re-index run without downtime (the FR-Q9 test).

        Refused with ``feature_disabled`` when embedding is off: the
        alternative is a queue no worker drains.
        """
        if not self._embedding_enabled:
            raise FeatureDisabledError(
                "Re-indexing is disabled on this deployment (GW_EMBEDDING_ENABLED is "
                "false): no worker would drain the queue. Set GW_EMBEDDING_ENABLED=true "
                "and restart, then re-index from Settings.",
                feature="reindex",
                setting="GW_EMBEDDING_ENABLED",
            )
        assert self._audit is not None, "reindex needs an AuditRepository"
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            enqueued = self._search.enqueue_all(conn, ts)
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="search_index",
                        entity_id="search_index",
                        action="reindex_requested",
                        new_value={"enqueued": enqueued},
                    )
                ],
            )
        logger.info("reindex_requested", enqueued=enqueued, principal_id=actor.principal_id)
        return enqueued

    # ------------------------------------------------------------- observation

    def status(self) -> IndexStatus:
        """The read behind ``GET /api/v1/admin/search-index``.

        With embedding disabled there is no provider to compare ``model_id`` against,
        so ``stale_chunks`` is computed against the configured ``GW_EMBEDDING_MODEL``
        name and that configured value is what ``embedding_model`` reports, alongside
        ``semantic_enabled: false``. Returning null instead would make the field's type
        conditional on a setting for every consumer (docs/DATA_MODEL.md section 13).
        """
        model_id = self._provider.model_id if self._provider is not None else self._configured_model
        with self._db.read() as conn:
            counts = self._search.counts(conn)
            return IndexStatus(
                pending_jobs=counts.pending_jobs,
                running_jobs=counts.running_jobs,
                failed_jobs=self._search.failed_jobs(conn),
                indexed_chunks=counts.indexed_chunks,
                stale_chunks=self._search.stale_chunk_count(conn, model_id),
                embedding_model=model_id,
                semantic_enabled=self._embedding_enabled,
            )
