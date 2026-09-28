"""Field type catalog: validation, per-type operators, and the coercion matrix.

Normative sources: docs/DATA_MODEL.md section 4 (types, storage shapes, coercion rules)
and docs/MCP_TOOLS.md section 4 (operator matrix, pseudo-fields). This module is pure:
it never touches the database, so the schema and record services can share it.
"""

from __future__ import annotations

import math
import re
from typing import Any

from glosswork.errors import ValidationFailedError
from glosswork.timeutil import format_date, format_datetime, parse_date, parse_datetime

FIELD_TYPES = frozenset(
    {
        "short_text",
        "long_text",
        "integer",
        "decimal",
        "boolean",
        "date",
        "datetime",
        "single_select",
        "multi_select",
        "user_ref",
        "relation",
        "url",
        "attachment",
    }
)

# DD-23: field types that can never label a record for a human. `relation` values do not
# live in ``records.data`` at all; an `attachment`'s value is a list of opaque blob ids; and
# a `user_ref`'s is a raw principal UUID on the wire (names arrive as a per-document
# sidecar, which a search hit's title and a relation `display` never carry). Everything else
# is eligible, `date` and `integer` included, because some object types are most
# identifiable by one.
_DISPLAY_INELIGIBLE_TYPES = frozenset({"relation", "attachment", "user_ref"})


def is_display_eligible(field_type: str) -> bool:
    """Whether a field of this type may be an object type's display field.

    The only implementation of the rule. ``FieldDoc.display_eligible`` puts its answer on
    the wire so the frontend reads it rather than re-stating the list.
    """
    return field_type not in _DISPLAY_INELIGIBLE_TYPES


# Machine identifiers: object type keys, field keys, enum option values.
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
KEY_PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")

_NULL_OPS = ("is_null", "is_not_null")
_TEXT_OPS = ("eq", "neq", "contains", "not_contains", "starts_with", "ends_with", "in")
_NUMBER_OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "between", "in")
_DATE_OPS = ("eq", "neq", "gt", "gte", "lt", "lte", "between")

# docs/MCP_TOOLS.md section 4: operators by field type. Every type also gets the
# null checks from the "all types" row.
_TYPE_OPS: dict[str, tuple[str, ...]] = {
    "short_text": _TEXT_OPS,
    "long_text": _TEXT_OPS,
    "url": _TEXT_OPS,
    "integer": _NUMBER_OPS,
    "decimal": _NUMBER_OPS,
    "boolean": ("eq",),
    "date": _DATE_OPS,
    "datetime": _DATE_OPS,
    "single_select": ("eq", "neq", "in", "not_in"),
    "multi_select": ("has_any", "has_all", "has_none", "is_empty", "is_not_empty"),
    "user_ref": ("eq", "neq", "in"),
    "relation": ("linked_to", "linked_to_any", "has_links", "has_no_links"),
    "attachment": ("is_empty", "is_not_empty"),
}

# System pseudo-fields, queryable on every object type (FR-R7, FR-C8), mapped to the
# records column that backs them and the field type whose operators they take.
PSEUDO_FIELDS: dict[str, tuple[str, str]] = {
    "key": ("key", "short_text"),
    "created_at": ("created_at", "datetime"),
    "updated_at": ("updated_at", "datetime"),
    "created_by": ("created_by", "user_ref"),
    "updated_by": ("updated_by", "user_ref"),
    "deleted_at": ("deleted_at", "datetime"),
    "comment_count": ("comment_count", "integer"),
    "last_comment_at": ("last_comment_at", "datetime"),
}

# Agent-facing meaning of each pseudo-field, served by describe_object_type and
# describe_capabilities (docs/MCP_TOOLS.md section 4, "System pseudo-fields").
PSEUDO_FIELD_DESCRIPTIONS: dict[str, str] = {
    "key": (
        "Human-readable record key such as INIT-014; also accepted wherever a record is referenced."
    ),
    "created_at": "UTC timestamp of record creation.",
    "updated_at": "UTC timestamp of the last value change (comments do not bump it).",
    "created_by": "Principal id of the creator; '@me' resolves to the calling principal.",
    "updated_by": "Principal id of the last value change; '@me' resolves to the calling principal.",
    "deleted_at": "UTC timestamp of soft deletion; only meaningful with include_deleted: true.",
    "comment_count": "Number of live comments, so 'records with no discussion' is one filter.",
    "last_comment_at": "UTC timestamp of the newest live comment, or null when there is none.",
}

# Types whose fields auto-enable is_indexed (docs/DATA_MODEL.md section 5).
AUTO_INDEXED_TYPES = frozenset({"single_select", "date", "datetime", "user_ref"})

