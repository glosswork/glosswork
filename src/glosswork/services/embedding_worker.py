"""The embedding worker and its durable queue (FR-Q7, docs/DATA_MODEL.md section 10).

One daemon thread, started in the application lifespan when ``GW_EMBEDDING_ENABLED``
is true and stopped gracefully at shutdown after finishing the source it is holding,
releasing the rest of its claimed batch back to the queue. It claims a batch, then
**commits once per source**, which is two decisions rather than one and both are
load-bearing:

- deleting the job in the *same* transaction as that source's rows is what keeps the
  queue count and the chunk count from ever being observably out of step, which the
  container proof depends on when it polls ``pending_jobs`` to zero and then asserts
  an exact ``indexed_chunks``;
- committing per source rather than per batch is what keeps ``BEGIN IMMEDIATE`` from
  being held across up to 2,048 vector inserts while request handlers wait on a
  5,000 ms ``busy_timeout``.

Tests drive :meth:`EmbeddingWorker.tick` synchronously rather than sleeping, and
inject the clock rather than waiting out a backoff.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Connection

from glosswork.db import Database
from glosswork.logging import get_logger
from glosswork.repositories.interfaces import (
    CommentRepository,
    RecordRepository,
    SchemaRepository,
    SearchRepository,
)
from glosswork.repositories.models import EmbeddingJobRow, IndexSource
from glosswork.services.chunking import chunk_source_text, content_hash
from glosswork.services.embedding import EmbeddingProvider
from glosswork.services.search_index import is_field_eligible
from glosswork.services.search_tuning import MAX_CHUNKS_PER_SOURCE
from glosswork.timeutil import format_datetime, utc_now

logger = get_logger(__name__)

# Jobs claimed per batch. Bounded so one claim cannot hold a large set of sources in
# ``running`` while the process dies, and so the per-batch log line stays meaningful.
BATCH_SIZE = 32

# Attempts before a job is terminal. The fifth failure makes it ``failed`` and it
# surfaces in admin settings.
MAX_ATTEMPTS = 5

# How long a ``running`` row may go untouched before reclaim assumes its worker died.
RUNNING_TIMEOUT_SECONDS = 600

# Idle poll interval. Busy, the worker drains continuously without sleeping.
IDLE_POLL_SECONDS = 1.0

# Ceiling on the exponential retry backoff, in seconds (``min(300, 2 ** attempts)``).
MAX_BACKOFF_SECONDS = 300

# How long :meth:`EmbeddingWorker.stop` waits for the worker thread to finish the source
# it is holding. Sized to **one worst-case source**, not to a batch: 64 chunks
# (``MAX_CHUNKS_PER_SOURCE``) at the 43.62 ms per full chunk in docs/PERFORMANCE.md is
# 2.8 s, plus its write. A batch is 32 of those and is minutes, which fits inside no
# platform's grace period: ``docker stop`` defaults to 10 s, and some platforms allow 5 s.
#
# **This is sized from the fastest machine this will ever run on**, and the maintainer
# accepted that cost rather than adding a setting. On a shared-CPU machine two
# or three times slower a worst-case single source takes 6 to 9 s, the join times out,
# the thread is abandoned mid-source, and the row is recovered by the startup reclaim at
# the cost of one attempt. That degradation is safe and bounded, and it is
# ``docs/DEPLOYMENT.md`` section 2a's reason for asking for a grace period of at least
# 10 seconds rather than exactly one.
STOP_GRACE_SECONDS = 5.0


@dataclass(slots=True)
class BatchResult:
    claimed: int = 0
    embedded: int = 0
    reused: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: int = 0
    reclaimed: int = 0
    released: int = 0


class EmbeddingWorker:
    def __init__(
        self,
        db: Database,
        search_repo: SearchRepository,
        schema_repo: SchemaRepository,
        record_repo: RecordRepository,
        comment_repo: CommentRepository,
        provider: EmbeddingProvider,
        clock: Callable[[], datetime] = utc_now,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        self._db = db
        self._search = search_repo
        self._schema = schema_repo
        self._records = record_repo
        self._comments = comment_repo
        self._provider = provider
        self._clock = clock
        self._batch_size = batch_size
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ----------------------------------------------------------- thread control

    def start(self) -> None:
        if self._thread is not None:
            return
        # Reclaim *everything* at startup, with no age threshold (DD-35): a process
        # killed mid-batch leaves ``running`` rows that nothing else will ever return
        # to the queue, and waiting out the ten-minute idle-poll threshold would leave
        # a workspace that scales to zero answering ``/readyz`` while carrying content
        # it has already reported as indexed. The idle poll keeps that threshold as
        # margin, not as protection for a live holder: it runs only on this worker's
        # own thread, between batches, while it holds nothing, so a row it finds
        # ``running`` was abandoned by this same process, and a pause of any length
        # (a suspended machine, a sleeping laptop) cannot make it take back its own
        # work. A second process on one database would break that, which is why the
        # entry point pins one (``workers=1``): unsupported, not impossible.
        self.reclaim_all()
        self._thread = threading.Thread(target=self._run, name="embedding-worker", daemon=True)
        self._thread.start()
        logger.info("embedding_worker_started", model=self._provider.model_id)

    def stop(self, timeout: float = STOP_GRACE_SECONDS) -> None:
        """Stop after the source in hand finishes; never mid-source, never mid-batch.

        ``embedding_worker_stopped`` is logged only when the thread actually stopped.
        Logged unconditionally after ``join`` it would be no evidence at all: a stop that
        timed out and was then ``SIGKILL``ed would leave the same line in the log as a
        clean one, and the container proof reads that line.
        """
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is None:
            return
        thread.join(timeout=timeout)
        if thread.is_alive():
            # The rows it is holding are returned to the queue by the next start's
            # reclaim, at the cost of one attempt each.
            logger.warning("embedding_worker_stop_timed_out", timeout_s=timeout)
        else:
            logger.info("embedding_worker_stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                result = self.tick()
            except Exception as exc:  # pragma: no cover - the loop must not die
                logger.error("embedding_worker_tick_failed", error=str(exc))
                self._stop.wait(IDLE_POLL_SECONDS)
                continue
            if result.claimed == 0:
                self._stop.wait(IDLE_POLL_SECONDS)

    # ------------------------------------------------------------------ the loop

    def tick(self, now: datetime | None = None) -> BatchResult:
        """One iteration of the loop: claim and process a batch, or reclaim if idle.

        Reclaim runs on the idle poll, not only at startup (section 10, rule 5). A
        restart-only reclaim cannot be caught by a test that starts a fresh worker, so
        it is reachable here on a worker that never restarted: an exception escaping
        :meth:`run_once` outside ``_process``'s handler (``fail_job``'s own write
        failing on ``busy_timeout``, say) leaves the rest of the batch ``running`` while
        the thread survives, and those rows would otherwise stay stranded for the life
        of the process and ``pending_jobs`` would never reach zero.
        """
        result = self.run_once(now)
        if result.claimed == 0:
            result.reclaimed = self.reclaim_stale(now)
        return result

    def run_once(self, now: datetime | None = None) -> BatchResult:
        started = time.monotonic()
        moment = now or self._clock()
        jobs = self._claim(moment)
        result = BatchResult(claimed=len(jobs))
        if not jobs:
            return result
        for index, job in enumerate(jobs):
            if self._stop.is_set():
                # Checkpoint, do not drain. The source in hand is always finished --
                # its write transaction is per source and interrupting it is what rule 3
                # exists to prevent -- but the rest of the batch goes back to the queue
                # rather than making the platform's grace period cover all 32 of them.
                result.released = self._release(jobs[index:])
                break
            self._process(job, moment, result)
        logger.info(
            "embedding_batch",
            claimed=result.claimed,
            embedded=result.embedded,
            reused=result.reused,
            deleted=result.deleted,
            failed=result.failed,
            skipped=result.skipped,
            released=result.released,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return result

    def _release(self, jobs: list[EmbeddingJobRow]) -> int:
        with self._db.write() as conn:
            return self._search.release_claimed(conn, jobs)

    def _claim(self, moment: datetime) -> list[EmbeddingJobRow]:
        """Claim up to ``batch_size`` pending jobs, honoring the retry backoff.

        The backoff is expressed as one cutoff timestamp per attempt count, computed
        here and compared in the claim predicate (section 10, rule 2). Doing it in SQL
        arithmetic instead would depend on SQLite's optional math functions; doing it
        in the reclaim path instead would be a
        no-op, because reclaim only ever looks at ``running`` rows.
        """
        cutoffs = [
            format_datetime(moment - timedelta(seconds=min(MAX_BACKOFF_SECONDS, 2**attempt)))
            for attempt in range(MAX_ATTEMPTS)
        ]
        with self._db.write() as conn:
            return self._search.claim_jobs(conn, self._batch_size, format_datetime(moment), cutoffs)

    def reclaim_stale(self, now: datetime | None = None) -> int:
        moment = now or self._clock()
        stale_before = format_datetime(moment - timedelta(seconds=RUNNING_TIMEOUT_SECONDS))
        with self._db.write() as conn:
            reclaimed = self._search.reclaim_stale(conn, stale_before, format_datetime(moment))
        if reclaimed:
            logger.info("embedding_jobs_reclaimed", count=reclaimed)
        return reclaimed

    def reclaim_all(self, now: datetime | None = None) -> int:
        """Startup's reclaim: every ``running`` row, whatever its age (DD-35)."""
        moment = now or self._clock()
        with self._db.write() as conn:
            reclaimed = self._search.reclaim_all(conn, format_datetime(moment))
        if reclaimed:
            logger.info("embedding_jobs_reclaimed", count=reclaimed)
        return reclaimed

    # ------------------------------------------------------------ one source

    def _process(self, job: EmbeddingJobRow, moment: datetime, result: BatchResult) -> None:
        ts = format_datetime(moment)
        try:
            with self._db.read() as conn:
                object_type_id = self._search.object_type_id_for_record(conn, job.record_id)
                if object_type_id is None:
                    source = job.source("")
                    body = None
                else:
                    source = job.source(object_type_id)
                    body = self._current_text(conn, source)
            if body is None:
                # The source stopped existing or stopped being eligible between
                # enqueue and claim -- a deleted field, a field whose type changed
                # away from text, a comment that never existed. That is not a failure
                # and must not consume attempts: the job is simply done.
                with self._db.write() as conn:
                    self._search.complete_job(conn, job.id)
                result.skipped += 1
                return

            chunks, truncated = chunk_source_text(body, self._provider)
            if truncated:
                logger.info(
                    "embedding_source_truncated",
                    record_id=source.record_id,
                    source_type=source.source_type,
                    field_key=source.field_key,
                    comment_id=source.comment_id,
                    cap=MAX_CHUNKS_PER_SOURCE,
                )
            model_id = self._provider.model_id
            triples = [
                (chunk.index, chunk.text, content_hash(model_id, chunk.text)) for chunk in chunks
            ]

            with self._db.read() as conn:
                stored = set(self._search.stored_chunk_hashes(conn, source))
            # One vector per *distinct* new hash: identical text embeds identically,
            # so a repeated paragraph costs one model call, not two.
            new_texts = {
                digest: chunk_text for _, chunk_text, digest in triples if digest not in stored
            }
            vectors_by_hash: dict[str, list[float]] = {}
            if new_texts:
                digests = list(new_texts)
                embedded = self._provider.embed_passages([new_texts[d] for d in digests])
                vectors_by_hash = dict(zip(digests, embedded, strict=True))

            with self._db.write() as conn:
                sync = self._search.sync_source_embeddings(
                    conn, source, triples, vectors_by_hash, model_id, ts
                )
                if body.strip():
                    self._search.replace_fts_row(conn, source, body)
                else:
                    self._search.delete_fts_row(conn, source)
                self._search.complete_job(conn, job.id)
            result.embedded += sync.inserted
            result.reused += sync.reused
            result.deleted += sync.deleted
        except Exception as exc:
            result.failed += 1
            with self._db.write() as conn:
                status = self._search.fail_job(
                    conn, job, f"{type(exc).__name__}: {exc}", ts, MAX_ATTEMPTS
                )
            logger.warning(
                "embedding_job_failed",
                job_id=job.id,
                record_id=job.record_id,
                attempts=job.attempts + 1,
                status=status,
                error=str(exc),
            )

    def _current_text(self, conn: Connection, source: IndexSource) -> str | None:
        """The source's text **as it is now**, never text captured at enqueue time.

        A job carries an identity, not content, so the last write always wins even
        when several edits coalesced into one job (docs/DATA_MODEL.md section 10).
        Returns ``None`` when the source should be skipped rather than indexed.
        """
        if source.source_type == "comment":
            assert source.comment_id is not None
            comment = self._comments.get_comment(conn, source.comment_id)
            # A soft-deleted comment is still indexed: exclusion is a query-time join
            # (docs/DATA_MODEL.md section 10), not index deletion.
            return None if comment is None else comment.body
        record = self._records.get_record(conn, source.record_id, include_deleted=True)
        if record is None:
            return None
        assert source.field_key is not None
        field = self._schema.get_field(conn, record.object_type_id, source.field_key)
        if field is None or not is_field_eligible(field):
            return None
        value = record.data.get(source.field_key)
        return value if isinstance(value, str) else ""
