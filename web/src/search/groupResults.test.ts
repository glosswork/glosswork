import { describe, expect, it } from "vitest";
import type { SearchHit } from "../api/search";
import { groupResultsByType } from "./groupResults";

function hit(overrides: Partial<SearchHit> & Pick<SearchHit, "record_key" | "object_type">): SearchHit {
  return {
    record_id: `id-${overrides.record_key}`,
    title: overrides.record_key,
    score: 1,
    hit_source: { type: "field", field_key: "summary" },
    snippet: "...",
    other_matches: 0,
    ...overrides,
  };
}

describe("groupResultsByType", () => {
  it("returns one group per distinct object_type, in order of first appearance", () => {
    const results = [
      hit({ record_key: "DEC-1", object_type: "decision" }),
      hit({ record_key: "INIT-1", object_type: "initiative" }),
      hit({ record_key: "DEC-2", object_type: "decision" }),
    ];

    const groups = groupResultsByType(results);

    expect(groups.map((group) => group.objectType)).toEqual(["decision", "initiative"]);
    expect(groups[0].hits.map((h) => h.record_key)).toEqual(["DEC-1", "DEC-2"]);
    expect(groups[1].hits.map((h) => h.record_key)).toEqual(["INIT-1"]);
  });

  it("preserves each hit's relative order within its group", () => {
    const results = [
      hit({ record_key: "A", object_type: "task", score: 0.9 }),
      hit({ record_key: "B", object_type: "task", score: 0.5 }),
    ];

    const groups = groupResultsByType(results);

    expect(groups).toHaveLength(1);
    expect(groups[0].hits.map((h) => h.record_key)).toEqual(["A", "B"]);
  });

  it("returns an empty array for no results", () => {
    expect(groupResultsByType([])).toEqual([]);
  });
});
