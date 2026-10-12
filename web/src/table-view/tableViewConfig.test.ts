import { describe, expect, it } from "vitest";
import type { FieldDoc } from "../api/objectTypes";
import {
  defaultTableViewConfig,
  parseTableViewConfig,
  serializeTableViewConfig,
} from "./tableViewConfig";

const fields: FieldDoc[] = [
  {
    key: "status",
    name: "Status",
    type: "single_select",
    description: "d",
    required: false,
    unique: false,
    indexed: false,
    embed: false,
    default: null,
    config: {},
    position: 0,
    operators: ["eq"],
    display_eligible: true,
  },
  {
    key: "owner",
    name: "Owner",
    type: "relation",
    description: "d",
    required: false,
    unique: false,
    indexed: false,
    embed: false,
    default: null,
    config: {},
    position: 1,
    operators: [],
    display_eligible: false,
  },
];

describe("defaultTableViewConfig", () => {
  it("orders columns by field declaration order with nothing hidden or resized", () => {
    expect(defaultTableViewConfig(fields)).toEqual({
      filter: null,
      sort: [],
      groupBy: null,
      columns: { order: ["status", "owner"], visibility: {}, sizing: {} },
      mode: "table",
    });
  });
});

describe("serializeTableViewConfig / parseTableViewConfig round-trip", () => {
  it("round-trips filter, sort, groupBy, and column state through the opaque config blob", () => {
    const live = {
      filter: { field: "status", op: "eq", value: "at_risk" },
      sort: [{ field: "status", dir: "asc" as const }],
      groupBy: "owner",
      columnOrder: ["owner", "status"],
      columnVisibility: { status: false },
      columnSizing: { owner: 220 },
      columnPinned: ["owner"],
      mode: "table" as const,
      density: "compact" as const,
    };
    const config = serializeTableViewConfig(live);
    const raw = JSON.parse(JSON.stringify(config)) as Record<string, unknown>;
    expect(parseTableViewConfig(raw, fields)).toEqual(config);
    expect(config.columns.pinned).toEqual(["owner"]);
  });

  it("keeps `pinned` absent, empty or listed, and drops a key naming a removed field", () => {
    const withColumns = (columns: Record<string, unknown>) =>
      parseTableViewConfig({ columns }, fields).columns.pinned;
    // Absent: a view saved before pinning existed. It stays absent, which is what lets
    // `resolvePinned` pin the display field for it.
    expect(withColumns({})).toBeUndefined();
    expect("pinned" in parseTableViewConfig({}, fields).columns).toBe(false);
    // Explicitly empty: nothing is pinned, and that is not the same thing.
    expect(withColumns({ pinned: [] })).toEqual([]);
    expect(withColumns({ pinned: ["status", "owner"] })).toEqual(["status", "owner"]);
    // Stale: a field removed since the view was saved has no column to pin.
    expect(withColumns({ pinned: ["gone", "owner"] })).toEqual(["owner"]);
    // Malformed: treated as absent, like every other member of the blob.
    expect(withColumns({ pinned: "owner" })).toBeUndefined();
    expect(withColumns({ pinned: [1, 2] })).toBeUndefined();
  });

  it("falls back to defaults for a missing or malformed config, field by field", () => {
    const parsed = parseTableViewConfig({ groupBy: "owner" }, fields);
    expect(parsed.filter).toBeNull();
    expect(parsed.sort).toEqual([]);
    expect(parsed.groupBy).toBe("owner");
    expect(parsed.columns).toEqual({ order: ["status", "owner"], visibility: {}, sizing: {} });
    expect(parsed.mode).toBe("table");
    // Left UNDEFINED rather than defaulted, so `resolveDensity` can tell "this view has no
    // opinion" (fall through to the user's own preference) from "this view says comfortable".
    expect(parsed.density).toBeUndefined();
  });

  it("round-trips a density and ignores a malformed one", () => {
    const fromView = parseTableViewConfig({ density: "compact" }, fields);
    expect(fromView.density).toBe("compact");
    expect(parseTableViewConfig({ density: "cosy" }, fields).density).toBeUndefined();
  });

  it("falls back column order to field defaults when the stored order isn't a string array", () => {
    const parsed = parseTableViewConfig({ columns: { order: [1, 2] } }, fields);
    expect(parsed.columns.order).toEqual(["status", "owner"]);
  });
});
