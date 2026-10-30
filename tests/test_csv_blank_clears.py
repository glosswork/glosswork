"""A CSV upsert can clear a field, by an explicit opt-in (FR-E1, FR-E2).

``blank_clears=true`` makes an empty cell, in a column the file carries, clear that field
on a record the row updates. Without it a blank cell changes nothing, as it always has.
The result reports the clears, and a dry run's report equals the commit's.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pytest
from fastapi.testclient import TestClient

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.repositories.models import ObjectType
from glosswork.services import ServiceBundle
from tests.conftest import make_actor, select_options

_GADGET_FIELDS: list[dict[str, Any]] = [
    {
        "key": "title",
        "name": "Title",
        "type": "short_text",
        "description": "Short human-readable name for the gadget.",
        "required": True,
    },
    {
        "key": "code",
        "name": "Code",
        "type": "short_text",
        "description": "External unique identifier for the gadget.",
        "unique": True,
    },
    {
        "key": "serial",
        "name": "Serial",
        "type": "short_text",
        "description": "Manufacturer serial number, unique where present.",
        "unique": True,
    },
    {
        "key": "notes",
        "name": "Notes",
        "type": "long_text",
        "description": "Free-form narrative about the gadget.",
    },
    {
        "key": "points",
        "name": "Points",
        "type": "integer",
        "description": "Effort estimate in points.",
        "default": 3,
    },
    {
        "key": "active",
        "name": "Active",
        "type": "boolean",
        "description": "Whether the gadget is currently active.",
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
        "description": "Principal accountable for this gadget.",
    },
    {
        "key": "related",
        "name": "Related",
        "type": "relation",
        "description": "Other gadgets this one relates to; self-referential.",
        "config": {
            "target_type_key": "gadget",
            "cardinality": "many",
            "inverse_field_key": "related_by",
        },
    },
]


@pytest.fixture
def gadget_type(app_services: ServiceBundle) -> ObjectType:
    return app_services.schema.create_object_type(
        make_actor(),
        key="gadget",
        name="Gadget",
        name_plural="Gadgets",
        description="A test object type for clearing fields by CSV import.",
        key_prefix="GDG",
        fields=_GADGET_FIELDS,
    )


def _csv_bytes(header: list[str], rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=header)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def _post(
    client: TestClient,
    body: bytes,
    *,
    mode: str = "upsert",
    upsert_key: str | None = "key",
    dry_run: bool = False,
    blank_clears: bool | None = True,
) -> Any:
    data = {"mode": mode, "dry_run": "true" if dry_run else "false"}
    if upsert_key is not None:
        data["upsert_key"] = upsert_key
    if blank_clears is not None:
        data["blank_clears"] = "true" if blank_clears else "false"
    return client.post(
        "/api/v1/object-types/gadget/import",
        files={"file": ("data.csv", body, "text/csv")},
        data=data,
    )


def _import(
    client: TestClient,
    header: list[str],
    rows: list[dict[str, str]],
    **options: Any,
) -> dict[str, Any]:
    response = _post(client, _csv_bytes(header, rows), **options)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _make(services: ServiceBundle, **values: Any) -> str:
    values.setdefault("title", "a gadget")
    return services.records.create_record(make_actor(), "gadget", values).key


def _read(services: ServiceBundle, key: str) -> tuple[int, dict[str, Any]]:
    record = services.records.get_record(make_actor(), key)
    return record.version, record.data


# ------------------------------------------------------------------ the default


@pytest.mark.parametrize("blank_clears", [None, False])
def test_without_the_flag_a_blank_cell_changes_nothing(
    client: TestClient,
    app_services: ServiceBundle,
    gadget_type: ObjectType,
    blank_clears: bool | None,
) -> None:
    key = _make(app_services, notes="keep me")
    before = _read(app_services, key)
    result = _import(
        client,
        ["key", "title", "notes"],
        [{"key": key, "title": "a gadget", "notes": ""}],
        blank_clears=blank_clears,
    )
    assert result["updated"] == 1
    assert result["cleared"] == 0
    assert result["cleared_by_field"] == {}
    assert _read(app_services, key) == before


# ------------------------------------------------------------------ the clear


def test_with_the_flag_a_blank_cell_clears_the_field_and_the_result_says_so(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    key = _make(app_services, notes="remove me", points=8)
    version, _ = _read(app_services, key)
    result = _import(
        client,
        ["key", "title", "notes", "points"],
        [{"key": key, "title": "a gadget", "notes": "", "points": ""}],
    )
    assert result["errors"] == []
    assert result["updated"] == 1
    assert result["cleared"] == 2
    assert result["cleared_by_field"] == {"notes": 1, "points": 1}
    new_version, data = _read(app_services, key)
    assert "notes" not in data
    assert "points" not in data
    assert data["title"] == "a gadget"
    assert new_version == version + 1


def test_a_dry_run_reports_the_same_clears_and_writes_nothing(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    first = _make(app_services, notes="one", points=1)
    second = _make(app_services, notes="two")
    before = [_read(app_services, first), _read(app_services, second)]
    header = ["key", "notes", "points"]
    rows = [
        {"key": first, "notes": "", "points": ""},
        {"key": second, "notes": "", "points": ""},
    ]
    dry = _import(client, header, rows, dry_run=True)
    assert dry["dry_run"] is True
    # Computed from what the records hold: ``second`` was created with no ``points``, so
    # it holds the field's default, and that is a value too.
    expected = {
        field: sum(1 for _, data in before if field in data) for field in ("notes", "points")
    }
    assert expected == {"notes": 2, "points": 2}
    assert dry["cleared_by_field"] == expected
    assert dry["cleared"] == sum(expected.values())
    assert [_read(app_services, first), _read(app_services, second)] == before

    live = _import(client, header, rows)
    assert live["cleared"] == dry["cleared"]
    assert live["cleared_by_field"] == dry["cleared_by_field"]
    for key in (first, second):
        _, data = _read(app_services, key)
        assert "notes" not in data and "points" not in data


def test_a_column_the_file_does_not_carry_is_never_cleared(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    key = _make(app_services, notes="stay", serial="S-1")
    result = _import(client, ["key", "notes"], [{"key": key, "notes": ""}])
    assert result["cleared_by_field"] == {"notes": 1}
    _, data = _read(app_services, key)
    assert data["serial"] == "S-1"
    assert data["title"] == "a gadget"


def test_a_field_that_holds_nothing_is_not_counted_as_a_clear(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    key = _make(app_services)
    version, _ = _read(app_services, key)
    header = ["key", "notes", "serial"]
    rows = [{"key": key, "notes": "", "serial": ""}]
    dry = _import(client, header, rows, dry_run=True)
    live = _import(client, header, rows)
    for result in (dry, live):
        assert result["cleared"] == 0
        assert result["cleared_by_field"] == {}
    assert _read(app_services, key)[0] == version


# ----------------------------------------------------------- rows that create


def test_a_row_that_creates_is_unaffected_and_takes_the_default(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    header = ["key", "title", "notes", "points"]
    rows = [{"key": "", "title": "brand new", "notes": "", "points": ""}]
    dry = _import(client, header, rows, dry_run=True)
    live = _import(client, header, rows)
    for result in (dry, live):
        assert result["created"] == 1
        assert result["updated"] == 0
        assert result["cleared"] == 0
        assert result["cleared_by_field"] == {}
    found = app_services.records.query_records(
        make_actor(),
        "gadget",
        filter={"field": "title", "op": "eq", "value": "brand new"},
        fields="*",
    ).records
    assert len(found) == 1
    assert found[0]["data"]["points"] == 3
    assert "notes" not in found[0]["data"]


def test_blank_clears_with_create_mode_is_refused(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    body = _csv_bytes(["title", "notes"], [{"title": "x", "notes": ""}])
    for dry_run in (True, False):
        response = _post(client, body, mode="create", upsert_key=None, dry_run=dry_run)
        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert error["message"] == (
            "blank_clears applies to mode='upsert'; a create import has no existing value to clear."
        )
    assert app_services.records.query_records(make_actor(), "gadget").total_count == 0


# ------------------------------------------------------------- required, unique


def test_a_required_field_cannot_be_cleared_and_the_batch_writes_nothing(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    first = _make(app_services, notes="one")
    second = _make(app_services, notes="two")
    before = [_read(app_services, first), _read(app_services, second)]
    header = ["key", "title", "notes"]
    rows = [
        {"key": first, "title": "a gadget", "notes": ""},
        {"key": second, "title": "", "notes": ""},
    ]
    for dry_run in (True, False):
        result = _import(client, header, rows, dry_run=dry_run)
        assert result["errors"] == [
            {
                "row": 3,
                "field": "title",
                "reason": "Field 'title' is required and cannot be cleared.",
            }
        ]
        assert result["updated"] == 0
        assert result["cleared"] == 0
        assert result["cleared_by_field"] == {}
    assert [_read(app_services, first), _read(app_services, second)] == before


def test_two_rows_clearing_the_same_unique_field_on_different_records_import_cleanly(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    first = _make(app_services, serial="S-1")
    second = _make(app_services, serial="S-2")
    header = ["key", "serial"]
    rows = [{"key": first, "serial": ""}, {"key": second, "serial": ""}]
    dry = _import(client, header, rows, dry_run=True)
    assert dry["errors"] == []
    live = _import(client, header, rows)
    assert live["errors"] == []
    assert live["cleared_by_field"] == {"serial": 2} == dry["cleared_by_field"]
    assert "serial" not in _read(app_services, first)[1]
    assert "serial" not in _read(app_services, second)[1]
    # The value is free again.
    third = _make(app_services, serial="S-1")
    assert _read(app_services, third)[1]["serial"] == "S-1"


def test_a_row_matched_on_a_unique_field_is_still_matched_with_the_flag_on(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    key = _make(app_services, code="C-1", notes="remove me")
    result = _import(
        client,
        ["code", "notes"],
        [{"code": "C-1", "notes": ""}],
        upsert_key="code",
    )
    assert result["errors"] == []
    assert (result["created"], result["updated"]) == (0, 1)
    assert result["cleared_by_field"] == {"notes": 1}
    _, data = _read(app_services, key)
    assert data["code"] == "C-1"
    assert "notes" not in data


def test_a_row_whose_matching_field_is_blank_creates_as_it_does_today(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    key = _make(app_services, code="C-1", notes="untouched")
    before = _read(app_services, key)
    result = _import(
        client,
        ["code", "title", "notes"],
        [{"code": "", "title": "no code", "notes": ""}],
        upsert_key="code",
    )
    assert (result["created"], result["updated"], result["cleared"]) == (1, 0, 0)
    assert _read(app_services, key) == before


# ------------------------------------------------------------------ cell kinds


def test_a_relation_columns_blank_removes_no_link(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    target = _make(app_services, title="target")
    key = _make(app_services, notes="remove me")
    app_services.records.link_records(make_actor(), key, "related", [target])
    result = _import(
        client, ["key", "notes", "related"], [{"key": key, "notes": "", "related": ""}]
    )
    assert result["cleared_by_field"] == {"notes": 1}
    exported = client.get("/api/v1/object-types/gadget/export", params={"columns": "key,related"})
    assert exported.status_code == 200, exported.text
    by_key = {row["key"]: row["related"] for row in csv.DictReader(io.StringIO(exported.text))}
    assert by_key[key] == target


def test_null_and_backslash_n_are_still_stored_as_text(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    """A fence: no cell text acquires a meaning (DD-20)."""
    first = _make(app_services, notes="one")
    second = _make(app_services, notes="two")
    result = _import(
        client,
        ["key", "notes"],
        [{"key": first, "notes": "null"}, {"key": second, "notes": "\\N"}],
    )
    assert result["cleared"] == 0
    assert _read(app_services, first)[1]["notes"] == "null"
    assert _read(app_services, second)[1]["notes"] == "\\N"


def test_a_blank_multi_select_boolean_and_user_ref_cell_each_clear(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    key = _make(app_services, tags=["red", "blue"], active=True, owner=BOOTSTRAP_PRINCIPAL_ID)
    _, data = _read(app_services, key)
    assert {"tags", "active", "owner"} <= set(data)
    result = _import(
        client,
        ["key", "tags", "active", "owner"],
        [{"key": key, "tags": "", "active": "", "owner": ""}],
    )
    assert result["errors"] == []
    assert result["cleared_by_field"] == {"tags": 1, "active": 1, "owner": 1}
    assert result["cleared"] == 3
    _, data = _read(app_services, key)
    assert not {"tags", "active", "owner"} & set(data)


def test_a_cell_of_spaces_is_not_blank(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    key = _make(app_services, notes="replace me")
    result = _import(client, ["key", "notes"], [{"key": key, "notes": "  "}])
    assert result["cleared"] == 0
    assert _read(app_services, key)[1]["notes"] == "  "


# ------------------------------------------------------------------ short rows


@pytest.mark.parametrize("blank_clears", [True, False])
def test_a_row_shorter_than_the_header_is_blank_in_the_cells_it_lacks(
    client: TestClient,
    app_services: ServiceBundle,
    gadget_type: ObjectType,
    blank_clears: bool,
) -> None:
    key = _make(app_services, notes="trailing", serial="S-9")
    before = _read(app_services, key)
    body = f"key,title,notes,serial\r\n{key},a gadget\r\n".encode()
    response = _post(client, body, blank_clears=blank_clears)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["errors"] == []
    if blank_clears:
        assert result["cleared_by_field"] == {"notes": 1, "serial": 1}
        _, data = _read(app_services, key)
        assert "notes" not in data and "serial" not in data
    else:
        assert result["cleared"] == 0
        assert _read(app_services, key) == before


# ------------------------------------------------- the count is what is removed


def test_a_value_set_by_one_row_and_blanked_by_a_later_one_is_not_a_clear(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    key = _make(app_services)
    header = ["key", "notes"]
    rows = [{"key": key, "notes": "set by row 2"}, {"key": key, "notes": ""}]
    dry = _import(client, header, rows, dry_run=True)
    live = _import(client, header, rows)
    assert dry["cleared"] == live["cleared"] == 0
    assert dry["cleared_by_field"] == live["cleared_by_field"] == {}
    assert "notes" not in _read(app_services, key)[1]


def test_two_rows_naming_one_record_report_what_the_commit_removes(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    key = _make(app_services, notes="held before", serial="S-1")
    header = ["key", "notes", "serial"]
    rows = [
        # Sets one, clears the other.
        {"key": key, "notes": "rewritten", "serial": ""},
        # Clears both: ``serial`` a second time, which is still one clear.
        {"key": key, "notes": "", "serial": ""},
    ]
    dry = _import(client, header, rows, dry_run=True)
    held_before = _read(app_services, key)[1]
    live = _import(client, header, rows)
    held_after = _read(app_services, key)[1]
    removed = sorted(set(held_before) - set(held_after))
    assert removed == ["notes", "serial"]
    assert live["cleared"] == len(removed) == dry["cleared"]
    assert live["cleared_by_field"] == {"notes": 1, "serial": 1} == dry["cleared_by_field"]
    assert live["updated"] == 2


def test_a_value_blanked_by_one_row_and_set_by_a_later_one_is_not_a_clear(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    key = _make(app_services, notes="held before")
    header = ["key", "notes"]
    rows = [{"key": key, "notes": ""}, {"key": key, "notes": "back again"}]
    dry = _import(client, header, rows, dry_run=True)
    live = _import(client, header, rows)
    assert dry["cleared"] == live["cleared"] == 0
    assert _read(app_services, key)[1]["notes"] == "back again"


def test_a_commit_counts_a_value_added_after_the_dry_run(
    client: TestClient, app_services: ServiceBundle, gadget_type: ObjectType
) -> None:
    key = _make(app_services)
    header = ["key", "notes"]
    rows = [{"key": key, "notes": ""}]
    dry = _import(client, header, rows, dry_run=True)
    assert dry["cleared"] == 0
    app_services.records.update_record(make_actor(), key, {"notes": "added by a colleague"})
    live = _import(client, header, rows)
    assert live["cleared"] == 1
    assert live["cleared_by_field"] == {"notes": 1}
    assert "notes" not in _read(app_services, key)[1]


# ------------------------------------------------------------------ the contract


def test_the_openapi_operation_states_the_behaviour(client: TestClient) -> None:
    document = client.get("/openapi.json").json()
    operation = document["paths"]["/api/v1/object-types/{object_type_key}/import"]["post"]
    assert operation["summary"] != "Import Csv"
    description: str = operation["description"]
    for phrase in ("blank_clears", "mode=create", "required", "add-only"):
        assert phrase in description, phrase
