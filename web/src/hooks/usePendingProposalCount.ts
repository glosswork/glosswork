/**
 * The Inbox count, and the one gate that decides whether it is asked for.
 *
 * **Measured, not assumed:** `GET /api/v1/schema-proposals` declares `require_scope("admin")`
 * (`routes/schema.py`), and `role_scope` maps `member -> write`, so a member's session is
 * refused with `403 insufficient_scope`. Calling it unconditionally from the shell would fire a
 * guaranteed 403 on every page a member opens.
 *
 * **That failure shape is not hypothetical.** An agent-labels panel with no role gate once made
 * a member's Settings page fire `GET /admin/agent-labels` and `GET /principals` and read "Could
 * not load agent labels." under a heading promising everyone's; `people/AgentLabelsTable.tsx`
 * chooses one read by role instead. The lesson stands whether or not an instance of it currently
 * ships: a panel that asks for what its caller may not have teaches the 403 rather than the gate.
 *
 * So the gate is `principal.scope`, the credential's own ceiling as `GET /api/v1/me` reports it,
 * rather than a role string: `roleScope` already exists precisely so a third role is one line
 * here instead of three silent behaviour changes elsewhere (DD-11).
 *
 * This gates an **affordance**, never authorization (DD-42). The server is and remains the
 * boundary; a caller who reaches the route without the scope is still refused by it.
 */
import { useQuery } from "@tanstack/react-query";

import { listSchemaProposals } from "../api/schemaProposals";
import type { Scope } from "../api/principals";

/** Shared with `inbox/InboxPage.tsx` so the sidebar's count and the Inbox are one cache entry:
 * deciding a proposal there updates the badge here without a second request. */
export const pendingProposalsQueryKey = ["schema-proposals", "pending"] as const;

/**
 * The number of pending schema proposals, or `null` when this caller's credential cannot read
 * them.
 *
 * `null` is not zero and the distinction is the point: zero would be the client asserting
 * "nothing is waiting" when it was in fact refused permission to look. The sidebar renders the
 * Inbox item either way and simply omits the badge, so nothing disappears from the navigation.
 */
export function usePendingProposalCount(scope: Scope | undefined): number | null {
  const canRead = scope === "admin";
  const { data } = useQuery({
    queryKey: pendingProposalsQueryKey,
    queryFn: () => listSchemaProposals("pending"),
    enabled: canRead,
  });

  if (!canRead || data === undefined) return null;
  // `total_count`, never `data.proposals.length`. The route takes a `limit`, so a length would
  // silently cap at the page size. A badge reading "50" against 200 waiting proposals is a wrong
  // number rendered
  // confidently -- the same failure `null`-is-not-zero exists to avoid, one layer along.
  return data.total_count;
}
