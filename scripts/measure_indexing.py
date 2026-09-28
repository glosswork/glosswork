"""Indexing throughput on a bounded slice, with the full-corpus drain extrapolated
(FR-Q9's operational number).

The shape of this measurement is specific:

    Indexing throughput is measured on a **bounded slice** -- drain roughly 5,000
    records with the real provider -- and reported as chunks/second at both chunk
    lengths, with the full-corpus drain time **extrapolated, not paid**.

Why extrapolated: an earlier pricing run put a full 256-token chunk at 19-22 ms
and a short title at 1.4-3.4 ms against the shipped ``OnnxBgeProvider``, so draining a
realistic 200,000-record corpus is roughly a hundred minutes of single-threaded work.
Paying that during a measurement pass buys one number and an afternoon.

Why it matters at all: the FR-Q9 Settings panel offers an administrator a re-index
button and does not currently tell them what pressing it costs. That number, and the
one in docs/DEPLOYMENT.md, come from here.

Two measurements, deliberately separate:

1. **Per-chunk cost at both lengths**, measured against the provider directly. This is
   the clean number, free of queue and transaction overhead, and it is what the
   extrapolation multiplies.
2. **End-to-end drain throughput**, measured by running the real worker over a bounded
   slice of real queued jobs. This is the number an operator actually experiences,
   and the gap between it and (1) is the cost of everything that is not inference.

Usage::

    uv run python scripts/measure_indexing.py --data-dir ./perf-data --out ./indexing.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import text  # noqa: E402

from glosswork.config import Settings  # noqa: E402
from glosswork.db import Database  # noqa: E402
from glosswork.repositories.sqlite import (  # noqa: E402
    SqliteCommentRepository,
    SqliteRecordRepository,
    SqliteSchemaRepository,
    SqliteSearchRepository,
)
from glosswork.services import ServiceBundle, build_services  # noqa: E402
from glosswork.services.embedding import build_provider  # noqa: E402
from glosswork.services.embedding_worker import EmbeddingWorker  # noqa: E402
from glosswork.services.search_tuning import CHUNK_MAX_TOKENS  # noqa: E402

DATABASE_FILENAME = "glosswork.sqlite3"
DEFAULT_SLICE = 5_000
DEFAULT_SAMPLES = 40

# A short chunk in the sense that pricing run used: a title or a name, the kind of
# ``short_text`` value that opts into embedding and produces one small chunk.
SHORT_TEXT = "Consolidate vendor onboarding in the finance shared service"


def full_length_text(provider: Any) -> str:
    """A passage that genuinely fills a 256-token chunk.

    Built by appending sentences until the provider's *own* tokenizer reports
    ``CHUNK_MAX_TOKENS`` tokens, rather than by guessing at a character count: the
    whole point of the "both chunk lengths" figure is that one of them is the model's
    maximum, and a passage that is merely long is not the same measurement.
    """
    sentence = (
        "The operations team reviewed the migration plan and agreed that the "
        "dependency on the payroll run should be tracked as a separate risk. "
    )
    passage = sentence
    while len(provider.token_offsets(passage)) < CHUNK_MAX_TOKENS:
        passage += sentence
    return passage


def measure_per_chunk(provider: Any, samples: int) -> dict[str, Any]:
    """Per-chunk inference cost at both lengths, one chunk at a time.

    One at a time rather than batched, because the extrapolation below multiplies a
    per-chunk figure and a batch's amortization would make it optimistic. The batched
    figure is measured separately by the drain.
    """
    results: dict[str, Any] = {}
    for label, passage in (("short", SHORT_TEXT), ("full_256_token", full_length_text(provider))):
        provider.embed_passages([passage])  # warm the graph before timing anything
        durations = []
        for _ in range(samples):
            started = time.perf_counter()
            provider.embed_passages([passage])
            durations.append((time.perf_counter() - started) * 1000)
        ordered = sorted(durations)
        median_ms = statistics.median(ordered)
        results[label] = {
            "tokens": len(provider.token_offsets(passage)),
            "samples": samples,
            "p50_ms": round(median_ms, 2),
            "p95_ms": round(ordered[max(0, round(0.95 * len(ordered)) - 1)], 2),
            "chunks_per_second": round(1000 / median_ms, 1) if median_ms else None,
        }
    return results


def queue_depth(db: Database) -> int:
    with db.read() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM embedding_jobs WHERE status = 'pending'")
            ).scalar_one()
        )


def indexed_chunks(db: Database) -> int:
    with db.read() as conn:
        return int(conn.execute(text("SELECT count(*) FROM embeddings")).scalar_one())


def enqueue_bounded_slice(db: Database, limit: int) -> int:
    """Queue roughly ``limit`` sources for re-embedding, and no more.

    ``SearchIndexService.reindex`` enqueues the *whole* corpus, which is exactly what
    this measurement is designed not to pay for. Bounding it here rather than adding a
    limit to the service keeps the production path unchanged: the button an operator
    presses re-indexes everything, and this script is not that button.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with db.write() as conn:
        conn.execute(
            text(
                # Keyed by the source's own columns, not by a search_sources id.
                "INSERT INTO embedding_jobs (record_id, source_type, field_key, "
                "comment_id, status, attempts, enqueued_at, updated_at) "
                "SELECT record_id, source_type, field_key, comment_id, 'pending', 0, "
                ":now, :now FROM search_sources LIMIT :limit ON CONFLICT DO NOTHING"
            ),
            {"now": now, "limit": limit},
        )
    return queue_depth(db)


