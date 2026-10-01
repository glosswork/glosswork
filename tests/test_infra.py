"""Infrastructure acceptance criteria (FR-P4, FR-P5, FR-P6; docs/DATA_MODEL.md
section 14).

Covers: migration idempotency and readiness reporting, SQLite connection discipline
(WAL, foreign keys, busy timeout, BEGIN IMMEDIATE writes vs. plain-BEGIN reads),
atomic rollback of a full logical write when the audit step fails, structured JSON
access logs carrying request id and actor fields, and uvicorn's own access logger
being disabled in favor of the application's JSON access log.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import Connection, event, text

from glosswork import entrypoint
from glosswork.actor import ActorContext, bootstrap_actor
from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.db import Database
from glosswork.errors import ValidationFailedError
from glosswork.migrations import run_migrations
from glosswork.repositories.models import AuditEvent, FieldDef, ObjectType
from glosswork.services import ServiceBundle


def _raise_runtime_error(conn: Connection, events: list[AuditEvent]) -> list[int]:
    """A drop-in replacement for ``AuditRepository.append`` that fails mid-write,
    so a caller can assert the surrounding logical write rolled back fully."""
    raise RuntimeError("simulated audit failure")


# --------------------------------------------------------------------------- FR-P6


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    """A second run against an already-migrated database is a no-op, and exactly
    one row per migration is tracked in ``schema_migrations`` (FR-P6)."""
    database = Database.connect(tmp_path / "fresh.sqlite3")
    try:
        first_run = run_migrations(database)
        assert first_run == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]

        second_run = run_migrations(database)
        assert second_run == []

        with database.read() as conn:
            rows = conn.execute(text("SELECT number FROM schema_migrations")).all()
        assert [row[0] for row in rows] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    finally:
        database.close()


# --------------------------------------------------------------------------- FR-P4


def test_readyz_reports_pending_migrations_then_ready(tmp_path: Path) -> None:
    """``/readyz`` is not-ready and lists pending migration numbers against a
    database that has never been migrated; a normal app start migrates it and
    ``/readyz`` reports ok (FR-P4)."""
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)

    unmigrated_app = create_app(settings, migrate_on_startup=False)
    with TestClient(unmigrated_app) as client:
        response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["pending_migrations"] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]

    migrated_app = create_app(settings)
    with TestClient(migrated_app) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ------------------------------------------------------------- connection discipline


def test_connection_discipline(db: Database) -> None:
    """WAL journal mode, foreign keys on, and a 5s busy timeout are set on every
    connection (docs/DATA_MODEL.md section 14)."""
    with db.read() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_write_transactions_open_with_begin_immediate(
    db: Database,
    services: ServiceBundle,
    actor: ActorContext,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
) -> None:
    """Write transactions open with ``BEGIN IMMEDIATE``; reads use a plain
    ``BEGIN`` (docs/DATA_MODEL.md section 14)."""
    statements: list[str] = []

    @event.listens_for(db.engine, "connect")
    def _trace(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.set_trace_callback(statements.append)

    # Drop any pooled connections opened by fixture setup so the next checkout is
    # a fresh connection carrying the trace callback registered above.
    db.engine.dispose()

    services.records.create_record(actor, "artifact", {"title": "x"})
    assert any(s.startswith("BEGIN IMMEDIATE") for s in statements)

    statements.clear()
    services.records.query_records(actor, "artifact")
    assert not any(s.startswith("BEGIN IMMEDIATE") for s in statements)


def test_an_empty_link_list_is_refused_before_a_write_transaction_opens(
    db: Database,
    services: ServiceBundle,
    actor: ActorContext,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
) -> None:
    """Refusing an empty link list costs no write lock, pinned rather than asserted in
    prose.

    ``link_records``'s empty-``to_refs`` guard ran before ``db.write()`` was entered
    until the body was extracted into ``link_records_in_txn``, which moved it after.
    The exception type and message were identical either way, so no black-box test
    could see it; what changed was that a refusable call started taking SQLite's
    single writer lock first. The guard is now duplicated, and this asserts the half
    that no other test can: refusing costs no ``BEGIN IMMEDIATE``.

    Found by the `verify` agent reading the diff, which no black-box test could have done.
    """
    record = services.records.create_record(actor, "artifact", {"title": "orphan"})

    statements: list[str] = []

    @event.listens_for(db.engine, "connect")
    def _trace(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.set_trace_callback(statements.append)

    # As above: drop pooled connections so the next checkout carries the callback.
    db.engine.dispose()

    with pytest.raises(ValidationFailedError):
        services.records.link_records(actor, record.key, "parent", [])

    assert not any(s.startswith("BEGIN IMMEDIATE") for s in statements), (
        f"an empty to_refs must be refused without opening a write transaction: {statements}"
    )


# --------------------------------------------------------- atomic logical writes


def test_record_create_rolls_back_fully_when_audit_write_fails(
    services: ServiceBundle,
    actor: ActorContext,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced failure in the audit step of ``create_record`` rolls back the
    record insert and does not burn a key sequence number
    (docs/DATA_MODEL.md section 14)."""
    first = services.records.create_record(actor, "artifact", {"title": "first"})
    assert first.key == "ART-001"

    monkeypatch.setattr(services.records._audit, "append", _raise_runtime_error)
    doomed_actor = bootstrap_actor(str(uuid.uuid4()))
    with pytest.raises(RuntimeError):
        services.records.create_record(doomed_actor, "artifact", {"title": "doomed"})
    monkeypatch.undo()  # restore the real audit repository before proceeding

    result = services.records.query_records(
        actor, "artifact", filter={"field": "title", "op": "eq", "value": "doomed"}
    )
    assert result.total_count == 0

    second = services.records.create_record(actor, "artifact", {"title": "second"})
    assert second.key == "ART-002"