# Types that never get a per-field index on ``records``, whatever their flags say. Their
# values are not in ``records.data`` at all: a ``relation``'s live in ``record_links``
# and an ``attachment``'s are opaque ids joined through ``record_attachments``, so an
# expression index over ``json_extract(data, '$.<key>')`` would index nothing.
#
# The flags lie about them and always have. ``add_field`` sets ``is_indexed`` from the
# spec, ``AUTO_INDEXED_TYPES`` or ``unique`` with no type guard, so such a field can
# carry ``is_indexed = 1`` and have no index; an index path that returns early for these
# two types keeps the discrepancy invisible. The declarative reconciler reads the flags,
# so the exclusion has to be stated somewhere it can be read once, and this is it.
NON_INDEXABLE_TYPES = frozenset({"relation", "attachment"})

# Types whose equality filters inherently match a large fraction of an object type's
# records, and which therefore need a sort-composite index to page efficiently
# (``sqlexpr.sort_index_ddl``).
#
# This is the bound on how many composites exist, and it is a real one: pairing every
# indexed field with every other would be quadratic in the number of indexed fields,
# while pairing only these with the rest is linear in the number of sortable fields.
# The measurement behind it is that a filtered page costs about 1.1 microseconds per
# row *matched*, so a selective filter never needed the composite in the first place --
# a ``user_ref`` filter matching 2,375 of 90,000 rows already returns in 4 ms, against
# 39 ms for a ``single_select`` filter matching 35,961.
#
# ``boolean`` is here for the same reason as ``single_select`` even though nothing in
# the seeded corpus exercised it: two distinct values over a whole object type is the
# least selective filter this product can express.
LOW_CARDINALITY_TYPES = frozenset({"single_select", "boolean"})

# Types that can serve as the second column of a sort composite. Sorting is defined for
# every scalar field; ``relation`` and ``attachment`` hold no scalar in ``data`` (their
# values live in ``record_links`` and ``attachments``), and ``multi_select`` holds an
# array, which has no meaningful ordering.
SORT_COMPOSITE_TYPES = frozenset(FIELD_TYPES) - {"relation", "attachment", "multi_select"}

CONFIG_KEYS: dict[str, frozenset[str]] = {
    "short_text": frozenset({"max_length"}),
    "long_text": frozenset({"format"}),
    "integer": frozenset({"min", "max"}),
    "decimal": frozenset({"precision", "scale"}),
    "boolean": frozenset(),
    "date": frozenset(),
    "datetime": frozenset(),
    "single_select": frozenset({"options"}),
    "multi_select": frozenset({"options"}),
    "user_ref": frozenset({"allow_service_accounts"}),
    "relation": frozenset({"target_type_key", "cardinality", "inverse_field_key"}),
    "url": frozenset(),
    "attachment": frozenset({"max_files", "max_bytes"}),
}

_URL_PATTERN = re.compile(r"^https?://[^\s]+$")
_INT_PATTERN = re.compile(r"^[+-]?\d+$")


def operators_for(field_type: str) -> list[str]:
    """All operators legal for a field type, null checks included."""
    return list(_TYPE_OPS[field_type]) + list(_NULL_OPS)


def require_description(description: Any, what: str) -> str:
    """Enforce the non-negotiable: descriptions are agent-facing and required."""
    if not isinstance(description, str) or not description.strip():
        raise ValidationFailedError(
            f"{what} requires a non-empty description. Descriptions are how agents "
            "interpret the schema: write what the value means and when to use it, "
            "not just a restated name."
        )
    return description.strip()


def validate_key(key: Any, what: str) -> str:
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        raise ValidationFailedError(
            f"{what} key {key!r} is invalid: keys are lowercase snake_case "
            "(pattern ^[a-z][a-z0-9_]*$, max 63 chars) and immutable after creation."
        )
    return key


def validate_field_key(key: Any, what: str) -> str:
    """A field key: a valid key that is not one of the system pseudo-fields (DD-20).

    Its own entry point rather than a branch inside ``validate_key`` on ``what``.
    ``what`` is free-form English that flows into the message, so a caller
    writing ``f"Field {key}"`` instead of ``"Field"`` would disable the rule in
    silence. Object type keys keep ``validate_key``: a type named ``key`` is odd but
    harmless, and it shares no namespace with a filter's field names.

    The reserved set is read from ``PSEUDO_FIELDS`` and is never a second list.
    """
    field_key = validate_key(key, what)
    if field_key in PSEUDO_FIELDS:
        raise ValidationFailedError(
            f"{what} {field_key!r} is reserved. These are system fields queryable on "
            f"every object type (FR-R7): {', '.join(sorted(PSEUDO_FIELDS))}. A field of "
            "the same name would shadow the system one in filters and sorts, so the "
            "same query would mean two different things depending on where it was "
            f"asked. Choose another key -- a suffixed one such as {field_key}_name is "
            "accepted."
        )
    return field_key


