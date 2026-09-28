"""Filesystem-backed :class:`BlobRepository` (docs/DATA_MODEL.md section 8, DD-2).

Bytes live under ``<DATA_DIR>/attachments/<sha256[0:2]>/<sha256>``, content-addressed
so writing identical content twice is a no-op after the first write: exactly one file
on disk regardless of how many ``attachments`` rows reference it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from glosswork.errors import NotFoundError, ValidationFailedError

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_STREAM_CHUNK_BYTES = 64 * 1024


class FilesystemBlobRepository:
    def __init__(self, root: Path) -> None:
        self._root = root

    def _path_for(self, sha256: str) -> Path:
        if not _SHA256_PATTERN.match(sha256):
            raise ValidationFailedError(f"Invalid content hash {sha256!r}.")
        return self._root / sha256[:2] / sha256

    def write(self, sha256: str, content: bytes) -> None:
        path = self._path_for(sha256)
        if path.exists():
            return  # content-addressed: identical bytes are already stored
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(content)
        tmp.replace(path)

    def read(self, sha256: str) -> bytes:
        path = self._path_for(sha256)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise NotFoundError("attachment blob", sha256) from None

    def open_stream(self, sha256: str) -> Iterator[bytes]:
        """Open the blob and return an iterator that yields it in fixed-size
        chunks, for the download route to stream to the client without
        buffering it fully into memory.

        Deliberately **not itself a generator function** (no ``yield`` in this
        method's own body), even though it looks like one should be here. A
        generator function defers its *entire* body -- including a leading
        ``try/except FileNotFoundError`` -- until the caller pulls the first
        chunk. By then the download route has already built a
        ``StreamingResponse`` and the ASGI server has already sent a 200 status
        line, so a missing blob would surface as a truncated response instead
        of a clean 404 (found while building the backup-restore negative
        case, ``container_tests/test_backup_restore.py``, which restores a
        database whose ``attachments`` rows outlive their bytes on purpose).
        Opening the file here, before returning, makes a missing blob raise
        synchronously -- matching what :meth:`AttachmentService.stream_download`
        already documents happening for a missing *row*.
        """
        path = self._path_for(sha256)
        try:
            handle = path.open("rb")
        except FileNotFoundError:
            raise NotFoundError("attachment blob", sha256) from None
        return self._stream_open_handle(handle)

    @staticmethod
    def _stream_open_handle(handle: BinaryIO) -> Iterator[bytes]:
        with handle:
            while chunk := handle.read(_STREAM_CHUNK_BYTES):
                yield chunk

    def delete(self, sha256: str) -> bool:
        """Remove one blob's bytes, returning whether a file was actually removed.

        Without it this repository would expose only ``write``, ``read`` and
        ``open_stream``, so there would be no deletion path at all and unreferenced
        bytes would be retained permanently. Deleting is idempotent --
        a missing file is not an error -- because the caller is a sweep that races
        nothing but itself.

        **This deletes bytes, not a reference.** Content addressing means one file can
        back many ``attachments`` rows, so nothing may call this without first
        establishing that no row references the hash. That check lives in
        :meth:`~glosswork.services.attachments.AttachmentService.sweep_orphan_blobs`,
        which is the only caller.
        """
        path = self._path_for(sha256)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def iter_hashes(self) -> Iterator[str]:
        """Yield the content hash of every blob currently on disk.

        Walks the two-level fan-out directory rather than reading the database, which
        is the point: the sweep compares what is *on the volume* against what the
        ``attachments`` table still references, and an orphan is by definition
        something the database cannot tell you about. Files whose name is not a valid
        content hash are skipped rather than reported -- an interrupted write leaves a
        ``.tmp`` sibling, and that is not a blob.
        """
        if not self._root.is_dir():
            return
        for prefix_dir in sorted(self._root.iterdir()):
            if not prefix_dir.is_dir():
                continue
            for candidate in sorted(prefix_dir.iterdir()):
                if candidate.is_file() and _SHA256_PATTERN.match(candidate.name):
                    yield candidate.name

    def path_for(self, sha256: str) -> Path:
        """The on-disk location of one blob. Exposed for the backup artifact, which
        copies the blob tree as files rather than reading each one into memory."""
        return self._path_for(sha256)
