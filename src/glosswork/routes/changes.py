"""Pull-based change feed route (FR-M7, docs/MCP_TOOLS.md sections 5.1 and 8).

Thin adapter over :class:`~glosswork.services.changes.ChangeFeedService`: parses
query params, calls the service, and shapes the response through the envelope
shared with the MCP tool. All cursor and scoping logic lives in the service (DD-3);
``GlossworkError`` subclasses are left to propagate to the single handler
registered in ``app.py`` (DD-2/DD-3).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.envelopes import changes_doc
from glosswork.scopes import require_scope
from glosswork.services import ServiceBundle
from glosswork.services.changes import DEFAULT_LIMIT

router = APIRouter(prefix="/api/v1", tags=["changes"])


@router.get("/changes", dependencies=[require_scope("read")])
def list_changes_since(
    cursor: int | None = Query(default=None),
    object_types: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT),
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    object_type_keys: list[str] | None = None
    if object_types is not None:
        object_type_keys = [key.strip() for key in object_types.split(",") if key.strip()]

    result = services.changes.list_changes_since(
        actor, cursor=cursor, object_type_keys=object_type_keys, limit=limit
    )
    return changes_doc(result)
