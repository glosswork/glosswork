"""Record, link, and comment mutation tools (docs/MCP_TOOLS.md section 5.2; scope
``write``). Every tool accepts an optional ``agent`` label overriding the
connection's ``X-Agent-Label`` header (FR-M5). No logic here (DD-3)."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp.types import CallToolResult
from pydantic import Field

from glosswork.envelopes import (
    attachment_doc,
    bulk_update_doc,
    comment_doc,
    record_response_doc,
)
from glosswork.mcp_server.adapter import McpAdapter, attachment_link
from glosswork.mcp_server.catalog import ToolCatalog
from glosswork.mcp_server.params import (
    AgentParam,
    BodyParam,
    CommentIdParam,
    FieldKeyParam,
    ObjectTypeParam,
    RecordParam,
    ValuesParam,
)
from glosswork.services.attachments import TEXT_ATTACHMENT_TYPES

ToRecordsParam = Annotated[
    list[str],
    Field(description="Target records, each by human key or UUID."),
]

_ALLOWED_TEXT_TYPES = ", ".join(TEXT_ATTACHMENT_TYPES)

# DD-16. Telling the model to use "the same bearer token this connection uses" would be
# true and not actionable: it describes a credential the *MCP client* holds, and no part
# of the protocol hands the model its own. An agent reading that went outside the session
# to find one. This names the tool that mints a credential the agent can actually see.
_BINARY_ROUTE = (
    "For any other file, including binary, call create_attachment_upload: it returns an "
    "absolute URL and a single-use credential to POST the bytes to as "
    "multipart/form-data, and you then put the returned id on the record with "
    "update_record."
)


def register_write_tools(catalog: ToolCatalog, adapter: McpAdapter) -> None:
    @catalog.tool("write")
    def create_record(
        object_type: ObjectTypeParam,
        values: ValuesParam,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Create a record of an object type from a values document keyed by field
        key (get the keys, types, and option values from describe_object_type).
        Required fields must be present; defaults fill the rest. Returns the record
        with its assigned key and version 1. Validation errors name the offending
        field, state the rule, and list valid options for select fields."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx, agent)
            record = adapter.services.records.create_record(actor, object_type, values)
            return record_response_doc(adapter.services, actor, record)

        return adapter.call(run)

    @catalog.tool("write")
    def update_record(
        record: RecordParam,
        values: ValuesParam,
        expected_version: Annotated[
            int | None,
            Field(
                description=(
                    "The record's version as you last read it (get_record or "
                    "query_records return it). Always pass it when you have it: the write "
                    "is refused with version_conflict if the record changed since, and the "
                    "error lists current_version, conflicting_fields, and "
                    "changed_since_your_version so you can merge and retry."
                )
            ),
        ] = None,
        force: Annotated[
            bool,
            Field(
                description=(
                    "Overwrite even if the record changed since expected_version. Use only "
                    "after reviewing a version_conflict error."
                )
            ),
        ] = False,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Partial update: only the supplied keys change; set a key to null to clear
        it. Pass expected_version from your last read so a concurrent change is
        detected instead of silently overwritten. On a version_conflict error,
        re-read the record with get_record, merge the changed_since_your_version
        fields, and retry with the new version, or pass force=true to overwrite
        deliberately. Relation fields are not values: use link_records."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx, agent)
            updated = adapter.services.records.update_record(
                actor,
                record,
                values,
                expected_version=expected_version,
                force=force,
            )
            return record_response_doc(adapter.services, actor, updated)

        return adapter.call(run)

    @catalog.tool("write")
    def delete_record(
        record: RecordParam,
        force: Annotated[
            bool,
            Field(
                description=(
                    "Also remove every inbound link and proceed when other records link to "
                    "this one. Without it the delete is refused with relation_blocked "
                    "listing the blocking record keys."
                )
            ),
        ] = False,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Soft-delete a record (restore_record undoes it). Refused with a
        relation_blocked error naming the blocking record keys when other records
        link to it, unless force=true, in which case those links are removed and
        audited."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx, agent)
            deleted = adapter.services.records.delete_record(actor, record, force=force)
            return record_response_doc(adapter.services, actor, deleted)

        return adapter.call(run)

    @catalog.tool("write")
    def restore_record(
        record: RecordParam,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Restore a soft-deleted record. Fails with validation_failed if the record
        is not deleted or a live record now holds one of its unique values."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx, agent)
            restored = adapter.services.records.restore_record(actor, record)
            return record_response_doc(adapter.services, actor, restored)

        return adapter.call(run)

    @catalog.tool("write")
    def bulk_update_records(
        object_type: ObjectTypeParam,
        filter: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Filter tree selecting the records to patch, in the query_records "
                    "grammar. Pass {} to select every live record of the type (be sure "
                    "you mean that)."
                )
            ),
        ],
        values: ValuesParam,
        dry_run: Annotated[
            bool,
            Field(
                description=(
                    "When true, write nothing and return affected_count plus up to twenty "
                    "sample_keys. Do this first."
                )
            ),
        ] = False,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Apply one values patch to every live record matching a filter. Dry-run
        first: call with dry_run=true, check affected_count and sample_keys, and only
        then call again with dry_run=false. A live run has no per-record version
        check and runs as one transaction, so a validation failure on any record
        rolls back the whole batch."""

        def run() -> dict[str, Any]:
            result = adapter.services.records.bulk_update(
                adapter.actor(ctx, agent),
                object_type,
                values,
                filter=filter,
                dry_run=dry_run,
            )
            return bulk_update_doc(result)

        return adapter.call(run)

    @catalog.tool("write")
    def link_records(
        from_record: RecordParam,
        field_key: FieldKeyParam,
        to_records: ToRecordsParam,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Link a record to one or more target records through a relation field of
        its type. Targets must be of the relation's target type; a cardinality-one
        field holds a single link. The inverse link on the target is maintained
        automatically."""

        def run() -> dict[str, Any]:
            adapter.services.records.link_records(
                adapter.actor(ctx, agent), from_record, field_key, to_records
            )
            return {"field_key": field_key, "linked": to_records}

        return adapter.call(run)

    @catalog.tool("write")
    def unlink_records(
        from_record: RecordParam,
        field_key: FieldKeyParam,
        to_records: ToRecordsParam,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Remove links from a record's relation field to the given targets. The
        inverse links are removed too. Fails with not_found if a link does not
        exist."""

        def run() -> dict[str, Any]:
            count = adapter.services.records.unlink_records(
                adapter.actor(ctx, agent), from_record, field_key, to_records
            )
            return {"field_key": field_key, "unlinked_count": count}

        return adapter.call(run)

    @catalog.tool("write")
    def add_comment(
        record: RecordParam,
        body: BodyParam,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Add a Markdown comment to a record. The comment is attributed to the
        token's principal and to the agent label, and it updates the record's
        comment_count and last_comment_at so it is filterable."""

        def run() -> dict[str, Any]:
            comment = adapter.services.comments.add_comment(adapter.actor(ctx, agent), record, body)
            return comment_doc(comment)

        return adapter.call(run)

    @catalog.tool("write")
    def update_comment(
        comment_id: CommentIdParam,
        body: BodyParam,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Edit a comment's body. Only the comment's author may edit it; the prior
        body is retained in the audit trail and the comment is marked edited."""

        def run() -> dict[str, Any]:
            comment = adapter.services.comments.update_comment(
                adapter.actor(ctx, agent), comment_id, body
            )
            return comment_doc(comment)

        return adapter.call(run)

    @catalog.tool("write")
    def delete_comment(
        comment_id: CommentIdParam,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Soft-delete a comment. The author may delete their own comments;
        administrators may delete any. The prior body is retained in the audit
        trail."""

        def run() -> dict[str, Any]:
            comment = adapter.services.comments.delete_comment(
                adapter.actor(ctx, agent), comment_id
            )
            return comment_doc(comment)

        return adapter.call(run)

    @catalog.tool("write")
    def create_text_attachment(
        filename: Annotated[
            str,
            Field(
                description=(
                    "Filename to store, including its extension (e.g. 'summary.md'). "
                    "Shown to humans in the UI and used as the download filename."
                )
            ),
        ],
        text: Annotated[
            str,
            Field(
                description=(
                    "The document's full text, stored verbatim as UTF-8. This is the "
                    "only payload allowed to travel inside a tool call: it is text you "
                    "wrote, so you were emitting it anyway. Never base64-encode a file "
                    "into this parameter. " + _BINARY_ROUTE
                )
            ),
        ],
        content_type: Annotated[
            str | None,
            Field(
                description=(
                    "One of: " + _ALLOWED_TEXT_TYPES + ". Defaults to text/markdown. "
                    "Anything else is validation_failed. " + _BINARY_ROUTE
                )
            ),
        ] = None,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Store a text document you wrote as an attachment, and get back its id.

        Use it for notes, summaries, reports, CSV extracts and JSON you produced.
        Returns the attachment document, including a download_url and a resource link
        you can read back with resources/read. The id is not attached to anything yet:
        put it on a record's attachment field with update_record, composing the new
        array from the ids already stored on that field so you do not drop one you
        cannot see. For any file you did not author as text, upload it to
        /api/v1/attachments as multipart/form-data instead."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx, agent)
            row = adapter.services.attachments.upload_text(
                actor, filename, content_type or "text/markdown", text
            )
            return attachment_doc(row, adapter.services.attachments.download_url(row.id))

        return adapter.call(run, lambda payload: [attachment_link(payload)])

    @catalog.tool("write")
    def create_attachment_upload(
        filename: Annotated[
            str,
            Field(
                description=(
                    "The name to store the file under, including its extension (e.g. "
                    "'quarterly.pdf'). The ticket is bound to this name and the stored "
                    "attachment takes it, so the filename in your multipart request is "
                    "ignored -- point the upload at whatever local path holds the bytes. "
                    "One name, no directory separators."
                )
            ),
        ],
        content_type: Annotated[
            str | None,
            Field(
                description=(
                    "The file's MIME type (e.g. 'application/pdf'). Bound to the ticket "
                    "in the same way and authoritative over the request's. Defaults to "
                    "application/octet-stream, which every client can still download."
                )
            ),
        ] = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Get a ready-to-use URL and credential for uploading one file over HTTP.

        Use this for any file you did not write as text yourself -- a PDF, an image, a
        spreadsheet, an archive. Bytes never travel inside a tool call in either
        direction: base64 costs about one token per raw byte, so a 100 KB file is
        100,000 output tokens, no model can emit much more than 130 KB that way, and a
        long base64 run drifts silently -- the server would store a corrupt file under a
        perfectly valid sha256. So you send the bytes over HTTP and only handles ride
        through tools.

        Returns upload_url, method, field, an authorization header value, expires_at,
        max_bytes, and a curl line you complete with your local path. The credential is
        **single use** and expires in minutes: mint it when you are ready to send, do not
        cache it, and mint a fresh one if a request fails. It is accepted on the upload
        route and nowhere else, and it can do nothing your own token cannot.

        The response to that POST carries an id. The id is attached to nothing until you
        write it onto a record: use update_record on the record's attachment field, and
        build the new array from the ids already stored there (get_record's data), not
        from the attachments you can see resolved -- an attachment you are not allowed to
        read is omitted from what you see, and rebuilding from what rendered would
        destroy an id you cannot see.

        Refused when this deployment has no GW_BASE_URL configured, because then it
        cannot tell you an absolute URL and a relative one is useless to you."""

        def run() -> dict[str, Any]:
            return adapter.services.attachments.create_upload_ticket(
                adapter.actor(ctx), filename, content_type
            )

        return adapter.call(run)
