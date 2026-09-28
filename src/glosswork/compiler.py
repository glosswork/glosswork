"""Filter-AST to SQL compilation, sorting, and keyset pagination (DD-1, FR-R5, FR-R6).

Binding constraints from docs/DATA_MODEL.md section 5:

- The object type id is inlined as a strict-UUID-validated literal (via
  :func:`glosswork.sqlexpr.type_scope_predicate`) so SQLite can prove the query
  implies each partial index's WHERE clause. All other values bind as parameters.
- Field extraction text comes only from :func:`glosswork.sqlexpr.json_field_expr`,
  so it is byte-identical to index DDL.
"""

from __future__ import annotations

import base64
import binascii
import difflib
import json
from dataclasses import dataclass
from typing import Any

from glosswork.cursors import is_cursor_scalar
from glosswork.errors import UnknownFieldError, ValidationFailedError
from glosswork.fieldtypes import PSEUDO_FIELDS
from glosswork.filters import And, Condition, FilterNode, Not, Or
from glosswork.repositories.models import FieldDef, ObjectType
from glosswork.sqlexpr import json_field_expr, type_scope_predicate, validate_uuid

_LIKE_ESCAPE = "\\"


@dataclass(frozen=True, slots=True)
class SortKey:
    field_key: str
    direction: str  # 'asc' | 'desc'


@dataclass(slots=True)
class CompiledQuery:
    sql: str
    params: dict[str, Any]
    count_sql: str
    count_params: dict[str, Any]
    sort: tuple[SortKey, ...]
    limit: int

    def cursor_for(self, extras: dict[str, Any], record_id: str) -> str:
        """Build the opaque keyset cursor pointing after one result row."""
        values = [extras[f"__s{i}"] for i in range(len(self.sort))]
        payload = {
            "v": values,
            "id": record_id,
            "k": [[s.field_key, s.direction] for s in self.sort],
        }
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


class _ParamAllocator:
    def __init__(self) -> None:
        self.params: dict[str, Any] = {}

    def add(self, value: Any) -> str:
        name = f"p{len(self.params)}"
        self.params[name] = value
        return f":{name}"


def _sql_value(field_type: str, value: Any) -> Any:
    """JSON booleans extract as 0/1 in SQLite; bind them accordingly."""
    if field_type == "boolean" and isinstance(value, bool):
        return int(value)
    return value


def _escape_like(value: str) -> str:
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE + _LIKE_ESCAPE)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


def field_expr(condition_field: FieldDef | None, field_key: str) -> str:
    """The SQL expression for a filterable/sortable field: json_extract for user
    fields, the backing column for pseudo-fields."""
    if condition_field is not None:
        return json_field_expr(condition_field.key)
    return PSEUDO_FIELDS[field_key][0]


def compile_node(node: FilterNode, alloc: _ParamAllocator) -> str:
    if isinstance(node, And):
        return "(" + " AND ".join(compile_node(c, alloc) for c in node.children) + ")"
    if isinstance(node, Or):
        return "(" + " OR ".join(compile_node(c, alloc) for c in node.children) + ")"
    if isinstance(node, Not):
        return f"NOT ({compile_node(node.child, alloc)})"
    return _compile_condition(node, alloc)


def _compile_condition(cond: Condition, alloc: _ParamAllocator) -> str:
    expr = field_expr(cond.field, cond.field_key)
    op = cond.op

    if op == "is_null":
        return f"{expr} IS NULL"
    if op == "is_not_null":
        return f"{expr} IS NOT NULL"

    if cond.field_type in ("multi_select", "attachment"):
        return _compile_array_condition(cond, expr, alloc)
    if cond.field_type == "relation":
        return _compile_relation_condition(cond, alloc)

    if op == "eq":
        return f"{expr} = {alloc.add(_sql_value(cond.field_type, cond.value))}"
    if op == "neq":
        return f"{expr} != {alloc.add(_sql_value(cond.field_type, cond.value))}"
    if op in ("gt", "gte", "lt", "lte"):
        sql_op = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        return f"{expr} {sql_op} {alloc.add(cond.value)}"
    if op == "between":
        low, high = cond.value
        return f"{expr} BETWEEN {alloc.add(low)} AND {alloc.add(high)}"
    if op in ("in", "not_in"):
        placeholders = ", ".join(alloc.add(_sql_value(cond.field_type, v)) for v in cond.value)
        keyword = "IN" if op == "in" else "NOT IN"
        return f"{expr} {keyword} ({placeholders})"
    if op in ("contains", "not_contains", "starts_with", "ends_with"):
        escaped = _escape_like(str(cond.value))
        pattern = {
            "contains": f"%{escaped}%",
            "not_contains": f"%{escaped}%",
            "starts_with": f"{escaped}%",
            "ends_with": f"%{escaped}",
        }[op]
        like = f"{expr} LIKE {alloc.add(pattern)} ESCAPE '{_LIKE_ESCAPE}'"
        if op == "not_contains":
            # A missing value contains nothing: include NULLs, unlike bare NOT LIKE.
            return f"({expr} IS NULL OR NOT ({like}))"
        return like
    raise ValidationFailedError(f"Operator {op!r} is not compilable for field {cond.field_key!r}.")


