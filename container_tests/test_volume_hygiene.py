"""The container suite strands no anonymous volume.

The image declares ``VOLUME ["/data"]``, so every ``docker run`` that names no volume
gets a fresh anonymous one. ``docker rm -f`` removes the container and leaves that
volume behind; ``docker rm -f -v`` removes both. With the first form at its single
removal chokepoint, one run of this suite strands one volume per container it starts.
Measured on the maintainer's machine on 2026-09-19: 214 of 216 volumes were dangling
anonymous ones, 21 of them created on the two days this suite had last run, and
Docker's disk had already filled once.

**This file is outside every automated gate**. ``pyproject.toml`` sets
``testpaths = ["tests"]``, so ``uv run pytest -q`` never collects it, and
``.github/workflows/ci.yml`` says in as many words that ``container_tests`` stays a local
gate. A later edit dropping ``-v`` would fail nothing in CI. This is a
check a person runs, not a guard, and saying so here is the point of saying it at all.

Run it bare: ``uv run pytest -q container_tests/test_volume_hygiene.py``.

``docker volume ls`` is global. If this test fails, establish whether
anything else on the machine touched Docker during the window before attributing the
difference to the suite.
"""

from __future__ import annotations

import subprocess

from container_tests import docker_support as ds


def _volume_ids() -> list[str]:
    """``docker volume ls -q``, sorted, as a list.

    Shelled out rather than read through ``docker_support``, because what this test
    proves is a property of the machine after the suite's own helpers have run, and
    reading it through those helpers would make the proof depend on the thing under
    test.
    """
    result = subprocess.run(
        ["docker", "volume", "ls", "-q"], capture_output=True, timeout=30, check=True
    )
    return sorted(line for line in result.stdout.decode().splitlines() if line.strip())


def test_a_container_lifecycle_strands_no_volume(image_tag: str) -> None:
    """One container's whole lifecycle leaves ``docker volume ls -q`` identical.

    The assertion is list equality rather than a count, so a run that strands one
    volume and removes an unrelated one in the same window still fails.
    """
    before = _volume_ids()
    cid = ds.start_container(image_tag, name_prefix="gw-volume-hygiene")
    try:
        started = _volume_ids()
        # The container is up, so the anonymous volume for /data exists. Asserting
        # that first is what keeps the real assertion from passing vacuously on an
        # image that stopped declaring VOLUME at all.
        assert len(started) == len(before) + 1, (
            "the image no longer strands an anonymous volume while running, so the "
            "removal below cannot prove anything: check the Dockerfile's VOLUME line"
        )
    finally:
        ds.remove_container(cid)

    after = _volume_ids()
    assert after == before, (
        f"the container lifecycle changed the machine's volume list: "
        f"added {sorted(set(after) - set(before))}, "
        f"removed {sorted(set(before) - set(after))}"
    )
