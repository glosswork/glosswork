"""In-house sequential migration runner (FR-P6, DD-6).

Numbered migrations tracked in ``schema_migrations``, applied idempotently at startup.
Each migration runs in one BEGIN IMMEDIATE transaction together with the row that
records it, so a crash mid-migration leaves the database at the prior version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.db import Database
from glosswork.timeutil import format_datetime, utc_now


@dataclass(frozen=True, slots=True)
class Migration:
    number: int
    name: str
    statements: tuple[str, ...]


_INITIAL_SCHEMA = (
    # -- identity and attribution (docs/DATA_MODEL.md section 2)
    """
    CREATE TABLE principals (
      id            TEXT PRIMARY KEY,
      type          TEXT NOT NULL CHECK (type IN ('user', 'service_account')),
      display_name  TEXT NOT NULL,
      email         TEXT UNIQUE,
      role          TEXT NOT NULL DEFAULT 'member'
                      CHECK (role IN ('admin', 'creator', 'member')),
      auth_provider TEXT,
      external_id   TEXT,
      password_hash TEXT,
      is_active     INTEGER NOT NULL DEFAULT 1,
      description   TEXT,
      created_at    TEXT NOT NULL,
      created_by    TEXT REFERENCES principals(id),
      workspace_id  TEXT NOT NULL DEFAULT 'default'
    )
    """,
    """
    CREATE UNIQUE INDEX ix_principals_external
      ON principals(auth_provider, external_id) WHERE external_id IS NOT NULL
    """,
    """
    CREATE TABLE agent_labels (
      id            TEXT PRIMARY KEY,
      principal_id  TEXT NOT NULL REFERENCES principals(id),
      label         TEXT NOT NULL,
      display_name  TEXT,
      description   TEXT,
      verified      INTEGER NOT NULL DEFAULT 0,
      first_seen_at TEXT NOT NULL,
      last_seen_at  TEXT NOT NULL,
      call_count    INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE UNIQUE INDEX ix_agent_labels ON agent_labels(principal_id, label)",
    # -- schema definition (docs/DATA_MODEL.md section 3)
    """
    CREATE TABLE object_types (
      id            TEXT PRIMARY KEY,
      key           TEXT NOT NULL UNIQUE,
      name          TEXT NOT NULL,
      name_plural   TEXT NOT NULL,
      description   TEXT NOT NULL,
      key_prefix    TEXT NOT NULL UNIQUE,
      key_counter   INTEGER NOT NULL DEFAULT 0,
      icon          TEXT,
      is_deleted    INTEGER NOT NULL DEFAULT 0,
      -- 007, AD-24: what a principal with no grant row gets on this type. Declared
      -- here rather than added by a later migration because nothing has ever been
      -- deployed (the same standing under which M6b amended migration 6's partition
      -- keys in place), so there is no pre-007 database whose access a backfill would
      -- have to preserve. Every type is uniformly closed by default, which removes the
      -- split-brain a backfill would have created between types existing before 007
      -- and types created after it.
      default_level TEXT NOT NULL DEFAULT 'none'
                      CHECK (default_level IN ('none', 'read', 'write', 'admin')),
      -- 010, AD-26: the field whose value labels a record for a human. Holds a fields.key
      -- (immutable, and available before the field row exists -- see 010 P1), not a
      -- fields.id, so it carries no FK and may dangle; NULL and dangling both fall back to
      -- the derived rule in services/base.py::display_field.
      display_field_key TEXT,
      created_at    TEXT NOT NULL,
      created_by    TEXT NOT NULL REFERENCES principals(id),
      updated_at    TEXT NOT NULL,
      updated_by    TEXT NOT NULL REFERENCES principals(id),
      workspace_id  TEXT NOT NULL DEFAULT 'default'
    )
    """,
    """
    CREATE TABLE fields (
      id             TEXT PRIMARY KEY,
      object_type_id TEXT NOT NULL REFERENCES object_types(id),
      key            TEXT NOT NULL,
      name           TEXT NOT NULL,
      description    TEXT NOT NULL,
      type           TEXT NOT NULL,
      position       INTEGER NOT NULL,
      is_required    INTEGER NOT NULL DEFAULT 0,
      is_unique      INTEGER NOT NULL DEFAULT 0,
      is_indexed     INTEGER NOT NULL DEFAULT 0,
      embed          INTEGER NOT NULL DEFAULT 0,
      default_value  TEXT,
      config         TEXT NOT NULL DEFAULT '{}',
      is_deleted     INTEGER NOT NULL DEFAULT 0,
      created_at     TEXT NOT NULL,
      created_by     TEXT NOT NULL REFERENCES principals(id),
      updated_at     TEXT NOT NULL,
      updated_by     TEXT NOT NULL REFERENCES principals(id)
    )
    """,
    "CREATE UNIQUE INDEX ix_fields_key ON fields(object_type_id, key) WHERE is_deleted = 0",
    """
    CREATE TABLE schema_proposals (
      id              TEXT PRIMARY KEY,
      status          TEXT NOT NULL CHECK
                        (status IN ('pending', 'approved', 'rejected', 'expired')),
      change_type     TEXT NOT NULL CHECK (change_type IN
                        ('delete_field', 'change_field_type', 'delete_object_type',
                         'remove_enum_option', 'tighten_constraint')),
      target_type_id  TEXT REFERENCES object_types(id),
      target_field_id TEXT REFERENCES fields(id),
      payload         TEXT NOT NULL,
      impact          TEXT NOT NULL,
      snapshot_ref    TEXT,
      reason          TEXT,
      proposed_at     TEXT NOT NULL,
      proposed_by     TEXT NOT NULL REFERENCES principals(id),
      proposed_agent  TEXT REFERENCES agent_labels(id),
      decided_at      TEXT,
      decided_by      TEXT REFERENCES principals(id),
      decision_note   TEXT
    )
    """,
    # -- records (docs/DATA_MODEL.md section 5)
    """
    CREATE TABLE records (
      id             TEXT PRIMARY KEY,
      object_type_id TEXT NOT NULL REFERENCES object_types(id),
      key            TEXT NOT NULL UNIQUE,
      key_seq        INTEGER NOT NULL,
      version        INTEGER NOT NULL DEFAULT 1,
      data           TEXT NOT NULL DEFAULT '{}',
      created_at     TEXT NOT NULL,
      created_by     TEXT NOT NULL REFERENCES principals(id),
      updated_at     TEXT NOT NULL,
      updated_by     TEXT NOT NULL REFERENCES principals(id),
      deleted_at     TEXT,
      deleted_by     TEXT REFERENCES principals(id),
      comment_count  INTEGER NOT NULL DEFAULT 0,
      last_comment_at TEXT,
      workspace_id   TEXT NOT NULL DEFAULT 'default'
    )
    """,
    "CREATE INDEX ix_records_type_live ON records(object_type_id) WHERE deleted_at IS NULL",
    "CREATE INDEX ix_records_updated ON records(updated_at)",
    # -- relations (docs/DATA_MODEL.md section 6)
    """
    CREATE TABLE record_links (
      id             TEXT PRIMARY KEY,
      field_id       TEXT NOT NULL REFERENCES fields(id),
      from_record_id TEXT NOT NULL REFERENCES records(id),
      to_record_id   TEXT NOT NULL REFERENCES records(id),
      position       INTEGER NOT NULL DEFAULT 0,
      created_at     TEXT NOT NULL,
      created_by     TEXT NOT NULL REFERENCES principals(id)
    )
    """,
    """
    CREATE UNIQUE INDEX ix_links_unique
      ON record_links(field_id, from_record_id, to_record_id)
    """,
    "CREATE INDEX ix_links_from ON record_links(from_record_id)",
    "CREATE INDEX ix_links_to ON record_links(to_record_id)",
    # -- comments (docs/DATA_MODEL.md section 7)
    """
    CREATE TABLE comments (
      id             TEXT PRIMARY KEY,
      record_id      TEXT NOT NULL REFERENCES records(id),
      body           TEXT NOT NULL,
      author_id      TEXT NOT NULL REFERENCES principals(id),
      agent_label_id TEXT REFERENCES agent_labels(id),
      created_at     TEXT NOT NULL,
      updated_at     TEXT NOT NULL,
      edited         INTEGER NOT NULL DEFAULT 0,
      deleted_at     TEXT,
      deleted_by     TEXT REFERENCES principals(id)
    )
    """,
    """
    CREATE INDEX ix_comments_record
      ON comments(record_id, created_at) WHERE deleted_at IS NULL
    """,
    # -- audit (docs/DATA_MODEL.md section 9)
    """
    CREATE TABLE audit_events (
      id             INTEGER PRIMARY KEY AUTOINCREMENT,
      ts             TEXT NOT NULL,
      request_id     TEXT NOT NULL,
      principal_id   TEXT NOT NULL REFERENCES principals(id),
      principal_type TEXT NOT NULL,
      agent_label_id TEXT REFERENCES agent_labels(id),
      auth_method    TEXT NOT NULL CHECK (auth_method IN ('session', 'pat')),
      surface        TEXT NOT NULL CHECK (surface IN ('ui', 'api', 'mcp')),
      entity_type    TEXT NOT NULL,
      entity_id      TEXT NOT NULL,
      record_id      TEXT,
      object_type_id TEXT,
      action         TEXT NOT NULL,
      field_key      TEXT,
      old_value      TEXT,
      new_value      TEXT,
      note           TEXT
    )
    """,
    "CREATE INDEX ix_audit_record ON audit_events(record_id, id)",
    "CREATE INDEX ix_audit_actor ON audit_events(principal_id, id)",
    "CREATE INDEX ix_audit_agent ON audit_events(agent_label_id, id)",
    "CREATE INDEX ix_audit_ts ON audit_events(ts)",
    "CREATE INDEX ix_audit_type ON audit_events(object_type_id, id)",
    # -- bootstrap principal (DD-4): every pre-auth write attributes to this row.
    f"""
    INSERT OR IGNORE INTO principals
      (id, type, display_name, role, is_active, description, created_at)
    VALUES
      ('{BOOTSTRAP_PRINCIPAL_ID}', 'service_account', 'bootstrap', 'admin', 1,
       'Seeded bootstrap principal for pre-authentication milestones (AD-6).',
       '1970-01-01T00:00:00Z')
    """,
)

