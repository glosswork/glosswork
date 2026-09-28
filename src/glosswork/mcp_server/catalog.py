"""The tool catalog: one registration per tool, each declaring its required scope
exactly once (DD-8). Two consumers read the declaration and never anything else:
the ``tools/list`` filter (omits every tool above the caller's scope, FR-M4) and
the ``tools/call`` gate (answers a direct call to a hidden tool with
``insufficient_scope``). Neither consults the token resolver at registration time;
the tool set is registered once per process and filtered per request.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server import MCPServer

from glosswork.actor import Scope
from glosswork.auth import scope_allows

_Fn = TypeVar("_Fn", bound=Callable[..., Any])

# The tools that declare a scope above ``read`` and only read (DD-38). Their REST
# twins are ``GET`` routes (``docs/MCP_TOOLS.md`` section 8). Pinned by equality in
# ``tests/test_mcp_read_only.py``. The rule fails **closed** on this surface: an admin
# tool added later is refused while read-only until it is named here, where the REST
# rule fails open for a non-GET route that declares ``read`` and is guarded by a pinned
# set of its own.
READ_TOOLS_ABOVE_READ_SCOPE: frozenset[str] = frozenset(
    {"list_schema_proposals", "list_object_type_grants"}
)


class ToolCatalog:
    def __init__(self, server: MCPServer) -> None:
        self._server = server
        self._scopes: dict[str, Scope] = {}

    def tool(self, scope: Scope) -> Callable[[_Fn], _Fn]:
        """Register ``fn`` as an MCP tool (name = function name, description =
        docstring, input schema = annotated signature) requiring ``scope``."""

        def decorator(fn: _Fn) -> _Fn:
            if fn.__name__ in self._scopes:
                raise ValueError(f"tool {fn.__name__!r} registered twice")
            self._server.tool()(fn)
            self._scopes[fn.__name__] = scope
            return fn

        return decorator

    def required_scope(self, tool_name: str) -> Scope | None:
        return self._scopes.get(tool_name)

    def writes(self, tool_name: str) -> bool:
        """Whether a call to ``tool_name`` is a write, for read-only mode.

        A tool declared above ``read`` is a write unless it is one of
        :data:`READ_TOOLS_ABOVE_READ_SCOPE`. An unregistered name is not a write: it falls
        through to the SDK's own unknown-tool answer, as it does today."""
        required = self._scopes.get(tool_name)
        if required is None or required == "read":
            return False
        return tool_name not in READ_TOOLS_ABOVE_READ_SCOPE

    def is_visible(self, tool_name: str, scope: Scope) -> bool:
        required = self._scopes.get(tool_name)
        return required is not None and scope_allows(scope, required)

    def names(self, scope: Scope | None = None) -> set[str]:
        """Every registered tool name, or only those visible at ``scope``."""
        if scope is None:
            return set(self._scopes)
        return {name for name in self._scopes if self.is_visible(name, scope)}
