import { describe, expect, it } from "vitest";
import { cycleMultiSort, cycleSingleSort } from "./sortSpec";

describe("cycleSingleSort", () => {
  it("cycles unsorted -> asc -> desc -> unsorted for one field, replacing any prior sort", () => {
    let sort = cycleSingleSort([{ field: "other", dir: "asc" }], "status");
    expect(sort).toEqual([{ field: "status", dir: "asc" }]);
    sort = cycleSingleSort(sort, "status");
    expect(sort).toEqual([{ field: "status", dir: "desc" }]);
    sort = cycleSingleSort(sort, "status");
    expect(sort).toEqual([]);
  });
});

describe("cycleMultiSort", () => {
  it("adds a second sort key without disturbing the first, in order", () => {
    const first = cycleMultiSort([], "status");
    expect(first).toEqual([{ field: "status", dir: "asc" }]);
    const second = cycleMultiSort(first, "target_date");
    expect(second).toEqual([
      { field: "status", dir: "asc" },
      { field: "target_date", dir: "asc" },
    ]);
  });

  it("cycles an existing key's direction in place, preserving position", () => {
    const sort = [
      { field: "status", dir: "asc" as const },
      { field: "target_date", dir: "asc" as const },
    ];
    const next = cycleMultiSort(sort, "status");
    expect(next).toEqual([
      { field: "status", dir: "desc" },
      { field: "target_date", dir: "asc" },
    ]);
  });

  it("removes a key once it cycles past desc", () => {
    const sort = [{ field: "status", dir: "desc" as const }];
    expect(cycleMultiSort(sort, "status")).toEqual([]);
  });
});