_ATTACHMENTS_SCHEMA = (
    # -- attachments (docs/DATA_MODEL.md section 8)
    """
    CREATE TABLE attachments (
      id            TEXT PRIMARY KEY,
      sha256        TEXT NOT NULL,
      filename      TEXT NOT NULL,
      content_type  TEXT NOT NULL,
      byte_size     INTEGER NOT NULL,
      uploaded_at   TEXT NOT NULL,
      uploaded_by   TEXT NOT NULL REFERENCES principals(id)
    )
    """,
    "CREATE INDEX ix_attachments_sha ON attachments(sha256)",
)

_SAVED_VIEWS_SCHEMA = (
    # -- saved views (docs/DATA_MODEL.md section 11, FR-U3)
    """
    CREATE TABLE saved_views (
      id             TEXT PRIMARY KEY,
      object_type_id TEXT NOT NULL REFERENCES object_types(id),
      name           TEXT NOT NULL,
      description    TEXT,
      mode           TEXT NOT NULL DEFAULT 'table',
      config         TEXT NOT NULL,
      is_default     INTEGER NOT NULL DEFAULT 0,
      created_at     TEXT NOT NULL,
      created_by     TEXT NOT NULL REFERENCES principals(id),
      updated_at     TEXT NOT NULL,
      updated_by     TEXT NOT NULL REFERENCES principals(id)
    )
    """,
    """
    CREATE UNIQUE INDEX ix_views_default
      ON saved_views(object_type_id) WHERE is_default = 1
    """,
)

