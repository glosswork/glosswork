"""Attachment storage (docs/DATA_MODEL.md section 8).
Service-layer coverage of content-hash dedup and max_bytes enforcement; the
upload/download HTTP routes are covered separately."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text

from glosswork.actor import ActorContext, Level, Scope
from glosswork.db import Database
from glosswork.errors import ForbiddenError, NotFoundError, ValidationFailedError
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.services import ServiceBundle
from tests.conftest import make_actor

SinkType = tuple[ObjectType, dict[str, FieldDef]]


class TestAttachmentUploadAndDownload:
    def test_upload_then_download_round_trips_bytes_and_metadata(
        self, services: ServiceBundle
    ) -> None:
        row = services.attachments.upload(make_actor(), "notes.txt", "text/plain", b"hello world")
        fetched, content = services.attachments.download(make_actor(), row.id)
        assert fetched.filename == "notes.txt"
        assert fetched.content_type == "text/plain"
        assert fetched.byte_size == len(b"hello world")
        assert content == b"hello world"

    def test_identical_content_uploaded_twice_produces_two_rows_one_file(
        self, services: ServiceBundle
    ) -> None:
        first = services.attachments.upload(make_actor(), "a.txt", "text/plain", b"same bytes")
        second = services.attachments.upload(make_actor(), "b.txt", "text/plain", b"same bytes")
        assert first.id != second.id
        assert first.sha256 == second.sha256
        # One underlying blob file backs both rows.
        blob_path = services.attachments._blobs._path_for(first.sha256)  # type: ignore[attr-defined]
        assert blob_path.exists()
        _, first_bytes = services.attachments.download(make_actor(), first.id)
        _, second_bytes = services.attachments.download(make_actor(), second.id)
        assert first_bytes == second_bytes == b"same bytes"

    def test_stream_download_of_a_row_whose_bytes_are_missing_raises_before_any_chunk(
        self, services: ServiceBundle
    ) -> None:
        """Regression for the backup-restore negative case
        (``container_tests/test_backup_restore.py``): an ``attachments`` row can
        outlive its blob file on disk (there, because a restore was taken without the
        blob tree). ``stream_download`` must raise ``NotFoundError`` synchronously
        when called, not only once the caller pulls the first chunk -- the download
        route builds its ``StreamingResponse`` around whatever it gets back, and by
        the time a chunk is first pulled a 200 status line has already gone out over
        the wire, so a lazily-raised error there corrupts the response instead of
        producing a clean 404."""
        row = services.attachments.upload(make_actor(), "notes.txt", "text/plain", b"hello world")
        blob_path = services.attachments._blobs._path_for(row.sha256)  # type: ignore[attr-defined]
        blob_path.unlink()

        with pytest.raises(NotFoundError) as excinfo:
            services.attachments.stream_download(
                make_actor(), row.id
            )  # raises before returning an iterator
        assert excinfo.value.details["entity"] == "attachment blob"


class TestAttachmentMaxBytes:
    def test_writing_an_attachment_field_enforces_max_bytes(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        services.schema.update_field(
            make_actor(), "artifact", "files", {"config": {"max_bytes": 10}}
        )
        small = services.attachments.upload(make_actor(), "s.txt", "text/plain", b"12345")
        big = services.attachments.upload(make_actor(), "b.txt", "text/plain", b"1234567890123")

        services.records.create_record(
            make_actor(), "artifact", {"title": "ok", "files": [small.id]}
        )
        try:
            services.records.create_record(
                make_actor(), "artifact", {"title": "too big", "files": [big.id]}
            )
        except ValidationFailedError as exc:
            assert "max_bytes" in exc.message
        else:
            raise AssertionError("expected ValidationFailedError")

    def test_max_files_still_enforced_by_the_base_field_validator(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        services.schema.update_field(
            make_actor(), "artifact", "files", {"config": {"max_files": 1}}
        )
        a = services.attachments.upload(make_actor(), "a.txt", "text/plain", b"a")
        b = services.attachments.upload(make_actor(), "b.txt", "text/plain", b"b")
        try:
            services.records.create_record(
                make_actor(), "artifact", {"title": "x", "files": [a.id, b.id]}
            )
        except ValidationFailedError as exc:
            assert "at most 1" in exc.message
        else:
            raise AssertionError("expected ValidationFailedError")


# ------------------------------- a reference is authorized when written


def actor_for(principal_id: str, scope: Scope = "write") -> ActorContext:
    return ActorContext(
        principal_id=principal_id,
        principal_type="user",
        agent_label_id=None,
        auth_method="pat",
        surface="api",
        request_id=str(uuid.uuid4()),
        scope=scope,
    )


def member(services: ServiceBundle, email: str) -> str:
    return services.principals.create_user(
        make_actor(),
        email=email,
        display_name=email,
        role="member",
        password="correct-horse-battery-staple",
    ).id


def grant(services: ServiceBundle, key: str, principal_id: str, level: Level) -> None:
    services.access.grant(make_actor(), key, principal_id, level)


def attach_type(services: ServiceBundle, key: str, prefix: str) -> ObjectType:
    created: ObjectType = services.schema.create_object_type(
        make_actor(),
        key=key,
        name=key.title(),
        name_plural=key.title() + "s",
        description=f"A {key} with a file field, for the attachment write-side rule.",
        key_prefix=prefix,
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "What this is called, shown wherever it is listed.",
            },
            {
                "key": "files",
                "name": "Files",
                "type": "attachment",
                "description": "Documents supporting this record, uploaded by anyone.",
            },
        ],
    )
    return created


@dataclass(frozen=True)
class Escalation:
    """A security review's probe, seeded: an attachment referenced only from ``secret``,
    and a writer who holds ``write`` on ``open_type`` and nothing at all on ``secret``."""

    attachment_id: str
    writer: ActorContext
    writer_id: str


@pytest.fixture
def escalation(services: ServiceBundle) -> Escalation:
    attach_type(services, "secret", "SEC")
    attach_type(services, "open_type", "OPN")
    uploaded = services.attachments.upload(make_actor(), "payroll.txt", "text/plain", b"salaries")
    services.records.create_record(
        make_actor(), "secret", {"title": "Compensation review", "files": [uploaded.id]}
    )
    writer_id = member(services, "writer@example.com")
    grant(services, "open_type", writer_id, "write")
    return Escalation(uploaded.id, actor_for(writer_id, "write"), writer_id)


def join_rows(db: Database, attachment_id: str) -> int:
    with db.read() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM record_attachments WHERE attachment_id = :a"),
                {"a": attachment_id},
            ).scalar()
            or 0
        )


@pytest.mark.parametrize("path", ["create", "update", "bulk_update"])
def test_referencing_an_unreadable_attachment_is_refused_on_every_write_path(
    services: ServiceBundle, db: Database, escalation: Escalation, path: str
) -> None:
    """The escalation a security review reproduced, refused per write path.

    A write funnel that validated attachment ids "only for shape" let the join
    row the write itself caused to exist make the subsequent download succeed:
    referencing an attachment was how a caller manufactured the right to read it.
    """
    before = join_rows(db, escalation.attachment_id)
    with pytest.raises(ForbiddenError) as excinfo:
        if path == "create":
            services.records.create_record(
                escalation.writer,
                "open_type",
                {"title": "Borrowed", "files": [escalation.attachment_id]},
            )
        else:
            record = services.records.create_record(
                escalation.writer, "open_type", {"title": "Borrowed"}
            )
            if path == "update":
                services.records.update_record(
                    escalation.writer, record.key, {"files": [escalation.attachment_id]}
                )
            else:
                services.records.bulk_update(
                    escalation.writer, "open_type", {"files": [escalation.attachment_id]}
                )
    assert escalation.attachment_id in excinfo.value.message

    # No row was written, so the download is still refused: the escalation is closed at
    # its source rather than patched at the read.
    assert join_rows(db, escalation.attachment_id) == before
    with pytest.raises(ForbiddenError):
        services.attachments.download(escalation.writer, escalation.attachment_id)


def test_csv_import_cannot_introduce_an_attachment_reference(
    services: ServiceBundle, db: Database, escalation: Escalation
) -> None:
    """The CSV path, proved rather than assumed. CSV declares attachment columns out of
    scope, so the escalation is unreachable there, and the security property holds: no
    borrowed id reaches ``records.data`` through an import.

    **The how changed, not the whether.** This test once asserted that the import
    *succeeded* and dropped the value, and its note said a change that let CSV carry an
    attachment column must break it. It broke in the other direction: the column is
    refused at the header instead of accepted and silently emptied, because
    ``errors: []`` beside a vanished id is the system reporting something it did not do.
    ``_build_row``'s skip is still there underneath as the funnel-level guarantee; this
    now asserts the refusal in front of it, plus the two properties that were always the
    point."""
    with pytest.raises(ValidationFailedError) as refusal:
        services.csv.import_csv(
            escalation.writer,
            "open_type",
            f"title,files\nBorrowed,{escalation.attachment_id}\n",
            mode="create",
        )
    assert refusal.value.details["columns"] == ["files"]

    # Nothing was written, and the borrowed attachment gained no
    # reference it did not already have.
    assert services.records.query_records(escalation.writer, "open_type").total_count == 0
    assert join_rows(db, escalation.attachment_id) == 1

    services.csv.import_csv(
        make_actor(),
        "secret",
        "key,title\nSEC-1,Compensation review v2\n",
        mode="upsert",
        upsert_key="key",
    )
    assert join_rows(db, escalation.attachment_id) == 1


def test_the_uploader_may_reference_its_own_unattached_upload(services: ServiceBundle) -> None:
    """The uploader clause of the attachment rule, which is the case that must keep working:
    between the upload and the record write there is no referencing row at all, so nothing
    but ``uploaded_by`` can authorize the first reference."""
    attach_type(services, "open_type", "OPN")
    writer_id = member(services, "selfserve@example.com")
    grant(services, "open_type", writer_id, "write")
    writer = actor_for(writer_id, "write")
    uploaded = services.attachments.upload(writer, "mine.txt", "text/plain", b"mine")
    record = services.records.create_record(
        writer, "open_type", {"title": "Mine", "files": [uploaded.id]}
    )
    assert services.records.get_record(writer, record.key).data["files"] == [uploaded.id]


def test_an_id_that_resolves_to_nothing_is_still_stored_and_authorizes_nothing(
    services: ServiceBundle, db: Database
) -> None:
    """An attachment field value is an opaque id validated for shape. The write-side
    check applies to ids that **resolve**, which are the only ones the attachment rule
    can be fooled by."""
    attach_type(services, "open_type", "OPN")
    writer_id = member(services, "opaque@example.com")
    grant(services, "open_type", writer_id, "write")
    writer = actor_for(writer_id, "write")
    dangling = str(uuid.uuid4())
    record = services.records.create_record(
        writer, "open_type", {"title": "Dangling", "files": [dangling]}
    )
    assert services.records.get_record(writer, record.key).data["files"] == [dangling]
    assert join_rows(db, dangling) == 0


def test_read_on_the_referencing_type_is_enough_to_reference_it_elsewhere(
    services: ServiceBundle, escalation: Escalation
) -> None:
    """The rule is the attachment read rule: ``read`` on **any** referencing type. A writer that
    can already see the attachment through ``secret`` may reference it from
    ``open_type``."""
    grant(services, "secret", escalation.writer_id, "read")
    record = services.records.create_record(
        escalation.writer, "open_type", {"title": "Borrowed", "files": [escalation.attachment_id]}
    )
    assert record.data["files"] == [escalation.attachment_id]


def test_resubmitting_an_id_already_stored_on_the_record_still_succeeds(
    services: ServiceBundle, escalation: Escalation
) -> None:
    """The record's own type is a referencing type once the reference exists, so a
    writer who can read that type can keep re-submitting the value it already holds."""
    grant(services, "secret", escalation.writer_id, "read")
    record = services.records.create_record(
        escalation.writer, "open_type", {"title": "Borrowed", "files": [escalation.attachment_id]}
    )
    services.access.grant(make_actor(), "secret", escalation.writer_id, "none")
    updated = services.records.update_record(
        escalation.writer, record.key, {"files": [escalation.attachment_id], "title": "Renamed"}
    )
    assert updated.data["files"] == [escalation.attachment_id]


def test_an_upload_handed_between_principals_is_refused(services: ServiceBundle) -> None:
    """One of the two accepted refusals. U1 uploads and U2 writes the
    record: U2 is neither the uploader nor a reader of any referencing type, because
    there are none yet. U1 must make the first reference."""
    attach_type(services, "open_type", "OPN")
    u1 = actor_for(member(services, "u1@example.com"), "write")
    u2_id = member(services, "u2@example.com")
    grant(services, "open_type", u2_id, "write")
    u2 = actor_for(u2_id, "write")
    uploaded = services.attachments.upload(u1, "handed.txt", "text/plain", b"handed")

    with pytest.raises(ForbiddenError):
        services.records.create_record(
            u2, "open_type", {"title": "Handed over", "files": [uploaded.id]}
        )


@pytest.mark.parametrize("path", ["revert_field_change", "revert_to_version"])
def test_a_revert_restoring_an_unreadable_attachment_is_refused(
    services: ServiceBundle, db: Database, path: str
) -> None:
    """The second accepted refusal. A revert re-writes the field through the same
    funnel, so restoring a reference the reverting principal cannot read fails rather
    than re-manufacturing the row."""
    attach_type(services, "secret", "SEC")
    attach_type(services, "open_type", "OPN")
    owner_id = member(services, "owner@example.com")
    grant(services, "open_type", owner_id, "write")
    grant(services, "secret", owner_id, "write")
    owner = actor_for(owner_id, "write")
    uploaded = services.attachments.upload(owner, "shared.txt", "text/plain", b"shared")
    services.records.create_record(owner, "secret", {"title": "Held", "files": [uploaded.id]})
    record = services.records.create_record(
        owner, "open_type", {"title": "Held too", "files": [uploaded.id]}
    )
    services.records.update_record(owner, record.key, {"files": []})

    reverter_id = member(services, "reverter@example.com")
    grant(services, "open_type", reverter_id, "write")
    reverter = actor_for(reverter_id, "write")
    current = services.records.get_record(reverter, record.key)

    with pytest.raises(ForbiddenError):
        if path == "revert_field_change":
            services.records.revert_field_change(
                reverter, _last_files_event_id(db, record.id), current.version
            )
        else:
            services.records.revert_to_version(
                reverter, record.key, target_version=1, expected_version=current.version
            )
    assert services.records.get_record(reverter, record.key).data.get("files") in (None, [])


def _last_files_event_id(db: Database, record_id: str) -> int:
    with db.read() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT id FROM audit_events WHERE entity_id = :r AND field_key = 'files' "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"r": record_id},
            ).scalar()
        )


def test_revocation_does_not_reach_back_but_does_stop_the_next_reference(
    services: ServiceBundle, escalation: Escalation
) -> None:
    """The accepted consequence, stated end to end (DD-12).

    Revoking ``read`` on ``secret`` does not delete the ``record_attachments`` row an
    earlier write established, so an attachment already referenced from ``open_type``
    stays readable through *that* reference -- the attachment read rule is unchanged,
    and revocation is deliberately not retroactive. The consequence runs one step
    further than it first appears: because readability is "``read`` on **any**
    referencing type", ``open_type`` is now itself a referencing type, so this writer
    may go on referencing that attachment freely. Making that untrue would mean deleting
    join rows on revoke, which would change DD-11.

    What revocation does stop is a reference to an attachment the writer had not
    already referenced -- the whole of the rest of ``secret``'s files.
    """
    second = services.attachments.upload(make_actor(), "bonuses.txt", "text/plain", b"bonuses")
    services.records.create_record(
        make_actor(), "secret", {"title": "Bonus pool", "files": [second.id]}
    )
    grant(services, "secret", escalation.writer_id, "read")
    services.records.create_record(
        escalation.writer, "open_type", {"title": "Borrowed", "files": [escalation.attachment_id]}
    )
    services.access.grant(make_actor(), "secret", escalation.writer_id, "none")

    # Not retroactive, and knowingly so.
    assert (
        services.attachments.download(escalation.writer, escalation.attachment_id)[1] == b"salaries"
    )
    assert services.records.create_record(
        escalation.writer,
        "open_type",
        {"title": "Borrowed again", "files": [escalation.attachment_id]},
    ).data["files"] == [escalation.attachment_id]

    # ...but the attachment it never referenced is closed to it in both directions.
    with pytest.raises(ForbiddenError):
        services.attachments.download(escalation.writer, second.id)
    with pytest.raises(ForbiddenError):
        services.records.create_record(
            escalation.writer, "open_type", {"title": "Reaching", "files": [second.id]}
        )
