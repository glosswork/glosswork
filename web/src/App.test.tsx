import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { useLocation } from "react-router-dom";
import { App } from "./App";
import { renderWithProviders } from "./test/renderWithProviders";
import type { ObjectTypeDetail, ObjectTypeSummary } from "./api/objectTypes";

const objectTypesFixture: ObjectTypeSummary[] = [
  {
    key: "initiative",
    name: "Initiative",
    description: "A funded, sponsored workstream.",
    key_prefix: "INIT",
    record_count: 3,
    field_count: 1,
    your_access: "admin",
  },
  {
    key: "task",
    name: "Task",
    description: "A unit of work assigned to an owner.",
    key_prefix: "TASK",
    record_count: 10,
    field_count: 1,
    your_access: "admin",
  },
  {
    key: "decision",
    name: "Decision",
    description: "A recorded decision and its rationale.",
    key_prefix: "DEC",
    record_count: 1,
    field_count: 1,
    your_access: "admin",
  },
];

const objectTypeDetails: Record<string, ObjectTypeDetail> = Object.fromEntries(
  objectTypesFixture.map((summary) => [
    summary.key,
    {
      ...summary,
      name_plural: `${summary.name}s`,
      display_field_key: null,
      effective_display_field_key: null,
      fields: [],
      system_fields: [],
    },
  ]),
);

