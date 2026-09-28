/**
 * `/inbox` renders a proposal as a page about a decision.
 *
 * **Every sentence is asserted with a locator**, never left to the visual baselines. AGENTS.md
 * is explicit about why: `toHaveScreenshot` runs at `maxDiffPixelRatio: 0.001`, which on a
 * 1280x800 shot permits about 1,024 differing pixels — more ink than a heading contains. The
 * product was renamed once and all 38 baselines still passed. A passing visual suite is evidence
 * about layout only, and this screen is almost entirely words.
 *
 * Two of these cases are the behavioural half of DD-42's exception: the creator who reads the
 * list and cannot decide, and the member whose credential cannot read it at all.
 */
import { HttpResponse, http } from "msw";
import { setupServer } from "msw/node";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { Route, Routes } from "react-router-dom";

import { InboxPage } from "./InboxPage";
import { renderWithProviders } from "../test/renderWithProviders";
import type { CurrentPrincipal } from "../api/auth";
import type { ProposalDoc, ProposalPage } from "../api/schemaProposals";

/**
 * Mounted under the two routes `App.tsx` declares, not bare.
 *
 * `InboxPage` reads its selection from `useParams`, so rendering `<InboxPage />` directly inside
 * a `MemoryRouter` gives it `{}` no matter what `initialEntries` says — every detail case then
 * renders the empty pane and fails for a reason that has nothing to do with what it asserts.
 * Duplicating the two paths here is deliberate: it keeps the test honest about the route shape
 * the app actually serves, which `tests/test_approval_message.py` pins from the other side.
 */
function routed() {
  return (
    <Routes>
      <Route path="/inbox" element={<InboxPage />} />
      <Route path="/inbox/:proposalId" element={<InboxPage />} />
    </Routes>
  );
}

const ADMIN_ID = "00000000-0000-4000-8000-000000000001";
const AGENT_ID = "2a8ea7e2-d0a3-4433-a3dc-dcaf8e26761b";

const CREATOR: CurrentPrincipal = {
  id: "creator-1",
  display_name: "Casey Rowe",
  email: "casey@example.com",
  type: "user",
  role: "creator",
  // `role_scope("creator")` is `admin`, so this credential genuinely reads the list.
  scope: "admin",
  auth_method: "session",
  auth_provider: "local",
};

const MEMBER: CurrentPrincipal = {
  id: "member-1",
  display_name: "Morgan Fell",
  email: "morgan@example.com",
  type: "user",
  role: "member",
  scope: "write",
  auth_method: "session",
  auth_provider: "local",
};

function proposal(overrides: Partial<ProposalDoc> = {}): ProposalDoc {
  return {
    id: "prop_7865f67c",
    status: "pending",
    change_type: "delete_field",
    target_type_id: "26f8e1d5",
    target_field_id: "a08f588e",
    payload: {},
    impact: {
      change_type: "delete_field",
      affected_records: 13,
      non_empty_values: 9,
      sample_values: ["Retire after the audit.", "Awaiting sign-off.", "Retire after Q3."],
    },
    snapshot_ref: null,
    reason: "The team stopped using it after the CRM migration.",
    proposed_at: RAISED_AT,
    proposed_by: ADMIN_ID,
    proposed_agent: AGENT_ID,
    decided_at: null,
    decided_by: null,
    decision_note: null,
    target: {
      object_type_key: "prospect",
      object_type_name: "Prospect",
      object_type_name_plural: "Prospects",
      field_key: "notes",
      field_name: "Notes",
      field_type: "long_text",
    },
    ...overrides,
  };
}

let page: ProposalPage;
let approved: string[] = [];
let rejected: string[] = [];

function pageOf(proposals: ProposalDoc[]): ProposalPage {
  return {
    proposals,
    next_cursor: null,
    total_count: proposals.length,
    principals: {
      [ADMIN_ID]: { display_name: "Test Admin", type: "user" },
    },
    agent_labels: {
      [AGENT_ID]: { label: "claude-code", display_name: null },
    },
  };
}

const server = setupServer(
  http.get("/api/v1/schema-proposals", () => HttpResponse.json(page)),
  http.post("/api/v1/schema-proposals/:id/approve", ({ params }) => {
    approved.push(String(params.id));
    return HttpResponse.json(proposal({ status: "approved" }));
  }),
  http.post("/api/v1/schema-proposals/:id/reject", ({ params }) => {
    rejected.push(String(params.id));
    return HttpResponse.json(proposal({ status: "rejected" }));
  }),
);

