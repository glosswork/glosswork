import { describe, expect, it } from "vitest";

import { pinPlacement, resolvePinned, togglePinned } from "./columnPins";

describe("resolvePinned", () => {
  it("pins the display field for a view with no pinned member", () => {
    expect(resolvePinned(undefined, "name")).toEqual(["name"]);
  });

  it("pins nothing when there is no display field either", () => {
    expect(resolvePinned(undefined, null)).toEqual([]);
  });

  it("takes an explicit list as it is, an empty one included", () => {
    expect(resolvePinned([], "name")).toEqual([]);
    expect(resolvePinned(["stage"], "name")).toEqual(["stage"]);
  });

  it("returns a copy, so the caller cannot edit a stored view by editing the live list", () => {
    const stored = ["stage"];
    expect(resolvePinned(stored, "name")).not.toBe(stored);
  });
});

describe("togglePinned", () => {
  it("adds a key that is not pinned and removes one that is", () => {
    expect(togglePinned(["name"], "stage")).toEqual(["name", "stage"]);
    expect(togglePinned(["name", "stage"], "name")).toEqual(["stage"]);
  });
});

describe("pinPlacement", () => {
  const widths: Record<string, number> = { __by__: 56, __key__: 120, name: 180, stage: 150, due: 90 };
  const widthOf = (id: string) => widths[id] ?? 0;
  const order = ["__by__", "__key__", "name", "stage", "due"];

  it("puts the only pinned column at the left edge, whatever scrolls before it", () => {
    expect(pinPlacement(order, ["name"], widthOf)).toEqual({
      offsets: { name: 0 },
      lastPinned: "name",
    });
  });

  it("offsets each pinned column by the pinned columns before it in the shown order", () => {
    expect(pinPlacement(order, ["due", "name", "stage"], widthOf)).toEqual({
      offsets: { name: 0, stage: 180, due: 330 },
      lastPinned: "due",
    });
  });

  it("follows a reorder: the order shown decides, not the order pinned", () => {
    expect(
      pinPlacement(["__by__", "__key__", "stage", "name", "due"], ["name", "stage"], widthOf),
    ).toEqual({ offsets: { stage: 0, name: 150 }, lastPinned: "name" });
  });

  it("follows a resize", () => {
    expect(pinPlacement(order, ["name", "stage"], (id) => (id === "name" ? 260 : widthOf(id)))).toEqual({
      offsets: { name: 0, stage: 260 },
      lastPinned: "stage",
    });
  });

  it("gives a pinned column that is hidden no offset and no room", () => {
    expect(pinPlacement(["__by__", "__key__", "stage", "due"], ["name", "stage"], widthOf)).toEqual({
      offsets: { stage: 0 },
      lastPinned: "stage",
    });
  });

  it("places nothing when nothing is pinned", () => {
    expect(pinPlacement(order, [], widthOf)).toEqual({ offsets: {}, lastPinned: null });
  });
});
