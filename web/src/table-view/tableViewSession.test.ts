import { afterEach, describe, expect, it, vi } from "vitest";

import {
  clearTableViewSessions,
  readTableViewSession,
  resolveSelectedViewId,
  sweepTableViewSessions,
  TABLE_VIEW_SESSION_PREFIX,
  tableViewSessionKey,
  writeTableViewSession,
} from "./tableViewSession";
import type { TableViewConfig } from "./types";

const ALICE = "principal-alice";
const BOB = "principal-bob";

const config: TableViewConfig = {
  filter: { field: "owner", op: "eq", value: "alice@example.com" },
  sort: [{ field: "name", dir: "asc" }],
  groupBy: null,
  columns: { order: ["name", "owner"], visibility: { owner: false }, sizing: { name: 220 }, pinned: ["name"] },
  mode: "table",
  density: "compact",
};

function keys(): string[] {
  return Object.keys(window.sessionStorage).sort();
}

afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("write and read", () => {
  it("round-trips the selected view and the config, per object type", () => {
    writeTableViewSession("deal", ALICE, "view-1", config);
    writeTableViewSession("task", ALICE, null, { ...config, filter: null });

    const deal = readTableViewSession("deal", ALICE);
    expect(deal?.selectedViewId).toBe("view-1");
    expect(deal?.config.filter).toEqual(config.filter);
    expect(deal?.config.columns).toEqual(config.columns);
    expect(readTableViewSession("task", ALICE)?.config.filter).toBeNull();
    expect(keys()).toEqual([`${TABLE_VIEW_SESSION_PREFIX}deal`, `${TABLE_VIEW_SESSION_PREFIX}task`]);
  });

  it("leaves density out: it has its own persistence", () => {
    writeTableViewSession("deal", ALICE, null, config);
    expect("density" in (readTableViewSession("deal", ALICE)?.config ?? {})).toBe(false);
  });

  it("answers null when nothing is stored", () => {
    expect(readTableViewSession("deal", ALICE)).toBeNull();
  });

  it("ignores and removes an entry written for another principal", () => {
    writeTableViewSession("deal", ALICE, "view-1", config);
    expect(readTableViewSession("deal", BOB)).toBeNull();
    expect(keys()).toEqual([]);
    // And it is gone for the person who wrote it too: nothing of theirs is left in the tab.
    expect(readTableViewSession("deal", ALICE)).toBeNull();
  });

  it("ignores and removes an entry when nobody is signed in", () => {
    writeTableViewSession("deal", ALICE, null, config);
    expect(readTableViewSession("deal", null)).toBeNull();
    expect(keys()).toEqual([]);
  });

  it.each([
    ["not JSON", "{"],
    ["not an object", '"text"'],
    ["null", "null"],
    ["another version", JSON.stringify({ version: 99, principalId: ALICE, selectedViewId: null, config: {} })],
    ["no config", JSON.stringify({ version: 1, principalId: ALICE, selectedViewId: null })],
  ])("ignores and removes an entry that is %s", (_name, raw) => {
    window.sessionStorage.setItem(tableViewSessionKey("deal"), raw);
    expect(readTableViewSession("deal", ALICE)).toBeNull();
    expect(keys()).toEqual([]);
  });

  it("reads a view id that is not a string as no view", () => {
    window.sessionStorage.setItem(
      tableViewSessionKey("deal"),
      JSON.stringify({ version: 1, principalId: ALICE, selectedViewId: 7, config: {} }),
    );
    expect(readTableViewSession("deal", ALICE)).toEqual({ selectedViewId: null, config: {} });
  });
});

describe("sign-out and sign-in", () => {
  it("sign-out removes every table's entry and nothing else", () => {
    writeTableViewSession("deal", ALICE, null, config);
    writeTableViewSession("task", ALICE, null, config);
    window.sessionStorage.setItem("something-else", "kept");
    clearTableViewSessions();
    expect(keys()).toEqual(["something-else"]);
  });

  it("sign-in sweeps out every entry that is not the new principal's, opened or not", () => {
    writeTableViewSession("deal", ALICE, null, config);
    writeTableViewSession("task", ALICE, null, config);
    writeTableViewSession("note", BOB, null, config);
    window.sessionStorage.setItem(tableViewSessionKey("broken"), "{");
    window.sessionStorage.setItem("something-else", "kept");

    sweepTableViewSessions(BOB);

    expect(keys()).toEqual([`${TABLE_VIEW_SESSION_PREFIX}note`, "something-else"]);
    expect(readTableViewSession("note", BOB)).not.toBeNull();
  });

  it("sign-in as the same principal keeps their entries", () => {
    writeTableViewSession("deal", ALICE, "view-1", config);
    sweepTableViewSessions(ALICE);
    expect(readTableViewSession("deal", ALICE)?.selectedViewId).toBe("view-1");
  });
});

describe("storage that throws", () => {
  it("is tolerated by every function: a private window is not a blank page", () => {
    const boom = () => {
      throw new DOMException("denied", "SecurityError");
    };
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(boom);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(boom);
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(boom);
    vi.spyOn(Storage.prototype, "key").mockImplementation(boom);

    expect(() => writeTableViewSession("deal", ALICE, null, config)).not.toThrow();
    expect(readTableViewSession("deal", ALICE)).toBeNull();
    expect(() => clearTableViewSessions()).not.toThrow();
    expect(() => sweepTableViewSessions(ALICE)).not.toThrow();
  });

  it("is tolerated when the storage object itself cannot be reached", () => {
    vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(() => writeTableViewSession("deal", ALICE, null, config)).not.toThrow();
    expect(readTableViewSession("deal", ALICE)).toBeNull();
    expect(() => clearTableViewSessions()).not.toThrow();
    expect(() => sweepTableViewSessions(ALICE)).not.toThrow();
  });
});

describe("resolveSelectedViewId", () => {
  const views = [{ id: "view-1" }, { id: "view-2" }];

  it("keeps an id that is in the list", () => {
    expect(resolveSelectedViewId("view-2", views)).toBe("view-2");
  });

  it("turns an id that is not in the list into no view", () => {
    expect(resolveSelectedViewId("deleted", views)).toBeNull();
    expect(resolveSelectedViewId("view-1", [])).toBeNull();
  });

  it("keeps the id while the list has not arrived", () => {
    expect(resolveSelectedViewId("view-1", undefined)).toBe("view-1");
  });

  it("keeps no view as no view", () => {
    expect(resolveSelectedViewId(null, views)).toBeNull();
  });
});
