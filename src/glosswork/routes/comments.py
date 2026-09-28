"""Comment routes (FR-C1 through FR-C8): thin adapters over ``CommentService``.

No business logic lives here (DD-3) — every route parses the request, calls one
``CommentService`` method, and shapes the response through the envelopes shared
with the MCP tools. ``GlossworkError`` subclasses are never caught here; the
single exception handler in ``app.py`` maps them onto the error envelope (FR-A4).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.envelopes import comment_doc, comments_page_doc
from glosswork.scopes import require_scope
from glosswork.services import ServiceBundle
from glosswork.services.comments import DEFAULT_COMMENT_LIMIT

router = APIRouter(prefix="/api/v1", tags=["comments"])

Actor = Annotated[ActorContext, Depends(get_actor)]
Services = Annotated[ServiceBundle, Depends(get_services)]


class CommentBody(BaseModel):
    body: str = Field(description="Markdown comment body (FR-C3).")


@router.post("/records/{ref}/comments", dependencies=[require_scope("write")])
def add_comment(
    ref: str,
    payload: CommentBody,
    actor: Actor,
    services: Services,
) -> dict[str, Any]:
    comment = services.comments.add_comment(actor, ref, payload.body)
    return comment_doc(comment)


@router.get("/records/{ref}/comments", dependencies=[require_scope("read")])
def list_comments(
    ref: str,
    actor: Actor,
    services: Services,
    limit: int = DEFAULT_COMMENT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    page = services.comments.list_comments_page(actor, ref, limit=limit, cursor=cursor)
    return comments_page_doc(page)


@router.patch("/comments/{comment_id}", dependencies=[require_scope("write")])
def update_comment(
    comment_id: str,
    payload: CommentBody,
    actor: Actor,
    services: Services,
) -> dict[str, Any]:
    comment = services.comments.update_comment(actor, comment_id, payload.body)
    return comment_doc(comment)


@router.delete("/comments/{comment_id}", dependencies=[require_scope("write")])
def delete_comment(
    comment_id: str,
    actor: Actor,
    services: Services,
) -> dict[str, Any]:
    comment = services.comments.delete_comment(actor, comment_id)
    return comment_doc(comment)
