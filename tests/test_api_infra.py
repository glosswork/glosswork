"""Cross-cutting infrastructure checks: coverage against the spec, the OpenAPI
document and the error envelope.

These are structural/architectural assertions over the whole REST surface, not
one resource's behavior, so they live together rather than in a per-resource
test module: the async-def sweep, the MCP-tool-to-REST mapping walk, the
service-method-to-route coverage check, the OpenAPI document, and every
documented error code produced through the real HTTP layer.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.services import ServiceBundle
from glosswork.services.comments import CommentService
from glosswork.services.records import RecordService
from glosswork.services.schema import SchemaService
from tests.conftest import make_actor, select_options

_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "glosswork"
_ROUTES_DIR = _SRC_DIR / "routes"
# The REST adapter is the route modules plus the response envelopes they share
# with the MCP tools (the composed shapes live in ``envelopes.py`` so the
# two surfaces cannot drift); a service method reached only through an envelope
# helper is still reachable over REST.
_ROUTE_SOURCE = "\n".join(
    p.read_text() for p in [*_ROUTES_DIR.glob("*.py"), _SRC_DIR / "envelopes.py"]
)

# --------------------------------------------------------------------------- fixture


@pytest.fixture
def widget_type(app_services: ServiceBundle) -> str:
    """A small object type with a required field, a single_select, and a
    self-referential relation — enough surface for every error-code test below."""
    app_services.schema.create_object_type(
        make_actor(),
        key="widget",
        name="Widget",
        name_plural="Widgets",
        description="A test object type used by the infrastructure checks.",
        key_prefix="WDG",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "Short human-readable name.",
                "required": True,
            },
            {
                "key": "status",
                "name": "Status",
                "type": "single_select",
                "description": "Delivery state.",
                "config": {"options": select_options("todo", "doing", "done")},
            },
            {
                "key": "parent",
                "name": "Parent",
                "type": "relation",
                "description": "Self-referential parent link.",
                "config": {
                    "target_type_key": "widget",
                    "cardinality": "one",
                    "inverse_field_key": "children",
                },
            },
        ],
    )
    return "widget"


# --------------------------------------------------------------------------- DD-5 amendment


def test_every_route_handler_is_sync_def_not_async_def() -> None:
    """An ``async def`` route calling the synchronous service layer blocks the
    event loop and is the easiest mistake to make in a route handler (DD-5)."""
    offenders = []
    for path in _ROUTES_DIR.glob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if re.match(r"\s*async def ", line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, f"found async def route handler(s): {offenders}"


# --------------------------------------------------------------------------- DD-4


def test_write_routes_thread_actor_context_from_the_edge(
    client: TestClient, widget_type: str
) -> None:
    """Every REST write is attributed to the seeded bootstrap principal via the
    edge-constructed ActorContext (DD-4) — no REST write path bypasses it."""
    response = client.post(f"/api/v1/object-types/{widget_type}/records", json={"title": "x"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created_by"] == BOOTSTRAP_PRINCIPAL_ID
    assert body["updated_by"] == BOOTSTRAP_PRINCIPAL_ID

    history = client.get(f"/api/v1/records/{body['key']}/history").json()["events"]
    assert history
    assert all(e["principal_id"] == BOOTSTRAP_PRINCIPAL_ID for e in history)
    assert all(e["surface"] == "api" for e in history)


# --------------------------------------------------------------------------- FR-A1, MCP parity


# docs/MCP_TOOLS.md section 8: every row not explicitly MCP-only. Path parameter names
# are normalized (routes use `{ref}` / `{object_type_key}` / `{proposal_id}` in place of
# the doc's generic `{key}` / `{id}`), which is an implementation detail the table
# doesn't pin down.
_MCP_TOOLS_SECTION_8_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/v1/object-types"),
    ("GET", "/api/v1/object-types/{key}"),
    ("POST", "/api/v1/object-types/{object_type_key}/query"),
    ("GET", "/api/v1/records/{ref}"),
    ("POST", "/api/v1/object-types/{object_type_key}/records"),
    ("PATCH", "/api/v1/records/{ref}"),
    ("DELETE", "/api/v1/records/{ref}"),
    ("POST", "/api/v1/object-types/{object_type_key}/bulk-update"),
    ("POST", "/api/v1/records/{ref}/links/{field_key}"),
    ("DELETE", "/api/v1/records/{ref}/links/{field_key}"),
    ("POST", "/api/v1/records/{ref}/comments"),
    ("PATCH", "/api/v1/comments/{comment_id}"),
    ("DELETE", "/api/v1/comments/{comment_id}"),
    ("GET", "/api/v1/records/{ref}/history"),
    ("GET", "/api/v1/changes"),
    ("POST", "/api/v1/object-types/{key}/fields"),
    ("PATCH", "/api/v1/object-types/{key}/fields/{field_key}"),
    ("POST", "/api/v1/schema-proposals"),
    ("GET", "/api/v1/schema-proposals"),
    ("POST", "/api/v1/schema-proposals/{proposal_id}/approve"),
    # Search on both surfaces (FR-Q1 to FR-Q9), and the two REST/UI-only index rows.
    ("POST", "/api/v1/search"),
    ("GET", "/api/v1/admin/search-index"),
    ("POST", "/api/v1/admin/search-index/reindex"),
]

# Routes that are required but not present as their own row in the
# docs/MCP_TOOLS.md section 8 table: object-type create/update (the table
# only lists the describe/list reads), proposal rejection (explicitly called out
# as "there is deliberately no gap: rejection is exercised too"), CSV (explicitly
# "REST-only at MVP" per the doc's closing paragraph), and attachments (not an MCP
# tool at all; the PRD's attachment requirements need the routes directly).
_ADDITIONAL_REQUIRED_ROUTES: list[tuple[str, str]] = [
    ("POST", "/api/v1/object-types"),
    ("PATCH", "/api/v1/object-types/{key}"),
    ("POST", "/api/v1/schema-proposals/{proposal_id}/reject"),
    ("GET", "/api/v1/schema-proposals/{proposal_id}"),
    ("POST", "/api/v1/records/{ref}/restore"),
    ("GET", "/api/v1/records/{ref}/comments"),
    ("POST", "/api/v1/object-types/{object_type_key}/import"),
    ("GET", "/api/v1/object-types/{object_type_key}/export"),
    ("POST", "/api/v1/attachments"),
    ("GET", "/api/v1/attachments/{attachment_id}"),
    ("GET", "/api/v1/attachments/{attachment_id}/download"),
]


def _registered_routes(client: TestClient) -> set[tuple[str, str]]:
    spec = client.get("/openapi.json").json()
    return {(method.upper(), path) for path, ops in spec["paths"].items() for method in ops}


def test_mcp_tools_section_8_mapping_table_routes_are_registered(client: TestClient) -> None:
    registered = _registered_routes(client)
    missing = [rp for rp in _MCP_TOOLS_SECTION_8_ROUTES if rp not in registered]
    assert not missing, f"missing REST routes from docs/MCP_TOOLS.md section 8: {missing}"


def test_additional_required_routes_are_registered(client: TestClient) -> None:
    registered = _registered_routes(client)
    missing = [rp for rp in _ADDITIONAL_REQUIRED_ROUTES if rp not in registered]
    assert not missing, f"missing required REST routes: {missing}"


# --------------------------------------------------------------------------- service coverage


def _public_methods(cls: type) -> set[str]:
    return {
        name
        for name, _member in inspect.getmembers(cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


# Public service methods that legitimately have no route, each named explicitly rather
# than matched by pattern, in the same spirit as ``scopes.SCOPE_EXEMPT_PATHS``: this
# list has to be edited, so exempting a method is a visible act rather than a side
# effect of adding one.
#
# All three are the sort-composite index maintenance (``sqlexpr.sort_index_ddl``).
# They are public because ``app.py`` calls ``reconcile_all_sort_indexes`` at startup and
# the other two are its unit-testable parts, and they are deliberately not reachable
# over HTTP: an index set is derived from the schema, so the way to change it is to
# change the schema, not to call an endpoint that would let the two disagree.
#
# Of ``RecordService``'s entries: ``list_link_summaries`` *is* reached over
# HTTP, but through ``envelopes.record_with_includes_doc`` rather than by name from a
# route body, which is the same indirection ``record_doc`` has always had.
# ``require_type_level`` is a gate ``CsvService`` calls, not an operation: routing it
# would publish an endpoint whose only effect is to raise or return nothing.
# ``list_links`` sits behind ``CsvService.export_csv``: the ``include=links``
# envelope calls ``list_link_summaries`` instead, because a list of rows has no way
# to carry a ``{"redacted": true}`` entry. It is still reached over HTTP,
# one service further down.
# ``linked_keys_for_page`` is the batched counterpart to ``list_links``
# and sits behind ``CsvService.export_csv_stream`` for the same reason and one service
# further down again. Routing it would publish a page-shaped relation read no client has
# asked for, and the two relation *documents* callers do use -- ``include=links`` and
# ``expand_relations`` -- already have their paths.
_ROUTELESS_SERVICE_METHODS: dict[str, frozenset[str]] = {
    "SchemaService": frozenset(
        {
            "desired_sort_indexes",
            "reconcile_sort_indexes",
            "reconcile_all_sort_indexes",
            "retire_legacy_index_names",
            # Called once from the lifespan and logged; routing it would
            # publish an operator diagnostic no client has asked for, and the answer is
            # already visible to an administrator through ``describe_object_type``,
            # which returns a colliding key twice.
            "reserved_key_collisions",
        }
    ),
    "RecordService": frozenset(
        {
            "list_link_summaries",
            "require_type_level",
            "list_links",
            "linked_keys_for_page",
            # The batch boundary and the three writes that share it. A route
            # reaches every one of them through the public wrapper it already calls
            # (`create_record`, `update_record`, `link_records`); these exist so
            # `CsvService` can put N of them in one transaction, which is FR-E2's
            # rollback guarantee, and routing them would publish a transaction handle.
            "write_batch",
            "create_record_in_txn",
            "update_record_in_txn",
            "link_records_in_txn",
        }
    ),
}


@pytest.mark.parametrize("service_cls", [SchemaService, RecordService, CommentService])
def test_every_public_service_method_is_called_from_a_route(service_cls: type) -> None:
    """A newly added service method cannot silently lack a route: every public
    method on SchemaService/RecordService/CommentService must appear as a call
    somewhere in the routes source."""
    exempt = _ROUTELESS_SERVICE_METHODS.get(service_cls.__name__, frozenset())
    uncalled = [
        m for m in _public_methods(service_cls) if m not in exempt and f".{m}(" not in _ROUTE_SOURCE
    ]
    assert not uncalled, f"{service_cls.__name__} method(s) with no route: {uncalled}"


def test_the_routeless_exemption_list_is_exactly_what_it_claims() -> None:
    """The exemption list must not outlive what it exempts. A method removed from a
    service but left here would silently widen the allowance for the next one."""
    for class_name, names in _ROUTELESS_SERVICE_METHODS.items():
        service_cls = {"SchemaService": SchemaService, "RecordService": RecordService}[class_name]
        stale = sorted(names - _public_methods(service_cls))
        assert not stale, f"{class_name} no longer has exempted method(s): {stale}"


# --------------------------------------------------------------------------- FR-A2


def test_openapi_json_is_valid_openapi_31(client: TestClient) -> None:
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    body = spec.json()
    assert body["openapi"].startswith("3.1")
    assert body["paths"]  # non-empty: routes are actually documented


def test_docs_serves_an_interactive_browser(client: TestClient) -> None:
    """``/docs`` is HTML, and so is the SPA catch-all that answers in its place when the
    route is absent and ``web/dist`` is built, so a status and content-type assertion
    alone survives the route's deletion. This asserts content only Swagger UI serves."""
    response = client.get("/docs")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "swagger-ui" in response.text
    assert "/openapi.json" in response.text


