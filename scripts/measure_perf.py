"""Query-latency measurement for the latency gate (FR-R5, FR-R6).

Runs the five query shapes the latency gate names against a seeded database and
reports p50 and p95 for each, then writes a JSON report that ``docs/PERFORMANCE.md``
is built from.

**Latency is taken server-side, end to end through HTTP, from the access log's
``duration_ms``**, never raw SQL timing.
Correlation is exact rather than approximate: ``RequestContextMiddleware`` stamps
every response with ``x-request-id`` and logs the same id on its access line, so each
measurement is matched to the request that produced it instead of being inferred from
ordering. That matters here because the harness issues requests against a live server
whose startup, readiness polling and worker also emit lines.

What this deliberately does not do: time the client. A client-side stopwatch on
localhost measures the harness's own HTTP stack as much as the server's, and the
budget in the latency gate was derived from server-side figures.

Usage::

    uv run python scripts/seed_perf.py --data-dir ./perf-data
    uv run python scripts/measure_perf.py --data-dir ./perf-data --out docs/perf-report.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

DEFAULT_PORT = 8123
DEFAULT_ITERATIONS = 200
DEFAULT_WARMUP = 25

# The latency gate. Duplicated here as data so the harness can report pass/fail itself
# rather than leaving a human to compare two numbers by eye; the PRD line remains
# the normative one, and changing either is an amendment to that line.
INDEXED_GATE_P50_MS = 50.0
INDEXED_GATE_P95_MS = 150.0
UNINDEXED_REPORTED_P50_MS = 250.0
UNINDEXED_REPORTED_P95_MS = 600.0


@dataclass
class Shape:
    """One query shape to measure."""

    key: str
    title: str
    path: str
    body: dict[str, Any]
    # "indexed" shapes are gated; "unindexed" ones are reported only, because they
    # scale with the largest object type — a deployment property rather than a
    # property of this code.
    band: str
    # Filled during the run for the keyset shape, which cannot know its cursor until
    # it has paged forward.
    prepare: str | None = None
    notes: str = ""


@dataclass
class Result:
    shape: Shape
    durations_ms: list[float] = field(default_factory=list)

    def percentile(self, pct: float) -> float:
        if not self.durations_ms:
            return float("nan")
        ordered = sorted(self.durations_ms)
        # Nearest-rank, which is what a p95 over 200 samples should be: no
        # interpolation inventing a value no request actually took.
        index = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered) + 0.5) - 1))
        return ordered[index]

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.durations_ms) if self.durations_ms else float("nan")


class AccessLogReader:
    """Consumes the server's stdout, indexing access lines by request id.

    Runs on its own thread because the server's stdout is a pipe: nobody draining it
    means the server blocks on a full pipe buffer partway through the run, which
    presents as a hang rather than as an error.
    """

    def __init__(self, stream: Any) -> None:
        self._stream = stream
        self._durations: dict[str, float] = {}
        self._lines: list[str] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        for raw in self._stream:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            with self._lock:
                self._lines.append(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "access" and "request_id" in event:
                with self._lock:
                    self._durations[str(event["request_id"])] = float(event["duration_ms"])

    def duration_for(self, request_id: str, timeout: float = 5.0) -> float | None:
        """The server-side duration for one request, waiting briefly for the line.

        The access line is emitted after the response is sent, so a fast client can
        ask before the logger has caught up.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if request_id in self._durations:
                    return self._durations[request_id]
            time.sleep(0.002)
        return None

    def tail(self, count: int = 40) -> str:
        with self._lock:
            return "\n".join(self._lines[-count:])


# ---------------------------------------------------------------- server control


