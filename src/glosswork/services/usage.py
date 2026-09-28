"""Operator usage counts, and the counter behind them (DD-39, FR-P10).

An operator who runs this image for somebody else has to know how much of it is being
used, without reading the tenant's database, and with a credential the operator holds and
a workspace administrator cannot also mint.

**The rule lives here and only here** (DD-3). ``routes/usage.py`` reads one header,
calls :meth:`UsageService.snapshot`, and shapes a response; every refusal below is this
service's.

Four things in this module are load-bearing, and each is here rather than spread out so
that there is exactly one place to read and one place to break.

1. **The credential comparison**, in :meth:`UsageService.token_matches`, and nowhere
   else. It is compared with :func:`hmac.compare_digest` over digests, and
   it is compared **before** the feature check -- the opposite order from
   ``BootstrapService.claim``, deliberately, and DD-39 records why. There, a caller
   without the secret must not learn the password policy, and whether bootstrap is
   configured tells a prober nothing. Here, whether *usage metering* is configured is
   precisely the fact that says "this workspace is somebody's hosted customer", so an
   unset variable and a wrong value must answer identically, byte for byte. Naming
   ``feature_disabled`` for the unset case would have been the oracle itself:
   ``feature_disabled`` is 409 and a credential refusal is 401.

   Do not add a constant-time dance on top. Measured on the machine this was written on,
   ``compare_digest`` over two 43-character strings costs 46 ns against 14 ns for a
   no-op: a 32 ns difference, five orders of magnitude under the millisecond this
   application already rounds ``duration_ms`` to, and unobservable across a network.

2. **The two allowlists**, in :func:`counter_key` and
   :data:`KNOWN_HARNESSES`. A string that reaches the counters table or the operator's
   eyes comes from one of them or is a literal. The tool name in a ``tools/call`` is the
   caller's own string and an agent label is any non-blank 200-character string anyone
   holding a token can set, so without this each is a channel out of the workspace. The
   two rules close at different times and the asymmetry is real: the tool-name rule
   closes at **write** time, so no tenant string is ever stored, while the label rule can
   only close at **read** time, because labels live in the tenant's own ``agent_labels``
   table. That is the weakest joint in this module and the reason
   ``tests/test_operator_usage.py``'s sweep exists.

3. **The seam classifier**, :func:`outcome`. ``McpAdapter.middleware`` is the one place
   that sees every ``tools/call``, and it does not see one shape: a plain camelCase wire
   ``dict`` for anything that passes ``call_next``, a ``CallToolResult`` object for a
   refusal the middleware raises itself, a ``dict`` with no ``structuredContent`` key at
   all for an unregistered name or an argument-coercion failure, and nothing whatever for
   a refused token, which raises. ``result.is_error`` is an ``AttributeError`` on the
   ordinary success path. One function knows this, so an SDK upgrade breaks one function
   and one test rather than the endpoint.

4. **The counter is held in memory and flushed on an interval**, the way
   ``services/tokens.py`` already coarsens ``last_used_at`` in this same product and for
   this same reason. Measured: a read tool called with no agent label opens **zero**
   write transactions on this product today, so a row written on every call would add a
   writer-lock acquisition to exactly the traffic a metering endpoint exists to watch,
   against a database with one writer. The standing objection to an in-memory counter is
   that it loses counts when a container scales to zero, which is the normal way a hosted
   workspace stops; every container gets a clean SIGTERM drain, so
   :meth:`UsageService.stop` flushes and a stop loses nothing. The flush is bounded so it
   cannot hang that drain, and fails safe by keeping its counts when it cannot write.

**A refused token is not counted, and an operator has to know it.** A refused token and a
refused capability credential raise ``MCPError`` out of the gate and return nothing at
all, so they are not reachable at the seam without a second call site -- which the
one-seam fence exists to forbid. A call that never resolved a caller cannot be attributed,
and counting it would put a database write on the one path a credential-stuffing loop
reaches without a valid credential. The consequence: **a workspace whose agents are all
presenting bad tokens reports zero tool calls and looks idle.**
"""

from __future__ import annotations

import hashlib
import hmac
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from glosswork.config import Settings
from glosswork.db import Database
from glosswork.errors import STATUS_BY_CODE, InternalError, OperatorTokenRefusedError
from glosswork.logging import get_logger
from glosswork.repositories.interfaces import PrincipalRepository, UsageRepository
from glosswork.services.workspace import WorkspaceService