def _compile_array_condition(cond: Condition, expr: str, alloc: _ParamAllocator) -> str:
    """multi_select and attachment filtering via json_each (docs/DATA_MODEL.md
    section 5). COALESCE to an empty array makes a missing key behave as empty."""
    coalesced = f"COALESCE({expr}, '[]')"
    op = cond.op
    if op == "is_empty":
        return f"({expr} IS NULL OR json_array_length({expr}) = 0)"
    if op == "is_not_empty":
        return f"({expr} IS NOT NULL AND json_array_length({expr}) > 0)"
    placeholders = ", ".join(alloc.add(v) for v in cond.value)
    exists = f"EXISTS (SELECT 1 FROM json_each({coalesced}) je WHERE je.value IN ({placeholders}))"
    if op == "has_any":
        return exists
    if op == "has_none":
        return f"NOT {exists}"
    if op == "has_all":
        count = (
            f"(SELECT COUNT(DISTINCT je.value) FROM json_each({coalesced}) je "
            f"WHERE je.value IN ({placeholders}))"
        )
        return f"{count} = {len(set(cond.value))}"
    raise ValidationFailedError(f"Operator {op!r} is not valid for arrays.")


def _compile_relation_condition(cond: Condition, alloc: _ParamAllocator) -> str:
    assert cond.field is not None
    field_param = alloc.add(cond.field.id)
    base = (
        "SELECT 1 FROM record_links rl "
        f"WHERE rl.field_id = {field_param} AND rl.from_record_id = records.id"
    )
    op = cond.op
    if op == "has_links":
        return f"EXISTS ({base})"
    if op == "has_no_links":
        return f"NOT EXISTS ({base})"
    if op == "linked_to":
        return f"EXISTS ({base} AND rl.to_record_id = {alloc.add(cond.value)})"
    if op == "linked_to_any":
        placeholders = ", ".join(alloc.add(v) for v in cond.value)
        return f"EXISTS ({base} AND rl.to_record_id IN ({placeholders}))"
    raise ValidationFailedError(f"Operator {op!r} is not valid for relations.")


def parse_sort(
    sort: list[dict[str, Any]] | None,
    fields_by_key: dict[str, FieldDef],
    object_type_key: str,
) -> tuple[SortKey, ...]:
    if not sort:
        return ()
    keys: list[SortKey] = []
    for entry in sort:
        if not isinstance(entry, dict) or "field" not in entry:
            raise ValidationFailedError("Each sort entry is {field, dir}; dir defaults to asc.")
        field_key = entry["field"]
        direction = entry.get("dir", "asc")
        if direction not in ("asc", "desc"):
            raise ValidationFailedError(
                f"Sort dir must be 'asc' or 'desc', got {direction!r}.", str(field_key)
            )
        if field_key not in fields_by_key and field_key not in PSEUDO_FIELDS:
            valid = sorted(fields_by_key) + sorted(PSEUDO_FIELDS)
            near = difflib.get_close_matches(field_key, valid, n=3, cutoff=0.6)
            raise UnknownFieldError(field_key, object_type_key, valid, list(near))
        keys.append(SortKey(field_key, direction))
    return tuple(keys)


def _sort_expr(sort_key: SortKey, fields_by_key: dict[str, FieldDef]) -> str:
    field = fields_by_key.get(sort_key.field_key)
    return field_expr(field, sort_key.field_key)


