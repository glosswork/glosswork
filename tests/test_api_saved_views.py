"""HTTP-layer acceptance tests for saved-view routes (FR-U3).

These prove the same behavior ``tests/test_saved_views.py`` already proves at the
service layer is reachable over HTTP, with no logic duplicated in the route
handlers (DD-3).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.services import ServiceBundle
from tests.conftest import make_actor


@pytest.fixture
def object_type_key(app_services: ServiceBundle) -> str:
    app_services.schema.create_object_type(
        make_actor(),
        key="task",
        name="Task",
        name_plural="Tasks",
        description="A unit of work tracked for the saved-view HTTP test suite.",
        key_prefix="TSK",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "Short human-readable title.",
                "required": True,
            }
        ],
    )
    return "task"


def test_post_creates_saved_view_with_full_config(client: TestClient, object_type_key: str) -> None:
    config = {
        "filter": {"field": "title", "op": "contains", "value": "foo"},
        "sort": [{"field": "title", "dir": "asc"}],
        "group_by": None,
        "columns": ["title"],
        "widths": {"title": 200},
    }
    response = client.post(
        f"/api/v1/object-types/{object_type_key}/saved-views",
        json={"name": "My view", "config": config, "mode": "table"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "My view"
    assert body["mode"] == "table"
    assert body["config"] == config
    assert body["is_default"] is False
    assert body["created_by"] == BOOTSTRAP_PRINCIPAL_ID
    assert body["updated_by"] == BOOTSTRAP_PRINCIPAL_ID


def test_post_on_unknown_object_type_returns_not_found_code(client: TestClient) -> None:
    response = client.post(
        "/api/v1/object-types/does-not-exist/saved-views",
        json={"name": "X", "config": {}},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_object_type"


def test_post_rejects_empty_name(client: TestClient, object_type_key: str) -> None:
    response = client.post(
        f"/api/v1/object-types/{object_type_key}/saved-views",
        json={"name": "   ", "config": {}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_get_lists_views_for_one_object_type_only(client: TestClient) -> None:
    task_body = {
        "key": "list_task",
        "name": "List Task",
        "name_plural": "List Tasks",
        "description": "First object type for the saved-view listing HTTP test.",
        "key_prefix": "LTA",
        "fields": [
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "Title.",
                "required": True,
            }
        ],
    }
    widget_body = {
        "key": "list_widget",
        "name": "List Widget",
        "name_plural": "List Widgets",
        "description": "Second object type for the saved-view listing HTTP test.",
        "key_prefix": "LWI",
        "fields": [
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "Title.",
                "required": True,
            }
        ],
    }
    assert client.post("/api/v1/object-types", json=task_body).status_code == 200
    assert client.post("/api/v1/object-types", json=widget_body).status_code == 200

    task_view = client.post(
        "/api/v1/object-types/list_task/saved-views",
        json={"name": "Task view", "config": {}},
    ).json()
    widget_view = client.post(
        "/api/v1/object-types/list_widget/saved-views",
        json={"name": "Widget view", "config": {}},
    ).json()

    task_views = client.get("/api/v1/object-types/list_task/saved-views").json()
    widget_views = client.get("/api/v1/object-types/list_widget/saved-views").json()

    assert [v["id"] for v in task_views] == [task_view["id"]]
    assert [v["id"] for v in widget_views] == [widget_view["id"]]


def test_patch_sets_default_and_atomically_unsets_the_previous_default(
    client: TestClient, object_type_key: str
) -> None:
    first = client.post(
        f"/api/v1/object-types/{object_type_key}/saved-views",
        json={"name": "First", "config": {}, "is_default": True},
    ).json()
    assert first["is_default"] is True

    second = client.post(
        f"/api/v1/object-types/{object_type_key}/saved-views",
        json={"name": "Second", "config": {}},
    ).json()
    assert second["is_default"] is False

    response = client.patch(f"/api/v1/saved-views/{second['id']}", json={"is_default": True})
    assert response.status_code == 200
    assert response.json()["is_default"] is True

    views = client.get(f"/api/v1/object-types/{object_type_key}/saved-views").json()
    defaults = [v for v in views if v["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == second["id"]


def test_patch_renames_and_changes_mode(client: TestClient, object_type_key: str) -> None:
    created = client.post(
        f"/api/v1/object-types/{object_type_key}/saved-views",
        json={"name": "Original", "config": {}, "mode": "table"},
    ).json()

    response = client.patch(
        f"/api/v1/saved-views/{created['id']}",
        json={"name": "Renamed", "mode": "card"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["mode"] == "card"


def test_patch_unknown_view_returns_not_found(client: TestClient) -> None:
    response = client.patch("/api/v1/saved-views/does-not-exist", json={"name": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_delete_removes_view_from_subsequent_list(client: TestClient, object_type_key: str) -> None:
    keep = client.post(
        f"/api/v1/object-types/{object_type_key}/saved-views",
        json={"name": "Keep", "config": {}},
    ).json()
    doomed = client.post(
        f"/api/v1/object-types/{object_type_key}/saved-views",
        json={"name": "Doomed", "config": {}},
    ).json()

    response = client.delete(f"/api/v1/saved-views/{doomed['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == doomed["id"]

    remaining = client.get(f"/api/v1/object-types/{object_type_key}/saved-views").json()
    assert [v["id"] for v in remaining] == [keep["id"]]


def test_delete_unknown_view_returns_not_found(client: TestClient) -> None:
    response = client.delete("/api/v1/saved-views/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
