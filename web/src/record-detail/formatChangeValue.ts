/**
 * One field's old or new value, formatted for the Activity card's change pills.
 * `ui/ActivityEvent.tsx` "holds no field-type logic" by design — its `changes` prop
 * takes plain strings the caller already formatted — so this is where a version's diff gets the
 * same display vocabulary a field's *current* value gets elsewhere on the page: a select's
 * label, a number's grouping, a date in words.
 *
 * **Never the relative-date hint.** `docs/DESIGN.md` 5's "(in 6 days)" is about a *current*
 * value; a change pill shows what a field *was*, three versions ago, and dating that relative to
 * today would misstate which moment it describes. `formatDate` is called with no options here,
 * on purpose.
 *
 * **`null` in, `null` out.** `ui/ActivityEvent.tsx` collapses a pill with `oldValue: null` to
 * "`<Field>` edited" rather than drawing an arrow with nothing on one side — the shape for a
 * `create`, where there was no prior value at all. `formatFieldValue`'s own em dash is for a
 * live field that is *currently* empty, a different fact, so it is not reused for this case.
 */
import type { FieldDoc } from "../api/objectTypes";
import type { PrincipalSidecar } from "../api/principals";
import { formatTimestamp } from "../ui/datetime";
import { formatDate, formatNumber } from "../ui/valueFormat";
import { formatFieldValue } from "./fieldDisplay";

export function formatChangeValue(
  field: FieldDoc,
  value: unknown,
  principals?: PrincipalSidecar,
): string | null {
  if (value === null || value === undefined) return null;

  if (field.type === "user_ref" && typeof value === "string") {
    return principals?.[value]?.display_name ?? value;
  }
  if (field.type === "attachment") {
    const count = Array.isArray(value) ? value.length : 0;
    return count === 1 ? "1 file" : `${count} files`;
  }
  if ((field.type === "integer" || field.type === "decimal") && typeof value !== "object") {
    return formatNumber(field, value);
  }
  if (field.type === "date" && typeof value === "string") {
    return formatDate(value);
  }
  if (field.type === "datetime" && typeof value === "string") {
    return formatTimestamp(value);
  }
  return formatFieldValue(field, value);
}
