/** The thirteen supported field types (FR-S4), in a stable display order. */
export const FIELD_TYPES = [
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
] as const;
