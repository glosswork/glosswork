"""Record CRUD, query, bulk-update, links, and history routes (DD-3: this module is a
thin adapter — every route parses the request, calls a service method, and shapes the
response through the envelopes shared with the MCP tools; no business logic lives
here).

PRD.md section 6.2 (FR-R1..FR-R11), section 6.3 (FR-L1..FR-L6), section 6.7
(FR-A1..FR-A4); docs/MCP_TOOLS.md sections 4, 5.1-5.2, 6, 8.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.envelopes import (
    bulk_update_doc,
    history_page_doc,
    query_result_doc,
    record_response_doc,
    record_with_includes_doc,
)
from glosswork.scopes import require_scope
from glosswork.services import ServiceBundle
from glosswork.services.records import DEFAULT_HISTORY_LIMIT, DEFAULT_QUERY_LIMIT

router = APIRouter(prefix="/api/v1", tags=["records"])

# Dependency aliases (Annotated form) so route signatures avoid a call expression
# in an argument default (ruff B008) while staying thin adapters over the actor
# and service bundle constructed at the edge (DD-4).
ActorDep = Annotated[ActorContext, Depends(get_actor)]
ServicesDep = Annotated[ServiceBundle, Depends(get_services)]


def _split_csv_param(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [part.strip() for part in value.split(",") if part.strip()]


class UpdateRecordBody(BaseModel):
    values: dict[str, Any]
    expected_version: int | None = None
    force: bool = False


class QueryBody(BaseModel):
    filter: dict[str, Any] | None = None
    sort: list[dict[str, Any]] | None = None
    limit: int = DEFAULT_QUERY_LIMIT
    cursor: str | None = None
    fields: list[str] | str | None = None
    expand_relations: list[str] | None = None
    include_deleted: bool = False


class BulkUpdateBody(BaseModel):
    filter: dict[str, Any] | None = None
    values: dict[str, Any]
    dry_run: bool = False


class LinkBody(BaseModel):
    to_records: list[str]


@router.post("/object-types/{object_type_key}/records", dependencies=[require_scope("write")])
def create_record(
    object_type_key: str,
    values: dict[str, Any],
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    record = services.records.create_record(actor, object_type_key, values)
    return record_response_doc(services, actor, record)


@router.get("/records/{ref}", dependencies=[require_scope("read")])
def get_record(
    ref: str,
    actor: ActorDep,
    services: ServicesDep,
    include: str | None = None,
    expand_relations: str | None = None,
    fields: str | None = None,
) -> dict[str, Any]:
    return record_with_includes_doc(
        services,
        actor,
        ref,
        set(_split_csv_param(include) or []),
        expand_relations=_split_csv_param(expand_relations),
        expand_fields=_split_csv_param(fields),
    )


@router.patch("/records/{ref}", dependencies=[require_scope("write")])
def update_record(
    ref: str,
    body: UpdateRecordBody,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    record = services.records.update_record(
        actor,
        ref,
        body.values,
        expected_version=body.expected_version,
        force=body.force,
    )
    return record_response_doc(services, actor, record)


@router.delete("/records/{ref}", dependencies=[require_scope("write")])
def delete_record(
    ref: str,
    actor: ActorDep,
    services: ServicesDep,
    force: bool = False,
) -> dict[str, Any]:
    record = services.records.delete_record(actor, ref, force=force)
    return record_response_doc(services, actor, record)


@router.post("/records/{ref}/restore", dependencies=[require_scope("write")])
def restore_record(
    ref: str,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    record = services.records.restore_record(actor, ref)
    return record_response_doc(services, actor, record)


@router.get("/records/{ref}/history", dependencies=[require_scope("read")])
def get_record_history(
    ref: str,
    actor: ActorDep,
    services: ServicesDep,
    field_key: str | None = None,
    limit: int = DEFAULT_HISTORY_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    page = services.records.get_record_history_page(
        actor, ref, field_key=field_key, limit=limit, cursor=cursor
    )
    return history_page_doc(page)


@router.post("/object-types/{object_type_key}/query", dependencies=[require_scope("read")])
def query_records(
    object_type_key: str,
    body: QueryBody,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    result = services.records.query_records(
        actor,
        object_type_key,
        filter=body.filter,
        sort=body.sort,
        limit=body.limit,
        cursor=body.cursor,
        fields=body.fields,
        expand_relations=body.expand_relations,
        include_deleted=body.include_deleted,
    )
    return query_result_doc(services, actor, object_type_key, result)


@router.post("/object-types/{object_type_key}/bulk-update", dependencies=[require_scope("write")])
def bulk_update(
    object_type_key: str,
    body: BulkUpdateBody,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    result = services.records.bulk_update(
        actor,
        object_type_key,
        body.values,
        filter=body.filter,
        dry_run=body.dry_run,
    )
    return bulk_update_doc(result)


@router.post("/records/{ref}/links/{field_key}", dependencies=[require_scope("write")])
def link_records(
    ref: str,
    field_key: str,
    body: LinkBody,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    services.records.link_records(actor, ref, field_key, body.to_records)
    return {"field_key": field_key, "linked": body.to_records}


@router.delete("/records/{ref}/links/{field_key}", dependencies=[require_scope("write")])
def unlink_records(
    ref: str,
    field_key: str,
    body: LinkBody,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    count = services.records.unlink_records(actor, ref, field_key, body.to_records)
    return {"field_key": field_key, "unlinked_count": count}
