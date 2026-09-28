"""Discovery and read tools (docs/MCP_TOOLS.md section 5.1; scope ``read``).

Each tool parses its arguments, builds the actor, calls the service layer, and
shapes the result with the envelopes shared with REST (DD-3). No logic here.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp.types import CallToolResult, ResourceLink
from pydantic import Field

from glosswork.envelopes import (
    attachment_doc,
    changes_doc,
    comments_page_doc,
    describe_object_type_doc,
    history_page_doc,
    list_object_types_doc,
    principal_directory_doc,
    query_result_doc,
    record_with_includes_doc,
    search_result_doc,
)
from glosswork.mcp_server.adapter import McpAdapter, attachment_link
from glosswork.mcp_server.catalog import ToolCatalog
from glosswork.mcp_server.params import (
    CursorParam,
    FilterParam,
    ObjectTypeParam,
    RecordParam,
    limit_param,
)
from glosswork.services.capabilities import capabilities_document
from glosswork.services.changes import DEFAULT_LIMIT as CHANGES_DEFAULT_LIMIT
from glosswork.services.comments import DEFAULT_COMMENT_LIMIT, MAX_COMMENT_LIMIT
from glosswork.services.principals import DIRECTORY_DEFAULT_LIMIT, DIRECTORY_MAX_LIMIT
from glosswork.services.records import (
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_QUERY_LIMIT,
    MAX_HISTORY_LIMIT,
    MAX_QUERY_LIMIT,
)
from glosswork.services.search import (
    DEFAULT_MODE,
    DEFAULT_SEARCH_LIMIT,
    MAX_QUERY_CHARS,
    MAX_SEARCH_LIMIT,
)

SearchFilterParam = Annotated[
    dict[str, Any] | None,
    Field(
        description=(
            "Structured filter applied on top of relevance, in the query_records grammar. "
            "One-type rule: when object_types names exactly one type, the filter may "
            "reference that type's fields (from describe_object_type) as well as the system "
            "pseudo-fields; with several or no object_types it may reference only the system "
            "pseudo-fields (key, created_at, updated_at, created_by, updated_by, "
            "comment_count, last_comment_at, deleted_at), and a user-field reference is "
            "validation_failed naming this rule. Those eight names are also reserved as "
            "field keys, so a type never has a field of the same name. Date values "
            "accept tokens such as '@today-7d'; user_ref values accept '@me'. Omit to "
            "apply no filter."
        )
    ),
]


def register_read_tools(catalog: ToolCatalog, adapter: McpAdapter) -> None:
    @catalog.tool("read")
    def list_object_types(ctx: Context) -> CallToolResult:
        """List every object type with its key, name, description, key prefix, record
        count, and field count. This is the starting point for any task: the schema is
        user-defined, so call this first to learn what exists and what each type is
        for, then call describe_object_type for the type you need."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx)
            return {"object_types": list_object_types_doc(adapter.services, actor)}

        return adapter.call(run)

    @catalog.tool("read")
    def describe_object_type(
        object_type: ObjectTypeParam,
        include_samples: Annotated[
            bool,
            Field(
                description=(
                    "When true, add up to three example values per field drawn from live "
                    "records (empty for fields with no data). Improves first-attempt "
                    "accuracy on free-text fields."
                )
            ),
        ] = False,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """The orientation tool: everything you need to read or write one object type
        without being pre-programmed with its schema. Returns the type's description
        and, for every field, its key, name, type, agent-facing description, whether it
        is required, unique, or indexed, its default, its select options with
        per-option descriptions, its relation target type, cardinality, and inverse
        field, and the exact filter operators legal for it. Also returns the system
        pseudo-fields (key, created_at, updated_at, created_by, updated_by,
        comment_count, last_comment_at, deleted_at) with their operators. Those eight
        names are reserved as field keys and will never appear under 'fields'. Build
        every
        query_records filter and every create_record/update_record values document
        from this response."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx)
            return describe_object_type_doc(
                adapter.services, actor, object_type, include_samples=include_samples
            )

        return adapter.call(run)

    @catalog.tool("read")
    def query_records(
        object_type: ObjectTypeParam,
        filter: FilterParam = None,
        sort: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description=(
                    "Sort keys in priority order, each {field, dir} with dir 'asc' or "
                    "'desc'. Fields may be user-defined or system pseudo-fields. Defaults "
                    "to creation order."
                )
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                description=(
                    f"Maximum records per page, 1 to {MAX_QUERY_LIMIT}. Follow "
                    "next_cursor for more."
                )
            ),
        ] = DEFAULT_QUERY_LIMIT,
        cursor: CursorParam = None,
        fields: Annotated[
            list[str] | str | None,
            Field(
                description=(
                    "Field keys to include in each record's data. Omit for the compact "
                    "default (the display field plus indexed fields), which protects your "
                    "context window; pass '*' for every field. System pseudo-fields are "
                    "always present on the record envelope."
                )
            ),
        ] = None,
        expand_relations: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Relation field keys to resolve one level deep, returning each linked "
                    "record's key, id, and display value under 'expand'."
                )
            ),
        ] = None,
        include_deleted: Annotated[
            bool,
            Field(description="Include soft-deleted records (deleted_at is then meaningful)."),
        ] = False,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Query records of one object type by any combination of user-defined fields
        and system pseudo-fields, using the field keys and operators returned by
        describe_object_type. Returns records, total_count, next_cursor for keyset
        pagination, and truncated: when true, a guidance string explains that the
        result was clipped and tells you to narrow the filter, request specific
        fields, or follow next_cursor. Errors name the tool to call next (for example
        unknown_field lists valid keys and suggests a near miss)."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx)
            result = adapter.services.records.query_records(
                actor,
                object_type,
                filter=filter,
                sort=sort,
                limit=limit,
                cursor=cursor,
                fields=fields,
                expand_relations=expand_relations,
                include_deleted=include_deleted,
            )
            return query_result_doc(adapter.services, actor, object_type, result)

        return adapter.call(run)

    @catalog.tool("read")
    def get_record(
        record: RecordParam,
        include: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Extra sections to include: any of 'comments', 'links', 'history', "
                    "'attachments'. Omit for the record alone."
                )
            ),
        ] = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Fetch one record by human key or UUID, with its full data document and
        version (pass the version as expected_version when you later update it).
        Optionally include its comments, links, field-level history, and attachment
        metadata. With include=['attachments'] each attachment carries a download_url
        and a resource link you can read with resources/read; the bytes never travel
        in this result."""

        def run() -> dict[str, Any]:
            return record_with_includes_doc(
                adapter.services, adapter.actor(ctx), record, set(include or [])
            )

        def links(payload: dict[str, Any]) -> list[ResourceLink]:
            # One link per *resolved* attachment, across every attachment field. A link
            # is a claim the server can serve those bytes, so an id ``get_many``
            # silently omitted -- dangling, or withheld by the read rule -- gets none.
            by_field = payload.get("attachments") or {}
            return [attachment_link(doc) for docs in by_field.values() for doc in docs]

        return adapter.call(run, links)

    @catalog.tool("read")
    def get_attachment(
        attachment_id: Annotated[
            str,
            Field(
                description=(
                    "The attachment's UUID, as stored on a record's attachment field and "
                    "returned by get_record with include=['attachments']."
                )
            ),
        ],
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Describe one attachment: filename, content type, byte size, sha256, who
        uploaded it and when, and a download_url. The bytes are never returned here.
        Read them with resources/read on the resource link this returns
        (attachment://{attachment_id}), or fetch download_url over HTTPS with the same
        bearer token you use for this connection. To store a file, see
        create_text_attachment for a document you wrote, or POST it to
        /api/v1/attachments as multipart/form-data with the field name 'file'."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx)
            row = adapter.services.attachments.get_attachment(actor, attachment_id)
            return attachment_doc(row, adapter.services.attachments.download_url(row.id))

        return adapter.call(run, lambda payload: [attachment_link(payload)])

    @catalog.tool("read")
    def find_principals(
        query: Annotated[
            str | None,
            Field(
                description=(
                    "Case-insensitive substring of a display name or an email address. Omit "
                    "to list the directory."
                )
            ),
        ] = None,
        type: Annotated[
            str | None,
            Field(description="'user' or 'service_account'. Omit for both."),
        ] = None,
        include_inactive: Annotated[
            bool,
            Field(
                description=(
                    "Include deactivated principals. They can still be filtered on, but a "
                    "record write will not assign one."
                )
            ),
        ] = False,
        limit: Annotated[
            int,
            Field(description=f"Maximum entries to return (maximum {DIRECTORY_MAX_LIMIT})."),
        ] = DIRECTORY_DEFAULT_LIMIT,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Look up people and service accounts by name or email, and get the principal id
        a user_ref field stores. Call it when you know who someone is but not their id, or
        to check how a name is spelled before you write it. Each entry is id,
        display_name, email, type, and is_active, and nothing else: roles and
        authentication settings are not part of the directory. You do not have to use it
        to write a user_ref field -- an email address, an exact display name, or '@me'
        resolve on the way in -- but a name shared by two people is refused rather than
        guessed, and this is how you find the id that disambiguates it."""

        def run() -> dict[str, Any]:
            # The only read tool that builds no ``ActorContext``, because
            # ``search_principals`` takes none: no object type is involved, so there is no
            # grant to consult, and the ``read`` scope gate already ran in the catalog
            # middleware. Constructing one to discard it would read as an oversight.
            principals = adapter.services.principals.search_principals(
                q=query, principal_type=type, include_inactive=include_inactive, limit=limit
            )
            return {"principals": [principal_directory_doc(p) for p in principals]}

        return adapter.call(run)

    @catalog.tool("read")
    def search(
        query: Annotated[
            str,
            Field(
                description=(
                    "What to look for, in plain words or as an exact identifier. Non-empty, "
                    f"at most {MAX_QUERY_CHARS} characters."
                )
            ),
        ],
        object_types: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Object type keys to search within. Omit (or pass []) to search every "
                    "type. Naming exactly one type also unlocks that type's fields in "
                    "filter."
                )
            ),
        ] = None,
        filter: SearchFilterParam = None,
        mode: Annotated[
            str,
            Field(
                description=(
                    "'hybrid' (default: keyword and semantic fused), 'semantic' (meaning "
                    "only), or 'keyword' (exact words and identifiers only). Use 'keyword' "
                    "for identifiers such as PO-88213: the semantic arm does not see them."
                )
            ),
        ] = DEFAULT_MODE,
        limit: Annotated[
            int,
            Field(
                description=(
                    f"Maximum results, 1 to {MAX_SEARCH_LIMIT} (default "
                    f"{DEFAULT_SEARCH_LIMIT}). There is no cursor: narrow with object_types "
                    "or filter instead of paging."
                )
            ),
        ] = DEFAULT_SEARCH_LIMIT,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Find records by meaning or by exact words across the long_text fields,
        opted-in short_text fields, and comment bodies of any object type. Use search
        when you know what something is about but not which field or record holds it,
        or when the answer may live only in a comment; use query_records when you can
        express the question as a filter over known fields. Use mode 'keyword' for
        exact identifiers (PO-88213, CHG-2291): each whitespace word becomes one
        phrase of its alphanumeric runs, so an identifier matches only text carrying
        it, and the semantic arm does not see identifiers at all. Record keys such as
        INIT-014 are not indexed text: fetch a record by key with get_record. Each
        result carries record_key, object_type, title, a score where 1.0 means ranked
        first by every arm that ran, hit_source (the field, or the comment with its
        author and created_at, where the match was found), a snippet with <em>
        markers, and other_matches (additional keyword-matched locations on the same
        record; 0 for a purely semantic hit). Read index_lag on every response: a
        non-zero pending_jobs means very recent writes may not be semantically
        searchable yet (keyword search sees them immediately). mode_applied tells you
        what actually ran; it differs from mode only when semantic search is disabled
        on this deployment and 'hybrid' degraded to keyword. On such a deployment
        mode 'semantic' is refused with feature_disabled and details.use_instead names
        the mode to use; describe_capabilities reports search.semantic_enabled so you
        can check first. An empty query is validation_failed; a query made only of
        stopwords returns no keyword results."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx)
            result = adapter.services.search.search(
                actor,
                query,
                object_types=object_types,
                filter=filter,
                mode=mode,
                limit=limit,
            )
            return search_result_doc(result)

        return adapter.call(run)

    @catalog.tool("read")
    def list_comments(
        record: RecordParam,
        limit: limit_param(MAX_COMMENT_LIMIT) = DEFAULT_COMMENT_LIMIT,  # type: ignore[valid-type]
        cursor: CursorParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Chronological comments on a record with author principal, agent label id
        (when written by an agent), timestamps, and an edited flag. Paginated by
        keyset: follow next_cursor until it is null."""

        def run() -> dict[str, Any]:
            page = adapter.services.comments.list_comments_page(
                adapter.actor(ctx), record, limit=limit, cursor=cursor
            )
            return comments_page_doc(page)

        return adapter.call(run)

    @catalog.tool("read")
    def get_record_history(
        record: RecordParam,
        field_key: Annotated[
            str | None,
            Field(description="Restrict the history to changes of this one field key."),
        ] = None,
        limit: limit_param(MAX_HISTORY_LIMIT) = DEFAULT_HISTORY_LIMIT,  # type: ignore[valid-type]
        cursor: CursorParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Field-level audit history for one record: who changed what, when, from what
        value to what value, through which surface ('ui', 'api', or 'mcp'), and under
        which agent label. Answers 'who changed this status and when' without UI
        access. Paginated by keyset: follow next_cursor until it is null."""

        def run() -> dict[str, Any]:
            page = adapter.services.records.get_record_history_page(
                adapter.actor(ctx), record, field_key=field_key, limit=limit, cursor=cursor
            )
            return history_page_doc(page)

        return adapter.call(run)

    @catalog.tool("read")
    def list_changes_since(
        cursor: Annotated[
            int | None,
            Field(
                description=(
                    "Change-feed cursor (an audit event id). Omit it to receive the current "
                    "cursor with no events, so you can start watching from now; then pass "
                    "each response's next_cursor back to receive what happened since."
                )
            ),
        ] = None,
        object_types: Annotated[
            list[str] | None,
            Field(description="Restrict the feed to these object type keys."),
        ] = None,
        limit: Annotated[
            int, Field(description="Maximum events per call (1 to 1000).")
        ] = CHANGES_DEFAULT_LIMIT,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Pull-based change feed: every audited change (records, links, comments,
        schema) after a cursor, ordered by audit event id, with next_cursor. This is
        how to react to activity without polling every record."""

        def run() -> dict[str, Any]:
            result = adapter.services.changes.list_changes_since(
                adapter.actor(ctx), cursor=cursor, object_type_keys=object_types, limit=limit
            )
            return changes_doc(result)

        return adapter.call(run)

    # ------------------------------------------------------- the manual (DD-30)
    #
    # ``read`` scope, and in this module rather than ``tools_admin.py``, because the
    # three modules match the three scopes. Nothing justifies a higher scope: it reads no
    # principal, token, grant or record, mutates nothing, and returns the platform's own
    # static description. The agent that has to act on it -- the one that uploads a file
    # -- holds ``write``, and at ``admin`` it could neither see it in ``tools/list`` nor
    # call it, which would push the file recipe (DD-29) into ``INSTRUCTIONS``, where a
    # host truncates it away.

    @catalog.tool("read")
    def describe_capabilities(ctx: Context) -> CallToolResult:
        """This endpoint's manual. Call it first, before anything else.

        Returns everything about this deployment you would otherwise guess at: the
        thirteen field types with their config keys and the filter operators legal on
        each, the system pseudo-fields and the field keys they reserve, the filter
        grammar, the date and identity tokens, key naming rules, every limit you can hit
        (query and history page sizes, filter depth, attachment bytes), and an
        `attachments` block carrying the whole file recipe -- where bytes go up, the
        tool that mints a credential to send them with, where they come back down, and
        what you may store as text. Takes no arguments and is readable at any scope."""

        def run() -> dict[str, Any]:
            adapter.actor(ctx)
            attachments = adapter.services.attachments
            return capabilities_document(
                adapter.services.search.semantic_enabled,
                # The service's own answers over a literal placeholder, so the published
                # pattern and a real download_url cannot describe different routes, and
                # the published upload_url is the one a minted ticket points at.
                download_url_pattern=attachments.download_url("{attachment_id}"),
                attachment_max_bytes=attachments.max_attachment_bytes,
                upload_url=attachments.upload_url,
                upload_ticket_ttl_seconds=attachments.upload_ticket_ttl_seconds,
            )

        return adapter.call(run)
