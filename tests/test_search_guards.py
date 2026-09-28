"""The search guards: the constraints that decay silently if nothing enforces them.

Five separate promises are asserted here, each of which is true today by inspection
and would stay "true by inspection" long after it stopped being true in fact:

1. DD-32: no module under ``src/`` imports ``huggingface_hub`` or names
   ``from_pretrained``, and the hub client is genuinely not reached at runtime.
2. DD-32/FR-Q6: nothing under ``src/`` can download the model.
3. DD-2: only ``SqliteSearchRepository`` names the SQLite
   search primitives, so migrating off either index stays a one-module change.
4. DD-33: the query-prefix asymmetry, asserted from the grep side.
5. DD-5: the forbidden heavyweight ML dependencies appear nowhere in ``pyproject.toml``.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.search_support import model_dir, real_provider, repo_root

SRC = repo_root() / "src"


def python_sources() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def code_text(path: Path) -> str:
    """A file's *code*, with comments and docstrings removed.

    The constraints below are about what the code does, not about what its prose
    explains. ``search_index.py`` says in a comment that it never touches
    ``vec_embeddings`` directly, and ``db.py`` explains in a docstring why the
    extension has to load on every connection; a raw grep flags both as violations,
    which would leave two bad options -- delete the explanations, or delete the
    guard.

    String literals are deliberately **kept**, because that is where SQL lives: a
    real violation would be a query in a string, and this still catches it. What is
    dropped is exactly the two forms that cannot execute.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_nodes.add(id(body[0].value))
    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstring_nodes:
                parts.append(node.value)
        elif isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            parts.append(node.name)
    return "\n".join(parts)


def imported_module_names(path: Path) -> set[str]:
    """Every module name imported by a file, from its AST rather than its text.

    Parsing rather than grepping so that a name appearing in a docstring or a
    comment -- this file's own prose, for instance -- cannot make the guard fail,
    and so that ``import huggingface_hub as hh`` is caught as readily as a plain
    import.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


# --------------------------------------------------------------- DD-32 guards


def test_no_module_under_src_imports_huggingface_hub() -> None:
    offenders = [
        str(path.relative_to(repo_root()))
        for path in python_sources()
        if "huggingface_hub" in imported_module_names(path)
    ]
    assert offenders == [], (
        "DD-32: the runtime tolerates huggingface-hub as a transitive dependency of "
        "tokenizers but must never import it. Offending modules: " + ", ".join(offenders)
    )


def test_no_module_under_src_names_from_pretrained() -> None:
    """``from_pretrained`` is the one tokenizer entry point that reaches the hub.

    ``Tokenizer.from_file`` on the bundled ``tokenizer.json`` is the only loader the
    project uses (DD-32), so this name appearing anywhere in the package is the
    signature of a change that would fetch at runtime.
    """
    offenders = [
        str(path.relative_to(repo_root()))
        for path in python_sources()
        if "from_pretrained" in code_text(path)
    ]
    assert offenders == [], (
        "DD-32: the model is baked into the image and located by configuration; "
        "from_pretrained would download it. Offending modules: " + ", ".join(offenders)
    )


def test_huggingface_hub_is_absent_from_sys_modules_after_a_real_embed() -> None:
    """A fresh interpreter embeds a passage and a query, then checks ``sys.modules``.

    In a **subprocess**, deliberately (DD-32):
    ``sys.modules`` is process-global, so an inline assertion would pass or fail on
    which other test in the session imported what first, and a guard whose result
    depends on collection order is not a guard. Run this way, a future ``tokenizers``
    release that makes the hub import eager fails this test the day it is pinned
    rather than in production.
    """
    root = model_dir()
    script = f"""
import sys
sys.path.insert(0, {str(SRC)!r})
from pathlib import Path
from glosswork.services.embedding import build_provider

provider = build_provider(Path({str(root)!r}), "bge-small-en-v1.5")
provider.embed_passages(["a passage the worker would embed"])
provider.embed_query("a query the search service would embed")

