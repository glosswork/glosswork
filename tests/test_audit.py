"""Attribution and audit acceptance criteria.

Covers PRD FR-D1, FR-D2, FR-D4 and DD-4: the seeded bootstrap principal,
``ActorContext`` required on every service write method, correct audit-row shape on
record create and update, request-id correlation across a single logical call even
when it spans entities, attribution of every mutation, and the audit repository's
append/read-only surface (docs/DATA_MODEL.md sections 2 and 9).
"""

import inspect
import re
from pathlib import Path

from sqlalchemy import text

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID, ActorContext
from glosswork.db import Database
from glosswork.repositories.interfaces import AuditRepository
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.repositories.sqlite import SqliteAuditRepository
from glosswork.services import ServiceBundle
from glosswork.services.comments import CommentService
from glosswork.services.records import RecordService
from glosswork.services.schema import SchemaService
from tests.conftest import make_actor

# -- 1. bootstrap principal -----------------------------------------------------


def test_bootstrap_principal_is_seeded_by_the_initial_migration(db: Database) -> None:
    """The initial migration seeds the bootstrap principal (DD-4)."""
    with db.read() as conn:
        row = (
            conn.execute(
                text("SELECT type, role, is_active FROM principals WHERE id = :id"),
                {"id": BOOTSTRAP_PRINCIPAL_ID},
            )
            .mappings()
            .first()
        )
    assert row is not None
    assert row["type"] == "service_account"
    assert row["role"] == "admin"
    assert bool(row["is_active"]) is True


# -- 2. every write method requires ActorContext ---------------------------------

_ACTOR_METHODS: dict[type, tuple[str, ...]] = {
    SchemaService: (
        "create_object_type",
        "update_object_type",
        "add_field",
        "update_field",
        "propose_schema_change",
        "approve_proposal",
        "reject_proposal",
    ),
    RecordService: (
        "create_record",
        "update_record",
        "delete_record",
        "restore_record",
        "link_records",
        "unlink_records",
        "revert_field_change",
        "revert_to_version",
    ),
    CommentService: (
        "add_comment",
        "update_comment",
        "delete_comment",
    ),
}


def test_every_service_write_method_requires_actor_context() -> None:
    """DD-4: every write path takes an ``ActorContext`` with no default, so a call
    that cannot attribute itself fails to type-check rather than silently omitting
    attribution."""
    for service_cls, method_names in _ACTOR_METHODS.items():
        for method_name in method_names:
            method = getattr(service_cls, method_name)
            sig = inspect.signature(method)
            assert "actor" in sig.parameters, (
                f"{service_cls.__name__}.{method_name} has no 'actor' parameter."
            )
            param = sig.parameters["actor"]
            assert param.default is inspect.Parameter.empty, (
                f"{service_cls.__name__}.{method_name}'s actor parameter has a "
                "default; every write requires an explicit ActorContext (DD-4)."
            )
            # `from __future__ import annotations` is active in the service
            # modules, so annotations are unevaluated strings at this point.
            assert param.annotation == "ActorContext", (
                f"{service_cls.__name__}.{method_name}'s actor parameter must be "
                f"annotated ActorContext, got {param.annotation!r}."
            )


# -- 3. record create audit shape -------------------------------------------------


def test_record_create_writes_one_row_per_field_plus_a_create_row(
    db: Database,
    services: ServiceBundle,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
) -> None:
    """FR-D2: a create writes one row per non-empty field plus a `create` row for
    the record itself, all sharing one `request_id` (FR-D4)."""
    create_actor = make_actor()
    record = services.records.create_record(
        create_actor,
        "artifact",
        {"title": "Rollout plan", "points": 5, "status": "todo"},
    )

    audit = SqliteAuditRepository()
    with db.read() as conn:
        events = audit.for_request(conn, create_actor.request_id)

    assert len(events) == 4

    record_events = [e for e in events if e.field_key is None]
    assert len(record_events) == 1
    assert record_events[0].entity_type == "record"
    assert record_events[0].action == "create"
    assert record_events[0].record_id == record.id

    field_events = {e.field_key: e for e in events if e.field_key is not None}
    assert set(field_events) == {"title", "points", "status"}
    assert field_events["title"].new_value == "Rollout plan"
    assert field_events["points"].new_value == 5
    assert field_events["status"].new_value == "todo"
    for event in field_events.values():
        assert event.entity_type == "record"
        assert event.action == "create"
        assert event.record_id == record.id

    for event in events:
        assert event.request_id == create_actor.request_id
        assert event.principal_id == BOOTSTRAP_PRINCIPAL_ID
        assert event.principal_type == "service_account"
        assert event.auth_method == "pat"
        assert event.surface == "api"


