"""Relations acceptance tests (FR-L1 through FR-L5, docs/DATA_MODEL.md section 6).

Every write call below uses a fresh actor from ``make_actor()`` (see
``tests/conftest.py``), one per logical call, exactly as separate real requests
would arrive.
"""

from __future__ import annotations

import pytest

from glosswork.actor import ActorContext
from glosswork.errors import RelationBlockedError, ValidationFailedError
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.services import ServiceBundle
from tests.conftest import make_actor


def _add_project_relation(services: ServiceBundle) -> ObjectType:
    """Create a 'project' object type and a many-to-many 'projects' relation field
    on 'artifact', with an auto-created inverse 'artifacts' field (FR-L2, FR-L3).
    'project' carries no fields of its own; records of that type are created with
    an empty value document.
    """
    project_type = services.schema.create_object_type(
        make_actor(),
        key="project",
        name="Project",
        name_plural="Projects",
        description="A funding vehicle that groups related artifacts, used by the "
        "relations test suite.",
        key_prefix="PROJ",
    )
    services.schema.add_field(
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
    return project_type


def test_many_to_many_links_visible_from_both_directions(
    services: ServiceBundle, sink_type: tuple[ObjectType, dict[str, FieldDef]]
) -> None:
    """Several artifacts linked to several projects through a cardinality-'many'
    field are visible via list_links from the field and from the auto-created
    inverse field alike (FR-L2)."""
    _add_project_relation(services)
    a1 = services.records.create_record(make_actor(), "artifact", {"title": "Artifact 1"})
    a2 = services.records.create_record(make_actor(), "artifact", {"title": "Artifact 2"})
    a3 = services.records.create_record(make_actor(), "artifact", {"title": "Artifact 3"})
    p1 = services.records.create_record(make_actor(), "project", {})
    p2 = services.records.create_record(make_actor(), "project", {})

    services.records.link_records(make_actor(), a1.key, "projects", [p1.key, p2.key])
    services.records.link_records(make_actor(), a2.key, "projects", [p1.key])
    services.records.link_records(make_actor(), a3.key, "projects", [p2.key])

    assert [r.key for r in services.records.list_links(make_actor(), a1.key, "projects")] == [
        p1.key,
        p2.key,
    ]
    assert [r.key for r in services.records.list_links(make_actor(), a2.key, "projects")] == [
        p1.key
    ]
    assert [r.key for r in services.records.list_links(make_actor(), a3.key, "projects")] == [
        p2.key
    ]
    assert [r.key for r in services.records.list_links(make_actor(), p1.key, "artifacts")] == [
        a1.key,
        a2.key,
    ]
    assert [r.key for r in services.records.list_links(make_actor(), p2.key, "artifacts")] == [
        a1.key,
        a3.key,
    ]


def test_self_referential_links(
    services: ServiceBundle, sink_type: tuple[ObjectType, dict[str, FieldDef]]
) -> None:
    """Self-referential relations are the hierarchy mechanism (FR-L2): 'artifact'
    appears on both ends of its own 'parent' / 'children' field pair, e.g. a macro
    artifact with several micro artifacts rolling up into it."""
    macro = services.records.create_record(make_actor(), "artifact", {"title": "Macro"})
    micro1 = services.records.create_record(make_actor(), "artifact", {"title": "Micro 1"})
    micro2 = services.records.create_record(make_actor(), "artifact", {"title": "Micro 2"})

    services.records.link_records(make_actor(), micro1.key, "parent", [macro.key])
    services.records.link_records(make_actor(), micro2.key, "parent", [macro.key])

    assert [r.key for r in services.records.list_links(make_actor(), micro1.key, "parent")] == [
        macro.key
    ]
    assert [r.key for r in services.records.list_links(make_actor(), micro2.key, "parent")] == [
        macro.key
    ]
    assert [r.key for r in services.records.list_links(make_actor(), macro.key, "children")] == [
        micro1.key,
        micro2.key,
    ]


def test_cardinality_one_enforced_in_service_layer(
    services: ServiceBundle, sink_type: tuple[ObjectType, dict[str, FieldDef]]
) -> None:
    """cardinality: 'one' is enforced in the service layer by rejecting a second
    link, not by a different table shape (FR-L1, docs/DATA_MODEL.md section 6).
    This holds whether the second link arrives as a separate call or as two
    targets in one call."""
    p1 = services.records.create_record(make_actor(), "artifact", {"title": "P1"})
    p2 = services.records.create_record(make_actor(), "artifact", {"title": "P2"})
    p3 = services.records.create_record(make_actor(), "artifact", {"title": "P3"})
    p4 = services.records.create_record(make_actor(), "artifact", {"title": "P4"})

    services.records.link_records(make_actor(), p1.key, "parent", [p2.key])

    with pytest.raises(ValidationFailedError, match="cardinality"):
        services.records.link_records(make_actor(), p1.key, "parent", [p3.key])

    with pytest.raises(ValidationFailedError, match="cardinality"):
        services.records.link_records(make_actor(), p4.key, "parent", [p2.key, p3.key])

    # The rejected attempts left the original link untouched.
    assert [r.key for r in services.records.list_links(make_actor(), p1.key, "parent")] == [p2.key]
    assert services.records.list_links(make_actor(), p4.key, "parent") == []


def test_inverse_field_auto_created_and_reciprocal_links_maintained(
    services: ServiceBundle, sink_type: tuple[ObjectType, dict[str, FieldDef]]
) -> None:
    """Declaring inverse_field_key auto-creates the inverse field on the target
    type, and reciprocal links are kept consistent on both link and unlink
    (FR-L3)."""
    _add_project_relation(services)

    _, project_fields = services.schema.get_object_type(make_actor(), "project")
    fields_by_key = {f.key: f for f in project_fields}
    assert "artifacts" in fields_by_key
    inverse = fields_by_key["artifacts"]
    assert inverse.type == "relation"
    assert inverse.config["target_type_key"] == "artifact"

    a1 = services.records.create_record(make_actor(), "artifact", {"title": "A1"})
    p1 = services.records.create_record(make_actor(), "project", {})

    services.records.link_records(make_actor(), a1.key, "projects", [p1.key])
    assert [r.key for r in services.records.list_links(make_actor(), a1.key, "projects")] == [
        p1.key
    ]
    assert [r.key for r in services.records.list_links(make_actor(), p1.key, "artifacts")] == [
        a1.key
    ]

    services.records.unlink_records(make_actor(), a1.key, "projects", [p1.key])
    assert services.records.list_links(make_actor(), a1.key, "projects") == []
    assert services.records.list_links(make_actor(), p1.key, "artifacts") == []


def test_delete_blocked_by_inbound_links_and_force_removes_them(
    services: ServiceBundle, sink_type: tuple[ObjectType, dict[str, FieldDef]]
) -> None:
    """Deleting a record that is the target of links is blocked and names the
    blocking records by key; force=True removes the links from both sides and
    the delete proceeds (FR-L4)."""
    macro = services.records.create_record(make_actor(), "artifact", {"title": "Macro"})
    micro = services.records.create_record(make_actor(), "artifact", {"title": "Micro"})
    services.records.link_records(make_actor(), micro.key, "parent", [macro.key])

    with pytest.raises(RelationBlockedError) as exc_info:
        services.records.delete_record(make_actor(), macro.key)
    assert exc_info.value.blocking_record_keys == [micro.key]

    deleted = services.records.delete_record(make_actor(), macro.key, force=True)
    assert deleted.deleted_at is not None

    # The link is gone from the source side...
    assert services.records.list_links(make_actor(), micro.key, "parent") == []

    # ...and from the reciprocal side, once the target is live again.
    restored = services.records.restore_record(make_actor(), macro.key)
    assert services.records.list_links(make_actor(), restored.key, "children") == []


def test_relation_filters(
    services: ServiceBundle, sink_type: tuple[ObjectType, dict[str, FieldDef]]
) -> None:
    """linked_to, linked_to_any, has_links, and has_no_links filters return the
    right record sets (FR-L5)."""
    _add_project_relation(services)
    a1 = services.records.create_record(make_actor(), "artifact", {"title": "A1"})
    a2 = services.records.create_record(make_actor(), "artifact", {"title": "A2"})
    a3 = services.records.create_record(make_actor(), "artifact", {"title": "A3"})
    p1 = services.records.create_record(make_actor(), "project", {})
    p2 = services.records.create_record(make_actor(), "project", {})

    services.records.link_records(make_actor(), a1.key, "projects", [p1.key])
    services.records.link_records(make_actor(), a2.key, "projects", [p2.key])
    # a3 is left unlinked to any project.

    actor: ActorContext = make_actor()

    linked_to_p1 = services.records.query_records(
        actor, "artifact", filter={"field": "projects", "op": "linked_to", "value": p1.key}
    )
    assert {r["key"] for r in linked_to_p1.records} == {a1.key}
    assert linked_to_p1.total_count == 1

    linked_to_any = services.records.query_records(
        actor,
        "artifact",
        filter={"field": "projects", "op": "linked_to_any", "value": [p1.key, p2.key]},
    )
    assert {r["key"] for r in linked_to_any.records} == {a1.key, a2.key}
    assert linked_to_any.total_count == 2

    has_links = services.records.query_records(
        actor, "artifact", filter={"field": "projects", "op": "has_links"}
    )
    assert {r["key"] for r in has_links.records} == {a1.key, a2.key}
    assert has_links.total_count == 2

    has_no_links = services.records.query_records(
        actor, "artifact", filter={"field": "projects", "op": "has_no_links"}
    )
    assert {r["key"] for r in has_no_links.records} == {a3.key}
    assert has_no_links.total_count == 1
