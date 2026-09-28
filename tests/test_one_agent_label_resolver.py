"""**Exactly one function** resolves an agent label, so REST and MCP cannot grow two
slightly different precedence rules that quietly drift apart.

Written in the shape of ``tests/test_one_principal_resolver.py``: an AST walk finds a
*call*, not a mention in a comment or a docstring, and the header name's one
definition is found the same way ``test_the_resolver_is_defined_exactly_once`` finds
``resolve_principal_ref``'s.

Not marked ``structural``: it reads source, not a document, and
``tests/test_structural_lane.py`` pins the marked set by equality.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "glosswork"

REGISTER_USE_METHOD = "register_use"
HEADER_NAME = "AGENT_LABEL_HEADER"


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _register_use_calls(path: Path) -> list[str]:
    """Every *call* whose attribute name is ``register_use`` (``<x>.register_use(...)``
    for any ``<x>``, since the caller may reach it as ``services.agent_labels`` or as
    the label service directly). A mention inside a comment or a docstring is not an
    ``ast.Call`` node, so it cannot appear here -- unlike a plain-text grep, which is
    why a grep counts a comment alongside the one real call site."""
    tree = ast.parse(_text(path), filename=str(path))
    return [
        ast.dump(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == REGISTER_USE_METHOD
    ]


def test_register_use_is_called_from_exactly_one_place_in_src() -> None:
    callers = {
        str(path.relative_to(REPO_ROOT)): calls
        for path in _modules()
        if (calls := _register_use_calls(path))
    }
    total_calls = sum(len(calls) for calls in callers.values())
    assert total_calls == 1, (
        "agent_labels.register_use must be called from exactly one place in src/, so "
        "REST and MCP resolve a label through one shared function rather than two "
        f"that can drift apart. Call sites found: {callers}"
    )


def test_agent_label_header_name_is_defined_exactly_once() -> None:
    definition = re.compile(rf"^{HEADER_NAME}\s*=", re.MULTILINE)
    definitions = [
        str(path.relative_to(REPO_ROOT)) for path in _modules() if definition.search(_text(path))
    ]
    assert len(definitions) == 1, (
        f"{HEADER_NAME} must be defined exactly once, so the header's spelling cannot "
        f"drift between the two surfaces that read it. Definitions found: {definitions}"
    )
