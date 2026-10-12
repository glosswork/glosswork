import type { CsvImportResult } from "../api/csv";
import type { FieldDoc } from "../api/objectTypes";

/**
 * What an import with `Blank cells clear the field` removes, as one sentence: `3 values will be
 * cleared: Superseded by (3).` before a commit and `3 values cleared: Superseded by (3).` after
 * it. Fields are named as the wizard names them everywhere else, by their display name, in the
 * object type's own field order.
 */
export function clearsSentence(
  result: Pick<CsvImportResult, "cleared" | "cleared_by_field">,
  fields: FieldDoc[],
  verb: "will be cleared" | "cleared",
): string {
  const count = result.cleared ?? 0;
  const noun = count === 1 ? "value" : "values";
  const byField = result.cleared_by_field ?? {};
  const parts = fields
    .filter((field) => (byField[field.key] ?? 0) > 0)
    .map((field) => `${field.name} (${byField[field.key]})`);
  return parts.length === 0
    ? `${count} ${noun} ${verb}.`
    : `${count} ${noun} ${verb}: ${parts.join(", ")}.`;
}
