"""Read-only mode, proven against the real image (DD-38, FR-P3).

Deliberately outside ``pyproject.toml``'s ``testpaths``, like its siblings here, so
``uv run pytest -q`` never needs Docker; run it with
``uv run pytest -q container_tests -k read_only``.

What this proves that the in-process suites cannot: that ``GW_READ_ONLY`` and
``GW_SUBSCRIBE_URL`` passed as container environment reach the application the image
runs, which is how a hosting operator freezes a hosted workspace (a machine config
update). It reaches the container only over HTTP on a published loopback port, for the
reason ``test_bootstrap_handoff.py`` gives: the program that freezes a workspace is
outside it.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx2
import pytest

from container_tests import docker_support as ds

READY_TIMEOUT_S = 90
HTTP_TIMEOUT_S = 30

SECRET = "r3adOnlyC0ntainerTestSecret00000"
EMAIL = "admin@read-only-test.local"
PASSWORD = "ContainerTest1234!"
SUBSCRIBE_URL = "https://glosswork.example.com/subscribe?workspace=container"


@pytest.fixture
def frozen(image_tag: str) -> Iterator[tuple[str, str]]:
    """A fresh container started read-only, with a subscribe URL and a bootstrap secret."""
    port = ds.free_port()
    origin = f"http://127.0.0.1:{port}"
    cid = ds.start_published_container(
        image_tag,
        environment={
            "GW_BASE_URL": origin,
            "GW_BOOTSTRAP_SECRET": SECRET,
            "GW_READ_ONLY": "true",
            "GW_SUBSCRIBE_URL": SUBSCRIBE_URL,
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


def test_a_read_only_container_refuses_a_write_and_still_exports(
    frozen: tuple[str, str],
) -> None:
    cid, origin = frozen
    _wait_until_ready(origin, cid)

    # Bootstrap is credential-exempt, so it is open while frozen: a hosting operator
    # claims before it could ever freeze.
    claim = httpx2.post(
        f"{origin}/api/v1/bootstrap",
        json={"secret": SECRET, "email": EMAIL, "password": PASSWORD},
        timeout=HTTP_TIMEOUT_S,
    )
    assert claim.status_code == 201, claim.status_code
    credential = {"Authorization": f"Bearer {claim.json()['token']}"}

    refused = httpx2.post(
        f"{origin}/api/v1/object-types",
        json={
            "key": "note",
            "name": "Note",
            "name_plural": "Notes",
            "description": "A free-form note attached to nothing in particular.",
            "key_prefix": "NOTE",
        },
        headers=credential,
        timeout=HTTP_TIMEOUT_S,
    )
    code = refused.json().get("error", {}).get("code")
    # The startup line is how an operator sees the state without making a
    # write. Compared in one value with the refusal, so that neither is measured only after
    # the other has failed.
    logged = '"read_only_mode"' in ds.logs(cid)
    assert (refused.status_code, code, logged) == (409, "workspace_read_only", True)
    error = refused.json()["error"]
    assert error["details"]["subscribe_url"] == SUBSCRIBE_URL
    assert SUBSCRIBE_URL in error["message"]

    # A fence: export works on an image without read-only mode too.
    exported = httpx2.get(
        f"{origin}/api/v1/admin/export", headers=credential, timeout=HTTP_TIMEOUT_S
    )
    assert exported.status_code == 200, exported.status_code
