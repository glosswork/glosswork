"""Schema administration tools (docs/MCP_TOOLS.md section 5.3; scope ``admin``).

Invisible to ``read`` and ``write`` tokens (FR-M4). Destructive changes never apply
directly: they become proposals a human approves in the UI, and there is
deliberately no approve or reject tool at any scope (FR-S6). No logic here (DD-3).
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp.types import CallToolResult
from pydantic import Field

from glosswork.envelopes import (
    describe_object_type_doc,
    field_doc,
    object_type_grants_doc,
    proposal_created_doc,
    proposal_list_doc,
    update_field_doc,
)
from glosswork.mcp_server.adapter import McpAdapter
from glosswork.mcp_server.catalog import ToolCatalog
from glosswork.mcp_server.params import AgentParam, FieldKeyParam, ObjectTypeParam
from glosswork.serializers import grant_doc
from glosswork.services.schema import DEFAULT_PROPOSAL_LIMIT, MAX_PROPOSAL_LIMIT

DescriptionParam = Annotated[
    str,
    Field(
        description=(
            "Required, agent-facing description: what the thing means and when to use it, "
            "not a restated name. Empty descriptions are rejected because descriptions are "
            "how agents interpret the schema."
        )
    ),
]

PrincipalIdParam = Annotated[
    str,
    Field(
        description=(
            "The principal's UUID, exactly as returned by find_principals. Grants take an "
            "id and not a name or an email address: a display name can be ambiguous, and "
            "who may use an object type is not a question to answer by guessing. Call "
            "find_principals first if all you have is a name."
        )
    ),
]

GrantLevelParam = Annotated[
    str,
    Field(
        description=(
            "What this principal may do to this object type: 'read' to query its records, "
            "'write' to also create and change them, 'admin' to also change the type's "
            "schema and its grants, or 'none' as an explicit deny. 'none' is not the same "
            "as revoking the grant: it overrides the type's default_level and keeps "
            "overriding it if that default is later widened, whereas revoking returns the "
            "principal to whatever the default says."
        )
    ),
]

FieldConfigParam = Annotated[
    dict[str, Any] | None,
    Field(
        description=(
            "Type-specific configuration. For 'relation': target_type_key, cardinality "
            "('one' or 'many'), and optionally inverse_field_key. For 'single_select' and "
            "'multi_select': options, a list of {value, label, description}. Call "
            "describe_capabilities for every type's config keys."
        )
    ),
]


def register_admin_tools(catalog: ToolCatalog, adapter: McpAdapter) -> None:
    @catalog.tool("admin")
    def create_object_type(
        key: Annotated[
            str,
            Field(description=("Immutable machine key, lowercase snake_case (e.g. 'initiative').")),
        ],
        name: Annotated[str, Field(description="Singular display name (e.g. 'Initiative').")],
        name_plural: Annotated[str, Field(description="Plural display name.")],
        description: DescriptionParam,
        key_prefix: Annotated[
            str,
            Field(
                description=(
                    "Immutable uppercase prefix for record keys, 2 to 10 characters "
                    "(e.g. 'INIT' yields INIT-001)."
                )
            ),
        ],
        fields: Annotated[
            list[dict[str, Any]] | None,
            Field(
                description=(
                    "Optional initial fields, each {key, name, type, description, config?, "
                    "required?, unique?, indexed?, embed?, default?} exactly as add_field "
                    "takes them. Relation fields may reference this new type by key."
                )
            ),
        ] = None,
        display_field_key: Annotated[
            str | None,
            Field(
                description=(
                    "Which field's value labels a record for a human, in search hit titles, "
                    "compact projections, and beside every link to it. Must name one of the "
                    "fields above, and not a relation, attachment or user_ref one. Defaults "
                    "to the first eligible field; describe_object_type reports the resolved "
                    "answer as effective_display_field_key."
                )
            ),
        ] = None,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Create an object type, optionally with its initial fields in one call.
        Returns the full describe document. Descriptions are required on the type
        and on every field: they are what agents read to interpret the schema."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx, agent)
            adapter.services.schema.create_object_type(
                actor,
                key=key,
                name=name,
                name_plural=name_plural,
                description=description,
                key_prefix=key_prefix,
                fields=fields,
                display_field_key=display_field_key,
            )
            return describe_object_type_doc(adapter.services, actor, key)

        return adapter.call(run)

    @catalog.tool("admin")
    def update_object_type(
        object_type: ObjectTypeParam,
        changes: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Keys to change among name, name_plural, description, icon, "
                    "default_level (what a principal with no grant row may do to this "
                    "type), and display_field_key (which field labels a record; null "
                    "returns it to the first eligible field). 'key' and 'key_prefix' are "
                    "immutable after creation and are rejected."
                )
            ),
        ],
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Rename or re-describe an object type. Only names, descriptions, and the
        icon change; key and key_prefix are immutable. Returns the describe
        document."""

        def run() -> dict[str, Any]:
            actor = adapter.actor(ctx, agent)
            adapter.services.schema.update_object_type(actor, object_type, changes)
            return describe_object_type_doc(adapter.services, actor, object_type)

        return adapter.call(run)

    @catalog.tool("admin")
    def add_field(
        object_type: ObjectTypeParam,
        key: Annotated[str, Field(description="Immutable field key, lowercase snake_case.")],
        name: Annotated[str, Field(description="Display name of the field.")],
        type: Annotated[
            str,
            Field(
                description=(
                    "Field type: one of the thirteen types listed by "
                    "describe_capabilities (short_text, long_text, integer, decimal, "
                    "boolean, date, datetime, single_select, multi_select, user_ref, "
                    "relation, url, attachment)."
                )
            ),
        ],
        description: DescriptionParam,
        config: FieldConfigParam = None,
        required: Annotated[
            bool | None, Field(description="Whether a value is required on every record.")
        ] = None,
        unique: Annotated[
            bool | None, Field(description="Whether values must be unique across the type.")
        ] = None,
        indexed: Annotated[
            bool | None,
            Field(
                description=(
                    "Whether to index the field for filtering and sorting. Select, date, "
                    "datetime, and user_ref fields are indexed automatically."
                )
            ),
        ] = None,
        embed: Annotated[
            bool | None,
            Field(description="Whether the field's text participates in semantic search."),
        ] = None,
        default: Annotated[
            Any | None, Field(description="Default value applied when a record omits it.")
        ] = None,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Add a field to an object type. Additive, so it applies immediately.
        Returns status 'applied' and the field document with its operators."""

        def run() -> dict[str, Any]:
            spec: dict[str, Any] = {
                "key": key,
                "name": name,
                "type": type,
                "description": description,
            }
            optional: dict[str, Any] = {
                "config": config,
                "required": required,
                "unique": unique,
                "indexed": indexed,
                "embed": embed,
                "default": default,
            }
            spec.update({k: v for k, v in optional.items() if v is not None})
            field = adapter.services.schema.add_field(adapter.actor(ctx, agent), object_type, spec)
            return {"status": "applied", "field": field_doc(field)}

        return adapter.call(run)

    @catalog.tool("admin")
    def update_field(
        object_type: ObjectTypeParam,
        field_key: FieldKeyParam,
        changes: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Keys to change among name, description, type, config, required, "
                    "unique, indexed, embed, default. The field key itself is immutable."
                )
            ),
        ],
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Change a field. Additive changes (renaming, editing the description,
        adding select options, relaxing required, toggling indexed or embed) apply
        immediately and return status 'applied'. Destructive changes (changing the
        type, removing an in-use option, tightening a constraint existing data
        violates) are not applied: they return status 'pending_human_approval' with a
        proposal_id and computed impact, and an administrator must approve the
        proposal in the UI. The response always says which path was taken."""

        def run() -> dict[str, Any]:
            result = adapter.services.schema.update_field(
                adapter.actor(ctx, agent), object_type, field_key, changes
            )
            return update_field_doc(result)

        return adapter.call(run)

    @catalog.tool("admin")
    def propose_schema_change(
        change_type: Annotated[
            str,
            Field(
                description=(
                    "One of delete_field, delete_object_type, change_field_type, "
                    "remove_enum_option, tighten_constraint."
                )
            ),
        ],
        object_type: ObjectTypeParam,
        field_key: Annotated[
            str | None,
            Field(
                description=(
                    "The field concerned; required for every change type except delete_object_type."
                )
            ),
        ] = None,
        payload: Annotated[
            dict[str, Any] | None,
            Field(
                description=(
                    "Change-specific details, e.g. {'type': 'integer'} for "
                    "change_field_type or {'value': 'old'} for remove_enum_option."
                )
            ),
        ] = None,
        reason: Annotated[
            str | None,
            Field(description="Why the change is needed; shown to the approving human."),
        ] = None,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Propose a destructive schema change (this is the only path for
        delete_field and delete_object_type, which never apply directly). Returns
        the proposal id, status 'pending_human_approval', and the computed impact
        (affected records, non-empty values, samples). Nothing is applied until an
        administrator approves it in the UI; poll list_schema_proposals to learn
        the outcome. Agents cannot approve proposals."""

        def run() -> dict[str, Any]:
            proposal = adapter.services.schema.propose_schema_change(
                adapter.actor(ctx, agent),
                change_type,
                object_type,
                field_key=field_key,
                payload=payload,
                reason=reason,
            )
            return proposal_created_doc(proposal)

        return adapter.call(run)

    @catalog.tool("admin")
    def list_schema_proposals(
        status: Annotated[
            str | None,
            Field(
                description=(
                    "Filter by status: 'pending', 'approved', or 'rejected'. Omit for all."
                )
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                description=(
                    "How many proposals to return, newest first. Defaults to "
                    f"{DEFAULT_PROPOSAL_LIMIT}; the ceiling is {MAX_PROPOSAL_LIMIT}. "
                    "Both are published in describe_capabilities under 'limits'."
                )
            ),
        ] = DEFAULT_PROPOSAL_LIMIT,
        cursor: Annotated[
            str | None,
            Field(
                description=("Opaque cursor from a previous call's next_cursor, for the next page.")
            ),
        ] = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """List schema-change proposals with their status, impact, and decision, so
        you can report whether a change you proposed has been approved. Each proposal
        carries a 'target' naming the object type and field it is about in words, and
        the response carries 'principals' and 'agent_labels' maps naming whoever raised
        it. Newest first; follow next_cursor for older ones."""

        def run() -> dict[str, Any]:
            page = adapter.services.schema.list_proposals_page(
                adapter.actor(ctx), status, limit, cursor
            )
            return proposal_list_doc(page)

        return adapter.call(run)

    # ------------------------------------------------------- grants (DD-11)
    #
    # `admin` scope, matching the three REST routes' `require_scope("admin")`. The
    # per-type authority is determined in the service, which is what lets a principal
    # holding `admin` on one object type administer that type's grants without being a
    # system administrator. `insufficient_scope` and `forbidden` are the two refusals,
    # and they mean different things (DD-11): the first is the credential, the second
    # the grant.

    @catalog.tool("admin")
    def list_object_type_grants(object_type: ObjectTypeParam, ctx: Context) -> CallToolResult:
        """List who has been granted explicit access to an object type, and what the
        type's default access is for everyone else. Returns the grant rows plus a
        `principals` map naming every principal id they mention, so you can report who
        holds what without looking ids up. Requires `admin` on the object type."""

        def run() -> dict[str, Any]:
            return object_type_grants_doc(
                adapter.services.access.list_grants_document(adapter.actor(ctx), object_type)
            )

        return adapter.call(run)

    @catalog.tool("admin")
    def set_object_type_grant(
        object_type: ObjectTypeParam,
        principal_id: PrincipalIdParam,
        level: GrantLevelParam,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Grant a principal a level of access to one object type, or change the level
        it already has. Creates the row if there is none and overwrites it if there is,
        so it is safe to call twice. Requires `admin` on the object type."""

        def run() -> dict[str, Any]:
            return grant_doc(
                adapter.services.access.grant(
                    adapter.actor(ctx, agent), object_type, principal_id, level
                )
            )

        return adapter.call(run)

    @catalog.tool("admin")
    def revoke_object_type_grant(
        object_type: ObjectTypeParam,
        principal_id: PrincipalIdParam,
        agent: AgentParam = None,
        *,
        ctx: Context,
    ) -> CallToolResult:
        """Remove a principal's grant row on an object type, returning it to the type's
        `default_level`. This is **not** the same as granting 'none': revoking restores
        whatever the default says, now and after any later change to it, while 'none' is
        a deny that survives the default being widened. Requires `admin` on the object
        type."""

        def run() -> dict[str, str]:
            adapter.services.access.revoke(adapter.actor(ctx, agent), object_type, principal_id)
            return {"status": "revoked"}

        return adapter.call(run)
