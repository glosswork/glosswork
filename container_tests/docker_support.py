"""Host-side ``docker`` CLI wrappers for the network-isolated container proof.

Every function here shells out to the ``docker`` binary rather than a Python Docker
SDK, matching DD-32's preference for no new dependency where a subprocess call
does the job, and keeping this module runnable on a machine that has nothing but
Docker itself installed.

No port is published on a ``--network none`` container, so :func:`api_call` and
:func:`seed_comments` reach the API through ``docker exec <cid> python3
/tmp/gw_client.py ...`` -- ``_gw_client.py``, copied into the container once by
:func:`start_container` -- against ``http://localhost:8000`` from inside the
container's own network namespace.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPT = Path(__file__).resolve().parent / "_gw_client.py"
CLIENT_PATH_IN_CONTAINER = "/tmp/gw_client.py"

DEFAULT_IMAGE_TAG = "glosswork:container-test"


class DockerError(RuntimeError):
    """A ``docker`` invocation failed; the message carries stdout and stderr."""


def _run(
    cmd: list[str], *, input_bytes: bytes | None = None, timeout: float = 60
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(cmd, input=input_bytes, capture_output=True, timeout=timeout)


def _require(result: subprocess.CompletedProcess[bytes], what: str) -> None:
    if result.returncode != 0:
        raise DockerError(
            f"{what} failed (exit {result.returncode}):\n"
            f"stdout={result.stdout.decode(errors='replace')}\n"
            f"stderr={result.stderr.decode(errors='replace')}"
        )


# --------------------------------------------------------------------- lifecycle


def _head_revision() -> str:
    """``git rev-parse HEAD``, or ``""`` if this is not a git checkout.

    Only used to label a locally built image. A build outside a checkout still works; the
    label falls back to the ``Dockerfile``'s ``unknown``, and
    ``test_image_notices.py`` rejects that, which is the intended signal.
    """
    result = _run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], timeout=30)
    return result.stdout.decode().strip() if result.returncode == 0 else ""


def build_image(tag: str) -> str:
    """Build the image, labelled with the revision it was built from.

    The ``--build-arg`` matters because ``org.opencontainers.image.revision`` is the only
    thing on a published image that says which commit produced it, and the ``Dockerfile``
    defaults it to ``unknown`` rather than guessing. CI's ``image:build`` passes
    ``$CI_COMMIT_SHA`` for the same reason.
    """
    cmd = ["docker", "build", "-t", tag]
    revision = _head_revision()
    if revision:
        cmd += ["--build-arg", f"GW_REVISION={revision}"]
    cmd.append(str(REPO_ROOT))
    result = _run(cmd, timeout=900)
    _require(result, f"docker build -t {tag}")
    return tag


def start_container(image_tag: str, *, name_prefix: str = "gw-container-test") -> str:
    """Start a fresh, network-isolated container and install the API client into it.

    Returns the container id. The caller is responsible for :func:`remove_container`
    in a ``finally`` block, including on a failing test.
    """
    name = f"{name_prefix}-{uuid.uuid4().hex[:8]}"
    result = _run(["docker", "run", "-d", "--network", "none", "--name", name, image_tag])
    _require(result, f"docker run --network none {image_tag}")
    cid = result.stdout.decode().strip()
    copy = _run(["docker", "cp", str(CLIENT_SCRIPT), f"{cid}:{CLIENT_PATH_IN_CONTAINER}"])
    _require(copy, f"docker cp {CLIENT_SCRIPT.name} into {cid}")
    return cid


def free_port() -> int:
    """A loopback port nothing is listening on, for :func:`start_published_container`.

    Bind-and-release rather than a fixed number, so two runs of this suite (or a run
    beside a dev server on 8000) cannot collide. The gap between releasing it and
    Docker binding it is a race in principle; in practice the kernel does not reuse a
    just-closed port that fast, and a fixed port collides far more often.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_published_container(
    image_tag: str,
    *,
    environment: dict[str, str],
    port: int,
    name_prefix: str = "gw-published-test",
) -> str:
    """Start a container reachable **from the host** on ``127.0.0.1:<port>``.

    Every other helper in this module starts a ``--network none`` container and talks
    to it through ``docker exec``, which is the right shape for proving the image works
    with no egress. It is the wrong shape for the bootstrap handoff, which exists so
    that a program *outside* the container can get a credential over HTTP: a proof that
    ran commands inside the container would be proving the thing the handoff replaces.

    So this one publishes a port and passes environment, and the test that uses it
    reaches the application only over HTTP. Bound to ``127.0.0.1`` rather than all
    interfaces, because a test fixture should not put a deployment holding a bootstrap
    secret on the network.

    The API client script is deliberately not copied in: nothing here runs inside the
    container.
    """
    name = f"{name_prefix}-{uuid.uuid4().hex[:8]}"
    cmd = ["docker", "run", "-d", "-p", f"127.0.0.1:{port}:8000"]
    for key, value in environment.items():
        cmd += ["-e", f"{key}={value}"]
    cmd += ["--name", name, image_tag]
    result = _run(cmd)
    _require(result, f"docker run -p 127.0.0.1:{port}:8000 {image_tag}")
    return result.stdout.decode().strip()