_ACCESS_TOKENS_SCHEMA = (
    # -- personal access tokens (docs/DATA_MODEL.md section 2, FR-I4)
    # The plaintext is shown once at mint time and never stored: only the sha256 of
    # the presented secret (unique, so a lookup is one indexed equality probe) and an
    # 8-char prefix for display.
    """
    CREATE TABLE access_tokens (
      id            TEXT PRIMARY KEY,
      principal_id  TEXT NOT NULL REFERENCES principals(id),
      name          TEXT NOT NULL,
      token_hash    TEXT NOT NULL UNIQUE,
      token_prefix  TEXT NOT NULL,
      scope         TEXT NOT NULL CHECK (scope IN ('read', 'write', 'admin')),
      expires_at    TEXT,
      last_used_at  TEXT,
      revoked_at    TEXT,
      created_at    TEXT NOT NULL,
      created_by    TEXT NOT NULL REFERENCES principals(id)
    )
    """,
    "CREATE INDEX ix_access_tokens_principal ON access_tokens(principal_id)",
)

_SESSIONS_SCHEMA = (
    # -- browser sessions (docs/DATA_MODEL.md section 2, FR-A3, DD-9, DD-10).
    # Shaped deliberately like access_tokens: the plaintext cookie value is shown once,
    # at issue time, and never stored — only its sha256. ``csrf_hash`` is the DD-10
    # double-submit token's sha256, session-bound in the same row rather than a
    # second table, so an attacker who plants a `gw_csrf` cookie still cannot make it
    # match a hash the server holds.
    """
    CREATE TABLE sessions (
      id            TEXT PRIMARY KEY,
      principal_id  TEXT NOT NULL REFERENCES principals(id),
      session_hash  TEXT NOT NULL UNIQUE,
      csrf_hash     TEXT NOT NULL,
      created_at    TEXT NOT NULL,
      expires_at    TEXT NOT NULL,
      last_seen_at  TEXT NOT NULL,
      revoked_at    TEXT
    )
    """,
    "CREATE INDEX ix_sessions_principal ON sessions(principal_id)",
)

