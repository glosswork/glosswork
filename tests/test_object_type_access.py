"""Per-type access control, end to end.

Enforcement path by path, then the two feeds, search, relation redaction,
``your_access``, the grant surfaces, and cross-surface parity.
"""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from glosswork.actor import ActorContext, Level, Scope
from glosswork.db import Database
from glosswork.errors import ForbiddenError, InsufficientScopeError
from glosswork.services import ServiceBundle
from glosswork.services.csv import CsvService
from tests.conftest import make_actor

PASSWORD = "correct-horse-battery-staple"


# --------------------------------------------------------------------- fixtures


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


def seed_type(services: ServiceBundle, key: str, prefix: str, **fields: str) -> Any:
    return services.schema.create_object_type(
        make_actor(),
        key=key,
        name=key.title(),
        name_plural=key.title() + "s",
        description=f"A {key}, seeded for the access-control suite.",
        key_prefix=prefix,
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "What this is called, shown wherever it is listed.",
            }
        ],
    )


def member(services: ServiceBundle, email: str = "m@example.com") -> str:
    return services.principals.create_user(
        make_actor(), email=email, display_name=email, role="member", password=PASSWORD
    ).id


def grant(services: ServiceBundle, key: str, principal_id: str, level: Level) -> None:
    services.access.grant(make_actor(), key, principal_id, level)


@pytest.fixture
def two_types(services: ServiceBundle) -> tuple[Any, Any]:
    """``initiative`` and ``decision``, both closed by default (there is no backfill)."""
    return seed_type(services, "initiative", "INI"), seed_type(services, "decision", "DEC")


# ------------------------------------------------------------------------ the model