def remove_container(cid: str) -> None:
    """Force-remove a container **and its anonymous volumes**. Never raises: fixture
    teardown must not mask a test's own failure with a cleanup failure.

    ``-v`` matters. The image declares ``VOLUME ["/data"]``, so every container this
    suite starts without naming a volume gets a fresh anonymous one, and ``docker rm -f``
    leaves it behind. One suite run stranded one volume per container it started: the
    maintainer's machine was observed on 2026-09-19 holding 214 dangling anonymous
    volumes out of 216, and Docker's disk had already filled once.

    ``-v`` removes anonymous volumes only, so the named volumes ``create_volume`` makes
    for the backup-restore proof survive a container's removal exactly as before.

    This is the suite's single removal chokepoint, which is why one flag closes all nine
    call sites. It is **not** the only ``docker rm -f`` in the repository:
    ``scripts/measure_cold_start.py`` has another, and it mounts a named volume at
    ``/data``, so it strands nothing and is deliberately left alone.

    Nothing in CI protects this. ``pyproject.toml`` sets ``testpaths = ["tests"]`` and
    the image job does not run ``container_tests``, so the guard is
    ``container_tests/test_volume_hygiene.py``, which a person runs.
    """
    _run(["docker", "rm", "-f", "-v", cid], timeout=30)


def kill_container(cid: str) -> None:
    result = _run(["docker", "kill", cid], timeout=30)
    _require(result, f"docker kill {cid}")


def stop_container(cid: str, *, grace: int = 10) -> float:
    """``docker stop -t <grace>`` and the wall time it took, in seconds.

    The duration is returned rather than logged because the clean-shutdown proof's
    graceful arm asserts on it: "the stop no longer waits for the whole claimed batch"
    is a behaviour that is only worth anything stated as a number.
    """
    started = time.monotonic()
    result = _run(["docker", "stop", "-t", str(grace), cid], timeout=grace + 60)
    elapsed = time.monotonic() - started
    _require(result, f"docker stop -t {grace} {cid}")
    return elapsed


def exit_code(cid: str) -> int:
    """``docker inspect -f '{{.State.ExitCode}}'``.

    Know this before asserting a value: an identical clean shutdown exits ``0`` when
    the application is PID 1 and ``143`` when anything supervises it, because uvicorn
    re-raises the signal it caught and Linux discards a default-action signal aimed at
    PID 1 only. This number says which process is PID 1, not whether the stop was clean.
    """
    result = _run(["docker", "inspect", "-f", "{{.State.ExitCode}}", cid], timeout=30)
    _require(result, f"docker inspect {cid}")
    return int(result.stdout.decode().strip())


