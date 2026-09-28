"""A proposal document names its target, and the list it comes in is bounded.

Three properties, and the order they are written in is the order they were measured in.

**The projection.** Without it a proposal is four UUIDs where a sentence needs four nouns:
``target_type_id``, ``target_field_id``, ``proposed_by`` and ``proposed_agent``, and nothing
client-visible resolves the first two. ``target`` is what the frontend reads instead of joining,
and it rides on **every** response that returns a proposal document, not only on the list and the
get -- ``proposal_doc`` is also the approve and the reject response, so a key added to it is added
to all five or the same document type carries different keys depending on which route produced
it.

**The cost.** One read per document, not one per proposal. "N+1 but correct" passes every
functional assertion and is invisible until it is counted, which is why ``test_principal_sidecar``
counts repository calls rather than inferring the shape from a passing read; this does the same.

**The bound.** Without a ``LIMIT``, a cursor and a published cap on ``list_proposals``, the
projection would fan out over every proposal the deployment had ever had -- the
unbounded read DD-18 forbids and DD-25 refused once already. The cap is asserted here, and so is
its presence in ``describe_capabilities``, because a cap an agent can hit that is not published is
a cap it discovers by failing.

``total_count`` is asserted on a page **shorter than the total**. That is the assertion
that would have caught the defect paging introduces: the sidebar badge reads a count, and a count
taken from the length of a page silently caps at the page size.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server import MCPServer

from glosswork.auth import PatTokenResolver
from glosswork.mcp_server import create_mcp_server
from glosswork.services import ServiceBundle
from glosswork.services.schema import DEFAULT_PROPOSAL_LIMIT, MAX_PROPOSAL_LIMIT
from tests.mcp_support import memory_session, structured

TYPE_KEY = "prop_target"

#: Exactly these five keys. ``field_*`` is null for ``delete_object_type``, which has no field.
#: Asserted by equality so a future column cannot ride along unnoticed -- the same discipline
#: ``test_principal_sidecar`` applies to its own map.
TARGET_KEYS = {
    "object_type_key",
    "object_type_name",
    "object_type_name_plural",
    "field_key",
    "field_name",
    "field_type",
}

#: Withheld by name rather than by omission: ``your_access`` is NOT on the projection. The
#: maintainer chose to keep the ``admin``-role gate on proposals rather than replace it inside a
#: redesign, so a per-row level is deliberately not knowable here. A future change that adds it
#: should have to delete this line and say why.
TARGET_WITHHELD = ("your_access", "object_type_id", "field_id")


@pytest.fixture
def seeded(client: TestClient) -> dict[str, Any]:
    """One object type with three fields and two records, so every change kind has a real,
    non-zero impact to compute rather than an empty one."""
    created = client.post(
        "/api/v1/object-types",
        json={
            "key": TYPE_KEY,
            "name": "Prospect",
            "name_plural": "Prospects",
            "description": "A fixture for the target projection.",
            "key_prefix": "PTGT",
            "fields": [
                {
                    "key": "title",
                    "name": "Title",
                    "type": "short_text",
                    "description": "The display value.",
                },
                {
                    "key": "notes",
                    "name": "Notes",
                    "type": "long_text",
                    "description": "The field the delete_field proposals target.",
                },
                {
                    "key": "stage",
                    "name": "Stage",
                    "type": "single_select",
                    "description": "The field the remove_enum_option proposals target.",
                    "config": {
                        "options": [
                            {"value": "new", "label": "New", "description": "Untouched."},
                            {"value": "won", "label": "Won", "description": "Closed won."},
                        ]
                    },
                },
            ],
        },
    )
    assert created.status_code == 200, created.text
    for i in range(2):
        row = client.post(
            f"/api/v1/object-types/{TYPE_KEY}/records",
            json={"title": f"Prospect {i}", "notes": f"note {i}", "stage": "new"},
        )
        assert row.status_code == 200, row.text
    return {"client": client}


def propose(client: TestClient, **body: Any) -> str:
    response = client.post(
        "/api/v1/schema-proposals",
        headers={"X-Agent-Label": "claude-code"},
        json={"object_type": TYPE_KEY, **body},
    )
    assert response.status_code == 200, response.text
    proposal_id: str = response.json()["proposal_id"]
    return proposal_id


def fetch(client: TestClient, proposal_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/schema-proposals/{proposal_id}")
    assert response.status_code == 200, response.text
    doc: dict[str, Any] = response.json()
    return doc


# ------------------------------------------------------------------ the projection


@pytest.mark.parametrize(
    ("change_type", "extra"),
    [
        ("delete_field", {"field_key": "notes"}),
        ("change_field_type", {"field_key": "notes", "payload": {"to_type": "short_text"}}),
        ("remove_enum_option", {"field_key": "stage", "payload": {"remove_values": ["won"]}}),
        ("tighten_constraint", {"field_key": "notes", "payload": {"constraint": "required"}}),
    ],
)
def test_a_field_change_names_its_type_and_its_field(
    seeded: dict[str, Any], change_type: str, extra: dict[str, Any]
) -> None:
    """Four of the five change kinds target a field, and all four name it."""
    client = seeded["client"]
    doc = fetch(client, propose(client, change_type=change_type, **extra))

    assert set(doc["target"]) == TARGET_KEYS
    assert doc["target"]["object_type_key"] == TYPE_KEY
    assert doc["target"]["object_type_name"] == "Prospect"
    assert doc["target"]["object_type_name_plural"] == "Prospects"
    assert doc["target"]["field_key"] == extra["field_key"]
    assert doc["target"]["field_name"] == {"notes": "Notes", "stage": "Stage"}[extra["field_key"]]
    assert doc["target"]["field_type"] in ("long_text", "single_select")
    for withheld in TARGET_WITHHELD:
        assert withheld not in doc["target"], f"{withheld} must not ride on the projection"


def test_delete_object_type_names_the_type_and_nulls_the_field(seeded: dict[str, Any]) -> None:
    """The fifth kind has no field, and says so with nulls rather than by omitting the keys:
    a document whose key set depends on its change type is one the client must branch on
    before it can read it."""
    client = seeded["client"]
    doc = fetch(client, propose(client, change_type="delete_object_type"))

    assert set(doc["target"]) == TARGET_KEYS
    assert doc["target"]["object_type_key"] == TYPE_KEY
    assert doc["target"]["object_type_name"] == "Prospect"
    assert doc["target"]["object_type_name_plural"] == "Prospects"
    assert doc["target"]["field_key"] is None
    assert doc["target"]["field_name"] is None
    assert doc["target"]["field_type"] is None


def test_target_rides_on_all_five_responses(seeded: dict[str, Any]) -> None:
    """``proposal_doc`` is the list, get, approve and reject response on
    REST and the list response on MCP. A key added to it is added to all of them."""
    client = seeded["client"]
    approved_id = propose(client, change_type="delete_field", field_key="notes")
    rejected_id = propose(
        client,
        change_type="tighten_constraint",
        field_key="title",
        payload={"constraint": "unique"},
    )

    listed = client.get("/api/v1/schema-proposals?status=pending").json()
    assert all(set(p["target"]) == TARGET_KEYS for p in listed["proposals"])
    assert set(fetch(client, approved_id)["target"]) == TARGET_KEYS

    rejected = client.post(f"/api/v1/schema-proposals/{rejected_id}/reject", json={})
    assert rejected.status_code == 200, rejected.text
    assert set(rejected.json()["target"]) == TARGET_KEYS

    approved = client.post(f"/api/v1/schema-proposals/{approved_id}/approve", json={})
    assert approved.status_code == 200, approved.text
    assert set(approved.json()["target"]) == TARGET_KEYS
    # The field really is gone: the projection names a field the change has just removed, which
    # is the one case where reading the live schema instead would render a blank.
    assert approved.json()["target"]["field_name"] == "Notes"


# ------------------------------------------------------------------ the sidecars


def test_the_list_names_the_person_and_the_agent(seeded: dict[str, Any]) -> None:
    """DD-25's two sidecars, keyed by id, as siblings of ``proposals`` rather than inside a
    proposal -- so a client that ignores them behaves exactly as it did before, and the stored
    value is still a bare id."""
    client = seeded["client"]
    propose(client, change_type="delete_field", field_key="notes")

    listed = client.get("/api/v1/schema-proposals?status=pending").json()
    proposal = listed["proposals"][0]

    assert proposal["proposed_by"] in listed["principals"]
    assert set(listed["principals"][proposal["proposed_by"]]) == {
        "display_name",
        "email",
        "is_active",
        "type",
    }
    assert proposal["proposed_agent"] in listed["agent_labels"]
    assert set(listed["agent_labels"][proposal["proposed_agent"]]) == {"label", "display_name"}
    assert listed["agent_labels"][proposal["proposed_agent"]]["label"] == "claude-code"
    for withheld in ("principal_id", "call_count", "last_seen_at", "description"):
        assert withheld not in listed["agent_labels"][proposal["proposed_agent"]]


def test_a_proposal_with_no_agent_contributes_no_label(
    client: TestClient, seeded: dict[str, Any]
) -> None:
    """docs/DESIGN.md 6.5: a missing label is a person, not a blank. A proposal raised without
    ``X-Agent-Label`` has ``proposed_agent`` null and the map simply has nothing for it, which
    is what the headline sentence falls back on."""
    response = client.post(
        "/api/v1/schema-proposals",
        json={"object_type": TYPE_KEY, "change_type": "delete_field", "field_key": "notes"},
    )
    assert response.status_code == 200, response.text

    listed = client.get("/api/v1/schema-proposals?status=pending").json()
    proposal = listed["proposals"][0]
    assert proposal["proposed_agent"] is None
    assert listed["agent_labels"] == {}
    assert proposal["proposed_by"] in listed["principals"]


def test_one_read_per_document_not_one_per_proposal(
    seeded: dict[str, Any], app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cost assertion. Five proposals must not cost five sidecar reads: "N+1 but correct"
    passes every other test in this file."""
    client = seeded["client"]
    for _ in range(5):
        propose(client, change_type="delete_field", field_key="notes")

    services: ServiceBundle = app.state.services
    repo = services.records._labels_repo()  # type: ignore[attr-defined]
    calls: list[int] = []
    original = repo.labels_by_ids

    def counted(conn: Any, ids: list[str]) -> Any:
        calls.append(len(ids))
        return original(conn, ids)

    monkeypatch.setattr(repo, "labels_by_ids", counted)
    listed = client.get("/api/v1/schema-proposals?status=pending").json()

    assert len(listed["proposals"]) == 5
    assert len(calls) == 1, f"one batched read expected, got {len(calls)}"