_SEARCH_SCHEMA = (
    # -- search indexes (docs/DATA_MODEL.md section 10, FR-Q1 through FR-Q8).
    #
    # This migration runs **unconditionally**, exactly like every other one, so
    # ``GW_EMBEDDING_ENABLED=false`` does not make sqlite-vec optional: creating
    # ``vec_embeddings`` needs the extension loaded on the connection that runs this
    # statement, on every deployment, disabled or not (docs/DATA_MODEL.md section 13).
    # A deployment that turns embedding off because it cannot run the *model* still
    # needs a loadable-extension build of SQLite.
    """
    CREATE TABLE embeddings (
      id             INTEGER PRIMARY KEY AUTOINCREMENT,
      source_type    TEXT NOT NULL CHECK (source_type IN ('field', 'comment')),
      record_id      TEXT NOT NULL REFERENCES records(id),
      object_type_id TEXT NOT NULL,
      field_key      TEXT,
      comment_id     TEXT,
      chunk_index    INTEGER NOT NULL DEFAULT 0,
      chunk_text     TEXT NOT NULL,
      content_hash   TEXT NOT NULL,
      model_id       TEXT NOT NULL,
      created_at     TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_emb_record ON embeddings(record_id)",
    "CREATE INDEX ix_emb_hash ON embeddings(content_hash)",
    # Every read the worker and the status endpoint make is "the rows for this one
    # source"; without this they scan.
    """
    CREATE INDEX ix_emb_source
      ON embeddings(record_id, source_type, coalesce(field_key, ''), coalesce(comment_id, ''))
    """,
    "CREATE INDEX ix_emb_model ON embeddings(model_id)",
    # sqlite-vec virtual table; its rowid is ``embeddings.id``, which is why the
    # worker preserves an unchanged chunk's row instead of deleting and re-inserting
    # the source's rows (docs/DATA_MODEL.md section 10).
    #
    # Both partition keys are in this migration's own statement, added **in place**
    # before any deployment had applied it. A KNN constrained by
    # ``object_type_id IN (...)`` and ``model_id = ?`` is answered inside those
    # partitions, so scoping a search to a small object type, or querying part-way
    # through a model-swap re-index, never post-filters a global pool down to nothing.
    # Partition-key values are never updated (sqlite-vec forbids it): a source's rows
    # are inserted and deleted, never moved between types or models. A local
    # development database that applied the earlier form of this migration, without the
    # partition keys, has row 6 recorded and the old table, and must be deleted rather
    # than migrated.
    """
    CREATE VIRTUAL TABLE vec_embeddings USING vec0(
      embedding      float[384],
      object_type_id text partition key,
      model_id       text partition key
    )
    """,
    """
    CREATE TABLE embedding_jobs (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      record_id    TEXT NOT NULL,
      source_type  TEXT NOT NULL CHECK (source_type IN ('field', 'comment')),
      field_key    TEXT,
      comment_id   TEXT,
      status       TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'running', 'failed')),
      attempts     INTEGER NOT NULL DEFAULT 0,
      last_error   TEXT,
      enqueued_at  TEXT NOT NULL,
      updated_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX ix_jobs_pending ON embedding_jobs(status, id)",
    # Enqueue coalescing: at most one *pending* job per source, so ten edits before
    # the worker runs leave one job. NULLs are distinct to a unique index, so the
    # nullable columns are coalesced in the index expression. Every transition *into*
    # ``pending`` therefore has to yield to a live sibling (section 10, rule 4).
    """
    CREATE UNIQUE INDEX ux_jobs_pending_source
      ON embedding_jobs(record_id, source_type, coalesce(field_key, ''), coalesce(comment_id, ''))
      WHERE status = 'pending'
    """,
    """
    CREATE VIRTUAL TABLE fts_content USING fts5(
      body,
      record_id UNINDEXED,
      source_type UNINDEXED,
      field_key UNINDEXED,
      comment_id UNINDEXED,
      tokenize = 'porter unicode61'
    )
    """,
    # Stable integer identity per indexed source, a fifth table beside the four search
    # tables docs/DATA_MODEL.md section 10 describes. An ``fts_content`` row is inserted with
    # an explicit rowid taken from here, so replacing a source's keyword row is a
    # rowid delete rather than a predicate over UNINDEXED columns. FTS5 keeps
    # UNINDEXED columns in its content shadow table with no index on them, so
    # ``DELETE ... WHERE record_id = ?`` is a full scan: measured when this table was
    # introduced, 26 ms per source against 200,000 rows versus 0.007 ms by
    # rowid. That difference is not a micro-optimization here, because a schema change
    # writes or purges one FTS row per live record of its type, and the purge stays
    # inside the change's own transaction (DD-34); at PRD section 4's scale the scanning
    # form turns that into tens of minutes under SQLite's single writer lock.
    """
    CREATE TABLE search_sources (
      id             INTEGER PRIMARY KEY AUTOINCREMENT,
      record_id      TEXT NOT NULL,
      object_type_id TEXT NOT NULL,
      source_type    TEXT NOT NULL CHECK (source_type IN ('field', 'comment')),
      field_key      TEXT,
      comment_id     TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX ux_search_sources
      ON search_sources(record_id, source_type, coalesce(field_key, ''), coalesce(comment_id, ''))
    """,
    # Purging a field's or an object type's keyword rows is one indexed delete rather
    # than a scan, which is the same reason the table exists at all.
    "CREATE INDEX ix_search_sources_type ON search_sources(object_type_id, field_key)",
)