logger = get_logger(__name__)

#: Where the operator credential rides. A dedicated header rather than ``Authorization``,
#: which means "a tenant credential" everywhere else in this product: one header meaning
#: two kinds of credential is how a later edit comes to confuse them. ``routes/usage.py``
#: is the only reader of the name.
OPERATOR_TOKEN_HEADER = "X-Operator-Token"

#: Written to ``usage_counters.tool_name`` when the tool catalog does not know the name
#: the caller sent. It is not a rare fallback: it is the answer for every call an agent
#: makes to a tool that does not exist, which is common early in an integration.
UNKNOWN_TOOL_NAME = "unknown_tool"

#: Written to ``usage_counters.error_code`` when a failed call carries no code that is a
#: key of ``STATUS_BY_CODE``. Two outcomes reach it: an unregistered tool name and an
#: argument-coercion failure, which both answer ``isError: true`` with no structured
#: content at all. An operator reading ``{tool: "create_record", error_code: "unknown"}``
#: learns that an agent is calling the tool wrongly and nothing more, which is the
#: correct amount to learn.
UNKNOWN_ERROR_CODE = "unknown"

#: Stored in ``usage_counters.error_code`` for a call that succeeded, and reported back
#: as JSON ``null``. A literal rather than a NULL because SQLite permits NULLs in a
#: PRIMARY KEY, which would admit duplicate success rows for one tool.
SUCCESS_ERROR_CODE = "ok"

#: Every tool name that may be emitted to the operator.
#:
#: The tool name is read straight off the caller's own ``tools/call`` request, so it is a
#: tenant-chosen string until something decides otherwise. ``counter_key`` decides it at
#: write time and is the only writer today, which closes the boundary now; this set
#: closes it again at read time, the way :data:`KNOWN_HARNESSES` is re-checked in
#: ``_emit_harnesses``. The asymmetry that made it worth doing: the harness column's rule
#: was enforced twice and this one's once, so a second writer -- a backfilling migration,
#: a restored backup from an older schema, a future second call site -- would have
#: reached an operator's screen with nothing failing first.
#:
#: Pinned by equality against a **live** ``list_tools()`` at ``admin`` scope in
#: ``tests/test_operator_usage.py::test_known_tools_is_exactly_the_live_catalog``, so a
#: tool registered without being listed here turns that test red. It is deliberately not
#: pinned the way ``READ_TOOLS_ABOVE_READ_SCOPE`` is: that one compares its constant to a
#: literal written inside the test, which cannot fail when a tool is added and merely
#: restates the constant.
#:
#: The cost, stated rather than hidden: these 32 names are now written down in four
#: places -- the catalog's registrations, ``CATALOG`` in ``tests/test_mcp_catalog.py``,
#: ``docs/MCP_TOOLS.md`` section 5, and here. A new tool turns two tests red until it is
#: listed, which is the alarm this exists to create.
KNOWN_TOOLS: frozenset[str] = frozenset(
    {
        "add_comment",
        "add_field",
        "bulk_update_records",
        "create_attachment_upload",
        "create_object_type",
        "create_record",
        "create_text_attachment",
        "delete_comment",
        "delete_record",
        "describe_capabilities",
        "describe_object_type",
        "find_principals",
        "get_attachment",
        "get_record",
        "get_record_history",
        "link_records",
        "list_changes_since",
        "list_comments",
        "list_object_type_grants",
        "list_object_types",
        "list_schema_proposals",
        "propose_schema_change",
        "query_records",
        "restore_record",
        "revoke_object_type_grant",
        "search",
        "set_object_type_grant",
        "unlink_records",
        "update_comment",
        "update_field",
        "update_object_type",
        "update_record",
    }
)

#: The known agent harnesses, lower-cased. An agent label is matched against this set
#: by **exact equality after case-folding** and what is emitted is this set's own literal,
#: never the presented string and never a prefix or substring of it: a prefix match would
#: report ``claude-code-ACME-CORP`` as a known harness while carrying the tenant's
#: customer name. Anything unmatched is counted under :data:`OTHER_HARNESS` and never
#: named, which is why a harness nobody has heard of prompts a list update rather than a
#: leak.
KNOWN_HARNESSES: frozenset[str] = frozenset(
    {
        "claude-code",
        "claude-desktop",
        "claude-ai",
        "claude-mobile",
        "cowork",
        "codex",
        "cursor",
        "vscode",
        "gemini-cli",
        "windsurf",
        "opencode",
        "goose",
        "zed",
        "cline",
        "kiro",
        "amp",
        "pi",
    }
)

