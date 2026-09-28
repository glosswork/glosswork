"""HTTP-layer completeness for the query grammar: the complete docs/MCP_TOOLS.md
section 4 operator matrix per field type, and every date token and offset unit,
exercised through the query route.

``and``/``or``/``not`` nesting and bare conditions are already covered over HTTP
in ``tests/test_api_records.py``; this module fills the remaining breadth gap:
every operator/field-type combination, every pseudo-field, and every documented
date token and offset unit, each driven through ``POST .../query`` rather than
the service layer directly (``tests/test_query_operators.py`` already proves the
same semantics at the service layer; this proves the HTTP route doesn't drop or
mis-thread anything on the way to the compiler).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.services import ServiceBundle
from tests.conftest import KITCHEN_SINK_FIELDS, make_actor, seed_second_principal
from tests.test_query_operators import (
    FIXTURE,
    NULLABLE_FIELDS,
    OPERATOR_CASES,
    OTHER_PRINCIPAL,
    _evaluate,
)

QUERY_URL = "/api/v1/object-types/artifact/query"


@pytest.fixture
def dataset(app: FastAPI, client: TestClient, app_services: ServiceBundle) -> dict[str, str]:
    """The same fixture object type and records as test_query_operators.py's
    ``dataset``, but seeded on the ``client``/``app_services`` database."""
    seed_second_principal(app.state.db)
    app_services.schema.create_object_type(
        make_actor(),
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="Kitchen-sink object type used to exercise the query route.",
        key_prefix="ART",
        fields=KITCHEN_SINK_FIELDS,
    )
    keys: dict[str, str] = {}
    for label, values in FIXTURE.items():
        keys[label] = app_services.records.create_record(make_actor(), "artifact", values).key
    return keys


def _query(client: TestClient, **body: Any) -> set[str]:
    response = client.post(QUERY_URL, json={"fields": "*", **body})
    assert response.status_code == 200, response.text
    return {r["key"] for r in response.json()["records"]}


class TestOperatorMatrixOverHttp:
    @pytest.mark.parametrize(("field", "op", "value"), OPERATOR_CASES)
    def test_operator_matches_reference_semantics(
        self,
        client: TestClient,
        dataset: dict[str, str],
        field: str,
        op: str,
        value: Any,
    ) -> None:
        condition: dict[str, Any] = {"field": field, "op": op}
        if value is not None:
            condition["value"] = value
        actual = _query(client, filter=condition)
        expected = {
            dataset[label]
            for label, values in FIXTURE.items()
            if _evaluate(op, values.get(field), value)
        }
        assert actual == expected, f"{field} {op} {value!r}"

    @pytest.mark.parametrize("field", NULLABLE_FIELDS)
    def test_null_checks_partition_every_field_type(
        self, client: TestClient, dataset: dict[str, str], field: str
    ) -> None:
        null_keys = _query(client, filter={"field": field, "op": "is_null"})
        not_null_keys = _query(client, filter={"field": field, "op": "is_not_null"})
        expected_null = {k for label, k in dataset.items() if field not in FIXTURE[label]}
        assert null_keys == expected_null
        assert not_null_keys == set(dataset.values()) - expected_null


class TestPseudoFieldsOverHttp:
    def test_every_pseudo_field_is_queryable_through_the_query_route(
        self, client: TestClient, app_services: ServiceBundle, dataset: dict[str, str]
    ) -> None:
        # key
        assert _query(client, filter={"field": "key", "op": "eq", "value": dataset["A"]}) == {
            dataset["A"]
        }
        assert _query(client, filter={"field": "key", "op": "starts_with", "value": "ART-"}) == set(
            dataset.values()
        )
        # created_at / updated_at
        assert _query(client, filter={"field": "created_at", "op": "lte", "value": "@now"}) == set(
            dataset.values()
        )
        assert (
            _query(client, filter={"field": "updated_at", "op": "gt", "value": "@now+1d"}) == set()
        )
        # created_by / updated_by accept @me (FR-R9)
        assert _query(client, filter={"field": "created_by", "op": "eq", "value": "@me"}) == set(
            dataset.values()
        )
        assert _query(client, filter={"field": "updated_by", "op": "neq", "value": "@me"}) == set()
        # comment_count / last_comment_at (FR-C8)
        app_services.comments.add_comment(make_actor(), dataset["C"], "only discussed record")
        assert _query(client, filter={"field": "comment_count", "op": "gte", "value": 1}) == {
            dataset["C"]
        }
        assert _query(client, filter={"field": "last_comment_at", "op": "is_null"}) == set(
            dataset.values()
        ) - {dataset["C"]}
        # deleted_at is meaningful with include_deleted
        app_services.records.delete_record(make_actor(), dataset["E"])
        response = client.post(
            QUERY_URL,
            json={
                "fields": "*",
                "filter": {"field": "deleted_at", "op": "is_not_null"},
                "include_deleted": True,
            },
        )
        assert {r["key"] for r in response.json()["records"]} == {dataset["E"]}

    def test_owner_user_ref_at_me_and_explicit_id(
        self, client: TestClient, dataset: dict[str, str]
    ) -> None:
        assert _query(client, filter={"field": "owner", "op": "eq", "value": "@me"}) == {
            dataset["A"],
            dataset["C"],
        }
        assert _query(
            client, filter={"field": "owner", "op": "eq", "value": BOOTSTRAP_PRINCIPAL_ID}
        ) == {dataset["A"], dataset["C"]}
        assert _query(client, filter={"field": "owner", "op": "neq", "value": OTHER_PRINCIPAL}) == {
            dataset["A"],
            dataset["C"],
        }


# ---------------------------------------------------------------------------
# Date tokens over HTTP: every base token and every offset unit, both
# directions (FR-R8). Token *arithmetic* is already exhaustively unit-tested
# against a frozen `now` in tests/test_query_operators.py::TestDateTokens; the
# HTTP route has no way to freeze time (there is deliberately no "now" override
# on the public query API), so this proves the route threads each token through
# to a directionally-correct boundary using real wall-clock time, with a buffer
# generous enough to absorb normal test-execution latency.
# ---------------------------------------------------------------------------

_BASE_TOKENS = [
    "@today",
    "@now",
    "@start_of_week",
    "@start_of_month",
    "@start_of_quarter",
    "@start_of_year",
]
_OFFSET_TOKENS = [
    "@today+3d",
    "@today-3d",
    "@today+2w",
    "@today-2w",
    "@today+1M",
    "@today-1M",
    "@today+1y",
    "@today-1y",
    "@today+5h",
    "@today-5h",
    "@now+30m",
    "@now-30m",
]


@pytest.fixture
def boundary_widget(client: TestClient, app_services: ServiceBundle) -> str:
    app_services.schema.create_object_type(
        make_actor(),
        key="event",
        name="Event",
        name_plural="Events",
        description="Object type used to exercise date tokens through the query route.",
        key_prefix="EVT",
        fields=[
            {
                "key": "seen_at",
                "name": "Seen at",
                "type": "datetime",
                "description": "UTC timestamp of the last review.",
            }
        ],
    )
    return "event"


class TestDateTokensOverHttp:
    @pytest.mark.parametrize("token", _BASE_TOKENS + _OFFSET_TOKENS)
    def test_token_resolves_to_a_directionally_correct_boundary(
        self, client: TestClient, app_services: ServiceBundle, boundary_widget: str, token: str
    ) -> None:
        now = datetime.now(UTC)
        # A generous buffer so real wall-clock drift between "now" here and the
        # server's own now during request handling can never flip the boundary.
        before = app_services.records.create_record(
            make_actor(), boundary_widget, {"seen_at": _iso(now - timedelta(days=400))}
        )
        after = app_services.records.create_record(
            make_actor(), boundary_widget, {"seen_at": _iso(now + timedelta(days=400))}
        )

        query_url = f"/api/v1/object-types/{boundary_widget}/query"
        lte_response = client.post(
            query_url,
            json={"fields": "*", "filter": {"field": "seen_at", "op": "lte", "value": token}},
        )
        assert lte_response.status_code == 200, lte_response.text
        lte_keys = {r["key"] for r in lte_response.json()["records"]}
        assert before.key in lte_keys
        assert after.key not in lte_keys

        gte_response = client.post(
            query_url,
            json={"fields": "*", "filter": {"field": "seen_at", "op": "gte", "value": token}},
        )
        gte_keys = {r["key"] for r in gte_response.json()["records"]}
        assert after.key in gte_keys
        assert before.key not in gte_keys


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
