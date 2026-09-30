"""**Exactly one place** in ``src/`` counts a tool
call, so the two allowlists that decide what may be written cannot be applied in one call
site and forgotten in another.

Written in the shape of ``tests/test_one_agent_label_resolver.py``: an AST walk finds a
*call*, not a mention in a comment or a docstring.

**This is a fence.** It guards a shape the code establishes rather than one a change alters,
so it is not counted as coverage. It was watched failing against a deliberately added
second call site, which is the only thing that makes a fence worth having.

There is a real temptation this exists to refuse. A refused token raises ``MCPError`` out
of ``McpAdapter._dispatch`` and returns nothing, so the only way to count it is a second
call site inside an ``except`` clause. The code does not do that, on purpose: a call
that never resolved a caller cannot be attributed, and counting it would put work on the
one path a credential-stuffing loop reaches without a valid credential. DD-39 records the
consequence, which is that a workspace whose agents all present bad tokens reports zero
tool calls and looks idle.

Not marked ``structural``: it reads source, not a document, and
``tests/test_structural_lane.py`` pins the marked set by equality.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "glosswork"

RECORD_METHOD = "record_tool_call"
HEADER_NAME = "OPERATOR_TOKEN_HEADER"
COMPARISON_FUNCTION = "compare_digest"


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _calls_named(path: Path, attribute: str) -> list[str]:
    tree = ast.parse(_text(path), filename=str(path))
    return [
        ast.dump(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
    ]


def test_a_tool_call_is_counted_from_exactly_one_place_in_src() -> None:
    callers = {
        str(path.relative_to(REPO_ROOT)): calls
        for path in _modules()
        if (calls := _calls_named(path, RECORD_METHOD))
    }
    total = sum(len(calls) for calls in callers.values())
    assert total == 1, (
        "usage.record_tool_call must be called from exactly one place in src/: the MCP "
        "seam. A second call site is how the tool-name allowlist comes to be applied in "
        f"one place and forgotten in the other. Call sites found: {callers}"
    )
    assert set(callers) == {"src/glosswork/mcp_server/adapter.py"}, callers


def test_the_operator_token_header_name_is_defined_exactly_once() -> None:
    definition = re.compile(rf"^{HEADER_NAME}\s*=", re.MULTILINE)
    definitions = [
        str(path.relative_to(REPO_ROOT)) for path in _modules() if definition.search(_text(path))
    ]
    assert definitions == ["src/glosswork/services/usage.py"], definitions


def test_every_constant_time_comparison_in_the_product_is_named() -> None:
    """One reader of the operator credential, the way
    ``refuse_capability_credential`` is the one reader of a capability (DD-16).

    Pinned as a set of modules rather than of calls, because three other credentials in
    this product are already compared with the same primitive and a test that counted
    calls would say nothing about which credential each one is. What this asserts is that
    the operator token is compared in ``services/usage.py`` and that no *new* module has
    quietly started comparing a secret. Widening the set is a visible edit here.
    """
    comparing = {
        str(path.relative_to(REPO_ROOT))
        for path in _modules()
        if _calls_named(path, COMPARISON_FUNCTION)
    }
    assert comparing == {
        "src/glosswork/routes/auth.py",
        "src/glosswork/services/bootstrap.py",
        "src/glosswork/services/sessions.py",
        # Change 9: an emailed sign-in code against each live code's stored hash.
        "src/glosswork/services/sign_in_codes.py",
        "src/glosswork/services/usage.py",
    }, comparing


def test_no_module_outside_the_usage_service_reads_the_configured_operator_token() -> None:
    """The setting has one reader. A route or a middleware that reached for it would be
    a second place the refusal could be got wrong, and the refusal is the whole endpoint.

    ``\\b`` on both sides, so the error **code** ``operator_token_refused`` -- which
    ``errors.py`` must of course contain -- is not mistaken for a read of the setting.
    """
    reads = re.compile(r"\boperator_token\b")
    readers = [
        str(path.relative_to(REPO_ROOT))
        for path in _modules()
        if reads.search(_text(path)) and path.name not in ("usage.py", "config.py")
    ]
    assert readers == [], readers
