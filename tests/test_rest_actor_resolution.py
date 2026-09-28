"""DD-8: REST resolves ActorContext through the same TokenResolver seam as MCP,
not a hardcoded bootstrap actor.

Uses the same capfd/JSON-parsing pattern as
``test_infra.test_access_logs_are_structured_json_with_request_and_actor_fields``:
the access log's ``scope`` field is the inspectable seam that proves resolution,
without adding a new endpoint or response field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from glosswork.app import create_app
from glosswork.config import Settings
from glosswork.services import ServiceBundle
from tests.conftest import KITCHEN_SINK_FIELDS, make_actor, mint_scope_tokens


def _access_events(captured_out: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in captured_out.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "access":
            events.append(event)
    return events


def _access_event_for(
    client: TestClient, capfd: pytest.CaptureFixture[str], **kwargs: Any
) -> dict[str, Any]:
    response = client.get("/api/v1/object-types", **kwargs)
    request_id = response.headers["x-request-id"]
    captured = capfd.readouterr()
    event = next(e for e in _access_events(captured.out) if e.get("request_id") == request_id)
    return {"response": response, "event": event}


def test_write_bearer_token_resolves_write_scope_on_actor(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
    app = create_app(settings)
    with TestClient(app) as client:
        # A real PAT minted against this app's own database: PatTokenResolver
        # verifies against `access_tokens`, so there is no literal scope selector to
        # present.
        write_token = mint_scope_tokens(app.state.services)["write"]
        result = _access_event_for(
            client, capfd, headers={"Authorization": f"Bearer {write_token}"}
        )
    assert result["response"].status_code == 200
    assert result["event"]["scope"] == "write"


def test_no_authorization_header_is_refused_with_401(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """The most consequential auth refusal (``PatTokenResolver`` docstring,
    ``src/glosswork/auth.py``): an absent ``Authorization`` header must never resolve to
    full ``admin`` access, which is what the deleted ``InterimTokenResolver`` did. It is
    refused before the route runs, with the same ``invalid_token`` envelope every surface
    shares.

    Refused before dispatch means no access log line fires for this request (the
    middleware returns directly from within ``TokenRefusedError`` handling, before the
    access-log ``finally`` block runs), so this asserts on the response alone rather
    than reusing ``_access_event_for``.
    """
    settings = Settings(data_dir=tmp_path / "data", embedding_enabled=False)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/v1/object-types")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_read_scoped_token_cannot_reach_a_write_route(
    app_services: ServiceBundle, client: TestClient, api_tokens: dict[str, str]
) -> None:
    """REST-side scope enforcement (``scopes.require_scope``) gates every write route
    exactly as the MCP catalog gates tool calls, so a read-scoped bearer token is refused
    rather than let through."""
    app_services.schema.create_object_type(
        make_actor(),
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact used by the HTTP test suite; exercises "
        "every field type the platform supports.",
        key_prefix="ART",
        fields=KITCHEN_SINK_FIELDS,
    )
    create_response = client.post("/api/v1/object-types/artifact/records", json={"title": "v1"})
    assert create_response.status_code == 200, create_response.text
    created = create_response.json()

    response = client.patch(
        f"/api/v1/records/{created['key']}",
        json={"values": {"title": "v2"}, "expected_version": 1},
        headers={"Authorization": f"Bearer {api_tokens['read']}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"


def test_bogus_bearer_token_returns_401_invalid_token(client: TestClient) -> None:
    response = client.get(
        "/api/v1/object-types", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