def start_stopped_container(cid: str) -> None:
    result = _run(["docker", "start", cid], timeout=30)
    _require(result, f"docker start {cid}")


# ---------------------------------------------------------------- named volumes
#
# The backup-restore proof (test_backup_restore.py) needs containers whose
# lifecycle is "create, populate /data, then boot" -- the opposite order from
# start_container's "boot, then talk to it over exec" -- because a restore has to
# land on an empty volume *before* the application ever starts (migrations run at
# startup, per FR-P6, and must see the restored database on their first pass).


def create_volume(name: str) -> str:
    """Create a named Docker volume and return its name.

    A named volume, not an anonymous one bound to a single container's lifecycle,
    because the restore proof create()s a container against it, copies files in
    while the container is stopped, *then* starts it -- an anonymous volume tied to
    ``docker run`` wouldn't exist yet at the point the files need to land.
    """
    result = _run(["docker", "volume", "create", name], timeout=30)
    _require(result, f"docker volume create {name}")
    return name


def remove_volume(name: str) -> None:
    """Force-remove a named volume. Never raises: fixture teardown must not mask a
    test's own failure with a cleanup failure, and a leaked named volume is worse
    than a leaked container -- it has no TTL and outlives every container that
    referenced it."""
    _run(["docker", "volume", "rm", "-f", name], timeout=30)


def create_container(
    image_tag: str,
    *,
    volume: str,
    name_prefix: str = "gw-container-test",
    user: str | None = None,
    entrypoint: str | None = None,
    command: list[str] | None = None,
    environment: dict[str, str] | None = None,
    port: int | None = None,
) -> str:
    """``docker create`` (never started) a container with ``volume`` mounted at
    ``/data``, and install the API client into it.

    Deliberately create-not-run, unlike :func:`start_container`: the restore proof
    needs to place files into ``/data`` (via :func:`copy_in`) before the
    application's first boot, which is only possible while the container has not
    started yet. The caller starts it explicitly with :func:`start_stopped_container`
    once the volume is ready, and is responsible for :func:`remove_container` in a
    ``finally`` block, including on a failing test.

    ``user``/``entrypoint``/``command`` override the image's defaults, which the
    restore proof's seeding helper uses to run as ``root`` with an idle entrypoint
    (see :func:`seed_data_volume`) rather than launching the real application.
    ``docker cp`` works against a stopped container's filesystem, so the client
    script installs here exactly as it does in :func:`start_container`.

    ``environment`` and ``port`` exist for the clean-shutdown proof, which is the
    first test here needing a named volume **and** a published port at once: a volume
    because the whole proof is stop, restart, and find the same queue; a port because
    the container is bootstrapped over HTTP, which is what keeps a second copy
    of :func:`bootstrap_admin`'s committed password literal out of this directory.
    Publishing a port drops ``--network none``, because the two cannot both hold: the
    loopback publication is bound to ``127.0.0.1`` so the container is reachable from
    this host and from nowhere else.
    """
    name = f"{name_prefix}-{uuid.uuid4().hex[:8]}"
    cmd = ["docker", "create"]
    if port is None:
        cmd += ["--network", "none"]
    else:
        cmd += ["-p", f"127.0.0.1:{port}:8000"]
    cmd += ["-v", f"{volume}:/data"]
    for key, value in (environment or {}).items():
        cmd += ["-e", f"{key}={value}"]
    if user is not None:
        cmd += ["--user", user]
    if entrypoint is not None:
        cmd += ["--entrypoint", entrypoint]
    cmd += ["--name", name, image_tag]
    if command is not None:
        cmd += command
    result = _run(cmd)
    _require(result, f"docker create -v {volume}:/data {image_tag}")
    cid = result.stdout.decode().strip()
    copy = _run(["docker", "cp", str(CLIENT_SCRIPT), f"{cid}:{CLIENT_PATH_IN_CONTAINER}"])
    _require(copy, f"docker cp {CLIENT_SCRIPT.name} into {cid}")
    return cid