_ACCESS_CONTROL_SCHEMA = (
    # -- per-object-type access control (docs/DATA_MODEL.md section 2, DD-11).
    #
    # The third authorization axis. Credential scope (``access_tokens.scope``) says how
    # much a *credential* may do anywhere; ``principals.role`` says whether a principal
    # is a system administrator; this table says how much a *principal* may do to one
    # object type. ``object_types.default_level`` is what a principal with no row here
    # gets, and it is declared on migration 1's CREATE TABLE rather than added here,
    # because no deployment exists to migrate.
    #
    # ``level = 'none'`` is legal and meaningful: an **explicit deny** that overrides a
    # permissive ``default_level``. Without it a type open to everyone by default could
    # not exclude one person, which is the second most common thing an administrator
    # wants after "let one person in".
    """
    CREATE TABLE object_type_grants (
      id             TEXT PRIMARY KEY,
      object_type_id TEXT NOT NULL REFERENCES object_types(id),
      principal_id   TEXT NOT NULL REFERENCES principals(id),
      level          TEXT NOT NULL CHECK (level IN ('none', 'read', 'write', 'admin')),
      created_at     TEXT NOT NULL,
      created_by     TEXT NOT NULL REFERENCES principals(id),
      updated_at     TEXT NOT NULL,
      updated_by     TEXT NOT NULL REFERENCES principals(id)
    )
    """,
    "CREATE UNIQUE INDEX ux_object_type_grants ON object_type_grants(object_type_id, principal_id)",
    "CREATE INDEX ix_object_type_grants_principal ON object_type_grants(principal_id)",
    # -- attachment back-references (docs/DATA_MODEL.md section 8, DD-11).
    #
    # ``attachments`` carries no record id and no object type id: an attachment field's
    # value is a list of attachment ids inside ``records.data``, and blobs are shared by
    # content hash, so "the owning record" is legitimately zero, one, or many records
    # across several object types. Answering "may this caller read this attachment"
    # against an object type therefore needs a materialized reverse index, which is what
    # this is. Keyed on ``attachment_id`` rather than on the content hash: attachment
    # rows are per upload, so two principals uploading identical bytes get distinct ids
    # backed by one blob, and keying on the row leaks nothing across types.
    # ``sweep_orphan_blobs`` is unaffected -- it already counts by hash, not by row.
    """
    CREATE TABLE record_attachments (
      record_id     TEXT NOT NULL REFERENCES records(id),
      attachment_id TEXT NOT NULL REFERENCES attachments(id),
      field_key     TEXT NOT NULL,
      PRIMARY KEY (record_id, field_key, attachment_id)
    )
    """,
    "CREATE INDEX ix_record_attachments_attachment ON record_attachments(attachment_id)",
)

