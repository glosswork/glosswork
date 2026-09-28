"""Request-scoped actor context, required on every write path (DD-4).

Constructed at the edge (middleware, MCP server, CLI) and passed into every service
write method, so attribution is threaded through every write and never has to be
retrofitted with gaps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    # A runtime import here would pull in the ``glosswork.services`` package, whose
    # ``__init__.py`` imports service modules that import ``glosswork.actor`` back
    # ``middleware.py`` solves the identical cycle for ``ServiceBundle`` the
    # same way.
    from glosswork.services.agent_labels import AgentLabelService

PrincipalType = Literal["user", "service_account"]
AuthMethod = Literal["session", "pat"]
Surface = Literal["ui", "api", "mcp"]
Scope = Literal["read", "write", "admin"]
# DD-11: the third authorization axis's vocabulary. ``Scope`` extended at the
# bottom with an explicit deny, not a second ordering beside it — ``auth.SCOPE_ORDER``
# is *derived* from ``auth.LEVEL_ORDER`` rather than declared twice. ``none`` exists so
# a type left open by ``object_types.default_level`` can still exclude one principal.
Level = Literal["none", "read", "write", "admin"]

# Seeded by the initial migration (DD-4). Fixed so tests and the pre-authentication
# paths can attribute writes deterministically.
BOOTSTRAP_PRINCIPAL_ID = "00000000-0000-4000-8000-000000000001"


@dataclass(frozen=True, slots=True)
class ActorContext:
    principal_id: str
    principal_type: PrincipalType
    agent_label_id: str | None
    auth_method: AuthMethod
    surface: Surface
    request_id: str
    scope: Scope
    # DD-16. A **capability token** is an ordinary ``access_tokens`` row whose
    # ``capability`` narrows the DD-11 credential ceiling from "any write" to one named
    # operation. It only ever narrows: it grants nothing, carries no object-type level of
    # its own, and there is no path by which it does more than the PAT that minted it.
    #
    # The rule is read for authorization in exactly one predicate,
    # ``scopes.refuse_capability_credential``, which ``enforce_scope`` and
    # ``McpAdapter.identity`` both call, so every route and the whole MCP surface are
    # closed to a ticket by construction and one dependency opts a single route back in.
    #
    # ``credential_id`` is the ``access_tokens.id`` behind the presented bearer. Without
    # it the edge knows a request holds a ticket but not *which* one, and the upload
    # could not mark the right row consumed. It is populated for every resolved PAT, not
    # only for tickets, because it is a fact about the credential rather than about the
    # capability.
    capability: str | None = None
    credential_id: str | None = None


# The one spelling of the header both surfaces read.
# ``tests/test_one_agent_label_resolver.py`` fails if a second definition appears.
AGENT_LABEL_HEADER = "x-agent-label"


def resolve_agent_label_id(
    labels: AgentLabelService,
    principal_id: str,
    header: str | None,
    override: str | None = None,
    token_label: str | None = None,
) -> str | None:
    """The one FR-M5 precedence rule, shared by every surface that fills in an agent
    label: a non-blank ``override`` (a tool's per-call ``agent`` parameter) wins;
    otherwise a non-blank ``header`` (the connection's or request's
    :data:`AGENT_LABEL_HEADER`); otherwise the non-blank ``token_label`` stored on the
    presented credential; otherwise no label at all. Blank and absent are the same thing
    on all three.

    ``token_label`` is the third source (DD-17). With the header alone, attribution would
    not reach this deployment from Claude's connector dialog, which accepts only header
    names Anthropic has approved -- that name is not one of them -- so every agent
    connected through the dialog would be attributed to nobody. A token minted for one
    named tool carries the label, which also covers harnesses that cannot send an extra
    header at all. It is last in the order because it is the least specific statement:
    a caller that says something about *this* call outranks a fact about the credential.

    Keeping the third source inside this one function is what preserves DD-17: every
    surface inherits the new precedence without a second rule appearing anywhere.

    When a label is resolved, it is auto-registered against ``principal_id``
    (FR-I6) via :meth:`AgentLabelService.register_use`, which is the sole call site
    this repository allows (``tests/test_one_agent_label_resolver.py``). A malformed
    label (blank after stripping, or over ``MAX_LABEL_LENGTH``) is refused there with
    ``ValidationFailedError`` rather than silently ignored. A malformed *token* label
    cannot reach here: it is refused at mint instead, because a bad
    label stored on a token would otherwise refuse every later call that token made.
    """
    label = override if override is not None and override.strip() else None
    if label is None:
        label = header if header is not None and header.strip() else None
    if label is None:
        label = token_label if token_label is not None and token_label.strip() else None
    if label is None:
        return None
    return labels.register_use(principal_id, label).id


def bootstrap_actor(request_id: str, surface: Surface = "api") -> ActorContext:
    """The pre-authentication actor for the deployment's own writes (DD-4), at ``admin`` scope.

    No request path calls this (DD-15): its callers are the lifespan seed
    (``app.py``) and the operator CLI (``admin.py``), both of which run with the
    deployment's own authority and no credential to resolve. A request that has no
    credential — or none *yet*, on the five ``middleware.AUTH_PUBLIC_PATHS`` routes —
    uses :func:`anonymous_actor` instead, so that a path added to that set fails
    closed at ``require_scope`` rather than answering as a full administrator.
    """
    return ActorContext(
        principal_id=BOOTSTRAP_PRINCIPAL_ID,
        principal_type="service_account",
        agent_label_id=None,
        auth_method="pat",
        surface=surface,
        request_id=request_id,
        scope="admin",
    )


def anonymous_actor(request_id: str, surface: Surface = "api") -> ActorContext:
    """The actor for a request that carries no resolved credential (DD-15).

    Same bootstrap principal as :func:`bootstrap_actor`, so the login and OIDC
    provisioning writes keep their audit attribution, but at ``scope="read"``: the
    credential-exempt edge is a reader, never an administrator. Every ``require_role``
    route also declares ``admin`` scope (``tests/test_rest_scope_enforcement.py``
    ``test_every_role_declaring_route_also_declares_admin_scope``), so lowering the
    scope alone is sufficient — a path that drifts into ``AUTH_PUBLIC_PATHS`` is
    refused ``insufficient_scope`` on every write and admin route rather than admitted.
    """
    return ActorContext(
        principal_id=BOOTSTRAP_PRINCIPAL_ID,
        principal_type="service_account",
        agent_label_id=None,
        auth_method="pat",
        surface=surface,
        request_id=request_id,
        scope="read",
    )
