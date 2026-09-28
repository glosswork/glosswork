"""The ``principals`` sidecar on all six document paths, on REST and MCP alike, its
projection parity, and the fence that keeps ``serializers.record_doc`` where it is.

Two properties matter more than the presence of the map.

The first is **cost**: one ``principals_by_ids`` call per document regardless of page
size. "N+1 but correct" passes every functional assertion and is exactly what a sidecar
assembled per record would be, so the call count is asserted directly by counting
repository calls rather than inferred from a passing read.

The second is **projection**: the map carries three keys and no ``role``. Same discipline
as the directory route, and for the same reason -- what makes a name
readable by everyone is that it is only a name.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server import MCPServer

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.auth import PatTokenResolver
from glosswork.mcp_server import create_mcp_server
from glosswork.repositories.models import PrincipalRow
from glosswork.serializers import record_doc
from glosswork.services import ServiceBundle
from glosswork.services import export as export_module
from glosswork.services.export import ExportService
from tests.conftest import make_actor
from tests.mcp_support import memory_session, structured

PASSWORD = "correct-horse-battery-staple"

# ``type`` is in the projection. docs/DESIGN.md 6.1 makes the kind of a principal
# load-bearing -- shape carries it so it survives greyscale -- and says a service account is
# an agent, so without this key one renders as a person circle. It discloses nothing new:
# ``GET /api/v1/principals/directory`` already serves ``type`` to any authenticated caller.
SIDECAR_KEYS = {"display_name", "email", "is_active", "type"}
WITHHELD_KEYS = ("role", "auth_provider", "external_id", "password_hash", "id")


# --------------------------------------------------------------------------- fixtures


def seed_owner_type(services: ServiceBundle) -> None:
    services.schema.create_object_type(
        make_actor(),
        key="initiative",
        name="Initiative",
        name_plural="Initiatives",
        description="A programme of work, seeded for the sidecar suite.",
        key_prefix="INI",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "What this initiative is called, shown wherever it is listed.",
            },
            {
                "key": "owner",
                "name": "Owner",
                "type": "user_ref",
                "description": "Who is accountable for this initiative day to day.",
            },
            {
                "key": "reviewer",
                "name": "Reviewer",
                "type": "user_ref",
                "description": "Who signs the initiative off before it closes.",
            },
        ],
    )


def seed_plain_type(services: ServiceBundle) -> None:
    """A type with **no** ``user_ref`` field at all, for the ``created_by`` case."""
    services.schema.create_object_type(
        make_actor(),
        key="note",
        name="Note",
        name_plural="Notes",
        description="A free-standing note, seeded for the sidecar suite.",
        key_prefix="NOT",
        fields=[
            {
                "key": "body",
                "name": "Body",
                "type": "long_text",
                "description": "What the note says, in the author's own words.",
            }
        ],
    )


def person(services: ServiceBundle, email: str, name: str) -> PrincipalRow:
    return services.principals.create_user(
        make_actor(), email=email, display_name=name, role="member", password=PASSWORD
    )


@pytest.fixture
def peopled(services: ServiceBundle) -> dict[str, Any]:
    seed_owner_type(services)
    seed_plain_type(services)
    sarah = person(services, "sarah@example.com", "Sarah Okonjo")
    raj = person(services, "raj@example.com", "Raj Patel")
    records = [
        services.records.create_record(
            make_actor(),
            "initiative",
            {"title": f"Initiative {i}", "owner": sarah.id, "reviewer": raj.id},
        )
        for i in range(5)
    ]
    return {"sarah": sarah, "raj": raj, "records": records}


class CountingPrincipals:
    """Wraps the real repository and counts ``principals_by_ids`` calls."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def principals_by_ids(self, conn: Any, ids: list[str]) -> list[PrincipalRow]:
        self.calls += 1
        return self._inner.principals_by_ids(conn, ids)


# ---------------------------------------------------------------- the map, at the service


