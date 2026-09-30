"""Row-shaped value objects passed between repositories and services.

JSON columns (``records.data``, ``fields.config``, proposal payload/impact, audit
values) are decoded to Python values here so nothing above the repository layer
touches JSON encoding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ObjectType:
    id: str
    key: str
    name: str
    name_plural: str
    description: str
    key_prefix: str
    key_counter: int
    icon: str | None
    is_deleted: bool
    created_at: str
    created_by: str
    updated_at: str
    updated_by: str
    # DD-11: what a principal with no ``object_type_grants`` row gets on this
    # type. Defaulted here so every existing construction site keeps compiling and a
    # type created without an explicit level is closed rather than open.
    default_level: str = "none"
    # DD-23: the chosen display field, held as a ``fields.key`` with no FK,
    # so it may dangle. NULL and dangling alike fall back to ``services/base.display_field``'s
    # derived rule. Defaulted so every existing construction site keeps compiling.
    display_field_key: str | None = None


@dataclass(slots=True)
class FieldDef:
    id: str
    object_type_id: str
    key: str
    name: str
    description: str
    type: str
    position: int
    is_required: bool
    is_unique: bool
    is_indexed: bool
    embed: bool
    default_value: Any
    config: dict[str, Any]
    is_deleted: bool
    created_at: str
    created_by: str
    updated_at: str
    updated_by: str


@dataclass(slots=True)
class RecordRow:
    id: str
    object_type_id: str
    key: str
    key_seq: int
    version: int
    data: dict[str, Any]
    created_at: str
    created_by: str
    updated_at: str
    updated_by: str
    deleted_at: str | None
    deleted_by: str | None
    comment_count: int
    last_comment_at: str | None
    # The agent label of the write that last changed a field value, or None when a
    # person made it. Written wherever ``updated_by`` is written and nowhere else -- delete and
    # restore do not move either, because ``updated_by`` is the last *value* change
    # (``fieldtypes.py``). Defaulted so every construction site that predates the column keeps
    # working; the write path passes it explicitly.
    updated_by_agent_label_id: str | None = None


@dataclass(slots=True)
class LinkRow:
    id: str
    field_id: str
    from_record_id: str
    to_record_id: str
    position: int
    created_at: str
    created_by: str


@dataclass(slots=True)
class CommentRow:
    id: str
    record_id: str
    body: str
    author_id: str
    agent_label_id: str | None
    created_at: str
    updated_at: str
    edited: bool
    deleted_at: str | None
    deleted_by: str | None
    # DD-25: resolved by a LEFT JOIN in the repository's own read, not by a service lookup.
    # None when the row was built by a write path that has not read it back, or when the
    # author principal no longer exists; the frontend then renders the raw id.
    principal_display_name: str | None = field(default=None, compare=False)
    # The same treatment for ``agent_label_id``, which reached the frontend only ever as
    # a UUID (`CommentItem.tsx` rendered `(agent: <uuid>)`). None is the COMMON case here, not
    # the exceptional one -- every comment written by a person at a keyboard has no label --
    # which is why the join is LEFT and why DESIGN.md 6.5 turns on the distinction.
    agent_label: str | None = field(default=None, compare=False)


@dataclass(slots=True)
class SavedViewRow:
    """One row of ``saved_views`` (docs/DATA_MODEL.md section 11, FR-U3). Views are
    shared across all principals at MVP: there is no owner/visibility column."""

    id: str
    object_type_id: str
    name: str
    description: str | None
    mode: str
    config: dict[str, Any]
    is_default: bool
    created_at: str
    created_by: str
    updated_at: str
    updated_by: str


@dataclass(slots=True)
class Proposal:
    id: str
    status: str
    change_type: str
    target_type_id: str | None
    target_field_id: str | None
    payload: dict[str, Any]
    impact: dict[str, Any]
    snapshot_ref: str | None
    reason: str | None
    proposed_at: str
    proposed_by: str
    proposed_agent: str | None
    decided_at: str | None
    decided_by: str | None
    decision_note: str | None


@dataclass(slots=True)
class AgentLabelRow:
    """One row of the per-principal agent label registry (docs/DATA_MODEL.md
    ``agent_labels``, FR-I6). Auto-created on first use with ``verified`` false."""

    id: str
    principal_id: str
    label: str
    display_name: str | None
    description: str | None
    verified: bool
    first_seen_at: str
    last_seen_at: str
    call_count: int


@dataclass(slots=True)
class AttachmentRow:
    id: str
    sha256: str
    filename: str
    content_type: str
    byte_size: int
    uploaded_at: str
    uploaded_by: str


@dataclass(slots=True)
class GrantRow:
    """One ``object_type_grants`` row (DD-11, docs/DATA_MODEL.md section 2): how
    much one principal may do to one object type.

    ``level`` may be ``'none'``, which is an explicit deny rather than the absence of a
    row: it overrides a permissive ``object_types.default_level``, which is how one
    person is excluded from a type that is otherwise open.
    """

    id: str
    object_type_id: str
    principal_id: str
    level: str
    created_at: str
    created_by: str
    updated_at: str
    updated_by: str


@dataclass(slots=True)
class AuditEvent:
    """One immutable audit row (docs/DATA_MODEL.md section 9). ``id`` is assigned by
    the database on append and doubles as the change-feed cursor."""

    ts: str
    request_id: str
    principal_id: str
    principal_type: str
    agent_label_id: str | None
    auth_method: str
    surface: str
    entity_type: str
    entity_id: str
    action: str
    record_id: str | None = None
    object_type_id: str | None = None
    field_key: str | None = None
    old_value: Any = None
    new_value: Any = None
    note: str | None = None
    id: int | None = field(default=None, compare=False)
    # DD-25: resolved by LEFT JOINs in the repository's own reads. Null where the referent
    # has since been deleted, which the frontend renders as the raw id.
    # `compare=False` so an event built for a write still equals the row read back.
    principal_display_name: str | None = field(default=None, compare=False)
    record_key: str | None = field(default=None, compare=False)
    # The agent label's text, resolved by a third LEFT JOIN. See ``CommentRow`` above for
    # why the null case is the ordinary one rather than the deleted-referent one.
    agent_label: str | None = field(default=None, compare=False)


@dataclass(slots=True)
class PrincipalRow:
    """One actor (docs/DATA_MODEL.md section 2): a human who can log in or a service
    account that only holds tokens. ``password_hash`` is carried here because the
    service layer needs it to verify a login; no serializer ever emits it."""

    id: str
    type: str
    display_name: str
    email: str | None
    role: str
    auth_provider: str | None
    external_id: str | None
    password_hash: str | None
    is_active: bool
    description: str | None
    created_at: str
    created_by: str | None


@dataclass(slots=True)
class AccessTokenRow:
    """One personal access token (docs/DATA_MODEL.md section 2, FR-I4). The plaintext
    secret is never stored; ``token_hash`` is its sha256 and ``token_prefix`` its
    first 8 characters, kept only so a human can tell two tokens apart in a list."""

    id: str
    principal_id: str
    name: str
    token_hash: str
    token_prefix: str
    scope: str
    expires_at: str | None
    last_used_at: str | None
    revoked_at: str | None
    created_at: str
    created_by: str
    # DD-16. Three nullable columns, absent on every ordinary PAT. ``capability``
    # narrows the DD-11 credential ceiling from "any write" to one named operation;
    # ``capability_data`` is the JSON object binding a ticket to one filename and
    # content type; ``consumed_at`` is stamped inside the transaction that inserts the
    # attachment, so a ticket is spent exactly once.
    capability: str | None = None
    capability_data: str | None = None
    consumed_at: str | None = None
    # DD-17. The agent label this token was minted for, as a **string**
    # rather than an ``agent_labels.id``: registration stays at the edge, on use,
    # so nothing is registered at mint time and ``register_use`` keeps its single call
    # site. ``None`` means "this token carries no label of its own", which is every row
    # that existed before migration 10 and every token minted without one.
    agent_label: str | None = None


@dataclass(slots=True)
class SignInCodeRow:
    """One emailed sign-in code (docs/DATA_MODEL.md section 2, change 9). Shaped like
    :class:`SessionRow`: the code is never stored, only ``code_hash``, the sha256 of
    ``"<id>:<code>"``. ``sent`` records whether the address could sign in when the code
    was made, so the unsent rows an unknown address leaves can be bounded."""

    id: str
    email: str
    code_hash: str
    created_at: str
    expires_at: str
    attempts: int
    consumed_at: str | None
    sent: bool


@dataclass(slots=True)
class InviteRow:
    """One invitation to a workspace (docs/DATA_MODEL.md section 2, change 9). No person
    exists until the invited address signs in with a code; ``principal_id`` then names
    the person the acceptance created or reactivated."""

    id: str
    email: str
    display_name: str
    role: str
    invited_by: str
    created_at: str
    accepted_at: str | None
    principal_id: str | None
    revoked_at: str | None
    revoked_by: str | None


@dataclass(slots=True)
class SessionRow:
    """One browser session (docs/DATA_MODEL.md section 2, FR-A3, DD-9). Shaped
    like :class:`AccessTokenRow`: the plaintext cookie value is never stored, only its
    sha256. ``csrf_hash`` is the DD-10 double-submit token's sha256, bound to the
    session in the same row rather than a second table."""

    id: str
    principal_id: str
    session_hash: str
    csrf_hash: str
    created_at: str
    expires_at: str
    last_seen_at: str
    revoked_at: str | None


@dataclass(frozen=True, slots=True)
class IndexSource:
    """One indexable unit of content (docs/DATA_MODEL.md section 10).

    Either a single field of a single record, or a single comment. This is the grain
    of every search-index operation: an ``fts_content`` row, a queue job, and a run of
    ``embeddings`` rows are all "per source". ``object_type_id`` rides along because
    purging by field or by object type is an indexed delete rather than a join back
    through ``records``.

    Frozen and hashable so a write path can deduplicate the sources it touched before
    enqueuing, which is what keeps one record update from enqueuing the same field
    twice.
    """

    record_id: str
    object_type_id: str
    source_type: str  # 'field' | 'comment'
    field_key: str | None = None
    comment_id: str | None = None

    @classmethod
    def field(cls, record_id: str, object_type_id: str, field_key: str) -> IndexSource:
        return cls(record_id, object_type_id, "field", field_key=field_key)

    @classmethod
    def comment(cls, record_id: str, object_type_id: str, comment_id: str) -> IndexSource:
        return cls(record_id, object_type_id, "comment", comment_id=comment_id)


@dataclass(slots=True)
class EmbeddingRow:
    """One stored chunk. ``id`` is also the ``vec_embeddings`` rowid, which is why the
    worker preserves an unchanged chunk's row instead of replacing a source's rows."""

    id: int
    source_type: str
    record_id: str
    object_type_id: str
    field_key: str | None
    comment_id: str | None
    chunk_index: int
    chunk_text: str
    content_hash: str
    model_id: str
    created_at: str
    vector: list[float] | None = None


@dataclass(slots=True)
class EmbeddingJobRow:
    id: int
    record_id: str
    source_type: str
    field_key: str | None
    comment_id: str | None
    status: str
    attempts: int
    last_error: str | None
    enqueued_at: str
    updated_at: str

    def source(self, object_type_id: str) -> IndexSource:
        """The job's source. ``object_type_id`` is supplied by the caller because the
        queue row deliberately carries no denormalized copy of it: a job holds an
        identity, never content or context, so the worker always re-reads what is
        true now (docs/DATA_MODEL.md section 10)."""
        return IndexSource(
            record_id=self.record_id,
            object_type_id=object_type_id,
            source_type=self.source_type,
            field_key=self.field_key,
            comment_id=self.comment_id,
        )


@dataclass(slots=True)
class FailedJobRow:
    """A failed job as the admin status endpoint reports it (FR-Q7, FR-P5)."""

    record_key: str | None
    record_id: str
    source_type: str
    field_key: str | None
    comment_id: str | None
    attempts: int
    last_error: str | None
    updated_at: str


@dataclass(slots=True)
class IndexCounts:
    pending_jobs: int
    running_jobs: int
    failed_jobs: int
    indexed_chunks: int


@dataclass(slots=True)
class KeywordHit:
    """One ``fts_content`` row of the keyword arm's pool (docs/DATA_MODEL.md
    section 10): its source, its FTS5 ``snippet()`` with ``<em>`` markers, and its
    ``bm25`` score (lower is better; carried for diagnostics, not for fusion, which
    ranks by position)."""

    source: IndexSource
    snippet: str
    bm25: float


@dataclass(slots=True)
class VectorHit:
    """One chunk row of the vector arm's pool: its source, the exact text that was
    embedded (the semantic snippet is rendered from it), and its distance."""

    source: IndexSource
    chunk_text: str
    distance: float


@dataclass(slots=True)
class VectorPool:
    """The vector arm's pool plus how many rows the KNN returned **before** the live
    join. A soft-deleted record's chunks occupy KNN slots (the ``k`` constraint is
    applied inside the virtual table, where ``records.deleted_at`` is invisible), so
    the service needs the pre-join count to know the pool was full and widen once
    (decision 6) rather than reading an emptied pool as "nothing more to find"."""

    hits: list[VectorHit]
    knn_rows: int


@dataclass(slots=True)
class EmbeddingSyncResult:
    """What one source's embedding sync actually did, for the worker's log line."""

    inserted: int
    reused: int
    deleted: int
