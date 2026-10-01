"""The embedding worker and its durable queue (FR-Q7, docs/DATA_MODEL.md section 10).

Everything here drives ``run_once``/``tick`` synchronously and injects the clock;
nothing sleeps. The five queue-state rules of docs/DATA_MODEL.md section 10 each have
a test below, and two of them are written the way they are *because the obvious
version of the test passes under the broken implementation too*:

- vector reuse asserts the stored vector is **byte-identical** across an edit, not
  merely that the provider went uncalled;
- reclaim is exercised on a worker that **never restarted**, because a test that
  starts a fresh worker cannot catch a row stranded by a long-lived one.
"""

from __future__ import annotations

import ast
import inspect
import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from glosswork.db import Database
from glosswork.repositories.sqlite import SqliteSearchRepository
from glosswork.services import ServiceBundle
from glosswork.services.embedding_worker import (
    MAX_ATTEMPTS,
    RUNNING_TIMEOUT_SECONDS,
    STOP_GRACE_SECONDS,
    EmbeddingWorker,
)
from glosswork.timeutil import format_datetime
from tests.conftest import KITCHEN_SINK_FIELDS, make_actor, make_worker
from tests.search_support import (
    FakeClock,
    FakeEmbeddingProvider,
    comment_source,
    drain,
    field_source,
    fts_body,
    index_counts,
    job_rows,
    jobs_for,
    stored_rows,
)


def paragraph(tag: str, words: int = 130) -> str:
    """A paragraph long enough that two never pack into one chunk.

    The fake provider tokenizes on whitespace, so 130 "tokens" each means any two
    paragraphs exceed the 256-token chunk limit and every paragraph becomes its own
    chunk. That is what makes "edit one paragraph, re-embed one chunk" observable at
    all -- with short paragraphs the whole field is a single chunk and the reuse
    assertions would be vacuous.
    """
    return " ".join(f"{tag}w{i}" for i in range(words))


THREE_PARAGRAPHS = "\n\n".join([paragraph("alpha"), paragraph("beta"), paragraph("gamma")])
EDITED = "\n\n".join([paragraph("alpha"), paragraph("BETA"), paragraph("gamma")])


