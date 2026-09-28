/**
 * Typed wrapper functions over the personal-access-token routes (FR-I4,
 * `src/glosswork/routes/identity.py`). Minting is the one call whose response carries the
 * plaintext secret (`MintedAccessTokenDoc.token`) — every other read returns
 * `AccessTokenDoc`, which never does.
 */
import { apiRequest } from "./client";
import type { Scope } from "./principals";

export interface AccessTokenDoc {
  id: string;
  principal_id: string;
  name: string;
  token_prefix: string;
  scope: Scope;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
  created_by: string;
  /** The agent label this token was minted for, or `null` for one minted without
   * one. Descriptive metadata: it grants nothing. */
  agent_label: string | null;
}

export interface MintedAccessTokenDoc extends AccessTokenDoc {
  /** Shown exactly once, in this response. No subsequent read of this or any other token ever
   * includes it again. */
  token: string;
}

/** `principal_id` defaults server-side to the caller's own principal; passing another
 * principal's id requires `admin` scope and the `admin` role (minting on a service account's
 * behalf). */
export function listAccessTokens(principalId?: string): Promise<AccessTokenDoc[]> {
  const query = principalId ? `?principal_id=${encodeURIComponent(principalId)}` : "";
  return apiRequest<{ access_tokens: AccessTokenDoc[] }>(`/access-tokens${query}`).then(
    (response) => response.access_tokens,
  );
}

export interface MintAccessTokenBody {
  name: string;
  scope: Scope;
  principal_id?: string;
  expires_at?: string;
  /** Optional. A malformed label is refused by the server at mint, never at use. */
  agent_label?: string;
}

export function mintAccessToken(body: MintAccessTokenBody): Promise<MintedAccessTokenDoc> {
  return apiRequest<MintedAccessTokenDoc>("/access-tokens", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function revokeAccessToken(id: string): Promise<AccessTokenDoc> {
  return apiRequest<AccessTokenDoc>(`/access-tokens/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}
