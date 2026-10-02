"""Structural guards for the documentation set.

Design decisions live in ``docs/DESIGN_DECISIONS.md``, numbered permanently and never in
the PRD; the PRD stays a durable specification rather than a work log; and no citation
points at a PRD section whose number now holds something else.

``test_every_referenced_document_exists`` is the broadest guard here: when two documents
were once removed, roughly forty citations to them were left dangling across this tree,
and nothing in the suite noticed. It reads documentation from disk, which makes this the
third such family alongside ``test_config`` and ``test_agents_guide``.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

# Runs in CI's `guards` job on every pipeline, including ones where the behavioural
# suites are skipped, because a change that touches only documentation can break this.
pytestmark = pytest.mark.structural

REPO_ROOT = Path(__file__).resolve().parents[1]
PRD = REPO_ROOT / "PRD.md"
DESIGN_DECISIONS = REPO_ROOT / "docs" / "DESIGN_DECISIONS.md"

DD_HEADING = re.compile(r"^### DD-(\d+):", re.MULTILINE)
DD_CITATION = re.compile(r"\bDD-(\d+)\b")
WORK_LOG_HEADING = re.compile(r"^### (M\d+[ab]?):", re.MULTILINE)

# A markdown link whose target is a relative path ending in .md, with an optional
# trailing anchor. External links and anchor-only links are skipped by the pattern
# itself. No example is written out here: a literal one would be indistinguishable from
# a real citation, and this test would flag its own comment.
MARKDOWN_LINK = re.compile(r"\]\((?!https?://|#)([^)\s#]+\.md)(?:#[^)\s]*)?\)")

# A backticked path naming one of this project's own documents, e.g. `docs/DATA_MODEL.md`
# or `AGENTS.md`. Deliberately narrow: it matches a path, not prose that mentions a
# document by name.
BACKTICKED_DOC = re.compile(r"`((?:docs/)?[A-Za-z0-9_][A-Za-z0-9_./-]*\.md)`")


def _tracked_files() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=REPO_ROOT
    ).stdout.split()


def test_every_design_decision_is_listed_once_in_ascending_order() -> None:
    """A design decision's number is permanent and never reused. A decision that stops
    being true is removed and leaves a gap rather than handing its number to something
    else, so the census asserts the numbers are unique and ascending from DD-1, not that
    they are consecutive."""
    numbers = [int(n) for n in DD_HEADING.findall(DESIGN_DECISIONS.read_text())]
    assert len(numbers) >= 44, numbers
    assert numbers[0] == 1, numbers
    assert numbers == sorted(set(numbers)), numbers


def test_no_design_decision_heading_is_in_the_prd() -> None:
    assert DD_HEADING.findall(PRD.read_text()) == []


def test_no_work_log_heading_is_in_the_prd() -> None:
    """A delivery checklist, headed ``### M<n>:``, is a work log. Its heading appearing in
    the PRD would mean the PRD had started accumulating one."""
    assert WORK_LOG_HEADING.findall(PRD.read_text()) == []


def test_the_prd_is_the_durable_specification_and_stays_small() -> None:
    """The bound is about shape, not size: what it stops is a **work log**
    re-accumulating in the requirements document, which is what the two tests
    above it look for directly. A genuine new user-facing requirement is what
    this document is for, and may raise the bound -- deliberately, in the change
    that adds the requirement, with the reason recorded here rather than nudged
    up in passing, after the requirement's prose is cut to the fewest lines that still
    read.

    Raised from 619 to 624 for FR-P11, five lines: a workspace that terminates TLS
    itself and admits one client certificate is a new deployment requirement, and the
    document had no line to spare for one.
    """
    assert len(PRD.read_text().splitlines()) < 624


def test_no_citation_points_at_a_prd_section_that_no_longer_exists() -> None:
    """Sections 7 and 8 moved out of the PRD and their numbers now hold other text; 1 to
    6 and 9 to 11 kept theirs, so only a citation naming 7 or 8 points at the wrong
    thing. Nothing is exempt."""
    pattern = re.compile(r"PRD(?:\.md)? section [78]\b")
    offenders = []
    for name in _tracked_files():
        path = REPO_ROOT / name
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if pattern.search(text):
            offenders.append(name)
    assert offenders == [], offenders


def test_every_referenced_document_exists() -> None:
    """Every markdown link and every backticked document path in a tracked file names a
    file that is actually there.

    This is the assertion whose absence once let roughly forty citations rot at once when
    two documents were removed. It is deliberately broad: a
    dead pointer in a docstring costs a reader the same time as a dead pointer in a
    document, so ``src/``, ``tests/`` and ``web/`` are all in scope.
    """
    offenders: list[str] = []
    for name in _tracked_files():
        path = REPO_ROOT / name
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue

        for target in MARKDOWN_LINK.findall(text):
            resolved = (path.parent / target).resolve()
            if not resolved.is_file():
                offenders.append(f"{name}: link to {target}")

        for target in BACKTICKED_DOC.findall(text):
            # Backticked paths are written relative to the repository root by
            # convention, which is what makes them checkable from anywhere.
            if not (REPO_ROOT / target).is_file():
                offenders.append(f"{name}: names {target}")

    assert offenders == [], "\n".join(sorted(set(offenders)))


def test_the_repository_has_the_files_a_remote_expects() -> None:
    """The front door. A contributor or an agent arriving with no context reads these,
    and an agent tool looks for ``AGENTS.md`` by name."""
    for name in (
        "README.md",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODEOWNERS",
        ".github/workflows/ci.yml",
    ):
        assert (REPO_ROOT / name).is_file(), name


def test_every_cold_start_number_in_performance_comes_from_the_committed_report() -> None:
    """The cold-start table and ``perf-data/cold-start.json`` cannot drift apart.

    The other sections of ``docs/PERFORMANCE.md`` are checked by eye against their
    committed reports. This one gets a test because it is the first number in that
    document a later run is meant to compare *itself* against, for a deployment that
    scales to zero, and a table that has drifted from the run that produced it
    is worse than no table: it reads as measured and is not.

    Every duration the section prints, in the form ``<n>.<n> s``, must appear as a value
    somewhere in the report. A number written into the prose by hand fails here.
    """
    report = json.loads((REPO_ROOT / "perf-data" / "cold-start.json").read_text())
    measured = {round(float(value), 6) for value in _numbers(report)}
    section = _performance_section("Cold start (scale to zero)")
    printed = [float(match) for match in re.findall(r"(\d+\.\d+) s\b", section)]
    assert printed, "the cold-start section prints no durations at all"
    assert [value for value in printed if round(value, 6) not in measured] == [], (
        f"printed={sorted(printed)} report={sorted(measured)}"
    )


def _numbers(node: object) -> list[float]:
    if isinstance(node, bool):
        return []
    if isinstance(node, (int, float)):
        return [float(node)]
    if isinstance(node, dict):
        return [value for child in node.values() for value in _numbers(child)]
    if isinstance(node, list):
        return [value for child in node for value in _numbers(child)]
    return []


def _performance_section(heading: str) -> str:
    text = (REPO_ROOT / "docs" / "PERFORMANCE.md").read_text()
    start = text.index(f"## {heading}")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def test_every_cited_design_decision_exists() -> None:
    """A citation of a design decision names a heading that is actually there, so a
    removed or mistyped number cannot point a reader at nothing."""
    known = set(DD_HEADING.findall(DESIGN_DECISIONS.read_text()))
    offenders = []
    for name in _tracked_files():
        try:
            text = (REPO_ROOT / name).read_text()
        except (UnicodeDecodeError, OSError):
            continue
        offenders += [f"{name}: DD-{n}" for n in DD_CITATION.findall(text) if n not in known]
    assert offenders == [], "\n".join(sorted(set(offenders)))


# The numbering the design decisions had before they were public: the hyphenated form,
# the template placeholders, lowercase and joined forms inside names, and the bare word,
# singular or plural. One union, so no form slips past a check written for another.
INTERNAL_DECISION = re.compile(r"\bAD-\d+|\bAD-(?:nn|_+)\b|(?i:(?<![a-z0-9])ad-?\d+)|\bADs?\b(?!-)")


def _undiffable_files() -> set[str]:
    """Files git is told not to diff: the lockfiles and the screenshots. Their hashes
    hold arbitrary hex, where the lowercase form above matches by coincidence, and
    ``git grep -I`` skips them for the same reason."""
    result = subprocess.run(
        ["git", "check-attr", "--stdin", "diff"],
        input="\n".join(_tracked_files()),
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    return {
        line.split(": diff: ")[0]
        for line in result.stdout.splitlines()
        if line.endswith(": diff: unset")
    }


def _frozen_internal_citations() -> int:
    """How many old decision numbers the migrations carry. A migration already on
    ``main`` is never edited (DD-6), so these can never move. The count is read from the
    statements themselves rather than written down here. Migration 12 compares against
    migration 1's seeded bootstrap description, which it reads from migration 1 rather
    than writing it a second time, so its statement carries that one citation too."""
    from glosswork.migrations import MIGRATIONS

    return sum(
        len(INTERNAL_DECISION.findall(statement))
        for migration in MIGRATIONS
        for statement in migration.statements
    )


def test_no_internal_decision_number_is_cited() -> None:
    """The tree cites design decisions by their public numbers only. The one exception
    is text inside migration statements, which cannot change, and it is allowed by count
    in the one file that holds them: a fifth would have to be a deliberate edit here."""
    frozen = _frozen_internal_citations()
    assert frozen == 4, frozen
    skipped = _undiffable_files()
    offenders = []
    for name in _tracked_files():
        if name in skipped:
            continue
        try:
            text = (REPO_ROOT / name).read_text()
        except (UnicodeDecodeError, OSError):
            continue
        found = INTERNAL_DECISION.findall(text)
        allowed = frozen if name == "src/glosswork/migrations.py" else 0
        if len(found) > allowed:
            offenders.append(f"{name}: {len(found)}")
    assert offenders == [], "\n".join(sorted(offenders))


# A change plan is named for its change, and a change's number is its GitHub issue number,
# written as it is (CONTRIBUTING.md, "One change at a time"). The Issue row is the
# template's first table row.
CHANGE_PLAN = re.compile(r"docs/changes/(?P<number>[1-9]\d*)-[a-z0-9][a-z0-9-]*\.md")
ISSUE_ROW = re.compile(r"^\| Issue \|[^|\n]*?#(\d+)\b", re.MULTILINE)


def test_every_change_plan_is_numbered_by_its_issue() -> None:
    """Every file under ``docs/changes/`` other than its README is named ``<N>-<slug>.md``
    with ``N`` unpadded, and its table's Issue row names ``#N``. It compares a plan's name
    with its own Issue row rather than with a count, so it holds whatever number the
    repository's issues have reached."""
    offenders = []
    for name in _tracked_files():
        if not name.startswith("docs/changes/") or name == "docs/changes/README.md":
            continue
        match = CHANGE_PLAN.fullmatch(name)
        if match is None:
            offenders.append(f"{name}: not <N>-<slug>.md")
            continue
        issue = ISSUE_ROW.search((REPO_ROOT / name).read_text())
        if issue is None or issue.group(1) != match["number"]:
            offenders.append(f"{name}: its Issue row does not name #{match['number']}")
    assert offenders == [], "\n".join(offenders)
