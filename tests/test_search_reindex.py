"""The re-index trigger (FR-Q9).

``SearchIndexService.reindex`` is the one index-maintenance path that *is* a domain
action: an administrator asks for it, so it carries an ``ActorContext`` and writes an
audit row, in the same transaction as the bulk enqueue.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from glosswork.db import Database
from glosswork.errors import FeatureDisabledError
from glosswork.services import ServiceBundle
from tests.conftest import make_actor, make_worker
from tests.search_support import FakeClock, drain, index_counts, job_rows, stored_rows
from tests.test_search_service import NOTE_FIELDS, comment, drain_all, note, search

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def corpus(search_services: ServiceBundle) -> ServiceBundle:
    search_services.schema.create_object_type(
        make_actor(),
        key="note",
        name="Note",
        name_plural="Notes",
        description="A note used by the re-index tests.",
        key_prefix="NOTE",
        fields=NOTE_FIELDS,
    )
    return search_services


def _audit_rows(db: Database) -> list[tuple[str, str, str, Any]]:
    with db.read() as conn:
        return [
            (row[0], row[1], row[2], row[3])
            for row in conn.execute(
                text(
                    "SELECT entity_type, action, principal_id, new_value FROM audit_events "
                    "WHERE entity_type = 'search_index' ORDER BY id"
                )
            ).all()
        ]


def test_reindex_enqueues_one_job_per_source_and_audits_the_actor_and_count(
    corpus: ServiceBundle, db: Database
) -> None:
    first = note(corpus, title="One", body="first body")
    comment(corpus, first, "a comment")
    note(corpus, title="Two", body="second body")
    drain_all(db, corpus)
    assert index_counts(db).pending_jobs == 0

    actor = make_actor()
    enqueued = corpus.search_index.reindex(actor)

    # Two records x (title + body) + one comment = five sources.
    assert enqueued == 5
    assert index_counts(db).pending_jobs == 5
    rows = _audit_rows(db)
    assert len(rows) == 1
    entity_type, action, principal_id, new_value = rows[0]
    assert (entity_type, action, principal_id) == (
        "search_index",
        "reindex_requested",
        actor.principal_id,
    )
    assert json.loads(str(new_value)) == {"enqueued": 5}


def test_reindex_coalesces_against_a_job_already_pending(
    corpus: ServiceBundle, db: Database
) -> None:
    note(corpus, title="One", body="first body")  # two jobs pending, never drained
    assert index_counts(db).pending_jobs == 2
    enqueued = corpus.search_index.reindex(make_actor())
    assert enqueued == 0
    assert index_counts(db).pending_jobs == 2
    assert len([job for job in job_rows(db) if job.status == "pending"]) == 2


def test_reindex_deletes_failed_rows_first(corpus: ServiceBundle, db: Database) -> None:
    record = note(corpus, title="One", body="first body")
    with db.write() as conn:
        conn.execute(
            text("UPDATE embedding_jobs SET status = 'failed', attempts = 5 WHERE record_id = :r"),
            {"r": record.id},
        )
    assert index_counts(db).failed_jobs == 2
    assert corpus.search_index.reindex(make_actor()) == 2
    counts = index_counts(db)
    assert counts.failed_jobs == 0 and counts.pending_jobs == 2


def test_reindex_enqueues_a_soft_deleted_records_sources_and_a_deleted_comment(
    corpus: ServiceBundle, db: Database
) -> None:
    """Decision 10: ``restore_record`` enqueues nothing, so a record deleted before a
    model swap and restored after it would otherwise never be semantically findable."""
    record = note(corpus, title="Gone", body="gone body")
    row = comment(corpus, record, "a deleted comment")
    drain_all(db, corpus)
    corpus.comments.delete_comment(make_actor(), row.id)
    corpus.records.delete_record(make_actor(), record.key)
    assert index_counts(db).pending_jobs == 0
    assert corpus.search_index.reindex(make_actor()) == 3
    pending = {(job.source_type, job.field_key, job.comment_id) for job in job_rows(db)}
    assert pending == {("field", "title", None), ("field", "body", None), ("comment", None, row.id)}


def test_reindex_is_feature_disabled_when_embedding_is_off(services: ServiceBundle) -> None:
    with pytest.raises(FeatureDisabledError) as excinfo:
        services.search_index.reindex(make_actor())
    assert excinfo.value.details == {"feature": "reindex", "setting": "GW_EMBEDDING_ENABLED"}
    assert "worker" in excinfo.value.message


def test_reindex_over_rest_is_202_admin_only_with_the_count(
    search_app: FastAPI, search_client: TestClient
) -> None:
    services: ServiceBundle = search_app.state.services
    services.schema.create_object_type(
        make_actor(),
        key="note",
        name="Note",
        name_plural="Notes",
        description="A note used by the re-index route test.",
        key_prefix="NOTE",
        fields=NOTE_FIELDS,
    )
    note(services, title="One", body="first body")
    drain(make_worker(search_app.state.db, services, FakeClock()))
    tokens: dict[str, str] = search_client.scope_tokens  # type: ignore[attr-defined]
    forbidden = search_client.post(
        "/api/v1/admin/search-index/reindex",
        headers={"Authorization": f"Bearer {tokens['write']}"},
    )
    assert forbidden.status_code == 403
    response = search_client.post("/api/v1/admin/search-index/reindex")
    assert response.status_code == 202, response.text
    assert response.json() == {"enqueued": 2}
    status = search_client.get("/api/v1/admin/search-index").json()
    assert status["pending_jobs"] == 2


def test_reindex_runs_without_downtime(corpus: ServiceBundle, db: Database) -> None:
    """FR-Q9: rows are replaced per source and never deleted ahead of time, so with
    the queue only partly processed a source in the unprocessed remainder and one in
    the processed batch are both still found in keyword and in semantic mode -- and,
    same model, the processed source's vector was reused byte-identical."""
    records = [note(corpus, title=f"Note {i}", body=f"zebra body number {i}") for i in range(4)]
    drain_all(db, corpus)
    before = {
        record.id: [
            row.vector for row in stored_rows(db, _body_source(record), include_vectors=True)
        ]
        for record in records
    }
    assert corpus.search_index.reindex(make_actor()) == 8

    # Exactly one batch, smaller than the queue: three of eight jobs, in id order.
    worker = make_worker(db, corpus, FakeClock(), batch_size=3)
    clock = FakeClock()
    clock.advance(2)
    result = worker.run_once(clock.now)
    assert result.claimed == 3
    remaining = [job for job in job_rows(db) if job.status == "pending"]
    assert len(remaining) == 5
    processed_ids = {r.id for r in records} - {job.record_id for job in remaining}
    unprocessed_ids = {job.record_id for job in remaining}
    assert processed_ids and unprocessed_ids

    for mode in ("keyword", "semantic"):
        found = {hit.record_id for hit in search(corpus, "zebra body", mode=mode, limit=10).results}
        assert found >= {next(iter(processed_ids)), next(iter(unprocessed_ids))}, mode

    for record in records:
        after = [row.vector for row in stored_rows(db, _body_source(record), include_vectors=True)]
        assert after == before[record.id]


def _body_source(record: Any) -> Any:
    from glosswork.repositories.models import IndexSource

    return IndexSource.field(record.id, record.object_type_id, "body")
