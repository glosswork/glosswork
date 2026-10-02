"""Request-ID middleware and application-emitted JSON access logs (FR-P5).

Uvicorn's access logger is disabled (its lines are not JSON); the application emits
one structured access line per request, carrying the request id and, where an actor
is bound, principal, agent label, and surface.

The session-cookie branch and CSRF enforcement (DD-9, DD-10) live in the same edge
that resolves a bearer token, rather than as a second middleware: both
answer the identical question ("who is this, and does this request need a CSRF
check"), and one place answering it is what keeps the two credential types from
disagreeing about anything.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any

import structlog
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from glosswork.actor import (
    AGENT_LABEL_HEADER,
    ActorContext,
    Surface,
    anonymous_actor,
    resolve_agent_label_id,
)
from glosswork.auth import TokenRefusedError, TokenResolver
from glosswork.cookies import CSRF_HEADER_NAME, SAFE_METHODS, SESSION_COOKIE_NAME
from glosswork.errors import (
    CsrfFailedError,
    GlossworkError,
    PayloadTooLargeError,
    error_envelope,
    http_status_for,
)
from glosswork.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from glosswork.services import ServiceBundle

REQUEST_ID_HEADER = b"x-request-id"
AUTHORIZATION_HEADER = b"authorization"
COOKIE_HEADER = b"cookie"
# Derived from ``actor.AGENT_LABEL_HEADER`` rather than a second literal, so the
# header's spelling stays defined exactly once.
AGENT_LABEL_HEADER_BYTES = AGENT_LABEL_HEADER.encode()
CONTENT_LENGTH_HEADER = b"content-length"
MCP_PATH = "/mcp"
API_PREFIX = "/api/"

# The two ``/api/`` routes exempt from the edge body cap, because each legitimately
# takes more than ``GW_MAX_REQUEST_BYTES`` and enforces its own ceiling instead: the
# attachment upload (``GW_MAX_ATTACHMENT_BYTES``) and the CSV import
# (``GW_MAX_CSV_IMPORT_BYTES``).
#
# The import path is **parameterized** -- ``/api/v1/object-types/{key}/import`` -- so
# this cannot be an exact-match frozenset the way ``AUTH_PUBLIC_PATHS`` is. A suffix
# match is what a parameterized path allows, and a suffix match is exactly the kind of
# set that grows silently, which is the failure mode DD-15 was written about. So it is
# pinned the way ``AUTH_PUBLIC_PATHS`` is: a test resolves these patterns
# against the application's real route table and asserts the matched set by equality.
BODY_CAP_EXEMPT_PATHS = frozenset({"/api/v1/attachments"})
BODY_CAP_EXEMPT_SUFFIXES = ("/import",)


def is_body_cap_exempt(path: str) -> bool:
    """True for the two routes that enforce their own, larger ceiling."""
    return path in BODY_CAP_EXEMPT_PATHS or path.endswith(BODY_CAP_EXEMPT_SUFFIXES)


async def read_capped_body(receive: Receive, cap: int) -> tuple[list[Message] | None, int]:
    """Buffer the request body, refusing the moment the running total exceeds ``cap``.

    The SDK's ``RequestBodySizeLimitMiddleware``
    (``mcp/server/streamable_http_manager.py``) is the reference implementation and this
    copies its shape rather than inventing one: count as ``http.request`` messages
    arrive, stop the moment the total goes over, and hand the buffered messages back to
    be replayed to the app otherwise. Buffering is bounded by ``cap`` by construction --
    the function returns as soon as the total exceeds it -- so this holds at most one
    capped body in memory.

    Returns ``(None, total)`` when the body is over the cap and ``(messages, total)``
    when it is not.
    """
    messages: list[Message] = []
    total = 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            # ``http.disconnect``: the client went away, and there is nothing to cap.
            return messages, total
        total += len(message.get("body", b""))
        if total > cap:
            return None, total
        if not message.get("more_body", False):
            return messages, total


def replay(messages: list[Message], receive: Receive) -> Receive:
    """A ``receive`` that hands back the already-buffered messages, then defers to the
    real one.

    Deferring rather than synthesizing an ``http.disconnect`` matters and is not
    defensive coding: ``StreamingResponse`` runs ``stream_response`` and
    ``listen_for_disconnect`` concurrently and cancels the stream the moment ``receive``
    reports a disconnect. A replay that invented one therefore cancelled every streaming
    response under the cap before it wrote a byte -- CSV export returned an empty 200.
    Falling through to the original ``receive`` blocks until the client *actually*
    disconnects, which is what an ASGI app is entitled to expect.
    """
    pending = iter(messages)

    async def _receive() -> Message:
        try:
            return next(pending)
        except StopIteration:
            return await receive()

    return _receive


class McpPayloadEnvelope:
    """Wraps the ``/mcp`` ASGI app so the SDK's 413 carries the project envelope.

    The MCP surface is body-capped whether or not anyone decides it: the SDK
    defaults ``max_request_body_size`` to 4 MiB, and its refusal is a bare 21-byte
    ``Request body too large`` in ``text/plain``. That is precisely the asymmetry DD-19
    removed for the unclassified case -- REST answering with an envelope while MCP
    answered in plain text -- so it is closed here rather than recorded as a limitation.

    The cap itself stays the SDK's to enforce; ``app.py`` passes it
    ``settings.max_request_bytes``, the same setting the REST edge reads, so the two
    surfaces cannot drift. This only reshapes the answer: on a 413 the
    SDK's ``http.response.start`` is swallowed, the project's JSON response is sent in
    its place, and every following body message from the SDK is dropped.

    ``/mcp`` is a ``Route`` rather than a ``Mount``, so there is
    exactly one attachment point for this wrapper.
    """

    def __init__(self, app: ASGIApp, max_request_bytes: int) -> None:
        self.app = app
        self._cap = max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        replacing = False

        async def send_wrapper(message: Message) -> None:
            nonlocal replacing
            if message["type"] == "http.response.start" and message["status"] == 413:
                replacing = True
                exc = PayloadTooLargeError(self._cap, "GW_MAX_REQUEST_BYTES")
                response = JSONResponse({"error": error_envelope(exc)}, status_code=413)
                await response(scope, receive, send)
                return
            if replacing:
                return
            await send(message)

        await self.app(scope, receive, send_wrapper)


# The pre-authentication auth-flow routes (FR-I1): none of them can require a
# credential, since none exists yet for login and the OIDC redirect, and
# `/api/v1/auth/modes` is the one thing a signed-out browser must be able to ask.
# `DELETE /api/v1/auth/session` (logout) is deliberately not here — it runs only once
# a real credential has resolved, exactly like any other authenticated route.
#
# `/api/v1/bootstrap` (DD-37) is the fifth entry and the first that is not an
# auth-flow route: it exchanges `GW_BOOTSTRAP_SECRET` for the first administrator's
# credential on a deployment that has no user yet, so by construction there is nothing
# to present. Its secret arrives in the body rather than in `Authorization`, precisely
# because the branch below reads that header as a PAT on every other `/api/` path.
#
# `/api/v1/usage` (DD-39) is the sixth, and it is here for the opposite reason to
# bootstrap's: not that no credential exists yet, but that the credential this route
# accepts is deliberately not one the edge can resolve. It reads `X-Operator-Token` and
# compares it with `GW_OPERATOR_TOKEN`, so a workspace `admin` PAT -- the highest thing a
# tenant can mint -- is refused exactly as an anonymous caller is.
#
# `/api/v1/auth/code/request` and `/api/v1/auth/code/verify` (change 9) are sign-in by
# emailed code: auth-flow routes like `/login`, with no credential yet by design.
#
# `/api/v1/operator/backup` (DD-39) is here for `/api/v1/usage`'s reason: it accepts the
# operator credential in `X-Operator-Token` and must refuse a workspace `admin` PAT as it
# refuses an anonymous caller, so the edge resolves nothing on it. The backup it streams
# is attributed to the anonymous actor built below, the deployment's own service account.
AUTH_PUBLIC_PATHS = frozenset(
    {
        "/api/v1/auth/login",
        "/api/v1/auth/oidc/start",
        "/api/v1/auth/oidc/callback",
        "/api/v1/auth/modes",
        "/api/v1/auth/code/request",
        "/api/v1/auth/code/verify",
        "/api/v1/bootstrap",
        "/api/v1/usage",
        "/api/v1/operator/backup",
    }
)


def is_api_path(path: str) -> bool:
    """True for the paths that carry a credential, and only those.

    Every scope-declaring route lives under ``/api/`` (asserted by a test, so this
    prefix and ``scopes.SCOPE_EXEMPT_PATHS`` cannot drift apart). Everything else —
    ``/healthz`` and ``/readyz``, which a load balancer probes with no credential at
    all (FR-P4); ``/openapi.json`` and ``/docs``; and the built frontend's assets and
    SPA fallback — must be reachable without one. The static bundle in particular is
    the thing that *carries* the credential, so requiring one to fetch it would be a
    bootstrap impossibility rather than a policy.
    """
    return path.startswith(API_PREFIX)


def _cookie_value(incoming: dict[bytes, bytes], name: str) -> str | None:
    raw = incoming.get(COOKIE_HEADER)
    if raw is None:
        return None
    jar = SimpleCookie()
    jar.load(raw.decode("latin-1"))
    morsel = jar.get(name)
    return None if morsel is None else morsel.value


def is_mcp_path(path: str) -> bool:
    """True for the MCP surface's exact path or anything nested under it.

    Shared with ``app.py``'s frontend SPA-fallback route so a GET under
    ``/mcp/...`` that the MCP SDK app itself didn't match (e.g. a typo'd
    sub-path) still 404s instead of being served the frontend's index.html.
    """
    return path == MCP_PATH or path.startswith(MCP_PATH + "/")


class RequestContextMiddleware:
    """Assigns a request id, binds it to the log context, constructs the edge
    ActorContext (DD-4), and emits one JSON access log line per request.

    For ``/api/*`` requests the ActorContext is derived from the same
    ``TokenResolver`` seam the MCP adapter uses (DD-8): the ``Authorization``
    header, when present, is resolved to a ``TokenIdentity`` and the actor built from
    it, rather than a hardcoded bootstrap actor. The ``/mcp`` path is unchanged: the
    MCP mount resolves its own per-call ActorContext from the bearer token, so the edge
    actor constructed here only labels that access log line's surface.

    There is one cookie branch, tried only when no ``Authorization`` header is present
    at all (DD-9's precedence rule: a header, valid or not, is authoritative whenever
    it exists, and only its absence falls through to the session cookie). The cookie
    branch calls ``SessionService.resolve`` directly rather than through
    ``TokenResolver`` — DD-9 is explicit that widening that protocol to take a whole
    request would reshape the seam the MCP adapter depends on for a browser-only
    concern — and builds the identical ``ActorContext`` shape, differing only in
    ``auth_method: "session"``. Immediately after, for any non-safe method
    (DD-10), the ``X-GW-CSRF`` header is compared against the session's stored hash;
    a bearer-authenticated request never reaches this check at all, which is what
    makes the exemption unconditional rather than a branch that could be gotten wrong.

    The resolver is ``PatTokenResolver``, so a request with no credential at
    all — no header and no cookie — is refused here with 401 before any route runs.
    Authorization (comparing the route's declared scope against ``ActorContext.scope``)
    stays the ``require_scope`` dependency in ``scopes.py``, not this edge.

    A bearer (PAT) identity also has its ``X-Agent-Label`` header resolved onto the actor, through
    the same :func:`~glosswork.actor.resolve_agent_label_id` the MCP adapter calls
    (FR-M5, FR-I6). A session identity keeps ``agent_label_id=None``: a cookie is a
    browser at a keyboard.
    """

    def __init__(
        self,
        app: ASGIApp,
        token_resolver: TokenResolver,
        get_services: Callable[[], ServiceBundle],
        max_request_bytes: int,
    ) -> None:
        self.app = app
        self.logger = get_logger("glosswork.access")
        self._token_resolver = token_resolver
        self._get_services = get_services
        # DD-18. The body cap lives at this edge because it is the only place that sees
        # every ``/api/`` request before any route, dependency or credential resolution
        # runs -- without it a 20 MB body to the unauthenticated login route is read and
        # parsed in full, and a proxy configuration is not guaranteed to set a limit.
        self._max_request_bytes = max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = {k.lower(): v for k, v in scope.get("headers", [])}
        request_id = incoming.get(REQUEST_ID_HEADER, b"").decode() or str(uuid.uuid4())
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        surface: Surface = "mcp" if is_mcp_path(path) else "api"
        if surface == "mcp" or not is_api_path(path) or path in AUTH_PUBLIC_PATHS:
            # Three cases, one behavior: the MCP mount resolves its own per-call
            # ActorContext from the bearer token (DD-8); an unauthenticated
            # non-``/api/`` path has no credential to resolve; and a pre-authentication
            # auth-flow route has no credential *yet*, by design. In all three the
            # edge actor here only labels the access log line; it is never used for
            # authorization.
            #
            # It is nonetheless a ``read``-scoped actor (DD-15), not the ``admin``
            # bootstrap one, because "never used for authorization" is a property of
            # today's five entries in ``AUTH_PUBLIC_PATHS`` rather than of this branch.
            # A fifth entry declaring a scope would otherwise satisfy
            # ``require_scope("admin")`` and ``require_role("admin")`` with no
            # credential at all; at ``read`` it fails closed instead.
            actor: ActorContext = anonymous_actor(request_id, surface=surface)
        else:
            authorization_header = incoming.get(AUTHORIZATION_HEADER, b"").decode() or None
            try:
                if authorization_header is not None:
                    identity = self._token_resolver.resolve(authorization_header)
                else:
                    cookie_value = _cookie_value(incoming, SESSION_COOKIE_NAME)
                    if cookie_value is None:
                        raise TokenRefusedError(
                            "This request carried no credential. Sign in, or send a "
                            "personal access token as 'Authorization: Bearer "
                            "gw_pat_...'.",
                            reason="missing",
                        )
                    services = self._get_services()
                    identity = services.sessions.resolve(cookie_value)
                    if method.upper() not in SAFE_METHODS:
                        csrf_header = incoming.get(CSRF_HEADER_NAME.encode(), b"").decode()
                        presented_csrf = csrf_header or None
                        if not services.sessions.verify_csrf(cookie_value, presented_csrf):
                            raise CsrfFailedError(
                                "This request's X-GW-CSRF header is missing or does "
                                "not match this session. Reload the page and try "
                                "again."
                            )
                # A session (cookie) identity keeps ``agent_label_id=None``, always: a
                # cookie is a browser at a keyboard, and PAT-only is exact parity with
                # ``/mcp``, which admits no other credential. A malformed label
                # raises ``ValidationFailedError`` here, inside this same ``try``, so it
                # is shaped by the ``error_envelope``/``http_status_for`` path below
                # rather than a response built by hand.
                agent_label_id: str | None = None
                if identity.auth_method == "pat":
                    header_label = incoming.get(AGENT_LABEL_HEADER_BYTES, b"").decode() or None
                    agent_label_id = resolve_agent_label_id(
                        self._get_services().agent_labels,
                        identity.principal_id,
                        header_label,
                        token_label=identity.agent_label,
                    )
            except GlossworkError as exc:
                # Shaped through the same ``error_envelope`` and status table as every
                # other domain error (failure is uniform across surfaces). The
                # ``@app.exception_handler(GlossworkError)`` in ``app.py`` cannot
                # reach this: credential resolution and CSRF enforcement happen in ASGI
                # middleware, which wraps the app rather than running inside it. One
                # mapping, read from two places, is what keeps the two from drifting.
                response = JSONResponse(
                    {"error": error_envelope(exc)}, status_code=http_status_for(exc)
                )
                await response(scope, receive, send)
                return
            # A cookie can only ever be presented by the browser, so a session-
            # authenticated request is the UI surface; a bearer token is the generic
            # REST API surface regardless of who holds it ("surface = 'ui'" for
            # cookie-authenticated writes, checked against a PAT write's "api" in the
            # same test).
            actor = ActorContext(
                principal_id=identity.principal_id,
                principal_type=identity.principal_type,
                agent_label_id=agent_label_id,
                auth_method=identity.auth_method,
                surface="ui" if identity.auth_method == "session" else "api",
                request_id=request_id,
                scope=identity.scope,
                # DD-16. Carried through unchanged from the resolver: the edge
                # never decides what a capability means, it only records what the
                # credential is. A session identity leaves both ``None``, as
                # ``bootstrap_actor`` and ``anonymous_actor`` do.
                capability=identity.capability,
                credential_id=identity.credential_id,
            )
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id
        scope["state"]["actor"] = actor
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        if is_api_path(path) and not is_body_cap_exempt(path):
            declared = incoming.get(CONTENT_LENGTH_HEADER, b"")
            if declared.isdigit() and int(declared) > self._max_request_bytes:
                # ``Content-Length`` first: an honest oversized upload is refused without
                # reading a byte of it.
                await self._refuse_oversized(scope, receive, send_wrapper, int(declared))
                return
            buffered, total = await read_capped_body(receive, self._max_request_bytes)
            if buffered is None:
                # No declared length, or a lying one: counted as the messages arrived.
                await self._refuse_oversized(scope, receive, send_wrapper, total)
                return
            receive = replay(buffered, receive)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            fields: dict[str, Any] = {
                "method": scope.get("method"),
                "path": scope.get("path"),
                "status": status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
                "principal_id": actor.principal_id,
                "agent_label": actor.agent_label_id,
                "surface": actor.surface,
                "scope": actor.scope,
            }
            self.logger.info("access", **fields)
            structlog.contextvars.clear_contextvars()

    async def _refuse_oversized(
        self, scope: Scope, receive: Receive, send: Send, received: int
    ) -> None:
        """A 413 in the project envelope, through the same ``error_envelope`` and status
        table as every other refusal shaped at this edge."""
        exc = PayloadTooLargeError(self._max_request_bytes, "GW_MAX_REQUEST_BYTES", received)
        response = JSONResponse({"error": error_envelope(exc)}, status_code=http_status_for(exc))
        await response(scope, receive, send)
