"""REST layer: record CRUD, query, bulk-update, links, and history routes.

Records (CRUD, versioning, soft delete), the query grammar over HTTP, and relations;
docs/MCP_TOOLS.md sections 4, 5.1-5.2, 6, 8.

These tests prove the HTTP wiring onto ``RecordService`` / ``CommentService`` /
``SchemaService`` / ``AttachmentService``; the underlying business rules are already
proven at the service layer in test_query_operators.py, test_records.py,
test_relations.py, test_projection_and_expansion.py, and test_bulk_update.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from glosswork.services import ServiceBundle
from tests.conftest import KITCHEN_SINK_FIELDS, make_actor


@pytest.fixture
def artifact_type(app_services: ServiceBundle) -> None:
    """Seeds the kitchen-sink 'artifact' object type directly through the service
    layer, on the same database the ``client`` fixture talks to."""
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


@pytest.fixture
def project_relation(app_services: ServiceBundle, artifact_type: None) -> None:
    """A many-to-many 'projects' relation from artifact to a new 'project' type,
    with an auto-created inverse 'artifacts' field (mirrors test_relations.py)."""
    app_services.schema.create_object_type(
        make_actor(),
        key="project",
        name="Project",
        name_plural="Projects",
        description="A funding vehicle that groups related artifacts, used by the "
        "HTTP relations tests.",
        key_prefix="PROJ",
    )
    app_services.schema.add_field(
        make_actor(),
        "artifact",
        {
            "key": "projects",
            "name": "Projects",
            "type": "relation",
            "description": "Projects this artifact contributes to.",
            "config": {
                "target_type_key": "project",
                "cardinality": "many",
                "inverse_field_key": "artifacts",
            },
        },
    )


def _create(client: TestClient, values: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/v1/object-types/artifact/records", json=values)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


class TestRecordCrud:
    def test_create_returns_assigned_key_and_version(
        self, client: TestClient, artifact_type: None
    ) -> None:
        body = _create(client, {"title": "First artifact"})
        assert body["key"] == "ART-001"
        assert body["version"] == 1
        assert body["data"]["title"] == "First artifact"

    def test_get_by_human_key_and_by_uuid(self, client: TestClient, artifact_type: None) -> None:
        created = _create(client, {"title": "Lookup me"})

        by_key = client.get(f"/api/v1/records/{created['key']}")
        assert by_key.status_code == 200
        assert by_key.json()["id"] == created["id"]

        by_uuid = client.get(f"/api/v1/records/{created['id']}")
        assert by_uuid.status_code == 200
        assert by_uuid.json()["key"] == created["key"]

    def test_get_unknown_ref_is_not_found(self, client: TestClient, artifact_type: None) -> None:
        response = client.get("/api/v1/records/ART-999")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_include_comments(
        self, client: TestClient, app_services: ServiceBundle, artifact_type: None
    ) -> None:
        created = _create(client, {"title": "Has comments"})
        app_services.comments.add_comment(make_actor(), created["key"], "First note")
        app_services.comments.add_comment(make_actor(), created["key"], "Second note")

        response = client.get(f"/api/v1/records/{created['key']}", params={"include": "comments"})
        assert response.status_code == 200
        comments = response.json()["comments"]
        assert [c["body"] for c in comments] == ["First note", "Second note"]
        assert comments[0]["record_id"] == created["id"]
        assert comments[0]["edited"] is False

    def test_include_links(
        self, client: TestClient, app_services: ServiceBundle, artifact_type: None
    ) -> None:
        macro = _create(client, {"title": "Macro"})
        micro = _create(client, {"title": "Micro"})
        app_services.records.link_records(make_actor(), micro["key"], "parent", [macro["key"]])

        response = client.get(f"/api/v1/records/{micro['key']}", params={"include": "links"})
        assert response.status_code == 200
        links = response.json()["links"]
        # The entry is {key, id, display}, not {key, id}. `display` is
        # the target type's display field value -- here the artifact type's own `title`.
        assert links["parent"] == [{"key": macro["key"], "id": macro["id"], "display": "Macro"}]
        assert links["children"] == []

    def test_include_history(self, client: TestClient, artifact_type: None) -> None:
        created = _create(client, {"title": "Audited"})
        client.patch(f"/api/v1/records/{created['key']}", json={"values": {"title": "Renamed"}})

        response = client.get(f"/api/v1/records/{created['key']}", params={"include": "history"})
        assert response.status_code == 200
        history = response.json()["history"]
        actions = {e["action"] for e in history}
        assert "create" in actions
        assert "update" in actions
        update_event = next(e for e in history if e["action"] == "update")
        assert update_event["field_key"] == "title"
        assert update_event["old_value"] == "Audited"
        assert update_event["new_value"] == "Renamed"

    def test_include_attachments_skips_unresolvable_ids(
        self, client: TestClient, app_services: ServiceBundle, artifact_type: None
    ) -> None:
        attachment = app_services.attachments.upload(
            make_actor(), "notes.txt", "text/plain", b"hello world"
        )
        created = _create(client, {"title": "With files", "files": [attachment.id, "missing-id"]})

        response = client.get(
            f"/api/v1/records/{created['key']}", params={"include": "attachments"}
        )
        assert response.status_code == 200
        files = response.json()["attachments"]["files"]
        assert len(files) == 1
        assert files[0]["id"] == attachment.id
        assert files[0]["filename"] == "notes.txt"
        assert files[0]["byte_size"] == len(b"hello world")

    def test_patch_matching_version_applies(self, client: TestClient, artifact_type: None) -> None:
        created = _create(client, {"title": "v1"})
        response = client.patch(
            f"/api/v1/records/{created['key']}",
            json={"values": {"title": "v2"}, "expected_version": 1},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["version"] == 2
        assert body["data"]["title"] == "v2"

    def test_patch_version_mismatch_returns_409_with_details(
        self, client: TestClient, artifact_type: None
    ) -> None:
        created = _create(client, {"title": "v1"})
        client.patch(f"/api/v1/records/{created['key']}", json={"values": {"title": "v2"}})

        response = client.patch(
            f"/api/v1/records/{created['key']}",
            json={"values": {"title": "v3-conflict"}, "expected_version": 1},
        )
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "version_conflict"
        details = error["details"]
        assert details["current_version"] == 2
        assert "title" in details["conflicting_fields"]
        assert details["conflicting_fields"]["title"]["current_value"] == "v2"
        assert "title" in details["changed_since_your_version"]

    def test_patch_force_bypasses_version_check(
        self, client: TestClient, artifact_type: None
    ) -> None:
        created = _create(client, {"title": "v1"})
        client.patch(f"/api/v1/records/{created['key']}", json={"values": {"title": "v2"}})

        response = client.patch(
            f"/api/v1/records/{created['key']}",
            json={"values": {"title": "forced"}, "expected_version": 1, "force": True},
        )
        assert response.status_code == 200
        assert response.json()["data"]["title"] == "forced"

    def test_delete_soft_deletes_and_restore_brings_it_back(
        self, client: TestClient, artifact_type: None
    ) -> None:
        created = _create(client, {"title": "Deletable"})
        deleted = client.delete(f"/api/v1/records/{created['key']}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted_at"] is not None

        restored = client.post(f"/api/v1/records/{created['key']}/restore")
        assert restored.status_code == 200
        assert restored.json()["deleted_at"] is None

    def test_delete_blocked_by_inbound_links_unless_forced(
        self, client: TestClient, app_services: ServiceBundle, artifact_type: None
    ) -> None:
        macro = _create(client, {"title": "Macro"})
        micro = _create(client, {"title": "Micro"})
        app_services.records.link_records(make_actor(), micro["key"], "parent", [macro["key"]])

        blocked = client.delete(f"/api/v1/records/{macro['key']}")
        assert blocked.status_code == 409
        error = blocked.json()["error"]
        assert error["code"] == "relation_blocked"
        assert error["details"]["blocking_record_keys"] == [micro["key"]]

        forced = client.delete(f"/api/v1/records/{macro['key']}", params={"force": True})
        assert forced.status_code == 200
        assert forced.json()["deleted_at"] is not None

    def test_unknown_field_on_create_names_valid_keys_and_near_miss(
        self, client: TestClient, artifact_type: None
    ) -> None:
        response = client.post(
            "/api/v1/object-types/artifact/records", json={"titel": "typo'd field"}
        )
        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "unknown_field"
        assert "title" in error["details"]["valid_keys"]
        assert "title" in error["details"]["near_misses"]

    def test_get_record_history_route(self, client: TestClient, artifact_type: None) -> None:
        created = _create(client, {"title": "History route"})
        client.patch(f"/api/v1/records/{created['key']}", json={"values": {"title": "Changed"}})

        response = client.get(f"/api/v1/records/{created['key']}/history")
        assert response.status_code == 200
        events = response.json()["events"]
        assert any(e["action"] == "update" and e["field_key"] == "title" for e in events)

        scoped = client.get(
            f"/api/v1/records/{created['key']}/history", params={"field_key": "title"}
        )
        assert scoped.status_code == 200
        assert all(e["field_key"] == "title" for e in scoped.json()["events"])


class TestQueryRoute:
    def _query(self, client: TestClient, **kwargs: Any) -> dict[str, Any]:
        response = client.post("/api/v1/object-types/artifact/query", json=kwargs)
        assert response.status_code == 200, response.text
        return response.json()  # type: ignore[no-any-return]

    def test_operator_matrix_slice(self, client: TestClient, artifact_type: None) -> None:
        _create(client, {"title": "Alpha service", "points": 1, "status": "todo", "tags": ["red"]})
        _create(
            client,
            {"title": "Beta service", "points": 5, "status": "doing", "tags": ["red", "green"]},
        )
        _create(client, {"title": "Gamma tool", "points": 10, "status": "done", "tags": ["blue"]})

        contains = self._query(
            client, filter={"field": "title", "op": "contains", "value": "service"}
        )
        assert {r["data"]["title"] for r in contains["records"]} == {
            "Alpha service",
            "Beta service",
        }

        between = self._query(
            client, filter={"field": "points", "op": "between", "value": [2, 10]}, fields="*"
        )
        assert {r["data"]["points"] for r in between["records"]} == {5, 10}

        status_in = self._query(
            client, filter={"field": "status", "op": "in", "value": ["todo", "done"]}
        )
        assert {r["data"]["status"] for r in status_in["records"]} == {"todo", "done"}

        has_any = self._query(client, filter={"field": "tags", "op": "has_any", "value": ["red"]})
        assert {r["data"]["title"] for r in has_any["records"]} == {
            "Alpha service",
            "Beta service",
        }

    def test_and_or_not_nesting_and_bare_condition(
        self, client: TestClient, artifact_type: None
    ) -> None:
        _create(client, {"title": "A", "points": 1, "status": "todo"})
        _create(client, {"title": "B", "points": 5, "status": "doing"})
        _create(client, {"title": "C", "points": 10, "status": "done"})

        bare = self._query(client, filter={"field": "status", "op": "eq", "value": "todo"})
        assert {r["data"]["title"] for r in bare["records"]} == {"A"}

        and_or = self._query(
            client,
            filter={
                "and": [
                    {"field": "points", "op": "gte", "value": 5},
                    {
                        "or": [
                            {"field": "status", "op": "eq", "value": "doing"},
                            {"field": "status", "op": "eq", "value": "done"},
                        ]
                    },
                ]
            },
        )
        assert {r["data"]["title"] for r in and_or["records"]} == {"B", "C"}

        negated = self._query(
            client, filter={"not": {"field": "status", "op": "eq", "value": "todo"}}
        )
        assert {r["data"]["title"] for r in negated["records"]} == {"B", "C"}

    def test_system_pseudo_fields_me_and_date_token(
        self, client: TestClient, artifact_type: None
    ) -> None:
        from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID

        today = datetime.now(UTC).date().isoformat()
        _create(client, {"title": "Owned by me", "owner": BOOTSTRAP_PRINCIPAL_ID, "due": today})
        _create(client, {"title": "Owned by nobody"})

        by_key = self._query(client, filter={"field": "key", "op": "eq", "value": "ART-001"})
        assert len(by_key["records"]) == 1

        by_owner_me = self._query(client, filter={"field": "owner", "op": "eq", "value": "@me"})
        assert {r["data"]["title"] for r in by_owner_me["records"]} == {"Owned by me"}

        by_created_by = self._query(
            client,
            filter={"field": "created_by", "op": "eq", "value": BOOTSTRAP_PRINCIPAL_ID},
        )
        assert len(by_created_by["records"]) == 2

        by_due_today = self._query(client, filter={"field": "due", "op": "eq", "value": "@today"})
        assert {r["data"]["title"] for r in by_due_today["records"]} == {"Owned by me"}

    def test_multi_key_sort(self, client: TestClient, artifact_type: None) -> None:
        _create(client, {"title": "B", "points": 1})
        _create(client, {"title": "A", "points": 1})
        _create(client, {"title": "C", "points": 0})

        result = self._query(
            client,
            fields="*",
            sort=[{"field": "points", "dir": "asc"}, {"field": "title", "dir": "asc"}],
        )
        titles = [r["data"]["title"] for r in result["records"]]
        assert titles == ["C", "A", "B"]

    def test_keyset_pagination_across_duplicate_sort_values(
        self, client: TestClient, artifact_type: None
    ) -> None:
        for i in range(5):
            _create(client, {"title": f"dup-{i}", "points": 1})

        seen: list[str] = []
        cursor: str | None = None
        for _ in range(10):
            page = self._query(
                client,
                fields="*",
                sort=[{"field": "points", "dir": "asc"}],
                limit=2,
                cursor=cursor,
            )
            seen.extend(r["key"] for r in page["records"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert len(seen) == 5
        assert len(set(seen)) == 5

    def test_field_projection_and_star(self, client: TestClient, artifact_type: None) -> None:
        _create(client, {"title": "Projected", "summary": "hidden by explicit projection"})

        explicit = self._query(client, fields=["title"])
        (record,) = explicit["records"]
        assert record["data"] == {"title": "Projected"}

        star = self._query(client, fields="*")
        (record,) = star["records"]
        assert record["data"] == {
            "title": "Projected",
            "summary": "hidden by explicit projection",
        }

    def test_compact_default_projection_and_truncated_flag(
        self, client: TestClient, artifact_type: None
    ) -> None:
        _create(
            client,
            {
                "title": "Compact me",
                "summary": "should not appear in the compact default",
                "points": 7,
                "status": "doing",
            },
        )
        result = self._query(client)
        (record,) = result["records"]
        assert "summary" not in record["data"]
        assert "points" not in record["data"]
        assert record["data"]["title"] == "Compact me"
        assert result["truncated"] is True

    def test_include_deleted(self, client: TestClient, artifact_type: None) -> None:
        created = _create(client, {"title": "Will be deleted"})
        client.delete(f"/api/v1/records/{created['key']}")

        default_result = self._query(client)
        assert created["key"] not in {r["key"] for r in default_result["records"]}

        with_deleted = self._query(client, include_deleted=True)
        assert created["key"] in {r["key"] for r in with_deleted["records"]}

    def test_expand_relations_on_query_route(self, client: TestClient, artifact_type: None) -> None:
        parent = _create(client, {"title": "Macro", "points": 100})
        child = _create(client, {"title": "Micro"})
        client.post(
            f"/api/v1/records/{child['key']}/links/parent",
            json={"to_records": [parent["key"]]},
        )

        result = self._query(
            client,
            filter={"field": "key", "op": "eq", "value": child["key"]},
            fields="*",
            expand_relations=["parent"],
        )
        (record,) = result["records"]
        (linked,) = record["expand"]["parent"]
        assert linked["key"] == parent["key"]
        assert linked["display"] == "Macro"


class TestBulkUpdateRoute:
    def test_dry_run_reports_count_and_samples_without_writing(
        self, client: TestClient, artifact_type: None
    ) -> None:
        for i in range(3):
            _create(client, {"title": f"bulk-{i}", "status": "todo"})

        response = client.post(
            "/api/v1/object-types/artifact/bulk-update",
            json={
                "filter": {"field": "status", "op": "eq", "value": "todo"},
                "values": {"status": "doing"},
                "dry_run": True,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["dry_run"] is True
        assert body["affected_count"] == 3
        assert len(body["sample_keys"]) == 3

        unchanged = client.get("/api/v1/records/ART-001")
        assert unchanged.json()["data"]["status"] == "todo"

    def test_live_run_applies_patch_and_returns_affected_count(
        self, client: TestClient, artifact_type: None
    ) -> None:
        for i in range(3):
            _create(client, {"title": f"bulk-{i}", "status": "todo"})

        response = client.post(
            "/api/v1/object-types/artifact/bulk-update",
            json={
                "filter": {"field": "status", "op": "eq", "value": "todo"},
                "values": {"status": "doing"},
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["dry_run"] is False
        assert body["affected_count"] == 3

        changed = client.get("/api/v1/records/ART-001")
        assert changed.json()["data"]["status"] == "doing"


class TestLinksRoutes:
    def test_link_and_unlink_round_trip(self, client: TestClient, project_relation: None) -> None:
        artifact = _create(client, {"title": "Linkable"})
        project_resp = client.post("/api/v1/object-types/project/records", json={})
        assert project_resp.status_code == 200
        project = project_resp.json()

        linked = client.post(
            f"/api/v1/records/{artifact['key']}/links/projects",
            json={"to_records": [project["key"]]},
        )
        assert linked.status_code == 200
        assert linked.json() == {"field_key": "projects", "linked": [project["key"]]}

        with_links = client.get(f"/api/v1/records/{artifact['key']}", params={"include": "links"})
        assert with_links.json()["links"]["projects"] == [
            # `project` is created with no fields, so it has no display field at all and
            # `display` is null -- the key is present and empty, which is the shape the
            # frontend falls back from.
            {"key": project["key"], "id": project["id"], "display": None}
        ]

        unlinked = client.request(
            "DELETE",
            f"/api/v1/records/{artifact['key']}/links/projects",
            json={"to_records": [project["key"]]},
        )
        assert unlinked.status_code == 200
        assert unlinked.json() == {"field_key": "projects", "unlinked_count": 1}

        after = client.get(f"/api/v1/records/{artifact['key']}", params={"include": "links"})
        assert after.json()["links"]["projects"] == []

    def test_cardinality_one_rejects_second_link(
        self, client: TestClient, artifact_type: None
    ) -> None:
        p1 = _create(client, {"title": "P1"})
        p2 = _create(client, {"title": "P2"})
        p3 = _create(client, {"title": "P3"})

        first = client.post(
            f"/api/v1/records/{p1['key']}/links/parent", json={"to_records": [p2["key"]]}
        )
        assert first.status_code == 200

        second = client.post(
            f"/api/v1/records/{p1['key']}/links/parent", json={"to_records": [p3["key"]]}
        )
        assert second.status_code == 422
        assert second.json()["error"]["code"] == "validation_failed"

    def test_relation_filters_through_query_route(
        self, client: TestClient, project_relation: None
    ) -> None:
        a1 = _create(client, {"title": "A1"})
        a2 = _create(client, {"title": "A2"})
        a3 = _create(client, {"title": "A3"})
        p1 = client.post("/api/v1/object-types/project/records", json={}).json()
        p2 = client.post("/api/v1/object-types/project/records", json={}).json()

        client.post(f"/api/v1/records/{a1['key']}/links/projects", json={"to_records": [p1["key"]]})
        client.post(f"/api/v1/records/{a2['key']}/links/projects", json={"to_records": [p2["key"]]})

        def query(filter_: dict[str, Any]) -> set[str]:
            response = client.post("/api/v1/object-types/artifact/query", json={"filter": filter_})
            assert response.status_code == 200
            return {r["key"] for r in response.json()["records"]}

        assert query({"field": "projects", "op": "linked_to", "value": p1["key"]}) == {a1["key"]}
        assert query(
            {"field": "projects", "op": "linked_to_any", "value": [p1["key"], p2["key"]]}
        ) == {a1["key"], a2["key"]}
        assert query({"field": "projects", "op": "has_links"}) == {a1["key"], a2["key"]}
        assert query({"field": "projects", "op": "has_no_links"}) == {a3["key"]}


class TestGetRecordExpandRelations:
    def test_expand_relations_query_params(self, client: TestClient, artifact_type: None) -> None:
        parent = _create(client, {"title": "Macro", "points": 42})
        child = _create(client, {"title": "Micro"})
        client.post(
            f"/api/v1/records/{child['key']}/links/parent",
            json={"to_records": [parent["key"]]},
        )

        response = client.get(
            f"/api/v1/records/{child['key']}",
            params={"expand_relations": "parent", "fields": "points"},
        )
        assert response.status_code == 200
        (linked,) = response.json()["expand"]["parent"]
        assert linked["key"] == parent["key"]
        assert linked["display"] == "Macro"
        assert linked["fields"] == {"points": 42}