assert "huggingface_hub" not in sys.modules, sorted(
    m for m in sys.modules if "hugging" in m.lower()
)
print("GUARD_OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, (
        "DD-32 guard (2) fired: huggingface_hub reached sys.modules during a real "
        f"embed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "GUARD_OK" in result.stdout


def test_no_heavyweight_ml_dependency_is_declared() -> None:
    """DD-32: the runtime is onnxruntime + tokenizers, and nothing larger."""
    pyproject = (repo_root() / "pyproject.toml").read_text(encoding="utf-8")
    # Only the dependency declarations, not the prose comments that explain *why*
    # these packages are excluded.
    declared = "\n".join(line for line in pyproject.splitlines() if line.strip().startswith('"'))
    for forbidden in (
        "huggingface-hub",
        "sentence-transformers",
        "transformers",
        "torch",
        "optimum",
    ):
        assert forbidden not in declared, (
            f"{forbidden!r} is declared in pyproject.toml. DD-32 keeps the "
            "runtime to onnxruntime plus tokenizers; huggingface-hub arrives only as a "
            "transitive dependency and is guarded, never declared."
        )


def test_the_pinned_search_dependencies_are_exactly_the_verified_versions() -> None:
    """The four search dependency pins, each verified on PyPI when pinned (DD-5)."""
    pyproject = (repo_root() / "pyproject.toml").read_text(encoding="utf-8")
    for pin in ("onnxruntime==1.29.0", "tokenizers==0.23.1", "sqlite-vec==0.1.9", "numpy==2.5.2"):
        assert f'"{pin}"' in pyproject, f"expected {pin} pinned in pyproject.toml"


# -------------------------------------------------------------- DD-2: one module


# The table and index names, plus the three query tokens added when the query methods
# appeared. ``MATCH`` is matched as a whole word: ``BULK_UPDATE_MAX_MATCHES`` is a
# name in the record service, not a query operator.
SEARCH_PRIMITIVES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("vec0", re.compile(r"vec0")),
    ("vec_embeddings", re.compile(r"vec_embeddings")),
    ("fts_content", re.compile(r"fts_content")),
    ("MATCH", re.compile(r"\bMATCH\b")),
    ("snippet(", re.compile(r"snippet\(")),
    ("bm25(", re.compile(r"bm25\(")),
)


def test_only_the_search_repository_names_the_sqlite_search_primitives() -> None:
    """Grep-backed.

    ``SqliteSearchRepository`` is the one implementation allowed to know that the
    vector index is sqlite-vec and the keyword index is FTS5. That is DD-2
    expressed as a constraint rather than a sentence: sqlite-vec is
    pre-1.0, and migrating off it -- or off FTS5 -- has to stay a one-module change.

    ``migrations.py`` is exempt because the DDL that *creates* those tables has to
    live in the numbered migration; it creates them and never queries them.
    ``MATCH``, ``snippet(``, and ``bm25(`` are in the same tuple, so the search service
    cannot grow its own FTS query either.
    """
    exempt = {
        # The one implementation allowed to query them.
        SRC / "glosswork" / "repositories" / "sqlite.py",
        # The DDL that *creates* them has to live in the numbered migration.
        SRC / "glosswork" / "migrations.py",
        # Loads the extension and reports on its absence. It names vec0 and
        # fts_content only inside the operator-facing fail-fast message, which is
        # the whole point of that message -- "no such module: vec0" is what an
        # operator will otherwise see with nothing telling them why. The narrower
        # assertion below holds it to that.
        SRC / "glosswork" / "db.py",
    }
    offenders: dict[str, list[str]] = {}
    for path in python_sources():
        if path in exempt:
            continue
        code = code_text(path)
        hits = [token for token, pattern in SEARCH_PRIMITIVES if pattern.search(code)]
        if hits:
            offenders[str(path.relative_to(repo_root()))] = hits
    assert offenders == {}, (
        "Only SqliteSearchRepository may name the SQLite search primitives "
        f"(DD-2). Offenders: {offenders}"
    )


