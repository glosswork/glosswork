/**
 * Typed wrapper functions over the record CRUD/query routes (`get_record`, `update_record`,
 * `query_records`, docs/MCP_TOOLS.md 5.1-5.2).
 *
 * `QueryBody` and `UpdateRecordBody` are precisely generated request-body types (`schema.ts`
 * `components["schemas"]`), reused here rather than duplicated. The response bodies are all
 * `dict[str, Any]` from FastAPI, so the generator only sees `{ [key: string]: unknown }`; the
 * interfaces below describe the real shape produced by `envelopes.py` (`record_doc`,
 * `query_result_doc`).
 */
import type { components } from "./schema";
import { apiRequest } from "./client";
import type { CommentDoc } from "./comments";
import type { AgentLabelSidecar } from "./agentLabels";
import type { PrincipalSidecar } from "./principals";

/**
 * `PrincipalSidecar` lives in `./principals`, beside the `PrincipalRef` it is a map of, not
 * here: the grants document carries one too, and a grants module importing a principal type
 * out of the records module would be the wrong dependency. Optional on every interface below
 * because it is **additive**: the stored value is still a bare principal id, and a client that
 * ignores the map still works.
 */

export type QueryBody = components["schemas"]["QueryBody"];
export type UpdateRecordBody = components["schemas"]["UpdateRecordBody"];
export type LinkBody = components["schemas"]["LinkBody"];
export type BulkUpdateBody = components["schemas"]["BulkUpdateBody"];

/** `bulk_update`'s response (`envelopes.py`; docs/MCP_TOOLS.md 5.2). */
export interface BulkUpdateResult {
  affected_count: number;
  sample_keys: string[];
  dry_run: boolean;
}

export interface RecordDoc {
  id: string;
  key: string;
  version: number;
  created_at: string;
  created_by: string;
  updated_at: string;
  updated_by: string;
  /** The agent label of the write that last changed a field value, or null when a
   * person made it. Resolved to text through the response's `agent_labels` sidecar; the raw id
   * is never rendered (docs/DESIGN.md 6.4). Delete and restore do not move it, for the same
   * reason they do not move `updated_by`. */
  updated_by_agent_label_id: string | null;
  deleted_at: string | null;
  comment_count: number;
  last_comment_at: string | null;
  data: Record<string, unknown>;
  /** One-level relation expansion (FR-L6), present only for fields named in the query's
   * `expand_relations`. See `src/glosswork/services/records.py::_expand_record`: each
   * summary is the linked record's key, id, and its target type's display-field value. */
  expand?: Record<string, ExpandedLinkSummary[]>;
  /** Present on the four write responses (`create`, `update`, `delete`,
   * `restore`) **and on `get_record`**, absent on a record that arrived inside a `QueryResult`,
   * whose map is a sibling of `records` rather than a key on each row.
   *
   * `get_record` is the path the record page reads, so a name in its header is already on the
   * wire and needs no backend change. `envelopes.py::record_with_includes_doc:427` composes it,
   * one of the six paths that carry the map. */
  principals?: PrincipalSidecar;
  /** Present wherever `principals` is, `get_record` included
   * (`envelopes.py::record_with_includes_doc:428`). */
  agent_labels?: AgentLabelSidecar;
}

/** One linked record inside a query result's `expand[fieldKey]` (FR-L6, relation grouping). */
export interface ExpandedLinkSummary {
  key: string;
  id: string;
  display: unknown;
}

export interface QueryResult {
  records: RecordDoc[];
  total_count: number;
  next_cursor: string | null;
  truncated: boolean;
  guidance?: string;
  /** One map for the whole page, not one per row. */
  principals?: PrincipalSidecar;
  /** The same, for every agent label this page references. */
  agent_labels?: AgentLabelSidecar;
}

export interface GetRecordOptions {
  include?: string[];
  expandRelations?: string[];
  fields?: string[];
}

