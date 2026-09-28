"""The backup artifact and the orphan blob sweep (FR-P8, FR-P2; DD-36).

The load-bearing test here is :func:`test_snapshot_precedes_the_blob_walk`. DD-36
calls database-first-then-blobs "the correctness invariant of the whole feature", so
it is asserted by a test rather than left to reading order. Because
``BackupService.stream`` is a generator, a test can sit between the snapshot and the
blob walk and mutate the deployment from there, which is what turns the ordering from
a comment into a fact.
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.services import ServiceBundle
from glosswork.services.backup import BLOB_ARCNAME_ROOT, SNAPSHOT_ARCNAME
from tests.conftest import make_actor


def _extract(chunks: list[bytes], into: Path) -> dict[str, Any]:
    """Unpack a streamed artifact and return ``{arcname: TarInfo}``."""
    buffer = io.BytesIO(b"".join(chunks))
    members: dict[str, Any] = {}
    with tarfile.open(fileobj=buffer, mode="r|") as tar:
        for member in tar:
            members[member.name] = member
            tar.extract(member, path=into, filter="data")
    return members


def _snapshot_query(snapshot: Path, sql: str) -> list[tuple[Any, ...]]:
    """Query the extracted snapshot with plain ``sqlite3``.

    Deliberately not through ``Database``: the point is that the artifact is an
    ordinary SQLite file anyone can open, and going through the application's own
    engine would hide a snapshot that only works because of a pragma we happen to set.
    """
    conn = sqlite3.connect(snapshot)
    try:
        return list(conn.execute(sql).fetchall())
    finally:
        conn.close()


def _seed_type(services: ServiceBundle, actor: ActorContext, key: str = "widget") -> None:
    services.schema.create_object_type(
        actor,
        key=key,
        name="Widget",
        name_plural="Widgets",
        description="A thing tracked for the backup tests.",
        key_prefix=key[:4].upper(),
        fields=[
            {
                "key": "name",
                "name": "Name",
                "type": "short_text",
                "description": "What this widget is called.",
                "required": True,
            }
        ],
    )


# --------------------------------------------------------------------- artifact


def test_artifact_is_a_readable_tar_with_the_snapshot_first(
    services: ServiceBundle, actor: ActorContext, tmp_path: Path
) -> None:
    _seed_type(services, actor)
    services.records.create_record(actor, "widget", {"name": "First"})
    services.attachments.upload(actor, "note.txt", "text/plain", b"attached bytes")

    chunks = list(services.backup.stream(make_actor()))
    names = list(_extract(chunks, tmp_path / "out"))

    # Ordering inside the archive, which mirrors the ordering of capture.
    assert names[0] == SNAPSHOT_ARCNAME
    assert all(name.startswith(f"{BLOB_ARCNAME_ROOT}/") for name in names[1:])
    assert len(names) == 2  # one snapshot, one blob


def test_snapshot_is_a_working_database_carrying_the_virtual_tables(
    services: ServiceBundle, actor: ActorContext, tmp_path: Path
) -> None:
    """DD-36's single load-bearing fact: ``vec0`` and FTS5 shadow storage is ordinary
    SQLite content, so it rides along and needs no separate backup mechanism."""
    _seed_type(services, actor)
    services.records.create_record(actor, "widget", {"name": "Indexed thing"})

    out = tmp_path / "out"
    _extract(list(services.backup.stream(make_actor())), out)
    snapshot = out / SNAPSHOT_ARCNAME

    tables = {row[0] for row in _snapshot_query(snapshot, "SELECT name FROM sqlite_master")}
    assert "vec_embeddings" in tables
    assert "fts_content" in tables
    assert _snapshot_query(snapshot, "SELECT count(*) FROM records")[0][0] == 1
    # Migration state travels with it, which is what makes the restored deployment
    # start without re-running anything (FR-P6).
    assert _snapshot_query(snapshot, "SELECT count(*) FROM schema_migrations")[0][0] == 12


# ---------------------------------------------------------- the DD-36 ordering


def test_snapshot_precedes_the_blob_walk(
    services: ServiceBundle, actor: ActorContext, tmp_path: Path
) -> None:
    """The correctness invariant of the whole feature (DD-36), asserted both ways.

    Pulling the first chunk forces the snapshot; the attachment uploaded *after* that
    must therefore be absent from the artifact's database. And every attachment row
    that *is* in the snapshot must have its blob present in the tar — which is the
    half that would break if the order were reversed.
    """
    _seed_type(services, actor)
    before = services.attachments.upload(actor, "before.txt", "text/plain", b"captured")

    stream = services.backup.stream(make_actor())
    first_chunk = next(stream)  # forces VACUUM INTO; the blob walk has not begun

    after = services.attachments.upload(actor, "after.txt", "text/plain", b"raced the snapshot")

    chunks = [first_chunk, *stream]
    out = tmp_path / "out"
    members = _extract(chunks, out)
    snapshot = out / SNAPSHOT_ARCNAME

    ids_in_snapshot = {row[0] for row in _snapshot_query(snapshot, "SELECT id FROM attachments")}
    assert before.id in ids_in_snapshot
    assert after.id not in ids_in_snapshot, (
        "an attachment uploaded after the snapshot leaked into the artifact's database; "
        "the snapshot is not being taken before the blob walk"
    )

    # Every row the snapshot does carry has its bytes in the archive.
    hashes_in_snapshot = {
        row[0] for row in _snapshot_query(snapshot, "SELECT sha256 FROM attachments")
    }
    for sha256 in hashes_in_snapshot:
        arcname = f"{BLOB_ARCNAME_ROOT}/{sha256[:2]}/{sha256}"
        assert arcname in members, f"snapshot references {sha256} but the tar has no blob for it"
        assert hashlib.sha256((out / arcname).read_bytes()).hexdigest() == sha256

    # The later blob rode along unreferenced, which DD-36 calls harmless and hands to
    # the sweep. Asserted so that "harmless" stays a decision rather than an accident.
    assert f"{BLOB_ARCNAME_ROOT}/{after.sha256[:2]}/{after.sha256}" in members


# -------------------------------------------------------- writes stay in flight


def test_backup_does_not_stop_writes(
    services: ServiceBundle, actor: ActorContext, tmp_path: Path
) -> None:
    """FR-P8's "without stopping writes", asserted rather than assumed.

    Records are created on a second thread for the duration of the backup; every one
    must succeed, and the snapshot must be internally consistent (every record row it
    holds resolves to a live object type, and its own count matches what it stores).
    """
    _seed_type(services, actor)
    for i in range(50):
        services.records.create_record(actor, "widget", {"name": f"Seeded {i}"})

    failures: list[BaseException] = []
    written: list[str] = []
    stop = threading.Event()

    def write_until_stopped() -> None:
        i = 0
        while not stop.is_set():
            try:
                row = services.records.create_record(actor, "widget", {"name": f"Concurrent {i}"})
                written.append(row.key)
            except BaseException as exc:  # noqa: BLE001 - the assertion is that none occur
                failures.append(exc)
                return
            i += 1
            time.sleep(0.001)

    writer = threading.Thread(target=write_until_stopped, daemon=True)
    writer.start()
    try:
        chunks = list(services.backup.stream(make_actor()))
    finally:
        stop.set()
        writer.join(timeout=10)

    assert not failures, f"a concurrent write failed during the backup: {failures[0]!r}"
    assert written, "the writer thread never completed a write; the test proved nothing"

    out = tmp_path / "out"
    _extract(chunks, out)
    snapshot = out / SNAPSHOT_ARCNAME
    orphaned = _snapshot_query(
        snapshot,
        "SELECT count(*) FROM records r LEFT JOIN object_types t "
        "ON t.id = r.object_type_id WHERE t.id IS NULL",
    )[0][0]
    assert orphaned == 0
    assert _snapshot_query(snapshot, "SELECT count(*) FROM records")[0][0] >= 50


# ------------------------------------------------------------------- audit row


def test_backup_is_audited_with_attribution(services: ServiceBundle, db: Database) -> None:
    backup_actor = make_actor()
    list(services.backup.stream(backup_actor))

    with db.read() as conn:
        rows = conn.execute(
            text(
                "SELECT principal_id, request_id, entity_type, new_value FROM audit_events "
                "WHERE action = 'backup_taken'"
            )
        ).all()
    assert len(rows) == 1
    principal_id, request_id, entity_type, new_value = rows[0]
    assert principal_id == backup_actor.principal_id
    assert request_id == backup_actor.request_id
    assert entity_type == "backup"
    assert "snapshot_bytes" in new_value


def test_staged_snapshot_is_removed_after_streaming(
    services: ServiceBundle, tmp_path: Path
) -> None:
    list(services.backup.stream(make_actor()))
    staged = list((tmp_path / "backup-tmp").glob("*.sqlite3"))
    assert staged == []


def test_staged_snapshot_is_removed_when_the_client_disconnects(
    services: ServiceBundle, tmp_path: Path
) -> None:
    """A closed generator must still clean up: an abandoned download otherwise leaves
    a full copy of the database on the volume every time someone hits Escape."""
    stream = services.backup.stream(make_actor())
    next(stream)
    assert list((tmp_path / "backup-tmp").glob("*.sqlite3"))
    stream.close()
    assert list((tmp_path / "backup-tmp").glob("*.sqlite3")) == []


# ------------------------------------------------------------- the orphan sweep


def test_sweep_removes_an_unreferenced_blob(services: ServiceBundle, tmp_path: Path) -> None:
    """The restore case DD-36 names: a blob on the volume with no row pointing at it."""
    blobs = tmp_path / "attachments"
    orphan = hashlib.sha256(b"nobody references me").hexdigest()
    (blobs / orphan[:2]).mkdir(parents=True, exist_ok=True)
    (blobs / orphan[:2] / orphan).write_bytes(b"nobody references me")

    result = services.attachments.sweep_orphan_blobs(make_actor())

    assert result["deleted"] == 1
    assert not (blobs / orphan[:2] / orphan).exists()


def test_sweep_keeps_a_blob_while_any_row_still_references_it(
    services: ServiceBundle, actor: ActorContext, db: Database, tmp_path: Path
) -> None:
    """Deduplication makes the refcount check non-negotiable.

    Two uploads of identical content produce two rows and one file. Deleting one row
    must not delete the file, and deleting the last one must. Rows are deleted
    directly here because no attachment-deletion surface exists — the same technique
    the comment suite uses to prove FR-C5 with a second principal.
    """
    content = b"shared between two rows"
    sha256 = hashlib.sha256(content).hexdigest()
    first = services.attachments.upload(actor, "a.txt", "text/plain", content)
    second = services.attachments.upload(actor, "b.txt", "text/plain", content)
    assert first.sha256 == second.sha256 == sha256
    blob_path = tmp_path / "attachments" / sha256[:2] / sha256
    assert blob_path.is_file()

    with db.write() as conn:
        conn.execute(text("DELETE FROM attachments WHERE id = :id"), {"id": first.id})
    assert services.attachments.sweep_orphan_blobs(make_actor())["deleted"] == 0
    assert blob_path.is_file(), "the last referencing row is still there; the bytes must stay"

    with db.write() as conn:
        conn.execute(text("DELETE FROM attachments WHERE id = :id"), {"id": second.id})
    assert services.attachments.sweep_orphan_blobs(make_actor())["deleted"] == 1
    assert not blob_path.exists()


def test_sweep_is_bounded_and_says_so(services: ServiceBundle, tmp_path: Path) -> None:
    blobs = tmp_path / "attachments"
    for i in range(5):
        digest = hashlib.sha256(f"orphan {i}".encode()).hexdigest()
        (blobs / digest[:2]).mkdir(parents=True, exist_ok=True)
        (blobs / digest[:2] / digest).write_bytes(b"x")

    result = services.attachments.sweep_orphan_blobs(make_actor(), limit=2)

    assert result["examined"] == 2
    assert result["deleted"] == 2
    assert result["truncated"] == 1


def test_sweep_of_a_clean_tree_writes_no_audit_row(
    services: ServiceBundle, actor: ActorContext, db: Database
) -> None:
    services.attachments.upload(actor, "kept.txt", "text/plain", b"referenced")
    services.attachments.sweep_orphan_blobs(make_actor())
    with db.read() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM audit_events WHERE action = 'blobs_swept'")
        ).scalar_one()
    assert count == 0


# ------------------------------------------------------------------ over HTTP


def test_backup_endpoint_requires_admin_scope(client: Any, api_tokens: dict[str, str]) -> None:
    from tests.conftest import auth

    assert client.post("/api/v1/admin/backup", headers=auth(api_tokens["write"])).status_code == 403
    response = client.post("/api/v1/admin/backup", headers=auth(api_tokens["admin"]))
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-tar"


def test_backup_endpoint_streams_a_readable_artifact(client: Any, tmp_path: Path) -> None:
    response = client.post("/api/v1/admin/backup")
    assert response.status_code == 200
    members = _extract([response.content], tmp_path / "http-out")
    assert SNAPSHOT_ARCNAME in members


def test_sweep_endpoint_requires_admin_scope(client: Any, api_tokens: dict[str, str]) -> None:
    from tests.conftest import auth

    denied = client.post("/api/v1/admin/blobs/sweep", headers=auth(api_tokens["write"]))
    assert denied.status_code == 403
    allowed = client.post("/api/v1/admin/blobs/sweep", headers=auth(api_tokens["admin"]))
    assert allowed.status_code == 200
    assert set(allowed.json()) == {"examined", "deleted", "limit", "truncated"}


@pytest.mark.parametrize("path", ["/api/v1/admin/backup", "/api/v1/admin/blobs/sweep"])
def test_operator_routes_refuse_an_absent_credential(client: Any, path: str) -> None:
    assert client.post(path, headers={"Authorization": ""}).status_code == 401
