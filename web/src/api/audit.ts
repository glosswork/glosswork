/**
 * Typed wrapper functions over the audit search and revert routes (FR-U8, FR-D6, DD-21).
 *
 * `AuditEventDoc` / `HistoryPage` are the exact same shapes `records.ts` already types for
 * `get_record_history` (`envelopes.py::audit_event_doc`), reused here rather than redefined —
 * `GET /api/v1/audit-events` returns the identical `{events, next_cursor}` envelope.
 */
import { apiRequest } from "./client";
import type { HistoryPage, RecordDoc } from "./records";

export interface AuditSearchOptions {
  record?: string;
  principalId?: string;
  agentLabelId?: string;
  objectType?: string;
  fieldKey?: string;
  since?: string;
  until?: string;
  limit?: number;
  cursor?: string;
}

function buildAuditSearchQuery(options: AuditSearchOptions): string {
  const params = new URLSearchParams();
  if (options.record) params.set("record", options.record);
  if (options.principalId) params.set("principal_id", options.principalId);
  if (options.agentLabelId) params.set("agent_label_id", options.agentLabelId);
  if (options.objectType) params.set("object_type", options.objectType);
  if (options.fieldKey) params.set("field_key", options.fieldKey);
  if (options.since) params.set("since", options.since);
  if (options.until) params.set("until", options.until);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.cursor) params.set("cursor", options.cursor);
  const search = params.toString();
  return search ? `?${search}` : "";
}

/** `GET /api/v1/audit-events` (`AuditService.search`, FR-U8): audit events filtered by any
 * combination of record, principal, agent label, object type, field, or time range, newest
 * first and keyset-paginated exactly like `get_record_history`. */
export async function searchAuditEvents(
  options: AuditSearchOptions = {},
): Promise<HistoryPage> {
  return apiRequest<HistoryPage>(`/audit-events${buildAuditSearchQuery(options)}`);
}

/** `POST /api/v1/audit-events/{eventId}/revert` (`RecordService.revert_field_change`, FR-D6,
 * DD-21): reverts one field-update audit event to its `old_value` as a new forward-audited
 * write. Only valid for `entity_type === "record" && action === "update"` events; the caller is
 * responsible for only offering this action on such rows. */
export async function revertFieldChange(
  eventId: number,
  expectedVersion: number,
  force = false,
): Promise<RecordDoc> {
  return apiRequest<RecordDoc>(`/audit-events/${eventId}/revert`, {
    method: "POST",
    body: JSON.stringify({ expected_version: expectedVersion, force }),
  });
}

/** `POST /api/v1/records/{ref}/revert-to-version` (`RecordService.revert_to_version`, FR-D6,
 * DD-21): reverts the whole record's fields to their state at `targetVersion`. `ref` accepts a
 * human key or a UUID. */
export async function revertToVersion(
  ref: string,
  targetVersion: number,
  expectedVersion: number,
  force = false,
): Promise<RecordDoc> {
  return apiRequest<RecordDoc>(`/records/${encodeURIComponent(ref)}/revert-to-version`, {
    method: "POST",
    body: JSON.stringify({
      target_version: targetVersion,
      expected_version: expectedVersion,
      force,
    }),
  });
}
