/**
 * The field definitions `/activity`'s change pills need, one read per object type in view.
 *
 * **Why a fan-out at all.** `formatChangeValue` needs a `FieldDoc` to render a value as a person
 * reads it — a date as `1 Sep`, a select as its display label. The record page has the record's
 * own type in hand; a cross-record feed does not, and `GET /object-types` returns summaries
 * without fields. Without this the pills would fall back to "`<Field>` edited" for every change,
 * which loses what the value changed from and to — information the table this screen replaces
 * showed, badly, as `JSON.stringify` output.
 *
 * **The key comes from the record key's prefix, not from the event.** An audit event carries
 * `object_type_id` and no key, and no frontend response maps one to the other — the reason
 * `objectTypeKeyForRecordKey` exists at all. A schema-level event (`entity_type` of
 * `object_type` or `field`) carries no `record_key`, resolves to nothing, and its changes take
 * the fallback shape; that is correct rather than a gap, since such an event is about the schema
 * and not about a field of a record.
 *
 * **The fan-out is bounded by the object-type list, not by the pages loaded** (DD-18). The set
 * of keys is derived from `useObjectTypes()`'s answer, so `Load more` can walk the whole audit
 * trail without adding a request beyond one per type the deployment has. `useQueries` shares
 * react-query's cache with the table pages, so a type already open costs nothing.
 */
import { useQueries } from "@tanstack/react-query";

import { getObjectType, type FieldDoc, type ObjectTypeSummary } from "../api/objectTypes";
import type { AuditEventDoc } from "../api/records";
import { objectTypeKeyForRecordKey } from "./objectTypeKeyForRecordKey";

export type FieldsByType = Record<string, Record<string, FieldDoc>>;

/** The object type keys the loaded events actually mention, in the summary list's order. */
export function keysInView(
  events: AuditEventDoc[],
  objectTypes: ObjectTypeSummary[],
): string[] {
  const mentioned = new Set<string>();
  for (const event of events) {
    const key = objectTypeKeyForRecordKey(event.record_key, objectTypes);
    if (key !== null) mentioned.add(key);
  }
  return objectTypes.map((t) => t.key).filter((key) => mentioned.has(key));
}

/**
 * `{objectTypeKey: {fieldKey: FieldDoc}}` for every type in view.
 *
 * A type still loading contributes nothing, and its changes render the fallback shape until it
 * arrives — the pills never wait on a spinner, because the write they describe is already on
 * screen.
 */
export function useFieldsByType(
  events: AuditEventDoc[],
  objectTypes: ObjectTypeSummary[],
): FieldsByType {
  const keys = keysInView(events, objectTypes);
  const results = useQueries({
    queries: keys.map((key) => ({
      // The same key `useObjectType` uses, so a type the schema editor or a table page has
      // already read costs nothing here. A near-miss key would be a second cache entry for one
      // document, which is the drift this comment exists to prevent.
      queryKey: ["object-types", key, { includeSamples: false }],
      queryFn: () => getObjectType(key, { include_samples: false }),
    })),
  });

  const byType: FieldsByType = {};
  results.forEach((result, index) => {
    const detail = result.data;
    if (!detail) return;
    const fields: Record<string, FieldDoc> = {};
    for (const field of detail.fields) fields[field.key] = field;
    byType[keys[index]] = fields;
  });
  return byType;
}
