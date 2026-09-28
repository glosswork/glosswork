"""Smoke test for the shared HTTP-layer fixtures (``client``, ``app_services``)
that the route test suites build on."""

from __future__ import annotations

from fastapi.testclient import TestClient

from glosswork.services import ServiceBundle
from tests.conftest import make_actor


def test_healthz_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_app_services_shares_the_clients_database(
    client: TestClient, app_services: ServiceBundle
) -> None:
    app_services.schema.create_object_type(
        make_actor(),
        key="widget",
        name="Widget",
        name_plural="Widgets",
        description="Seeded directly through the service layer for an HTTP test.",
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
    record = app_services.records.create_record(make_actor(), "widget", {"label": "seeded"})
    assert record.key == "WID-001"
