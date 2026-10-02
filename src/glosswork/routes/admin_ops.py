"""Operator surface: backup, full-deployment export, and the orphan blob sweep
(FR-P8, FR-E5, FR-P2; DD-36).

Three administrator-scoped actions that act on the deployment rather than on any
record in it, which is why they share a module rather than joining a resource
family's router. Thin adapters, like every other route here (DD-3): each one calls
exactly one service method and shapes a response around it.

Restore is deliberately absent. DD-36 records why: a restore endpoint would have to
overwrite the database it is being served from, so it is an operator procedure
(stop the container, replace the volume contents, start it — migrations run at
startup per FR-P6). docs/DEPLOYMENT.md carries the steps and the container proof
exercises them.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.closing_response import ClosingStreamingResponse
from glosswork.scopes import require_role, require_scope
from glosswork.services import ServiceBundle
from glosswork.services.attachments import DEFAULT_SWEEP_LIMIT

router = APIRouter(prefix="/api/v1", tags=["admin-ops"])

ActorDep = Annotated[ActorContext, Depends(get_actor)]
ServicesDep = Annotated[ServiceBundle, Depends(get_services)]


@router.post("/admin/backup", dependencies=[require_scope("admin"), require_role("admin")])
def take_backup(actor: ActorDep, services: ServicesDep) -> ClosingStreamingResponse:
    """Stream one tar artifact: a consistent database snapshot, then the attachment
    blob tree (FR-P8, DD-36).

    ``POST`` rather than ``GET`` because it is not a safe method: it writes a staged
    snapshot to the volume and appends a ``backup_taken`` audit row. The service is a
    generator, so the snapshot is taken as the response begins streaming and is
    removed when it ends — including when the client disconnects.

    No ``Content-Length`` is sent: the artifact's size is not known until the blob
    tree has been walked, and walking it twice to find out would defeat the point.
    """
    return ClosingStreamingResponse(
        services.backup.stream(actor),
        media_type="application/x-tar",
        headers={"Content-Disposition": 'attachment; filename="glosswork-backup.tar"'},
    )


@router.get("/admin/export", dependencies=[require_scope("admin"), require_role("admin")])
def export_deployment(actor: ActorDep, services: ServicesDep) -> StreamingResponse:
    """The full-deployment JSON export (FR-E5): schema, records, links, comments,
    saved views, agent labels, and audit.

    Shares the streaming envelope and the admin scope with the backup above and
    deliberately not a format (DD-36): this one's consumer is a future backing store,
    the backup's is this deployment. The two are built together so that they never
    become two whole-deployment snapshot mechanisms.
    """
    return StreamingResponse(
        services.export.stream(actor),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="glosswork-export.json"'},
    )


@router.post("/admin/blobs/sweep", dependencies=[require_scope("admin"), require_role("admin")])
def sweep_orphan_blobs(
    actor: ActorDep,
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=1_000_000)] = DEFAULT_SWEEP_LIMIT,
) -> dict[str, Any]:
    """Delete attachment blobs no ``attachments`` row references any more (FR-P2).

    The on-demand half of the sweep; the other half is the bounded pass ``app.py``
    runs at startup. ``limit`` bounds how many blobs one call examines, and the
    response reports ``truncated`` so an operator can tell a clean tree from a pass
    that stopped early and needs running again.
    """
    return services.attachments.sweep_orphan_blobs(actor, limit=limit)
