"""How long a backup takes at 200,000 records, and who waits while it runs
(FR-P8, DD-36).

The backup measurement needs two things this script provides and a unit test cannot:

    Measure how long the call takes and how long any lock is held at 200k records; if
    ``VACUUM INTO`` proves to hold a writer lock unacceptably, fall back to
    ``sqlite3.Connection.backup()`` with a step size, which DD-36 permits.

``tests/test_backup.py`` already asserts the *behaviour* -- that writes succeed
throughout and the snapshot is internally consistent -- but it does so against a
fifty-record database, where ``VACUUM INTO`` returns in milliseconds and could not
stall anything even if it took the lock exclusively. DD-36 is explicit that a 200,000
record database "is a materially different operation from the same call on a
fifty-record test database", so the number that decides whether the fallback is needed
has to come from the real corpus.

What is measured, with concurrent writers running throughout:

1. Wall clock for the whole artifact: the ``VACUUM INTO`` snapshot plus streaming the
   tar (the blob tree is walked after the snapshot, so both are in the number an
   operator experiences).
2. The snapshot alone, which is the only part that touches the database.
3. Every concurrent write's latency, and whether any reached the 5,000 ms
   ``busy_timeout`` -- the number that would actually condemn ``VACUUM INTO``.

Usage::

    uv run python scripts/measure_backup.py --data-dir ./perf-data --out ./backup.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from measure_fanout import ConcurrentWriters, open_services  # noqa: E402

from glosswork.actor import bootstrap_actor  # noqa: E402
from glosswork.db import BUSY_TIMEOUT_MS  # noqa: E402
from glosswork.services.backup import SNAPSHOT_ARCNAME  # noqa: E402

DEFAULT_WRITERS = 4


def measure(data_dir: Path, writer_type_key: str, writers: int) -> dict[str, Any]:
    db, services = open_services(data_dir)
    try:
        actor = bootstrap_actor("measure-backup")
        with ConcurrentWriters(services, writer_type_key, writers) as concurrent:
            started = time.perf_counter()
            first_chunk_at: float | None = None
            total_bytes = 0
            for chunk in services.backup.stream(actor):
                if first_chunk_at is None:
                    # The first chunk is the tar header for the snapshot member, which
                    # is emitted only after VACUUM INTO has returned. Time to first
                    # byte is therefore the snapshot's own cost, isolated from the
                    # blob walk that follows it.
                    first_chunk_at = time.perf_counter()
                total_bytes += len(chunk)
            elapsed = time.perf_counter() - started
        stats = concurrent.stats().summary()
    finally:
        db.close()

    snapshot_s = (first_chunk_at - started) if first_chunk_at else float("nan")
    return {
        "artifact_bytes": total_bytes,
        "artifact_mb": round(total_bytes / 1024 / 1024, 1),
        "total_seconds": round(elapsed, 2),
        "snapshot_seconds": round(snapshot_s, 2),
        "blob_walk_seconds": round(elapsed - snapshot_s, 2),
        "throughput_mb_per_second": round(total_bytes / 1024 / 1024 / elapsed, 1),
        "snapshot_member": SNAPSHOT_ARCNAME,
        "busy_timeout_ms": BUSY_TIMEOUT_MS,
        "concurrent_writers": stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--writers", type=int, default=DEFAULT_WRITERS)
    parser.add_argument("--writer-type-key", default="task")
    args = parser.parse_args()

    document = measure(args.data_dir, args.writer_type_key, args.writers)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2) + "\n")
    print(json.dumps(document, indent=2))

    stats = document["concurrent_writers"]
    if stats.get("failures") or stats.get("exceeded_busy_timeout"):
        print(
            "\nFINDING: a concurrent write failed or reached the busy timeout during "
            "the backup. DD-36 permits falling back to sqlite3.Connection.backup() "
            "with a step size; that fallback is now required rather than optional.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
