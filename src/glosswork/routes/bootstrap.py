"""The bootstrap handoff route (DD-37, FR-I1).

A thin adapter over :class:`~glosswork.services.bootstrap.BootstrapService` (DD-3): it
parses a body, calls ``claim``, and shapes the response. Every refusal is the
service's, and no route below catches one -- ``app.py``'s single handler maps them onto
the error envelope.

**Its own module**, rather than a fifth route in ``auth.py``, because that module's
router is prefixed ``/api/v1/auth`` and this path is not part of the sign-in flow, and
rather than one in ``identity.py``, because every route there resolves a caller first
and this one by construction has none.

``POST /api/v1/bootstrap`` carries no ``require_scope`` declaration: it is named in
``scopes.SCOPE_EXEMPT_PATHS`` and in ``middleware.AUTH_PUBLIC_PATHS``, the fifth entry
in each and the first that is not an auth-flow route (DD-15).

**The secret rides in the body, not in ``Authorization``.** The edge resolves that
header as a personal access token on every path that is not credential-exempt, and one
header meaning two different kinds of credential is how a later edit comes to confuse
them. The body also keeps the secret out of the access log line, which records method,
path, status and actor and never a body.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from glosswork.api_deps import get_request_id, get_services
from glosswork.services import ServiceBundle

router = APIRouter(prefix="/api/v1", tags=["bootstrap"])

ServicesDep = Annotated[ServiceBundle, Depends(get_services)]
RequestIdDep = Annotated[str, Depends(get_request_id)]


class BootstrapBody(BaseModel):
    """``secret`` is optional *here* and required *there*.

    Declaring it required would let FastAPI's own body validation answer a claim that
    omits it with 422 "Field required", before ``BootstrapService`` compares anything --
    and an unauthenticated caller would learn the body's shape from a deployment it
    holds no secret for. The service treats an absent secret as a mismatch and answers
    401, which is the same answer a wrong one gets. Optionality in the
    schema is what routes the decision to the one place that makes it.
    """

    secret: str | None = None
    email: str
    password: str
    display_name: str | None = None
    # The agent label to mint the first token for, so a hosting operator provisioning
    # a deployment can attribute the credential it is about to hand to a named tool.
    agent_label: str | None = None


class BootstrapHandoffResponse(BaseModel):
    """The approved contract with the connection kit and a hosting operator.

    It reaches clients through ``/openapi.json`` and therefore through the generated
    ``web/src/api/schema.ts``: the handoff is a contract, and the schema is where a
    client reads it.

    ``agent_label`` is the seventh name, beside DD-37's other six.
    """

    token: str
    token_prefix: str
    scope: str
    principal_id: str
    mcp_url: str | None
    sign_in_url: str | None
    agent_label: str | None


@router.post("/bootstrap", status_code=201)
def claim_bootstrap(
    body: BootstrapBody,
    response: Response,
    services: ServicesDep,
    request_id: RequestIdDep,
) -> BootstrapHandoffResponse:
    """Exchange ``GW_BOOTSTRAP_SECRET`` for the first administrator's credential.

    ``Cache-Control: no-store`` because the body carries a credential that is returned
    exactly once: a proxy or a browser cache holding this response holds an ``admin``
    token.
    """
    handoff = services.bootstrap.claim(
        request_id,
        secret=body.secret,
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        agent_label=body.agent_label,
    )
    response.headers["Cache-Control"] = "no-store"
    return BootstrapHandoffResponse(
        token=handoff.token,
        token_prefix=handoff.token_prefix,
        scope=handoff.scope,
        principal_id=handoff.principal_id,
        mcp_url=handoff.mcp_url,
        sign_in_url=handoff.sign_in_url,
        agent_label=handoff.agent_label,
    )
