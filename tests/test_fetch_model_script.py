"""``scripts/fetch_model.py``: the developer's half of DD-32.

The script lives outside the package on purpose -- DD-32's guarantee is that no module
the application can import is able to download anything, and a downloader inside
``src/`` would make that untrue however carefully it were guarded (``test_search_guards``
asserts the guarantee itself). It is still shipped code, and the one behavior that
matters is that it **refuses to write a file whose digest does not match**: a fetch
script that writes first and checks afterwards is a fetch script that can leave a
substituted model on disk.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from tests.search_support import repo_root

SCRIPT = repo_root() / "scripts" / "fetch_model.py"


def load_script() -> ModuleType:
    """Import the script by path; it is deliberately not an importable package module."""
    spec = importlib.util.spec_from_file_location("gw_fetch_model", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_is_outside_the_package() -> None:
    assert SCRIPT.is_file()
    assert not (repo_root() / "src" / "glosswork" / "fetch_model.py").exists()


def test_the_pinned_urls_and_digests_match_the_dockerfile() -> None:
    """The build stage and the developer script must never disagree (DD-32).

    They are the same three files at the same revision verified against the same
    digests; a drift between them would mean a developer and the image were running
    different models while every test still passed.
    """
    module = load_script()
    dockerfile = (repo_root() / "Dockerfile").read_text(encoding="utf-8")
    assert module.REVISION in dockerfile
    for remote_path, _filename, digest, _size in module.ARTIFACTS:
        assert f"--checksum=sha256:{digest}" in dockerfile, (
            f"{remote_path} is pinned to {digest} in scripts/fetch_model.py but the "
            "Dockerfile's model stage does not carry that checksum"
        )
        assert f"{module.BASE_URL}/{remote_path}" in dockerfile


def test_the_recorded_digests_match_the_model_actually_on_disk() -> None:
    """The artifact the tests run against is the artifact that is pinned.

    Fails rather than skips when the model is absent, like every other model-dependent
    test (DD-32); the message names the fetch command.
    """
    from tests.search_support import model_dir

    directory = model_dir() / "bge-small-en-v1.5"
    module = load_script()
    for _remote_path, filename, digest, size in module.ARTIFACTS:
        path = directory / filename
        if not path.is_file():
            # config.json is fetched by the script but not required by the provider.
            assert filename == "config.json", f"{filename} missing from {directory}"
            continue
        raw = path.read_bytes()
        assert len(raw) == size, f"{filename} is {len(raw)} bytes, expected {size}"
        assert hashlib.sha256(raw).hexdigest() == digest, f"{filename} digest mismatch"


def test_a_digest_mismatch_refuses_to_write_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The load-bearing behavior: bad bytes leave nothing on disk.

    The download is stubbed rather than performed, so this test makes no network call
    of its own. What it exercises is the branch after the download: the temporary file
    is discarded and no file appears at the destination.
    """
    module = load_script()
    monkeypatch.setattr(
        module,
        "ARTIFACTS",
        (("onnx/model.onnx", "model.onnx", "0" * 64, 11),),
    )

    def fake_download(url: str, target: Path) -> tuple[str, int, Path]:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / ".partial"
        tmp.write_bytes(b"not the model")
        return hashlib.sha256(b"not the model").hexdigest(), 13, tmp

    monkeypatch.setattr(module, "_download", fake_download)

    assert module.fetch(tmp_path) == 1
    written = tmp_path / "bge-small-en-v1.5" / "model.onnx"
    assert not written.exists(), "a file with a mismatched digest was written"
    assert not list((tmp_path / "bge-small-en-v1.5").glob(".partial*")), (
        "the temporary download was left behind"
    )
    out = capsys.readouterr()
    assert "REFUSED" in out.err
    assert "Nothing was written" in out.err


def test_a_matching_digest_writes_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other side of the same branch, so the refusal above is not vacuous."""
    module = load_script()
    payload = b"the model bytes"
    digest = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(
        module, "ARTIFACTS", (("onnx/model.onnx", "model.onnx", digest, len(payload)),)
    )

    def fake_download(url: str, target: Path) -> tuple[str, int, Path]:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / ".partial"
        tmp.write_bytes(payload)
        return digest, len(payload), tmp

    monkeypatch.setattr(module, "_download", fake_download)

    assert module.fetch(tmp_path) == 0
    written = tmp_path / "bge-small-en-v1.5" / "model.onnx"
    assert written.read_bytes() == payload


def test_a_size_mismatch_alone_is_also_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both recorded facts are checked, not just the digest."""
    module = load_script()
    payload = b"the model bytes"
    monkeypatch.setattr(
        module,
        "ARTIFACTS",
        (("onnx/model.onnx", "model.onnx", hashlib.sha256(payload).hexdigest(), 999),),
    )

    def fake_download(url: str, target: Path) -> tuple[str, int, Path]:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / ".partial"
        tmp.write_bytes(payload)
        return hashlib.sha256(payload).hexdigest(), len(payload), tmp

    monkeypatch.setattr(module, "_download", fake_download)

    assert module.fetch(tmp_path) == 1
    assert not (tmp_path / "bge-small-en-v1.5" / "model.onnx").exists()
