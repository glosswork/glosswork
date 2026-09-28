"""The deployment's capability catalog behind ``describe_capabilities``
(docs/MCP_TOOLS.md section 5.3): field types with their ``config`` keys, the
operator matrix, date tokens, and current limits, so an agent can design a schema
without guessing what the platform supports. Service-layer so both surfaces can
serve it (DD-3)."""

from __future__ import annotations

from typing import Any

from glosswork.datetokens import DATE_TOKEN_BASES, DATE_TOKEN_UNITS
from glosswork.fieldtypes import (
    AUTO_INDEXED_TYPES,
    CONFIG_KEYS,
    FIELD_TYPES,
    KEY_PATTERN,
    KEY_PREFIX_PATTERN,
    PSEUDO_FIELD_DESCRIPTIONS,
    PSEUDO_FIELDS,
    operators_for,
)
from glosswork.filters import MAX_FILTER_DEPTH
from glosswork.services.agent_labels import MAX_LABEL_LENGTH
from glosswork.services.attachments import (
    ATTACHMENT_URI_TEMPLATE,
    TEXT_ATTACHMENT_TYPES,
    UPLOAD_FIELD,
    UPLOAD_PATH,
)
from glosswork.services.changes import DEFAULT_LIMIT as CHANGES_DEFAULT_LIMIT
from glosswork.services.changes import MAX_LIMIT as CHANGES_MAX_LIMIT
from glosswork.services.comments import MAX_COMMENT_LIMIT
from glosswork.services.records import (
    BULK_UPDATE_MAX_MATCHES,
    BULK_UPDATE_SAMPLE_SIZE,
    DEFAULT_QUERY_LIMIT,
    MAX_HISTORY_LIMIT,
    MAX_QUERY_LIMIT,
    SAMPLE_VALUES_PER_FIELD,
)
from glosswork.services.schema import DEFAULT_PROPOSAL_LIMIT, MAX_PROPOSAL_LIMIT
from glosswork.services.search import (
    DEFAULT_MODE,
    DEFAULT_SEARCH_LIMIT,
    FILTER_RULE,
    MAX_QUERY_CHARS,
    MAX_SEARCH_LIMIT,
    MODES,
)
from glosswork.services.search_tuning import (
    CANDIDATE_MULTIPLIER,
    RRF_K,
    WIDEN_MULTIPLIER,
)

FIELD_TYPE_DESCRIPTIONS: dict[str, str] = {
    "short_text": "Single-line text; config.max_length caps its length.",
    "long_text": "Multi-line text; config.format may be 'markdown' or 'plain'.",
    "integer": "Whole number; config.min and config.max bound it.",
    "decimal": "Decimal number; config.precision and config.scale bound its digits.",
    "boolean": "true or false.",
    "date": "Calendar date as YYYY-MM-DD. Accepts date tokens in filters.",
    "datetime": "UTC timestamp as YYYY-MM-DDTHH:MM:SSZ. Accepts date tokens in filters.",
    "single_select": (
        "Exactly one of config.options; each option has value, label, and description."
    ),
    "multi_select": "Zero or more of config.options; each option has value, label, description.",
    "user_ref": (
        "A reference to one principal, stored as a principal id. Accepts a principal id, an "
        "email address, an exact display name, or '@me', on writes and in filters alike; "
        "find_principals looks names up. Validated against live principals on write, and an "
        "ambiguous display name is refused with the candidates rather than guessed. "
        "config.allow_service_accounts false rejects a service account. Responses carrying "
        "records also carry a 'principals' map resolving every id to a display name."
    ),
    "relation": (
        "Links to records of config.target_type_key with config.cardinality 'one' or 'many'; "
        "config.inverse_field_key names the reciprocal field on the target type. Relation "
        "values are managed with link_records/unlink_records, not record values."
    ),
    "url": "An http(s) URL.",
    "attachment": "A list of attachment ids; config.max_files and config.max_bytes bound it.",
}