def test_the_extension_loader_names_the_primitives_only_in_its_diagnostic() -> None:
    """``db.py``'s exemption above is narrow, and this is what keeps it narrow.

    It may say ``vec0`` in a message; it may not read from or write to either index.
    Without this, the exemption would be a hole big enough to move a query through.
    """
    code = code_text(SRC / "glosswork" / "db.py").lower()
    for shape in (
        "from fts_content",
        "into fts_content",
        "update fts_content",
        "delete from fts_content",
        "from vec_embeddings",
        "into vec_embeddings",
        "update vec_embeddings",
        "delete from vec_embeddings",
        "using vec0",
    ):
        assert shape not in code, (
            f"db.py contains {shape!r}. Its exemption from the primitive-name guard "
            "covers the fail-fast diagnostic only, never a query."
        )


# ---------------------------------------------- DD-33: the prefix, from the grep side


def test_embed_passages_is_called_only_from_the_worker() -> None:
    """DD-33: one assertion on the query/passage asymmetry.

    Reversing the prefix passes every functional test, so its correctness is pinned
    by where each method may be called from, not only by what the provider does.
    """
    callers = [
        str(path.relative_to(repo_root()))
        for path in python_sources()
        if "embed_passages" in code_text(path)
        and path.name != "embedding.py"  # its definition site
    ]
    assert callers == ["src/glosswork/services/embedding_worker.py"], (
        f"embed_passages must be called only from the indexing worker; found: {callers}"
    )


def test_embed_query_is_called_only_from_the_search_service() -> None:
    """The search service is the query path's single caller, and this assertion pins
    exactly that. A second
    caller -- a route, a tool, the worker -- is how the prefix asymmetry would start
    to drift, because only the service pairs a query embedding with the vector arm.
    """
    callers = [
        str(path.relative_to(repo_root()))
        for path in python_sources()
        if "embed_query" in code_text(path) and path.name != "embedding.py"  # its definition site
    ]
    assert callers == ["src/glosswork/services/search.py"], (
        f"embed_query may be called only from the search service (DD-33). Found: {callers}"
    )


def test_the_query_prefix_is_written_in_exactly_one_place() -> None:
    literal = "Represent this sentence for searching relevant passages: "
    holders = [
        str(path.relative_to(repo_root()))
        for path in python_sources()
        if literal in code_text(path)
    ]
    assert holders == ["src/glosswork/services/embedding.py"], (
        "The bge query prefix is defined once, in the provider, and applied inside "
        f"embed_query only. Found in: {holders}"
    )


# ------------------------------------- DD-33: the prefix, from the tokenizer side


class CapturingTokenizer:
    """Wraps the real tokenizer to record the exact text handed to the model."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.seen: list[str] = []

    def encode_batch(self, texts: list[str]) -> object:
        self.seen.extend(texts)
        return self._inner.encode_batch(texts)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


@pytest.fixture
def captured() -> tuple[object, CapturingTokenizer]:
    provider = real_provider()
    wrapper = CapturingTokenizer(provider._tokenizer)  # type: ignore[attr-defined]
    original = provider._tokenizer  # type: ignore[attr-defined]
    provider._tokenizer = wrapper  # type: ignore[attr-defined]
    yield provider, wrapper
    provider._tokenizer = original  # type: ignore[attr-defined]


def test_the_text_reaching_the_model_carries_the_prefix_for_a_query_and_not_for_a_passage(
    captured: tuple[object, CapturingTokenizer],
) -> None:
    """DD-33: the query-prefix asymmetry, at the layer where reversal would actually happen."""
    provider, wrapper = captured
    prefix = "Represent this sentence for searching relevant passages: "

    provider.embed_query("who owns the renewal")  # type: ignore[attr-defined]
    assert len(wrapper.seen) == 1
    assert wrapper.seen[0].startswith(prefix)
    assert wrapper.seen[0] == prefix + "who owns the renewal"

    wrapper.seen.clear()
    provider.embed_passages(["Dana owns the renewal.", "The rack overheated."])  # type: ignore[attr-defined]
    assert wrapper.seen == ["Dana owns the renewal.", "The rack overheated."]
    assert not any(text.startswith(prefix) for text in wrapper.seen)
