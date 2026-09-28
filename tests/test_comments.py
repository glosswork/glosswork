"""Comment acceptance tests (FR-C1, FR-C6, FR-C8; PRD section 6.4).

Comments are records-adjacent, not a field on the record (DD-7, docs/DATA_MODEL.md
section 7): a flat list ordered by ``created_at``, soft-deletable, with edits
retaining the full prior body in the audit store, and ``comment_count`` /
``last_comment_at`` maintained on the record in the same transaction as every
comment write, which is what makes them cheap to filter and sort on (FR-C8).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from glosswork.actor import ActorContext, Scope
from glosswork.db import Database
from glosswork.errors import (
    ForbiddenError,
    InsufficientScopeError,
    NotFoundError,
    ValidationFailedError,
)
from glosswork.repositories.models import FieldDef, ObjectType, RecordRow
from glosswork.repositories.sqlite import SqliteAuditRepository
from glosswork.services import ServiceBundle
from glosswork.services.comments import MAX_COMMENT_LIMIT
from glosswork.timeutil import format_datetime, utc_now
from tests.conftest import make_actor

SinkType = tuple[ObjectType, dict[str, FieldDef]]


def _insert_principal(
    db: Database, principal_id: str, display_name: str, role: str = "member"
) -> None:
    """A second principal row, inserted directly rather than through the principal
    service: FR-C5 authorization keys off the calling ActorContext, which is
    independent of how the row was made.

    ``role`` is what ``AccessService._is_system_admin`` reads, so the moderation
    cases -- where the authority is the ``admin`` *level* on the type and a system
    ``admin`` is unrestricted -- need to be able to set it."""
    with db.write() as conn:
        conn.execute(
            text(
                "INSERT INTO principals (id, type, display_name, role, created_at) "
                "VALUES (:id, 'user', :name, :role, :ts)"
            ),
            {
                "id": principal_id,
                "name": display_name,
                "role": role,
                "ts": format_datetime(utc_now()),
            },
        )


def _actor_for(principal_id: str, scope: Scope) -> ActorContext:
    return ActorContext(
        principal_id=principal_id,
        principal_type="user",
        agent_label_id=None,
        auth_method="session",
        surface="ui",
        request_id=str(uuid.uuid4()),
        scope=scope,
    )


def _make_record(
    services: ServiceBundle,
    actor: ActorContext,
    title: str,
    now: datetime | None = None,
) -> RecordRow:
    return services.records.create_record(actor, "artifact", {"title": title}, now=now)


def test_add_comment_orders_chronologically_and_updates_counters_immediately(
    services: ServiceBundle, actor: ActorContext, sink_type: SinkType
) -> None:
    record = _make_record(services, actor, "Comment target")
    t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=5)

    first = services.comments.add_comment(actor, record.key, "First comment", now=t0)
    second = services.comments.add_comment(actor, record.key, "Second comment", now=t1)

    comments = services.comments.list_comments(actor, record.key)
    assert [c.id for c in comments] == [first.id, second.id]
    assert [c.body for c in comments] == ["First comment", "Second comment"]

    # comment_count and last_comment_at are refreshed inside add_comment's own
    # write transaction (docs/DATA_MODEL.md section 7): a read immediately after
    # the call already reflects both comments, with no separate reconciliation step.
    refreshed = services.records.get_record(actor, record.key)
    assert refreshed.comment_count == 2
    assert refreshed.last_comment_at == format_datetime(t1)


def test_update_comment_marks_edited_and_audit_retains_full_prior_body(
    services: ServiceBundle, actor: ActorContext, db: Database, sink_type: SinkType
) -> None:
    record = _make_record(services, actor, "Edit target")
    t0 = datetime(2026, 1, 2, 9, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)
    original_body = "This is the original comment body, kept in full for the record."
    comment = services.comments.add_comment(actor, record.key, original_body, now=t0)

    updated = services.comments.update_comment(actor, comment.id, "Revised body.", now=t1)

    assert updated.edited is True
    assert updated.body == "Revised body."

    with db.read() as conn:
        events = SqliteAuditRepository().for_record(conn, record.id)
    update_events = [e for e in events if e.entity_type == "comment" and e.action == "update"]
    assert len(update_events) == 1
    event = update_events[0]
    # FR-C6: the audit store retains the exact full prior body, not a diff or a
    # truncated summary.
    assert event.old_value == original_body
    assert event.new_value == "Revised body."


def test_delete_comment_soft_deletes_and_recomputes_counters(
    services: ServiceBundle, actor: ActorContext, sink_type: SinkType
) -> None:
    record = _make_record(services, actor, "Delete target")
    t_old = datetime(2026, 1, 3, 8, 0, 0, tzinfo=UTC)
    t_new = t_old + timedelta(minutes=30)
    t_delete_newer = t_new + timedelta(minutes=1)
    t_delete_older = t_delete_newer + timedelta(minutes=1)

    older = services.comments.add_comment(actor, record.key, "Older comment", now=t_old)
    newer = services.comments.add_comment(actor, record.key, "Newer comment", now=t_new)

    deleted_newer = services.comments.delete_comment(actor, newer.id, now=t_delete_newer)
    assert deleted_newer.deleted_at == format_datetime(t_delete_newer)

    live = services.comments.list_comments(actor, record.key)
    assert [c.id for c in live] == [older.id]

    with_deleted = services.comments.list_comments(actor, record.key, include_deleted=True)
    assert {c.id for c in with_deleted} == {older.id, newer.id}

    # comment_count falls back to 1 and last_comment_at recomputes to the
    # remaining (older) comment's created_at, not to a stale or null value.
    refreshed = services.records.get_record(actor, record.key)
    assert refreshed.comment_count == 1
    assert refreshed.last_comment_at == format_datetime(t_old)

    services.comments.delete_comment(actor, older.id, now=t_delete_older)

    fully_refreshed = services.records.get_record(actor, record.key)
    assert fully_refreshed.comment_count == 0
    assert fully_refreshed.last_comment_at is None
    assert services.comments.list_comments(actor, record.key) == []
    assert len(services.comments.list_comments(actor, record.key, include_deleted=True)) == 2


def test_comment_count_and_last_comment_at_are_filterable_and_sortable(
    services: ServiceBundle, actor: ActorContext, sink_type: SinkType
) -> None:
    # "Today" for filter purposes is controlled by the `now` passed to
    # query_records, not wall-clock time, so the @today+1d token resolves
    # deterministically regardless of when the test actually runs.
    reference = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)

    no_discussion = _make_record(services, actor, "No discussion")
    one_old = _make_record(services, actor, "One old comment")
    two_old = _make_record(services, actor, "Two old comments")
    one_future = _make_record(services, actor, "One comment after the cutoff")

    services.comments.add_comment(actor, one_old.key, "old", now=reference - timedelta(days=5))
    services.comments.add_comment(actor, two_old.key, "older", now=reference - timedelta(days=4))
    services.comments.add_comment(actor, two_old.key, "newer", now=reference - timedelta(days=3))
    services.comments.add_comment(actor, one_future.key, "later", now=reference + timedelta(days=5))

    has_activity = services.records.query_records(
        actor,
        "artifact",
        filter={"field": "comment_count", "op": "gte", "value": 1},
        now=reference,
    )
    assert {r["key"] for r in has_activity.records} == {
        one_old.key,
        two_old.key,
        one_future.key,
    }

    # Records with no recent discussion: either no comments at all (last_comment_at
    # is null) or the last comment landed before the @today+1d cutoff.
    no_recent_discussion = services.records.query_records(
        actor,
        "artifact",
        filter={
            "or": [
                {"field": "last_comment_at", "op": "lt", "value": "@today+1d"},
                {"field": "last_comment_at", "op": "is_null"},
            ]
        },
        now=reference,
    )
    assert {r["key"] for r in no_recent_discussion.records} == {
        no_discussion.key,
        one_old.key,
        two_old.key,
    }
    # one_future's last comment is after the cutoff, so it is correctly excluded.
    assert one_future.key not in {r["key"] for r in no_recent_discussion.records}

    sorted_result = services.records.query_records(
        actor,
        "artifact",
        sort=[{"field": "comment_count", "dir": "desc"}, {"field": "key", "dir": "asc"}],
        now=reference,
    )
    assert [r["key"] for r in sorted_result.records] == [
        two_old.key,
        one_old.key,
        one_future.key,
        no_discussion.key,
    ]


def test_comment_create_audit_event_carries_full_attribution(
    services: ServiceBundle, actor: ActorContext, db: Database, sink_type: SinkType
) -> None:
    record = _make_record(services, actor, "Attribution target")
    caller = make_actor()
    services.comments.add_comment(caller, record.key, "Attributed comment")

    with db.read() as conn:
        events = SqliteAuditRepository().for_record(conn, record.id)
    create_events = [e for e in events if e.entity_type == "comment" and e.action == "create"]
    assert len(create_events) == 1
    event = create_events[0]
    assert event.principal_id == caller.principal_id
    assert event.auth_method == caller.auth_method
    assert event.surface == caller.surface
    assert event.agent_label_id == caller.agent_label_id
    assert event.request_id == caller.request_id
    # The comment's own call gets its own request id, distinct from the record
    # creation call that preceded it.
    assert event.request_id != actor.request_id


@pytest.mark.parametrize("body", ["", "   ", "\n\t"])
def test_add_comment_rejects_empty_or_whitespace_body(
    services: ServiceBundle, actor: ActorContext, sink_type: SinkType, body: str
) -> None:
    record = _make_record(services, actor, "Validation target")
    with pytest.raises(ValidationFailedError):
        services.comments.add_comment(actor, record.key, body)


def test_add_comment_on_missing_record_raises_not_found(
    services: ServiceBundle, actor: ActorContext
) -> None:
    with pytest.raises(NotFoundError):
        services.comments.add_comment(actor, "ART-999", "Hello there")


def test_only_the_author_or_an_admin_may_edit_or_delete(
    services: ServiceBundle, actor: ActorContext, db: Database, sink_type: SinkType
) -> None:
    """FR-C5: authors may edit and delete their own comments; a non-author is
    rejected; a type's administrator may delete anyone's.

    DD-11 decides *which* administrator that is. A rule of
    ``actor.scope != "admin"`` would read the credential's raw scope as a grant of
    authority, in contradiction of DD-11, where the credential is only ever a ceiling.
    It is the ``admin`` level on the record's object type, so both halves of that
    ``min`` can be what refuses, with the code that is true of each.

    ``update_comment`` is deliberately untouched and still refuses a non-author with
    ``ValidationFailedError``: editing has no administrator path at all, so the two
    adjacent methods refuse with different classes on purpose.
    """
    record = _make_record(services, actor, "Auth target")
    comment = services.comments.add_comment(actor, record.key, "original body")

    other_id = str(uuid.uuid4())
    _insert_principal(db, other_id, "Other User")
    # The object-type level is the coarser gate and is checked first, so the second
    # principal needs `write` on `artifact` for FR-C5's authorship rule to be what
    # refuses it. Without the grant this would fail with `forbidden` and prove nothing
    # about authorship.
    services.access.grant(actor, "artifact", other_id, "write")
    other_member = _actor_for(other_id, "write")

    with pytest.raises(ValidationFailedError, match="author"):
        services.comments.update_comment(other_member, comment.id, "hijacked")
    # The delete refusal moved off `ValidationFailedError`. This caller's *credential*
    # is what falls short of `admin` first, so the truthful code is `insufficient_scope`
    # rather than `forbidden`; the `forbidden` half is the credential-sufficient probe
    # below.
    with pytest.raises(InsufficientScopeError):
        services.comments.delete_comment(other_member, comment.id)
    # Neither rejected call took effect.
    untouched = services.comments.list_comments(actor, record.key)[0]
    assert untouched.body == "original body"
    assert untouched.deleted_at is None

    # The author themself may still edit and delete.
    edited = services.comments.update_comment(actor, comment.id, "author's own edit")
    assert edited.body == "author's own edit"

    # A different principal granted `admin` **on the type** may delete someone else's.
    services.access.grant(actor, "artifact", other_id, "admin")
    deleted = services.comments.delete_comment(_actor_for(other_id, "admin"), comment.id)
    assert deleted.deleted_at is not None


def test_a_creator_with_an_admin_credential_cannot_moderate_a_type_it_only_writes(
    services: ServiceBundle, actor: ActorContext, db: Database, sink_type: SinkType
) -> None:
    """A security review's probe. ``role_scope('creator')`` is ``admin``, so a creator
    can mint itself an ``admin`` PAT; under a scope-based rule that alone would let it
    delete any principal's comment on any record it held ``write`` on."""
    record = _make_record(services, actor, "Moderation probe")
    comment = services.comments.add_comment(actor, record.key, "somebody else's comment")

    creator_id = str(uuid.uuid4())
    _insert_principal(db, creator_id, "A Creator", role="creator")
    services.access.grant(actor, "artifact", creator_id, "write")

    with pytest.raises(ForbiddenError) as excinfo:
        services.comments.delete_comment(_actor_for(creator_id, "admin"), comment.id)
    assert excinfo.value.code == "forbidden"
    assert "artifact" in excinfo.value.message
    assert services.comments.list_comments(actor, record.key)[0].deleted_at is None