_CAPABILITY_TOKENS_SCHEMA = (
    # -- upload tickets (docs/DATA_MODEL.md section 2, DD-16)
    #
    # Three nullable columns on ``access_tokens``, and nothing else. A ticket is a real
    # personal access token at ``write`` scope with a short ``expires_at``, so it
    # inherits -- for free and already tested -- the sha256-only storage, the
    # ``token_prefix`` that makes a refusal legible, expiry enforcement in
    # ``PatTokenResolver``, the deactivated-principal check, and the revoke-everything
    # on a password reset or a deactivation. A separate table would have had to restate
    # all of it.
    #
    # ``capability`` narrows the DD-11 credential ceiling from "any write" to one named
    # operation; ``capability_data`` holds the small JSON object binding the ticket to a
    # filename and content type, which is what makes it unrepurposable; ``consumed_at``
    # is set inside the same transaction as the attachment insert, so a ticket is spent
    # exactly once.
    #
    # **Migration 4 is not amended in place.** A database that has already run it must
    # migrate forward, which is the lesson this project records about editing a shipped
    # migration: a perf corpus seeded before ``display_field_key`` was added to migration 1,
    # rather than to a new one, could not be opened at all afterwards.
    #
    # No index: lookup is by ``token_hash``, which is already UNIQUE, and the only query
    # the new columns serve is the opportunistic purge at mint time. If that purge ever
    # measurably needs one, it is added then, with the measurement recorded.
    "ALTER TABLE access_tokens ADD COLUMN capability TEXT",
    "ALTER TABLE access_tokens ADD COLUMN capability_data TEXT",
    "ALTER TABLE access_tokens ADD COLUMN consumed_at TEXT",
)


_RECORD_AGENT_LABEL_SCHEMA: tuple[str, ...] = (
    # -- the record row carries the hand that last changed it, agent included.
    #
    # ``ActorContext`` has carried an agent label since DD-4 and DD-17 put one on every
    # surface, but the label only ever landed on *audit rows*. The record row carried
    # ``updated_by`` -- a principal id -- so ``docs/DESIGN.md`` 6.4's ``By`` column had no
    # agent to render. This denormalises it onto the row, written wherever ``updated_by``
    # is written and nowhere else.
    #
    # **Migration 1 is not amended in place**, for the reason migration 4 records above: a
    # perf corpus seeded before ``display_field_key`` was added to migration 1, rather than
    # to a new one, could not be opened at all afterwards.
    #
    # No index. The column is read as part of a record row that is already being selected,
    # and it is never a query predicate: ``By`` is a display column that offers no sort
    # (docs/DESIGN.md 6.4). If a sort ever arrives it needs a pseudo-field
    # first, which is its own decision, and the index is added then with the measurement.
    "ALTER TABLE records ADD COLUMN updated_by_agent_label_id TEXT REFERENCES agent_labels(id)",
    #
    # **The backfill counts field writes and nothing else, and this is the load-bearing
    # half.** ``audit_events.record_id`` is populated by four different kinds of event and
    # only one of them moves ``updated_by``: comments (``entity_type='comment'``), links and
    # unlinks (``entity_type='link'``), deletes and restores (``action`` of ``delete`` or
    # ``restore``), and field writes. So "the newest audit event for this record" is *not*
    # "the hand that last changed this row". A backfill that took it would attribute a record
    # to whoever last commented on it while ``updated_by`` still named whoever last edited a
    # field -- the By column would then render an agent square beside a person's name, on
    # historical data, at upgrade time.
    #
    # Reciprocal links are the sharpest case: ``records.py::link_records_in_txn`` writes a
    # second link event stamped with the *target* record's id, so linking A to B would have
    # marked both A and B with the linker's label though neither's data changed.
    #
    # Schema-level events are excluded for free: they carry ``record_id IS NULL`` and so join
    # to no record.
    #
    # A revert IS counted, and correctly: FR-D6 implements it as a new forward-audited write
    # through ``update_record``, so it appends ``entity_type='record', action='update'``.
    #
    # ``ix_audit_record ON audit_events(record_id, id)`` (migration 1) covers the correlated
    # subquery, which runs once, at upgrade.
    """
    UPDATE records SET updated_by_agent_label_id = (
      SELECT a.agent_label_id FROM audit_events a
      WHERE a.record_id = records.id
        AND a.entity_type = 'record'
        AND a.action IN ('create', 'update')
      ORDER BY a.id DESC
      LIMIT 1
    )
    """,
)

