/**
 * The object type key a record key belongs to.
 *
 * **Moved out of `audit-browser/auditRows.ts`**, which retired with the table. Its two tests
 * moved with it; the eight covering `sortAuditEvents` and `cycleAuditSort` did not, because a
 * feed has no column headers to click and that sort only ever ordered the pages already fetched
 * — `GET /api/v1/audit-events` takes no sort spec.
 */
import type { ObjectTypeSummary } from "../api/objectTypes";

/**
 * The object type key a record key belongs to, or `null` when no loaded type claims it.
 *
 * DD-25 gives the audit envelope `record_key` but not the object type's *key*, and the audit row
 * carries only `object_type_id`, which no frontend response exposes a mapping for. The link is
 * therefore resolved through the record key's own prefix: `validate_key_prefix` constrains a
 * prefix to 2-10 uppercase letters and digits starting with a letter — no hyphen — and the
 * database holds `object_types.key_prefix` unique, so the segment before the first hyphen
 * identifies exactly one type. A record key whose prefix matches nothing loaded returns null and
 * the caller renders plain text rather than a link that would 404.
 *
 * **`null` in, `null` out.** An audit event's `record_key` is nullable — every schema-level
 * event has none — and a cross-record feed meets those routinely, where the record page's history
 * never did. The old signature took a bare `string` because its one caller had already checked.
 */
export function objectTypeKeyForRecordKey(
  recordKey: string | null,
  objectTypes: ObjectTypeSummary[],
): string | null {
  if (recordKey === null) return null;
  const hyphen = recordKey.indexOf("-");
  if (hyphen <= 0) return null;
  const prefix = recordKey.slice(0, hyphen);
  return objectTypes.find((objectType) => objectType.key_prefix === prefix)?.key ?? null;
}
