import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { Route, Routes, useLocation } from "react-router-dom";
import { SearchPage } from "./SearchPage";
import { renderWithProviders } from "../test/renderWithProviders";
import type { ObjectTypeDetail, ObjectTypeSummary } from "../api/objectTypes";
import type { SearchBody, SearchResponse } from "../api/search";

const objectTypesFixture: ObjectTypeSummary[] = [
  {
    key: "decision",
    name: "Decision",
    description: "A recorded decision and its rationale.",
    key_prefix: "DEC",
    record_count: 1,
    field_count: 1,
    your_access: "admin",
  },
  {
    key: "initiative",
    name: "Initiative",
    description: "A funded, sponsored workstream.",
    key_prefix: "INIT",
    record_count: 1,
    field_count: 1,
    your_access: "admin",
  },
];

const objectTypeDetails: Record<string, ObjectTypeDetail> = {
  decision: {
    ...objectTypesFixture[0],
    name_plural: "Decisions",
    display_field_key: null,
    effective_display_field_key: null,
    fields: [],
    system_fields: [],
  },
  initiative: {
    ...objectTypesFixture[1],
    name_plural: "Initiatives",
    display_field_key: null,
    effective_display_field_key: "scope_summary",
    fields: [
      {
        key: "scope_summary",
        name: "Scope Summary",
        type: "long_text",
        description: "What this initiative covers.",
        required: false,
        unique: false,
        indexed: false,
        embed: true,
        default: null,
        config: {},
        position: 0,
        operators: ["contains"],
        display_eligible: true,
      },
    ],
    system_fields: [],
  },
};

// Mirrors docs/MCP_TOOLS.md 5.1's own `search` response example verbatim.
const docsExampleResponse: SearchResponse = {
  results: [
    {
      record_key: "DEC-031",
      record_id: "id-dec-031",
      object_type: "decision",
      title: "Consolidate regional pricing authority",
      score: 0.83,
      hit_source: {
        type: "comment",
        comment_id: "comment-1",
        author: "Dana Reyes",
        created_at: "2026-03-03T14:22:00Z",
      },
      snippet: "...pushed back on the <em>sales pricing</em> exception process because regional GMs...",
      other_matches: 2,
    },
    {
      record_key: "INIT-014",
      record_id: "id-init-014",
      object_type: "initiative",
      title: "Commercial operating model",
      score: 0.71,
      hit_source: { type: "field", field_key: "scope_summary" },
      snippet: "...harmonize <em>sales pricing</em> governance across the three regions...",
      other_matches: 0,
    },
  ],
  index_lag: { pending_jobs: 0, failed_jobs: 0 },
  mode_applied: "hybrid",
};

let searchResponse: SearchResponse = docsExampleResponse;
let capturedRequests: SearchBody[] = [];

