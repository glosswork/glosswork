"""REST layer: audit browsing and revert routes (FR-U8, FR-D6, DD-21), plus
personal/admin agent-label routes (FR-I6, FR-I7).

Underlying service/repository behavior is proven in test_audit_browse_and_revert.py
and test_agent_labels_settings.py; these tests prove the HTTP wiring, including
that a stale revert produces the exact same ``version_conflict`` envelope shape
``PATCH /api/v1/records/{key}`` already does (DD-21: "no second conflict UI").
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.services import ServiceBundle
from tests.conftest import KITCHEN_SINK_FIELDS, make_actor

pytestmark = pytest.mark.usefixtures("artifact_type")


@pytest.fixture
def artifact_type(app_services: ServiceBundle) -> None:
    """Seeds the kitchen-sink 'artifact' object type directly through the service
    layer, on the same database the ``client`` fixture talks to (mirrors
    test_api_records.py's fixture of the same name)."""
    app_services.schema.create_object_type(
        make_actor(),
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the HTTP test suite; exercises "
        "every field type the platform supports.",
        key_prefix="ART",
        fields=KITCHEN_SINK_FIELDS,
    )


def _create(client: TestClient, values: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/v1/object-types/artifact/records", json=values)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _patch(client: TestClient, key: str, values: dict[str, Any], **kw: Any) -> dict[str, Any]:
    response = client.patch(f"/api/v1/records/{key}", json={"values": values, **kw})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


class TestAuditEventsRoute:
    def test_search_by_record_and_field_key(self, client: TestClient) -> None:
        record = _create(client, {"title": "a"})
        _patch(client, record["key"], {"points": 1})

        response = client.get(
            "/api/v1/audit-events", params={"record": record["key"], "field_key": "points"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["events"]
        assert all(e["field_key"] == "points" for e in body["events"])
        assert "next_cursor" in body

    def test_search_unknown_object_type_is_400(self, client: TestClient) -> None:
        response = client.get("/api/v1/audit-events", params={"object_type": "nope"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "unknown_object_type"

    def test_search_unknown_record_is_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/audit-events", params={"record": "ART-999"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestRevertFieldChangeRoute:
    def test_revert_applies_the_inverse_and_returns_the_record(self, client: TestClient) -> None:
        record = _create(client, {"title": "a", "points": 1})
        _patch(client, record["key"], {"points": 2}, expected_version=1)

        events = client.get(
            "/api/v1/audit-events",
            params={"record": record["key"], "field_key": "points"},
        ).json()["events"]
        change_event = next(e for e in events if e["action"] == "update")

        response = client.post(
            f"/api/v1/audit-events/{change_event['id']}/revert",
            json={"expected_version": 2},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["version"] == 3
        assert body["data"]["points"] == 1

    def test_revert_conflict_is_shape_identical_to_a_normal_patch_conflict(
        self, client: TestClient
    ) -> None:
        record = _create(client, {"title": "a", "points": 1})
        _patch(client, record["key"], {"points": 2}, expected_version=1)
        events = client.get(
            "/api/v1/audit-events",
            params={"record": record["key"], "field_key": "points"},
        ).json()["events"]
        change_event = next(e for e in events if e["action"] == "update")

        # Race: someone else updates the record after the revert's caller read it.
        _patch(client, record["key"], {"points": 3}, expected_version=2)

        revert_response = client.post(
            f"/api/v1/audit-events/{change_event['id']}/revert",
            json={"expected_version": 2},
        )
        patch_response = client.patch(
            f"/api/v1/records/{record['key']}",
            json={"values": {"points": 99}, "expected_version": 2},
        )
        assert revert_response.status_code == 409
        assert patch_response.status_code == 409
        revert_error = revert_response.json()["error"]
        patch_error = patch_response.json()["error"]
        assert revert_error["code"] == patch_error["code"] == "version_conflict"
        assert set(revert_error["details"]) == set(patch_error["details"])
        assert (
            revert_error["details"]["current_version"] == patch_error["details"]["current_version"]
        )

    def test_revert_of_a_link_event_is_422(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        parent = _create(client, {"title": "parent"})
        child = _create(client, {"title": "child"})
        link_response = client.post(
            f"/api/v1/records/{child['key']}/links/parent",
            json={"to_records": [parent["key"]]},
        )
        assert link_response.status_code == 200, link_response.text

        events = client.get("/api/v1/audit-events", params={"record": child["key"]}).json()[
            "events"
        ]
        link_event = next(e for e in events if e["entity_type"] == "link")

        response = client.post(
            f"/api/v1/audit-events/{link_event['id']}/revert",
            json={"expected_version": 1},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


class TestRevertToVersionRoute:
    def test_revert_to_version_restores_prior_state(self, client: TestClient) -> None:
        record = _create(client, {"title": "a"})
        _patch(client, record["key"], {"points": 1}, expected_version=1)
        latest = _patch(client, record["key"], {"points": 2}, expected_version=2)
        assert latest["version"] == 3

        response = client.post(
            f"/api/v1/records/{record['key']}/revert-to-version",
            json={"target_version": 2, "expected_version": 3},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["version"] == 4
        assert body["data"]["points"] == 1

    def test_out_of_range_target_version_is_422(self, client: TestClient) -> None:
        record = _create(client, {"title": "a"})
        response = client.post(
            f"/api/v1/records/{record['key']}/revert-to-version",
            json={"target_version": 1, "expected_version": 1},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

    def test_revert_conflict_is_shape_identical_to_a_normal_patch_conflict(
        self, client: TestClient
    ) -> None:
        record = _create(client, {"title": "a"})
        _patch(client, record["key"], {"points": 1}, expected_version=1)

        # Race: someone else updates the record after the revert's caller read it.
        _patch(client, record["key"], {"points": 3}, expected_version=2)

        revert_response = client.post(
            f"/api/v1/records/{record['key']}/revert-to-version",
            json={"target_version": 1, "expected_version": 2},
        )
        patch_response = client.patch(
            f"/api/v1/records/{record['key']}",
            json={"values": {"points": 99}, "expected_version": 2},
        )
        assert revert_response.status_code == 409
        assert patch_response.status_code == 409
        revert_error = revert_response.json()["error"]
        patch_error = patch_response.json()["error"]
        assert revert_error["code"] == patch_error["code"] == "version_conflict"
        assert set(revert_error["details"]) == set(patch_error["details"])
        assert (
            revert_error["details"]["current_version"] == patch_error["details"]["current_version"]
        )


class TestAgentLabelRoutes:
    def test_list_own_labels_and_update_one(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        label = app_services.agent_labels.register_use(BOOTSTRAP_PRINCIPAL_ID, "planner")

        list_response = client.get("/api/v1/agent-labels")
        assert list_response.status_code == 200
        labels = list_response.json()["labels"]
        assert any(row["id"] == label.id for row in labels)

        patch_response = client.patch(
            f"/api/v1/agent-labels/{label.id}",
            json={"display_name": "Planner", "description": "Plans initiatives."},
        )
        assert patch_response.status_code == 200
        body = patch_response.json()
        assert body["display_name"] == "Planner"
        assert body["verified"] is True

    def test_admin_route_returns_the_cross_user_view(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        """The admin cross-user view is its own ``admin``-scoped route, not ``?all=true``
        on this one (``routes/agent_labels.py``), so the personal route
        stays own-labels-only regardless of query parameters."""
        app_services.agent_labels.register_use(BOOTSTRAP_PRINCIPAL_ID, "planner")
        other = app_services.principals.create_service_account(
            make_actor(),
            display_name="Other Bot",
            description="A second principal, so the admin view has something the "
            "personal view must not show.",
        )
        app_services.agent_labels.register_use(other.id, "other-bots-label")

        own_response = client.get("/api/v1/agent-labels")
        assert own_response.status_code == 200
        own_labels = {row["label"] for row in own_response.json()["labels"]}
        assert own_labels == {"planner"}

        admin_response = client.get("/api/v1/admin/agent-labels")
        assert admin_response.status_code == 200
        admin_labels = {row["label"] for row in admin_response.json()["labels"]}
        assert admin_labels == {"planner", "other-bots-label"}
