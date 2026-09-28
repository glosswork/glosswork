/**
 * `/activity`'s chip row: the six FR-U8 dimensions, and the two that stopped being UUID
 * fields.
 *
 * The file is named for what it covers rather than for a component, because it is meant to be
 * run **whole**: a `-t` name filter that matches nothing exits 0 with every test skipped, so
 * a renamed test would report green.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { ActivityPage } from "./ActivityPage";
import { ACTIVITY_DIMENSIONS, DIMENSION_LABELS } from "./activityFilters";
import { activityHandlers, auditEvent, resetEventIds } from "./testFixtures";
import { renderWithProviders } from "../test/renderWithProviders";

let requestedUrls: string[] = [];

const server = setupServer(
  ...activityHandlers,
  http.get("/api/v1/audit-events", ({ request }) => {
    requestedUrls.push(request.url);
    return HttpResponse.json({ events: [auditEvent()], next_cursor: null });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  resetEventIds();
  requestedUrls = [];
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

async function openAddFilter(user: ReturnType<typeof userEvent.setup>) {
  const row = await screen.findByTestId("activity-filters");
  await user.click(within(row).getByRole("button", { name: "Add filter" }));
}

describe("ActivityPage filters", () => {
  it("offers every FR-U8 filter dimension", async () => {
    // FR-U8: "Filter by record, principal, agent label, object type, field, or time range."
    // Asserted against the literal list, so a dropped dimension names itself rather than
    // leaving a test that still passes with five.
    const user = userEvent.setup();
    renderWithProviders(<ActivityPage />);
    await openAddFilter(user);

    const menu = screen.getByRole("menu", { name: "Add filter" });
    const offered = within(menu)
      .getAllByRole("menuitem")
      .map((item) => item.textContent);
    expect(offered).toEqual(ACTIVITY_DIMENSIONS.map((d) => DIMENSION_LABELS[d]));
  });

  it("asks for no identifier", async () => {
    /**
     * The issue's first line. The screen this replaces rendered `Principal ID` and `Agent label
     * ID` as bare inputs, both taking UUIDs nothing on the page told you, so in practice neither
     * filter was usable at all. Measured against the unfixed tree, this assertion found both.
     */
    const user = userEvent.setup();
    renderWithProviders(<ActivityPage />);
    await openAddFilter(user);

    for (const element of screen.queryAllByRole("textbox")) {
      expect(element.getAttribute("aria-label") ?? "").not.toMatch(/\bid\b/i);
    }
    expect(screen.queryByLabelText(/\bid\b/i)).toBeNull();
  });

  it("picks a person from the directory and sends the id, not the name", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ActivityPage />);
    await openAddFilter(user);

    await user.click(screen.getByRole("menuitem", { name: "Person" }));
    await user.click(await screen.findByRole("option", { name: /Dana Reyes/ }));

    // The chip says the name; the wire carries the id.
    expect(await screen.findByText("Person is Dana Reyes")).toBeInTheDocument();
    expect(requestedUrls.at(-1)).toContain(
      "principal_id=00000000-0000-4000-8000-000000000001",
    );
  });

  it("picks an agent from the directory and sends its label id", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ActivityPage />);
    await openAddFilter(user);

    await user.click(screen.getByRole("menuitem", { name: "Agent" }));
    // Named by `agentName`: a label with a display name shows it (docs/DESIGN.md 6.1), and the
    // chip must say what the option said.
    await user.click(await screen.findByRole("option", { name: /Sales Agent/ }));

    expect(await screen.findByText("Agent is Sales Agent")).toBeInTheDocument();
    expect(requestedUrls.at(-1)).toContain("agent_label_id=label-1");
  });

  it("runs no query until a condition is complete (docs/DESIGN.md 7.4)", async () => {
    // What replaced the old debounce. The old screen debounced four free-text inputs into the
    // query key and left `since`/`until` undebounced, so a half-typed date issued a request per
    // keystroke. An incomplete chip stays in its popover and never reaches the network.
    const user = userEvent.setup();
    renderWithProviders(<ActivityPage />);
    await screen.findByTestId("activity-feed");
    const before = requestedUrls.length;

    await openAddFilter(user);
    await user.click(screen.getByRole("menuitem", { name: "Date" }));
    const apply = await screen.findByRole("button", { name: "Apply" });
    expect(apply).toBeDisabled();
    expect(screen.getByText("Give at least one end of the range.")).toBeInTheDocument();
    expect(requestedUrls).toHaveLength(before);

    // And it is not disabled forever: one end is enough.
    await user.type(screen.getByLabelText("Since"), "2026-09-01");
    expect(await screen.findByRole("button", { name: "Apply" })).toBeEnabled();
  });

  it("removes a filter through the chip's own control", async () => {
    const user = userEvent.setup();
    renderWithProviders(<ActivityPage />);
    await openAddFilter(user);
    await user.click(screen.getByRole("menuitem", { name: "Field" }));
    await user.type(screen.getByLabelText("Field"), "stage");
    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(await screen.findByText("Field is stage")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove Field filter" }));
    expect(screen.queryByText("Field is stage")).not.toBeInTheDocument();
  });
});