class TestSidecarService:
    def test_it_covers_every_user_ref_value_plus_created_by_and_updated_by(
        self, services: ServiceBundle, peopled: dict[str, Any]
    ) -> None:
        docs = [record_doc(r) for r in peopled["records"]]
        sidecar = services.records.principal_sidecar(make_actor(), "initiative", docs)
        assert set(sidecar) == {
            peopled["sarah"].id,
            peopled["raj"].id,
            BOOTSTRAP_PRINCIPAL_ID,
        }
        assert sidecar[peopled["sarah"].id]["display_name"] == "Sarah Okonjo"

    def test_a_type_with_no_user_ref_field_still_gets_created_by_and_updated_by(
        self, services: ServiceBundle, peopled: dict[str, Any]
    ) -> None:
        note = services.records.create_record(make_actor(), "note", {"body": "hello"})
        sidecar = services.records.principal_sidecar(make_actor(), "note", [record_doc(note)])
        assert set(sidecar) == {BOOTSTRAP_PRINCIPAL_ID}

    def test_the_object_type_may_be_named_by_key_or_by_id(
        self, services: ServiceBundle, peopled: dict[str, Any]
    ) -> None:
        """``query_records`` has the key; the four write paths have a ``RecordRow`` and
        therefore an id. One method serves both rather than two that can drift."""
        record = peopled["records"][0]
        docs = [record_doc(record)]
        by_key = services.records.principal_sidecar(make_actor(), "initiative", docs)
        by_id = services.records.principal_sidecar(make_actor(), record.object_type_id, docs)
        assert by_key == by_id

    @pytest.mark.parametrize("withheld", WITHHELD_KEYS)
    def test_each_withheld_key_is_absent_by_name(
        self, services: ServiceBundle, peopled: dict[str, Any], withheld: str
    ) -> None:
        docs = [record_doc(r) for r in peopled["records"]]
        sidecar = services.records.principal_sidecar(make_actor(), "initiative", docs)
        for entry in sidecar.values():
            assert set(entry) == SIDECAR_KEYS
            assert withheld not in entry

    def test_no_records_means_no_read_at_all(
        self, services: ServiceBundle, peopled: dict[str, Any]
    ) -> None:
        assert services.records.principal_sidecar(make_actor(), "initiative", []) == {}

    def test_exactly_one_repository_call_per_document_regardless_of_page_size(
        self, services: ServiceBundle, peopled: dict[str, Any]
    ) -> None:
        """The property the shape exists for. Five records referencing three principals
        is **one** call, not five and not three."""
        counting = CountingPrincipals(services.records._principals)  # noqa: SLF001 - test seam
        services.records._principals = counting  # noqa: SLF001
        docs = [record_doc(r) for r in peopled["records"]]
        sidecar = services.records.principal_sidecar(make_actor(), "initiative", docs)
        assert counting.calls == 1
        assert len(sidecar) == 3

    def test_an_unresolvable_id_is_simply_absent(
        self, services: ServiceBundle, peopled: dict[str, Any]
    ) -> None:
        """A referent that is gone renders as its raw id on the client, which is the
        fallback AuditTimeline has used since DD-25. It is not an error here."""
        doc = record_doc(peopled["records"][0])
        doc["data"] = {**doc["data"], "owner": "00000000-0000-4000-8000-00000000ffff"}
        sidecar = services.records.principal_sidecar(make_actor(), "initiative", [doc])
        assert "00000000-0000-4000-8000-00000000ffff" not in sidecar


# ------------------------------------------------------------ the six documents, on REST


@pytest.fixture
def http(app: FastAPI, client: TestClient) -> dict[str, Any]:
    services: ServiceBundle = app.state.services
    seed_owner_type(services)
    sarah = person(services, "sarah@example.com", "Sarah Okonjo")
    record = services.records.create_record(
        make_actor(), "initiative", {"title": "One", "owner": sarah.id}
    )
    return {"client": client, "services": services, "sarah": sarah, "record": record}


def assert_names(body: dict[str, Any], principal_id: str, expected: str) -> None:
    assert body["principals"][principal_id]["display_name"] == expected
    assert set(body["principals"][principal_id]) == SIDECAR_KEYS


