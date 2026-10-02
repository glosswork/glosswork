"""The operator usage endpoint, proven against the real image (DD-39, FR-P10).

Deliberately outside ``pyproject.toml``'s ``testpaths``, like its siblings here, so
``uv run pytest -q`` never needs Docker; run it with
``uv run pytest -q container_tests/test_operator_usage.py``.

**Why this one publishes a port**, for the same reason ``test_bootstrap_handoff.py``
does and the rest of this directory does not: the whole point of this endpoint is that a
program *outside* the container reads it over HTTP. A proof that reached in through
``docker exec`` would be demonstrating a mechanism the endpoint does not use. No command
runs inside the container anywhere in this file.

**What only a container can prove here.** Two things, and neither is reachable from a
``TestClient``.

1. A real ``SIGTERM``. The counter holds counts in memory and flushes on an interval, so
   "a stop loses nothing" is a claim about what a container does when the platform stops
   it. Every container drains cleanly on a stop and this rides on it: the test makes
   tool calls, stops the container inside its grace period, starts it again on the same
   filesystem, and reads the same numbers back.
2. That the endpoint answers at all on the built image, with the variable passed the way
   an operator passes it, rather than only against an application object in a test
   process.

**The operator backup** (``-k operator_backup``, 2 passed) is here for the second reason
and one more: its consumer is a scheduled program outside the container, reading a
streamed tar over a real socket, and ``TestClient`` and uvicorn differ on streaming. With
``GW_OPERATOR_BACKUP=true`` the operator credential downloads the artifact; without the
variable the same request is refused.
"""

from __future__ import annotations

import io
import tarfile
import time
from collections.abc import Iterator

import httpx2
import pytest

from container_tests import docker_support as ds

READY_TIMEOUT_S = 90
HTTP_TIMEOUT_S = 30

# Five seconds, not ten, because five is the grace the claim is about: it is the shortest
# default grace a hosting platform gives (docs/DEPLOYMENT.md section 2a), the grace the
# clean shutdown is built to fit, and DD-39 says the flush fits inside it. Asserting
# ``elapsed < 10`` against a ten-second stop proved only that the drain finished within
# a grace nothing runs on. Measured at 0.57 s.
STOP_GRACE_S = 5

# At least 32 characters, or the container refuses to start. Obviously a fixture rather
# than a random-looking blob, so it does not trip a secret scanner.
OPERATOR_TOKEN = "operator-token-for-container-tst"
BOOTSTRAP_SECRET = "bootstrap-secret-for-container-t"
EMAIL = "admin@container-test.local"
PASSWORD = "ContainerTest1234!"

JSON_RPC_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture
def metered(image_tag: str) -> Iterator[tuple[str, str]]:
    """A container with an operator token and a bootstrap secret, reachable on loopback."""
    port = ds.free_port()
    origin = f"http://127.0.0.1:{port}"
    cid = ds.start_published_container(
        image_tag,
        environment={
            "GW_BASE_URL": origin,
            "GW_BOOTSTRAP_SECRET": BOOTSTRAP_SECRET,
            "GW_OPERATOR_TOKEN": OPERATOR_TOKEN,
        },
        port=port,
    )
    try:
        yield cid, origin
    finally:
        ds.remove_container(cid)


