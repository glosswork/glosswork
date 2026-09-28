"""Existing migrations are never edited (DD-6), and this guard fails when one changes.

The runner recognizes an applied migration by its number alone (``pending_migrations``), so a
database that already ran migration N never receives an edit to migration N. Once a deployment
holds real data, an in-place edit silently forks the schema: the deployment keeps the old shape,
a fresh install gets the new one. So every migration in ``MIGRATIONS`` is checked against
``tests/migration_hashes.txt``, one ``number name sha256`` line per migration.

A migration is not a file (all of them live in ``src/glosswork/migrations.py``, beside the runner
and its Python comments), so the digest covers a ``Migration`` entry's number, name and rendered
statements, and nothing else. Whitespace and SQL comments inside a statement count, because
SQLite stores a ``CREATE`` statement's text as written.

A red guard means the edit is wrong, never the manifest. Adding a migration appends its line:

    uv run python -m tests.test_migrations_are_forward_only >> tests/migration_hashes.txt

That command prints a line only for a migration the manifest does not list yet, so it can append
and can never re-baseline an edited migration.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from glosswork.migrations import MIGRATIONS, Migration

pytestmark = pytest.mark.structural

#: Resolved from this file, never from the working directory, so a scratch copy's guard reads
#: that copy's manifest.
MANIFEST = Path(__file__).resolve().with_name("migration_hashes.txt")

ADD_A_MIGRATION = (
    "a schema change is a new migration appended to MIGRATIONS (DD-6); revert the edit, "
    "never the manifest"
)


def migration_digest(migration: Migration) -> str:
    """sha256 of ``number\\0name\\0`` followed by each statement and a ``\\0``."""
    digest = hashlib.sha256(f"{migration.number}\0{migration.name}\0".encode())
    for statement in migration.statements:
        digest.update(statement.encode() + b"\0")
    return digest.hexdigest()


def manifest_lines(path: Path = MANIFEST) -> list[tuple[int, str, str]]:
    """``(number, name, digest)`` per line, skipping ``#`` lines and blank lines."""
    lines = []
    for raw in path.read_text().splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        parts = raw.split()
        assert len(parts) == 3, f"manifest line is not `number name sha256`: {raw!r}"
        lines.append((int(parts[0]), parts[1], parts[2]))
    return lines


def test_the_manifest_exists() -> None:
    assert MANIFEST.is_file(), f"{MANIFEST} is missing; every migration needs a line in it"


def test_every_manifest_line_matches_its_migration() -> None:
    by_number = {migration.number: migration for migration in MIGRATIONS}
    for number, name, digest in manifest_lines():
        migration = by_number.get(number)
        assert migration is not None, f"migration {number} ({name}) was removed; {ADD_A_MIGRATION}"
        assert migration.name == name, (
            f"migration {number} was renamed from {name!r} to {migration.name!r}; {ADD_A_MIGRATION}"
        )
        assert migration_digest(migration) == digest, (
            f"migration {number} ({name}) was edited; {ADD_A_MIGRATION}"
        )


def test_every_migration_has_a_manifest_line() -> None:
    listed = {number for number, _, _ in manifest_lines()}
    unlisted = [migration.number for migration in MIGRATIONS if migration.number not in listed]
    assert unlisted == [], (
        f"migrations with no manifest line: {unlisted}; append them with "
        "`uv run python -m tests.test_migrations_are_forward_only >> tests/migration_hashes.txt`"
    )


def test_manifest_numbers_are_consecutive_from_one() -> None:
    numbers = [number for number, _, _ in manifest_lines()]
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"manifest numbers must run 1 to n, each once: {numbers}"
    )


def test_migration_numbers_run_from_one_once_each() -> None:
    """A duplicated number is invisible to the runner: on a fresh install the first entry with
    that number is applied and recorded, and the real migration is skipped."""
    numbers = [migration.number for migration in MIGRATIONS]
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"MIGRATIONS numbers must run 1 to n, in order, each once. MIGRATIONS numbers: {numbers}"
    )


def unlisted_manifest_lines(path: Path = MANIFEST) -> list[str]:
    """Lines for migrations the manifest does not list yet. Never one already listed."""
    listed = {number for number, _, _ in manifest_lines(path)} if path.is_file() else set()
    return [
        f"{migration.number} {migration.name} {migration_digest(migration)}"
        for migration in MIGRATIONS
        if migration.number not in listed
    ]


if __name__ == "__main__":
    for line in unlisted_manifest_lines():
        print(line)
