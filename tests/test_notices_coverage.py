"""``scripts/notices_coverage.py`` judges coverage the way its docstring says.

The release workflow runs it against each built image and publishes nothing while it
exits 1, so a rule that is too lenient ships an image without its notices and a rule
that is too strict blocks every release. The rules are exercised here on synthetic
inputs; the image half (``docker run``) is exercised by the release itself and by
running the script against a local build.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "notices_coverage.py"
PACKAGE_LOCK = REPO_ROOT / "web" / "package-lock.json"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("notices_coverage", SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    # A dataclass looks its module up in sys.modules while it is being defined.
    sys.modules["notices_coverage"] = module
    spec.loader.exec_module(module)
    return module


nc = _script()


def _distribution(name: str, *files: str) -> dict[str, object]:
    return {"name": name, "files": list(files)}


def test_a_distribution_is_covered_by_a_licence_file_it_installs() -> None:
    """In its metadata, as most wheels do, or in its package, as ``onnxruntime`` does."""
    packages = nc.python_packages(
        [
            _distribution("attrs", "attrs-25.1.0.dist-info/licenses/LICENSE", "attrs/__init__.py"),
            _distribution("onnxruntime", "onnxruntime/LICENSE", "onnxruntime/__init__.py"),
            _distribution("idna", "idna-3.10.dist-info/LICENSE.md"),
        ],
        set(),
    )
    assert [package.covered for package in packages] == [True, True, True]


def test_a_module_or_a_bare_record_is_not_a_licence_file() -> None:
    """``tokenizers`` installs no licence file at all; a module whose name starts with
    one of the words is source, not a notice."""
    packages = nc.python_packages(
        [
            _distribution(
                "tokenizers", "tokenizers-0.1.dist-info/RECORD", "tokenizers/__init__.py"
            ),
            _distribution("x", "x/license.py", "x/licenses_helper.py"),
        ],
        set(),
    )
    assert [package.covered for package in packages] == [False, False]


def test_prose_naming_a_package_is_not_coverage() -> None:
    """The notices file's own scope paragraph names packages it does not cover."""
    named = nc.named_in_notices(
        "## What this covers\n\nIt does not cover `react` or `react-dom`.\n"
    )
    assert named == set()


def test_a_distribution_with_a_heading_is_covered_whatever_its_spelling() -> None:
    named = nc.named_in_notices("## `sqlite_vec`\n\ntext\n\n### `Huggingface.Hub`, a hub\n")
    packages = nc.python_packages(
        [_distribution("sqlite-vec"), _distribution("huggingface-hub")], named
    )
    assert [package.covered for package in packages] == [True, True]


def test_the_project_itself_is_not_counted() -> None:
    assert nc.python_packages([_distribution("glosswork")], set()) == []


def test_npm_production_names_leave_out_dev_packages_and_the_root() -> None:
    lock = {
        "packages": {
            "": {"name": "web"},
            "node_modules/react": {"version": "19.0.0"},
            "node_modules/@tanstack/react-query": {"version": "5.0.0"},
            "node_modules/a/node_modules/scheduler": {"version": "0.25.0"},
            "node_modules/vite": {"version": "8.0.0", "dev": True},
            "node_modules/fsevents": {"version": "2.3.3", "devOptional": True},
        }
    }
    assert nc.npm_production_names(lock) == sorted(
        ["@tanstack/react-query", "react", "scheduler", *nc.BUNDLED_BUILD_TOOLS]
    )
    assert "fsevents" not in nc.npm_production_names(lock)


def test_an_npm_package_is_covered_only_when_the_notices_name_it() -> None:
    named = nc.named_in_notices("## `react`\n")
    packages = nc.npm_packages(["react", "react-dom"], named)
    assert [(package.name, package.covered) for package in packages] == [
        ("react", True),
        ("react-dom", False),
    ]


