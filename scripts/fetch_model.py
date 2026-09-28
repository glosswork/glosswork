#!/usr/bin/env python3
"""Fetch the bundled embedding model for local development and the test suite.

This is the developer's equivalent of the ``model`` stage in the Dockerfile (DD-32):
the same three files, from the same revision-pinned URLs, verified against the same
sha256 digests. It is deliberately **outside** ``src/glosswork`` and uses only the
standard library, because DD-32's guarantee is that no module the application can
import is able to download anything -- a grep-backed test asserts exactly that, and a
downloader living inside the package would make the guarantee untrue no matter how
carefully it were guarded.

Usage::

    uv run python scripts/fetch_model.py            # into <repo>/models/
    uv run python scripts/fetch_model.py --dest DIR

Files land at ``<dest>/bge-small-en-v1.5/{model.onnx,tokenizer.json,config.json}``,
the flat layout ``GW_MODEL_DIR`` plus ``GW_EMBEDDING_MODEL`` expects. ``models/`` is
gitignored.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

# BAAI/bge-small-en-v1.5 pinned to a repository revision, never to ``main`` (DD-32).
# The revision was last modified 2024-02-22 and the artifact has been stable since.
REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
BASE_URL = f"https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/{REVISION}"

# (remote path, local filename, sha256, byte size). The digests are the contract:
# a file whose bytes differ is refused, not written. DD-32 recorded the digest for
# model.onnx and byte sizes for all three; the two small files' digests were computed
# from downloads whose byte sizes matched DD-32 exactly, and are recorded in DD-32 so
# every fetch and every image build is digest-pinned.
ARTIFACTS: tuple[tuple[str, str, str, int], ...] = (
    (
        "onnx/model.onnx",
        "model.onnx",
        "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35",
        133_093_490,
    ),
    (
        "tokenizer.json",
        "tokenizer.json",
        "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
        711_396,
    ),
    (
        "config.json",
        "config.json",
        "094f8e891b932f2000c92cfc663bac4c62069f5d8af5b5278c4306aef3084750",
        743,
    ),
)

MODEL_DIRNAME = "bge-small-en-v1.5"
_CHUNK = 1 << 20


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _download(url: str, target: Path) -> tuple[str, int, Path]:
    """Stream ``url`` to a temporary file beside ``target``.

    Returns the downloaded bytes' digest, their size, and the temporary file's path;
    the caller moves it into place only once both match what is pinned above.

    Written to a temporary file first so a failed digest check can never leave a
    partial or substituted artifact where the provider would load it.
    """
    digest = hashlib.sha256()
    size = 0
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    tmp = Path(tmp_name)
    try:
        with open(fd, "wb") as out, urllib.request.urlopen(url) as response:
            while chunk := response.read(_CHUNK):
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), size, tmp


def fetch(dest_root: Path, force: bool = False) -> int:
    model_dir = dest_root / MODEL_DIRNAME
    for remote_path, filename, expected_sha, expected_size in ARTIFACTS:
        target = model_dir / filename
        if target.exists() and not force:
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual == expected_sha:
                print(f"ok       {target}  (digest matches, skipping)")
                continue
            print(f"replacing {target}  (digest {actual[:12]}... does not match)")
        url = f"{BASE_URL}/{remote_path}"
        print(f"fetching {url}")
        actual_sha, actual_size, tmp = _download(url, target)
        if actual_sha != expected_sha or actual_size != expected_size:
            tmp.unlink(missing_ok=True)
            print(
                f"REFUSED  {filename}: expected sha256 {expected_sha} "
                f"({expected_size} bytes), got {actual_sha} ({actual_size} bytes). "
                "Nothing was written.",
                file=sys.stderr,
            )
            return 1
        tmp.chmod(0o644)  # mkstemp creates 0600; the model is read-only shared data
        shutil.move(str(tmp), str(target))
        print(f"ok       {target}  ({actual_size} bytes, sha256 {actual_sha[:12]}...)")
    print(f"\nModel ready at {model_dir}")
    print("Point GW_MODEL_DIR at its parent, or run from the repo root.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Directory to write <dest>/bge-small-en-v1.5/ into (default: <repo>/models).",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-download even if the digest already matches."
    )
    args = parser.parse_args()
    return fetch(args.dest or (_repo_root() / "models"), force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