def test_a_system_admin_may_moderate_any_type(
    services: ServiceBundle, actor: ActorContext, db: Database, sink_type: SinkType
) -> None:
    """**Fence.** A system ``admin`` moderates because ``granted()`` is unrestricted
    for it (``_is_system_admin``), not because anything reads the role name here. This
    passes on a tree with the scope-based rule too -- it guards the outcome the
    level-based rule must not change while changing which *other* principals reach it."""
    record = _make_record(services, actor, "System admin target")
    comment = services.comments.add_comment(actor, record.key, "a comment to moderate")

    admin_id = str(uuid.uuid4())
    _insert_principal(db, admin_id, "System Admin", role="admin")

    deleted = services.comments.delete_comment(_actor_for(admin_id, "admin"), comment.id)
    assert deleted.deleted_at is not None


def test_a_system_admin_holding_a_write_credential_is_refused_on_the_ceiling(
    services: ServiceBundle, actor: ActorContext, db: Database, sink_type: SinkType
) -> None:
    """DD-11's ceiling, on the one principal whose grant can never be the thing that
    refuses: the credential is too narrow, and ``insufficient_scope`` says so."""
    record = _make_record(services, actor, "Ceiling target")
    comment = services.comments.add_comment(actor, record.key, "a comment to moderate")

    admin_id = str(uuid.uuid4())
    _insert_principal(db, admin_id, "System Admin", role="admin")

    with pytest.raises(InsufficientScopeError) as excinfo:
        services.comments.delete_comment(_actor_for(admin_id, "write"), comment.id)
    assert excinfo.value.code == "insufficient_scope"
    assert services.comments.list_comments(actor, record.key)[0].deleted_at is None


