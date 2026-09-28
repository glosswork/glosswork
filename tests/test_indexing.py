"""Indexing acceptance tests (docs/DATA_MODEL.md section 5 binding constraints)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import glosswork.compiler as compiler_module
import glosswork.sqlexpr as sqlexpr_module
from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.compiler import compile_query, parse_sort
from glosswork.db import Database
from glosswork.errors import ValidationFailedError
from glosswork.filters import FilterContext, parse_filter
from glosswork.repositories.models import FieldDef, ObjectType, RecordRow
from glosswork.repositories.sqlite import SqliteRecordRepository, SqliteSchemaRepository
from glosswork.services import ServiceBundle
from glosswork.sqlexpr import index_ddl, index_name, json_field_expr, sort_index_name
from tests.conftest import make_actor, select_options

SinkType = tuple[ObjectType, dict[str, FieldDef]]


def _index_names(db: Database) -> set[str]:
    with db.read() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'index' AND name IS NOT NULL")
        ).all()
    return {row[0] for row in rows}


class TestIndexCreation:
    def test_auto_enable_for_selects_dates_user_refs(
        self, db: Database, sink_type: SinkType
    ) -> None:
        object_type, fields = sink_type
        auto = {"status", "due", "seen_at", "owner"}
        for key in auto:
            assert fields[key].is_indexed, f"{key} should auto-enable is_indexed"
        for key in ("title", "summary", "points", "score", "active", "homepage"):
            assert not fields[key].is_indexed, f"{key} should not be indexed by default"
        names = _index_names(db)
        for key in auto:
            assert index_name(object_type.id, key) in names
        assert index_name(object_type.id, "title") not in names
        # Relation and attachment fields never get value indexes, whatever their flags
        # say: `parent` and `files` can carry `is_indexed` and never have one.
        assert index_name(object_type.id, "parent") not in names
        assert index_name(object_type.id, "files") not in names

    def test_unique_fields_auto_enable_and_use_a_unique_index(
        self, db: Database, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        object_type, _ = sink_type
        field = services.schema.add_field(
            make_actor(),
            "artifact",
            {
                "key": "slug",
                "name": "Slug",
                "type": "short_text",
                "description": "Unique short identifier.",
                "unique": True,
            },
        )
        assert field.is_indexed is True
        unique_name = index_name(object_type.id, "slug", unique=True)
        assert unique_name in _index_names(db)
        with db.read() as conn:
            ddl = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": unique_name}
            ).scalar()
        assert ddl is not None and "CREATE UNIQUE INDEX" in ddl

    def test_toggling_indexed_creates_and_drops_the_index(
        self, db: Database, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        object_type, _ = sink_type
        points = index_name(object_type.id, "points")
        assert points not in _index_names(db)
        services.schema.update_field(make_actor(), "artifact", "points", {"indexed": True})
        assert points in _index_names(db)
        services.schema.update_field(make_actor(), "artifact", "points", {"indexed": False})
        assert points not in _index_names(db)

    def test_index_ddl_is_partial_and_type_scoped(self, db: Database, sink_type: SinkType) -> None:
        object_type, _ = sink_type
        with db.read() as conn:
            ddl = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name = :n"),
                {"n": index_name(object_type.id, "status")},
            ).scalar()
        assert ddl is not None
        assert f"object_type_id = '{object_type.id}'" in ddl
        assert "deleted_at IS NULL" in ddl


# ---------------------------------------------------------------------------
# EXPLAIN QUERY PLAN over a realistic multi-type fixture
# ---------------------------------------------------------------------------

TYPE_KEYS = ["deal", "ticket", "vendor", "policy", "review"]


@pytest.fixture
def multi_type_fixture(services: ServiceBundle) -> ServiceBundle:
    """Five object types, 60 live records each, with an indexed single_select and a
    non-indexed text field per type — the shape a real deployment has."""
    for i, key in enumerate(TYPE_KEYS):
        services.schema.create_object_type(
            make_actor(),
            key=key,
            name=key.title(),
            name_plural=key.title() + "s",
            description=f"Realistic fixture type '{key}' for planner tests.",
            key_prefix=f"T{i}X",
            fields=[
                {
                    "key": "stage",
                    "name": "Stage",
                    "type": "single_select",
                    "description": "Lifecycle stage; auto-indexed.",
                    "config": {"options": select_options("new", "open", "won", "lost")},
                },
                {
                    "key": "note",
                    "name": "Note",
                    "type": "short_text",
                    "description": "Unindexed free text.",
                },
            ],
        )
        for n in range(60):
            services.records.create_record(
                make_actor(),
                key,
                {"stage": ["new", "open", "won", "lost"][n % 4], "note": f"note {n}"},
            )
    return services


def _stage_index_name(services: ServiceBundle, type_key: str) -> str:
    """The type's own ``stage`` index name, derived from its id.

    Written out rather than spelled ``ix_rec_<type key>_stage`` because the type key is
    exactly what stopped being in the name: two type keys can compose to one index name
    and an id cannot.
    """
    object_type, _ = services.schema.get_object_type(make_actor(), type_key)
    return index_name(object_type.id, "stage")


def _compile_stage_query(services: ServiceBundle, type_key: str) -> tuple[str, dict[str, object]]:
    object_type, fields = services.schema.get_object_type(make_actor(), type_key)
    fields_by_key = {f.key: f for f in fields}
    ctx = FilterContext(
        object_type_key=type_key,
        fields_by_key=fields_by_key,
        now=datetime.now(UTC),
        resolve_record_ref=lambda ref: ref,
        resolve_principal_ref=lambda ref: ref,
    )
    node = parse_filter({"field": "stage", "op": "eq", "value": "won"}, ctx)
    compiled = compile_query(
        object_type,
        fields_by_key,
        node,
        parse_sort(None, fields_by_key, type_key),
        50,
        None,
        False,
    )
    return compiled.sql, compiled.params


class TestExplainQueryPlan:
    def test_compiled_query_on_indexed_field_uses_its_partial_index(
        self, db: Database, multi_type_fixture: ServiceBundle
    ) -> None:
        """The enforcement-test binding constraint (docs/DATA_MODEL.md section 5): SEARCH or
        SCAN of the field's partial index proves the literal-inlining implication
        held; a bare scan of the records table would mean it did not."""
        expected = _stage_index_name(multi_type_fixture, "deal")
        sql, params = _compile_stage_query(multi_type_fixture, "deal")
        with db.read() as conn:
            plan = [row[-1] for row in conn.execute(text("EXPLAIN QUERY PLAN " + sql), params)]
        index_lines = [line for line in plan if expected in line]
        assert index_lines, f"plan did not use {expected}: {plan}"
        assert any(line.startswith("SEARCH") or line.startswith("SCAN") for line in index_lines), (
            plan
        )

    def test_each_type_uses_its_own_index(
        self, db: Database, multi_type_fixture: ServiceBundle
    ) -> None:
        for type_key in TYPE_KEYS:
            sql, params = _compile_stage_query(multi_type_fixture, type_key)
            with db.read() as conn:
                plan = [row[-1] for row in conn.execute(text("EXPLAIN QUERY PLAN " + sql), params)]
            expected = _stage_index_name(multi_type_fixture, type_key)
            assert any(expected in line for line in plan), (type_key, plan)


class TestByteIdenticalExpression:
    def test_compiled_filter_and_index_ddl_share_expression_bytes(
        self, db: Database, multi_type_fixture: ServiceBundle
    ) -> None:
        expr = json_field_expr("stage")
        sql, _ = _compile_stage_query(multi_type_fixture, "deal")
        assert expr in sql
        with db.read() as conn:
            ddl = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name = :n"),
                {"n": _stage_index_name(multi_type_fixture, "deal")},
            ).scalar()
        assert ddl is not None
        assert expr in ddl

    def test_one_module_owns_expression_text_and_ddl(self) -> None:
        """The compiler does not define its own extraction text: the symbol it uses
        IS sqlexpr's, and index DDL is generated by the same module."""
        assert compiler_module.json_field_expr is sqlexpr_module.json_field_expr
        assert compiler_module.type_scope_predicate is sqlexpr_module.type_scope_predicate
        ddl = index_ddl("12345678-1234-4123-8123-123456789012", "stage")
        assert json_field_expr("stage") in ddl


