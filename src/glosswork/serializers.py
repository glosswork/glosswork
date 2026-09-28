"""Row-to-document serializers shared across layers (DD-3).

These six functions live here rather than in ``envelopes.py`` because
``ExportService`` (FR-E5, ``services/export.py``) reuses them: ``envelopes.py``
imports ``ServiceBundle`` from ``glosswork.services`` (several of its other helpers take
a whole service bundle to compute derived fields like a live record count), so a service
importing ``envelopes`` back would be a circular import -- ``glosswork.services``
would be importing ``glosswork.services.export``, which imports ``glosswork.envelopes``,
which imports ``glosswork.services`` again, before ``ServiceBundle`` exists yet.

These particular functions never needed that: each takes only a repository row (plus, for
``field_doc``, an optional samples list) and returns a plain dict. Splitting them out into a
module with no dependency on ``ServiceBundle`` or any service lets both ``envelopes.py`` (REST
and MCP adapters) and ``services/export.py`` (the service layer) import the *same* function
rather than the service layer duplicating what the adapters already do.

``envelopes.py`` re-exports the four it still uses itself, so the route and MCP tool modules
that import them from there needed no change. The other two, ``saved_view_doc`` and
``agent_label_doc``, had no remaining use inside ``envelopes.py``, so their two callers
(``routes/saved_views.py`` and ``routes/agent_labels.py``) import them from here directly
rather than through a re-export that exists only to avoid touching two import lines.
"""

from __future__ import annotations

from typing import Any

from glosswork.fieldtypes import is_display_eligible, operators_for
from glosswork.repositories.models import (
    AgentLabelRow,
    AuditEvent,
    CommentRow,
    FieldDef,
    GrantRow,
    PrincipalRow,
    RecordRow,
    SavedViewRow,
)


def principal_sidecar_doc(principals: list[PrincipalRow]) -> dict[str, dict[str, Any]]:
    """The ``principals`` map that rides alongside every response document carrying a
    record (DD-25), keyed by principal id.

    Three keys, not the directory's five: ``id`` is the map key, and ``type`` is not
    something a name rendered next to a value needs. Same projection discipline as the
    directory -- ``role``, ``auth_provider``, ``external_id`` and ``password_hash`` are
    absent, and ``tests/test_principal_sidecar.py`` asserts each by name.

    It lives here rather than in ``envelopes`` because its callers are services --
    ``RecordService.principal_sidecar`` and ``AccessService.list_grants_document`` -- and
    this module is the dependency-free half the service layer may import
    (see the module docstring). Both build their own map by calling this rather than
    handing rows to an envelope to project, so there is one projection of a principal
    sidecar in the codebase and one place to audit it (DD-25).
    """
    return {
        p.id: {
            "display_name": p.display_name,
            "email": p.email,
            "is_active": p.is_active,
            # ``type`` is projected even though "a name rendered next to a value has no use
            # for it" would hold if the sidecar's only job were turning an id into a name.
            # docs/DESIGN.md 6.1 makes the *kind* of a
            # principal load-bearing -- shape carries it, so the distinction survives greyscale
            # -- and says a service account is an agent. Without this key a service account
            # writing under no agent label renders as a person circle: a wrong answer rendered
            # confidently. Discloses nothing new: any authenticated principal already reads
            # ``type`` from ``GET /api/v1/principals/directory``.
            "type": p.type,
        }
        for p in principals
    }


def agent_label_sidecar_doc(labels: list[AgentLabelRow]) -> dict[str, dict[str, Any]]:
    """The ``agent_labels`` map that rides alongside every response document carrying a
    record, keyed by agent-label id.

    **Two keys, and the projection is a disclosure decision rather than a convenience**
    Cross-principal label reads are admin-gated: ``GET /api/v1/agent-labels``
    returns the caller's own, and every principal's labels sit behind ``require_scope("admin")``
    *and* ``require_role("admin")``. This map hands a label's **name** to anyone who may read
    the record it wrote, which is the point of the map -- a ``By`` column that says
    "sales-agent" is the feature -- and is no more than the audit trail already discloses to
    the same caller as an id.

    Withheld, by name and asserted by name in ``tests/test_record_agent_label.py``:
    ``principal_id`` (whose label it is, which is also the key an unprivileged caller would
    enumerate by), ``call_count`` and ``last_seen_at`` (FR-I7 operational telemetry, which is
    what the admin view is *for*), ``description``, ``first_seen_at`` and ``verified``.

    Same projection discipline, and same reason for living here rather than in ``envelopes``,
    as :func:`principal_sidecar_doc` above.
    """
    return {
        label.id: {"label": label.label, "display_name": label.display_name} for label in labels
    }


