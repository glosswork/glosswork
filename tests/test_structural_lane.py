"""The structural lane is complete, and CI actually runs it.

CI skips the behavioural suites on a pull request that touches no code (see
``.github/workflows/ci.yml`` and ``scripts/ci_changes.py``). That is only safe while two
things hold: every test whose input is a document in this repository carries the
``structural`` marker, and the ``guards`` job runs that marker on every run. This file
asserts both, because the failure mode is silent -- a new documentation test that nobody
marked simply stops being run on the changes most likely to break it.

Grep-backed, in the pattern of ``tests/test_one_principal_resolver.py`` and the
frontend's ``hidingIsNeverTheOnlySignal.test.ts``. Two proxies for "reads a document
from disk": a path built to a ``.md`` file, and a module that searches the whole tree
(``str(REPO_ROOT)`` handed to a tool, or ``git ls-files``), which reads every document
without naming one. This module excludes itself because its own source necessarily
contains the patterns it searches for.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.structural

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS = REPO_ROOT / "tests"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

#: ``REPO_ROOT / "docs" / "MCP_TOOLS.md"`` and friends. Narrow on purpose: it matches a
#: path being built, not prose in a docstring that names a document.
DOC_PATH = re.compile(r'/\s*"[^"]+\.md"')

#: A module that greps or lists the whole repository. It names no document, so
#: ``DOC_PATH`` cannot see it, and it reads every one of them.
WHOLE_TREE = re.compile(r'str\(REPO_ROOT\)|"ls-files"')

#: This module searches for the pattern, so its own source always matches it.
SELF = "test_structural_lane.py"

#: Pinned, so adding one is a deliberate act rather than a side effect. Keep it in step
#: with what carries ``pytestmark = pytest.mark.structural``.
MARKED = {
    "test_agents_guide.py",
    "test_approval_message.py",
    "test_ci_changes.py",
    "test_ci_workflow.py",
    "test_display_vocabulary.py",
    "test_documentation_structure.py",
    "test_migrations_are_forward_only.py",
    "test_relay_definition.py",
    "test_release_workflow.py",
    "test_retired_names.py",
    "test_structural_lane.py",
    "test_supply_chain.py",
    "test_third_party_licenses.py",
}


def _test_modules() -> list[Path]:
    return sorted(p for p in TESTS.glob("test_*.py"))


def _is_marked(source: str) -> bool:
    return "pytestmark = pytest.mark.structural" in source


def test_every_test_that_reads_a_document_is_in_the_structural_lane() -> None:
    offenders = [
        path.name
        for path in _test_modules()
        if path.name != SELF
        and DOC_PATH.search(source := path.read_text())
        and not _is_marked(source)
    ]
    assert offenders == [], (
        "these read a document from disk but are not marked `structural`, so CI's "
        f"guards job will not run them on a documentation-only change: {offenders}"
    )


def test_every_test_that_reads_the_whole_tree_is_in_the_structural_lane() -> None:
    offenders = [
        path.name
        for path in _test_modules()
        if path.name != SELF
        and WHOLE_TREE.search(source := path.read_text())
        and not _is_marked(source)
    ]
    assert offenders == [], (
        "these search the whole repository, documents included, but are not marked "
        f"`structural`, so a documentation-only change skips them: {offenders}"
    )


def test_the_marked_set_is_pinned() -> None:
    marked = {path.name for path in _test_modules() if _is_marked(path.read_text())}
    assert marked == MARKED


def test_ci_runs_the_structural_lane_on_every_pipeline() -> None:
    """The marker is worthless if the job that selects it stops running.

    ``guards`` deliberately carries no ``if`` and no ``needs``, so it runs on every pull
    request and every push to ``main``. If either appears, the lane can be skipped on
    exactly the changes it exists for.
    """
    workflow: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text())
    guards = workflow["jobs"]["guards"]
    assert "if" not in guards, "guards must run on every change; it has an `if` now"
    assert "needs" not in guards, "guards must run on every change; it has `needs` now"
    runs = [step.get("run", "") for step in guards["steps"]]
    assert "uv run pytest -q -m structural" in runs
