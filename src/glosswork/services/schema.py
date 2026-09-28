"""Schema engine: object types, fields, additive changes, and the destructive-change
proposal model (FR-S1 through FR-S10, docs/DATA_MODEL.md sections 3-5).

Additive changes apply on the call (FR-S5). Destructive changes create a proposal
with a computed impact document (FR-S6, FR-S8); service-layer approval recomputes
the impact, snapshots affected data to the audit store (FR-S7), and applies with
the approver's elections (FR-S9).
"""

from __future__ import annotations

import difflib
import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from glosswork.actor import ActorContext, Level, Scope
from glosswork.auth import level_allows
from glosswork.cursors import decode_cursor, encode_cursor, validate_page_limit
from glosswork.db import Database
from glosswork.errors import (
    ImpactChangedError,
    NotFoundError,
    ProposalStateError,
    UnknownFieldError,
    UnknownObjectTypeError,
    ValidationFailedError,
)
from glosswork.fieldtypes import (
    AUTO_INDEXED_TYPES,
    LOW_CARDINALITY_TYPES,
    NON_INDEXABLE_TYPES,
    PSEUDO_FIELDS,
    SORT_COMPOSITE_TYPES,
    coerce_value,
    is_display_eligible,
    option_values,
    require_description,
    validate_config,
    validate_field_key,
    validate_key,
    validate_key_prefix,
    validate_value,
)
from glosswork.repositories.interfaces import (
    AgentLabelRepository,
    AuditRepository,
    PrincipalRepository,
    RecordRepository,
    SchemaRepository,
)
from glosswork.repositories.models import FieldDef, ObjectType, Proposal
from glosswork.serializers import agent_label_sidecar_doc, principal_sidecar_doc
from glosswork.services.access import AccessService, require_level_value
from glosswork.services.base import make_event
from glosswork.services.search_index import SearchIndexService, collect_fan_outs
from glosswork.sqlexpr import (
    SORT_INDEX_PREFIX,
    drop_index_by_name_ddl,
    index_ddl,
    index_name,
    index_prefix,
    sort_index_ddl,
    sort_index_name,
    sort_index_prefix,
)
from glosswork.timeutil import format_datetime, utc_now

DESTRUCTIVE_CHANGE_TYPES = frozenset(
    {
        "delete_field",
        "change_field_type",
        "delete_object_type",
        "remove_enum_option",
        "tighten_constraint",
    }
)

_FIELD_SPEC_KEYS = frozenset(
    {
        "key",
        "name",
        "type",
        "description",
        "config",
        "required",
        "unique",
        "indexed",
        "embed",
        "default",
    }
)
_FIELD_CHANGE_KEYS = frozenset(
    {"name", "description", "config", "required", "unique", "indexed", "embed", "default", "type"}
)
_IMPACT_SAMPLE_SIZE = 5

#: The proposal list's page size, and its ceiling (DD-18).
#:
#: Without a ``LIMIT``, a cursor and a published cap, ``GET /api/v1/schema-proposals`` would
#: return every proposal a deployment had ever raised, and the ``target`` projection fans out
#: over the result, which is the read DD-25 refuses. The names follow ``DEFAULT_COMMENT_LIMIT`` /
#: ``MAX_COMMENT_LIMIT`` and ``DIRECTORY_DEFAULT_LIMIT`` / ``DIRECTORY_MAX_LIMIT``, and both are
#: published in ``describe_capabilities`` because a cap an agent can hit and cannot read is a cap
#: it discovers by failing.
DEFAULT_PROPOSAL_LIMIT = 50
MAX_PROPOSAL_LIMIT = 200


@dataclass(slots=True)
class ProposalPage:
    """One page of proposals, plus what the page cannot say about itself.

    ``total_count`` is **not** ``len(proposals)``. The sidebar's Inbox badge reads a count, and a
    count taken from a page's length silently caps at the page size -- a wrong number rendered
    confidently, which is also why the Inbox badge is ``null`` rather than ``0`` when it cannot
    know. Both are computed under the same authorization filter, so the total never counts a
    proposal the caller could not have been shown.
    """

    proposals: list[Proposal]
    next_cursor: str | None
    total_count: int
    #: Keyed by proposal id, one entry per proposal on this page (DD-25).
    targets: dict[str, dict[str, Any]]
    #: DD-25's two maps, keyed by id. Assembled here rather than in the envelope, exactly as
    #: ``GrantListing`` carries its own ``principals``: the service reads, the envelope places.
    principals: dict[str, dict[str, Any]]
    agent_labels: dict[str, dict[str, Any]]


class FieldUpdateResult:
    """Which path a field update took (FR-S6): applied immediately, or proposed."""

    def __init__(
        self, applied: bool, field: FieldDef | None, proposal: Proposal | None, message: str
    ) -> None:
        self.applied = applied
        self.field = field
        self.proposal = proposal
        self.message = message


# The three prefixes a per-type index name can start with. Matched literally, never as a
# LIKE pattern.
LEGACY_INDEX_PREFIXES: tuple[str, ...] = ("ix_rec_", "ux_rec_", f"{SORT_INDEX_PREFIX}_")

_HEX = frozenset("0123456789abcdef")


def _names_an_object_type_id(name: str, prefix: str) -> bool:
    """Does ``name`` carry a 32-hex object type id straight after ``prefix``?

    The single sanctioned reading of an index name's structure (DD-12), and it
    exists only to tell a name this version generates from one it does not, so that the
    one-time retirement sweep drops the latter and leaves the former alone. Nothing else
    parses a name: names are generated, listed by prefix, and diffed.
    """
    rest = name[len(prefix) :]
    return len(rest) > 33 and rest[32] == "_" and set(rest[:32]) <= _HEX


