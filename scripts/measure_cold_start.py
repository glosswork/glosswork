"""How long a stopped container takes to answer ``/readyz`` (FR-P4).

A hosted workspace scales to zero, so it is stopped and started many times a day and
somebody waits for each start. ``docs/PERFORMANCE.md`` records every other number in this
system and had no cold-start line, so "a few seconds" was an assumption and a later run
had nothing to be comparable against.

**Two instants per run, because one is not enough.** The same cold start measured 10
percent apart under two probes while this was being written, and the ``docker start``
call itself cost another 0.12 to 0.16 s that neither number included. So each run carries
both:

``start_call_s``
    From invoking ``docker start`` to the first ``200``. What a person waiting on a
    scaled-to-zero workspace actually experiences.
``start_returned_s``
    From ``docker start`` returning to the first ``200``. The comparable-over-time
    number, because it excludes the container runtime's own latency.

The report also carries the probe -- the poll client, the poll interval, and whether the
volume was already migrated -- because two numbers measured differently and labelled the
same is the only way the PERFORMANCE section this feeds can mislead a later run.

This measures a **local Docker VM** with the image layers already on the host, a local
volume and a warm page cache. That is the right environment for catching a regression in
this repository. It is not a hosted number: the same start on a real platform also pays
machine creation or resume, an image pull, attaching the volume and the round trip to the
region, on a machine with fewer and slower cores.

Usage::

    docker build -t glosswork:container-test .
    uv run python scripts/measure_cold_start.py \\
        --image glosswork:container-test --runs 5 --out ./perf-data/cold-start.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

#: How often ``/readyz`` is polled. Small enough that the poll interval is not a
#: meaningful part of a two-second measurement, and recorded in the report because it is.
POLL_INTERVAL_S = 0.02
READY_TIMEOUT_S = 120
DEFAULT_RUNS = 5


class MeasurementError(RuntimeError):
    """A ``docker`` invocation failed; the message carries stdout and stderr."""


def _run(cmd: list[str], *, timeout: float = 120) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise MeasurementError(
            f"{' '.join(cmd)} failed (exit {result.returncode}):\n"
            f"stdout={result.stdout.decode(errors='replace')}\n"
            f"stderr={result.stderr.decode(errors='replace')}"
        )
    return result


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _poll_until_ready(origin: str, deadline: float) -> None:
    """Poll ``GET /readyz`` until it answers 200.

    ``urllib``, not a client library, so the probe itself is part of the standard library
    and a later run reproduces it without matching a dependency version.
    """
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{origin}/readyz", timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass
        time.sleep(POLL_INTERVAL_S)
    raise MeasurementError(f"{origin}/readyz did not answer 200 within {READY_TIMEOUT_S}s")


def _time_start(cid: str, origin: str) -> dict[str, float]:
    call_started = time.monotonic()
    _run(["docker", "start", cid], timeout=60)
    returned = time.monotonic()
    _poll_until_ready(origin, call_started + READY_TIMEOUT_S)
    ready = time.monotonic()
    return {
        "start_call_s": round(ready - call_started, 3),
        "start_returned_s": round(ready - returned, 3),
        "docker_start_s": round(returned - call_started, 3),
    }


def _platform_facts(image: str) -> dict[str, Any]:
    uname = platform.uname()
    inspect = _run(["docker", "image", "inspect", "-f", "{{.Id}}", image], timeout=60)
    version = _run(["docker", "version", "-f", "{{.Server.Version}}"], timeout=60)
    return {
        "uname": f"{uname.system} {uname.release} {uname.machine}",
        "host_cores": os.cpu_count(),
        "docker_server_version": version.stdout.decode().strip(),
        "image": image,
        "image_id": inspect.stdout.decode().strip(),
    }


def measure(image: str, runs: int) -> dict[str, Any]:
    """First boot on an empty volume, then ``runs`` stop/start cycles on the migrated one."""
    suffix = uuid.uuid4().hex[:8]
    volume = f"gw-cold-start-{suffix}"
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    cid = ""
    try:
        _run(["docker", "volume", "create", volume], timeout=60)
        created = _run(
            [
                "docker",
                "create",
                "-p",
                f"127.0.0.1:{port}:8000",
                "-v",
                f"{volume}:/data",
                "--name",
                f"gw-cold-start-{suffix}",
                image,
            ],
            timeout=60,
        )
        cid = created.stdout.decode().strip()

        first_boot = _time_start(cid, origin)
        cycles = []
        for _ in range(runs):
            _run(["docker", "stop", "-t", "30", cid], timeout=120)
            cycles.append(_time_start(cid, origin))
        _run(["docker", "stop", "-t", "30", cid], timeout=120)
    finally:
        if cid:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=60)
        subprocess.run(["docker", "volume", "rm", "-f", volume], capture_output=True, timeout=60)

    document: dict[str, Any] = {
        "probe": {
            "client": "urllib.request.urlopen, one request per poll",
            "endpoint": "GET /readyz",
            "poll_interval_s": POLL_INTERVAL_S,
            "published_port": True,
            "volume": "named, local",
            "first_boot_volume": "empty, so migrations run inside the measurement",
            "cycle_volume": "already migrated",
            "environment": (
                "local Docker VM, image layers already on the host, warm page cache; "
                "not a hosted number"
            ),
        },
        "platform": _platform_facts(image),
        "first_boot": first_boot,
        "runs": cycles,
    }
    for key in ("start_call_s", "start_returned_s", "docker_start_s"):
        values = sorted(cycle[key] for cycle in cycles)
        document[f"{key[:-2]}_min_s"] = values[0]
        document[f"{key[:-2]}_p50_s"] = values[len(values) // 2]
        document[f"{key[:-2]}_max_s"] = values[-1]
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="the image tag to measure")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    document = measure(args.image, args.runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2) + "\n")
    print(json.dumps(document, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
