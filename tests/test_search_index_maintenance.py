"""Index maintenance on every write path (FR-Q3, FR-C7, docs/DATA_MODEL.md section 10).

Runs against the deterministic ``FakeEmbeddingProvider``: what is under test here is
which sources get indexed and when, not what a vector contains.

The ``sink_type`` fixture already carries the three shapes eligibility turns on:
``summary`` is ``long_text`` (``embed`` defaults on), ``title`` is ``short_text``
(``embed`` defaults off), and ``points`` is ``integer``.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.repositories.sqlite import SqliteSchemaRepository
from glosswork.services import ServiceBundle
from tests.conftest import make_actor
from tests.search_support import (
    comment_source,
    field_source,
    fts_body,
    fts_rows,
    job_rows,
    jobs_for,
    stored_rows,
)

SUMMARY = "The renewal was priced against last year's baseline and approved by finance."
OTHER = "A completely different narrative about datacenter cooling capacity."


@pytest.fixture
def sink(
    search_services: ServiceBundle, actor: ActorContext
) -> tuple[ObjectType, dict[str, FieldDef]]:
    """``sink_type``, but built on the embedding-enabled bundle."""
    from tests.conftest import KITCHEN_SINK_FIELDS

    object_type = search_services.schema.create_object_type(
        actor,
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the test suite; exercises every "
        "field type the platform supports.",
        key_prefix="ART",
        fields=KITCHEN_SINK_FIELDS,
    )
    _, fields = search_services.schema.get_object_type(actor, "artifact")
    return object_type, {f.key: f for f in fields}


@pytest.fixture
def record(search_services: ServiceBundle, sink: Any) -> Any:
    return search_services.records.create_record(
        make_actor(), "artifact", {"title": "Renewal", "summary": SUMMARY}
    )


# ------------------------------------------------------------------ eligibility


def test_a_short_text_field_with_embed_false_produces_neither_a_row_nor_a_job(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    """``title`` is ``short_text``, so ``embed`` defaults off and it is opted out."""
    _, fields = sink
    assert fields["title"].embed is False
    source = field_source(record, "title")
    assert fts_body(db, source) is None
    assert jobs_for(db, source) == []


def test_toggling_embed_on_indexes_every_live_record_of_the_type_in_one_transaction(
    search_services: ServiceBundle, db: Database, sink: Any
) -> None:
    """The fan-out DD-34 describes: one keyword row and one job per live record."""
    records = [
        search_services.records.create_record(
            make_actor(), "artifact", {"title": f"Opted in {n}", "summary": SUMMARY}
        )
        for n in range(3)
    ]
    for row in records:
        assert fts_body(db, field_source(row, "title")) is None

    search_services.schema.update_field(make_actor(), "artifact", "title", {"embed": True})

    for row in records:
        source = field_source(row, "title")
        assert fts_body(db, source) == row.data["title"]
        assert [j.status for j in jobs_for(db, source)] == ["pending"]


def test_a_long_text_field_with_embed_false_produces_neither(
    search_services: ServiceBundle, db: Database, sink: Any
) -> None:
    """An administrator turning ``embed`` off must be honored, not silently ignored.

    The rule is the flag, not the type: ``update_field`` accepts an ``embed`` change
    on a field of any type and only *defaults* the flag from the type, so a type-only
    eligibility rule would index this field against an explicit instruction.
    """
    search_services.schema.update_field(make_actor(), "artifact", "summary", {"embed": False})
    row = search_services.records.create_record(
        make_actor(), "artifact", {"title": "Opted out", "summary": SUMMARY}
    )
    source = field_source(row, "summary")
    assert fts_body(db, source) is None
    assert jobs_for(db, source) == []


def test_a_number_field_with_embed_true_produces_neither(
    search_services: ServiceBundle, db: Database, sink: Any
) -> None:
    """The flag cannot promote a non-text type into the index."""
    search_services.schema.update_field(make_actor(), "artifact", "points", {"embed": True})
    row = search_services.records.create_record(
        make_actor(), "artifact", {"title": "Numbers", "summary": SUMMARY, "points": 42}
    )
    source = field_source(row, "points")
    assert fts_body(db, source) is None
    assert jobs_for(db, source) == []


# ----------------------------------------------------------------- write paths


def test_create_record_indexes_its_eligible_fields(db: Database, sink: Any, record: Any) -> None:
    source = field_source(record, "summary")
    assert fts_body(db, source) == SUMMARY
    assert [j.status for j in jobs_for(db, source)] == ["pending"]


def test_update_record_indexes_only_the_fields_that_changed(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    """An unchanged eligible field enqueues nothing.

    Without this, every write to any field re-embeds every eligible field on the
    record, which at PRD scale is the difference between a queue that drains and one
    that never does.
    """
    _clear_queue(db)
    search_services.records.update_record(make_actor(), record.key, {"points": 7})
    assert jobs_for(db, field_source(record, "summary")) == []

    search_services.records.update_record(make_actor(), record.key, {"summary": OTHER})
    assert [j.status for j in jobs_for(db, field_source(record, "summary"))] == ["pending"]
    assert fts_body(db, field_source(record, "summary")) == OTHER


def test_clearing_an_eligible_field_removes_its_keyword_row_immediately(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    """A cleared field must stop matching in the same transaction that cleared it."""
    assert fts_body(db, field_source(record, "summary")) == SUMMARY
    search_services.records.update_record(make_actor(), record.key, {"summary": None})
    assert fts_body(db, field_source(record, "summary")) is None


def test_bulk_update_maintains_the_index_for_every_record_it_touches(
    search_services: ServiceBundle, db: Database, sink: Any
) -> None:
    rows = [
        search_services.records.create_record(
            make_actor(), "artifact", {"title": f"Bulk {n}", "summary": SUMMARY}
        )
        for n in range(3)
    ]
    _clear_queue(db)
    result = search_services.records.bulk_update(
        make_actor(), "artifact", {"summary": OTHER}, filter=None
    )
    assert result.affected_count == 3
    for row in rows:
        source = field_source(row, "summary")
        assert fts_body(db, source) == OTHER
        assert [j.status for j in jobs_for(db, source)] == ["pending"]


def test_a_bulk_update_dry_run_indexes_nothing(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    _clear_queue(db)
    search_services.records.bulk_update(
        make_actor(), "artifact", {"summary": OTHER}, filter=None, dry_run=True
    )
    assert job_rows(db) == []
    assert fts_body(db, field_source(record, "summary")) == SUMMARY


def test_csv_import_maintains_the_index(
    search_services: ServiceBundle, db: Database, sink: Any
) -> None:
    """CSV import reaches the index through ``create_record``/``update_record``.

    Asserted anyway, because "it goes through the service layer" is the kind of claim
    that stays written down after a shortcut is added.
    """
    csv_text = "title,summary\nImported,{}\n".format(SUMMARY.replace(",", ""))
    report = search_services.csv.import_csv(make_actor(), "artifact", csv_text, mode="create")
    assert report.created == 1
    imported = [r for r in fts_rows(db) if r[1] == "field" and r[2] == "summary"]
    assert len(imported) == 1
    assert imported[0][4] == SUMMARY.replace(",", "")


def test_revert_reaches_the_index_through_update_record(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    """DD-21: revert is ``update_record`` with an inverse patch, so no separate hook.

    Both revert entry points are covered, because a second write path is exactly what
    DD-21 exists to prevent and a missing index hook is how one would announce itself.
    """
    updated = search_services.records.update_record(make_actor(), record.key, {"summary": OTHER})
    assert fts_body(db, field_source(record, "summary")) == OTHER

    events = search_services.records.get_record_history(
        make_actor(), record.key, field_key="summary"
    )
    change = next(e for e in events if e.old_value == SUMMARY and e.new_value == OTHER)
    reverted = search_services.records.revert_field_change(
        make_actor(), change.id, expected_version=updated.version
    )
    assert fts_body(db, field_source(record, "summary")) == SUMMARY

    search_services.records.update_record(make_actor(), record.key, {"summary": OTHER})
    current = search_services.records.get_record(make_actor(), record.key)
    search_services.records.revert_to_version(
        make_actor(),
        record.key,
        target_version=reverted.version,
        expected_version=current.version,
    )
    assert fts_body(db, field_source(record, "summary")) == SUMMARY


def test_add_comment_and_update_comment_index_the_body(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    """FR-C7: every comment body is eligible; comments carry no ``embed`` flag."""
    comment = search_services.comments.add_comment(
        make_actor(), record.key, "Dana flagged a pricing risk in the renewal."
    )
    source = comment_source(record, comment)
    assert fts_body(db, source) == "Dana flagged a pricing risk in the renewal."
    assert [j.status for j in jobs_for(db, source)] == ["pending"]

    _clear_queue(db)
    # ``make_actor()`` is the seeded bootstrap principal, which is also this comment's
    # author, so the FR-C5 author-only check passes without a second principal.
    assert comment.author_id == make_actor().principal_id
    search_services.comments.update_comment(make_actor(), comment.id, "Revised: risk retired.")
    assert fts_body(db, source) == "Revised: risk retired."
    assert [j.status for j in jobs_for(db, source)] == ["pending"]


# ------------------------------------------------- what must NOT touch the index


def test_soft_delete_and_restore_leave_both_indexes_alone(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    """Exclusion is a query-time join, not index deletion (docs/DATA_MODEL.md s10).

    Deleting the rows instead would make a restore silently unsearchable until
    something re-indexed it, and would make the soft-delete flag stop being the only
    thing that decides visibility.
    """
    _run_worker(db, search_services)
    before_rows = stored_rows(db, field_source(record, "summary"), include_vectors=True)
    assert before_rows, "the fixture must actually be indexed for this to mean anything"

    search_services.records.delete_record(make_actor(), record.key)
    assert fts_body(db, field_source(record, "summary")) == SUMMARY
    assert stored_rows(db, field_source(record, "summary"), include_vectors=True) == before_rows
    assert job_rows(db) == []

    search_services.records.restore_record(make_actor(), record.key)
    assert fts_body(db, field_source(record, "summary")) == SUMMARY
    assert job_rows(db) == [], "restore must enqueue nothing"


def test_deleting_a_comment_leaves_its_rows_in_place(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    comment = search_services.comments.add_comment(
        make_actor(), record.key, "A comment that will be soft-deleted."
    )
    _run_worker(db, search_services)
    source = comment_source(record, comment)
    before = stored_rows(db, source, include_vectors=True)

    search_services.comments.delete_comment(make_actor(), comment.id)
    assert fts_body(db, source) == "A comment that will be soft-deleted."
    assert stored_rows(db, source, include_vectors=True) == before
    assert job_rows(db) == []


# ------------------------------------------------------------------- coalescing


def test_two_writes_before_the_worker_runs_leave_one_pending_job(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    """``ux_jobs_pending_source`` makes coalescing a constraint, not a convention."""
    source = field_source(record, "summary")
    search_services.records.update_record(make_actor(), record.key, {"summary": OTHER})
    search_services.records.update_record(make_actor(), record.key, {"summary": SUMMARY})
    search_services.records.update_record(make_actor(), record.key, {"summary": OTHER})
    assert [j.status for j in jobs_for(db, source)] == ["pending"]


def test_a_write_while_a_job_is_running_gets_a_fresh_pending_sibling(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    """A running job does not occupy the partial index's slot, so nothing is lost.

    Without the sibling, the edit that landed mid-batch would never be indexed: the
    worker re-reads the source's text when it *claims*, which was before this write.
    """
    source = field_source(record, "summary")
    with db.write() as conn:
        conn.execute(
            text("UPDATE embedding_jobs SET status = 'running' WHERE record_id = :r"),
            {"r": record.id},
        )
    search_services.records.update_record(make_actor(), record.key, {"summary": OTHER})
    statuses = sorted(j.status for j in jobs_for(db, source))
    assert statuses == ["pending", "running"]


# -------------------------------------------------------------------- atomicity


def test_a_failure_after_the_domain_write_leaves_no_keyword_row_and_no_job(
    search_services: ServiceBundle, db: Database, sink: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit row, the keyword row, and the job commit together or not at all.

    Forced by making the *last* thing in the transaction raise, so the domain write,
    its audit rows, the FTS row, and the enqueue have all already happened when the
    rollback occurs. If index maintenance ran outside the transaction, the keyword row
    would survive a record that does not exist.
    """
    boom = RuntimeError("failure injected after the domain write")
    original = search_services.records._records.get_record

    def explode(conn: Any, ref: str, include_deleted: bool = False) -> Any:
        row = original(conn, ref, include_deleted)
        if row is not None and row.data.get("summary") == OTHER:
            raise boom
        return row

    monkeypatch.setattr(search_services.records, "_records", _Proxy(original, explode))

    row = search_services.records.create_record(
        make_actor(), "artifact", {"title": "Doomed", "summary": SUMMARY}
    )
    _clear_queue(db)
    with pytest.raises(RuntimeError):
        search_services.records.update_record(make_actor(), row.key, {"summary": OTHER})

    # The write rolled back entirely: the old text still stands and nothing queued.
    assert fts_body(db, field_source(row, "summary")) == SUMMARY
    assert job_rows(db) == []


