/**
 * `/people` (docs/DESIGN.md 8.6): the people and the agents on this deployment, as a
 * directory rather than as a settings screen.
 *
 * **Split by question rather than by permission.** One settings page interleaving what is yours
 * with what you administer (your own agent labels, then everybody's, then your own tokens, then
 * admin panels) is a scroll. This page answers "who is here", `/setup` answers "how is this
 * deployment configured", and `/settings` redirects here.
 *
 * **Pending schema proposals are not here.** They are on `/inbox`, a screen about a decision
 * (docs/DESIGN.md 8.4). So this page has no exemption from DD-42's "hiding is never the only
 * signal" rule: the one deliberate exception is the proposals screen, being cross-type and so
 * having no single object type to name in a banner. See
 * `access/hidingIsNeverTheOnlySignal.test.ts`, which pins `inbox/InboxPage.tsx`.
 *
 * **A non-admin is told why this page is nearly empty.** `GET /api/v1/principals` declares the
 * `admin` role (FR-I10), so two of the three cards are admin-only, and a page titled "People &
 * agents" showing neither would be promising what it withholds. The sentence follows 8.1's own
 * precedent for the Inbox badge, where an absence gets a reason rather than a silence. It is
 * deliberately **not** `access/ReadOnlyBanner.tsx`: that sentence names one object type and the
 * level held on it (DD-42), and this is a deployment-wide role, not a grant.
 */
import { useAuth } from "../auth/useAuth";
import { AgentLabelsTable } from "./AgentLabelsTable";
import { PeopleTable } from "./PeopleTable";
import { ServiceAccountsTable } from "./ServiceAccountsTable";

export function PeoplePage() {
  const { principal } = useAuth();
  const isAdmin = principal?.role === "admin";

  return (
    <div className="max-w-5xl space-y-4">
      <h1 className="font-display text-2xl font-semibold text-ink">People &amp; agents</h1>
      {!isAdmin && (
        <p data-testid="people-admin-only-note" className="max-w-prose text-sm text-ink-2">
          The people and service-account directories are administrator-only on this deployment.
          Your own agent labels are below; an administrator can show you the rest.
        </p>
      )}
      {isAdmin && principal && <PeopleTable callerId={principal.id} />}
      <AgentLabelsTable />
      {isAdmin && <ServiceAccountsTable />}
    </div>
  );
}
