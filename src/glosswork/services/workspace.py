"""The workspace document: what this deployment is called and who is in it
(DD-28; docs/DESIGN.md 4.3 and 8.1).

Backs ``GET /api/v1/workspace``, the one bounded read the sidebar's top block needs.
Without it there would be no workspace name anywhere in the product and no count of
anything a caller below ``admin`` scope could reach: ``GET /principals/directory``
returns rows, bounded at 50, and every principal's agent labels sit behind
``require_scope("admin")``. Composing both numbers here, from two ``SELECT COUNT(*)``
reads, is what keeps the sidebar's read bounded (DD-18) and keeps the counting logic
out of the route (DD-3).
"""

from __future__ import annotations

from dataclasses import dataclass

from glosswork.config import Settings
from glosswork.db import Database
from glosswork.errors import WorkspaceReadOnlyError
from glosswork.repositories.interfaces import AgentLabelRepository, PrincipalRepository

#: The path the MCP surface is served on. ``app.py`` attaches it as a ``Route`` on this
#: exact path rather than a ``Mount``, so there is no trailing-slash variant and no setting
#: that moves it: only the origin half of the URL is deployment-specific.
MCP_PATH = "/mcp"

#: The path the browser signs in at (``web/src/App.tsx``: ``<Route path="/login">``).
#: Beside ``MCP_PATH`` because the bootstrap handoff returns both addresses and they
#: are the same kind of fact: a fixed path this application serves, whose origin half is
#: the only deployment-specific part.
SIGN_IN_PATH = "/login"


@dataclass(slots=True)
class WorkspaceDocument:
    """What ``GET /api/v1/workspace`` reports. ``name`` is ``None`` when
    ``GW_WORKSPACE_NAME`` is unset; the sidebar then renders the mark alone
    rather than falling back to the product's name."""

    name: str | None
    people: int
    agents: int
    #: The absolute URL an agent connects to, or ``None`` when ``GW_BASE_URL`` is unset
    #: See ``WorkspaceService.get_workspace`` for why this is composed here rather
    #: than in the browser.
    mcp_url: str | None


class WorkspaceService:
    """Composes the workspace document. The definition of the two counts, in the one
    place it lives:

    - ``people`` counts active principals of type ``user``.
    - ``agents`` counts registered agent labels. FR-I6 auto-registers a label on an
      agent's first call, so a label exists exactly when an agent has acted in this
      deployment, which is what "N agents" should mean to somebody reading the
      sidebar.
    - A **service account is not a person**. ``docs/DESIGN.md`` 6.5 says a service
      account is an agent, and it reaches the ``agents`` count only through a label
      it presented, never on its own.
    - A service account that acts and presents **no** label is in neither number.
      DD-17 made a label present on writes from every surface, so this is now the
      exception rather than the norm -- but it is a real case and it is a stated
      limitation, not an oversight.
    """

    def __init__(
        self,
        db: Database,
        principal_repo: PrincipalRepository,
        agent_label_repo: AgentLabelRepository,
        settings: Settings,
    ) -> None:
        self._db = db
        self._principals = principal_repo
        self._labels = agent_label_repo
        self._settings = settings

    def get_workspace(self) -> WorkspaceDocument:
        """The read behind ``GET /api/v1/workspace``. Both counts come from the same
        read transaction, so they describe one consistent instant.

        ``mcp_url`` needs no read at all; it is here because it belongs to the same
        question -- what is this deployment and how do you reach it -- and because the
        browser cannot compose it correctly on its own.
        """
        with self._db.read() as conn:
            people = self._principals.count_active_users(conn)
            agents = self._labels.count_labels(conn)
        return WorkspaceDocument(
            name=self._settings.workspace_name,
            people=people,
            agents=agents,
            mcp_url=self.mcp_url(),
        )

    def refuse_write_if_read_only(self, attempted: str) -> None:
        """The **one** predicate that decides whether a frozen workspace refuses a write
        (DD-38). Raises :class:`~glosswork.errors.WorkspaceReadOnlyError` when
        ``GW_READ_ONLY`` is on, and returns otherwise.

        DD-16's shape: one rule, called from exactly the two adapter gates --
        ``scopes.refuse_read_only_write`` for REST and ``McpAdapter``'s ``tools/call``
        gate for MCP -- and from no route handler, tool body or service write method
        (pinned by ``tests/test_one_read_only_predicate.py``). *Which*
        calls are writes is each gate's question, because a route and a tool are named
        differently; *whether* to refuse one is this method's alone.

        It lives here rather than at ``Database.write()`` because ordinary reads write
        too: a credential's ``last_used_at``, a session's ``last_seen_at``, an
        agent label's first use. A freeze at the storage layer would break reads.
        """
        if not self._settings.read_only:
            return
        raise WorkspaceReadOnlyError(attempted, self._settings.subscribe_url)

    def sign_in_url(self) -> str | None:
        """``GW_BASE_URL`` plus ``/login``, or ``None`` when no base URL is set.

        The bootstrap handoff returns this beside :meth:`mcp_url` so that a program which just
        provisioned a container can tell a person where to sign in. It is composed here,
        through the same ``rstrip('/')`` as its sibling, because one composition of
        ``GW_BASE_URL`` is the point: a second ``rstrip`` somewhere else is how the two
        answers start disagreeing about a trailing slash.
        """
        return self._absolute(SIGN_IN_PATH)

    def mcp_url(self) -> str | None:
        """``GW_BASE_URL`` plus ``/mcp``, or ``None`` when no base URL is set.

        **Why the server composes this and the browser does not.** The first-run screen
        (``docs/DESIGN.md`` 8.5) prints this URL for a person to paste into an agent, and the
        obvious client-side answer -- the page's own origin plus ``/mcp`` -- is wrong in the
        configuration the transport's hardening exists for: ``create_app`` builds the ``/mcp``
        Host and Origin allowlists from ``GW_BASE_URL`` (DD-15), so a UI reached on an origin
        outside that allowlist would print a URL this deployment refuses. The URL's consumer is
        also the **agent** rather than the person at the keyboard, and an agent on another
        machine cannot reach the ``http://localhost:8000`` a laptop trial is browsed at.

        **``None`` rather than a guess** when the setting is unset, which is legal outside OIDC
        mode. That is precisely the case where the allowlist is empty and the check disables
        itself, so any origin reaches ``/mcp`` there and the client falls back to its own.
        Composing a fake absolute URL here would be this process asserting an origin it was
        never told. The same ``rstrip('/')`` the attachment service applies to this setting,
        because ``GW_BASE_URL=https://host/`` is a thing a person writes and ``https://host//mcp``
        is a different path to the route matcher.
        """
        return self._absolute(MCP_PATH)

    def _absolute(self, path: str) -> str | None:
        """One composition of ``GW_BASE_URL`` for both public URLs above.

        ``rstrip('/')`` because ``GW_BASE_URL=https://host/`` is a thing a person writes
        and ``https://host//mcp`` is a different path to the route matcher.
        """
        if not self._settings.base_url:
            return None
        return f"{self._settings.base_url.rstrip('/')}{path}"
