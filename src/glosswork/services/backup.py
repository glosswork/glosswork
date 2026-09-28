"""The operator backup artifact (FR-P8, FR-P2, DD-36).

One tar stream containing a consistent database snapshot and the attachment blob
tree, in that order. DD-36 decided all three of the things this module implements,
and the ordering is the one that matters:

**Database first, blob tree second.** Attachments live on the filesystem outside the
database, so a backup is two things that can disagree. Snapshotting the database
*before* walking the blobs means every ``attachments`` row in the snapshot references
a blob that already existed and is therefore in the copy. The reverse order produces
a restored row pointing at bytes that were never captured. A blob written after the
snapshot and copied anyway is harmless -- it is unreferenced, and
:meth:`~glosswork.services.attachments.AttachmentService.sweep_orphan_blobs`
removes it.

The ordering is a property of *when things are read*, not of where they sit in the
archive, which is why :meth:`BackupService.stream` is a generator: nothing happens
until the response starts pulling, the snapshot is taken on the first pull, and the
blob tree is walked only after that snapshot is complete. ``test_backup.py`` drives
exactly that interleaving to prove it.

Restore is deliberately not here. A restore endpoint would have to overwrite the
database it is being served from; DD-36 makes it an operator procedure instead
(stop the container, replace the volume contents, start it -- migrations run at
startup per FR-P6). docs/DEPLOYMENT.md carries the steps.
"""

from __future__ import annotations

import tarfile
import time
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.logging import get_logger
from glosswork.repositories.interfaces import (
    AuditRepository,
    BackupRepository,
    BlobRepository,
)
from glosswork.services.base import make_event
from glosswork.timeutil import format_datetime, utc_now

logger = get_logger(__name__)

# The name the database snapshot carries inside the archive. It is deliberately the
# same filename the live deployment uses (``app.DATABASE_FILENAME``), so the restore
# procedure is "extract into the empty volume" with no rename step for an operator to
# get wrong at 3am.
SNAPSHOT_ARCNAME = "glosswork.sqlite3"
BLOB_ARCNAME_ROOT = "attachments"

_TAR_BLOCK = 512
_READ_CHUNK_BYTES = 256 * 1024

# Where the snapshot is staged before streaming. Under the data directory rather than
# the system temp dir on purpose: it is the volume the operator sized for this data,
# and a snapshot of a 200k-record database does not belong on a container's small
# writable layer.
_SNAPSHOT_DIRNAME = "backup-tmp"


def _tar_member(path: Path, arcname: str) -> Iterator[bytes]:
    """One complete tar member -- header, content, padding -- yielded in chunks.

    Written against ``TarInfo.tobuf`` rather than driving ``tarfile`` itself because
    ``tarfile`` is push-shaped: it writes a whole member into a file object in one
    call, so a 150 MB snapshot would be buffered entirely in memory before the first
    byte could be yielded. Emitting the header tarfile itself produces and then
    streaming the payload keeps memory flat at ``_READ_CHUNK_BYTES`` and still yields
    an archive ``tar -xf`` and ``tarfile.open(mode="r|")`` read back byte for byte
    (asserted in ``test_backup.py``).
    """
    stat = path.stat()
    info = tarfile.TarInfo(name=arcname)
    info.size = stat.st_size
    info.mtime = int(stat.st_mtime)
    info.mode = 0o644
    info.type = tarfile.REGTYPE
    yield info.tobuf(format=tarfile.PAX_FORMAT)

    written = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK_BYTES):
            written += len(chunk)
            yield chunk
    if written != info.size:
        # The header declared a length; a short or long read would produce a corrupt
        # archive that only fails on extraction, hours later, on a different machine.
        raise OSError(
            f"{path} changed size while it was being archived "
            f"({info.size} declared, {written} read)."
        )
    padding = -written % _TAR_BLOCK
    if padding:
        yield bytes(padding)


def _tar_end_of_archive() -> bytes:
    """The two zero blocks that terminate a tar stream."""
    return bytes(2 * _TAR_BLOCK)


class BackupService:
    def __init__(
        self,
        db: Database,
        data_dir: Path,
        backup_repo: BackupRepository,
        blob_repo: BlobRepository,
        audit_repo: AuditRepository,
    ) -> None:
        self._db = db
        self._data_dir = data_dir
        self._backup = backup_repo
        self._blobs = blob_repo
        self._audit = audit_repo

    def stream(self, actor: ActorContext, now: datetime | None = None) -> Iterator[bytes]:
        """Yield one tar artifact: the database snapshot, then the blob tree.

        A generator, and lazily so: the snapshot is taken on the first pull. That is
        what makes DD-36's ordering testable rather than merely documented -- a test
        can pull one chunk, upload an attachment, and then drain the rest, and assert
        the new attachment is absent from the snapshot's database.

        The staged snapshot is removed in a ``finally``, which runs when the response
        finishes *or* when the client disconnects and the server closes the generator.
        """
        staging_dir = self._data_dir / _SNAPSHOT_DIRNAME
        staging_dir.mkdir(parents=True, exist_ok=True)
        # A fresh name every time: VACUUM INTO refuses to overwrite an existing file,
        # and two operators pressing the button at once must not collide.
        snapshot = staging_dir / f"snapshot-{uuid.uuid4().hex}.sqlite3"
        try:
            started = time.perf_counter()
            with self._db.raw_connection() as raw:
                self._backup.vacuum_into(raw, snapshot)
            snapshot_ms = round((time.perf_counter() - started) * 1000, 2)
            snapshot_bytes = snapshot.stat().st_size

            # Audited here rather than after the stream completes: the event this
            # records is that a consistent snapshot of the deployment was taken and
            # handed out, and that has already happened. A client that disconnects
            # mid-download still got the snapshot's first bytes.
            self._record_audit(actor, snapshot_bytes, snapshot_ms, now)

            yield from _tar_member(snapshot, SNAPSHOT_ARCNAME)

            # Second, and only now: every blob the snapshot's rows can reference
            # already exists on disk, because the snapshot is older than this walk.
            blob_count = 0
            blob_bytes = 0
            for sha256 in self._blobs.iter_hashes():
                path = self._blobs.path_for(sha256)
                if not path.is_file():
                    continue  # swept between the listing and here; harmless
                blob_count += 1
                blob_bytes += path.stat().st_size
                yield from _tar_member(path, f"{BLOB_ARCNAME_ROOT}/{sha256[:2]}/{sha256}")

            yield _tar_end_of_archive()
            logger.info(
                "backup_streamed",
                snapshot_bytes=snapshot_bytes,
                snapshot_ms=snapshot_ms,
                blob_count=blob_count,
                blob_bytes=blob_bytes,
                principal_id=actor.principal_id,
            )
        finally:
            snapshot.unlink(missing_ok=True)

    def _record_audit(
        self,
        actor: ActorContext,
        snapshot_bytes: int,
        snapshot_ms: float,
        now: datetime | None,
    ) -> None:
        """One ``backup_taken`` row with full attribution (DD-4), the same shape
        ``reindex_requested`` uses: an operator action against the deployment itself,
        so it carries no record or object type."""
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="backup",
                        entity_id="backup",
                        action="backup_taken",
                        new_value={
                            "snapshot_bytes": snapshot_bytes,
                            "snapshot_ms": snapshot_ms,
                        },
                    )
                ],
            )
