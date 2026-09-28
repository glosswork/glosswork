/**
 * Is a filter tree complete enough to send? An incomplete condition never reaches the network.
 *
 * **This module is a mirror of `src/glosswork/filters.py`, and that is the only reason it is
 * allowed to exist.** The frontend cannot read the operator vocabulary at runtime — the full list
 * is published only on MCP, by `describe_capabilities`, and REST publishes operators only per
 * field inside `describe_object_type`. So the shape rule has to be written down
 * here, once, and every consumer has to import it rather than transcribe it. Two transcriptions of
 * one rule is exactly the drift `linked_to_any` was an instance of: `filters.py:47` put it in
 * `LIST_OPS` and a local list in `ValueInput.tsx` did not, so the builder sent a string where the
 * API required an array.
 *
 * **What it is not.** This is a *shape* gate, not a validity gate. `filters.py::_resolve_scalar`
 * rejects plenty of complete values — a `date` string that is neither ISO nor a date token, a
 * non-`int` for an `integer`, an unresolvable `user_ref`, a relation key naming no record — and
 * none of that is mirrored here, deliberately: a second copy of `_resolve_scalar`, date tokens
 * and `@me` included, is the drift this module exists to prevent.
 * A complete condition can still be refused by the server. What this module eliminates is the 422
 * the product generates **on its own behalf**, without the person having typed anything wrong.
 *
 * The rule, transcribed from `filters.py:42-48`, `_parse_node` at `:148-161` and
 * `_parse_condition` at `:166-205`:
 *
 * | Node | Sendable when | `filters.py` |
 * | --- | --- | --- |
 * | an `and`/`or` group | it has **at least one child** | `:158-160` — "{kind!r} requires a non-empty list of filters." |
 * | one of the six `NO_VALUE_OPS` | it carries no value | `:182-185` — "Operator {op!r} takes no value." |
 * | `between` | a two-element array, **neither element null** | `:191-197`, plus `_resolve_scalar` |
 * | one of the six `LIST_OPS` | a non-empty array, no null members | `:198-203`, plus `_resolve_scalar` |
 * | every other operator | a non-null scalar | `:187-189`, `:204-205` |
 *
 * **The group row is here because an empty group is the same defect from a different button.**
 * The defect this gate prevents reads "adding a filter condition fires the query before a value
 * exists, the server rejects it, a red error appears and the table empties" — and `+ Group (AND)`
 * produces that identical sequence, on the identical screen, differing only in which 422 comes
 * back: `'and' requires a non-empty list of filters.` rather than `Operator 'eq' requires a
 * value.`. `docs/DESIGN.md` 7.4's "never produces a server error" is about what the product
 * generates **on its own behalf**, and an empty group is exactly that — nobody typed anything
 * wrong. Gating the condition but not the group would be a half-fix.
 *
 * The group rule is **recursive, not a root check**: `{"and": [{"and": []}]}` has a non-empty
 * root and is still unsendable, because `_parse_node` descends into every child before building
 * the node. `incompleteNodes` descends the same way.
 *
 * **Two rows are one step past what `filters.py` literally says, and the step is justified.**
 * `filters.py` checks only "a two-element array" for `between` and only "a
 * non-empty array" for the list operators; it then passes every element to `_resolve_scalar`,
 * whose every branch is an `isinstance` check, so `None` is refused for all thirteen field types.
 * A one-sided `between` serialises as `[1000, null]` and the server would refuse it — so it is
 * incomplete here.
 *
 * `undefined` and `null` are treated alike throughout: a condition built in the UI carries
 * `undefined`, and `JSON.stringify` turns both an absent key and an `undefined` array member into
 * exactly what the server sees as a missing value.
 */
import {
  groupChildren,
  isAndNode,
  isNotNode,
  isOrNode,
  type FilterCondition,
  type FilterNode,
} from "./types";

/** `src/glosswork/filters.py:43-45`. Operators that take no value at all. */
export const NO_VALUE_OPS: ReadonlySet<string> = new Set([
  "is_null",
  "is_not_null",
  "is_empty",
  "is_not_empty",
  "has_links",
  "has_no_links",
]);

/**
 * `src/glosswork/filters.py:47`. Operators whose value is a non-empty list. `linked_to_any` is the
 * member a local copy once missed, and it is the only one of the six that lands on a
 * `relation` field — which is why `ValueInput.tsx`'s relation branch reads this set rather than
 * keeping a list of its own.
 */
export const LIST_OPS: ReadonlySet<string> = new Set([
  "in",
  "not_in",
  "has_any",
  "has_all",
  "has_none",
  "linked_to_any",
]);

/**
 * How many values an operator takes. This is a property of the operator's semantics, not of the
 * field's type — `between` always takes a pair and `in` always takes a list, whatever they are
 * filtering (docs/MCP_TOOLS.md section 4). `ValueInput.tsx` picks a *widget* from this and a field
 * type; nothing here decides which operators are legal for a field, which always comes from the
 * field's own `operators` array.
 */
export type ValueCardinality = "none" | "one" | "pair" | "list";

export function valueCardinality(op: string): ValueCardinality {
  if (NO_VALUE_OPS.has(op)) return "none";
  if (op === "between") return "pair";
  if (LIST_OPS.has(op)) return "list";
  return "one";
}

/** A value that is present at all — neither absent nor explicitly null. */
function present(value: unknown): boolean {
  return value !== undefined && value !== null;
}

/**
 * Would `filters.py::_parse_condition` accept this condition's *shape*? See the module header for
 * what "shape" excludes.
 */
export function isConditionComplete(condition: Pick<FilterCondition, "op" | "value">): boolean {
  const { op, value } = condition;
  switch (valueCardinality(op)) {
    case "none":
      return !present(value);
    case "pair":
      return Array.isArray(value) && value.length === 2 && value.every(present);
    case "list":
      return Array.isArray(value) && value.length > 0 && value.every(present);
    case "one":
      return present(value);
  }
}

/**
 * Every node anywhere in one filter tree that the server would refuse for its shape, in document
 * order: an incomplete leaf condition, or an `and`/`or` group with no children.
 *
 * `and`/`or` nest arbitrarily and `not` wraps a single node (docs/MCP_TOOLS.md section 4), so a
 * gate that only looked at the root would miss both the condition three levels down that the
 * person is still typing and the empty group they just added inside a populated one. A `not`
 * wrapper is transparent here — it is refused for what it wraps, never for itself.
 *
 * `null` is the empty filter ("matches all live records"), which is sendable by definition. Note
 * that `null` and `{"and": []}` are **not** the same thing: the first is how the grammar spells
 * "no filter" and the second is a malformed node.
 */
export function incompleteNodes(node: FilterNode | null): FilterNode[] {
  if (node === null) return [];
  if (isNotNode(node)) return incompleteNodes(node.not);
  if (isAndNode(node) || isOrNode(node)) {
    const children = groupChildren(node);
    // `filters.py:158-160`. An empty group is reported as itself; there is nothing below it to
    // descend into, and the server never gets far enough to look.
    if (children.length === 0) return [node];
    return children.flatMap(incompleteNodes);
  }
  return isConditionComplete(node) ? [] : [node];
}

/** True when nothing anywhere in `tree` is unsendable, so the tree may reach the network. */
export function isFilterComplete(tree: FilterNode | null): boolean {
  return incompleteNodes(tree).length === 0;
}
