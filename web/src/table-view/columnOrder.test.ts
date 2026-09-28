import { describe, expect, it } from "vitest";
import { moveColumn } from "./columnOrder";

describe("moveColumn", () => {
  it("swaps a column with its predecessor when moved up", () => {
    expect(moveColumn(["a", "b", "c"], "b", "up")).toEqual(["b", "a", "c"]);
  });

  it("swaps a column with its successor when moved down", () => {
    expect(moveColumn(["a", "b", "c"], "b", "down")).toEqual(["a", "c", "b"]);
  });

  it("is a no-op at the boundary", () => {
    expect(moveColumn(["a", "b", "c"], "a", "up")).toEqual(["a", "b", "c"]);
    expect(moveColumn(["a", "b", "c"], "c", "down")).toEqual(["a", "b", "c"]);
  });

  it("is a no-op for an unknown key", () => {
    expect(moveColumn(["a", "b"], "z", "up")).toEqual(["a", "b"]);
  });
});
