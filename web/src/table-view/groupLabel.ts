/**
 * A group header's display label: a `single_select` field shows the option's `label` (never the
 * raw stored value, same rule `fieldDisplay.ts` applies everywhere else); a `relation` field
 * shows the linked record's key plus its display field, when the expand summary carries one.
 */
import type { FieldDoc } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import { formatFieldValue } from "../record-detail/fieldDisplay";
import { relationGroupLabel } from "./relationExpand";

export function groupRowLabel(
  field: FieldDoc,
  groupValue: unknown,
  sampleRecord: RecordDoc | undefined,
): string {
  if (field.type === "relation") {
    return sampleRecord ? relationGroupLabel(sampleRecord, field.key) : String(groupValue);
  }
  return formatFieldValue(field, groupValue);
}
