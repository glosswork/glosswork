"""The **image** carries its licence, its notices and its provenance labels.

These clauses are about a built artifact, and the ``Dockerfile`` is not the
artifact. A ``LABEL`` line can be present and still not reach the image (a label set in
the wrong stage does not survive to the final one), and a ``COPY`` can be silently
defeated by ``.dockerignore``. So everything here is read off the built image with
``docker image inspect`` and out of a container with ``docker cp``, never by reading the
``Dockerfile``.

**Nothing here starts a container and nothing contacts a registry.** The files are
pulled out of a container that is created and immediately removed, which is enough to
read a filesystem and avoids booting the application to answer a question about three text
files.

**This file is outside every automated gate**, like the rest of this directory:
``pyproject.toml`` sets ``testpaths = ["tests"]`` and the ``image`` job in
``.github/workflows/ci.yml`` builds the image without running ``container_tests``. That is
also why the CI half of the revision label is pinned separately, in
``tests/test_supply_chain.py``, which runs on every change: the ``image`` job is skipped on
a documentation-only change, and a lost ``--build-arg`` fails nothing at build time.

Run it bare: ``uv run pytest -q container_tests/test_image_notices.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from container_tests import docker_support as ds

REPO_ROOT = Path(__file__).resolve().parents[1]

SOURCE_LABEL = "org.opencontainers.image.source"
REVISION_LABEL = "org.opencontainers.image.revision"
LICENSES_LABEL = "org.opencontainers.image.licenses"

EXPECTED_SOURCE = "https://github.com/glosswork/glosswork"
EXPECTED_LICENSES = "FSL-1.1-ALv2"

#: Verbatim from the upstream files, fetched and digest-checked when this file was
#: written. Three OFL copyright lines and the one MIT copyright line the model's
#: designated licence carries. Byte-for-byte: a reflowed or re-typed notice is a
#: defective notice.
COPYRIGHT_LINES = (
    "Copyright 2022 The Bricolage Grotesque Project Authors "
    "(https://github.com/ateliertriay/bricolage)",
    "Copyright 2022 The Figtree Project Authors (https://github.com/erikdkennedy/figtree)",
    "Copyright 2020 The DM Mono Project Authors (https://www.github.com/googlefonts/dm-mono)",
    "Copyright (c) 2022 staoxiao",
)

#: A line that appears in the OFL body and nowhere else, and one that appears in the MIT
#: body and nowhere else. Four copyright lines with no licence text underneath them would
#: satisfy every other assertion in this file, which is the whole reason these two exist.
OFL_BODY_MARKER = "This Font Software is licensed under the SIL Open Font License, Version 1.1."
MIT_BODY_MARKER = "Permission is hereby granted, free of charge, to any person obtaining a copy"

#: The fetched sources are 93 lines (each OFL file) and 21 lines (the MIT file). A
#: notices file shorter than one OFL body plus one MIT body cannot be reproducing either.
MINIMUM_NOTICES_LINES = 93 + 21


def _labels(image_tag: str) -> dict[str, str]:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image_tag],
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise ds.DockerError(f"docker image inspect {image_tag}: {result.stderr.decode()}")
    labels = json.loads(result.stdout.decode().strip())
    return labels or {}


def _head_revision() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, timeout=30
    )
    return result.stdout.decode().strip()


def _tree_is_clean() -> bool:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"], capture_output=True, timeout=30
    )
    return result.stdout.decode().strip() == ""


def _create_for_inspection(image_tag: str) -> str:
    """``docker create`` with no volume, no network and no client script installed.

    Deliberately not :func:`docker_support.create_container`, which mounts a named volume
    and copies the API client in: both exist for tests that talk to a running
    application, and this one reads three text files out of a filesystem. The container is
    never started.
    """
    result = subprocess.run(
        ["docker", "create", "--network", "none", image_tag],
        capture_output=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise ds.DockerError(f"docker create {image_tag}: {result.stderr.decode()}")
    return result.stdout.decode().strip()


@pytest.fixture(scope="module")
def image_files(image_tag: str) -> dict[str, str]:
    """``/app/LICENSE``, ``/app/THIRD_PARTY_NOTICES.md`` and
    ``/app/THIRD_PARTY_LICENSES.md``, read out of the image.

    ``docker create`` and never ``docker start``: reading three text files does not need
    the application running, and a container that never boots cannot leave a database or
    a log behind. Removed with ``-v`` through the suite's single chokepoint.
    """
    cid = _create_for_inspection(image_tag)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            contents = {}
            for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_LICENSES.md"):
                ds.copy_out(cid, f"/app/{name}", out / name)
                contents[name] = (out / name).read_text()
            return contents
    finally:
        ds.remove_container(cid)


def test_the_image_declares_where_it_came_from(image_tag: str) -> None:
    labels = _labels(image_tag)
    assert labels.get(SOURCE_LABEL) == EXPECTED_SOURCE, (
        f"{SOURCE_LABEL} is {labels.get(SOURCE_LABEL)!r}, expected {EXPECTED_SOURCE!r}. "
        "A published image with no source label gives a stranger holding it no way back "
        "to the repository it was built from."
    )


def test_the_image_declares_its_licence(image_tag: str) -> None:
    labels = _labels(image_tag)
    assert labels.get(LICENSES_LABEL) == EXPECTED_LICENSES, (
        f"{LICENSES_LABEL} is {labels.get(LICENSES_LABEL)!r}, expected "
        f"{EXPECTED_LICENSES!r}, which is a listed SPDX identifier."
    )


def test_the_image_declares_the_revision_it_was_built_from(image_tag: str) -> None:
    """Weak on purpose, and the weakness is worth stating.

    When this suite built the image, the build argument and this assertion both come from
    ``git rev-parse HEAD`` in the same run, so all this can catch is Docker dropping the
    argument. It says nothing about whether the label is *correct*, because nothing here
    is an independent witness to what the image was built from. On a dirty tree it would
    compare a revision the image was not built from, so it skips instead.

    When ``GW_IMAGE`` names an image somebody else built, the revision is not knowable
    from here at all, and the assertion narrows to "something was passed".
    """
    labels = _labels(image_tag)
    revision = labels.get(REVISION_LABEL)
    assert revision, f"{REVISION_LABEL} is missing or empty"
    assert revision != "unknown", (
        f"{REVISION_LABEL} is the Dockerfile's default, so the build passed no "
        "--build-arg GW_REVISION"
    )
    if os.environ.get("GW_IMAGE"):
        pytest.skip(
            "GW_IMAGE names an image this run did not build, so the revision it was "
            "built from is not knowable here; asserted non-empty and not 'unknown' only"
        )
    if not _tree_is_clean():
        pytest.skip(
            "the working tree is dirty, so HEAD is not what the image was built from "
            "and comparing the two would assert something false"
        )
    assert revision == _head_revision()


def test_the_licence_travels_with_the_image(image_files: dict[str, str]) -> None:
    """The FSL's Redistribution clause requires the terms to travel with any copy.

    Compared against the repository's own ``LICENSE``, not merely asserted non-empty: a
    truncated or stale copy inside the image is exactly the failure this is for.
    """
    assert image_files["LICENSE"] == (REPO_ROOT / "LICENSE").read_text()


def test_the_notices_travel_with_the_image(image_files: dict[str, str]) -> None:
    assert (
        image_files["THIRD_PARTY_NOTICES.md"] == (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text()
    )


def test_the_generated_licences_travel_with_the_image(image_files: dict[str, str]) -> None:
    """Every third-party package's licence text, generated from the lockfiles.

    Compared with the repository's copy rather than regenerated here: the repository's
    copy is what ``tests/test_third_party_licenses.py`` holds to the lockfiles, so an
    image carrying exactly that file carries an entry for every package.
    """
    assert (
        image_files["THIRD_PARTY_LICENSES.md"]
        == (REPO_ROOT / "THIRD_PARTY_LICENSES.md").read_text()
    )


def test_the_notices_in_the_image_carry_every_copyright_line(
    image_files: dict[str, str],
) -> None:
    notices = image_files["THIRD_PARTY_NOTICES.md"]
    missing = [line for line in COPYRIGHT_LINES if line not in notices]
    assert missing == [], (
        f"the notices file in the image is missing {len(missing)} copyright line(s) "
        f"byte for byte: {missing}"
    )


def test_the_notices_in_the_image_carry_the_licence_texts(
    image_files: dict[str, str],
) -> None:
    """Copyright lines are not a notice. The licence body has to be there too.

    OFL-1.1 and MIT both require the licence itself to be distributed, not a reference to
    it, so a file listing four holders and nothing else discharges neither obligation
    while passing every other check here.
    """
    notices = image_files["THIRD_PARTY_NOTICES.md"]
    assert OFL_BODY_MARKER in notices, "the OFL-1.1 body is not in the notices file"
    assert MIT_BODY_MARKER in notices, "the MIT body is not in the notices file"
    assert len(notices.splitlines()) >= MINIMUM_NOTICES_LINES, (
        f"the notices file is {len(notices.splitlines())} lines, shorter than one OFL "
        f"body plus one MIT body ({MINIMUM_NOTICES_LINES}), so it cannot be reproducing "
        "both licences in full"
    )
