"""A date literal in a filter is validated, not compared as text (FR-R5, FR-R8).

Before this rule the server accepted any string for a ``date`` or ``datetime`` filter
value and compared it as text against stored ISO strings, so ``lt "7 Jan 2026"`` answered
200 with every record. Every surface that parses a filter shares the one function, so the
refusal is asserted here for a query, a search, a CSV export and a bulk update.

Counts are computed in the test from the seeded rows, never written as constants.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from glosswork.repositories.models import ObjectType
from glosswork.services import ServiceBundle
from glosswork.timeutil import is_canonical_date, is_canonical_datetime
from tests.conftest import make_actor

BOUNDARY_DAY = "2026-01-07"
BOUNDARY_INSTANT = "2026-01-07T00:00:00Z"

# Either side of the boundary, one stamped at the boundary to the second, and one in the
# middle of the boundary day.
_SEEN = [
    "2026-01-05T10:00:00Z",
    "2026-01-06T23:59:59Z",
    "2026-01-07T00:00:00Z",
    "2026-01-07T12:30:00Z",
    "2026-01-09T08:00:00Z",
    "2026-02-01T00:00:00Z",
]
_DUE = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-07", "2026-01-09", "2026-02-01"]

# What the rule refuses on both field types and on a datetime pseudo-field.
_REFUSED_EVERYWHERE: list[Any] = [
    "7 Jan 2026",
    "01/07/2026",
    "banana",
    "2026-01-07T00:00",
    "2026-01-07T00:00:00.000Z",
    "2026-01-07T00:00:00+00:00",
    "2026-01-07T02:00:00+02:00",
    "2026-1-7",
    "2026-1-7T0:0:0Z",
    "2026-01-07T00:00:00z",
    "2026-01-07t00:00:00Z",
    "2026-02-30",
    "2026-01-07T25:00:00Z",
    # Full-width digits: ``str.isdigit`` and ``strptime`` both accept them.
    "２０２６-０１-０７",
    " 2026-01-07",
    "",
    20260107,
    True,
]

_DATETIME_FIELDS = ["seen_at", "created_at"]


@pytest.fixture
def probe_type(app_services: ServiceBundle) -> ObjectType:
    object_type = app_services.schema.create_object_type(
        make_actor(),
        key="probe",
        name="Probe",
        name_plural="Probes",
        description="A test object type with one date and one datetime field.",
        key_prefix="PRB",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "Short human-readable name for the probe.",
                "required": True,
            },
            {
                "key": "due",
                "name": "Due",
                "type": "date",
                "description": "Calendar date the probe is due.",
            },
            {
                "key": "seen_at",
                "name": "Seen at",
                "type": "datetime",
                "description": "UTC timestamp the probe was last seen.",
            },
        ],
    )
    for index, (seen, due) in enumerate(zip(_SEEN, _DUE, strict=True)):
        app_services.records.create_record(
            make_actor(), "probe", {"title": f"probe {index}", "due": due, "seen_at": seen}
        )
    return object_type


def _query(client: TestClient, condition: dict[str, Any]) -> Any:
    return client.post("/api/v1/object-types/probe/query", json={"filter": condition})


def _count(client: TestClient, condition: dict[str, Any]) -> int:
    response = _query(client, condition)
    assert response.status_code == 200, response.text
    total: int = response.json()["total_count"]
    return total


def _assert_refused(response: Any, field_key: str, literal: Any, *, datetime_forms: bool) -> None:
    assert response.status_code == 422, (literal, response.text)
    error = response.json()["error"]
    assert error["code"] == "validation_failed", (literal, error)
    assert error["details"].get("field_key") == field_key, (literal, error)
    message: str = error["message"]
    assert "YYYY-MM-DD" in message, message
    assert "@today-7d" in message, message
    assert ("YYYY-MM-DDTHH:MM:SSZ" in message) is datetime_forms, message
    if datetime_forms:
        assert "UTC, whole seconds, ending in Z" in message, message
    assert f"got {literal!r}" in message, message


@pytest.mark.parametrize("literal", _REFUSED_EVERYWHERE, ids=repr)
@pytest.mark.parametrize("field_key", _DATETIME_FIELDS)
def test_a_datetime_field_refuses_a_literal_it_cannot_read(
    client: TestClient, probe_type: ObjectType, field_key: str, literal: Any
) -> None:
    response = _query(client, {"field": field_key, "op": "lt", "value": literal})
    _assert_refused(response, field_key, literal, datetime_forms=True)


@pytest.mark.parametrize("literal", [*_REFUSED_EVERYWHERE, "2026-01-07T00:00:00Z"], ids=repr)
def test_a_date_field_refuses_a_literal_it_cannot_read(
    client: TestClient, probe_type: ObjectType, literal: Any
) -> None:
    """The last case is a full datetime: a canonical instant is not a calendar date."""
    response = _query(client, {"field": "due", "op": "lt", "value": literal})
    _assert_refused(response, "due", literal, datetime_forms=False)


def test_the_quoted_literal_is_cut_at_forty_characters(
    client: TestClient, probe_type: ObjectType
) -> None:
    literal = "x" * 200
    response = _query(client, {"field": "seen_at", "op": "lt", "value": literal})
    assert response.status_code == 422, response.text
    message: str = response.json()["error"]["message"]
    assert "x" * 40 in message
    assert "x" * 41 not in message


@pytest.mark.parametrize("op", ["lt", "lte", "gt", "gte", "eq", "neq"])
def test_a_date_only_value_on_a_datetime_field_is_that_day_at_midnight_utc(
    client: TestClient, probe_type: ObjectType, op: str
) -> None:
    compare = {
        "lt": lambda a, b: a < b,
        "lte": lambda a, b: a <= b,
        "gt": lambda a, b: a > b,
        "gte": lambda a, b: a >= b,
        "eq": lambda a, b: a == b,
        "neq": lambda a, b: a != b,
    }[op]
    expected = sum(1 for seen in _SEEN if compare(seen, BOUNDARY_INSTANT))
    by_day = _count(client, {"field": "seen_at", "op": op, "value": BOUNDARY_DAY})
    by_instant = _count(client, {"field": "seen_at", "op": op, "value": BOUNDARY_INSTANT})
    assert by_day == by_instant == expected


def test_between_rewrites_each_date_only_end_on_a_datetime_field(
    client: TestClient, probe_type: ObjectType
) -> None:
    expected = sum(1 for seen in _SEEN if "2026-01-06T00:00:00Z" <= seen <= BOUNDARY_INSTANT)
    got = _count(
        client, {"field": "seen_at", "op": "between", "value": ["2026-01-06", BOUNDARY_DAY]}
    )
    assert got == expected


def test_a_canonical_date_still_filters_a_date_field(
    client: TestClient, probe_type: ObjectType
) -> None:
    """A fence: the accepted form on a ``date`` field answers as it did before."""
    expected = sum(1 for due in _DUE if due < BOUNDARY_DAY)
    assert _count(client, {"field": "due", "op": "lt", "value": BOUNDARY_DAY}) == expected


@pytest.mark.parametrize("field_key", ["due", "seen_at", "created_at"])
def test_a_relative_token_still_resolves(
    client: TestClient, probe_type: ObjectType, field_key: str
) -> None:
    """A fence: the token grammar and its resolution are untouched."""
    response = _query(client, {"field": field_key, "op": "gte", "value": "@today-7d"})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("field_key", ["due", "seen_at", "created_at"])
def test_an_unknown_token_is_refused_naming_its_field(
    client: TestClient, probe_type: ObjectType, field_key: str
) -> None:
    response = _query(client, {"field": field_key, "op": "lt", "value": "@tod"})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert error["details"].get("field_key") == field_key
    # The token resolver's own sentence is unchanged.
    assert "Unknown date token '@tod'" in error["message"]
    assert "@start_of_quarter" in error["message"]


@pytest.mark.parametrize("field_key", ["due", "seen_at"])
@pytest.mark.parametrize("position", [0, 1])
def test_between_with_one_bad_end_is_refused(
    client: TestClient, probe_type: ObjectType, field_key: str, position: int
) -> None:
    ends: list[Any] = ["2026-01-01", "2026-01-31"]
    ends[position] = "7 Jan 2026"
    response = _query(client, {"field": field_key, "op": "between", "value": ends})
    _assert_refused(response, field_key, "7 Jan 2026", datetime_forms=field_key == "seen_at")


@pytest.mark.parametrize("field_key", ["due", "seen_at"])
def test_no_list_operator_reaches_a_date_field(
    client: TestClient, probe_type: ObjectType, field_key: str
) -> None:
    """A fence. The operator matrix offers a date field no list operator, so ``between``
    above is the only place a date literal arrives member by member."""
    response = _query(
        client, {"field": field_key, "op": "in", "value": ["2026-01-07", "7 Jan 2026"]}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_operator"


def _versions(services: ServiceBundle) -> dict[str, tuple[int, str]]:
    result = services.records.query_records(make_actor(), "probe", fields="*", limit=100)
    return {r["key"]: (r["version"], r["data"]["title"]) for r in result.records}


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_bulk_update_with_a_refused_literal_changes_no_record(
    client: TestClient, app_services: ServiceBundle, probe_type: ObjectType, dry_run: bool
) -> None:
    before = _versions(app_services)
    response = client.post(
        "/api/v1/object-types/probe/bulk-update",
        json={
            "filter": {"field": "seen_at", "op": "lt", "value": "7 Jan 2026"},
            "values": {"title": "overwritten"},
            "dry_run": dry_run,
        },
    )
    _assert_refused(response, "seen_at", "7 Jan 2026", datetime_forms=True)
    assert _versions(app_services) == before


def test_a_search_and_an_export_with_a_refused_literal_say_the_same_thing(
    client: TestClient, probe_type: ObjectType
) -> None:
    condition = {"field": "seen_at", "op": "lt", "value": "7 Jan 2026"}
    queried = _query(client, condition)
    _assert_refused(queried, "seen_at", "7 Jan 2026", datetime_forms=True)
    message = queried.json()["error"]["message"]

    searched = client.post(
        "/api/v1/search",
        json={"query": "probe", "object_types": ["probe"], "filter": condition, "mode": "keyword"},
    )
    _assert_refused(searched, "seen_at", "7 Jan 2026", datetime_forms=True)
    assert searched.json()["error"]["message"] == message

    exported = client.get(
        "/api/v1/object-types/probe/export", params={"filter": json.dumps(condition)}
    )
    _assert_refused(exported, "seen_at", "7 Jan 2026", datetime_forms=True)
    assert exported.json()["error"]["message"] == message


def test_a_search_across_types_refuses_a_bad_literal_on_a_system_field(
    client: TestClient, probe_type: ObjectType
) -> None:
    response = client.post(
        "/api/v1/search",
        json={
            "query": "probe",
            "filter": {"field": "created_at", "op": "gte", "value": "last week"},
            "mode": "keyword",
        },
    )
    _assert_refused(response, "created_at", "last week", datetime_forms=True)


# ------------------------------------------------------------ the strict checks


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-07", True),
        ("2024-02-29", True),
        ("2026-02-29", False),
        ("2026-1-7", False),
        ("2026-01-07 ", False),
        ("2026-01-07\n", False),
        ("２０２６-０１-０７", False),
        ("0000-01-01", False),
        ("2026-13-01", False),
        ("2026-01-07T00:00:00Z", False),
    ],
)
def test_is_canonical_date(value: str, expected: bool) -> None:
    assert is_canonical_date(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-01-07T00:00:00Z", True),
        ("2026-01-07T23:59:59Z", True),
        ("2026-01-07T24:00:00Z", False),
        ("2026-01-07T23:59:60Z", False),
        ("2026-02-30T00:00:00Z", False),
        ("2026-01-07T00:00:00z", False),
        ("2026-01-07t00:00:00Z", False),
        ("2026-01-07 00:00:00Z", False),
        ("2026-1-7T0:0:0Z", False),
        ("2026-01-07T00:00:00Z\n", False),
        ("2026-01-07T00:00:00.000Z", False),
        ("2026-01-07T00:00:00+00:00", False),
        ("2026-01-07", False),
    ],
)
def test_is_canonical_datetime(value: str, expected: bool) -> None:
    assert is_canonical_datetime(value) is expected