# ------------------------------------------------------------------ the order


def test_newest_first_is_stable_within_one_second(seeded: dict[str, Any]) -> None:
    """``proposed_at`` is second-precision, so two proposals raised in the
    same second have no defined order under ``ORDER BY proposed_at`` alone -- and "newest first"
    is then not a property anything can assert. The secondary key is what makes it one.

    **The assertion is the whole expected order, not membership.** A version ending
    ``or ids[0] in (first, second)`` is true of any two-element list containing those two ids, so
    it passes against a repository whose order is ``proposed_at`` ASCENDING, the exact opposite
    of what the screen needs. An assertion that has never failed has never been measured
    (docs/changes/README.md), and that one could not fail at all.
    """
    client = seeded["client"]
    made = [propose(client, change_type="delete_field", field_key="notes") for _ in range(4)]

    listed = client.get("/api/v1/schema-proposals?status=pending").json()
    rows = listed["proposals"]
    ids = [p["id"] for p in rows]

    # Newest first, with the id as the tiebreak: exactly the order the repository must produce.
    expected = [
        p["id"] for p in sorted(rows, key=lambda p: (p["proposed_at"], p["id"]), reverse=True)
    ]
    assert ids == expected
    assert set(ids) == set(made)
    # And deterministic across reads, which is what stops a list test being flaky rather than
    # wrong: an unstable sort passes the line above roughly half the time.
    again = client.get("/api/v1/schema-proposals?status=pending").json()
    assert [p["id"] for p in again["proposals"]] == ids