/**
 * **The clock is pinned, and this is a CI failure rather than a precaution.**
 *
 * `proposed_at` was `Date.now() - 2h` and the raised-at case asserts "Raised **today** at HH:MM".
 * That is true for twenty-two hours of every day and false for the other two: between 00:00 and
 * 02:00 local, two hours ago is yesterday. It passed on every local run and failed in CI on the
 * first push of this branch, at 01:12 UTC, with `Raised yesterday at 23:11`. Reproduced
 * deterministically before fixing, by running under a zone whose local clock was inside the
 * window (`TZ=Etc/GMT+1`, local 01:15).
 *
 * Only `Date` is faked. Timers are left real: React Testing Library's `waitFor` and `findBy*`
 * are built on them, and faking those would hang every async assertion in this file.
 *
 * `NOW` is 15:00Z deliberately. The offsets in use run from UTC-12 to UTC+14, so the earliest any
 * zone can read is 03:00 and the latest 05:00 next day — the two-hour window never crosses local
 * midnight anywhere. At 12:00Z it would: UTC-12 would read 00:00, and the raised time would land
 * on the previous day, which is the same defect wearing a fixed number.
 *
 * `formatRaisedAt`'s own unit tests take `now` as a parameter and are deterministic by
 * construction (`web/src/ui/datetime.ts`). This is the component test, where the component calls
 * `new Date()` itself, so the clock has to be pinned from outside.
 */
const NOW = new Date("2026-09-10T15:00:00Z");
const RAISED_AT = "2026-09-10T13:00:00Z";

beforeAll(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  server.listen({ onUnhandledRequest: "error" });
});
beforeEach(() => {
  page = pageOf([proposal()]);
  approved = [];
  rejected = [];
});
afterEach(() => server.resetHandlers());
afterAll(() => {
  server.close();
  vi.useRealTimers();
});

describe("the proposal detail", () => {
  it("leads with the sentence, not with the change type", async () => {
    renderWithProviders(routed(), { route: "/inbox/prop_7865f67c" });

    const headline = await screen.findByTestId("proposal-headline");
    expect(headline).toHaveTextContent("claude-code wants to remove the Notes field from Prospects");
    // The screen this replaces headed each proposal with its change type in monospace.
    expect(headline).not.toHaveTextContent("delete_field");
  });

  it("quotes the rationale as the agent's own words", async () => {
    renderWithProviders(routed(), { route: "/inbox/prop_7865f67c" });

    expect(await screen.findByTestId("proposal-reason")).toHaveTextContent(
      "The team stopped using it after the CRM migration.",
    );
  });

  it("renders three impact tiles, and the snapshot one is in the future tense", async () => {
    renderWithProviders(routed(), { route: "/inbox/prop_7865f67c" });

    const tiles = await screen.findByTestId("proposal-impact-tiles");
    expect(within(tiles).getByTestId("impact-affected")).toHaveTextContent("13");
    expect(within(tiles).getByTestId("impact-nothing-yet")).toHaveTextContent(
      "changes happen until you approve",
    );

    // The reason this assertion exists: `snapshot_ref` is null while a proposal is
    // pending, so docs/DESIGN.md 8.4's "Saved" would have claimed something that has not
    // happened. A tile asserting a snapshot already exists is a lie about a safety feature.
    const snapshot = within(tiles).getByTestId("impact-snapshot");
    expect(snapshot).toHaveTextContent("taken before anything changes");
    expect(snapshot).not.toHaveTextContent("Saved");
  });

  it("says in a plain paragraph what each button does and whose name it lands under", async () => {
    renderWithProviders(routed(), { route: "/inbox/prop_7865f67c" });

    const paragraph = await screen.findByTestId("proposal-decision-paragraph");
    expect(paragraph).toHaveTextContent("Approve takes a snapshot of the affected data first");
    expect(paragraph).toHaveTextContent("Decline leaves everything exactly as it is");
    expect(paragraph).toHaveTextContent("under your name, Test Admin");
  });

  it("strikes the sample values through and states both counts, with no See all link", async () => {
    renderWithProviders(routed(), { route: "/inbox/prop_7865f67c" });

    const samples = await screen.findByTestId("proposal-samples");
    expect(within(samples).getByText("Retire after the audit.")).toBeInTheDocument();
    // Three distinct samples out of nine non-empty values. There is no route that returns
    // the rest, so the screen says how many it is showing rather than offering a link it cannot
    // honour.
    expect(within(samples).getByTestId("proposal-sample-count")).toHaveTextContent(
      "3 of 9 values shown.",
    );
    expect(within(samples).queryByRole("link", { name: /see all/i })).not.toBeInTheDocument();
  });

  it("renders the raised-at line in words, not as an ISO timestamp", async () => {
    renderWithProviders(routed(), { route: "/inbox/prop_7865f67c" });

    const raised = await screen.findByTestId("proposal-raised-at");
    expect(raised).toHaveTextContent(/Raised today at \d{2}:\d{2}/);
    expect(raised).not.toHaveTextContent(/\d{4}-\d{2}-\d{2}T/);
  });

  it("has no Ask why button", async () => {
    // Asserted rather than left to absence: a comment cannot exist off a record and
    // `list_schema_proposals` returns no comments, so the question would be unreadable by the
    // agent it is addressed to. If a future change adds the button, it must also delete this.
    renderWithProviders(routed(), { route: "/inbox/prop_7865f67c" });

    await screen.findByTestId("proposal-headline");
    expect(screen.queryByRole("button", { name: /ask/i })).not.toBeInTheDocument();
  });
});

