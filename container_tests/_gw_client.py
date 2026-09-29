#!/usr/bin/env python3
"""HTTP client run *inside* the network-isolated container via ``docker exec``.

``docker run --network none`` publishes no port, so there is no host-reachable
address for the container's API at all; the only way in is a process started inside
the container's own network namespace. ``docker cp``'d into the container once by
``docker_support.start_container``, then invoked by every subsequent ``docker exec``
call the test suite makes -- once per REST call for ``call``, once per batch for
``seed-comments`` so seeding a backlog does not pay one ``docker exec`` per comment.

stdlib only, deliberately: this script is the moral equivalent of ``curl`` here, not
application code, and it must work with nothing installed beyond what the image
already ships.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BASE_URL = "http://localhost:8000"
_STREAM_CHUNK_BYTES = 256 * 1024


def _headers(token: str | None, has_body: bool) -> dict[str, str]:
    headers: dict[str, str] = {}
    if token and token != "-":
        headers["Authorization"] = f"Bearer {token}"
    if has_body:
        headers["Content-Type"] = "application/json"
    return headers


def call(method: str, path: str, token: str | None, body: Any) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        headers=_headers(token, data is not None),
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status = response.status
            payload = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = exc.read()
    parsed: Any = None
    if payload:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = payload.decode("utf-8", errors="replace")
    return {"status": status, "body": parsed}


def cmd_call(argv: list[str]) -> int:
    method, path, token = argv[0], argv[1], argv[2]
    raw = sys.stdin.buffer.read()
    body = json.loads(raw) if raw else None
    print(json.dumps(call(method, path, token, body)))
    return 0


def cmd_seed_comments(argv: list[str]) -> int:
    """POST ``count`` comments onto one record in a single process.

    One ``docker exec`` seeds an entire batch, so the restart-resumption clause can
    put a real backlog on the queue in tens of milliseconds rather than paying a
    ``docker exec`` round trip per comment.
    """
    ref, token, count, start = argv[0], argv[1], int(argv[2]), int(argv[3])
    for i in range(start, start + count):
        result = call(
            "POST",
            f"/api/v1/records/{ref}/comments",
            token,
            {"body": f"Seed comment {i} for the restart resumption drill."},
        )
        if result["status"] != 200:
            print(json.dumps(result), file=sys.stderr)
            return 1
    print(json.dumps({"seeded": count}))
    return 0


def cmd_loop_write(argv: list[str]) -> int:
    """Write records of one object type in a tight loop, entirely inside this one
    process, until a stop-marker file appears on disk.

    Exists for the backup-restore proof's concurrent-writer clause (FR-P8,
    "without stopping writes"): a host-side loop that shells out to ``docker exec``
    once *per write* pays roughly 100ms of process-spawn overhead each time, which is
    slower than ``VACUUM INTO`` on the small database the container proof seeds --
    fewer than a handful of writes ever land inside the backup's window that way. A
    loop that pays that startup cost once and then writes over a plain local HTTP
    connection can get many writes into the same window instead. The host starts this
    as one ``docker exec`` on its own thread (it blocks for as long as the loop
    runs), then signals it to stop by creating ``stop_marker_path`` from a second,
    short-lived ``docker exec`` once the operation it was racing has finished.

    ``writing_marker_path`` is created after the first write succeeds, so the host can
    start the operation it races only once this loop is demonstrably writing. Without
    it the two ``docker exec`` sessions start together and a fast machine finishes the
    backup before this process has made its first request (release dry run
    36554918375, amd64). Every write carries its own ``started`` and ``finished``
    readings of ``time.monotonic()``, which on Linux is one clock for every process in
    the container, so the host can count the writes that completed inside the window
    ``download`` reports rather than every write the loop made.
    """
    object_type_key, token, stop_marker_path = argv[0], argv[1], argv[2]
    writing_marker_path = argv[3] if len(argv) > 3 else None
    succeeded: list[dict[str, Any]] = []
    failed: dict[str, Any] | None = None
    i = 0
    while not os.path.exists(stop_marker_path):
        note = f"concurrent write {i}"
        started = time.monotonic()
        result = call(
            "POST", f"/api/v1/object-types/{object_type_key}/records", token, {"note": note}
        )
        finished = time.monotonic()
        if result["status"] != 200:
            failed = result
            break
        succeeded.append(
            {"key": result["body"]["key"], "note": note, "started": started, "finished": finished}
        )
        if i == 0 and writing_marker_path:
            open(writing_marker_path, "w").close()
        i += 1
    print(json.dumps({"succeeded": succeeded, "failed": failed}))
    return 0


def cmd_download(argv: list[str]) -> int:
    """GET or POST one endpoint and write the RAW response body to a file inside the
    container, rather than parsing it as JSON -- ``call`` cannot carry a tar (or, on
    an error response, an arbitrary byte stream) without corrupting it through
    ``json.loads``/``json.dumps``.

    Prints one JSON summary line -- ``{"status", "bytes", "sha256", "started",
    "finished"}`` -- so the host
    side can assert on size and content hash without a second ``docker exec`` to read
    the file back. An HTTP error response's body (still bytes, usually the JSON error
    envelope) is written and hashed exactly the same way as a success, which is what
    lets the restore proof's negative case assert the *specific* failure body
    (``not_found`` / ``"attachment blob"``) rather than merely that the request
    failed.
    """
    method, path, token, out_path = argv[0], argv[1], argv[2], argv[3]
    raw = sys.stdin.buffer.read()
    body = json.loads(raw) if raw else None
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}", data=data, headers=_headers(token, data is not None), method=method
    )
    hasher = hashlib.sha256()
    total = 0
    started = time.monotonic()
    try:
        response_ctx: Any = urllib.request.urlopen(request, timeout=120)
        status = response_ctx.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        response_ctx = exc
    with response_ctx as response, open(out_path, "wb") as out:
        while chunk := response.read(_STREAM_CHUNK_BYTES):
            out.write(chunk)
            hasher.update(chunk)
            total += len(chunk)
    finished = time.monotonic()
    print(
        json.dumps(
            {
                "status": status,
                "bytes": total,
                "sha256": hasher.hexdigest(),
                "started": started,
                "finished": finished,
            }
        )
    )
    return 0


def cmd_upload(argv: list[str]) -> int:
    """``POST /api/v1/attachments`` with a single-file ``multipart/form-data`` body
    built by hand (stdlib only, matching this script's whole reason for existing):
    ``call``'s JSON body cannot express a file upload, and this is the only place
    the restore proof needs one -- container A's seeded attachment.

    Content comes from stdin so the caller never has to stage a file inside the
    container first; the boundary is a fixed literal since nothing this script sends
    can ever contain it.
    """
    token, filename, content_type = argv[0], argv[1], argv[2]
    content = sys.stdin.buffer.read()
    boundary = "----dtclientuploadboundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    body += content
    body += f"\r\n--{boundary}--\r\n".encode()
    headers = _headers(token, has_body=False)
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    request = urllib.request.Request(
        f"{BASE_URL}/api/v1/attachments", data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            payload = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = exc.read()
    parsed: Any = json.loads(payload) if payload else None
    print(json.dumps({"status": status, "body": parsed}))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(
            "usage: gw_client.py call|seed-comments|download|upload|loop-write ...",
            file=sys.stderr,
        )
        return 2
    command, rest = argv[0], argv[1:]
    if command == "call":
        return cmd_call(rest)
    if command == "seed-comments":
        return cmd_seed_comments(rest)
    if command == "download":
        return cmd_download(rest)
    if command == "upload":
        return cmd_upload(rest)
    if command == "loop-write":
        return cmd_loop_write(rest)
    print(f"unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