class TestSidecarOverRest:
    def test_query_records_carries_it_as_a_sibling_of_records(self, http: dict[str, Any]) -> None:
        response = http["client"].post(
            "/api/v1/object-types/initiative/query", json={"fields": "*"}
        )
        assert response.status_code == 200
        assert_names(response.json(), http["sarah"].id, "Sarah Okonjo")

    def test_get_record_carries_it(self, http: dict[str, Any]) -> None:
        response = http["client"].get(f"/api/v1/records/{http['record'].key}")
        assert_names(response.json(), http["sarah"].id, "Sarah Okonjo")

    def test_create_record_carries_it(self, http: dict[str, Any]) -> None:
        """An agent confirms who it just assigned in the same turn."""
        response = http["client"].post(
            "/api/v1/object-types/initiative/records",
            json={"title": "Two", "owner": "Sarah Okonjo"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"]["owner"] == http["sarah"].id
        assert_names(body, http["sarah"].id, "Sarah Okonjo")

    def test_update_record_carries_it(self, http: dict[str, Any]) -> None:
        response = http["client"].patch(
            f"/api/v1/records/{http['record'].key}",
            json={"values": {"title": "Renamed"}},
        )
        assert_names(response.json(), http["sarah"].id, "Sarah Okonjo")

    def test_delete_and_restore_carry_it(self, http: dict[str, Any]) -> None:
        deleted = http["client"].delete(f"/api/v1/records/{http['record'].key}")
        assert_names(deleted.json(), http["sarah"].id, "Sarah Okonjo")
        restored = http["client"].post(f"/api/v1/records/{http['record'].key}/restore")
        assert_names(restored.json(), http["sarah"].id, "Sarah Okonjo")

    def test_bulk_update_does_not_carry_it(self, http: dict[str, Any]) -> None:
        """Measured, not assumed: ``bulk_update_doc`` returns ``affected_count``,
        ``sample_keys`` and ``dry_run``, and carries no record, no field value and no
        principal id."""
        response = http["client"].post(
            "/api/v1/object-types/initiative/bulk-update",
            json={"filter": {}, "values": {"title": "Bulk"}, "dry_run": True},
        )
        assert response.status_code == 200
        assert set(response.json()) == {"affected_count", "sample_keys", "dry_run"}

    def test_search_does_not_carry_it(self, http: dict[str, Any]) -> None:
        response = http["client"].post("/api/v1/search", json={"query": "One"})
        assert response.status_code == 200
        assert "principals" not in response.json()


# ----------------------------------------------------------------------- the MCP surface


@pytest.fixture
def mcp(app: FastAPI, client: TestClient) -> MCPServer:
    services: ServiceBundle = app.state.services
    return create_mcp_server(lambda: services, PatTokenResolver(lambda: services))


@pytest.mark.anyio
async def test_mcp_query_and_get_record_carry_the_same_map_as_rest(
    http: dict[str, Any], mcp: MCPServer, api_tokens: dict[str, str]
) -> None:
    rest_query = (
        http["client"].post("/api/v1/object-types/initiative/query", json={"fields": "*"}).json()
    )
    rest_get = http["client"].get(f"/api/v1/records/{http['record'].key}").json()
    async with memory_session(mcp, token=api_tokens["read"]) as c:
        mcp_query = structured(
            await c.call_tool("query_records", {"object_type": "initiative", "fields": "*"})
        )
        mcp_get = structured(await c.call_tool("get_record", {"record": http["record"].key}))
    assert mcp_query["principals"] == rest_query["principals"]
    assert mcp_get["principals"] == rest_get["principals"]


@pytest.mark.anyio
async def test_mcp_create_and_update_carry_the_map(
    http: dict[str, Any], mcp: MCPServer, api_tokens: dict[str, str]
) -> None:
    async with memory_session(mcp, token=api_tokens["write"]) as c:
        created = structured(
            await c.call_tool(
                "create_record",
                {
                    "object_type": "initiative",
                    "values": {"title": "By name", "owner": "sarah@example.com"},
                },
            )
        )
        updated = structured(
            await c.call_tool(
                "update_record",
                {"record": created["key"], "values": {"title": "Renamed"}},
            )
        )
    assert created["data"]["owner"] == http["sarah"].id
    assert_names(created, http["sarah"].id, "Sarah Okonjo")
    assert_names(updated, http["sarah"].id, "Sarah Okonjo")


@pytest.mark.anyio
async def test_find_principals_returns_the_same_projection_as_the_rest_route(
    http: dict[str, Any], mcp: MCPServer, api_tokens: dict[str, str]
) -> None:
    """One service method, two adapters, one projection -- which is what makes
    REST/MCP parity structural rather than aspirational (DD-3)."""
    rest = http["client"].get(
        "/api/v1/principals/directory",
        headers={"Authorization": f"Bearer {api_tokens['read']}"},
    )
    assert rest.status_code == 200
    async with memory_session(mcp, token=api_tokens["read"]) as c:
        tool = structured(await c.call_tool("find_principals", {}))
    assert tool == rest.json()
    assert {"role", "auth_provider"} & set(tool["principals"][0]) == set()


@pytest.mark.anyio
async def test_find_principals_filters_by_query(
    http: dict[str, Any], mcp: MCPServer, api_tokens: dict[str, str]
) -> None:
    async with memory_session(mcp, token=api_tokens["read"]) as c:
        found = structured(await c.call_tool("find_principals", {"query": "okonjo"}))
    assert [p["display_name"] for p in found["principals"]] == ["Sarah Okonjo"]


# --------------------------------------------------------------------------------- fence
#
# *(Fence.)* Where the record serializer lives and what it takes, guarded. These cannot fail by
# construction and are not counted as measured assertions.


def test_fence_record_doc_still_takes_only_a_record_row() -> None:
    signature = inspect.signature(record_doc)
    assert list(signature.parameters) == ["record"]


def test_fence_record_doc_output_carries_no_principals_key(
    services: ServiceBundle, peopled: dict[str, Any]
) -> None:
    """The sidecar is composed *onto* the document by the service and the envelopes, not
    baked into the serializer, which is what lets ``ExportService`` keep sharing it."""
    doc = record_doc(peopled["records"][0])
    assert "principals" not in doc
    # ``updated_by_agent_label_id`` is in the set. The fence is about the sidecar being
    # composed ON the document rather than baked into the serializer -- which is
    # what lets ``ExportService`` keep sharing ``record_doc`` -- so it keeps pinning the key set
    # by equality, with every column named. Restated rather than loosened when a column joins: a
    # ``set(doc) >= {...}`` here would stop catching the thing it exists to catch.
    assert set(doc) == {
        "id",
        "key",
        "version",
        "created_at",
        "created_by",
        "updated_at",
        "updated_by",
        "updated_by_agent_label_id",
        "deleted_at",
        "comment_count",
        "last_comment_at",
        "data",
    }


def test_fence_export_service_still_imports_record_doc() -> None:
    """The reason ``record_doc`` lives in ``serializers`` at all: the service layer
    reuses it without importing ``envelopes``, which imports ``ServiceBundle`` back."""
    assert export_module.record_doc is record_doc
    assert ExportService is not None
