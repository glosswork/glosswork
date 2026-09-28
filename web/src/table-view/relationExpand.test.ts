import { describe, expect, it } from "vitest";
import type { RecordDoc } from "../api/records";
import { relationCellDisplay, relationGroupKey, relationGroupLabel } from "./relationExpand";

function record(expand?: RecordDoc["expand"]): RecordDoc {
  return {
    id: "id-1",
    key: "INIT-1",
    version: 1,
    created_at: "",
    created_by: "",
    updated_at: "",
    updated_by: "",
    updated_by_agent_label_id: null,
    deleted_at: null,
    comment_count: 0,
    last_comment_at: null,
    data: {},
    expand,
  };
}

describe("relationGroupKey", () => {
  it("is the first linked record's key", () => {
    expect(relationGroupKey(record({ owner: [{ key: "PERSON-1", id: "p1", display: "Ada" }] }), "owner")).toBe(
      "PERSON-1",
    );
  });

  it("is the unlinked sentinel when there are no links", () => {
    expect(relationGroupKey(record({ owner: [] }), "owner")).toBe("(unlinked)");
  });
});

describe("relationGroupLabel", () => {
  it("includes the display field's value when present", () => {
    expect(
      relationGroupLabel(record({ owner: [{ key: "PERSON-1", id: "p1", display: "Ada Lovelace" }] }), "owner"),
    ).toBe("PERSON-1 — Ada Lovelace");
  });

  it("falls back to just the key when there's no display value", () => {
    expect(relationGroupLabel(record({ owner: [{ key: "PERSON-1", id: "p1", display: null }] }), "owner")).toBe(
      "PERSON-1",
    );
  });
});

describe("relationCellDisplay", () => {
  it("shows a placeholder when the field wasn't expanded at all", () => {
    expect(relationCellDisplay(record(undefined), "owner")).toBe("(open record to view)");
  });

  it("falls back to the linked keys when no link carries a display value", () => {
    expect(
      relationCellDisplay(
        record({ tasks: [{ key: "TASK-1", id: "t1", display: null }, { key: "TASK-2", id: "t2", display: null }] }),
        "tasks",
      ),
    ).toBe("TASK-1, TASK-2");
  });

  it("shows the empty marker for an expanded-but-unlinked field", () => {
    expect(relationCellDisplay(record({ owner: [] }), "owner")).toBe("—");
  });

  /**
   * The cell renders the display value **alone** — a
   * deliberate divergence from `LinkedRecordsPanel`, which keeps the key beside the
   * label. A relation cell joins n links with commas inside a table column, and
   * repeating a key after each one produces a string no column measure can carry.
   * `relationGroupLabel` is left alone on purpose: the group header directly above
   * these rows still renders `KEY — display`, which is where the key stays visible.
   */
  it("renders each link's display value when the expand summary carries one", () => {
    expect(
      relationCellDisplay(
        record({
          tasks: [
            { key: "TASK-1", id: "t1", display: "Migrate the billing ledger" },
            { key: "TASK-2", id: "t2", display: "Consolidate vendor contracts" },
          ],
        }),
        "tasks",
      ),
    ).toBe("Migrate the billing ledger, Consolidate vendor contracts");
  });

  it("falls back per link, so a mixed list is never half-blank", () => {
    expect(
      relationCellDisplay(
        record({
          tasks: [
            { key: "TASK-1", id: "t1", display: "Migrate the billing ledger" },
            { key: "TASK-2", id: "t2", display: null },
            { key: "TASK-3", id: "t3", display: "   " },
          ],
        }),
        "tasks",
      ),
    ).toBe("Migrate the billing ledger, TASK-2, TASK-3");
  });

  it("renders a non-string display value, since a display field may be an integer or a date", () => {
    expect(
      relationCellDisplay(record({ tasks: [{ key: "TASK-1", id: "t1", display: 2026 }] }), "tasks"),
    ).toBe("2026");
  });

  it("falls back to the key for a list or object value, which is never a label", () => {
    expect(
      relationCellDisplay(
        record({ tasks: [{ key: "TASK-1", id: "t1", display: ["a", "b"] }] }),
        "tasks",
      ),
    ).toBe("TASK-1");
  });
});
