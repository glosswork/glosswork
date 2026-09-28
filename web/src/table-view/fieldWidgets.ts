/**
 * Pure conversions between an inline-edit widget's raw draft value (always string-ish, since
 * HTML form controls only ever hand back strings/booleans/string arrays) and the typed value
 * `PATCH /api/v1/records/{key}` expects in `values[fieldKey]`. Kept out of `EditableCell.tsx`'s
 * render body per AGENTS.md.
 */
import type { FieldDoc } from "../api/objectTypes";

export type EditDraft = string | boolean | string[];

/** The widget kind an editable field's `type` maps to (never used to look up an operator —
 * that ban is `filters/noHardcodedOperators.ts`'s concern; this only picks an input control). */
export type WidgetKind =
  | "text"
  | "textarea"
  | "number"
  | "boolean"
  | "date"
  | "datetime"
  | "select"
  | "multi_select"
  | "user_ref";

const WIDGET_BY_FIELD_TYPE: Record<string, WidgetKind> = {
  short_text: "text",
  url: "text",
  // A directory picker (`FieldInput`'s `user_ref` branch), not free text — the
  // picker submits a principal id, and `draftFromStoredValue`/`parseEditedValue` below need no
  // change for it: the default (final) branch of each already round-trips a bare id string.
  user_ref: "user_ref",
  long_text: "textarea",
  integer: "number",
  decimal: "number",
  boolean: "boolean",
  date: "date",
  datetime: "datetime",
  single_select: "select",
  multi_select: "multi_select",
};

export function widgetKindFor(field: FieldDoc): WidgetKind {
  return WIDGET_BY_FIELD_TYPE[field.type] ?? "text";
}

/** The draft value an edit widget should initialize from the field's current stored value. */
export function draftFromStoredValue(field: FieldDoc, value: unknown): EditDraft {
  const widget = widgetKindFor(field);
  if (widget === "boolean") return value === true;
  if (widget === "multi_select") return Array.isArray(value) ? value.map(String) : [];
  if (value === null || value === undefined) return "";
  return String(value);
}

/** The typed value to submit in `PATCH`'s `values[fieldKey]`, converted from the widget's raw
 * draft. Empty text commits `null` (clearing the field) rather than an empty string, matching
 * how the rest of the app treats "no value". */
export function parseEditedValue(field: FieldDoc, draft: EditDraft): unknown {
  const widget = widgetKindFor(field);
  if (widget === "boolean") return draft === true;
  if (widget === "multi_select") return Array.isArray(draft) ? draft : [];
  if (widget === "number") {
    if (typeof draft !== "string" || draft.trim() === "") return null;
    const parsed = Number(draft);
    return Number.isNaN(parsed) ? null : parsed;
  }
  if (typeof draft === "string" && draft.trim() === "") return null;
  return draft;
}
