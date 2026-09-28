import { describe, expect, it } from "vitest";
import { pageRangeLabel } from "./pageRange";

describe("pageRangeLabel", () => {
  it("names the visible slice of a multi-page set", () => {
    expect(pageRangeLabel(1, 200, 200, 512)).toBe("Showing 201-400 of 512.");
    expect(pageRangeLabel(2, 200, 112, 512)).toBe("Showing 401-512 of 512.");
  });

  it("says so plainly when one page holds everything", () => {
    expect(pageRangeLabel(0, 200, 12, 12)).toBe("Showing all 12.");
  });

  it("still counts a first page that is full but not the whole set", () => {
    expect(pageRangeLabel(0, 200, 200, 512)).toBe("Showing 1-200 of 512.");
  });

  it("handles an empty result without inventing a range", () => {
    expect(pageRangeLabel(0, 200, 0, 0)).toBe("Showing 0 of 0.");
  });
});
