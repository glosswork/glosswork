"""CI's documentation-only rule is written once in prose and once in code, and they agree.

``scripts/ci_changes.py`` decides whether a pull request can skip the backend, frontend,
end-to-end and image jobs. ``CONTRIBUTING.md`` ("What CI runs") is where a person reads
the same rule. If the two lists drift apart, either a contributor is told a change is
cheap when CI runs everything, or, worse, CI skips the behavioural suites on a change the
document says is code. So the lists are compared here, and the classifications that
settled where the boundary falls are pinned as cases.

The script is standard library only, because the ``changes`` job runs it with the
runner's own ``python3`` before anything is installed. It is loaded here by path for the
same reason: ``scripts/`` is not a package.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.structural

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ci_changes.py"
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"

SECTION = "## What CI runs"


def _classifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_changes", SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _documented_patterns() -> list[str]:
    """The backticked pattern opening each bullet of the section's first list."""
    text = CONTRIBUTING.read_text()
    assert SECTION in text, f"CONTRIBUTING.md has no {SECTION!r} section"
    section = text[text.index(SECTION) + len(SECTION) :]
    section = section.split("\n## ", 1)[0]
    patterns: list[str] = []
    in_list = False
    for line in section.splitlines():
        match = re.match(r"^- `([^`]+)`", line)
        if match:
            patterns.append(match.group(1))
            in_list = True
        elif in_list and not line.startswith("  "):
            break
    return patterns


def test_contributing_and_the_classifier_name_the_same_documentation_paths() -> None:
    documented = _documented_patterns()
    assert documented, "the 'What CI runs' section lists no documentation paths"
    assert tuple(documented) == _classifier().DOCUMENTATION


@pytest.mark.parametrize(
    "paths",
    [
        ["README.md"],
        ["docs/DESIGN_DECISIONS.md"],
        ["docs/design/anything.png"],
        ["PRD.md", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md", "CODEOWNERS"],
        [".claude/agents/impl.md"],
        [".github/ISSUE_TEMPLATE/change.md"],
        [".github/pull_request_template.md"],
    ],
)
def test_documentation_only(paths: list[str]) -> None:
    assert _classifier().is_code(paths) is False


@pytest.mark.parametrize(
    "paths",
    [
        # An empty diff is code, so nothing is ever skipped by accident.
        [],
        # The image ships these three (container_tests/test_image_notices.py). The third
        # is a fence, not coverage: a root-level markdown file is already code by rule.
        ["LICENSE"],
        ["THIRD_PARTY_NOTICES.md"],
        ["THIRD_PARTY_LICENSES.md"],
        # Markdown is not documentation by extension, only by place.
        ["web/src/brand/README.md"],
        ["src/glosswork/README.md"],
        ["docs/../src/glosswork/app.py"],
        ["perf-data/cold-start.json"],
        # The files that define CI are code.
        [".github/workflows/ci.yml"],
        [".github/ISSUE_TEMPLATE"],
        [".claude/agents/nested/brief.md"],
        [".claude/settings.json"],
        ["scripts/ci_changes.py"],
        ["README.md", "src/glosswork/app.py"],
        # A root-only name one level down is not the root file.
        ["docs2/README.md"],
        ["web/README.md"],
        # A code file moved into docs/ lists both sides under --no-renames.
        ["docs/fetch_model.py", "scripts/fetch_model.py"],
        # Git quotes a path with unusual characters; a quoted path is code, which is safe.
        ['"docs/caf\\303\\251.md"'],
    ],
)
def test_code(paths: list[str]) -> None:
    assert _classifier().is_code(paths) is True


@pytest.mark.parametrize(
    ("stdin", "expected"),
    [
        ("README.md\ndocs/DESIGN_DECISIONS.md\n", "code=false\n"),
        ("README.md\nsrc/glosswork/app.py\n", "code=true\n"),
        ("", "code=true\n"),
        ("\n\n", "code=true\n"),
    ],
)
def test_the_script_reads_paths_and_prints_one_output_line(stdin: str, expected: str) -> None:
    """What the ``changes`` job appends to ``$GITHUB_OUTPUT``, read off a real run."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], input=stdin, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected
