"""Query grammar acceptance tests: the full operator matrix, boolean nesting,
pseudo-fields, tokens, sorting, projection, and keyset pagination
(FR-R5 through FR-R9, FR-C8; docs/MCP_TOOLS.md section 4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.datetokens import resolve_date_token
from glosswork.db import Database
from glosswork.errors import (
    InvalidOperatorError,
    ValidationFailedError,
)
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.services import ServiceBundle
from tests.conftest import SECOND_PRINCIPAL_ID, make_actor, seed_second_principal, select_options

SinkType = tuple[ObjectType, dict[str, FieldDef]]

# A real, live principal who owns none of the fixture records. A fabricated UUID will not
# do, because ``user_ref`` values resolve rather than merely shape-check, so "matches
# nobody" and "names nobody" are not the same thing: the second is ``validation_failed``,
# deliberately, so a typo cannot come back as an empty result set.
OTHER_PRINCIPAL = SECOND_PRINCIPAL_ID

# Fixture data: values chosen so every operator case below is discriminating.
# Missing keys are genuinely absent (stored by key omission).
FIXTURE: dict[str, dict[str, Any]] = {
    "A": {
        "title": "Alpha service",
        "summary": "the quick brown fox",
        "points": 1,
        "score": 1.5,
        "active": True,
        "due": "2026-08-01",
        "seen_at": "2026-08-01T08:00:00Z",
        "status": "todo",
        "tags": ["red"],
        "owner": BOOTSTRAP_PRINCIPAL_ID,
        "homepage": "https://alpha.example.com/docs",
        "files": ["a1"],
    },
    "B": {
        "title": "Beta service",
        "summary": "jumps over the lazy dog",
        "points": 5,
        "score": 2.5,
        "active": False,
        "due": "2026-08-10",
        "seen_at": "2026-08-10T12:00:00Z",
        "status": "doing",
        "tags": ["red", "green"],
        "homepage": "https://beta.example.com/",
        "files": [],
    },
    "C": {
        "title": "Gamma tool",
        "summary": "quick start guide",
        "points": 10,
        "score": 10.0,
        "due": "2026-08-20",
        "seen_at": "2026-08-20T18:30:00Z",
        "status": "done",
        "tags": ["blue"],
        "owner": BOOTSTRAP_PRINCIPAL_ID,
    },
    "D": {
        "title": "Delta tool",
        "points": 7,
        "score": 3.5,
        "active": True,
        "status": "todo",
        "tags": [],
    },
    "E": {
        "title": "Epsilon",
        "summary": "unrelated content",
        "points": 4,
        "due": "2026-09-05",
        "seen_at": "2026-09-05T00:00:00Z",
        "status": "doing",
        "tags": ["red", "green", "blue"],
        "homepage": "https://gamma.other.org/x",
        "files": ["e1", "e2"],
    },
}


@pytest.fixture
def dataset(db: Database, services: ServiceBundle, sink_type: SinkType) -> dict[str, str]:
    """Creates the fixture records; returns {label: record_key}."""
    seed_second_principal(db)
    keys: dict[str, str] = {}
    for label, values in FIXTURE.items():
        keys[label] = services.records.create_record(make_actor(), "artifact", values).key
    return keys


def _evaluate(op: str, stored: Any, value: Any) -> bool:
    """Reference semantics for one condition, mirroring the compiler's SQL
    (SQL NULL comparison semantics; arrays treat a missing key as empty)."""
    if op == "is_null":
        return stored is None
    if op == "is_not_null":
        return stored is not None
    if op in ("has_any", "has_all", "has_none", "is_empty", "is_not_empty"):
        array = stored if isinstance(stored, list) else []
        if op == "has_any":
            return any(v in array for v in value)
        if op == "has_all":
            return all(v in array for v in value)
        if op == "has_none":
            return not any(v in array for v in value)
        if op == "is_empty":
            return len(array) == 0
        return len(array) > 0
    if op == "not_contains":  # missing values contain nothing
        return stored is None or value not in stored
    if stored is None:
        return False
    if op == "eq":
        return bool(stored == value)
    if op == "neq":
        return bool(stored != value)
    if op == "gt":
        return bool(stored > value)
    if op == "gte":
        return bool(stored >= value)
    if op == "lt":
        return bool(stored < value)
    if op == "lte":
        return bool(stored <= value)
    if op == "between":
        return bool(value[0] <= stored <= value[1])
    if op == "in":
        return stored in value
    if op == "not_in":
        return stored not in value
    if op == "contains":
        return value in stored
    if op == "starts_with":
        return bool(stored.startswith(value))
    if op == "ends_with":
        return bool(stored.endswith(value))
    raise AssertionError(f"no reference semantics for {op}")


# Every operator in the docs/MCP_TOOLS.md section 4 matrix, against the field
# types of its row. Relation operators are covered in test_relations.py and again
# in TestRelationOperators below.
OPERATOR_CASES: list[tuple[str, str, Any]] = [
    # short_text / long_text / url: full text operator row on each type
    ("title", "eq", "Alpha service"),
    ("title", "neq", "Alpha service"),
    ("title", "contains", "service"),
    ("title", "not_contains", "service"),
    ("title", "starts_with", "Alpha"),
    ("title", "ends_with", "tool"),
    ("title", "in", ["Alpha service", "Epsilon"]),
    ("summary", "eq", "quick start guide"),
    ("summary", "neq", "quick start guide"),
    ("summary", "contains", "quick"),
    ("summary", "not_contains", "quick"),
    ("summary", "starts_with", "jumps"),
    ("summary", "ends_with", "dog"),
    ("summary", "in", ["quick start guide", "unrelated content"]),
    ("homepage", "eq", "https://beta.example.com/"),
    ("homepage", "neq", "https://beta.example.com/"),
    ("homepage", "contains", "example.com"),
    ("homepage", "not_contains", "example.com"),
    ("homepage", "starts_with", "https://alpha"),
    ("homepage", "ends_with", ".org/x"),
    ("homepage", "in", ["https://beta.example.com/", "https://gamma.other.org/x"]),
    # integer / decimal: full numeric row on each
    ("points", "eq", 5),
    ("points", "neq", 5),
    ("points", "gt", 5),
    ("points", "gte", 5),
    ("points", "lt", 5),
    ("points", "lte", 5),
    ("points", "between", [2, 7]),
    ("points", "in", [1, 7]),
    ("score", "eq", 2.5),
    ("score", "neq", 2.5),
    ("score", "gt", 3.0),
    ("score", "gte", 3.5),
    ("score", "lt", 3.0),
    ("score", "lte", 2.5),
    ("score", "between", [1.5, 3.5]),
    ("score", "in", [1.5, 10.0]),
    # boolean
    ("active", "eq", True),
    ("active", "eq", False),
    # date / datetime: full row on each
    ("due", "eq", "2026-08-10"),
    ("due", "neq", "2026-08-10"),
    ("due", "gt", "2026-08-10"),
    ("due", "gte", "2026-08-10"),
    ("due", "lt", "2026-08-10"),
    ("due", "lte", "2026-08-10"),
    ("due", "between", ["2026-08-05", "2026-08-25"]),
    ("seen_at", "eq", "2026-08-10T12:00:00Z"),
    ("seen_at", "neq", "2026-08-10T12:00:00Z"),
    ("seen_at", "gt", "2026-08-10T12:00:00Z"),
    ("seen_at", "gte", "2026-08-10T12:00:00Z"),
    ("seen_at", "lt", "2026-08-10T12:00:00Z"),
    ("seen_at", "lte", "2026-08-10T12:00:00Z"),
    ("seen_at", "between", ["2026-08-05T00:00:00Z", "2026-08-25T00:00:00Z"]),
    # single_select
    ("status", "eq", "todo"),
    ("status", "neq", "todo"),
    ("status", "in", ["todo", "done"]),
    ("status", "not_in", ["todo", "done"]),
    # multi_select
    ("tags", "has_any", ["red"]),
    ("tags", "has_any", ["blue", "green"]),
    ("tags", "has_all", ["red", "green"]),
    ("tags", "has_none", ["red"]),
    ("tags", "is_empty", None),
    ("tags", "is_not_empty", None),
    # user_ref
    ("owner", "eq", BOOTSTRAP_PRINCIPAL_ID),
    ("owner", "neq", OTHER_PRINCIPAL),
    ("owner", "in", [BOOTSTRAP_PRINCIPAL_ID, OTHER_PRINCIPAL]),
    # attachment
    ("files", "is_empty", None),
    ("files", "is_not_empty", None),
]

# is_null / is_not_null are legal on every type ("all types" row); exercise both
# on every non-relation field of the fixture type.
NULLABLE_FIELDS = [
    "title",
    "summary",
    "points",
    "score",
    "active",
    "due",
    "seen_at",
    "status",
    "tags",
    "owner",
    "homepage",
    "files",
]


class TestOperatorMatrix:
    @pytest.mark.parametrize(("field", "op", "value"), OPERATOR_CASES)
    def test_operator_matches_reference_semantics(
        self,
        services: ServiceBundle,
        dataset: dict[str, str],
        field: str,
        op: str,
        value: Any,
    ) -> None:
        condition: dict[str, Any] = {"field": field, "op": op}
        if value is not None:
            condition["value"] = value
        result = services.records.query_records(make_actor(), "artifact", filter=condition)
        actual = {r["key"] for r in result.records}
        expected = {
            dataset[label]
            for label, values in FIXTURE.items()
            if _evaluate(op, values.get(field), value)
        }
        assert actual == expected, f"{field} {op} {value!r}"
        assert result.total_count == len(expected)
        # Every case is designed to discriminate: a filter that matches nothing or
        # everything would prove nothing about the operator.
        assert 0 < len(expected) < len(FIXTURE), f"non-discriminating case: {field} {op}"

    @pytest.mark.parametrize("field", NULLABLE_FIELDS)
    def test_null_checks_partition_every_field_type(
        self, services: ServiceBundle, dataset: dict[str, str], field: str
    ) -> None:
        null = services.records.query_records(
            make_actor(), "artifact", filter={"field": field, "op": "is_null"}
        )
        not_null = services.records.query_records(
            make_actor(), "artifact", filter={"field": field, "op": "is_not_null"}
        )
        null_keys = {r["key"] for r in null.records}
        not_null_keys = {r["key"] for r in not_null.records}
        expected_null = {k for label, k in dataset.items() if field not in FIXTURE[label]}
        assert null_keys == expected_null
        assert not_null_keys == set(dataset.values()) - expected_null
        assert null_keys.isdisjoint(not_null_keys)

    def test_invalid_operator_for_type_is_rejected(
        self, services: ServiceBundle, dataset: dict[str, str]
    ) -> None:
        with pytest.raises(InvalidOperatorError) as excinfo:
            services.records.query_records(
                make_actor(), "artifact", filter={"field": "points", "op": "contains", "value": "5"}
            )
        assert "gt" in excinfo.value.details["valid_ops"]

    def test_relation_operators_over_links(
        self, services: ServiceBundle, dataset: dict[str, str]
    ) -> None:
        # parent is the self-referential relation on the fixture type.
        services.records.link_records(make_actor(), dataset["B"], "parent", [dataset["A"]])
        services.records.link_records(make_actor(), dataset["C"], "parent", [dataset["A"]])
        services.records.link_records(make_actor(), dataset["D"], "parent", [dataset["E"]])

        def keys(filter: dict[str, Any]) -> set[str]:
            result = services.records.query_records(make_actor(), "artifact", filter=filter)
            return {r["key"] for r in result.records}

        assert keys({"field": "parent", "op": "linked_to", "value": dataset["A"]}) == {
            dataset["B"],
            dataset["C"],
        }
        assert keys(
            {"field": "parent", "op": "linked_to_any", "value": [dataset["A"], dataset["E"]]}
        ) == {dataset["B"], dataset["C"], dataset["D"]}
        assert keys({"field": "parent", "op": "has_links"}) == {
            dataset["B"],
            dataset["C"],
            dataset["D"],
        }
        assert keys({"field": "parent", "op": "has_no_links"}) == {
            dataset["A"],
            dataset["E"],
        }
        # The auto-maintained inverse side is filterable the same way.
        assert keys({"field": "children", "op": "has_links"}) == {dataset["A"], dataset["E"]}


class TestBooleanNesting:
    def test_bare_condition_is_a_valid_filter(
        self, services: ServiceBundle, dataset: dict[str, str]
    ) -> None:
        result = services.records.query_records(
            make_actor(), "artifact", filter={"field": "status", "op": "eq", "value": "todo"}
        )
        assert {r["key"] for r in result.records} == {dataset["A"], dataset["D"]}

    def test_empty_filter_matches_all_live_records(
        self, services: ServiceBundle, dataset: dict[str, str]
    ) -> None:
        assert services.records.query_records(make_actor(), "artifact").total_count == 5
        assert services.records.query_records(make_actor(), "artifact", filter={}).total_count == 5

    def test_and_or_not_nesting(self, services: ServiceBundle, dataset: dict[str, str]) -> None:
        # (status in [todo, doing]) AND (points >= 5 OR score is null) AND NOT tags has 'blue'
        result = services.records.query_records(
            make_actor(),
            "artifact",
            filter={
                "and": [
                    {"field": "status", "op": "in", "value": ["todo", "doing"]},
                    {
                        "or": [
                            {"field": "points", "op": "gte", "value": 5},
                            {"field": "score", "op": "is_null"},
                        ]
                    },
                    {"not": {"field": "tags", "op": "has_any", "value": ["blue"]}},
                ]
            },
        )
        expected = {
            key
            for label, key in dataset.items()
            if FIXTURE[label]["status"] in ("todo", "doing")
            and (FIXTURE[label].get("points", 0) >= 5 or FIXTURE[label].get("score") is None)
            and "blue" not in FIXTURE[label].get("tags", [])
        }
        assert {r["key"] for r in result.records} == expected
        assert expected == {dataset["B"], dataset["D"]}  # sanity: non-trivial nesting

    def test_deeply_nested_not(self, services: ServiceBundle, dataset: dict[str, str]) -> None:
        # NOT(NOT(x)) == x
        inner = {"field": "status", "op": "eq", "value": "done"}
        result = services.records.query_records(
            make_actor(), "artifact", filter={"not": {"not": inner}}
        )
        assert {r["key"] for r in result.records} == {dataset["C"]}


class TestPseudoFieldsAndTokens:
    def test_every_pseudo_field_is_queryable(
        self, services: ServiceBundle, dataset: dict[str, str]
    ) -> None:
        records = services.records

        def keys(filter: dict[str, Any], **kwargs: Any) -> set[str]:
            result = records.query_records(make_actor(), "artifact", filter=filter, **kwargs)
            return {r["key"] for r in result.records}

        # key
        assert keys({"field": "key", "op": "eq", "value": dataset["A"]}) == {dataset["A"]}
        assert keys({"field": "key", "op": "starts_with", "value": "ART-"}) == set(dataset.values())
        # created_at / updated_at
        assert keys({"field": "created_at", "op": "lte", "value": "@now"}) == set(dataset.values())
        assert keys({"field": "updated_at", "op": "gt", "value": "@now+1d"}) == set()
        # created_by / updated_by accept @me (FR-R9)
        assert keys({"field": "created_by", "op": "eq", "value": "@me"}) == set(dataset.values())
        assert keys({"field": "updated_by", "op": "neq", "value": "@me"}) == set()
        # comment_count and last_comment_at (FR-C8)
        services.comments.add_comment(make_actor(), dataset["C"], "only discussed record")
        assert keys({"field": "comment_count", "op": "gte", "value": 1}) == {dataset["C"]}
        assert keys({"field": "last_comment_at", "op": "is_null"}) == set(dataset.values()) - {
            dataset["C"]
        }
        # deleted_at is meaningful with include_deleted
        services.records.delete_record(make_actor(), dataset["E"])
        assert keys({"field": "deleted_at", "op": "is_not_null"}, include_deleted=True) == {
            dataset["E"]
        }

    def test_me_resolves_in_user_ref_field_filters(
        self, services: ServiceBundle, dataset: dict[str, str]
    ) -> None:
        result = services.records.query_records(
            make_actor(), "artifact", filter={"field": "owner", "op": "eq", "value": "@me"}
        )
        assert {r["key"] for r in result.records} == {dataset["A"], dataset["C"]}

    def test_date_token_end_to_end(self, services: ServiceBundle, dataset: dict[str, str]) -> None:
        now = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
        result = services.records.query_records(
            make_actor(),
            "artifact",
            filter={"field": "due", "op": "lte", "value": "@today+7d"},
            now=now,
        )
        # due <= today plus a week: A (08-01), B (08-10), and C (08-20), since 08-20 <= 08-22.
        assert {r["key"] for r in result.records} == {
            dataset["A"],
            dataset["B"],
            dataset["C"],
        }


# ---------------------------------------------------------------------------
# Date tokens: every base and every offset unit, both directions (FR-R8)
# ---------------------------------------------------------------------------

NOW = datetime(2026, 8, 22, 15, 30, 45, tzinfo=UTC)  # a Saturday

TOKEN_CASES: list[tuple[str, str, str]] = [
    ("@today", "datetime", "2026-08-22T00:00:00Z"),
    ("@today", "date", "2026-08-22"),
    ("@now", "datetime", "2026-08-22T15:30:45Z"),
    ("@now", "date", "2026-08-22"),
    ("@start_of_week", "datetime", "2026-08-17T00:00:00Z"),
    ("@start_of_week", "date", "2026-08-17"),
    ("@start_of_month", "datetime", "2026-08-01T00:00:00Z"),
    ("@start_of_month", "date", "2026-08-01"),
    ("@start_of_quarter", "datetime", "2026-07-01T00:00:00Z"),
    ("@start_of_quarter", "date", "2026-07-01"),
    ("@start_of_year", "datetime", "2026-01-01T00:00:00Z"),
    ("@start_of_year", "date", "2026-01-01"),
    # every unit, both directions
    ("@today+3d", "date", "2026-08-25"),
    ("@today-3d", "date", "2026-08-19"),
    ("@today+2w", "date", "2026-09-05"),
    ("@today-2w", "date", "2026-08-08"),
    ("@today+1M", "date", "2026-09-22"),
    ("@today-1M", "date", "2026-07-22"),
    ("@today+1y", "date", "2027-08-22"),
    ("@today-1y", "date", "2025-08-22"),
    ("@today+5h", "datetime", "2026-08-22T05:00:00Z"),
    ("@today-5h", "datetime", "2026-08-21T19:00:00Z"),
    ("@now+30m", "datetime", "2026-08-22T16:00:45Z"),
    ("@now-30m", "datetime", "2026-08-22T15:00:45Z"),
    # offsets compose with non-today bases too
    ("@start_of_month-1M", "date", "2026-07-01"),
    ("@start_of_week+1w", "date", "2026-08-24"),
    ("@now-12h", "datetime", "2026-08-22T03:30:45Z"),
]


class TestDateTokens:
    @pytest.mark.parametrize(("token", "field_type", "expected"), TOKEN_CASES)
    def test_token_resolution(self, token: str, field_type: str, expected: str) -> None:
        assert resolve_date_token(token, field_type, NOW) == expected

    def test_month_arithmetic_clamps_day(self) -> None:
        jan31 = datetime(2027, 1, 31, 8, 0, 0, tzinfo=UTC)
        assert resolve_date_token("@today+1M", "date", jan31) == "2027-02-28"
        leap = datetime(2028, 2, 29, 8, 0, 0, tzinfo=UTC)
        assert resolve_date_token("@today+1y", "date", leap) == "2029-02-28"

    def test_unknown_token_rejected_with_grammar(self) -> None:
        with pytest.raises(ValidationFailedError, match="@today"):
            resolve_date_token("@yesterday", "date", NOW)
        with pytest.raises(ValidationFailedError):
            resolve_date_token("@today-7q", "date", NOW)


# ---------------------------------------------------------------------------
# Sorting, projection, pagination (FR-R6)
# ---------------------------------------------------------------------------


class TestSortingAndProjection:
    def test_multi_key_sort_with_per_key_direction(
        self, services: ServiceBundle, dataset: dict[str, str]
    ) -> None:
        result = services.records.query_records(
            make_actor(),
            "artifact",
            sort=[{"field": "status", "dir": "asc"}, {"field": "points", "dir": "desc"}],
        )
        # doing: B(5), E(4); done: C(10); todo: D(7), A(1) — no ties anywhere.
        assert [r["key"] for r in result.records] == [
            dataset["B"],
            dataset["E"],
            dataset["C"],
            dataset["D"],
            dataset["A"],
        ]

    def test_sort_on_pseudo_field(self, services: ServiceBundle, dataset: dict[str, str]) -> None:
        result = services.records.query_records(
            make_actor(), "artifact", sort=[{"field": "key", "dir": "desc"}]
        )
        assert [r["key"] for r in result.records] == sorted(dataset.values(), reverse=True)

    def test_field_projection(self, services: ServiceBundle, dataset: dict[str, str]) -> None:
        result = services.records.query_records(
            make_actor(), "artifact", fields=["title", "points"]
        )
        for record in result.records:
            assert set(record["data"]) <= {"title", "points"}
            assert "key" in record and "version" in record  # envelope always present
        full = services.records.query_records(make_actor(), "artifact", fields="*")
        by_key = {r["key"]: r for r in full.records}
        assert set(by_key[dataset["A"]]["data"]) == set(FIXTURE["A"])


class TestKeysetPagination:
    @pytest.fixture
    def paged_type(self, services: ServiceBundle) -> list[str]:
        """23 records whose sort key ('bucket') has heavy duplication."""
        services.schema.create_object_type(
            make_actor(),
            key="pagetest",
            name="Page Test",
            name_plural="Page Tests",
            description="Fixture type for keyset pagination over duplicate sort values.",
            key_prefix="PAGE",
            fields=[
                {
                    "key": "bucket",
                    "name": "Bucket",
                    "type": "single_select",
                    "description": "Deliberately duplicated sort key.",
                    "config": {"options": select_options("a", "b", "c")},
                },
                {
                    "key": "n",
                    "name": "N",
                    "type": "integer",
                    "description": "Creation ordinal.",
                },
            ],
        )
        keys = []
        for i in range(23):
            record = services.records.create_record(
                make_actor(), "pagetest", {"bucket": "abc"[i % 3], "n": i}
            )
            keys.append(record.key)
        return keys

    def test_pagination_yields_every_record_exactly_once(
        self, services: ServiceBundle, paged_type: list[str]
    ) -> None:
        seen: list[str] = []
        cursor: str | None = None
        pages = 0
        while True:
            result = services.records.query_records(
                make_actor(),
                "pagetest",
                sort=[{"field": "bucket", "dir": "asc"}],
                limit=4,
                cursor=cursor,
            )
            seen.extend(r["key"] for r in result.records)
            pages += 1
            if result.next_cursor is None:
                break
            cursor = result.next_cursor
            assert pages < 30, "pagination did not terminate"
        assert len(seen) == 23, "a page boundary skipped or repeated a record"
        assert len(set(seen)) == 23
        assert set(seen) == set(paged_type)
        assert pages == 6  # ceil(23 / 4)
        # Order is bucket-ascending overall.
        buckets = [key for key in seen]
        assert buckets == sorted(
            seen,
            key=lambda k: (
                services.records.get_record(make_actor(), k).data["bucket"],
                services.records.get_record(make_actor(), k).id,
            ),
        )

    def test_pagination_with_descending_and_null_sort_values(
        self, services: ServiceBundle, paged_type: list[str]
    ) -> None:
        # Clear 'bucket' on a few records so the sort key has NULLs at boundaries.
        for key in paged_type[:5]:
            services.records.update_record(make_actor(), key, {"bucket": None})
        seen: list[str] = []
        cursor: str | None = None
        while True:
            result = services.records.query_records(
                make_actor(),
                "pagetest",
                sort=[{"field": "bucket", "dir": "desc"}, {"field": "n", "dir": "asc"}],
                limit=3,
                cursor=cursor,
            )
            seen.extend(r["key"] for r in result.records)
            if result.next_cursor is None:
                break
            cursor = result.next_cursor
        assert len(seen) == 23
        assert len(set(seen)) == 23

    def test_cursors_are_opaque_and_validated(
        self, services: ServiceBundle, paged_type: list[str]
    ) -> None:
        result = services.records.query_records(
            make_actor(), "pagetest", sort=[{"field": "bucket", "dir": "asc"}], limit=4
        )
        cursor = result.next_cursor
        assert isinstance(cursor, str) and cursor
        assert "PAGE-" not in cursor  # no readable record key leaks
        with pytest.raises(ValidationFailedError, match="[Cc]ursor"):
            services.records.query_records(make_actor(), "pagetest", cursor="garbage-cursor")
        # A cursor from one sort cannot be replayed under another sort.
        with pytest.raises(ValidationFailedError, match="sort"):
            services.records.query_records(
                make_actor(),
                "pagetest",
                sort=[{"field": "n", "dir": "asc"}],
                cursor=cursor,
            )