@pytest.fixture
def sink(search_services: ServiceBundle) -> None:
    search_services.schema.create_object_type(
        make_actor(),
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the test suite; exercises every "
        "field type the platform supports.",
        key_prefix="ART",
        fields=KITCHEN_SINK_FIELDS,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def worker(
    db: Database, search_services: ServiceBundle, clock: FakeClock, sink: None
) -> EmbeddingWorker:
    return make_worker(db, search_services, clock)


@pytest.fixture
def record(search_services: ServiceBundle, sink: None) -> Any:
    return search_services.records.create_record(
        make_actor(), "artifact", {"title": "Renewal", "summary": THREE_PARAGRAPHS}
    )


# ------------------------------------------------------------ the happy path


def test_a_drain_indexes_every_chunk_and_deletes_every_job(
    db: Database, worker: EmbeddingWorker, clock: FakeClock, record: Any
) -> None:
    drain(worker, clock)
    rows = stored_rows(db, field_source(record, "summary"))
    assert len(rows) == 3
    assert [r.chunk_index for r in rows] == [0, 1, 2]
    assert {r.model_id for r in rows} == {"fake-model@test"}
    assert job_rows(db) == []
    assert index_counts(db).pending_jobs == 0


def test_the_worker_re_reads_the_current_text_not_the_text_at_enqueue_time(
    db: Database,
    search_services: ServiceBundle,
    worker: EmbeddingWorker,
    clock: FakeClock,
    record: Any,
) -> None:
    """A job carries an identity, never content, so the last write always wins.

    Three edits coalesce into one job; the text that gets indexed must be the third.
    """
    for tag in ("first", "second", "third"):
        search_services.records.update_record(make_actor(), record.key, {"summary": paragraph(tag)})
    assert len(jobs_for(db, field_source(record, "summary"))) == 1

    drain(worker, clock)
    rows = stored_rows(db, field_source(record, "summary"))
    assert len(rows) == 1
    assert rows[0].chunk_text == paragraph("third")


# -------------------------------------------------- reuse, and byte-identity


def test_editing_one_paragraph_re_embeds_only_that_chunk_and_leaves_the_others_byte_identical(
    db: Database,
    search_services: ServiceBundle,
    worker: EmbeddingWorker,
    clock: FakeClock,
    fake_provider: FakeEmbeddingProvider,
    record: Any,
) -> None:
    """The reuse test, written so the broken implementation cannot pass it.

    ``embeddings.id`` *is* the ``vec_embeddings`` rowid. An implementation that
    deletes a source's rows and re-inserts them calls the provider exactly as few
    times as this one does -- it reuses nothing but re-embeds nothing either, because
    it would still only embed changed text -- yet it silently assigns new ids and
    orphans every unchanged chunk's vector. Asserting "the provider was not called"
    passes under both. Only reading the stored vectors back and comparing them byte
    for byte discriminates.
    """
    drain(worker, clock)
    before = {r.chunk_index: r for r in stored_rows(db, field_source(record, "summary"), True)}
    assert len(before) == 3
    fake_provider.embedded_passages.clear()

    search_services.records.update_record(make_actor(), record.key, {"summary": EDITED})
    drain(worker, clock)

    # Exactly one chunk's text changed, so exactly one model call was made.
    assert fake_provider.embedded_passages == [paragraph("BETA")]

    after = {r.chunk_index: r for r in stored_rows(db, field_source(record, "summary"), True)}
    assert len(after) == 3
    for index in (0, 2):
        assert after[index].id == before[index].id, (
            f"chunk {index} was re-inserted with a new id, orphaning its vector"
        )
        assert after[index].vector == before[index].vector, (
            f"chunk {index}'s stored vector changed although its text did not"
        )
        assert after[index].content_hash == before[index].content_hash
    assert after[1].chunk_text == paragraph("BETA")
    assert after[1].id != before[1].id


def test_a_source_containing_the_same_paragraph_twice_matches_hashes_as_a_multiset(
    db: Database,
    search_services: ServiceBundle,
    worker: EmbeddingWorker,
    clock: FakeClock,
    fake_provider: FakeEmbeddingProvider,
    sink: None,
) -> None:
    """``content_hash`` is not unique within a source: a repeated paragraph repeats it.

    N stored rows carrying a hash may satisfy at most N new chunks carrying it, so
    dropping one of two identical paragraphs must delete exactly one row.
    """
    repeated = "\n\n".join([paragraph("same"), paragraph("same"), paragraph("other")])
    row = search_services.records.create_record(
        make_actor(), "artifact", {"title": "Repeats", "summary": repeated}
    )
    drain(worker, clock)
    source = field_source(row, "summary")
    stored = stored_rows(db, source, True)
    assert len(stored) == 3
    assert stored[0].content_hash == stored[1].content_hash
    # One model call for two identical chunks: identical text embeds identically.
    assert sorted(fake_provider.embedded_passages) == sorted(
        [paragraph("same"), paragraph("other")]
    )

    search_services.records.update_record(
        make_actor(), row.key, {"summary": "\n\n".join([paragraph("same"), paragraph("other")])}
    )
    drain(worker, clock)
    after = stored_rows(db, source, True)
    assert len(after) == 2
    assert after[0].content_hash == stored[0].content_hash
    assert after[0].vector == stored[0].vector


# ------------------------------------------------------------- atomicity


def test_the_job_deletion_and_the_row_writes_are_one_transaction(
    db: Database,
    search_services: ServiceBundle,
    clock: FakeClock,
    record: Any,
    search_services_provider: FakeEmbeddingProvider,
) -> None:
    """Section 10, rule 3: the queue count and the chunk count are never out of step.

    The container proof depends on exactly this: it polls ``pending_jobs`` to zero and
    *then* asserts an exact ``indexed_chunks``. If the rows and the job deletion
    committed separately, there would be a window where the queue reads empty and the
    chunks are not all there yet, and the proof would be flaky rather than wrong.

    Observed from a second connection during the worker's write transaction. Under
    WAL that connection sees the pre-commit snapshot, so if it can already see the new
    chunks the write was not one transaction.
    """
    repo = SqliteSearchRepository()
    observations: list[tuple[int, int]] = []
    original_complete = repo.complete_job

    def observing_complete(conn: Any, job_id: int) -> None:
        # Still inside the worker's transaction at this point.
        with db.read() as other:
            chunks = other.execute(text("SELECT count(*) FROM embeddings")).scalar_one()
            jobs = other.execute(text("SELECT count(*) FROM embedding_jobs")).scalar_one()
            observations.append((int(chunks), int(jobs)))
        original_complete(conn, job_id)

    repo.complete_job = observing_complete  # type: ignore[method-assign]
    worker = EmbeddingWorker(
        db,
        repo,
        *_repos(),
        search_services_provider,
        clock=clock,
    )
    drain(worker, clock)

    assert observations, "the worker never completed a job"
    for chunks, jobs in observations:
        assert (chunks, jobs) == (0, 1), (
            "a concurrent reader saw the source's rows or the job's deletion before "
            f"the transaction committed: {(chunks, jobs)}"
        )
    assert index_counts(db).indexed_chunks == 3
    assert job_rows(db) == []


def _repos() -> tuple[Any, Any, Any]:
    from glosswork.repositories.sqlite import (
        SqliteCommentRepository,
        SqliteRecordRepository,
        SqliteSchemaRepository,
    )

    return SqliteSchemaRepository(), SqliteRecordRepository(), SqliteCommentRepository()


@pytest.fixture
def search_services_provider(fake_provider: FakeEmbeddingProvider) -> FakeEmbeddingProvider:
    return fake_provider


def test_a_claim_is_bounded_by_the_batch_size_and_takes_jobs_in_id_order(
    db: Database, search_services: ServiceBundle, clock: FakeClock, sink: None
) -> None:
    """The claim takes up to ``batch_size`` rows in ``id`` order (FR-Q7).

    The bound matters because a claim marks rows ``running``: an unbounded claim that
    dies mid-batch strands every row it took until the reclaim timeout, and the batch
    log line stops meaning anything. ``id`` order makes the queue FIFO, so a burst of
    writes cannot starve the edit that arrived first.
    """
    for n in range(5):
        search_services.records.create_record(
            make_actor(), "artifact", {"title": f"Row {n}", "summary": paragraph(f"body{n}")}
        )
    worker = make_worker(db, search_services, clock, batch_size=2)

    queued = [job.id for job in job_rows(db)]
    assert len(queued) == 5

    clock.advance(400)
    first = worker._claim(clock.now)
    assert [job.id for job in first] == queued[:2]
    second = worker._claim(clock.now)
    assert [job.id for job in second] == queued[2:4]


# -------------------------------------------------- retry, backoff, terminal state


def test_a_failing_job_retries_with_backoff_and_becomes_failed_after_five_attempts(
    db: Database,
    worker: EmbeddingWorker,
    clock: FakeClock,
    fake_provider: FakeEmbeddingProvider,
    record: Any,
) -> None:
    source = field_source(record, "summary")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        fake_provider.fail_next = RuntimeError(f"provider exploded on attempt {attempt}")
        clock.advance(400)  # past any backoff
        result = worker.run_once(clock.now)
        assert result.claimed == 1
        assert result.failed == 1

        jobs = jobs_for(db, source)
        assert len(jobs) == 1
        assert jobs[0].attempts == attempt
        assert "provider exploded" in (jobs[0].last_error or "")
        expected = "failed" if attempt == MAX_ATTEMPTS else "pending"
        assert jobs[0].status == expected, f"after attempt {attempt}"

    assert index_counts(db).failed_jobs == 1


def test_a_just_failed_job_is_not_reclaimed_before_its_backoff_elapses(
    db: Database,
    worker: EmbeddingWorker,
    clock: FakeClock,
    fake_provider: FakeEmbeddingProvider,
    record: Any,
) -> None:
    """Section 10, rule 2: the backoff lives in the **claim predicate**.

    The earlier wording put it in reclaim, which was a no-op: reclaim only ever looks
    at ``running`` rows, and a ``pending`` row is claimed, never reclaimed. Under that
    version a failing job burns all five attempts in a few seconds. Here the clock is
    injected rather than slept through, so the assertion is about the predicate rather
    than about timing luck.
    """
    fake_provider.fail_next = RuntimeError("first failure")
    clock.advance(10)
    assert worker.run_once(clock.now).failed == 1
    job = jobs_for(db, field_source(record, "summary"))[0]
    assert job.attempts == 1 and job.status == "pending"

    # min(300, 2 ** 1) == 2 seconds of backoff. One second in, it must not be claimed.
    clock.advance(1)
    assert worker.run_once(clock.now).claimed == 0

    clock.advance(5)
    assert worker.run_once(clock.now).claimed == 1
    assert job_rows(db) == [], "the retry succeeded and the job was deleted"


# ------------------------------------------- rule 4: yielding to a live sibling


def test_a_failure_with_a_live_pending_sibling_yields_and_carries_its_attempts(
    db: Database,
    search_services: ServiceBundle,
    worker: EmbeddingWorker,
    clock: FakeClock,
    fake_provider: FakeEmbeddingProvider,
    record: Any,
) -> None:
    """Section 10, rule 4.

    ``ux_jobs_pending_source`` makes ``pending`` a scarce state, so the failure
    handler's own transition into it can collide with a sibling enqueued while the job
    was running -- a UNIQUE violation raised inside the handler whose job is to handle
    failure. The row yields instead, and carries its ``attempts`` and ``last_error``
    onto the sibling so the five-attempt terminal state counts the source's failures
    rather than resetting on every concurrent edit.
    """
    source = field_source(record, "summary")

    # Two failures first, so there is a non-zero attempts count to carry.
    for _ in range(2):
        fake_provider.fail_next = RuntimeError("provider exploded")
        clock.advance(400)
        worker.run_once(clock.now)
    assert jobs_for(db, source)[0].attempts == 2

    # Claim it (now running), then land a write, then let the failure handler run.
    clock.advance(400)
    claimed = worker._claim(clock.now)
    assert len(claimed) == 1
    search_services.records.update_record(
        make_actor(), record.key, {"summary": paragraph("rewritten")}
    )
    assert sorted(j.status for j in jobs_for(db, source)) == ["pending", "running"]

    fake_provider.fail_next = RuntimeError("third failure")
    worker._process(claimed[0], clock.now, _result())

    survivors = jobs_for(db, source)
    assert len(survivors) == 1, "exactly one row survives; no UNIQUE violation escaped"
    assert survivors[0].status == "pending"
    assert survivors[0].attempts == 3, "the sibling carries the failed job's attempts"
    assert "third failure" in (survivors[0].last_error or "")


def test_reclaim_with_a_live_pending_sibling_yields_the_same_way(
    db: Database,
    search_services: ServiceBundle,
    worker: EmbeddingWorker,
    clock: FakeClock,
    record: Any,
) -> None:
    """The same rule covers reclaim, which transitions into ``pending`` identically."""
    source = field_source(record, "summary")
    clock.advance(400)
    claimed = worker._claim(clock.now)
    assert len(claimed) == 1

    search_services.records.update_record(
        make_actor(), record.key, {"summary": paragraph("rewritten")}
    )
    assert sorted(j.status for j in jobs_for(db, source)) == ["pending", "running"]

    clock.advance(RUNNING_TIMEOUT_SECONDS + 60)
    assert worker.reclaim_stale(clock.now) == 1

    survivors = jobs_for(db, source)
    assert len(survivors) == 1
    assert survivors[0].status == "pending"
    assert "reclaim" in (survivors[0].last_error or "").lower()


# ------------------------------------------------------------------- reclaim


def test_reclaim_runs_on_the_idle_poll_of_a_worker_that_never_restarted(
    db: Database, worker: EmbeddingWorker, clock: FakeClock, record: Any
) -> None:
    """Section 10, rule 5, and the second item fixed as a *test* and not only as code.

    The restart test below starts a fresh worker, so it cannot catch a row stranded by
    a long-lived one, and a reclaim that only ran at startup would pass it. Here the
    worker stays up: a batch is claimed and then abandoned without its failure handler
    running -- an uncaught ``BaseException``, a thread stopped mid-write -- and
    ``pending_jobs`` must still reach zero with no restart.
    """
    clock.advance(400)
    claimed = worker._claim(clock.now)
    assert len(claimed) == 1
    assert index_counts(db).running_jobs == 1

    # Not yet stale: the idle poll must leave it alone.
    clock.advance(60)
    assert worker.tick(clock.now).reclaimed == 0
    assert index_counts(db).running_jobs == 1

    clock.advance(RUNNING_TIMEOUT_SECONDS + 60)
    assert worker.tick(clock.now).reclaimed == 1
    assert index_counts(db).running_jobs == 0

    drain(worker, clock)
    assert index_counts(db).pending_jobs == 0
    assert len(stored_rows(db, field_source(record, "summary"))) == 3


def test_a_restart_resumes_pending_work_and_does_not_double_embed_reclaimed_rows(
    db: Database,
    search_services: ServiceBundle,
    clock: FakeClock,
    fake_provider: FakeEmbeddingProvider,
    sink: None,
) -> None:
    """In-process half of "restart resumes pending work, proven twice".

    Leaves both a stranded ``running`` row and an untouched ``pending`` row behind, as
    a killed process would, then starts a **fresh** worker over the same database.

    The row is stranded **seconds** ago, not ten minutes ago (DD-35). Writing
    ``updated_at = '2000-01-01'`` exercised the ten-minutes-later path and never the
    seconds-ago path a real kill produces, which is the only path a container that is
    stopped and started many times a day ever takes.
    """
    stranded = search_services.records.create_record(
        make_actor(), "artifact", {"title": "Stranded", "summary": paragraph("stranded")}
    )
    queued = search_services.records.create_record(
        make_actor(), "artifact", {"title": "Queued", "summary": paragraph("queued")}
    )
    # Strand one job in `running` as a kill seconds ago would: `updated_at` is now.
    with db.write() as conn:
        conn.execute(
            text(
                "UPDATE embedding_jobs SET status = 'running', updated_at = :now "
                "WHERE record_id = :r"
            ),
            {"now": format_datetime(clock.now), "r": stranded.id},
        )
    assert index_counts(db).running_jobs == 1

    fresh = make_worker(db, search_services, clock)
    clock.advance(5)
    assert fresh.reclaim_stale(clock.now) == 0, (
        "the age threshold still governs the idle poll, so this row is invisible to it"
    )
    assert fresh.reclaim_all(clock.now) == 1  # what start() does before the loop begins
    drain(fresh, clock)

    assert index_counts(db).pending_jobs == 0
    assert job_rows(db) == []
    for row in (stranded, queued):
        assert len(stored_rows(db, field_source(row, "summary"))) == 1
    # Two sources, one chunk each, embedded once each: nothing was double-embedded.
    assert index_counts(db).indexed_chunks == 2
    assert sorted(fake_provider.embedded_passages) == sorted(
        [paragraph("stranded"), paragraph("queued")]
    )


def _record_every_charge(db: Database) -> None:
    """Install a trigger that logs every update raising a job's ``attempts``.

    A trigger sees every writer on the database, whatever repository instance or
    thread it uses, which is the point: a counting repository sees only its own
    instance's reclaims, and a sweeper built on a fresh ``SqliteSearchRepository()``
    walked straight past one.
    """
    with db.write() as conn:
        conn.execute(
            text("CREATE TABLE prod_test_charges (job_id INTEGER, attempts INTEGER, error TEXT)")
        )
        conn.execute(
            text(
                "CREATE TRIGGER prod_test_record_charges AFTER UPDATE OF attempts "
                "ON embedding_jobs WHEN NEW.attempts > OLD.attempts BEGIN "
                "INSERT INTO prod_test_charges VALUES (NEW.id, NEW.attempts, NEW.last_error); "
                "END"
            )
        )


def _charges(db: Database) -> list[tuple[Any, ...]]:
    with db.read() as conn:
        return [tuple(r) for r in conn.execute(text("SELECT * FROM prod_test_charges"))]


def test_a_pause_longer_than_the_timeout_inside_a_batch_charges_nothing(
    db: Database,
    worker: EmbeddingWorker,
    clock: FakeClock,
    search_services: ServiceBundle,
    fake_provider: FakeEmbeddingProvider,
) -> None:
    """A pause of any length cannot make the worker reclaim the batch it is holding.

    A paused machine (a laptop sleeping under Docker Desktop, a suspended VM) resumes
    with its wall clock moved on and the worker's thread exactly where it was. Here the
    injected wall clock jumps twice the running timeout *inside* the second of four
    sources, which is the pause, and the worker then finishes the batch and goes idle.
    The idle reclaim runs only on this thread and only when it holds nothing, so
    nothing is charged an attempt and every source is embedded once.

    It passes on a correct tree by design. What it guards is any reclaim that could
    run while a batch is held, from this thread or any other writer: the trigger sees
    every charge, and a ``reclaim_stale()`` after each source fails it.
    """
    bodies = [paragraph(f"paused{i}") for i in range(4)]
    for i, body in enumerate(bodies):
        search_services.records.create_record(
            make_actor(), "artifact", {"title": f"Paused {i}", "summary": body}
        )
    _record_every_charge(db)
    assert worker.reclaim_all() == 0  # what start() does first

    original = fake_provider.embed_passages
    calls: list[int] = []

    def pausing(texts: Sequence[str]) -> list[list[float]]:
        calls.append(1)
        if len(calls) == 2:
            clock.advance(2 * RUNNING_TIMEOUT_SECONDS)
        return original(texts)

    fake_provider.embed_passages = pausing  # type: ignore[method-assign]

    clock.advance(10)  # past a new job's one-second claim backoff
    assert worker.tick().claimed == 4
    clock.advance(2)
    idle = worker.tick()
    assert (idle.claimed, idle.reclaimed) == (0, 0)

    assert _charges(db) == [], "a row the worker still held was charged an attempt"
    assert job_rows(db) == []
    assert sorted(fake_provider.embedded_passages) == sorted(bodies)


# Where each reclaim and the loop may be reached from, as ``Class.function``. The
# worker's ``reclaim_stale`` and ``reclaim_all`` appear because each delegates to the
# repository method of the same name, which is the one call it is allowed to make.
RECLAIM_STALE_CALLERS = {"EmbeddingWorker.tick", "EmbeddingWorker.reclaim_stale"}
RECLAIM_ALL_CALLERS = {"EmbeddingWorker.start", "EmbeddingWorker.reclaim_all"}
TICK_CALLERS = {"EmbeddingWorker._run"}

SRC = Path(__file__).resolve().parents[1] / "src" / "glosswork"


def _references_in_src() -> dict[str, list[str]]:
    """Every reference to a reclaim and to ``tick`` in ``src/``.

    Keyed by name, each value lists the enclosing ``Class.function`` path of one
    reference. References rather than calls, so a bound method handed to a thread
    (``Thread(target=self.reclaim_stale)``) counts as much as a call does.
    """
    names = {"reclaim_stale", "reclaim_all", "tick"}
    found: dict[str, list[str]] = {name: [] for name in names}

    class Visitor(ast.NodeVisitor):
        def __init__(self, module: str) -> None:
            self.stack: list[str] = []
            self.module = module

        def _scoped(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_ClassDef = _scoped
        visit_FunctionDef = _scoped
        visit_AsyncFunctionDef = _scoped

        def _note(self, name: str) -> None:
            if name in names:
                found[name].append(".".join(self.stack) or self.module)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if isinstance(node.ctx, ast.Load):
                self._note(node.attr)
            self.generic_visit(node)

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, ast.Load):
                self._note(node.id)

    for path in sorted(SRC.rglob("*.py")):
        Visitor(str(path.relative_to(SRC))).visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def test_only_the_worker_thread_reclaims() -> None:
    """The structural half of "a pause cannot reclaim held work": exactly one reclaimer.

    The idle reclaim is safe across a pause only because the one thread that holds a
    batch is the one thread that reclaims, and only between batches. A second
    reclaimer of any period, a sweeper thread or a second worker on the same
    database, would take back rows a paused batch still holds. A timed test can miss
    a slow sweeper; this cannot. Annotations and imports name ``EmbeddingWorker``
    without constructing it, so the construction count reads calls only.
    """
    found = _references_in_src()
    assert set(found["reclaim_stale"]) == RECLAIM_STALE_CALLERS, found["reclaim_stale"]
    assert len(found["reclaim_stale"]) == len(RECLAIM_STALE_CALLERS), found["reclaim_stale"]
    assert set(found["reclaim_all"]) == RECLAIM_ALL_CALLERS, found["reclaim_all"]
    assert len(found["reclaim_all"]) == len(RECLAIM_ALL_CALLERS), found["reclaim_all"]
    assert sorted(found["tick"]) == sorted(TICK_CALLERS), found["tick"]

    constructions = [
        str(path.relative_to(SRC))
        for path in sorted(SRC.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "EmbeddingWorker"
    ]
    assert constructions == ["app.py"], constructions


# ----------------------------------------------- stopping between sources


def _stop_after_the_first_source(
    worker: EmbeddingWorker, fake_provider: FakeEmbeddingProvider
) -> None:
    """Set the worker's stop event from inside the first source's model call.

    The provider is the only seam that runs *during* a source, which is what a real
    ``SIGTERM`` landing mid-batch looks like. Setting the event before ``run_once``
    would release the whole batch and never exercise "the source in hand is always
    finished".
    """
    original = fake_provider.embed_passages
    calls: list[int] = []

    def embed_and_stop(texts: Sequence[str]) -> list[list[float]]:
        calls.append(1)
        if len(calls) == 1:
            worker._stop.set()
        return original(texts)

    fake_provider.embed_passages = embed_and_stop  # type: ignore[method-assign]


def test_a_stop_finishes_the_source_in_hand_and_releases_the_rest_of_the_batch(
    db: Database,
    search_services: ServiceBundle,
    worker: EmbeddingWorker,
    clock: FakeClock,
    fake_provider: FakeEmbeddingProvider,
) -> None:
    """The grace period has to cover one source, not a whole batch.

    Before this, ``run_once`` iterated the claimed batch with no check of ``_stop``, so
    a stop waited for every source still in it -- up to 32 sources at 64 chunks, which
    is minutes, against ``docker stop``'s 10 s default and a platform that allows 5 s.
    """
    for index in range(4):
        search_services.records.create_record(
            make_actor(),
            "artifact",
            {"title": f"Source {index}", "summary": paragraph(f"s{index}")},
        )
    clock.advance(400)
    _stop_after_the_first_source(worker, fake_provider)

    result = worker.run_once(clock.now)

    assert result.claimed == 4
    assert result.embedded == 1, "the source in hand is finished, and only that one"
    assert result.released == 3
    survivors = job_rows(db)
    assert [job.status for job in survivors] == ["pending"] * 3
    assert {job.attempts for job in survivors} == {0}, "a stop nobody asked for is not an attempt"
    assert {job.last_error for job in survivors} == {None}
    assert index_counts(db).running_jobs == 0
    assert len(fake_provider.embedded_passages) == 1


def test_a_stop_and_a_restart_embed_each_released_source_exactly_once(
    db: Database,
    search_services: ServiceBundle,
    worker: EmbeddingWorker,
    clock: FakeClock,
    fake_provider: FakeEmbeddingProvider,
) -> None:
    """The real double-work exclusion, extended from reclaim to release.

    Chunk counts cannot see a source that was embedded twice, because ``_process``
    stores one row per distinct hash either way. ``embedded_passages`` can: it is the
    list of texts the provider was actually handed.
    """
    texts = [paragraph(f"s{index}") for index in range(4)]
    for index, body in enumerate(texts):
        search_services.records.create_record(
            make_actor(), "artifact", {"title": f"Source {index}", "summary": body}
        )
    clock.advance(400)
    _stop_after_the_first_source(worker, fake_provider)
    assert worker.run_once(clock.now).released == 3

    # A released row keeps its `updated_at` from the claim, so the claim predicate
    # holds it back by min(300, 2 ** attempts) = 1 second. Advancing past
    # that is what separates "released and re-claimable" from "released and lost".
    clock.advance(60)
    fresh = make_worker(db, search_services, clock)
    assert fresh.reclaim_all(clock.now) == 0, "a graceful stop leaves no running rows"
    drain(fresh, clock)

    assert index_counts(db).pending_jobs == 0
    assert job_rows(db) == []
    assert index_counts(db).indexed_chunks == 4
    assert sorted(fake_provider.embedded_passages) == sorted(texts)


def test_a_released_row_returns_to_pending_with_attempts_error_and_time_untouched(
    db: Database,
    worker: EmbeddingWorker,
    clock: FakeClock,
    record: Any,
) -> None:
    """``release_claimed`` is the one place this SQL lives (DD-2)."""
    source = field_source(record, "summary")
    with db.write() as conn:
        conn.execute(text("UPDATE embedding_jobs SET attempts = 2, last_error = 'earlier failure'"))
    clock.advance(400)
    claimed = worker._claim(clock.now)
    assert len(claimed) == 1
    before = jobs_for(db, source)[0]
    assert before.status == "running"

    clock.advance(30)
    with db.write() as conn:
        assert SqliteSearchRepository().release_claimed(conn, claimed) == 1

    after = jobs_for(db, source)[0]
    assert after.status == "pending"
    assert after.attempts == 2
    assert after.last_error == "earlier failure"
    assert after.updated_at == before.updated_at, "release charges no backoff of its own"


def test_a_released_row_yields_to_a_live_pending_sibling_and_carries_its_attempts(
    db: Database,
    search_services: ServiceBundle,
    worker: EmbeddingWorker,
    clock: FakeClock,
    record: Any,
) -> None:
    """Rule 4 governs release too, and it is the ``attempts`` that has to survive.

    A sibling exists because an edit landed while the job was running. Dropping
    ``attempts`` on that path would reset a source's failure count every time a stop
    landed while an edit was queued, which is the reset rule 4 exists to prevent: a
    workspace that scales to zero often while its content is edited would hand a
    failing source a fresh five attempts each time.
    """
    source = field_source(record, "summary")
    with db.write() as conn:
        conn.execute(text("UPDATE embedding_jobs SET attempts = 3, last_error = 'earlier failure'"))
    clock.advance(400)
    claimed = worker._claim(clock.now)
    assert len(claimed) == 1

    search_services.records.update_record(
        make_actor(), record.key, {"summary": paragraph("rewritten")}
    )
    assert sorted(job.status for job in jobs_for(db, source)) == ["pending", "running"]

    with db.write() as conn:
        assert SqliteSearchRepository().release_claimed(conn, claimed) == 1

    survivors = jobs_for(db, source)
    assert len(survivors) == 1, "exactly one row survives; no UNIQUE violation escaped"
    assert survivors[0].status == "pending"
    assert survivors[0].attempts == 3, "the sibling carries the released row's attempts"
    assert survivors[0].last_error is None, "releasing is not a failure; the sibling keeps its own"


def test_a_restart_reclaims_a_running_row_of_any_age_and_counts_an_attempt(
    db: Database,
    worker: EmbeddingWorker,
    clock: FakeClock,
    record: Any,
) -> None:
    """DD-35: at the moment a process starts, every ``running`` row is residue.

    The idle poll keeps the ten-minute threshold, because there a live worker may
    genuinely hold the row; startup does not, because only the application lifespan
    ever constructs a worker and the documented upgrade stops the old container first.
    """
    source = field_source(record, "summary")
    clock.advance(400)
    assert len(worker._claim(clock.now)) == 1
    assert index_counts(db).running_jobs == 1

    clock.advance(5)
    assert worker.reclaim_stale(clock.now) == 0
    assert worker.reclaim_all(clock.now) == 1

    row = jobs_for(db, source)[0]
    assert row.status == "pending"
    assert row.attempts == 1, "the kill path still counts an attempt; only release is free"
    assert index_counts(db).running_jobs == 0


# ------------------------------------------ sources that changed under the job


def test_a_source_whose_record_was_deleted_is_processed_harmlessly(
    db: Database,
    search_services: ServiceBundle,
    worker: EmbeddingWorker,
    clock: FakeClock,
    record: Any,
) -> None:
    """Rows are written; exclusion is a query-time join, not index deletion."""
    search_services.records.delete_record(make_actor(), record.key)
    drain(worker, clock)
    assert len(stored_rows(db, field_source(record, "summary"))) == 3
    assert job_rows(db) == []


def test_a_source_whose_field_was_deleted_is_skipped_with_the_job_deleted_not_failed(
    db: Database,
    search_services: ServiceBundle,
    worker: EmbeddingWorker,
    clock: FakeClock,
    record: Any,
) -> None:
    """Deleting the field between enqueue and claim is not a failure.

    Marking it failed would put a permanent entry on the admin status endpoint for a
    source that no longer exists, and re-indexing everything -- the only other
    remedy on offer -- would not clear it.
    """
    # Enqueue, then remove the field's job-visible eligibility out from under it by
    # approving a delete_field proposal, which also purges. Re-enqueue by hand so a
    # job for the vanished source is definitely present at claim time.
    proposal = search_services.schema.propose_schema_change(
        make_actor(), "delete_field", "artifact", field_key="summary", reason="gone"
    )
    search_services.schema.approve_proposal(make_actor(), proposal.id)
    with db.write() as conn:
        SqliteSearchRepository().enqueue(
            conn, field_source(record, "summary"), "2020-01-01T00:00:00Z"
        )
    assert len(jobs_for(db, field_source(record, "summary"))) == 1

    clock.advance(400)
    result = worker.run_once(clock.now)
    assert result.skipped == 1
    assert result.failed == 0
    assert job_rows(db) == [], "the job is deleted, not failed"
    assert index_counts(db).failed_jobs == 0


def test_a_comment_source_is_indexed_and_a_deleted_comment_still_indexes(
    db: Database,
    search_services: ServiceBundle,
    worker: EmbeddingWorker,
    clock: FakeClock,
    record: Any,
) -> None:
    comment = search_services.comments.add_comment(
        make_actor(), record.key, "Dana flagged a pricing risk on the renewal."
    )
    drain(worker, clock)
    source = comment_source(record, comment)
    rows = stored_rows(db, source)
    assert len(rows) == 1
    assert rows[0].chunk_text == "Dana flagged a pricing risk on the renewal."
    assert fts_body(db, source) == "Dana flagged a pricing risk on the renewal."


# --------------------------------------------------------------- observability


def test_the_worker_logs_one_structured_line_per_batch(
    db: Database,
    worker: EmbeddingWorker,
    clock: FakeClock,
    record: Any,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """FR-P5: an operator watches a drain from ``docker logs``.

    ``capfd``, not ``capsys``: structlog's ``PrintLoggerFactory`` binds to the real
    stdout, so only file-descriptor-level capture sees these lines.
    """
    from glosswork.logging import configure_logging

    configure_logging("info")
    clock.advance(400)
    worker.run_once(clock.now)

    lines = capfd.readouterr().out.splitlines()
    batches = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "embedding_batch":
            batches.append(event)
    assert batches, f"no embedding_batch log line found in: {lines}"
    batch = batches[-1]
    for key in (
        "claimed",
        "embedded",
        "reused",
        "deleted",
        "failed",
        "skipped",
        "released",
        "duration_ms",
    ):
        assert key in batch, f"{key} missing from the batch log line"
    assert batch["claimed"] == 1
    assert batch["embedded"] == 3
    assert batch["released"] == 0


def test_stop_reports_that_it_stopped_only_when_the_thread_actually_stopped(
    worker: EmbeddingWorker, capfd: pytest.CaptureFixture[str]
) -> None:
    """``embedding_worker_stopped`` is evidence, or it is nothing.

    It used to be logged unconditionally after ``join``, whether or not the thread was
    still running, so the container proof's "the logs carry ``embedding_worker_stopped``"
    assertion would have held over a stop that timed out and was killed. A worker grace
    sized on this laptop is exactly the case where that happens: a machine two or three
    times slower makes a worst-case single source outlast ``STOP_GRACE_SECONDS``.
    """
    from glosswork.logging import configure_logging

    configure_logging("info")

    blocked = threading.Event()
    stuck = threading.Thread(target=blocked.wait, daemon=True)
    stuck.start()
    worker._thread = stuck
    try:
        worker.stop(timeout=0.05)
        events = _events(capfd)
        assert "embedding_worker_stop_timed_out" in events
        assert "embedding_worker_stopped" not in events
    finally:
        blocked.set()
        stuck.join(timeout=5)

    finished = threading.Thread(target=lambda: None, daemon=True)
    finished.start()
    worker._stop.clear()
    worker._thread = finished
    worker.stop(timeout=5)
    events = _events(capfd)
    assert "embedding_worker_stopped" in events
    assert "embedding_worker_stop_timed_out" not in events

    # Sized to one worst-case source (64 chunks at 43.62 ms is 2.8 s) plus its write,
    # and inside `docker stop`'s 10 s default. Not to a batch, which is minutes.
    assert inspect.signature(EmbeddingWorker.stop).parameters["timeout"].default is (
        STOP_GRACE_SECONDS
    )
    assert STOP_GRACE_SECONDS == 5.0


def _events(capfd: pytest.CaptureFixture[str]) -> list[str]:
    names = []
    for line in capfd.readouterr().out.splitlines():
        try:
            names.append(str(json.loads(line).get("event")))
        except ValueError:
            continue
    return names


def test_no_worker_is_started_when_embedding_is_disabled(tmp_path: Any) -> None:
    """DD-34: with embedding off there is no worker, so the queue cannot grow."""
    from fastapi.testclient import TestClient

    from glosswork.app import create_app
    from glosswork.config import Settings

    app = create_app(Settings(data_dir=tmp_path / "data", embedding_enabled=False))
    with TestClient(app):
        assert app.state.embedding_worker is None


def _result() -> Any:
    from glosswork.services.embedding_worker import BatchResult

    return BatchResult()
