"""The agent label reaches the audit trail from REST, not only MCP.

``X-Agent-Label`` is honored on ``/api/v1/*`` for a bearer (PAT) credential, on every
request that carries it -- not only writes -- with the same precedence and
auto-registration rule MCP already applies (FR-M5, FR-I6, FR-I7). A session-
authenticated write ignores the header, deliberately: a cookie is a browser at a
keyboard.

The REST create-record body is the bare values object, not ``{"values": {...}}`` --
the wrapped form returns 400 ``unknown_field`` and creates nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from glosswork.app import create_app
from glosswork.auth import PatTokenResolver
from glosswork.config import Settings
from glosswork.cookies import CSRF_HEADER_NAME
from glosswork.mcp_server import create_mcp_server
from glosswork.services import ServiceBundle
from tests.conftest import auth, make_actor
from tests.mcp_support import memory_session, seed_task_type

PASSWORD = "correct-horse-battery-staple"


def _create_prospect_type(client: TestClient) -> None:
    """A single-field object type over the caller's default (admin) credential."""
    response = client.post(
        "/api/v1/object-types",
        json={
            "key": "prospect",
            "name": "Prospect",
            "name_plural": "Prospects",
            "description": "A prospective customer being pursued by sales.",
            "key_prefix": "PROS",
            "fields": [
                {
                    "key": "company",
                    "name": "Company",
                    "type": "short_text",
                    "description": "The prospect's company name.",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text


# ------------------------------------------------------------------- a PAT write


def test_pat_write_is_attributed(client: TestClient, api_tokens: dict[str, str]) -> None:
    """A REST write carrying ``Authorization: Bearer <pat>`` plus ``X-Agent-Label``
    attributes its audit row to a registry row for that label under that token's
    principal -- read back through the same run, never compared to a constant."""
    _create_prospect_type(client)
    write_token = api_tokens["write"]

    me = client.get("/api/v1/me", headers=auth(write_token))
    assert me.status_code == 200, me.text
    token_principal_id = me.json()["id"]

    response = client.post(
        "/api/v1/object-types/prospect/records",
        # The bare values object, not {"values": {...}}.
        json={"company": "Example Co"},
        headers={**auth(write_token), "X-Agent-Label": "sales-agent"},
    )
    assert response.status_code == 200, response.text
    key = response.json()["key"]

    audit = client.get(f"/api/v1/audit-events?record={key}")
    assert audit.status_code == 200, audit.text
    events = audit.json()["events"]
    assert events

    label_ids = {e["agent_label_id"] for e in events}
    assert label_ids != {None}, "the labeled write left every audit row unattributed"
    assert len(label_ids) == 1
    label_id = label_ids.pop()

    labels_response = client.get("/api/v1/agent-labels", headers=auth(write_token))
    assert labels_response.status_code == 200, labels_response.text
    by_id = {row["id"]: row for row in labels_response.json()["labels"]}
    assert label_id in by_id, by_id
    assert by_id[label_id]["label"] == "sales-agent"
    assert by_id[label_id]["principal_id"] == token_principal_id


# -------------------------------------------------------------- one registry row


@pytest.mark.anyio
async def test_one_registry_row_across_surfaces(
    client: TestClient, app_services: ServiceBundle, api_tokens: dict[str, str]
) -> None:
    """The same header string over REST and over MCP, under the same principal, is one
    registry row -- proof the two surfaces share a resolver rather than each growing its
    own (no existing fixture pair shares one database, so this test builds its own MCP
    server over the REST app's own service bundle)."""
    seed_task_type(app_services)
    header_label = "cross-surface-bot"
    write_token = api_tokens["write"]

    rest_response = client.post(
        "/api/v1/object-types/task/records",
        json={"title": "from rest"},
        headers={**auth(write_token), "X-Agent-Label": header_label},
    )
    assert rest_response.status_code == 200, rest_response.text
    record_key = rest_response.json()["key"]

    mcp_server = create_mcp_server(lambda: app_services, PatTokenResolver(lambda: app_services))
    async with memory_session(mcp_server, token=write_token, agent_label=header_label) as session:
        result = await session.call_tool("add_comment", {"record": record_key, "body": "from mcp"})
    assert not result.is_error, result.content

    matching = [row for row in app_services.agent_labels.list_labels() if row.label == header_label]
    assert len(matching) == 1, matching
    assert matching[0].call_count == 2


# --------------------------------------------------------------- a session write


def test_session_authenticated_write_stays_unlabeled(tmp_path: Path) -> None:
    """A cookie is a browser at a keyboard: a session write carrying
    ``X-Agent-Label`` is attributed to no label at all, and ``surface`` is ``"ui"``."""
    settings = Settings(data_dir=tmp_path / "data", cookie_secure=False, embedding_enabled=False)
    app = create_app(settings)
    with TestClient(app) as session_client:
        services: ServiceBundle = app.state.services
        services.principals.create_user(
            make_actor(),
            email="admin-session@example.com",
            display_name="Admin Session",
            role="admin",
            password=PASSWORD,
        )
        services.schema.create_object_type(
            make_actor(),
            key="task",
            name="Task",
            name_plural="Tasks",
            description="A unit of work tracked to completion.",
            key_prefix="TSK",
            fields=[
                {
                    "key": "title",
                    "name": "Title",
                    "type": "short_text",
                    "description": "Short imperative summary of the work.",
                }
            ],
        )

        login = session_client.post(
            "/api/v1/auth/login",
            json={"email": "admin-session@example.com", "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        csrf = session_client.cookies.get("gw_csrf")

        response = session_client.post(
            "/api/v1/object-types/task/records",
            json={"title": "via session"},
            headers={CSRF_HEADER_NAME: csrf, "X-Agent-Label": "should-be-ignored"},
        )
        assert response.status_code == 200, response.text
        key = response.json()["key"]

        audit = session_client.get(f"/api/v1/audit-events?record={key}")
        assert audit.status_code == 200, audit.text
        events = audit.json()["events"]
        assert events
        assert all(e["agent_label_id"] is None for e in events)
        assert all(e["surface"] == "ui" for e in events)


# ------------------------------------------------------------------------- reads


def test_read_counts_labeled_reads_register_and_unlabeled_reads_do_not(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """Two labeled ``GET``s and one unlabeled one: the label's ``call_count`` is 2,
    and the unlabeled request registers no row at all (parity with MCP's read
    registration, FR-I7)."""
    read_token = api_tokens["read"]
    label = "reader-bot"

    for _ in range(2):
        response = client.get(
            "/api/v1/object-types", headers={**auth(read_token), "X-Agent-Label": label}
        )
        assert response.status_code == 200, response.text

    unlabeled = client.get("/api/v1/object-types", headers=auth(read_token))
    assert unlabeled.status_code == 200, unlabeled.text

    labels_response = client.get("/api/v1/agent-labels", headers=auth(read_token))
    assert labels_response.status_code == 200, labels_response.text
    labels: list[dict[str, Any]] = labels_response.json()["labels"]
    assert len(labels) == 1, labels
    assert labels[0]["label"] == label
    assert labels[0]["call_count"] == 2


# ------------------------------------------------------------- a malformed label


def test_malformed_label_refuses_the_request_and_writes_nothing(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """A label over ``MAX_LABEL_LENGTH`` (200) is refused as ``validation_failed``,
    in the project envelope, and the record it would have attributed is never
    created."""
    _create_prospect_type(client)
    write_token = api_tokens["write"]
    too_long = "x" * 201

    response = client.post(
        "/api/v1/object-types/prospect/records",
        json={"company": "Should not persist"},
        headers={**auth(write_token), "X-Agent-Label": too_long},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "validation_failed"

    listing = client.post("/api/v1/object-types/prospect/query", json={})
    assert listing.status_code == 200, listing.text
    assert listing.json()["total_count"] == 0


# ------------------------------------------------------ the token's own label

TOKEN_LABEL = "token-borne-bot"


def _labeled_token(services: ServiceBundle, label: str = TOKEN_LABEL) -> str:
    """A PAT minted for one named tool, carrying that tool's agent label."""
    return services.tokens.mint(
        make_actor(), name=f"for-{label}", scope="write", agent_label=label
    ).plaintext


def test_token_label_attributes_an_unlabeled_rest_call(
    client: TestClient, app_services: ServiceBundle
) -> None:
    """A token minted for one named tool attributes every call it makes, with no
    ``X-Agent-Label`` anywhere. The header is precisely what Claude's connector dialog
    cannot send, which is the whole reason the label moves onto the token."""
    _create_prospect_type(client)
    token = _labeled_token(app_services)

    response = client.post(
        "/api/v1/object-types/prospect/records",
        json={"company": "Token Labelled Co"},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    key = response.json()["key"]

    audit = client.get(f"/api/v1/audit-events?record={key}")
    assert audit.status_code == 200, audit.text
    events = audit.json()["events"]
    assert events
    label_ids = {e["agent_label_id"] for e in events}
    assert label_ids != {None}, "the token's own label left every audit row unattributed"
    assert len(label_ids) == 1
    assert app_services.agent_labels.get_label(label_ids.pop()).label == TOKEN_LABEL


def test_the_header_overrides_the_token_label_on_rest(
    client: TestClient, app_services: ServiceBundle
) -> None:
    """FR-M5's precedence with a third source under it: a harness that *can* send the
    header keeps its override, which is what the item requires."""
    _create_prospect_type(client)
    token = _labeled_token(app_services)

    response = client.post(
        "/api/v1/object-types/prospect/records",
        json={"company": "Header Wins Co"},
        headers={**auth(token), "X-Agent-Label": "header-label"},
    )
    assert response.status_code == 200, response.text
    key = response.json()["key"]

    audit = client.get(f"/api/v1/audit-events?record={key}")
    assert audit.status_code == 200, audit.text
    events = audit.json()["events"]
    assert events
    labels = {app_services.agent_labels.get_label(e["agent_label_id"]).label for e in events}
    assert labels == {"header-label"}


def test_an_unlabeled_token_and_no_header_is_attributed_to_nobody(
    client: TestClient, app_services: ServiceBundle
) -> None:
    """The "behaves exactly as today" clause at the REST edge: a token minted with no
    label registers nothing and attributes nothing."""
    _create_prospect_type(client)
    token = app_services.tokens.mint(make_actor(), name="unlabelled", scope="write").plaintext

    response = client.post(
        "/api/v1/object-types/prospect/records",
        json={"company": "Anonymous Co"},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    key = response.json()["key"]

    audit = client.get(f"/api/v1/audit-events?record={key}")
    assert audit.status_code == 200, audit.text
    events = audit.json()["events"]
    assert events
    assert all(e["agent_label_id"] is None for e in events)
    assert app_services.agent_labels.list_labels() == []
