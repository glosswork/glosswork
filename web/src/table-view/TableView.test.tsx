import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { renderWithProviders } from "../test/renderWithProviders";
import type { FieldDoc, ObjectTypeDetail } from "../api/objectTypes";
import type { PrincipalSidecar } from "../api/principals";
import type { RecordDoc } from "../api/records";
import type { SavedView } from "../api/savedViews";
import { TableView } from "./TableView";
import { useObjectTypes } from "../hooks/useObjectTypes";

const objectType: ObjectTypeDetail = {
  key: "initiative",
  name: "Initiative",
  name_plural: "Initiatives",
  description: "A funded, sponsored workstream.",
  key_prefix: "INIT",
  record_count: 2,
  field_count: 3,
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
    {
      key: "owner",
      name: "Owner",
      type: "relation",
      description: "Who owns this initiative.",
      required: false,
      unique: false,
      indexed: false,
      embed: false,
      default: null,
      config: {},
      position: 2,
      operators: [],
      display_eligible: false,
      target_type_key: "person",
      cardinality: "one",
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

let queryRequests: {
  filter?: unknown;
  sort?: unknown;
  expand_relations?: string[];
  cursor?: string | null;
}[] = [];
let recordsStore: RecordDoc[] = [];
// Undefined by default (a page with no user_ref field carries none), and set by
// the `user_ref` rendering describe block below to prove the sidecar reaches the table cell.
let principalsStore: PrincipalSidecar | undefined;
let savedViewsStore: SavedView[] = [];
let nextViewId = 0;
let patchRequests: { ref: string; body: unknown }[] = [];
let versionConflictOnNextPatch: string | null = null;
let bulkUpdateRequests: { filter: unknown; values: unknown; dry_run: boolean }[] = [];

function savedView(overrides: Partial<SavedView> & { name: string; config: Record<string, unknown> }): SavedView {
  nextViewId += 1;
  return {
    id: `view-${nextViewId}`,
    object_type_id: "obj-1",
    description: null,
    mode: "table",
    is_default: false,
    created_at: "2026-08-20T09:00:00",
    created_by: "principal-1",
    updated_at: "2026-08-20T09:00:00",
    updated_by: "principal-1",
    ...overrides,
  };
}

const server = setupServer(
  http.post("/api/v1/object-types/:key/query", async ({ request }) => {
    const body = (await request.json()) as { filter?: unknown; sort?: unknown; expand_relations?: string[] };
    queryRequests.push(body);
    return HttpResponse.json({
      records: recordsStore,
      total_count: recordsStore.length,
      next_cursor: null,
      truncated: false,
      principals: principalsStore,
    });
  }),
  http.patch("/api/v1/records/:ref", async ({ request, params }) => {
    const body = (await request.json()) as {
      values: Record<string, unknown>;
      expected_version: number;
    };
    const ref = params.ref as string;
    patchRequests.push({ ref, body });
    if (versionConflictOnNextPatch === ref) {
      versionConflictOnNextPatch = null;
      return HttpResponse.json(
        {
          error: {
            code: "version_conflict",
            message: "stale write",
            details: {
              record_key: ref,
              current_version: 9,
              supplied_version: body.expected_version,
              conflicting_fields: {
                status: { your_value: body.values.status, current_value: "at_risk" },
              },
              changed_since_your_version: ["status"],
            },
          },
        },
        { status: 409 },
      );
    }
    const existing = recordsStore.find((candidate) => candidate.key === ref);
    if (!existing) return HttpResponse.json({ error: { code: "not_found", message: "n/a", details: {} } }, { status: 404 });
    const updated: RecordDoc = {
      ...existing,
      data: { ...existing.data, ...body.values },
      version: existing.version + 1,
    };
    recordsStore = recordsStore.map((candidate) => (candidate.key === ref ? updated : candidate));
    return HttpResponse.json(updated);
  }),
  http.delete("/api/v1/records/:ref", ({ params }) => {
    const ref = params.ref as string;
    if (ref === "INIT-BLOCKED") {
      return HttpResponse.json(
        {
          error: {
            code: "relation_blocked",
            message: "blocked",
            details: { record_key: ref, blocking_record_keys: ["INIT-OTHER"] },
          },
        },
        { status: 409 },
      );
    }
    const existing = recordsStore.find((candidate) => candidate.key === ref);
    recordsStore = recordsStore.filter((candidate) => candidate.key !== ref);
    return HttpResponse.json({ ...existing, deleted_at: "2026-08-24T10:00:00" });
  }),
  http.post("/api/v1/object-types/:key/bulk-update", async ({ request }) => {
    const body = (await request.json()) as { filter: unknown; values: Record<string, unknown>; dry_run: boolean };
    bulkUpdateRequests.push(body);
    const filterValue = (body.filter as { value: string[] }).value;
    if (!body.dry_run) {
      recordsStore = recordsStore.map((record) =>
        filterValue.includes(record.key) ? { ...record, data: { ...record.data, ...body.values } } : record,
      );
    }
    return HttpResponse.json({ affected_count: filterValue.length, sample_keys: filterValue, dry_run: body.dry_run });
  }),
  http.get("/api/v1/object-types/:key/saved-views", () => HttpResponse.json(savedViewsStore)),
  http.post("/api/v1/object-types/:key/saved-views", async ({ request }) => {
    const body = (await request.json()) as { name: string; config: Record<string, unknown>; mode: string; is_default?: boolean };
    const created = savedView({ name: body.name, config: body.config, mode: body.mode, is_default: body.is_default ?? false });
    savedViewsStore = [...savedViewsStore, created];
    return HttpResponse.json(created);
  }),
  http.patch("/api/v1/saved-views/:viewId", async ({ request, params }) => {
    const body = (await request.json()) as Partial<SavedView>;
    savedViewsStore = savedViewsStore.map((view) =>
      view.id === params.viewId ? { ...view, ...body } : view,
    );
    const updated = savedViewsStore.find((view) => view.id === params.viewId);
    return HttpResponse.json(updated);
  }),
  // The `user_ref` picker's directory. No test below opens a `user_ref` editor,
  // except the describe block that adds one, so an empty list is a safe default everywhere else.
  http.get("/api/v1/principals/directory", () =>
    HttpResponse.json({
      principals: [
        { id: "principal-owner", display_name: "Sarah Okonjo", email: "sarah@example.com", type: "user", is_active: true },
      ],
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  queryRequests = [];
  patchRequests = [];
  bulkUpdateRequests = [];
  versionConflictOnNextPatch = null;
  savedViewsStore = [];
  principalsStore = undefined;
  recordsStore = [
    record({ key: "INIT-1", data: { name: "Alpha", status: "on_track" } }),
    record({ key: "INIT-2", data: { name: "Beta", status: "at_risk" } }),
  ];
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

async function findCell(recordKey: string, fieldLabel: string) {
  return screen.findByRole("button", { name: `Edit ${fieldLabel} for ${recordKey}` });
}

/**
 * Group, sort and columns are chips, so the control each one used to be now lives in a popover
 * behind it. The control keeps its accessible name and the test takes an opening step, rather
 * than the name changing to suit the test.
 *
 * Addressed by the chip's test id rather than by its sentence, because the sentence is the thing
 * under test in some of these — `Sort: Status, Name` is what the chip says once two keys are in
 * it, and a helper that had to know that would be asserting it by accident.
 */
async function openChip(user: ReturnType<typeof userEvent.setup>, testId: string) {
  await user.click(within(screen.getByTestId(testId)).getAllByRole("button")[0]);
}

/**
 * The same again for the View menu: the saved-view list, `Save`, `Save as new`,
 * `Set as default`, `New view name` and `Export CSV` are one popover on the right of the
 * toolbar. Every one of those accessible names is kept; what the tests driving them take is
 * this click.
 *
 * Addressed by the trigger's accessible name rather than by a test id, because `Popover` names
 * its panel and not its trigger, and `Current view: <name>` is the name a person hears.
 */
async function openViewMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /^Current view/ }));
}

describe("TableView: inline cell editing", () => {
  it("commits an edit via PATCH with expected_version and updates the row without a full reload", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);

    const cell = await findCell("INIT-1", "Name");
    expect(cell).toHaveTextContent("Alpha");
    await user.click(cell);

    const input = screen.getByLabelText("Name value for INIT-1");
    await user.clear(input);
    await user.type(input, "Alpha Two");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(patchRequests).toHaveLength(1));
    expect(patchRequests[0]).toEqual({
      ref: "INIT-1",
      body: { values: { name: "Alpha Two" }, expected_version: 1, force: false },
    });
    expect(await screen.findByRole("button", { name: "Edit Name for INIT-1" })).toHaveTextContent(
      "Alpha Two",
    );
  });

  it("opens a side-by-side merge dialog on a 409 and resubmits with expected_version from current_version", async () => {
    const user = userEvent.setup();
    versionConflictOnNextPatch = "INIT-1";
    renderWithProviders(<TableView objectType={objectType} />);

    const cell = await findCell("INIT-1", "Status");
    await user.click(cell);
    const select = screen.getByLabelText("Status value for INIT-1");
    await user.selectOptions(select, "on_track");
    await user.keyboard("{Tab}");

    const dialog = await screen.findByTestId("merge-conflict-dialog");
    expect(within(dialog).getByTestId("conflict-field-status")).toBeInTheDocument();
    const row = within(dialog).getByTestId("conflict-field-status");
    // Side-by-side: both the pending and current values are visible in the same row.
    expect(within(row).getByText("On Track")).toBeInTheDocument();
    expect(within(row).getByText("At Risk")).toBeInTheDocument();

    await user.click(within(row).getByLabelText("Theirs"));
    await user.click(within(dialog).getByRole("button", { name: "Resubmit" }));

    await waitFor(() => expect(patchRequests).toHaveLength(2));
    expect(patchRequests[1]).toEqual({
      ref: "INIT-1",
      body: { values: { status: "at_risk" }, expected_version: 9, force: false },
    });
    await waitFor(() => expect(screen.queryByTestId("merge-conflict-dialog")).not.toBeInTheDocument());
  });

  it("closes the merge dialog on Escape without resubmitting (native <dialog> cancel)", async () => {
    const user = userEvent.setup();
    versionConflictOnNextPatch = "INIT-1";
    renderWithProviders(<TableView objectType={objectType} />);

    const cell = await findCell("INIT-1", "Status");
    await user.click(cell);
    await user.selectOptions(screen.getByLabelText("Status value for INIT-1"), "on_track");
    await user.keyboard("{Tab}");

    const dialog = await screen.findByTestId("merge-conflict-dialog");
    // A real engine turns Escape inside showModal() into the `cancel` event; jsdom does not
    // synthesize it from a keypress, so fire the event the engine would.
    fireEvent(dialog, new Event("cancel", { cancelable: true }));
    await waitFor(() => expect(screen.queryByTestId("merge-conflict-dialog")).not.toBeInTheDocument());
    // Cancelled, not resubmitted: only the original conflicting PATCH went out.
    expect(patchRequests).toHaveLength(1);
  });
});

