"""REST scope enforcement (FR-I3, FR-M4's REST counterpart, DD-8).

The REST twin of the MCP catalog's ``ToolCatalog.tool(scope)``
(``src/glosswork/mcp_server/catalog.py``): a required scope is **declared exactly
once, where the route is registered**, and **compared centrally**, never inside a
handler body. Nothing under ``routes/`` reads ``ActorContext.scope`` at all — a
grep-backed test asserts that, because the failure mode this design exists to prevent
is not "someone forgets a check" but "every handler grows its own slightly different
one".

Declaration::

    @router.patch("/records/{ref}", dependencies=[require_scope("write")])
    def update_record(...): ...

Enforcement is :func:`enforce_scope`, which compares the declaration against the
credential's scope using ``auth.scope_allows`` — the same ``read < write < admin``
helper the MCP gate uses, not a second ordering — and raises
:class:`~glosswork.errors.InsufficientScopeError`, which the MCP gate raises too and
which maps onto the ``insufficient_scope`` envelope at 403. REST produces and exercises
``insufficient_scope`` exactly as MCP does.

:func:`route_scopes` is what makes the declaration checkable rather than promised. The
completeness meta-test walks it, the ``read``-PAT sweep walks it, and the cross-surface
parity test compares it against the MCP catalog.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

from glosswork.actor import ActorContext, Scope
from glosswork.auth import scope_allows
from glosswork.errors import ForbiddenError, InsufficientScopeError

SCOPE_ATTRIBUTE = "__gw_required_scope__"
ROLE_ATTRIBUTE = "__gw_required_role__"
CAPABILITY_ATTRIBUTE = "__gw_required_capability__"
# Where ``require_capability`` leaves the ticket's own ``access_tokens.id`` for the
# handler to hand to the service. The handler reads *this*, never ``actor.capability``:
# the capability is readable for authorization in one predicate only,
# and consuming the right row is a different question from whether the call is allowed.
CAPABILITY_CREDENTIAL_STATE = "gw_capability_credential_id"

# Paths that legitimately carry no scope declaration, each named explicitly rather
# than matched by pattern. A pattern would silently absorb a future
# route; this list has to be edited, and the meta-test asserts its exact contents, so
# widening it is a visible act rather than a side effect.
#
# - /healthz, /readyz: liveness and readiness, which a load balancer probes with no
#   credential at all (FR-P4).
# - /openapi.json, /docs: the API description and its viewer.
# - /mcp: the MCP surface, which runs its own DD-8 scope middleware over the tool
#   catalog; a second, route-level check here would be the "two orderings" this
#   module exists to avoid.
# - /assets, /{full_path:path}: the built frontend's static assets and the SPA
#   fallback. These serve the JavaScript bundle that then authenticates; requiring a
#   credential to fetch the login page is a bootstrap impossibility, not a policy.
# - /api/v1/auth/login, /api/v1/auth/oidc/start, /api/v1/auth/oidc/callback,
#   /api/v1/auth/modes (FR-I1): the pre-authentication auth-flow routes. A
#   ``require_scope`` declaration on a route nobody can be authenticated to yet would
#   either be vacuous (trivially satisfied by the edge's bootstrap actor, the same one
#   ``RequestContextMiddleware`` hands every credential-exempt path) or wrong — neither
#   is honest about what these routes check, which is nothing, by design. Logout
#   (``DELETE /api/v1/auth/session``) is not here: it runs only once a real credential
#   (session or PAT) has resolved and declares ``read`` like any other authenticated
#   route.
# - /api/v1/bootstrap (DD-37): exchanges ``GW_BOOTSTRAP_SECRET`` for the first
#   administrator's account and token. The first exemption here that is not an
#   auth-flow route, and the argument is the same one: on a deployment with no user in
#   it there is no credential to present, which is the state this route exists to end.
#   It is guarded by the secret rather than by a scope, and closes permanently once the
#   deployment has a user.
# - /api/v1/usage (DD-39, FR-P10): the operator's usage counts, guarded by
#   ``GW_OPERATOR_TOKEN`` in an ``X-Operator-Token`` header. The second exemption that is
#   not an auth-flow route, and the reverse of bootstrap's case: bootstrap has no
#   credential to present *yet*, where this route must never accept the credential the
#   edge knows how to resolve -- a workspace ``admin`` PAT opens every other ``/api/``
#   path and must not open this one. Declaring ``read`` here would be worse than vacuous:
#   the edge's anonymous actor carries ``read``, so the declaration would *pass* for a
#   caller presenting nothing at all.
SCOPE_EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/healthz",
        "/readyz",
        "/openapi.json",
        "/docs",
        "/mcp",
        "/assets",
        "/{full_path:path}",
        "/api/v1/auth/login",
        "/api/v1/auth/oidc/start",
        "/api/v1/auth/oidc/callback",
        "/api/v1/auth/modes",
        "/api/v1/bootstrap",
        "/api/v1/usage",
    }
)


# The writes that stay open while a workspace is read-only (DD-38), as
# ``(method, route template)``. Named rather than matched, for ``SCOPE_EXEMPT_PATHS``'
# reason, and pinned by equality in ``tests/test_read_only_mode.py``, so widening it is a
# visible edit to a test.
#
# - The backup, because it must keep running through the frozen days, and a person who
#   comes back to take their data needs it to have run.
# - Minting and revoking an access token: a person back to export connects an agent,
#   which needs a new token (and a token can only read while frozen), and revoking a
#   leaked one is security, which a freeze never blocks.
# - Changing one's own password, and deactivating a principal, for the same reason.
#   An administrator resetting *another* person's password stays refused; deactivating
#   that person is the containment action that stays open.
#
# Every other non-GET route that declares above ``read`` is refused. Credential-exempt
# routes (sign-in, OIDC, bootstrap, ``/mcp`` itself) never reach a scope dependency and
# so stay open by construction.
READ_ONLY_OPEN_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("POST", "/api/v1/admin/backup"),
        ("POST", "/api/v1/access-tokens"),
        ("DELETE", "/api/v1/access-tokens/{token_id}"),
        ("POST", "/api/v1/me/password"),
        ("DELETE", "/api/v1/principals/{principal_id}"),
    }
)


@dataclass(frozen=True, slots=True)
class RouteScope:
    method: str
    path: str
    name: str
    scope: Scope | None

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


@dataclass(frozen=True, slots=True)
class RouteRole:
    """One route's declared system role. Separate from
    :class:`RouteScope` because a role declaration is optional on every route and
    present on exactly twelve, where a scope declaration is required on all of them."""

    method: str
    path: str
    role: str

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


def refuse_capability_credential(capability: str | None, where: str) -> None:
    """The **one** predicate that reads a capability for authorization (DD-16).

    A capability token is a credential narrowed from "any write" to one named
    operation, so the default is closed: it is refused everywhere, and exactly one
    route opts back in with :func:`require_capability`. Every route in the system --
    including every route added later -- is therefore closed to a ticket without anyone
    having to remember to close it.

    Two callers, both at a gate and neither in a handler, a service or a tool body:
    :func:`enforce_scope`, which covers every declaring route, and
    ``McpAdapter.identity``, which covers the MCP surface. ``/mcp`` needs its own call
    because it is in :data:`SCOPE_EXEMPT_PATHS` by design -- DD-8 puts the tool
    catalog's gate in the adapter, and a second route-level scope check here would be
    the "two orderings" this module exists to avoid. Without that second call a ticket
    would resolve on ``/mcp`` to an ordinary ``write`` identity and see every write
    tool, including the one that mints more tickets.

    ``where`` is what was attempted, in the caller's own vocabulary -- a method and
    path, or the MCP endpoint -- because a refusal that names the wrong surface sends an
    agent to fix the wrong thing.
    """
    if capability is None:
        return
    raise InsufficientScopeError.for_capability(where, capability)


def enforce_scope(actor: ActorContext, required: Scope, method: str, path: str) -> None:
    """The one comparison. Called only from the two dependencies below and from nowhere
    else; a handler that wants to know the caller's scope is a design error, not a
    missing accessor.

    It also refuses a capability-bearing credential outright
    (:func:`refuse_capability_credential`). The refusal comes **first**: a ticket's
    scope is ``write``, so a scope comparison alone would admit it to every write route
    in the deployment.
    """
    refuse_capability_credential(actor.capability, f"{method} {path}")
    if not scope_allows(actor.scope, required):
        raise InsufficientScopeError.for_route(method, path, required, actor.scope)


def refuse_read_only_write(request: Request, method: str, path: str, declared: Scope) -> None:
    """The REST gate of read-only mode (DD-38).

    A REST call is a write when its method is not ``GET`` and its route declares a scope
    above ``read``: that selects exactly the state-changing routes today, where
    "declares above ``read``" alone would also refuse the full export, a ``GET`` that
    declares ``admin``. A write outside :data:`READ_ONLY_OPEN_ROUTES` is handed to
    ``WorkspaceService.refuse_write_if_read_only``, the one predicate that decides; this
    function only decides what a write *is* on this surface.

    Called from three places, all below and each after that path's own scope comparison,
    so a credential too weak for the route hears about its scope first. Because
    every role-declaring route lists its scope dependency before ``require_role``, and the
    grant check runs later still in the service, the order a caller sees is credential,
    capability, scope, read-only, role, grant.
    """
    if method == "GET" or declared == "read":
        return
    if (method, path) in READ_ONLY_OPEN_ROUTES:
        return
    request.app.state.services.workspace.refuse_write_if_read_only(f"{method} {path}")


def require_scope(scope: Scope) -> Any:
    """Declare a route's required scope, for use in ``dependencies=[...]``.

    Returns a ``Depends`` whose callable is tagged with the scope, so the declaration
    is readable both at request time (the dependency runs and enforces) and at
    introspection time (:func:`route_scopes` reads the tag off the registered route).
    One declaration, two readers — exactly the shape ``ToolCatalog`` has on the MCP
    side.
    """

    def dependency(request: Request) -> None:
        actor: ActorContext = request.state.actor
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        enforce_scope(actor, scope, request.method, path)
        refuse_read_only_write(request, request.method, path, scope)

    setattr(dependency, SCOPE_ATTRIBUTE, scope)
    return Depends(dependency)


def require_capability(capability: str, scope: Scope) -> Any:
    """Declare the one route an upload ticket may reach (DD-16).

    Replaces ``require_scope(scope)`` on that route rather than sitting beside it, and
    is tagged with the scope as well as the capability, so the completeness meta-test,
    the ``read``-PAT sweep and the cross-surface parity walk all keep seeing a declared
    scope there. A capability is a *narrowing* of the DD-11 credential ceiling, never a
    way around declaring one.

    Two callers reach this dependency and it treats them differently on purpose:

    - A **plain PAT** carries no capability, so the behaviour is exactly
      ``require_scope(scope)`` -- one call to the same :func:`enforce_scope`, whose
      capability refusal is a no-op for it. Nothing about the pre-021 route moves.
    - A **ticket** must name this exact capability, and the comparison happens here
      instead of in ``enforce_scope`` because this is the opt-in. The ticket's own
      ``access_tokens.id`` is then left on ``request.state`` for the handler, which is
      how the upload marks the right row consumed without reading ``actor.capability``
      itself.
    """

    def dependency(request: Request) -> None:
        actor: ActorContext = request.state.actor
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        held = actor.capability
        if held is None:
            enforce_scope(actor, scope, request.method, path)
            refuse_read_only_write(request, request.method, path, scope)
            setattr(request.state, CAPABILITY_CREDENTIAL_STATE, None)
            return
        if held != capability:
            # A ticket for some *other* capability. Unreachable today, because the product mints
            # exactly one value, and it uses the capability refusal rather than the scope
            # one so that a second value added later reads correctly instead of claiming
            # a scope named 'attachment_upload' was required.
            refuse_capability_credential(held, f"{request.method} {path}")
        if not scope_allows(actor.scope, scope):
            raise InsufficientScopeError.for_route(request.method, path, scope, actor.scope)
        # This branch never runs ``enforce_scope``, so the read-only gate is called
        # here as well: without it a ticket minted before a freeze, still inside its life,
        # would upload into the frozen workspace after the restart.
        refuse_read_only_write(request, request.method, path, scope)
        setattr(request.state, CAPABILITY_CREDENTIAL_STATE, actor.credential_id)

    setattr(dependency, SCOPE_ATTRIBUTE, scope)
    setattr(dependency, CAPABILITY_ATTRIBUTE, capability)
    return Depends(dependency)


def declared_capability(route: BaseRoute) -> str | None:
    """The capability declared on ``route``, or None when it declares none."""
    for dependency in getattr(route, "dependencies", ()) or ():
        call: Callable[..., Any] | None = getattr(dependency, "dependency", None)
        found = getattr(call, CAPABILITY_ATTRIBUTE, None)
        if found is not None:
            return found  # type: ignore[no-any-return]
    return None


def route_capabilities(app: FastAPI) -> list[tuple[str, str, str]]:
    """Every route that accepts a capability credential, as ``(method, path, capability)``.

    :func:`route_scopes`' counterpart, and the thing a test pins by equality over the
    real route table -- the way the body-cap exemptions are pinned. ``capability`` is a
    general mechanism and the product mints exactly one value of it; a second is a new
    decision with its own blast-radius argument, and a test that has to be edited is
    what makes adding one deliberate rather than incidental.
    """
    entries: list[tuple[str, str, str]] = []
    for route in flatten_routes(app):
        path = getattr(route, "path", None)
        capability = declared_capability(route)
        if path is None or capability is None:
            continue
        methods: Iterable[str] = getattr(route, "methods", None) or ["GET"]
        for method in sorted(methods):
            if method in ("HEAD", "OPTIONS"):
                continue
            entries.append((method, path, capability))
    return entries


def require_role(role: str) -> Any:
    """Declare a route's required **system role**, beside :func:`require_scope`.

    Schema editing is not a system-wide capability, so ``creator`` needs ``admin``
    credential scope -- which would otherwise open ``/admin/export`` to it. The fix
    declares the thing that is actually true of these twelve routes rather than
    weakening the schema routes' declaration, which is what keeps the load-bearing claim
    ("no route's declared scope is lowered") true::

        @router.get("/admin/export", dependencies=[require_scope("admin"), require_role("admin")])

    Built exactly like ``require_scope``: tagged with an attribute, declared once at
    registration, compared centrally, readable by introspection. This one needs a
    ``principals`` read because ``ActorContext`` carries no role; that read happens
    on twelve routes, none of them on a hot path.
    """

    def dependency(request: Request) -> None:
        actor: ActorContext = request.state.actor
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        principal = request.app.state.services.principals.get_principal_or_none(actor.principal_id)
        actual = principal.role if principal is not None else "unknown"
        if actual != role:
            raise ForbiddenError.for_role(request.method, path, role, actual)

    setattr(dependency, ROLE_ATTRIBUTE, role)
    return Depends(dependency)


def declared_role(route: BaseRoute) -> str | None:
    """The system role declared on ``route``, or None when it declares none."""
    for dependency in getattr(route, "dependencies", ()) or ():
        call: Callable[..., Any] | None = getattr(dependency, "dependency", None)
        role = getattr(call, ROLE_ATTRIBUTE, None)
        if role is not None:
            return role  # type: ignore[no-any-return]
    return None


def route_roles(app: FastAPI) -> list[RouteRole]:
    """Every route that declares a required role, as ``(method, path, role)``.

    :func:`route_scopes`' counterpart. The meta-test asserts this is *exactly* section
    8's twelve entries, by the same argument ``SCOPE_EXEMPT_PATHS`` is asserted exactly:
    a pattern would silently absorb a future route, and a list has to be edited.
    """
    entries: list[RouteRole] = []
    for route in flatten_routes(app):
        path = getattr(route, "path", None)
        role = declared_role(route)
        if path is None or role is None:
            continue
        methods: Iterable[str] = getattr(route, "methods", None) or ["GET"]
        for method in sorted(methods):
            if method in ("HEAD", "OPTIONS"):
                continue
            entries.append(RouteRole(method=method, path=path, role=role))
    return entries


def declared_scope(route: BaseRoute) -> Scope | None:
    """The scope declared on ``route``, or None when it declares none."""
    for dependency in getattr(route, "dependencies", ()) or ():
        call: Callable[..., Any] | None = getattr(dependency, "dependency", None)
        scope = getattr(call, SCOPE_ATTRIBUTE, None)
        if scope is not None:
            return scope  # type: ignore[no-any-return]
    return None


def flatten_routes(app: FastAPI) -> list[BaseRoute]:
    """Every leaf route on ``app``, following ``include_router`` wrappers.

    ``fastapi==0.141`` keeps an included router as a single wrapper object on
    ``app.routes`` rather than splicing its routes in, so a naive walk of
    ``app.routes`` sees six wrappers and no endpoints. The completeness meta-test is
    only worth anything if it actually reaches every registered route, so the walk
    descends rather than assuming a flat list.
    """
    leaves: list[BaseRoute] = []

    def visit(routes: Iterable[BaseRoute]) -> None:
        for route in routes:
            nested = getattr(route, "original_router", None)
            if nested is not None:
                visit(nested.routes)
                continue
            leaves.append(route)

    visit(app.routes)
    return leaves


def route_scopes(app: FastAPI) -> list[RouteScope]:
    """Every registered API route paired with its declared scope.

    One entry per (method, path): a route registered for several methods yields one
    entry per method, because ``GET`` and ``DELETE`` on the same path are two
    capabilities with two answers. HEAD and OPTIONS are dropped — FastAPI adds HEAD
    alongside GET automatically, so including it would double every read route in the
    sweep without testing anything new.
    """
    entries: list[RouteScope] = []
    for route in flatten_routes(app):
        path = getattr(route, "path", None)
        if path is None or path in SCOPE_EXEMPT_PATHS:
            continue
        methods: Iterable[str] = getattr(route, "methods", None) or ["GET"]
        scope = declared_scope(route)
        name = getattr(route, "name", "") or ""
        for method in sorted(methods):
            if method in ("HEAD", "OPTIONS"):
                continue
            entries.append(RouteScope(method=method, path=path, name=name, scope=scope))
    return entries


def undeclared_routes(app: FastAPI) -> list[RouteScope]:
    """Registered routes with no scope declaration. The completeness meta-test asserts
    this is empty, so adding a route without a scope fails the suite rather than
    shipping an unprotected endpoint."""
    return [entry for entry in route_scopes(app) if entry.scope is None]


def api_routes(app: FastAPI) -> list[APIRoute]:
    return [route for route in flatten_routes(app) if isinstance(route, APIRoute)]