def test_npm_production_entries_carry_each_version_and_where_it_came_from() -> None:
    """One entry per name and version, however many install paths hold it, and a build
    tool's entries although each is marked ``dev``."""
    lock = {
        "packages": {
            "": {"name": "web"},
            "node_modules/@types/unist": {"version": "3.0.3", "resolved": "u3", "integrity": "i3"},
            "node_modules/a/node_modules/@types/unist": {
                "version": "2.0.11",
                "resolved": "u2",
                "integrity": "i2",
            },
            "node_modules/b/node_modules/@types/unist": {
                "version": "2.0.11",
                "resolved": "u2",
                "integrity": "i2",
            },
            "node_modules/rolldown": {"version": "1.2.5", "dev": True, "resolved": "r"},
            "node_modules/typescript": {"version": "6.0.0", "dev": True, "resolved": "t"},
        }
    }
    entries = nc.npm_production_entries(lock)
    assert [(entry.name, entry.version, entry.resolved) for entry in entries] == [
        ("@types/unist", "2.0.11", "u2"),
        ("@types/unist", "3.0.3", "u3"),
        ("rolldown", "1.2.5", "r"),
    ]
    assert entries[0].integrity == "i2"


def test_the_real_lockfile_yields_the_runtime_and_the_build_tools_that_ship_code() -> None:
    """Vite, Tailwind and rolldown are development packages whose code the bundle
    carries (rolldown's interop helpers open the shipped JavaScript); TypeScript and
    ESLint are development packages whose code it does not."""
    names = nc.npm_production_names(json.loads(PACKAGE_LOCK.read_text()))
    assert "react" in names and "react-dom" in names
    assert "vite" in names and "tailwindcss" in names and "rolldown" in names
    assert "typescript" not in names and "eslint" not in names


def test_a_heading_in_the_licences_file_covers_a_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The generated licences file counts as the notices file does."""
    notices = tmp_path / "notices.txt"
    notices.write_text("# Notices\n")
    licenses = tmp_path / "licenses.txt"
    licenses.write_text("## `react` 19.0.0\n\n    MIT\n")
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps({"packages": {"": {}, "node_modules/react": {}, "node_modules/ms": {}}})
    )
    monkeypatch.setattr(nc, "image_distributions", lambda image: [])
    argv = ["--image", "x", "--notices", str(notices), "--licenses", str(licenses)]
    assert nc.main([*argv, "--package-lock", str(lock)]) == 1
    uncovered = capsys.readouterr().out.split("Not covered:")[1]
    assert "- npm `ms`" in uncovered
    assert "`react`" not in uncovered


def test_main_exits_1_and_names_what_is_uncovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notices = tmp_path / "notices.txt"
    notices.write_text("## `react`\n\nCovers `ms` in prose only.\n")
    licenses = tmp_path / "licenses.txt"
    licenses.write_text("# Licences\n")
    lock = tmp_path / "package-lock.json"
    lock.write_text(
        json.dumps({"packages": {"": {}, "node_modules/react": {}, "node_modules/ms": {}}})
    )
    monkeypatch.setattr(
        nc,
        "image_distributions",
        lambda image: [_distribution("idna", "idna-3.10.dist-info/LICENSE.md")],
    )
    summary = tmp_path / "summary.txt"
    code = nc.main(
        [
            "--image",
            "x",
            "--notices",
            str(notices),
            "--licenses",
            str(licenses),
            "--package-lock",
            str(lock),
            "--summary",
            str(summary),
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "- npm `ms`" in out
    assert "idna" not in out.split("Not covered:")[1]
    assert "## Third-party notices coverage" in summary.read_text()


def test_main_exits_0_when_everything_is_covered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notices = tmp_path / "notices.txt"
    notices.write_text("## `react`\n\n## `vite`\n\n## `tailwindcss`\n")
    licenses = tmp_path / "licenses.txt"
    licenses.write_text("## `rolldown` 1.2.5\n")
    lock = tmp_path / "package-lock.json"
    lock.write_text(json.dumps({"packages": {"": {}, "node_modules/react": {}}}))
    monkeypatch.setattr(
        nc,
        "image_distributions",
        lambda image: [_distribution("idna", "idna-3.10.dist-info/LICENSE.md")],
    )
    argv = ["--image", "x", "--notices", str(notices), "--licenses", str(licenses)]
    assert nc.main([*argv, "--package-lock", str(lock)]) == 0
