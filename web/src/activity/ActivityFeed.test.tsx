/**
 * `/activity`'s feed: one entry per write, change pills, and two shapes that are easy to get
 * wrong.
 *
 * Run whole rather than through a `-t` filter: a name filter that matches nothing reports success
 * with every test skipped.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { ActivityPage } from "./ActivityPage";
import { activityHandlers, auditEvent, resetEventIds } from "./testFixtures";
import { renderWithProviders } from "../test/renderWithProviders";
import type { AuditEventDoc } from "../api/records";

let feed: AuditEventDoc[] = [];

const server = setupServer(
  ...activityHandlers,
  // Newest first, as `GET /api/v1/audit-events` orders (`e.id DESC`).
  http.get("/api/v1/audit-events", () =>
    HttpResponse.json({ events: [...feed].reverse(), next_cursor: null }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  resetEventIds();
  feed = [];
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("ActivityPage feed", () => {
  it("groups one write into one entry, with a pill per field", async () => {
    feed = [
      auditEvent({ field_key: "stage", old_value: "won", new_value: "lost" }),
      auditEvent({ field_key: "owner", old_value: null, new_value: "p-1" }),
      auditEvent({ field_key: "note", old_value: "a", new_value: "b" }),
    ];
    renderWithProviders(<ActivityPage />);

    const events = await screen.findAllByTestId(/^activity-event-/);
    expect(events).toHaveLength(1);
    expect(within(events[0]).getAllByTestId(/^activity-change-/)).toHaveLength(3);
    expect(within(events[0]).getByText(/Updated 3 fields/)).toBeInTheDocument();
  });

  it("splits one request across two records into two entries", async () => {
    /**
     * The finding that made the group key `(request_id, record_id)` rather than the request id
     * alone. A request id identifies an HTTP request, not a write: `write_batch` (DD-22) and a
     * CSV import are one request over many records, so the request id alone would collapse an
     * import into a single entry with an unbounded number of pills. It was invisible on the
     * record page, where every group shares one record.
     */
    feed = [
      auditEvent({ request_id: "import-1", record_id: "record-1", record_key: "PROS-001" }),
      auditEvent({ request_id: "import-1", record_id: "record-2", record_key: "PROS-002" }),
    ];
    renderWithProviders(<ActivityPage />);

    expect(await screen.findAllByTestId(/^activity-event-/)).toHaveLength(2);
  });

  it("reads a write's own events in ascending order despite the newest-first feed", async () => {
    // `AuditWriteGroup.events` is documented as ascending and `summarizeWrite` reads `events[0]`;
    // this route orders `id DESC`. A group rendered without reversing would describe a write by
    // its last event.
    feed = [
      auditEvent({ entity_type: "link", action: "link", field_key: null, record_key: "PROS-005" }),
      auditEvent({ entity_type: "link", action: "unlink", field_key: null, record_key: "PROS-005" }),
    ];
    renderWithProviders(<ActivityPage />);

    const events = await screen.findAllByTestId(/^activity-event-/);
    expect(within(events[0]).getByText(/Linked record/)).toBeInTheDocument();
  });

  it("renders a user_ref change as edited rather than printing a UUID", async () => {
    /**
     * `formatChangeValue` resolves a `user_ref` through a principals sidecar, and
     * `GET /api/v1/audit-events` carries none — the record page gets its from the record
     * document. Formatting one here would print a 36-character id inside a pill, which is the
     * defect this screen exists to remove.
     */
    const owner = "3f1a2b4c-5d6e-4f70-8a91-b2c3d4e5f607";
    feed = [auditEvent({ field_key: "owner", old_value: null, new_value: owner })];
    renderWithProviders(<ActivityPage />);

    const pill = (await screen.findAllByTestId(/^activity-change-/))[0];
    expect(pill).toHaveTextContent("Owner edited");
    expect(pill.textContent).not.toContain(owner);
  });

  it("formats every other field type for a reader", async () => {
    feed = [auditEvent({ field_key: "stage", old_value: "won", new_value: "lost" })];
    renderWithProviders(<ActivityPage />);

    const pill = (await screen.findAllByTestId(/^activity-change-/))[0];
    // The select's display labels, not its option keys (docs/DESIGN.md 5's vocabulary layer).
    expect(pill).toHaveTextContent("Stage");
    expect(pill).toHaveTextContent("Won");
    expect(pill).toHaveTextContent("Lost");
  });

  it("links the record an entry is about, and does not link one no loaded type claims", async () => {
    feed = [
      auditEvent({ record_key: "PROS-005" }),
      auditEvent({ request_id: "req-2", record_id: "r-9", record_key: "ZZZ-001" }),
    ];
    renderWithProviders(<ActivityPage />);

    expect(await screen.findByRole("link", { name: "PROS-005" })).toHaveAttribute(
      "href",
      "/prospect/PROS-005",
    );
    expect(screen.queryByRole("link", { name: "ZZZ-001" })).toBeNull();
    expect(screen.getByText("ZZZ-001")).toBeInTheDocument();
  });

  it("renders no version number, because none is derivable here", async () => {
    // `auditGroups`' version pass counts forward from a record's create event, which is exact
    // only when the history has been walked from its start; a cross-record keyset walk never is.
    feed = [auditEvent({ action: "create", old_value: null, new_value: "won" })];
    renderWithProviders(<ActivityPage />);

    const events = await screen.findAllByTestId(/^activity-event-/);
    expect(events[0].textContent).not.toMatch(/\bv\d+\b/i);
    expect(within(events[0]).queryByText(/version/i)).toBeNull();
  });

  it("offers revert on a field update and not on a create", async () => {
    feed = [
      auditEvent({ field_key: "stage", old_value: "won", new_value: "lost" }),
      auditEvent({
        request_id: "req-2",
        action: "create",
        field_key: null,
        record_id: "record-2",
        record_key: "PROS-006",
      }),
    ];
    renderWithProviders(<ActivityPage />);

    // Addressed by the record each entry is about, not by position: the feed is newest-first,
    // so a positional assertion here is a claim about fixture order rather than about revert.
    await screen.findAllByTestId(/^activity-event-/);
    const updated = screen.getByText("PROS-005").closest("article") as HTMLElement;
    const created = screen.getByText("PROS-006").closest("article") as HTMLElement;
    expect(within(updated).getByRole("button", { name: /^Revert/ })).toBeInTheDocument();
    expect(within(created).queryByRole("button", { name: /^Revert/ })).toBeNull();
  });
});
