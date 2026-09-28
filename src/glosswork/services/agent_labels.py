"""Agent label registry (FR-I6 registry half; FR-I7 read side).

Labels auto-register on first use against the calling principal: the row is
created ``verified = 0`` with ``call_count = 1``, and every later use increments the
counter and advances ``last_seen_at``. Unknown labels are accepted, never rejected:
an agent's write must not fail because of a label typo. The same label string under
two principals is two independent rows (docs/DATA_MODEL.md ``agent_labels``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from glosswork.actor import ActorContext
from glosswork.db import Database
from glosswork.errors import NotFoundError, ValidationFailedError
from glosswork.repositories.interfaces import AgentLabelRepository
from glosswork.repositories.models import AgentLabelRow
from glosswork.services.access import AccessService
from glosswork.services.principals import DIRECTORY_DEFAULT_LIMIT, DIRECTORY_MAX_LIMIT
from glosswork.timeutil import format_datetime, utc_now

MAX_LABEL_LENGTH = 200


class AgentLabelService:
    def __init__(
        self, db: Database, label_repo: AgentLabelRepository, access: AccessService
    ) -> None:
        self._db = db
        self._labels = label_repo
        # This service touches object types through the directory read below, which is why
        # `tests/test_access_completeness.py` lists it among the enforcing services -- see
        # that file for the reason.
        self._access = access

    def search_labels(
        self,
        actor: ActorContext,
        q: str | None = None,
        limit: int = DIRECTORY_DEFAULT_LIMIT,
    ) -> list[AgentLabelRow]:
        """The picker's options (FR-U8): every label this caller could encounter on
        `/activity`, so a person can filter by an agent without knowing a UUID.

        **Scoped to what the caller can already see, and that is the safety property.**
        It asks `AccessService` the same question `AuditService.search` asks -- unrestricted,
        or the set of object types held at `read` -- and the repository applies the same
        predicate. So this route's rows are exactly the labels an unfiltered `/audit-events`
        walk by the same caller would reveal, and it publishes nothing the audit trail does
        not. An unscoped directory over `list_labels(None)` would have published every
        label string in the deployment, which is a disclosure decision nobody has
        made; `tests/test_agent_label_directory.py` asserts the equivalence rather than
        asserting the route merely answers.

        **The bound is the directory's own constant pair**, reused rather than redeclared:
        a second pair for one concept is the drift DD-18 is about. It is published nowhere,
        matching `/principals/directory`, whose number rides `find_principals`' parameter
        description -- a REST-only route has no such carrier, and `docs/DESIGN.md` 8.7
        records that as a known gap.
        """
        with self._db.read() as conn:
            accessible: list[str] | None = None
            include_untyped = True
            if not self._access.is_unrestricted(conn, actor):
                accessible = sorted(self._access.accessible_type_ids(conn, actor, "read"))
                include_untyped = False
            return self._labels.search_labels(
                conn,
                q=q,
                accessible_type_ids=accessible,
                include_untyped=include_untyped,
                limit=min(limit, DIRECTORY_MAX_LIMIT),
            )

    @staticmethod
    def validate_label(label: str) -> str:
        """The registry's own rule -- non-blank after stripping, at most
        :data:`MAX_LABEL_LENGTH` -- returning the cleaned string.

        **Extracted rather than reached by calling** :meth:`register_use`.
        Minting a token has to refuse a malformed label without *registering* one, and a
        second ``register_use`` call site would turn
        ``tests/test_one_agent_label_resolver.py`` red -- the fence whose red means the
        design went wrong rather than the code. So both callers share this check and
        :meth:`register_use` stays the only writer.

        The bound is not a new one: ``describe_capabilities`` already publishes it as
        ``agent_label_max_length`` from this same constant, so refusing at mint satisfies
        DD-18 as it stands.

        An *unknown* label is still never rejected (FR-I6). What is refused here is a
        **malformed** one.
        """
        cleaned = label.strip()
        if not cleaned:
            raise ValidationFailedError("Agent label must be a non-empty string.")
        if len(cleaned) > MAX_LABEL_LENGTH:
            raise ValidationFailedError(
                f"Agent label must be at most {MAX_LABEL_LENGTH} characters.", label=label
            )
        return cleaned

    def register_use(
        self, principal_id: str, label: str, now: datetime | None = None
    ) -> AgentLabelRow:
        """Resolve a label string to its registry row for this principal, creating
        it on first use, and count this use."""
        cleaned = self.validate_label(label)
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            existing = self._labels.find_label(conn, principal_id, cleaned)
            if existing is None:
                row = AgentLabelRow(
                    id=str(uuid.uuid4()),
                    principal_id=principal_id,
                    label=cleaned,
                    display_name=None,
                    description=None,
                    verified=False,
                    first_seen_at=ts,
                    last_seen_at=ts,
                    call_count=1,
                )
                self._labels.insert_label(conn, row)
                return row
            self._labels.record_use(conn, existing.id, ts)
            refreshed = self._labels.get_label(conn, existing.id)
            assert refreshed is not None
            return refreshed

    def get_label(self, label_id: str) -> AgentLabelRow:
        with self._db.read() as conn:
            row = self._labels.get_label(conn, label_id)
            if row is None:
                raise NotFoundError("agent label", label_id)
            return row

    def list_labels(self, principal_id: str | None = None) -> list[AgentLabelRow]:
        """Labels for one principal, or every principal's when ``principal_id`` is
        None (the administrator cross-user view, FR-I7)."""
        with self._db.read() as conn:
            return self._labels.list_labels(conn, principal_id)

    def update_label(
        self,
        principal_id: str,
        label_id: str,
        display_name: str | None = None,
        description: str | None = None,
    ) -> AgentLabelRow:
        """Rename/describe one of the caller's own labels, marking it verified
        (FR-I6: "flagged unverified until the user names them")."""
        with self._db.write() as conn:
            row = self._labels.get_label(conn, label_id)
            if row is None or row.principal_id != principal_id:
                raise NotFoundError("agent label", label_id)
            self._labels.update_label(conn, label_id, display_name, description)
            refreshed = self._labels.get_label(conn, label_id)
            assert refreshed is not None
            return refreshed
