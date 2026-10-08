"""Response envelopes shared by the REST routes and the MCP tools (DD-3, FR-A1).

Both adapters shape service results through these helpers, so a record, comment,
audit event, or describe document has exactly one JSON shape and the two surfaces
cannot drift. Nothing here decides anything: helpers read service results and
compose them. Business logic stays in ``services/``; the error envelope lives in
``errors.error_envelope`` for the same reason.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from glosswork.actor import ActorContext
from glosswork.fieldtypes import PSEUDO_FIELD_DESCRIPTIONS, PSEUDO_FIELDS, operators_for
from glosswork.repositories.models import (
    AccessTokenRow,
    AgentLabelRow,
    AttachmentRow,
    FieldDef,
    InviteRow,
    ObjectType,
    PrincipalRow,
    Proposal,
    RecordRow,
)
from glosswork.serializers import (
    audit_event_doc,
    comment_doc,
    field_doc,
    grant_doc,
    record_doc,
)
from glosswork.services import ServiceBundle
from glosswork.services.access import GrantListing
from glosswork.services.audit import AuditSearchResult
from glosswork.services.base import display_field
from glosswork.services.changes import ChangesResult
from glosswork.services.comments import CommentPage
from glosswork.services.invites import invite_expires_at, outcome_message
from glosswork.services.records import BulkUpdateResult, HistoryPage, QueryResult
from glosswork.services.relay import RelayResult
from glosswork.services.schema import FieldUpdateResult, ProposalPage
from glosswork.services.search import SearchResult
from glosswork.services.search_index import IndexStatus
from glosswork.services.workspace import WorkspaceDocument

RECORD_INCLUDES = ("comments", "links", "history", "attachments")

TRUNCATION_GUIDANCE = (
    "The result was clipped to protect your context window: the compact default "
    "projection omitted some field values and/or more records match than were "
    "returned. Narrow the filter, request only the fields you need with `fields` "
    '(or `fields: "*"` for everything), or follow `next_cursor` for the next page.'
)

#: What an agent is told when its schema change becomes a proposal instead of applying.
#:
#: **It names a URL, not a screen.** A string that named a screen went false when proposals
#: moved to ``/inbox`` -- and nothing in the suite noticed, because no test pinned it. It is
#: the only place the product tells an agent where a human should go, so an agent relaying
#: it to a person is relaying this sentence verbatim; ``tests/test_approval_message.py`` now
#: pins it to the route it names.
APPROVAL_MESSAGE = (
    "This change requires human approval. An administrator must approve proposal "
    "{proposal_id} in the Glosswork UI, under Inbox > /inbox/{proposal_id}. The "
    "affected data will be snapshotted before the change is applied. Call "
    "list_schema_proposals to check its status; nothing has been applied yet."
)


# ----------------------------------------------------------------------- rows


def attachment_doc(row: AttachmentRow, download_url: str) -> dict[str, Any]:
    """One attachment, on every surface that returns one (DD-29).

    Both REST handlers that return an envelope, the ``get_attachment`` tool, and the
    ``attachments`` block of :func:`record_with_includes_doc` call this. Three
    hand-written copies of it drifted, and one of them projected four keys, so
    ``get_record include=attachments`` and ``GET /api/v1/attachments/{id}`` disagreed
    about the same row. That is the same drift the grant documents had.

    ``download_url`` is passed in rather than derived, because where the bytes live is
    a deployment fact only ``AttachmentService`` knows (``GW_BASE_URL``), and this
    module decides nothing.
    """
    return {
        "id": row.id,
        "sha256": row.sha256,
        "filename": row.filename,
        "content_type": row.content_type,
        "byte_size": row.byte_size,
        "uploaded_at": row.uploaded_at,
        "uploaded_by": row.uploaded_by,
        "download_url": download_url,
    }


#
# ``record_doc``, ``comment_doc``, ``saved_view_doc``, ``audit_event_doc``,
# ``agent_label_doc``, and ``field_doc`` live in ``glosswork.serializers`` so
# ``services/export.py`` (FR-E5) can reuse them without the service layer importing this
# module, which imports ``ServiceBundle`` back from ``glosswork.services`` -- see
# ``serializers.py``'s module docstring. Four of the six (``record_doc``, ``comment_doc``,
# ``audit_event_doc``, ``field_doc``) are still used below and re-imported above.
# ``saved_view_doc`` and ``agent_label_doc`` are not used anywhere else in this module, so
# ``routes/saved_views.py`` and ``routes/agent_labels.py`` import them from
# ``glosswork.serializers`` directly rather than through this one.


def proposal_doc(proposal: Proposal, target: dict[str, Any] | None = None) -> dict[str, Any]:
    """One proposal, on every surface and from every route that returns one.

    ``target`` is a projection (DD-25): what this proposal is *about*, in words. Without
    it, a proposal is four UUIDs where a sentence needs four nouns, and two of the four
    -- ``target_type_id`` and ``target_field_id`` -- are resolvable by nothing a client can
    call. The stored ids stay exactly where they were; this is additive.

    It is a parameter rather than something computed here because envelopes take rows and place
    them (DD-3): the service reads the type and the field, in one batched pass per page, and
    hands the answer down. ``None`` is for the paths that have not resolved one -- no caller in
    ``src/`` passes it, and ``tests/test_proposal_target_projection.py`` asserts the key on all
    five responses so a new route cannot quietly drop it.
    """
    doc = dataclasses.asdict(proposal)
    doc["target"] = target
    return doc


def proposal_list_doc(page: ProposalPage) -> dict[str, Any]:
    """The proposal list, for REST and MCP alike.

    This function exists because ``{"proposals": [...]}`` written as a literal twice -- once in
    ``routes/schema.py`` and once in ``mcp_server/tools_admin.py`` -- with only ``proposal_doc``
    shared between them drifts. The grant documents had the same drift, answered with
    ``object_type_grants_doc``; this is that answer applied a second time,
    and it is what makes "REST and MCP return the same keys" an assertion rather than a habit.

    ``total_count`` is the total under the caller's own access filter, not the page's length:
    the sidebar's Inbox badge reads it, and a badge fed a page length silently caps at the
    page size.
    """
    return {
        "proposals": [proposal_doc(p, page.targets.get(p.id)) for p in page.proposals],
        "next_cursor": page.next_cursor,
        "total_count": page.total_count,
        # DD-25's two maps, siblings of ``proposals`` rather than keys inside one, so a client
        # that ignores them behaves exactly as it did before and the stored value stays a bare id.
        "principals": page.principals,
        "agent_labels": page.agent_labels,
    }


def principal_doc(principal: PrincipalRow) -> dict[str, Any]:
    """One ``principals`` row (docs/DATA_MODEL.md section 2). ``password_hash``
    is deliberately absent: no serializer, envelope, or OpenAPI response schema may
    ever expose it."""
    return {
        "id": principal.id,
        "type": principal.type,
        "display_name": principal.display_name,
        "email": principal.email,
        "role": principal.role,
        "auth_provider": principal.auth_provider,
        "external_id": principal.external_id,
        "is_active": principal.is_active,
        "description": principal.description,
        "created_at": principal.created_at,
        "created_by": principal.created_by,
    }


def agent_label_directory_doc(label: AgentLabelRow) -> dict[str, Any]:
    """One agent-label directory entry. **Exactly three keys.**

    The picker on ``/activity`` needs a name to show and an id to filter by, and nothing
    else. Withheld, all six of them FR-I7 telemetry and all six on
    :func:`serializers.agent_label_doc`: ``principal_id`` (whose label it is),
    ``call_count``, ``first_seen_at``, ``last_seen_at``, ``verified`` and ``description``.
    ``GET /admin/agent-labels`` is what serves those, and it keeps its ``admin`` role.

    These three are the same two facts ``AgentLabelRef`` already puts on every record
    document and every audit row, plus the id they are keyed by -- so the projection
    discloses nothing new **given** that the route's rows are already scoped to what the
    caller's own audit search would show (``AgentLabelService.search_labels``). The
    scoping is what makes that sentence true; the projection alone would not.

    ``tests/test_agent_label_directory.py`` asserts the key set by equality and each
    withheld key by name, so a field added to ``agent_labels`` cannot leak here through a
    spread.
    """
    return {
        "id": label.id,
        "label": label.label,
        "display_name": label.display_name,
    }


def invite_doc(invite: InviteRow) -> dict[str, Any]:
    """One ``invites`` row (change 9). ``expires_at`` is derived, 14 days after
    ``created_at``; ``invited_by`` is a principal id, resolved by the client like any
    other."""
    return {
        "id": invite.id,
        "email": invite.email,
        "display_name": invite.display_name,
        "role": invite.role,
        "invited_by": invite.invited_by,
        "created_at": invite.created_at,
        "expires_at": invite_expires_at(invite),
        "accepted_at": invite.accepted_at,
        "revoked_at": invite.revoked_at,
    }


def invite_email_doc(result: RelayResult) -> dict[str, str]:
    """What became of an invite's email: the relay's outcome, and the sentence an
    administrator reads (docs/DEPLOYMENT.md section 5a)."""
    return {"outcome": result.outcome, "message": outcome_message(result)}


def principal_directory_doc(principal: PrincipalRow) -> dict[str, Any]:
    """One directory entry (DD-25). **Exactly five keys.**

    Narrower than :func:`principal_doc` on purpose, and the narrowing is what lets the
    directory route drop ``require_role("admin")``: ``role``, ``auth_provider``,
    ``external_id``, ``description``, ``created_by``, ``created_at`` and
    ``password_hash`` are absent, so an authenticated member learns who exists and how to
    address them and nothing about how the deployment is administered.

    The permissions panel (DD-11) needs none of those: it reads a principal's id and
    display name and nothing else, so the count of withheld keys it needs is zero. It takes
    this projection
    for its picker and a ``principals`` sidecar for its row labels, and is gated on the
    ``admin`` **level on the object type** alone.

    ``tests/test_principal_directory.py`` asserts the key set by equality and each
    withheld key by name, so a future field added to ``principals`` cannot leak here by
    being added to a spread.
    """
    return {
        "id": principal.id,
        "display_name": principal.display_name,
        "email": principal.email,
        "type": principal.type,
        "is_active": principal.is_active,
    }


def access_token_doc(row: AccessTokenRow) -> dict[str, Any]:
    """One ``access_tokens`` row (FR-I4). ``token_hash`` and the plaintext are
    deliberately absent: this is the shape every read path returns, and the plaintext
    is carried only by :class:`~glosswork.services.tokens.MintedToken`, never by
    this row."""
    return {
        "id": row.id,
        "principal_id": row.principal_id,
        "name": row.name,
        "token_prefix": row.token_prefix,
        "scope": row.scope,
        "expires_at": row.expires_at,
        "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
        "created_at": row.created_at,
        "created_by": row.created_by,
        # Published so the Setup page reads which tool a token belongs to rather
        # than re-deriving it, which is the question a person asks right before revoking
        # one. Descriptive metadata: it grants nothing.
        "agent_label": row.agent_label,
    }


def me_doc(principal: PrincipalRow, actor: ActorContext) -> dict[str, Any]:
    """``GET /api/v1/me``: the calling principal plus the credential's
    ``scope`` and ``auth_method``, which come from ``actor`` rather than the principal
    row itself — the same principal can be reached through credentials of different
    scope and auth method.

    ``auth_provider`` is what lets the ``Password`` card on ``/setup``
    decide whether to render a form or a sentence: it is already served to an
    administrator on every principal row through ``principal_doc``, so serving a
    caller their own is no new disclosure.
    """
    return {
        "id": principal.id,
        "display_name": principal.display_name,
        "email": principal.email,
        "type": principal.type,
        "role": principal.role,
        "scope": actor.scope,
        "auth_method": actor.auth_method,
        "auth_provider": principal.auth_provider,
    }


def workspace_doc(workspace: WorkspaceDocument) -> dict[str, Any]:
    """``GET /api/v1/workspace`` (DD-28). **Exactly five keys**: two counts with no names
    in them, the operator-set name they sit under (``null`` when ``GW_WORKSPACE_NAME`` is
    unset), the deployment's MCP URL (``null`` when ``GW_BASE_URL`` is unset), and the
    trial (``null`` when ``GW_TRIAL_ENDS_AT`` is unset).

    The fourth key has a reader rather than being on spec: the first-run screen has to print
    the URL an agent should connect to, and the browser cannot compose it correctly because
    the ``/mcp`` transport's own allowlists are built from ``GW_BASE_URL`` (DD-15).
    ``WorkspaceService.mcp_url`` carries the argument. The fifth has one too: the trial
    banner, which counts down to ``ends_at`` and links to ``subscribe_url``. The subscribe
    address appears only inside a non-null ``trial``. The key set stays pinned by equality
    in ``tests/test_api_workspace.py``: a sixth key needs a reader too.
    """
    trial = workspace.trial
    return {
        "name": workspace.name,
        "people": workspace.people,
        "agents": workspace.agents,
        "mcp_url": workspace.mcp_url,
        "trial": None
        if trial is None
        else {"ends_at": trial.ends_at, "subscribe_url": trial.subscribe_url},
    }


# --------------------------------------------------------------------- schema


def system_field_doc(key: str) -> dict[str, Any]:
    _column, field_type = PSEUDO_FIELDS[key]
    return {
        "key": key,
        "type": field_type,
        "description": PSEUDO_FIELD_DESCRIPTIONS[key],
        "operators": operators_for(field_type),
    }


def _record_count(services: ServiceBundle, actor: ActorContext, key: str) -> int:
    return services.records.query_records(actor, key, limit=1).total_count


def object_type_summary(
    services: ServiceBundle, actor: ActorContext, object_type: ObjectType, fields: list[FieldDef]
) -> dict[str, Any]:
    return {
        "key": object_type.key,
        "name": object_type.name,
        "description": object_type.description,
        "key_prefix": object_type.key_prefix,
        "record_count": _record_count(services, actor, object_type.key),
        "field_count": len(fields),
        "your_access": services.schema.your_access(actor, object_type.key),
    }


def list_object_types_doc(services: ServiceBundle, actor: ActorContext) -> list[dict[str, Any]]:
    result = []
    for object_type in services.schema.list_object_types(actor):
        _, fields = services.schema.get_object_type(actor, object_type.key)
        result.append(object_type_summary(services, actor, object_type, fields))
    return result


def describe_object_type_doc(
    services: ServiceBundle, actor: ActorContext, key: str, include_samples: bool = False
) -> dict[str, Any]:
    object_type, fields = services.schema.get_object_type(actor, key)
    samples = services.records.sample_values(actor, key) if include_samples else None
    effective = display_field({f.key: f for f in fields}, object_type.display_field_key)
    return {
        "key": object_type.key,
        "name": object_type.name,
        "name_plural": object_type.name_plural,
        "description": object_type.description,
        "key_prefix": object_type.key_prefix,
        "record_count": _record_count(services, actor, object_type.key),
        "field_count": len(fields),
        # Additive: no field is removed or renamed. This is what makes the MCP surface
        # honest under the rule that the tool catalog is a pure function of credential
        # scope -- a `write`-scoped agent still *sees*
        # `create_record`, and learns from here what it can actually do.
        "your_access": services.schema.your_access(actor, key),
        # DD-23. Two keys because one value cannot serve both readers: the
        # stored column is what the schema editor's select must show (so saving an unrelated
        # setting cannot silently convert an implicit null into a pin nobody chose), and the
        # resolved answer is what an agent needs so it does not re-derive the rule. Merging
        # them would make `null` unobservable on the client; re-deriving the second in the
        # frontend is what the single rule forbids.
        "display_field_key": object_type.display_field_key,
        "effective_display_field_key": effective.key if effective is not None else None,
        "fields": [
            field_doc(f, samples.get(f.key, []) if samples is not None else None) for f in fields
        ],
        "system_fields": [system_field_doc(k) for k in PSEUDO_FIELDS],
    }


def update_field_doc(result: FieldUpdateResult) -> dict[str, Any]:
    if result.applied:
        assert result.field is not None
        return {"status": "applied", "field": field_doc(result.field), "message": result.message}
    assert result.proposal is not None
    return {
        "status": "pending_human_approval",
        "proposal_id": result.proposal.id,
        "change_type": result.proposal.change_type,
        "impact": result.proposal.impact,
        "message": result.message + " " + APPROVAL_MESSAGE.format(proposal_id=result.proposal.id),
    }


def proposal_created_doc(proposal: Proposal) -> dict[str, Any]:
    return {
        "status": "pending_human_approval",
        "proposal_id": proposal.id,
        "change_type": proposal.change_type,
        "impact": proposal.impact,
        "message": APPROVAL_MESSAGE.format(proposal_id=proposal.id),
    }


def object_type_grants_doc(listing: GrantListing) -> dict[str, Any]:
    """One object type's grants, on REST and MCP alike (DD-11).

    Four keys. ``principals`` is additive to the first three, and is the records
    sidecar's shape applied a second time -- three keys per
    entry, keyed by principal id -- so a row naming a deactivated colleague, or one the
    directory's 200-row cap would miss, is still a name on the screen.

    The map arrives already projected: the service builds it, this only places it, which
    is why there is exactly one projection of a principal sidecar to audit.
    """
    return {
        "object_type": listing.object_type_key,
        "default_level": listing.default_level,
        "grants": [grant_doc(row) for row in listing.grants],
        "principals": listing.principals,
    }


# -------------------------------------------------------------------- records


def query_result_doc(
    services: ServiceBundle, actor: ActorContext, object_type_key: str, result: QueryResult
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "records": result.records,
        "total_count": result.total_count,
        "next_cursor": result.next_cursor,
        "truncated": result.truncated,
        # Names for every principal this page references, as a sibling of
        # ``records`` rather than inside a value. A client that ignores it behaves exactly
        # as it did before, and the stored value is still a bare id.
        "principals": services.records.principal_sidecar(actor, object_type_key, result.records),
        # The same shape one layer over: the ``By`` column needs the label's text, and
        # without it an agent label would reach the frontend only ever as a UUID. Projected
        # rather than passed through whole.
        "agent_labels": services.records.agent_label_sidecar(actor, result.records),
    }
    if result.truncated:
        doc["guidance"] = TRUNCATION_GUIDANCE  # FR-M8
    return doc


def record_response_doc(
    services: ServiceBundle, actor: ActorContext, record: RecordRow
) -> dict[str, Any]:
    """``record_doc`` plus the ``principals`` sidecar: the shape the four write paths
    that return the written record use (``create_record``, ``update_record``,
    ``delete_record``, ``restore_record``), on REST and MCP alike.

    An agent confirms *who* it just assigned in the same turn rather than making a second
    call to find out. ``record_doc`` itself is untouched -- ``ExportService`` and
    ``serializers`` still share it -- and this composes onto it, exactly as
    ``record_with_includes_doc`` composes its includes.
    """
    body = record_doc(record)
    body["principals"] = services.records.principal_sidecar(actor, record.object_type_id, [body])
    body["agent_labels"] = services.records.agent_label_sidecar(actor, [body])
    return body


def bulk_update_doc(result: BulkUpdateResult) -> dict[str, Any]:
    return {
        "affected_count": result.affected_count,
        "sample_keys": result.sample_keys,
        "dry_run": result.dry_run,
    }


def record_with_includes_doc(
    services: ServiceBundle,
    actor: ActorContext,
    ref: str,
    includes: set[str],
    expand_relations: list[str] | None = None,
    expand_fields: list[str] | None = None,
) -> dict[str, Any]:
    """``get_record`` on both surfaces: the record envelope plus any of
    ``comments``, ``links``, ``history``, ``attachments`` (docs/MCP_TOOLS.md 5.1)."""
    record = services.records.get_record(actor, ref)
    body = record_doc(record)
    # One of the six paths. Composed before the includes so a caller
    # reading the response top-down meets the names next to the record they annotate.
    body["principals"] = services.records.principal_sidecar(actor, record.object_type_id, [body])
    body["agent_labels"] = services.records.agent_label_sidecar(actor, [body])

    if "comments" in includes:
        body["comments"] = [comment_doc(c) for c in services.comments.list_comments(actor, ref)]

    if "links" in includes:
        # Entries for a target type the caller cannot read come back as
        # ``{"redacted": true}`` rather than being dropped, so the count a caller sees
        # matches the count a system administrator sees.
        body["links"] = services.records.list_link_summaries(actor, ref)

    if "history" in includes:
        body["history"] = [
            audit_event_doc(e) for e in services.records.get_record_history(actor, ref)
        ]

    if "attachments" in includes:
        _, type_fields = services.schema.get_object_type_by_id(actor, record.object_type_id)
        attachments: dict[str, list[dict[str, Any]]] = {}
        for field in type_fields:
            if field.type != "attachment":
                continue
            resolved = services.attachments.get_many(actor, record.data.get(field.key, []))
            # The same eight-key document ``GET /api/v1/attachments/{id}`` returns, rather
            # than a four-key copy. Nothing is disclosed that is not already disclosed,
            # because the metadata route returns the same keys to any caller who passes
            # the same read rule, and that rule
            # decides whether a row appears, never which of its keys do.
            attachments[field.key] = [
                attachment_doc(a, services.attachments.download_url(a.id)) for a in resolved
            ]
        body["attachments"] = attachments

    if expand_relations:
        body["expand"] = services.records.get_record_expansions(
            actor, ref, expand_relations, expand_fields=expand_fields
        )
    return body


def history_page_doc(page: HistoryPage) -> dict[str, Any]:
    return {"events": [audit_event_doc(e) for e in page.events], "next_cursor": page.next_cursor}


def comments_page_doc(page: CommentPage) -> dict[str, Any]:
    return {"comments": [comment_doc(c) for c in page.comments], "next_cursor": page.next_cursor}


def changes_doc(result: ChangesResult) -> dict[str, Any]:
    return {
        "events": [audit_event_doc(e) for e in result.events],
        "next_cursor": result.next_cursor,
    }


def audit_search_result_doc(result: AuditSearchResult) -> dict[str, Any]:
    return {
        "events": [audit_event_doc(e) for e in result.events],
        "next_cursor": result.next_cursor,
    }


def search_result_doc(result: SearchResult) -> dict[str, Any]:
    """``search`` on both surfaces (docs/MCP_TOOLS.md section 5.1).

    ``index_lag`` is how an agent knows whether very recent writes are searchable
    yet; ``mode_applied`` differs from the requested mode only when semantic search
    is disabled and ``hybrid`` degraded to keyword. The service's ``widened`` flag is
    deliberately not here: it is a test seam, not a contract.
    """
    return {
        "results": [
            {
                "record_key": hit.record_key,
                "record_id": hit.record_id,
                "object_type": hit.object_type,
                "title": hit.title,
                "score": round(hit.score, 6),
                "hit_source": hit.hit_source,
                "snippet": hit.snippet,
                "other_matches": hit.other_matches,
            }
            for hit in result.results
        ],
        "index_lag": {"pending_jobs": result.pending_jobs, "failed_jobs": result.failed_jobs},
        "mode_applied": result.mode_applied,
    }


def search_index_status_doc(status: IndexStatus) -> dict[str, Any]:
    """``GET /api/v1/admin/search-index`` (FR-Q7, FR-P5).

    ``embedding_model`` and ``stale_chunks`` are never null and never conditional on
    a setting: with ``GW_EMBEDDING_ENABLED=false`` there is no provider to compare
    against, so both are computed against the configured ``GW_EMBEDDING_MODEL`` name
    and ``semantic_enabled`` reports false alongside them (docs/DATA_MODEL.md
    section 13).
    """
    return {
        # Counts rows a worker is currently holding as well: an operator asking
        # "how much is not indexed yet" means both.
        "pending_jobs": status.pending_jobs,
        "running_jobs": status.running_jobs,
        "failed_jobs": [
            {
                "record_key": job.record_key,
                "record_id": job.record_id,
                "source_type": job.source_type,
                "field_key": job.field_key,
                "comment_id": job.comment_id,
                "attempts": job.attempts,
                "last_error": job.last_error,
                "updated_at": job.updated_at,
            }
            for job in status.failed_jobs
        ],
        "indexed_chunks": status.indexed_chunks,
        "stale_chunks": status.stale_chunks,
        "embedding_model": status.embedding_model,
        "semantic_enabled": status.semantic_enabled,
    }


# ``audit_event_doc``, ``comment_doc``, ``field_doc``, and ``record_doc`` are imported from
# ``glosswork.serializers`` above rather than defined here (see the ``# --- rows``
# comment near the top of this file); listing them here is what tells mypy's
# ``strict``/``no_implicit_reexport`` that routes and MCP tool modules importing them *from
# this module* are importing a name this module deliberately re-exports, not an incidental
# one. Every other name in this file is defined here directly and needs no such declaration.
__all__ = [
    "audit_event_doc",
    "comment_doc",
    "field_doc",
    "record_doc",
]
