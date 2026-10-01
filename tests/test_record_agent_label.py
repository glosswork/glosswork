"""The record row carries the hand that last changed it, agent included.

``ActorContext`` has carried an agent label since DD-4 and DD-17 put one on every surface, but
the label only ever landed on *audit rows*. The record row carried ``updated_by`` -- a principal
id -- and nothing else, so ``docs/DESIGN.md`` 6.4's ``By`` column had no agent to render and the
``Avatar`` for one had no string to derive its two-letter code from.

Migration 9 denormalises the label onto the row, written wherever ``updated_by`` is written and
nowhere else. The two halves that are easy to get wrong, and that this file exists to pin:

* **Which writes set it.** Delete and restore do not move ``updated_by`` (``fieldtypes.py``
  defines it as the last *value* change), so they do not move the label either. Setting it there
  would leave a record whose person and agent disagree.
* **What the backfill counts.** ``audit_events.record_id`` is populated by comments, links,
  unlinks, deletes and restores as well as by field writes. "The newest audit event for this
  record" is *not* "the hand that last changed this row", and a backfill that assumed otherwise
  would have attributed a record to whoever last commented on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.migrations import MIGRATIONS, applied_migrations, run_migrations
from glosswork.services import ServiceBundle
from tests.conftest import auth, make_actor

ARTIFACT_FIELDS: list[dict[str, Any]] = [
    {
        "key": "title",
        "name": "Title",
        "type": "short_text",
        "description": "Short human-readable name for the artifact.",
    },
]


def _writer(principal_id: str, label_id: str | None, request_id: str) -> ActorContext:
    return ActorContext(
        principal_id=principal_id,
        principal_type="user",
        agent_label_id=label_id,
        auth_method="pat",
        surface="api",
        request_id=request_id,
        scope="write",
    )


@pytest.fixture
def workspace(services: ServiceBundle, actor: ActorContext) -> tuple[ServiceBundle, str, str]:
    """One artifact type, one member who may write to it, and that member's agent label."""
    services.schema.create_object_type(
        actor,
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the test suite.",
        key_prefix="ART",
        fields=ARTIFACT_FIELDS,
    )
    author = services.principals.create_user(
        actor,
        email="dana@example.com",
        display_name="Dana Okonkwo",
        role="member",
    )
    services.access.grant(actor, "artifact", author.id, "write")
    label = services.agent_labels.register_use(author.id, "sales-agent")
    return services, author.id, label.id


# ------------------------------------------------------------------ the write path


def test_a_labeled_create_marks_the_row(workspace: tuple[ServiceBundle, str, str]) -> None:
    services, principal_id, label_id = workspace
    record = services.records.create_record(
        _writer(principal_id, label_id, "req-1"), "artifact", {"title": "By an agent."}
    )
    assert record.updated_by_agent_label_id == label_id


def test_a_labeled_update_marks_the_row(workspace: tuple[ServiceBundle, str, str]) -> None:
    services, principal_id, label_id = workspace
    record = services.records.create_record(
        _writer(principal_id, None, "req-1"), "artifact", {"title": "By a person."}
    )
    assert record.updated_by_agent_label_id is None
    updated = services.records.update_record(
        _writer(principal_id, label_id, "req-2"),
        record.key,
        {"title": "Then by an agent."},
        expected_version=record.version,
    )
    assert updated.updated_by_agent_label_id == label_id


def test_a_persons_edit_clears_a_previous_agents_mark(
    workspace: tuple[ServiceBundle, str, str],
) -> None:
    """The column is written on every value-changing write *including an unlabeled one*.

    Left to default, an agent's mark would outlive the agent's involvement and the ``By`` column
    would keep naming it long after a person took the record over.
    """
    services, principal_id, label_id = workspace
    record = services.records.create_record(
        _writer(principal_id, label_id, "req-1"), "artifact", {"title": "By an agent."}
    )
    assert record.updated_by_agent_label_id == label_id
    updated = services.records.update_record(
        _writer(principal_id, None, "req-2"),
        record.key,
        {"title": "Taken over by a person."},
        expected_version=record.version,
    )
    assert updated.updated_by_agent_label_id is None