#: Everything that matched nothing. It discloses a cardinality and not a character of
#: anyone's text, which is the same class of thing ``agent_labels_distinct`` already
#: discloses.
OTHER_HARNESS = "__other__"

#: How often the in-memory counts are written down. Coarse on purpose, for
#: ``LAST_USED_COARSENESS``'s reason: the question this table answers is "how much is
#: this workspace being used", which a thirty-second resolution answers exactly as well
#: as a per-call one, and the per-call version costs a writer lock on every call.
FLUSH_INTERVAL_SECONDS = 30.0

#: How long :meth:`UsageService.stop` waits for the flushing thread to finish.
#:
#: Sized to fit **inside** a stop, not to be generous. The platform grace this has to live
#: within is as little as 5 s on some platforms, and ``EmbeddingWorker.stop`` already claims
#: up to 5 s of it for one worst-case source. A flush is one ``BEGIN IMMEDIATE`` and at most
#: a few dozen single-row upserts, so it is sub-millisecond whenever it is not queued behind
#: another writer, and this budget exists only for the queued case. The counter is stopped
#: **before** the embedding worker for that reason. A timeout is not a failure: the thread
#: is a daemon, the counts it was holding are the ones the last interval did not cover, and
#: losing thirty seconds of counts is strictly better than hanging a shutdown past its grace
#: period and being ``SIGKILL``ed.
STOP_GRACE_SECONDS = 1.5


def counter_key(tool_name: str, error_code: str | None, *, registered: bool) -> tuple[str, str]:
    """The one function that decides what a counter row may be keyed on.

    ``tool_name`` is written only when the tool catalog knows it, and otherwise the
    literal :data:`UNKNOWN_TOOL_NAME`; ``error_code`` only when it is a key of
    ``STATUS_BY_CODE``, and otherwise the literal :data:`UNKNOWN_ERROR_CODE`. Both rules
    live here so there is one place that decides what may be written, and so a reader
    checking the boundary has one function to read rather than a call graph.

    ``registered`` is the catalog's answer, passed in as a boolean rather than reached
    for: the catalog is built with the MCP server, after the services, and a boolean
    cannot smuggle a string.
    """
    name = tool_name if registered and tool_name else UNKNOWN_TOOL_NAME
    if error_code is None:
        return (name, SUCCESS_ERROR_CODE)
    code = error_code if error_code in STATUS_BY_CODE else UNKNOWN_ERROR_CODE
    return (name, code)


def _emittable_key(key: tuple[str, str]) -> tuple[str, str]:
    """The read-time half of :func:`counter_key`'s rule.

    Given a ``(tool_name, error_code)`` pair as it is **stored**, answer the pair that
    may be emitted. A tool name that is neither a member of :data:`KNOWN_TOOLS` nor
    already the literal :data:`UNKNOWN_TOOL_NAME` becomes ``UNKNOWN_TOOL_NAME``; an error
    code that is neither a key of ``STATUS_BY_CODE`` nor :data:`SUCCESS_ERROR_CODE` nor
    :data:`UNKNOWN_ERROR_CODE` becomes ``UNKNOWN_ERROR_CODE``.

    This does not replace ``counter_key``, which stays exactly as it is and remains the
    one place that decides what may be **written**. It is a second enforcement of the
    same rule at the other end, so the boundary does not depend on there only ever being
    one writer.

    Separate from the emit loop because the mapping and the summing are different
    concerns: the caller has to sum, and it can only sum correctly if the key it sums
    into is decided in one place.
    """
    tool, code = key
    if tool not in KNOWN_TOOLS and tool != UNKNOWN_TOOL_NAME:
        tool = UNKNOWN_TOOL_NAME
    if code not in STATUS_BY_CODE and code not in (SUCCESS_ERROR_CODE, UNKNOWN_ERROR_CODE):
        code = UNKNOWN_ERROR_CODE
    return (tool, code)


