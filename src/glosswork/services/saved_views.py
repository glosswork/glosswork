"""Saved views: named, persisted, shared filter/sort/grouping/column presets per
object type (FR-U3, docs/DATA_MODEL.md section 11).

Views are shared across all principals at MVP (no per-user private views). At most
one view per object type may be marked default; the DB's partial unique index on
``saved_views(object_type_id) WHERE is_default = 1`` is the hard enforcement, and
this service clears every other default of the same type inside the same write
transaction that sets a new one, so the flip is atomic and never trips the index.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Connection

from glosswork.actor import ActorContext, Scope
from glosswork.db import Database
from glosswork.errors import NotFoundError, UnknownObjectTypeError, ValidationFailedError
from glosswork.repositories.interfaces import (
    AuditRepository,
    SavedViewRepository,
    SchemaRepository,
)
from glosswork.repositories.models import ObjectType, SavedViewRow
from glosswork.services.access import AccessService
from glosswork.services.base import make_event
from glosswork.timeutil import format_datetime, utc_now

VALID_MODES = frozenset({"table", "card"})


class SavedViewService:
    def __init__(
        self,
        db: Database,
        schema_repo: SchemaRepository,
        saved_view_repo: SavedViewRepository,
        audit_repo: AuditRepository,
        access: AccessService,
    ) -> None:
        self._db = db
        self._schema = schema_repo
        self._views = saved_view_repo
        self._audit = audit_repo
        # DD-11. A saved view has no grants of its own: it is a stored query over
        # one object type, so it is exactly as visible as that type. A view over a type
        # you cannot read is not listed.
        self._access = access

    # Both reads take an ``ActorContext``, first and positional.

    def list_saved_views(self, actor: ActorContext, object_type_key: str) -> list[SavedViewRow]:
        with self._db.read() as conn:
            object_type = self._require_type(conn, object_type_key)
            self._access.require_level(conn, actor, object_type, "read")
            return self._views.list_for_type(conn, object_type.id)

    def get_saved_view(self, actor: ActorContext, view_id: str) -> SavedViewRow:
        with self._db.read() as conn:
            view = self._require_view(conn, view_id)
            self._require_view_level(conn, actor, view, "read")
            return view

    def create_saved_view(
        self,
        actor: ActorContext,
        object_type_key: str,
        name: str,
        config: dict[str, Any],
        mode: str = "table",
        description: str | None = None,
        is_default: bool = False,
        now: datetime | None = None,
    ) -> SavedViewRow:
        self._validate_name(name)
        self._validate_mode(mode)
        self._validate_config(config)
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            object_type = self._require_type(conn, object_type_key)
            self._access.require_level(conn, actor, object_type, "write")
            if is_default:
                # Atomic within this one write transaction: clear every other
                # default of this type before the new row lands, so the partial
                # unique index on saved_views(object_type_id) WHERE is_default = 1
                # never sees two default rows at once.
                self._views.clear_default_for_type(conn, object_type.id)
            view = SavedViewRow(
                id=str(uuid.uuid4()),
                object_type_id=object_type.id,
                name=name,
                description=description,
                mode=mode,
                config=config,
                is_default=is_default,
                created_at=ts,
                created_by=actor.principal_id,
                updated_at=ts,
                updated_by=actor.principal_id,
            )
            self._views.insert(conn, view)
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="saved_view",
                        entity_id=view.id,
                        action="create",
                        object_type_id=object_type.id,
                        new_value=self._audit_snapshot(view),
                    )
                ],
            )
            return view

    def update_saved_view(
        self,
        actor: ActorContext,
        view_id: str,
        name: str | None = None,
        description: str | None = None,
        config: dict[str, Any] | None = None,
        mode: str | None = None,
        is_default: bool | None = None,
        now: datetime | None = None,
    ) -> SavedViewRow:
        if name is not None:
            self._validate_name(name)
        if mode is not None:
            self._validate_mode(mode)
        if config is not None:
            self._validate_config(config)
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            view = self._require_view(conn, view_id)
            self._require_view_level(conn, actor, view, "write")
            old_snapshot = self._audit_snapshot(view)
            changes: dict[str, Any] = {"updated_at": ts, "updated_by": actor.principal_id}
            if name is not None:
                changes["name"] = name
            if description is not None:
                changes["description"] = description
            if config is not None:
                changes["config"] = config
            if mode is not None:
                changes["mode"] = mode
            if is_default is not None:
                changes["is_default"] = is_default
                if is_default:
                    # Same atomicity guarantee as create_saved_view: clear every
                    # other default of this type before this row is flipped on,
                    # in the same transaction.
                    self._views.clear_default_for_type(conn, view.object_type_id, except_id=view.id)
            self._views.update_row(conn, view.id, changes)
            updated = self._views.get(conn, view.id)
            assert updated is not None
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="saved_view",
                        entity_id=view.id,
                        action="update",
                        object_type_id=view.object_type_id,
                        old_value=old_snapshot,
                        new_value=self._audit_snapshot(updated),
                    )
                ],
            )
            return updated

    def delete_saved_view(
        self, actor: ActorContext, view_id: str, now: datetime | None = None
    ) -> SavedViewRow:
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            view = self._require_view(conn, view_id)
            self._require_view_level(conn, actor, view, "write")
            self._views.delete(conn, view.id)
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="saved_view",
                        entity_id=view.id,
                        action="delete",
                        object_type_id=view.object_type_id,
                        old_value=self._audit_snapshot(view),
                    )
                ],
            )
            return view

    def _require_view_level(
        self, conn: Connection, actor: ActorContext, view: SavedViewRow, required: Scope
    ) -> None:
        object_type = self._schema.get_object_type_by_id(conn, view.object_type_id)
        if object_type is None:
            raise NotFoundError("object type", view.object_type_id)
        self._access.require_level(conn, actor, object_type, required)

    def _require_type(self, conn: Connection, object_type_key: str) -> ObjectType:
        object_type = self._schema.get_object_type_by_key(conn, object_type_key)
        if object_type is None:
            valid = [t.key for t in self._schema.list_object_types(conn)]
            raise UnknownObjectTypeError(object_type_key, valid)
        return object_type

    def _require_view(self, conn: Connection, view_id: str) -> SavedViewRow:
        view = self._views.get(conn, view_id)
        if view is None:
            raise NotFoundError("saved view", view_id)
        return view

    @staticmethod
    def _validate_name(name: str) -> None:
        if not isinstance(name, str) or not name.strip():
            raise ValidationFailedError("Saved view name must be non-empty.")

    @staticmethod
    def _validate_mode(mode: str) -> None:
        if mode not in VALID_MODES:
            raise ValidationFailedError(f"mode must be one of {sorted(VALID_MODES)}, got {mode!r}.")

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> None:
        if not isinstance(config, dict):
            raise ValidationFailedError("config must be a JSON object.")

    @staticmethod
    def _audit_snapshot(view: SavedViewRow) -> dict[str, Any]:
        return {
            "name": view.name,
            "description": view.description,
            "mode": view.mode,
            "config": view.config,
            "is_default": view.is_default,
        }
