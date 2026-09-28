"""HTTP-layer acceptance tests for comment routes (FR-C1 through FR-C8).

These prove the same behavior ``tests/test_comments.py`` already proves at the
service layer is reachable over HTTP, with no logic duplicated in the route
handlers (DD-3). Most requests in this suite are attributed to the same seeded
bootstrap principal (``ActorContext`` constructed at the edge by
``RequestContextMiddleware``), so "only the author may edit" is mostly exercised as a
happy path here; ``tests/test_comments.py`` holds the cross-principal failures.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.services import ServiceBundle
from tests.conftest import auth, make_actor


@pytest.fixture
def task_key(app_services: ServiceBundle) -> str:
    app_services.schema.create_object_type(
        make_actor(),
        key="task",
        name="Task",
        name_plural="Tasks",
        description="A unit of work tracked for the comment HTTP test suite.",
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
    record = app_services.records.create_record(make_actor(), "task", {"title": "Do the thing"})
    return record.key


def test_post_creates_comment_attributed_to_bootstrap_principal(
    client: TestClient, task_key: str
) -> None:
    response = client.post(f"/api/v1/records/{task_key}/comments", json={"body": "First note"})
    assert response.status_code == 200
    body = response.json()
    assert body["body"] == "First note"
    assert body["record_id"]
    assert body["author_id"] == BOOTSTRAP_PRINCIPAL_ID
    assert body["agent_label_id"] is None
    assert body["edited"] is False
    assert body["deleted_at"] is None
    assert body["created_at"] == body["updated_at"]


def test_post_on_unknown_record_returns_not_found(client: TestClient) -> None:
    response = client.post("/api/v1/records/TSK-999/comments", json={"body": "Hello"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_post_rejects_empty_body(client: TestClient, task_key: str) -> None:
    response = client.post(f"/api/v1/records/{task_key}/comments", json={"body": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_get_lists_comments_chronologically_with_author_agent_label_and_edited(
    client: TestClient, task_key: str
) -> None:
    first = client.post(f"/api/v1/records/{task_key}/comments", json={"body": "one"}).json()
    second = client.post(f"/api/v1/records/{task_key}/comments", json={"body": "two"}).json()

    response = client.get(f"/api/v1/records/{task_key}/comments")
    assert response.status_code == 200
    comments = response.json()["comments"]
    assert [c["id"] for c in comments] == [first["id"], second["id"]]
    assert [c["body"] for c in comments] == ["one", "two"]
    for comment in comments:
        assert comment["author_id"] == BOOTSTRAP_PRINCIPAL_ID
        assert comment["agent_label_id"] is None
        assert comment["edited"] is False


def test_patch_edits_comment_and_sets_edited_flag(client: TestClient, task_key: str) -> None:
    created = client.post(
        f"/api/v1/records/{task_key}/comments", json={"body": "original body"}
    ).json()

    response = client.patch(f"/api/v1/comments/{created['id']}", json={"body": "revised body"})
    assert response.status_code == 200
    updated = response.json()
    assert updated["id"] == created["id"]
    assert updated["body"] == "revised body"
    assert updated["edited"] is True
    assert updated["deleted_at"] is None


def test_patch_rejects_empty_body(client: TestClient, task_key: str) -> None:
    created = client.post(f"/api/v1/records/{task_key}/comments", json={"body": "keep me"}).json()

    response = client.patch(f"/api/v1/comments/{created['id']}", json={"body": ""})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_patch_unknown_comment_returns_not_found(client: TestClient) -> None:
    response = client.patch("/api/v1/comments/does-not-exist", json={"body": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_delete_soft_deletes_and_is_omitted_from_subsequent_list(
    client: TestClient, task_key: str
) -> None:
    keep = client.post(f"/api/v1/records/{task_key}/comments", json={"body": "keep"}).json()
    doomed = client.post(f"/api/v1/records/{task_key}/comments", json={"body": "doomed"}).json()

    response = client.delete(f"/api/v1/comments/{doomed['id']}")
    assert response.status_code == 200
    deleted = response.json()
    assert deleted["id"] == doomed["id"]
    assert deleted["deleted_at"] is not None

    listing = client.get(f"/api/v1/records/{task_key}/comments").json()["comments"]
    assert [c["id"] for c in listing] == [keep["id"]]


def test_delete_unknown_comment_returns_not_found(client: TestClient) -> None:
    response = client.delete("/api/v1/comments/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_comment_count_and_last_comment_at_reflect_http_writes_in_same_transaction(
    client: TestClient, app_services: ServiceBundle, task_key: str
) -> None:
    before = app_services.records.get_record(make_actor(), task_key)
    assert before.comment_count == 0
    assert before.last_comment_at is None

    created = client.post(f"/api/v1/records/{task_key}/comments", json={"body": "counted"}).json()

    after = app_services.records.get_record(make_actor(), task_key)
    assert after.comment_count == 1
    assert after.last_comment_at == created["created_at"]


def test_a_non_authors_refused_delete_is_403_forbidden_over_http(
    client: TestClient, app_services: ServiceBundle, task_key: str
) -> None:
    """The moderation rule over the wire. Were ``delete_comment`` to read the
    credential's raw scope as authority, this refusal would be ``validation_failed`` /
    422; it is the access service's own ``forbidden`` / 403, carrying the level held and who
    can raise it.

    The caller is the sharpest case: a ``creator`` (``role_scope`` is ``admin``, so
    it may hold an ``admin`` PAT) that holds only ``write`` on the type.
    """
    doomed = client.post(
        f"/api/v1/records/{task_key}/comments", json={"body": "not yours to delete"}
    ).json()

    creator = app_services.principals.create_user(
        make_actor(),
        email="moderator@example.com",
        display_name="Would-be Moderator",
        role="creator",
        password="correct-horse-battery-staple",
    )
    token = app_services.tokens.mint(
        make_actor(), name="creator pat", scope="admin", principal_id=creator.id
    ).plaintext
    app_services.access.grant(make_actor(), "task", creator.id, "write")

    refused = client.delete(f"/api/v1/comments/{doomed['id']}", headers=auth(token))
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "forbidden"
    assert "task" in refused.json()["error"]["message"]

    # And the comment is still there.
    listed = client.get(f"/api/v1/records/{task_key}/comments").json()["comments"]
    assert [c["id"] for c in listed] == [doomed["id"]]
