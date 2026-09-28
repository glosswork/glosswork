"""The bootstrap handoff, proven from outside the container (DD-37, FR-I1, FR-P3).

Deliberately outside ``pyproject.toml``'s ``testpaths``, like its siblings here, so
``uv run pytest -q`` never needs Docker; run it with
``uv run pytest -q container_tests/test_bootstrap_handoff.py``.

**Why this one publishes a port when every other container test refuses to.** The rest
of this directory starts ``--network none`` containers and reaches the application
through the Docker CLI, which is exactly right for proving the image works with no
egress. It would prove essentially nothing here: the handoff exists so that a program
*outside* a fresh container can obtain a working credential over HTTP, without running
anything inside it. A proof that reached in through the container runtime would be
demonstrating the mechanism the handoff replaces.

So this module talks to the container only over ``http://127.0.0.1:<port>``: no command
runs inside the container anywhere in this file, which a grep over it confirms.

``GW_BASE_URL`` is the published origin, which matters twice over. It is what the
handoff's ``mcp_url`` and ``sign_in_url`` are composed from, and it is what the MCP
transport's ``Host`` allowlist is built from -- so an agent that connects to anything
but the address the handoff returned is refused 421 before any credential is read.
This test therefore calls the returned ``mcp_url`` rather than composing its own.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx2
import pytest

from container_tests import docker_support as ds

READY_TIMEOUT_S = 90
HTTP_TIMEOUT_S = 30

# At least 32 characters, or the container refuses to start (and the fail-fast is
# the point: the length floor is checked at boot, not at claim time).
SECRET = "n6QHcLmR2vTkX8pZwFdJbY4sA7eG3uNq"
EMAIL = "admin@container-test.local"
PASSWORD = "ContainerTest1234!"

JSON_RPC_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture
def published(image_tag: str) -> Iterator[tuple[str, str]]:
    """A fresh container with a published loopback port, its own bootstrap secret, and
    ``GW_BASE_URL`` set to the origin it is actually reachable at."""
    port = ds.free_port()
    origin = f"http://127.0.0.1:{port}"
    cid = ds.start_published_container(
        image_tag,
        environment={"GW_BASE_URL": origin, "GW_BOOTSTRAP_SECRET": SECRET},
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


def _claim(origin: str, email: str) -> httpx2.Response:
    return httpx2.post(
        f"{origin}/api/v1/bootstrap",
        json={"secret": SECRET, "email": email, "password": PASSWORD},
        timeout=HTTP_TIMEOUT_S,
    )


def test_a_fresh_container_hands_back_its_first_credential_once(
    published: tuple[str, str],
) -> None:
    """The whole handoff, end to end, against the real image: wait for readiness, claim
    once, be refused the second time, and use the returned token on both surfaces."""
    cid, origin = published
    _wait_until_ready(origin, cid)

    first = _claim(origin, EMAIL)
    assert first.status_code == 201, first.text
    handoff = first.json()
    assert handoff["token"].startswith("gw_pat_")
    assert handoff["token_prefix"] == handoff["token"][:8]
    assert handoff["scope"] == "admin"
    assert handoff["mcp_url"] == f"{origin}/mcp"
    assert handoff["sign_in_url"] == f"{origin}/login"
    assert first.headers["cache-control"] == "no-store"

    second = _claim(origin, "second@container-test.local")
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "bootstrap_claimed"

    credential = {"Authorization": f"Bearer {handoff['token']}"}
    workspace = httpx2.get(f"{origin}/api/v1/workspace", headers=credential, timeout=HTTP_TIMEOUT_S)
    assert workspace.status_code == 200, workspace.text
    assert workspace.headers["content-type"].startswith("application/json")

    # At the address the handoff named, which is the only one the Host allowlist admits.
    tools = httpx2.post(
        handoff["mcp_url"],
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={**JSON_RPC_HEADERS, **credential},
        timeout=HTTP_TIMEOUT_S,
    )
    assert tools.status_code == 200, tools.text
    listed = tools.json()
    assert "result" in listed, listed
    assert "create_object_type" in {tool["name"] for tool in listed["result"]["tools"]}
