"""Compact default projection (FR-M8) and relation expansion (FR-L6).
docs/MCP_TOOLS.md section 4."""

from __future__ import annotations

from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.services import ServiceBundle
from tests.conftest import make_actor

SinkType = tuple[ObjectType, dict[str, FieldDef]]

# Auto-indexed by type (docs/DATA_MODEL.md section 5): due (date), seen_at
# (datetime), status (single_select), owner (user_ref); title is the display
# field (first non-relation field by position).
_COMPACT_KEYS = {"title", "due", "seen_at", "status", "owner"}


class TestCompactDefaultProjection:
    def test_omitted_fields_returns_key_display_field_and_indexed_fields_only(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        services.records.create_record(
            make_actor(),
            "artifact",
            {
                "title": "compact me",
                "summary": "a long narrative that should not appear by default",
                "points": 7,
                "status": "doing",
                "owner": make_actor().principal_id,
            },
        )
        result = services.records.query_records(make_actor(), "artifact")
        (record,) = result.records
        assert set(record["data"]) <= _COMPACT_KEYS
        assert record["data"]["title"] == "compact me"
        assert "summary" not in record["data"]
        assert "points" not in record["data"]
        # Pseudo-fields are always present at the envelope level regardless of projection.
        assert record["key"] == "ART-001"

    def test_star_returns_every_field(self, services: ServiceBundle, sink_type: SinkType) -> None:
        services.records.create_record(
            make_actor(), "artifact", {"title": "all of it", "summary": "full text", "points": 3}
        )
        result = services.records.query_records(make_actor(), "artifact", fields="*")
        (record,) = result.records
        assert record["data"] == {"title": "all of it", "summary": "full text", "points": 3}

    def test_explicit_fields_projects_exactly_those(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        services.records.create_record(
            make_actor(), "artifact", {"title": "explicit", "summary": "hidden", "points": 9}
        )
        result = services.records.query_records(
            make_actor(), "artifact", fields=["title", "points"]
        )
        (record,) = result.records
        assert record["data"] == {"title": "explicit", "points": 9}


class TestTruncatedFlag:
    def test_truncated_when_compact_projection_dropped_fields(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        services.records.create_record(
            make_actor(), "artifact", {"title": "has extra", "summary": "dropped by compaction"}
        )
        result = services.records.query_records(make_actor(), "artifact")
        assert result.truncated is True

    def test_not_truncated_when_compact_projection_drops_nothing(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        services.records.create_record(make_actor(), "artifact", {"title": "just the display"})
        result = services.records.query_records(make_actor(), "artifact")
        assert result.records[0]["data"] == {"title": "just the display"}
        assert result.truncated is False

    def test_truncated_when_a_next_page_exists_regardless_of_projection(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        for i in range(3):
            services.records.create_record(make_actor(), "artifact", {"title": f"r{i}"})
        result = services.records.query_records(make_actor(), "artifact", fields="*", limit=2)
        assert result.next_cursor is not None
        assert result.truncated is True
        last_page = services.records.query_records(
            make_actor(), "artifact", fields="*", limit=2, cursor=result.next_cursor
        )
        assert last_page.next_cursor is None
        assert last_page.truncated is False


class TestRelationExpansion:
    def test_query_records_expand_relations_resolves_key_display_and_subset(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        parent = services.records.create_record(
            make_actor(), "artifact", {"title": "Macro", "points": 100}
        )
        child = services.records.create_record(make_actor(), "artifact", {"title": "Micro"})
        services.records.link_records(make_actor(), child.key, "parent", [parent.key])

        result = services.records.query_records(
            make_actor(),
            "artifact",
            filter={"field": "key", "op": "eq", "value": child.key},
            fields="*",
            expand_relations=["parent"],
        )
        (record,) = result.records
        (linked,) = record["expand"]["parent"]
        assert linked["key"] == parent.key
        assert linked["id"] == parent.id
        assert linked["display"] == "Macro"
        assert "fields" not in linked

    def test_get_record_expansions_supports_field_subset(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        parent = services.records.create_record(
            make_actor(), "artifact", {"title": "Macro", "points": 42}
        )
        child = services.records.create_record(make_actor(), "artifact", {"title": "Micro"})
        services.records.link_records(make_actor(), child.key, "parent", [parent.key])

        expanded = services.records.get_record_expansions(
            make_actor(), child.key, ["parent"], expand_fields=["points"]
        )
        (linked,) = expanded["parent"]
        assert linked["display"] == "Macro"
        assert linked["fields"] == {"points": 42}

    def test_expand_relations_empty_when_no_links(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "lonely"})
        expanded = services.records.get_record_expansions(make_actor(), record.key, ["parent"])
        assert expanded == {"parent": []}
