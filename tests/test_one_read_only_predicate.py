"""**Exactly one predicate** (DD-38) decides whether a frozen workspace
refuses a write, and it is called from the two gates and nowhere else.

``WorkspaceService.refuse_write_if_read_only`` is DD-16's ``refuse_capability_credential``
in shape: one rule, two adapter seams, no route handler, tool body or service write
method checking the flag. A third caller is how two slightly different
freezes start to exist.

Walked rather than grepped, because comments and docstrings in ``scopes.py``
and ``adapter.py`` name these functions, and a text search counts them.

Not marked ``structural``: it reads source, not a document, and
``tests/test_structural_lane.py`` pins the marked set by equality.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "glosswork"

PREDICATE = "refuse_write_if_read_only"
REST_GATE = "refuse_read_only_write"
SETTING = "read_only"


def _calls(path: Path, name: str) -> int:
    """How many calls in ``path`` name ``name``, as a method (``x.name(...)``) or a bare
    function (``name(...)``). A mention in a comment or docstring is not a call."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == name) or (
            isinstance(func, ast.Name) and func.id == name
        ):
            count += 1
    return count


def _attribute_reads(path: Path, name: str) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return sum(
        1 for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == name
    )


def _where(counter: Callable[[Path, str], int], name: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for path in sorted(SRC.rglob("*.py")):
        count = counter(path, name)
        if count:
            found[str(path.relative_to(SRC))] = count
    return found


def test_the_predicate_is_called_from_exactly_the_two_gates() -> None:
    """**Measured** (fails while nothing calls it). One call in ``scopes.py``, inside
    the REST gate, and one in the MCP adapter's call gate."""
    assert _where(_calls, PREDICATE) == {"scopes.py": 1, "mcp_server/adapter.py": 1}


def test_the_rest_gate_is_called_three_times_all_inside_scopes() -> None:
    """**Measured.** ``require_scope``, ``require_capability``'s plain-credential
    branch, and its ticket branch, which never runs ``enforce_scope``."""
    assert _where(_calls, REST_GATE) == {"scopes.py": 3}


def test_the_flag_is_read_only_by_the_predicate_and_the_startup_log() -> None:
    """**Measured** (fails while nothing reads it). ``config.py`` declares the field
    and ``app.py`` logs the state at startup; the predicate is the only reader that
    enforces anything.

    The subset half is a **fence** on a tree where nothing reads the flag (an empty set is
    a subset of anything); it fails when a read appears in any other module."""
    found = set(_where(_attribute_reads, SETTING))
    assert "services/workspace.py" in found
    assert found <= {"services/workspace.py", "config.py", "app.py"}, found