def _wait_until_ready(origin: str, cid: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            response = httpx2.get(f"{origin}/readyz", timeout=5)
        except httpx2.HTTPError:
            response = None
        if response is not None and response.status_code == 200:
            return
        time.sleep(1)
    raise AssertionError(
        f"{origin}/readyz did not answer 200 within {READY_TIMEOUT_S}s. "
        f"Container logs:\n{ds.logs(cid)}"
    )


def _usage(origin: str, token: str | None = OPERATOR_TOKEN) -> httpx2.Response:
    headers = {} if token is None else {"X-Operator-Token": token}
    return httpx2.get(f"{origin}/api/v1/usage", headers=headers, timeout=HTTP_TIMEOUT_S)


def _call_tool(origin: str, mcp_url: str, token: str, name: str, arguments: dict) -> None:
    response = httpx2.post(
        mcp_url,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={**JSON_RPC_HEADERS, "Authorization": f"Bearer {token}"},
        timeout=HTTP_TIMEOUT_S,
    )
    assert response.status_code == 200, response.text


def test_an_operator_reads_counts_over_http_and_a_stop_loses_none_of_them(
    metered: tuple[str, str],
) -> None:
    cid, origin = metered
    _wait_until_ready(origin, cid)

    claim = httpx2.post(
        f"{origin}/api/v1/bootstrap",
        json={"secret": BOOTSTRAP_SECRET, "email": EMAIL, "password": PASSWORD},
        timeout=HTTP_TIMEOUT_S,
    )
    assert claim.status_code == 201, claim.text
    handoff = claim.json()
    token = handoff["token"]

    # The tenant's own highest credential is refused, over HTTP, on the built image.
    refused = httpx2.get(
        f"{origin}/api/v1/usage",
        headers={"Authorization": f"Bearer {token}"},
        timeout=HTTP_TIMEOUT_S,
    )
    assert refused.status_code == 401, refused.text
    assert refused.json()["error"]["code"] == "operator_token_refused"

    # A wrong operator token and no operator token get the same answer, byte for byte.
    wrong = _usage(origin, "operator-token-for-container-XXX")
    absent = _usage(origin, None)
    assert wrong.status_code == absent.status_code == 401
    assert wrong.content == absent.content == refused.content

    opened = _usage(origin)
    assert opened.status_code == 200, opened.text
    assert opened.headers["cache-control"] == "no-store"
    before = opened.json()
    assert before["since"].endswith("Z")

    # Three tool calls over the real MCP transport, at the address the handoff named.
    for _ in range(3):
        _call_tool(origin, handoff["mcp_url"], token, "list_object_types", {})
    _call_tool(origin, handoff["mcp_url"], token, "no_such_tool_at_all", {})

    counted = _usage(origin).json()["tool_calls"]
    by_pair = {(row["tool"], row["error_code"]): row["count"] for row in counted}
    assert by_pair[("list_object_types", None)] == 3, counted
    assert by_pair[("unknown_tool", "unknown")] == 1, counted
    assert "no_such_tool_at_all" not in _usage(origin).text

    # The stop, well inside the grace period, then the same filesystem again. The flush
    # interval is thirty seconds and nothing here waits that long, so anything that
    # survives survived because the drain wrote it.
    elapsed = ds.stop_container(cid, grace=STOP_GRACE_S)
    assert elapsed < STOP_GRACE_S, f"the stop took {elapsed:.1f}s of its {STOP_GRACE_S}s grace"
    ds.start_stopped_container(cid)
    _wait_until_ready(origin, cid)

    after = _usage(origin).json()
    assert after["tool_calls"] == counted, (counted, after["tool_calls"])
    assert after["since"] == before["since"]


# ------------------------------------------------------------- the operator backup


def _container(image_tag: str, environment: dict[str, str]) -> tuple[str, str]:
    port = ds.free_port()
    origin = f"http://127.0.0.1:{port}"
    cid = ds.start_published_container(
        image_tag,
        environment={"GW_BASE_URL": origin, "GW_OPERATOR_TOKEN": OPERATOR_TOKEN, **environment},
        port=port,
    )
    return cid, origin


def _operator_backup(origin: str, token: str = OPERATOR_TOKEN) -> httpx2.Response:
    return httpx2.post(
        f"{origin}/api/v1/operator/backup",
        headers={"X-Operator-Token": token},
        timeout=HTTP_TIMEOUT_S,
    )


def test_operator_backup_downloads_the_artifact_when_the_deployment_opted_in(
    image_tag: str,
) -> None:
    cid, origin = _container(image_tag, {"GW_OPERATOR_BACKUP": "true"})
    try:
        _wait_until_ready(origin, cid)
        response = _operator_backup(origin)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/x-tar"
        assert response.headers["cache-control"] == "no-store"
        with tarfile.open(fileobj=io.BytesIO(response.content), mode="r|") as tar:
            names = [member.name for member in tar]
        assert names[0] == "glosswork.sqlite3", names

        # A wrong credential on the same container gets the one refusal and no tar.
        wrong = _operator_backup(origin, "operator-token-for-container-XXX")
        assert wrong.status_code == 401, wrong.text
        assert wrong.json()["error"]["code"] == "operator_token_refused"
    finally:
        ds.remove_container(cid)


def test_operator_backup_is_refused_when_the_variable_is_not_set(image_tag: str) -> None:
    cid, origin = _container(image_tag, {})
    try:
        _wait_until_ready(origin, cid)
        refused = _operator_backup(origin)
        assert refused.status_code == 401, refused.text
        assert refused.json()["error"]["code"] == "operator_token_refused"
        # The positive control: the same credential still opens what it always opened.
        assert _usage(origin).status_code == 200
    finally:
        ds.remove_container(cid)
