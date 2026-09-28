"""The sentence that tells an agent where a human should go.

``APPROVAL_MESSAGE`` once ended "in the Glosswork UI under Settings > Schema Proposals". Moving
proposals to ``/inbox`` made that false -- and **nothing in the suite noticed**, because no test
pinned the string. Measured before the move: ``grep -rn "Settings > Schema
Proposals" tests/ container_tests/ web/`` returned nothing at all, so the only two occurrences
were the message itself and the example in ``docs/MCP_TOOLS.md``.

This is the only place the product tells an agent where to send a person, and an agent relaying
it is relaying it verbatim, so it is pinned to the route it names rather than to a screen's name.
The next change that moves this screen fails here instead of shipping a dead end.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from glosswork.envelopes import APPROVAL_MESSAGE

REPO_ROOT = Path(__file__).resolve().parents[1]

# This file reads docs/MCP_TOOLS.md and web/src/App.tsx from disk, so it belongs in the lane CI
# runs on a documentation-only change -- which is exactly the change that would otherwise edit
# the quoted example out of sync with the code.
pytestmark = pytest.mark.structural


def test_the_message_names_the_proposal_s_own_url() -> None:
    """Not just the screen. Every proposal has a URL precisely so this sentence can
    point at the one the agent is talking about, rather than telling a person to go and find it
    among however many others are waiting."""
    rendered = APPROVAL_MESSAGE.format(proposal_id="prop_9f2c")

    assert "/inbox/prop_9f2c" in rendered
    assert "Settings" not in rendered
    assert "Schema Proposals" not in rendered


def test_the_route_it_names_is_the_route_the_frontend_serves() -> None:
    """The pin that makes this more than a spelling check.

    A message naming a URL the SPA does not route is worse than one naming a screen, because it
    looks actionable. ``App.tsx`` is read rather than requested: an unknown path under the SPA
    answers 200 with HTML (AGENTS.md, Traps), so a status code could not tell us anything here.
    """
    app_source = (REPO_ROOT / "web" / "src" / "App.tsx").read_text()

    assert 'path="/inbox/:proposalId"' in app_source
    assert 'path="/inbox"' in app_source


def test_the_documented_example_matches_the_code(client: TestClient) -> None:
    """docs/MCP_TOOLS.md quotes this message in full. A doc example that drifts from the string
    it quotes is how the previous version survived: the copy was wrong in two places and neither
    was checked against the other."""
    documented = (REPO_ROOT / "docs" / "MCP_TOOLS.md").read_text()

    assert APPROVAL_MESSAGE.format(proposal_id="prop_9f2c").split(" Call ")[0] in documented


def test_a_real_proposal_returns_it(client: TestClient) -> None:
    """End to end, because the string being right in ``envelopes.py`` and the string reaching
    the agent are two different claims."""
    client.post(
        "/api/v1/object-types",
        json={
            "key": "msg_probe",
            "name": "Probe",
            "name_plural": "Probes",
            "description": "A fixture for the approval message.",
            "key_prefix": "MSGP",
            "fields": [
                {"key": "note", "name": "Note", "type": "long_text", "description": "d"},
            ],
        },
    )
    response = client.post(
        "/api/v1/schema-proposals",
        json={"change_type": "delete_field", "object_type": "msg_probe", "field_key": "note"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert f"/inbox/{body['proposal_id']}" in body["message"]