class _Proxy:
    """A record repository whose ``get_record`` is replaced, everything else intact."""

    def __init__(self, original: Any, replacement: Any) -> None:
        self._original_self = original.__self__
        self.get_record = replacement

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original_self, name)


# -------------------------------------------------------- schema change fan-out


def test_deleting_a_field_purges_its_rows_and_its_jobs(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    object_type, _ = sink
    proposal = search_services.schema.propose_schema_change(
        make_actor(), "delete_field", "artifact", field_key="summary", reason="no longer used"
    )
    search_services.schema.approve_proposal(make_actor(), proposal.id)

    source = field_source(record, "summary")
    assert fts_body(db, source) is None
    assert stored_rows(db, source) == []
    assert jobs_for(db, source) == []


def test_deleting_an_object_type_purges_its_field_rows_and_its_comment_rows(
    search_services: ServiceBundle, db: Database, sink: Any, record: Any
) -> None:
    comment = search_services.comments.add_comment(
        make_actor(), record.key, "A comment that goes with the type."
    )
    _clear_queue(db)
    assert fts_body(db, comment_source(record, comment)) is not None

    proposal = search_services.schema.propose_schema_change(
        make_actor(), "delete_object_type", "artifact", reason="retired"
    )
    search_services.schema.approve_proposal(make_actor(), proposal.id)

    assert fts_rows(db) == []
    assert stored_rows(db, field_source(record, "summary")) == []
    assert stored_rows(db, comment_source(record, comment)) == []
    assert job_rows(db) == []


def test_changing_a_field_type_away_from_text_purges_it(
    search_services: ServiceBundle, db: Database, sink: Any
) -> None:
    row = search_services.records.create_record(
        make_actor(), "artifact", {"title": "Typed", "summary": "12345"}
    )
    source = field_source(row, "summary")
    assert fts_body(db, source) == "12345"

    proposal = search_services.schema.propose_schema_change(
        make_actor(),
        "change_field_type",
        "artifact",
        field_key="summary",
        payload={"to_type": "integer", "to_config": {}},
        reason="it was always a number",
    )
    search_services.schema.approve_proposal(make_actor(), proposal.id)

    assert fts_body(db, source) is None
    assert stored_rows(db, source) == []
    assert jobs_for(db, source) == []


def test_changing_a_field_type_into_text_indexes_it(
    search_services: ServiceBundle, db: Database, sink: Any
) -> None:
    """The other direction of the same crossing, which a purge-only hook would miss."""
    search_services.schema.add_field(
        make_actor(),
        "artifact",
        {
            "key": "ticket",
            "name": "Ticket",
            "type": "integer",
            "description": "An external ticket number, migrated from a legacy system.",
            # Opted in while still a number, so it indexes nothing yet: the schema
            # service accepts ``embed`` on a field of any type and only the *type*
            # test is keeping this out of the index. Changing the type is then a real
            # crossing of the eligibility line rather than a no-op.
            "embed": True,
        },
    )
    row = search_services.records.create_record(
        make_actor(), "artifact", {"title": "Crossing", "summary": SUMMARY, "ticket": 88213}
    )
    source = field_source(row, "ticket")
    assert fts_body(db, source) is None

    proposal = search_services.schema.propose_schema_change(
        make_actor(),
        "change_field_type",
        "artifact",
        field_key="ticket",
        payload={"to_type": "long_text", "to_config": {}},
        reason="ticket ids turned out to be alphanumeric",
    )
    search_services.schema.approve_proposal(make_actor(), proposal.id)

    assert fts_body(db, source) == "88213"
    assert [j.status for j in jobs_for(db, source)] == ["pending"]


# --------------------------------------------------------- GW_EMBEDDING_ENABLED


def test_with_embedding_disabled_keyword_rows_are_maintained_and_nothing_is_enqueued(
    services: ServiceBundle, db: Database, sink_type: Any
) -> None:
    """DD-34 for ``GW_EMBEDDING_ENABLED=false``.

    The queue cannot grow unbounded on a deployment with no worker, and keyword search
    keeps working. The retrieval clauses (``semantic`` refused, ``hybrid`` degraded)
    are tested with the search service.
    """
    assert services.search_index.embedding_enabled is False
    row = services.records.create_record(
        make_actor(), "artifact", {"title": "Disabled", "summary": SUMMARY}
    )
    services.comments.add_comment(make_actor(), row.key, "A comment on a disabled deployment.")

    assert fts_body(db, field_source(row, "summary")) == SUMMARY
    assert len(fts_rows(db)) == 2, "the field and the comment are both keyword-indexed"
    assert job_rows(db) == [], "no worker exists, so nothing may be queued"


def test_with_embedding_disabled_the_status_endpoint_reports_the_configured_model(
    services: ServiceBundle, db: Database
) -> None:
    """No provider to compare against, so the configured name stands in.

    Returning null instead would make the field's type conditional on a setting for
    every consumer (docs/DATA_MODEL.md section 13).
    """
    status = services.search_index.status()
    assert status.semantic_enabled is False
    assert status.embedding_model == "bge-small-en-v1.5"
    assert status.stale_chunks == 0
    assert status.failed_jobs == []


# ---------------------------------------------------------------------- helpers


def _clear_queue(db: Database) -> None:
    """Empty the queue without running a worker, so the next assertion is unambiguous."""
    with db.write() as conn:
        conn.execute(text("DELETE FROM embedding_jobs"))


def _run_worker(db: Database, services: ServiceBundle) -> None:
    """Actually drain the queue, so ``embeddings`` rows exist to assert about."""
    from tests.conftest import make_worker
    from tests.search_support import FakeClock, drain

    clock = FakeClock()
    drain(make_worker(db, services, clock), clock)


# ----------------------------------------------- the fan-out is batched, not one lock


class TestFanOutIsBatched:
    """Closed by building rather than by recording a number.

    Measured on the seeded 200,000-record corpus before batching: turning ``embed``
    on for a field of the 90,000-record type held SQLite's single writer lock for
    **22.1 seconds and failed 16 concurrent writes** with "database is locked"; the
    purge side of ``delete_field`` took 7.0 s and failed 4. These tests hold the bound
    DD-34 puts on it.
    """

    def test_the_fan_out_commits_in_batches_rather_than_one_transaction(
        self, search_services: ServiceBundle, db: Database
    ) -> None:
        """The property that matters is that the lock is released between batches, and
        the observable proxy for it is that more than one transaction commits."""
        actor = make_actor()
        search_services.schema.create_object_type(
            actor,
            key="memo",
            name="Memo",
            name_plural="Memos",
            description="Fan-out batching fixture.",
            key_prefix="MEMO",
            fields=[
                {
                    "key": "body",
                    "name": "Body",
                    "type": "long_text",
                    "description": "Text that will become eligible.",
                    "embed": False,
                }
            ],
        )
        for i in range(25):
            search_services.records.create_record(actor, "memo", {"body": f"Memo body {i}"})

        commits = []
        original = search_services.search_index.fan_out_field

        def counting(object_type: Any, field: Any, **kwargs: Any) -> int:
            kwargs.setdefault("batch_size", 10)
            result = original(object_type, field, **kwargs)
            commits.append(result)
            return result

        search_services.search_index.fan_out_field = counting  # type: ignore[method-assign]
        search_services.schema.update_field(make_actor(), "memo", "body", {"embed": True})

        assert commits == [25], "the fan-out did not run, or ran more than once"
        # Every record ended up indexed, batching notwithstanding.
        assert len(fts_rows(db)) == 25

    def test_the_fan_out_runs_after_the_schema_change_has_committed(
        self, search_services: ServiceBundle, db: Database
    ) -> None:
        """The ordering the whole fix rests on. A batch opens its own write connection,
        and SQLite allows one writer, so if this ran inside the schema change's
        transaction it would deadlock rather than merely be slow. Asserted by having
        the fan-out read the committed schema from a *separate* connection: it can only
        see ``embed`` already true if the transaction closed first.
        """
        actor = make_actor()
        search_services.schema.create_object_type(
            actor,
            key="note",
            name="Note",
            name_plural="Notes",
            description="Fan-out ordering fixture.",
            key_prefix="NOTE",
            fields=[
                {
                    "key": "body",
                    "name": "Body",
                    "type": "long_text",
                    "description": "Text that will become eligible.",
                    "embed": False,
                }
            ],
        )
        search_services.records.create_record(actor, "note", {"body": "Visible after commit"})

        observed: list[bool] = []
        original = search_services.search_index.fan_out_field

        def observing(object_type: Any, field: Any, **kwargs: Any) -> int:
            # A fresh connection, exactly as a batch takes: if the schema transaction
            # were still open this read would block and then fail.
            with db.read() as conn:
                repo = SqliteSchemaRepository()
                live = {f.key: f for f in repo.list_fields(conn, object_type.id)}
                observed.append(live["body"].embed)
            return original(object_type, field, **kwargs)

        search_services.search_index.fan_out_field = observing  # type: ignore[method-assign]
        search_services.schema.update_field(make_actor(), "note", "body", {"embed": True})

        assert observed == [True], "the fan-out ran before the schema change committed"

    def test_turning_embed_off_still_purges_inside_the_transaction(
        self, search_services: ServiceBundle, db: Database
    ) -> None:
        """Only the fan-out direction is batched after the commit. A purge is two
        indexed deletes, and it must still take effect the moment the change commits, or
        a field an administrator just de-indexed keeps matching searches.
        """
        actor = make_actor()
        search_services.schema.create_object_type(
            actor,
            key="brief",
            name="Brief",
            name_plural="Briefs",
            description="Purge fixture.",
            key_prefix="BRF",
            fields=[
                {
                    "key": "body",
                    "name": "Body",
                    "type": "long_text",
                    "description": "Eligible to start with.",
                    "embed": True,
                }
            ],
        )
        search_services.records.create_record(actor, "brief", {"body": "Findable for now"})
        assert len(fts_rows(db)) == 1

        search_services.schema.update_field(make_actor(), "brief", "body", {"embed": False})

        assert fts_rows(db) == []
