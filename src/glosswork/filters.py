"""Filter tree parsing and validation (FR-R5, docs/MCP_TOOLS.md section 4).

Turns the JSON filter grammar into a validated AST with all tokens resolved:
``@me`` to the calling principal (FR-R9), date tokens to stored-format literals
(FR-R8), and relation record references to record ids. The SQL emission for the
AST lives in :mod:`glosswork.compiler`.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from glosswork.datetokens import is_date_token, resolve_date_token
from glosswork.errors import (
    InvalidOperatorError,
    UnknownFieldError,
    ValidationFailedError,
)
from glosswork.fieldtypes import PSEUDO_FIELDS, operators_for, option_values
from glosswork.repositories.models import FieldDef

# How deep a filter tree may nest (DD-18).
#
# **The unit is filter nodes, and that matters.** Two walkers descend a raw filter and
# they do not count the same thing. ``_parse_node`` here costs one Python frame per
# filter node. ``search._filter_field_keys`` recurses through ``raw.values()`` and then
# through list items, so an ``and``/``or`` node costs it two frames where this one costs
# one, and it descends into filter *values*, which ``_parse_node`` never does. A single
# ``> 32`` written into both would have refused, on the multi-type search path, a filter
# the query path accepts at depth 32 -- two surfaces disagreeing about the same tree.
# Counting nodes is what makes them agree.
#
# 32 is far past anything a human or an agent composes -- the deepest filter in the
# fixtures and the golden set is 4 -- and far below where recursion becomes the problem
# (``_parse_node`` blew at roughly 991 frames).
MAX_FILTER_DEPTH = 32

# Operators that take no value at all.
NO_VALUE_OPS = frozenset(
    {"is_null", "is_not_null", "is_empty", "is_not_empty", "has_links", "has_no_links"}
)
# Operators whose value is a list.
LIST_OPS = frozenset({"in", "not_in", "has_any", "has_all", "has_none", "linked_to_any"})


@dataclass(frozen=True, slots=True)
class Condition:
    field_key: str
    field_type: str
    op: str
    value: Any
    # Set for user fields; None for pseudo-fields (which compile to columns).
    field: FieldDef | None


@dataclass(frozen=True, slots=True)
class And:
    children: tuple[FilterNode, ...]


@dataclass(frozen=True, slots=True)
class Or:
    children: tuple[FilterNode, ...]


@dataclass(frozen=True, slots=True)
class Not:
    child: FilterNode


FilterNode = Condition | And | Or | Not


class FilterContext:
    """Everything token resolution needs: when now is, how to resolve a record
    reference (key or UUID) to a record id, and how to resolve a user reference (id,
    email, display name or ``@me``) to a principal id.

    "Who is asking" is not a field here. It is inside ``resolve_principal_ref``'s
    closure, because that function is the *only* thing that expands ``@me`` on either
    surface, and a second copy of the caller's id here would be state nothing reads.

    Both resolvers arrive as injected callables rather than as repositories, because
    **this module has no database access** (DD-2): it validates and shapes the
    filter tree, and the service that built the context is what owns the connection.
    ``resolve_principal_ref`` follows ``resolve_record_ref``'s existing precedent
    exactly, and is threaded through all three construction sites --
    ``records.query_records``, ``records.bulk_update`` and ``search._parse_filter``.
    """

    def __init__(
        self,
        object_type_key: str,
        fields_by_key: dict[str, FieldDef],
        now: datetime,
        resolve_record_ref: Callable[[str], str],
        resolve_principal_ref: Callable[[str], str],
    ) -> None:
        self.object_type_key = object_type_key
        self.fields_by_key = fields_by_key
        self.now = now
        self.resolve_record_ref = resolve_record_ref
        self.resolve_principal_ref = resolve_principal_ref

    def lookup(self, field_key: str) -> tuple[str, FieldDef | None]:
        """Resolve a filterable field key to (field_type, FieldDef | None)."""
        if field_key in self.fields_by_key:
            field = self.fields_by_key[field_key]
            return field.type, field
        if field_key in PSEUDO_FIELDS:
            return PSEUDO_FIELDS[field_key][1], None
        valid = sorted(self.fields_by_key) + sorted(PSEUDO_FIELDS)
        near = difflib.get_close_matches(field_key, valid, n=3, cutoff=0.6)
        raise UnknownFieldError(field_key, self.object_type_key, valid, list(near))


def too_deep(depth: int) -> ValidationFailedError:
    """The one refusal both walkers raise, so the message cannot drift between the
    query path and the multi-type search path."""
    return ValidationFailedError(
        f"Filter is nested more than {MAX_FILTER_DEPTH} levels deep. Flatten it: a "
        "single 'and' or 'or' takes a list of any length, so nesting is rarely needed "
        "past two or three levels.",
        max_depth=MAX_FILTER_DEPTH,
        depth=depth,
    )


def parse_filter(raw: Any, ctx: FilterContext) -> FilterNode | None:
    """Parse and validate a filter tree. ``None`` or ``{}`` matches all live records."""
    if raw is None or raw == {}:
        return None
    return _parse_node(raw, ctx)


def _parse_node(raw: Any, ctx: FilterContext, depth: int = 1) -> FilterNode:
    if depth > MAX_FILTER_DEPTH:
        raise too_deep(depth)
    if not isinstance(raw, dict):
        raise ValidationFailedError(
            "A filter must be an object: a bare condition {field, op, value} or a "
            "boolean node {and|or|not: ...}."
        )
    combinators = [k for k in ("and", "or", "not") if k in raw]
    if combinators:
        if len(raw) != 1:
            raise ValidationFailedError(
                f"A boolean filter node must have exactly one key, got {sorted(raw)}."
            )
        kind = combinators[0]
        if kind == "not":
            return Not(_parse_node(raw["not"], ctx, depth + 1))
        children = raw[kind]
        if not isinstance(children, list) or not children:
            raise ValidationFailedError(f"{kind!r} requires a non-empty list of filters.")
        parsed = tuple(_parse_node(c, ctx, depth + 1) for c in children)
        return And(parsed) if kind == "and" else Or(parsed)
    return _parse_condition(raw, ctx)


def _parse_condition(raw: dict[str, Any], ctx: FilterContext) -> Condition:
    unknown = set(raw) - {"field", "op", "value"}
    if unknown:
        raise ValidationFailedError(
            f"Condition has unknown keys {sorted(unknown)}; expected field, op, value."
        )
    field_key = raw.get("field")
    if not isinstance(field_key, str):
        raise ValidationFailedError("Condition requires a 'field' string.")
    op = raw.get("op")
    if not isinstance(op, str):
        raise ValidationFailedError("Condition requires an 'op' string.")
    field_type, field = ctx.lookup(field_key)
    valid_ops = operators_for(field_type)
    if op not in valid_ops:
        raise InvalidOperatorError(op, field_key, field_type, valid_ops)

    if op in NO_VALUE_OPS:
        if raw.get("value") is not None:
            raise ValidationFailedError(f"Operator {op!r} takes no value.", field_key)
        return Condition(field_key, field_type, op, None, field)

    if "value" not in raw or raw["value"] is None:
        raise ValidationFailedError(f"Operator {op!r} requires a value.", field_key)
    value = raw["value"]

    if op == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise ValidationFailedError(
                "Operator 'between' takes a two-element array, inclusive on both ends.",
                field_key,
            )
        value = [_resolve_scalar(field_type, field, field_key, v, ctx) for v in value]
    elif op in LIST_OPS:
        if not isinstance(value, list) or not value:
            raise ValidationFailedError(
                f"Operator {op!r} takes a non-empty array of values.", field_key
            )
        value = [_resolve_scalar(field_type, field, field_key, v, ctx) for v in value]
    else:
        value = _resolve_scalar(field_type, field, field_key, value, ctx)
    return Condition(field_key, field_type, op, value, field)


def _resolve_scalar(
    field_type: str,
    field: FieldDef | None,
    field_key: str,
    value: Any,
    ctx: FilterContext,
) -> Any:
    """Resolve tokens and validate one comparison literal for its field type."""
    if field_type == "user_ref":
        # The same four reference forms the write path accepts, resolved by the same
        # function through the injected callable -- for ``eq``, ``neq`` and each member of
        # an ``in`` list alike, since every one of them arrives here. ``@me`` still
        # resolves to the caller and is still first, so nobody's display name can shadow
        # it. An unresolvable value raises ``validation_failed`` rather than quietly
        # matching nothing: a filter that silently returns zero rows is the worst
        # possible answer to a typo.
        #
        # Unlike the write path this accepts an **inactive** principal (the caller passes
        # ``allow_inactive=True``): finding a departed colleague's open work is the
        # handover query, and a filter is a read. It also never consults
        # ``allow_service_accounts``, which is a *field* config -- ``created_by`` and
        # ``updated_by`` are ``user_ref`` pseudo-fields with no config at all, and a
        # filter is not an assignment.
        if not isinstance(value, str) or not value:
            raise ValidationFailedError(
                f"Field {field_key!r} (user_ref) compares against a principal id, an email "
                "address, an exact display name, or @me.",
                field_key,
            )
        try:
            return ctx.resolve_principal_ref(value)
        except ValidationFailedError as exc:
            details = {k: v for k, v in exc.details.items() if k != "field_key"}
            raise ValidationFailedError(
                f"Field {field_key!r}: {exc.message}", field_key, **details
            ) from exc
    if field_type in ("date", "datetime"):
        if is_date_token(value):
            return resolve_date_token(str(value), field_type, ctx.now)
        if not isinstance(value, str):
            raise ValidationFailedError(
                f"Field {field_key!r} ({field_type}) compares against an ISO-8601 string "
                "or a date token like @today-7d.",
                field_key,
            )
        return value
    if field_type in ("short_text", "long_text", "url"):
        if not isinstance(value, str):
            raise ValidationFailedError(
                f"Field {field_key!r} ({field_type}) compares against a string.", field_key
            )
        return value
    if field_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationFailedError(
                f"Field {field_key!r} (integer) compares against an integer.", field_key
            )
        return value
    if field_type == "decimal":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValidationFailedError(
                f"Field {field_key!r} (decimal) compares against a number.", field_key
            )
        return value
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise ValidationFailedError(
                f"Field {field_key!r} (boolean) compares against true or false.", field_key
            )
        return value
    if field_type in ("single_select", "multi_select"):
        valid = option_values(field.config) if field is not None else []
        if not isinstance(value, str) or (valid and value not in valid):
            raise ValidationFailedError(
                f"Field {field_key!r} compares against one of: {', '.join(valid)}; got {value!r}.",
                field_key,
                valid_options=valid,
            )
        return value
    if field_type == "relation":
        if not isinstance(value, str) or not value:
            raise ValidationFailedError(
                f"Field {field_key!r} (relation) compares against a record key or id.",
                field_key,
            )
        return ctx.resolve_record_ref(value)
    raise ValidationFailedError(
        f"Field {field_key!r} of type {field_type!r} cannot be compared.", field_key
    )
