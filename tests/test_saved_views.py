"""Saved view acceptance tests (FR-U3).

Saved views persist object type, filter, sort, grouping, visible columns, and
display mode as one ``saved_views`` row (docs/DATA_MODEL.md section 11); they are
shared across all principals (no per-user private views at MVP), and at most one
per object type may be marked default, enforced by the DB's partial unique index
on ``saved_views(object_type_id) WHERE is_default = 1``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.errors import NotFoundError, UnknownObjectTypeError, ValidationFailedError
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.repositories.sqlite import SqliteAuditRepository
from glosswork.services import ServiceBundle
from tests.conftest import make_actor

SinkType = tuple[ObjectType, dict[str, FieldDef]]

FULL_CONFIG = {
    "filter": {"field": "status", "op": "eq", "value": "doing"},
    "sort": [{"field": "created_at", "dir": "desc"}],
    "group_by": "status",
    "columns": ["title", "status", "owner"],
    "widths": {"title": 240, "status": 120},
}


@pytest.fixture
def second_type(services: ServiceBundle, actor: ActorContext) -> ObjectType:
    return services.schema.create_object_type(
        actor,
        key="widget",
        name="Widget",
        name_plural="Widgets",
        description="A second object type used to prove saved views don't cross-contaminate.",
        key_prefix="WID",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "Short human-readable name for the widget.",
                "required": True,
            }
        ],
    )


def test_create_saved_view_persists_full_config_and_mode(
    services: ServiceBundle, actor: ActorContext, sink_type: SinkType
) -> None:
    view = services.saved_views.create_saved_view(
        actor,
        "artifact",
        "Doing, by status",
        FULL_CONFIG,
        mode="card",
        description="Everything currently in progress, grouped by status.",
    )

    assert view.name == "Doing, by status"
    assert view.mode == "card"
    assert view.config == FULL_CONFIG
    assert view.is_default is False
    assert view.created_by == actor.principal_id
    assert view.updated_by == actor.principal_id

    [reloaded] = services.saved_views.list_saved_views(actor, "artifact")
    assert reloaded.id == view.id
    assert reloaded.config == FULL_CONFIG
    assert reloaded.mode == "card"


def test_create_saved_view_on_unknown_object_type_raises(
    services: ServiceBundle, actor: ActorContext
) -> None:
    with pytest.raises(UnknownObjectTypeError):
        services.saved_views.create_saved_view(actor, "does-not-exist", "A view", {})


def test_create_saved_view_rejects_empty_name(
    services: ServiceBundle, actor: ActorContext, sink_type: SinkType
) -> None:
    with pytest.raises(ValidationFailedError):
        services.saved_views.create_saved_view(actor, "artifact", "   ", {})


def test_create_saved_view_rejects_bad_mode(
    services: ServiceBundle, actor: ActorContext, sink_type: SinkType
) -> None:
    with pytest.raises(ValidationFailedError):
        services.saved_views.create_saved_view(actor, "artifact", "Bad mode", {}, mode="grid")


def test_list_saved_views_scoped_to_one_object_type(
    services: ServiceBundle, actor: ActorContext, sink_type: SinkType, second_type: ObjectType
) -> None:
    artifact_view = services.saved_views.create_saved_view(actor, "artifact", "Artifact view", {})
    widget_view = services.saved_views.create_saved_view(actor, "widget", "Widget view", {})

    artifact_views = services.saved_views.list_saved_views(actor, "artifact")
    widget_views = services.saved_views.list_saved_views(actor, "widget")

    assert [v.id for v in artifact_views] == [artifact_view.id]
    assert [v.id for v in widget_views] == [widget_view.id]


def test_setting_a_new_default_atomically_unsets_the_old_one(
    services: ServiceBundle, actor: ActorContext, db: Database, sink_type: SinkType
) -> None:
    first = services.saved_views.create_saved_view(actor, "artifact", "First", {}, is_default=True)
    assert first.is_default is True

    second = services.saved_views.create_saved_view(
        actor, "artifact", "Second", {}, is_default=True
    )
    assert second.is_default is True

    refreshed_first = services.saved_views.get_saved_view(actor, first.id)
    assert refreshed_first.is_default is False

    # The DB-level partial unique index never saw two default rows at once: a
    # naive two-statement, non-transactional flip would raise IntegrityError here
    # instead of getting this far, and at most one row is default at rest.
    with db.read() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) FROM saved_views WHERE object_type_id = :tid AND is_default = 1"),
            {"tid": first.object_type_id},
        ).first()
    assert row is not None
    assert row[0] == 1

    # Flipping default via update_saved_view is equally atomic.
    third = services.saved_views.create_saved_view(actor, "artifact", "Third", {})
    updated_third = services.saved_views.update_saved_view(actor, third.id, is_default=True)
    assert updated_third.is_default is True
    assert services.saved_views.get_saved_view(actor, second.id).is_default is False


def test_update_saved_view_renames_and_changes_config_and_mode(
    services: ServiceBundle, actor: ActorContext, sink_type: SinkType
) -> None:
    view = services.saved_views.create_saved_view(actor, "artifact", "Original", {}, mode="table")

    updated = services.saved_views.update_saved_view(
        actor,
        view.id,
        name="Renamed",
        config=FULL_CONFIG,
        mode="card",
    )

    assert updated.name == "Renamed"
    assert updated.config == FULL_CONFIG
    assert updated.mode == "card"
    assert updated.updated_by == actor.principal_id


def test_update_unknown_saved_view_raises_not_found(
    services: ServiceBundle, actor: ActorContext
) -> None:
    with pytest.raises(NotFoundError):
        services.saved_views.update_saved_view(actor, "does-not-exist", name="X")


def test_delete_saved_view_removes_it_from_the_list(
    services: ServiceBundle, actor: ActorContext, sink_type: SinkType
) -> None:
    keep = services.saved_views.create_saved_view(actor, "artifact", "Keep", {})
    doomed = services.saved_views.create_saved_view(actor, "artifact", "Doomed", {})

    deleted = services.saved_views.delete_saved_view(actor, doomed.id)
    assert deleted.id == doomed.id

    remaining = services.saved_views.list_saved_views(actor, "artifact")
    assert [v.id for v in remaining] == [keep.id]

    with pytest.raises(NotFoundError):
        services.saved_views.get_saved_view(actor, doomed.id)


def test_delete_unknown_saved_view_raises_not_found(
    services: ServiceBundle, actor: ActorContext
) -> None:
    with pytest.raises(NotFoundError):
        services.saved_views.delete_saved_view(actor, "does-not-exist")


def test_create_update_delete_are_all_audited_with_full_attribution(
    services: ServiceBundle, db: Database, sink_type: SinkType
) -> None:
    creator = make_actor()
    view = services.saved_views.create_saved_view(creator, "artifact", "Audited view", {})

    editor = make_actor()
    services.saved_views.update_saved_view(editor, view.id, name="Audited view (renamed)")

    deleter = make_actor()
    services.saved_views.delete_saved_view(deleter, view.id)

    with db.read() as conn:
        events = [
            e
            for e in SqliteAuditRepository().for_request(conn, creator.request_id)
            + SqliteAuditRepository().for_request(conn, editor.request_id)
            + SqliteAuditRepository().for_request(conn, deleter.request_id)
            if e.entity_type == "saved_view" and e.entity_id == view.id
        ]

    actions = {e.action: e for e in events}
    assert set(actions) == {"create", "update", "delete"}

    create_event = actions["create"]
    assert create_event.principal_id == creator.principal_id
    assert create_event.request_id == creator.request_id
    assert create_event.new_value["name"] == "Audited view"

    update_event = actions["update"]
    assert update_event.principal_id == editor.principal_id
    assert update_event.old_value["name"] == "Audited view"
    assert update_event.new_value["name"] == "Audited view (renamed)"

    delete_event = actions["delete"]
    assert delete_event.principal_id == deleter.principal_id
    assert delete_event.old_value["name"] == "Audited view (renamed)"