def test_no_grant_omits_the_type_and_refuses_a_query(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    """A `member` with a `write` PAT and no grant on `decision`, with
    `decision.default_level = 'none'`."""
    who = actor_for(member(services), "write")
    assert [t.key for t in services.schema.list_object_types(who)] == []

    with pytest.raises(ForbiddenError) as excinfo:
        services.records.query_records(who, "decision")
    assert excinfo.value.details == {
        "object_type": "decision",
        "held": "none",
        "required": "read",
    }
    assert "decision" in excinfo.value.message


def test_a_read_grant_shows_the_type_and_refuses_a_write(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    principal_id = member(services)
    grant(services, "decision", principal_id, "read")
    who = actor_for(principal_id, "write")

    assert [t.key for t in services.schema.list_object_types(who)] == ["decision"]
    assert services.schema.your_access(who, "decision") == "read"
    assert services.records.query_records(who, "decision").total_count == 0
    with pytest.raises(ForbiddenError) as excinfo:
        services.records.create_record(who, "decision", {"title": "Nope"})
    assert excinfo.value.details["required"] == "write"


def test_the_ceiling_holds_over_an_admin_grant(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    """A principal granted `admin` on `decision` holding a `read` PAT: `your_access` is
    `read` and `add_field` is `insufficient_scope`, not `forbidden`."""
    principal_id = member(services)
    grant(services, "decision", principal_id, "admin")
    who = actor_for(principal_id, "read")

    assert services.schema.your_access(who, "decision") == "read"
    with pytest.raises(InsufficientScopeError) as excinfo:
        services.schema.add_field(
            who,
            "decision",
            {
                "key": "rationale",
                "name": "Rationale",
                "type": "long_text",
                "description": "Why this decision was taken, for whoever reads it later.",
            },
        )
    assert excinfo.value.code == "insufficient_scope"


# ------------------------------------------------------------ enforcement by path


def test_schema_reads_omit_and_refuse(services: ServiceBundle, two_types: tuple[Any, Any]) -> None:
    principal_id = member(services)
    grant(services, "initiative", principal_id, "read")
    who = actor_for(principal_id, "write")

    assert [t.key for t in services.schema.list_object_types(who)] == ["initiative"]
    services.schema.get_object_type(who, "initiative")
    with pytest.raises(ForbiddenError):
        services.schema.get_object_type(who, "decision")


def test_record_reads_are_gated_on_the_records_type(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    record = services.records.create_record(make_actor(), "decision", {"title": "Ship it"})
    who = actor_for(member(services), "write")
    for call in (
        lambda: services.records.get_record(who, record.key),
        lambda: services.records.get_record_history(who, record.key),
        lambda: services.records.get_record_history_page(who, record.key),
        lambda: services.records.sample_values(who, "decision"),
        lambda: services.records.get_record_expansions(who, record.key, []),
        lambda: services.records.query_records(who, "decision"),
    ):
        with pytest.raises(ForbiddenError):
            call()


def test_record_writes_need_write_not_read(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    record = services.records.create_record(make_actor(), "decision", {"title": "Ship it"})
    principal_id = member(services)
    grant(services, "decision", principal_id, "read")
    who = actor_for(principal_id, "write")

    services.records.get_record(who, record.key)  # read is enough to read
    for call in (
        lambda: services.records.create_record(who, "decision", {"title": "New"}),
        lambda: services.records.update_record(who, record.key, {"title": "Edited"}),
        lambda: services.records.delete_record(who, record.key),
        lambda: services.records.bulk_update(who, "decision", {"title": "Bulk"}),
    ):
        with pytest.raises(ForbiddenError):
            call()


def test_restore_needs_write(services: ServiceBundle, two_types: tuple[Any, Any]) -> None:
    record = services.records.create_record(make_actor(), "decision", {"title": "Gone"})
    services.records.delete_record(make_actor(), record.key)
    principal_id = member(services)
    grant(services, "decision", principal_id, "read")
    with pytest.raises(ForbiddenError):
        services.records.restore_record(actor_for(principal_id, "write"), record.key)


def test_bulk_update_dry_run_is_refused_too(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    """A dry run reads which records *would* be affected, so it is a read of the type
    at least, and it is gated as the write it is a rehearsal for."""
    services.records.create_record(make_actor(), "decision", {"title": "One"})
    principal_id = member(services)
    grant(services, "decision", principal_id, "read")
    with pytest.raises(ForbiddenError):
        services.records.bulk_update(
            actor_for(principal_id, "write"), "decision", {"title": "X"}, dry_run=True
        )


def test_linking_needs_write_on_the_source_and_read_on_the_target(
    services: ServiceBundle,
) -> None:
    services.schema.create_object_type(
        make_actor(),
        key="task",
        name="Task",
        name_plural="Tasks",
        description="A unit of work, the source side of the relation under test.",
        key_prefix="TSK",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "What the task is, shown in every listing.",
            }
        ],
    )
    seed_type(services, "decision", "DEC")
    services.schema.add_field(
        make_actor(),
        "task",
        {
            "key": "decided_by",
            "name": "Authorised by",
            "type": "relation",
            "description": "The decision that authorised this task.",
            "config": {"target_type_key": "decision", "cardinality": "many"},
        },
    )
    task = services.records.create_record(make_actor(), "task", {"title": "Do it"})
    decision = services.records.create_record(make_actor(), "decision", {"title": "Approved"})

    principal_id = member(services)
    grant(services, "task", principal_id, "write")
    who = actor_for(principal_id, "write")

    # write on the source is not enough: you may not link into a type you cannot see.
    with pytest.raises(ForbiddenError) as excinfo:
        services.records.link_records(who, task.key, "decided_by", [decision.key])
    assert excinfo.value.details["object_type"] == "decision"

    grant(services, "decision", principal_id, "read")
    assert services.records.link_records(who, task.key, "decided_by", [decision.key])


def test_comments_take_the_parent_records_level(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    record = services.records.create_record(make_actor(), "decision", {"title": "Ship it"})
    services.comments.add_comment(make_actor(), record.key, "First.")
    principal_id = member(services)
    who = actor_for(principal_id, "write")

    with pytest.raises(ForbiddenError):
        services.comments.list_comments(who, record.key)

    grant(services, "decision", principal_id, "read")
    assert len(services.comments.list_comments(who, record.key)) == 1
    with pytest.raises(ForbiddenError):
        services.comments.add_comment(who, record.key, "Mine.")

    grant(services, "decision", principal_id, "write")
    assert services.comments.add_comment(who, record.key, "Mine.")


def test_saved_views_take_their_types_level(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    view = services.saved_views.create_saved_view(
        make_actor(), "decision", "All", {"columns": ["title"]}
    )
    principal_id = member(services)
    who = actor_for(principal_id, "write")
    with pytest.raises(ForbiddenError):
        services.saved_views.list_saved_views(who, "decision")
    with pytest.raises(ForbiddenError):
        services.saved_views.get_saved_view(who, view.id)

    grant(services, "decision", principal_id, "read")
    assert [v.id for v in services.saved_views.list_saved_views(who, "decision")] == [view.id]
    with pytest.raises(ForbiddenError):
        services.saved_views.update_saved_view(who, view.id, name="Renamed")
    with pytest.raises(ForbiddenError):
        services.saved_views.delete_saved_view(who, view.id)


def test_csv_import_needs_write_and_export_needs_read(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    principal_id = member(services)
    who = actor_for(principal_id, "write")
    with pytest.raises(ForbiddenError):
        services.csv.export_csv(who, "decision")
    with pytest.raises(ForbiddenError):
        services.csv.import_csv(who, "decision", "title\nOne\n", mode="create")

    grant(services, "decision", principal_id, "read")
    assert "title" in services.csv.export_csv(who, "decision")
    with pytest.raises(ForbiddenError):
        services.csv.import_csv(who, "decision", "title\nOne\n", mode="create")
    with pytest.raises(ForbiddenError):
        # A dry run writes nothing and is refused on the same terms as a live import.
        services.csv.import_csv(who, "decision", "title\nOne\n", mode="create", dry_run=True)

    grant(services, "decision", principal_id, "write")
    assert services.csv.import_csv(who, "decision", "title\nOne\n", mode="create").created == 1


def test_a_relation_to_an_unreadable_type_still_exports_as_an_empty_cell(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    """A fence, re-run against the batched relation read.

    ``export_csv`` once resolved relation columns with one ``list_links`` per record and
    now resolves them once per (page, field), which moves the
    ``_may_read_target`` decision out of the per-record loop. Hoisting it is sound
    because a relation field targets exactly one object type -- but the behaviour it
    produces has to be unchanged: a target type the caller cannot read yields an **empty
    cell**, not a redaction marker, because a CSV cell of record keys has no way to
    express one.
    """
    services.schema.add_field(
        make_actor(),
        "initiative",
        {
            "key": "decided_by",
            "name": "Authorised by",
            "type": "relation",
            "description": "The decision this initiative was authorized by.",
            "config": {
                "target_type_key": "decision",
                "cardinality": "many",
                "inverse_field_key": "authorized",
            },
        },
    )
    initiative = services.records.create_record(make_actor(), "initiative", {"title": "One"})
    decision = services.records.create_record(make_actor(), "decision", {"title": "Yes"})
    services.records.link_records(make_actor(), initiative.key, "decided_by", [decision.key])

    principal_id = member(services)
    grant(services, "initiative", principal_id, "read")
    who = actor_for(principal_id, "read")

    exported = services.csv.export_csv(who, "initiative")
    row = next(r for r in csv.DictReader(io.StringIO(exported)) if r["key"] == initiative.key)
    assert row["decided_by"] == ""

    # And with the target readable, the same cell carries the key: the empty cell above
    # is the access rule, not a batching bug.
    grant(services, "decision", principal_id, "read")
    exported = services.csv.export_csv(who, "initiative")
    row = next(r for r in csv.DictReader(io.StringIO(exported)) if r["key"] == initiative.key)
    assert row["decided_by"] == decision.key


def test_schema_writes_need_admin_on_the_type(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    """Only a `creator` or `admin` principal can hold a credential that satisfies
    this at all, which is why the test's principal is a creator."""
    principal_id = services.principals.create_user(
        make_actor(), email="c@example.com", display_name="C", role="creator", password=PASSWORD
    ).id
    grant(services, "decision", principal_id, "write")
    who = actor_for(principal_id, "admin")

    field = {
        "key": "rationale",
        "name": "Rationale",
        "type": "long_text",
        "description": "Why this decision was taken, for whoever reads it later.",
    }
    with pytest.raises(ForbiddenError):
        services.schema.add_field(who, "decision", field)
    with pytest.raises(ForbiddenError):
        services.schema.update_object_type(who, "decision", {"name": "Call"})

    # `write` *is* enough to propose: proposing is not deciding.
    services.schema.add_field(make_actor(), "decision", field)
    proposal = services.schema.propose_schema_change(who, "delete_field", "decision", "rationale")
    assert proposal.status == "pending"
    # ... but deciding needs admin on the type.
    with pytest.raises(ForbiddenError):
        services.schema.approve_proposal(who, proposal.id)
    with pytest.raises(ForbiddenError):
        services.schema.reject_proposal(who, proposal.id)

    grant(services, "decision", principal_id, "admin")
    assert services.schema.reject_proposal(who, proposal.id).status == "rejected"


def test_proposals_are_filtered_to_types_you_can_write(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    for key in ("initiative", "decision"):
        services.schema.add_field(
            make_actor(),
            key,
            {
                "key": "note",
                "name": "Note",
                "type": "long_text",
                "description": "A free-text note, present only so it can be proposed away.",
            },
        )
    mine = services.schema.propose_schema_change(make_actor(), "delete_field", "initiative", "note")
    theirs = services.schema.propose_schema_change(make_actor(), "delete_field", "decision", "note")
    principal_id = services.principals.create_user(
        make_actor(), email="c@example.com", display_name="C", role="creator", password=PASSWORD
    ).id
    grant(services, "initiative", principal_id, "write")
    who = actor_for(principal_id, "admin")

    # The filter is in the SQL and the method is a bounded page, so this also asserts
    # that ``total_count`` is counted under the same filter rather than before it.
    page = services.schema.list_proposals_page(who)
    assert [p.id for p in page.proposals] == [mine.id]
    assert page.total_count == 1
    services.schema.get_proposal(who, mine.id)
    with pytest.raises(ForbiddenError):
        services.schema.get_proposal(who, theirs.id)


# -------------------------------------------------------------------- attachments


def _attach_type(services: ServiceBundle, key: str, prefix: str) -> Any:
    return services.schema.create_object_type(
        make_actor(),
        key=key,
        name=key.title(),
        name_plural=key.title() + "s",
        description=f"A {key} with a file field, for the attachment rule.",
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


@pytest.fixture
def attach_types(services: ServiceBundle) -> tuple[Any, Any]:
    return _attach_type(services, "initiative", "INI"), _attach_type(services, "decision", "DEC")


def test_an_attachment_is_readable_from_any_referencing_type(
    services: ServiceBundle, attach_types: tuple[Any, Any]
) -> None:
    uploaded = services.attachments.upload(make_actor(), "spec.txt", "text/plain", b"hello")
    services.records.create_record(
        make_actor(), "initiative", {"title": "Open", "files": [uploaded.id]}
    )
    services.records.create_record(
        make_actor(), "decision", {"title": "Closed", "files": [uploaded.id]}
    )

    reader_id = member(services, "reader@example.com")
    grant(services, "initiative", reader_id, "read")
    reader = actor_for(reader_id, "read")
    assert services.attachments.get_attachment(reader, uploaded.id).filename == "spec.txt"
    assert services.attachments.download(reader, uploaded.id)[1] == b"hello"

    outsider = actor_for(member(services, "outsider@example.com"), "read")
    with pytest.raises(ForbiddenError):
        services.attachments.get_attachment(outsider, uploaded.id)
    assert services.attachments.get_many(outsider, [uploaded.id]) == []


def test_an_unreferenced_upload_is_readable_only_by_its_uploader(
    services: ServiceBundle, attach_types: tuple[Any, Any]
) -> None:
    """The uploader clause is required, not a convenience: between the upload and the
    record write that attaches the id there is no referencing row at all."""
    uploader_id = member(services, "uploader@example.com")
    uploader = actor_for(uploader_id, "write")
    uploaded = services.attachments.upload(uploader, "draft.txt", "text/plain", b"draft")

    assert services.attachments.get_attachment(uploader, uploaded.id).id == uploaded.id
    other = actor_for(member(services, "other@example.com"), "read")
    with pytest.raises(ForbiddenError):
        services.attachments.get_attachment(other, uploaded.id)


@pytest.mark.parametrize("path", ["create", "update", "bulk_update", "csv_import", "revert"])
def test_the_join_rows_are_maintained_on_every_write_path(
    services: ServiceBundle, db: Database, attach_types: tuple[Any, Any], path: str
) -> None:
    """A test **per write path**, not an inspection of the funnel. A missed path leaves
    an attachment with no join rows, which falls back to the uploader clause and is
    invisible to everyone else -- it reads as working until the wrong person cannot see
    a file."""
    first = services.attachments.upload(make_actor(), "a.txt", "text/plain", b"a")
    second = services.attachments.upload(make_actor(), "b.txt", "text/plain", b"b")

    def rows(record_id: str) -> list[tuple[str, str]]:
        with db.read() as conn:
            return [
                (str(r[0]), str(r[1]))
                for r in conn.execute(
                    text(
                        "SELECT field_key, attachment_id FROM record_attachments "
                        "WHERE record_id = :r ORDER BY attachment_id"
                    ),
                    {"r": record_id},
                ).all()
            ]

    if path == "create":
        record = services.records.create_record(
            make_actor(), "initiative", {"title": "T", "files": [first.id]}
        )
        assert rows(record.id) == [("files", first.id)]
        return

    record = services.records.create_record(
        make_actor(), "initiative", {"title": "T", "files": [first.id]}
    )
    if path == "update":
        services.records.update_record(make_actor(), record.key, {"files": [second.id]})
        assert rows(record.id) == [("files", second.id)]
    elif path == "bulk_update":
        services.records.bulk_update(make_actor(), "initiative", {"files": [second.id]})
        assert rows(record.id) == [("files", second.id)]
    elif path == "csv_import":
        # CSV declares attachment columns out of scope, so an import must not silently
        # clear the rows an earlier write established.
        services.csv.import_csv(
            make_actor(),
            "initiative",
            f"key,title\n{record.key},Renamed\n",
            mode="upsert",
            upsert_key="key",
        )
        assert rows(record.id) == [("files", first.id)]
    elif path == "revert":
        services.records.update_record(make_actor(), record.key, {"files": [second.id]})
        assert rows(record.id) == [("files", second.id)]
        current = services.records.get_record(make_actor(), record.key)
        services.records.revert_to_version(
            make_actor(), record.key, target_version=1, expected_version=current.version
        )
        assert rows(record.id) == [("files", first.id)]


def test_clearing_an_attachment_field_drops_its_join_rows(
    services: ServiceBundle, db: Database, attach_types: tuple[Any, Any]
) -> None:
    uploaded = services.attachments.upload(make_actor(), "a.txt", "text/plain", b"a")
    record = services.records.create_record(
        make_actor(), "initiative", {"title": "T", "files": [uploaded.id]}
    )
    services.records.update_record(make_actor(), record.key, {"files": []})
    with db.read() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM record_attachments WHERE record_id = :r"),
            {"r": record.id},
        ).scalar()
    assert count == 0


# ---------------------------------------------------------------------- the feeds


def test_the_audit_feed_shows_only_readable_types(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    seed_type(services, "risk", "RSK")
    for key in ("initiative", "decision", "risk"):
        services.records.create_record(make_actor(), key, {"title": f"{key} one"})

    principal_id = member(services)
    grant(services, "initiative", principal_id, "read")
    who = actor_for(principal_id, "read")

    result = services.audit.search(who, limit=100)
    type_ids = {e.object_type_id for e in result.events}
    assert type_ids == {two_types[0].id}
    assert all(e.object_type_id is not None for e in result.events)


def test_the_audit_feed_hides_untyped_rows_from_a_non_admin(
    services: ServiceBundle, two_types: tuple[Any, Any], db: Database
) -> None:
    """`audit_events.object_type_id` is nullable with no foreign key and is
    populated by whichever call site remembers to, so this rule is a fail-closed
    heuristic. `services/comments.py` already writes null when a record lookup misses,
    which is the case exercised here rather than only principal/token/session events."""
    principal_id = member(services)
    grant(services, "initiative", principal_id, "read")
    with db.write() as conn:
        conn.execute(
            text(
                "INSERT INTO audit_events (ts, request_id, principal_id, principal_type, "
                "auth_method, surface, entity_type, entity_id, action) VALUES "
                "('2026-09-02T00:00:00Z', 'r', :p, 'user', 'pat', 'api', 'comment', 'c1', "
                "'update')"
            ),
            {"p": principal_id},
        )
    who = actor_for(principal_id, "read")
    assert all(e.object_type_id is not None for e in services.audit.search(who, limit=100).events)
    # A system administrator does see it.
    admin = services.audit.search(make_actor(), limit=200).events
    assert any(e.entity_type == "comment" and e.object_type_id is None for e in admin)


def test_pagination_is_correct_across_a_filtered_boundary(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    """Filtering after the page is read would return short pages whose cursor skipped
    rows the caller never saw. Interleaving the two types guarantees the boundary falls
    inside a run of filtered-out rows."""
    for i in range(6):
        services.records.create_record(make_actor(), "initiative", {"title": f"I{i}"})
        services.records.create_record(make_actor(), "decision", {"title": f"D{i}"})

    principal_id = member(services)
    grant(services, "initiative", principal_id, "read")
    who = actor_for(principal_id, "read")

    seen: list[int] = []
    cursor: str | None = None
    for _ in range(10):
        page = services.audit.search(who, limit=2, cursor=cursor)
        seen.extend(e.id for e in page.events if e.id is not None)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == len(set(seen))
    unpaginated = services.audit.search(who, limit=500).events
    assert sorted(seen) == sorted(e.id for e in unpaginated if e.id is not None)


def test_the_change_feed_is_filtered_and_naming_a_closed_type_is_forbidden(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    services.records.create_record(make_actor(), "initiative", {"title": "I"})
    services.records.create_record(make_actor(), "decision", {"title": "D"})
    principal_id = member(services)
    grant(services, "initiative", principal_id, "read")
    who = actor_for(principal_id, "read")

    events = services.changes.list_changes_since(who, cursor=0).events
    assert {e.object_type_id for e in events} == {two_types[0].id}
    with pytest.raises(ForbiddenError):
        services.changes.list_changes_since(who, cursor=0, object_type_keys=["decision"])


# ------------------------------------------------------------------------ search


def _searchable_type(services: ServiceBundle, key: str, prefix: str) -> Any:
    return services.schema.create_object_type(
        make_actor(),
        key=key,
        name=key.title(),
        name_plural=key.title() + "s",
        description=f"A {key}, seeded with an indexed field for the search narrowing test.",
        key_prefix=prefix,
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "What this is called, indexed so keyword search finds it.",
                "embed": True,
            }
        ],
    )


def test_search_narrows_to_readable_types_and_refuses_a_named_one(
    search_services: ServiceBundle,
) -> None:
    _searchable_type(search_services, "initiative", "INI")
    _searchable_type(search_services, "decision", "DEC")
    search_services.records.create_record(make_actor(), "initiative", {"title": "migration"})
    search_services.records.create_record(make_actor(), "decision", {"title": "migration"})

    principal_id = member(search_services)
    grant(search_services, "initiative", principal_id, "read")
    who = actor_for(principal_id, "read")

    result = search_services.search.search(who, "migration", mode="keyword")
    assert {hit.object_type for hit in result.results} == {"initiative"}

    with pytest.raises(ForbiddenError):
        search_services.search.search(who, "migration", mode="keyword", object_types=["decision"])


# ------------------------------------------------------------ relation redaction


@pytest.fixture
def linked(services: ServiceBundle) -> Any:
    services.schema.create_object_type(
        make_actor(),
        key="task",
        name="Task",
        name_plural="Tasks",
        description="A unit of work, the readable side of the redaction test.",
        key_prefix="TSK",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "What the task is, shown in every listing.",
            }
        ],
    )
    seed_type(services, "decision", "DEC")
    services.schema.add_field(
        make_actor(),
        "task",
        {
            "key": "blocked_by",
            "name": "Blocked by",
            "type": "relation",
            "description": "Decisions this task is waiting on before it can proceed.",
            "config": {"target_type_key": "decision", "cardinality": "many"},
        },
    )
    task = services.records.create_record(make_actor(), "task", {"title": "Migrate"})
    for i in range(2):
        decision = services.records.create_record(
            make_actor(), "decision", {"title": f"Decision {i}"}
        )
        services.records.link_records(make_actor(), task.key, "blocked_by", [decision.key])
    return task


def test_links_into_an_unreadable_type_are_redacted_not_hidden(
    services: ServiceBundle, linked: Any
) -> None:
    principal_id = member(services)
    grant(services, "task", principal_id, "read")
    who = actor_for(principal_id, "read")

    expanded = services.records.get_record_expansions(who, linked.key, ["blocked_by"])
    assert expanded["blocked_by"] == [{"redacted": True}, {"redacted": True}]

    summaries = services.records.list_link_summaries(who, linked.key)
    assert summaries["blocked_by"] == [{"redacted": True}, {"redacted": True}]

    # The count matches what a system administrator sees, and no key, id, title, object
    # type or field value leaks.
    admin_view = services.records.list_link_summaries(make_actor(), linked.key)
    assert len(admin_view["blocked_by"]) == len(summaries["blocked_by"])
    assert all(set(entry) == {"redacted"} for entry in summaries["blocked_by"])


def test_has_links_stays_truthful(services: ServiceBundle, linked: Any) -> None:
    """**Scope fence.** Nothing about grants is compiled into a user filter AST, so
    `has_links` reports the relation that exists rather than the relation the caller can
    see. This cannot fail by construction; it is here because the redaction design turns on it."""
    principal_id = member(services)
    grant(services, "task", principal_id, "read")
    who = actor_for(principal_id, "read")
    result = services.records.query_records(
        who, "task", filter={"field": "blocked_by", "op": "has_links"}
    )
    assert [r["key"] for r in result.records] == [linked.key]


def test_linked_to_naming_an_unreadable_key_is_forbidden(
    services: ServiceBundle, linked: Any
) -> None:
    """Not a silent empty result: the caller named the thing."""
    decision = services.records.query_records(make_actor(), "decision").records[0]
    principal_id = member(services)
    grant(services, "task", principal_id, "read")
    who = actor_for(principal_id, "read")
    with pytest.raises(ForbiddenError):
        services.records.query_records(
            who,
            "task",
            filter={"field": "blocked_by", "op": "linked_to", "value": decision["key"]},
        )


def test_a_readable_target_is_not_redacted(services: ServiceBundle, linked: Any) -> None:
    """**Scope fence** on the other side: redaction must not swallow what is readable."""
    principal_id = member(services)
    grant(services, "task", principal_id, "read")
    grant(services, "decision", principal_id, "read")
    who = actor_for(principal_id, "read")
    entries = services.records.list_link_summaries(who, linked.key)["blocked_by"]
    assert all("key" in entry for entry in entries)


# -------------------------------------------------------------------- your_access


def test_your_access_carries_the_composed_value_over_rest(
    client: TestClient, app_services: ServiceBundle
) -> None:
    seed_type(app_services, "decision", "DEC")
    listing = client.get("/api/v1/object-types")
    assert listing.status_code == 200
    assert listing.json()[0]["your_access"] == "admin"
    described = client.get("/api/v1/object-types/decision")
    assert described.json()["your_access"] == "admin"

    # The ceiling shows through: a read PAT on the same type reports `read`.
    read_only = client.get(
        "/api/v1/object-types/decision",
        headers={"Authorization": f"Bearer {client.scope_tokens['read']}"},
    )
    assert read_only.json()["your_access"] == "read"


# ------------------------------------------------------------ the grant surfaces


def test_grant_endpoints_round_trip_over_rest(
    client: TestClient, app_services: ServiceBundle
) -> None:
    seed_type(app_services, "decision", "DEC")
    principal_id = member(app_services)

    put = client.put(f"/api/v1/object-types/decision/grants/{principal_id}", json={"level": "read"})
    assert put.status_code == 200, put.text
    assert put.json()["level"] == "read"

    listed = client.get("/api/v1/object-types/decision/grants")
    assert listed.status_code == 200
    body = listed.json()
    assert body["default_level"] == "none"
    assert {(g["principal_id"], g["level"]) for g in body["grants"]} >= {(principal_id, "read")}

    deleted = client.delete(f"/api/v1/object-types/decision/grants/{principal_id}")
    assert deleted.status_code == 200
    assert all(
        g["principal_id"] != principal_id
        for g in client.get("/api/v1/object-types/decision/grants").json()["grants"]
    )


def test_default_level_is_patchable_on_the_object_type(
    client: TestClient, app_services: ServiceBundle
) -> None:
    seed_type(app_services, "decision", "DEC")
    patched = client.patch("/api/v1/object-types/decision", json={"default_level": "read"})
    assert patched.status_code == 200, patched.text
    who = actor_for(member(app_services), "write")
    assert app_services.schema.your_access(who, "decision") == "read"


def test_a_creator_is_refused_the_admin_export(
    client: TestClient, app_services: ServiceBundle
) -> None:
    creator = app_services.principals.create_user(
        make_actor(), email="c@example.com", display_name="C", role="creator", password=PASSWORD
    )
    token = app_services.tokens.mint(
        make_actor(), name="creator", scope="admin", principal_id=creator.id
    ).plaintext
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/api/v1/object-types",
        json={
            "key": "playbook",
            "name": "Playbook",
            "name_plural": "Playbooks",
            "description": "A repeatable procedure, created by a creator principal.",
            "key_prefix": "PBK",
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["your_access"] == "admin"

    refused = client.get("/api/v1/admin/export", headers=headers)
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "forbidden"
    assert refused.json()["error"]["details"]["required_role"] == "admin"


# ---------------------------------------------------------- cross-surface parity


@pytest.mark.anyio
async def test_every_write_path_refuses_a_read_granted_principal_identically(
    search_app: FastAPI,
) -> None:
    """The same code and the same message over REST and MCP.

    This is the property that makes the two surfaces' parity structural rather than
    aspirational -- both are adapters over one service layer, so the refusal is the same
    object rendered twice.
    """
    from glosswork.auth import PatTokenResolver
    from glosswork.mcp_server import create_mcp_server
    from tests.mcp_support import error_of, memory_session

    rest = TestClient(search_app)
    rest.__enter__()
    services = search_app.state.services
    seed_type(services, "decision", "DEC")
    existing = services.records.create_record(make_actor(), "decision", {"title": "Ship it"})
    principal_id = member(services)
    grant(services, "decision", principal_id, "read")
    token = services.tokens.mint(
        make_actor(), name="reader", scope="write", principal_id=principal_id
    ).plaintext

    server = create_mcp_server(lambda: services, PatTokenResolver(lambda: services))
    rest.headers["Authorization"] = f"Bearer {token}"

    key = existing.key
    write_paths = (
        ("create_record", {"object_type": "decision", "values": {"title": "New"}}),
        ("update_record", {"record": key, "values": {"title": "Edited"}}),
        ("delete_record", {"record": key}),
        ("add_comment", {"record": key, "body": "Mine."}),
    )
    rest_calls = {
        "create_record": lambda: rest.post(
            "/api/v1/object-types/decision/records", json={"values": {"title": "New"}}
        ),
        "update_record": lambda: rest.patch(
            f"/api/v1/records/{key}", json={"values": {"title": "Edited"}}
        ),
        "delete_record": lambda: rest.delete(f"/api/v1/records/{key}"),
        "add_comment": lambda: rest.post(f"/api/v1/records/{key}/comments", json={"body": "Mine."}),
    }

    async with memory_session(server, token=token) as client:
        for tool, args in write_paths:
            mcp_error = error_of(await client.call_tool(tool, args))
            response = rest_calls[tool]()
            assert response.status_code == 403, (tool, response.text)
            rest_error = response.json()["error"]
            assert mcp_error["code"] == rest_error["code"] == "forbidden", tool
            assert mcp_error["message"] == rest_error["message"], tool
    rest.__exit__(None, None, None)


def test_an_oversized_import_is_refused_for_the_grant_before_the_size(
    services: ServiceBundle, two_types: tuple[Any, Any]
) -> None:
    """CSV import has two ceilings, and neither runs before the access check.

    A caller who may not write this type should be told that, not told their file is too
    big -- authorization comes before anything the payload can influence, even when the
    size check is the cheaper one.
    """
    who = actor_for(member(services), "write")
    oversized = "title\n" + "".join(f"Row {i}\n" for i in range(50))
    service = CsvService(services.records, services.schema, max_import_bytes=16, max_import_rows=2)
    with pytest.raises(ForbiddenError):
        service.import_csv(who, "decision", oversized, mode="create")
