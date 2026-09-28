"""REST coverage for the pull-based change feed (FR-M7). Mirrors the
cursor-correctness and scoping behavior already proven at the service layer in
``tests/test_change_feed.py``, but drives it through ``client`` HTTP calls against
``GET /api/v1/changes``."""

from __future__ import annotations

from fastapi.testclient import TestClient

from glosswork.services import ServiceBundle
from tests.conftest import make_actor


def _seed_artifact_type(services: ServiceBundle) -> None:
    services.schema.create_object_type(
        make_actor(),
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the change-feed API tests.",
        key_prefix="ART",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "Short human-readable name for the artifact.",
                "required": True,
            }
        ],
    )


class TestChangesEndpoint:
    def test_omitting_cursor_returns_current_cursor_with_no_events(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _seed_artifact_type(app_services)
        app_services.records.create_record(make_actor(), "artifact", {"title": "before"})

        response = client.get("/api/v1/changes")
        assert response.status_code == 200
        body = response.json()
        assert body["events"] == []
        assert body["next_cursor"] > 0

    def test_passing_cursor_back_returns_new_events(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _seed_artifact_type(app_services)
        app_services.records.create_record(make_actor(), "artifact", {"title": "before"})

        cursor = client.get("/api/v1/changes").json()["next_cursor"]

        app_services.records.create_record(make_actor(), "artifact", {"title": "after"})

        response = client.get("/api/v1/changes", params={"cursor": cursor})
        assert response.status_code == 200
        body = response.json()
        assert len(body["events"]) > 0
        assert all(event["id"] > cursor for event in body["events"])

    def test_cursor_correctness_across_pages_with_interleaved_writes(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _seed_artifact_type(app_services)
        for i in range(6):
            app_services.records.create_record(make_actor(), "artifact", {"title": f"e{i}"})
        latest = client.get("/api/v1/changes").json()["next_cursor"]

        seen: list[int] = []
        cursor = 0
        first_page_cursor: int | None = None
        while True:
            response = client.get("/api/v1/changes", params={"cursor": cursor, "limit": 2})
            assert response.status_code == 200
            body = response.json()
            events = body["events"]
            if not events:
                break
            if first_page_cursor is None:
                first_page_cursor = body["next_cursor"]
                # More writes land between fetching page one and resuming from it.
                app_services.records.create_record(
                    make_actor(), "artifact", {"title": "interleaved"}
                )
                latest = client.get("/api/v1/changes").json()["next_cursor"]
            seen.extend(event["id"] for event in events)
            cursor = body["next_cursor"]
            if cursor >= latest:
                break

        assert seen == sorted(set(seen))  # no skips or duplicates
        assert len(seen) == len(set(seen))
        assert first_page_cursor is not None

        # The cursor handed back from page one is still valid and resumable.
        resumed = client.get("/api/v1/changes", params={"cursor": first_page_cursor}).json()
        assert all(event["id"] > first_page_cursor for event in resumed["events"])

    def test_object_types_scoping(self, client: TestClient, app_services: ServiceBundle) -> None:
        _seed_artifact_type(app_services)
        app_services.schema.create_object_type(
            make_actor(),
            key="widget",
            name="Widget",
            name_plural="Widgets",
            description="A second object type used to test change-feed scoping.",
            key_prefix="WID",
            fields=[
                {
                    "key": "label",
                    "name": "Label",
                    "type": "short_text",
                    "description": "Short label.",
                }
            ],
        )
        widget_type, _ = app_services.schema.get_object_type(make_actor(), "widget")

        app_services.records.create_record(make_actor(), "artifact", {"title": "a"})
        app_services.records.create_record(make_actor(), "widget", {"label": "w"})

        response = client.get("/api/v1/changes", params={"cursor": 0, "object_types": "widget"})
        assert response.status_code == 200
        body = response.json()
        assert body["events"]
        assert all(event["object_type_id"] == widget_type.id for event in body["events"])

    def test_unknown_object_type_returns_error_envelope(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        _seed_artifact_type(app_services)

        response = client.get("/api/v1/changes", params={"cursor": 0, "object_types": "nope"})
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "unknown_object_type"
