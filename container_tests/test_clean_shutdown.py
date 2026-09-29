"""A real container stops cleanly mid-batch and loses no work (FR-Q7, DD-35).

Deliberately outside ``pyproject.toml``'s ``testpaths``, like its siblings here, so
``uv run pytest -q`` never needs Docker. Run it with
``uv run pytest -s container_tests/test_clean_shutdown.py``: ``-s`` rather than ``-q``
on purpose, because pytest shows no stdout for a passing test and the numbers this proof
measures -- the yardstick ``C``, the per-source and stop durations, the observed batch --
are the point of running it. Set ``GW_SHUTDOWN_REPORT`` to a path to get the same numbers
as JSON.

**Three arms over one corpus.** The control arm drains undisturbed and its exact
``indexed_chunks`` is the yardstick every other arm is measured against. The graceful arm
takes a real ``docker stop`` while a batch is in flight; the killed arm takes a real
``docker kill``. Both then restart and must reach the same number.

**The corpus is sized to the machine, not written down.** A fourth container, the
sizing arm, first measures how long this machine's worker takes over a probe source and
picks the words per source that make one source take about ``TARGET_SOURCE_S``. Every
threshold that depends on speed is then derived from the control arm's own measured
per-source time rather than carried as a number from the laptop the proof was written
on: how many sources must still be claimed when the signal lands, and the band the
per-source time must sit in for the arms to be able to fail. The first release dry run
(36554918375) is why: the arm64 runner took 3.7 s per source against a band of 0.3 to
2.5 s sized on an Apple silicon laptop.

**Why each arm can fail.** The graceful arm's discriminator is ``released`` in the
``embedding_batch`` log line, a field only a worker that releases its claimed sources
on a stop writes, plus a measured bound on the stop itself. The killed arm's is the 180 s
drain: before DD-35 a freshly stranded row waited out the ten-minute ``RUNNING_TIMEOUT_SECONDS``,
so ``pending_jobs`` could not reach zero. One assertion here is a **fence** and is
labelled as one: the graceful arm's "no ``embedding_jobs_reclaimed`` after the restart"
holds for a worker that does not release too, because reclaim logs nothing when the
count is zero.

**Exit codes say which process is PID 1, not whether the stop was clean.** Measured both
ways on this image: the identical clean shutdown, same log sequence, exits ``0`` as PID 1
and ``143`` under ``docker run --init``, because uvicorn re-raises the signal it caught
and Linux discards a default-action signal aimed at PID 1 only. So the graceful arm
accepts either, and what it actually asserts about cleanliness is the log sequence.

The container is reached only over ``http://127.0.0.1:<port>``, and its credential comes
from ``POST /api/v1/bootstrap``. The secret and the password are generated per run
and reach the container as environment; no credential literal is committed here.
"""

from __future__ import annotations

import json
import math
import os
import random
import secrets
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import pytest

from container_tests import docker_support as ds

READY_TIMEOUT_S = 120
DRAIN_TIMEOUT_S = 180
HTTP_TIMEOUT_S = 60


def _is_ready_body(response: httpx2.Response) -> bool:
    """True only for ``/readyz``'s own successful body.

    A 200 alone is not readiness in a container: ``web/dist`` is always built there, so
    the SPA catch-all would answer a removed ``/readyz`` with ``index.html`` and a
    status-only poll could not tell that apart from a healthy deployment.
    """
    if response.status_code != 200:
        return False
    try:
        return bool(response.json() == {"status": "ok"})
    except ValueError:
        return False


#: What ``docker stop`` is given before it escalates to ``SIGKILL``. Docker's own
#: default, so the arm measures the platform an operator actually meets.
STOP_GRACE_S = 10

#: The stop bound stated as a number. A stop covers one source, not a batch.
MAX_STOP_S = 8.0

#: A generous ceiling that catches a catastrophic regression without a loaded
#: laptop flaking. ``scripts/measure_cold_start.py`` produces the recorded number; this
#: rides along on the two arms that already stop and start a container.
COLD_START_CEILING_S = 30.0

#: More than ``BATCH_SIZE`` (32), so a full batch is claimed with work still queued
#: behind it.
CORPUS_SOURCES = 40