# -- 4. record update audit shape -------------------------------------------------


def test_record_update_writes_one_row_per_changed_field(
    db: Database,
    services: ServiceBundle,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
) -> None:
    """FR-D2: an update writes one row per changed field, with old and new
    values, sharing one `request_id`."""
    record = services.records.create_record(
        make_actor(),
        "artifact",
        {"title": "Initial", "points": 1, "status": "todo"},
    )

    update_actor = make_actor()
    services.records.update_record(update_actor, record.key, {"points": 2, "status": "doing"})

    audit = SqliteAuditRepository()
    with db.read() as conn:
        events = audit.for_request(conn, update_actor.request_id)

    assert len(events) == 2
    by_field = {e.field_key: e for e in events}
    assert set(by_field) == {"points", "status"}
    assert all(e.action == "update" and e.entity_type == "record" for e in events)
    assert by_field["points"].old_value == 1
    assert by_field["points"].new_value == 2
    assert by_field["status"].old_value == "todo"
    assert by_field["status"].new_value == "doing"


# -- 5. one request_id spans entities within a single logical call ---------------


def test_one_logical_call_shares_a_request_id_across_entities(
    db: Database,
    services: ServiceBundle,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
) -> None:
    """FR-D4: a call whose effects span multiple entities still writes rows under
    one `request_id`: `link_records` writes a link plus its reciprocal inverse
    link, and a comment create writes a `comment` event, each correlated to the
    single actor that made the call."""
    record_a = services.records.create_record(make_actor(), "artifact", {"title": "A"})
    record_b = services.records.create_record(make_actor(), "artifact", {"title": "B"})

    link_actor = make_actor()
    services.records.link_records(link_actor, record_a.key, "parent", [record_b.key])

    audit = SqliteAuditRepository()
    with db.read() as conn:
        link_events = audit.for_request(conn, link_actor.request_id)
    assert len(link_events) == 2
    assert all(e.entity_type == "link" for e in link_events)
    assert all(e.action == "link" for e in link_events)
    assert all(e.request_id == link_actor.request_id for e in link_events)

    comment_actor = make_actor()
    services.comments.add_comment(comment_actor, record_a.key, "Kickoff scheduled.")
    with db.read() as conn:
        comment_events = audit.for_request(conn, comment_actor.request_id)
    assert len(comment_events) == 1
    assert comment_events[0].entity_type == "comment"
    assert comment_events[0].request_id == comment_actor.request_id


# -- 6. every mutation is attributed ----------------------------------------------


def test_every_mutation_is_attributed_to_its_actor(
    db: Database,
    services: ServiceBundle,
    sink_type: tuple[ObjectType, dict[str, FieldDef]],
) -> None:
    """FR-D1/FR-D4: every mutation in a create/update/delete/restore/comment
    sequence is attributed to the bootstrap principal, with auth_method and
    surface matching the actor that made each call."""
    create_actor = make_actor()
    record = services.records.create_record(create_actor, "artifact", {"title": "Doc"})
    update_actor = make_actor()
    services.records.update_record(update_actor, record.key, {"points": 3})
    delete_actor = make_actor()
    services.records.delete_record(delete_actor, record.key)
    restore_actor = make_actor()
    services.records.restore_record(restore_actor, record.key)
    comment_actor = make_actor()
    services.comments.add_comment(comment_actor, record.key, "Reopened for review.")

    actors: list[ActorContext] = [
        create_actor,
        update_actor,
        delete_actor,
        restore_actor,
        comment_actor,
    ]
    actor_by_request = {a.request_id: a for a in actors}

    audit = SqliteAuditRepository()
    with db.read() as conn:
        events = audit.for_record(conn, record.id)

    assert {e.request_id for e in events} == set(actor_by_request)
    for event in events:
        owner = actor_by_request[event.request_id]
        assert event.principal_id == BOOTSTRAP_PRINCIPAL_ID
        assert event.principal_id == owner.principal_id
        assert event.principal_type == owner.principal_type
        assert event.auth_method == owner.auth_method
        assert event.surface == owner.surface