def decode_cursor(cursor: str, sort: tuple[SortKey, ...]) -> tuple[list[Any], str]:
    """Decode an opaque cursor and check it belongs to this sort spec."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        values = payload["v"]
        record_id = payload["id"]
        spec = payload["k"]
    except (binascii.Error, ValueError, KeyError, TypeError):
        raise ValidationFailedError(
            "Invalid cursor. Pass the next_cursor value from a previous response, unmodified."
        ) from None
    if spec != [[s.field_key, s.direction] for s in sort] or not isinstance(values, list):
        raise ValidationFailedError(
            "Cursor does not match this query's sort. Re-run the query without a "
            "cursor to start over."
        )
    if len(values) != len(sort) or not isinstance(record_id, str):
        raise ValidationFailedError("Invalid cursor. Re-run the query without a cursor.")
    # Element types, not just the list's length. Every boundary value is
    # bound as a SQL parameter by ``_keyset_predicate``; a dict or a list reaches sqlite3,
    # which refuses to bind it, and the ``InterfaceError`` that follows was a 500 whose
    # log line carried the statement text **and** the bound parameters. The two decoders
    # take different contracts -- this one the sort, ``cursors.decode_cursor`` an expected
    # key set -- but they need the same guard, so both call ``is_cursor_scalar``.
    if not all(is_cursor_scalar(value) for value in values):
        raise ValidationFailedError(
            "Invalid cursor: a boundary value is not a scalar. Pass the next_cursor value "
            "from a previous response, unmodified, or re-run the query without a cursor."
        )
    return values, record_id


def _keyset_predicate(
    sort: tuple[SortKey, ...],
    boundary: list[Any],
    boundary_id: str,
    fields_by_key: dict[str, FieldDef],
    alloc: _ParamAllocator,
) -> str:
    """Row-after-boundary predicate matching SQLite ordering (NULLS FIRST on ASC,
    NULLS LAST on DESC), so pagination over duplicate and NULL sort values is exact."""
    exprs = [_sort_expr(s, fields_by_key) for s in sort] + ["records.id"]
    directions = [s.direction for s in sort] + ["asc"]
    values = [*boundary, boundary_id]

    clauses: list[str] = []
    for i in range(len(exprs)):
        parts: list[str] = []
        for j in range(i):
            parts.append(f"{exprs[j]} IS {alloc.add(values[j])}")
        expr, direction, value = exprs[i], directions[i], values[i]
        if direction == "asc":
            after = f"{expr} > {alloc.add(value)}" if value is not None else f"{expr} IS NOT NULL"
        else:
            after = f"({expr} < {alloc.add(value)} OR {expr} IS NULL)" if value is not None else "0"
        parts.append(after)
        clauses.append("(" + " AND ".join(parts) + ")")
    return "(" + " OR ".join(clauses) + ")"


def compile_candidate_filter(
    object_type: ObjectType,
    filter_node: FilterNode | None,
    candidate_ids: list[str],
) -> tuple[str, dict[str, Any]]:
    """The search post-filter (docs/DATA_MODEL.md section 10, rule 4):
    ``records.id IN (<candidates>) AND <compiled>`` over one object type's live
    records, with the same ``compile_node`` and the same ``json_extract`` text
    ``query_records`` uses, so DD-2 holds for search exactly as it does for queries.
    Returns the SQL and its bound parameters; the rows come back through
    ``RecordRepository.run_query`` like any other compiled query."""
    alloc = _ParamAllocator()
    where = [type_scope_predicate(object_type.id)]
    placeholders = ", ".join(alloc.add(record_id) for record_id in candidate_ids)
    where.append(f"records.id IN ({placeholders})")
    if filter_node is not None:
        where.append(compile_node(filter_node, alloc))
    return f"SELECT records.* FROM records WHERE {' AND '.join(where)}", alloc.params


def compile_query(
    object_type: ObjectType,
    fields_by_key: dict[str, FieldDef],
    filter_node: FilterNode | None,
    sort: tuple[SortKey, ...],
    limit: int,
    cursor: str | None,
    include_deleted: bool,
) -> CompiledQuery:
    alloc = _ParamAllocator()

    if include_deleted:
        scope = f"object_type_id = '{validate_uuid(object_type.id)}'"
    else:
        scope = type_scope_predicate(object_type.id)
    where = [scope]
    if filter_node is not None:
        where.append(compile_node(filter_node, alloc))

    count_sql = f"SELECT COUNT(*) FROM records WHERE {' AND '.join(where)}"
    count_params = dict(alloc.params)

    if cursor is not None:
        boundary, boundary_id = decode_cursor(cursor, sort)
        where.append(_keyset_predicate(sort, boundary, boundary_id, fields_by_key, alloc))

    select_extras = "".join(
        f", {_sort_expr(s, fields_by_key)} AS __s{i}" for i, s in enumerate(sort)
    )
    order_by = ", ".join(
        [f"{_sort_expr(s, fields_by_key)} {s.direction.upper()}" for s in sort] + ["records.id ASC"]
    )
    alloc.params["__limit"] = limit + 1  # one extra row to detect the next page
    sql = (
        f"SELECT records.*{select_extras} FROM records "
        f"WHERE {' AND '.join(where)} ORDER BY {order_by} LIMIT :__limit"
    )
    return CompiledQuery(
        sql=sql,
        params=alloc.params,
        count_sql=count_sql,
        count_params=count_params,
        sort=sort,
        limit=limit,
    )
