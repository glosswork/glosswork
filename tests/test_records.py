"""Record store acceptance tests: keys, versions, soft delete, validation
(FR-R1 through FR-R4, FR-R11)."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from glosswork.db import Database
from glosswork.errors import (
    NotFoundError,
    UnknownFieldError,
    ValidationFailedError,
    VersionConflictError,
)
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.services import ServiceBundle
from glosswork.services.records import MAX_HISTORY_LIMIT, MAX_QUERY_LIMIT
from glosswork.sqlexpr import index_name
from tests.conftest import make_actor

SinkType = tuple[ObjectType, dict[str, FieldDef]]


# ---------------------------------------------------------------------------
# Key allocation (FR-R2)
# ---------------------------------------------------------------------------


class TestKeyAllocation:
    def test_keys_are_zero_padded_sequential_and_unique(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        keys = [
            services.records.create_record(make_actor(), "artifact", {"title": f"r{i}"}).key
            for i in range(4)
        ]
        assert keys == ["ART-001", "ART-002", "ART-003", "ART-004"]

    def test_keys_never_reused_after_deletion(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        first = services.records.create_record(make_actor(), "artifact", {"title": "a"})
        services.records.create_record(make_actor(), "artifact", {"title": "b"})
        services.records.delete_record(make_actor(), first.key)
        third = services.records.create_record(make_actor(), "artifact", {"title": "c"})
        assert third.key == "ART-003"  # counter never decremented; ART-001 never reused

    def test_padding_grows_past_three_digits(
        self, db: Database, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        object_type, _ = sink_type
        with db.write() as conn:
            conn.execute(
                text("UPDATE object_types SET key_counter = 999 WHERE id = :id"),
                {"id": object_type.id},
            )
        record = services.records.create_record(make_actor(), "artifact", {"title": "big"})
        assert record.key == "ART-1000"

    def test_concurrent_creates_receive_distinct_keys(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        def create(i: int) -> str:
            return services.records.create_record(make_actor(), "artifact", {"title": f"c{i}"}).key

        with ThreadPoolExecutor(max_workers=8) as pool:
            keys = list(pool.map(create, range(24)))
        assert len(set(keys)) == 24
        assert sorted(keys) == [f"ART-{i:03d}" for i in range(1, 25)]


# ---------------------------------------------------------------------------
# Versioning (FR-R3, FR-R4)
# ---------------------------------------------------------------------------


class TestVersioning:
    def test_version_increments_on_every_successful_update(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "v"})
        assert record.version == 1
        updated = services.records.update_record(make_actor(), record.key, {"points": 1})
        assert updated.version == 2
        updated = services.records.update_record(make_actor(), record.key, {"points": 2})
        assert updated.version == 3

    def test_version_conflict_carries_full_payload(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "conflict", "status": "todo", "points": 1}
        )
        # Two updates happen after the caller read version 1.
        services.records.update_record(make_actor(), record.key, {"status": "doing"})
        services.records.update_record(make_actor(), record.key, {"points": 2})

        with pytest.raises(VersionConflictError) as excinfo:
            services.records.update_record(
                make_actor(),
                record.key,
                {"status": "done", "title": "renamed"},
                expected_version=1,
            )
        err = excinfo.value
        assert err.current_version == 3
        # Fields changed since the caller's version, whether or not the caller wrote them.
        assert set(err.changed_since_your_version) == {"status", "points"}
        # Conflicting fields: the intersection of the caller's patch with those changes,
        # carrying both sides so the caller can merge.
        assert err.conflicting_fields == {
            "status": {"your_value": "done", "current_value": "doing"}
        }
        assert err.details["current_version"] == 3
        # Nothing was applied.
        current = services.records.get_record(make_actor(), record.key)
        assert current.version == 3
        assert current.data["title"] == "conflict"

    def test_matching_expected_version_applies(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "m"})
        updated = services.records.update_record(
            make_actor(), record.key, {"points": 5}, expected_version=1
        )
        assert updated.version == 2

    def test_force_bypasses_the_version_check(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "f", "status": "todo"}
        )
        services.records.update_record(make_actor(), record.key, {"status": "doing"})
        forced = services.records.update_record(
            make_actor(), record.key, {"status": "done"}, expected_version=1, force=True
        )
        assert forced.version == 3
        assert forced.data["status"] == "done"


# ---------------------------------------------------------------------------
# Soft delete and restore (FR-R1, FR-R11)
# ---------------------------------------------------------------------------


class TestSoftDelete:
    def test_delete_excludes_by_default_and_include_deleted_returns(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "gone"})
        services.records.create_record(make_actor(), "artifact", {"title": "stays"})
        deleted = services.records.delete_record(make_actor(), record.key)
        assert deleted.deleted_at is not None

        with pytest.raises(NotFoundError):
            services.records.get_record(make_actor(), record.key)
        assert (
            services.records.get_record(make_actor(), record.key, include_deleted=True).key
            == record.key
        )

        default_result = services.records.query_records(make_actor(), "artifact")
        assert [r["key"] for r in default_result.records] == ["ART-002"]
        assert default_result.total_count == 1

        with_deleted = services.records.query_records(
            make_actor(), "artifact", include_deleted=True
        )
        assert with_deleted.total_count == 2

    def test_restore(self, services: ServiceBundle, sink_type: SinkType) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "back"})
        services.records.delete_record(make_actor(), record.key)
        restored = services.records.restore_record(make_actor(), record.key)
        assert restored.deleted_at is None
        assert services.records.get_record(make_actor(), record.key).key == record.key
        with pytest.raises(ValidationFailedError, match="not deleted"):
            services.records.restore_record(make_actor(), record.key)


# ---------------------------------------------------------------------------
# Write validation: required, unique, default, key omission
# ---------------------------------------------------------------------------


class TestWriteValidation:
    def test_required_enforced_on_create_and_clear(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        with pytest.raises(ValidationFailedError, match="[Rr]equired"):
            services.records.create_record(make_actor(), "artifact", {"points": 1})
        record = services.records.create_record(make_actor(), "artifact", {"title": "req"})
        with pytest.raises(ValidationFailedError, match="required"):
            services.records.update_record(make_actor(), record.key, {"title": None})

    def test_unique_enforced_via_unique_partial_expression_index(
        self, db: Database, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        object_type, _ = sink_type
        services.schema.add_field(
            make_actor(),
            "artifact",
            {
                "key": "slug",
                "name": "Slug",
                "type": "short_text",
                "description": "Stable unique identifier used in URLs.",
                "unique": True,
            },
        )
        # The enforcement mechanism is a unique partial expression index.
        with db.read() as conn:
            row = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name = :n"),
                {"n": index_name(object_type.id, "slug", unique=True)},
            ).first()
        assert row is not None
        ddl = row[0]
        assert "CREATE UNIQUE INDEX" in ddl
        assert "json_extract(data, '$.slug')" in ddl
        assert "deleted_at IS NULL" in ddl

        services.records.create_record(make_actor(), "artifact", {"title": "one", "slug": "same"})
        with pytest.raises(ValidationFailedError, match="slug"):
            services.records.create_record(
                make_actor(), "artifact", {"title": "two", "slug": "same"}
            )
        # The index itself rejects a raw write that bypasses service validation.
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "three", "slug": "other"}
        )
        with pytest.raises(Exception, match="(?i)unique"):
            with db.write() as conn:
                conn.execute(
                    text(
                        "UPDATE records SET data = json_set(data, '$.slug', 'same') WHERE id = :id"
                    ),
                    {"id": record.id},
                )
        # A soft-deleted record's value does not block reuse (partial index scope).
        services.records.delete_record(make_actor(), "ART-001")
        services.records.create_record(make_actor(), "artifact", {"title": "four", "slug": "same"})
        # And restoring the deleted record now collides.
        with pytest.raises(ValidationFailedError, match="slug"):
            services.records.restore_record(make_actor(), "ART-001")

    def test_default_applied_on_create(self, services: ServiceBundle, sink_type: SinkType) -> None:
        services.schema.update_field(make_actor(), "artifact", "status", {"default": "todo"})
        record = services.records.create_record(make_actor(), "artifact", {"title": "d"})
        assert record.data["status"] == "todo"
        explicit = services.records.create_record(
            make_actor(), "artifact", {"title": "e", "status": "done"}
        )
        assert explicit.data["status"] == "done"

    def test_absent_values_stored_by_key_omission_never_null(
        self, db: Database, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "sparse", "points": 3}
        )
        with db.read() as conn:
            raw = conn.execute(
                text("SELECT data FROM records WHERE id = :id"), {"id": record.id}
            ).scalar()
        assert raw is not None
        assert "null" not in raw
        assert set(record.data) == {"title", "points"}
        # Clearing a value removes the key entirely rather than writing null.
        cleared = services.records.update_record(make_actor(), record.key, {"points": None})
        assert "points" not in cleared.data
        with db.read() as conn:
            raw = conn.execute(
                text("SELECT data FROM records WHERE id = :id"), {"id": record.id}
            ).scalar()
        assert raw is not None
        assert "points" not in raw and "null" not in raw

    def test_unknown_field_on_write_names_valid_keys_and_near_misses(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        with pytest.raises(UnknownFieldError) as excinfo:
            services.records.create_record(
                make_actor(), "artifact", {"title": "x", "titel": "typo"}
            )
        err = excinfo.value
        assert "title" in err.details["valid_keys"]
        assert "title" in err.details["near_misses"]
        assert "Did you mean 'title'?" in err.message

    def test_unknown_field_on_filter_names_valid_keys_and_near_misses(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        with pytest.raises(UnknownFieldError) as excinfo:
            services.records.query_records(
                make_actor(),
                "artifact",
                filter={"field": "statuss", "op": "eq", "value": "todo"},
            )
        assert "statuss" == excinfo.value.details["field_key"]
        assert "status" in excinfo.value.details["near_misses"]

    def test_record_addressable_by_key_and_uuid(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "ref"})
        assert services.records.get_record(make_actor(), record.id).key == record.key
        assert services.records.get_record(make_actor(), record.key).id == record.id

    def test_noop_update_does_not_bump_version(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(
            make_actor(), "artifact", {"title": "same", "points": 1}
        )
        unchanged = services.records.update_record(make_actor(), record.key, {"points": 1})
        assert unchanged.version == 1


# ---------------------------------------------------------------------------
# Page sizes are capped (DD-18)
#
# Watched to fail against a tree whose limits checked only ``limit < 1``:
# ``limit=100000000`` answered 200 on query, on history and on comments, while the same
# value was already correctly refused on audit.
# ---------------------------------------------------------------------------


class TestPageLimits:
    def test_query_accepts_the_cap_and_refuses_one_past_it(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        assert (
            services.records.query_records(
                make_actor(), "artifact", limit=MAX_QUERY_LIMIT
            ).total_count
            == 0
        )
        with pytest.raises(ValidationFailedError) as exc:
            services.records.query_records(make_actor(), "artifact", limit=MAX_QUERY_LIMIT + 1)
        assert exc.value.details["max_limit"] == MAX_QUERY_LIMIT

    def test_history_accepts_the_cap_and_refuses_one_past_it(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "One"})
        services.records.get_record_history_page(make_actor(), record.key, limit=MAX_HISTORY_LIMIT)
        with pytest.raises(ValidationFailedError) as exc:
            services.records.get_record_history_page(
                make_actor(), record.key, limit=MAX_HISTORY_LIMIT + 1
            )
        assert exc.value.details["max_limit"] == MAX_HISTORY_LIMIT

    def test_the_review_probe_is_refused_on_every_capped_read(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        """A security probe's value, on the three paths that once answered 200 for it."""
        record = services.records.create_record(make_actor(), "artifact", {"title": "One"})
        for call in (
            lambda: services.records.query_records(make_actor(), "artifact", limit=100_000_000),
            lambda: services.records.get_record_history_page(
                make_actor(), record.key, limit=100_000_000
            ),
            lambda: services.comments.list_comments_page(
                make_actor(), record.key, limit=100_000_000
            ),
        ):
            with pytest.raises(ValidationFailedError):
                call()

    def test_the_query_cap_clears_what_the_ui_asks_for(self) -> None:
        """A fence, not a measurement of the cap: the table view pages at 200
        (``web/src/table-view/constants.ts``), so any query cap below that would break
        a screen. Read from the frontend source so it cannot drift silently."""
        constants = (
            Path(__file__).resolve().parents[1] / "web" / "src" / "table-view" / "constants.ts"
        ).read_text()
        page_size = int(
            re.search(r"TABLE_VIEW_PAGE_SIZE\s*=\s*(\d+)", constants).group(1)  # type: ignore[union-attr]
        )
        assert page_size <= MAX_QUERY_LIMIT

    def test_a_limit_over_the_cap_is_refused_over_rest_too(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        app_services.schema.create_object_type(
            make_actor(),
            key="widget",
            name="Widget",
            name_plural="Widgets",
            description="A type used to prove the REST surface inherits the service cap.",
            key_prefix="WDG",
            fields=[
                {
                    "key": "title",
                    "name": "Title",
                    "type": "short_text",
                    "description": "Short human-readable name for the widget.",
                }
            ],
        )
        response = client.post(
            "/api/v1/object-types/widget/query", json={"limit": MAX_QUERY_LIMIT + 1}
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert error["details"]["max_limit"] == MAX_QUERY_LIMIT
