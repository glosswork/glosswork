"""Search (FR-Q1 through FR-Q5, FR-A1). One route, ``read``-scoped, parsing a
body and shaping the service's result through the envelope the MCP ``search`` tool
also uses (DD-3); no ranking, scoping, or filter logic lives here.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.envelopes import search_result_doc
from glosswork.scopes import require_scope
from glosswork.services import ServiceBundle
from glosswork.services.search import DEFAULT_MODE, DEFAULT_SEARCH_LIMIT

router = APIRouter(prefix="/api/v1", tags=["search"])

ActorDep = Annotated[ActorContext, Depends(get_actor)]
ServicesDep = Annotated[ServiceBundle, Depends(get_services)]


class SearchBody(BaseModel):
    query: str
    object_types: list[str] | None = None
    filter: dict[str, Any] | None = None
    mode: str = DEFAULT_MODE
    limit: int = DEFAULT_SEARCH_LIMIT


@router.post("/search", dependencies=[require_scope("read")])
def search(body: SearchBody, actor: ActorDep, services: ServicesDep) -> dict[str, Any]:
    """Hybrid, semantic, or keyword search across object types (docs/MCP_TOOLS.md 5.1)."""
    result = services.search.search(
        actor,
        body.query,
        object_types=body.object_types,
        filter=body.filter,
        mode=body.mode,
        limit=body.limit,
    )
    return search_result_doc(result)
