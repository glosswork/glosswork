"""The MCP server: an adapter over the service layer (PRD section 6.6, DD-3, DD-8).

``create_mcp_server`` registers the whole generic tool catalog once (FR-M3) and
installs the scope middleware; the caller mounts ``streamable_http_app()`` at
``/mcp`` next to the REST routes (FR-M1) or drives the server directly with an
in-memory ``mcp.Client`` in tests. Services are looked up lazily through
``get_services`` because the application constructs them in its lifespan, after the
server has been built and mounted.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import anyio
from mcp.server import MCPServer
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.mcpserver.context import Context
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, INVALID_REQUEST, InputRequiredResult, ResourceTemplate
from pydantic import AnyUrl

from glosswork.auth import TokenResolver
from glosswork.errors import GlossworkError, error_envelope
from glosswork.mcp_server.adapter import GATED_METHODS, McpAdapter
from glosswork.mcp_server.catalog import READ_TOOLS_ABOVE_READ_SCOPE, ToolCatalog
from glosswork.mcp_server.tools_admin import register_admin_tools
from glosswork.mcp_server.tools_read import register_read_tools
from glosswork.mcp_server.tools_write import register_write_tools
from glosswork.services import ServiceBundle
from glosswork.services.attachments import (
    ATTACHMENT_URI_SCHEME,
    ATTACHMENT_URI_TEMPLATE,
)

# Re-exported so the credential gate's method set can be pinned by equality from a
# test without importing ``mcp_server.adapter`` -- which
# ``test_no_test_module_imports_the_mcp_tool_modules`` forbids, its pattern being
# ``glosswork\.mcp_server\.`` with the trailing dot. Same standing as
# ``create_mcp_server``: an import of the package, not of a tool module.
# ``INSTRUCTIONS`` and ``INSTRUCTIONS_BUDGET`` are here for the same reason and under the
# same rule: a test reads them without importing a tool module. So is
# ``READ_TOOLS_ABOVE_READ_SCOPE``, so read-only mode's pinned set is read the same way.
__all__ = [
    "ATTACHMENT_TEMPLATE",
    "GATED_METHODS",
    "INSTRUCTIONS",
    "INSTRUCTIONS_BUDGET",
    "READ_TOOLS_ABOVE_READ_SCOPE",
    "GlossworkMcpServer",
    "create_mcp_server",
]

# DD-30. The ceiling on ``INSTRUCTIONS``, in characters.
#
# **Measured, not chosen.** ``instructions`` reaches a model through a window whose size
# the server does not control and cannot detect. Observed on 2026-09-06: one host delivered
# exactly 2,048 characters of a 3,319-character string into a session's system prompt,
# cut mid-word with a truncation marker; the Files paragraph began at offset 2,078 and
# missed the window by thirty characters, taking the agent-label paragraph with it.
#
# 2,048 is that host's behaviour on that day and may change in either direction, so the
# budget is 2,000: the margin is the whole point. The rule this enforces is DD-30 --
# ``instructions`` is an *index*, and anything load-bearing goes in
# ``describe_capabilities``, whose result is never truncated. Raising this number is the
# wrong fix for a paragraph that will not fit.
INSTRUCTIONS_BUDGET = 2000

_ATTACHMENT_URI_PREFIX = f"{ATTACHMENT_URI_SCHEME}://"

ATTACHMENT_TEMPLATE = ResourceTemplate(
    uri_template=ATTACHMENT_URI_TEMPLATE,
    name="attachment",
    title="Attachment contents",
    description=(
        "The bytes of one attachment, by its UUID. Text types (markdown, plain text, "
        "CSV, HTML, JSON, XML) come back as text; everything else comes back as a "
        "blob carrying its own MIME type, which your host may render or ignore. Get "
        "an id from get_record with include=['attachments'], or from get_attachment, "
        "and read size on the resource link before reading a large one. Requires the "
        "same bearer token as every tool call."
    ),
)

# DD-29. A domain error on the ``resources/read`` seam has one shape, the
# JSON-RPC error, because that method has no ``is_error`` result the way ``tools/call``
# does. ``not_found`` takes the code the SDK itself uses for an unknown resource; every
# other domain refusal takes the code the credential gate already uses. The envelope
# rides in ``data`` so a client reads the same ``{code, message, details}`` it reads on
# a tool error, and the message is the service's own, verbatim (FR-M6).
_RESOURCE_ERROR_CODES: dict[str, int] = {"not_found": INVALID_PARAMS}

SERVER_NAME = "glosswork"

# DD-30. An **index**, not a manual. Its job is to name the tool that holds the
# manual and to carry the handful of rules that change how the very first call is made;
# everything else lives in ``describe_capabilities``, whose result is never truncated,
# is fetched on demand rather than carried in every request, and is readable at ``read``
# scope so the agent that has to act on it can. Under ``INSTRUCTIONS_BUDGET`` by a
# tested assertion, because the delivery window is not ours to control.
INSTRUCTIONS = """\
Glosswork is a schema-flexible record store shared by humans and AI agents. Object \
types and fields are user-defined, so never assume a field name or an option value.

