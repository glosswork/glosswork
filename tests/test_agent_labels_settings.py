"""Settings backend: ``AgentLabelService.update_label`` (FR-I6, FR-I7).

Label auto-registration itself is already covered by the MCP suite
(tests/test_mcp_agent_label.py); this covers the new rename/describe update.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from glosswork.actor import BOOTSTRAP_PRINCIPAL_ID
from glosswork.db import Database
from glosswork.errors import NotFoundError
from glosswork.services import ServiceBundle


def _insert_principal(db: Database) -> str:
    principal_id = str(uuid.uuid4())
    with db.write() as conn:
        conn.execute(
            text(
                "INSERT INTO principals (id, type, display_name, email, is_active, "
                "created_at) VALUES (:id, 'user', 'Other', 'other@example.com', 1, "
                "'2026-01-01T00:00:00Z')"
            ),
            {"id": principal_id},
        )
    return principal_id


def test_update_label_sets_display_name_description_and_marks_verified(
    services: ServiceBundle,
) -> None:
    label = services.agent_labels.register_use(BOOTSTRAP_PRINCIPAL_ID, "claude-code-planner")
    assert label.verified is False

    updated = services.agent_labels.update_label(
        BOOTSTRAP_PRINCIPAL_ID,
        label.id,
        display_name="Planner",
        description="Drafts and updates initiative plans.",
    )
    assert updated.display_name == "Planner"
    assert updated.description == "Drafts and updates initiative plans."
    assert updated.verified is True


def test_update_label_belonging_to_another_principal_is_not_found(
    services: ServiceBundle, db: Database
) -> None:
    other_principal = _insert_principal(db)
    label = services.agent_labels.register_use(other_principal, "some-agent")
    with pytest.raises(NotFoundError):
        services.agent_labels.update_label(
            BOOTSTRAP_PRINCIPAL_ID, label.id, display_name="Hijacked"
        )


def test_list_labels_scoped_vs_cross_user(services: ServiceBundle, db: Database) -> None:
    other_principal = _insert_principal(db)
    services.agent_labels.register_use(BOOTSTRAP_PRINCIPAL_ID, "agent-a")
    services.agent_labels.register_use(other_principal, "agent-b")

    own = services.agent_labels.list_labels(BOOTSTRAP_PRINCIPAL_ID)
    assert {label.label for label in own} == {"agent-a"}

    everyone = services.agent_labels.list_labels(None)
    assert {label.label for label in everyone} == {"agent-a", "agent-b"}
