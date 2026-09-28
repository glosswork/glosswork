"""bulk_update (FR-R10): one value patch applied to every filter match, and a
dry-run reports the count and up to twenty sample keys without writing."""

from __future__ import annotations

from glosswork.errors import UnknownFieldError, ValidationFailedError
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.services import ServiceBundle
from tests.conftest import make_actor

SinkType = tuple[ObjectType, dict[str, FieldDef]]


class TestBulkUpdate:
    def test_dry_run_reports_count_and_samples_without_writing(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        for i in range(25):
            services.records.create_record(
                make_actor(), "artifact", {"title": f"t{i}", "status": "todo"}
            )
        result = services.records.bulk_update(
            make_actor(),
            "artifact",
            values={"status": "doing"},
            filter={"field": "status", "op": "eq", "value": "todo"},
            dry_run=True,
        )
        assert result.dry_run is True
        assert result.affected_count == 25
        assert len(result.sample_keys) == 20
        assert len(set(result.sample_keys)) == 20
        # Nothing was written.
        untouched = services.records.get_record(make_actor(), "ART-001")
        assert untouched.data["status"] == "todo"
        assert untouched.version == 1

    def test_live_run_applies_patch_to_every_match_and_returns_affected_count(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        matching = [
            services.records.create_record(
                make_actor(), "artifact", {"title": f"m{i}", "status": "todo"}
            )
            for i in range(3)
        ]
        other = services.records.create_record(
            make_actor(), "artifact", {"title": "other", "status": "done"}
        )
        result = services.records.bulk_update(
            make_actor(),
            "artifact",
            values={"status": "doing"},
            filter={"field": "status", "op": "eq", "value": "todo"},
        )
        assert result.dry_run is False
        assert result.affected_count == 3
        assert set(result.sample_keys) == {r.key for r in matching}
        for record in matching:
            refreshed = services.records.get_record(make_actor(), record.key)
            assert refreshed.data["status"] == "doing"
            assert refreshed.version == 2
        untouched = services.records.get_record(make_actor(), other.key)
        assert untouched.data["status"] == "done"
        assert untouched.version == 1

    def test_no_op_matches_are_not_counted_as_affected(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "same", "status": "todo"}
        )
        result = services.records.bulk_update(make_actor(), "artifact", values={"status": "todo"})
        assert result.affected_count == 0
        assert services.records.get_record(make_actor(), record.key).version == 1

    def test_empty_filter_matches_every_live_record(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        for i in range(4):
            services.records.create_record(make_actor(), "artifact", {"title": f"e{i}"})
        deleted = services.records.create_record(make_actor(), "artifact", {"title": "gone"})
        services.records.delete_record(make_actor(), deleted.key)

        result = services.records.bulk_update(
            make_actor(), "artifact", values={"points": 1}, dry_run=True
        )
        assert result.affected_count == 4  # soft-deleted records are excluded

    def test_clearing_a_required_field_is_rejected(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        services.records.create_record(make_actor(), "artifact", {"title": "x"})
        try:
            services.records.bulk_update(make_actor(), "artifact", values={"title": None})
        except ValidationFailedError as exc:
            assert "required" in exc.message
        else:
            raise AssertionError("expected ValidationFailedError")

    def test_unknown_field_in_patch_is_rejected(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        services.records.create_record(make_actor(), "artifact", {"title": "x"})
        try:
            services.records.bulk_update(make_actor(), "artifact", values={"titel": "typo"})
        except UnknownFieldError:
            pass
        else:
            raise AssertionError("expected UnknownFieldError")

    def test_requires_at_least_one_value(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        try:
            services.records.bulk_update(make_actor(), "artifact", values={})
        except ValidationFailedError:
            pass
        else:
            raise AssertionError("expected ValidationFailedError")

    def test_bulk_update_writes_one_audit_row_per_changed_field_per_record(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "audited", "status": "todo", "points": 1}
        )
        services.records.bulk_update(
            make_actor(), "artifact", values={"status": "doing", "points": 2}
        )
        history = services.records.get_record_history(make_actor(), record.key)
        update_events = [e for e in history if e.action == "update"]
        assert {e.field_key for e in update_events} == {"status", "points"}
