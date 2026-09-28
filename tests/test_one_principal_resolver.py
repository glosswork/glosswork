"""**Exactly one function** turns a ``user_ref`` reference into a principal id, and it
has exactly three callers.

Written in the style of the access-control and attachment meta-tests, and for the same
reason:
the failure mode is not "someone forgets once" but "a second write path grows its own
slightly different check and nobody notices". A funnel with two ways around it is not
one, which is the argument ``_check_attachment_refs`` already makes in
``services/records.py``.

The specific decay this guards against is an earlier shape. ``principal_exists`` was
called straight from ``RecordService._validate_values``, which is why ``@me`` worked in a
filter and not on a write, why an email was never accepted anywhere, and why
``allow_service_accounts`` was declared and unread for a long time. Re-acquiring that
habit is one line, and this test is what makes it a failing line.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "glosswork"

RESOLVER_MODULE = SRC / "services" / "principals.py"
RESOLVER_NAME = "resolve_principal_ref"

# Where ``principal_exists`` may legitimately appear: its definition, its interface
# declaration, and the resolver module that calls it. Listed rather than pattern-matched,
# so widening the set is an edit a reviewer sees.
PRINCIPAL_EXISTS_ALLOWED = {
    SRC / "repositories" / "sqlite.py",
    SRC / "repositories" / "interfaces.py",
    RESOLVER_MODULE,
}

# Section 2 names three callers -- the write path, the filter compiler and CSV import --
# and they land in two modules. CSV import is absent because it inherits resolution
# through ``create_record`` / ``update_record`` and has no code of its own, which is the
# property this list asserts rather than a gap in it; the filter compiler is absent for
# the opposite reason, that it reaches the resolver as an injected callable built in the
# two services below and never touches the database itself.
EXPECTED_CALLER_MODULES = {
    "src/glosswork/services/records.py",  # the write path, and the filter callable
    "src/glosswork/services/search.py",  # search's filter callable
}


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_principal_exists_has_no_caller_outside_the_resolver() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _modules()
        if path not in PRINCIPAL_EXISTS_ALLOWED and "principal_exists" in _text(path)
    ]
    assert offenders == [], (
        "principal_exists answers 'is this a live principal id' and nothing else. Every "
        "user_ref reference must go through resolve_principal_ref instead, which is what "
        "makes @me, an email and a display name work on every surface at once, and what "
        "enforces allow_service_accounts. Offending modules: " + str(offenders)
    )


def test_the_resolver_is_the_only_caller_inside_its_own_module() -> None:
    """Not merely "somewhere in principals.py": inside the resolver function itself."""
    tree = ast.parse(_text(RESOLVER_MODULE))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == RESOLVER_NAME:
            body = ast.dump(node)
            assert "principal_exists" in body
            break
    else:  # pragma: no cover - the resolver is gone, which every other test catches
        raise AssertionError(f"{RESOLVER_NAME} is not defined in {RESOLVER_MODULE}")

    outside = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name != RESOLVER_NAME
        and "principal_exists" in ast.dump(node)
    ]
    assert outside == [], f"principal_exists is also called by {outside}"


def test_the_resolver_is_defined_exactly_once() -> None:
    definitions = [
        str(path.relative_to(REPO_ROOT))
        for path in _modules()
        if f"def {RESOLVER_NAME}(" in _text(path)
    ]
    assert definitions == ["src/glosswork/services/principals.py"], definitions


def _calls_the_resolver(path: Path) -> bool:
    """A *call* to the bare function, not a mention of its name.

    The distinction matters: ``filters.py`` names ``resolve_principal_ref`` as the
    injected callable on ``FilterContext`` and calls it as ``ctx.resolve_principal_ref``,
    which is the attribute, not this function. Matching on text would count that module
    as a fourth caller and make the assertion meaningless in the direction it exists to
    guard.
    """
    for node in ast.walk(ast.parse(_text(path))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == RESOLVER_NAME:
                return True
    return False


def test_the_resolver_has_exactly_the_three_documented_callers() -> None:
    callers = {str(path.relative_to(REPO_ROOT)) for path in _modules() if _calls_the_resolver(path)}
    assert callers == EXPECTED_CALLER_MODULES, (
        "A fourth module resolves a user_ref reference, or one of the three stopped. "
        f"Found: {sorted(callers)}"
    )


def test_no_adapter_resolves_a_name() -> None:
    """DD-3. Neither ``routes/`` nor ``mcp_server/`` may turn a name into
    a principal id: they are adapters over one service layer, which is what makes REST
    and MCP parity structural."""
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for directory in ("routes", "mcp_server")
        for path in sorted((SRC / directory).rglob("*.py"))
        if RESOLVER_NAME in _text(path) or "principal_exists" in _text(path)
    ]
    assert offenders == [], offenders


def test_only_the_principal_repository_reads_the_principals_table_for_the_directory() -> None:
    """DD-2. The directory search and the sidecar read are SQL, and SQL
    lives in the repository. In particular the filter compiler has no database access at
    all: it reaches the resolver as an injected callable."""
    assert "principals_by_ids" not in _text(SRC / "filters.py")
    assert "SELECT" not in _text(SRC / "filters.py")
    sql_modules = [
        str(path.relative_to(REPO_ROOT)) for path in _modules() if "FROM principals" in _text(path)
    ]
    assert sql_modules == ["src/glosswork/repositories/sqlite.py"], sql_modules