/** One linked record as returned inside `get_record`'s `links` include: just enough to render
 * and to navigate to it. `display` is the target type's display-field value
 * (`records.py::list_link_summaries`), typed `unknown` to match `ExpandedLinkSummary` rather
 * than inventing a second shape for the same backend value: a display field may be an
 * `integer` or a `date` as readily as a `short_text`, and it is null when the target type has
 * no eligible field or the record's value is empty. */
export interface LinkedRecordRef {
  key: string;
  id: string;
  display: unknown;
}

/** One field-level audit row (`envelopes.py::audit_event_doc`, FR-D1 through FR-D6). */
export interface AuditEventDoc {
  id: number;
  ts: string;
  request_id: string;
  principal_id: string;
  principal_type: string;
  agent_label_id: string | null;
  /** The agent label's text, resolved at the repository (DD-25). Null when the write
   * carried no label, which is the ordinary case — the primitive then renders no agent at all
   * rather than fabricating one (docs/DESIGN.md 6.5). */
  agent_label: string | null;
  auth_method: string;
  surface: string;
  entity_type: string;
  entity_id: string;
  record_id: string | null;
  object_type_id: string | null;
  action: string;
  field_key: string | null;
  old_value: unknown;
  new_value: unknown;
  note: string | null;
  /** DD-25: the acting principal's human label, resolved at the repository layer. Null when
   * the referent is gone; render `principal_id` instead. */
  principal_display_name: string | null;
  /** DD-25: the key of the record this event is about, or null for an event that is about no
   * record at all — every schema-level event. */
  record_key: string | null;
}

export interface HistoryPage {
  events: AuditEventDoc[];
  next_cursor: string | null;
}

/**
 * One resolved entry of the `attachments` sidecar (`include=attachments`). These four keys are
 * the ones this SPA reads; the server projects eight, `envelopes.py::attachment_doc`
 * being the one definition REST metadata, `get_attachment` and this sidecar all share. Named
 * rather than left as `unknown` because the record card renders a filename and a
 * size off it.
 *
 * **The sidecar is not the field value.** `AttachmentService.get_many` silently omits an id
 * that names no row *or that the attachment read rule says this caller may not read*, and the
 * two are indistinguishable here, so this list may be shorter than `data[fieldKey]`. The stored
 * ids are the record's own; anything that composes a new value composes it from those.
 */
export interface AttachmentRef {
  id: string;
  filename: string;
  content_type: string;
  byte_size: number;
}

/** A `get_record` response, plus whichever `include` sections were requested. */
export interface RecordWithIncludes extends RecordDoc {
  comments?: CommentDoc[];
  links?: Record<string, LinkedRecordRef[]>;
  history?: AuditEventDoc[];
  attachments?: Record<string, AttachmentRef[]>;
}

function buildRecordQuery(options: GetRecordOptions): string {
  const params = new URLSearchParams();
  if (options.include?.length) {
    params.set("include", options.include.join(","));
  }
  if (options.expandRelations?.length) {
    params.set("expand_relations", options.expandRelations.join(","));
  }
  if (options.fields?.length) {
    params.set("fields", options.fields.join(","));
  }
  const search = params.toString();
  return search ? `?${search}` : "";
}

/** `GET /api/v1/records/{ref}` (`get_record`). `ref` accepts a human key or a UUID. */
export async function getRecord(
  ref: string,
  options: GetRecordOptions = {},
): Promise<RecordWithIncludes> {
  return apiRequest<RecordWithIncludes>(
    `/records/${encodeURIComponent(ref)}${buildRecordQuery(options)}`,
  );
}

/**
 * `POST /api/v1/object-types/{objectTypeKey}/records` (`create_record`, FR-R1).
 *
 * **The body is the values object itself**, not `{values: ...}`: `routes/records.py::create_record`
 * declares `values: dict[str, Any]` as the whole request body, which is where it differs from
 * `updateRecord` below and the one thing about this call that is easy to get wrong.
 *
 * Two kinds of field are not submittable here and the caller must not offer them (DD-44).
 * A `relation` key is refused outright — `fieldtypes.py` sends it to `link_records`,
 * because a relation's values live in the link table rather than in `records.data` — and an
 * `attachment` value must name ids that an upload has already produced, which at create time do
 * not exist. Both are managed on the record page (FR-U2).
 *
 * The server fills in each omitted field's `default_value` **before** it checks which required
 * fields are missing, so omitting a key is not the same as sending null: omission can be
 * satisfied by a default, and null cannot. `newRecordDraft.ts` is what holds that distinction.
 */
