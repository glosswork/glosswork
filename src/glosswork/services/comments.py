"""Comments: the primary human/agent narrative channel (FR-C1 through FR-C8, DD-7).

``records.comment_count`` and ``records.last_comment_at`` are maintained in the same
transaction as every comment change, which is what makes them cheap to filter and
sort on (FR-C8). Edits retain the full prior body in the audit store (FR-C6).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Connection

from glosswork.actor import ActorContext, Scope
from glosswork.cursors import decode_cursor, encode_cursor, validate_page_limit
from glosswork.db import Database
from glosswork.errors import NotFoundError, ValidationFailedError
from glosswork.repositories.interfaces import (
    AuditRepository,
    CommentRepository,
    RecordRepository,
    SchemaRepository,
)
from glosswork.repositories.models import CommentRow, RecordRow
from glosswork.services.access import AccessService
from glosswork.services.base import make_event
from glosswork.services.search_index import SearchIndexService
from glosswork.timeutil import format_datetime, utc_now

DEFAULT_COMMENT_LIMIT = 100
# DD-18. The history ceiling's number, for the same reason: both are one record's
# audit trail or one record's conversation, and neither is a table scan. Enforced here
# in the service so REST and MCP inherit one rule.
MAX_COMMENT_LIMIT = 500


@dataclass(slots=True)
class CommentPage:
    comments: list[CommentRow]
    next_cursor: str | None


class CommentService:
    def __init__(
        self,
        db: Database,
        schema_repo: SchemaRepository,
        record_repo: RecordRepository,
        comment_repo: CommentRepository,
        audit_repo: AuditRepository,
        access: AccessService,
        search_index: SearchIndexService | None = None,
    ) -> None:
        self._db = db
        self._schema = schema_repo
        self._records = record_repo
        self._comments = comment_repo
        self._audit = audit_repo
        # DD-11. Comments have **no grants of their own**: a comment is exactly as
        # visible as the record it hangs on, which is exactly as visible as that record's
        # object type. `read` on the parent type to list, `write` to mutate.
        self._access = access
        # Every comment body is indexed (FR-C7): a concept that appears only in a
        # comment being findable is PRD section 9's third success criterion.
        self._search_index = search_index

    # Both reads take an ``ActorContext``, first and positional, exactly as
    # the writes do.

    def list_comments(
        self, actor: ActorContext, record_ref: str, include_deleted: bool = False
    ) -> list[CommentRow]:
        with self._db.read() as conn:
            record = self._records.get_record(conn, record_ref, include_deleted=True)
            if record is None:
                raise NotFoundError("record", record_ref)
            self._require_record_level(conn, actor, record, "read")
            return self._comments.list_for_record(conn, record.id, include_deleted)

    def list_comments_page(
        self,
        actor: ActorContext,
        record_ref: str,
        limit: int = DEFAULT_COMMENT_LIMIT,
        cursor: str | None = None,
        include_deleted: bool = False,
    ) -> CommentPage:
        """Chronological comments, keyset-paginated over (created_at, insertion
        order) so every comment is yielded exactly once across page boundaries
        (docs/MCP_TOOLS.md 5.1 ``list_comments``)."""
        validate_page_limit(limit, MAX_COMMENT_LIMIT, "MAX_COMMENT_LIMIT")
        after: tuple[str, str] | None = None
        if cursor is not None:
            boundary = decode_cursor(cursor, {"created_at", "id"})
            after = (str(boundary["created_at"]), str(boundary["id"]))
        with self._db.read() as conn:
            record = self._records.get_record(conn, record_ref, include_deleted=True)
            if record is None:
                raise NotFoundError("record", record_ref)
            self._require_record_level(conn, actor, record, "read")
            comments = self._comments.list_for_record(
                conn, record.id, include_deleted, after=after, limit=limit + 1
            )
        next_cursor: str | None = None
        if len(comments) > limit:
            comments = comments[:limit]
            last = comments[-1]
            next_cursor = encode_cursor({"created_at": last.created_at, "id": last.id})
        return CommentPage(comments=comments, next_cursor=next_cursor)

    def add_comment(
        self,
        actor: ActorContext,
        record_ref: str,
        body: str,
        now: datetime | None = None,
    ) -> CommentRow:
        if not isinstance(body, str) or not body.strip():
            raise ValidationFailedError("Comment body must be non-empty markdown text.")
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            record = self._records.get_record(conn, record_ref)
            if record is None:
                raise NotFoundError("record", record_ref)
            self._require_record_level(conn, actor, record, "write")
            comment = CommentRow(
                id=str(uuid.uuid4()),
                record_id=record.id,
                body=body,
                author_id=actor.principal_id,
                agent_label_id=actor.agent_label_id,
                created_at=ts,
                updated_at=ts,
                edited=False,
                deleted_at=None,
                deleted_by=None,
            )
            self._comments.insert_comment(conn, comment)
            self._refresh_counters(conn, record.id)
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="comment",
                        entity_id=comment.id,
                        action="create",
                        record_id=record.id,
                        object_type_id=record.object_type_id,
                        new_value=body,
                    )
                ],
            )
            if self._search_index is not None:
                self._search_index.index_comment(conn, record, comment, now)
            # Read the row back so the returned envelope carries the author's resolved
            # display name (DD-25) exactly as a later list would, rather than the raw id
            # until the thread refetches. `update_comment` and `delete_comment` already do.
            created = self._comments.get_comment(conn, comment.id)
            assert created is not None
            return created

    def update_comment(
        self,
        actor: ActorContext,
        comment_id: str,
        body: str,
        now: datetime | None = None,
    ) -> CommentRow:
        """Authors edit their own comments (FR-C5); the prior body is retained in
        the audit store and the comment is marked edited (FR-C6)."""
        if not isinstance(body, str) or not body.strip():
            raise ValidationFailedError("Comment body must be non-empty markdown text.")
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            comment = self._require_live_comment(conn, comment_id)
            record = self._records.get_record(conn, comment.record_id, include_deleted=True)
            if record is not None:
                self._require_record_level(conn, actor, record, "write")
            if comment.author_id != actor.principal_id:
                raise ValidationFailedError("Only the comment's author may edit it (FR-C5).")
            self._comments.update_comment_row(
                conn, comment.id, {"body": body, "updated_at": ts, "edited": True}
            )
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="comment",
                        entity_id=comment.id,
                        action="update",
                        record_id=comment.record_id,
                        object_type_id=record.object_type_id if record else None,
                        old_value=comment.body,
                        new_value=body,
                    )
                ],
            )
            updated = self._comments.get_comment(conn, comment.id)
            assert updated is not None
            if self._search_index is not None and record is not None:
                self._search_index.index_comment(conn, record, updated, now)
            return updated

    def delete_comment(
        self, actor: ActorContext, comment_id: str, now: datetime | None = None
    ) -> CommentRow:
        """Soft delete by the author, or by an administrator of the record's object
        type (FR-C5, FR-C6; DD-11).

        The credential's own scope (``actor.scope``) is never read as a *grant* of
        authority: under DD-11 the scope is only ever a ceiling that can narrow.
        ``role_scope('creator')`` is ``admin``, so reading it as a grant would let a creator
        mint itself an ``admin`` PAT and delete any principal's comment on any record it
        held ``write`` on.

        The authority is the ``admin`` level on the record's type, asked of the one
        service that answers that question. Which half of ``min(credential, grant)``
        fell short decides the error, so the refusal is truthful: ``forbidden`` when the
        grant was too low, ``insufficient_scope`` when the credential was. A system
        ``admin`` is unrestricted because ``granted()`` says so, not because anything
        here reads a role.

        ``update_comment`` is deliberately not changed: editing has no administrator
        path at all, so the two adjacent methods refuse a non-author with different
        error classes on purpose.
        """
        ts = format_datetime(now or utc_now())
        with self._db.write() as conn:
            comment = self._require_live_comment(conn, comment_id)
            record = self._records.get_record(conn, comment.record_id, include_deleted=True)
            if record is not None:
                self._require_record_level(conn, actor, record, "write")
            if comment.author_id != actor.principal_id:
                if record is None:
                    # Unreachable, and it cannot express a `ForbiddenError` anyway:
                    # that error names the object type whose grant fell short, and with
                    # no record there is no type. `RecordService.delete_record` is a
                    # soft delete and nothing in `src/` removes a `records` row.
                    raise ValidationFailedError(
                        "Only the comment's author or an administrator may delete it (FR-C5)."
                    )
                self._require_record_level(conn, actor, record, "admin")
            self._comments.update_comment_row(
                conn, comment.id, {"deleted_at": ts, "deleted_by": actor.principal_id}
            )
            self._refresh_counters(conn, comment.record_id)
            self._audit.append(
                conn,
                [
                    make_event(
                        actor,
                        ts,
                        entity_type="comment",
                        entity_id=comment.id,
                        action="delete",
                        record_id=comment.record_id,
                        object_type_id=record.object_type_id if record else None,
                        old_value=comment.body,
                    )
                ],
            )
            deleted = self._comments.get_comment(conn, comment.id)
            assert deleted is not None
            return deleted

    def _require_record_level(
        self, conn: Connection, actor: ActorContext, record: RecordRow, required: Scope
    ) -> None:
        """The level check for a comment, expressed against its parent record's type."""
        object_type = self._schema.get_object_type_by_id(conn, record.object_type_id)
        if object_type is None:
            raise NotFoundError("object type", record.object_type_id)
        self._access.require_level(conn, actor, object_type, required)

    def _require_live_comment(self, conn: Connection, comment_id: str) -> CommentRow:
        comment = self._comments.get_comment(conn, comment_id)
        if comment is None or comment.deleted_at is not None:
            raise NotFoundError("comment", comment_id)
        return comment

    def _refresh_counters(self, conn: Connection, record_id: str) -> None:
        count, last_at = self._comments.live_stats(conn, record_id)
        self._records.update_record_row(
            conn, record_id, {"comment_count": count, "last_comment_at": last_at}
        )
