/**
 * Typed wrapper functions over the schema-proposal routes (`src/glosswork/routes/schema.py`):
 * listing and deciding proposals.
 *
 * The list is a **bounded page** carrying `total_count` and `next_cursor` (DD-18), and every
 * proposal carries a `target` naming the object type and field it is about in words, beside the
 * two DD-25 sidecars naming whoever raised it. Nothing in the browser resolves an id any more,
 * which is the whole reason `/inbox` can write a sentence.
 */
import { apiRequest } from "./client";
import type { ProposalTarget } from "../inbox/proposalSentence";
import type { AgentLabelLike, PrincipalLike } from "../ui/attributionDerivation";

export interface ProposalDoc {
  id: string;
  status: string;
  change_type: string;
  target_type_id: string | null;
  target_field_id: string | null;
  payload: Record<string, unknown>;
  impact: Record<string, unknown>;
  snapshot_ref: string | null;
  reason: string | null;
  proposed_at: string;
  proposed_by: string;
  proposed_agent: string | null;
  decided_at: string | null;
  decided_by: string | null;
  decision_note: string | null;
  /** The proposal's target in words. Typed nullable, but the service always resolves one. */
  target: ProposalTarget | null;
}

/** One page of proposals, newest first, plus what a page cannot say about itself. */
export interface ProposalPage {
  proposals: ProposalDoc[];
  next_cursor: string | null;
  /**
   * How many the caller could reach in total, **not** `proposals.length`.
   *
   * The sidebar's Inbox badge reads this. The list takes a `limit`, so a badge fed a length
   * would silently cap at the page size — a wrong number rendered confidently, which is what
   * the badge's `null` rather than `0` exists to avoid.
   */
  total_count: number;
  /** DD-25's two maps, keyed by id, siblings of `proposals` rather than keys inside one. */
  principals: Record<string, PrincipalLike>;
  agent_labels: Record<string, AgentLabelLike>;
}

/** `GET /api/v1/schema-proposals` (optionally filtered by `status`, e.g. `"pending"`). */
export async function listSchemaProposals(status?: string): Promise<ProposalPage> {
  return apiRequest<ProposalPage>(
    `/schema-proposals${status ? `?status=${encodeURIComponent(status)}` : ""}`,
  );
}

export interface ApproveProposalBody {
  null_non_coercible?: boolean;
  create_missing_options?: boolean;
  confirm_impact?: Record<string, unknown>;
  decision_note?: string;
}

/** `POST /api/v1/schema-proposals/{id}/approve`. */
export async function approveProposal(
  id: string,
  body: ApproveProposalBody = {},
): Promise<ProposalDoc> {
  return apiRequest<ProposalDoc>(`/schema-proposals/${encodeURIComponent(id)}/approve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** `POST /api/v1/schema-proposals/{id}/reject`. */
export async function rejectProposal(id: string, decisionNote?: string): Promise<ProposalDoc> {
  return apiRequest<ProposalDoc>(`/schema-proposals/${encodeURIComponent(id)}/reject`, {
    method: "POST",
    body: JSON.stringify(decisionNote ? { decision_note: decisionNote } : {}),
  });
}
