/**
 * FR-U6's "column mapping with automatic suggestions by name similarity" — a frontend-only
 * concern, since the import route (`POST /api/v1/object-types/{key}/import`) takes no mapping
 * parameter and requires the CSV header to already equal field keys (or the literal `key`
 * column; see `src/glosswork/services/csv.py`). This is exact-match-after-normalization, not
 * fuzzy matching: normalize the header the same way as each field's `key` and `name`, and
 * suggest a field only when one of those normalized forms matches exactly.
 *
 * No per-field-type branching, matching the filter builder's "no hardcoded per-type table"
 * discipline (`web/src/filters/noHardcodedOperators.ts`): this function only ever looks at
 * `field.key` and `field.name`, never `field.type`.
 */
import type { FieldDoc } from "../api/objectTypes";

/** Lowercases, trims, and collapses runs of whitespace/underscore/hyphen into a single `_` so
 * "Target Date", "target_date", and "target-date" all normalize identically. */
function normalizeHeaderText(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[\s_-]+/g, "_");
}

/** Returns the `key` of the field whose normalized `key` or `name` exactly matches the
 * normalized header, or `null` if none matches. */
export function suggestFieldForHeader(header: string, fields: FieldDoc[]): string | null {
  const normalizedHeader = normalizeHeaderText(header);
  for (const field of fields) {
    if (
      normalizeHeaderText(field.key) === normalizedHeader ||
      normalizeHeaderText(field.name) === normalizedHeader
    ) {
      return field.key;
    }
  }
  return null;
}
