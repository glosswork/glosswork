"""Saved view routes (FR-U3): thin adapters over ``SavedViewService``.

No business logic lives here (DD-3) — every route parses the request, calls one
``SavedViewService`` method, and shapes the response through the envelopes shared
with the MCP tools. ``GlossworkError`` subclasses are never caught here; the
single exception handler in ``app.py`` maps them onto the error envelope (FR-A4).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.scopes import require_scope
from glosswork.serializers import saved_view_doc
from glosswork.services import ServiceBundle

router = APIRouter(prefix="/api/v1", tags=["saved-views"])

ActorDep = Annotated[ActorContext, Depends(get_actor)]
ServicesDep = Annotated[ServiceBundle, Depends(get_services)]


class CreateSavedViewBody(BaseModel):
    name: str
    config: dict[str, Any]
    mode: str = "table"
    description: str | None = None
    is_default: bool = False


class UpdateSavedViewBody(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None
    mode: str | None = None
    description: str | None = None
    is_default: bool | None = None


@router.post("/object-types/{object_type_key}/saved-views", dependencies=[require_scope("write")])
def create_saved_view(
    object_type_key: str,
    payload: CreateSavedViewBody,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    view = services.saved_views.create_saved_view(
        actor,
        object_type_key,
        payload.name,
        payload.config,
        mode=payload.mode,
        description=payload.description,
        is_default=payload.is_default,
    )
    return saved_view_doc(view)


@router.get("/object-types/{object_type_key}/saved-views", dependencies=[require_scope("read")])
def list_saved_views(
    object_type_key: str,
    actor: ActorDep,
    services: ServicesDep,
) -> list[dict[str, Any]]:
    views = services.saved_views.list_saved_views(actor, object_type_key)
    return [saved_view_doc(v) for v in views]


@router.patch("/saved-views/{view_id}", dependencies=[require_scope("write")])
def update_saved_view(
    view_id: str,
    payload: UpdateSavedViewBody,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    view = services.saved_views.update_saved_view(
        actor,
        view_id,
        name=payload.name,
        description=payload.description,
        config=payload.config,
        mode=payload.mode,
        is_default=payload.is_default,
    )
    return saved_view_doc(view)


@router.delete("/saved-views/{view_id}", dependencies=[require_scope("write")])
def delete_saved_view(
    view_id: str,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    view = services.saved_views.delete_saved_view(actor, view_id)
    return saved_view_doc(view)
