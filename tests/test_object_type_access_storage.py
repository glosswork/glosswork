"""The storage the third authorization axis sits on.

Two columns live in migration 1, edited in place before any deployment existed, so no
database anywhere applied its prior form, and the genuinely new
tables land as migration 7 because the runner's numbered discipline (DD-6, FR-P6) is
about applying changes in order rather than about pretending a table was always there.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from glosswork.db import Database
from glosswork.migrations import MIGRATIONS, applied_migrations, run_migrations


@pytest.fixture
def fresh(tmp_path: Path):
    database = Database.connect(tmp_path / "fresh.sqlite3")
    run_migrations(database)
    yield database
    database.close()


def _columns(db: Database, table: str) -> dict[str, str]:
    with db.read() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return {row[1]: row[2] for row in rows}


def _sql_for(db: Database, name: str) -> str:
    with db.read() as conn:
        row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": name}
        ).first()
    assert row is not None, f"{name} does not exist"
    return str(row[0])


# ------------------------------------------------------------------ the runner


def test_migration_seven_is_numbered_seven_and_idempotent(tmp_path: Path) -> None:
    database = Database.connect(tmp_path / "idempotent.sqlite3")
    try:
        assert run_migrations(database) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
        assert run_migrations(database) == []
        assert applied_migrations(database) == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
        assert {m.number for m in MIGRATIONS} == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}
    finally:
        database.close()


# ------------------------------------------- migration 1's columns


def test_principals_role_check_admits_creator(fresh: Database) -> None:
    """The `creator` role value. SQLite cannot alter a CHECK, so this is an in-place
    edit to migration 1 rather than a 12-table rebuild."""
    sql = _sql_for(fresh, "principals")
    assert "'creator'" in sql
    with fresh.write() as conn:
        conn.execute(
            text(
                "INSERT INTO principals (id, type, display_name, role, is_active, created_at) "
                "VALUES ('p-creator', 'user', 'Creator', 'creator', 1, '2026-09-02T00:00:00Z')"
            )
        )
    with fresh.read() as conn:
        role = conn.execute(text("SELECT role FROM principals WHERE id = 'p-creator'")).scalar()
    assert role == "creator"


def test_principals_role_check_still_refuses_an_unknown_role(fresh: Database) -> None:
    """**Scope fence**: widening the CHECK to three values must not
    turn it into no CHECK. This cannot fail against the unfixed tree by construction,
    so it is labelled and not counted as a measured assertion."""
    with pytest.raises(IntegrityError), fresh.write() as conn:
        conn.execute(
            text(
                "INSERT INTO principals (id, type, display_name, role, is_active, created_at) "
                "VALUES ('p-x', 'user', 'X', 'superuser', 1, '2026-09-02T00:00:00Z')"
            )
        )


def test_object_types_carry_a_default_level_closed_by_default(fresh: Database) -> None:
    """No backfill and no split-brain: every type is uniformly closed."""
    columns = _columns(fresh, "object_types")
    assert columns["default_level"] == "TEXT"
    assert "'none'" in _sql_for(fresh, "object_types")


def test_object_types_default_level_check_refuses_a_bad_level(fresh: Database) -> None:
    _seed_principal(fresh)
    with pytest.raises(IntegrityError), fresh.write() as conn:
        conn.execute(text(_object_type_insert("t-bad", "bad", "BAD", "owner")))


def _seed_principal(db: Database, principal_id: str = "owner") -> None:
    with db.write() as conn:
        conn.execute(
            text(
                "INSERT OR IGNORE INTO principals "
                "(id, type, display_name, role, is_active, created_at) "
                f"VALUES ('{principal_id}', 'user', 'Owner', 'admin', 1, '2026-09-02T00:00:00Z')"
            )
        )


def _object_type_insert(type_id: str, key: str, prefix: str, owner: str, level: str = "bad") -> str:
    return (
        "INSERT INTO object_types (id, key, name, name_plural, description, key_prefix, "
        "key_counter, is_deleted, default_level, created_at, created_by, updated_at, "
        f"updated_by) VALUES ('{type_id}', '{key}', 'N', 'Ns', 'D', '{prefix}', 0, 0, "
        f"'{level}', '2026-09-02T00:00:00Z', '{owner}', '2026-09-02T00:00:00Z', '{owner}')"
    )


# ------------------------------------------------------- migration 7's tables


def test_object_type_grants_table_and_indexes_exist(fresh: Database) -> None:
    assert set(_columns(fresh, "object_type_grants")) == {
        "id",
        "object_type_id",
        "principal_id",
        "level",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
    }
    with fresh.read() as conn:
        names = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'index'")
            ).all()
        }
    assert "ux_object_type_grants" in names
    assert "ix_object_type_grants_principal" in names


def test_a_grant_is_unique_per_type_and_principal(fresh: Database) -> None:
    _seed_principal(fresh)
    with fresh.write() as conn:
        conn.execute(text(_object_type_insert("t-1", "widget", "WID", "owner", level="none")))
        conn.execute(text(_grant_insert("g-1", "t-1", "owner", "read")))
    with pytest.raises(IntegrityError), fresh.write() as conn:
        conn.execute(text(_grant_insert("g-2", "t-1", "owner", "write")))


def test_grant_level_check_admits_none_and_refuses_junk(fresh: Database) -> None:
    """`none` is an explicit deny, not the absence of a row: it overrides a permissive
    ``default_level``, which is how one person is excluded from an open type."""
    _seed_principal(fresh)
    with fresh.write() as conn:
        conn.execute(text(_object_type_insert("t-2", "gadget", "GAD", "owner", level="read")))
        conn.execute(text(_grant_insert("g-3", "t-2", "owner", "none")))
    with pytest.raises(IntegrityError), fresh.write() as conn:
        conn.execute(text(_grant_insert("g-4", "t-2", "owner", "sudo")))


def _grant_insert(grant_id: str, type_id: str, principal: str, level: str) -> str:
    return (
        "INSERT INTO object_type_grants (id, object_type_id, principal_id, level, "
        "created_at, created_by, updated_at, updated_by) VALUES "
        f"('{grant_id}', '{type_id}', '{principal}', '{level}', '2026-09-02T00:00:00Z', "
        f"'{principal}', '2026-09-02T00:00:00Z', '{principal}')"
    )


def test_record_attachments_join_table_exists(fresh: Database) -> None:
    """Attachments carry no record back-reference, so one is materialized here."""
    assert set(_columns(fresh, "record_attachments")) == {
        "record_id",
        "attachment_id",
        "field_key",
    }
    sql = _sql_for(fresh, "record_attachments")
    assert "PRIMARY KEY (record_id, field_key, attachment_id)" in sql
    with fresh.read() as conn:
        names = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'index'")
            ).all()
        }
    assert "ix_record_attachments_attachment" in names