# ------------------------------------------------------------------ the bound


def test_the_list_is_bounded_and_pages(seeded: dict[str, Any]) -> None:
    """The page is a page, the cursor yields every proposal exactly once, and
    nothing is repeated across the boundary."""
    client = seeded["client"]
    made = {propose(client, change_type="delete_field", field_key="notes") for _ in range(6)}

    first = client.get("/api/v1/schema-proposals?status=pending&limit=4").json()
    assert len(first["proposals"]) == 4
    assert first["next_cursor"] is not None

    second = client.get(
        f"/api/v1/schema-proposals?status=pending&limit=4&cursor={first['next_cursor']}"
    ).json()
    assert len(second["proposals"]) == 2
    assert second["next_cursor"] is None

    seen = [p["id"] for p in first["proposals"]] + [p["id"] for p in second["proposals"]]
    assert len(seen) == len(set(seen)) == 6
    assert set(seen) == made


def test_total_count_is_the_total_and_not_the_page(seeded: dict[str, Any]) -> None:
    """The sidebar badge reads a count. Read off the page's length it silently caps at the page
    size, which is a wrong number rendered confidently -- exactly what answering ``null``
    rather than ``0`` for a count that is not known avoids."""
    client = seeded["client"]
    for _ in range(6):
        propose(client, change_type="delete_field", field_key="notes")

    page = client.get("/api/v1/schema-proposals?status=pending&limit=2").json()
    assert len(page["proposals"]) == 2
    assert page["total_count"] == 6


