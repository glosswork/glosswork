/**
 * The filter-tree wire shape (docs/MCP_TOOLS.md section 4, "Filter grammar").
 *
 * This is the single source of truth for the JSON shape sent as `filter` in
 * `POST /api/v1/object-types/{key}/query`'s request body (`QueryBody.filter` in `../api/schema.ts`
 * is typed there only as `{ [key: string]: unknown } | null` because the backend route accepts an
 * arbitrary `dict[str, Any]`; this module gives that value real structure on the frontend side).
 *
 * `and`/`or` nest arbitrarily, `not` wraps a single node, and a bare condition object is itself a
 * valid filter (or a valid leaf anywhere in the tree). There is deliberately no operator-by-field-
 * type table here or anywhere else in `web/src/`: a condition's `op` is just a `string` supplied
 * by whatever the field's `operators` array (from `describe_object_type`) contains.
 */

/** A leaf condition: `{"field": "status", "op": "eq", "value": "active"}`. */
export interface FilterCondition {
  field: string;
  op: string;
  value?: unknown;
}

export interface FilterAndNode {
  and: FilterNode[];
}

export interface FilterOrNode {
  or: FilterNode[];
}

export interface FilterNotNode {
  not: FilterNode;
}

/** Any node in a filter tree: a boolean combinator or a bare leaf condition. */
export type FilterNode = FilterAndNode | FilterOrNode | FilterNotNode | FilterCondition;

export function isAndNode(node: FilterNode): node is FilterAndNode {
  return "and" in node;
}

export function isOrNode(node: FilterNode): node is FilterOrNode {
  return "or" in node;
}

export function isNotNode(node: FilterNode): node is FilterNotNode {
  return "not" in node;
}

export function isConditionNode(node: FilterNode): node is FilterCondition {
  return !isAndNode(node) && !isOrNode(node) && !isNotNode(node);
}

/** The boolean kind of an `and`/`or` group node, used for the group's own toggle control. */
export type GroupKind = "and" | "or";

export function groupChildren(node: FilterAndNode | FilterOrNode): FilterNode[] {
  return isAndNode(node) ? node.and : node.or;
}
