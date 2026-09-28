import { useState } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { renderWithProviders } from "../test/renderWithProviders";
import type { ObjectTypeDetail } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import { ObjectTypeTablePlaceholder } from "./ObjectTypeTablePlaceholder";

/**
 * `ObjectTypeTablePlaceholder` must remount `TableView` when `objectType` changes, so
 * per-collection state (sort, column order/visibility, row selection, the saved-view default
 * gate) never survives a swap. `typeA` and `typeB` share the `status` field (so a stale sort key
 * is reproducible) but declare it in different column order, and `typeB` has a field `typeA`
 * lacks (so the column picker's order is a real, observable difference).
 */
const typeA: ObjectTypeDetail = {
  key: "alpha_type",
  name: "Alpha Type",
  name_plural: "Alpha Types",
  description: "The first collection.",
  key_prefix: "ALPHA",
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
      description: "Where it stands.",
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

const typeB: ObjectTypeDetail = {
  key: "beta_type",
  name: "Beta Type",
  name_plural: "Beta Types",
  description: "The second collection.",
  key_prefix: "BETA",
  record_count: 1,
  field_count: 2,
  your_access: "admin",
  display_field_key: null,
  effective_display_field_key: null,
  fields: [
    {
      key: "status",
      name: "Status",
      type: "single_select",
      description: "Where it stands.",
      required: true,
      unique: false,
      indexed: true,
      embed: false,
      default: null,
      config: {},
      position: 0,
      operators: ["eq", "neq", "in"],
      display_eligible: true,
      options: [
        { value: "on_track", label: "On Track", description: "Progressing." },
        { value: "at_risk", label: "At Risk", description: "Needs attention." },
      ],
    },
    {
      key: "extra",
      name: "Extra",
      type: "short_text",
      description: "A field only Beta has.",
      required: false,
      unique: false,
      indexed: false,
      embed: false,
      default: null,
      config: {},
      position: 1,
      operators: ["eq", "neq", "contains"],
      display_eligible: true,
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

const recordsByType: Record<string, RecordDoc[]> = {
  alpha_type: [record({ key: "ALPHA-1", data: { name: "First", status: "on_track" } })],
  beta_type: [record({ key: "BETA-1", data: { status: "at_risk", extra: "hi" } })],
};

const server = setupServer(
  http.post("/api/v1/object-types/:key/query", ({ params }) => {
    const key = params.key as string;
    const records = recordsByType[key] ?? [];
    return HttpResponse.json({
      records,
      total_count: records.length,
      next_cursor: null,
      truncated: false,
    });
  }),
  http.get("/api/v1/object-types/:key/saved-views", () => HttpResponse.json([])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** Holds the current object type in its own state and swaps it via a button, so the swap is a
 * prop change on an already-mounted `ObjectTypeTablePlaceholder` rather than a fresh render.
 * `renderWithProviders`'s `rerender` replaces the whole tree and drops the `QueryClientProvider`
 * (measured — throws "No QueryClient set"), so it cannot be used here. */
function SwappableParent() {
  const [objectType, setObjectType] = useState<ObjectTypeDetail>(typeA);
  return (
    <div>
      <button type="button" onClick={() => setObjectType(typeB)}>
        Switch to Beta
      </button>
      <ObjectTypeTablePlaceholder objectType={objectType} />
    </div>
  );
}

/** One opening click on a chip, addressed by its test id rather than by its sentence — the
 * sentence changes as the control's state does, which is half of what this test is about. */
async function openChip(user: ReturnType<typeof userEvent.setup>, testId: string) {
  await user.click(within(screen.getByTestId(testId)).getAllByRole("button")[0]);
}

describe("ObjectTypeTablePlaceholder: remount on collection change", () => {
  it("resets sort, column order, and row selection when the object type prop changes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SwappableParent />);

    // Wait for typeA's table to render.
    await screen.findByTestId("row-ALPHA-1");

    // Set a sort key on typeA. Sort and columns are chips, so each control is reached through an
    // opening click — and the opening
    // click on the second chip is what dismisses the first popover, since `Popover` closes on an
    // outside `mousedown`.
    await openChip(user, "sort-chip");
    await user.selectOptions(screen.getByLabelText("Add sort key"), "status");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Toggle sort direction for Status" })).toBeInTheDocument(),
    );

    // Reorder typeA's columns: move Status above Name.
    await openChip(user, "columns-chip");
    await user.click(screen.getByRole("button", { name: "Move Status up" }));
    await waitFor(() => {
      const picker = within(screen.getByTestId("column-picker"));
      const labels = picker.getAllByRole("listitem").map((item) => item.textContent);
      expect(labels[0]).toContain("Status");
    });

    // Select typeA's row.
    await user.click(screen.getByLabelText("Select row ALPHA-1"));
    expect(screen.getByLabelText("Select row ALPHA-1")).toBeChecked();

    // Swap to typeB without unmounting the placeholder.
    await user.click(screen.getByRole("button", { name: "Switch to Beta" }));
    await screen.findByTestId("row-BETA-1");

    // Sort keys are back to typeB's default: empty. The chip says so without being opened —
    // `Sort`, not `Sort: Status` — which is the one assertion here that a closed popover cannot
    // make vacuous.
    expect(screen.getByTestId("sort-chip")).toHaveTextContent("Sort");
    expect(screen.getByTestId("sort-chip")).not.toHaveTextContent("Sort: Status");

    // And inside it: no "Toggle sort direction" button for Status survives from typeA, and the
    // "Add sort key" dropdown lists Status as available again (it would be excluded if it were
    // still an active sort key). Opened first on purpose: behind a closed popover both of these
    // pass because the control is unmounted rather than because the sort reset (the same
    // failure shape was found on another spec).
    await openChip(user, "sort-chip");
    expect(
      screen.queryByRole("button", { name: "Toggle sort direction for Status" }),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByLabelText("Add sort key")).getByRole("option", { name: "Status" }),
    ).toBeInTheDocument();

    // Column picker order is back to typeB's own field order: status, extra.
    await openChip(user, "columns-chip");
    const picker = within(screen.getByTestId("column-picker"));
    const labels = picker.getAllByRole("listitem").map((item) => item.textContent);
    expect(labels[0]).toContain("Status");
    expect(labels[1]).toContain("Extra");

    // Row selection is cleared: the header "select all" checkbox is unchecked, and typeA's
    // selected row is gone entirely (a different collection's records).
    expect(screen.getByLabelText("Select all rows")).not.toBeChecked();
    expect(screen.queryByLabelText("Select row ALPHA-1")).not.toBeInTheDocument();
  });
});