describe("TableView: multi-column sort", () => {
  it("sends a correctly ordered multi-key sort array", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    await openChip(user, "sort-chip");
    await user.selectOptions(screen.getByLabelText("Add sort key"), "status");
    await user.selectOptions(screen.getByLabelText("Add sort key"), "name");
    await user.click(screen.getByRole("button", { name: "Toggle sort direction for Name" }));

    await waitFor(() => {
      const last = queryRequests[queryRequests.length - 1];
      expect(last.sort).toEqual([
        { field: "status", dir: "asc" },
        { field: "name", dir: "desc" },
      ]);
    });
  });
});

describe("TableView: grouping", () => {
  it("groups by a single_select field with per-group headers and counts", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    await openChip(user, "group-chip");
    await user.selectOptions(screen.getByLabelText("Group by"), "status");

    const headers = await screen.findAllByTestId("group-header");
    const texts = headers.map((header) => header.textContent);
    expect(texts).toContain("On Track (1)");
    expect(texts).toContain("At Risk (1)");
  });

  it("groups by a relation field using the expanded link's key", async () => {
    const user = userEvent.setup();
    recordsStore = [
      record({
        key: "INIT-1",
        data: { name: "Alpha", status: "on_track" },
        expand: { owner: [{ key: "PERSON-1", id: "p1", display: "Ada" }] },
      }),
      record({
        key: "INIT-2",
        data: { name: "Beta", status: "at_risk" },
        expand: { owner: [] },
      }),
    ];
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    await openChip(user, "group-chip");
    await user.selectOptions(screen.getByLabelText("Group by"), "owner");

    await waitFor(() => {
      const last = queryRequests[queryRequests.length - 1];
      expect(last.expand_relations).toEqual(["owner"]);
    });

    const headers = await screen.findAllByTestId("group-header");
    const texts = headers.map((header) => header.textContent);
    expect(texts).toContain("PERSON-1 — Ada (1)");
    expect(texts).toContain("(unlinked) (1)");
  });
});

/**
 * docs/DESIGN.md 7.4 for group and sort — "Group and sort are the same chip grammar
 * (`Group by Stage`, `Sort: Next action`)" — and 8.2 for the picker being a popover.
 *
 * What is asserted is the **sentence** and the `×`, because those are what the chip adds. The
 * controls inside the popovers are covered by the tests above, which open a popover to reach
 * them.
 */
describe("TableView: group, sort and columns are chips", () => {
  it("says what it is grouped by, and the × puts it back to no grouping", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    expect(screen.getByTestId("group-chip")).toHaveTextContent("Group");
    await openChip(user, "group-chip");
    await user.selectOptions(screen.getByLabelText("Group by"), "status");

    // 7.4's own example sentence, with this fixture's field name in it.
    expect(screen.getByTestId("group-chip")).toHaveTextContent("Group by Status");
    await screen.findAllByTestId("group-header");

    await user.click(screen.getByRole("button", { name: "Remove grouping" }));
    expect(screen.getByTestId("group-chip")).toHaveTextContent("Group");
    expect(screen.queryAllByTestId("group-header")).toEqual([]);
  });

  it("names the sort keys in order, and the × clears the sort", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    expect(screen.getByTestId("sort-chip")).toHaveTextContent("Sort");
    await openChip(user, "sort-chip");
    await user.selectOptions(screen.getByLabelText("Add sort key"), "status");
    await user.selectOptions(screen.getByLabelText("Add sort key"), "name");

    // Names, in key order, and no directions: 7.4's form is `Sort: Next action`.
    expect(screen.getByTestId("sort-chip")).toHaveTextContent("Sort: Status, Name");

    await user.click(screen.getByRole("button", { name: "Remove sorting" }));
    await waitFor(() => {
      const last = queryRequests[queryRequests.length - 1];
      expect(last.sort).toBeUndefined();
    });
  });

  it("counts the columns it is hiding, and says nothing when it is hiding none", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    expect(screen.getByTestId("columns-chip")).toHaveTextContent("Columns");
    await openChip(user, "columns-chip");
    await user.click(within(screen.getByTestId("column-picker")).getByLabelText("Owner"));

    // A picker behind a popover can hide a column and say nothing about it; this is the count
    // that stops it (the fixture type has three fields).
    expect(screen.getByTestId("columns-chip")).toHaveTextContent("Columns: 2 of 3");
  });
});

