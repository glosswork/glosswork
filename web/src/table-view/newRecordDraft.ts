/**
 * The four pure rules behind the create-record form (DD-44). Kept out of
 * `NewRecordDialog.tsx`'s render body per AGENTS.md, and separate from `fieldWidgets.ts` because
 * these are about a *whole record that does not exist yet*, where that module is about one value.
 *
 * Two of the four exist because the obvious version is wrong against the real server. Both were
 * measured against a running deployment rather than reasoned about, and both would have shipped
 * silently: nothing in the type system or the test suite would have objected.
 */
import type { FieldDoc } from "../api/objectTypes";
import { isEditableFieldType } from "./fieldEditability";
import { draftFromStoredValue, parseEditedValue, type EditDraft } from "./fieldWidgets";

export type RecordDraft = Record<string, EditDraft>;

/**
 * The fields a create form may offer, in schema order.
 *
 * **Filters; never sorts.** `SqliteSchemaRepository.list_fields` is `ORDER BY position, key`, so
 * the wire already delivers schema order and re-deriving it here would be a second answer to a
 * question the backend has answered — and `api/oneDisplayFieldRule.test.ts` fails any module that
 * reads a field's position property, precisely to stop that. (That test greps rather than parses,
 * so it cannot tell a comment from code: a version of this paragraph that named the property
 * outright, in the course of explaining why nothing here touches it, failed the check.)
 *
 * The predicate is `isEditableFieldType`, shared with the table's inline cell edit rather than
 * duplicated. The two surfaces ask different questions — "may I edit this cell" and "may I set
 * this at create" — and get the same answer for the same underlying reason: `relation` and
 * `attachment` values do not live in `records.data`.
 */
export function creatableFields(fields: FieldDoc[]): FieldDoc[] {
  return fields.filter((field) => isEditableFieldType(field.type));
}

/** The draft a fresh form starts from: each field's own `default`, through the same conversion
 * the table cell uses when it opens an editor over a stored value. */
export function initialDraft(fields: FieldDoc[]): RecordDraft {
  return Object.fromEntries(
    creatableFields(fields).map((field) => [field.key, draftFromStoredValue(field, field.default)]),
  );
}

/** Structural equality over the three shapes `EditDraft` can take (`string | boolean | string[]`).
 * Only the array case needs more than `===`. */
function sameDraft(a: EditDraft, b: EditDraft): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return false;
    return a.length === b.length && a.every((value, index) => value === b[index]);
  }
  return a === b;
}

/**
 * The values to POST: every field the person actually touched, and nothing else.
 *
 * **The rule is "omit what is unchanged", not "omit nulls", and the difference is load-bearing.**
 * `parseEditedValue` can never return null for a `boolean` (it returns `draft === true`) or for a
 * `multi_select` (it returns the array, empty at worst). So an omit-nulls rule would have posted a
 * value for both of those on every create, whether or not the person went near them — and measured
 * against a real server that produces two distinct bugs:
 *
 *   - an untouched checkbox posts `false`, **overwriting a field's `default_value: true`**, because
 *     the server only applies a default to a key the request omits; and
 *   - an untouched multi-select posts `[]`, storing an empty array where `fieldtypes.py` says
 *     "absent optional values omit the key entirely".
 *
 * Comparing against `initialDraft` fixes both without special-casing either, and keeps working for
 * field types nobody has added yet. It also means a person who deliberately clears a defaulted
 * field still sends the cleared value, because their draft no longer matches the initial one.
 */
export function draftToValues(fields: FieldDoc[], draft: RecordDraft): Record<string, unknown> {
  const initial = initialDraft(fields);
  const values: Record<string, unknown> = {};
  for (const field of creatableFields(fields)) {
    const current = draft[field.key];
    if (current === undefined) continue;
    if (sameDraft(current, initial[field.key])) continue;
    values[field.key] = parseEditedValue(field, current);
  }
  return values;
}

/** Whether a draft value counts as "nothing was entered". A `boolean` never does: both of its
 * states are a value a person chose. */
function isEmptyDraft(draft: EditDraft | undefined): boolean {
  if (draft === undefined) return true;
  if (typeof draft === "boolean") return false;
  if (Array.isArray(draft)) return draft.length === 0;
  return draft.trim() === "";
}

/**
 * The keys of required fields the person has left empty — the pre-flight that puts a refusal in
 * front of the field it is about instead of behind a 422.
 *
 * **A required field carrying a default is not missing**, and that is not a convenience: it is the
 * server's own rule. `RecordService.create_record` applies every omitted field's `default_value`
 * *and then* computes which required fields are absent, so a create that omits a defaulted
 * required field succeeds. Measured: posting `{"name": "C"}` to a type whose required `stage`
 * defaults to `"draft"` returns 200 and stores `"draft"`. A pre-flight without this clause would
 * refuse, in the client, a write the server was going to accept — the one failure mode a
 * client-side mirror of a server rule must not have.
 *
 * Relations are excluded on the way in by `creatableFields`, which matches the server's own
 * `f.type != "relation"` exclusion from the same check, for the same reason.
 */
export function missingRequired(fields: FieldDoc[], draft: RecordDraft): string[] {
  return creatableFields(fields)
    .filter((field) => field.required)
    .filter((field) => field.default === null || field.default === undefined)
    .filter((field) => isEmptyDraft(draft[field.key]))
    .map((field) => field.key);
}
