"""Pull-based change feed (FR-M7): cursor correctness across pages with interleaved
writes, and omitting a cursor starts watching from now without a replay."""

from __future__ import annotations

from glosswork.errors import UnknownObjectTypeError
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.services import ServiceBundle
from tests.conftest import make_actor

SinkType = tuple[ObjectType, dict[str, FieldDef]]


class TestChangeFeed:
    def test_omitting_cursor_returns_current_cursor_with_no_events(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        services.records.create_record(make_actor(), "artifact", {"title": "before"})
        result = services.changes.list_changes_since(make_actor())
        assert result.events == []
        assert result.next_cursor > 0

        services.records.create_record(make_actor(), "artifact", {"title": "after"})
        resumed = services.changes.list_changes_since(make_actor(), cursor=result.next_cursor)
        assert len(resumed.events) > 0
        assert all(e.id > result.next_cursor for e in resumed.events)

    def test_cursor_from_page_one_is_resumable_after_further_writes(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        for i in range(5):
            services.records.create_record(make_actor(), "artifact", {"title": f"r{i}"})
        page_one = services.changes.list_changes_since(make_actor(), cursor=0, limit=3)
        assert len(page_one.events) == 3

        # More writes land between fetching page one and resuming from its cursor.
        services.records.create_record(make_actor(), "artifact", {"title": "interleaved"})

        page_two = services.changes.list_changes_since(make_actor(), cursor=page_one.next_cursor)
        assert all(e.id > page_one.next_cursor for e in page_two.events)
        assert page_two.events[0].id == page_one.next_cursor + 1

    def test_pagination_across_the_full_feed_yields_every_event_exactly_once(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        for i in range(6):
            services.records.create_record(make_actor(), "artifact", {"title": f"e{i}"})
        latest = services.changes.list_changes_since(make_actor()).next_cursor

        seen: list[int] = []
        cursor = 0
        while True:
            page = services.changes.list_changes_since(make_actor(), cursor=cursor, limit=2)
            if not page.events:
                break
            seen.extend(e.id for e in page.events)
            cursor = page.next_cursor
            if cursor >= latest:
                break
        assert seen == sorted(set(seen))  # no skips or duplicates
        assert len(seen) == len(set(seen))

    def test_scoped_to_object_type_keys(self, services: ServiceBundle, sink_type: SinkType) -> None:
        other_type = services.schema.create_object_type(
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
        services.records.create_record(make_actor(), "artifact", {"title": "a"})
        services.records.create_record(make_actor(), other_type.key, {"label": "w"})

        scoped = services.changes.list_changes_since(
            make_actor(), cursor=0, object_type_keys=["widget"]
        )
        assert scoped.events
        assert all(e.object_type_id == other_type.id for e in scoped.events)

    def test_unknown_object_type_key_is_rejected(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        try:
            services.changes.list_changes_since(make_actor(), cursor=0, object_type_keys=["nope"])
        except UnknownObjectTypeError:
            pass
        else:
            raise AssertionError("expected UnknownObjectTypeError")
