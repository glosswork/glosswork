import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { renderWithProviders } from "../test/renderWithProviders";
import type { ObjectTypeDetail } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import * as csvApi from "../api/csv";
import { TableView } from "./TableView";

vi.mock("../api/csv", () => ({ exportCsv: vi.fn() }));

const objectType: ObjectTypeDetail = {
  key: "initiative",
  name: "Initiative",
  name_plural: "Initiatives",
  description: "A funded, sponsored workstream.",
  key_prefix: "INIT",
  record_count: 1,
  field_count: 2,
  your_access: "admin",
  display_field_key: null,
  effective_display_field_key: null,
  fields: [
    {
      key: "name",
      name: "Name",
      type: "short_text",
      description: "Short name.",
      required: true,
      unique: false,
      indexed: true,
      embed: false,
      default: null,
      config: {},
      position: 0,
      operators: ["eq", "neq", "contains"],
      display_eligible: true,
    },
    {
      key: "status",
      name: "Status",
      type: "single_select",
      description: "Where the initiative stands.",
      required: true,
      unique: false,
      indexed: true,
      embed: false,
      default: null,
      config: {},
      position: 1,
      operators: ["eq", "neq", "in"],
      display_eligible: true,
      options: [
        { value: "on_track", label: "On Track", description: "Progressing." },
        { value: "at_risk", label: "At Risk", description: "Needs attention." },
      ],
    },
  ],
  system_fields: [
    { key: "key", type: "short_text", description: "Human key.", operators: ["eq", "in"] },
  ],
};

function record(overrides: Partial<RecordDoc> & { key: string }): RecordDoc {
  return {
    id: `${overrides.key}-id`,
    version: 1,
    created_at: "2026-08-20T09:00:00",
    created_by: "principal-1",
    updated_at: "2026-08-20T09:00:00",
    updated_by: "principal-1",
    updated_by_agent_label_id: null,
    deleted_at: null,
    comment_count: 0,
    last_comment_at: null,
    data: {},
    ...overrides,
  };
}

const server = setupServer(
  http.post("/api/v1/object-types/:key/query", () =>
    HttpResponse.json({
      records: [record({ key: "INIT-1", data: { name: "Alpha", status: "on_track" } })],
      total_count: 1,
      next_cursor: null,
      truncated: false,
    }),
  ),
  http.get("/api/v1/object-types/:key/saved-views", () => HttpResponse.json([])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  vi.mocked(csvApi.exportCsv).mockReset();
  vi.mocked(csvApi.exportCsv).mockResolvedValue(new Blob(["key,name\n"], { type: "text/csv" }));
  // jsdom doesn't implement object URLs; stub them so the download side-effect is a no-op.
  URL.createObjectURL = vi.fn(() => "blob:mock-url");
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("TableView: CSV export", () => {
  it("sends the table's current filter/sort/visible-columns state to exportCsv", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await screen.findByRole("button", { name: "Edit Name for INIT-1" });

    // Build a filter: status eq on_track, through the filter chips; FR-U7's export reads
    // `filter` state, which is where both UIs put it.
    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    const condition = screen.getByTestId("condition-popover");
    await user.selectOptions(within(condition).getByLabelText("Field"), "status");
    await user.selectOptions(within(condition).getByLabelText("Status value"), "on_track");

    // Add a sort key (name, ascending by default) via the explicit multi-sort control, and hide
    // the Status column via the column picker. Both are chips, so each takes an opening click
    // and keeps the accessible name it had. Clicking the second chip's trigger is
    // also what dismisses the first popover: `Popover` closes on an outside `mousedown`.
    await user.click(within(screen.getByTestId("sort-chip")).getAllByRole("button")[0]);
    await user.selectOptions(screen.getByLabelText("Add sort key"), "name");

    await user.click(within(screen.getByTestId("columns-chip")).getAllByRole("button")[0]);
    await user.click(screen.getByLabelText("Status"));

    // FR-U7's entry point is inside the View menu (docs/DESIGN.md 8.2 sends CSV export to the
    // view menu). The export's own behaviour — which columns,
    // which filter, which sort — is untouched, which is what the assertions below still pin.
    await user.click(screen.getByRole("button", { name: /^Current view/ }));
    await user.click(screen.getByRole("button", { name: "Export CSV" }));

    await waitFor(() => expect(vi.mocked(csvApi.exportCsv)).toHaveBeenCalledTimes(1));
    const [calledKey, calledOptions] = vi.mocked(csvApi.exportCsv).mock.calls[0];
    expect(calledKey).toBe("initiative");
    expect(calledOptions).toMatchObject({
      filter: { field: "status", op: "eq", value: "on_track" },
      sort: [{ field: "name", dir: "asc" }],
      columns: ["name"],
    });
  });
});
