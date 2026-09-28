import type { FieldDoc } from "../api/objectTypes";

export const EMPTY_FIELD_VALUE = "—";

/**
 * Resolves one non-relation field's stored value to display text, honoring the field's type
 * (FR-U2): `single_select`/`multi_select` fields show the option's `label`, never the raw
 * stored `value`. Relation fields are rendered by `LinkedRecordsPanel`, not here.
 */
export function formatFieldValue(field: FieldDoc, value: unknown): string {
  if (value === null || value === undefined) {
    return EMPTY_FIELD_VALUE;
  }
  if (field.type === "single_select") {
    return selectValueLabel(field, value);
  }
  if (field.type === "multi_select") {
    if (!Array.isArray(value) || value.length === 0) {
      return EMPTY_FIELD_VALUE;
    }
    return value.map((one) => selectValueLabel(field, one)).join(", ");
  }
  if (field.type === "boolean") {
    return value ? "Yes" : "No";
  }
  if (Array.isArray(value) || typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

/**
 * A linked record's display-field value as label text, or `null` to fall back to its key. Shared by
 * `LinkedRecordsPanel` and the table's `relationCellDisplay`, which render the same backend value
 * differently but decide *whether there is one* identically — the fallback rule lives here once
 * rather than in each of them.
 *
 * `display` is typed `unknown` because a display field may be an `integer` or a `date` as
 * readily as a `short_text`. A list or object is never a label, and an empty
 * or whitespace-only string is no better than none.
 */
export function linkDisplayLabel(value: unknown): string | null {
  if (typeof value === "string") {
    return value.trim() === "" ? null : value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return null;
}

/**
 * **One** select value as its option's display label, falling back to the stored key when no
 * option matches it (FR-U2, docs/DESIGN.md 7.3). Both branches of `formatFieldValue` above call
 * it, so "a select shows its label, never its stored value" has one implementation.
 *
 * Exported for the table cell, which renders a select as a `Pill` — a React **node**, while
 * `formatFieldValue` returns a `string` and must keep doing so for its three string-consuming
 * callers. The cell therefore composes the pill *over* this function
 * rather than teaching `formatFieldValue` to return nodes, and a `multi_select`'s per-value
 * pills need the label of one value rather than the joined sentence.
 */
export function selectValueLabel(field: FieldDoc, value: unknown): string {
  return optionLabel(field, value) ?? String(value);
}

function optionLabel(field: FieldDoc, value: unknown): string | undefined {
  return field.options?.find((option) => option.value === value)?.label;
}