# --------------------------------------------------------------------------- FR-A4 error codes


def test_unknown_object_type(client: TestClient) -> None:
    response = client.get("/api/v1/object-types/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_object_type"


def test_unknown_field(client: TestClient, widget_type: str) -> None:
    response = client.post(f"/api/v1/object-types/{widget_type}/records", json={"titel": "typo"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_field"


def test_invalid_operator(client: TestClient, widget_type: str) -> None:
    response = client.post(
        f"/api/v1/object-types/{widget_type}/query",
        json={"filter": {"field": "status", "op": "gt", "value": "todo"}},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_operator"


def test_validation_failed(client: TestClient, widget_type: str) -> None:
    response = client.post(f"/api/v1/object-types/{widget_type}/records", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_version_conflict(client: TestClient, widget_type: str) -> None:
    created = client.post(
        f"/api/v1/object-types/{widget_type}/records", json={"title": "conflict"}
    ).json()
    response = client.patch(
        f"/api/v1/records/{created['key']}",
        json={"values": {"title": "new"}, "expected_version": 99},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "version_conflict"
    assert "current_version" in body["error"]["details"]
    assert "conflicting_fields" in body["error"]["details"]
    assert "changed_since_your_version" in body["error"]["details"]


def test_relation_blocked(client: TestClient, widget_type: str) -> None:
    parent = client.post(f"/api/v1/object-types/{widget_type}/records", json={"title": "p"}).json()
    child = client.post(f"/api/v1/object-types/{widget_type}/records", json={"title": "c"}).json()
    client.post(
        f"/api/v1/records/{child['key']}/links/parent", json={"to_records": [parent["key"]]}
    )
    response = client.delete(f"/api/v1/records/{parent['key']}")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "relation_blocked"
    assert body["error"]["details"]["blocking_record_keys"] == [child["key"]]


def test_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/records/WDG-9999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_impact_changed(client: TestClient, app_services: ServiceBundle, widget_type: str) -> None:
    client.post(f"/api/v1/object-types/{widget_type}/records", json={"title": "existing"})
    proposal = client.patch(
        f"/api/v1/object-types/{widget_type}/fields/title",
        json={"changes": {"required": False}},
    )
    # Relax first so the field can be legally tightened again below with a fresh
    # violator, forcing the recomputed impact to differ from proposal time.
    assert proposal.json()["status"] == "applied"

    tighten = client.patch(
        f"/api/v1/object-types/{widget_type}/fields/title", json={"changes": {"required": True}}
    )
    # No existing violations yet: this call itself is additive (nothing to tighten
    # against), so create the destructive scenario by relaxing again and adding a
    # violating record before re-tightening.
    assert tighten.json()["status"] == "applied"

    client.patch(
        f"/api/v1/object-types/{widget_type}/fields/title", json={"changes": {"required": False}}
    )
    app_services.records.create_record(make_actor(), widget_type, {})  # violates future 'required'
    tighten = client.patch(
        f"/api/v1/object-types/{widget_type}/fields/title", json={"changes": {"required": True}}
    )
    assert tighten.json()["status"] == "pending_human_approval"
    proposal_id = tighten.json()["proposal_id"]

    # Data changes between proposal and approval: the recomputed impact differs.
    app_services.records.create_record(make_actor(), widget_type, {})

    response = client.post(f"/api/v1/schema-proposals/{proposal_id}/approve", json={})
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "impact_changed"
    assert "new_impact" in body["error"]["details"]

    confirmed = client.post(
        f"/api/v1/schema-proposals/{proposal_id}/approve",
        json={"confirm_impact": body["error"]["details"]["new_impact"], "null_non_coercible": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "approved"


def test_proposal_state(client: TestClient, widget_type: str) -> None:
    proposal = client.post(
        "/api/v1/schema-proposals",
        json={"change_type": "delete_field", "object_type": widget_type, "field_key": "status"},
    ).json()
    proposal_id = proposal["proposal_id"]
    approve = client.post(f"/api/v1/schema-proposals/{proposal_id}/approve", json={})
    assert approve.status_code == 200

    # Already decided: a second decision is a proposal_state conflict, not applied.
    response = client.post(f"/api/v1/schema-proposals/{proposal_id}/approve", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "proposal_state"

    reject_response = client.post(f"/api/v1/schema-proposals/{proposal_id}/reject", json={})
    assert reject_response.status_code == 409
    assert reject_response.json()["error"]["code"] == "proposal_state"
