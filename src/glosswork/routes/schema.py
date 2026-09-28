"""Schema-administration REST routes (FR-S1 through FR-S10, FR-A1 through FR-A4).

Thin adapters over :class:`~glosswork.services.schema.SchemaService` (DD-3): every
route parses the request, calls exactly one service method, and shapes the response
through the envelopes shared with the MCP tools. No business logic lives here, and
no route catches ``GlossworkError`` — the single exception handler registered in
``app.py`` maps it onto the error envelope.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.envelopes import (
    describe_object_type_doc,
    field_doc,
    list_object_types_doc,
    object_type_grants_doc,
    proposal_created_doc,
    proposal_doc,
    proposal_list_doc,
    update_field_doc,
)
from glosswork.scopes import require_scope
from glosswork.serializers import grant_doc
from glosswork.services import ServiceBundle
from glosswork.services.schema import DEFAULT_PROPOSAL_LIMIT

router = APIRouter(prefix="/api/v1", tags=["schema"])


# --------------------------------------------------------------------------- bodies


class FieldSpecBody(BaseModel):
    """One field spec, used both for the initial field list on object-type creation
    and for the add-field route (docs/MCP_TOOLS.md section 5.3)."""

    key: str
    name: str
    type: str
    description: str
    config: dict[str, Any] | None = None
    required: bool | None = None
    unique: bool | None = None
    indexed: bool | None = None
    embed: bool | None = None
    default: Any | None = None


class CreateObjectTypeBody(BaseModel):
    key: str
    name: str
    name_plural: str
    description: str
    key_prefix: str
    icon: str | None = None
    fields: list[FieldSpecBody] | None = None
    # Declared rather than left to Pydantic v2's default `extra="ignore"`,
    # which would discard it without raising. A creation path that looks like it works and
    # stores nothing is worse than one that fails. The adapter forwards it and branches on
    # nothing; eligibility is `SchemaService`'s (DD-3).
    display_field_key: str | None = None


class FieldUpdateBody(BaseModel):
    changes: dict[str, Any]
    reason: str | None = None


class ProposeSchemaChangeBody(BaseModel):
    change_type: str
    object_type: str
    field_key: str | None = None
    payload: dict[str, Any] | None = None
    reason: str | None = None


class ApproveProposalBody(BaseModel):
    null_non_coercible: bool = False
    create_missing_options: bool = False
    confirm_impact: dict[str, Any] | None = None
    decision_note: str | None = None


class RejectProposalBody(BaseModel):
    decision_note: str | None = None


def _field_spec_dict(spec: FieldSpecBody) -> dict[str, Any]:
    return spec.model_dump(exclude_unset=True)


# --------------------------------------------------------------------------- routes


@router.post("/object-types", dependencies=[require_scope("admin")])
def create_object_type(
    body: CreateObjectTypeBody,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    fields = [_field_spec_dict(f) for f in body.fields] if body.fields is not None else None
    services.schema.create_object_type(
        actor,
        key=body.key,
        name=body.name,
        name_plural=body.name_plural,
        description=body.description,
        key_prefix=body.key_prefix,
        fields=fields,
        icon=body.icon,
        display_field_key=body.display_field_key,
    )
    return describe_object_type_doc(services, actor, body.key)


@router.get("/object-types", dependencies=[require_scope("read")])
def list_object_types(
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> list[dict[str, Any]]:
    return list_object_types_doc(services, actor)


@router.get("/object-types/{key}", dependencies=[require_scope("read")])
def get_object_type(
    key: str,
    include_samples: bool = False,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    return describe_object_type_doc(services, actor, key, include_samples=include_samples)


@router.patch("/object-types/{key}", dependencies=[require_scope("admin")])
def update_object_type(
    key: str,
    changes: dict[str, Any],
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    services.schema.update_object_type(actor, key, changes)
    return describe_object_type_doc(services, actor, key)


@router.post("/object-types/{key}/fields", dependencies=[require_scope("admin")])
def add_field(
    key: str,
    body: FieldSpecBody,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    field = services.schema.add_field(actor, key, _field_spec_dict(body))
    return {"status": "applied", "field": field_doc(field)}


@router.patch("/object-types/{key}/fields/{field_key}", dependencies=[require_scope("admin")])
def update_field(
    key: str,
    field_key: str,
    body: FieldUpdateBody,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    result = services.schema.update_field(actor, key, field_key, body.changes, reason=body.reason)
    return update_field_doc(result)


# ------------------------------------------------------------------------- grants
#
# All three declare ``require_scope("admin")``, *not* ``write``:
# the level check is always an additional narrowing on top of an unchanged credential
# floor, never a mechanism for admitting a weaker credential than reaches a route today.
# The service layer applies the admin-level check on the type on top of that, which is
# what lets a type's own administrator manage its grants without being a system
# administrator.


class GrantBody(BaseModel):
    level: str


@router.get("/object-types/{key}/grants", dependencies=[require_scope("admin")])
def list_object_type_grants(
    key: str,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    return object_type_grants_doc(services.access.list_grants_document(actor, key))


@router.put("/object-types/{key}/grants/{principal_id}", dependencies=[require_scope("admin")])
def put_object_type_grant(
    key: str,
    principal_id: str,
    body: GrantBody,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    return grant_doc(services.access.grant(actor, key, principal_id, body.level))


@router.delete("/object-types/{key}/grants/{principal_id}", dependencies=[require_scope("admin")])
def delete_object_type_grant(
    key: str,
    principal_id: str,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, str]:
    services.access.revoke(actor, key, principal_id)
    return {"status": "revoked"}


@router.post("/schema-proposals", dependencies=[require_scope("admin")])
def propose_schema_change(
    body: ProposeSchemaChangeBody,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    proposal = services.schema.propose_schema_change(
        actor,
        body.change_type,
        body.object_type,
        field_key=body.field_key,
        payload=body.payload,
        reason=body.reason,
    )
    return proposal_created_doc(proposal)


@router.get("/schema-proposals", dependencies=[require_scope("admin")])
def list_schema_proposals(
    status: str | None = None,
    limit: int = DEFAULT_PROPOSAL_LIMIT,
    cursor: str | None = None,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    return proposal_list_doc(services.schema.list_proposals_page(actor, status, limit, cursor))


@router.get("/schema-proposals/{proposal_id}", dependencies=[require_scope("admin")])
def get_schema_proposal(
    proposal_id: str,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    proposal = services.schema.get_proposal(actor, proposal_id)
    return proposal_doc(proposal, services.schema.proposal_targets_for([proposal])[proposal.id])


@router.post("/schema-proposals/{proposal_id}/approve", dependencies=[require_scope("admin")])
def approve_proposal(
    proposal_id: str,
    body: ApproveProposalBody | None = None,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    body = body or ApproveProposalBody()
    proposal = services.schema.approve_proposal(
        actor,
        proposal_id,
        null_non_coercible=body.null_non_coercible,
        create_missing_options=body.create_missing_options,
        confirm_impact=body.confirm_impact,
        decision_note=body.decision_note,
    )
    return proposal_doc(proposal, services.schema.proposal_targets_for([proposal])[proposal.id])


@router.post("/schema-proposals/{proposal_id}/reject", dependencies=[require_scope("admin")])
def reject_proposal(
    proposal_id: str,
    body: RejectProposalBody | None = None,
    actor: ActorContext = Depends(get_actor),  # noqa: B008
    services: ServiceBundle = Depends(get_services),  # noqa: B008
) -> dict[str, Any]:
    body = body or RejectProposalBody()
    proposal = services.schema.reject_proposal(actor, proposal_id, decision_note=body.decision_note)
    return proposal_doc(proposal, services.schema.proposal_targets_for([proposal])[proposal.id])