def test_a_delete_does_not_move_the_mark(workspace: tuple[ServiceBundle, str, str]) -> None:
    """Delete does not move ``updated_by``, so it does not move the label either.

    Writing it here would produce a row whose person and agent name different writes -- exactly
    the misattribution the column exists to prevent.
    """
    services, principal_id, label_id = workspace
    record = services.records.create_record(
        _writer(principal_id, label_id, "req-1"), "artifact", {"title": "By an agent."}
    )
    services.records.delete_record(_writer(principal_id, None, "req-2"), record.key)
    deleted = services.records.get_record(make_actor(), record.key, include_deleted=True)
    assert deleted.updated_by == principal_id
    assert deleted.updated_by_agent_label_id == label_id


def test_a_restore_does_not_move_the_mark(workspace: tuple[ServiceBundle, str, str]) -> None:
    services, principal_id, label_id = workspace
    record = services.records.create_record(
        _writer(principal_id, label_id, "req-1"), "artifact", {"title": "By an agent."}
    )
    services.records.delete_record(make_actor(), record.key)
    services.records.restore_record(make_actor(), record.key)
    restored = services.records.get_record(make_actor(), record.key)
    assert restored.updated_by_agent_label_id == label_id


def test_a_comment_does_not_move_the_mark(workspace: tuple[ServiceBundle, str, str]) -> None:
    """A comment appends an audit row carrying this record's id and changes no field value."""
    services, principal_id, label_id = workspace
    record = services.records.create_record(
        _writer(principal_id, None, "req-1"), "artifact", {"title": "By a person."}
    )
    services.comments.add_comment(
        _writer(principal_id, label_id, "req-2"), record.key, "An agent's note."
    )
    unchanged = services.records.get_record(make_actor(), record.key)
    assert unchanged.updated_by_agent_label_id is None


# ------------------------------------------------------------------ the wire