# Existing migrations are never edited (DD-6). The runner recognizes an applied migration by
# its number alone, so a database that already ran one never receives an edit to it. A schema
# change is a new Migration appended below; a mistake in a merged one is corrected by another.
# tests/test_migrations_are_forward_only.py checks every entry against
# tests/migration_hashes.txt, including whitespace and SQL comments inside a statement. Append
# a new migration's line with the command in that module's docstring, and never change an
# existing one.
_AGENT_LABEL_ON_TOKEN_SCHEMA: tuple[str, ...] = (
    # -- the agent label a token was minted for (docs/DATA_MODEL.md section 2, DD-17)
    #
    # One nullable column on ``access_tokens``, and nothing else. Without it, attribution
    # reaches a deployment through one door, the ``X-Agent-Label`` request header, and
    # Claude's connector dialog accepts only header names Anthropic has approved -- that
    # name is not one of them. So every agent connected through that dialog, which is the
    # path every hosted Claude surface takes, would be attributed to nobody. A token is
    # already minted for one named place, so the label belongs on it.
    #
    # **A string, not an ``agent_labels.id``**. Registration stays where DD-17 put
    # it: at the edge, on use. Storing a registry id would mean registering at mint time,
    # which adds a second ``register_use`` call site and breaks the meta-test that exists
    # precisely to stop a second path appearing. It also avoids a foreign key from a
    # credential to a per-principal registry row.
    #
    # **Migrations 4 and 8 are not amended in place** (DD-6). A database that has already
    # run them migrates forward, which is the lesson this project records about editing a
    # shipped migration.
    #
    # No index and no backfill: every existing row is correctly ``NULL``, meaning "this
    # token carries no label of its own", and the column is only ever read through the row
    # already fetched by ``token_hash``, which is UNIQUE.
    "ALTER TABLE access_tokens ADD COLUMN agent_label TEXT",
)

_USAGE_COUNTERS_SCHEMA: tuple[str, ...] = (
    # -- operator usage counters (docs/DATA_MODEL.md section 2a, DD-39, FR-P10)
    #
    # One row per ``(tool_name, error_code)`` pair with a monotonic count, and **no
    # tenant string can reach it**. That is closed at write time rather than described:
    # ``services/usage.py::counter_key`` writes ``tool_name`` only when the tool catalog
    # knows the name and otherwise the literal ``unknown_tool``, and writes ``error_code``
    # only when the code is a key of ``errors.STATUS_BY_CODE`` and otherwise the literal
    # ``unknown``. The tool name in a ``tools/call`` is the caller's own string, so
    # without that rule this table would take an arbitrary 200-character value from
    # anyone holding a token and have unbounded cardinality.
    #
    # ``error_code`` is ``NOT NULL`` with the literal ``ok`` for a successful call rather
    # than nullable, because SQLite permits NULLs in a PRIMARY KEY and would then admit
    # duplicate success rows. The endpoint reports it back as JSON ``null``.
    #
    # No ``id`` column and no timestamps per row: the pair is the identity, the count is
    # the value, and when counting started is a property of the *table*, below.
    """
    CREATE TABLE usage_counters (
      tool_name  TEXT NOT NULL,
      error_code TEXT NOT NULL,
      count      INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY (tool_name, error_code)
    )
    """,
    # When counting started, so a hosting operator that sees the counters fall knows whether
    # the volume was replaced or usage genuinely dropped. Written by the migration itself,
    # which is the only way to make it mean "when this database began counting": a fresh
    # volume runs migration 11 at first boot and gets its own value, and an existing
    # deployment gets the moment it upgraded. The format is ``timeutil.DATETIME_FORMAT``.
    """
    CREATE TABLE usage_meta (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    )
    """,
    """
    INSERT INTO usage_meta (key, value)
      VALUES ('counting_since', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    """,
)

