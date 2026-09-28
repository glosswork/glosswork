"""Parameter annotations shared across the tool modules. Every parameter carries a
description written for an agent that has never seen this codebase (FR-M6); the
ones reused by several tools are defined once here so their wording cannot drift.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

AGENT_LABEL_DESCRIPTION = (
    "Agent label recorded on this call's audit rows, overriding the connection's "
    "X-Agent-Label header for this one call. Descriptive metadata only: the label is "
    "not a security boundary and grants nothing; authorization derives entirely from "
    "the bearer token's scope. Unknown labels are accepted and auto-registered."
)

ObjectTypeParam = Annotated[
    str,
    Field(
        description=(
            "Object type key, e.g. 'initiative'. Call list_object_types for the valid keys."
        )
    ),
]

RecordParam = Annotated[
    str,
    Field(
        description=(
            "The record's human key (e.g. 'INIT-014') or its UUID. Either form is accepted."
        )
    ),
]

AgentParam = Annotated[str | None, Field(description=AGENT_LABEL_DESCRIPTION)]

FilterParam = Annotated[
    dict[str, Any] | None,
    Field(
        description=(
            "Filter tree over the type's fields and the system pseudo-fields. A bare "
            "condition is {field, op, value}; combine with {and: [...]}, {or: [...]}, "
            "{not: {...}}, nested arbitrarily. Use only operators listed for the field by "
            "describe_object_type. Date values accept tokens such as '@today-7d'; user_ref "
            "values accept '@me'. Omit or pass {} to match every live record."
        )
    ),
]

CursorParam = Annotated[
    str | None,
    Field(
        description=(
            "Opaque pagination cursor: pass the next_cursor value from the previous "
            "response, unmodified, to fetch the next page. Omit for the first page."
        )
    ),
]


def limit_param(max_limit: int) -> Any:
    """A page-limit parameter whose **description** carries the bound.

    Deliberately **not** a Pydantic upper-bound constraint. Two reasons, and the second
    is the one that
    matters: such a bound refuses inside the SDK's ``_handle_call_tool`` coercion, which
    ``adapter.py`` leaves as the SDK's own message rather than the project envelope, so
    ``limit=1001`` would come back ``validation_failed`` on REST and an SDK schema error
    on MCP -- the opposite of what a change about uniform refusals is for. And the
    parameter is not shared by all three capped tools anyway: ``query_records`` declares
    its own inline annotation, so the cap that matters most would have gone undocumented.
    The service raises; this tells the agent the number before it asks.
    """
    return Annotated[
        int,
        Field(
            description=(
                f"Maximum number of items to return in this page, 1 to {max_limit}. "
                "Follow next_cursor for more."
            )
        ),
    ]


BodyParam = Annotated[
    str,
    Field(description="Comment body in Markdown. Must be non-empty."),
]

CommentIdParam = Annotated[
    str,
    Field(description="The comment's id, as returned by add_comment or list_comments."),
]

FieldKeyParam = Annotated[
    str,
    Field(
        description=("Field key on the object type, exactly as returned by describe_object_type.")
    ),
]

ValuesParam = Annotated[
    dict[str, Any],
    Field(
        description=(
            "Field values keyed by field key, as returned by describe_object_type. Select "
            "fields take option values (not labels); date fields take 'YYYY-MM-DD'; "
            "datetime fields take 'YYYY-MM-DDTHH:MM:SSZ'. Relation fields are not values: "
            "use link_records. Validation errors name the field and list valid options."
        )
    ),
]
