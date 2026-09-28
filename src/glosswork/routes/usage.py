"""The operator usage route (DD-39, FR-P10).

A thin adapter over :class:`~glosswork.services.usage.UsageService` (DD-3): it reads one
header, calls ``snapshot``, and shapes the response. Every refusal is the service's, and
nothing below catches one -- ``app.py``'s single handler maps them onto the error
envelope.

**Its own module**, for ``routes/bootstrap.py``'s reason: it resolves no caller, so it
belongs with neither the authenticated routes nor the sign-in flow.

``GET /api/v1/usage`` carries **no** ``require_scope`` declaration, and that is not an
omission. It is named in ``scopes.SCOPE_EXEMPT_PATHS`` and in
``middleware.AUTH_PUBLIC_PATHS``, the sixth entry in each, so the edge builds an
anonymous ``read``-scoped actor and never resolves ``Authorization`` at all. Declaring
even ``require_scope("read")`` here would therefore *pass* for a caller presenting no
credential whatsoever, which is the opposite of what a declaration looks like it is
doing. The refusal is the route's own, through the service.

**Under ``/api/``, not beside ``/healthz``.** The endpoint is not placed beside
``/healthz``, for a measured reason: a path outside ``/api/`` is answered by the
static-file fallback, so ``GET /usage`` returns 200 with the browser app's HTML on a tree
that has no such route at all, and five assertions in this repository are passing that
way today. A route that is dropped or renamed later must hand
a hosting operator an error, not a web page with a success status.

**The credential rides in ``X-Operator-Token``, not in ``Authorization``.** That header
means "a tenant personal access token" on every other path in this product, and one
header meaning two kinds of credential is how a later edit comes to confuse them.
Bootstrap put its secret in a *body* for that reason and for a second one, keeping it out
of the access log; the second does not apply here, because the access log line records
method, path, status, duration, request id, principal, agent label, surface and scope,
and no header, no query string and no body.

The route's path and its complete response schema are public: ``/openapi.json`` answers
200 with no credential. That discloses no tenant value and no count, and the route is in
every image whether or not the variable is set, so it does not distinguish a metered
workspace from an unmetered one. It is a second reason no response key may be
tenant-chosen: the key names themselves are published.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel

from glosswork.api_deps import get_request_id, get_services
from glosswork.services import ServiceBundle

router = APIRouter(prefix="/api/v1", tags=["usage"])

ServicesDep = Annotated[ServiceBundle, Depends(get_services)]
RequestIdDep = Annotated[str, Depends(get_request_id)]


class ToolCallCountResponse(BaseModel):
    """One ``(tool, error_code, count)`` row. ``error_code`` is ``null`` for a call that
    succeeded; ``unknown`` for one that failed without a code this deployment publishes,
    which is what an unregistered tool name and an argument-coercion failure both
    produce."""

    tool: str
    error_code: str | None
    count: int


class UsageResponse(BaseModel):
    """The operator's whole view, and the contract with a hosting operator.

    Every value is a number except the tool names, error codes and harness names, each of
    which comes from an allowlist this image ships or is a literal. **No field carries
    record content, a field value, an object type key, a field key, an attachment
    filename, a principal name, an email address or an agent label string**, and a test
    asserts that by writing one sentinel into every one of those places and sweeping the
    whole response for it, and by pinning this key set by equality.

    ``attachment_bytes_logical`` and ``attachment_bytes_stored`` are both here because
    storage and billing disagree about a file uploaded twice and reporting one number
    would silently pick a side.

    ``since`` is when this database began counting, so a hosting operator that sees the
    counters fall knows whether the volume was replaced or usage genuinely dropped.
    """

    since: str | None
    records_live: int
    records_deleted: int
    attachment_count: int
    attachment_bytes_logical: int
    attachment_bytes_stored: int
    humans_active: int
    humans_total: int
    agent_labels_distinct: int
    agent_label_calls_total: int
    agent_labels_by_harness: dict[str, int]
    object_types: int
    fields: int
    tool_calls: list[ToolCallCountResponse]


@router.get("/usage")
def read_usage(
    response: Response,
    services: ServicesDep,
    request_id: RequestIdDep,
    x_operator_token: Annotated[str | None, Header()] = None,
) -> UsageResponse:
    """Counts for the operator who configured ``GW_OPERATOR_TOKEN``, and a refusal for
    everybody else.

    ``Cache-Control: no-store`` because a proxy holding this response holds a workspace's
    volume numbers, which are a customer's commercial data even though they are not the
    customer's content.
    """
    snapshot = services.usage.snapshot(request_id, x_operator_token)
    response.headers["Cache-Control"] = "no-store"
    return UsageResponse(
        since=snapshot.since,
        records_live=snapshot.records_live,
        records_deleted=snapshot.records_deleted,
        attachment_count=snapshot.attachment_count,
        attachment_bytes_logical=snapshot.attachment_bytes_logical,
        attachment_bytes_stored=snapshot.attachment_bytes_stored,
        humans_active=snapshot.humans_active,
        humans_total=snapshot.humans_total,
        agent_labels_distinct=snapshot.agent_labels_distinct,
        agent_label_calls_total=snapshot.agent_label_calls_total,
        agent_labels_by_harness=snapshot.agent_labels_by_harness,
        object_types=snapshot.object_types,
        fields=snapshot.fields,
        tool_calls=[
            ToolCallCountResponse(tool=row.tool, error_code=row.error_code, count=row.count)
            for row in snapshot.tool_calls
        ],
    )
