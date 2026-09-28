"""Shared service plumbing: audit event construction from the actor context."""

from __future__ import annotations

from typing import Any

from glosswork.actor import ActorContext
from glosswork.fieldtypes import is_display_eligible
from glosswork.repositories.models import AuditEvent, FieldDef


def display_field(
    fields_by_key: dict[str, FieldDef], display_field_key: str | None = None
) -> FieldDef | None:
    """The field used as a record's human-readable label (FR-S11, FR-M8, FR-L6, FR-Q4).

    The object type's chosen ``display_field_key`` when it names a live, eligible field;
    otherwise the derived fallback, the first non-relation field by declared position.
    A dangling key falls back rather than raising: the reference has no FK and
    a record must always be labelable.

    Shared so the search service's ``title`` (docs/MCP_TOOLS.md section 5.1) is the same
    value the compact projection and relation expansion already show, rather than a
    second rule. The optional argument is what lets the answer be chosen rather than
    only guessed: a caller that passes nothing gets the fallback.

    **The fallback is deliberately "first non-relation", not "first eligible."** The
    *creation default* skips ineligible fields because it is choosing a key and an
    immediately-unusable one is no choice at all. This fallback is the older rule
    untouched, so an existing type whose first field is an ``attachment`` or a
    ``user_ref`` labels its records exactly as it did. Two rules that read alike and are
    deliberately not the same.
    """
    if display_field_key is not None:
        chosen = fields_by_key.get(display_field_key)
        if chosen is not None and is_display_eligible(chosen.type):
            return chosen
    candidates = [f for f in fields_by_key.values() if f.type != "relation"]
    if not candidates:
        return None
    return min(candidates, key=lambda f: f.position)


def make_event(
    actor: ActorContext,
    ts: str,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    record_id: str | None = None,
    object_type_id: str | None = None,
    field_key: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    note: str | None = None,
) -> AuditEvent:
    """One audit row carrying full attribution (FR-D4): principal, principal type,
    agent label, auth method, surface, and the request id correlating the call."""
    return AuditEvent(
        ts=ts,
        request_id=actor.request_id,
        principal_id=actor.principal_id,
        principal_type=actor.principal_type,
        agent_label_id=actor.agent_label_id,
        auth_method=actor.auth_method,
        surface=actor.surface,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        record_id=record_id,
        object_type_id=object_type_id,
        field_key=field_key,
        old_value=old_value,
        new_value=new_value,
        note=note,
    )