Start with describe_capabilities. It is this endpoint's manual: field types and the \
operators legal on each, the filter grammar, date tokens, key rules, every limit, and \
how files work. It takes no arguments and is readable at any scope. Then \
list_object_types, then describe_object_type for the type you need, then build a \
query_records filter from the keys, options and operators it returned. Use date tokens \
(@today, @today-7d) and @me rather than computing dates or looking up your own \
identity. When the question is about meaning, or the answer may live only in a comment, \
use search and read index_lag before trusting that very recent writes are covered.

Errors are written for you and name the tool to call next. Read the message and correct \
the call rather than retrying it unchanged.

Writes: read a record before changing it and pass its version as expected_version; \
dry-run bulk_update_records before applying it; set relation fields with link_records, \
not values. A destructive schema change returns a proposal a human approves in the UI, \
and there is no tool to approve one.

Files are handles, never bytes: never base64 a file into a tool argument. \
create_text_attachment stores text you wrote yourself. For any other file, call \
create_attachment_upload and it returns a ready-to-use URL and a short-lived credential \
to POST the bytes to; then put the returned id on a record with update_record.

Your agent label attributes your writes in the audit trail. It is metadata, not a \
security boundary: authorization is the bearer token's scope, and tools above it are \
not listed.
"""


class GlossworkMcpServer(MCPServer):
    """``MCPServer`` plus the one resource template, ``attachment://{attachment_id}``.

    An ``MCPServer`` subclass rather than the ``@resource`` decorator, and the reason is
    measured: ``resources/templates.py::create_resource`` builds every
    ``FunctionResource`` with ``mime_type=self.mime_type``, one type fixed at
    registration, so a decorator-registered template cannot serve one attachment as
    ``application/pdf`` and the next as ``text/markdown``. :meth:`read_resource` returns
    ``ReadResourceContents`` values that each carry their own type, and the SDK's
    ``_handle_read_resource`` maps ``str`` to a text content and ``bytes`` to a blob.

    The service still does the work. This is an adapter: it resolves the actor, calls
    ``AttachmentService.read_content``, and shapes errors.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._adapter: McpAdapter | None = None

    def bind(self, adapter: McpAdapter) -> None:
        """Wire the adapter in after construction. The adapter needs a ``ToolCatalog``,
        which needs the server, so the cycle is broken here rather than by giving the
        server a second way to reach the services."""
        self._adapter = adapter

    async def list_resource_templates(self) -> list[ResourceTemplate]:
        templates = await super().list_resource_templates()
        return [ATTACHMENT_TEMPLATE, *templates]

    async def read_resource(
        self, uri: AnyUrl | str, context: Context[Any, Any] | None = None
    ) -> Iterable[ReadResourceContents] | InputRequiredResult:
        text = str(uri)
        if not text.startswith(_ATTACHMENT_URI_PREFIX):
            return await super().read_resource(uri, context)
        attachment_id = text[len(_ATTACHMENT_URI_PREFIX) :]
        adapter = self._adapter
        assert adapter is not None, "create_mcp_server binds the adapter before serving"
        headers = context.headers if context is not None else None

        def read() -> tuple[str, str | bytes]:
            actor = adapter.actor_from_headers(headers)
            row, content = adapter.services.attachments.read_content(actor, attachment_id)
            return row.content_type, content

        try:
            # Offloaded, because ``read_resource`` is ``async`` while every tool body in
            # this catalog is sync only by virtue of the SDK running it through this same
            # ``anyio.to_thread.run_sync`` (``utilities/func_metadata.py``). Calling the
            # service inline would put a database read and a whole-blob disk read on the
            # event loop, which no other path in this server does.
            content_type, content = await anyio.to_thread.run_sync(read)
        except GlossworkError as exc:
            # Caught here so the service's own message survives. Left uncaught, the
            # adapter middleware would classify it as unclassified and answer with a
            # request id and nothing else, which is right for a bug and wrong for
            # "no such attachment". An *unclassified* exception is still
            # the middleware's: the two ``InternalError`` seams (DD-19) stay two.
            raise MCPError(
                code=_RESOURCE_ERROR_CODES.get(exc.code, INVALID_REQUEST),
                message=exc.message,
                data={"error": error_envelope(exc)},
            ) from None
        return [ReadResourceContents(content=content, mime_type=content_type)]


def create_mcp_server(
    get_services: Callable[[], ServiceBundle], resolver: TokenResolver
) -> MCPServer:
    """Build the server against a ``TokenResolver`` (DD-8). Tool registration never
    consults the resolver; only the middleware does, per request."""
    server = GlossworkMcpServer(name=SERVER_NAME, instructions=INSTRUCTIONS)
    catalog = ToolCatalog(server)
    adapter = McpAdapter(get_services, resolver, catalog)
    server.bind(adapter)
    register_read_tools(catalog, adapter)
    register_write_tools(catalog, adapter)
    register_admin_tools(catalog, adapter)
    server.middleware.append(adapter.middleware)
    return server
