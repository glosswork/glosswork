"""The schema-change fan-out measurement that DD-34's bound rests on.

Unbounded, turning ``embed`` on for a field fans out synchronously inside the schema
change's own request transaction: one keyword row and one queue job per live record of
the type, with the single SQLite writer lock held throughout. Whether that needs
bounding is settled by this measurement and not before:

    If the stall is seconds, chunk the fan-out or move it behind the queue. If it is
    milliseconds, record the measured number in DD-34 rather than building anything.
    Either way the number is recorded.

So this script measures the thing the decision turns on: **how long other writers
wait**, against the 5,000 ms ``busy_timeout`` (``db.BUSY_TIMEOUT_MS``,
docs/DATA_MODEL.md section 14). A fan-out that takes four seconds while nobody else is
writing is a slow schema change; a fan-out that takes four seconds while a colleague's
record save times out is a defect. Only the second number decides the item, which is
why concurrent writers run throughout and their individual latencies are what gets
reported.

Two operations are timed, both against the **largest** seeded object type, because the
cost is per live record and the largest type is where it bites:

1. Turning ``embed`` on for ``vendor`` (an additive change, so it applies immediately).
2. The purge side of ``delete_field``, which goes through propose-then-approve because
   deletes never apply directly (FR-S6).

Embedding is **enabled** for the run, over the deterministic fake provider. That is
the worst case and the honest one: with embedding on, ``_apply_source`` writes a
keyword row *and* a queue job per record, doubling the write volume inside the
transaction. The fake provider is used because what is being measured is the fan-out's
hold on the writer lock, not inference cost -- that is measured separately, by
``scripts/measure_indexing.py``, with the real model.

This script **mutates the database it is given**. Point it at a copy, or re-seed
afterwards.

Usage::

    uv run python scripts/measure_fanout.py --data-dir ./perf-data --out ./fanout.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from glosswork.actor import bootstrap_actor  # noqa: E402
from glosswork.config import Settings  # noqa: E402
from glosswork.db import BUSY_TIMEOUT_MS, Database  # noqa: E402
from glosswork.services import ServiceBundle, build_services  # noqa: E402

DATABASE_FILENAME = "glosswork.sqlite3"
DEFAULT_WRITERS = 4

# The field the fan-out is measured on. ``vendor`` ships from the seed with
# embed=False and indexed=False, so turning embed on is a genuine transition across
# the eligibility line for every live record of the type (FR-Q3 as amended).
FANOUT_FIELD = "vendor"


@dataclass
class WriterStats:
    """What one concurrent writer thread observed while the schema change ran."""

    durations_ms: list[float] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def merge(self, other: WriterStats) -> None:
        self.durations_ms.extend(other.durations_ms)
        self.failures.extend(other.failures)

    def summary(self) -> dict[str, Any]:
        if not self.durations_ms:
            return {"writes": 0, "failures": self.failures}
        ordered = sorted(self.durations_ms)
        return {
            "writes": len(ordered),
            "p50_ms": round(statistics.median(ordered), 2),
            "p95_ms": round(ordered[max(0, round(0.95 * len(ordered)) - 1)], 2),
            "max_ms": round(ordered[-1], 2),
            "failures": self.failures,
            # The number the decision actually turns on: did any writer come close to
            # giving up? A write that waits longer than the busy timeout does not wait,
            # it fails.
            "exceeded_busy_timeout": sum(1 for d in ordered if d >= BUSY_TIMEOUT_MS),
        }


class ConcurrentWriters:
    """Background record writers, running for the duration of a schema change.

    They write to a *different* object type than the one being altered, which is the
    realistic case and the harder one to dismiss: SQLite's writer lock is
    database-wide, so an unrelated colleague saving an unrelated record is exactly who
    gets stalled by a fan-out over the largest type.
    """

    def __init__(self, services: ServiceBundle, type_key: str, writers: int) -> None:
        self._services = services
        self._type_key = type_key
        self._stop = threading.Event()
        self._stats = WriterStats()
        self._lock = threading.Lock()
        self._threads = [
            threading.Thread(target=self._run, args=(i,), daemon=True) for i in range(writers)
        ]

    def _run(self, index: int) -> None:
        local = WriterStats()
        counter = 0
        actor = bootstrap_actor(f"fanout-writer-{index}")
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                self._services.records.create_record(
                    actor, self._type_key, self._values(index, counter)
                )
            except Exception as exc:  # noqa: BLE001 - the failures are the finding
                local.failures.append(f"{type(exc).__name__}: {exc}")
            else:
                local.durations_ms.append((time.perf_counter() - started) * 1000)
            counter += 1
        with self._lock:
            self._stats.merge(local)

    def _values(self, index: int, counter: int) -> dict[str, Any]:
        return {"name": f"Concurrent write {index}-{counter}"}

    def __enter__(self) -> ConcurrentWriters:
        for thread in self._threads:
            thread.start()
        # Let them get into steady state before the measured operation begins, so the
        # first sample is not thread startup.
        time.sleep(0.5)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=30)

    def stats(self) -> WriterStats:
        with self._lock:
            return self._stats


def open_services(data_dir: Path) -> tuple[Database, ServiceBundle]:
    from tests.search_support import FakeEmbeddingProvider

    db = Database.connect(data_dir / DATABASE_FILENAME)
    settings = Settings(data_dir=data_dir, embedding_enabled=True)
    services = build_services(db, data_dir, settings, embedding_provider=FakeEmbeddingProvider())
    return db, services


def live_record_count(services: ServiceBundle, type_key: str) -> int:
    actor = bootstrap_actor("fanout-count")
    return services.records.query_records(actor, type_key, limit=1).total_count


def measure_embed_on(
    services: ServiceBundle, type_key: str, writer_type_key: str, writers: int
) -> dict[str, Any]:
    """Time the additive ``embed: true`` transition, with writers in flight."""
    actor = bootstrap_actor("fanout-embed-on")
    with ConcurrentWriters(services, writer_type_key, writers) as concurrent:
        started = time.perf_counter()
        result = services.schema.update_field(actor, type_key, FANOUT_FIELD, {"embed": True})
        elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "operation": "embed_on",
        "object_type": type_key,
        "field": FANOUT_FIELD,
        "applied_immediately": result.applied,
        "elapsed_ms": round(elapsed_ms, 2),
        "concurrent_writers": concurrent.stats().summary(),
    }


def measure_delete_field(
    services: ServiceBundle, type_key: str, writer_type_key: str, writers: int
) -> dict[str, Any]:
    """Time the purge side of ``delete_field``.

    The proposal itself is cheap; the approval is where ``purge_field`` runs, so the
    two are timed separately and only the approval is the fan-out number.
    """
    actor = bootstrap_actor("fanout-delete-field")
    proposal = services.schema.propose_schema_change(
        actor,
        "delete_field",
        type_key,
        field_key=FANOUT_FIELD,
        reason="Fan-out measurement (scripts/measure_fanout.py).",
    )
    with ConcurrentWriters(services, writer_type_key, writers) as concurrent:
        started = time.perf_counter()
        services.schema.approve_proposal(actor, proposal.id)
        elapsed_ms = (time.perf_counter() - started) * 1000
    return {
        "operation": "delete_field_approval",
        "object_type": type_key,
        "field": FANOUT_FIELD,
        "elapsed_ms": round(elapsed_ms, 2),
        "concurrent_writers": concurrent.stats().summary(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--writers", type=int, default=DEFAULT_WRITERS)
    parser.add_argument(
        "--writer-type-key",
        default="task",
        help=(
            "Object type the concurrent writers write to. Deliberately not the type "
            "being altered: SQLite's writer lock is database-wide, so the realistic "
            "victim of a fan-out is an unrelated colleague saving an unrelated record."
        ),
    )
    args = parser.parse_args()

    seed_report_path = args.data_dir / "seed_report.json"
    if not seed_report_path.is_file():
        parser.error(f"{seed_report_path} not found: run scripts/seed_perf.py first.")
    seed_report = json.loads(seed_report_path.read_text())
    type_key = seed_report["largest_type_key"]
    writer_type_key = args.writer_type_key
    if writer_type_key == type_key:
        parser.error("--writer-type-key must differ from the type being altered")

    db, services = open_services(args.data_dir)
    try:
        records = live_record_count(services, type_key)
        measurements = [
            measure_embed_on(services, type_key, writer_type_key, args.writers),
            measure_delete_field(services, type_key, writer_type_key, args.writers),
        ]
    finally:
        db.close()

    document = {
        "busy_timeout_ms": BUSY_TIMEOUT_MS,
        "object_type": type_key,
        "live_records_in_type": records,
        "concurrent_writer_threads": args.writers,
        "writer_object_type": writer_type_key,
        "measurements": measurements,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2) + "\n")
    print(json.dumps(document, indent=2))

    stalled = any(
        m["concurrent_writers"].get("exceeded_busy_timeout", 0) for m in measurements
    ) or any(m["concurrent_writers"].get("failures") for m in measurements)
    if stalled:
        print(
            "\nFINDING: a concurrent write hit or exceeded the busy timeout during a "
            "fan-out. DD-34 resolves it by chunking the fan-out or moving it "
            "behind the queue.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
