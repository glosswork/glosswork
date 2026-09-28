/**
 * `/inbox` and `/inbox/:proposalId` (docs/DESIGN.md 8.4 and 9).
 *
 * Two panes at or above 960px — a 300px list and a detail — and below it, list *then* detail:
 * `/inbox` is the list and `/inbox/:proposalId` is the proposal. That split is why the selection
 * is a route and not `useState`. With in-memory state the Back button leaves the Inbox
 * instead of returning to the list, and a proposal has no URL — which would make the message the
 * API sends every agent ("approve proposal X in the Glosswork UI, under Inbox > /inbox/X") a
 * dead end.
 *
 * **One request feeds both panes.** The detail renders from the row already in the list page,
 * not from a second fetch of `/schema-proposals/{id}`: the list carries `target` and both DD-25
 * sidecars, so everything the detail needs is already here, and a second request would be a
 * second copy of the same document that could disagree with the first.
 *
 * **It reads and writes through `pendingProposalsQueryKey`**, which is the cache entry the
 * sidebar's Inbox badge reads (`hooks/usePendingProposalCount.ts`). That sharing is deliberate:
 * deciding a proposal updates the badge without a second request. Otherwise approving the only
 * pending proposal would leave the badge reading `1` until a reload, on the one action this
 * screen exists for.
 *
 * **Who sees what** (DD-42). Every signed-in principal reaches the route; a caller whose
 * credential cannot read proposals gets the heading and one sentence, and **fires no request** —
 * all five proposal routes declare `require_scope("admin")`, the `GET` included, so asking would
 * be a guaranteed 403. Approve and Decline are gated on the `admin` role, a known simplification
 * of the real rule (see `canDecide` below).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  approveProposal,
  listSchemaProposals,
  rejectProposal,
  type ProposalDoc,
  type ProposalPage,
} from "../api/schemaProposals";
import { useAuth } from "../auth/useAuth";
import { pendingProposalsQueryKey } from "../hooks/usePendingProposalCount";
import { Alert } from "../ui/Alert";
import { EmptyState } from "../ui/EmptyState";
import { Spinner } from "../ui/Spinner";
import { useIsWideViewport } from "../hooks/useIsWideViewport";
import {
  INBOX_EMPTY_BODY,
  INBOX_EMPTY_TITLE,
  inboxAccessMessage,
} from "./inboxAccessMessage";
import { NoProposalSelected, ProposalDetail, ProposalListItem } from "./ProposalDetail";

export function InboxPage() {
  const { proposalId } = useParams<{ proposalId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { principal } = useAuth();
  const isWide = useIsWideViewport();

  // Gated on the credential's own scope, exactly as the sidebar badge is: a member's
  // session never fires the 403 that route would return.
  const canRead = principal?.scope === "admin";
  const accessMessage = inboxAccessMessage(principal?.scope);
  // The role, not the level. The real rule is `admin` on the proposal's own object type,
  // which the browser still cannot compute — `target` deliberately carries no `your_access`.
  const canDecide = principal?.role === "admin";

  const { data, isLoading, isError } = useQuery({
    queryKey: pendingProposalsQueryKey,
    queryFn: () => listSchemaProposals("pending"),
    enabled: canRead,
  });

  /** Drop a decided proposal from the shared cache entry, which is what keeps the sidebar badge
   * honest without a second request. `total_count` moves with it: it is the number the badge
   * reads, and leaving it behind would be the page-length bug in a different costume. */
  const removeFromCache = (id: string) => {
    queryClient.setQueryData<ProposalPage>(pendingProposalsQueryKey, (old) =>
      old === undefined
        ? old
        : {
            ...old,
            proposals: old.proposals.filter((proposal) => proposal.id !== id),
            total_count: Math.max(0, old.total_count - 1),
          },
    );
  };

  const decide = (id: string) => {
    removeFromCache(id);
    navigate("/inbox");
  };

  const approve = useMutation({
    mutationFn: (id: string) => approveProposal(id),
    onSuccess: (_result, id) => decide(id),
  });
  const decline = useMutation({
    mutationFn: (id: string) => rejectProposal(id),
    onSuccess: (_result, id) => decide(id),
  });

  const proposals: ProposalDoc[] = data?.proposals ?? [];
  const selected = proposalId ? proposals.find((p) => p.id === proposalId) : undefined;

  if (!canRead) {
    return (
      <div>
        <h1 className="font-display text-[28px] text-ink">Inbox</h1>
        {/* DD-42: hiding is never the only signal. The page renders, and says why it is empty. */}
        <div className="mt-4 max-w-xl">
          <Alert tone="info" data-testid="inbox-access-banner" title={accessMessage ?? ""} />
        </div>
      </div>
    );
  }

  const list = (
    <div data-testid="inbox-list">
      {isLoading && <Spinner label="Loading proposals..." />}
      {isError && <Alert tone="error" title="Could not load schema proposals." />}
      {!isLoading && !isError && proposals.length === 0 && (
        <EmptyState title={INBOX_EMPTY_TITLE} bordered={false} data-testid="inbox-empty">
          {INBOX_EMPTY_BODY}
        </EmptyState>
      )}
      <ul>
        {proposals.map((proposal) => (
          <li key={proposal.id} className="border-b border-line last:border-b-0">
            <Link
              to={`/inbox/${proposal.id}`}
              className="block hover:bg-raised"
              aria-current={proposal.id === proposalId ? "page" : undefined}
              data-testid={`proposal-${proposal.id}`}
            >
              <ProposalListItem
                proposal={proposal}
                principals={data?.principals ?? {}}
                agentLabels={data?.agent_labels ?? {}}
                selected={proposal.id === proposalId}
              />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );

  const detail = selected ? (
    <ProposalDetail
      proposal={selected}
      principals={data?.principals ?? {}}
      agentLabels={data?.agent_labels ?? {}}
      approverName={principal?.display_name ?? "you"}
      canDecide={canDecide}
      onApprove={() => approve.mutate(selected.id)}
      onDecline={() => decline.mutate(selected.id)}
      pending={approve.isPending || decline.isPending}
    />
  ) : proposalId ? (
    // A proposal that is not in the pending page: already approved or declined by someone
    // else, or never existed. Both read the same from here, and saying so is better than an
    // empty pane (DD-27's instinct).
    <EmptyState title="That proposal is no longer pending." bordered={false}>
      It may have been approved or declined already.
    </EmptyState>
  ) : (
    <NoProposalSelected />
  );

  // Below 960px the two panes become two screens (docs/DESIGN.md 9). `useIsWideViewport` is the
  // same hook the shell uses, so the breakpoint has one definition.
  if (!isWide) {
    return (
      <div>
        {proposalId ? (
          <>
            <Link to="/inbox" className="text-sm text-human-ink hover:underline">
              ← All proposals
            </Link>
            <div className="mt-3">{detail}</div>
          </>
        ) : (
          <>
            <h1 className="font-display text-[28px] text-ink">Inbox</h1>
            <div className="mt-4">{list}</div>
          </>
        )}
      </div>
    );
  }

  return (
    <div>
      <h1 className="font-display text-[28px] text-ink">Inbox</h1>
      {(approve.isError || decline.isError) && (
        <div className="mt-4 max-w-xl">
          <Alert
            tone="error"
            title="Could not record that decision."
            error={approve.error ?? decline.error}
          />
        </div>
      )}
      <div className="mt-4 flex gap-6">
        <div className="w-[300px] shrink-0 rounded-card border border-line bg-surface">{list}</div>
        <div className="min-w-0 flex-1">{detail}</div>
      </div>
    </div>
  );
}
