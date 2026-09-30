/**
 * Typed wrappers over the invite routes (change 9, `src/glosswork/routes/identity.py`). They
 * exist only on a workspace that signs people in by emailed code; elsewhere every one answers
 * `feature_disabled`, and nothing in the UI calls them.
 */
import { apiRequest } from "./client";
import type { PrincipalRole } from "./principals";

/** One pending invite (`envelopes.py::invite_doc`). No person exists until the invited address
 * signs in with a code. */
export interface InviteDoc {
  id: string;
  email: string;
  display_name: string;
  role: PrincipalRole;
  invited_by: string;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
}

/** What became of an invite's email. The invite is saved whatever the outcome. */
export interface InviteEmailResult {
  outcome: "accepted" | "refused_credential" | "refused_fields" | "rate_limited" | "unavailable";
  message: string;
}

export interface CreatedInvite {
  invite: InviteDoc;
  email: InviteEmailResult;
}

export interface CreateInviteBody {
  email: string;
  display_name: string;
  role: PrincipalRole;
}

export async function listInvites(): Promise<InviteDoc[]> {
  const body = await apiRequest<{ invites: InviteDoc[] }>("/invites");
  return body.invites;
}

export function createInvite(body: CreateInviteBody): Promise<CreatedInvite> {
  return apiRequest<CreatedInvite>("/invites", { method: "POST", body: JSON.stringify(body) });
}

export function revokeInvite(id: string): Promise<InviteDoc> {
  return apiRequest<InviteDoc>(`/invites/${encodeURIComponent(id)}`, { method: "DELETE" });
}
