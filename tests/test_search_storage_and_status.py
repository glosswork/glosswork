"""Migration 6, extension loading, and the index status endpoint (DD-31, FR-Q7, FR-P5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from glosswork.db import Database, SearchExtensionError, check_search_extensions
from glosswork.migrations import MIGRATIONS, applied_migrations, run_migrations
from glosswork.repositories.models import IndexSource
from glosswork.repositories.sqlite import SqliteSearchRepository
from glosswork.services import ServiceBundle
from tests.conftest import (
    KITCHEN_SINK_FIELDS,
    auth,
    make_actor,
    make_worker,
    mint_scope_tokens,
)
from tests.search_support import (
    FakeClock,
    FakeEmbeddingProvider,
    drain,
    field_source,
    index_counts,
    job_rows,
    jobs_for,
)

# --------------------------------------------------------------- migration 6


def test_migration_six_creates_the_four_search_tables_and_the_coalescing_index(
    db: Database,
) -> None:
    with db.read() as conn:
        names = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")
            ).all()
        }
    for expected in (
        "embeddings",
        "embedding_jobs",
        "fts_content",
        "vec_embeddings",
        "search_sources",
        "ux_jobs_pending_source",
        "ux_search_sources",
    ):
        assert expected in names, f"{expected} missing after migration 6"


def test_vec_embeddings_carries_both_partition_keys(db: Database) -> None:
    """Migration 6: ``object_type_id`` and ``model_id`` are vec0 **partition keys**, so
    a KNN scoped by type and model is answered inside those partitions rather than
    post-filtering a global pool. Asserted on the DDL SQLite recorded, not on the
    migration's text, so a database that applied an earlier form is caught."""
    with db.read() as conn:
        ddl = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'vec_embeddings'")
        ).scalar_one()
    normalized = " ".join(str(ddl).split())
    assert "embedding float[384]" in normalized
    assert "object_type_id text partition key" in normalized
    assert "model_id text partition key" in normalized


def _vector_type(services: ServiceBundle, key: str) -> str:
    """One object type with a ``long_text`` field; returns its id."""
    created = services.schema.create_object_type(
        make_actor(),
        key=key,
        name=key.title(),
        name_plural=key.title() + "s",
        description=f"A {key} used by the partition-key KNN tests.",
        key_prefix=key[:3].upper(),
        fields=[
            {
                "key": "body",
                "name": "Body",
                "type": "long_text",
                "description": "Free text, embedded.",
            }
        ],
    )
    return created.id


def _seed_vectors(
    db: Database, services: ServiceBundle, rows: list[tuple[str, str, str, list[float]]]
) -> dict[str, str]:
    """Create one record per ``(label, type_key, model_id, vector)`` through the
    service layer, then store the vector through the same repository method the
    worker uses, so the partition keys travel the shipped path. Returns record id by
    label."""
    repo = SqliteSearchRepository()
    ids: dict[str, str] = {}
    for label, type_key, model_id, vector in rows:
        record = services.records.create_record(make_actor(), type_key, {"body": f"text {label}"})
        ids[label] = record.id
        source = IndexSource.field(record.id, record.object_type_id, "body")
        digest = f"hash-{label}"
        with db.write() as conn:
            repo.sync_source_embeddings(
                conn, source, [(0, f"text {label}", digest)], {digest: vector}, model_id, "T"
            )
    return ids


def _unit(*coords: float) -> list[float]:
    vector = [0.0] * 384
    for index, value in enumerate(coords):
        vector[index] = value
    return vector


def test_a_knn_scoped_to_one_type_returns_k_rows_from_that_type_when_the_other_is_closer(
    db: Database, search_services: ServiceBundle
) -> None:
    """Type A holds the four nearest vectors, type B holds four further away. A KNN
    scoped to B with ``k = 4`` returns B's four rows -- under a global-KNN-then-post-filter
    shape it would return nothing, because A's rows fill the pool first."""
    type_a = _vector_type(search_services, "alpha")
    type_b = _vector_type(search_services, "beta")
    query = _unit(1.0)
    rows = []
    for index in range(4):
        rows.append((f"a{index}", "alpha", "m1", _unit(1.0, 0.01 * index)))
    for index in range(4):
        rows.append((f"b{index}", "beta", "m1", _unit(1.0, 0.5 + 0.01 * index)))
    ids = _seed_vectors(db, search_services, rows)
    label_of = {record_id: label for label, record_id in ids.items()}
    repo = SqliteSearchRepository()
    with db.read() as conn:
        pool = repo.vector_search(conn, query, [type_b], "m1", 4)
    assert [label_of[hit.source.record_id] for hit in pool.hits] == ["b0", "b1", "b2", "b3"]
    assert pool.knn_rows == 4
    with db.read() as conn:
        both = repo.vector_search(conn, query, [type_a, type_b], "m1", 4)
    assert [label_of[hit.source.record_id] for hit in both.hits] == ["a0", "a1", "a2", "a3"]
    # ``IN`` over two partitions returns k rows per partition before the outer
    # ordering picks the global nearest, which is what makes scoped KNN exact.
    assert both.knn_rows == 8


