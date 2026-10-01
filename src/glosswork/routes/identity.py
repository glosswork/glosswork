"""Identity routes: the calling principal, principal/service-account management, and
personal access tokens (FR-I3, FR-I4, FR-I5, docs/DATA_MODEL.md section 2).

Thin adapters over ``PrincipalService`` and ``AccessTokenService`` (DD-3): every route
parses the request, calls exactly one service method, and shapes the response through
the envelopes shared with the MCP tools. No business logic lives here, and no route
catches ``GlossworkError`` — the single exception handler registered in ``app.py``
maps it onto the error envelope. Required scope is declared once per route, in the
decorator, via ``require_scope`` (``src/glosswork/scopes.py``); nothing here reads
the credential's authorization level directly off ``ActorContext`` — the grep-backed
completeness test asserts that.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from glosswork.actor import ActorContext
from glosswork.api_deps import get_actor, get_services, source_ip
from glosswork.cookies import SESSION_COOKIE_NAME
from glosswork.envelopes import (
    access_token_doc,
    invite_doc,
    invite_email_doc,
    me_doc,
    principal_directory_doc,
    principal_doc,
    workspace_doc,
)
from glosswork.errors import ValidationFailedError
from glosswork.scopes import require_role, require_scope
from glosswork.services import ServiceBundle
from glosswork.services.principals import DIRECTORY_DEFAULT_LIMIT
from glosswork.services.relay import MAX_ADDRESS_LENGTH
from glosswork.services.sessions import hash_session_value

router = APIRouter(prefix="/api/v1", tags=["identity"])

ActorDep = Annotated[ActorContext, Depends(get_actor)]
ServicesDep = Annotated[ServiceBundle, Depends(get_services)]


class CreatePrincipalBody(BaseModel):
    type: str
    display_name: str
    email: str | None = None
    role: str = "member"
    description: str | None = None
    password: str | None = None


class UpdatePrincipalBody(BaseModel):
    display_name: str | None = None
    role: str | None = None
    description: str | None = None


class SetPasswordBody(BaseModel):
    password: str


class ChangeOwnPasswordBody(BaseModel):
    current_password: str
    new_password: str


class CreateInviteBody(BaseModel):
    email: str = Field(max_length=MAX_ADDRESS_LENGTH)
    display_name: str
    role: str = "member"


class MintAccessTokenBody(BaseModel):
    name: str
    scope: str
    principal_id: str | None = None
    expires_at: str | None = None
    # The agent label this token is minted for. Optional, and descriptive only: it
    # grants nothing and is read by nothing that authorizes. A malformed one is refused
    # by the service at mint, not here (DD-3), and not at use.
    agent_label: str | None = None


# ------------------------------------------------------------------------------- me


@router.get("/me", dependencies=[require_scope("read")])
def get_me(actor: ActorDep, services: ServicesDep) -> dict[str, Any]:
    """The calling principal plus the credential's ``scope`` and ``auth_method``."""
    principal = services.principals.get_principal(actor.principal_id)
    return me_doc(principal, actor)


@router.post("/me/password", dependencies=[require_scope("write")])
def change_own_password(
    body: ChangeOwnPasswordBody, request: Request, actor: ActorDep, services: ServicesDep
) -> dict[str, Any]:
    """A signed-in person changes their own password (FR-I17, DD-13): the
    same containment a reset gives an administrator, applied to yourself -- every
    personal access token and every other session the account holds is revoked in
    the one transaction that also sets the new hash. Refused for anything but a
    browser session; that check, the provider and activity checks, and the
    current-password verification all live in
    ``PrincipalService.change_own_password``, not here (DD-3).

    Counted into the same rate limiter ``/auth/login`` uses, keyed on the
    principal id rather than the email because this route already has the id
    without a second read: the source address's budget is shared with login, and
    the pair budget is this account's own, from this address. Every attempt is
    counted, including one the service refuses for the wrong credential kind, for
    the same reason login counts before looking anything up. A successful change
    clears the pair window, exactly as a successful login does.
    """
    ip = source_ip(request)
    limiter_key = f"principal:{actor.principal_id}"
    services.login_limiter.check_and_record(limiter_key, ip, attempt="password change")
    # The session to keep is computed exactly as the admin route computes it below:
    # the sha256 of the presented cookie, never the cookie itself.
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    keep_session_hash = hash_session_value(cookie) if cookie else None
    principal = services.principals.change_own_password(
        actor,
        body.current_password,
        body.new_password,
        keep_session_hash=keep_session_hash,
    )
    services.login_limiter.reset(limiter_key, ip)
    return me_doc(principal, actor)


