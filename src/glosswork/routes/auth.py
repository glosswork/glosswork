"""Authentication routes: login, logout, and the OIDC redirect flow (FR-I1, DD-9,
DD-10).

Thin adapters over ``AuthService``, ``SessionService``, and ``OidcFlowService``
(DD-3): a route parses the request, calls exactly the service methods it needs, and
shapes cookies and redirects. No business logic lives here — session issue/resolve/
revoke, CSRF token generation and comparison, and the OIDC authorization-URL
construction and code exchange are all service methods with their own tests.

``/login``, ``/oidc/start``, ``/oidc/callback``, and ``/modes`` carry no
``require_scope`` declaration: they are named explicitly in
``scopes.SCOPE_EXEMPT_PATHS`` and in ``middleware.AUTH_PUBLIC_PATHS``, because no
credential exists yet for a signed-out browser to present. ``DELETE /session``
(logout) is an ordinary authenticated route and declares ``read`` like any other.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from glosswork.actor import ActorContext, anonymous_actor
from glosswork.api_deps import get_actor, get_request_id, get_services, get_settings, source_ip
from glosswork.config import Settings
from glosswork.cookies import (
    CSRF_COOKIE_NAME,
    OIDC_CALLBACK_PATH_PREFIX,
    OIDC_TRANSACTION_COOKIE_NAME,
    OIDC_TRANSACTION_MAX_AGE_SECONDS,
    SESSION_COOKIE_NAME,
    cookie_kwargs,
)
from glosswork.envelopes import me_doc
from glosswork.errors import AuthenticationFailedError
from glosswork.scopes import require_scope
from glosswork.services import ServiceBundle
from glosswork.services.principals import role_scope
from glosswork.services.relay import MAX_ADDRESS_LENGTH
from glosswork.services.sign_in_codes import REQUEST_MESSAGE

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

ActorDep = Annotated[ActorContext, Depends(get_actor)]
ServicesDep = Annotated[ServiceBundle, Depends(get_services)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
RequestIdDep = Annotated[str, Depends(get_request_id)]


class LoginBody(BaseModel):
    email: str
    password: str


class CodeRequestBody(BaseModel):
    email: str = Field(max_length=MAX_ADDRESS_LENGTH)


class CodeVerifyBody(BaseModel):
    email: str = Field(max_length=MAX_ADDRESS_LENGTH)
    # Unbounded here on purpose: a code that is not six digits is the same failure as a
    # wrong one, decided in the service before anything is looked up, and the edge's body
    # cap (DD-18) bounds the string.
    code: str


def _login_actor(request_id: str, principal_id: str, role: str) -> ActorContext:
    """The actor a login attributes its own session-issuance audit row to: the
    principal that just authenticated, at the scope their role now carries. Built
    here rather than taken from ``request.state.actor`` because these routes are
    pre-authentication — the edge actor for them is the unauthenticated bootstrap
    stand-in (``middleware.AUTH_PUBLIC_PATHS``), never used for authorization."""
    return ActorContext(
        principal_id=principal_id,
        principal_type="user",
        agent_label_id=None,
        auth_method="session",
        surface="ui",
        request_id=request_id,
        scope=role_scope(role),
    )


def _redirect_uri(settings: Settings) -> str:
    base = (settings.base_url or "").rstrip("/")
    return f"{base}/api/v1/auth/oidc/callback"


def _set_session_cookies(
    response: Response, settings: Settings, cookie_value: str, csrf_value: str
) -> None:
    session_kwargs = cookie_kwargs(settings, http_only=True)
    response.set_cookie(SESSION_COOKIE_NAME, cookie_value, **session_kwargs)
    csrf_kwargs = cookie_kwargs(settings, http_only=False)
    response.set_cookie(CSRF_COOKIE_NAME, csrf_value, **csrf_kwargs)


# ------------------------------------------------------------------------------ modes


@router.get("/modes")
def auth_modes(settings: SettingsDep) -> dict[str, Any]:
    """Which login methods a signed-out browser should render (FR-I1). The one route
    reachable with no credential at all besides the rest of the auth flow, and the
    only one of the four that also needs a scope-level exemption
    (``scopes.SCOPE_EXEMPT_PATHS``) rather than only the middleware's credential
    exemption, since it is a plain read with no session or provider round trip behind
    it.
    """
    codes = settings.email_codes_enabled
    return {
        # With codes on, password sign-in is off (DQ1): the form is not offered.
        "standalone": settings.auth_mode in ("standalone", "both") and not codes,
        "oidc": settings.auth_mode in ("oidc", "both"),
        "email_code": codes,
    }


# -------------------------------------------------------------------- email codes


@router.post("/code/request", status_code=202)
def request_sign_in_code(
    body: CodeRequestBody,
    request: Request,
    background_tasks: BackgroundTasks,
    services: ServicesDep,
    request_id: RequestIdDep,
) -> dict[str, str]:
    """Ask for a sign-in code (change 9). Always the same ``202`` and sentence, whatever
    the address: the attempt is counted in the login limiter before anything is looked
    up, and the rest runs after the answer is sent."""
    services.sign_in_codes.require_enabled()
    services.login_limiter.check_and_record(body.email, source_ip(request), attempt="sign-in code")
    background_tasks.add_task(services.sign_in_codes.request_code, body.email, request_id)
    return {"message": REQUEST_MESSAGE}


@router.post("/code/verify")
def verify_sign_in_code(
    body: CodeVerifyBody,
    request: Request,
    settings: SettingsDep,
    services: ServicesDep,
    request_id: RequestIdDep,
) -> Response:
    """Sign in with an emailed code (change 9), answering exactly as ``/login`` does. Every
    failure is one ``401 invalid_credentials`` with one message."""
    services.sign_in_codes.require_enabled()
    ip = source_ip(request)
    services.login_limiter.check_and_record(body.email, ip, attempt="sign-in code")
    principal = services.sign_in_codes.verify_code(body.email, body.code, request_id)
    services.login_limiter.reset(body.email, ip)
    actor = _login_actor(request_id, principal.id, principal.role)
    prior_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    minted = services.sessions.issue(
        actor, principal, replacing_cookie=prior_cookie, note="email code sign-in"
    )
    response = JSONResponse(me_doc(principal, actor))
    _set_session_cookies(response, settings, minted.cookie_value, minted.csrf_value)
    return response


# ------------------------------------------------------------------------------ login


@router.post("/login")
def login(
    body: LoginBody, request: Request, settings: SettingsDep, services: ServicesDep
) -> Response:
    """Local password login over ``AuthService.login_with_password`` (FR-I1),
    which already produces the single indistinguishable ``invalid_credentials`` error
    for every failure mode; this route adds nothing that could re-distinguish them.

    The rate-limit check runs **before** the credential check and counts the attempt
    unconditionally: counting only known accounts, or only failures, would make the
    limiter itself an account-existence oracle and undo the login's dummy-hash branch.
    A success clears the window.
    """
    services.authn.require_password_sign_in()
    ip = source_ip(request)
    services.login_limiter.check_and_record(body.email, ip)
    principal = services.authn.login_with_password(body.email, body.password)
    services.login_limiter.reset(body.email, ip)
    request_id = request.state.request_id
    actor = _login_actor(request_id, principal.id, principal.role)
    prior_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    minted = services.sessions.issue(
        actor, principal, replacing_cookie=prior_cookie, note="local login"
    )
    response = JSONResponse(me_doc(principal, actor))
    _set_session_cookies(response, settings, minted.cookie_value, minted.csrf_value)
    return response


@router.delete("/session", dependencies=[require_scope("read")])
def logout(request: Request, actor: ActorDep, services: ServicesDep) -> Response:
    """Logout that genuinely invalidates server-side (DD-9): the row is deleted, not
    flagged, so a replayed cookie resolves to nothing. Reachable by either credential
    type — a PAT holder logging out a browser session they also happen to carry a
    cookie for is a real if unusual case, and revoking a cookie that was not there is
    a no-op (``SessionService.revoke_by_cookie``)."""
    cookie_value = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_value:
        services.sessions.revoke_by_cookie(actor, cookie_value)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    return response


# -------------------------------------------------------------------------------- oidc


@router.get("/oidc/start")
def oidc_start(settings: SettingsDep, services: ServicesDep) -> Response:
    """Begin the authorization-code + PKCE flow (FR-I1). ``state`` and
    ``code_verifier`` are generated by ``OidcFlowService.start`` and stored, together,
    in the ``gw_oidc_tx`` cookie (DD-10) — nowhere else — so the callback can complete
    PKCE and compare ``state`` with no server-side transaction store."""
    transaction = services.oidc_flow.start(_redirect_uri(settings))
    response = RedirectResponse(url=transaction.authorization_url, status_code=302)
    response.set_cookie(
        OIDC_TRANSACTION_COOKIE_NAME,
        f"{transaction.state}:{transaction.code_verifier}",
        max_age=OIDC_TRANSACTION_MAX_AGE_SECONDS,
        **cookie_kwargs(settings, http_only=True, path=OIDC_CALLBACK_PATH_PREFIX),
    )
    return response


@router.get("/oidc/callback")
def oidc_callback(
    request: Request,
    settings: SettingsDep,
    services: ServicesDep,
    request_id: RequestIdDep,
    code: str | None = None,
    state: str | None = None,
) -> Response:
    """Complete the flow (FR-I1): compare ``state`` before exchanging anything (a
    mismatched or missing ``state`` must not reach the token endpoint), exchange the
    code, verify the ID token through ``OidcVerifier``, provision or refresh the
    principal through ``login_with_id_token``, and issue a session.
    """
    transaction_cookie = request.cookies.get(OIDC_TRANSACTION_COOKIE_NAME) or ""
    saved_state, _, code_verifier = transaction_cookie.partition(":")
    if not transaction_cookie or not state or not hmac.compare_digest(saved_state, state):
        raise AuthenticationFailedError(
            "This sign-in link has expired, was already used, or was not started in "
            "this browser. Start signing in again."
        )
    if not code:
        raise AuthenticationFailedError(
            "The identity provider did not return an authorization code."
        )
    id_token = services.oidc_flow.exchange_code(code, code_verifier, _redirect_uri(settings))
    # The provisioning write's actor is the pre-authentication bootstrap principal at
    # ``read`` scope (DD-15), matching the edge's own exempt actor: the write
    # creates or refreshes the very principal whose identity is not yet known to this
    # request, and nothing on this path reads the actor's scope or role. Building it
    # here rather than taking ``request.state.actor`` keeps the two halves visibly the
    # same decision. No route module names the admin-scoped constructor any more, and
    # a grep for it under ``routes/`` is the check that a third one has not appeared.
    principal, _identity = services.authn.login_with_id_token(anonymous_actor(request_id), id_token)
    actor = _login_actor(request_id, principal.id, principal.role)
    prior_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    minted = services.sessions.issue(
        actor, principal, replacing_cookie=prior_cookie, note="oidc login"
    )
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(OIDC_TRANSACTION_COOKIE_NAME, path=OIDC_CALLBACK_PATH_PREFIX)
    _set_session_cookies(response, settings, minted.cookie_value, minted.csrf_value)
    return response
