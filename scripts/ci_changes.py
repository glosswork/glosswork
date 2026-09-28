"""Classify a change as documentation-only or code, for CI's ``changes`` job.

Reads one path per line on standard input (``git diff --no-renames --name-only``) and
prints ``code=false`` when every path is documentation, ``code=true`` otherwise, in the
form ``$GITHUB_OUTPUT`` takes. An empty list is code, so nothing is ever skipped by
accident.

The rule is written once here and once in ``CONTRIBUTING.md`` ("What CI runs");
``tests/test_ci_changes.py`` fails if the two lists differ. Standard library only: the
job runs this with the runner's own ``python3``, before anything is installed.

Pattern forms: ``dir/**`` is anything under ``dir/``; a pattern with ``*`` matches one
path segment per ``*``; anything else is one exact path from the repository root.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from fnmatch import fnmatchcase

DOCUMENTATION = (
    "docs/**",
    "README.md",
    "PRD.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODEOWNERS",
    ".claude/agents/*.md",
    ".github/ISSUE_TEMPLATE/**",
    ".github/pull_request_template.md",
)


def _matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2]) and len(path) > len(pattern) - 2
    if "*" in pattern:
        return path.count("/") == pattern.count("/") and fnmatchcase(path, pattern)
    return path == pattern


def is_documentation(path: str) -> bool:
    """A path git printed plainly, with no ``..`` segment, that a pattern names.

    Git quotes a path with unusual characters (``"docs/caf\\303\\251.md"``); a quoted
    path matches no pattern and so counts as code, which is the safe direction.
    """
    if not path or path.startswith(("/", '"')) or ".." in path.split("/"):
        return False
    return any(_matches(path, pattern) for pattern in DOCUMENTATION)


def is_code(paths: Iterable[str]) -> bool:
    listed = [path for path in paths if path]
    return not listed or not all(is_documentation(path) for path in listed)


def main() -> int:
    paths = [line.rstrip("\n") for line in sys.stdin]
    print(f"code={'true' if is_code(paths) else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