const server = setupServer(
  http.get("/api/v1/object-types", () => HttpResponse.json(objectTypesFixture)),
  http.get("/api/v1/object-types/:key", ({ params }) => {
    const detail = objectTypeDetails[params.key as string];
    if (!detail) {
      return new HttpResponse(null, { status: 404 });
    }
    return HttpResponse.json(detail);
  }),
  http.post("/api/v1/search", async ({ request }) => {
    const body = (await request.json()) as SearchBody;
    capturedRequests.push(body);
    return HttpResponse.json(searchResponse);
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  searchResponse = docsExampleResponse;
  capturedRequests = [];
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** Renders alongside the routed `SearchPage` so a test can read the URL params a control change
 * actually produced — `SearchPage` owns `mode`/`types`/`filter` through `useSearchParams`, and
 * there is no other seam to inspect the resulting location from. */
function LocationProbe() {
  const location = useLocation();
  return <span data-testid="location-probe">{`${location.pathname}${location.search}`}</span>;
}

function renderSearchPage(route: string) {
  return renderWithProviders(
    <>
      <Routes>
        <Route path="/search" element={<SearchPage />} />
      </Routes>
      <LocationProbe />
    </>,
    { route },
  );
}

describe("SearchPage", () => {
  it("groups results by object type, in order of first appearance, with a hit-source label per hit", async () => {
    renderSearchPage("/search?q=sales+pricing");

    const decisionSection = await screen.findByRole("region", { name: "Decision results" });
    expect(within(decisionSection).getByRole("heading", { name: "Decision" })).toBeInTheDocument();
    const decisionResult = within(decisionSection).getByTestId("search-result-DEC-031");
    expect(within(decisionResult).getByRole("link", { name: "DEC-031" })).toHaveAttribute(
      "href",
      "/decision/DEC-031",
    );
    expect(within(decisionResult).getByText("Consolidate regional pricing authority")).toBeInTheDocument();
    expect(within(decisionResult).getByTestId("hit-source")).toHaveTextContent("comment by Dana Reyes");
    expect(within(decisionResult).getByTestId("other-matches")).toHaveTextContent("+2 more matches");

    const initiativeSection = await screen.findByRole("region", { name: "Initiative results" });
    const initiativeResult = within(initiativeSection).getByTestId("search-result-INIT-014");
    // The field hit-source label is the field's display NAME, resolved from
    // `describe_object_type`, not its raw key.
    expect(await within(initiativeResult).findByTestId("hit-source")).toHaveTextContent(
      "Scope Summary",
    );
    expect(within(initiativeResult).queryByTestId("other-matches")).not.toBeInTheDocument();
  });

  it("renders a field hit's snippet with <em> promoted to <mark>", async () => {
    renderSearchPage("/search?q=sales+pricing");

    const initiativeResult = await screen.findByTestId("search-result-INIT-014");
    const snippet = within(initiativeResult).getByTestId("snippet");
    expect(snippet.querySelector("mark")).toHaveTextContent("sales pricing");
  });

  it("escapes hostile snippet content instead of rendering it as an element", async () => {
    searchResponse = {
      results: [
        {
          record_key: "DEC-099",
          record_id: "id-dec-099",
          object_type: "decision",
          title: "Hostile snippet fixture",
          score: 0.5,
          hit_source: { type: "field", field_key: "notes" },
          snippet: "<script>alert(1)</script> and <em>ok</em>",
          other_matches: 0,
        },
      ],
      index_lag: { pending_jobs: 0, failed_jobs: 0 },
      mode_applied: "hybrid",
    };
    renderSearchPage("/search?q=hostile");

    const result = await screen.findByTestId("search-result-DEC-099");
    const snippet = within(result).getByTestId("snippet");
    expect(snippet.querySelector("script")).toBeNull();
    expect(snippet.textContent).toContain("alert(1)");
    expect(snippet.querySelector("mark")).toHaveTextContent("ok");
  });

  it("switching the mode toggle updates the mode URL param and the next request body", async () => {
    const user = userEvent.setup();
    renderSearchPage("/search?q=sales+pricing");

    await screen.findByTestId("search-result-DEC-031");
    expect(capturedRequests).toHaveLength(1);
    expect(capturedRequests[0].mode).toBe("hybrid");

    await user.click(screen.getByRole("radio", { name: "Semantic" }));

    await waitFor(() => expect(capturedRequests).toHaveLength(2));
    expect(capturedRequests[1].mode).toBe("semantic");
    expect(screen.getByTestId("location-probe")).toHaveTextContent("mode=semantic");
  });

  it("shows the FilterBuilder only when exactly one object type is selected", async () => {
    const user = userEvent.setup();
    renderSearchPage("/search?q=sales+pricing");

    await screen.findByTestId("search-result-DEC-031");
    expect(screen.queryByTestId("filter-builder")).not.toBeInTheDocument();

    const typesSelect = screen.getByLabelText("Object types");
    await user.selectOptions(typesSelect, ["initiative"]);

    expect(await screen.findByTestId("filter-builder")).toBeInTheDocument();

    await user.selectOptions(typesSelect, ["decision", "initiative"]);

    await waitFor(() => expect(screen.queryByTestId("filter-builder")).not.toBeInTheDocument());
  });

  it("shows an index-lag notice when index_lag.pending_jobs is greater than zero", async () => {
    searchResponse = { ...docsExampleResponse, index_lag: { pending_jobs: 4, failed_jobs: 0 } };
    renderSearchPage("/search?q=sales+pricing");

    expect(await screen.findByTestId("index-lag")).toHaveTextContent(
      "4 items are still being indexed",
    );
  });

  it("shows a mode-applied notice when the response degraded from the requested mode", async () => {
    searchResponse = { ...docsExampleResponse, mode_applied: "keyword" };
    renderSearchPage("/search?q=sales+pricing");

    expect(await screen.findByTestId("mode-applied")).toHaveTextContent(
      "Semantic search is disabled on this deployment; showing keyword results.",
    );
  });

  it("shows the server's message in a feature_disabled alert", async () => {
    server.use(
      http.post("/api/v1/search", () =>
        HttpResponse.json(
          {
            error: {
              code: "feature_disabled",
              message: "Semantic search is disabled on this deployment.",
              details: { feature: "semantic_search", setting: "GW_EMBEDDING_ENABLED" },
            },
          },
          { status: 409 },
        ),
      ),
    );
    renderSearchPage("/search?q=sales+pricing&mode=semantic");

    expect(await screen.findByTestId("feature-disabled")).toHaveTextContent(
      "Semantic search is disabled on this deployment.",
    );
  });

  it(
    "surfaces the server's FR-A4 envelope beside the fixed " +
      "'Search failed' title",
    async () => {
      server.use(
        http.post(
          "/api/v1/search",
          () =>
            HttpResponse.json(
              {
                error: {
                  code: "internal_error",
                  message: "The search index is temporarily unavailable.",
                  details: {},
                },
              },
              { status: 500 },
            ),
          { once: true },
        ),
      );
      renderSearchPage("/search?q=sales+pricing");

      const alert = await screen.findByRole("alert");
      expect(within(alert).getByText("Search failed. Try again.")).toBeInTheDocument();
      expect(alert.textContent).toContain("internal_error");
      expect(alert.textContent).toContain("The search index is temporarily unavailable.");
    },
  );

  it("shows a hint instead of fetching when q is empty", async () => {
    renderSearchPage("/search");

    expect(await screen.findByText("Enter a search term to begin.")).toBeInTheDocument();
    expect(capturedRequests).toHaveLength(0);
  });

  it("shows 'No results.' for an empty result set", async () => {
    searchResponse = { results: [], index_lag: { pending_jobs: 0, failed_jobs: 0 }, mode_applied: "hybrid" };
    renderSearchPage("/search?q=nothing+here");

    expect(await screen.findByText("No results.")).toBeInTheDocument();
  });
});
