/**
 * Relation field values never live in `record.data` (DD-1: links are a separate table); they
 * only appear under `record.expand[fieldKey]` when that field was named in the query's
 * `expand_relations` (FR-L6). These are the pure read helpers both the relation column's cell
 * and its grouping key share.
 */
import type { ExpandedLinkSummary, RecordDoc } from "../api/records";
import { EMPTY_FIELD_VALUE, linkDisplayLabel } from "../record-detail/fieldDisplay";

export const UNLINKED_GROUP_KEY = "(unlinked)";

function expandedLinks(record: RecordDoc, fieldKey: string): ExpandedLinkSummary[] | undefined {
  return record.expand?.[fieldKey];
}

/** The group key for a relation field: the first linked record's key, or the sentinel
 * "(unlinked)" group when there is none (FR-U1 grouping). */
export function relationGroupKey(record: RecordDoc, fieldKey: string): string {
  return expandedLinks(record, fieldKey)?.[0]?.key ?? UNLINKED_GROUP_KEY;
}

/** The group header label: the linked record's key, plus its display field's value when the
 * expand summary carries one. */
export function relationGroupLabel(record: RecordDoc, fieldKey: string): string {
  const first = expandedLinks(record, fieldKey)?.[0];
  if (!first) return UNLINKED_GROUP_KEY;
  return first.display !== null && first.display !== undefined
    ? `${first.key} — ${String(first.display)}`
    : first.key;
}

/** Table-cell display for a relation column. Relation fields are view-only in the table (FR-U1
 * scope note: editing a relation happens on the record detail view). Only the field currently
 * driving grouping has its links fetched at all (`expand_relations` is scoped to that one field
 * to keep the query lean); other relation columns show a placeholder rather than an
 * unrepresentative "unlinked". */
export function relationCellDisplay(record: RecordDoc, fieldKey: string): string {
  const links = expandedLinks(record, fieldKey);
  if (links === undefined) {
    return "(open record to view)";
  }
  if (links.length === 0) {
    return EMPTY_FIELD_VALUE;
  }
  // The display value **alone**, falling back to the key. A deliberate
  // divergence from `LinkedRecordsPanel`, which keeps the key beside the label: a relation
  // cell joins n links with commas inside a table column, and repeating a key after each one
  // produces a string no column measure can carry. The key is one click away on the record,
  // and `relationGroupLabel` above -- deliberately unchanged -- still renders `KEY — display`
  // in the header directly above these rows, so the key is not absent from the screen.
  return links.map((link) => linkDisplayLabel(link.display) ?? link.key).join(", ");
}