def test_rows_from_another_model_id_are_invisible_to_the_vector_arm(
    db: Database, search_services: ServiceBundle
) -> None:
    """Decision 5: during a model-swap re-index a not-yet-re-embedded source must be
    invisible to the semantic arm rather than ranked by a distance between two
    vector spaces. The nearest row belongs to the old model and never appears."""
    type_a = _vector_type(search_services, "alpha")
    query = _unit(1.0)
    ids = _seed_vectors(
        db,
        search_services,
        [
            ("old", "alpha", "old-model", _unit(1.0)),
            ("new1", "alpha", "new-model", _unit(1.0, 0.2)),
            ("new2", "alpha", "new-model", _unit(1.0, 0.3)),
        ],
    )
    label_of = {record_id: label for label, record_id in ids.items()}
    repo = SqliteSearchRepository()
    with db.read() as conn:
        pool = repo.vector_search(conn, query, [type_a], "new-model", 10)
    assert [label_of[hit.source.record_id] for hit in pool.hits] == ["new1", "new2"]
    assert pool.knn_rows == 2


def test_migration_six_is_numbered_six_and_idempotent(tmp_path: Path) -> None:
    database = Database.connect(tmp_path / "idempotent.sqlite3")
    try:
        assert run_migrations(database) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
        assert run_migrations(database) == []
        assert 6 in applied_migrations(database)
        assert {m.number for m in MIGRATIONS} == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
    finally:
        database.close()


def test_migration_six_applies_with_embedding_disabled(tmp_path: Path) -> None:
    """It runs **unconditionally**, so sqlite-vec is not optional (section 13).

    Creating ``vec_embeddings`` needs the extension on every deployment, disabled or
    not: a deployment that turned embedding off precisely because it cannot run the
    model still cannot start without a loadable-extension build of SQLite. Stating
    that here means it is discovered by a test rather than on such a deployment.
    """
    from fastapi.testclient import TestClient as Client

    from glosswork.app import create_app
    from glosswork.config import Settings

    app = create_app(Settings(data_dir=tmp_path / "disabled", embedding_enabled=False))
    with Client(app) as client:
        assert client.get("/readyz").json() == {"status": "ok"}  # the body
    database = Database.connect(tmp_path / "disabled" / "glosswork.sqlite3")
    try:
        assert 6 in applied_migrations(database)
        with database.read() as conn:
            conn.execute(text("SELECT count(*) FROM vec_embeddings")).scalar_one()
    finally:
        database.close()


def test_the_coalescing_index_is_a_real_constraint_not_a_convention(db: Database) -> None:
    """A second ``pending`` row for one source must be impossible at the storage layer."""
    source = IndexSource.field("r1", "t1", "summary")
    with db.write() as conn:
        conn.execute(
            text(
                "INSERT INTO embedding_jobs (record_id, source_type, field_key, status, "
                "enqueued_at, updated_at) VALUES ('r1', 'field', 'summary', 'pending', 'T', 'T')"
            )
        )
    with pytest.raises(Exception) as excinfo:  # noqa: B017 - the driver's IntegrityError
        with db.write() as conn:
            conn.execute(
                text(
                    "INSERT INTO embedding_jobs (record_id, source_type, field_key, status, "
                    "enqueued_at, updated_at) VALUES ('r1', 'field', 'summary', 'pending', "
                    "'T', 'T')"
                )
            )
    assert "UNIQUE" in str(excinfo.value).upper()
    assert source.field_key == "summary"


def test_nulls_are_coalesced_so_comment_sources_also_coalesce(db: Database) -> None:
    """NULLs are distinct to a unique index, so the nullable columns are coalesced.

    Without ``coalesce(field_key, '')`` in the index expression, every comment job
    would have ``field_key IS NULL`` and no two would ever collide -- the coalescing
    index would silently do nothing for exactly half the sources it covers.
    """
    repo = SqliteSearchRepository()
    source = IndexSource.comment("r1", "t1", "c1")
    with db.write() as conn:
        repo.enqueue(conn, source, "T1")
        repo.enqueue(conn, source, "T2")
        repo.enqueue(conn, source, "T3")
    assert len(jobs_for(db, source)) == 1


