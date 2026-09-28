"""Search index status (FR-Q7, FR-P5).

Administrator-scoped. The status read is how the container proof observes indexing at
all: it polls ``pending_jobs`` to zero and then asserts an exact ``indexed_chunks``. The
re-index trigger pairs with it (FR-Q9).

Both are REST/UI-only capabilities, like proposal approval: docs/MCP_TOOLS.md section 8
records them as having no MCP tool. An agent reads ``index_lag`` on every ``search``
response instead, and a full re-index is an operator action with a real CPU cost.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.envelopes import search_index_status_doc
from glosswork.scopes import require_role, require_scope
from glosswork.services import ServiceBundle

router = APIRouter(prefix="/api/v1", tags=["search-index"])

ActorDep = Annotated[ActorContext, Depends(get_actor)]
ServicesDep = Annotated[ServiceBundle, Depends(get_services)]


@router.get("/admin/search-index", dependencies=[require_scope("admin"), require_role("admin")])
def get_search_index_status(services: ServicesDep) -> dict[str, Any]:
    """Queue depth, failures, chunk counts, and which model produced them."""
    return search_index_status_doc(services.search_index.status())


@router.post(
    "/admin/search-index/reindex",
    dependencies=[require_scope("admin"), require_role("admin")],
    status_code=202,
)
def reindex_search_index(actor: ActorDep, services: ServicesDep) -> dict[str, int]:
    """Enqueue every indexed source for re-embedding (FR-Q9); ``202`` because the
    worker drains the queue afterwards, and the status read above reports progress."""
    return {"enqueued": services.search_index.reindex(actor)}
