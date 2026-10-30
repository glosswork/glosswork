/**
 * Can this condition's value be half-typed?
 *
 * Read off the widget `ValueInput` renders, branch for branch: no widget at all for a
 * `NO_VALUE_OPS` operator; a `<select>` or a checkbox group for a select field, a boolean and a
 * `user_ref`; a text or number box for everything else, including the comma-separated list a
 * `relation` gets for `linked_to_any` and the two boxes of a `between`.
 *
 * Two filter surfaces ask, and give different answers to "what then":
 *
 * - a chip's popover (`table-view/ConditionPopover.tsx`) commits a typed value on Apply, Enter
 *   or close, and a chosen one as soon as it changes;
 * - the tree builder (`FilterBuilder.tsx`) has no Apply, so it sends a typed value once typing
 *   pauses, and a chosen one at once.
 *
 * One function, so the two cannot disagree about which widgets are typed.
 */
import { valueCardinality } from "./completeness";
import type { FilterableField } from "./filterableFields";

export function isTypedValueWidget(field: FilterableField, op: string): boolean {
  const cardinality = valueCardinality(op);
  if (cardinality === "none") return false;
  if (field.type === "single_select" || field.type === "multi_select") return false;
  if (cardinality !== "one") return true;
  return field.type !== "boolean" && field.type !== "user_ref";
}