def test_a_limit_over_the_cap_is_a_4xx_naming_the_bound(seeded: dict[str, Any]) -> None:
    """DD-19: client-correctable input is a 4xx naming what to fix, never a 500."""
    client = seeded["client"]
    response = client.get(f"/api/v1/schema-proposals?limit={MAX_PROPOSAL_LIMIT + 1}")
    assert response.status_code == 422, response.text
    assert "MAX_PROPOSAL_LIMIT" in response.text


@pytest.mark.anyio
async def test_the_cap_is_published(mcp: MCPServer, api_tokens: dict[str, str]) -> None:
    """A cap an agent can hit that is not published is a cap it discovers by failing.

    Read through the MCP tool, not ``GET /api/v1/capabilities``, which is not a route --
    ``describe_capabilities`` is an MCP tool (DD-30: the manual is a tool result, not a page). A
    REST call fails with a JSON decode error rather than a 404, which is AGENTS.md's "an unknown
    path under /api/v1/ is not a 404" trap arriving in person.
    """
    async with memory_session(mcp, token=api_tokens["read"]) as session:
        limits = structured(await session.call_tool("describe_capabilities", {}))["limits"]

    assert limits["proposal_default_limit"] == DEFAULT_PROPOSAL_LIMIT
    assert limits["proposal_max_limit"] == MAX_PROPOSAL_LIMIT


# ------------------------------------------------------------------ parity


@pytest.fixture
def mcp(app: FastAPI, client: TestClient) -> MCPServer:
    services: ServiceBundle = app.state.services
    return create_mcp_server(lambda: services, PatTokenResolver(lambda: services))


@pytest.mark.anyio
async def test_mcp_returns_the_same_document_as_rest(
    seeded: dict[str, Any], mcp: MCPServer, api_tokens: dict[str, str]
) -> None:
    """``{"proposals": [...]}`` is one shared envelope, not a literal at two call sites beside a
    shared ``proposal_doc``, which would add the sidecars twice. One envelope is what makes this an
    assertion rather than a coincidence."""
    client = seeded["client"]
    propose(client, change_type="delete_field", field_key="notes")

    rest = client.get("/api/v1/schema-proposals?status=pending").json()
    async with memory_session(mcp, token=api_tokens["admin"]) as session:
        over_mcp = structured(
            await session.call_tool("list_schema_proposals", {"status": "pending"})
        )

    assert set(over_mcp) == set(rest)
    assert over_mcp["proposals"] == rest["proposals"]
    assert over_mcp["principals"] == rest["principals"]
    assert over_mcp["agent_labels"] == rest["agent_labels"]
    assert over_mcp["total_count"] == rest["total_count"]
