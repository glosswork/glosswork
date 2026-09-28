"""SQLite engine and connection discipline (docs/DATA_MODEL.md section 14, DD-5).

- WAL journal mode, ``foreign_keys = ON``, and a busy timeout on every connection.
- pysqlite's implicit transaction handling is disabled; transactions are explicit.
- Write transactions open with ``BEGIN IMMEDIATE`` so lock acquisition happens up
  front instead of failing mid-transaction; reads use a plain deferred ``BEGIN``.

Services own transaction boundaries via :meth:`Database.write` so one logical write
(record change, links, comment counters, audit rows) commits atomically.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlite_vec
from sqlalchemy import Connection, Engine, create_engine, event, text

BUSY_TIMEOUT_MS = 5000

# Why this is process-global rather than per-``Database``: whether the interpreter's
# SQLite build allows loadable extensions at all is a property of the build, not of a
# database file, and the connect listener that discovers it has no clean channel back
# to the caller. The connect listener deliberately does not raise -- a raise there
# makes every connection fail, including the ones ``/readyz`` needs to report *why* --
# so the failure is stashed here and ``check_search_extensions`` turns it into the
# fail-fast message that names what is missing (DD-31).
_extension_load_error: str | None = None


class Database:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @classmethod
    def connect(cls, path: Path) -> Database:
        engine = create_engine(
            f"sqlite:///{path}",
            # sqlite3 connections are used by whichever thread checks them out of the
            # pool; access is serialized per-connection by the pool itself.
            connect_args={"check_same_thread": False},
        )
        _install_discipline(engine)
        return cls(engine)

    @contextmanager
    def read(self) -> Iterator[Connection]:
        """A read connection. Transactions autobegin with a deferred BEGIN."""
        with self.engine.connect() as conn:
            yield conn

    @contextmanager
    def write(self) -> Iterator[Connection]:
        """One atomic write transaction, opened with BEGIN IMMEDIATE."""
        with self.engine.connect() as conn:
            conn = conn.execution_options(gw_write=True)
            with conn.begin():
                yield conn

    @contextmanager
    def raw_connection(self) -> Iterator[Any]:
        """A DBAPI connection with no transaction open on it (DD-36).

        ``VACUUM INTO`` is the one statement this application issues that SQLite
        refuses inside a transaction, and both :meth:`read` and :meth:`write` open one:
        the ``begin`` listener below fires for either, and setting SQLAlchemy's
        ``AUTOCOMMIT`` isolation level does not suppress it (measured -- both forms
        raise "cannot VACUUM from within a transaction"). Handing the repository the
        pooled DBAPI connection directly is the narrowest way through, and it keeps the
        statement itself inside the repository layer where DD-2 requires it.

        The connection carries the same discipline as every other one, including the
        loaded sqlite-vec extension, because it comes from the same pool.
        """
        raw = self.engine.raw_connection()
        try:
            yield raw
        finally:
            raw.close()

    def close(self) -> None:
        self.engine.dispose()


def _install_discipline(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection: Any, _record: Any) -> None:
        # Disable pysqlite's implicit BEGIN entirely; we emit our own.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()
        _load_sqlite_vec(dbapi_connection)

    @event.listens_for(engine, "begin")
    def _on_begin(conn: Connection) -> None:
        if conn.get_execution_options().get("gw_write"):
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            conn.exec_driver_sql("BEGIN")


def _load_sqlite_vec(dbapi_connection: Any) -> None:
    """Load sqlite-vec onto one connection (DD-31).

    Every connection needs it, not just the worker's: migration 6 creates
    ``vec_embeddings`` on whichever connection runs it, and any request handler that
    touches the vector table sees the same virtual table only if the extension is
    loaded there too. Extension loading is re-disabled immediately afterwards, so an
    application bug cannot turn ``load_extension()`` into an arbitrary-code-execution
    primitive over the SQL surface.
    """
    global _extension_load_error
    try:
        dbapi_connection.enable_load_extension(True)
        try:
            sqlite_vec.load(dbapi_connection)
        finally:
            dbapi_connection.enable_load_extension(False)
    except AttributeError:
        _extension_load_error = (
            "this Python's sqlite3 module was built without enable_load_extension() support"
        )
    except Exception as exc:  # pragma: no cover - platform-specific load failure
        _extension_load_error = f"{type(exc).__name__}: {exc}"


class SearchExtensionError(Exception):
    """Raised at startup when sqlite-vec or FTS5 is unavailable (DD-31)."""


def check_search_extensions(db: Database) -> None:
    """Fail fast unless sqlite-vec and FTS5 are both available (DD-31).

    Not gated on ``GW_EMBEDDING_ENABLED``, and it cannot be: migration 6 creates
    ``vec_embeddings`` unconditionally, so a deployment that turned embedding off
    because it cannot run the model still needs the extension to start at all
    (docs/DATA_MODEL.md section 13). Raises :class:`SearchExtensionError` naming what
    is missing rather than letting a bare "no such module: vec0" escape from the
    migration runner.
    """
    missing: list[str] = []
    with db.read() as conn:
        try:
            conn.execute(text("SELECT vec_version()")).scalar_one()
        except Exception:
            detail = f" ({_extension_load_error})" if _extension_load_error else ""
            missing.append(
                "sqlite-vec: the vec0 extension did not load on this connection"
                f"{detail}. The sqlite-vec wheel supplies the extension; SQLite itself "
                "must permit loading it."
            )
        options = {
            row[0] for row in conn.execute(text("SELECT * FROM pragma_compile_options()")).all()
        }
        if "ENABLE_FTS5" not in options:
            missing.append(
                "FTS5: this SQLite build does not report ENABLE_FTS5, so the "
                "fts_content virtual table cannot be created."
            )
    if missing:
        raise SearchExtensionError(
            "Glosswork requires a SQLite build with loadable extensions and FTS5 "
            "(docs/DATA_MODEL.md section 10):\n  " + "\n  ".join(missing)
        )