export async function createRecord(
  objectTypeKey: string,
  values: Record<string, unknown>,
): Promise<RecordDoc> {
  return apiRequest<RecordDoc>(`/object-types/${encodeURIComponent(objectTypeKey)}/records`, {
    method: "POST",
    body: JSON.stringify(values),
  });
}

/** `PATCH /api/v1/records/{ref}` (`update_record`). */
export async function updateRecord(ref: string, body: UpdateRecordBody): Promise<RecordDoc> {
  return apiRequest<RecordDoc>(`/records/${encodeURIComponent(ref)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** `POST /api/v1/object-types/{objectTypeKey}/bulk-update` (`bulk_update`, FR-R10). Call with
 * `dry_run: true` first to preview `affected_count` before confirming with `dry_run: false`. */
export async function bulkUpdateRecords(
  objectTypeKey: string,
  body: BulkUpdateBody,
): Promise<BulkUpdateResult> {
  return apiRequest<BulkUpdateResult>(
    `/object-types/${encodeURIComponent(objectTypeKey)}/bulk-update`,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}

/** `DELETE /api/v1/records/{ref}` (`delete_record`). Soft-deletes; rejected with
 * `relation_blocked` (FR-L4) when other live records still link to this one. */
export async function deleteRecord(ref: string): Promise<RecordDoc> {
  return apiRequest<RecordDoc>(`/records/${encodeURIComponent(ref)}`, {
    method: "DELETE",
  });
}

/** `POST /api/v1/object-types/{objectTypeKey}/query` (`query_records`). */
export async function queryRecords(
  objectTypeKey: string,
  body: QueryBody,
): Promise<QueryResult> {
  return apiRequest<QueryResult>(`/object-types/${encodeURIComponent(objectTypeKey)}/query`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface GetRecordHistoryOptions {
  fieldKey?: string;
  limit?: number;
  cursor?: string;
}

function buildHistoryQuery(options: GetRecordHistoryOptions): string {
  const params = new URLSearchParams();
  if (options.fieldKey !== undefined) {
    params.set("field_key", options.fieldKey);
  }
  if (options.limit !== undefined) {
    params.set("limit", String(options.limit));
  }
  if (options.cursor !== undefined) {
    params.set("cursor", options.cursor);
  }
  const search = params.toString();
  return search ? `?${search}` : "";
}

/** `GET /api/v1/records/{ref}/history` (`get_record_history`): field-level audit,
 * keyset-paginated via `limit`/`cursor` (FR-D5). */
export async function getRecordHistory(
  ref: string,
  options: GetRecordHistoryOptions = {},
): Promise<HistoryPage> {
  return apiRequest<HistoryPage>(
    `/records/${encodeURIComponent(ref)}/history${buildHistoryQuery(options)}`,
  );
}

/** `POST /api/v1/records/{ref}/links/{fieldKey}` (`link_records`). */
export async function linkRecords(
  ref: string,
  fieldKey: string,
  toRecords: string[],
): Promise<void> {
  await apiRequest(
    `/records/${encodeURIComponent(ref)}/links/${encodeURIComponent(fieldKey)}`,
    {
      method: "POST",
      body: JSON.stringify({ to_records: toRecords } satisfies LinkBody),
    },
  );
}

/** `DELETE /api/v1/records/{ref}/links/{fieldKey}` (`unlink_records`). */
export async function unlinkRecords(
  ref: string,
  fieldKey: string,
  toRecords: string[],
): Promise<void> {
  await apiRequest(
    `/records/${encodeURIComponent(ref)}/links/${encodeURIComponent(fieldKey)}`,
    {
      method: "DELETE",
      body: JSON.stringify({ to_records: toRecords } satisfies LinkBody),
    },
  );
}