def test_comment_add_rolls_back_fully_when_audit_write_fails(
    services: ServiceBundle,
    actor: ActorContext,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced failure in the audit step of ``add_comment`` rolls back the
    comment insert and the denormalized counters on the record
    (docs/DATA_MODEL.md section 14, FR-C8)."""
    record = services.records.create_record(actor, "artifact", {"title": "commented"})

    monkeypatch.setattr(services.comments._audit, "append", _raise_runtime_error)
    doomed_actor = bootstrap_actor(str(uuid.uuid4()))
    with pytest.raises(RuntimeError):
        services.comments.add_comment(doomed_actor, record.key, "doomed comment")
    monkeypatch.undo()  # restore the real audit repository before proceeding

    refreshed = services.records.get_record(actor, record.key)
    assert refreshed.comment_count == 0
    assert refreshed.last_comment_at is None
    assert services.comments.list_comments(actor, record.key) == []


def test_link_creation_rolls_back_fully_when_audit_write_fails(
    services: ServiceBundle,
    actor: ActorContext,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced failure in the audit step of ``link_records`` rolls back the link
    row and its auto-maintained reciprocal together (docs/DATA_MODEL.md section 14,
    FR-L3)."""
    parent = services.records.create_record(actor, "artifact", {"title": "parent"})
    child = services.records.create_record(actor, "artifact", {"title": "child"})

    monkeypatch.setattr(services.records._audit, "append", _raise_runtime_error)
    doomed_actor = bootstrap_actor(str(uuid.uuid4()))
    with pytest.raises(RuntimeError):
        services.records.link_records(doomed_actor, child.key, "parent", [parent.key])
    monkeypatch.undo()  # restore the real audit repository before proceeding

    assert services.records.list_links(actor, child.key, "parent") == []
    assert services.records.list_links(actor, parent.key, "children") == []
    # And the record can now be deleted: no inbound link survived the rollback.
    services.records.delete_record(actor, parent.key)


# --------------------------------------------------------------------------- FR-P5


def test_access_logs_are_structured_json_with_request_and_actor_fields(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """Access logs are emitted as JSON lines carrying request id, method, path,
    status, principal, and surface; the response carries the same request id in
    an ``x-request-id`` header, and a caller-supplied ``X-Request-ID`` is honored
    (FR-P5).

    Uses ``capfd``, not ``capsys``: structlog's ``PrintLoggerFactory`` binds to
    real stdout, so only file-descriptor-level capture sees these lines.
    """
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
    app = create_app(settings)
    with TestClient(app) as client:
        default_response = client.get("/healthz")
        fixed_response = client.get("/healthz", headers={"X-Request-ID": "fixed-id-123"})

    # The body, not the status. The SPA catch-all answers 200 for a route that
    # does not exist, so a status-only assertion here proved nothing about /healthz.
    assert default_response.json() == {"status": "ok"}
    assert fixed_response.json() == {"status": "ok"}
    assert "x-request-id" in default_response.headers
    assert fixed_response.headers["x-request-id"] == "fixed-id-123"

    captured = capfd.readouterr()
    events: list[dict[str, Any]] = []
    for line in captured.out.splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    access_events = [e for e in events if e.get("event") == "access"]
    assert access_events

    default_request_id = default_response.headers["x-request-id"]
    default_event = next(e for e in access_events if e.get("request_id") == default_request_id)
    assert default_event["method"] == "GET"
    assert default_event["path"] == "/healthz"
    assert default_event["status"] == 200
    assert "principal_id" in default_event
    assert default_event["surface"] == "api"

    fixed_event = next(e for e in access_events if e.get("request_id") == "fixed-id-123")
    assert fixed_event["request_id"] == "fixed-id-123"


def test_entrypoint_disables_uvicorns_own_access_logger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The container entry point disables uvicorn's own access logging, since its
    lines are not JSON; the application middleware is the sole source of access
    logs (FR-P5)."""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    entrypoint.main()

    assert captured["access_log"] is False
    assert captured["log_config"] is None


def test_entrypoint_passes_trusted_proxy_ips_to_uvicorn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`GW_TRUSTED_PROXY_IPS` (FR-P7) reaches uvicorn's own `forwarded_allow_ips`,
    which is what makes it honor `X-Forwarded-Proto`/`X-Forwarded-For` from the
    configured proxy rather than discarding them (uvicorn's own default trusts only
    loopback)."""
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GW_TRUSTED_PROXY_IPS", "10.0.0.5,10.0.0.6")
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    entrypoint.main()

    assert captured["forwarded_allow_ips"] == "10.0.0.5,10.0.0.6"
    assert captured["proxy_headers"] is True


def test_entrypoint_serves_one_process_even_when_web_concurrency_asks_for_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The image runs one process per database (DD-35, FR-P1), and ``WEB_CONCURRENCY``
    cannot change that.

    uvicorn reads ``WEB_CONCURRENCY`` whenever ``workers`` is left unset, and a second
    process on one database is a second embedding worker whose idle reclaim takes back
    rows the first still holds, plus a second in-memory login limiter. The assertion
    hands what the entry point passed to uvicorn's own ``Config``, so it measures the
    worker count uvicorn would actually start rather than the presence of an argument.
    """
    monkeypatch.setenv("GW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    captured: dict[str, Any] = {}

    def fake_run(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    entrypoint.main()

    # ``log_config=None`` as the entry point passes it: uvicorn's default would install
    # its own handlers on the ``uvicorn`` loggers and break the JSON-logging tests after
    # this one.
    config = uvicorn.Config(
        "glosswork.app:app", workers=captured.get("workers"), log_config=captured["log_config"]
    )
    assert config.workers == 1
    assert captured["workers"] == 1