# ------------------------------------------------------------------------- workspace


@router.get("/workspace", dependencies=[require_scope("read")])
def get_workspace(services: ServicesDep) -> dict[str, Any]:
    """The deployment's name and who is in it (DD-28): the sidebar's one
    bounded read.

    ``read`` is the right gate, argued rather than assumed: two integers with no
    names in them are strictly less disclosure than ``GET /principals/directory``
    below, which already serves every authenticated caller each principal's id,
    display name, email, type and active flag at this same scope. A count cannot be
    the thing that needs protecting when the list it counts is already published.
    """
    return workspace_doc(services.workspace.get_workspace())


# ------------------------------------------------------------------------ principals


@router.get("/principals", dependencies=[require_scope("admin"), require_role("admin")])
def list_principals(
    services: ServicesDep,
    type: str | None = None,
    include_inactive: bool = True,
) -> dict[str, Any]:
    principals = services.principals.list_principals(type, include_inactive)
    return {"principals": [principal_doc(p) for p in principals]}


@router.post(
    "/principals", status_code=201, dependencies=[require_scope("admin"), require_role("admin")]
)
def create_principal(
    body: CreatePrincipalBody, actor: ActorDep, services: ServicesDep
) -> dict[str, Any]:
    if body.type == "user":
        if not body.email:
            raise ValidationFailedError(
                "email is required to create a user principal.", type=body.type
            )
        principal = services.principals.create_user(
            actor,
            email=body.email,
            display_name=body.display_name,
            role=body.role,
            password=body.password,
        )
    elif body.type == "service_account":
        if not body.description:
            raise ValidationFailedError(
                "description is required to create a service_account principal.",
                type=body.type,
            )
        principal = services.principals.create_service_account(
            actor,
            display_name=body.display_name,
            description=body.description,
            role=body.role,
        )
    else:
        raise ValidationFailedError(
            f"Unknown principal type {body.type!r}. Use 'user' or 'service_account'.",
            type=body.type,
        )
    return principal_doc(principal)