def validate_key_prefix(prefix: Any) -> str:
    if not isinstance(prefix, str) or not KEY_PREFIX_PATTERN.match(prefix):
        raise ValidationFailedError(
            f"key_prefix {prefix!r} is invalid: 2-10 uppercase letters/digits starting "
            "with a letter, e.g. 'INIT'. It prefixes human-readable record keys like "
            "INIT-014."
        )
    return prefix


def validate_options(options: Any, field_key: str) -> list[dict[str, Any]]:
    """Validate select options: {value, label, description, color?, position?}."""
    if not isinstance(options, list) or not options:
        raise ValidationFailedError(
            f"Select field {field_key!r} requires config.options: a non-empty list of "
            "{value, label, description} objects.",
            field_key,
        )
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for i, opt in enumerate(options):
        if not isinstance(opt, dict):
            raise ValidationFailedError(
                f"Option {i} of field {field_key!r} must be an object.", field_key
            )
        unknown = set(opt) - {"value", "label", "description", "color", "position"}
        if unknown:
            raise ValidationFailedError(
                f"Option {i} of field {field_key!r} has unknown keys: {sorted(unknown)}.",
                field_key,
            )
        value = opt.get("value")
        if not isinstance(value, str) or not value.strip():
            raise ValidationFailedError(
                f"Option {i} of field {field_key!r} requires a non-empty string value.",
                field_key,
            )
        if value in seen:
            raise ValidationFailedError(
                f"Duplicate option value {value!r} on field {field_key!r}.", field_key
            )
        seen.add(value)
        label = opt.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValidationFailedError(
                f"Option {value!r} of field {field_key!r} requires a non-empty label.",
                field_key,
            )
        description = require_description(
            opt.get("description"), f"Enum option {value!r} on field {field_key!r}"
        )
        normalized.append(
            {
                "value": value,
                "label": label,
                "description": description,
                "color": opt.get("color"),
                "position": opt.get("position", i),
            }
        )
    return normalized


def validate_config(field_type: str, config: dict[str, Any], field_key: str) -> dict[str, Any]:
    """Validate and normalize a field's type-specific config."""
    if field_type not in FIELD_TYPES:
        raise ValidationFailedError(
            f"Unknown field type {field_type!r}. Supported types: "
            f"{', '.join(sorted(FIELD_TYPES))}.",
            field_key,
        )
    allowed = CONFIG_KEYS[field_type]
    unknown = set(config) - allowed
    if unknown:
        raise ValidationFailedError(
            f"Field {field_key!r} of type {field_type!r} does not accept config keys "
            f"{sorted(unknown)}. Allowed: {sorted(allowed) or '(none)'}.",
            field_key,
        )
    config = dict(config)
    if field_type in ("single_select", "multi_select"):
        config["options"] = validate_options(config.get("options"), field_key)
    if field_type == "long_text":
        fmt = config.setdefault("format", "markdown")
        if fmt not in ("markdown", "plain"):
            raise ValidationFailedError(
                f"long_text format must be 'markdown' or 'plain', got {fmt!r}.", field_key
            )
    if field_type == "relation":
        target = config.get("target_type_key")
        if not isinstance(target, str) or not target:
            raise ValidationFailedError(
                f"Relation field {field_key!r} requires config.target_type_key.", field_key
            )
        cardinality = config.get("cardinality")
        if cardinality not in ("one", "many"):
            raise ValidationFailedError(
                f"Relation field {field_key!r} requires config.cardinality of 'one' or "
                f"'many', got {cardinality!r}.",
                field_key,
            )
        inverse = config.get("inverse_field_key")
        if inverse is not None:
            validate_field_key(inverse, "inverse_field_key")
    return config


def option_values(config: dict[str, Any]) -> list[str]:
    return [str(o["value"]) for o in config.get("options", [])]


