"""The operator backup route (DD-39, FR-P8, FR-P10).

``POST /api/v1/operator/backup`` hands the deployment's backup artifact, the same one
``POST /api/v1/admin/backup`` streams, to whoever runs this deployment for somebody else:
the holder of ``GW_OPERATOR_TOKEN``, on a deployment that turned the backup on with
``GW_OPERATOR_BACKUP``. A hosting operator holds no workspace credential, by design, and
this is how it takes a scheduled backup without one.

A thin adapter (DD-3). The refusal is ``UsageService.require_operator_backup``'s and the
artifact is ``BackupService.stream``'s; nothing here decides anything.

**Its own module and its own path**, for ``routes/usage.py``'s reason: it resolves no
tenant caller. ``admin`` in a path means "a tenant ``admin`` credential" on every other
route, and one path answering two unrelated credentials is how a later edit comes to
confuse them.

**Credential-exempt at the edge, like the usage route.** The path is named in
``scopes.SCOPE_EXEMPT_PATHS`` and in ``middleware.AUTH_PUBLIC_PATHS``, so the edge never
resolves ``Authorization`` on it and a workspace ``admin`` personal access token is
refused exactly as a caller presenting nothing is. It declares no scope, because the
edge's anonymous actor carries ``read`` and a declaration would pass for anybody. For the
same reason it reaches no scope dependency and so stays open while a workspace is
read-only (DD-38), without being named in ``scopes.READ_ONLY_OPEN_ROUTES``.

**The check is a dependency, never a line in the generator.** A refusal raised once the
response has started streaming arrives after the status line has said 200. As a
dependency it runs before the handler, so a refused caller gets a clean 401 and no
snapshot is staged and nothing is audited. The dependency carries a tag that
``scopes.route_operator_credentials`` reads, so the set of routes that accept this
credential this way is pinned by a test.

**Always registered, whether or not the deployment opted in.** An unregistered path under
``/api/`` answers 404, which would tell a prober the setting's state. Off, unset, absent
and wrong all answer the one ``401``.

The header is read as ``X-Operator-Token`` and never from ``Authorization``, as on the
usage route and for its reason.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services
from glosswork.closing_response import ClosingStreamingResponse
from glosswork.scopes import OPERATOR_CREDENTIAL_ATTRIBUTE
from glosswork.services import ServiceBundle

router = APIRouter(prefix="/api/v1", tags=["operator"])

ActorDep = Annotated[ActorContext, Depends(get_actor)]
ServicesDep = Annotated[ServiceBundle, Depends(get_services)]


def operator_backup_credential(
    services: ServicesDep,
    x_operator_token: Annotated[str | None, Header()] = None,
) -> None:
    """Refuse every caller who may not take the operator backup, before any response
    exists. The rule is the service's; this only hands it the header."""
    services.usage.require_operator_backup(x_operator_token)


setattr(operator_backup_credential, OPERATOR_CREDENTIAL_ATTRIBUTE, True)


@router.post("/operator/backup", dependencies=[Depends(operator_backup_credential)])
def take_operator_backup(actor: ActorDep, services: ServicesDep) -> ClosingStreamingResponse:
    """Stream the backup artifact to the operator: a consistent database snapshot, then
    the attachment blob tree (FR-P8, DD-36).

    ``actor`` is the edge's anonymous actor, the deployment's own service account: no
    tenant principal is behind this call, and the audit row has to be attributed to
    something (DD-4). ``operator_triggered`` is what makes that row say so.

    ``Cache-Control: no-store``, as the usage route sets for a far smaller disclosure.

    A ``200`` means a backup started, not that the artifact is complete: the status line
    is sent before the snapshot is taken, so a failure after it truncates the stream. A
    reader checks the tar's end-of-archive and the snapshot's integrity.
    """
    return ClosingStreamingResponse(
        services.backup.stream(actor, operator_triggered=True),
        media_type="application/x-tar",
        headers={
            "Content-Disposition": 'attachment; filename="glosswork-backup.tar"',
            "Cache-Control": "no-store",
        },
    )