def measure_drain(db: Database, services: ServiceBundle, slice_size: int) -> dict[str, Any]:
    """End-to-end throughput over a bounded slice, through the real worker."""
    enqueued = enqueue_bounded_slice(db, slice_size)
    if enqueued == 0:
        raise RuntimeError(
            "nothing was enqueued: the seeded corpus has no eligible search_sources "
            "rows, so there is no indexing to measure."
        )
    before = indexed_chunks(db)
    assert services.embedding_provider is not None
    worker = EmbeddingWorker(
        db,
        SqliteSearchRepository(),
        SqliteSchemaRepository(),
        SqliteRecordRepository(),
        SqliteCommentRepository(),
        services.embedding_provider,
    )
    started = time.perf_counter()
    worker.start()
    try:
        # Poll rather than sleep a fixed duration: the slice is bounded, so this ends
        # when the queue does.
        while queue_depth(db) > 0:
            time.sleep(0.25)
    finally:
        worker.stop()
    elapsed_s = time.perf_counter() - started
    after = indexed_chunks(db)
    written = after - before
    return {
        "jobs_enqueued": enqueued,
        "elapsed_s": round(elapsed_s, 2),
        "chunks_written": written,
        "sources_per_second": round(enqueued / elapsed_s, 1) if elapsed_s else None,
        "note": (
            "chunks_written counts newly inserted rows only; an unchanged chunk keeps "
            "its existing row by content hash, so a re-index of unmodified "
            "content writes nothing while still paying to discover that."
        ),
    }


def extrapolate(per_chunk: dict[str, Any], total_sources: int) -> dict[str, Any]:
    """Full-corpus drain time, from the per-chunk figures and a stated mix.

    The mix is stated rather than measured because it is a property of a deployment's
    schema, not of this corpus: an office that opts every long_text field into
    embedding pays the 256-token figure far more often than one that embeds only
    names. Both bounds are reported so an operator can place their own deployment
    between them.
    """
    short_ms = per_chunk["short"]["p50_ms"]
    full_ms = per_chunk["full_256_token"]["p50_ms"]
    return {
        "total_sources": total_sources,
        "all_short_minutes": round(total_sources * short_ms / 1000 / 60, 1),
        "all_full_length_minutes": round(total_sources * full_ms / 1000 / 60, 1),
        "even_mix_minutes": round(total_sources * ((short_ms + full_ms) / 2) / 1000 / 60, 1),
        "basis": (
            "single-threaded, one chunk per source. A source whose text exceeds one "
            "chunk costs proportionally more; MAX_CHUNKS_PER_SOURCE caps it at 64."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--slice", type=int, default=DEFAULT_SLICE, dest="slice_size")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=REPO_ROOT / "models",
        help="Where the bundled model lives (DD-32). Never downloaded.",
    )
    args = parser.parse_args()

    provider = build_provider(args.model_dir, "bge-small-en-v1.5")
    print(f"provider: {provider.model_id}", file=sys.stderr)

    print("measuring per-chunk inference cost ...", file=sys.stderr)
    per_chunk = measure_per_chunk(provider, args.samples)

    db = Database.connect(args.data_dir / DATABASE_FILENAME)
    try:
        settings = Settings(data_dir=args.data_dir, embedding_enabled=True)
        services = build_services(db, args.data_dir, settings, embedding_provider=provider)
        with db.read() as conn:
            total_sources = int(
                conn.execute(text("SELECT count(*) FROM search_sources")).scalar_one()
            )
        print(f"draining a bounded slice of {args.slice_size} sources ...", file=sys.stderr)
        drain = measure_drain(db, services, args.slice_size)
    finally:
        db.close()

    document = {
        "model_id": provider.model_id,
        "chunk_max_tokens": CHUNK_MAX_TOKENS,
        "per_chunk": per_chunk,
        "bounded_drain": drain,
        "full_corpus_extrapolation": extrapolate(per_chunk, total_sources),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2) + "\n")
    print(json.dumps(document, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