#: The worker's ``BATCH_SIZE`` (``services/embedding_worker.py``), written here rather
#: than imported because this directory proves the image from the outside. No more
#: than this many sources can be claimed when the signal lands.
BATCH_SIZE = 32

#: What the sizing arm aims one source at: about 2.3x under the ceiling below and about
#: 1.9x over the floor ``MAX_REQUIRED_RUNNING`` sets (0.8 s), so a calibration that
#: misses by less than either still leaves every arm able to fail.
TARGET_SOURCE_S = 1.5

#: The sizing arm's probe: a warm-up source, then this many sources of ``PROBE_WORDS``
#: each, timed by the worker's own ``embedding_batch`` log lines.
PROBE_WORDS = 2800
PROBE_SOURCES = 4

#: Bounds on the words per source, in chunks of the probe's own measured size. The
#: floor keeps at least one filler chunk beside the nonce's own, so the exact-count
#: yardstick counts more than sources, and keeps a slow machine's sources as short as
#: they can usefully be; at most 60 stays under ``MAX_CHUNKS_PER_SOURCE`` (64), past
#: which a source is truncated.
MIN_CHUNKS_PER_SOURCE = 1.5
MAX_CHUNKS_PER_SOURCE = 60

#: The ceiling on one source's worker time, from a product number rather than a test
#: one: 70% of the worker's own ``STOP_GRACE_SECONDS`` (5.0), the join a stop gives the
#: source in hand before it abandons it, leaving the rest for the shutdown around it
#: (measured at under 0.4 s). Above it the fixed tree's stop can time out on this corpus
#: and the graceful arm fails for a reason that is not a regression. It is a
#: precondition, not a discriminator: no arm's assertion reads it.
PER_SOURCE_CEILING_S = 3.5

#: How far past ``STOP_GRACE_S`` the batch in flight must reach, so that a worker that
#: drains its batch on a stop (the unfixed tree) is still working when ``docker stop``
#: escalates to ``SIGKILL``, and the stop-duration bound discriminates as well as
#: ``released``.
UNFIXED_OVERRUN = 1.2

#: At least two sources claimed at the signal makes ``released > 0`` reachable at all.
MIN_RELEASABLE = 2

#: The most sources the arms can wait to see claimed at once. Not ``BATCH_SIZE``: the
#: corpus is written across the claim's one-second hold-back, so it arrives in two or
#: more batches ([9, 31], [24, 16] and [3, 32] measured), and a requirement above half a
#: batch can be unreachable. A requirement of 24 was, on a 24 then 16 split.
MAX_REQUIRED_RUNNING = BATCH_SIZE // 2

EMAIL = "admin@container-test.local"

OBJECT_TYPE: dict[str, Any] = {
    "key": "shutdownnote",
    "name": "Shutdown Note",
    "name_plural": "Shutdown Notes",
    "description": (
        "A long-text note written by the clean-shutdown container proof, sized so that "
        "embedding one of them takes a measurable fraction of a second."
    ),
    "key_prefix": "SDN",
    "fields": [
        {
            "key": "body",
            "name": "Body",
            "type": "long_text",
            "description": (
                "The indexed body. Long enough that a source is many chunks, which is "
                "what makes a batch outlive a platform grace period."
            ),
        }
    ],
}

#: Common words, laid down as whole cycles, each cycle shuffled by a per-source seed.
#: Whole cycles give every body the same words in the same numbers, so every body is the
#: same number of tokens and chunks; the shuffle makes every chunk's text its own. A plain
#: rotation repeated its chunks, the worker embeds each distinct chunk once, and a
#: source's cost stopped growing at 13 distinct chunks (measured in the image: 2800 and
#: 5305 words both 13 distinct, both 0.51 s), which a sizing arm cannot scale. A free
#: random draw scaled but gave bodies different token counts, and so different chunk
#: counts, which the exact yardstick caught (1419 chunks over 40 sources).
VOCABULARY = (
    "renewal pricing migration rollout checklist onboarding retention forecast "
    "contract escalation handover integration deployment quarterly scorecard"
).split()