def capabilities_document(
    semantic_enabled: bool,
    download_url_pattern: str,
    attachment_max_bytes: int | None,
    upload_url: str,
    upload_ticket_ttl_seconds: int,
) -> dict[str, Any]:
    """The three deployment-dependent values arrive as parameters; everything else is
    read from the services' own constants.

    ``semantic_enabled``: with ``GW_EMBEDDING_ENABLED=false`` an agent reading this
    block knows to use ``mode: keyword`` before it asks and is refused with
    ``feature_disabled`` (DD-34).

    ``download_url_pattern`` and ``attachment_max_bytes`` (DD-29) come from
    ``AttachmentService`` itself -- the pattern is that service's own ``download_url``
    over a literal ``{attachment_id}``, so it cannot describe a route the service does
    not serve. ``attachment_max_bytes`` is a **setting**, not a named constant, and it is
    published because an agent about to upload has to know the ceiling before it reads
    a file.
    """
    return {
        "search": {
            "modes": list(MODES),
            "default_mode": DEFAULT_MODE,
            "default_limit": DEFAULT_SEARCH_LIMIT,
            "max_limit": MAX_SEARCH_LIMIT,
            "max_query_chars": MAX_QUERY_CHARS,
            "semantic_enabled": semantic_enabled,
            "filter_rule": FILTER_RULE,
            "fusion": {
                "k": RRF_K,
                "candidate_multiplier": CANDIDATE_MULTIPLIER,
                "widen_multiplier": WIDEN_MULTIPLIER,
            },
        },
        "field_types": [
            {
                "type": field_type,
                "description": FIELD_TYPE_DESCRIPTIONS[field_type],
                "config_keys": sorted(CONFIG_KEYS[field_type]),
                "operators": operators_for(field_type),
                "auto_indexed": field_type in AUTO_INDEXED_TYPES,
            }
            for field_type in sorted(FIELD_TYPES)
        ],
        "system_fields": [
            {
                "key": key,
                "type": PSEUDO_FIELDS[key][1],
                "description": PSEUDO_FIELD_DESCRIPTIONS[key],
                "operators": operators_for(PSEUDO_FIELDS[key][1]),
            }
            for key in PSEUDO_FIELDS
        ],
        "filter_grammar": {
            "combinators": ["and", "or", "not"],
            "condition_shape": {"field": "<field key>", "op": "<operator>", "value": "<value>"},
            "between": "two-element array, inclusive on both ends",
            "empty_filter": "matches every live record",
        },
        "date_tokens": {
            "bases": [f"@{base}" for base in DATE_TOKEN_BASES],
            "offset_units": dict(DATE_TOKEN_UNITS),
            "examples": ["@today", "@today-7d", "@now-12h", "@start_of_month-1M"],
            "identity_token": "@me (user_ref comparisons resolve to the calling principal)",
        },
        "key_rules": {
            "object_type_and_field_keys": KEY_PATTERN.pattern,
            "key_prefix": KEY_PREFIX_PATTERN.pattern,
            # DD-20. An agent that reads the pattern and nothing else will
            # name a field ``created_by`` and take a refusal it could have avoided.
            # Read from PSEUDO_FIELDS, which is the one source of truth for the set.
            "reserved_field_keys": sorted(PSEUDO_FIELDS),
            "reserved_field_keys_note": (
                "These are the system pseudo-fields, queryable on every object type. A "
                "field key may not be one of them; an object type key may. Suffix "
                "instead: 'created_by_name' is accepted."
            ),
        },
        # DD-29. The whole file recipe in one block: where bytes go up, where they come
        # down, what an agent may write as text, and the ceiling on all of it. Every name in
        # it is something the caller can actually obtain (DD-16). The two a recipe would
        # otherwise name and not hand over are the credential -- the connection's bearer
        # belongs to the MCP client, and no part of the protocol gives the model its own --
        # and the origin, which the model never sees either. ``upload_url`` and
        # ``ticket_tool`` are those two, and ``credential`` says plainly that looking for
        # the bearer in tool output is a dead end, so an agent stops looking.
        "attachments": {
            "upload_route": f"POST {UPLOAD_PATH}",
            "upload_url": upload_url,
            "upload_field": UPLOAD_FIELD,
            "ticket_tool": "create_attachment_upload",
            "download_url_pattern": download_url_pattern,
            "resource_uri_template": ATTACHMENT_URI_TEMPLATE,
            "text_content_types": list(TEXT_ATTACHMENT_TYPES),
            "credential": (
                "The bearer token this connection authenticates with is held by your MCP "
                "client, not by this server, so no tool will ever return it and you "
                "cannot upload with it. Call create_attachment_upload instead: it returns "
                "an absolute upload_url and a ready Authorization header for one "
                "single-use, short-lived credential bound to one filename. If you already "
                "hold a personal access token of your own outside this connection, you "
                "may POST with that instead."
            ),
            "note": (
                "Attachment bytes never travel in a tool argument or a tool result. "
                "Upload a file with an HTTP POST to upload_url as multipart/form-data "
                "under upload_field, using a credential from ticket_tool, and put the "
                "returned id on a record's attachment field with update_record -- "
                "composing the new array from the ids already stored on that field, not "
                "from what you can see resolved, because an id you may not read is "
                "omitted from what you see and must not be dropped. Text you wrote "
                "yourself goes through create_text_attachment instead. Read bytes back "
                "with resources/read on resource_uri_template, or with an HTTP GET of "
                "download_url_pattern."
            ),
        },
        "limits": {
            "query_default_limit": DEFAULT_QUERY_LIMIT,
            # DD-18. Every cap an agent can hit is published here, because
            # docs/MCP_TOOLS.md and docs/AGENT_ONBOARDING.md both tell agents to read this block
            # before they start guessing at page sizes.
            "query_max_limit": MAX_QUERY_LIMIT,
            "history_max_limit": MAX_HISTORY_LIMIT,
            "comment_max_limit": MAX_COMMENT_LIMIT,
            "filter_max_depth": MAX_FILTER_DEPTH,
            "changes_default_limit": CHANGES_DEFAULT_LIMIT,
            "changes_max_limit": CHANGES_MAX_LIMIT,
            "bulk_update_max_matches": BULK_UPDATE_MAX_MATCHES,
            "bulk_update_sample_keys": BULK_UPDATE_SAMPLE_SIZE,
            "describe_samples_per_field": SAMPLE_VALUES_PER_FIELD,
            "agent_label_max_length": MAX_LABEL_LENGTH,
            # DD-18. The proposal list is bounded like every other list, and its cap is
            # published with the rest.
            "proposal_default_limit": DEFAULT_PROPOSAL_LIMIT,
            "proposal_max_limit": MAX_PROPOSAL_LIMIT,
            # GW_MAX_ATTACHMENT_BYTES. ``None`` only where a service is constructed
            # without the setting, which ``build_services`` never does.
            "attachment_max_bytes": attachment_max_bytes,
            # GW_UPLOAD_TICKET_TTL_SECONDS. How long a create_attachment_upload
            # credential lives, so an agent knows whether to mint before or after it
            # reads the file it is about to send.
            "upload_ticket_ttl_seconds": upload_ticket_ttl_seconds,
        },
    }
