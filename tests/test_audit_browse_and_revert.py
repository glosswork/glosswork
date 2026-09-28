"""Service-layer acceptance tests: audit browsing (``AuditRepository.search`` /
``AuditService``) and revert (``RecordService.revert_field_change`` /
``revert_to_version``), per FR-U8, FR-D6, and DD-21.

HTTP-layer wiring (routes, error-envelope shape parity) is covered separately in
tests/test_api_audit.py; these tests prove the underlying service and repository
behavior.
"""

from __future__ import annotations

import pytest

from glosswork.errors import (
    NotFoundError,
    UnknownObjectTypeError,
    ValidationFailedError,
    VersionConflictError,
)
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.services import ServiceBundle
from tests.conftest import make_actor

SinkType = tuple[ObjectType, dict[str, FieldDef]]


# ---------------------------------------------------------------------------
# AuditRepository.search / AuditService.search (FR-U8, DD-21)
# ---------------------------------------------------------------------------


class TestAuditSearch:
    def test_filters_by_record(self, services: ServiceBundle, sink_type: SinkType) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        other = services.records.create_record(make_actor(), "artifact", {"title": "b"})
        services.records.update_record(make_actor(), record.key, {"points": 1})
        services.records.update_record(make_actor(), other.key, {"points": 2})

        result = services.audit.search(make_actor(), record=record.key)
        assert result.events
        assert all(e.record_id == record.id for e in result.events)

    def test_filters_by_record_accepts_uuid_too(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        result = services.audit.search(make_actor(), record=record.id)
        assert result.events
        assert all(e.record_id == record.id for e in result.events)

    def test_filters_by_field_key_across_records(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        a = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        b = services.records.create_record(make_actor(), "artifact", {"title": "b"})
        services.records.update_record(make_actor(), a.key, {"points": 1})
        services.records.update_record(make_actor(), b.key, {"points": 2})

        result = services.audit.search(make_actor(), field_key="points")
        record_events = [e for e in result.events if e.entity_type == "record"]
        assert {e.record_id for e in record_events} == {a.id, b.id}
        assert all(e.field_key == "points" for e in result.events)

    def test_filters_by_principal(self, services: ServiceBundle, sink_type: SinkType) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        result = services.audit.search(make_actor(), principal_id=record.created_by)
        assert result.events
        assert all(e.principal_id == record.created_by for e in result.events)

    def test_filters_by_object_type(self, services: ServiceBundle, sink_type: SinkType) -> None:
        services.records.create_record(make_actor(), "artifact", {"title": "a"})
        result = services.audit.search(make_actor(), object_type="artifact")
        assert result.events
        object_type, _ = sink_type
        assert all(e.object_type_id == object_type.id for e in result.events)

    def test_filters_by_time_range(self, services: ServiceBundle, sink_type: SinkType) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        all_events = services.audit.search(make_actor(), record=record.key).events
        ts = all_events[0].ts
        assert services.audit.search(make_actor(), record=record.key, since=ts).events
        assert services.audit.search(make_actor(), record=record.key, until=ts).events
        far_future = "2999-01-01T00:00:00Z"
        assert services.audit.search(make_actor(), record=record.key, since=far_future).events == []

    def test_unknown_object_type_names_valid_keys(self, services: ServiceBundle) -> None:
        with pytest.raises(UnknownObjectTypeError):
            services.audit.search(make_actor(), object_type="does-not-exist")

    def test_unknown_record_is_not_found(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        with pytest.raises(NotFoundError):
            services.audit.search(make_actor(), record="ART-999")

    def test_pagination_is_keyset_newest_first_and_covers_every_row_once(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        for i in range(6):
            services.records.update_record(make_actor(), record.key, {"points": i})

        full = services.audit.search(make_actor(), record=record.key, limit=100).events
        assert len(full) >= 7  # 1 create row + 6 point updates
        ids_desc = [e.id for e in full]
        assert ids_desc == sorted(ids_desc, reverse=True)

        collected: list[int] = []
        cursor: str | None = None
        for _ in range(20):
            page = services.audit.search(make_actor(), record=record.key, limit=2, cursor=cursor)
            collected.extend(e.id for e in page.events)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        assert collected == ids_desc


# ---------------------------------------------------------------------------
# RecordService.revert_field_change (FR-D6, DD-21)
# ---------------------------------------------------------------------------


class TestRevertFieldChange:
    def test_reverts_one_field_as_a_new_forward_audited_write(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "a", "points": 1}
        )
        updated = services.records.update_record(make_actor(), record.key, {"points": 2})
        assert updated.version == 2

        history = services.records.get_record_history(make_actor(), record.key, field_key="points")
        change_event = next(e for e in history if e.old_value == 1 and e.new_value == 2)

        reverted = services.records.revert_field_change(
            make_actor(), change_event.id, expected_version=2
        )
        assert reverted.version == 3
        assert reverted.data["points"] == 1

        history_after = services.records.get_record_history(
            make_actor(), record.key, field_key="points"
        )
        revert_event = history_after[-1]
        assert revert_event.old_value == 2
        assert revert_event.new_value == 1
        assert revert_event.action == "update"
        assert revert_event.note == f"revert of event {change_event.id}"
        # History is forward-only: the original event is untouched, not deleted.
        assert any(e.id == change_event.id for e in history_after)

    def test_reverting_clears_a_field_that_did_not_exist_before(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        updated = services.records.update_record(make_actor(), record.key, {"points": 1})
        assert updated.version == 2
        history = services.records.get_record_history(make_actor(), record.key, field_key="points")
        change_event = next(e for e in history if e.old_value is None and e.new_value == 1)

        reverted = services.records.revert_field_change(
            make_actor(), change_event.id, expected_version=2
        )
        assert "points" not in reverted.data

    def test_stale_expected_version_raises_the_same_conflict_shape_as_update_record(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "a", "points": 1}
        )
        services.records.update_record(make_actor(), record.key, {"points": 2})
        change_event = next(
            e
            for e in services.records.get_record_history(
                make_actor(), record.key, field_key="points"
            )
            if e.action == "update"
        )
        services.records.update_record(make_actor(), record.key, {"points": 3})

        with pytest.raises(VersionConflictError) as excinfo:
            services.records.revert_field_change(make_actor(), change_event.id, expected_version=2)
        err = excinfo.value
        assert err.current_version == 3
        assert "points" in err.changed_since_your_version
        # Nothing applied.
        assert services.records.get_record(make_actor(), record.key).data["points"] == 3

    def test_force_bypasses_the_version_check(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "a", "points": 1}
        )
        services.records.update_record(make_actor(), record.key, {"points": 2})
        change_event = next(
            e
            for e in services.records.get_record_history(
                make_actor(), record.key, field_key="points"
            )
            if e.action == "update"
        )
        services.records.update_record(make_actor(), record.key, {"points": 3})

        reverted = services.records.revert_field_change(
            make_actor(), change_event.id, expected_version=2, force=True
        )
        assert reverted.data["points"] == 1

    def test_unknown_event_id_is_not_found(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        with pytest.raises(NotFoundError):
            services.records.revert_field_change(make_actor(), 999_999, expected_version=1)

    def test_rejects_the_records_own_create_row(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        create_row = next(
            e
            for e in services.records.get_record_history(make_actor(), record.key)
            if e.action == "create" and e.field_key is None
        )
        with pytest.raises(ValidationFailedError):
            services.records.revert_field_change(make_actor(), create_row.id, expected_version=1)

    def test_rejects_a_link_event(self, services: ServiceBundle, sink_type: SinkType) -> None:
        parent = services.records.create_record(make_actor(), "artifact", {"title": "parent"})
        child = services.records.create_record(make_actor(), "artifact", {"title": "child"})
        services.records.link_records(make_actor(), child.key, "parent", [parent.key])

        link_event = next(
            e
            for e in services.records.get_record_history(make_actor(), child.key)
            if e.entity_type == "link"
        )
        with pytest.raises(ValidationFailedError):
            services.records.revert_field_change(make_actor(), link_event.id, expected_version=1)

    def test_rejects_a_comment_event(self, services: ServiceBundle, sink_type: SinkType) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        comment = services.comments.add_comment(make_actor(), record.key, "hello")
        services.comments.update_comment(make_actor(), comment.id, "hello, edited")

        comment_event = next(
            e
            for e in services.records.get_record_history(make_actor(), record.key)
            if e.entity_type == "comment"
        )
        with pytest.raises(ValidationFailedError):
            services.records.revert_field_change(make_actor(), comment_event.id, expected_version=1)


# ---------------------------------------------------------------------------
# RecordService.revert_to_version (FR-D6, DD-21)
# ---------------------------------------------------------------------------


class TestRevertToVersion:
    def test_reverts_through_three_updates_spanning_two_versions(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        assert record.version == 1
        services.records.update_record(make_actor(), record.key, {"points": 1})  # -> v2
        services.records.update_record(
            make_actor(), record.key, {"points": 2, "score": 5.0}
        )  # -> v3
        latest = services.records.update_record(make_actor(), record.key, {"points": 3})  # -> v4
        assert latest.version == 4

        reverted = services.records.revert_to_version(
            make_actor(), record.key, target_version=2, expected_version=4
        )
        assert reverted.version == 5
        assert reverted.data["points"] == 1
        # 'score' did not exist yet at version 2, so reverting to it clears it.
        assert "score" not in reverted.data
        assert reverted.data["title"] == "a"

        revert_events = [
            e
            for e in services.records.get_record_history(make_actor(), record.key)
            if e.note == "revert to version 2"
        ]
        assert {e.field_key for e in revert_events} == {"points", "score"}

    def test_reverting_to_version_one_restores_creation_state(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        services.records.update_record(make_actor(), record.key, {"points": 1})
        latest = services.records.update_record(make_actor(), record.key, {"points": 2})

        reverted = services.records.revert_to_version(
            make_actor(), record.key, target_version=1, expected_version=latest.version
        )
        assert "points" not in reverted.data
        assert reverted.data["title"] == "a"

    def test_target_version_out_of_range_is_validation_failed(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        with pytest.raises(ValidationFailedError):
            services.records.revert_to_version(
                make_actor(), record.key, target_version=1, expected_version=1
            )  # version 1 is the current version, not a prior one
        with pytest.raises(ValidationFailedError):
            services.records.revert_to_version(
                make_actor(), record.key, target_version=0, expected_version=1
            )

    def test_stale_expected_version_raises_the_same_conflict_shape_as_update_record(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        services.records.update_record(make_actor(), record.key, {"points": 1})
        services.records.update_record(make_actor(), record.key, {"points": 2})

        with pytest.raises(VersionConflictError) as excinfo:
            services.records.revert_to_version(
                make_actor(), record.key, target_version=1, expected_version=2
            )
        err = excinfo.value
        assert err.current_version == 3
        assert "points" in err.changed_since_your_version
