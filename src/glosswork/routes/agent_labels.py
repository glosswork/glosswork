"""Personal and admin agent-label routes (FR-I6, FR-I7).

Labels themselves auto-register at the service layer on first tool/API use
(``AgentLabelService.register_use``); these routes only expose the read and the
rename/describe update over that existing registry — no new write path.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.envelopes import agent_label_directory_doc
from glosswork.scopes import require_role, require_scope
from glosswork.serializers import agent_label_doc
from glosswork.services import ServiceBundle
from glosswork.services.principals import DIRECTORY_DEFAULT_LIMIT

router = APIRouter(prefix="/api/v1", tags=["agent-labels"])

ActorDep = Annotated[ActorContext, Depends(get_actor)]
ServicesDep = Annotated[ServiceBundle, Depends(get_services)]


class UpdateAgentLabelBody(BaseModel):
    display_name: str | None = None
    description: str | None = None


@router.get("/agent-labels/directory", dependencies=[require_scope("read")])
def agent_label_directory(
    actor: ActorDep,
    services: ServicesDep,
    q: str | None = None,
    limit: int = DIRECTORY_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """The agent picker's options (FR-U8): every label this caller could meet on
    ``/activity``, so filtering by an agent does not require knowing a UUID.

    ``read`` is the right gate, argued rather than assumed, and the argument is about the
    **rows**, not the projection. ``AgentLabelService.search_labels`` scopes the result to
    the labels appearing on audit events this caller may read, using the same predicate
    ``AuditService.search`` applies, so this route returns exactly what an unfiltered
    ``GET /audit-events`` walk by the same caller would already reveal. An unscoped list
    of ``agent_labels`` would not: it would publish labels whose only activity is on
    object types the caller is closed out of, which is a disclosure nothing here decides
    to make.

    **Registered before the parameterised route below.** Nothing collides today -- that
    one is a ``PATCH`` and FastAPI matches on method as well as path -- so this is a note
    rather than a pin, placed against a future ``GET /agent-labels/{id}``.
    """
    labels = services.agent_labels.search_labels(actor, q=q, limit=limit)
    return {"agent_labels": [agent_label_directory_doc(label) for label in labels]}


@router.get("/agent-labels", dependencies=[require_scope("read")])
def list_agent_labels(
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    """The caller's own labels (FR-I6, personal settings)."""
    labels = services.agent_labels.list_labels(actor.principal_id)
    return {"labels": [agent_label_doc(label) for label in labels]}


@router.get("/admin/agent-labels", dependencies=[require_scope("admin"), require_role("admin")])
def list_all_agent_labels(services: ServicesDep) -> dict[str, Any]:
    """The administrator cross-user view (FR-I7): every principal's labels.

    A separate route rather than an ``?all=true`` parameter on the personal one. A
    single route could not raise its own requirement from ``read``
    to ``admin`` when that parameter is present without inspecting the caller's
    credential inside the handler, which is exactly what declaring scope once at
    registration exists to prevent. Two capabilities, two declarations, one
    enforcement point.
    """
    labels = services.agent_labels.list_labels(None)
    return {"labels": [agent_label_doc(label) for label in labels]}


@router.patch("/agent-labels/{label_id}", dependencies=[require_scope("write")])
def update_agent_label(
    label_id: str,
    body: UpdateAgentLabelBody,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    label = services.agent_labels.update_label(
        actor.principal_id,
        label_id,
        display_name=body.display_name,
        description=body.description,
    )
    return agent_label_doc(label)