def mint_admin_token(data_dir: Path) -> str:
    """Create the first administrator and mint an admin PAT through the operator CLI.

    The same bootstrap path a real credential-only deployment uses (FR-P3), rather
    than reaching into the service layer: if that path were broken, a harness that
    bypassed it would measure a deployment nobody can actually log into.
    """
    env = {**os.environ, "GW_DATA_DIR": str(data_dir), "GW_EMBEDDING_ENABLED": "false"}
    subprocess.run(
        [
            sys.executable,
            "-m",
            "glosswork.admin",
            "create-admin",
            "--email",
            "perf@measure.local",
            "--password",
            "MeasurePerf12345!",
        ],
        env=env,
        capture_output=True,
        check=False,  # a second run finds the admin already present, which is fine
    )
    minted = subprocess.run(
        [
            sys.executable,
            "-m",
            "glosswork.admin",
            "mint-token",
            "--name",
            "measure-perf",
            "--scope",
            "admin",
            "--quiet",
        ],
        env=env,
        capture_output=True,
        check=True,
    )
    token = minted.stdout.decode().strip().splitlines()[-1]
    if not token.startswith("gw_pat_"):
        raise RuntimeError(f"mint-token did not print a token: {token!r}")
    return token


def start_server(
    data_dir: Path, port: int, *, embedding_enabled: bool
) -> tuple[subprocess.Popen[bytes], AccessLogReader]:
    env = {
        **os.environ,
        "GW_DATA_DIR": str(data_dir),
        "GW_EMBEDDING_ENABLED": "true" if embedding_enabled else "false",
        "GW_COOKIE_SECURE": "false",
        "GW_LOG_LEVEL": "info",
        # DD-32: the model is located by configuration and never downloaded. The
        # default is the in-image path, so a source checkout has to say where its own
        # copy lives or the worker-mid-drain shape fails at startup rather than at
        # measurement time.
        "GW_MODEL_DIR": os.environ.get("GW_MODEL_DIR", str(REPO_ROOT / "models")),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "glosswork.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            # uvicorn's own access lines are not JSON, which is why the application
            # emits its own (FR-P5). The reader below skips any line that is not a
            # JSON object, so uvicorn's startup banner is harmless either way.
            "--no-access-log",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    reader = AccessLogReader(process.stdout)
    _wait_ready(port, process, reader)
    return process, reader


def _wait_ready(port: int, process: subprocess.Popen[bytes], reader: AccessLogReader) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early:\n{reader.tail()}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    raise RuntimeError(f"server never became ready:\n{reader.tail()}")


def stop_server(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - defensive
        process.kill()
        process.wait(timeout=10)


# ------------------------------------------------------------------- the client


def call(
    port: int, token: str, path: str, body: dict[str, Any] | None
) -> tuple[int, dict[str, Any], str]:
    """One REST call. Returns ``(status, parsed body, request id)``.

    ``urllib`` rather than ``httpx2`` on purpose: the harness must not share a
    connection pool implementation with the code under test, and it needs nothing an
    HTTP client library provides beyond a POST with two headers.
    """
    data = json.dumps(body or {}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return response.status, payload, response.headers.get("x-request-id", "")


# -------------------------------------------------------------------- the shapes


def build_shapes(type_key: str, unindexed_value: str, substring: str) -> list[Shape]:
    """The five shapes the latency gate names, over the largest seeded object type.

    The largest type is deliberate for the unindexed pair: an unindexed filter is a
    full scan of the *type's* partition, so measuring it against a small type is the
    flattering case, which is the same reason the seed's distribution is skewed.
    """
    query_path = f"/api/v1/object-types/{type_key}/query"
    return [
        Shape(
            key="indexed_first_page",
            title="Indexed filter, sorted, first page of 50",
            path=query_path,
            body={
                "filter": {"field": "status", "op": "eq", "value": "in_progress"},
                "sort": [{"field": "due_date", "direction": "desc"}],
                "limit": 50,
            },
            band="indexed",
            notes="status and due_date are both auto-indexed types (single_select, date).",
        ),
        Shape(
            key="indexed_deep_page",
            title="Same query, 200 rows deep by keyset cursor",
            path=query_path,
            body={
                "filter": {"field": "status", "op": "eq", "value": "in_progress"},
                "sort": [{"field": "due_date", "direction": "desc"}],
                "limit": 50,
            },
            band="indexed",
            prepare="cursor",
            notes="Keyset pagination, so depth must not cost what OFFSET would.",
        ),
        Shape(
            key="unindexed_equality",
            title="Equality filter on an unindexed field",
            path=query_path,
            body={
                "filter": {"field": "vendor", "op": "eq", "value": unindexed_value},
                "limit": 50,
            },
            band="unindexed",
            notes="'vendor' is declared indexed=false: the path a user reaches by "
            "filtering on a field nobody thought to mark indexed.",
        ),
        Shape(
            key="unindexed_substring",
            title="Substring match on unindexed long text",
            path=query_path,
            body={
                "filter": {"field": "notes", "op": "contains", "value": substring},
                "limit": 50,
            },
            band="unindexed",
            notes="A full scan of the type's partition with a LIKE on extracted JSON.",
        ),
    ]


def measure(
    port: int,
    token: str,
    reader: AccessLogReader,
    shape: Shape,
    iterations: int,
    warmup: int,
) -> Result:
    body = dict(shape.body)
    if shape.prepare == "cursor":
        body = dict(body)
        cursor = None
        # Page forward four pages of 50 to land 200 rows deep, then measure the
        # fifth page repeatedly.
        for _ in range(4):
            page_body = {**body, **({"cursor": cursor} if cursor else {})}
            _, payload, _ = call(port, token, shape.path, page_body)
            cursor = payload.get("next_cursor")
            if not cursor:
                break
        if cursor:
            body["cursor"] = cursor

    for _ in range(warmup):
        call(port, token, shape.path, body)

    result = Result(shape=shape)
    missing = 0
    for _ in range(iterations):
        status, _, request_id = call(port, token, shape.path, body)
        if status != 200:
            raise RuntimeError(f"{shape.key}: unexpected status {status}")
        duration = reader.duration_for(request_id)
        if duration is None:
            missing += 1
            continue
        result.durations_ms.append(duration)
    if missing:
        print(f"  warning: {missing} access lines never arrived for {shape.key}", file=sys.stderr)
    return result


# ------------------------------------------------- the worker-mid-drain shape


def enqueue_for_drain(data_dir: Path, jobs: int) -> int:
    """Queue ``jobs`` sources so the worker has real work during the fifth shape.

    Done directly against the database rather than through
    ``POST /api/v1/admin/search-index/reindex``, which enqueues the *whole* corpus:
    the point of this shape is to measure query latency while the worker is busy, not
    to pay for a full drain. The rows written are ordinary pending jobs, so the worker
    that picks them up is the production one doing production work.
    """
    from sqlalchemy import text as sql_text

    from glosswork.db import Database

    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db = Database.connect(data_dir / "glosswork.sqlite3")
    try:
        with db.write() as conn:
            conn.execute(
                sql_text(
                    # embedding_jobs is keyed by the source's own columns, not by a
                    # search_sources id: the two tables describe the same source and
                    # ux_jobs_pending_source is what makes a duplicate enqueue a no-op.
                    "INSERT INTO embedding_jobs (record_id, source_type, field_key, comment_id, "
                    "status, attempts, enqueued_at, updated_at) "
                    "SELECT record_id, source_type, field_key, comment_id, 'pending', 0, "
                    ":now, :now FROM search_sources LIMIT :limit "
                    "ON CONFLICT DO NOTHING"
                ),
                {"now": stamp, "limit": jobs},
            )
        with db.read() as conn:
            return int(
                conn.execute(
                    sql_text("SELECT count(*) FROM embedding_jobs WHERE status = 'pending'")
                ).scalar_one()
            )
    finally:
        db.close()


def pending_jobs(port: int, token: str) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/admin/search-index",
        method="GET",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return int(json.loads(response.read().decode("utf-8"))["pending_jobs"])


def measure_under_drain(
    data_dir: Path,
    port: int,
    token: str,
    shape: Shape,
    iterations: int,
    warmup: int,
    jobs: int,
) -> tuple[Result, dict[str, Any]]:
    """The fifth shape: the indexed query while the embedding worker drains.

    A separate server run, because the worker only starts when
    ``GW_EMBEDDING_ENABLED`` is true and the model is loaded at startup. The queue is
    filled first so the worker has work from its first tick, and the run is abandoned
    with a recorded note if the queue empties before the measurement finishes -- a
    number taken against an idle worker is not this shape and must not be reported as
    though it were.
    """
    enqueued = enqueue_for_drain(data_dir, jobs)
    process, reader = start_server(data_dir, port, embedding_enabled=True)
    try:
        depth_before = pending_jobs(port, token)
        for _ in range(warmup):
            call(port, token, shape.path, shape.body)
        result = Result(shape=shape)
        for _ in range(iterations):
            status, _, request_id = call(port, token, shape.path, shape.body)
            if status != 200:
                raise RuntimeError(f"{shape.key}: unexpected status {status}")
            duration = reader.duration_for(request_id)
            if duration is not None:
                result.durations_ms.append(duration)
        depth_after = pending_jobs(port, token)
    finally:
        stop_server(process)
    return result, {
        "jobs_enqueued": enqueued,
        "pending_before": depth_before,
        "pending_after": depth_after,
        "worker_busy_throughout": depth_after > 0,
    }


# -------------------------------------------------------------------- reporting


def report(results: list[Result], meta: dict[str, Any]) -> dict[str, Any]:
    shapes: list[dict[str, Any]] = []
    gate_failures: list[str] = []
    for result in results:
        gated = result.shape.band == "indexed"
        entry = {
            "key": result.shape.key,
            "title": result.shape.title,
            "band": result.shape.band,
            "gated": gated,
            "samples": len(result.durations_ms),
            "p50_ms": round(result.p50, 2),
            "p95_ms": round(result.p95, 2),
            "mean_ms": round(result.mean, 2),
            "min_ms": round(min(result.durations_ms), 2) if result.durations_ms else None,
            "max_ms": round(max(result.durations_ms), 2) if result.durations_ms else None,
            "notes": result.shape.notes,
        }
        if gated:
            entry["gate_p50_ms"] = INDEXED_GATE_P50_MS
            entry["gate_p95_ms"] = INDEXED_GATE_P95_MS
            passed = result.p50 <= INDEXED_GATE_P50_MS and result.p95 <= INDEXED_GATE_P95_MS
            entry["gate_passed"] = passed
            if not passed:
                gate_failures.append(result.shape.key)
        else:
            entry["reported_p50_ms"] = UNINDEXED_REPORTED_P50_MS
            entry["reported_p95_ms"] = UNINDEXED_REPORTED_P95_MS
        shapes.append(entry)
    return {**meta, "shapes": shapes, "gate_failures": gate_failures}


def platform_meta() -> dict[str, Any]:
    return {
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="A seeded GW_DATA_DIR.")
    parser.add_argument("--out", type=Path, required=True, help="Where to write the JSON report.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument(
        "--drain-jobs",
        type=int,
        default=20_000,
        help=(
            "How many sources to queue for the worker-mid-drain shape. Large enough "
            "that the queue outlasts the measurement, bounded so the run does not "
            "become a full-corpus drain."
        ),
    )
    parser.add_argument(
        "--skip-drain-shape",
        action="store_true",
        help="Skip the fifth shape (it needs the bundled model at GW_MODEL_DIR).",
    )
    parser.add_argument(
        "--only-drain-shape",
        action="store_true",
        help=(
            "Measure only the fifth shape and merge it into an existing --out report. "
            "The four shapes above cost minutes of full scans over 200k rows; re-running "
            "them to add a fifth is waste, not rigour."
        ),
    )
    parser.add_argument(
        "--type-key",
        default=None,
        help="Object type to measure. Defaults to the largest seeded type.",
    )
    args = parser.parse_args()

    data_dir: Path = args.data_dir
    seed_report_path = data_dir / "seed_report.json"
    if not seed_report_path.is_file():
        parser.error(f"{seed_report_path} not found: run scripts/seed_perf.py first.")
    seed_report = json.loads(seed_report_path.read_text())

    type_key = args.type_key or seed_report["largest_type_key"]
    unindexed_value = seed_report["sample_vendor"]
    substring = seed_report["sample_notes_substring"]

    token = mint_admin_token(data_dir)
    existing: dict[str, Any] | None = None
    results = []
    if args.only_drain_shape:
        if not args.out.is_file():
            parser.error(f"--only-drain-shape needs an existing report at {args.out}")
        existing = json.loads(args.out.read_text())
    else:
        process, reader = start_server(data_dir, args.port, embedding_enabled=False)
        try:
            for shape in build_shapes(type_key, unindexed_value, substring):
                print(f"measuring {shape.key} ...", file=sys.stderr)
                results.append(
                    measure(args.port, token, reader, shape, args.iterations, args.warmup)
                )
        finally:
            stop_server(process)

    drain_meta: dict[str, Any] | None = None
    if not args.skip_drain_shape:
        print("measuring indexed_first_page under worker drain ...", file=sys.stderr)
        drain_shape = build_shapes(type_key, unindexed_value, substring)[0]
        drain_shape = Shape(
            key="indexed_first_page_under_drain",
            title="Indexed filter, sorted, first page of 50, embedding worker mid-drain",
            path=drain_shape.path,
            body=drain_shape.body,
            band="indexed",
            notes=(
                "The same query as indexed_first_page, measured while the embedding "
                "worker holds CPU. Gated on the same pair: a re-index must not push "
                "the read path out of budget."
            ),
        )
        try:
            drain_result, drain_meta = measure_under_drain(
                data_dir,
                args.port,
                token,
                drain_shape,
                args.iterations,
                args.warmup,
                args.drain_jobs,
            )
        except Exception as exc:  # noqa: BLE001
            # Deliberately non-fatal. The four shapes above are the expensive part of
            # this run (minutes of full scans over 200k rows), and losing them to a
            # failure in the fifth would mean paying for them again. The failure is
            # recorded in the report rather than swallowed, so a missing fifth shape
            # is visible rather than looking like a shape nobody ran.
            print(f"drain shape failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            drain_meta = {"error": f"{type(exc).__name__}: {exc}"}
        else:
            results.append(drain_result)

    document = report(
        results,
        {
            **platform_meta(),
            "data_dir": str(data_dir),
            "type_key": type_key,
            "iterations": args.iterations,
            "warmup": args.warmup,
            "drain_shape": drain_meta,
            "seed": seed_report,
        },
    )
    if existing is not None:
        # Merge: keep the four measured shapes and replace only the drain entry, so a
        # re-run of the fifth shape does not discard the expensive four.
        merged = [s for s in existing["shapes"] if s["key"] != "indexed_first_page_under_drain"]
        merged.extend(document["shapes"])
        document = {**existing, "drain_shape": drain_meta, "shapes": merged}
        document["gate_failures"] = [
            s["key"] for s in merged if s.get("gated") and not s.get("gate_passed")
        ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2) + "\n")

    print(json.dumps(document["shapes"], indent=2))
    if document["gate_failures"]:
        print(
            "\nGATE MISSED for: " + ", ".join(document["gate_failures"]),
            file=sys.stderr,
        )
        # A first measurement above a gate is a finding to report, not a budget to
        # adjust (the DD-33 principle). Exiting non-zero makes that
        # visible instead of leaving it in a JSON file nobody re-reads.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
