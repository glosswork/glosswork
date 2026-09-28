"""Fail closed on an unenforced path.

Two meta-tests, in the same spirit as the ``SCOPE_EXEMPT_PATHS`` assertion and the
``vec0``-isolation grep:

1. **Completeness.** Every public entry point on a service that touches object types
   consults ``AccessService``, directly or through one of the named gate helpers. A new
   service method that reaches records without an access check fails the suite rather
   than shipping unenforced.
2. **Layering.** ``AccessService`` is not reachable from ``routes/`` or ``mcp_server/``
   (DD-3). Enforcement lives in the services, exactly as another test asserts nothing
   under ``routes/`` reads ``ActorContext.scope``.

Both are grep- and signature-backed rather than promised, because the failure mode they
exist to prevent is not "someone forgets once" but "the discipline decays one method at
a time and nobody notices".
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from glosswork.services.access import AccessService
from glosswork.services.agent_labels import AgentLabelService
from glosswork.services.attachments import AttachmentService
from glosswork.services.audit import AuditService
from glosswork.services.changes import ChangeFeedService
from glosswork.services.comments import CommentService
from glosswork.services.csv import CsvService
from glosswork.services.records import RecordService
from glosswork.services.saved_views import SavedViewService
from glosswork.services.schema import SchemaService
from glosswork.services.search import SearchService

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "glosswork"

# Every service whose entry points name, or reach, an object type.
#
# ``ExportService`` and ``BackupService`` are deliberately absent rather than exempt: both are
# whole-deployment operations reached only through ``/api/v1/admin/*``, which declares
# ``require_role("admin")``. A system administrator is implicitly ``admin`` on
# every type, so a per-type check inside them could never refuse anything -- the role declaration
# is the enforcement, and it is asserted as an exact set in
# ``tests/test_rest_scope_enforcement.py``.
#
# ``AgentLabelService`` is listed although most of it "touches no object type at all".
# ``search_labels`` is the agent picker's directory on ``/activity``, and its whole safety property
# is that it shows a caller no label their own audit search would hide -- so it asks
# ``AccessService`` the same question ``AuditService.search`` asks, and the service belongs in the
# list below. Its four other entry points still touch no object type and are named in EXEMPT with
# that reason, which is the difference between a service that is unenforced and one that has nothing
# to enforce.
#
# The two record-write paths in the audit router --
# ``POST /api/v1/audit-events/{event_id}/revert`` and
# ``POST /api/v1/records/{ref}/revert-to-version`` -- *are* enforced: both compute an inverse patch
# and hand it to ``update_record``, which requires ``write`` on the record's type. They appear in
# EXEMPT below as delegations, which is what makes them visible here rather than invisible.
ENFORCING_SERVICES = (
    AgentLabelService,
    SchemaService,
    RecordService,
    CommentService,
    AttachmentService,
    SavedViewService,
    ChangeFeedService,
    AuditService,
    SearchService,
    CsvService,
)

# What "consults AccessService" looks like in a method body: the service itself, or one
# of the named helpers that exists only to call it. Listed rather than pattern-matched,
# so a new way of spelling the check has to be added here in a diff a reviewer sees.
GATE_EXPRESSIONS = (
    "self._access.",
    "self._require_record_level(",
    "self._require_view_level(",
    "self._require_proposal_level(",
    "self._require_readable(",
    "self._may_read_target(",
    "self._restriction(",
    "require_type_level(",
    "self._resolve_readable_ref(",
    "self._resolve_scope(",
    "self.get_attachment(",
    # A public write method that is a thin wrapper opening the transaction and
    # delegating to its own ``*_in_txn`` twin is enforced exactly if the twin is, and the
    # twin is public too, so this same test checks it directly on the next iteration of
    # the loop. The gate sits one level in, and both levels are asserted.
    "_in_txn(",
)

# Exempted **by name with a stated reason**, in the same spirit as SCOPE_EXEMPT_PATHS: a
# pattern would silently absorb a future method, and this dict has to be edited.
EXEMPT: dict[tuple[str, str], str] = {
    # The registry half of `AgentLabelService` (FR-I6): a label belongs to a principal,
    # not to an object type, and a principal may only rename its own. `register_use` is called
    # from the request edge before any object type is in view, and the three reads are keyed by
    # label id or principal id. There is nothing for a level check to be about. `search_labels`
    # is deliberately absent from this list: it is the one entry point that does reach object
    # types, and it is gated.
    ("AgentLabelService", "register_use"): "a label belongs to a principal, not to a type",
    ("AgentLabelService", "get_label"): "keyed by label id; touches no object type",
    ("AgentLabelService", "list_labels"): "keyed by principal id; touches no object type",
    ("AgentLabelService", "update_label"): "renames the caller's own label; touches no type",
    # `validate_label` is the registry's own string rule, extracted from
    # `register_use` so the mint path can refuse a malformed label without *registering*
    # one -- a second `register_use` call site would break DD-17's one-resolver meta-test.
    # It is a pure function: no actor, no principal, no object type, no database. There is
    # nothing for a level check to be about, which is this list's whole distinction.
    ("AgentLabelService", "validate_label"): "a pure string rule; no actor and no database",
    # Internal sort-composite reconcilers: they issue CREATE INDEX / DROP INDEX
    # DDL, take no actor, and are unreachable from any adapter. Giving them an access
    # check would mean giving them an actor they have no caller to get one from.
    ("SchemaService", "desired_sort_indexes"): "internal DDL reconciler, no adapter reaches it",
    ("SchemaService", "reconcile_sort_indexes"): "internal DDL reconciler, no adapter reaches it",
    (
        "SchemaService",
        "reconcile_all_sort_indexes",
    ): "internal DDL reconciler, no adapter reaches it",
    (
        "SchemaService",
        "retire_legacy_index_names",
    ): "internal DDL reconciler, no adapter reaches it",
    # A startup report over schema metadata: it takes no actor, reaches no
    # record, and returns (object type key, field key) pairs for an operator-facing log
    # line. Giving it an access check would mean giving it an actor it has no caller to
    # get one from, exactly as with the reconcilers above.
    (
        "SchemaService",
        "reserved_key_collisions",
    ): "startup report over schema metadata, no actor and no record",
    # DD-25. A projection over proposals the caller is **already holding**: its four
    # callers -- `list_proposals_page`, `get_proposal`, `approve_proposal` and
    # `reject_proposal` -- each enforce before they have a proposal to project, so a check here
    # would re-ask a question answered one frame up the stack. Same argument, and same shape,
    # as `RecordService.principal_sidecar`, which takes an actor it deliberately does not read
    # for exactly this reason. It takes no actor at all rather than taking one and
    # ignoring it, so the absence of a check is visible in the signature instead of hidden
    # behind a parameter that looks like a gate.
    (
        "SchemaService",
        "proposal_targets_for",
    ): "names the type and field of proposals the caller was already authorized to read",
    # `upload` takes no record (`services/attachments.py`), so there is nothing to
    # check a level against. The real gate is reference time: attaching an id to a record
    # happens inside a record write already gated by require_level(write).
    # `write_batch` yields the connection a batch of record writes shares and does nothing
    # else: it takes no actor, names no object type, and reads no record. Every write
    # made on the connection it yields goes through a ``*_in_txn`` method that carries
    # its own require_level, so the gate is on the writes rather than on the boundary.
    ("RecordService", "write_batch"): "yields a transaction, touches no object type",
    ("AttachmentService", "upload"): "upload names no record, so there is no type to check",
    # DD-29, on the same terms. ``upload_text`` is a delegation to ``upload``: it
    # checks a content-type allowlist, encodes UTF-8, and calls it. The gate is still at
    # reference time, when the returned id is written onto a record inside a write
    # already gated by require_level(write), which is a real check.
    # (``read_content`` needs no entry: it calls ``self.get_attachment(``, which is
    # already a GATE_EXPRESSION, and that is where the attachment read rule is applied.)
    (
        "AttachmentService",
        "upload_text",
    ): "delegates to upload, which names no record either",
    # DD-16, on the same terms and one step further removed: minting an upload
    # ticket touches no object type because it touches no attachment either. It creates
    # a credential narrowed to the upload route, which is itself exempt above, and the
    # credential's own ceiling is enforced by AccessTokenService (write scope, never
    # above the caller's). The gate that matters is still at reference time, when the
    # resulting id is written onto a record inside a write already gated by
    # require_level(write).
    (
        "AttachmentService",
        "create_upload_ticket",
    ): "DD-16: mints a credential for the upload route; names no record and no type",
    # Pure string composition over GW_BASE_URL and an id the caller already has. It
    # reads nothing, so there is nothing to authorize; whether the caller may *fetch*
    # that URL is the download route's question, which requires read scope and applies
    # the same attachment read rule.
    (
        "AttachmentService",
        "download_url",
    ): "composes a URL from a setting and an id; reads nothing",
    # An operator sweep over the blob tree by content hash. It reads no record and no
    # object type, and its route declares admin scope plus require_role('admin').
    ("AttachmentService", "sweep_orphan_blobs"): "operates on blobs by hash, not on records",
    # Both compute an inverse patch and hand it to `update_record`, which is gated. The
    # gate is one method away rather than absent, and duplicating it here would mean two
    # places to keep in step.
    ("RecordService", "revert_field_change"): "delegates to update_record, which is gated",
    ("RecordService", "revert_to_version"): "delegates to update_record, which is gated",
    # Export reaches only `SchemaService.get_object_type` and `RecordService.query_records`,
    # both of which require `read` on the type. Import is *not* exempt: it calls
    # `require_type_level(actor, key, "write")` at its entry point so a dry run is refused
    # on the same terms as a live one.
    ("CsvService", "export_csv"): "delegates to get_object_type and query_records, both gated",
    # The generator export_csv is a thin caller of, on the same terms:
    # `get_object_type`, `query_records` and `linked_keys_for_page` are each gated, and
    # the last of those is where the relation fan-out's access decision lives -- once
    # per field for the whole page rather than once per record, which is what makes the
    # empty cell for an unreadable target survive the batching.
    (
        "CsvService",
        "export_csv_stream",
    ): "delegates to get_object_type, query_records and linked_keys_for_page, all gated",
    # The sidecar is composed onto a document whose records the caller has
    # *already* been through `require_level("read")` for -- `principal_sidecar` is handed
    # those record documents, never a ref it fetches itself, so it cannot widen what the
    # response carries. What it adds is a display name and an email address per principal,
    # which `GET /principals/directory` returns to any authenticated caller anyway
    # (FR-I16) and which DD-25 publishes on every comment and audit row. It takes an
    # actor and does not read it; the docstring says so and says why.
    (
        "RecordService",
        "principal_sidecar",
    ): "reads no record: it names principals in documents the caller already passed read on",
    # Same shape and same reasoning as `principal_sidecar` directly above: it is
    # handed record documents the caller has already been through `require_level("read")` for,
    # never a ref it fetches itself, so it cannot widen what the response carries. What it adds
    # is an agent label's *name* for a label id that DD-25 publishes on every audit row -- to
    # the same caller. Its docstring records why that disclosure is right and pins what the
    # projection withholds.
    (
        "RecordService",
        "agent_label_sidecar",
    ): "reads no record: it names agent labels in documents the caller already passed read on",
}


def _entry_points(service: type) -> list[tuple[str, str]]:
    """``(method name, source)`` for every public method of ``service``."""
    out: list[tuple[str, str]] = []
    for name, member in vars(service).items():
        if name.startswith("_"):
            continue
        func = member.__func__ if isinstance(member, staticmethod | classmethod) else member
        if isinstance(member, property):
            continue
        if not callable(func):
            continue
        out.append((name, inspect.getsource(func)))
    return out


@pytest.mark.parametrize("service", ENFORCING_SERVICES, ids=lambda s: s.__name__)
def test_every_object_type_entry_point_consults_access_service(service: type) -> None:
    """Fail closed on an unenforced path."""
    unenforced: list[str] = []
    for name, source in _entry_points(service):
        if (service.__name__, name) in EXEMPT:
            continue
        if not any(gate in source for gate in GATE_EXPRESSIONS):
            unenforced.append(f"{service.__name__}.{name}")
    assert unenforced == [], (
        "These service entry points reach object-type-scoped data without consulting "
        "AccessService. Add a require_level / accessible_type_ids call, or, if the "
        "method genuinely touches no object type, name it in EXEMPT with a reason: "
        f"{unenforced}"
    )


def test_every_exemption_names_a_method_that_exists() -> None:
    """An exemption for a method that has been renamed or deleted is an exemption
    quietly covering something else. This is what stops the list rotting."""
    by_name = {s.__name__: {n for n, _ in _entry_points(s)} for s in ENFORCING_SERVICES}
    for (service_name, method), reason in EXEMPT.items():
        assert service_name in by_name, service_name
        assert method in by_name[service_name], f"{service_name}.{method} no longer exists"
        assert reason.strip(), f"{service_name}.{method} is exempt with no stated reason"


def test_the_completeness_test_can_actually_fail() -> None:
    """A guard on the guard. If ``_entry_points`` returned nothing -- a refactor to
    ``__slots__``, a decorator that hides the function -- the sweep above would pass
    vacuously on an entirely unenforced tree."""
    counts = {s.__name__: len(_entry_points(s)) for s in ENFORCING_SERVICES}
    assert all(n > 0 for n in counts.values()), counts
    assert sum(counts.values()) >= 40, counts


# ------------------------------------------------------------------- layering


def _code(path: Path) -> str:
    """``path``'s source with whole-line comments dropped.

    A prose comment naming a gate is documentation, not a call; scanning raw text would
    make these greps fail on their own explanations.
    """
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.lstrip().startswith("#")
    )


def _sources(directory: Path) -> dict[Path, str]:
    return {p: _code(p) for p in sorted(directory.rglob("*.py"))}


@pytest.mark.parametrize("package", ["routes", "mcp_server"])
def test_access_service_is_not_reachable_from_an_adapter(package: str) -> None:
    """DD-3. A route handler or an MCP tool that made its own
    authorization decision would be the "every handler grows its own slightly different
    one" failure ``scopes.py`` was written to prevent, one axis over.

    ``services.access.grant`` and friends are reached through ``ServiceBundle.access``,
    which is a *service call*, not an authorization decision: the decision is inside it.
    """
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path, source in _sources(SRC / package).items()
        if "AccessService" in source
        or "require_level(" in source
        or "accessible_type_ids(" in source
        or "effective_level(" in source
    ]
    assert offenders == [], (
        "These adapter modules reach AccessService directly. Authorization decisions "
        f"belong in the service layer (DD-3): {offenders}"
    )


def _names_grant_table(path: Path) -> bool:
    """Whether ``path`` puts ``object_type_grants`` into SQL, rather than merely naming
    the table in prose. A docstring that explains what the table is for is not a second
    place the schema is known."""
    code = _code(path)
    return any(
        f"{keyword} object_type_grants" in code
        for keyword in ("FROM", "INTO", "UPDATE", "TABLE", "JOIN", "ON")
    )


def test_raw_grant_sql_lives_only_in_the_grant_repository() -> None:
    """DD-2 -- the same argument that keeps ``vec0`` inside
    ``SqliteSearchRepository``: migrating this table stays a one-module change."""
    allowed = {
        SRC / "repositories" / "sqlite.py",
        SRC / "migrations.py",
    }
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted(SRC.rglob("*.py"))
        if path not in allowed and _names_grant_table(path)
    ]
    assert offenders == [], (
        f"Only SqliteGrantRepository may name object_type_grants in SQL: {offenders}"
    )


def test_access_service_is_the_only_module_that_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ForbiddenError`` is raised in exactly two places: ``AccessService`` (the object
    type axis) and ``scopes.require_role`` (the system role axis). Anywhere
    else would be a third place deciding what a principal may do."""
    raisers = [
        str(path.relative_to(REPO_ROOT))
        for path in sorted(SRC.rglob("*.py"))
        if "raise ForbiddenError" in _code(path) or "ForbiddenError.for_role" in _code(path)
    ]
    assert sorted(raisers) == [
        # The system-role axis, declared once per route and compared here.
        "src/glosswork/scopes.py",
        # The object-type axis, and the attachment rule with it: the rule lives here
        # because the record write funnel is its second enforcement site, so the module
        # list is the two the docstring names. If a third entry
        # appears, a third place is deciding.
        "src/glosswork/services/access.py",
    ], raisers
    assert AccessService is not None  # the import is the point of the module boundary
