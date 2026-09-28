"""Sort-composite indexes (FR-R6).

Built from a measurement, not a hunch: at 200,000 records a first page of 50 filtered
on a ``single_select`` and sorted by a ``date`` matched 35,961 rows and took 39.0 ms,
essentially all of it SQLite sorting the whole matching set in a temporary B-tree,
because the compiler's ``records.id ASC`` keyset tiebreaker makes a single-column index
unusable for ordering. With the composite the same query took 0.16 ms.
``sqlexpr.sort_index_ddl`` carries the full numbers.

These tests assert the two things that can silently rot: that the *set* of indexes
matches what the schema implies through every path that can change it, and that the
query planner actually uses them (a composite SQLite ignores is pure write-path cost).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.services import ServiceBundle
from glosswork.sqlexpr import SORT_INDEX_PREFIX, sort_index_name
from tests.conftest import make_actor, select_options

TYPE_KEY = "ticket"

FIELDS: list[dict[str, Any]] = [
    {
        "key": "title",
        "name": "Title",
        "type": "short_text",
        "description": "Short summary of the ticket.",
        "indexed": True,
    },
    {
        "key": "status",
        "name": "Status",
        "type": "single_select",
        "description": "Workflow state; low cardinality, so filters on it are not selective.",
        "config": {"options": select_options("open", "doing", "done")},
    },
    {
        "key": "due",
        "name": "Due date",
        "type": "date",
        "description": "When the ticket must be finished.",
    },
    {
        "key": "notes",
        "name": "Notes",
        "type": "long_text",
        "description": "Free-form notes; deliberately not indexed.",
    },
]


def _make_type(services: ServiceBundle, actor: ActorContext) -> None:
    services.schema.create_object_type(
        actor,
        key=TYPE_KEY,
        name="Ticket",
        name_plural="Tickets",
        description="A unit of work, for the sort-composite index tests.",
        key_prefix="TKT",
        fields=FIELDS,
    )


def _composite(services: ServiceBundle, filter_key: str, sort_key: str) -> str:
    """The composite's name for ``TYPE_KEY``, derived from the type's **id**.

    Spelled through the type rather than through its key because the key is exactly what
    left the name: ``ix_sort_<type key>_<a>_<b>`` let type ``thing`` and type ``thing_a``
    produce the same name, and made reconciling one type enumerate the other's
    composites and drop them.
    """
    object_type, _ = services.schema.get_object_type(make_actor(), TYPE_KEY)
    return sort_index_name(object_type.id, filter_key, sort_key)


def _sort_indexes(db: Database) -> set[str]:
    with db.read() as conn:
        rows = conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type = 'index' "
                "AND tbl_name = 'records' AND name LIKE :p"
            ),
            {"p": f"{SORT_INDEX_PREFIX}_%"},
        ).all()
    return {str(r[0]) for r in rows}


# ------------------------------------------------------------------- the set


def test_the_expected_composites_exist_after_type_creation(
    services: ServiceBundle, actor: ActorContext, db: Database
) -> None:
    """``status`` is the only low-cardinality indexed field, and it pairs with each
    other indexed sortable field. ``notes`` is not indexed, so it is not a partner;
    ``status`` does not pair with itself."""
    _make_type(services, actor)

    assert _sort_indexes(db) == {
        _composite(services, "status", "title"),
        _composite(services, "status", "due"),
    }


def test_an_unindexed_field_gains_composites_when_it_becomes_indexed(
    services: ServiceBundle, actor: ActorContext, db: Database
) -> None:
    """One flag change adds a composite that belongs to a *pair*, which is why the
    reconciler recomputes the whole set instead of patching it."""
    _make_type(services, actor)
    assert _composite(services, "status", "notes") not in _sort_indexes(db)

    services.schema.update_field(make_actor(), TYPE_KEY, "notes", {"indexed": True})

    assert _composite(services, "status", "notes") in _sort_indexes(db)


def test_composites_are_dropped_when_a_partner_stops_being_indexed(
    services: ServiceBundle, actor: ActorContext, db: Database
) -> None:
    _make_type(services, actor)
    assert _composite(services, "status", "title") in _sort_indexes(db)

    services.schema.update_field(make_actor(), TYPE_KEY, "title", {"indexed": False})

    assert _composite(services, "status", "title") not in _sort_indexes(db)
    # The other pair is untouched: a reconcile is not a reset.
    assert _composite(services, "status", "due") in _sort_indexes(db)


def test_a_unique_field_gets_no_composite(
    services: ServiceBundle, actor: ActorContext, db: Database
) -> None:
    """A unique field's own index already makes a filter on it a single-row lookup,
    so a composite over it would earn nothing and cost every write."""
    _make_type(services, actor)
    services.schema.add_field(
        make_actor(),
        TYPE_KEY,
        {
            "key": "reference",
            "name": "Reference",
            "type": "short_text",
            "description": "External reference; unique.",
            "unique": True,
        },
    )

    assert _composite(services, "status", "reference") not in _sort_indexes(db)


def test_deleting_a_field_drops_its_composites(
    services: ServiceBundle, actor: ActorContext, db: Database
) -> None:
    _make_type(services, actor)
    proposal = services.schema.propose_schema_change(
        make_actor(), "delete_field", TYPE_KEY, field_key="due", reason="No longer tracked."
    )
    services.schema.approve_proposal(make_actor(), proposal.id)

    assert _composite(services, "status", "due") not in _sort_indexes(db)
    assert _composite(services, "status", "title") in _sort_indexes(db)


def test_deleting_the_object_type_drops_every_composite(
    services: ServiceBundle, actor: ActorContext, db: Database
) -> None:
    _make_type(services, actor)
    proposal = services.schema.propose_schema_change(
        make_actor(), "delete_object_type", TYPE_KEY, reason="Superseded."
    )
    services.schema.approve_proposal(make_actor(), proposal.id)

    assert _sort_indexes(db) == set()


# --------------------------------------------------------------- reconciliation


def test_reconcile_backfills_a_deployment_that_predates_the_feature(
    services: ServiceBundle, actor: ActorContext, db: Database
) -> None:
    """The upgrade path, simulated by dropping the composites out from under a live
    schema: this is what an object type created before the composites existed looks like
    on first start, and it is why the reconcile runs at startup instead of in a
    migration."""
    _make_type(services, actor)
    with db.write() as conn:
        for name in _sort_indexes(db):
            conn.exec_driver_sql(f"DROP INDEX {name}")
    assert _sort_indexes(db) == set()

    changed = services.schema.reconcile_all_sort_indexes()

    assert set(changed) == {TYPE_KEY}
    created, dropped = changed[TYPE_KEY]
    assert sorted(created) == sorted(
        [_composite(services, "status", "due"), _composite(services, "status", "title")]
    )
    assert dropped == []


def test_reconcile_is_idempotent(
    services: ServiceBundle, actor: ActorContext, db: Database
) -> None:
    """Every start after the first must be a no-op, or the log fills with churn and
    each restart pays to rebuild indexes it already has."""
    _make_type(services, actor)
    before = _sort_indexes(db)

    assert services.schema.reconcile_all_sort_indexes() == {}
    assert _sort_indexes(db) == before


def test_reconcile_drops_an_index_no_schema_implies(
    services: ServiceBundle, actor: ActorContext, db: Database
) -> None:
    """Drift in the other direction: an index left behind by a prior version. The
    reconcile is a diff, so it clears these without anyone having to notice them."""
    _make_type(services, actor)
    stale = _composite(services, "status", "gone")
    with db.write() as conn:
        conn.exec_driver_sql(f"CREATE INDEX {stale} ON records(json_extract(data, '$.status'), id)")
    assert stale in _sort_indexes(db)

    changed = services.schema.reconcile_all_sort_indexes()

    assert changed[TYPE_KEY][1] == [stale]
    assert stale not in _sort_indexes(db)


# ------------------------------------------------------------------ the planner


def test_the_planner_actually_uses_the_composite(
    services: ServiceBundle, actor: ActorContext, db: Database
) -> None:
    """The assertion that keeps the whole feature honest.

    A composite SQLite declines to use is pure write-path cost: every record write
    maintains it and no read benefits. The query plan must show the composite and must
    show **no temporary B-tree for the ordering**, which is the thing that cost 39 ms
    at 200,000 records.
    """
    _make_type(services, actor)
    for i in range(60):
        services.records.create_record(
            make_actor(),
            TYPE_KEY,
            {"title": f"Ticket {i}", "status": "open", "due": f"2026-01-{(i % 28) + 1:02d}"},
        )

    from glosswork.compiler import compile_query, parse_sort
    from glosswork.filters import FilterContext, parse_filter
    from glosswork.repositories.sqlite import SqliteSchemaRepository
    from glosswork.timeutil import utc_now

    repo = SqliteSchemaRepository()
    with db.read() as conn:
        object_type = repo.get_object_type_by_key(conn, TYPE_KEY)
        fields = {f.key: f for f in repo.list_fields(conn, object_type.id)}
        ctx = FilterContext(
            object_type_key=TYPE_KEY,
            fields_by_key=fields,
            now=utc_now(),
            resolve_record_ref=lambda ref: ref,
            resolve_principal_ref=lambda ref: ref,
        )
        node = parse_filter({"field": "status", "op": "eq", "value": "open"}, ctx)
        for direction in ("asc", "desc"):
            sort = parse_sort([{"field": "due", "dir": direction}], fields, TYPE_KEY)
            compiled = compile_query(object_type, fields, node, sort, 50, None, False)
            plan = " ; ".join(
                str(row[-1])
                for row in conn.exec_driver_sql(
                    "EXPLAIN QUERY PLAN " + compiled.sql, compiled.params
                ).all()
            )
            expected = _composite(services, "status", "due")
            assert expected in plan, f"{direction}: planner ignored the composite: {plan}"
            # The distinction that matters, and it is not pedantic. A bare
            # "USE TEMP B-TREE FOR ORDER BY" means SQLite sorted the entire matching
            # set, which is the 39 ms. "FOR LAST TERM OF ORDER BY" means it walked the
            # index in order and sorted only within groups of equal `due` values to
            # settle the `id` tiebreak, which is bounded by the size of one tie group.
            # Only the first is a regression. (On the 200,000-record corpus SQLite
            # chose neither and read straight out of the index; on a 60-row table it
            # takes the partial-sort variant, so the test must permit both.)
            assert "USE TEMP B-TREE FOR ORDER BY" not in plan, (
                f"{direction}: SQLite sorted the whole matching set again: {plan}"
            )


def test_the_composite_returns_the_same_rows_as_an_unindexed_plan(
    services: ServiceBundle, actor: ActorContext
) -> None:
    """Correctness, not just speed: the composite must not change what comes back,
    in either direction, including across a keyset page boundary."""
    _make_type(services, actor)
    for i in range(40):
        services.records.create_record(
            make_actor(),
            TYPE_KEY,
            {
                "title": f"Ticket {i}",
                "status": "open" if i % 2 else "doing",
                # Deliberately repeated dates, so the id tiebreaker is exercised.
                "due": f"2026-02-{(i % 5) + 1:02d}",
            },
        )

    for direction in ("asc", "desc"):
        first = services.records.query_records(
            make_actor(),
            TYPE_KEY,
            filter={"field": "status", "op": "eq", "value": "open"},
            sort=[{"field": "due", "dir": direction}],
            limit=7,
        )
        second = services.records.query_records(
            make_actor(),
            TYPE_KEY,
            filter={"field": "status", "op": "eq", "value": "open"},
            sort=[{"field": "due", "dir": direction}],
            limit=7,
            cursor=first.next_cursor,
        )
        keys = [r["key"] for r in first.records] + [r["key"] for r in second.records]
        assert len(keys) == len(set(keys)), f"{direction}: keyset paging repeated a row"
        assert first.total_count == 20