describe("deciding", () => {
  it("approves, and drops the row from the shared cache the badge reads", async () => {
    const user = userEvent.setup();
    renderWithProviders(routed(), { route: "/inbox/prop_7865f67c" });

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() => expect(approved).toEqual(["prop_7865f67c"]));
    // The row leaves the cache entry `usePendingProposalCount` reads, so the sidebar
    // badge follows without a second request. Its absence from the list is that, observed.
    await waitFor(() =>
      expect(screen.queryByTestId("proposal-prop_7865f67c")).not.toBeInTheDocument(),
    );
  });

  it("declines without asking for a reason", async () => {
    const user = userEvent.setup();
    renderWithProviders(routed(), { route: "/inbox/prop_7865f67c" });

    await user.click(await screen.findByRole("button", { name: "Decline" }));

    await waitFor(() => expect(rejected).toEqual(["prop_7865f67c"]));
    // `Decline` posts `{}`. No dialog, because there is no channel to carry a note back.
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("who sees what", () => {
  it("shows a creator the proposal but not the decisions", async () => {
    // `role_scope("creator")` is `admin`, so this credential genuinely reads the list; the
    // decision is
    // gated on the `admin` ROLE, which a creator does not hold. This is the case DD-42's
    // exception was actually about.
    renderWithProviders(routed(), { route: "/inbox/prop_7865f67c", principal: CREATOR });

    expect(await screen.findByTestId("proposal-headline")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Decline" })).not.toBeInTheDocument();
  });

  it("shows a member the page and one sentence, and asks the server for nothing", async () => {
    // Not "a member sees rows without buttons": no real server could produce that, and such a
    // test would pass only because `msw` serves the list to a member anyway.
    //
    // The request assertion has a POSITIVE CONTROL. `expect(seen).toEqual([])` over a filtered
    // list passes identically when the filter is wrong, when nothing has rendered, and when the
    // spy was never installed — an assertion that cannot fail has not been measured.
    const seen: string[] = [];
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      seen.push(String(input));
      return Promise.reject(new Error("no request should reach the network in this test"));
    });

    renderWithProviders(routed(), { route: "/inbox", principal: MEMBER });

    const banner = await screen.findByTestId("inbox-access-banner");
    expect(banner).toHaveTextContent("Schema proposals are shown to administrators.");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(seen.filter((url) => url.includes("schema-proposals"))).toEqual([]);

    // The control: the spy really would have recorded a request had one been made.
    void fetch("/api/v1/probe").catch(() => undefined);
    expect(seen).toContain("/api/v1/probe");

    fetchSpy.mockRestore();
  });
});

describe("the list pane", () => {
  it("says what the screen is for when nothing is waiting", async () => {
    page = pageOf([]);
    renderWithProviders(routed(), { route: "/inbox" });

    const empty = await screen.findByTestId("inbox-empty");
    expect(empty).toHaveTextContent("Nothing is waiting on you.");
    // The second sentence is the point: this screen is empty most days, and an empty hero screen
    // otherwise never gets to say what it is for.
    expect(empty).toHaveTextContent("before anything happens");
  });

  it("distinguishes nothing-waiting from cannot-look", async () => {
    // The two sentences must never be interchangeable: "nothing is waiting" rendered to someone
    // the server refused is the client asserting something it was denied the right to know.
    page = pageOf([]);
    renderWithProviders(routed(), { route: "/inbox", principal: MEMBER });

    await screen.findByTestId("inbox-access-banner");
    expect(screen.queryByTestId("inbox-empty")).not.toBeInTheDocument();
  });

  it("says so when a linked proposal is no longer pending", async () => {
    page = pageOf([]);
    renderWithProviders(routed(), { route: "/inbox/prop_gone" });

    expect(await screen.findByText("That proposal is no longer pending.")).toBeInTheDocument();
  });
});