def test_the_query_response_resolves_the_label_to_text(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """The id on the row resolves through the *same response's* sidecar.

    Read out of the run rather than compared to a constant, so the criterion still holds when
    the fixture changes.
    """
    write_token = api_tokens["write"]
    created = client.post(
        "/api/v1/object-types",
        json={
            "key": "artifact",
            "name": "Artifact",
            "name_plural": "Artifacts",
            "description": "A tracked work artifact used by the test suite.",
            "key_prefix": "ART",
            "fields": ARTIFACT_FIELDS,
        },
    )
    assert created.status_code == 200, created.text

    written = client.post(
        "/api/v1/object-types/artifact/records",
        json={"title": "Written by an agent."},
        headers={**auth(write_token), "X-Agent-Label": "sales-agent"},
    )
    assert written.status_code == 200, written.text

    queried = client.post("/api/v1/object-types/artifact/query", json={})
    assert queried.status_code == 200, queried.text
    body = queried.json()
    row = body["records"][0]
    label_id = row["updated_by_agent_label_id"]
    assert label_id is not None, "the labeled write left the row unattributed"
    assert body["agent_labels"][label_id]["label"] == "sales-agent"


def test_the_label_sidecar_withholds_the_operational_keys(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """The sidecar discloses a label's *name*, which the audit trail already discloses to
    the same caller as an id. It does not disclose whose label it is, nor FR-I7's telemetry."""
    write_token = api_tokens["write"]
    client.post(
        "/api/v1/object-types",
        json={
            "key": "artifact",
            "name": "Artifact",
            "name_plural": "Artifacts",
            "description": "A tracked work artifact used by the test suite.",
            "key_prefix": "ART",
            "fields": ARTIFACT_FIELDS,
        },
    )
    client.post(
        "/api/v1/object-types/artifact/records",
        json={"title": "Written by an agent."},
        headers={**auth(write_token), "X-Agent-Label": "sales-agent"},
    )
    body = client.post("/api/v1/object-types/artifact/query", json={}).json()
    entry = next(iter(body["agent_labels"].values()))
    assert set(entry) == {"label", "display_name"}
    for withheld in ("principal_id", "call_count", "last_seen_at", "description", "created_at"):
        assert withheld not in entry, f"the sidecar leaked {withheld}"


# ------------------------------------------------------------------ the migration


def test_migration_nine_is_numbered_nine_and_idempotent(tmp_path: Path) -> None:
    database = Database.connect(tmp_path / "idempotent.sqlite3")
    try:
        assert run_migrations(database) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
        assert run_migrations(database) == []
        assert applied_migrations(database) == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
        assert {m.number for m in MIGRATIONS} == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
    finally:
        database.close()


# ------------------------------------------------------------------ the backfill
#
# The reason this section exists at all: "the newest audit event for this record" is NOT "the hand
# that last changed this row". `audit_events.record_id` is populated by comments, links, unlinks,
# deletes and restores as well as by field writes, so a backfill that took the newest event would
# attribute a record to whoever last commented on it, while `updated_by` still named whoever last
# edited a field -- an agent square rendered beside a person's name, on historical data, at upgrade
# time.
#
# These build a database at migration 8 and upgrade it, because that is the only way to exercise
# the backfill: a database created today is already at 9, with nothing to backfill. The perf
# measurement cannot cover this either -- `scripts/seed_perf.py` writes no agent labels at
# all, so it marks zero rows and measures only the scan.


def _db_at_migration_eight(tmp_path: Path) -> Database:
    """A database with migrations 1..8 applied and 9 deliberately withheld."""
    import glosswork.migrations as migrations_module

    db = Database.connect(tmp_path / "upgrade.sqlite3")
    original = migrations_module.MIGRATIONS
    migrations_module.MIGRATIONS = tuple(m for m in original if m.number <= 8)
    try:
        assert run_migrations(db) == [1, 2, 3, 4, 5, 6, 7, 8]
    finally:
        migrations_module.MIGRATIONS = original
    return db


def _seed_pre_upgrade_row(db: Database, *, record_id: str, key: str) -> tuple[str, str]:
    """One principal, one agent label, one object type and one record, by raw SQL.

    Raw SQL rather than the service layer on purpose: the services write through today's schema,
    which has the column this fixture must not have yet.
    """
    principal_id = "11111111-1111-4111-8111-111111111111"
    label_id = "22222222-2222-4222-8222-222222222222"
    type_id = "33333333-3333-4333-8333-333333333333"
    with db.write() as conn:
        conn.exec_driver_sql(
            "INSERT INTO principals (id, type, display_name, email, role, is_active, "
            "created_at) VALUES (?, 'user', 'Dana Okonkwo', 'dana@example.com', 'member', 1, "
            "'2026-09-01T10:00:00Z')",
            (principal_id,),
        )
        conn.exec_driver_sql(
            "INSERT INTO agent_labels (id, principal_id, label, verified, first_seen_at, "
            "last_seen_at, call_count) VALUES (?, ?, 'sales-agent', 0, '2026-09-01T10:00:00Z', "
            "'2026-09-01T10:00:00Z', 1)",
            (label_id, principal_id),
        )
        conn.exec_driver_sql(
            "INSERT INTO object_types (id, key, name, name_plural, description, key_prefix, "
            "created_at, created_by, updated_at, updated_by) VALUES (?, 'artifact', 'Artifact', "
            "'Artifacts', 'A tracked artifact.', 'ART', '2026-09-01T10:00:00Z', ?, "
            "'2026-09-01T10:00:00Z', ?)",
            (type_id, principal_id, principal_id),
        )
        conn.exec_driver_sql(
            "INSERT INTO records (id, object_type_id, key, key_seq, version, data, created_at, "
            "created_by, updated_at, updated_by) VALUES (?, ?, ?, 1, 1, '{}', "
            "'2026-09-01T10:00:00Z', ?, '2026-09-01T10:00:00Z', ?)",
            (record_id, type_id, key, principal_id, principal_id),
        )
    return principal_id, label_id


def _append_event(
    db: Database,
    *,
    record_id: str,
    principal_id: str,
    label_id: str | None,
    entity_type: str,
    action: str,
) -> None:
    with db.write() as conn:
        conn.exec_driver_sql(
            "INSERT INTO audit_events (ts, request_id, principal_id, principal_type, "
            "agent_label_id, auth_method, surface, entity_type, entity_id, record_id, action) "
            "VALUES ('2026-09-01T10:00:00Z', 'req', ?, 'user', ?, 'pat', 'api', ?, 'e', ?, ?)",
            (principal_id, label_id, entity_type, record_id, action),
        )


def _mark(db: Database, record_id: str) -> str | None:
    with db.read() as conn:
        row = conn.exec_driver_sql(
            "SELECT updated_by_agent_label_id FROM records WHERE id = ?", (record_id,)
        ).first()
    return None if row is None else row[0]


def test_the_backfill_attributes_a_pre_existing_agent_write(tmp_path: Path) -> None:
    db = _db_at_migration_eight(tmp_path)
    try:
        rid = "44444444-4444-4444-8444-444444444444"
        principal_id, label_id = _seed_pre_upgrade_row(db, record_id=rid, key="ART-001")
        _append_event(
            db,
            record_id=rid,
            principal_id=principal_id,
            label_id=label_id,
            entity_type="record",
            action="update",
        )
        assert run_migrations(db) == [9, 10, 11, 12, 13]
        assert _mark(db, rid) == label_id
    finally:
        db.close()


def test_the_backfill_ignores_a_comment_by_a_different_hand(tmp_path: Path) -> None:
    """The first case: an agent COMMENTS on a record a person last edited.

    The comment's audit row carries this record's id and is the newest event for it, but no
    field value changed, so the record's hand is still the person's.
    """
    db = _db_at_migration_eight(tmp_path)
    try:
        rid = "44444444-4444-4444-8444-444444444444"
        principal_id, label_id = _seed_pre_upgrade_row(db, record_id=rid, key="ART-001")
        _append_event(
            db,
            record_id=rid,
            principal_id=principal_id,
            label_id=None,
            entity_type="record",
            action="update",
        )
        _append_event(
            db,
            record_id=rid,
            principal_id=principal_id,
            label_id=label_id,
            entity_type="comment",
            action="create",
        )
        assert run_migrations(db) == [9, 10, 11, 12, 13]
        assert _mark(db, rid) is None
    finally:
        db.close()


def test_the_backfill_ignores_a_link_and_a_delete(tmp_path: Path) -> None:
    """The other cases. A reciprocal link is the sharpest: `link_records_in_txn` writes a second
    event stamped with the TARGET record's id, so a backfill counting links would mark both
    records though neither's data changed."""
    db = _db_at_migration_eight(tmp_path)
    try:
        rid = "44444444-4444-4444-8444-444444444444"
        principal_id, label_id = _seed_pre_upgrade_row(db, record_id=rid, key="ART-001")
        _append_event(
            db,
            record_id=rid,
            principal_id=principal_id,
            label_id=None,
            entity_type="record",
            action="update",
        )
        for entity_type, action in (("link", "link"), ("record", "delete"), ("record", "restore")):
            _append_event(
                db,
                record_id=rid,
                principal_id=principal_id,
                label_id=label_id,
                entity_type=entity_type,
                action=action,
            )
        assert run_migrations(db) == [9, 10, 11, 12, 13]
        assert _mark(db, rid) is None
    finally:
        db.close()


def test_the_backfill_takes_the_newest_field_write_not_the_first(tmp_path: Path) -> None:
    """An agent wrote, then a person took the record over. The person is the hand."""
    db = _db_at_migration_eight(tmp_path)
    try:
        rid = "44444444-4444-4444-8444-444444444444"
        principal_id, label_id = _seed_pre_upgrade_row(db, record_id=rid, key="ART-001")
        _append_event(
            db,
            record_id=rid,
            principal_id=principal_id,
            label_id=label_id,
            entity_type="record",
            action="create",
        )
        _append_event(
            db,
            record_id=rid,
            principal_id=principal_id,
            label_id=None,
            entity_type="record",
            action="update",
        )
        assert run_migrations(db) == [9, 10, 11, 12, 13]
        assert _mark(db, rid) is None
    finally:
        db.close()
