/**
 * Which field types the table view lets a user edit inline or group by. Not an operator table
 * (that ban is `filters/noHardcodedOperators.ts`'s concern) — this is a small, explicit,
 * intentional scope line: relations and attachments are view-only in the table (relations
 * live in a separate link table and have their own editing surface on the record detail view;
 * attachments have one there too, in `record-detail/AttachmentField.tsx`. The set below governs
 * the *table's* inline cell edit, which stays read-only for attachments, and the card dispatches
 * on `field.type` before asking here).
 *
 * **A second caller** — `newRecordDraft.ts`, which asks this predicate
 * which fields a create form may offer. That is a different question from "may I edit this cell",
 * and the answers coincide for a reason rather than by luck: neither a `relation` nor an
 * `attachment` value lives in `records.data`. The create route makes it explicit, refusing a
 * relation value outright (`fieldtypes.py` sends the caller to `link_records`) and requiring an
 * attachment value to name ids that only an upload has already produced. So the set below is the
 * *stored-scalar* boundary, which is what both surfaces are really asking about.
 */
import type { FieldDoc } from "../api/objectTypes";

const NON_EDITABLE_FIELD_TYPES = new Set(["relation", "attachment"]);

export function isEditableFieldType(fieldType: string): boolean {
  return !NON_EDITABLE_FIELD_TYPES.has(fieldType);
}

const GROUPABLE_FIELD_TYPES = new Set(["single_select", "relation"]);

/** FR-U1: "grouping by any select or relation field." Multi-select is a select field but its
 * values aren't single group keys, so it's excluded from the groupable set on purpose. */
export function isGroupableFieldType(fieldType: string): boolean {
  return GROUPABLE_FIELD_TYPES.has(fieldType);
}

export function groupableFields(fields: FieldDoc[]): FieldDoc[] {
  return fields.filter((field) => isGroupableFieldType(field.type));
}