class TestUuidLiteralInlining:
    def _object_type(self, type_id: str) -> ObjectType:
        return ObjectType(
            id=type_id,
            key="probe",
            name="Probe",
            name_plural="Probes",
            description="d",
            key_prefix="PRB",
            key_counter=0,
            icon=None,
            is_deleted=False,
            created_at="2026-01-01T00:00:00Z",
            created_by="x",
            updated_at="2026-01-01T00:00:00Z",
            updated_by="x",
        )

    def test_object_type_id_is_inlined_as_a_literal_not_a_parameter(self) -> None:
        type_id = "12345678-1234-4123-8123-123456789012"
        compiled = compile_query(self._object_type(type_id), {}, None, (), 10, None, False)
        assert f"object_type_id = '{type_id}'" in compiled.sql
        assert type_id not in {str(v) for v in compiled.params.values()}
        assert "deleted_at IS NULL" in compiled.sql

    @pytest.mark.parametrize(
        "bad_id",
        [
            "not-a-uuid",
            "12345678-1234-4123-8123-12345678901",  # too short
            "12345678-1234-4123-8123-1234567890123",  # too long
            "12345678-1234-4123-8123-12345678901Z",  # bad char
            "12345678123441238123123456789012",  # no dashes
            "ABCDEF78-1234-4123-8123-123456789012",  # uppercase rejected: strict form
            "x' OR '1'='1",  # the reason the gate exists
        ],
    )
    def test_non_uuid_ids_are_rejected_before_interpolation(self, bad_id: str) -> None:
        with pytest.raises(ValidationFailedError, match="UUID"):
            compile_query(self._object_type(bad_id), {}, None, (), 10, None, False)

    def test_index_ddl_rejects_non_uuid_ids_too(self) -> None:
        with pytest.raises(ValidationFailedError, match="UUID"):
            index_ddl("deal", "not-a-uuid", "stage")