describe("TableView: column state persists through a saved view", () => {
  it("round-trips visibility and order through save-as-new and a simulated reload", async () => {
    const user = userEvent.setup();
    const { unmount } = renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    // Hide the "Owner" column and move "Status" above "Name".
    await openChip(user, "columns-chip");
    const ownerCheckbox = within(screen.getByTestId("column-picker")).getByLabelText("Owner");
    await user.click(ownerCheckbox);
    await user.click(screen.getByLabelText("Move Status up"));

    await openViewMenu(user);
    await user.type(screen.getByLabelText("New view name"), "My view");
    await user.click(screen.getByRole("button", { name: "Save as new" }));

    await waitFor(() => expect(savedViewsStore).toHaveLength(1));
    const saved = savedViewsStore[0];
    expect(saved.config).toMatchObject({
      columns: {
        order: ["status", "name", "owner"],
        visibility: { owner: false },
      },
    });

    unmount();

    const secondRender = renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");
    await openViewMenu(user);
    await user.selectOptions(screen.getByLabelText("Saved view"), saved.id);

    await openChip(user, "columns-chip");
    await waitFor(() => {
      const picker = within(screen.getByTestId("column-picker"));
      expect(picker.getByLabelText("Owner")).not.toBeChecked();
    });
    const picker = within(screen.getByTestId("column-picker"));
    const labels = picker.getAllByRole("listitem").map((item) => item.textContent);
    expect(labels[0]).toContain("Status");
    secondRender.unmount();
  });
});

describe("TableView: row-selection column order", () => {
  it("renders the selection checkbox first, in both the header row and a data row", async () => {
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    const table = screen.getByTestId("records-table");
    const headerRow = table.querySelector("thead tr");
    expect(headerRow).not.toBeNull();
    const firstHeaderCell = headerRow!.querySelectorAll("th")[0];
    expect(within(firstHeaderCell).getByLabelText("Select all rows")).toBeInTheDocument();

    const dataRow = screen.getByTestId("row-INIT-1");
    const firstDataCell = dataRow.querySelectorAll("td")[0];
    expect(within(firstDataCell).getByLabelText("Select row INIT-1")).toBeInTheDocument();
  });
});

describe("TableView: bulk edit", () => {
  it("previews with a key-in filter, shows affected_count, then confirms", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    await user.click(screen.getByLabelText("Select row INIT-1"));
    await user.click(screen.getByLabelText("Select row INIT-2"));

    const toolbar = within(screen.getByTestId("bulk-toolbar"));
    await user.selectOptions(toolbar.getByLabelText("Bulk edit field"), "status");
    await user.selectOptions(toolbar.getByLabelText("Bulk edit value"), "at_risk");
    await user.click(toolbar.getByRole("button", { name: "Preview" }));

    await waitFor(() => expect(bulkUpdateRequests).toHaveLength(1));
    expect(bulkUpdateRequests[0]).toEqual({
      filter: { field: "key", op: "in", value: ["INIT-1", "INIT-2"] },
      values: { status: "at_risk" },
      dry_run: true,
    });
    expect(await screen.findByText("2 record(s) will be updated.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(bulkUpdateRequests).toHaveLength(2));
    expect(bulkUpdateRequests[1].dry_run).toBe(false);
  });
});

describe("TableView: bulk delete", () => {
  it("distinguishes successful deletes from relation_blocked failures with blocking keys", async () => {
    const user = userEvent.setup();
    recordsStore = [
      record({ key: "INIT-1", data: { name: "Alpha", status: "on_track" } }),
      record({ key: "INIT-BLOCKED", data: { name: "Blocked", status: "on_track" } }),
    ];
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    await user.click(screen.getByLabelText("Select row INIT-1"));
    await user.click(screen.getByLabelText("Select row INIT-BLOCKED"));
    await user.click(within(screen.getByTestId("bulk-toolbar")).getByRole("button", { name: "Bulk delete" }));

    const results = await screen.findByTestId("bulk-delete-results");
    expect(within(results).getByTestId("bulk-delete-result-INIT-1")).toHaveTextContent("deleted");
    expect(within(results).getByTestId("bulk-delete-result-INIT-BLOCKED")).toHaveTextContent(
      "blocked by INIT-OTHER",
    );
  });
});

describe("TableView: default saved view", () => {
  it("loads the view flagged is_default on mount when no view is explicitly selected", async () => {
    const user = userEvent.setup();
    savedViewsStore = [
      savedView({
        name: "At-risk board",
        is_default: true,
        config: {
          filter: null,
          sort: [],
          groupBy: "status",
          columns: { order: ["name", "status", "owner"], visibility: {}, sizing: {} },
          mode: "table",
        },
      }),
    ];
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    await openViewMenu(user);
    await waitFor(() =>
      expect(screen.getByLabelText("Saved view")).toHaveValue(savedViewsStore[0].id),
    );
    await openChip(user, "group-chip");
    expect(screen.getByLabelText("Group by")).toHaveValue("status");
  });
});

/**
 * The nested tree, which is reached through the `Advanced` chip rather than from a builder
 * sitting open on the page (the chip row is a flat AND, and the grammar lives one click away).
 * **The asserted AST literal below is the one the open builder sent** — the chips change the
 * interaction, not the filter on the wire, and this test is what pins it.
 *
 * It also exercises the thing a flat chip row makes awkward and the row therefore handles
 * deliberately: the tree passes through states that *are* chip-expressible
 * (`{"and": [one condition]}`) on its way to one that is not, so a row that mounted the `Advanced`
 * chip only in one of those two shapes would unmount the builder mid-edit and this test would fail
 * with a lost tree.
 */
describe("TableView: nested filter builder", () => {
  it("sends the exact nested and/or/not filter tree built through the Advanced chip", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    await user.click(screen.getByRole("button", { name: "Advanced" }));
    await screen.findByTestId("filter-builder");

    // Start the tree as a top-level AND group.
    await user.click(screen.getByRole("button", { name: "+ Group (AND)" }));

    // Condition 0: status eq on_track.
    await user.click(
      within(screen.getByTestId("filter-node-root-actions")).getByRole("button", {
        name: "+ Condition",
      }),
    );
    const condition0 = screen.getByTestId("filter-node-root-0");
    await user.selectOptions(within(condition0).getByLabelText("Field"), "status");
    await user.selectOptions(within(condition0).getByLabelText("Status value"), "on_track");

    // Nested group (child 1), toggled from its AND default to OR.
    await user.click(
      within(screen.getByTestId("filter-node-root-actions")).getByRole("button", {
        name: "+ Group",
      }),
    );
    const nestedGroup = screen.getByTestId("filter-node-root-1");
    await user.selectOptions(within(nestedGroup).getByLabelText("Group type"), "or");

    // Nested condition 0: name contains "Alpha".
    await user.click(
      within(screen.getByTestId("filter-node-root-1-actions")).getByRole("button", {
        name: "+ Condition",
      }),
    );
    const nestedCondition0 = screen.getByTestId("filter-node-root-1-0");
    await user.selectOptions(within(nestedCondition0).getByLabelText("Field"), "name");
    await user.selectOptions(within(nestedCondition0).getByLabelText("Operator"), "contains");
    await user.type(within(nestedCondition0).getByLabelText("Name value"), "Alpha");

    // Nested condition 1: key eq "INIT-1" (a system field).
    await user.click(
      within(screen.getByTestId("filter-node-root-1-actions")).getByRole("button", {
        name: "+ Condition",
      }),
    );
    const nestedCondition1 = screen.getByTestId("filter-node-root-1-1");
    await user.selectOptions(within(nestedCondition1).getByLabelText("Field"), "key");
    await user.type(within(nestedCondition1).getByLabelText("key value"), "INIT-1");

    // Condition 2: NOT(status eq at_risk).
    await user.click(
      within(screen.getByTestId("filter-node-root-actions")).getByRole("button", {
        name: "+ Condition",
      }),
    );
    const condition2 = screen.getByTestId("filter-node-root-2");
    await user.selectOptions(within(condition2).getByLabelText("Field"), "status");
    await user.selectOptions(within(condition2).getByLabelText("Status value"), "at_risk");
    await user.click(within(condition2).getByRole("button", { name: "NOT" }));

    await waitFor(() => {
      const last = queryRequests[queryRequests.length - 1];
      expect(last.filter).toEqual({
        and: [
          { field: "status", op: "eq", value: "on_track" },
          {
            or: [
              { field: "name", op: "contains", value: "Alpha" },
              { field: "key", op: "eq", value: "INIT-1" },
            ],
          },
          { not: { field: "status", op: "eq", value: "at_risk" } },
        ],
      });
    });
  });
});

