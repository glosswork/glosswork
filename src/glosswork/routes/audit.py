"""Audit browsing and revert routes (FR-U8, FR-D6, DD-21). REST/UI-only: there is
deliberately no MCP tool for these (docs/MCP_TOOLS.md section 8) — revert is a
human-driven action FR-D6 frames as happening through the audit browser, and
``get_record_history`` already gives an agent the one MCP-visible audit read it's
documented to have.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.envelopes import audit_search_result_doc, record_doc
from glosswork.scopes import require_scope
from glosswork.services import ServiceBundle
from glosswork.services.audit import DEFAULT_LIMIT

router = APIRouter(prefix="/api/v1", tags=["audit"])

ActorDep = Annotated[ActorContext, Depends(get_actor)]
ServicesDep = Annotated[ServiceBundle, Depends(get_services)]


class RevertFieldBody(BaseModel):
    expected_version: int
    force: bool = False


class RevertToVersionBody(BaseModel):
    target_version: int
    expected_version: int
    force: bool = False


@router.get("/audit-events", dependencies=[require_scope("read")])
def search_audit_events(
    actor: ActorDep,
    services: ServicesDep,
    record: str | None = None,
    principal_id: str | None = None,
    agent_label_id: str | None = None,
    object_type: str | None = None,
    field_key: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    result = services.audit.search(
        actor,
        record=record,
        principal_id=principal_id,
        agent_label_id=agent_label_id,
        object_type=object_type,
        field_key=field_key,
        since=since,
        until=until,
        limit=limit,
        cursor=cursor,
    )
    return audit_search_result_doc(result)


@router.post("/audit-events/{event_id}/revert", dependencies=[require_scope("write")])
def revert_field_change(
    event_id: int,
    body: RevertFieldBody,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    record = services.records.revert_field_change(
        actor, event_id, body.expected_version, force=body.force
    )
    return record_doc(record)


@router.post("/records/{ref}/revert-to-version", dependencies=[require_scope("write")])
def revert_to_version(
    ref: str,
    body: RevertToVersionBody,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    record = services.records.revert_to_version(
        actor, ref, body.target_version, body.expected_version, force=body.force
    )
    return record_doc(record)