class SchemaService:
    def __init__(
        self,
        db: Database,
        schema_repo: SchemaRepository,
        record_repo: RecordRepository,
        audit_repo: AuditRepository,
        access: AccessService,
        search_index: SearchIndexService | None = None,
        principal_repo: PrincipalRepository | None = None,
        agent_label_repo: AgentLabelRepository | None = None,
    ) -> None:
        self._db = db
        self._schema = schema_repo
        self._records = record_repo
        self._audit = audit_repo
        # DD-25. The two sidecars ride on the proposal list, so this service needs
        # to read a principal and a label by id. Optional in the signature and asserted where
        # used, matching ``RecordService``'s own treatment of the same two repositories: nothing
        # outside ``build_services`` constructs a service, and the tests that build one by hand
        # are about schema mechanics rather than about who raised a proposal.
        self._principals = principal_repo
        self._labels = agent_label_repo
        # DD-11. **Required**, deliberately: an optional access service would give
        # every entry point a silent unenforced branch, which is exactly the failure mode
        # the access completeness meta-test exists to catch. Nothing outside
        # ``build_services`` constructs a service, so there is nothing to keep working.
        self._access = access
        # Schema changes fan out into the search index (FR-Q3): turning ``embed`` off
        # purges a field's rows, and an approved delete or type change purges or
        # re-indexes whatever crossed the eligibility line. The fan-out runs after the
        # change commits, in paused batches, and the purge stays inside it (DD-34).
        self._search_index = search_index

    # ------------------------------------------------------------------ reads
    #
    # Every read below takes an ``ActorContext``. DD-4 requires an actor on every *write*
    # path for attribution; access control needs one on every read too, because the
    # third authorization axis is per principal and these are the entry points the
    # adapters call. The parameter is first and positional, exactly as on the writes.

    def list_object_types(self, actor: ActorContext) -> list[ObjectType]:
        """Only the types this actor holds at least ``read`` on.

        **Omitted, not refused** (FR-M4's rule): an orientation call is
        how an agent finds out what exists, so answering it with an error because one
        type is closed would make the whole surface unusable to a narrowly granted
        agent. A type the caller cannot read simply is not there.
        """
        with self._db.read() as conn:
            types = self._schema.list_object_types(conn)
            levels = self._access.effective_levels(conn, actor, types)
            return [t for t in types if level_allows(levels[t.id], "read")]

    def get_object_type(self, actor: ActorContext, key: str) -> tuple[ObjectType, list[FieldDef]]:
        with self._db.read() as conn:
            object_type = self._require_type(conn, key)
            self._access.require_level(conn, actor, object_type, "read")
            return object_type, self._schema.list_fields(conn, object_type.id)

    def get_object_type_by_id(
        self, actor: ActorContext, type_id: str
    ) -> tuple[ObjectType, list[FieldDef]]:
        with self._db.read() as conn:
            object_type = self._schema.get_object_type_by_id(conn, type_id)
            if object_type is None or object_type.is_deleted:
                raise NotFoundError("object type", type_id)
            self._access.require_level(conn, actor, object_type, "read")
            return object_type, self._schema.list_fields(conn, object_type.id)

    def get_proposal(self, actor: ActorContext, proposal_id: str) -> Proposal:
        """``write`` on the proposal's target type.

        This does not mean members may read proposals --
        ``GET /api/v1/schema-proposals/{id}`` declares ``admin`` scope. It means an
        ``admin``-scoped principal sees only the proposals against types it holds
        ``write`` on.
        """
        with self._db.read() as conn:
            proposal = self._schema.get_proposal(conn, proposal_id)
            if proposal is None:
                raise NotFoundError("schema proposal", proposal_id)
            self._require_proposal_level(conn, actor, proposal, "write")
            return proposal

    def list_proposals_page(
        self,
        actor: ActorContext,
        status: str | None = None,
        limit: int = DEFAULT_PROPOSAL_LIMIT,
        cursor: str | None = None,
    ) -> ProposalPage:
        """One bounded page of proposals, newest first, filtered to types this actor holds
        ``write`` on. Same reading as :meth:`get_proposal`.

        **The access filter is a SQL clause, not a list comprehension.** A list comprehension
        would be correct only for an unbounded query: filtering after ``LIMIT`` yields short
        pages, and counting before the filter reports proposals the caller may not see. Both the
        page and ``total_count`` come back already narrowed.

        A proposal whose ``target_type_id`` is null has no producer today and is visible to
        nobody, which is the safe direction and the one the list comprehension also took.
        """
        validate_page_limit(limit, MAX_PROPOSAL_LIMIT, "MAX_PROPOSAL_LIMIT")
        after: tuple[str, str] | None = None
        if cursor is not None:
            boundary = decode_cursor(cursor, {"proposed_at", "id"})
            after = (str(boundary["proposed_at"]), str(boundary["id"]))
        with self._db.read() as conn:
            writable = self._access.accessible_type_ids(conn, actor, "write")
            rows = self._schema.list_proposals(
                conn, status, type_ids=writable, after=after, limit=limit + 1
            )
            total = self._schema.count_proposals(conn, status, type_ids=writable)
        next_cursor: str | None = None
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            next_cursor = encode_cursor({"proposed_at": last.proposed_at, "id": last.id})
        return ProposalPage(
            proposals=rows,
            next_cursor=next_cursor,
            total_count=total,
            targets=self.proposal_targets_for(rows),
            principals=self._principal_sidecar(rows),
            agent_labels=self._agent_label_sidecar(rows),
        )

    def _principal_sidecar(self, proposals: list[Proposal]) -> dict[str, dict[str, Any]]:
        """Who raised each proposal, by id. One batched read per page (DD-25)."""
        ids = [p.proposed_by for p in proposals if p.proposed_by]
        ids += [p.decided_by for p in proposals if p.decided_by]
        if not ids or self._principals is None:
            return {}
        with self._db.read() as conn:
            rows = self._principals.principals_by_ids(conn, list(dict.fromkeys(ids)))
        return principal_sidecar_doc(rows)

    def _agent_label_sidecar(self, proposals: list[Proposal]) -> dict[str, dict[str, Any]]:
        """Which agent raised each proposal, by id. A proposal with no label contributes
        nothing and a page raised entirely by people costs no read at all -- docs/DESIGN.md 6.5
        then renders the person, which is under-claiming agency rather than inventing it."""
        ids = [p.proposed_agent for p in proposals if p.proposed_agent]
        if not ids or self._labels is None:
            return {}
        with self._db.read() as conn:
            rows = self._labels.labels_by_ids(conn, list(dict.fromkeys(ids)))
        return agent_label_sidecar_doc(rows)

    def proposal_targets_for(self, proposals: list[Proposal]) -> dict[str, dict[str, Any]]:
        """What each proposal is *about*, in words, keyed by proposal id (DD-25).

        Five keys, always all five: ``field_*`` is null for ``delete_object_type`` rather than
        absent, because a document whose key set depends on its change type is one every reader
        must branch on before it can read it.

        **Resolved by id, never from the live schema listing**, and that distinction is the whole
        correctness of this function. Approving a ``delete_field`` marks the field row
        ``is_deleted`` and approving a ``delete_object_type`` retires the type, so a projection
        built from ``list_object_types`` would render exactly the decision that just happened as
        a blank -- the approve response would stop naming the thing it had just removed.
        ``get_object_type_by_id`` and ``get_field_by_id`` read the rows the proposal named,
        deleted or not. An id naming no row yields nulls rather than raising (DD-27: a reference
        the reader cannot resolve is shown as withheld, never composed away).

        **One read per distinct id, not one per proposal.** A page of fifty proposals against one
        object type costs one type read, not fifty. "N+1 but correct" passes every functional
        assertion in this area and is invisible until it is counted, which is why
        ``tests/test_proposal_target_projection.py`` counts repository calls.

        Discloses a key and a name for a type the caller already holds ``write`` on, which is
        strictly less than ``describe_object_type`` hands the same caller.
        """
        if not proposals:
            return {}
        type_ids = {p.target_type_id for p in proposals if p.target_type_id is not None}
        field_ids = {p.target_field_id for p in proposals if p.target_field_id is not None}
        with self._db.read() as conn:
            types = {
                type_id: self._schema.get_object_type_by_id(conn, type_id) for type_id in type_ids
            }
            fields = {
                field_id: self._schema.get_field_by_id(conn, field_id) for field_id in field_ids
            }
        out: dict[str, dict[str, Any]] = {}
        for proposal in proposals:
            object_type = types.get(proposal.target_type_id or "")
            field = fields.get(proposal.target_field_id or "")
            out[proposal.id] = {
                "object_type_key": object_type.key if object_type is not None else None,
                "object_type_name": object_type.name if object_type is not None else None,
                # The sentence says "remove the Notes field from Prospects", and a type is a
                # collection there. Carrying only the singular produced "from Inbox Probe" in
                # e2e, which is the kind of wrong English a screenshot cannot catch.
                "object_type_name_plural": (
                    object_type.name_plural if object_type is not None else None
                ),
                "field_key": field.key if field is not None else None,
                "field_name": field.name if field is not None else None,
                "field_type": field.type if field is not None else None,
            }
        return out

    def your_access(self, actor: ActorContext, key: str) -> Level:
        """``effective(credential, T)`` for one type, for the orientation paths.

        Carries the **already composed** value, never the raw grant, so an agent holding
        a ``read`` PAT on a type it administers is told ``read`` -- which is the truth
        about what it can do right now, and the only answer it can act on.
        """
        with self._db.read() as conn:
            object_type = self._require_type(conn, key)
            return self._access.effective_level(conn, actor, object_type)

    # ----------------------------------------------------------------- writes

    def create_object_type(
        self,
        actor: ActorContext,
        *,
        key: str,
        name: str,
        name_plural: str,
        description: str,
        key_prefix: str,
        fields: list[dict[str, Any]] | None = None,
        icon: str | None = None,
        display_field_key: str | None = None,
        now: datetime | None = None,
    ) -> ObjectType:
        validate_key(key, "Object type")
        validate_key_prefix(key_prefix)
        description = require_description(description, f"Object type {key!r}")
        if not name.strip() or not name_plural.strip():
            raise ValidationFailedError("Object type name and name_plural are required.")
        display_field_key = self._resolve_creation_display_field(display_field_key, fields or [])
        ts = format_datetime(now or utc_now())
        row = ObjectType(
            id=str(uuid.uuid4()),
            key=key,
            name=name,
            name_plural=name_plural,
            description=description,
            key_prefix=key_prefix,
            key_counter=0,
            icon=icon,
            is_deleted=False,
            display_field_key=display_field_key,
            created_at=ts,
            created_by=actor.principal_id,
            updated_at=ts,
            updated_by=actor.principal_id,
        )
        with self._db.write() as conn:
            # Creating an object type needs `role >= creator`, which
            # is what makes "let a colleague define their own type" something other than
            # "promote them to system administrator".
            self._access.require_creator_role(conn, actor)
            try:
                self._schema.insert_object_type(conn, row)
            except IntegrityError as exc:
                raise _map_type_integrity_error(exc, key, key_prefix) from exc
            # The creator's own `admin` grant, in the same transaction as the type.
            # Inserted for a `role == 'admin'` creator too, where it is
            # strictly redundant: the redundancy makes "who owns this type" a legible
            # query against one table rather than a rule the reader has to know.
            self._access.grant_owner_in_txn(conn, actor, row.id, ts)
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="object_type",
                        entity_id=row.id,
                        action="create",
                        object_type_id=row.id,
                        new_value={
                            "key": key,
                            "name": name,
                            "description": description,
                            "key_prefix": key_prefix,
                            "display_field_key": display_field_key,
                        },
                    )
                ],
            )
            for spec in fields or []:
                self._add_field_in_txn(conn, actor, ts, row, spec)
        return row

    @staticmethod
    def _resolve_creation_display_field(
        display_field_key: str | None, fields: list[dict[str, Any]]
    ) -> str | None:
        """The chosen display field for a type being created.

        Supplied: it must name one of the ``fields`` specs and that spec's type must be
        display-eligible. Omitted with a non-empty list: the first **eligible** field, so
        a type whose first declared field is a relation gets a usable default instead of
        an immediately-dangling one. Omitted with no fields: ``None`` — the type has
        nothing to label a record with yet, and ``display_field`` returns ``None`` for it.
        """
        eligible = [
            str(spec["key"])
            for spec in fields
            if spec.get("key") is not None and is_display_eligible(str(spec.get("type", "")))
        ]
        if display_field_key is None:
            return eligible[0] if eligible else None
        if display_field_key not in eligible:
            raise ValidationFailedError(
                f"display_field_key {display_field_key!r} must name one of this type's "
                f"fields whose type can label a record; eligible keys: "
                f"{eligible if eligible else 'none'}."
            )
        return display_field_key

    @staticmethod
    def _validate_display_field_key(value: Any, fields: list[FieldDef]) -> str | None:
        """The chosen display field for a type being updated, resolved
        against the type's **live** fields, so a deleted field's key is rejected on the
        way in. Explicit ``None`` clears the choice and returns the type to the derived
        rule."""
        if value is None:
            return None
        eligible = [f.key for f in fields if is_display_eligible(f.type)]
        if not isinstance(value, str) or value not in eligible:
            raise ValidationFailedError(
                f"display_field_key {value!r} must name one of this type's fields whose "
                f"type can label a record; eligible keys: "
                f"{eligible if eligible else 'none'}."
            )
        return value

    def update_object_type(
        self,
        actor: ActorContext,
        object_type_key: str,
        changes: dict[str, Any],
        now: datetime | None = None,
    ) -> ObjectType:
        immutable = {"key", "key_prefix"} & set(changes)
        if immutable:
            raise ValidationFailedError(
                f"{sorted(immutable)} are immutable after creation (FR-S2). Create a new "
                "object type instead."
            )
        unknown = set(changes) - {
            "name",
            "name_plural",
            "description",
            "icon",
            "default_level",
            "display_field_key",
        }
        if unknown:
            raise ValidationFailedError(
                f"Unknown object type changes {sorted(unknown)}; allowed: name, "
                "name_plural, description, icon, default_level, display_field_key."
            )
        if "description" in changes:
            changes["description"] = require_description(
                changes["description"], f"Object type {object_type_key!r}"
            )
        if "default_level" in changes:
            # DD-11: what a principal with no grant row gets. Validated here rather
            # than left to the CHECK constraint so the caller gets the vocabulary back.
            changes["default_level"] = require_level_value(changes["default_level"])
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            object_type = self._require_type(conn, object_type_key)
            self._access.require_level(conn, actor, object_type, "admin")
            if "display_field_key" in changes:
                # Not a destructive change and no proposal. No stored record
                # value moves; only how one is labeled. Validated against the live fields
                # inside the transaction that reads them.
                changes["display_field_key"] = self._validate_display_field_key(
                    changes["display_field_key"], self._schema.list_fields(conn, object_type.id)
                )
            old = {k: getattr(object_type, k) for k in changes}
            self._schema.update_object_type_row(
                conn,
                object_type.id,
                {**changes, "updated_at": ts, "updated_by": actor.principal_id},
            )
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="object_type",
                        entity_id=object_type.id,
                        action="update",
                        object_type_id=object_type.id,
                        old_value=old,
                        new_value=changes,
                    )
                ],
            )
            refreshed = self._schema.get_object_type_by_key(conn, object_type_key)
            assert refreshed is not None
            return refreshed

    def add_field(
        self,
        actor: ActorContext,
        object_type_key: str,
        spec: dict[str, Any],
        now: datetime | None = None,
    ) -> FieldDef:
        """Additive: applies immediately (FR-S5)."""
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            object_type = self._require_type(conn, object_type_key)
            self._access.require_level(conn, actor, object_type, "admin")
            return self._add_field_in_txn(conn, actor, ts, object_type, spec)

    def update_field(
        self,
        actor: ActorContext,
        object_type_key: str,
        field_key: str,
        changes: dict[str, Any],
        reason: str | None = None,
        now: datetime | None = None,
    ) -> FieldUpdateResult:
        """Auto-routes to the additive or proposal path (FR-S5, FR-S6) and says which."""
        if "key" in changes:
            raise ValidationFailedError(
                "Field keys are immutable after creation (FR-S3). Add a new field instead."
            )
        unknown = set(changes) - _FIELD_CHANGE_KEYS
        if unknown:
            raise ValidationFailedError(
                f"Unknown field changes {sorted(unknown)}; allowed: "
                f"{', '.join(sorted(_FIELD_CHANGE_KEYS))}."
            )
        ts = format_datetime(now or utc_now())
        with self._fan_out_scope(now), self._db.write() as conn:
            object_type = self._require_type(conn, object_type_key)
            self._access.require_level(conn, actor, object_type, "admin")
            field = self._require_field(conn, object_type, field_key)
            destructive = self._detect_destructive(conn, object_type, field, changes)
            if destructive is None:
                updated = self._apply_field_changes(conn, actor, ts, object_type, field, changes)
                return FieldUpdateResult(
                    True,
                    updated,
                    None,
                    "Change was additive and has been applied immediately (FR-S5).",
                )
            change_type, impact_payload = destructive
            implicated = {
                "change_field_type": {"type", "config"},
                "remove_enum_option": {"config"},
                "tighten_constraint": {str(impact_payload.get("constraint"))},
            }[change_type]
            extra = set(changes) - implicated
            if extra:
                raise ValidationFailedError(
                    f"This change mixes a destructive component ({change_type}) with "
                    f"additive changes {sorted(extra)}. Apply the additive changes in "
                    "their own call, then propose the destructive one, so the proposal "
                    "is reviewable on its own.",
                    field.key,
                )
            proposal = self._create_proposal(
                conn,
                actor,
                ts,
                change_type,
                object_type,
                field,
                {**impact_payload, "changes": changes},
                reason,
            )
            return FieldUpdateResult(
                False,
                None,
                proposal,
                f"Change is destructive ({change_type}) and requires human approval. "
                f"Nothing was applied. An administrator must approve proposal "
                f"{proposal.id} (FR-S6).",
            )

    def propose_schema_change(
        self,
        actor: ActorContext,
        change_type: str,
        object_type_key: str,
        field_key: str | None = None,
        payload: dict[str, Any] | None = None,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> Proposal:
        """Explicit proposal entry point; deletes never apply directly (FR-S6)."""
        if change_type not in DESTRUCTIVE_CHANGE_TYPES:
            raise ValidationFailedError(
                f"Unknown change_type {change_type!r}. Destructive change types: "
                f"{', '.join(sorted(DESTRUCTIVE_CHANGE_TYPES))}."
            )
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            object_type = self._require_type(conn, object_type_key)
            # `write`, not `admin`, because proposing is not deciding. It does not admit a
            # weaker credential -- POST /api/v1/schema-proposals declares `admin` scope --
            # it narrows which types an
            # already-admin-scoped principal may propose against.
            self._access.require_level(conn, actor, object_type, "write")
            field: FieldDef | None = None
            if change_type != "delete_object_type":
                if field_key is None:
                    raise ValidationFailedError(f"change_type {change_type!r} requires field_key.")
                field = self._require_field(conn, object_type, field_key)
            if change_type == "change_field_type":
                to_type = (payload or {}).get("to_type")
                if not isinstance(to_type, str):
                    raise ValidationFailedError("change_field_type requires payload.to_type.")
                if to_type in ("relation", "attachment"):
                    raise ValidationFailedError(
                        f"Changing a field to {to_type!r} is not supported; delete the "
                        "field and create a new one (docs/DATA_MODEL.md section 4)."
                    )
            return self._create_proposal(
                conn, actor, ts, change_type, object_type, field, payload or {}, reason
            )

    def approve_proposal(
        self,
        actor: ActorContext,
        proposal_id: str,
        *,
        null_non_coercible: bool = False,
        create_missing_options: bool = False,
        confirm_impact: dict[str, Any] | None = None,
        decision_note: str | None = None,
        now: datetime | None = None,
    ) -> Proposal:
        """Service-layer approval (FR-S7): recompute impact, snapshot, apply."""
        ts = format_datetime(now or utc_now())
        with self._fan_out_scope(now), self._db.write() as conn:
            proposal = self._schema.get_proposal(conn, proposal_id)
            if proposal is None:
                raise NotFoundError("schema proposal", proposal_id)
            if proposal.status != "pending":
                raise ProposalStateError(proposal_id, proposal.status)
            object_type, field = self._proposal_targets(conn, proposal)
            self._access.require_level(conn, actor, object_type, "admin")

            recomputed = self._compute_impact(
                conn, proposal.change_type, object_type, field, proposal.payload
            )
            if _impact_json(recomputed) != _impact_json(proposal.impact) and (
                confirm_impact is None or _impact_json(recomputed) != _impact_json(confirm_impact)
            ):
                raise ImpactChangedError(proposal_id, recomputed)

            snapshot = self._build_snapshot(conn, proposal, object_type, field)
            snapshot_ids = self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="schema_proposal",
                        entity_id=proposal.id,
                        action="snapshot",
                        object_type_id=object_type.id,
                        old_value=snapshot,
                        note=f"pre-change snapshot for proposal {proposal.id} (FR-S7)",
                    )
                ],
            )
            self._apply_proposal(
                conn,
                actor,
                ts,
                proposal,
                object_type,
                field,
                null_non_coercible=null_non_coercible,
                create_missing_options=create_missing_options,
            )
            self._schema.update_proposal_row(
                conn,
                proposal.id,
                {
                    "status": "approved",
                    "impact": recomputed,
                    "snapshot_ref": str(snapshot_ids[0]),
                    "decided_at": ts,
                    "decided_by": actor.principal_id,
                    "decision_note": decision_note,
                },
            )
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="schema_proposal",
                        entity_id=proposal.id,
                        action="update",
                        object_type_id=object_type.id,
                        old_value={"status": "pending"},
                        new_value={"status": "approved"},
                        note=decision_note,
                    )
                ],
            )
            approved = self._schema.get_proposal(conn, proposal.id)
            assert approved is not None
            return approved

    def reject_proposal(
        self,
        actor: ActorContext,
        proposal_id: str,
        decision_note: str | None = None,
        now: datetime | None = None,
    ) -> Proposal:
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            proposal = self._schema.get_proposal(conn, proposal_id)
            if proposal is None:
                raise NotFoundError("schema proposal", proposal_id)
            if proposal.status != "pending":
                raise ProposalStateError(proposal_id, proposal.status)
            self._require_proposal_level(conn, actor, proposal, "admin")
            self._schema.update_proposal_row(
                conn,
                proposal.id,
                {
                    "status": "rejected",
                    "decided_at": ts,
                    "decided_by": actor.principal_id,
                    "decision_note": decision_note,
                },
            )
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="schema_proposal",
                        entity_id=proposal.id,
                        action="update",
                        object_type_id=proposal.target_type_id,
                        old_value={"status": "pending"},
                        new_value={"status": "rejected"},
                        note=decision_note,
                    )
                ],
            )
            rejected = self._schema.get_proposal(conn, proposal.id)
            assert rejected is not None
            return rejected

    # -------------------------------------------------------------- internals

    def _require_proposal_level(
        self, conn: Connection, actor: ActorContext, proposal: Proposal, required: Scope
    ) -> None:
        """The level check for a proposal, expressed against its target object type.

        A proposal with a null ``target_type_id`` has no type to check against and no
        producer today; it is treated as reachable only by a principal that holds
        ``required`` on everything, which is the fail-closed direction.
        """
        if proposal.target_type_id is None:
            self._access.require_every_type(conn, actor, required, "(untargeted proposal)")
            return
        object_type = self._schema.get_object_type_by_id(conn, proposal.target_type_id)
        if object_type is None:
            raise NotFoundError("object type", proposal.target_type_id)
        self._access.require_level(conn, actor, object_type, required)

    def _require_type(self, conn: Connection, key: str) -> ObjectType:
        object_type = self._schema.get_object_type_by_key(conn, key)
        if object_type is None:
            valid = [t.key for t in self._schema.list_object_types(conn)]
            raise UnknownObjectTypeError(key, valid)
        return object_type

    def _require_field(self, conn: Connection, object_type: ObjectType, field_key: str) -> FieldDef:
        field = self._schema.get_field(conn, object_type.id, field_key)
        if field is None:
            valid = [f.key for f in self._schema.list_fields(conn, object_type.id)]
            near = difflib.get_close_matches(field_key, valid, n=3, cutoff=0.6)
            raise UnknownFieldError(field_key, object_type.key, valid, list(near))
        return field

    def _add_field_in_txn(
        self,
        conn: Connection,
        actor: ActorContext,
        ts: str,
        object_type: ObjectType,
        spec: dict[str, Any],
        create_inverse: bool = True,
    ) -> FieldDef:
        unknown = set(spec) - _FIELD_SPEC_KEYS
        if unknown:
            raise ValidationFailedError(
                f"Unknown field spec keys {sorted(unknown)}; allowed: "
                f"{', '.join(sorted(_FIELD_SPEC_KEYS))}."
            )
        field_key = validate_field_key(spec.get("key"), "Field")
        field_type = spec.get("type")
        if not isinstance(field_type, str):
            raise ValidationFailedError(f"Field {field_key!r} requires a type.", field_key)
        description = require_description(spec.get("description"), f"Field {field_key!r}")
        name = spec.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValidationFailedError(f"Field {field_key!r} requires a name.", field_key)
        config = validate_config(field_type, dict(spec.get("config") or {}), field_key)

        if self._schema.get_field(conn, object_type.id, field_key) is not None:
            raise ValidationFailedError(
                f"Object type {object_type.key!r} already has a field {field_key!r}.",
                field_key,
            )

        required = bool(spec.get("required", False))
        unique = bool(spec.get("unique", False))
        if field_type == "relation":
            if required or unique or spec.get("indexed") or "default" in spec:
                raise ValidationFailedError(
                    f"Relation field {field_key!r} cannot be required, unique, indexed, "
                    "or defaulted; its values live in links.",
                    field_key,
                )
            target = self._schema.get_object_type_by_key(conn, config["target_type_key"])
            if target is None:
                valid = [t.key for t in self._schema.list_object_types(conn)]
                raise UnknownObjectTypeError(str(config["target_type_key"]), valid)
        if field_type in ("multi_select", "attachment") and unique:
            raise ValidationFailedError(
                f"Field {field_key!r} ({field_type}) cannot be unique.", field_key
            )

        indexed = bool(spec.get("indexed", False)) or field_type in AUTO_INDEXED_TYPES or unique
        if field_type in ("relation", "attachment"):
            indexed = False
        embed_default = field_type == "long_text"
        embed = bool(spec.get("embed", embed_default))

        default_value = spec.get("default")
        if default_value is not None:
            default_value = validate_value(field_type, config, field_key, default_value)

        field = FieldDef(
            id=str(uuid.uuid4()),
            object_type_id=object_type.id,
            key=field_key,
            name=name,
            description=description,
            type=field_type,
            position=self._schema.next_field_position(conn, object_type.id),
            is_required=required,
            is_unique=unique,
            is_indexed=indexed,
            embed=embed,
            default_value=default_value,
            config=config,
            is_deleted=False,
            created_at=ts,
            created_by=actor.principal_id,
            updated_at=ts,
            updated_by=actor.principal_id,
        )
        self._schema.insert_field(conn, field)
        self._ensure_field_indexes(conn, object_type)
        self._audit.append(
            conn,
            [
                make_event(
                    actor,
                    ts,
                    entity_type="field",
                    entity_id=field.id,
                    action="create",
                    object_type_id=object_type.id,
                    field_key=field_key,
                    new_value={"key": field_key, "type": field_type, "description": description},
                )
            ],
        )
        if field_type == "relation" and create_inverse:
            inverse_key = config.get("inverse_field_key")
            if inverse_key is not None:
                self._create_inverse_field(conn, actor, ts, object_type, field, str(inverse_key))
        return field

    def _create_inverse_field(
        self,
        conn: Connection,
        actor: ActorContext,
        ts: str,
        object_type: ObjectType,
        field: FieldDef,
        inverse_key: str,
    ) -> None:
        """Auto-create the reciprocal relation field on the target type (FR-L3)."""
        target = self._schema.get_object_type_by_key(conn, field.config["target_type_key"])
        assert target is not None  # validated by the caller
        if self._schema.get_field(conn, target.id, inverse_key) is not None:
            raise ValidationFailedError(
                f"Cannot auto-create inverse field {inverse_key!r}: object type "
                f"{target.key!r} already has a field with that key.",
                inverse_key,
            )
        pretty = inverse_key.replace("_", " ").capitalize()
        self._add_field_in_txn(
            conn,
            actor,
            ts,
            target,
            {
                "key": inverse_key,
                "name": pretty,
                "type": "relation",
                "description": (
                    f"Automatically maintained inverse of {object_type.key}.{field.key}: "
                    f"the {object_type.name_plural.lower()} linking here through that field."
                ),
                "config": {
                    "target_type_key": object_type.key,
                    "cardinality": "many",
                    "inverse_field_key": field.key,
                },
            },
            create_inverse=False,
        )

    def _ensure_field_indexes(self, conn: Connection, object_type: ObjectType) -> None:
        """Make every index on ``records`` for this object type match its schema.

        Called after each write of a field row, and it is the *whole* of index
        maintenance: per-field indexes through :meth:`_reconcile_field_indexes` and
        composites through :meth:`reconcile_sort_indexes`, both declarative, both
        reading the field rows as they now stand.

        An incremental per-field half that took ``was_unique`` and ``was_indexed`` from
        the caller would drift: call sites passing ``was_indexed=False`` unconditionally
        are harmless only while ``index_ddl`` guards its create against an existing name,
        and that same guard turns a name collision into silence. Neither exists.
        """
        fields = self._schema.list_fields(conn, object_type.id)
        self._reconcile_field_indexes(conn, object_type, fields)
        # One field's flags can add or remove several composites, so the whole set is
        # recomputed rather than patched (see ``reconcile_sort_indexes``).
        self.reconcile_sort_indexes(conn, object_type, fields)

    def _reconcile_field_indexes(
        self, conn: Connection, object_type: ObjectType, fields: list[FieldDef]
    ) -> tuple[list[str], list[str]]:
        """Create missing per-field indexes and drop stale ones. Returns (created, dropped).

        The same declarative shape :meth:`reconcile_sort_indexes` has, for the same
        reason: the desired set is a
        pure function of the field rows, so it cannot drift from what four call sites
        each believed the previous state to be, and it backfills a deployment that
        upgraded into this version.

        **The desired set is the flags minus two field types.** ``relation`` and
        ``attachment`` fields can carry ``is_indexed = 1`` -- ``add_field`` sets the flag
        from the spec, ``AUTO_INDEXED_TYPES`` or ``unique`` with no type guard -- and
        have never had an index, because the old incremental path returned early for
        them. Reading the flags alone would create indexes at first startup that have
        never existed. ``fieldtypes`` decides which types those are, not this method.

        A unique field is served by its unique index alone (docs/DATA_MODEL.md section
        5); a plain indexed field gets the non-unique index.
        """
        desired: dict[str, tuple[str, bool]] = {}
        for field in fields:
            if field.is_deleted or field.type in NON_INDEXABLE_TYPES:
                continue
            if field.is_unique:
                desired[index_name(object_type.id, field.key, unique=True)] = (field.key, True)
            elif field.is_indexed:
                desired[index_name(object_type.id, field.key)] = (field.key, False)
        existing = set(self._schema.list_index_names(conn, index_prefix(object_type.id))) | set(
            self._schema.list_index_names(conn, index_prefix(object_type.id, unique=True))
        )

        created = []
        for name, (field_key, unique) in sorted(desired.items()):
            if name not in existing:
                self._schema.execute_index_ddl(
                    conn, index_ddl(object_type.id, field_key, unique=unique)
                )
                created.append(name)
        dropped = []
        for name in sorted(existing - set(desired)):
            self._schema.execute_index_ddl(conn, drop_index_by_name_ddl(name))
            dropped.append(name)
        return created, dropped

    def _apply_field_changes(
        self,
        conn: Connection,
        actor: ActorContext,
        ts: str,
        object_type: ObjectType,
        field: FieldDef,
        changes: dict[str, Any],
    ) -> FieldDef:
        """Apply an additive change set to a field (FR-S5)."""
        row_changes: dict[str, Any] = {}
        old: dict[str, Any] = {}
        new_config = field.config
        new_type = field.type
        if "type" in changes:  # same type: no-op component
            changes = {k: v for k, v in changes.items() if k != "type"}
        if "config" in changes:
            new_config = validate_config(new_type, dict(changes["config"]), field.key)
            old["config"] = field.config
            row_changes["config"] = new_config
        if "name" in changes:
            if not isinstance(changes["name"], str) or not changes["name"].strip():
                raise ValidationFailedError(
                    f"Field {field.key!r} requires a non-empty name.", field.key
                )
            old["name"] = field.name
            row_changes["name"] = changes["name"]
        if "description" in changes:
            old["description"] = field.description
            row_changes["description"] = require_description(
                changes["description"], f"Field {field.key!r}"
            )
        if "required" in changes:
            old["is_required"] = field.is_required
            row_changes["is_required"] = bool(changes["required"])
        if "unique" in changes:
            old["is_unique"] = field.is_unique
            row_changes["is_unique"] = bool(changes["unique"])
        if "indexed" in changes:
            old["is_indexed"] = field.is_indexed
            row_changes["is_indexed"] = bool(changes["indexed"])
        if "embed" in changes:
            old["embed"] = field.embed
            row_changes["embed"] = bool(changes["embed"])
        if "default" in changes:
            old["default_value"] = field.default_value
            default = changes["default"]
            if default is not None:
                default = validate_value(new_type, new_config, field.key, default)
            row_changes["default_value"] = default

        if not row_changes:
            return field
        self._schema.update_field_row(
            conn,
            field.id,
            {**row_changes, "updated_at": ts, "updated_by": actor.principal_id},
        )
        updated = self._schema.get_field_by_id(conn, field.id)
        assert updated is not None
        self._ensure_field_indexes(conn, object_type)
        if "embed" in row_changes and self._search_index is not None:
            # Honored in both directions: off purges the field's keyword and embedding
            # rows synchronously, on indexes every live record of the type. An
            # administrator turning it off and being silently ignored is the failure
            # this branch exists to prevent (docs/DATA_MODEL.md section 10).
            self._search_index.sync_field_eligibility(conn, object_type, updated, None)
        audit_new = {k: v for k, v in row_changes.items()}
        self._audit.append(
            conn,
            [
                make_event(
                    actor,
                    ts,
                    entity_type="field",
                    entity_id=field.id,
                    action="update",
                    object_type_id=object_type.id,
                    field_key=field.key,
                    old_value=old,
                    new_value=audit_new,
                )
            ],
        )
        return updated

    def _detect_destructive(
        self,
        conn: Connection,
        object_type: ObjectType,
        field: FieldDef,
        changes: dict[str, Any],
    ) -> tuple[str, dict[str, Any]] | None:
        """Classify a field change set (FR-S6). Returns (change_type, payload extras)
        for the destructive component, or None when everything is additive."""
        detected: list[tuple[str, dict[str, Any]]] = []
        if "type" in changes and changes["type"] != field.type:
            to_type = changes["type"]
            if to_type in ("relation", "attachment"):
                raise ValidationFailedError(
                    f"Changing a field to {to_type!r} is not supported; delete the field "
                    "and create a new one (docs/DATA_MODEL.md section 4).",
                    field.key,
                )
            detected.append(
                (
                    "change_field_type",
                    {"to_type": to_type, "to_config": changes.get("config", {})},
                )
            )
        if (
            "config" in changes
            and "type" not in changes
            and field.type in ("single_select", "multi_select")
        ):
            new_values = {str(o.get("value")) for o in (changes["config"].get("options") or [])}
            removed = [v for v in option_values(field.config) if v not in new_values]
            if removed:
                in_use = self._values_in_use(conn, object_type, field, removed)
                if in_use:
                    detected.append(("remove_enum_option", {"remove_values": sorted(in_use)}))
        if changes.get("required") is True and not field.is_required:
            missing = self._records_missing_value(conn, object_type, field)
            if missing:
                detected.append(("tighten_constraint", {"constraint": "required"}))
        if changes.get("unique") is True and not field.is_unique:
            duplicates = self._duplicate_values(conn, object_type, field)
            if duplicates:
                detected.append(("tighten_constraint", {"constraint": "unique"}))
        if not detected:
            return None
        if len(detected) > 1:
            raise ValidationFailedError(
                "This change combines multiple destructive components "
                f"({', '.join(kind for kind, _ in detected)}). Make one destructive "
                "change per call so each proposal is reviewable on its own.",
                field.key,
            )
        return detected[0]

    def _create_proposal(
        self,
        conn: Connection,
        actor: ActorContext,
        ts: str,
        change_type: str,
        object_type: ObjectType,
        field: FieldDef | None,
        payload: dict[str, Any],
        reason: str | None,
    ) -> Proposal:
        impact = self._compute_impact(conn, change_type, object_type, field, payload)
        proposal = Proposal(
            id=f"prop_{uuid.uuid4().hex[:8]}",
            status="pending",
            change_type=change_type,
            target_type_id=object_type.id,
            target_field_id=field.id if field is not None else None,
            payload=payload,
            impact=impact,
            snapshot_ref=None,
            reason=reason,
            proposed_at=ts,
            proposed_by=actor.principal_id,
            proposed_agent=actor.agent_label_id,
            decided_at=None,
            decided_by=None,
            decision_note=None,
        )
        self._schema.insert_proposal(conn, proposal)
        self._audit.append(
            conn,
            [
                make_event(
                    actor,
                    ts,
                    entity_type="schema_proposal",
                    entity_id=proposal.id,
                    action="create",
                    object_type_id=object_type.id,
                    field_key=field.key if field is not None else None,
                    new_value={"change_type": change_type, "payload": payload, "impact": impact},
                    note=reason,
                )
            ],
        )
        return proposal

    def _proposal_targets(
        self, conn: Connection, proposal: Proposal
    ) -> tuple[ObjectType, FieldDef | None]:
        assert proposal.target_type_id is not None
        object_type = self._schema.get_object_type_by_id(conn, proposal.target_type_id)
        if object_type is None or object_type.is_deleted:
            raise NotFoundError("object type", str(proposal.target_type_id))
        field: FieldDef | None = None
        if proposal.target_field_id is not None:
            field = self._schema.get_field_by_id(conn, proposal.target_field_id)
            if field is None or field.is_deleted:
                raise NotFoundError("field", str(proposal.target_field_id))
        return object_type, field

    # ----------------------------------------------------- impact and snapshot

    def _field_values(
        self, conn: Connection, object_type: ObjectType, field: FieldDef
    ) -> list[tuple[str, Any]]:
        """(record_key, value) for every live record where the field key is present."""
        rows = self._records.records_for_type(conn, object_type.id)
        return [(r.key, r.data[field.key]) for r in rows if field.key in r.data]

    def _values_in_use(
        self,
        conn: Connection,
        object_type: ObjectType,
        field: FieldDef,
        candidates: list[str],
    ) -> set[str]:
        used: set[str] = set()
        for _, value in self._field_values(conn, object_type, field):
            if field.type == "multi_select" and isinstance(value, list):
                used.update(v for v in candidates if v in value)
            elif value in candidates:
                used.add(str(value))
        return used

    def _records_missing_value(
        self, conn: Connection, object_type: ObjectType, field: FieldDef
    ) -> list[str]:
        rows = self._records.records_for_type(conn, object_type.id)
        return [r.key for r in rows if field.key not in r.data]

    def _duplicate_values(
        self, conn: Connection, object_type: ObjectType, field: FieldDef
    ) -> dict[str, list[str]]:
        by_value: dict[str, list[str]] = {}
        for record_key, value in self._field_values(conn, object_type, field):
            by_value.setdefault(json.dumps(value, sort_keys=True), []).append(record_key)
        return {v: keys for v, keys in by_value.items() if len(keys) > 1}

    def _compute_impact(
        self,
        conn: Connection,
        change_type: str,
        object_type: ObjectType,
        field: FieldDef | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """The blast-radius document (FR-S8), computed at proposal time and
        recomputed at approval time."""
        if change_type == "delete_object_type":
            rows = self._records.records_for_type(conn, object_type.id)
            return {
                "change_type": change_type,
                "affected_records": len(rows),
                "sample_values": [r.key for r in rows[:_IMPACT_SAMPLE_SIZE]],
            }
        assert field is not None
        values = self._field_values(conn, object_type, field)
        non_empty = [(k, v) for k, v in values if v not in (None, "", [])]
        samples: list[Any] = []
        for _, value in non_empty:
            if value not in samples:
                samples.append(value)
            if len(samples) >= _IMPACT_SAMPLE_SIZE:
                break
        impact: dict[str, Any] = {
            "change_type": change_type,
            "affected_records": len(values),
            "non_empty_values": len(non_empty),
            "sample_values": samples,
        }
        if change_type == "change_field_type":
            to_type = str(payload.get("to_type"))
            to_config = dict(payload.get("to_config") or {})
            failures = [
                {"record_key": record_key, "value": value, "reason": reason}
                for record_key, value in values
                for ok, _, reason in [coerce_value(field.type, to_type, value, to_config)]
                if not ok
            ]
            impact["coercion_failures"] = failures
        elif change_type == "remove_enum_option":
            removed = [str(v) for v in payload.get("remove_values", [])]
            affected = [
                record_key
                for record_key, value in values
                if (
                    any(v in value for v in removed)
                    if isinstance(value, list)
                    else value in removed
                )
            ]
            impact["in_use"] = {
                "values": removed,
                "count": len(affected),
                "sample_record_keys": affected[:_IMPACT_SAMPLE_SIZE],
            }
        elif change_type == "tighten_constraint":
            constraint = str(payload.get("constraint"))
            if constraint == "required":
                missing = self._records_missing_value(conn, object_type, field)
                impact["violations"] = {
                    "constraint": "required",
                    "count": len(missing),
                    "sample_record_keys": missing[:_IMPACT_SAMPLE_SIZE],
                }
            elif constraint == "unique":
                duplicates = self._duplicate_values(conn, object_type, field)
                impact["violations"] = {
                    "constraint": "unique",
                    "count": sum(len(keys) for keys in duplicates.values()),
                    "duplicates": {
                        v: keys for v, keys in sorted(duplicates.items())[:_IMPACT_SAMPLE_SIZE]
                    },
                }
            else:
                raise ValidationFailedError(
                    f"tighten_constraint payload.constraint must be 'required' or "
                    f"'unique', got {constraint!r} (FR-S10)."
                )
        return impact

    def _build_snapshot(
        self,
        conn: Connection,
        proposal: Proposal,
        object_type: ObjectType,
        field: FieldDef | None,
    ) -> dict[str, Any]:
        """Everything needed to reverse the change (FR-S7), stored as one audit row."""
        snapshot: dict[str, Any] = {
            "proposal_id": proposal.id,
            "change_type": proposal.change_type,
            "object_type_key": object_type.key,
        }
        if proposal.change_type == "delete_object_type":
            snapshot["fields"] = [
                {"key": f.key, "type": f.type, "config": f.config, "description": f.description}
                for f in self._schema.list_fields(conn, object_type.id)
            ]
            snapshot["records"] = {
                r.key: r.data for r in self._records.records_for_type(conn, object_type.id)
            }
        else:
            assert field is not None
            snapshot["field_key"] = field.key
            snapshot["field_def"] = {
                "key": field.key,
                "name": field.name,
                "type": field.type,
                "description": field.description,
                "config": field.config,
                "required": field.is_required,
                "unique": field.is_unique,
                "indexed": field.is_indexed,
                "embed": field.embed,
                "default": field.default_value,
            }
            snapshot["records"] = {
                record_key: value
                for record_key, value in self._field_values(conn, object_type, field)
            }
        return snapshot

    def _apply_proposal(
        self,
        conn: Connection,
        actor: ActorContext,
        ts: str,
        proposal: Proposal,
        object_type: ObjectType,
        field: FieldDef | None,
        *,
        null_non_coercible: bool,
        create_missing_options: bool,
    ) -> None:
        change_type = proposal.change_type
        if change_type == "delete_field":
            assert field is not None
            self._schema.update_field_row(
                conn,
                field.id,
                {"is_deleted": True, "updated_at": ts, "updated_by": actor.principal_id},
            )
            self._records.remove_field_key_from_type(conn, object_type.id, field.key)
            if object_type.display_field_key == field.key:
                # The one place a field disappears is the one place the dangling reference
                # is closed. Holding a key rather than an id buys the ordering in
                # `create_object_type` and costs exactly this. The type falls back to the
                # derived rule -- a degradation rather than a break.
                self._schema.update_object_type_row(
                    conn,
                    object_type.id,
                    {
                        "display_field_key": None,
                        "updated_at": ts,
                        "updated_by": actor.principal_id,
                    },
                )
            # The field row is already marked deleted, so the desired set no longer
            # contains it and both reconcilers drop what it had, with no separate
            # ``_drop_all_indexes`` run *before* the row is updated: with a declarative
            # reconciler, "drop this field's indexes" and
            # "make this type's indexes match its schema" are the same operation, and
            # two ways to say it is how the two drift.
            self._ensure_field_indexes(conn, object_type)
            if self._search_index is not None:
                self._search_index.purge_field(conn, object_type.id, field.key)
            return
        if change_type == "delete_object_type":
            for type_field in self._schema.list_fields(conn, object_type.id):
                self._schema.update_field_row(
                    conn,
                    type_field.id,
                    {"is_deleted": True, "updated_at": ts, "updated_by": actor.principal_id},
                )
            self._records.soft_delete_all_for_type(conn, object_type.id, ts, actor.principal_id)
            self._schema.update_object_type_row(
                conn,
                object_type.id,
                {"is_deleted": True, "updated_at": ts, "updated_by": actor.principal_id},
            )
            # Every field is now deleted, so both desired sets are empty and this
            # drops every index the type had.
            self._ensure_field_indexes(conn, object_type)
            if self._search_index is not None:
                # Takes the type's field rows and its records' comment rows with it.
                self._search_index.purge_object_type(conn, object_type.id)
            return
        if change_type == "change_field_type":
            assert field is not None
            self._apply_type_change(
                conn,
                actor,
                ts,
                object_type,
                field,
                proposal.payload,
                null_non_coercible=null_non_coercible,
                create_missing_options=create_missing_options,
            )
            return
        if change_type == "remove_enum_option":
            assert field is not None
            self._apply_enum_removal(conn, actor, ts, object_type, field, proposal.payload)
            return
        if change_type == "tighten_constraint":
            assert field is not None
            self._apply_tighten(conn, actor, ts, object_type, field, proposal.payload)
            return
        raise ValidationFailedError(f"Unknown proposal change_type {change_type!r}.")

    @contextmanager
    def _fan_out_scope(self, now: datetime | None) -> Iterator[None]:
        """Defer any index fan-out a schema change implies until after it commits.

        Wraps the write transaction from *outside*, which is the whole mechanism:
        Python closes nested context managers inner first, so by the time this one
        exits, ``self._db.write()`` has already committed and released SQLite's single
        writer lock. A batch can then take that lock for half a second at a time
        instead of the schema change holding it for twenty-two.

        Wrapping rather than restructuring also means the two public methods keep their
        existing early returns: a ``return`` from inside the ``with`` still unwinds
        through here, so the fan-out runs whichever branch the method took.

        ``SearchIndexService.sync_field_eligibility`` carries the measurement that made
        this necessary.
        """
        with collect_fan_outs() as pending:
            yield
        if self._search_index is None:
            return
        for object_type, field in pending:
            self._search_index.fan_out_field(object_type, field, now=now)

    # ------------------------------------------------------- sort composites

    @staticmethod
    def desired_sort_indexes(fields: list[FieldDef]) -> list[tuple[str, str]]:
        """The sort-composite index set one object type's schema implies.

        A pure function of the field list, which is what makes the reconciler below
        declarative rather than incremental: the answer never depends on what order
        fields were added or on which code path is asking.

        The pairing rule is ``(low-cardinality indexed field, other indexed sortable
        field)``. ``sqlexpr.sort_index_ddl`` carries the measurement behind it and
        ``fieldtypes.LOW_CARDINALITY_TYPES`` carries why that side is bounded to select
        and boolean fields rather than to every indexed field.

        Deleted fields and unique fields are excluded: a unique field's own index
        already makes any filter on it a single-row lookup, so a composite over it
        would earn nothing.

        Returns the ``(filter_key, sort_key)`` pairs themselves, not a dict keyed by
        ``sort_index_name("t", ...)``: a placeholder type key standing in for the real
        one purely to dedupe would fail, because ``sort_index_name`` takes the object type
        **id**, which ``validate_uuid`` would reject. The pairs are the thing
        the caller wants and the thing that is unique, so they are what it returns.
        """
        live = [f for f in fields if not f.is_deleted and f.is_indexed and not f.is_unique]
        filters = [f for f in live if f.type in LOW_CARDINALITY_TYPES]
        sorts = [f for f in live if f.type in SORT_COMPOSITE_TYPES]
        return sorted({(a.key, b.key) for a in filters for b in sorts if a.key != b.key})

    def reconcile_sort_indexes(
        self, conn: Connection, object_type: ObjectType, fields: list[FieldDef]
    ) -> tuple[list[str], list[str]]:
        """Create missing sort composites and drop stale ones. Returns (created, dropped).

        Declarative on purpose. The per-field indexes above are maintained
        incrementally, one flag change at a time, and that works because each one
        belongs to exactly one field. A composite belongs to a *pair*, so a single
        field change can add or remove several of them, and an incremental
        implementation would have to enumerate those consequences correctly at four
        different call sites. Computing the whole desired set and diffing it against
        what is on disk cannot drift, and it backfills a deployment that upgraded into
        this version with no migration to write.
        """
        desired = {
            sort_index_name(object_type.id, a, b): (a, b)
            for a, b in self.desired_sort_indexes(fields)
        }
        existing = set(self._schema.list_index_names(conn, sort_index_prefix(object_type.id)))
        created = []
        for name, (a, b) in sorted(desired.items()):
            if name not in existing:
                self._schema.execute_index_ddl(conn, sort_index_ddl(object_type.id, a, b))
                created.append(name)
        dropped = []
        for name in sorted(existing - set(desired)):
            self._schema.execute_index_ddl(conn, drop_index_by_name_ddl(name))
            dropped.append(name)
        return created, dropped

    def reconcile_all_sort_indexes(self) -> dict[str, tuple[list[str], list[str]]]:
        """Reconcile every live object type. Called once at startup (``app.py``).

        This is what makes the feature arrive without a migration: an existing
        deployment gets its composites built on the first start after the upgrade, and
        every start afterwards is a no-op that costs one ``sqlite_master`` read per
        object type.
        """
        result: dict[str, tuple[list[str], list[str]]] = {}
        with self._db.write() as conn:
            for object_type in self._schema.list_object_types(conn):
                if object_type.is_deleted:
                    continue
                fields = self._schema.list_fields(conn, object_type.id)
                created, dropped = self.reconcile_sort_indexes(conn, object_type, fields)
                if created or dropped:
                    result[object_type.key] = (created, dropped)
        return result

    # ---------------------------------------------- retiring legacy index names

    def retire_legacy_index_names(self) -> tuple[list[str], list[str]]:
        """Drop every index carrying a legacy name, then rebuild the desired set.

        Returns ``(dropped, created)``. Called once from ``app.py``'s lifespan, after
        the migrations, and idempotent: a second start finds no legacy name and no
        missing index, and reports ``([], [])``.

        **Why this is not a numbered migration.** It needs the schema repository and the two
        reconcilers' desired-set logic, and ``migrations.py`` takes static SQL strings. That
        is the same reason ``reconcile_sort_indexes`` is not one, and SQLite cannot rename
        an index in any case, so an upgrade has to drop and recreate whatever it is.

        **Why one transaction.** The lifespan's startup hooks are deliberately
        non-fatal so a deployment that cannot build an index still serves, more slowly,
        rather than refusing to start. A drop that committed without its matching create
        would leave a serving deployment with *no* per-type indexes at all, which is the
        one failure that trade is not worth. Drops and creates therefore share one write
        transaction and roll back together.

        **The one place an index name is parsed** (DD-12). Nothing else in ``src/``
        recovers structure from an index name; names are generated, listed by prefix and
        diffed, never read. This reads exactly one thing -- whether the segment after
        the prefix is a 32-hex object type id -- to tell a name this version generates
        from one it does not, and it is scoped to names this version no longer
        generates. The prefixes are matched literally (``list_index_names`` escapes
        them), so ``ix_records_type_live`` and ``ix_records_updated`` are out of reach.
        """
        dropped: list[str] = []
        created: list[str] = []
        with self._db.write() as conn:
            for prefix in LEGACY_INDEX_PREFIXES:
                for name in self._schema.list_index_names(conn, prefix):
                    if _names_an_object_type_id(name, prefix):
                        continue
                    self._schema.execute_index_ddl(conn, drop_index_by_name_ddl(name))
                    dropped.append(name)
            for object_type in self._schema.list_object_types(conn):
                if object_type.is_deleted:
                    continue
                fields = self._schema.list_fields(conn, object_type.id)
                for made, removed in (
                    self._reconcile_field_indexes(conn, object_type, fields),
                    self.reconcile_sort_indexes(conn, object_type, fields),
                ):
                    created.extend(made)
                    dropped.extend(removed)
        return sorted(dropped), sorted(created)

    # ------------------------------------- reporting reserved-key collisions

    def reserved_key_collisions(self) -> list[tuple[str, str]]:
        """Every live field whose key is a reserved pseudo-field name.

        Returns ``(object_type_key, field_key)`` pairs, sorted. A deployment created
        before the names were reserved may hold such a field, created in good faith;
        nothing rewrites it (DD-20), so this reports it and the lifespan logs it.

        Shaped like ``retire_legacy_index_names``: it takes no connection and
        opens its own, because passing one down from the lifespan would put connection
        ownership above the service layer. It walks ``list_object_types`` then
        ``list_fields`` per type -- an N+1 bounded by the number of object types, once
        at startup, which is the price of keeping the raw SQL inside the repository
        layer (DD-2).

        No access check: it takes no actor and reaches no record. It reads schema
        metadata for an operator-facing log line, and it is exempted by name in
        ``tests/test_access_completeness.py``.
        """
        collisions: list[tuple[str, str]] = []
        with self._db.read() as conn:
            for object_type in self._schema.list_object_types(conn):
                if object_type.is_deleted:
                    continue
                for field in self._schema.list_fields(conn, object_type.id):
                    if not field.is_deleted and field.key in PSEUDO_FIELDS:
                        collisions.append((object_type.key, field.key))
        return sorted(collisions)

    def _apply_type_change(
        self,
        conn: Connection,
        actor: ActorContext,
        ts: str,
        object_type: ObjectType,
        field: FieldDef,
        payload: dict[str, Any],
        *,
        null_non_coercible: bool,
        create_missing_options: bool,
    ) -> None:
        to_type = str(payload["to_type"])
        to_config = dict(payload.get("to_config") or {})
        values = self._field_values(conn, object_type, field)

        if create_missing_options and to_type in ("single_select", "multi_select"):
            existing = set(option_values(to_config))
            options = list(to_config.get("options") or [])
            for _, value in values:
                for candidate in value if isinstance(value, list) else [value]:
                    text = str(candidate)
                    if text not in existing:
                        existing.add(text)
                        options.append(
                            {
                                "value": text,
                                "label": text,
                                "description": (
                                    "Created from existing data during the type change "
                                    f"approved for proposal-driven field {field.key!r}."
                                ),
                            }
                        )
            to_config["options"] = options
        to_config = validate_config(to_type, to_config, field.key)

        failures: list[str] = []
        coerced: dict[str, Any] = {}
        for record_key, value in values:
            ok, new_value, _reason = coerce_value(field.type, to_type, value, to_config)
            if ok:
                coerced[record_key] = new_value
            else:
                failures.append(record_key)
        if failures and not null_non_coercible:
            raise ValidationFailedError(
                f"{len(failures)} value(s) cannot be converted to {to_type!r} "
                f"(e.g. records {failures[:3]}). Field type changes apply only where "
                "every value coerces cleanly, unless the approver elects "
                "null_non_coercible=True to null the failures (FR-S9).",
                field.key,
            )

        rows = self._records.records_for_type(conn, object_type.id)
        for row in rows:
            if field.key not in row.data:
                continue
            new_data = dict(row.data)
            if row.key in coerced:
                new_data[field.key] = coerced[row.key]
            else:
                del new_data[field.key]  # approver elected to null non-coercible values
            if new_data != row.data:
                self._records.update_record_row(conn, row.id, {"data": new_data})

        new_default = field.default_value
        if new_default is not None:
            ok, converted, _ = coerce_value(field.type, to_type, new_default, to_config)
            new_default = converted if ok else None

        indexed = field.is_indexed or to_type in AUTO_INDEXED_TYPES
        self._schema.update_field_row(
            conn,
            field.id,
            {
                "type": to_type,
                "config": to_config,
                "default_value": new_default,
                "is_indexed": indexed,
                "updated_at": ts,
                "updated_by": actor.principal_id,
            },
        )
        updated = self._schema.get_field_by_id(conn, field.id)
        assert updated is not None
        self._ensure_field_indexes(conn, object_type)
        if self._search_index is not None:
            # A type change can cross the eligibility line either way: text to number
            # purges, number to long_text indexes every live record's new value.
            self._search_index.sync_field_eligibility(conn, object_type, updated, None)

    def _apply_enum_removal(
        self,
        conn: Connection,
        actor: ActorContext,
        ts: str,
        object_type: ObjectType,
        field: FieldDef,
        payload: dict[str, Any],
    ) -> None:
        removed = {str(v) for v in payload.get("remove_values", [])}
        changes = payload.get("changes") or {}
        new_config = changes.get("config") or {
            "options": [o for o in field.config.get("options", []) if o["value"] not in removed]
        }
        new_config = validate_config(field.type, dict(new_config), field.key)
        rows = self._records.records_for_type(conn, object_type.id)
        for row in rows:
            if field.key not in row.data:
                continue
            value = row.data[field.key]
            new_data = dict(row.data)
            if isinstance(value, list):
                trimmed = [v for v in value if v not in removed]
                if trimmed:
                    new_data[field.key] = trimmed
                else:
                    del new_data[field.key]
            elif value in removed:
                del new_data[field.key]
            if new_data != row.data:
                self._records.update_record_row(conn, row.id, {"data": new_data})
        self._schema.update_field_row(
            conn,
            field.id,
            {"config": new_config, "updated_at": ts, "updated_by": actor.principal_id},
        )

    def _apply_tighten(
        self,
        conn: Connection,
        actor: ActorContext,
        ts: str,
        object_type: ObjectType,
        field: FieldDef,
        payload: dict[str, Any],
    ) -> None:
        constraint = str(payload.get("constraint"))
        if constraint == "required":
            self._schema.update_field_row(
                conn,
                field.id,
                {"is_required": True, "updated_at": ts, "updated_by": actor.principal_id},
            )
            return
        if constraint == "unique":
            duplicates = self._duplicate_values(conn, object_type, field)
            if duplicates:
                raise ValidationFailedError(
                    f"Field {field.key!r} still has duplicate values "
                    f"({len(duplicates)} value(s) shared by multiple records). Resolve "
                    "the duplicates, then approve; the recomputed impact will reflect "
                    "the change.",
                    field.key,
                )
            self._schema.update_field_row(
                conn,
                field.id,
                {"is_unique": True, "updated_at": ts, "updated_by": actor.principal_id},
            )
            updated = self._schema.get_field_by_id(conn, field.id)
            assert updated is not None
            self._ensure_field_indexes(conn, object_type)
            return
        raise ValidationFailedError(
            f"tighten_constraint payload.constraint must be 'required' or 'unique', "
            f"got {constraint!r}."
        )


def _impact_json(impact: dict[str, Any]) -> str:
    return json.dumps(impact, sort_keys=True)


def _map_type_integrity_error(
    exc: IntegrityError, key: str, key_prefix: str
) -> ValidationFailedError:
    message = str(exc.orig)
    if "object_types.key_prefix" in message:
        return ValidationFailedError(
            f"key_prefix {key_prefix!r} is already used by another object type."
        )
    if "object_types.key" in message:
        return ValidationFailedError(f"Object type key {key!r} already exists.")
    return ValidationFailedError(f"Object type {key!r} violates a uniqueness constraint.")
