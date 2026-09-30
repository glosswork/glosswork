"""The bootstrap account's shown description is plain words (migration 12).

Migration 1 seeds the bootstrap account with a description that every administrator sees on
the People page. Migration 12 replaces it, and only where it is still exactly the seeded
text: a description an administrator has changed is theirs, even when it keeps the seeded
text's first words. These build real databases, at migration 11 and at the latest version,
because that is the only way to exercise an upgrade.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.db import Database
from glosswork.migrations import run_migrations

NEW_DESCRIPTION = (
    "The deployment's own account: writes made before anyone signs in are attributed to it."
)


def _db_at_migration_eleven(tmp_path: Path) -> Database:
    """A database with migrations 1..11 applied and everything after deliberately withheld."""
    import glosswork.migrations as migrations_module

    db = Database.connect(tmp_path / "upgrade.sqlite3")
    original = migrations_module.MIGRATIONS
    migrations_module.MIGRATIONS = tuple(m for m in original if m.number <= 11)
    try:
        assert run_migrations(db) == list(range(1, 12))
    finally:
        migrations_module.MIGRATIONS = original
    return db


def _description(db: Database) -> str:
    with db.read() as conn:
        row = conn.exec_driver_sql(
            "SELECT description FROM principals WHERE id = ?", (BOOTSTRAP_PRINCIPAL_ID,)
        ).one()
    return str(row[0])


def _set_description(db: Database, description: str) -> None:
    with db.write() as conn:
        conn.exec_driver_sql(
            "UPDATE principals SET description = ? WHERE id = ?",
            (description, BOOTSTRAP_PRINCIPAL_ID),
        )


def test_a_fresh_database_ends_with_the_new_description(tmp_path: Path) -> None:
    db = Database.connect(tmp_path / "fresh.sqlite3")
    try:
        run_migrations(db)
        assert _description(db) == NEW_DESCRIPTION
    finally:
        db.close()


def test_an_upgraded_database_ends_with_the_new_description(tmp_path: Path) -> None:
    db = _db_at_migration_eleven(tmp_path)
    try:
        seeded = _description(db)
        assert seeded != NEW_DESCRIPTION
        assert run_migrations(db) == [12, 13]
        assert _description(db) == NEW_DESCRIPTION
    finally:
        db.close()


def test_an_administrators_own_description_is_kept(tmp_path: Path) -> None:
    db = _db_at_migration_eleven(tmp_path)
    try:
        _set_description(db, "Runs the nightly import.")
        assert run_migrations(db) == [12, 13]
        assert _description(db) == "Runs the nightly import."
    finally:
        db.close()


def test_an_edit_that_keeps_the_seeded_first_words_is_kept(tmp_path: Path) -> None:
    """Equality, not a prefix match: an administrator who kept the seeded text's first words
    and changed the rest still wrote their own description."""
    db = _db_at_migration_eleven(tmp_path)
    try:
        edited = "Seeded bootstrap principal, kept for the import job."
        _set_description(db, edited)
        assert run_migrations(db) == [12, 13]
        assert _description(db) == edited
    finally:
        db.close()


def test_the_rest_api_serves_the_new_description(client: TestClient) -> None:
    """What the People page reads: the principal list a browser session fetches."""
    response = client.get("/api/v1/principals")
    assert response.status_code == 200, response.text
    bootstrap = next(p for p in response.json()["principals"] if p["id"] == BOOTSTRAP_PRINCIPAL_ID)
    assert bootstrap["description"] == NEW_DESCRIPTION