def copy_out(cid: str, container_path: str, host_path: Path) -> None:
    """``docker cp <cid>:<container_path> <host_path>``: pull one file (or
    directory) out of a container onto the host filesystem."""
    result = _run(["docker", "cp", f"{cid}:{container_path}", str(host_path)], timeout=120)
    _require(result, f"docker cp {cid}:{container_path} {host_path}")


def copy_in(cid: str, host_path: Path | str, container_path: str) -> None:
    """``docker cp <host_path> <cid>:<container_path>``: push a file (or directory)
    from the host into a container, running or stopped.

    Ownership warning, load-bearing for the restore proof: ``docker cp`` writes
    files owned by the *host* caller's uid/gid, not the image's runtime user
    (verified empirically against this image -- see :func:`seed_data_volume`,
    which exists because of it). A bare ``copy_in`` straight into the real
    container's ``/data`` leaves the application's non-root ``appuser`` unable to
    write the restored database; callers that need a bootable volume must go
    through :func:`seed_data_volume` instead of calling this directly.
    """
    result = _run(["docker", "cp", str(host_path), f"{cid}:{container_path}"], timeout=120)
    _require(result, f"docker cp {host_path} {cid}:{container_path}")


def seed_data_volume(image_tag: str, volume: str, host_dir: Path) -> None:
    """Populate a fresh named volume from ``host_dir``'s contents, owned correctly
    for the runtime image's non-root user, before the real container ever boots.

    ``docker cp`` always writes with the *host* caller's uid/gid (proven empirically
    while building this test: a file copied in this way came back owned by the host
    user with group ``root``, which the image's ``appuser`` -- uid 1000, its own
    group -- cannot write). Restoring a database this way would boot into a
    permission error on the very first write, not the restore this proof exists to
    demonstrate.

    The fix is a throwaway helper container attached to the *same* volume, running
    as ``root`` with its entrypoint overridden to an idle ``sleep`` so the real
    application never starts against a half-populated volume: copy ``host_dir`` in,
    ``chown -R`` it to the runtime uid/gid, then remove the helper. The volume is
    left correctly owned for :func:`create_container` to build the real, unmodified
    container against next.
    """
    helper = create_container(
        image_tag,
        volume=volume,
        name_prefix="gw-seed-helper",
        user="root",
        entrypoint="/bin/sleep",
        command=["3600"],
    )
    try:
        start_stopped_container(helper)
        copy_in(helper, f"{host_dir}/.", "/data")
        chown = exec_in(helper, ["chown", "-R", "1000:1000", "/data"], timeout=60)
        _require(chown, f"chown -R 1000:1000 /data in seeding helper for volume {volume}")
    finally:
        remove_container(helper)


def logs(cid: str, *, tail: int | None = 200) -> str:
    """The container's log, tailed to ``tail`` lines, or the whole history at ``None``.

    A caller asserting that a line appeared **after** a restart needs the whole history
    and its own length bookmark: a tail can silently drop the pre-restart half, and then
    "no reclaim line is present" is a statement about 200 lines rather than about the
    run.
    """
    cmd = ["docker", "logs"]
    if tail is not None:
        cmd += ["--tail", str(tail)]
    result = _run([*cmd, cid], timeout=30)
    return result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")


# ------------------------------------------------------------------------- exec