def test_the_author_still_deletes_their_own_comment_at_write(
    services: ServiceBundle, actor: ActorContext, db: Database, sink_type: SinkType
) -> None:
    """**Fence.** Authorship is still the ordinary path and still costs only ``write``
    on the type; the administrator branch sits beside it, not in front of it."""
    record = _make_record(services, actor, "Own comment")
    author_id = str(uuid.uuid4())
    _insert_principal(db, author_id, "Author")
    services.access.grant(actor, "artifact", author_id, "write")
    author = _actor_for(author_id, "write")

    comment = services.comments.add_comment(author, record.key, "mine to delete")
    deleted = services.comments.delete_comment(author, comment.id)
    assert deleted.deleted_at is not None


# ---------------------------------------------------------------------------
# The comment page is capped (DD-18)
# ---------------------------------------------------------------------------


class TestCommentPageLimit:
    def test_the_cap_is_accepted_and_one_past_it_is_refused(
        self, services: ServiceBundle, sink_type: SinkType
    ) -> None:
        record = services.records.create_record(make_actor(), "artifact", {"title": "One"})
        services.comments.list_comments_page(make_actor(), record.key, limit=MAX_COMMENT_LIMIT)
        with pytest.raises(ValidationFailedError) as exc:
            services.comments.list_comments_page(
                make_actor(), record.key, limit=MAX_COMMENT_LIMIT + 1
            )
        assert exc.value.details["max_limit"] == MAX_COMMENT_LIMIT
        assert str(MAX_COMMENT_LIMIT) in exc.value.message

    def test_a_limit_over_the_cap_is_refused_over_rest_too(
        self, client: TestClient, app_services: ServiceBundle
    ) -> None:
        app_services.schema.create_object_type(
            make_actor(),
            key="widget",
            name="Widget",
            name_plural="Widgets",
            description="A type used to prove the REST surface inherits the comment cap.",
            key_prefix="WDG",
            fields=[
                {
                    "key": "title",
                    "name": "Title",
                    "type": "short_text",
                    "description": "Short human-readable name for the widget.",
                }
            ],
        )
        record = app_services.records.create_record(make_actor(), "widget", {"title": "One"})
        response = client.get(
            f"/api/v1/records/{record.key}/comments", params={"limit": MAX_COMMENT_LIMIT + 1}
        )
        assert response.status_code == 422
        assert response.json()["error"]["details"]["max_limit"] == MAX_COMMENT_LIMIT