# ---------------------------------------------------------------------------
# Index names are injective, and the old key-based names are retired at startup
# ---------------------------------------------------------------------------

# A collision pair found in a security review, verbatim. `sqlexpr.index_name` used to join the
# object type key and the field key with `_`, and `KEY_PATTERN` admits `_` inside both,
# so these two unique fields on two different types produced one name:
# `ux_rec_thing_a_code`.
COLLIDING_PAIR = (("thing_a", "code", "TGA"), ("thing", "a_code", "TGB"))


def _collision_type(services: ServiceBundle, key: str, field_key: str, prefix: str) -> ObjectType:
    created: ObjectType = services.schema.create_object_type(
        make_actor(),
        key=key,
        name=key.title(),
        name_plural=key.title() + "s",
        description=f"Type {key!r}, half of the index-name collision pair.",
        key_prefix=prefix,
        fields=[
            {
                "key": field_key,
                "name": field_key.title(),
                "type": "short_text",
                "description": "A unique code, enforced by a unique partial expression index.",
                "unique": True,
            },
            {
                "key": "stage",
                "name": "Stage",
                "type": "single_select",
                "description": "Lifecycle stage; auto-indexed, and one half of a composite.",
                "config": {"options": select_options("new", "done")},
            },
            {
                "key": "due",
                "name": "Due date",
                "type": "date",
                "description": "When this is expected; the other half of the composite.",
            },
        ],
    )
    return created


@pytest.fixture
def collision_pair(services: ServiceBundle) -> tuple[ObjectType, ObjectType]:
    return tuple(  # type: ignore[return-value]
        _collision_type(services, key, field_key, prefix)
        for key, field_key, prefix in COLLIDING_PAIR
    )


class TestIndexNamesAreInjective:
    def test_the_colliding_pair_gets_two_distinct_indexes(
        self, db: Database, collision_pair: tuple[ObjectType, ObjectType]
    ) -> None:
        first, second = collision_pair
        names = _index_names(db)
        one = index_name(first.id, "code", unique=True)
        two = index_name(second.id, "a_code", unique=True)
        assert one != two
        assert {one, two} <= names

    def test_the_second_types_duplicate_is_refused_by_sqlite(
        self, db: Database, services: ServiceBundle, collision_pair: tuple[ObjectType, ObjectType]
    ) -> None:
        """The consequence that matters. With one shared name, ``CREATE UNIQUE INDEX
        IF NOT EXISTS`` silently made the second type's index a no-op, and
        docs/DATA_MODEL.md section 5's "the unique partial expression index remains the
        hard enforcement" stopped being true -- leaving only the racy service-layer
        pre-check. The insert goes through the repository so that pre-check is bypassed
        and SQLite alone is answering."""
        _, second = collision_pair
        services.records.create_record(make_actor(), "thing", {"a_code": "DUP"})
        repo = SqliteRecordRepository()
        with pytest.raises(IntegrityError):
            with db.write() as conn:
                repo.insert_record(
                    conn,
                    RecordRow(
                        id=str(uuid.uuid4()),
                        object_type_id=second.id,
                        key="TGB-999",
                        key_seq=999,
                        version=1,
                        data={"a_code": "DUP"},
                        created_at="2026-09-04T00:00:00Z",
                        created_by=BOOTSTRAP_PRINCIPAL_ID,
                        updated_at="2026-09-04T00:00:00Z",
                        updated_by=BOOTSTRAP_PRINCIPAL_ID,
                        deleted_at=None,
                        deleted_by=None,
                        comment_count=0,
                        last_comment_at=None,
                    ),
                )

    def test_dropping_one_types_index_leaves_the_others_in_place(
        self, db: Database, services: ServiceBundle, collision_pair: tuple[ObjectType, ObjectType]
    ) -> None:
        """``drop_index_ddl`` used ``IF EXISTS``, so toggling ``indexed`` off or
        approving ``delete_field`` on one type dropped the *other* type's index --
        including a UNIQUE one, silently removing its enforcement."""
        first, second = collision_pair
        survivor = index_name(first.id, "code", unique=True)
        proposal = services.schema.propose_schema_change(
            make_actor(), "delete_field", "thing", field_key="a_code", reason="No longer tracked."
        )
        services.schema.approve_proposal(make_actor(), proposal.id)

        names = _index_names(db)
        assert index_name(second.id, "a_code", unique=True) not in names
        assert survivor in names

    def test_reconciling_one_types_composites_leaves_the_others(
        self, db: Database, services: ServiceBundle, collision_pair: tuple[ObjectType, ObjectType]
    ) -> None:
        """The third consequence, which the security review that found the pair did not carry.
        ``reconcile_sort_indexes`` listed existing composites by the prefix
        ``ix_sort_{type.key}_``, so reconciling type ``thing`` enumerated -- and dropped
        -- every composite belonging to type ``thing_a``."""
        first, second = collision_pair
        survivor = sort_index_name(first.id, "stage", "due")
        assert survivor in _index_names(db)

        with db.write() as conn:
            repo = SqliteSchemaRepository()
            fields = repo.list_fields(conn, second.id)
            services.schema.reconcile_sort_indexes(conn, second, fields)

        assert survivor in _index_names(db)


