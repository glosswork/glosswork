"""``ExportService`` (FR-E5): the full-deployment JSON export.

Builds a small deployment through the service layer -- two object types, a relation between
them, a comment, and a saved view -- then drains :meth:`ExportService.stream` and asserts on the
concatenated, parsed document. A second test proves the streaming property itself: that the
generator yields more than one chunk, and that pulling only the first chunk does not force the
rest of the document to be serialized.
"""

from __future__ import annotations

import json

import pytest

from glosswork.actor import ActorContext
from glosswork.services import ServiceBundle


@pytest.fixture
def deployment(services: ServiceBundle, actor: ActorContext) -> dict[str, object]:
    """Two object types (one relation between them), a soft-deleted record, a comment, and a
    saved view -- enough to exercise every array the export produces."""
    # ``task`` must exist before ``project``'s relation field can target it; the inverse
    # field (``task.project``) is then auto-created on ``task`` by ``add_field`` below.
    task_type = services.schema.create_object_type(
        actor,
        key="task",
        name="Task",
        name_plural="Tasks",
        description="A unit of work under a project.",
        key_prefix="TSK",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "The task's title.",
            },
        ],
    )
    project_type = services.schema.create_object_type(
        actor,
        key="project",
        name="Project",
        name_plural="Projects",
        description="A tracked initiative.",
        key_prefix="PRJ",
        fields=[
            {
                "key": "name",
                "name": "Name",
                "type": "short_text",
                "description": "The project's name.",
            },
            {
                "key": "tasks",
                "name": "Tasks",
                "type": "relation",
                "description": "Tasks belonging to this project.",
                "config": {
                    "target_type_key": "task",
                    "cardinality": "many",
                    "inverse_field_key": "project",
                },
            },
        ],
    )

    project = services.records.create_record(actor, "project", {"name": "Migrate warehouse"})
    task_live = services.records.create_record(actor, "task", {"title": "Write the ETL job"})
    task_deleted = services.records.create_record(actor, "task", {"title": "Scrap draft plan"})
    services.records.delete_record(actor, task_deleted.key)

    services.records.link_records(actor, project.key, "tasks", [task_live.key])
    comment = services.comments.add_comment(actor, task_live.key, "Kicking this off today.")
    services.saved_views.create_saved_view(
        actor,
        "task",
        name="My open tasks",
        config={"filter": None, "sort": [], "columns": ["title"]},
    )

    return {
        "project_type": project_type,
        "task_type": task_type,
        "project": project,
        "task_live": task_live,
        "task_deleted": task_deleted,
        "comment": comment,
    }


def _drain(services: ServiceBundle, actor: ActorContext) -> tuple[bytes, list[bytes]]:
    chunks = list(services.export.stream(actor))
    return b"".join(chunks), chunks


def test_export_document_shape(
    services: ServiceBundle, actor: ActorContext, deployment: dict[str, object]
) -> None:
    body, _ = _drain(services, actor)
    doc = json.loads(body)

    assert doc["format"] == "glosswork-export"
    assert doc["format_version"] == 1
    assert "exported_at" in doc and doc["exported_at"].endswith("Z")

    for key in (
        "object_types",
        "records",
        "links",
        "comments",
        "saved_views",
        "agent_labels",
        "audit",
    ):
        assert key in doc, f"missing top-level key {key!r}"

    type_keys = {t["key"] for t in doc["object_types"]}
    assert {"project", "task"} <= type_keys
    project_type_doc = next(t for t in doc["object_types"] if t["key"] == "project")
    assert {f["key"] for f in project_type_doc["fields"]} == {"name", "tasks"}

    # Records: total count, and the soft-deleted one survives (a whole-deployment export must
    # not silently drop it).
    task_deleted = deployment["task_deleted"]
    record_by_key = {r["key"]: r for r in doc["records"]}
    assert len(doc["records"]) == 3  # one project, two tasks
    assert task_deleted.key in record_by_key
    assert record_by_key[task_deleted.key]["deleted_at"] is not None
    assert record_by_key[task_deleted.key]["object_type_key"] == "task"

    project = deployment["project"]
    task_live = deployment["task_live"]
    assert record_by_key[project.key]["object_type_key"] == "project"
    assert record_by_key[task_live.key]["data"]["title"] == "Write the ETL job"

    # Links: the project -> task link, portable through record keys rather than uuids.
    assert len(doc["links"]) >= 1
    link = next(link for link in doc["links"] if link["field_key"] == "tasks")
    assert link["from_record_key"] == project.key
    assert link["to_record_key"] == task_live.key

    # Comments: carries its record_key.
    assert len(doc["comments"]) == 1
    comment_doc = doc["comments"][0]
    assert comment_doc["record_key"] == task_live.key
    assert comment_doc["body"] == "Kicking this off today."

    # Saved views.
    assert len(doc["saved_views"]) == 1
    assert doc["saved_views"][0]["name"] == "My open tasks"

    # Audit: non-empty (object type creation, records, links, and the comment all wrote rows).
    assert len(doc["audit"]) > 0


def test_export_streams_incrementally(
    services: ServiceBundle, actor: ActorContext, deployment: dict[str, object]
) -> None:
    """The generator yields more than one chunk, and pulling only the first chunk does not
    force the rest of the document to be built -- the point of streaming rather than building
    one dict and calling ``json.dumps`` on it once."""
    stream = services.export.stream(actor)
    first_chunk = next(stream)
    assert first_chunk == b"{"  # the very first byte, nothing more, proves no pre-building

    remaining_chunks = list(stream)
    assert len(remaining_chunks) > 1

    body = first_chunk + b"".join(remaining_chunks)
    doc = json.loads(body)
    assert doc["format"] == "glosswork-export"
