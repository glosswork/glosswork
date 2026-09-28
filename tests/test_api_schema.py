"""HTTP-layer acceptance tests for schema-administration routes (FR-S1 through
FR-S10, FR-A4).

The business rules themselves belong to the service layer and are already proven against
the service layer directly in tests/test_schema_engine.py; these tests prove the
identical behavior is reachable over HTTP with the documented response shapes
(docs/MCP_TOOLS.md sections 5.3, 6, 8).
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from glosswork.services import ServiceBundle
from tests.conftest import make_actor

ARTIFACT_FIELDS: list[dict[str, Any]] = [
    {
        "key": "title",
        "name": "Title",
        "type": "short_text",
        "description": "Short human-readable name for the artifact.",
        "required": True,
    },
    {
        "key": "summary",
        "name": "Summary",
        "type": "long_text",
        "description": "Longer narrative of what this artifact is and why it exists.",
    },
    {
        "key": "points",
        "name": "Points",
        "type": "integer",
        "description": "Effort estimate in story points.",
    },
    {
        "key": "status",
        "name": "Status",
        "type": "single_select",
        "description": "Delivery state; use 'doing' only for actively worked artifacts.",
        "config": {
            "options": [
                {"value": "todo", "label": "Todo", "description": "Not started."},
                {"value": "doing", "label": "Doing", "description": "In progress."},
                {"value": "done", "label": "Done", "description": "Complete."},
            ]
        },
    },
]


def _create_artifact_type(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/object-types",
        json={
            "key": "artifact",
            "name": "Artifact",
            "name_plural": "Artifacts",
            "description": "A tracked work artifact used by the HTTP test suite.",
            "key_prefix": "ART",
            "fields": ARTIFACT_FIELDS,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _seed_records(app_services: ServiceBundle) -> None:
    for i, status in enumerate(["todo", "doing", "done"], start=1):
        app_services.records.create_record(
            make_actor(),
            "artifact",
            {"title": f"Rec {i}", "status": status, "points": i, "summary": f"body {i}"},
        )


# --------------------------------------------------------------------- object types


class TestObjectTypeCrud:
    def test_create_with_fields_in_one_call(self, client: TestClient) -> None:
        body = _create_artifact_type(client)
        assert body["key"] == "artifact"
        assert body["field_count"] == len(ARTIFACT_FIELDS)
        field_keys = {f["key"] for f in body["fields"]}
        assert field_keys == {f["key"] for f in ARTIFACT_FIELDS}
        title_field = next(f for f in body["fields"] if f["key"] == "title")
        assert title_field["required"] is True
        assert "eq" in title_field["operators"]

    def test_list_object_types_shape(self, client: TestClient) -> None:
        _create_artifact_type(client)
        response = client.get("/api/v1/object-types")
        assert response.status_code == 200
        types = response.json()
        assert len(types) == 1
        entry = types[0]
        assert set(entry) == {
            "key",
            "name",
            "description",
            "key_prefix",
            "record_count",
            "field_count",
            # Additive, never replacing a field. It carries the already
            # composed effective level, so an admin PAT on a type this principal
            # administers reads "admin".
            "your_access",
        }
        assert entry["your_access"] == "admin"
        assert entry["key"] == "artifact"
        assert entry["record_count"] == 0
        assert entry["field_count"] == len(ARTIFACT_FIELDS)

    def test_list_object_types_record_count_reflects_writes(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _create_artifact_type(client)
        _seed_records(app_services)
        response = client.get("/api/v1/object-types")
        assert response.json()[0]["record_count"] == 3

    def test_get_object_type_full_describe_document(self, client: TestClient) -> None:
        _create_artifact_type(client)
        response = client.get("/api/v1/object-types/artifact")
        assert response.status_code == 200
        body = response.json()
        assert body["key"] == "artifact"
        assert body["name_plural"] == "Artifacts"
        assert body["field_count"] == len(ARTIFACT_FIELDS)
        assert len(body["fields"]) == len(ARTIFACT_FIELDS)
        status_field = next(f for f in body["fields"] if f["key"] == "status")
        assert status_field["operators"] == ["eq", "neq", "in", "not_in", "is_null", "is_not_null"]
        # System pseudo-fields are present with their own operator sets.
        system_keys = {f["key"] for f in body["system_fields"]}
        assert system_keys == {
            "key",
            "created_at",
            "updated_at",
            "created_by",
            "updated_by",
            "deleted_at",
            "comment_count",
            "last_comment_at",
        }
        comment_count_field = next(f for f in body["system_fields"] if f["key"] == "comment_count")
        assert "gte" in comment_count_field["operators"]

    def test_get_unknown_object_type_is_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/object-types/does-not-exist")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "unknown_object_type"

    def test_patch_updates_name_description_icon(self, client: TestClient) -> None:
        _create_artifact_type(client)
        response = client.patch(
            "/api/v1/object-types/artifact",
            json={"name": "Work Artifact", "description": "Updated description.", "icon": "box"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Work Artifact"
        assert body["description"] == "Updated description."

    def test_patch_rejects_immutable_key_and_prefix(self, client: TestClient) -> None:
        _create_artifact_type(client)
        response = client.patch(
            "/api/v1/object-types/artifact",
            json={"key": "renamed", "key_prefix": "REN"},
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert "key" in error["message"] and "key_prefix" in error["message"]
        assert "immutable" in error["message"]


# --------------------------------------------------------------------------- fields


class TestFieldAdminstration:
    def test_add_field_applies_immediately(self, client: TestClient) -> None:
        _create_artifact_type(client)
        response = client.post(
            "/api/v1/object-types/artifact/fields",
            json={
                "key": "notes",
                "name": "Notes",
                "type": "long_text",
                "description": "Freeform notes about this artifact.",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "applied"
        assert body["field"]["key"] == "notes"
        assert body["field"]["type"] == "long_text"
        assert "contains" in body["field"]["operators"]

    def test_update_field_additive_applies_immediately(self, client: TestClient) -> None:
        _create_artifact_type(client)
        # Relaxing a constraint, editing a description, and adding an enum option are
        # all additive (FR-S5).
        response = client.patch(
            "/api/v1/object-types/artifact/fields/title",
            json={"changes": {"required": False, "description": "Renamed description."}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "applied"
        assert body["field"]["required"] is False
        assert body["field"]["description"] == "Renamed description."
        assert "message" in body

        response2 = client.patch(
            "/api/v1/object-types/artifact/fields/status",
            json={
                "changes": {
                    "config": {
                        "options": [
                            {"value": "todo", "label": "Todo", "description": "Not started."},
                            {"value": "doing", "label": "Doing", "description": "In progress."},
                            {"value": "done", "label": "Done", "description": "Complete."},
                            {"value": "blocked", "label": "Blocked", "description": "Stuck."},
                        ]
                    }
                }
            },
        )
        assert response2.status_code == 200
        assert response2.json()["status"] == "applied"

    def test_update_field_destructive_type_change_returns_pending(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _create_artifact_type(client)
        _seed_records(app_services)
        response = client.patch(
            "/api/v1/object-types/artifact/fields/points",
            json={"changes": {"type": "short_text"}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending_human_approval"
        assert body["proposal_id"].startswith("prop_")
        assert "affected_records" in body["impact"]
        assert "message" in body

        # Nothing applied: the field is still an integer.
        describe = client.get("/api/v1/object-types/artifact").json()
        points_field = next(f for f in describe["fields"] if f["key"] == "points")
        assert points_field["type"] == "integer"

    def test_update_field_destructive_remove_in_use_enum_option_returns_pending(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _create_artifact_type(client)
        _seed_records(app_services)  # uses status values todo/doing/done
        response = client.patch(
            "/api/v1/object-types/artifact/fields/status",
            json={
                "changes": {
                    "config": {
                        "options": [
                            {"value": "todo", "label": "Todo", "description": "Not started."},
                            {"value": "done", "label": "Done", "description": "Complete."},
                        ]
                    }
                }
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending_human_approval"
        assert body["impact"]["in_use"]["values"] == ["doing"]

    def test_update_field_destructive_tighten_required_returns_pending(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _create_artifact_type(client)
        _seed_records(app_services)
        # 'summary' is present on all seeded records; clear one so 'required' would
        # be violated by tightening.
        app_services.records.update_record(make_actor(), "ART-003", {"summary": None})
        response = client.patch(
            "/api/v1/object-types/artifact/fields/summary",
            json={"changes": {"required": True}},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending_human_approval"
        assert body["impact"]["violations"]["constraint"] == "required"

    def test_add_field_blank_description_is_validation_failed(self, client: TestClient) -> None:
        _create_artifact_type(client)
        response = client.post(
            "/api/v1/object-types/artifact/fields",
            json={"key": "blank", "name": "Blank", "type": "short_text", "description": "   "},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

    def test_add_field_blank_enum_option_description_is_validation_failed(
        self, client: TestClient
    ) -> None:
        _create_artifact_type(client)
        response = client.post(
            "/api/v1/object-types/artifact/fields",
            json={
                "key": "priority",
                "name": "Priority",
                "type": "single_select",
                "description": "How urgent this artifact is.",
                "config": {"options": [{"value": "high", "label": "High", "description": ""}]},
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


class TestObjectTypeBlankDescription:
    def test_create_object_type_blank_description_is_validation_failed(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/v1/object-types",
            json={
                "key": "widget",
                "name": "Widget",
                "name_plural": "Widgets",
                "description": " ",
                "key_prefix": "WID",
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

    def test_patch_object_type_blank_description_is_validation_failed(
        self, client: TestClient
    ) -> None:
        _create_artifact_type(client)
        response = client.patch(
            "/api/v1/object-types/artifact",
            json={"description": ""},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


# --------------------------------------------------------------------- proposals


class TestSchemaProposals:
    def test_explicit_propose_delete_field(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _create_artifact_type(client)
        _seed_records(app_services)
        response = client.post(
            "/api/v1/schema-proposals",
            json={
                "change_type": "delete_field",
                "object_type": "artifact",
                "field_key": "points",
                "reason": "unused field",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending_human_approval"
        assert body["change_type"] == "delete_field"
        assert body["proposal_id"].startswith("prop_")
        assert body["impact"]["affected_records"] == 3
        assert "human approval" in body["message"]

    def test_explicit_propose_delete_object_type(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _create_artifact_type(client)
        _seed_records(app_services)
        response = client.post(
            "/api/v1/schema-proposals",
            json={"change_type": "delete_object_type", "object_type": "artifact"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "pending_human_approval"
        assert body["change_type"] == "delete_object_type"
        assert body["impact"]["affected_records"] == 3

    def test_list_proposals_by_status(self, client: TestClient) -> None:
        _create_artifact_type(client)
        proposed = client.post(
            "/api/v1/schema-proposals",
            json={"change_type": "delete_field", "object_type": "artifact", "field_key": "points"},
        ).json()

        response = client.get("/api/v1/schema-proposals", params={"status": "pending"})
        assert response.status_code == 200
        proposals = response.json()["proposals"]
        assert any(p["id"] == proposed["proposal_id"] for p in proposals)
        assert all(p["status"] == "pending" for p in proposals)

        empty = client.get("/api/v1/schema-proposals", params={"status": "approved"})
        assert empty.json()["proposals"] == []

    def test_approve_applies_and_returns_approved_status(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _create_artifact_type(client)
        _seed_records(app_services)
        proposed = client.post(
            "/api/v1/schema-proposals",
            json={"change_type": "delete_field", "object_type": "artifact", "field_key": "points"},
        ).json()

        response = client.post(
            f"/api/v1/schema-proposals/{proposed['proposal_id']}/approve", json={}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["snapshot_ref"] is not None

        describe = client.get("/api/v1/object-types/artifact").json()
        assert "points" not in {f["key"] for f in describe["fields"]}

    def test_approve_impact_changed_requires_confirm(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _create_artifact_type(client)
        _seed_records(app_services)
        proposed = client.post(
            "/api/v1/schema-proposals",
            json={"change_type": "delete_field", "object_type": "artifact", "field_key": "points"},
        ).json()
        assert proposed["impact"]["affected_records"] == 3

        # Mutate the underlying data through the service layer so the blast radius
        # changes between proposal and approval.
        app_services.records.create_record(
            make_actor(), "artifact", {"title": "late arrival", "points": 99}
        )

        without_confirm = client.post(
            f"/api/v1/schema-proposals/{proposed['proposal_id']}/approve", json={}
        )
        assert without_confirm.status_code == 409
        error = without_confirm.json()["error"]
        assert error["code"] == "impact_changed"
        new_impact = error["details"]["new_impact"]
        assert new_impact["affected_records"] == 4

        # Proposal is still pending; nothing applied.
        pending = client.get("/api/v1/object-types/artifact").json()
        assert "points" in {f["key"] for f in pending["fields"]}

        with_confirm = client.post(
            f"/api/v1/schema-proposals/{proposed['proposal_id']}/approve",
            json={"confirm_impact": new_impact},
        )
        assert with_confirm.status_code == 200
        approved = with_confirm.json()
        assert approved["status"] == "approved"
        assert approved["impact"]["affected_records"] == 4

    def test_reject_proposal(self, client: TestClient, app_services: ServiceBundle) -> None:
        _create_artifact_type(client)
        _seed_records(app_services)
        proposed = client.post(
            "/api/v1/schema-proposals",
            json={"change_type": "delete_field", "object_type": "artifact", "field_key": "points"},
        ).json()

        response = client.post(
            f"/api/v1/schema-proposals/{proposed['proposal_id']}/reject",
            json={"decision_note": "not needed right now"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "rejected"
        assert body["decision_note"] == "not needed right now"

        # Nothing applied; field still present.
        describe = client.get("/api/v1/object-types/artifact").json()
        assert "points" in {f["key"] for f in describe["fields"]}

    def test_update_field_destructive_reachable_via_explicit_proposal_too(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        """The same destructive change (change_field_type) is reachable both by
        PATCH .../fields/{field_key} (auto-routed) and by the explicit
        POST /api/v1/schema-proposals entry point."""
        _create_artifact_type(client)
        _seed_records(app_services)

        via_patch = client.patch(
            "/api/v1/object-types/artifact/fields/points",
            json={"changes": {"type": "short_text"}},
        )
        assert via_patch.json()["status"] == "pending_human_approval"

        via_explicit = client.post(
            "/api/v1/schema-proposals",
            json={
                "change_type": "change_field_type",
                "object_type": "artifact",
                "field_key": "points",
                "payload": {"to_type": "short_text"},
            },
        )
        assert via_explicit.status_code == 200
        assert via_explicit.json()["status"] == "pending_human_approval"
