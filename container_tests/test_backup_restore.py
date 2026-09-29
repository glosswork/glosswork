"""The backup/restore container proof (FR-P8, FR-P2; DD-36).

Deliberately outside ``pyproject.toml``'s ``testpaths`` (``["tests"]``), like
``test_network_isolated.py``; run explicitly with ``uv run pytest -q container_tests``.

DD-36's restore procedure, proven end to end here rather than merely documented:
stop the container, replace an *empty* volume's contents with the backup artifact,
start it -- migrations run at startup (FR-P6), and no restore endpoint exists because
one would have to overwrite the database serving it. Three containers on three named
volumes (``docker_support.create_volume``/``create_container``, which -- unlike
``start_container`` -- create without starting, so files can land in ``/data`` before
the application's first boot):

- **Container A** (``backup_source``, module-scoped) seeds an object type with a
  relation field, three records, a link, a comment, and a real uploaded attachment;
  drains the search index; takes a backup through ``POST /api/v1/admin/backup`` while
  a second, concurrent writer keeps writing records of a *different* object type
  (``churn``) on the same container for the "while writes are in flight" clause; and
  records everything the other two containers get checked against, so seeding and
  draining -- the expensive part -- happens exactly once.
  The writer is a single ``docker exec`` running ``_gw_client.py``'s ``loop-write``
  command on its own host-side thread, not a host-side loop making one ``docker
  exec`` per write: the latter's ~100ms process-spawn overhead per write turned out
  to be slower than ``VACUUM INTO`` on this small database, so a host-side loop
  measurably failed to get more than one write into the window (see
  ``run_loop_write``'s docstring in ``docker_support.py``). ``churn`` is a separate
  type from ``memo`` on purpose: it lets every ``memo`` count stay an exact equality
  (DD-36's ordering means a churn write racing the snapshot may legitimately be
  absent from it, so only churn's own count gets a range assertion) rather than
  turning every existing exact assertion into a fuzzier one.
- **Container B** (``restored_container``, module-scoped) is restored from the full
  artifact -- database and blob tree both -- onto a fresh volume, and must be
  equivalent to A on every DD-36 clause: record counts and a spot-checked body, the
  attachment's bytes and hash, the search index status (**with zero pending jobs**,
  the fact that the vector index needed no separate backup mechanism), a hybrid and a
  keyword search each landing the same top hit, an intact audit trail, and -- the one
  clause that is not merely "readable" -- a real write that lands on a fresh,
  non-colliding key.
- **Container C** (``db_only_restored_container``, module-scoped) is DD-36's required
  negative case: restored from the database snapshot *alone*, with the
  ``attachments/`` tree never copied in. Its attachment row survives; its bytes do
  not. Without this container, the blob half of the artifact would be proven only by
  coincidence.

Both restored containers reuse container A's own admin PAT rather than minting a
fresh one: if the restored deployment did not actually carry ``access_tokens`` and
``principals`` along with everything else, every call below would fail with 401
before any equivalence assertion ran, which is itself part of what "equivalence"
here means.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from container_tests import docker_support as ds

READY_TIMEOUT_S = 90
RESTORE_READY_TIMEOUT_S = 120
INDEX_TIMEOUT_S = 120
BACKUP_TIMEOUT_S = 120

# "A handful" for the FR-P8 "without stopping writes" clause: the floor at which the
# test can credibly claim writes really landed *during* the backup call, rather than
# the backup simply having finished before the writer got scheduled.
MIN_CONCURRENT_WRITES = 3

# Where the writer's stop signal lives inside container A. Under /tmp, not /data:
# it is a marker for the writer loop, not deployment content, and must not appear in
# the backup artifact.
CHURN_STOP_MARKER = "/tmp/churn-stop"

# Created by the writer after its first successful write, and waited for before the
# backup starts. Under /tmp for the same reason as the stop marker.
CHURN_WRITING_MARKER = "/tmp/churn-writing"
WRITER_START_TIMEOUT_S = 60

# Mirrors services/backup.py's SNAPSHOT_ARCNAME/BLOB_ARCNAME_ROOT as string literals
# rather than importing them: this module proves the built image from the outside,
# over docker exec and docker cp, and never imports glosswork itself (see
# docker_support.py's module docstring on the same point for _gw_client.py).
SNAPSHOT_ARCNAME = "glosswork.sqlite3"
BLOB_ARCNAME_ROOT = "attachments"

ATTACHMENT_CONTENT = b"attachment payload for the backup-restore container proof"
ATTACHMENT_SHA256 = hashlib.sha256(ATTACHMENT_CONTENT).hexdigest()

MEMO_OBJECT_TYPE: dict[str, Any] = {
    "key": "memo",
    "name": "Memo",
    "name_plural": "Memos",
    "description": "A test record for the backup-restore container proof.",
    "key_prefix": "MEMO",
    "fields": [
        {
            "key": "title",
            "name": "Title",
            "type": "short_text",
            "description": "A short label for the memo (embed defaults to true).",
        },
        {
            "key": "body",
            "name": "Body",
            "type": "long_text",
            "description": "Free text body (embed defaults to true for long_text).",
        },
        {
            "key": "related",
            "name": "Related memo",
            "type": "relation",
            "description": "Another memo this one references.",
            "config": {
                "target_type_key": "memo",
                "cardinality": "one",
                "inverse_field_key": "related_from",
            },
        },
    ],
}

# A second, unrelated object type for the concurrent-write clause. Deliberately not
# ``embed``-eligible (short_text defaults to embed=false, docs/DATA_MODEL.md section
# 3): a churn write that happened to enqueue an embedding job during the backup
# window would race container B's "zero pending jobs" assertion for reasons that have
# nothing to do with this test, so churn content never enters the search index at all.
CHURN_OBJECT_TYPE: dict[str, Any] = {
    "key": "churn",
    "name": "Churn",
    "name_plural": "Churns",
    "description": (
        "Records written concurrently with the backup call, to prove FR-P8's "
        "'without stopping writes' inside the container proof rather than only at "
        "the service layer."
    ),
    "key_prefix": "CHRN",
    "fields": [
        {
            "key": "note",
            "name": "Note",
            "type": "short_text",
            "description": (
                "Which concurrent write produced this record, for spot-checking after a restore."
            ),
        }
    ],
}


def _create_object_type(cid: str, token: str, spec: dict[str, Any]) -> None:
    result = ds.api_call(cid, "POST", "/api/v1/object-types", token=token, body=spec)
    assert result["status"] == 200, result


def _create_record(cid: str, token: str, object_type_key: str, values: dict[str, Any]) -> Any:
    result = ds.api_call(
        cid, "POST", f"/api/v1/object-types/{object_type_key}/records", token=token, body=values
    )
    assert result["status"] == 200, result
    return result["body"]


def _add_comment(cid: str, token: str, ref: str, body_text: str) -> Any:
    result = ds.api_call(
        cid, "POST", f"/api/v1/records/{ref}/comments", token=token, body={"body": body_text}
    )
    assert result["status"] == 200, result
    return result["body"]


def _link_records(cid: str, token: str, ref: str, field_key: str, to_records: list[str]) -> None:
    result = ds.api_call(
        cid,
        "POST",
        f"/api/v1/records/{ref}/links/{field_key}",
        token=token,
        body={"to_records": to_records},
    )
    assert result["status"] == 200, result


def _get_record(cid: str, token: str, ref: str) -> Any:
    result = ds.api_call(cid, "GET", f"/api/v1/records/{ref}", token=token)
    assert result["status"] == 200, result
    return result["body"]


def _query_total_count(cid: str, token: str, object_type_key: str) -> int:
    result = ds.api_call(
        cid, "POST", f"/api/v1/object-types/{object_type_key}/query", token=token, body={}
    )
    assert result["status"] == 200, result
    total_count: int = result["body"]["total_count"]
    return total_count


def _query_records(cid: str, token: str, object_type_key: str) -> list[dict[str, Any]]:
    result = ds.api_call(
        cid, "POST", f"/api/v1/object-types/{object_type_key}/query", token=token, body={}
    )
    assert result["status"] == 200, result
    records: list[dict[str, Any]] = result["body"]["records"]
    return records


def _search_top_hit(cid: str, token: str, query: str, *, mode: str) -> str:
    result = ds.api_call(
        cid, "POST", "/api/v1/search", token=token, body={"query": query, "mode": mode}
    )
    assert result["status"] == 200, result
    results = result["body"]["results"]
    assert results, f"search({query!r}, mode={mode!r}) returned no hits: {result}"
    top_hit: str = results[0]["record_key"]
    return top_hit


def _record_history_event_count(cid: str, token: str, ref: str) -> int:
    result = ds.api_call(cid, "GET", f"/api/v1/records/{ref}/history", token=token)
    assert result["status"] == 200, result
    events: list[Any] = result["body"]["events"]
    return len(events)


def _run_churn_writer(cid: str, token: str, result_holder: list[dict[str, Any]]) -> None:
    """Thread target: one blocking call to :func:`docker_support.run_loop_write`,
    which is itself one ``docker exec`` that writes ``churn`` records in a tight loop
    inside the container until :data:`CHURN_STOP_MARKER` appears. Runs on a host-side
    thread only because the call blocks for as long as the loop does, and the main
    thread needs to get on with taking the backup concurrently; the actual writing
    happens entirely inside the container, not over repeated ``docker exec`` calls
    from here (see the module docstring for why that distinction is load-bearing)."""
    result_holder.append(
        ds.run_loop_write(cid, token, "churn", CHURN_STOP_MARKER, CHURN_WRITING_MARKER)
    )


def _writes_inside(
    writes: list[dict[str, Any]], window_started: float, window_finished: float
) -> list[dict[str, Any]]:
    """The writes that began after the backup call began and committed before it ended.

    Both sides are ``time.monotonic()`` read inside the one container, by the writer
    loop and by ``_gw_client.py``'s ``download``, so they are one clock. A write that
    straddles either edge is not counted: it might have been held until the backup
    finished, which is exactly what FR-P8 says must not happen.
    """
    return [
        write
        for write in writes
        if write["started"] >= window_started and write["finished"] <= window_finished
    ]


# ------------------------------------------------------------------ container A


@pytest.fixture(scope="module")
def backup_source(
    image_tag: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[dict[str, Any]]:
    """Container A: seeds content, drains the index, and takes a backup.

    On a named volume like the two restored containers below, even though nothing
    here depends on that -- for symmetry with them, and so a developer inspecting
    ``docker volume ls`` mid-run sees three volumes for three containers rather than
    two named ones and one anonymous one.
    """
    volume = ds.create_volume(f"gw-backup-a-{uuid.uuid4().hex[:8]}")
    cid = ds.create_container(image_tag, volume=volume, name_prefix="gw-backup-a")
    try:
        ds.start_stopped_container(cid)
        ds.wait_ready(cid, timeout=READY_TIMEOUT_S)
        token = ds.bootstrap_admin(cid)

        _create_object_type(cid, token, MEMO_OBJECT_TYPE)
        primary = _create_record(
            cid,
            token,
            "memo",
            {
                "title": "Quarterly onboarding checklist",
                "body": "The quarterly onboarding checklist mentions a wombat.",
            },
        )
        secondary = _create_record(
            cid,
            token,
            "memo",
            {
                "title": "Payroll notes",
                "body": "Nothing about wombats appears in this payroll memo.",
            },
        )
        filler = _create_record(
            cid,
            token,
            "memo",
            {
                "title": "Filler memo",
                "body": "Just filler content for record counting purposes.",
            },
        )
        _link_records(cid, token, secondary["key"], "related", [primary["key"]])
        comment = _add_comment(
            cid, token, primary["key"], "Follow up needed about the wombat migration timeline."
        )
        uploaded = ds.upload_attachment(cid, token, "notes.txt", "text/plain", ATTACHMENT_CONTENT)
        assert uploaded["status"] == 200, uploaded
        attachment = uploaded["body"]
        assert attachment["sha256"] == ATTACHMENT_SHA256, attachment

        status = ds.wait_for_drain(cid, token, timeout=INDEX_TIMEOUT_S)
        assert status["failed_jobs"] == [], status

        record_count = _query_total_count(cid, token, "memo")
        assert record_count == 3, record_count
        hybrid_top_hit = _search_top_hit(cid, token, "wombat migration timeline", mode="hybrid")
        keyword_top_hit = _search_top_hit(cid, token, "wombat migration timeline", mode="keyword")
        assert hybrid_top_hit == keyword_top_hit == primary["key"], (
            hybrid_top_hit,
            keyword_top_hit,
        )
        primary_history_count = _record_history_event_count(cid, token, primary["key"])
        assert primary_history_count > 0, "seeding produced no audit events for primary"

        # FR-P8: the backup is taken "while writes are in
        # flight", not against a quiet deployment. A second object type absorbs the
        # churn (see CHURN_OBJECT_TYPE's comment) so every memo-count assertion above
        # and below stays an exact equality. The writer (see _run_churn_writer and
        # docker_support.run_loop_write) is one docker exec running a tight loop
        # inside the container, not a host-side loop making one docker exec per
        # write -- the latter measurably could not keep pace with this backup (see
        # the module docstring).
        #
        # The backup starts only once the writer has announced its first successful
        # write, and only the writes that began and committed inside the backup call
        # are counted. Starting both at once made this a race between two docker exec
        # start-ups, which a fast runner lost with zero writes (release dry run
        # 36554918375); seeding more content would only have moved that race.
        _create_object_type(cid, token, CHURN_OBJECT_TYPE)
        writer_result: list[dict[str, Any]] = []
        writer = threading.Thread(
            target=_run_churn_writer, args=(cid, token, writer_result), daemon=True
        )
        writer.start()
        try:
            ds.wait_for_path(cid, CHURN_WRITING_MARKER, timeout=WRITER_START_TIMEOUT_S)
            host_tar = tmp_path_factory.mktemp("backup") / "backup.tar"
            summary = ds.download_to_container(
                cid,
                "POST",
                "/api/v1/admin/backup",
                token,
                "/tmp/backup.tar",
                timeout=BACKUP_TIMEOUT_S,
            )
        finally:
            ds.touch_in_container(cid, CHURN_STOP_MARKER)
            writer.join(timeout=30)
        assert summary["status"] == 200, summary
        ds.copy_out(cid, "/tmp/backup.tar", host_tar)

        assert writer_result, "the churn writer thread produced no result at all"
        loop_summary = writer_result[0]
        assert loop_summary["failed"] is None, (
            f"a concurrent write failed during the backup: {loop_summary['failed']!r}"
        )
        churn_records = loop_summary["succeeded"]
        inside = _writes_inside(churn_records, summary["started"], summary["finished"])
        backup_s = summary["finished"] - summary["started"]
        measured = {
            "backup_s": round(backup_s, 3),
            "writes_inside": len(inside),
            "writes_total": len(churn_records),
        }
        print(f"\n[backup] {json.dumps(measured)}")
        assert len(inside) >= MIN_CONCURRENT_WRITES, (
            f"only {len(inside)} of the writer's {len(churn_records)} write(s) began and "
            f"committed inside the {backup_s:.3f}s backup call; this proves nothing about "
            f"FR-P8's 'without stopping writes' clause. The writer was already writing "
            f"when the backup started, so a count this low means the backup held writes "
            f"back, not that the writer was late."
        )

        yield {
            "cid": cid,
            "token": token,
            "tar_path": host_tar,
            "primary": primary,
            "secondary": secondary,
            "filler": filler,
            "comment": comment,
            "attachment_id": attachment["id"],
            "attachment_sha256": attachment["sha256"],
            "indexed_chunks": status["indexed_chunks"],
            "record_count": record_count,
            "allocated_keys": {primary["key"], secondary["key"], filler["key"]},
            "hybrid_top_hit": hybrid_top_hit,
            "keyword_top_hit": keyword_top_hit,
            "primary_history_count": primary_history_count,
            "churn_records": churn_records,
        }
    finally:
        ds.remove_container(cid)
        ds.remove_volume(volume)


# ------------------------------------------------------------- host-side artifact


def test_artifact_member_order_is_database_first_blob_tree_second(
    backup_source: dict[str, Any],
) -> None:
    """DD-36's normative ordering, read back from the tar exactly as an operator
    (or a restore procedure) would see it: the database snapshot is the first
    member, and every member after it lives under ``attachments/``."""
    with tarfile.open(backup_source["tar_path"]) as tar:
        names = [member.name for member in tar.getmembers()]
    assert names, "backup artifact has no members"
    assert names[0] == SNAPSHOT_ARCNAME, names
    assert all(name.startswith(f"{BLOB_ARCNAME_ROOT}/") for name in names[1:]), names
    assert f"{BLOB_ARCNAME_ROOT}/{ATTACHMENT_SHA256[:2]}/{ATTACHMENT_SHA256}" in names, names


# ------------------------------------------------------------------ container B


@pytest.fixture(scope="module")
def restored_container(
    image_tag: str, backup_source: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[str, str]]:
    """Container B: a fresh, empty volume restored from ``backup_source``'s artifact
    in full -- database and blob tree both -- then booted, exactly as DD-36's
    operator procedure describes it."""
    extracted = tmp_path_factory.mktemp("restore-b")
    with tarfile.open(backup_source["tar_path"]) as tar:
        tar.extractall(extracted, filter="data")

    volume = ds.create_volume(f"gw-restore-b-{uuid.uuid4().hex[:8]}")
    cid: str | None = None
    try:
        ds.seed_data_volume(image_tag, volume, extracted)
        cid = ds.create_container(image_tag, volume=volume, name_prefix="gw-restore-b")
        ds.start_stopped_container(cid)
        ds.wait_ready(cid, timeout=RESTORE_READY_TIMEOUT_S)
        yield cid, backup_source["token"]
    finally:
        if cid is not None:
            ds.remove_container(cid)
        ds.remove_volume(volume)


def test_restored_deployment_is_equivalent_to_the_source(
    backup_source: dict[str, Any], restored_container: tuple[str, str]
) -> None:
    """Every DD-36 equivalence clause, in one test against one restored container:
    splitting these across separate tests would multiply the container's expensive
    startup cost for no independent information, since each clause only means
    anything read against the same restore."""
    cid, token = restored_container

    # Record counts match, and one spot-checked body is identical.
    assert _query_total_count(cid, token, "memo") == backup_source["record_count"]
    restored_primary = _get_record(cid, token, backup_source["primary"]["key"])
    assert restored_primary["data"]["body"] == backup_source["primary"]["data"]["body"]
    assert restored_primary["data"]["title"] == backup_source["primary"]["data"]["title"]

    # The attachment downloads, and its sha256 matches.
    out_path = f"/tmp/downloaded-{backup_source['attachment_id']}.bin"
    download = ds.download_to_container(
        cid,
        "GET",
        f"/api/v1/attachments/{backup_source['attachment_id']}/download",
        token,
        out_path,
    )
    assert download["status"] == 200, download
    assert download["bytes"] == len(ATTACHMENT_CONTENT), download
    assert download["sha256"] == backup_source["attachment_sha256"], download

    # The search index status reports the same indexed_chunks with zero pending
    # jobs -- the single fact the whole restore story rests on (DD-36): the vector
    # index rode along in the snapshot and needed no re-index.
    status = ds.api_call(cid, "GET", "/api/v1/admin/search-index", token=token)
    assert status["status"] == 200, status
    index_status = status["body"]
    assert index_status["pending_jobs"] == 0, index_status
    assert index_status["indexed_chunks"] == backup_source["indexed_chunks"], index_status

    # A hybrid search and a keyword search each return the same top hit as on A.
    assert (
        _search_top_hit(cid, token, "wombat migration timeline", mode="hybrid")
        == backup_source["hybrid_top_hit"]
    )
    assert (
        _search_top_hit(cid, token, "wombat migration timeline", mode="keyword")
        == backup_source["keyword_top_hit"]
    )

    # The known record's audit trail is intact.
    assert (
        _record_history_event_count(cid, token, backup_source["primary"]["key"])
        == backup_source["primary_history_count"]
    )

    # A write succeeds, and its key does not collide with anything A allocated --
    # this is what distinguishes a deployment that is genuinely working from one
    # that is merely readable, since it exercises key allocation and migration
    # state (DD-36).
    written = _create_record(
        cid,
        token,
        "memo",
        {"title": "Post-restore record", "body": "Created after restore to prove the write path."},
    )
    assert written["key"] not in backup_source["allocated_keys"], written
    assert _query_total_count(cid, token, "memo") == backup_source["record_count"] + 1

    # The FR-P8 concurrent-writer clause: unlike every memo assertion above, this is
    # deliberately a range, not an equality. DD-36's ordering (database-first) means
    # the snapshot was taken partway through the writer's loop, so a churn write that
    # landed after the snapshot legitimately has no row in the restore -- the honest
    # claim is that the restore has *some prefix* of what was written, never more,
    # and that whatever prefix it has reads back correctly.
    restored_churn = _query_records(cid, token, "churn")
    written_churn = backup_source["churn_records"]  # [{"key", "note"}, ...]
    assert 0 <= len(restored_churn) <= len(written_churn), (len(restored_churn), len(written_churn))
    expected_notes_by_key = {record["key"]: record["note"] for record in written_churn}
    for record in restored_churn:
        assert record["key"] in expected_notes_by_key, record
        assert record["data"]["note"] == expected_notes_by_key[record["key"]], record


# ------------------------------------------------------------------ container C


@pytest.fixture(scope="module")
def db_only_restored_container(
    image_tag: str, backup_source: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[str, str]]:
    """Container C: DD-36's required negative case. Restored from the database
    snapshot *alone* -- the ``attachments/`` tree is never extracted, let alone
    copied in -- so every ``attachments`` row survives the restore with no bytes
    behind it anywhere on the volume."""
    extracted = tmp_path_factory.mktemp("restore-c")
    with tarfile.open(backup_source["tar_path"]) as tar:
        member = tar.getmember(SNAPSHOT_ARCNAME)
        tar.extract(member, path=extracted, filter="data")
    assert not (extracted / BLOB_ARCNAME_ROOT).exists(), (
        "the negative case must not extract the blob tree at all"
    )

    volume = ds.create_volume(f"gw-restore-c-{uuid.uuid4().hex[:8]}")
    cid: str | None = None
    try:
        ds.seed_data_volume(image_tag, volume, extracted)
        cid = ds.create_container(image_tag, volume=volume, name_prefix="gw-restore-c")
        ds.start_stopped_container(cid)
        ds.wait_ready(cid, timeout=RESTORE_READY_TIMEOUT_S)
        yield cid, backup_source["token"]
    finally:
        if cid is not None:
            ds.remove_container(cid)
        ds.remove_volume(volume)


def test_restoring_the_database_without_the_blob_tree_fails_the_attachment_download(
    backup_source: dict[str, Any],
    db_only_restored_container: tuple[str, str],
    tmp_path: Path,
) -> None:
    """DD-36's negative case. Without it, the blob half of the backup artifact would
    be proven only by coincidence: every assertion above could pass even if the
    backup silently dropped the blob tree, as long as nothing ever checked that the
    tree was the thing making the download work.

    The record assertions still pass -- the row, the schema, and the rest of the
    database are intact -- but the attachment download fails with the *specific*
    failure a missing blob produces (``not_found`` / ``"attachment blob"``), not a
    generic error. ``repositories/blobs.py``'s ``open_stream`` opens the file eagerly
    for exactly this reason: a lazily-raised error there would corrupt an
    already-started 200 response instead of producing this clean 404 (see the unit
    tests in ``tests/test_attachments.py`` and
    ``tests/test_api_attachments.py``).
    """
    cid, token = db_only_restored_container

    # The record assertions still pass: only the blobs are gone.
    assert _query_total_count(cid, token, "memo") == backup_source["record_count"]
    restored_primary = _get_record(cid, token, backup_source["primary"]["key"])
    assert restored_primary["data"]["body"] == backup_source["primary"]["data"]["body"]

    out_path = f"/tmp/downloaded-{backup_source['attachment_id']}.bin"
    download = ds.download_to_container(
        cid,
        "GET",
        f"/api/v1/attachments/{backup_source['attachment_id']}/download",
        token,
        out_path,
    )
    assert download["status"] == 404, download

    # Assert the *specific* failure, not merely that something went wrong: the raw
    # error body (written verbatim inside the container by _gw_client.py's
    # ``download`` command) names the missing blob, not the missing row -- proving
    # the row survived the restore and only the bytes did not.
    body_path = tmp_path / "download.json"
    ds.copy_out(cid, out_path, body_path)
    error = json.loads(body_path.read_text())["error"]
    assert error["code"] == "not_found", error
    assert error["details"]["entity"] == "attachment blob", error