# The bootstrap account's seeded description, read from migration 1's own seed statement
# rather than written a second time, so migration 12 matches exactly what a fresh database
# received. The pattern must match once.
_SEEDED_BOOTSTRAP_DESCRIPTIONS = [
    match.group(1)
    for statement in _INITIAL_SCHEMA
    for match in re.finditer(r"'service_account', 'bootstrap', 'admin', 1,\s*'([^']*)',", statement)
]
assert len(_SEEDED_BOOTSTRAP_DESCRIPTIONS) == 1, _SEEDED_BOOTSTRAP_DESCRIPTIONS
_SEEDED_BOOTSTRAP_DESCRIPTION = _SEEDED_BOOTSTRAP_DESCRIPTIONS[0]
_BOOTSTRAP_DESCRIPTION = (
    "The deployment's own account: writes made before anyone signs in are attributed to it."
)

_BOOTSTRAP_DESCRIPTION_SCHEMA: tuple[str, ...] = (
    # -- the bootstrap account's description (docs/DATA_MODEL.md section 2, DD-4)
    #
    # Every administrator reads this description on the People page. It is replaced only
    # where it is still exactly the seeded text: a description an administrator has
    # changed is theirs, even one that keeps the seeded text's first words, so the
    # comparison is equality and never a prefix match. Like every migration, it writes no
    # audit event.
    f"""
    UPDATE principals
       SET description = '{_BOOTSTRAP_DESCRIPTION.replace("'", "''")}'
     WHERE id = '{BOOTSTRAP_PRINCIPAL_ID}'
       AND description = '{_SEEDED_BOOTSTRAP_DESCRIPTION.replace("'", "''")}'
    """,
)

MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "initial_schema", _INITIAL_SCHEMA),
    Migration(2, "attachments", _ATTACHMENTS_SCHEMA),
    Migration(3, "saved_views", _SAVED_VIEWS_SCHEMA),
    Migration(4, "access_tokens", _ACCESS_TOKENS_SCHEMA),
    Migration(5, "sessions", _SESSIONS_SCHEMA),
    Migration(6, "search_indexes", _SEARCH_SCHEMA),
    Migration(7, "access_control", _ACCESS_CONTROL_SCHEMA),
    Migration(8, "capability_tokens", _CAPABILITY_TOKENS_SCHEMA),
    Migration(9, "record_agent_label", _RECORD_AGENT_LABEL_SCHEMA),
    Migration(10, "agent_label_on_token", _AGENT_LABEL_ON_TOKEN_SCHEMA),
    Migration(11, "usage_counters", _USAGE_COUNTERS_SCHEMA),
    Migration(12, "bootstrap_description", _BOOTSTRAP_DESCRIPTION_SCHEMA),
)


def _ensure_migrations_table(db: Database) -> None:
    with db.write() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              number     INTEGER PRIMARY KEY,
              name       TEXT NOT NULL,
              applied_at TEXT NOT NULL
            )
            """
        )


def applied_migrations(db: Database) -> set[int]:
    with db.read() as conn:
        exists = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table' AND name = :n"),
            {"n": "schema_migrations"},
        ).first()
        if exists is None:
            return set()
        rows = conn.execute(text("SELECT number FROM schema_migrations")).all()
        return {row[0] for row in rows}


def pending_migrations(db: Database) -> list[Migration]:
    applied = applied_migrations(db)
    return [m for m in MIGRATIONS if m.number not in applied]


def run_migrations(db: Database) -> list[int]:
    """Apply all pending migrations in order. Idempotent; returns the numbers applied."""
    _ensure_migrations_table(db)
    applied: list[int] = []
    for migration in pending_migrations(db):
        with db.write() as conn:
            already = conn.execute(
                text("SELECT 1 FROM schema_migrations WHERE number = :n"),
                {"n": migration.number},
            ).first()
            if already is not None:
                continue
            for statement in migration.statements:
                conn.exec_driver_sql(statement)
            conn.execute(
                text(
                    "INSERT INTO schema_migrations (number, name, applied_at) "
                    "VALUES (:number, :name, :applied_at)"
                ),
                {
                    "number": migration.number,
                    "name": migration.name,
                    "applied_at": format_datetime(utc_now()),
                },
            )
        applied.append(migration.number)
    return applied