REPORT: dict[str, Any] = {}


def _required_running(per_source_s: float) -> int:
    """How many sources must still be claimed when the signal lands, at this speed.

    The one in hand finishes; the rest are what the unfixed tree would go on to drain.
    They must outlast ``STOP_GRACE_S`` by ``UNFIXED_OVERRUN`` at the measured per-source
    time. The yardstick used to be a fixed twelve, which is right at 0.83 s a source and
    nowhere else.
    """
    return max(MIN_RELEASABLE, math.ceil(STOP_GRACE_S * UNFIXED_OVERRUN / per_source_s) + 1)


def _worker_seconds_per_source(batch_lines: list[dict[str, Any]]) -> float:
    """The worker's own time per source, from its ``embedding_batch`` lines.

    Measured where the stop waits: a batch's ``duration_ms`` over the sources it
    processed (claimed less released). Wall-clock drain time divided by the corpus also
    counts the concurrent writes and the claim's one-second hold-back, and is only an
    upper bound on it.
    """
    processed = sum(int(line["claimed"]) - int(line.get("released") or 0) for line in batch_lines)
    seconds = sum(float(line["duration_ms"]) for line in batch_lines) / 1000
    assert processed > 0, batch_lines
    return seconds / processed


# ------------------------------------------------------------------ the corpus


def _body(index: int, nonce: str, words: int) -> str:
    """The nonce as its own paragraph, then ``words`` rounded up to whole cycles.

    Its own paragraph so it is its own small first chunk: a nonce's token count varies
    with its hex digits, and inline it moved the filler's window boundaries by a token or
    two, which near a boundary is a chunk.
    """
    draw = random.Random(index)
    filler: list[str] = []
    for _ in range(math.ceil(words / len(VOCABULARY))):
        cycle = list(VOCABULARY)
        draw.shuffle(cycle)
        filler.extend(cycle)
    return f"{nonce}\n\n{' '.join(filler)}"


def _nonces(count: int) -> list[str]:
    """One searchable term per record, present in exactly one record's first chunk.

    Counts alone would pass over the wrong content, so the yardstick reads two of these
    back through real search: the first record written and the last one before the stop.
    """
    stem = uuid.uuid4().hex[:8]
    return [f"zq{stem}x{index:03d}" for index in range(count)]


# -------------------------------------------------------------------- the arm