def outcome(result: Any) -> tuple[bool, str | None]:
    """``(counted, error_code)`` for whatever the MCP seam was handed.

    The seam sees three shapes and one absence, and this is the only function in the
    product that knows that:

    ==================================  ==========================================
    Outcome                             What arrives
    ==================================  ==========================================
    any registered tool, success        plain ``dict``, ``isError`` false
    any registered tool, domain error   plain ``dict``, code under
                                        ``structuredContent.error.code``
    unregistered tool name              plain ``dict``, **no** ``structuredContent``
    argument coercion failure           plain ``dict``, **no** ``structuredContent``
    ``insufficient_scope`` and kin      ``CallToolResult`` **object**
    refused token                       nothing: ``MCPError`` is raised
    ==================================  ==========================================

    So the ``Mapping`` branch comes first, because it is the ordinary case, and
    ``result.is_error`` -- which looks like the only reader needed -- raises
    ``AttributeError`` on every successful call in the deployment.

    A result whose ``resultType`` is anything but ``complete`` is **not counted**.
    ``CallToolResult.result_type`` is ``Literal["complete", "input_required"] | str`` and
    this repository already imports ``InputRequiredResult``. No tool in this catalog
    returns one today; when the first elicitation tool does, an unfinished call must not
    be recorded as a finished one.
    """
    if result is None:
        return (False, None)
    if isinstance(result, Mapping):
        if str(result.get("resultType", "complete")) != "complete":
            return (False, None)
        if not bool(result.get("isError", False)):
            return (True, None)
        return (True, _error_code_of(result.get("structuredContent")))
    if str(getattr(result, "result_type", "complete")) != "complete":
        return (False, None)
    if not bool(getattr(result, "is_error", False)):
        return (True, None)
    return (True, _error_code_of(getattr(result, "structured_content", None)))


def _error_code_of(structured: Any) -> str:
    """The code out of a structured error envelope, or the literal ``unknown``.

    A missing envelope, a missing ``error``, a missing ``code`` and a code outside
    ``STATUS_BY_CODE`` all collapse to the same literal. An error *code* is a plain class
    attribute on every ``GlossworkError`` subclass and can never carry a value, which is
    what makes it safe to return at all; a code that is *not* one of those is a string
    this code has never seen and is treated as one.
    """
    if not isinstance(structured, Mapping):
        return UNKNOWN_ERROR_CODE
    error = structured.get("error")
    if not isinstance(error, Mapping):
        return UNKNOWN_ERROR_CODE
    code = error.get("code")
    return str(code) if isinstance(code, str) and code in STATUS_BY_CODE else UNKNOWN_ERROR_CODE


@dataclass(frozen=True, slots=True)
class ToolCallCount:
    """One ``(tool, error_code, count)`` row of the response. ``error_code`` is ``None``
    for a successful call."""

    tool: str
    error_code: str | None
    count: int


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """What an operator reads. Every value is a number except the tool names, error codes
    and harness names, each of which comes from an allowlist or is a literal.

    ``since`` is when this database began counting, so a hosting operator that sees the
    counters fall can tell a replaced volume from a workspace that went quiet.
    """

    since: str | None
    records_live: int
    records_deleted: int
    attachment_count: int
    attachment_bytes_logical: int
    attachment_bytes_stored: int
    humans_active: int
    humans_total: int
    agent_labels_distinct: int
    agent_label_calls_total: int
    agent_labels_by_harness: dict[str, int]
    object_types: int
    fields: int
    tool_calls: list[ToolCallCount]