def record_doc(record: RecordRow) -> dict[str, Any]:
    return {
        "id": record.id,
        "key": record.key,
        "version": record.version,
        "created_at": record.created_at,
        "created_by": record.created_by,
        "updated_at": record.updated_at,
        "updated_by": record.updated_by,
        # The agent label of the write that last changed a value, or null when a
        # person made it. Resolved to text through the response's ``agent_labels`` sidecar,
        # never rendered raw (docs/DESIGN.md 6.4).
        "updated_by_agent_label_id": record.updated_by_agent_label_id,
        "deleted_at": record.deleted_at,
        "comment_count": record.comment_count,
        "last_comment_at": record.last_comment_at,
        "data": record.data,
    }


def comment_doc(comment: CommentRow) -> dict[str, Any]:
    return {
        "id": comment.id,
        "record_id": comment.record_id,
        "body": comment.body,
        "author_id": comment.author_id,
        "agent_label_id": comment.agent_label_id,
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
        "edited": comment.edited,
        "deleted_at": comment.deleted_at,
        # DD-25: the author's human label, resolved at the repository layer. Null when the
        # author principal no longer exists; the frontend falls back to `author_id`.
        "principal_display_name": comment.principal_display_name,
        # The agent label's text. Null when the comment carries no label, which is the
        # ordinary case; the primitive then renders no agent at all (docs/DESIGN.md 6.5).
        "agent_label": comment.agent_label,
    }


def saved_view_doc(view: SavedViewRow) -> dict[str, Any]:
    return {
        "id": view.id,
        "object_type_id": view.object_type_id,
        "name": view.name,
        "description": view.description,
        "mode": view.mode,
        "config": view.config,
        "is_default": view.is_default,
        "created_at": view.created_at,
        "created_by": view.created_by,
        "updated_at": view.updated_at,
        "updated_by": view.updated_by,
    }


def audit_event_doc(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "ts": event.ts,
        "request_id": event.request_id,
        "principal_id": event.principal_id,
        "principal_type": event.principal_type,
        "agent_label_id": event.agent_label_id,
        # Resolved at the repository (DD-25), so the audit browser and the record timeline
        # never render this id raw.
        "agent_label": event.agent_label,
        "auth_method": event.auth_method,
        "surface": event.surface,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "record_id": event.record_id,
        "object_type_id": event.object_type_id,
        "action": event.action,
        "field_key": event.field_key,
        "old_value": event.old_value,
        "new_value": event.new_value,
        "note": event.note,
        # DD-25: both resolved at the repository layer. Null where the referent has since
        # been deleted, which the frontend renders as the raw id.
        "principal_display_name": event.principal_display_name,
        "record_key": event.record_key,
    }


def agent_label_doc(label: AgentLabelRow) -> dict[str, Any]:
    return {
        "id": label.id,
        "principal_id": label.principal_id,
        "label": label.label,
        "display_name": label.display_name,
        "description": label.description,
        "verified": label.verified,
        "first_seen_at": label.first_seen_at,
        "last_seen_at": label.last_seen_at,
        "call_count": label.call_count,
    }


def field_doc(field: FieldDef, samples: list[Any] | None = None) -> dict[str, Any]:
    """One field of a describe document (docs/MCP_TOOLS.md 5.1): select options
    and relation targets are surfaced at the top level, as the example shows, in
    addition to the raw ``config``."""
    doc: dict[str, Any] = {
        "key": field.key,
        "name": field.name,
        "type": field.type,
        "description": field.description,
        "required": field.is_required,
        "unique": field.is_unique,
        "indexed": field.is_indexed,
        "embed": field.embed,
        "default": field.default_value,
        "config": field.config,
        "position": field.position,
        "operators": operators_for(field.type),
        # DD-23: whether this field may be the type's display field. On the wire so
        # the schema editor's "Display field" select filters on the one implementation
        # (``fieldtypes.is_display_eligible``) rather than on a second hand-maintained
        # list a TypeScript module cannot import.
        "display_eligible": is_display_eligible(field.type),
    }
    if field.type in ("single_select", "multi_select"):
        doc["options"] = field.config.get("options", [])
    if field.type == "relation":
        doc["target_type_key"] = field.config.get("target_type_key")
        doc["cardinality"] = field.config.get("cardinality")
        doc["inverse_field_key"] = field.config.get("inverse_field_key")
    if samples is not None:
        doc["samples"] = samples
    return doc


def grant_doc(row: GrantRow) -> dict[str, Any]:
    """One ``object_type_grants`` row. ``level`` is the stored grant,
    not the composed ``effective`` value: this is the administrative view of who was
    given what, and a caller asking what *it* can do reads ``your_access`` instead."""
    return {
        "object_type_id": row.object_type_id,
        "principal_id": row.principal_id,
        "level": row.level,
        "created_at": row.created_at,
        "created_by": row.created_by,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }
