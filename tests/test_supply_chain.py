"""``uv.lock`` resolves from the public index, and stays that way.

Every package in this lockfile is published on PyPI and every artifact URL in it is
already on ``files.pythonhosted.org``. The ``source = { registry = ... }`` field records
where resolution *happened*, and for 70 packages it recorded a private index that answers
``401`` to anybody but its owner. Nothing ever failed, because a private index that
proxies PyPI resolves fine for whoever can reach it, and because every CI job installs
with ``uv sync --frozen``, which reads the recorded artifact URLs and never contacts an
index at all.

That is the whole problem with this field: **no ordinary command can see it.**
``uv lock --check`` exits 0 with the registry pointed at a host that does not exist.
``uv sync --frozen`` installs all 71 packages from the same lockfile. A green pipeline
says nothing. So this file is the only thing that reads it, and it runs in the
``structural`` lane, which CI runs on every pipeline.

It is not a formality. A ``uv lock``, ``uv add`` or ``uv lock --upgrade-package`` run on a
machine whose user-level ``uv`` configuration names a private index rewrites **every**
registry line in the file, not only the package being changed, and no project-level
setting, environment variable or command-line flag overrides that. The ``[[tool.uv.index]]``
pin in ``pyproject.toml`` governs every machine that has no such configuration, which is
not the maintainer's. So this guard is expected to fire, and :data:`REMEDY` is the fix.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.structural

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCKFILE = REPO_ROOT / "uv.lock"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: What the ``image`` job must pass so the image records the commit it came from.
REVISION_BUILD_ARG = "--build-arg GW_REVISION=${{ github.sha }}"

#: The only index this project resolves from.
PUBLIC_INDEX = "https://pypi.org/simple"

#: The only hosts allowed to appear anywhere in the lockfile: the index it resolves
#: from, and the CDN the artifacts are served by.
ALLOWED_HOSTS = {"pypi.org", "files.pythonhosted.org"}

#: Written generically on purpose: it repairs whatever private index was written in,
#: and it names none, so this file carries no third party's registry URL.
REMEDY = (
    "Re-run the substitution from the repository root and commit the result:\n"
    '    sed -i \'\' -E \'s|registry = "[^"]*"|registry = "' + PUBLIC_INDEX + "\"|g' uv.lock\n"
    "then confirm `git diff --text uv.lock` changes nothing but `source = { registry` lines\n"
    "(`--text` is required: .gitattributes marks this file `-diff`, so a plain diff calls it\n"
    "binary and any grep over it finds nothing and reads as a pass)."
)

HOST = re.compile(r'https?://([^/"]+)')


def _lock() -> dict[str, object]:
    data: dict[str, object] = tomllib.loads(LOCKFILE.read_text())
    return data


def _packages() -> list[dict[str, object]]:
    packages = _lock()["package"]
    assert isinstance(packages, list)
    return [package for package in packages if isinstance(package, dict)]


def _source(package: dict[str, object]) -> dict[str, object]:
    source = package.get("source", {})
    return source if isinstance(source, dict) else {}


def test_every_locked_package_resolves_from_the_public_index() -> None:
    offenders = sorted(
        {
            str(_source(package)["registry"])
            for package in _packages()
            if "registry" in _source(package) and _source(package)["registry"] != PUBLIC_INDEX
        }
    )
    assert offenders == [], (
        f"uv.lock resolves from an index that is not {PUBLIC_INDEX}: {offenders}. "
        "A public contributor cannot reach a private index, and a `uv lock` on a machine "
        "that has one configured rewrites every registry line at once.\n" + REMEDY
    )


def test_the_lockfile_names_no_host_but_the_index_and_its_cdn() -> None:
    """Wider than the check above, and it catches what that one cannot.

    Renaming the key (``index =`` rather than ``registry =``) or moving a private host
    into an artifact URL both leave the assertion above vacuously true. This one reads
    every URL in the file, whatever key it sits under.
    """
    hosts = {host for host in HOST.findall(LOCKFILE.read_text())}
    unexpected = sorted(hosts - ALLOWED_HOSTS)
    assert unexpected == [], (
        f"uv.lock names hosts it should not: {unexpected}. Every package this project "
        f"locks is published on PyPI and served from files.pythonhosted.org.\n" + REMEDY
    )


def test_every_registry_package_is_accounted_for() -> None:
    """The count comes from the run, so the check above cannot pass on an empty set.

    A lockfile with every ``source = { registry = ... }`` line deleted satisfies "every
    registry value is the public index" vacuously. Each package resolves from somewhere,
    and the only package here with a non-registry source is ``glosswork`` itself, which
    is ``source = { editable = "." }``.
    """
    packages = _packages()
    with_registry = [package for package in packages if "registry" in _source(package)]
    not_editable = [package for package in packages if "editable" not in _source(package)]
    missing = sorted(
        str(package.get("name", "?"))
        for package in not_editable
        if "registry" not in _source(package)
    )
    assert len(with_registry) == len(not_editable), (
        f"{len(with_registry)} of {len(not_editable)} non-editable packages record a "
        f"registry source; these record none: {missing}. A package with no registry "
        "source is invisible to the check that every registry is the public index.\n" + REMEDY
    )


def test_the_image_build_labels_the_commit_it_came_from() -> None:
    """The image job's ``docker build`` passes the commit as ``GW_REVISION``.

    The ``image`` job in ``.github/workflows/ci.yml`` is skipped on a documentation-only
    change, and publishing will build on it. Dropping the flag fails nothing at
    build time: the image would carry ``org.opencontainers.image.revision`` reading
    ``unknown``, with nothing else on it naming the source commit. This lane runs on every
    change, so the flag is pinned here.

    This lives here rather than in ``container_tests/`` because that directory is outside
    every automated gate (``testpaths = ["tests"]``).

    It reads the ``docker build`` command itself and not the job's whole text, because the
    job carries a comment naming the flag and a substring search over the block would pass
    on a command that had lost it.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text())
    runs = [step.get("run", "") for step in workflow["jobs"]["image"]["steps"]]
    commands = [
        line.strip()
        for run in runs
        for line in run.splitlines()
        if line.strip().startswith("docker build")
    ]
    assert commands, "the image job runs no `docker build` command"
    offenders = [line for line in commands if REVISION_BUILD_ARG not in line]
    assert offenders == [], (
        f"an image job `docker build` command does not pass {REVISION_BUILD_ARG}, so "
        f"the image it builds records no revision: {offenders}. The Dockerfile's "
        "ARG GW_REVISION defaults to 'unknown' rather than guessing, so this fails "
        "silently in the image rather than in the build."
    )