def validate_value(field_type: str, config: dict[str, Any], field_key: str, value: Any) -> Any:
    """Validate one stored value against its field definition. Returns the normalized value.

    ``None`` is never a stored value: absent optional values omit the key entirely
    (docs/DATA_MODEL.md section 4), so callers strip Nones before validation.
    """
    if field_type == "relation":
        raise ValidationFailedError(
            f"Field {field_key!r} is a relation; its values live in links, not in record "
            "data. Use link_records/unlink_records instead.",
            field_key,
        )
    if field_type in ("short_text", "long_text", "url"):
        if not isinstance(value, str):
            raise ValidationFailedError(
                f"Field {field_key!r} ({field_type}) requires a string, got "
                f"{type(value).__name__}.",
                field_key,
            )
        if field_type == "short_text":
            max_length = config.get("max_length")
            if max_length is not None and len(value) > int(max_length):
                raise ValidationFailedError(
                    f"Field {field_key!r} allows at most {max_length} characters; got "
                    f"{len(value)}.",
                    field_key,
                )
        if field_type == "url" and not _URL_PATTERN.match(value):
            raise ValidationFailedError(
                f"Field {field_key!r} requires an http(s) URL, got {value!r}.", field_key
            )
        return value
    if field_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationFailedError(
                f"Field {field_key!r} (integer) requires a JSON integer, got "
                f"{type(value).__name__}.",
                field_key,
            )
        for bound, op_name in ((config.get("min"), "min"), (config.get("max"), "max")):
            if bound is not None:
                if op_name == "min" and value < int(bound):
                    raise ValidationFailedError(
                        f"Field {field_key!r} minimum is {bound}; got {value}.", field_key
                    )
                if op_name == "max" and value > int(bound):
                    raise ValidationFailedError(
                        f"Field {field_key!r} maximum is {bound}; got {value}.", field_key
                    )
        return value
    if field_type == "decimal":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValidationFailedError(
                f"Field {field_key!r} (decimal) requires a JSON number, got "
                f"{type(value).__name__}.",
                field_key,
            )
        if isinstance(value, float) and not math.isfinite(value):
            raise ValidationFailedError(
                f"Field {field_key!r} (decimal) requires a finite number.", field_key
            )
        return value
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise ValidationFailedError(
                f"Field {field_key!r} (boolean) requires true or false, got "
                f"{type(value).__name__}.",
                field_key,
            )
        return value
    if field_type == "date":
        if not isinstance(value, str):
            raise ValidationFailedError(
                f"Field {field_key!r} (date) requires an ISO-8601 string YYYY-MM-DD.",
                field_key,
            )
        try:
            parse_date(value)
        except ValueError:
            raise ValidationFailedError(
                f"Field {field_key!r} (date) requires YYYY-MM-DD, got {value!r}.", field_key
            ) from None
        return value
    if field_type == "datetime":
        if not isinstance(value, str):
            raise ValidationFailedError(
                f"Field {field_key!r} (datetime) requires an ISO-8601 UTC string "
                "YYYY-MM-DDTHH:MM:SSZ.",
                field_key,
            )
        try:
            parse_datetime(value)
        except ValueError:
            raise ValidationFailedError(
                f"Field {field_key!r} (datetime) requires YYYY-MM-DDTHH:MM:SSZ (UTC), "
                f"got {value!r}.",
                field_key,
            ) from None
        return value
    if field_type == "single_select":
        valid = option_values(config)
        if not isinstance(value, str) or value not in valid:
            raise ValidationFailedError(
                f"Field {field_key!r} accepts one of: {', '.join(valid)}; got {value!r}.",
                field_key,
                valid_options=valid,
            )
        return value
    if field_type == "multi_select":
        valid = option_values(config)
        if not isinstance(value, list):
            raise ValidationFailedError(
                f"Field {field_key!r} (multi_select) requires an array of option values.",
                field_key,
            )
        bad = [v for v in value if not isinstance(v, str) or v not in valid]
        if bad:
            raise ValidationFailedError(
                f"Field {field_key!r} got invalid option values {bad!r}. Valid options: "
                f"{', '.join(valid)}.",
                field_key,
                valid_options=valid,
            )
        if len(set(value)) != len(value):
            raise ValidationFailedError(
                f"Field {field_key!r} (multi_select) contains duplicate values.", field_key
            )
        return value
    if field_type == "user_ref":
        if not isinstance(value, str) or not value:
            raise ValidationFailedError(
                f"Field {field_key!r} (user_ref) requires a principal id string.", field_key
            )
        return value
    if field_type == "attachment":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValidationFailedError(
                f"Field {field_key!r} (attachment) requires an array of attachment ids.",
                field_key,
            )
        max_files = config.get("max_files")
        if max_files is not None and len(value) > int(max_files):
            raise ValidationFailedError(
                f"Field {field_key!r} allows at most {max_files} attachments.", field_key
            )
        return value
    raise ValidationFailedError(f"Unknown field type {field_type!r}.", field_key)