class UsageService:
    def __init__(
        self,
        db: Database,
        usage_repo: UsageRepository,
        principal_repo: PrincipalRepository,
        workspace: WorkspaceService,
        settings: Settings,
    ) -> None:
        self._db = db
        self._usage = usage_repo
        self._principals = principal_repo
        self._workspace = workspace
        self._settings = settings
        self._pending: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------- the credential

    def token_matches(self, presented: str | None) -> bool:
        """The **one** reader of ``GW_OPERATOR_TOKEN``.

        False when the variable is unset, when it is blank, when no header was presented,
        and when the value is wrong. Four situations, one answer, so that none of them is
        distinguishable from outside.

        Digests rather than the raw strings, so the comparison is over a fixed length and
        cannot leak the configured value's length through timing, and ``compare_digest``
        rather than ``==`` so it cannot leak a matching prefix either. Both are
        ``BootstrapService._secret_matches``' reasoning, taken as read.
        """
        configured = self._settings.operator_token
        if not configured or presented is None:
            return False
        return hmac.compare_digest(
            hashlib.sha256(presented.encode("utf-8")).digest(),
            hashlib.sha256(configured.encode("utf-8")).digest(),
        )

    # ------------------------------------------------------------------ the read

    def snapshot(self, request_id: str, presented: str | None) -> UsageSnapshot:
        """Refuse, then read. Never the other way round.

        Raises :class:`~glosswork.errors.OperatorTokenRefusedError` for every caller who
        is not the operator, and :class:`~glosswork.errors.InternalError` for anything
        else that goes wrong below.

        **The blanket catch is the point, not laziness.** The content
        boundary is written for the success path, and an error *message* carries tenant
        values routinely where an error *code* cannot: ``unknown_field`` names the field
        key and lists every valid key in the same string. If a count query raised a
        ``GlossworkError``, the application's single handler would put that envelope in
        front of the operator. So everything that is not this service's own refusal
        becomes ``internal_error``, which discloses a request id and nothing else.
        """
        if not self.token_matches(presented):
            raise OperatorTokenRefusedError()
        try:
            return self._read_counts()
        except Exception:
            logger.error("usage_snapshot_failed", request_id=request_id, exc_info=True)
            raise InternalError(request_id) from None

    def _read_counts(self) -> UsageSnapshot:
        """Every count, from one read transaction, so they describe one instant."""
        document = self._workspace.get_workspace()
        with self._db.read() as conn:
            records_live, records_deleted = self._usage.count_records(conn)
            attachments, logical, stored = self._usage.count_attachments(conn)
            object_types, fields = self._usage.count_schema(conn)
            label_calls = self._usage.sum_agent_label_calls(conn)
            harnesses = self._usage.count_labels_by_harness(conn, KNOWN_HARNESSES)
            humans_total = self._principals.count_users(conn)
            since = self._usage.counting_since(conn)
            persisted = self._usage.read_counters(conn)
        return UsageSnapshot(
            since=since,
            records_live=records_live,
            records_deleted=records_deleted,
            attachment_count=attachments,
            attachment_bytes_logical=logical,
            attachment_bytes_stored=stored,
            # These two come from ``WorkspaceService``, which already holds their
            # definition and already shows both numbers to the tenant in the workspace
            # sidebar. A second query here is how an operator's numbers come to disagree
            # with the tenant's own screen with neither being wrong.
            humans_active=document.people,
            agent_labels_distinct=document.agents,
            humans_total=humans_total,
            agent_label_calls_total=label_calls,
            agent_labels_by_harness=self._emit_harnesses(harnesses),
            object_types=object_types,
            fields=fields,
            tool_calls=self._tool_calls(persisted),
        )

    @staticmethod
    def _emit_harnesses(counted: Mapping[str, int]) -> dict[str, int]:
        """Emit the **allowlist's own literals** and the residual, and nothing else.

        The repository already matched by exact equality against the set it was handed,
        so every key here is a value this module supplied. This second pass is not
        redundant: it is what makes the guarantee local, so a later edit to the query
        cannot widen what is emitted without failing here first.
        """
        out = {name: int(counted.get(name, 0)) for name in sorted(KNOWN_HARNESSES)}
        out[OTHER_HARNESS] = int(counted.get(OTHER_HARNESS, 0))
        return {name: value for name, value in out.items() if value}

    def _tool_calls(self, persisted: Mapping[tuple[str, str], int]) -> list[ToolCallCount]:
        """The persisted rows plus whatever has not been flushed yet, re-filtered.

        Merged rather than flushed-then-read, so an operator's read is a read: it opens
        no write transaction, takes no writer lock, and still reports the exact number of
        calls the deployment has served. Ordered by tool then error code, both ascending
        -- without a stated order the list arrives in insertion order, which discloses the
        sequence in which a workspace first used each tool.

        Both columns are re-checked here, the way ``_emit_harnesses``
        re-checks the harness label. ``_emit_harnesses`` can build its output from the
        allowlist's own literals and read counts out by key; this one has to map stored
        keys instead, which is why it **sums** into the mapped key rather than emitting
        one row per stored row. The first wording of this filter did not, and emitted two
        rows for ``(unknown_tool, null)`` where the table's primary key admits one. An
        operator bills from these numbers, so a duplicated pair is not cosmetic.
        """
        totals: dict[tuple[str, str], int] = dict(persisted)
        with self._lock:
            for key, delta in self._pending.items():
                totals[key] = totals.get(key, 0) + delta
        emitted: dict[tuple[str, str], int] = {}
        for key, count in totals.items():
            mapped = _emittable_key(key)
            emitted[mapped] = emitted.get(mapped, 0) + count
        return [
            ToolCallCount(
                tool=tool,
                error_code=None if code == SUCCESS_ERROR_CODE else code,
                count=count,
            )
            for (tool, code), count in sorted(emitted.items())
        ]

    # --------------------------------------------------------------- the counter

    def record_tool_call(self, *, tool_name: str, error_code: str | None, registered: bool) -> None:
        """Count one ``tools/call``. In memory, under a lock, touching no connection.

        Called from exactly one place in ``src/`` -- the MCP seam -- which
        ``tests/test_one_usage_counter.py`` pins.
        """
        key = counter_key(tool_name, error_code, registered=registered)
        with self._lock:
            self._pending[key] = self._pending.get(key, 0) + 1

    def pending_count(self) -> int:
        """How many distinct pairs are waiting to be written. For tests and for the
        shutdown log line; never part of the response."""
        with self._lock:
            return len(self._pending)

    def flush(self) -> None:
        """Write the pending counts down, in one transaction of this service's own.

        **Fails safe.** A flush that cannot take the writer lock puts its counts back and
        returns; it never raises into a caller and never drops what it was holding. That
        matters because the caller is either a background thread, whose loop must not die,
        or a shutdown, which must not be hung by this.

        The transaction is this service's own and is never nested inside another
        A ``db.write()`` opened inside an open ``db.write()`` on this
        ``Database`` does not fail fast: it blocks for nearly thirteen seconds and then
        raises ``database is locked`` on ``BEGIN IMMEDIATE``, so getting this wrong is a
        hang rather than a red test. Recording is what happens on the hot path, and
        recording opens nothing.
        """
        with self._lock:
            deltas, self._pending = self._pending, {}
        if not deltas:
            return
        try:
            self._write_counts(deltas)
        except Exception:
            with self._lock:
                for key, delta in deltas.items():
                    self._pending[key] = self._pending.get(key, 0) + delta
            logger.warning("usage_counter_flush_failed", pairs=len(deltas), exc_info=True)

    def _write_counts(self, deltas: dict[tuple[str, str], int]) -> None:
        with self._db.write() as conn:
            self._usage.add_counters(conn, deltas)

    # -------------------------------------------------------------- thread control

    def start(self) -> None:
        """Start the flushing thread. Idempotent, and a no-op if already running."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="usage-counter", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.wait(FLUSH_INTERVAL_SECONDS):
            self.flush()
        # The stop flush, inside the thread that owns the loop, so a stop cannot race a
        # flush already in progress.
        self.flush()

    def stop(self, timeout: float = STOP_GRACE_SECONDS) -> None:
        """Flush what is held and stop, inside ``timeout`` seconds.

        This is what closes the one real objection to counting in memory: a lossy counter
        loses counts exactly when a hosted container scales to zero, which is the normal
        way a hosted workspace stops. Every container gets a clean SIGTERM drain, so a
        stop flushes.

        With no thread running -- a bundle built for a test, or a stop called twice -- it
        still flushes, synchronously, because "stop loses nothing" must not depend on how
        the bundle was assembled.
        """
        self._stop_event.set()
        thread, self._thread = self._thread, None
        if thread is None:
            self.flush()
            return
        thread.join(timeout=timeout)
        if thread.is_alive():
            # Not a failure. The counts it holds are the ones this interval did not cover,
            # and losing them is strictly better than hanging the drain past the platform's
            # grace period and being killed mid-write.
            logger.warning("usage_counter_stop_timed_out", timeout_s=timeout)
        else:
            logger.info("usage_counter_stopped", pairs_pending=self.pending_count())


__all__ = [
    "FLUSH_INTERVAL_SECONDS",
    "KNOWN_HARNESSES",
    "KNOWN_TOOLS",
    "OPERATOR_TOKEN_HEADER",
    "OTHER_HARNESS",
    "STOP_GRACE_SECONDS",
    "SUCCESS_ERROR_CODE",
    "UNKNOWN_ERROR_CODE",
    "UNKNOWN_TOOL_NAME",
    "ToolCallCount",
    "UsageService",
    "UsageSnapshot",
    "counter_key",
    "outcome",
]