class TestRetiringTheLegacyIndexNames:
    """The one-time startup retirement of the old key-based index names."""

    def _legacy_names(self, db: Database, object_type: ObjectType) -> set[str]:
        """Rename this type's indexes back to the old key-based form. SQLite cannot rename an
        index, so this drops and recreates under the old name, which is precisely the
        shape an upgrading deployment arrives in."""
        renamed = set()
        with db.write() as conn:
            for name in sorted(_index_names(db)):
                for prefix, legacy in (
                    (f"ix_rec_{object_type.id.replace('-', '')}_", "ix_rec"),
                    (f"ux_rec_{object_type.id.replace('-', '')}_", "ux_rec"),
                    (f"ix_sort_{object_type.id.replace('-', '')}_", "ix_sort"),
                ):
                    if not name.startswith(prefix):
                        continue
                    tail = name[len(prefix) :]
                    if legacy == "ix_sort":
                        tail = tail.split("_", 1)[1]  # drop the pair digest
                    old = f"{legacy}_{object_type.key}_{tail}"
                    sql = conn.execute(
                        text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": name}
                    ).scalar()
                    assert sql is not None
                    conn.exec_driver_sql(f"DROP INDEX {name}")
                    conn.exec_driver_sql(sql.replace(name, old, 1))
                    renamed.add(old)
        return renamed

    def test_a_database_carrying_the_old_names_is_retired_and_rebuilt(
        self, db: Database, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        object_type, _ = sink_type
        legacy = self._legacy_names(db, object_type)
        assert legacy and legacy <= _index_names(db)

        dropped, created = services.schema.retire_legacy_index_names()

        assert sorted(dropped) == sorted(legacy)
        assert created, "the desired set must be rebuilt in the same transaction"
        names = _index_names(db)
        assert not (legacy & names)
        assert set(created) <= names
        # Every new name carries the type id, and none carries the type key.
        for name in created:
            assert object_type.id.replace("-", "") in name

    def test_a_second_start_drops_nothing_and_creates_nothing(
        self, db: Database, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        """Idempotent. The first pass is the migration; every pass after
        it costs a few ``sqlite_master`` reads and does nothing."""
        object_type, _ = sink_type
        self._legacy_names(db, object_type)
        services.schema.retire_legacy_index_names()
        before = _index_names(db)

        assert services.schema.retire_legacy_index_names() == ([], [])
        assert _index_names(db) == before

    def test_the_two_base_indexes_on_records_survive(
        self, db: Database, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        """It fails against an unescaped prefix. In SQL ``LIKE``, ``_`` is a
        single-character wildcard: ``'ix_rec_%'`` matches ``ix_records_type_live`` and
        ``ix_records_updated``, both created by migration 1 on ``records`` and both
        depended on by every query scope. A naive sweep would drop them at first start."""
        base = {"ix_records_type_live", "ix_records_updated"}
        assert base <= _index_names(db)
        self._legacy_names(db, sink_type[0])

        services.schema.retire_legacy_index_names()

        assert base <= _index_names(db)