# ---------------------------------------------------------------------------
# Coercion matrix for field type changes (docs/DATA_MODEL.md section 4, FR-S9)
# ---------------------------------------------------------------------------

_TEXTUAL = ("short_text", "long_text", "url")


def coerce_value(
    from_type: str,
    to_type: str,
    value: Any,
    to_config: dict[str, Any],
) -> tuple[bool, Any, str | None]:
    """Attempt to convert one stored value for a field type change.

    Returns ``(ok, new_value, reason)``. ``reason`` explains a failure for the
    per-row dry-run report (FR-S8). Callers handle absent values (key omitted)
    before calling; this function only sees present values.
    """
    if to_type in ("relation", "attachment"):
        return False, None, f"conversion to {to_type} is not supported; recreate the field"

    if from_type in _TEXTUAL and to_type in _TEXTUAL:
        if to_type == "short_text":
            max_length = to_config.get("max_length")
            if max_length is not None and len(str(value)) > int(max_length):
                return False, None, f"exceeds max_length {max_length}"
        if to_type == "url" and not _URL_PATTERN.match(str(value)):
            return False, None, "not a valid http(s) URL"
        return True, value, None

    if from_type in _TEXTUAL and to_type == "integer":
        if isinstance(value, str) and _INT_PATTERN.match(value.strip()):
            return True, int(value.strip()), None
        return False, None, f"{value!r} does not parse as an integer"
    if from_type in _TEXTUAL and to_type == "decimal":
        try:
            parsed = float(str(value).strip())
        except ValueError:
            return False, None, f"{value!r} does not parse as a number"
        if not math.isfinite(parsed):
            return False, None, f"{value!r} is not a finite number"
        return True, parsed, None
    if from_type in _TEXTUAL and to_type == "date":
        try:
            parse_date(str(value))
        except ValueError:
            return False, None, f"{value!r} does not parse as ISO-8601 YYYY-MM-DD"
        return True, str(value), None
    if from_type in _TEXTUAL and to_type == "datetime":
        try:
            parse_datetime(str(value))
        except ValueError:
            return False, None, f"{value!r} does not parse as ISO-8601 YYYY-MM-DDTHH:MM:SSZ"
        return True, str(value), None
    if from_type in _TEXTUAL and to_type == "boolean":
        lowered = str(value).strip().lower()
        if lowered in ("true", "false"):
            return True, lowered == "true", None
        return False, None, f"{value!r} is not 'true' or 'false'"
    if from_type in _TEXTUAL and to_type in ("single_select", "multi_select"):
        valid = option_values(to_config)
        if str(value) in valid:
            coerced = str(value) if to_type == "single_select" else [str(value)]
            return True, coerced, None
        return False, None, f"{value!r} is not an existing option"

    if from_type == "single_select" and to_type == "multi_select":
        return True, [value], None
    if from_type == "multi_select" and to_type == "single_select":
        if isinstance(value, list) and len(value) == 1:
            candidate = value[0]
            if candidate in option_values(to_config):
                return True, candidate, None
            return False, None, f"{candidate!r} is not an existing option"
        return False, None, f"has {len(value) if isinstance(value, list) else '?'} values"

    if to_type in ("short_text", "long_text"):
        rendered = _render_as_text(value)
        if to_type == "short_text":
            max_length = to_config.get("max_length")
            if max_length is not None and len(rendered) > int(max_length):
                return False, None, f"exceeds max_length {max_length}"
        return True, rendered, None

    if from_type == "integer" and to_type == "decimal":
        return True, value, None
    if from_type == "decimal" and to_type == "integer":
        if isinstance(value, int | float) and float(value).is_integer():
            return True, int(value), None
        return False, None, f"{value!r} is not a whole number"
    if from_type == "date" and to_type == "datetime":
        return True, f"{value}T00:00:00Z", None
    if from_type == "datetime" and to_type == "date":
        return True, str(value)[:10], None

    return False, None, f"conversion from {from_type} to {to_type} is not supported"


def _render_as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


__all__ = [
    "AUTO_INDEXED_TYPES",
    "NON_INDEXABLE_TYPES",
    "FIELD_TYPES",
    "KEY_PATTERN",
    "PSEUDO_FIELDS",
    "PSEUDO_FIELD_DESCRIPTIONS",
    "coerce_value",
    "format_date",
    "format_datetime",
    "operators_for",
    "option_values",
    "require_description",
    "validate_config",
    "validate_field_key",
    "validate_key",
    "validate_key_prefix",
    "validate_options",
    "validate_value",
]