# -- 7. the audit repository is append/read only -----------------------------------

_EXPECTED_AUDIT_METHODS = frozenset(
    {
        "append",
        "for_record",
        "record_update_events",
        "get_event",
        "for_request",
        "latest_id",
        "since",
        "search",
    }
)
_MUTATING_EXACT = frozenset({"update", "delete"})
_MUTATING_PREFIXES = ("update_", "delete_")


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name, _member in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


def _assert_no_mutating_operation(method_names: set[str]) -> None:
    """No method is itself an update/delete operation on audit rows.

    ``record_update_events`` is a read query returning past *field-update* audit
    rows (action == 'update'); it performs no write. A method that performed a
    mutating operation would, by this codebase's own naming convention (see
    ``update_field_row``, ``delete_link_row`` etc. in the repository layer), be
    named with a leading ``update_``/``delete_`` verb, not merely contain the
    word as a later component.
    """
    for name in method_names:
        assert name not in _MUTATING_EXACT, f"{name} is a mutating verb"
        assert not name.startswith(_MUTATING_PREFIXES), (
            f"{name} looks like a mutating operation against audit_events"
        )


def test_audit_repository_exposes_no_update_or_delete_operation() -> None:
    """FR-D1: audit rows are never updated or deleted by application code, and
    the repository's public surface reflects that (append and read only)."""
    sqlite_methods = _public_methods(SqliteAuditRepository)
    assert sqlite_methods == _EXPECTED_AUDIT_METHODS
    _assert_no_mutating_operation(sqlite_methods)

    protocol_methods = _public_methods(AuditRepository)
    assert protocol_methods == _EXPECTED_AUDIT_METHODS
    _assert_no_mutating_operation(protocol_methods)


# -- 8. no application code issues UPDATE/DELETE against audit_events -------------

_AUDIT_MUTATION_RE = re.compile(r"(?i)\b(update|delete\s+from)\s+audit_events\b")


def test_no_source_file_issues_update_or_delete_against_audit_events() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "glosswork"
    offenders = [
        path for path in src_root.rglob("*.py") if _AUDIT_MUTATION_RE.search(path.read_text())
    ]
    assert not offenders, (
        f"found UPDATE/DELETE against audit_events (FR-D1 violation) in: {offenders}"
    )


# -- 9. AuditRepository.search's combined WHERE clause is confined to one module (DD-2) ----

_AUDIT_SELECT_RE = re.compile(r"(?i)\bSELECT\b[^;]*\bFROM\s+audit_events\b")


def test_audit_search_query_is_built_only_in_the_repository_module() -> None:
    """DD-2: raw SQL for the audit browser's combined filter (record/principal/agent
    label/object type/field/time range, docs/MCP_TOOLS.md, AuditRepository.search)
    lives only in repositories/sqlite.py. Every other module reaches audit rows only
    through AuditRepository's typed methods.

    **migrations.py is excluded explicitly, not accidentally.** The docstring
    already claimed the exclusion, but only DDL had ever appeared there and DDL does not
    match this SELECT pattern, so nothing enforced it either way. Migration 9 backfills
    ``records.updated_by_agent_label_id`` from the newest field-writing audit row per
    record, which is a real ``SELECT ... FROM audit_events``. It belongs here rather than
    behind a repository method because **a migration is a frozen historical artifact**: it
    must keep doing exactly what it did the day it shipped, and a repository method is free
    to change shape, which would silently rewrite the past. It is also not what this rule
    guards -- it is a one-shot upgrade pass, not the audit browser's query path.
    """
    src_root = Path(__file__).resolve().parents[1] / "src" / "glosswork"
    allowed = {src_root / "repositories" / "sqlite.py", src_root / "migrations.py"}
    offenders = [
        path
        for path in src_root.rglob("*.py")
        if path not in allowed and _AUDIT_SELECT_RE.search(path.read_text())
    ]
    assert not offenders, (
        f"found a SELECT ... FROM audit_events outside repositories/sqlite.py (DD-2 "
        f"violation) in: {offenders}"
    )