@router.get("/principals/directory", dependencies=[require_scope("read")])
def principal_directory(
    services: ServicesDep,
    q: str | None = None,
    type: str | None = None,
    include_inactive: bool = False,
    limit: int = DIRECTORY_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Who exists, by name (DD-24, FR-I16).

    **The only route in this namespace without ``require_role("admin")``**, and the only
    one whose response is not ``principal_doc``. Both facts are the same decision: a
    display name and an email address are already published to every reader through
    comment and audit rows (DD-25), so withholding a directory made ``user_ref`` a field
    you could only fill in if you already knew a UUID -- while everything an
    administrator needs and a member does not (``role``, ``auth_provider``,
    ``external_id``) is projected away by ``principal_directory_doc``.

    It is declared **before** ``GET /principals/{principal_id}``: FastAPI matches routes
    in declaration order, so the reverse would capture ``directory`` as a path parameter
    and apply that route's ``admin`` gate. ``tests/test_principal_directory.py`` pins the
    ordering rather than trusting it.
    """
    principals = services.principals.search_principals(
        q=q, principal_type=type, include_inactive=include_inactive, limit=limit
    )
    return {"principals": [principal_directory_doc(p) for p in principals]}


@router.get(
    "/principals/{principal_id}", dependencies=[require_scope("admin"), require_role("admin")]
)
def get_principal(principal_id: str, services: ServicesDep) -> dict[str, Any]:
    return principal_doc(services.principals.get_principal(principal_id))


@router.patch(
    "/principals/{principal_id}", dependencies=[require_scope("admin"), require_role("admin")]
)
def update_principal(
    principal_id: str, body: UpdatePrincipalBody, actor: ActorDep, services: ServicesDep
) -> dict[str, Any]:
    principal = services.principals.update_principal(
        actor,
        principal_id,
        display_name=body.display_name,
        role=body.role,
        description=body.description,
    )
    return principal_doc(principal)


@router.delete(
    "/principals/{principal_id}", dependencies=[require_scope("admin"), require_role("admin")]
)
def deactivate_principal(
    principal_id: str, actor: ActorDep, services: ServicesDep
) -> dict[str, Any]:
    """Deactivate the principal (``is_active = 0``), never delete it: every audit row,
    record ``created_by``, and comment author is a foreign key onto this table."""
    principal = services.principals.deactivate_principal(actor, principal_id)
    return principal_doc(principal)


@router.post(
    "/principals/{principal_id}/password",
    dependencies=[require_scope("admin"), require_role("admin")],
)
def set_principal_password(
    principal_id: str,
    body: SetPasswordBody,
    request: Request,
    actor: ActorDep,
    services: ServicesDep,
) -> dict[str, Any]:
    # A reset revokes every credential the account holds (DD-13), with one
    # carve-out this route decides and the service only receives (DD-3): an
    # administrator resetting their **own** password over the browser keeps the session
    # that made the request, because logging the caller out on success is the wrong
    # outcome and is the pattern the OIDC role-change path already avoids. The session
    # is named by the sha256 of the presented cookie, never by the cookie itself --
    # `PrincipalService` has no use for a live credential and would carry one into every
    # traceback of that frame.
    #
    # A comment rather than a docstring on purpose: FastAPI publishes a route docstring
    # as the operation's OpenAPI `description`, which would regenerate
    # `web/src/api/schema.ts` for an explanation that alters no wire contract.
    keep_session_hash: str | None = None
    if actor.auth_method == "session" and principal_id == actor.principal_id:
        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        if cookie:
            keep_session_hash = hash_session_value(cookie)
    principal = services.principals.set_password(
        actor, principal_id, body.password, keep_session_hash=keep_session_hash
    )
    return principal_doc(principal)


# --------------------------------------------------------------------------- invites
#
# Change 9. All three answer ``feature_disabled`` unless the relay is configured, which
# ``InviteService`` decides (DD-3). Revoking stays open while the workspace is read-only
# (``scopes.READ_ONLY_OPEN_ROUTES``), beside removing a person and for the same reason.


@router.get("/invites", dependencies=[require_scope("admin"), require_role("admin")])
def list_invites(services: ServicesDep) -> dict[str, Any]:
    return {"invites": [invite_doc(i) for i in services.invites.list_live()]}


@router.post(
    "/invites", status_code=201, dependencies=[require_scope("admin"), require_role("admin")]
)
def create_invite(body: CreateInviteBody, actor: ActorDep, services: ServicesDep) -> dict[str, Any]:
    invite, result = services.invites.create_invite(
        actor, email=body.email, display_name=body.display_name, role=body.role
    )
    return {"invite": invite_doc(invite), "email": invite_email_doc(result)}


@router.delete("/invites/{invite_id}", dependencies=[require_scope("admin"), require_role("admin")])
def revoke_invite(invite_id: str, actor: ActorDep, services: ServicesDep) -> dict[str, Any]:
    return invite_doc(services.invites.revoke_invite(actor, invite_id))


# --------------------------------------------------------------------- access tokens


@router.post("/access-tokens", status_code=201, dependencies=[require_scope("write")])
def mint_access_token(
    body: MintAccessTokenBody, actor: ActorDep, services: ServicesDep
) -> dict[str, Any]:
    """Mint a personal access token. The plaintext secret is returned here and only
    here: no subsequent read of this or any token ever includes it again."""
    minted = services.tokens.mint(
        actor,
        name=body.name,
        scope=body.scope,
        principal_id=body.principal_id,
        expires_at=body.expires_at,
        agent_label=body.agent_label,
    )
    return {**access_token_doc(minted.row), "token": minted.plaintext}


@router.get("/access-tokens", dependencies=[require_scope("read")])
def list_access_tokens(
    actor: ActorDep, services: ServicesDep, principal_id: str | None = None
) -> dict[str, Any]:
    # The declared scope stays `read` so a member can list its own; naming somebody
    # else's `principal_id` is a delegation the service refuses unless the caller holds
    # both an `admin` credential and the `admin` role. The route resolves no ownership
    # of its own -- that comparison belongs in the service (DD-3) -- so it hands the
    # actor over unexamined. A comment rather than a docstring: a route's docstring is
    # its OpenAPI description and would move `web/src/api/schema.ts` for a note that
    # changes no contract.
    tokens = services.tokens.list_tokens(actor, principal_id)
    return {"access_tokens": [access_token_doc(t) for t in tokens]}


@router.delete("/access-tokens/{token_id}", dependencies=[require_scope("write")])
def revoke_access_token(token_id: str, actor: ActorDep, services: ServicesDep) -> dict[str, Any]:
    token = services.tokens.revoke(actor, token_id)
    return access_token_doc(token)