@dataclass
class Arm:
    """One container, its volume, its published origin and its admin credential."""

    name: str
    cid: str
    origin: str
    token: str
    client: httpx2.Client
    nonces: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)

    def call(self, method: str, path: str, body: Any = None) -> httpx2.Response:
        return self.client.request(
            method,
            path,
            json=body,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=HTTP_TIMEOUT_S,
        )

    def status(self) -> dict[str, Any]:
        response = self.call("GET", "/api/v1/admin/search-index")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        return body

    def wait_for(
        self, predicate: Callable[[dict[str, Any]], bool], *, timeout: float, interval: float
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last = self.status()
            if predicate(last):
                return last
            time.sleep(interval)
        raise AssertionError(
            f"[{self.name}] the search-index status never satisfied the condition within "
            f"{timeout}s. last={last}\n\n--- docker logs ---\n{ds.logs(self.cid, tail=None)}"
        )

    def wait_ready(self, *, timeout: float = READY_TIMEOUT_S) -> float:
        """Poll ``/readyz`` until it answers ``{"status": "ok"}``; return how long that
        took.

        The readiness body, not the status code. In the container ``web/dist`` is
        always present, so the SPA catch-all is always registered: if ``/readyz`` ever
        went away, a status-only poll would have been satisfied by ``index.html`` and
        every container test would have stopped waiting for readiness and started racing
        a still-migrating application.
        """
        started = time.monotonic()
        deadline = started + timeout
        while time.monotonic() < deadline:
            try:
                response = self.client.get("/readyz", timeout=5)
            except httpx2.HTTPError:
                response = None
            if response is not None and _is_ready_body(response):
                return time.monotonic() - started
            time.sleep(0.05)
        raise AssertionError(
            f"[{self.name}] /readyz did not answer {{'status': 'ok'}} within {timeout}s.\n\n"
            f"--- docker logs ---\n{ds.logs(self.cid, tail=None)}"
        )


@contextmanager
def _arm(image_tag: str, name: str) -> Iterator[Arm]:
    """A fresh container on its own named volume, bootstrapped over HTTP.

    A named volume **and** a published port, which is what ``create_container`` was
    extended for: the volume because the whole proof is stop, restart, and find the same
    queue; the port because the credential is obtained over HTTP rather than by running
    the operator CLI inside the container, which would have meant a second copy of
    ``docker_support.bootstrap_admin``'s committed password default.
    """
    secret = secrets.token_urlsafe(24)
    # 24 characters, against a policy that is a length floor of 12 and nothing else
    # (services/passwords.py). Generated rather than written down, so this file adds no
    # credential-shaped literal of any length.
    password = secrets.token_urlsafe(18)
    port = ds.free_port()
    origin = f"http://127.0.0.1:{port}"
    volume = ds.create_volume(f"gw-shutdown-{name}-{uuid.uuid4().hex[:8]}")
    cid = ds.create_container(
        image_tag,
        volume=volume,
        name_prefix=f"gw-shutdown-{name}",
        environment={"GW_BASE_URL": origin, "GW_BOOTSTRAP_SECRET": secret},
        port=port,
    )
    client = httpx2.Client(base_url=origin)
    try:
        ds.start_stopped_container(cid)
        arm = Arm(name=name, cid=cid, origin=origin, token="", client=client)
        arm.wait_ready()
        handoff = client.post(
            "/api/v1/bootstrap",
            json={"secret": secret, "email": EMAIL, "password": password},
            timeout=HTTP_TIMEOUT_S,
        )
        assert handoff.status_code == 201, handoff.text
        arm.token = handoff.json()["token"]
        yield arm
    finally:
        client.close()
        ds.remove_container(cid)
        ds.remove_volume(volume)


def _write_corpus(
    arm: Arm, words: int, *, sources: int = CORPUS_SOURCES, create_type: bool = True
) -> None:
    """Create the object type and write the corpus as fast as the API allows.

    Concurrent on purpose. The claim predicate holds a brand-new job back for one second
    (``min(300, 2 ** attempts)``), so the narrower the window in which the corpus is
    enqueued, the more of it becomes claimable at the same instant and the closer the
    worker's first claim gets to a full ``BATCH_SIZE`` batch. The arm asserts what it
    actually observed rather than assuming it got one.
    """
    if create_type:
        created = arm.call("POST", "/api/v1/object-types", OBJECT_TYPE)
        assert created.status_code == 200, created.text
    arm.nonces = _nonces(sources)

    def write(pair: tuple[int, str]) -> str:
        index, nonce = pair
        response = arm.call(
            "POST",
            f"/api/v1/object-types/{OBJECT_TYPE['key']}/records",
            {"body": _body(index, nonce, words)},
        )
        assert response.status_code == 200, response.text
        key: str = response.json()["key"]
        return key

    with ThreadPoolExecutor(max_workers=8) as pool:
        arm.keys = list(pool.map(write, list(enumerate(arm.nonces))))
    assert len(arm.keys) == sources, arm.keys


def _batch_lines(log: str) -> list[dict[str, Any]]:
    events = []
    for line in log.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("event") == "embedding_batch":
            events.append(event)
    return events


def _yardstick(arm: Arm, expected_chunks: int) -> dict[str, Any]:
    """The five clauses "no lost work" means, measured rather than inferred."""
    status = arm.wait_for(
        lambda body: bool(body["pending_jobs"] == 0), timeout=DRAIN_TIMEOUT_S, interval=0.5
    )
    assert status["indexed_chunks"] == expected_chunks, (
        f"[{arm.name}] indexed_chunks is {status['indexed_chunks']}, the control arm "
        f"reached {expected_chunks} over the identical corpus. A re-indexed source and a "
        f"skipped source both move this number, in opposite directions."
    )
    assert status["failed_jobs"] == [], status
    assert status["stale_chunks"] == 0, status
    assert status["running_jobs"] == 0, status

    # ``keyword``, not ``hybrid``: the semantic arm returns its k nearest whatever the
    # query, so "exactly one record holds this term" is a statement the keyword arm can
    # make and the hybrid one cannot.
    for label, index in (("first", 0), ("last", CORPUS_SOURCES - 1)):
        nonce = arm.nonces[index]
        found = arm.call("POST", "/api/v1/search", {"query": nonce, "mode": "keyword"})
        assert found.status_code == 200, found.text
        body = found.json()
        keys = [hit["record_key"] for hit in body["results"]]
        assert keys == [arm.keys[index]], (
            f"[{arm.name}] the {label} record's nonce {nonce} returned {keys}, not "
            f"{[arm.keys[index]]}; counts alone would pass over the wrong content"
        )
    return status


def _interrupt_and_restart(
    arm: Arm, interrupt: Callable[[Arm], float], words: int, required_running: int
) -> dict[str, Any]:
    """Everything the graceful and killed arms share: write the corpus, catch a batch in
    flight, interrupt, restart, and hand back **observations**.

    It asserts nothing beyond what it needs to get that far. An assertion that only ever
    runs after a failing one has never been measured (AGENTS.md, Traps), and a fixture
    that asserts turns every test depending on it into one error with one cause. So the
    drain, the yardstick and every discriminator are separate tests below, each able to
    fail on its own against the unfixed tree.
    """
    observed: dict[str, Any] = {"arm": arm, "required_running": required_running}
    _write_corpus(arm, words)
    at_signal = arm.wait_for(
        lambda body: bool(body["indexed_chunks"] > 0 and body["running_jobs"] >= required_running),
        timeout=DRAIN_TIMEOUT_S,
        interval=0.1,
    )
    observed["running_at_signal"] = at_signal["running_jobs"]
    observed["indexed_at_signal"] = at_signal["indexed_chunks"]

    observed["interrupt_s"] = round(interrupt(arm), 3)
    observed["exit_code"] = ds.exit_code(arm.cid)
    before_restart = ds.logs(arm.cid, tail=None)
    observed["claimed_batches"] = [line["claimed"] for line in _batch_lines(before_restart)]
    observed["released_batches"] = [line.get("released") for line in _batch_lines(before_restart)]
    observed["pre_restart_log"] = before_restart

    started = time.monotonic()
    ds.start_stopped_container(arm.cid)
    observed["start_call_s"] = round(time.monotonic() - started, 3)
    arm.wait_ready()
    observed["cold_start_s"] = round(time.monotonic() - started, 3)
    observed["after_restart"] = ds.logs(arm.cid, tail=None)[len(before_restart) :]
    return observed


def _publish(name: str, observed: dict[str, Any]) -> dict[str, Any]:
    REPORT[name] = {
        key: value
        for key, value in observed.items()
        if key not in ("arm", "pre_restart_log", "after_restart")
    }
    return observed


# ------------------------------------------------------------------ the arms


@pytest.fixture(scope="module")
def sizing(image_tag: str) -> dict[str, Any]:
    """Measure this machine, then choose the words per source every arm writes.

    Its own container, so the control arm's yardstick counts the corpus and nothing
    else. A warm-up source first, so whatever the first embedding pays once is not
    charged to the probe; then ``PROBE_SOURCES`` sources of ``PROBE_WORDS``, timed by
    the worker's own batch lines. Words scale linearly to ``TARGET_SOURCE_S``, inside
    the chunk bounds. This only aims: the control arm measures what it actually got and
    asserts the band. The container is removed before the other arms start, so it
    takes no CPU from them.
    """
    with _arm(image_tag, "sizing") as arm:
        _write_corpus(arm, PROBE_WORDS, sources=1)
        arm.wait_for(
            lambda body: bool(body["pending_jobs"] == 0), timeout=DRAIN_TIMEOUT_S, interval=0.2
        )
        warmed = len(ds.logs(arm.cid, tail=None))
        _write_corpus(arm, PROBE_WORDS, sources=PROBE_SOURCES, create_type=False)
        status = arm.wait_for(
            lambda body: bool(body["pending_jobs"] == 0), timeout=DRAIN_TIMEOUT_S, interval=0.2
        )
        assert status["failed_jobs"] == [], status
        probe_lines = _batch_lines(ds.logs(arm.cid, tail=None)[warmed:])
        probe_source_s = _worker_seconds_per_source(probe_lines)
        chunks_per_probe = status["indexed_chunks"] / (PROBE_SOURCES + 1)
        words_per_chunk = PROBE_WORDS / chunks_per_probe
        aimed = round(PROBE_WORDS * TARGET_SOURCE_S / probe_source_s)
        words = min(
            max(aimed, math.ceil(MIN_CHUNKS_PER_SOURCE * words_per_chunk)),
            math.floor(MAX_CHUNKS_PER_SOURCE * words_per_chunk),
        )
        # Said here, in one line, rather than as a 180 s drain timeout three arms later.
        predicted_s = probe_source_s * words / PROBE_WORDS
        assert predicted_s <= PER_SOURCE_CEILING_S, (
            f"this machine takes {probe_source_s:.3f}s over a {PROBE_WORDS}-word source, so "
            f"even the shortest corpus the arms can use ({words} words) would take about "
            f"{predicted_s:.1f}s a source, over the {PER_SOURCE_CEILING_S}s ceiling that keeps "
            "one source inside the worker's STOP_GRACE_SECONDS. The graceful arm cannot be "
            "sized to pass for the right reason here; this is a machine too slow for the "
            "proof, not a regression."
        )
    return _publish(
        "sizing",
        {
            "probe_source_s": round(probe_source_s, 3),
            "probe_chunks_per_source": chunks_per_probe,
            "words_per_source": words,
        },
    )


@pytest.fixture(scope="module")
def control(image_tag: str, sizing: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """The yardstick: the same corpus, drained with nothing interrupting it.

    Also the arm that measures the corpus. ``C`` is asserted to be an exact multiple of
    the number of sources the arm wrote, so a corpus that drifted between arms shows up
    as itself rather than as a failure in the arm under test. Its per-source time, read
    from the worker's own batch lines, is what the other two arms size their signal to.
    """
    with _arm(image_tag, "control") as arm:
        started = time.monotonic()
        _write_corpus(arm, sizing["words_per_source"])
        status = arm.wait_for(
            lambda body: bool(body["pending_jobs"] == 0), timeout=DRAIN_TIMEOUT_S, interval=0.5
        )
        elapsed = time.monotonic() - started
        chunks = int(status["indexed_chunks"])
        per_source_s = _worker_seconds_per_source(_batch_lines(ds.logs(arm.cid, tail=None)))
        yield _publish(
            "control",
            {
                "indexed_chunks": chunks,
                "sources": CORPUS_SOURCES,
                "words_per_source": sizing["words_per_source"],
                "chunks_per_source": chunks // CORPUS_SOURCES,
                "drain_s": round(elapsed, 3),
                "per_source_s": round(per_source_s, 3),
                "required_running": _required_running(per_source_s),
                "status": status,
            },
        )


def test_the_control_arm_drains_the_whole_corpus_and_sets_the_yardstick(
    control: dict[str, Any],
) -> None:
    """``C``, and the corpus rule that makes the other two arms able to fail."""
    print(f"\n[control] {json.dumps(REPORT['control'])}")
    status = control["status"]
    assert status["failed_jobs"] == [], status
    assert status["running_jobs"] == 0, status
    assert status["stale_chunks"] == 0, status
    assert control["chunks_per_source"] >= 2, control
    assert control["indexed_chunks"] == control["chunks_per_source"] * CORPUS_SOURCES, (
        "the corpus is not a clean multiple of the sources written, so a later arm's "
        "exact-count assertion would be measuring corpus drift rather than lost work"
    )
    assert control["per_source_s"] <= PER_SOURCE_CEILING_S, (
        f"a source takes the worker {control['per_source_s']}s at "
        f"{control['words_per_source']} words, over the {PER_SOURCE_CEILING_S}s ceiling: "
        "a single source may no longer fit inside the worker's STOP_GRACE_SECONDS on the "
        f"fixed tree. The sizing arm aimed at {TARGET_SOURCE_S}s and missed by more "
        f"than {PER_SOURCE_CEILING_S / TARGET_SOURCE_S}x, or this machine cannot embed "
        f"even {MIN_CHUNKS_PER_SOURCE} chunks inside it. Report: {REPORT.get('sizing')}"
    )
    assert control["required_running"] <= MAX_REQUIRED_RUNNING, (
        f"at {control['per_source_s']}s a source, {control['required_running']} sources "
        f"must still be claimed when the signal lands for the unfixed tree to overrun "
        f"the stop grace, and the arms can only rely on {MAX_REQUIRED_RUNNING} at once: "
        "the graceful arm could not fail. The sources are too short for this machine; the "
        "sizing arm aimed too low, or this machine is fast enough to hit the chunk cap. "
        f"Report: {REPORT.get('sizing')}"
    )


# ---------------------------------------------------------------- graceful arm


@pytest.fixture(scope="module")
def graceful(image_tag: str, control: dict[str, Any]) -> Iterator[dict[str, Any]]:
    with _arm(image_tag, "graceful") as arm:
        observed = _interrupt_and_restart(
            arm,
            lambda held: ds.stop_container(held.cid, grace=STOP_GRACE_S),
            control["words_per_source"],
            control["required_running"],
        )
        _publish("graceful", observed)
        print(f"\n[graceful] {json.dumps(REPORT['graceful'])}")
        yield observed


def test_a_graceful_stop_releases_the_rest_of_its_batch(graceful: dict[str, Any]) -> None:
    """**The discriminator.** ``released`` is a field the unfixed tree has no name for.

    It is asserted first and alone because it is the one assertion here that cannot
    quietly become a fence if the corpus is mis-sized: every other signal in this arm can
    be produced by a tree that drains its batch, given short enough sources.
    """
    interrupted = [
        claimed
        for claimed, released in zip(
            graceful["claimed_batches"], graceful["released_batches"], strict=True
        )
        if released
    ]
    assert interrupted, (
        "no embedding_batch line carried a non-zero `released`, so the stop drained the "
        f"batch rather than checkpointing it. batches claimed: {graceful['claimed_batches']}"
    )
    # The batch the stop interrupted, not the first one claimed. A slow machine writes
    # the corpus across the claim's one-second hold-back, so an earlier, smaller batch
    # can drain before the big one; the dry run's arm64 runner claimed [3, 32] and
    # released 31 of the 32, which is the behaviour this arm exists to prove.
    assert interrupted[-1] >= graceful["required_running"], (
        "the batch in flight when the stop landed was too small to outlive the stop "
        f"grace, so this arm was measured against a batch of {interrupted[-1]}; batches "
        f"claimed: {graceful['claimed_batches']}"
    )


def test_a_graceful_stop_takes_the_time_one_source_takes_not_one_batch(
    graceful: dict[str, Any],
) -> None:
    """The stop bound stated as a number, plus the log sequence and the exit code.

    Separate from the discriminator so that it is measured in its own right: a worker
    that holds a whole claimed batch through a stop fails this, because the batch
    outlives ``docker stop -t 10``
    and the container is ``SIGKILL``ed, which moves all three of these at once.
    """
    assert graceful["interrupt_s"] < MAX_STOP_S, (
        f"docker stop took {graceful['interrupt_s']}s with "
        f"{graceful['running_at_signal']} sources still claimed; a stop now covers the "
        "source in hand, not the batch"
    )
    assert "embedding_worker_stopped" in graceful["pre_restart_log"]
    assert "embedding_worker_stop_timed_out" not in graceful["pre_restart_log"]
    # The same clean shutdown exits 0 as PID 1 and 143 under an init, because
    # uvicorn re-raises the signal it caught and Linux drops that on PID 1 only. A test
    # that demanded 0 would report a clean stop as a dirty one on every supervised
    # platform.
    assert graceful["exit_code"] in (0, 143), graceful["exit_code"]


def test_a_graceful_stop_leaves_the_next_start_nothing_to_reclaim(
    graceful: dict[str, Any],
) -> None:
    """**Fence, not coverage.** ``reclaim_all`` logs only when the count is non-zero, so
    an unfixed graceful stop that drained its batch emits nothing here either. Kept
    because it distinguishes release from reclaim on the fixed tree."""
    assert "embedding_jobs_reclaimed" not in graceful["after_restart"], (
        "a graceful stop leaves no running rows, so startup reclaim has nothing to do"
    )


def test_a_graceful_stop_and_restart_loses_no_work(
    graceful: dict[str, Any], control: dict[str, Any]
) -> None:
    """The yardstick: the exact control count, nothing failed, nothing stale, nothing
    running, and the first and last records still findable by their own nonce."""
    _yardstick(graceful["arm"], control["indexed_chunks"])


def test_the_graceful_arm_restarts_inside_the_cold_start_ceiling(
    graceful: dict[str, Any],
) -> None:
    """The cold-start ceiling, riding along on an arm that already stops and starts a container. A
    ceiling, not the recorded number; ``scripts/measure_cold_start.py`` produces that."""
    assert graceful["cold_start_s"] < COLD_START_CEILING_S, graceful["cold_start_s"]


# ------------------------------------------------------------------ killed arm


@pytest.fixture(scope="module")
def killed(image_tag: str, control: dict[str, Any]) -> Iterator[dict[str, Any]]:
    def kill(arm: Arm) -> float:
        started = time.monotonic()
        ds.kill_container(arm.cid)
        return time.monotonic() - started

    with _arm(image_tag, "killed") as arm:
        observed = _interrupt_and_restart(
            arm, kill, control["words_per_source"], control["required_running"]
        )
        _publish("killed", observed)
        print(f"\n[killed] {json.dumps(REPORT['killed'])}")
        yield observed


def test_a_kill_strands_running_rows_and_the_next_start_reclaims_them_all(
    killed: dict[str, Any],
) -> None:
    """DD-35. ``docker kill`` is ``SIGKILL`` with no grace, so no clean path runs at all.

    Against the unfixed tree the next start reclaims nothing: it looked only at rows
    older than ``RUNNING_TIMEOUT_SECONDS``, which is 600, so rows stranded seconds ago
    stayed ``running`` for ten minutes while ``/readyz`` answered 200.
    """
    assert killed["exit_code"] == 137, killed["exit_code"]
    assert "embedding_jobs_reclaimed" in killed["after_restart"], (
        f"{killed['running_at_signal']} rows were running when the container was killed "
        "and the next start reclaimed none of them"
    )
    reclaimed = [
        json.loads(line)
        for line in killed["after_restart"].splitlines()
        if '"embedding_jobs_reclaimed"' in line
    ]
    assert reclaimed and reclaimed[0]["count"] > 0, reclaimed


def test_a_kill_and_restart_loses_no_work(killed: dict[str, Any], control: dict[str, Any]) -> None:
    """The same yardstick as the graceful arm, over the same corpus.

    Against the unfixed tree this cannot drain inside 180 s, because the rows the kill
    stranded wait out the 600 s reclaim threshold, so it fails loudly rather than slowly.
    """
    _yardstick(killed["arm"], control["indexed_chunks"])


def test_the_killed_arm_restarts_inside_the_cold_start_ceiling(killed: dict[str, Any]) -> None:
    assert killed["cold_start_s"] < COLD_START_CEILING_S, killed["cold_start_s"]


# ---------------------------------------------------------------- the numbers


def test_the_measured_numbers_are_reported(
    control: dict[str, Any], graceful: dict[str, Any], killed: dict[str, Any]
) -> None:
    """The numbers reach a person, and a file when one is named.

    The report is a run artifact quoted in the pull request, not a committed file: it
    is machine-specific and every re-run would rewrite it.
    """
    print(f"\n[report] {json.dumps(REPORT, indent=2, default=str)}")
    destination = os.environ.get("GW_SHUTDOWN_REPORT")
    if destination:
        Path(destination).write_text(json.dumps(REPORT, indent=2, default=str) + "\n")
    assert set(REPORT) == {"sizing", "control", "graceful", "killed"}