# ------------------------------------------------------- the extension check


def test_the_startup_check_passes_on_this_build(db: Database) -> None:
    check_search_extensions(db)


def test_the_startup_check_message_names_what_is_missing() -> None:
    """Fails fast naming the dependency, not with a bare "no such module: vec0"."""

    class FakeConn:
        def execute(self, statement: Any) -> Any:
            if "vec_version" in str(statement):
                raise RuntimeError("no such function: vec_version")
            return _Rows([("ENABLE_FTS5",)])

    class FakeDb:
        def read(self) -> Any:
            from contextlib import contextmanager

            @contextmanager
            def cm() -> Any:
                yield FakeConn()

            return cm()

    with pytest.raises(SearchExtensionError) as excinfo:
        check_search_extensions(FakeDb())  # type: ignore[arg-type]
    message = str(excinfo.value)
    assert "sqlite-vec" in message
    assert "loadable extensions" in message
    assert "docs/DATA_MODEL.md section 10" in message


class _Rows:
    def __init__(self, rows: list[tuple[str, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, ...]]:
        return self._rows

    def scalar_one(self) -> Any:  # pragma: no cover - not reached in this path
        return self._rows[0][0]


def test_fts5_absence_is_reported_too(db: Database) -> None:
    """The other half of the same check, so a build missing FTS5 is named as such."""

    class FakeConn:
        def execute(self, statement: Any) -> Any:
            if "vec_version" in str(statement):
                return _Rows([("v0.1.9",)])
            return _Rows([("ENABLE_JSON1",), ("THREADSAFE=1",)])

    class FakeDb:
        def read(self) -> Any:
            from contextlib import contextmanager

            @contextmanager
            def cm() -> Any:
                yield FakeConn()

            return cm()

    with pytest.raises(SearchExtensionError) as excinfo:
        check_search_extensions(FakeDb())  # type: ignore[arg-type]
    assert "FTS5" in str(excinfo.value)
    assert "ENABLE_FTS5" in str(excinfo.value)


# ------------------------------------------------------- the status endpoint


def test_the_status_endpoint_reports_counts_and_the_revision_qualified_model(
    db: Database, search_services: ServiceBundle, tmp_path: Path
) -> None:
    """Read at the service layer; the route is a one-line adapter over exactly this."""
    search_services.schema.create_object_type(
        make_actor(),
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the test suite.",
        key_prefix="ART",
        fields=KITCHEN_SINK_FIELDS,
    )
    search_services.records.create_record(
        make_actor(), "artifact", {"title": "One", "summary": "A short summary."}
    )
    status = search_services.search_index.status()
    assert status.semantic_enabled is True
    assert status.embedding_model == "fake-model@test"
    assert status.pending_jobs == 1
    assert status.running_jobs == 0
    assert status.indexed_chunks == 0
    assert status.failed_jobs == []

    clock = FakeClock()
    drain(make_worker(db, search_services, clock), clock)
    after = search_services.search_index.status()
    assert after.pending_jobs == 0
    assert after.indexed_chunks == 1
    assert after.stale_chunks == 0


def test_stale_chunks_counts_rows_from_another_model(
    db: Database, search_services: ServiceBundle
) -> None:
    """A model swap makes every existing row visibly stale rather than silently mixed."""
    search_services.schema.create_object_type(
        make_actor(),
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the test suite.",
        key_prefix="ART",
        fields=KITCHEN_SINK_FIELDS,
    )
    search_services.records.create_record(
        make_actor(), "artifact", {"title": "One", "summary": "A short summary."}
    )
    clock = FakeClock()
    drain(make_worker(db, search_services, clock), clock)
    assert search_services.search_index.status().stale_chunks == 0

    with db.write() as conn:
        conn.execute(text("UPDATE embeddings SET model_id = 'an-older-model@0000000'"))
    assert search_services.search_index.status().stale_chunks == 1


def test_the_status_route_requires_admin_and_returns_the_documented_shape(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """A REST/UI-only capability (docs/MCP_TOOLS.md section 8); ``admin`` scope."""
    assert (
        client.get("/api/v1/admin/search-index", headers=auth(api_tokens["read"])).status_code
        == 403
    )
    assert (
        client.get("/api/v1/admin/search-index", headers=auth(api_tokens["write"])).status_code
        == 403
    )

    response = client.get("/api/v1/admin/search-index")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "pending_jobs",
        "running_jobs",
        "failed_jobs",
        "indexed_chunks",
        "stale_chunks",
        "embedding_model",
        "semantic_enabled",
    }
    # This app runs with embedding disabled, so the configured name stands in for a
    # provider that was never constructed, and the field is still a string.
    assert body["semantic_enabled"] is False
    assert body["embedding_model"] == "bge-small-en-v1.5"
    assert body["failed_jobs"] == []


def test_a_new_write_supersedes_a_prior_failure_and_clears_the_status_report(
    db: Database,
    search_services: ServiceBundle,
    fake_provider: FakeEmbeddingProvider,
) -> None:
    """Section 10, rule 1, end to end.

    Without it, a source that failed five times, was rewritten, and then indexed
    successfully is reported as failed forever -- and re-indexing everything is not what
    fixes that.
    """
    search_services.schema.create_object_type(
        make_actor(),
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the test suite.",
        key_prefix="ART",
        fields=KITCHEN_SINK_FIELDS,
    )
    record = search_services.records.create_record(
        make_actor(), "artifact", {"title": "Doomed", "summary": "Text that will not embed."}
    )
    clock = FakeClock()
    worker = make_worker(db, search_services, clock)

    for _ in range(5):
        fake_provider.fail_next = RuntimeError("provider exploded")
        clock.advance(400)
        worker.run_once(clock.now)

    failed = search_services.search_index.status()
    assert failed.failed_jobs != []
    assert failed.failed_jobs[0].record_key == record.key
    assert failed.failed_jobs[0].field_key == "summary"
    assert failed.failed_jobs[0].attempts == 5

    # A new write to the same source supersedes that failure.
    search_services.records.update_record(
        make_actor(), record.key, {"summary": "Replacement text that embeds fine."}
    )
    assert [j.status for j in jobs_for(db, field_source(record, "summary"))] == ["pending"]

    drain(worker, clock)
    cleared = search_services.search_index.status()
    assert cleared.failed_jobs == [], "a past failure must not be reported permanently"
    assert cleared.pending_jobs == 0
    assert cleared.indexed_chunks == 1
    assert job_rows(db) == []
    assert index_counts(db).failed_jobs == 0


def test_a_new_write_clears_the_failure_over_http_on_the_status_endpoint(
    tmp_path: Path,
) -> None:
    """Section 10 rule 1, driven end to end over ``GET /api/v1/admin/search-index``.

    The service-layer test above proves the same rule against ``status()``; this one
    proves it through the surface an operator actually reads, over a real app with
    embedding enabled. Without rule 1 a source that failed five times, was rewritten,
    and then indexed successfully is reported as failed forever -- and re-indexing
    everything is not what fixes that.
    """
    from glosswork.app import DATABASE_FILENAME, create_app
    from glosswork.config import Settings

    provider = FakeEmbeddingProvider()
    app = create_app(
        Settings(data_dir=tmp_path / "data", embedding_enabled=True),
        embedding_provider=provider,
    )
    with TestClient(app) as http:
        services: ServiceBundle = app.state.services
        tokens = mint_scope_tokens(services)
        http.headers["Authorization"] = f"Bearer {tokens['admin']}"

        services.schema.create_object_type(
            make_actor(),
            key="artifact",
            name="Artifact",
            name_plural="Artifacts",
            description="A tracked work artifact used by the test suite.",
            key_prefix="ART",
            fields=KITCHEN_SINK_FIELDS,
        )
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "Doomed", "summary": "Text that will not embed."}
        )

        database = Database.connect(tmp_path / "data" / DATABASE_FILENAME)
        try:
            clock = FakeClock()
            worker = make_worker(database, services, clock)
            for _ in range(5):
                provider.fail_next = RuntimeError("provider exploded")
                clock.advance(400)
                worker.run_once(clock.now)

            failed = http.get("/api/v1/admin/search-index").json()
            assert len(failed["failed_jobs"]) == 1
            assert failed["failed_jobs"][0]["record_key"] == record.key
            assert failed["failed_jobs"][0]["attempts"] == 5
            assert failed["semantic_enabled"] is True
            assert failed["embedding_model"] == "fake-model@test"

            services.records.update_record(
                make_actor(), record.key, {"summary": "Replacement text that embeds fine."}
            )
            drain(worker, clock)

            cleared = http.get("/api/v1/admin/search-index").json()
            assert cleared["failed_jobs"] == [], "a past failure must not be reported permanently"
            assert cleared["pending_jobs"] == 0
            assert cleared["indexed_chunks"] == 1
        finally:
            database.close()