describe("TableView: loading and empty states", () => {
  it("shows the loading skeleton while the records query is in flight, then the rows", async () => {
    renderWithProviders(<TableView objectType={objectType} />);
    expect(screen.getByTestId("table-loading")).toBeInTheDocument();
    await screen.findByTestId("row-INIT-1");
    expect(screen.queryByTestId("table-loading")).not.toBeInTheDocument();
  });

  it("renders the no-records-yet empty state when the type has no records and no filter", async () => {
    recordsStore = [];
    renderWithProviders(<TableView objectType={objectType} />);
    const empty = await screen.findByTestId("table-empty");
    expect(empty).toHaveTextContent("No records yet");
    expect(screen.queryByTestId("table-empty-filtered")).not.toBeInTheDocument();
  });

  it("offers Clear filter when a filter matches nothing, and clearing returns to unfiltered", async () => {
    const user = userEvent.setup();
    recordsStore = [];
    renderWithProviders(<TableView objectType={objectType} />);
    await screen.findByTestId("table-empty");

    // The arrangement changed twice and the assertions did not. It once reached the filtered empty
    // state with one click on `+ Condition`, because the builder committed a condition with no
    // value — a defect in its own right: that click also sent a 422, and the "filtered" state it
    // produced was a state no complete filter had ever asked for. The gate then held an incomplete
    // condition back, so the filter had to be *finished* first, and finishing it is now a chip and
    // a popover. What this test is about — a filter that matches nothing offers Clear filter, and
    // clearing it returns to unfiltered — is untouched by both and is still asserted below.
    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    const popover = screen.getByTestId("condition-popover");
    await user.type(within(popover).getByLabelText("Name value"), "Nothing matches this");
    await user.click(within(popover).getByRole("button", { name: "Apply" }));

    const filtered = await screen.findByTestId("table-empty-filtered");
    expect(filtered).toHaveTextContent("No records match this filter");

    await user.click(within(filtered).getByRole("button", { name: "Clear filter" }));
    await screen.findByTestId("table-empty");
    // Cleared back to no filter at all: the row is the `+ Add filter` chip and nothing else.
    expect(screen.queryByTestId("filter-chip-0")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ Add filter" })).toBeInTheDocument();
  });
});

// `query_records` returns a `next_cursor` from a keyset walk; without the pager the frontend never
// asks for page two, so a table could not reach record 201.
describe("TableView: the pager", () => {
  const PAGE_ONE = [
    record({ key: "INIT-1", data: { name: "Alpha", status: "on_track" } }),
    record({ key: "INIT-2", data: { name: "Beta", status: "at_risk" } }),
  ];
  const PAGE_TWO = [record({ key: "INIT-3", data: { name: "Gamma", status: "on_track" } })];

  /** A query handler that walks two pages by cursor and records every request body. */
  function usePagedHandler() {
    server.use(
      http.post("/api/v1/object-types/:key/query", async ({ request }) => {
        const body = (await request.json()) as { cursor?: string | null };
        queryRequests.push(body);
        if (body.cursor === "cursor-1") {
          return HttpResponse.json({
            records: PAGE_TWO,
            total_count: 3,
            next_cursor: null,
            truncated: false,
          });
        }
        return HttpResponse.json({
          records: PAGE_ONE,
          total_count: 3,
          next_cursor: "cursor-1",
          truncated: false,
        });
      }),
    );
  }

  const lastCursor = () => queryRequests[queryRequests.length - 1].cursor ?? null;

  async function goToPageTwo(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByTestId("row-INIT-1");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByTestId("row-INIT-3");
  }

  it("Next carries the previous page's next_cursor and Previous walks back without one", async () => {
    const user = userEvent.setup();
    usePagedHandler();
    renderWithProviders(<TableView objectType={objectType} />);

    await screen.findByTestId("row-INIT-1");
    expect(screen.getByRole("status")).toHaveTextContent("Showing 1-2 of 3.");
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    expect(lastCursor()).toBeNull();

    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByTestId("row-INIT-3");
    expect(lastCursor()).toBe("cursor-1");
    expect(screen.queryByTestId("row-INIT-1")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();

    // Nothing is popped, so Previous is a cache hit: page one is on screen immediately, with no
    // loading skeleton, and react-query revalidates it in the background.
    await user.click(screen.getByRole("button", { name: "Previous" }));
    expect(screen.getByTestId("row-INIT-1")).toBeInTheDocument();
    expect(screen.queryByTestId("table-loading")).not.toBeInTheDocument();
    await waitFor(() => expect(lastCursor()).toBeNull());

    // And the forward-only cursor contract is never asked to run backwards: every cursor ever
    // sent is either null or one the server itself handed out.
    const sent = queryRequests.map((body) => body.cursor ?? null);
    expect(new Set(sent)).toEqual(new Set([null, "cursor-1"]));
  });

  it("clears the row selection on a page turn, because a stale selection is data loss", async () => {
    const user = userEvent.setup();
    usePagedHandler();
    renderWithProviders(<TableView objectType={objectType} />);

    await screen.findByTestId("row-INIT-1");
    await user.click(screen.getByLabelText("Select row INIT-1"));
    expect(screen.getByTestId("bulk-toolbar")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByTestId("row-INIT-3");
    // `useBulkDelete` deletes exactly the selected keys, so a selection made on page one and a
    // delete pressed on page two would destroy rows the user cannot see.
    expect(screen.queryByTestId("bulk-toolbar")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Select row INIT-3")).not.toBeChecked();
  });

  // The five call sites that change the query identity. None of them fires a reset itself: the
  // reset is derived from the identity during render, so these five are the test matrix rather
  // than five places a line had to be added.
  describe("restarts the walk at page one whenever the query identity changes", () => {
    async function expectRestarted() {
      await waitFor(() => expect(lastCursor()).toBeNull());
      await screen.findByTestId("row-INIT-1");
      expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();
    }

    it("1. the filter chip row's onChange", async () => {
      const user = userEvent.setup();
      usePagedHandler();
      renderWithProviders(<TableView objectType={objectType} />);
      await goToPageTwo(user);

      // The first of the five call sites is the chip row; it was the FilterBuilder, which is now
      // behind the `Advanced` chip and reaches `setFilter`
      // through the same prop. The reset is derived from the query identity either way, which is
      // the property this matrix exists to hold.
      await user.click(screen.getByRole("button", { name: "+ Add filter" }));
      const popover = screen.getByTestId("condition-popover");
      await user.selectOptions(within(popover).getByLabelText("Field"), "status");
      await user.selectOptions(within(popover).getByLabelText("Status value"), "on_track");

      await expectRestarted();
    });

    it("2. SortControls' onChange", async () => {
      const user = userEvent.setup();
      usePagedHandler();
      renderWithProviders(<TableView objectType={objectType} />);
      await goToPageTwo(user);

      await openChip(user, "sort-chip");
      await user.selectOptions(screen.getByLabelText("Add sort key"), "status");

      await expectRestarted();
    });

    it("3. the column header's own cycleSingleSort", async () => {
      const user = userEvent.setup();
      usePagedHandler();
      renderWithProviders(<TableView objectType={objectType} />);
      await goToPageTwo(user);

      await user.click(screen.getByRole("button", { name: /^Name/ }));

      await expectRestarted();
    });

    it("4. GroupBySelect's onChange", async () => {
      const user = userEvent.setup();
      usePagedHandler();
      renderWithProviders(<TableView objectType={objectType} />);
      await goToPageTwo(user);

      await openChip(user, "group-chip");
      await user.selectOptions(screen.getByLabelText("Group by"), "status");

      await expectRestarted();
    });

    it("5. applyConfig on saved-view load", async () => {
      const user = userEvent.setup();
      usePagedHandler();
      savedViewsStore = [
        savedView({
          name: "At risk",
          config: {
            mode: "table",
            filter: { field: "status", op: "eq", value: "at_risk" },
            sort: [],
            groupBy: null,
            columns: { order: ["name", "status", "owner"], visibility: {}, sizing: {} },
          },
        }),
      ];
      renderWithProviders(<TableView objectType={objectType} />);
      await goToPageTwo(user);

      await openViewMenu(user);
      await user.selectOptions(screen.getByLabelText("Saved view"), savedViewsStore[0].id);

      await expectRestarted();
    });
  });

  // Without an error branch a failed records query would render nothing, and the pager is what
  // makes a rejected cursor reachable.
  it("renders the server's envelope when the records query fails", async () => {
    server.use(
      http.post("/api/v1/object-types/:key/query", () =>
        HttpResponse.json(
          {
            error: {
              code: "validation_failed",
              message: "cursor does not match the current sort.",
              details: {},
            },
          },
          { status: 400 },
        ),
      ),
    );
    renderWithProviders(<TableView objectType={objectType} />);

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Could not load records.")).toBeInTheDocument();
    expect(alert.textContent).toContain("validation_failed");
    expect(alert.textContent).toContain("cursor does not match the current sort.");
  });
});

/**
 * Completeness is not acceptance: `filters.py::_resolve_scalar`
 * refuses **complete** values too — a `date` that is neither ISO nor a date token, a non-`int`
 * for an `integer`, an unresolvable `user_ref`, a relation key naming no record — so a chip can
 * be finished, sent, and refused. The answer to that is not the page-level alert this file
 * asserts in the pager's failed-query case above: that alert empties the table, which is the
 * defect. It goes to the chip that carries the field the server named, and the rows the
 * person was looking at stay where they are.
 *
 * The two halves are asserted separately on purpose. The failed-query case's `validation_failed`
 * carries no
 * `field_key` and arrives with no filter at all, so it is nobody's chip and keeps the page-level
 * alert — the fence that stops this behaviour swallowing every error on the screen.
 */
describe("TableView: a complete-but-refused filter value", () => {
  function useRefusingHandler() {
    server.use(
      http.post("/api/v1/object-types/:key/query", async ({ request }) => {
        const body = (await request.json()) as { filter?: unknown };
        if (body.filter) {
          return HttpResponse.json(
            {
              error: {
                code: "validation_failed",
                message: "Invalid value for field 'status'.",
                details: { field_key: "status" },
              },
            },
            { status: 422 },
          );
        }
        return HttpResponse.json({
          records: recordsStore,
          total_count: recordsStore.length,
          next_cursor: null,
          truncated: false,
        });
      }),
    );
  }

  it("shows the server's message in the offending chip's popover and keeps the rows on screen", async () => {
    const user = userEvent.setup();
    useRefusingHandler();
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    const popover = screen.getByTestId("condition-popover");
    await user.selectOptions(within(popover).getByLabelText("Field"), "status");
    // A select commits on change and leaves the popover open, so the refusal lands
    // where the mistake was made, in front of the person who made it.
    await user.selectOptions(within(popover).getByLabelText("Status value"), "at_risk");

    const message = await within(screen.getByTestId("condition-popover")).findByRole("alert");
    expect(message).toHaveTextContent("Invalid value for field 'status'.");
    expect(screen.queryByText("Could not load records.")).not.toBeInTheDocument();
    // The previous result set, still there. `keepPreviousData` drops it the moment the query
    // errors, so this is the half that needs the component to hold it.
    expect(screen.getByTestId("row-INIT-1")).toBeInTheDocument();
    expect(screen.getByTestId("row-INIT-2")).toBeInTheDocument();
  });
});

// Without a record key a row could not be named, quoted to an agent, or typed into the audit
// browser's Record filter — and there would be nothing to click through to the record with.
describe("TableView: the Key column", () => {
  it("renders each row's key as a link to its detail page", async () => {
    renderWithProviders(<TableView objectType={objectType} />);

    const row = await screen.findByTestId("row-INIT-1");
    const link = within(row).getByRole("link", { name: "INIT-1" });
    expect(link).toHaveAttribute("href", "/initiative/INIT-1");
  });

  // Restated for the `By` column, which docs/DESIGN.md 6.4 puts SECOND — `columns.tsx` documents
  // the selection checkbox as always first, and 6.4 matches the code rather than the code
  // contradicting it. Pinning all four positions rather than the two that moved: the order is the
  // assertion.
  it("sits between the By column and the first field", async () => {
    renderWithProviders(<TableView objectType={objectType} />);

    const row = await screen.findByTestId("row-INIT-1");
    const cells = within(row).getAllByRole("cell");
    expect(within(cells[0]).getByLabelText("Select row INIT-1")).toBeInTheDocument();
    expect(within(cells[1]).getByTestId("avatar")).toBeInTheDocument();
    expect(within(cells[2]).getByRole("link", { name: "INIT-1" })).toBeInTheDocument();
    expect(within(cells[3]).getByRole("button", { name: "Edit Name for INIT-1" })).toBeInTheDocument();
  });

  it("offers no sort, because the query has no field to sort on", async () => {
    renderWithProviders(<TableView objectType={objectType} />);
    await screen.findByTestId("row-INIT-1");

    const headerRow = within(screen.getByTestId("records-table")).getAllByRole("row")[0];
    const keyHeader = within(headerRow).getByText("Key");
    expect(keyHeader.closest("button")).toBeNull();
    // The field headers still do sort.
    expect(within(headerRow).getByRole("button", { name: /^Name/ })).toBeInTheDocument();
  });

  it("never enters columnOrder state, so the saved view and the CSV export never see it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    // The picker lists only fields, and its move-up/move-down indexing is against fields alone.
    await openChip(user, "columns-chip");
    const picker = within(screen.getByTestId("column-picker"));
    expect(picker.queryByLabelText("Key")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Move Status up"));

    await openViewMenu(user);
    await user.type(screen.getByLabelText("New view name"), "Keyless view");
    await user.click(screen.getByRole("button", { name: "Save as new" }));

    await waitFor(() => expect(savedViewsStore).toHaveLength(1));
    expect(savedViewsStore[0].config).toMatchObject({
      columns: { order: ["status", "name", "owner"] },
    });
  });
});

// The `long_text` cell keeps its one-line clip and clicking it opens ONE pop-out surface rather
// than an in-cell textarea: it opens in view mode showing
// the whole value, and an Edit control inside it switches that same surface to the text box.
// There is no read-only variant — `long_text` is always editable — and no other field type
// uses it.
const longTextObjectType: ObjectTypeDetail = {
  key: "brief",
  name: "Brief",
  name_plural: "Briefs",
  description: "A fixture type carrying one long_text field.",
  key_prefix: "BRF",
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
      key: "notes",
      name: "Notes",
      type: "long_text",
      description: "The narrative field this pop-out is for.",
      required: false,
      unique: false,
      indexed: false,
      embed: true,
      default: null,
      config: {},
      position: 1,
      operators: ["contains"],
      display_eligible: true,
    },
  ],
  system_fields: [
    { key: "key", type: "short_text", description: "Human key.", operators: ["eq", "in"] },
  ],
};

const LONG_NOTES =
  "The cutover depends on the vendor's reconciliation window closing before the quarter does.\n" +
  "It also depends on the finance team accepting a manual journal for the residue.";

/** The same value written as markdown: the two sides of one fence in one fixture. */
const MARKDOWN_NOTES = "## Cutover\n\n- vendor window\n- manual journal";

describe("TableView: the long-text pop-out", () => {
  beforeEach(() => {
    recordsStore = [record({ key: "BRF-1", data: { name: "Alpha", notes: LONG_NOTES } })];
  });

  it("opens one pop-out in view mode, showing the whole value and no text box", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={longTextObjectType} />);

    const cell = await findCell("BRF-1", "Notes");
    // The cell itself keeps the one-line clip whether or not the pop-out opens.
    expect(cell).toHaveClass("truncate");
    await user.click(cell);

    const dialog = await screen.findByTestId("long-text-cell-dialog");
    expect(dialog).toBe(screen.getByRole("dialog", { name: "Notes for BRF-1" }));
    // View mode: the whole value, formatted, and NOT a textarea — anywhere on the page, which
    // is what proves the in-cell editor is gone rather than merely hidden behind the dialog.
    expect(within(dialog).getByTestId("long-text-cell-value")).toHaveTextContent(
      "the finance team accepting a manual journal for the residue.",
    );
    expect(screen.queryByLabelText("Notes value for BRF-1")).not.toBeInTheDocument();
  });

  it("the pop-out's view mode renders the value as markdown", async () => {
    recordsStore = [record({ key: "BRF-1", data: { name: "Alpha", notes: MARKDOWN_NOTES } })];
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={longTextObjectType} />);

    await user.click(await findCell("BRF-1", "Notes"));

    const value = within(await screen.findByTestId("long-text-cell-dialog")).getByTestId(
      "long-text-cell-value",
    );
    expect(value.querySelectorAll("li")).toHaveLength(2);
    expect(value.querySelector(".md-h")?.textContent).toBe("Cutover");
    // The heading rule reaches here too: the pop-out is a dialog, and a user-authored `<h2>`
    // inside it would be just as much a document-outline injection as one in a comment.
    expect(value.querySelectorAll("h1, h2, h3, h4, h5, h6")).toHaveLength(0);
  });

  it("SCOPE FENCE: the table cell behind it still renders markdown as plain text", async () => {
    recordsStore = [record({ key: "BRF-1", data: { name: "Alpha", notes: MARKDOWN_NOTES } })];
    renderWithProviders(<TableView objectType={longTextObjectType} />);

    // A FENCE, not a defect proof. Table cells are ruled out on purpose — they are clipped to
    // one line, and there is no room to read a formatted value in one — so this guards behavior
    // that does NOT change and passes against an unfixed tree by construction. It is not
    // counted among the failing-first assertions.
    const cell = await findCell("BRF-1", "Notes");
    expect(cell).toHaveClass("truncate");
    expect(cell.querySelector("li")).toBeNull();
    expect(cell.querySelector(".md-h")).toBeNull();
    expect(cell.textContent).toContain("## Cutover");
  });

  it("switches that same surface to the text box from its Edit control", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={longTextObjectType} />);

    await user.click(await findCell("BRF-1", "Notes"));
    const dialog = await screen.findByTestId("long-text-cell-dialog");
    await user.click(within(dialog).getByRole("button", { name: "Edit" }));

    // One surface, not two: the text box is inside the same dialog element the view was in.
    const textarea = within(dialog).getByLabelText("Notes value for BRF-1");
    expect(textarea.tagName).toBe("TEXTAREA");
    expect(textarea).toHaveValue(LONG_NOTES);
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(within(dialog).queryByTestId("long-text-cell-value")).not.toBeInTheDocument();
  });

  it("saves from the pop-out through the same PATCH the in-cell editor used, and closes", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={longTextObjectType} />);

    await user.click(await findCell("BRF-1", "Notes"));
    const dialog = await screen.findByTestId("long-text-cell-dialog");
    await user.click(within(dialog).getByRole("button", { name: "Edit" }));
    const textarea = within(dialog).getByLabelText("Notes value for BRF-1");
    await user.clear(textarea);
    await user.type(textarea, "Rewritten in the pop-out.");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchRequests).toHaveLength(1));
    expect(patchRequests[0]).toEqual({
      ref: "BRF-1",
      body: { values: { notes: "Rewritten in the pop-out." }, expected_version: 1, force: false },
    });
    await waitFor(() =>
      expect(screen.queryByTestId("long-text-cell-dialog")).not.toBeInTheDocument(),
    );
    expect(await findCell("BRF-1", "Notes")).toHaveTextContent("Rewritten in the pop-out.");
  });

  it("returns to view mode from Cancel, discarding the draft and writing nothing", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={longTextObjectType} />);

    await user.click(await findCell("BRF-1", "Notes"));
    const dialog = await screen.findByTestId("long-text-cell-dialog");
    await user.click(within(dialog).getByRole("button", { name: "Edit" }));
    await user.type(within(dialog).getByLabelText("Notes value for BRF-1"), " and a stray edit");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    // Back to the view mode of the SAME surface: still open, still showing the stored value.
    expect(within(dialog).getByTestId("long-text-cell-value")).toHaveTextContent(
      "The cutover depends on the vendor's reconciliation window closing",
    );
    expect(within(dialog).queryByLabelText("Notes value for BRF-1")).not.toBeInTheDocument();
    expect(patchRequests).toHaveLength(0);

    // And re-entering edit shows the stored value, not the discarded draft.
    await user.click(within(dialog).getByRole("button", { name: "Edit" }));
    expect(within(dialog).getByLabelText("Notes value for BRF-1")).toHaveValue(LONG_NOTES);
  });

  it("opens from the keyboard and dismisses on the native dialog cancel, writing nothing", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={longTextObjectType} />);

    const cell = await findCell("BRF-1", "Notes");
    cell.focus();
    expect(cell).toHaveFocus();
    await user.keyboard("{Enter}");

    const dialog = await screen.findByTestId("long-text-cell-dialog");
    // jsdom never synthesizes the native `cancel` event from an Escape keypress; fire the event
    // a real engine fires, which is what `showModal()` turns Escape into.
    fireEvent(dialog, new Event("cancel", { cancelable: true }));

    await waitFor(() =>
      expect(screen.queryByTestId("long-text-cell-dialog")).not.toBeInTheDocument(),
    );
    expect(patchRequests).toHaveLength(0);
    // The Close control is the pointer equivalent of that same dismissal.
    await user.click(await findCell("BRF-1", "Notes"));
    const reopened = await screen.findByTestId("long-text-cell-dialog");
    await user.click(within(reopened).getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(screen.queryByTestId("long-text-cell-dialog")).not.toBeInTheDocument(),
    );
    expect(patchRequests).toHaveLength(0);
  });

  it("leaves every other field type editing in place, with no pop-out (a scope fence)", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={longTextObjectType} />);

    const row = await screen.findByTestId("row-BRF-1");
    await user.click(await findCell("BRF-1", "Name"));

    // The `short_text` editor is still the input inside its own cell, and nothing modal opened.
    const input = screen.getByLabelText("Name value for BRF-1");
    expect(input.tagName).toBe("INPUT");
    expect(row).toContainElement(input);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

