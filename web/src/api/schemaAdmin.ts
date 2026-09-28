/**
 * Typed wrappers over the schema-administration routes used by the schema editor
 * (FR-U4, FR-S1 through FR-S9): object-type create/update, field add/update, and the
 * explicit proposal route used for `delete_field`/`delete_object_type` (the two
 * destructive change types that have no additive counterpart to auto-route from).
 *
 * `create_object_type` / `update_object_type` return a full `describe_object_type`
 * document (typed as `ObjectTypeDetail` in `./objectTypes.ts` — reused here, not
 * duplicated, per FR-U4's "no second schema-shape parser"). `add_field` always
 * applies immediately (FR-S5); `update_field` auto-routes to the additive or
 * destructive-proposal path and says which (`FieldUpdateResponse`).
 */
import type { FieldDoc, ObjectTypeDetail } from "./objectTypes";
import { apiRequest } from "./client";

export interface FieldSpec {
  key: string;
  name: string;
  type: string;
  description: string;
  config?: Record<string, unknown>;
  required?: boolean;
  unique?: boolean;
  indexed?: boolean;
  embed?: boolean;
  default?: unknown;
}

export interface CreateObjectTypeBody {
  key: string;
  name: string;
  name_plural: string;
  description: string;
  key_prefix: string;
  icon?: string;
  fields?: FieldSpec[];
  /** Which of `fields` labels a record. Omitted defaults to the first
   * display-eligible one, server-side. */
  display_field_key?: string | null;
}

/** `POST /api/v1/object-types` (`create_object_type`; FR-S1, FR-S2). Always additive. */
export async function createObjectType(body: CreateObjectTypeBody): Promise<ObjectTypeDetail> {
  return apiRequest<ObjectTypeDetail>("/object-types", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** `PATCH /api/v1/object-types/{key}` (`name`/`name_plural`/`description`/`icon`/
 * `default_level`/`display_field_key`; `key` and `key_prefix` are immutable and rejected
 * with `validation_failed` if attempted). */
export async function updateObjectType(
  key: string,
  changes: Record<string, unknown>,
): Promise<ObjectTypeDetail> {
  return apiRequest<ObjectTypeDetail>(`/object-types/${encodeURIComponent(key)}`, {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
}

export interface AddFieldResult {
  status: "applied";
  field: FieldDoc;
}

/** `POST /api/v1/object-types/{key}/fields` (`add_field`; FR-S4). Always additive (FR-S5). */
export async function addField(objectTypeKey: string, spec: FieldSpec): Promise<AddFieldResult> {
  return apiRequest<AddFieldResult>(
    `/object-types/${encodeURIComponent(objectTypeKey)}/fields`,
    { method: "POST", body: JSON.stringify(spec) },
  );
}

/** The blast-radius document (FR-S8): shape varies by `change_type` (see
 * `SchemaService._compute_impact`), so only the fields every change type carries are
 * typed strictly; the rest come through as an index signature and are rendered
 * generically by `BlastRadiusPanel`. */
export interface ImpactDoc {
  change_type: string;
  affected_records: number;
  sample_values?: unknown[];
  non_empty_values?: number;
  coercion_failures?: { record_key: string; value: unknown; reason: string }[];
  in_use?: { values: string[]; count: number; sample_record_keys: string[] };
  violations?: {
    constraint: string;
    count: number;
    sample_record_keys?: string[];
    duplicates?: Record<string, string[]>;
  };
  [key: string]: unknown;
}

export interface FieldUpdateApplied {
  status: "applied";
  field: FieldDoc;
  message: string;
}

export interface FieldUpdatePending {
  status: "pending_human_approval";
  proposal_id: string;
  change_type: string;
  impact: ImpactDoc;
  message: string;
}

export type FieldUpdateResponse = FieldUpdateApplied | FieldUpdatePending;

/** `PATCH /api/v1/object-types/{key}/fields/{fieldKey}` (`update_field`; FR-S5, FR-S6).
 * Auto-routes to the additive or destructive-proposal path; the response says which. */
export async function updateField(
  objectTypeKey: string,
  fieldKey: string,
  changes: Record<string, unknown>,
  reason?: string,
): Promise<FieldUpdateResponse> {
  return apiRequest<FieldUpdateResponse>(
    `/object-types/${encodeURIComponent(objectTypeKey)}/fields/${encodeURIComponent(fieldKey)}`,
    { method: "PATCH", body: JSON.stringify({ changes, reason }) },
  );
}

export interface ProposalCreatedDoc {
  status: "pending_human_approval";
  proposal_id: string;
  change_type: string;
  impact: ImpactDoc;
  message: string;
}

/** `POST /api/v1/schema-proposals` (`propose_schema_change`). Used directly for
 * `delete_field` and `delete_object_type`, the two destructive change types with no
 * additive counterpart to auto-route from via `update_field`/`update_object_type`. */
export async function proposeSchemaChange(body: {
  change_type: string;
  object_type: string;
  field_key?: string;
  payload?: Record<string, unknown>;
  reason?: string;
}): Promise<ProposalCreatedDoc> {
  return apiRequest<ProposalCreatedDoc>("/schema-proposals", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