const server = setupServer(
  http.get("/api/v1/object-types", () => HttpResponse.json(objectTypesFixture)),
  http.get("/api/v1/object-types/:key", ({ params }) => {
    const detail = objectTypeDetails[params.key as string];
    if (!detail) {
      return new HttpResponse(null, { status: 404 });
    }
    return HttpResponse.json(detail);
  }),
  http.post("/api/v1/object-types/:key/query", () =>
    HttpResponse.json({ records: [], total_count: 0, next_cursor: null, truncated: false }),
  ),
  http.get("/api/v1/object-types/:key/saved-views", () => HttpResponse.json([])),
  http.get("/api/v1/auth/modes", () => HttpResponse.json({ standalone: true, oidc: false, email_code: false })),
  // The shell's own two reads. `onUnhandledRequest: "error"` means an unhandled one
  // fails the test rather than resolving to undefined, which is what caught these.
  http.get("/api/v1/workspace", () =>
    HttpResponse.json({ name: "Northwind", people: 6, agents: 3 }),
  ),
  http.get("/api/v1/schema-proposals", () => HttpResponse.json({ proposals: [] })),
  // The reads `/people` and `/setup` make when the shell routes to them. They are here so
  // the route assertions below fail on the heading they are about rather than on
  // `onUnhandledRequest: "error"`, which is a different sentence entirely.
  http.get("/api/v1/agent-labels", () => HttpResponse.json({ labels: [] })),
  http.get("/api/v1/admin/agent-labels", () => HttpResponse.json({ labels: [] })),
  http.get("/api/v1/principals", () => HttpResponse.json({ principals: [] })),
  http.get("/api/v1/access-tokens", () => HttpResponse.json({ access_tokens: [] })),
  http.get("/api/v1/audit-events", () =>
    HttpResponse.json({ events: [], next_cursor: null }),
  ),
  http.get("/api/v1/principals/directory", () => HttpResponse.json({ principals: [] })),
  http.get("/api/v1/agent-labels/directory", () => HttpResponse.json({ agent_labels: [] })),
  http.get("/api/v1/admin/search-index", () =>
    HttpResponse.json({
      pending_jobs: 0,
      running_jobs: 0,
      failed_jobs: [],
      indexed_chunks: 0,
      stale_chunks: 0,
      embedding_model: "bge-small-en-v1.5",
      semantic_enabled: true,
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** Renders alongside `<App />` under the same `MemoryRouter` (via `renderWithProviders`) so a
 * test can read where a `navigate()` call inside the app actually landed — `App` owns its own
 * `<Routes>` tree end to end, so there is no other seam to inspect the resulting location from. */
function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location-probe">{`${location.pathname}${location.search}`}</span>;
}

describe("App", () => {
  /**
   * Replaces an older test, "submits the header search bar and navigates to /search with the
   * query". There is no header search box; the entry points are the sidebar's Search item and
   * the `/` key, and the query box lives on the search page itself. Only where you start from
   * moved.
   *
   * The route this asserts is the one the old test asserted end to end: something in the shell
   * reaches `/search`, and a typed query lands in `q`.
   */
  it("reaches /search from the sidebar and puts the typed query in `q`", async () => {
    server.use(
      http.post("/api/v1/search", () =>
        HttpResponse.json({
          results: [],
          index_lag: { pending_jobs: 0, failed_jobs: 0 },
          mode_applied: "hybrid",
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <App />
        <LocationProbe />
      </>,
    );

    const sidebar = await screen.findByTestId("sidebar");
    await user.click(within(sidebar).getByRole("link", { name: "Search" }));

    await screen.findByRole("heading", { name: "Search" });
    await user.type(screen.getByTestId("search-page-input"), "deal desk");
    await user.click(screen.getByRole("button", { name: "Search" }));

    const location = screen.getByTestId("location-probe").textContent ?? "";
    expect(location.startsWith("/search?")).toBe(true);
    const params = new URLSearchParams(location.slice(location.indexOf("?")));
    expect(params.get("q")).toBe("deal desk");
  });

  it("renders the workspace name and its people-and-agents line", async () => {
    renderWithProviders(<App />);

    expect(await screen.findByTestId("workspace-name")).toHaveTextContent("Northwind");
    // Asserted with a locator, never by screenshot: the visual tolerance is looser than a
    // heading's worth of ink (AGENTS.md), so a picture proves nothing about this text.
    expect(screen.getByTestId("workspace-people-agents")).toHaveTextContent("6 people · 3 agents");
  });

  it("renders one Tracking entry per live object type, with its record count", async () => {
    renderWithProviders(<App />);

    // Scoped to the sidebar: unscoped, a link of the same name inside `main` would
    // satisfy this, and the point is that the SHELL lists them.
    const sidebar = await screen.findByTestId("sidebar");
    // `findBy` on the first one: the sidebar renders immediately and its Tracking list arrives
    // with `list_object_types`, so a `getBy` here races the fetch and fails on an empty column.
    await within(sidebar).findByRole("link", { name: /^Initiative/ });
    for (const summary of objectTypesFixture) {
      const link = within(sidebar).getByRole("link", { name: new RegExp(`^${summary.name}`) });
      expect(link).toBeInTheDocument();
      // The count is data from `list_object_types`, never a client-side guess.
      expect(link).toHaveTextContent(String(summary.record_count));
    }
  });

  it("redirects `/` to the first object type's route and shows its page shell", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />);

    expect(await screen.findByRole("heading", { name: "Initiative" })).toBeInTheDocument();
    expect(screen.getByTestId("records-table")).toBeInTheDocument();

    // The description is behind the About disclosure (docs/DESIGN.md 8.2): it does not print
    // above the table. Still asserted rather than dropped, and gaining only
    // an opening click, because what this test is about is that `/` landed on the FIRST object
    // type's page and not merely on a page.
    await user.click(screen.getByTestId("about-toggle"));
    expect(screen.getByText("A funded, sponsored workstream.")).toBeInTheDocument();
  });

  it("navigates to an object type's page shell when its nav entry is clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<App />);

    await screen.findByRole("heading", { name: "Initiative" });

    const sidebar = screen.getByTestId("sidebar");
    await user.click(within(sidebar).getByRole("link", { name: /^Task/ }));

    expect(await screen.findByRole("heading", { name: "Task" })).toBeInTheDocument();
    // Same as above: the description names which type this is, one click away.
    await user.click(screen.getByTestId("about-toggle"));
    expect(screen.getByText("A unit of work assigned to an owner.")).toBeInTheDocument();
  });

  it("renders the signed-in principal's name and role from GET /api/v1/me", async () => {
    renderWithProviders(<App />);

    await screen.findByRole("heading", { name: "Initiative" });

    expect(screen.getByTestId("current-principal-name")).toHaveTextContent("Test Admin");
    expect(screen.getByTestId("current-principal-role")).toHaveTextContent("admin");
  });

  it("redirects an unauthenticated visitor to /login instead of showing the app shell", async () => {
    renderWithProviders(<App />, { principal: null });

    expect(await screen.findByTestId("login-email")).toBeInTheDocument();
    expect(screen.queryByTestId("current-principal")).not.toBeInTheDocument();
  });

  /**
   * `/settings` was one page and is now two, so the old URL has to keep working: it is in
   * this repository's own comments, and in whatever a person bookmarked.
   *
   * `replace`, not a push. A pushed redirect puts `/settings` back on the history stack, so Back
   * from `/people` lands on `/settings`, which redirects to `/people` again and the button stops
   * working. Asserted through `LocationProbe` rather than by the heading alone, because a
   * heading cannot tell a redirect from a route that happens to render the same page.
   */
  it("redirects /settings to /people", async () => {
    renderWithProviders(
      <>
        <App />
        <LocationProbe />
      </>,
      { route: "/settings" },
    );

    expect(
      await screen.findByRole("heading", { level: 1, name: "People & agents" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("location-probe")).toHaveTextContent("/people");
  });

  it("serves /people under its own heading", async () => {
    renderWithProviders(<App />, { route: "/people" });

    expect(
      await screen.findByRole("heading", { level: 1, name: "People & agents" }),
    ).toBeInTheDocument();
  });

  /**
   * `/audit` became `/activity`, and the old URL keeps working for the reason `/settings`
   * does. `replace`, not a push, so Back leaves the app rather than bouncing through the
   * redirect.
   */
  it("redirects /audit to /activity", async () => {
    renderWithProviders(
      <>
        <App />
        <LocationProbe />
      </>,
      { route: "/audit" },
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Activity" })).toBeInTheDocument();
    expect(screen.getByTestId("location-probe")).toHaveTextContent("/activity");
  });

  it("serves /activity under its own heading", async () => {
    renderWithProviders(<App />, { route: "/activity" });

    expect(await screen.findByRole("heading", { level: 1, name: "Activity" })).toBeInTheDocument();
  });

  it("serves /setup under its own heading", async () => {
    renderWithProviders(<App />, { route: "/setup" });

    expect(await screen.findByRole("heading", { level: 1, name: "Setup" })).toBeInTheDocument();
  });

  it("signs out through the app shell's control", async () => {
    server.use(http.delete("/api/v1/auth/session", () => new HttpResponse(null, { status: 204 })));
    const user = userEvent.setup();
    renderWithProviders(<App />);

    await screen.findByRole("heading", { name: "Initiative" });
    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByTestId("login-email")).toBeInTheDocument();
  });
});

/**
 * The trial banner's place in the shell (change 30). What the banner says is
 * `app/TrialBanner.test.tsx`; this is whether the shell mounts it.
 *
 * **Every absence here is asserted after the workspace document has arrived**, by first
 * finding the sidebar line drawn from that same document. Before it arrives there is no
 * banner on any workspace, including one about to show one.
 */
describe("App, on a workspace with or without a trial", () => {
  const FAR_FUTURE = "2999-01-01T00:00:00Z";
  const LONG_PAST = "2001-01-01T00:00:00Z";
  const WORKSPACE = { name: "Northwind", people: 6, agents: 3, mcp_url: null };

  it("renders the shell and no banner for a workspace document with no trial key at all", async () => {
    // The file's own default handler, on purpose: its document has no `trial` key. "Not null"
    // is not "an object", and a shell that reads `ends_at` off `undefined` renders nothing.
    renderWithProviders(<App />);

    expect(await screen.findByTestId("workspace-people-agents")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Initiative" })).toBeInTheDocument();
    expect(screen.queryByTestId("trial-banner")).toBeNull();
  });

  it("renders no banner when the workspace's trial is null", async () => {
    server.use(
      http.get("/api/v1/workspace", () => HttpResponse.json({ ...WORKSPACE, trial: null })),
    );
    renderWithProviders(<App />);

    expect(await screen.findByTestId("workspace-people-agents")).toBeInTheDocument();
    expect(screen.queryByTestId("trial-banner")).toBeNull();
  });

  it("renders the banner as the first thing in main when the workspace has a trial", async () => {
    server.use(
      http.get("/api/v1/workspace", () =>
        HttpResponse.json({
          ...WORKSPACE,
          trial: { ends_at: FAR_FUTURE, subscribe_url: "https://subscribe.example.com/plan" },
        }),
      ),
    );
    renderWithProviders(<App />);

    const banner = await screen.findByTestId("trial-banner");
    expect(within(banner).getByTestId("trial-time-left")).toBeInTheDocument();
    expect(within(banner).getByTestId("trial-subscribe")).toHaveAttribute(
      "href",
      "https://subscribe.example.com/plan",
    );
    expect(screen.getByRole("main").firstElementChild).toBe(banner);
  });

  it("says the trial ended for a trial whose end time has passed", async () => {
    server.use(
      http.get("/api/v1/workspace", () =>
        HttpResponse.json({ ...WORKSPACE, trial: { ends_at: LONG_PAST, subscribe_url: null } }),
      ),
    );
    renderWithProviders(<App />);

    const banner = await screen.findByTestId("trial-banner");
    expect(within(banner).queryByTestId("trial-time-left")).toBeNull();
    expect(within(banner).queryByRole("link")).toBeNull();
  });

  it("reads the workspace document again when the person comes back to the tab", async () => {
    // The banner depends on this. A tab that is open when the workspace is restarted with no
    // trial end, as it is after the person subscribes, keeps its banner until the document is
    // read again, and nothing here polls. Returning to the tab is what re-reads it, and that
    // is the query library's default rather than a setting this repository makes, so it is
    // pinned here.
    let reads = 0;
    server.use(
      http.get("/api/v1/workspace", () => {
        reads += 1;
        return HttpResponse.json({
          ...WORKSPACE,
          trial: reads === 1 ? { ends_at: FAR_FUTURE, subscribe_url: null } : null,
        });
      }),
    );
    renderWithProviders(<App />);
    expect(await screen.findByTestId("trial-banner")).toBeInTheDocument();
    expect(reads).toBe(1);

    window.dispatchEvent(new Event("visibilitychange"));

    await waitFor(() => expect(screen.queryByTestId("trial-banner")).toBeNull());
    expect(reads).toBe(2);
  });
});
