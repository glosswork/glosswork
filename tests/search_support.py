"""Shared helpers for the search-index suite.

Two seams, matching the ones the auth and MCP suites already use
(``StaticJwksSource``, ``FixedScopeResolver``):

- :func:`real_provider` loads the actual bundled model. Tests that need it **fail
  rather than skip** when it is absent (DD-32): a suite that silently skips its
  retrieval tests passes the ``verify`` agent's run while proving nothing.
- :class:`FakeEmbeddingProvider` is deterministic, instant, and model-free, so the
  queue, worker, retry, and restart tests exercise the state machine without paying
  for inference on every case.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from glosswork.db import Database
from glosswork.repositories.models import (
    CommentRow,
    EmbeddingJobRow,
    EmbeddingRow,
    IndexCounts,
    IndexSource,
    RecordRow,
)
from glosswork.repositories.sqlite import SqliteSearchRepository
from glosswork.services.embedding import QUERY_PREFIX, build_provider
from glosswork.services.embedding_worker import EmbeddingWorker
from glosswork.timeutil import utc_now

FETCH_HINT = (
    "The bundled embedding model is not present. Fetch it once with\n"
    "    uv run python scripts/fetch_model.py\n"
    "or point GW_MODEL_DIR at a directory containing "
    "bge-small-en-v1.5/{model.onnx,tokenizer.json}.\n"
    "This test fails rather than skips on purpose (DD-32): a suite that skips "
    "its model-dependent tests would pass a verification run while proving nothing."
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def model_dir() -> Path:
    """Where the real model lives, or a hard failure naming the fetch command."""
    configured = os.environ.get("GW_MODEL_DIR")
    root = Path(configured) if configured else repo_root() / "models"
    model_name = os.environ.get("GW_EMBEDDING_MODEL", "bge-small-en-v1.5")
    directory = root / model_name
    missing = [
        name for name in ("model.onnx", "tokenizer.json") if not (directory / name).is_file()
    ]
    if missing:
        raise AssertionError(f"{', '.join(missing)} missing from {directory}.\n\n{FETCH_HINT}")
    return root


_provider_cache: list[object] = []


def real_provider() -> object:
    """The real ONNX provider, loaded at most once per test session.

    Cached because constructing an ``InferenceSession`` over a 133 MB graph costs
    real time, and every test that wants the real model wants the same one.
    """
    if not _provider_cache:
        _provider_cache.append(
            build_provider(model_dir(), os.environ.get("GW_EMBEDDING_MODEL", "bge-small-en-v1.5"))
        )
    return _provider_cache[0]


class FakeEmbeddingProvider:
    """A deterministic stand-in for the real provider (the FR-Q8 seam).

    Vectors are seeded from the sha256 of the text, so identical text always embeds
    identically -- which is what makes it usable for the reuse and byte-identity
    tests -- and different text almost never collides. Vectors are L2-normalized like
    the real provider's, so anything downstream that assumes unit length holds.

    ``token_offsets`` tokenizes on word boundaries rather than WordPiece. Chunking is
    tested against the *real* tokenizer elsewhere; here the point is only that the
    boundaries are consistent and cheap.
    """

    def __init__(self, model_id: str = "fake-model@test", dimensions: int = 384) -> None:
        self._model_id = model_id
        self._dimensions = dimensions
        self.embedded_passages: list[str] = []
        self.embedded_queries: list[str] = []
        self.fail_next: Exception | None = None

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def token_offsets(self, text: str) -> list[tuple[int, int]]:
        return [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]

    def embed_query(self, text: str) -> list[float]:
        self.embedded_queries.append(QUERY_PREFIX + text)
        return self._vector(QUERY_PREFIX + text)

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        self.embedded_passages.extend(texts)
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [digest[i % len(digest)] - 128 + (i // len(digest)) for i in range(self._dimensions)]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]


class FakeClock:
    """An injectable clock, so backoff is asserted rather than slept through."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or utc_now()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> datetime:
        self.now = self.now + timedelta(seconds=seconds)
        return self.now


def drain(worker: EmbeddingWorker, clock: FakeClock | None = None, max_ticks: int = 50) -> int:
    """Run the worker synchronously until it stops claiming work.

    Advances the clock a little between ticks so the claim predicate's backoff -- a
    brand-new job must be at least ``min(300, 2 ** 0)`` = 1 second old -- is satisfied
    without the test sleeping. Returns the number of ticks that did work.
    """
    clock = clock or FakeClock()
    worked = 0
    for _ in range(max_ticks):
        clock.advance(2)
        result = worker.tick(clock.now)
        if result.claimed == 0 and result.reclaimed == 0:
            return worked
        worked += 1
    raise AssertionError(f"worker did not drain within {max_ticks} ticks")


# --------------------------------------------------------------- introspection
#
# Raw reads of the index tables, for tests only. The rule that only
# ``SqliteSearchRepository`` may name these tables governs ``src/`` (that is what the
# grep in ``test_search_guards.py`` walks); a test that asserts what was actually
# stored has to look at storage, and going through the repository for the reads it
# offers keeps most of that honest anyway.


def fts_rows(db: Database) -> list[tuple[str, str, str | None, str | None, str]]:
    """Every keyword row as ``(record_id, source_type, field_key, comment_id, body)``."""
    with db.read() as conn:
        return [
            (row[0], row[1], row[2], row[3], row[4])
            for row in conn.execute(
                text(
                    "SELECT record_id, source_type, field_key, comment_id, body "
                    "FROM fts_content ORDER BY rowid"
                )
            ).all()
        ]


def fts_body(db: Database, source: IndexSource) -> str | None:
    for record_id, source_type, field_key, comment_id, body in fts_rows(db):
        if (
            record_id == source.record_id
            and source_type == source.source_type
            and (field_key or "") == (source.field_key or "")
            and (comment_id or "") == (source.comment_id or "")
        ):
            return body
    return None


def job_rows(db: Database) -> list[EmbeddingJobRow]:
    with db.read() as conn:
        return [
            EmbeddingJobRow(
                id=int(r[0]),
                record_id=r[1],
                source_type=r[2],
                field_key=r[3],
                comment_id=r[4],
                status=r[5],
                attempts=int(r[6]),
                last_error=r[7],
                enqueued_at=r[8],
                updated_at=r[9],
            )
            for r in conn.execute(
                text(
                    "SELECT id, record_id, source_type, field_key, comment_id, status, "
                    "attempts, last_error, enqueued_at, updated_at "
                    "FROM embedding_jobs ORDER BY id"
                )
            ).all()
        ]


def jobs_for(db: Database, source: IndexSource) -> list[EmbeddingJobRow]:
    return [
        job
        for job in job_rows(db)
        if job.record_id == source.record_id
        and job.source_type == source.source_type
        and (job.field_key or "") == (source.field_key or "")
        and (job.comment_id or "") == (source.comment_id or "")
    ]


def stored_rows(
    db: Database, source: IndexSource, include_vectors: bool = False
) -> list[EmbeddingRow]:
    repo = SqliteSearchRepository()
    with db.read() as conn:
        return repo.source_rows(conn, source, include_vectors=include_vectors)


def index_counts(db: Database) -> IndexCounts:
    repo = SqliteSearchRepository()
    with db.read() as conn:
        return repo.counts(conn)


def field_source(record: RecordRow, field_key: str) -> IndexSource:
    return IndexSource.field(record.id, record.object_type_id, field_key)


def comment_source(record: RecordRow, comment: CommentRow) -> IndexSource:
    return IndexSource.comment(record.id, record.object_type_id, comment.id)
