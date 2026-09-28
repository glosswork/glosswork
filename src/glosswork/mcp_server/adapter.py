"""The MCP adapter core (DD-3, DD-4, DD-8, FR-M4 through FR-M6).

Everything an MCP tool needs that is not a service call lives here: resolving the
caller's identity from the bearer token, resolving and registering the agent label,
building the per-call ``ActorContext``, converting service results and domain
errors into ``CallToolResult`` values, and the single server middleware that filters
``tools/list`` and gates ``tools/call`` by scope. Tool functions themselves parse
their arguments, call the service layer, and shape results with the shared
envelopes; they contain no logic of their own.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.mcpserver.context import Context
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    CallToolResult,
    ContentBlock,
    ResourceLink,
    TextContent,
)
from pydantic import BaseModel

from glosswork.actor import AGENT_LABEL_HEADER, ActorContext, resolve_agent_label_id
from glosswork.auth import TokenIdentity, TokenRefusedError, TokenResolver, scope_allows
from glosswork.errors import (
    GlossworkError,
    InsufficientScopeError,
    InternalError,
    WorkspaceReadOnlyError,
    error_envelope,
)
from glosswork.logging import get_logger
from glosswork.mcp_server.catalog import ToolCatalog
from glosswork.scopes import refuse_capability_credential
from glosswork.services import ServiceBundle
from glosswork.services.attachments import ATTACHMENT_URI_SCHEME
from glosswork.services.usage import outcome

AUTHORIZATION_HEADER = "authorization"

# Every request method that returns data (DD-15). ``initialize`` is here because
# it discloses the full capability set and the onboarding instructions, and because a
# handshake is the natural place to demand the credential rather than the first call
# after it. Notifications are excluded by construction, not by name: they carry no
# ``request_id`` and take the non-gated early return below, so a compliant client that
# sends one before it sends a token does not see a protocol error it cannot act on.
# The three resource methods (DD-29) are here too. All three return data, so FR-M4
# covers them, and the gate is credential-only: ``read`` is the floor of
# every scope and there is no resource above it. They take their own early return in
# ``_dispatch`` rather than falling through the ``tools/call`` tail.
#
# The reason for that early return. It is tempting to say the tail "would be asking
# whether a tool named ``""`` is in scope". That sentence is true and its implication is
# false: the tail does ask, and the answer is harmless, because
# ``required_scope("")`` returns ``None`` so the scope check is skipped and
# ``writes("")`` returns ``False`` by the catalog's own documented rule that an
# unregistered name is not a write. Measured: gating by the set alone gives
# byte-identical refusals across all three credential states. The early return stays for
# a better reason, which is that the tail's safety is an accident of the current
# implementation rather than a contract. The tail exists to check a *tool name*; a method
# routed through it acquires a dependency on how it happens to treat a name it has never
# heard of.
RESOURCE_METHODS = frozenset({"resources/list", "resources/templates/list", "resources/read"})

# DD-15. Every remaining request method this server registers.
#
# Ungated, ``server/discover`` answers a request carrying no ``Authorization`` header at
# all with the full capability set and the complete onboarding instructions -- the same
# orientation document ``initialize`` is gated for disclosing, and 2,123 bytes of it.
# ``prompts/list`` answers with a result and the server's own name. ``prompts/get`` and
# ``subscriptions/listen`` reach their handlers. ``ping`` is registered but does not
# route on the streamable HTTP wire, so gating it costs nothing observable and it needs
# no exemption.
#
# The shape of this set matters more than its members. A denylist extended by naming one
# member at a time, with a test that pins the denylist rather than the server, misses a
# method and then misses the next one, because no test can find the next miss.
# ``test_every_registered_request_method_is_gated`` now reads the server's own handler
# registry, so a method that joins the server without joining this set turns red with no
# edit to any list. There is deliberately no exemption set: an exemption set is a second
# denylist, which is the thing being ended.
#
# Notifications are still excluded by construction and not by name. They carry no
# ``request_id`` and take the non-gated early return below, so a compliant client that
# sends one before it sends a token does not see a protocol error it cannot act on. The
# registry holds zero notification handlers today.
DISCOVERY_AND_PROMPT_METHODS = frozenset(
    {"ping", "prompts/get", "prompts/list", "server/discover", "subscriptions/listen"}
)

GATED_METHODS = (
    frozenset({"initialize", "tools/list", "tools/call"})
    | RESOURCE_METHODS
    | DISCOVERY_AND_PROMPT_METHODS
)


def header_value(headers: Mapping[str, str] | None, name: str) -> str | None:
    """Case-insensitive header lookup over whatever mapping the transport carries
    (Starlette's ``Headers`` on streamable HTTP; a plain dict on the test transport)."""
    if headers is None:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def ok(payload: dict[str, Any], links: list[ResourceLink] | None = None) -> CallToolResult:
    """A successful tool result: the payload as structured content, and the same
    JSON as text for clients that only read ``content``.

    ``links`` (DD-29) appends resource links after the text block. They are an
    MCP-only affordance and never touch ``structured_content``, so the two surfaces
    keep returning identical JSON and REST loses nothing by not having them.
    """
    content: list[ContentBlock] = [TextContent(type="text", text=json.dumps(payload, default=str))]
    if links:
        content.extend(links)
    return CallToolResult(content=content, structured_content=payload)


def attachment_link(doc: Mapping[str, Any]) -> ResourceLink:
    """One ``resource_link`` for an attachment, built from its own document.

    The four keys are read off an :func:`~glosswork.envelopes.attachment_doc`, never
    off a repository row: ``test_mcp_package_imports_no_repository_sqlalchemy_or_http_client``
    forbids a repository import under this package, and taking the values from the
    document the tool is already returning also makes the link and the structured
    content incapable of disagreeing about a filename or a size.

    The scheme is custom rather than ``https://`` on purpose. The specification reserves
    ``https://`` for a resource the client can fetch without the server, and this one
    needs a bearer; a custom scheme tells a host to read it through ``resources/read``.
    ``size`` is published so the host can weigh that read before making it.
    """
    return ResourceLink(
        type="resource_link",
        uri=f"{ATTACHMENT_URI_SCHEME}://{doc['id']}",
        name=str(doc["filename"]),
        mime_type=str(doc["content_type"]),
        size=int(doc["byte_size"]),
    )


def error_result(exc: GlossworkError) -> CallToolResult:
    """A domain error as a tool result the model can read: ``is_error`` set, the
    shared ``{code, message, details}`` envelope under ``structured_content["error"]``,
    and the actionable message as the text content (FR-M6). Never raised, so it
    never becomes a JSON-RPC protocol error the model would not see."""
    return CallToolResult(
        content=[TextContent(type="text", text=exc.message)],
        structured_content={"error": error_envelope(exc)},
        is_error=True,
    )


class McpAdapter:
    def __init__(
        self,
        get_services: Callable[[], ServiceBundle],
        resolver: TokenResolver,
        catalog: ToolCatalog,
    ) -> None:
        self._get_services = get_services
        self._resolver = resolver
        self._catalog = catalog
        self._logger = get_logger("glosswork.mcp")

    @property
    def services(self) -> ServiceBundle:
        return self._get_services()

    # ----------------------------------------------------------- per-call actor

    def identity(self, headers: Mapping[str, str] | None) -> TokenIdentity:
        """The caller's identity, and the one place this surface is closed to a
        capability credential (DD-16).

        ``/mcp`` is in ``scopes.SCOPE_EXEMPT_PATHS`` by design -- DD-8 puts the tool
        catalog's gate here, in the adapter -- so ``enforce_scope`` never runs for it and
        a route-only rule would leave the whole surface open. An upload ticket resolves
        to an ordinary ``write`` identity, which would see every write tool including
        the one that mints more tickets, so the refusal happens at the resolution seam
        every gated method and the resource override already funnel through. The rule
        itself is not restated here: this delegates to the same predicate
        ``enforce_scope`` calls, which is what keeps that predicate a capability's one reader.
        """
        identity = self._resolver.resolve(header_value(headers, AUTHORIZATION_HEADER))
        refuse_capability_credential(identity.capability, "The MCP endpoint")
        return identity

    def actor(self, ctx: Context, agent: str | None = None) -> ActorContext:
        """The DD-4 actor for one tool call: the resolver's principal and scope,
        ``surface: "mcp"``, a fresh request id, and the agent label resolved from
        the per-call ``agent`` parameter (wins, FR-M5) or the connection's
        ``X-Agent-Label`` header, auto-registered against the principal (FR-I6)."""
        return self.actor_from_headers(ctx.headers, agent)

    def actor_from_headers(
        self, headers: Mapping[str, str] | None, agent: str | None = None
    ) -> ActorContext:
        """:meth:`actor` over the headers alone. The ``resources/read`` override
        resolves its actor here rather than restating the rule, so a resource read is
        attributed exactly as a tool call is.

        This owns the header lookup -- it is the one place that knows the
        ``Mapping`` shape the transport hands it -- and delegates the FR-M5
        precedence rule and the FR-I6 registration to
        :func:`~glosswork.actor.resolve_agent_label_id`, the one function both
        surfaces share."""
        identity = self.identity(headers)
        label_id = resolve_agent_label_id(
            self.services.agent_labels,
            identity.principal_id,
            header_value(headers, AGENT_LABEL_HEADER),
            agent,
            token_label=identity.agent_label,
        )
        return ActorContext(
            principal_id=identity.principal_id,
            principal_type=identity.principal_type,
            agent_label_id=label_id,
            auth_method=identity.auth_method,
            surface="mcp",
            request_id=str(uuid.uuid4()),
            scope=identity.scope,
        )

    def call(
        self,
        run: Callable[[], dict[str, Any]],
        links: Callable[[dict[str, Any]], list[ResourceLink]] | None = None,
    ) -> CallToolResult:
        """Run one tool body; a domain error becomes an error result (FR-M6), and
        anything else becomes ``internal_error`` rather than ``str(exc)`` (DD-19).

        ``links`` derives resource links from the payload the body returned, so a
        tool never has to build a result itself and the link builder is inside the same
        two handlers the body is.

        Without the second handler an unclassified exception fell through to the SDK's
        own catch-all (``mcp/server/mcpserver/server.py``), which puts ``str(e)`` in
        the tool result. The traceback goes to the application log at ``error``, where
        an operator can find it by the request id the caller was given; the caller gets
        the id and nothing else.
        """
        try:
            payload = run()
            return ok(payload, links(payload) if links is not None else None)
        except GlossworkError as exc:
            return error_result(exc)
        except Exception:
            return error_result(self._unclassified())

    def _unclassified(self) -> InternalError:
        """Log one unclassified exception with its traceback and build the opaque
        error. Shared by both seams so the log line and the caller's answer cannot
        drift apart. The caller decides the *shape*; this decides the disclosure, which
        is the request id and nothing else."""
        request_id = str(uuid.uuid4())
        self._logger.error("mcp_unclassified_exception", request_id=request_id, exc_info=True)
        return InternalError(request_id)

    # ---------------------------------------------------------------- middleware

    async def middleware(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        """Scope visibility and gating (FR-M4, DD-8), before any tool dispatch.

        An unverifiable bearer token is refused with JSON-RPC ``INVALID_REQUEST``
        naming the accepted tokens; ``tools/list`` is filtered to the caller's
        scope (omitted, not refused); ``tools/call`` of a tool above the caller's
        scope answers ``insufficient_scope`` without touching the tool.
        """
        result = await self._classified(ctx, call_next)
        # The usage counter, and it is **outside** the ``try`` below rather than
        # inside it. That is not tidiness: ``_classified``'s ``except Exception``
        # answers ``tools/call`` with ``error_result(self._unclassified())``, so an
        # exception raised by counting code that sits inside it converts a call that
        # **succeeded** into ``internal_error`` for the caller -- every successful tool
        # call in the deployment, from one ``AttributeError``. Sitting outside makes
        # that structurally impossible, and ``_count_tool_call`` carries its own
        # ``try`` as well, because a counting failure must never fail a call.
        if ctx.method == "tools/call":
            self._count_tool_call(ctx, result)
        return result

    def _count_tool_call(self, ctx: ServerRequestContext[Any, Any], result: HandlerResult) -> None:
        """Record one ``tools/call`` against the usage counters (DD-39, FR-P10).

        The **one** call site in ``src/``, which ``tests/test_one_usage_counter.py``
        pins: a second one is how the boundary's two allowlists come to be applied in
        one place and not the other.

        Three things this deliberately does not do. It does not decide what may be
        written -- ``services/usage.py::counter_key`` does, from the catalog's answer and
        ``STATUS_BY_CODE``, so the tool name in the request never reaches a table. It
        does not read the result itself -- ``outcome()`` does, because the seam sees
        three different shapes and the commonest of them has no ``is_error`` attribute.
        And it does not touch a connection: recording is a dictionary increment under a
        lock, because a read tool called with no agent label opens zero write
        transactions today and a counter row per call would add a writer-lock
        acquisition to exactly the traffic a metering endpoint exists to watch.

        A refused token never arrives here at all: it raises ``MCPError`` out of
        ``_dispatch`` and returns nothing, so it is uncounted, and DD-39 records the
        consequence -- a workspace whose agents all present bad tokens reports zero tool
        calls and looks idle.
        """
        try:
            counted, error_code = outcome(result)
            if not counted:
                return
            name = str((ctx.params or {}).get("name", ""))
            self.services.usage.record_tool_call(
                tool_name=name,
                error_code=error_code,
                registered=self._catalog.required_scope(name) is not None,
            )
        except Exception:
            self._logger.warning("usage_counter_failed", exc_info=True)

    async def _classified(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        """``middleware``'s error classification, separated only so the usage counter
        above can sit outside its ``try``."""
        try:
            return await self._dispatch(ctx, call_next)
        except MCPError:
            # The gate's own refusal, and the SDK's own protocol errors. Re-raised
            # untouched: classifying them would change the shape every MCP client
            # already expects (the SDK re-raises them at the same seam).
            raise
        except Exception:
            # Everything ``call`` cannot see (DD-19): ``self.identity(headers)``
            # runs here, outside any tool closure, so a lock timeout or a
            # ``sqlite3.OperationalError`` from credential resolution reached ``str(e)``
            # before this handler existed. (``agent_labels.register_use`` does *not*
            # need this: it runs inside ``actor()``, which every tool calls from within
            # its ``run()`` closure, so ``call`` already covers it.)
            #
            # Pydantic argument coercion is **not** reachable from here and is not
            # meant to be: the SDK's catch-all lives in ``_handle_call_tool``, which is
            # the handler ``call_next`` invokes, strictly inside this middleware. Its
            # message echoes the caller's own argument against a schema ``tools/list``
            # publishes, so it discloses nothing and is left as the actionable error.
            #
            # The shape follows the method, because this seam spans all three gated
            # ones and only ``tools/call`` has a result type that can carry an error a
            # model reads (FR-M6). ``initialize`` and ``tools/list`` have no such shape,
            # so a ``CallToolResult`` there would be malformed and the SDK's own
            # handling of *that* is what would leak next; they get the JSON-RPC error
            # the transport already expects. The disclosure is identical either way.
            exc = self._unclassified()
            if ctx.method == "tools/call":
                return error_result(exc)
            raise MCPError(code=INTERNAL_ERROR, message=exc.message) from None

    async def _dispatch(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        """The gate proper. Separated from ``middleware`` only so the classifier there
        wraps this body and ``call_next`` alike in one ``try``."""
        if ctx.method not in GATED_METHODS:
            return await call_next(ctx)
        headers = getattr(ctx.request, "headers", None)
        try:
            identity = self.identity(headers)
        except TokenRefusedError as exc:
            raise MCPError(code=INVALID_REQUEST, message=str(exc)) from exc
        except InsufficientScopeError as exc:
            # DD-16. An upload ticket presented to ``/mcp``. Shaped like the
            # token refusal beside it rather than like a tool error, because it is one:
            # the credential does not reach this surface at all, so there is no tool
            # call to answer and every gated method must get the same answer. Left
            # uncaught it would reach ``middleware``'s classifier and become an opaque
            # ``internal_error``, which is right for a bug and wrong for a credential
            # being used where it does not belong.
            raise MCPError(code=INVALID_REQUEST, message=exc.message) from exc

        if ctx.method in RESOURCE_METHODS or ctx.method in DISCOVERY_AND_PROMPT_METHODS:
            # Credential-only, and its own return. The tail below is
            # ``tools/call`` shape: it reads ``params["name"]`` and asks the catalog about
            # it. For a method that is not ``tools/call`` that name is ``""``, which the
            # catalog today answers harmlessly, so falling through would work. It is not
            # relied on: that safety is a property of the catalog's current implementation
            # and not a contract it owes, and a method that depends on it breaks the day
            # the tail learns to treat an unknown name as something other than nothing.
            #
            # Credential-only is the whole check for these. There is no scope above
            # ``read`` to demand: discovery and the prompt list return what any resolvable
            # credential may see, and the point of the gate is that the session has one.
            return await call_next(ctx)

        if ctx.method == "initialize":
            # Nothing to filter and nothing to scope-check: the credential merely has
            # to resolve. Refusing here rather than after ``call_next`` is the SDK's
            # documented way to veto a handshake -- the post-chain handshake commit
            # reads the wire params, so ``initialize`` is observed but not rewritable.
            return await call_next(ctx)

        if ctx.method == "tools/list":
            result = await call_next(ctx)
            if isinstance(result, BaseModel):
                result = result.model_dump(by_alias=True, mode="json", exclude_none=True)
            if isinstance(result, dict) and isinstance(result.get("tools"), list):
                result["tools"] = [
                    tool
                    for tool in result["tools"]
                    if self._catalog.is_visible(str(tool.get("name")), identity.scope)
                ]
            return result

        params = ctx.params or {}
        name = str(params.get("name", ""))
        required = self._catalog.required_scope(name)
        if required is not None and not scope_allows(identity.scope, required):
            return error_result(InsufficientScopeError(name, required, identity.scope))
        if self._catalog.writes(name):
            # DD-38. After the scope check, as on REST, and before the tool body, so a
            # refused call runs nothing and registers no agent label. A tool result rather
            # than a JSON-RPC error: the model reads a tool result, and the subscribe URL
            # is in it. ``tools/list`` is untouched.
            try:
                self.services.workspace.refuse_write_if_read_only(name)
            except WorkspaceReadOnlyError as exc:
                return error_result(exc)
        return await call_next(ctx)