/**
 * The table view's level gates. Every gate here has a `require_level` twin on the server:
 * inline edit, bulk edit/delete, CSV import and the three
 * saved-view writes are all `write`; the CSV export is `read` and is therefore ungated in
 * practice, since a screen below `read` is unreachable.
 */
describe("TableView: level gating", () => {
  const readOnlyType: ObjectTypeDetail = { ...objectType, your_access: "read" };

  it("hides every write affordance for a 'read' caller and explains it exactly once", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={readOnlyType} />);

    await screen.findByTestId("row-INIT-1");

    // Inline cell edit: the cell renders its value, not a button that opens an editor.
    expect(
      screen.queryByRole("button", { name: "Edit Name for INIT-1" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("row-INIT-1")).toHaveTextContent("Alpha");

    // Bulk edit and bulk delete, and the row selection that exists only to feed them.
    expect(screen.queryByTestId("bulk-toolbar")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Select row INIT-1")).not.toBeInTheDocument();

    // Import CSV stays in the toolbar and stays gated.
    expect(screen.queryByRole("link", { name: "Import CSV" })).not.toBeInTheDocument();

    // Creating a record is a write, so the title line's primary is gated too. This line could not
    // fail against the tree that introduced it — there was no such control anywhere in the product
    // — and it is here because an enumeration of "no write affordance" that a new write affordance
    // can walk straight past is worse than no enumeration at all. Its twin in the writer fence
    // below is what makes it non-vacuous from now on.
    expect(screen.queryByRole("button", { name: "New Initiative" })).not.toBeInTheDocument();

    // The View menu is OPENED before anything inside it is asserted. Behind a
    // closed popover the three absence assertions below would pass because the control is
    // UNMOUNTED rather than because access was denied — a vacuous pass — and the two presence
    // assertions would fail for a reason that has nothing to do with access.
    await openViewMenu(user);
    expect(screen.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();

    // The three saved-view writes. Selecting a saved view is a read and stays.
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save as new" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set as default" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Saved view")).toBeInTheDocument();

    // Hiding is never the only signal, and the explanation appears once.
    const banners = screen.getAllByTestId("read-only-banner");
    expect(banners).toHaveLength(1);
    expect(banners[0]).toHaveTextContent(
      "Read-only. You hold read on Initiative. Ask an administrator of Initiative, or a " +
        "system administrator, for write access.",
    );
  });

  it("carries no banner and every affordance for a caller who may write (fence)", async () => {
    // A fence: a writer's view is the same with or without level gating, so it cannot fail against
    // an ungated tree and is not counted as a failing-first assertion. It exists because the
    // failure mode of the item above is over-hiding, which no read-level assertion can catch.
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={{ ...objectType, your_access: "write" }} />);

    await screen.findByTestId("row-INIT-1");
    expect(screen.getByRole("button", { name: "Edit Name for INIT-1" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Import CSV" })).toBeInTheDocument();
    expect(screen.getByLabelText("Select row INIT-1")).toBeInTheDocument();
    // **This one is not a fence** — it fails against a tree with no create control, and it is what
    // stops the reader enumeration above from passing merely because the control does not exist.
    expect(screen.getByRole("button", { name: "New Initiative" })).toBeInTheDocument();
    expect(screen.queryByTestId("read-only-banner")).not.toBeInTheDocument();
    await openViewMenu(user);
    expect(screen.getByRole("button", { name: "Save as new" })).toBeInTheDocument();
  });
});

/**
 * The state this proves is the one a forbidden refusal exists for: a grant revoked between
 * the page load and the click, so the affordance was legitimately shown and then legitimately
 * refused. Gating is an affordance; the server is the boundary; this is what happens when the
 * two disagree.
 */
/** The app shell's object-type nav, reduced to the one thing that matters here: an active
 * observer on `objectTypesQueryKey`. */
function NavProbe() {
  const { data } = useObjectTypes();
  return <span data-testid="nav-probe">{data?.length ?? 0}</span>;
}

describe("TableView: a forbidden 403", () => {
  const FORBIDDEN_MESSAGE =
    "Your access to object type 'initiative' is 'read'; this call needs at least 'write'. " +
    "Ask an administrator of 'initiative', or a system administrator, to raise it.";

  it("renders the backend's message verbatim and refetches the object-type list", async () => {
    const user = userEvent.setup();
    let objectTypeListFetches = 0;
    server.use(
      http.get("/api/v1/object-types", () => {
        objectTypeListFetches += 1;
        return HttpResponse.json([{ ...objectType, your_access: "read" }]);
      }),
      http.patch("/api/v1/records/:key", () =>
        HttpResponse.json(
          {
            error: {
              code: "forbidden",
              message: FORBIDDEN_MESSAGE,
              details: { object_type: "initiative", held: "read", required: "write" },
            },
          },
          { status: 403 },
        ),
      ),
    );

    // Rendered with `admin`, so the control is offered — which is the point: it was shown
    // legitimately, and the grant moved underneath it. `NavProbe` stands in for the app shell's
    // object-type nav, which is mounted on every real screen: an `invalidateQueries` with no
    // live observer marks the entry stale without refetching, so without it this test would
    // assert nothing about whether the refetch actually happens in the app.
    renderWithProviders(
      <>
        <NavProbe />
        <TableView objectType={objectType} />
      </>,
    );
    await waitFor(() => expect(objectTypeListFetches).toBeGreaterThan(0));
    const before = objectTypeListFetches;

    const cell = await findCell("INIT-1", "Name");
    await user.click(cell);
    const input = screen.getByLabelText("Name value for INIT-1");
    await user.clear(input);
    await user.type(input, "Alpha Two");
    await user.keyboard("{Enter}");

    // Verbatim: no paraphrase, no prefix, no truncation.
    expect(await screen.findByText(FORBIDDEN_MESSAGE)).toBeInTheDocument();

    // The refetch is the smallest correct response to state the 403 proves is stale: it
    // re-derives every `your_access`, so the affordances disappear on the next render.
    await waitFor(() => expect(objectTypeListFetches).toBeGreaterThan(before));
  });

  it("does not refetch the object-type list for an unrelated failure", async () => {
    const user = userEvent.setup();
    let objectTypeListFetches = 0;
    server.use(
      http.get("/api/v1/object-types", () => {
        objectTypeListFetches += 1;
        return HttpResponse.json([objectType]);
      }),
      http.patch("/api/v1/records/:key", () =>
        HttpResponse.json(
          { error: { code: "validation_failed", message: "Nope.", details: {} } },
          { status: 422 },
        ),
      ),
    );

    renderWithProviders(
      <>
        <NavProbe />
        <TableView objectType={objectType} />
      </>,
    );
    await waitFor(() => expect(objectTypeListFetches).toBeGreaterThan(0));
    const before = objectTypeListFetches;

    await user.click(await findCell("INIT-1", "Name"));
    const input = screen.getByLabelText("Name value for INIT-1");
    await user.clear(input);
    await user.type(input, "Alpha Two");
    await user.keyboard("{Enter}");

    await screen.findByRole("alert");
    expect(objectTypeListFetches).toBe(before);
  });
});

describe("TableView: user_ref cell rendering", () => {
  const ownerPrincipalField: FieldDoc = {
    key: "owner_principal",
    name: "Owner Principal",
    type: "user_ref",
    description: "The principal accountable for this record.",
    required: false,
    unique: false,
    indexed: false,
    embed: false,
    default: null,
    config: {},
    position: 3,
    operators: ["eq", "neq", "is_null", "is_not_null"],
    display_eligible: false,
  };
  const objectTypeWithOwner: ObjectTypeDetail = {
    ...objectType,
    field_count: objectType.fields.length + 1,
    fields: [...objectType.fields, ownerPrincipalField],
  };

  it("shows the resolved display name, not the raw principal id", async () => {
    recordsStore = [
      record({
        key: "INIT-1",
        data: { name: "Alpha", status: "on_track", owner_principal: "principal-owner" },
      }),
    ];
    principalsStore = {
      "principal-owner": { display_name: "Sarah Okonjo", email: "sarah@example.com", is_active: true, type: "user" },
    };

    renderWithProviders(<TableView objectType={objectTypeWithOwner} />);

    const cell = await screen.findByRole("button", { name: "Edit Owner Principal for INIT-1" });
    expect(cell).toHaveTextContent("Sarah Okonjo");
    expect(cell).not.toHaveTextContent("principal-owner");
  });

  it("stays editable: the picker still commits the selected id through the same PATCH", async () => {
    const user = userEvent.setup();
    recordsStore = [
      record({ key: "INIT-1", data: { name: "Alpha", status: "on_track", owner_principal: null } }),
    ];

    renderWithProviders(<TableView objectType={objectTypeWithOwner} />);

    const cell = await screen.findByRole("button", { name: "Edit Owner Principal for INIT-1" });
    await user.click(cell);
    const select = screen.getByLabelText("Owner Principal value for INIT-1");
    await waitFor(() => expect(within(select).getByText(/Sarah Okonjo/)).toBeInTheDocument());

    await user.selectOptions(select, "principal-owner");
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(patchRequests.at(-1)?.body).toMatchObject({
        values: { owner_principal: "principal-owner" },
      });
    });
  });
});


/**
 * docs/DESIGN.md 8.2's page: the title line with the count and the view name, the description
 * behind About, the one-row toolbar with the View menu on its right, and the footer's counts.
 *
 * **Geometry is not here.** "One row" and "28px" are Playwright assertions in
 * `e2e/ui-visual.spec.ts` (the one-row scenario and the chip/pill scenario beside it), because
 * `getBoundingClientRect` is zeroes under jsdom (AGENTS.md, Traps). What is here is what the
 * page says and which controls exist where.
 */
describe("TableView: the data leads the page", () => {
  it("puts the count and the view name on the title line, and takes the description out of the flow", async () => {
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    // `record_count`, the type's own live count, not the filtered one — `footerCounts.ts` says
    // why, and the footer is where filtering is accounted for.
    expect(screen.getByTestId("table-title-meta")).toHaveTextContent("2 records · Unsaved view");
    expect(screen.getByRole("heading", { name: "Initiative", level: 1 })).toBeInTheDocument();
    expect(screen.queryByText("A funded, sponsored workstream.")).not.toBeInTheDocument();
  });

  it("names the loaded view on the title line", async () => {
    const user = userEvent.setup();
    savedViewsStore = [
      savedView({
        name: "At risk only",
        is_default: true,
        config: {
          filter: null,
          sort: [],
          groupBy: null,
          columns: { order: ["name", "status", "owner"], visibility: {}, sizing: {} },
          mode: "table",
        },
      }),
    ];
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    await waitFor(() =>
      expect(screen.getByTestId("table-title-meta")).toHaveTextContent("2 records · At risk only"),
    );
    // And the toolbar control that opens the menu names it too.
    await openViewMenu(user);
    expect(screen.getByLabelText("Saved view")).toHaveValue(savedViewsStore[0].id);
  });

  it("keeps the description one click away, with aria-expanded and aria-controls", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    const about = screen.getByTestId("about-toggle");
    expect(about).toHaveAttribute("aria-expanded", "false");
    // Closed, `aria-controls` names nothing, because the panel is unmounted rather than hidden
    // — the same rule `Popover` follows.
    expect(about).not.toHaveAttribute("aria-controls");
    expect(screen.queryByTestId("about-panel")).not.toBeInTheDocument();

    await user.click(about);

    expect(about).toHaveAttribute("aria-expanded", "true");
    const panel = screen.getByTestId("about-panel");
    expect(panel).toHaveTextContent("A funded, sponsored workstream.");
    expect(about.getAttribute("aria-controls")).toBe(panel.getAttribute("id"));

    await user.click(about);
    expect(about).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("about-panel")).not.toBeInTheDocument();
  });

  it("holds the six controls 8.2 does not place behind one View menu", async () => {
    const user = userEvent.setup();
    savedViewsStore = [
      savedView({ name: "Everything", config: { mode: "table", filter: null, sort: [], groupBy: null, columns: { order: [], visibility: {}, sizing: {} } } }),
    ];
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    // Closed, none of the six is in the toolbar: that is the collapse whose absence was measured
    // at eleven controls wrapped to two rows.
    expect(screen.queryByLabelText("Saved view")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("New view name")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save as new" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Set as default" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Export CSV" })).not.toBeInTheDocument();

    await openViewMenu(user);

    const menu = within(screen.getByTestId("view-menu"));
    expect(menu.getByLabelText("Saved view")).toBeInTheDocument();
    expect(menu.getByLabelText("New view name")).toBeInTheDocument();
    expect(menu.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(menu.getByRole("button", { name: "Save as new" })).toBeInTheDocument();
    expect(menu.getByRole("button", { name: "Set as default" })).toBeInTheDocument();
    expect(menu.getByRole("button", { name: "Export CSV" })).toBeInTheDocument();
  });

  it("says how many rows the filter is holding back, once one is active", async () => {
    // The type holds two records (`record_count: 2`); this query answers with one. The
    // subtraction is across two requests by construction, which is the whole reason the
    // clause is clamped and conditional.
    server.use(
      http.post("/api/v1/object-types/:key/query", async ({ request }) => {
        queryRequests.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({
          records: [recordsStore[0]],
          total_count: 1,
          next_cursor: null,
          truncated: false,
        });
      }),
    );
    savedViewsStore = [
      savedView({
        name: "On track",
        is_default: true,
        config: {
          filter: { field: "status", op: "eq", value: "on_track" },
          sort: [],
          groupBy: null,
          columns: { order: ["name", "status", "owner"], visibility: {}, sizing: {} },
          mode: "table",
        },
      }),
    ];
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(
        "Showing all 1. · 1 hidden by your filter",
      ),
    );

    // **And the title line still says 2, which is the only place in this file the two counts
    // disagree.** The assertion above ("puts the count and the view name on
    // the title line") runs against a fixture whose `record_count` and `total_count` are both 2,
    // so a product reading `total_count` there passes it — measured, the mutation passed. Which
    // count the title carries is a decision `footerCounts.ts` records and the whole point of the
    // footer being a different number, so it is asserted where the two differ.
    expect(screen.getByTestId("table-title-meta")).toHaveTextContent("2 records · On track");
  });

  it("says nothing about hidden rows when no filter is active", async () => {
    // Same disagreeing counts, no filter: the clause is not "0 hidden", it is absent.
    // Without this the assertion above would pass against a page that always subtracts.
    server.use(
      http.post("/api/v1/object-types/:key/query", async ({ request }) => {
        queryRequests.push((await request.json()) as Record<string, unknown>);
        return HttpResponse.json({
          records: [recordsStore[0]],
          total_count: 1,
          next_cursor: null,
          truncated: false,
        });
      }),
    );
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    expect(screen.getByRole("status")).toHaveTextContent("Showing all 1.");
    expect(screen.queryByText(/hidden by your filter/)).not.toBeInTheDocument();
  });

  it("counts the agent-touched rows on this page, and words it as this page", async () => {
    recordsStore = [
      record({ key: "INIT-1", data: { name: "Alpha", status: "on_track" }, updated_by_agent_label_id: "agent-1" }),
      record({ key: "INIT-2", data: { name: "Beta", status: "at_risk" } }),
    ];
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    // Only the fetched page is knowable client-side, so the sentence claims the page.
    expect(screen.getByTestId("table-agent-count")).toHaveTextContent(
      "1 of these rows was last touched by an agent",
    );
  });

  it("puts the density presets behind a chip that names the current one", async () => {
    // Not a visible pair of buttons, because 8.2's one row could not hold a 197.8px pair beside
    // a named view (the module header carries the measurement). Both test ids and both
    // accessible names are kept — `e2e/density.spec.ts` measures 2.4's real row heights through
    // them.
    const user = userEvent.setup();
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    // **The TRIGGER's text, not the chip's.** `Popover` renders its panel inside the chip's
    // span, so `getByTestId("density-chip")` reads the open panel's own "Comfortable" and
    // "Compact" buttons too — and the first form of this assertion passed against a mutant whose
    // sentence was hardcoded to `OPTIONS[0]`. The mutation is what found it.
    const sentence = () => within(screen.getByTestId("density-chip")).getAllByRole("button")[0];

    expect(sentence()).toHaveTextContent("Comfortable");
    expect(screen.queryByTestId("density-toggle")).not.toBeInTheDocument();
    expect(screen.queryByTestId("density-compact")).not.toBeInTheDocument();

    await openChip(user, "density-chip");
    expect(screen.getByTestId("density-comfortable")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("density-compact")).toHaveAttribute("aria-pressed", "false");

    await user.click(screen.getByTestId("density-compact"));
    expect(sentence()).toHaveTextContent("Compact");
    expect(sentence()).not.toHaveTextContent("Comfortable");
    expect(screen.getByTestId("density-compact")).toHaveAttribute("aria-pressed", "true");

    // Put it back: `chooseDensity` writes the preset to `localStorage`, which jsdom keeps for
    // the rest of this file's tests. The same courtesy `e2e/density.spec.ts` pays the shared
    // browser profile, for the same reason.
    await user.click(screen.getByTestId("density-comfortable"));
    expect(sentence()).toHaveTextContent("Comfortable");
  });

  it("shows no paging controls when there is only one page", async () => {
    // docs/DESIGN.md 8.2: "paging controls right when more than one page." They rendered
    // disabled on a single-page table before. DD-42's "disabled, never hidden" is about an
    // affordance a caller's ACCESS withholds, and a Next button on a table with no next page is
    // not one: `pageRangeLabel` already says there is nothing beyond this. The pager's presence
    // when there IS another page is the assertion in "the pager" above, which is what
    // keeps this one from being satisfiable by never rendering a pager at all.
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    expect(screen.getByRole("status")).toHaveTextContent("Showing all 2.");
    expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Previous" })).not.toBeInTheDocument();
  });

  it("says nothing about agents when no row on this page was written by one", async () => {
    renderWithProviders(<TableView objectType={objectType} />);
    await findCell("INIT-1", "Name");

    expect(screen.queryByTestId("table-agent-count")).not.toBeInTheDocument();
  });
});
