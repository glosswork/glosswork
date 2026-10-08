/**
 * `GET /api/v1/workspace`: what this deployment is called and who is in it.
 *
 * `docs/DESIGN.md` 4.3 puts a workspace name and an "N people · M agents" line at the top of the
 * sidebar. Neither value existed anywhere in the product, and neither could be assembled from
 * the routes that did: `GET /principals/directory` returns rows bounded at 50 rather than a
 * count, and every principal's agent labels sit behind `require_scope("admin")`. This is the one
 * bounded read that answers both, at `read` scope.
 */
import { apiRequest } from "./client";

/** A hosted trial, as the workspace reports it (change 30). */
export interface WorkspaceTrial {
  /** When the trial ends: ISO 8601 in UTC, whole seconds and a `Z`. The browser counts down to
   * it on its own clock; the workspace sends no clock reading of its own. */
  ends_at: string;
  /** `GW_SUBSCRIBE_URL`, or `null` when the operator has set none: the banner then has no
   * link. Readable by every credential on the workspace, so it never carries a secret. */
  subscribe_url: string | null;
}

export interface WorkspaceDoc {
  /**
   * `GW_WORKSPACE_NAME`, or `null` when the operator has not set one.
   *
   * Null renders the mark alone. It deliberately does **not** fall back to "Glosswork":
   * `docs/DESIGN.md` 4.3 says the product name does not appear in the shell and the workspace's
   * does, so a fallback would print the one thing 4.3 rules out.
   */
  name: string | null;
  /** Active principals of type `user`. */
  people: number;
  /**
   * Registered agent labels. FR-I6 registers a label on an agent's first call, so this counts
   * agents that have actually acted here. A service account reaches this number through the
   * label it presents; one that has never presented a label is in neither count, which is a
   * stated limitation of DD-28, not a bug.
   */
  agents: number;
  /**
   * The absolute URL an agent connects to, `GW_BASE_URL` plus `/mcp`, or `null` when the
   * operator has not set a base URL.
   *
   * Composed by the server rather than by the browser because the two do not always
   * agree and the server's answer is the correct one: `create_app` builds the `/mcp` Host and
   * Origin allowlists from `GW_BASE_URL` (DD-15), so a UI reached on some other origin would
   * otherwise print a URL this deployment refuses. The consumer is also the agent rather than
   * the person, and an agent elsewhere cannot reach a laptop's `http://localhost:8000`.
   *
   * `null` is not a gap to paper over: it means `GW_BASE_URL` is unset, which is exactly when
   * the allowlist is empty and the check disables itself, so the reader's own origin works.
   * `routes/mcpUrl.ts` holds that fallback.
   */
  mcp_url: string | null;
  /**
   * The trial, or `null` when `GW_TRIAL_ENDS_AT` is unset, which is every self-hosted
   * workspace.
   *
   * **`null` and absent are one case: no banner.** Optional in the type because a document
   * without the key is a thing the shell has met, in its own tests' fixtures, and "not null"
   * is not "an object": reading `ends_at` off `undefined` takes the whole shell down.
   */
  trial?: WorkspaceTrial | null;
}

export async function getWorkspace(): Promise<WorkspaceDoc> {
  return apiRequest<WorkspaceDoc>("/workspace");
}
