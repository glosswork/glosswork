/**
 * Typed wrapper functions over the agent-label routes (FR-I6, FR-I7,
 * `src/glosswork/routes/agent_labels.py`): the caller's own agent labels, the admin
 * cross-user view, and renaming/describing a label.
 */
import { apiRequest } from "./client";

export interface AgentLabelDoc {
  id: string;
  principal_id: string;
  label: string;
  display_name: string | null;
  description: string | null;
  verified: boolean;
  first_seen_at: string;
  last_seen_at: string;
  call_count: number;
}

/**
 * One entry of the `agent_labels` sidecar that rides on every response document carrying a
 * record, keyed by agent-label id.
 *
 * **Two keys, and the projection is a disclosure decision.** Cross-principal label
 * reads are admin-gated — `listAgentLabels(true)` above needs `admin` scope *and* the `admin`
 * role — while this map hands a label's **name** to anyone who may read the record it wrote.
 * That is the point of the change (a `By` column that says "sales-agent" is the feature) and it
 * is no more than the audit trail already gives the same caller as an id. What it does not
 * carry: `principal_id` (whose label it is), `call_count` and `last_seen_at` (FR-I7 telemetry,
 * which is what the admin view is for), `description`, `first_seen_at` and `verified`.
 *
 * A separate interface from `AgentLabelDoc` rather than a `Pick<>` of it, for the reason
 * `PrincipalRef` is separate from `PrincipalDirectoryEntry`: these are two documents from two
 * routes, and a shape that overlaps today is not a reason for a change to one to silently
 * retype the other.
 */
export interface AgentLabelRef {
  label: string;
  display_name: string | null;
}

/** The map itself: every agent-label id a response document references, resolved to its name. */
export type AgentLabelSidecar = Record<string, AgentLabelRef>;

/**
 * `GET /api/v1/agent-labels` (own labels, scope `read`) or `GET /api/v1/admin/agent-labels`
 * (admin cross-user view, FR-I7, scope `admin`).
 *
 * Two routes rather than an `?all=true` parameter on one: a route declares its required scope
 * exactly once where it is registered, and a single route could not raise its own
 * requirement from `read` to `admin` for one parameter value without checking scope inside the
 * handler.
 */
export async function listAgentLabels(all = false): Promise<AgentLabelDoc[]> {
  const response = await apiRequest<{ labels: AgentLabelDoc[] }>(
    all ? "/admin/agent-labels" : "/agent-labels",
  );
  return response.labels;
}

/**
 * One entry of `GET /api/v1/agent-labels/directory` — the agent picker's options on
 * `/activity`, and the one route in this family any authenticated principal may read.
 *
 * **Three keys, and the reason it may be read at `read` scope is not the projection.** The
 * route scopes its rows to the labels appearing on audit events the caller may read, using the
 * same restriction `AuditService.search` applies, so it returns exactly what an unfiltered
 * `/audit-events` walk by the same caller would already reveal. `principal_id`, `call_count`,
 * `first_seen_at`, `last_seen_at`, `verified` and `description` are FR-I7 telemetry and stay on
 * the admin route.
 *
 * A separate interface from `AgentLabelRef` rather than a widening of it, for the reason
 * `PrincipalDirectoryEntry` is separate from `PrincipalRef`: two documents from two routes, and
 * a shape that overlaps today is not a reason for a change to one to silently retype the other.
 */
export interface AgentLabelDirectoryEntry {
  id: string;
  label: string;
  display_name: string | null;
}

export interface AgentLabelDirectoryOptions {
  q?: string;
  limit?: number;
}

/** The directory read: what makes an agent filter pickable rather than a UUID field. */
export async function fetchAgentLabelDirectory(
  options: AgentLabelDirectoryOptions = {},
): Promise<AgentLabelDirectoryEntry[]> {
  const params = new URLSearchParams();
  if (options.q) params.set("q", options.q);
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  const query = params.toString();
  const response = await apiRequest<{ agent_labels: AgentLabelDirectoryEntry[] }>(
    `/agent-labels/directory${query ? `?${query}` : ""}`,
  );
  return response.agent_labels;
}

export interface UpdateAgentLabelBody {
  display_name?: string;
  description?: string;
}

/** `PATCH /api/v1/agent-labels/{id}` (FR-I6): must belong to the caller; marks the label
 * `verified` once named. */
export async function updateAgentLabel(
  id: string,
  body: UpdateAgentLabelBody,
): Promise<AgentLabelDoc> {
  return apiRequest<AgentLabelDoc>(`/agent-labels/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
