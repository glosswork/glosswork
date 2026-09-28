"""A ``long_text`` field is embed-eligible whichever surface created it.

``services/schema.py``'s ``add_field`` derives ``embed`` from ``field_type == "long_text"`` but
honors an explicit value over that default. REST and MCP both omit the key unless the caller sends
one, so both inherit the default; the browser form used to always send ``embed: false``, which made
the *surface* rather than the type decide whether a field was searchable at all.

This test pins all three payload shapes against the one service rule. The third case is the payload
the schema editor now sends after the fix — that the form actually produces it is proven in
``web/src/schema-editor/SchemaEditorPage.test.tsx``'s embed cases; what is proven here is that the
payload lands embed-eligible on the server, which is the half the frontend cannot see.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.server import MCPServer

from glosswork.services import ServiceBundle
from tests.conftest import auth, make_actor
from tests.mcp_support import memory_session, structured

NOTES_DESCRIPTION = "Free-form operator notes; the narrative behind this record's state."


def _create_type(client: TestClient, token: str) -> None:
    response = client.post(
        "/api/v1/object-types",
        json={
            "key": "artifact",
            "name": "Artifact",
            "name_plural": "Artifacts",
            "description": "A tracked work artifact.",
            "key_prefix": "ART",
            "fields": [
                {
                    "key": "title",
                    "name": "Title",
                    "type": "short_text",
                    "description": "Short human-readable name for the artifact.",
                }
            ],
        },
        headers=auth(token),
    )
    assert response.status_code in (200, 201), response.text


def _add_field(client: TestClient, token: str, spec: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/v1/object-types/artifact/fields", json=spec, headers=auth(token))
    assert response.status_code in (200, 201), response.text
    return response.json()["field"]


def test_rest_omitting_embed_leaves_a_long_text_field_embed_eligible(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    _create_type(client, api_tokens["admin"])
    field = _add_field(
        client,
        api_tokens["admin"],
        {
            "key": "notes",
            "name": "Notes",
            "type": "long_text",
            "description": NOTES_DESCRIPTION,
        },
    )
    assert field["embed"] is True


def test_rest_explicit_embed_true_is_the_browser_forms_payload(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """The schema editor sends every key explicitly, ``embed`` among them."""
    _create_type(client, api_tokens["admin"])
    field = _add_field(
        client,
        api_tokens["admin"],
        {
            "key": "notes",
            "name": "Notes",
            "type": "long_text",
            "description": NOTES_DESCRIPTION,
            "config": {},
            "required": False,
            "unique": False,
            "indexed": False,
            "embed": True,
            "default": None,
        },
    )
    assert field["embed"] is True


def test_rest_explicit_embed_false_is_still_honored(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """The opt-out has to keep working: the fix restores the default, it does not remove choice."""
    _create_type(client, api_tokens["admin"])
    field = _add_field(
        client,
        api_tokens["admin"],
        {
            "key": "notes",
            "name": "Notes",
            "type": "long_text",
            "description": NOTES_DESCRIPTION,
            "embed": False,
        },
    )
    assert field["embed"] is False


@pytest.mark.anyio
async def test_mcp_omitting_embed_leaves_a_long_text_field_embed_eligible(
    mcp_server: MCPServer, services: ServiceBundle, pat: dict[str, str]
) -> None:
    services.schema.create_object_type(
        make_actor(),
        key="artifact",
        name="Artifact",
        name_plural="Artifacts",
        description="A tracked work artifact.",
        key_prefix="ART",
        fields=[
            {
                "key": "title",
                "name": "Title",
                "type": "short_text",
                "description": "Short human-readable name for the artifact.",
            }
        ],
    )
    async with memory_session(mcp_server, token=pat["admin"]) as c:
        added = structured(
            await c.call_tool(
                "add_field",
                {
                    "object_type": "artifact",
                    "key": "notes",
                    "name": "Notes",
                    "type": "long_text",
                    "description": NOTES_DESCRIPTION,
                },
            )
        )
    assert added["status"] == "applied"
    assert added["field"]["embed"] is True
