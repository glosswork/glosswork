"""The agent-label directory, and the one property that makes it safe.

``/activity`` filters by agent, and a filter that will not show you its options is a
filter nobody can use: the screen it replaces asked for ``agent_label_id`` as free text,
a UUID no person knows. A picker needs a list, and without this route no ``read``-scoped
route serves one (``GET /agent-labels`` is the caller's own; ``GET /admin/agent-labels``
declares the ``admin`` role).

**The property under test is not "the route answers".** ``AuditService.search`` narrows a
restricted caller to ``accessible_type_ids(conn, actor, "read")``, so a member only ever
sees agent labels attached to events on object types they hold ``read`` on. A directory
built on ``list_labels(None)`` -- ``SELECT * FROM agent_labels``, no predicate -- would
publish every label string in the deployment, including labels whose activity is confined
to types the caller is closed out of. That is a disclosure the audit trail does not make,
and it is what ``test_a_member_is_shown_no_label_their_audit_search_would_hide`` exists
for. Everything else here is projection and bounds.

``tests/test_access_completeness.py`` catches it only because ``AgentLabelService`` is in
``ENFORCING_SERVICES``: excluded on the ground that it "touches no object type at all", as it
could easily be, it would not.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from glosswork.actor import ActorContext
from glosswork.services import ServiceBundle
from glosswork.services.principals import DIRECTORY_MAX_LIMIT
from tests.conftest import make_actor

# The three keys of ``envelopes.agent_label_directory_doc``, written out rather than
# imported, so a change to that function is a diff here too.
DIRECTORY_KEYS = {"id", "label", "display_name"}

# Withheld by name. All six are on ``agent_label_doc`` and all six are FR-I7 telemetry,
# which is what ``GET /admin/agent-labels`` is for and what keeps its admin gate.
WITHHELD_KEYS = (
    "principal_id",
    "call_count",
    "first_seen_at",
    "last_seen_at",
    "verified",
    "description",
)


def _actor(principal_id: str, *, agent_label_id: str | None = None) -> ActorContext:
    return ActorContext(
        principal_id=principal_id,
        principal_type="user",
        agent_label_id=agent_label_id,
        auth_method="pat",
        surface="api",
        request_id=str(uuid.uuid4()),
        scope="read",
    )


@pytest.fixture
def two_types_one_hidden(services: ServiceBundle) -> dict[str, Any]:
    """Two object types, a member who can read exactly one of them, and one agent label
    whose only activity is on each.

    The hidden type is closed by default (every object type is), so the member needs a
    grant on the visible one and nothing at all on the other.
    """
    visible = services.schema.create_object_type(
        make_actor(),
        key="visible",
        name="Visible",
        name_plural="Visibles",
        description="The type the member holds read on.",
        key_prefix="VIS",
    )
    hidden = services.schema.create_object_type(
        make_actor(),
        key="hidden",
        name="Hidden",
        name_plural="Hiddens",
        description="The type the member is closed out of.",
        key_prefix="HID",
    )
    member = services.principals.create_user(
        make_actor(), email="member@example.com", display_name="Member", role="member"
    )
    services.access.grant(make_actor(), "visible", member.id, "read")

    seen_label = services.agent_labels.register_use(member.id, "seen-agent")
    hidden_label = services.agent_labels.register_use(member.id, "hidden-agent")

    # One record on each type, each written under its own agent label, so the only thing
    # separating the two labels is the object type of the events that carry them.
    #
    # Written by the bootstrap actor rather than by the member: the access filter keys on
    # the event's `object_type_id` and never on who wrote it, and making the member hold
    # `write` on one type would be a second variable in a test about reading.
    services.records.create_record(
        replace(make_actor(), agent_label_id=seen_label.id), "visible", {}
    )
    services.records.create_record(
        replace(make_actor(), agent_label_id=hidden_label.id), "hidden", {}
    )
    return {
        "member": member,
        "seen_label": seen_label,
        "hidden_label": hidden_label,
        "visible": visible,
        "hidden": hidden,
    }


def test_a_member_is_shown_no_label_their_audit_search_would_hide(
    services: ServiceBundle, two_types_one_hidden: dict[str, Any]
) -> None:
    """The whole point of the route, and the case a naive implementation fails.

    ``list_labels(None)`` returns both labels. The directory must return only the one
    attached to events on a type this member holds ``read`` on -- which is exactly the
    set their own ``/audit-events`` walk would reveal.
    """
    member = two_types_one_hidden["member"]
    labels = services.agent_labels.search_labels(_actor(member.id), q=None, limit=50)
    names = {row.label for row in labels}

    assert "seen-agent" in names
    assert "hidden-agent" not in names


def test_an_unrestricted_admin_is_shown_both(
    services: ServiceBundle, two_types_one_hidden: dict[str, Any]
) -> None:
    """The converse, so the case above cannot be satisfied by a directory that returns
    nothing at all."""
    labels = services.agent_labels.search_labels(make_actor(), q=None, limit=50)
    names = {row.label for row in labels}

    assert {"seen-agent", "hidden-agent"} <= names


def test_the_route_serves_a_read_scoped_caller_the_three_keys(
    client: TestClient, api_tokens: dict[str, str]
) -> None:
    """Asserted on the BODY, never on the status code alone: an unknown path under
    ``/api/v1/`` returns ``200 text/html`` from the SPA's static fallback whenever
    ``web/dist`` exists, so a status assertion passes against a route nobody wrote.

    Seeded over HTTP rather than through the ``services`` fixture, which builds its own
    database: the ``client`` app has a different one, and a fixture written against the
    wrong database returns an empty list that looks exactly like a working route with
    nothing to say. `X-Agent-Label` auto-registers the label at the REST edge for a bearer
    credential on any request (FR-I6, DD-17), and the write is what puts it on an audit
    event, which is what the directory reads.
    """
    created = client.post(
        "/api/v1/object-types",
        json={
            "key": "widget",
            "name": "Widget",
            "name_plural": "Widgets",
            "description": "The type whose audit events carry the seeded label.",
            "key_prefix": "WID",
        },
        headers={"Authorization": f"Bearer {api_tokens['admin']}"},
    )
    assert created.status_code == 200, created.text
    written = client.post(
        "/api/v1/object-types/widget/records",
        json={},
        headers={
            "Authorization": f"Bearer {api_tokens['write']}",
            "X-Agent-Label": "seen-agent",
        },
    )
    assert written.status_code == 200, written.text

    response = client.get(
        "/api/v1/agent-labels/directory",
        headers={"Authorization": f"Bearer {api_tokens['read']}"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    body = response.json()
    assert "agent_labels" in body
    assert body["agent_labels"], "the fixture registered two labels; the route returned none"
    for entry in body["agent_labels"]:
        assert set(entry) == DIRECTORY_KEYS
        for withheld in WITHHELD_KEYS:
            assert withheld not in entry


def test_the_limit_is_clamped_at_the_directory_maximum(
    services: ServiceBundle, two_types_one_hidden: dict[str, Any]
) -> None:
    """The bound is `DIRECTORY_MAX_LIMIT`, reused rather than redeclared: a second pair of
    constants for one concept is the drift DD-18 is about."""
    asked = DIRECTORY_MAX_LIMIT * 10
    labels = services.agent_labels.search_labels(make_actor(), q=None, limit=asked)
    assert len(labels) <= DIRECTORY_MAX_LIMIT


def test_the_query_narrows_by_label_text(
    services: ServiceBundle, two_types_one_hidden: dict[str, Any]
) -> None:
    labels = services.agent_labels.search_labels(make_actor(), q="hidden", limit=50)
    assert {row.label for row in labels} == {"hidden-agent"}