def exec_in(
    cid: str, args: list[str], *, timeout: float = 30, stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    cmd = ["docker", "exec"]
    if stdin is not None:
        cmd.append("-i")
    cmd += [cid, *args]
    return _run(cmd, input_bytes=stdin, timeout=timeout)


def bootstrap_admin(
    cid: str,
    *,
    email: str = "admin@container-test.local",
    password: str = "ContainerTest1234!",
) -> str:
    """Create the first admin and mint an admin PAT through the operator CLI
    (FR-P3), exactly the bootstrap path a credential-only deployment needs. Returns
    the plaintext token."""
    create = exec_in(
        cid,
        [
            "python",
            "-m",
            "glosswork.admin",
            "create-admin",
            "--email",
            email,
            "--password",
            password,
        ],
    )
    _require(create, "create-admin")
    mint = exec_in(
        cid,
        [
            "python",
            "-m",
            "glosswork.admin",
            "mint-token",
            "--name",
            "container-tests",
            "--scope",
            "admin",
            "--quiet",
        ],
    )
    _require(mint, "mint-token")
    token = mint.stdout.decode().strip()
    if not token.startswith("gw_pat_"):
        raise DockerError(f"mint-token --quiet did not print a plaintext token: {token!r}")
    return token


# --------------------------------------------------------------------------- API


def api_call(
    cid: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: Any = None,
    timeout: float = 20,
) -> dict[str, Any]:
    """``{"status": int, "body": <parsed JSON or None>}`` for one REST call, made from
    inside the container via ``docker exec`` (no port is published to call from
    outside)."""
    input_bytes = json.dumps(body).encode("utf-8") if body is not None else b""
    result = exec_in(
        cid,
        ["python3", CLIENT_PATH_IN_CONTAINER, "call", method, path, token or "-"],
        timeout=timeout,
        stdin=input_bytes,
    )
    _require(result, f"api_call {method} {path}")
    line = result.stdout.decode().strip().splitlines()[-1]
    parsed: dict[str, Any] = json.loads(line)
    return parsed


def upload_attachment(
    cid: str,
    token: str,
    filename: str,
    content_type: str,
    content: bytes,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    """``POST /api/v1/attachments`` with a real ``multipart/form-data`` body (see
    ``_gw_client.py``'s ``upload`` command) from inside the container. ``api_call``
    cannot do this: its body is JSON-only, and a file upload isn't JSON.
    Returns ``{"status": int, "body": <parsed JSON or None>}``, matching
    :func:`api_call`'s shape."""
    result = exec_in(
        cid,
        ["python3", CLIENT_PATH_IN_CONTAINER, "upload", token, filename, content_type],
        timeout=timeout,
        stdin=content,
    )
    _require(result, f"upload_attachment {filename}")
    line = result.stdout.decode().strip().splitlines()[-1]
    parsed: dict[str, Any] = json.loads(line)
    return parsed


def download_to_container(
    cid: str,
    method: str,
    path: str,
    token: str,
    out_path_in_container: str,
    *,
    timeout: float = 60,
) -> dict[str, Any]:
    """GET or POST one endpoint and write the RAW response body to a file inside the
    container (see ``_gw_client.py``'s ``download`` command), for a response
    ``api_call`` cannot carry -- a tar artifact, or a non-JSON error body a test needs
    to inspect byte-for-byte. Returns ``{"status": int, "bytes": int, "sha256": str}``
    describing what landed at ``out_path_in_container``; the caller pulls it onto the
    host with :func:`copy_out` if it needs the bytes themselves."""
    result = exec_in(
        cid,
        [
            "python3",
            CLIENT_PATH_IN_CONTAINER,
            "download",
            method,
            path,
            token,
            out_path_in_container,
        ],
        timeout=timeout,
    )
    _require(result, f"download {method} {path} -> {out_path_in_container}")
    line = result.stdout.decode().strip().splitlines()[-1]
    parsed: dict[str, Any] = json.loads(line)
    return parsed


def touch_in_container(cid: str, path: str, *, timeout: float = 10) -> None:
    """Create an empty file at ``path`` inside a running container, through a real
    ``docker exec``.

    Used as a stop signal for :func:`run_loop_write`'s writer loop, which polls for
    the file's existence between writes rather than needing a signal delivered to a
    PID: the two are separate ``docker exec`` sessions, but both see the same
    container filesystem, so the write is visible to the loop on its very next check.
    """
    result = exec_in(cid, ["python3", "-c", f"open({path!r}, 'w').close()"], timeout=timeout)
    _require(result, f"touch {path} in {cid}")


def run_loop_write(
    cid: str,
    token: str,
    object_type_key: str,
    stop_marker_path: str,
    *,
    timeout: float = 120,
) -> dict[str, Any]:
    """Run ``_gw_client.py``'s ``loop-write`` command: one ``docker exec`` that
    writes records of ``object_type_key`` in a tight, in-container loop until
    :func:`touch_in_container` creates ``stop_marker_path``.

    This call **blocks for as long as the loop runs**, so it is meant to be the
    target of a host-side thread the caller starts before, and stops (via
    :func:`touch_in_container`, then ``Thread.join``) after, whatever concurrent
    operation it exists to overlap -- see the backup-restore proof's FR-P8 clause,
    where a per-write ``docker exec`` (as :func:`api_call` would require, called in a
    host-side loop) proved too slow to reliably overlap a fast backup on a small test
    database.

    Returns ``{"succeeded": [{"key", "note"}, ...], "failed": <error body> | None}``.
    """
    result = exec_in(
        cid,
        [
            "python3",
            CLIENT_PATH_IN_CONTAINER,
            "loop-write",
            object_type_key,
            token,
            stop_marker_path,
        ],
        timeout=timeout,
    )
    _require(result, f"loop-write {object_type_key}")
    line = result.stdout.decode().strip().splitlines()[-1]
    parsed: dict[str, Any] = json.loads(line)
    return parsed


def seed_comments(
    cid: str, ref: str, token: str, count: int, *, start: int = 0, timeout: float = 60
) -> dict[str, Any]:
    """POST ``count`` comments onto ``ref`` in one ``docker exec`` (see
    ``_gw_client.py``'s ``seed-comments`` command), so seeding a visible backlog costs
    tens of milliseconds rather than one round trip per comment."""
    result = exec_in(
        cid,
        [
            "python3",
            CLIENT_PATH_IN_CONTAINER,
            "seed-comments",
            ref,
            token,
            str(count),
            str(start),
        ],
        timeout=timeout,
    )
    _require(result, f"seed_comments({count}, start={start})")
    line = result.stdout.decode().strip().splitlines()[-1]
    parsed: dict[str, Any] = json.loads(line)
    return parsed


# --------------------------------------------------------------------- polling


def wait_ready(cid: str, *, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        result = exec_in(
            cid, ["python3", CLIENT_PATH_IN_CONTAINER, "call", "GET", "/readyz", "-"], timeout=10
        )
        if result.returncode == 0:
            out = result.stdout.decode().strip()
            if out:
                last = json.loads(out.splitlines()[-1])
                if last.get("status") == 200:
                    return
        time.sleep(0.5)
    raise DockerError(
        f"container {cid} did not become ready within {timeout}s; last={last}\n\n"
        f"--- docker logs (tail) ---\n{logs(cid)}"
    )


def wait_for_status(
    cid: str,
    token: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout: float,
    poll_interval: float = 0.5,
) -> dict[str, Any]:
    """Poll ``GET /api/v1/admin/search-index`` until ``predicate(body)`` is true.

    On timeout, raises with the last observed status payload and the container's
    logs, so a stuck queue is diagnosable from the test failure alone.
    """
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = api_call(cid, "GET", "/api/v1/admin/search-index", token=token)
        if response["status"] != 200:
            raise DockerError(f"GET /api/v1/admin/search-index returned {response}")
        last = response["body"]
        if predicate(last):
            return last
        time.sleep(poll_interval)
    raise DockerError(
        f"search-index status did not satisfy the expected condition within {timeout}s.\n"
        f"last status={last}\n\n--- docker logs (tail) ---\n{logs(cid)}"
    )


def wait_for_drain(cid: str, token: str, *, timeout: float = 60) -> dict[str, Any]:
    return wait_for_status(
        cid, token, lambda body: bool(body["pending_jobs"] == 0), timeout=timeout
    )
