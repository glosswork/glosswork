/**
 * `completeness.ts`, over **all twenty-four operators the API accepts** (the union of
 * `operators_for(t)` across the thirteen field types).
 *
 * The list below is transcribed, and transcribing it here is not the thing this project forbids:
 * no REST route publishes the vocabulary, so a frontend test cannot iterate it
 * at runtime, and the *closure* half — "every operator the API accepts has been accounted for" —
 * is proved by a Python test against `operators_for` (`tests/test_display_vocabulary.py`),
 * not here. What this file proves is that each of the twenty-four is classified and judged the way
 * `src/glosswork/filters.py` judges it.
 */
import { describe, expect, it } from "vitest";
import {
  LIST_OPS,
  NO_VALUE_OPS,
  incompleteNodes,
  isConditionComplete,
  isFilterComplete,
  valueCardinality,
  type ValueCardinality,
} from "./completeness";
import type { FilterNode } from "./types";

/** `src/glosswork/filters.py:43-45`, `:47`, and everything else `_parse_condition` reaches. */
const NO_VALUE_OPERATORS = [
  "is_null",
  "is_not_null",
  "is_empty",
  "is_not_empty",
  "has_links",
  "has_no_links",
] as const;

const PAIR_OPERATORS = ["between"] as const;

const LIST_OPERATORS = [
  "in",
  "not_in",
  "has_any",
  "has_all",
  "has_none",
  "linked_to_any",
] as const;

const SCALAR_OPERATORS = [
  "eq",
  "neq",
  "contains",
  "not_contains",
  "starts_with",
  "ends_with",
  "gt",
  "gte",
  "lt",
  "lte",
  "linked_to",
] as const;

const ALL_OPERATORS: readonly string[] = [
  ...NO_VALUE_OPERATORS,
  ...PAIR_OPERATORS,
  ...LIST_OPERATORS,
  ...SCALAR_OPERATORS,
];

describe("the twenty-four operators are partitioned, with nothing counted twice", () => {
  it("covers the twenty-four names exactly once each", () => {
    expect(ALL_OPERATORS).toHaveLength(24);
    expect(new Set(ALL_OPERATORS).size).toBe(24);
  });

  it("exports the same two sets `filters.py` declares", () => {
    expect([...NO_VALUE_OPS].sort()).toEqual([...NO_VALUE_OPERATORS].sort());
    expect([...LIST_OPS].sort()).toEqual([...LIST_OPERATORS].sort());
  });

  it.each<[string, ValueCardinality]>([
    ...NO_VALUE_OPERATORS.map((op): [string, ValueCardinality] => [op, "none"]),
    ...PAIR_OPERATORS.map((op): [string, ValueCardinality] => [op, "pair"]),
    ...LIST_OPERATORS.map((op): [string, ValueCardinality] => [op, "list"]),
    ...SCALAR_OPERATORS.map((op): [string, ValueCardinality] => [op, "one"]),
  ])("classifies %s as taking %s", (op, cardinality) => {
    expect(valueCardinality(op)).toBe(cardinality);
  });
});

describe("the six NO_VALUE_OPS are complete with no value and incomplete carrying one", () => {
  it.each(NO_VALUE_OPERATORS)("%s is complete with the value key absent", (op) => {
    expect(isConditionComplete({ op })).toBe(true);
  });

  it.each(NO_VALUE_OPERATORS)("%s is complete with an explicit null (filters.py:183)", (op) => {
    // `raw.get("value") is not None` is the server's test, so a null reads as "no value".
    expect(isConditionComplete({ op, value: null })).toBe(true);
  });

  it.each(NO_VALUE_OPERATORS)('%s is incomplete carrying a value ("takes no value")', (op) => {
    expect(isConditionComplete({ op, value: "anything" })).toBe(false);
  });
});

describe("between takes a two-element array with neither element null", () => {
  it("is complete with both bounds", () => {
    expect(isConditionComplete({ op: "between", value: [1000, 2000] })).toBe(true);
  });

  it("is complete with falsy-but-present bounds", () => {
    // 0 and "" are values. `_resolve_scalar` is an isinstance check, not a truthiness test.
    expect(isConditionComplete({ op: "between", value: [0, 0] })).toBe(true);
  });

  it.each<[string, unknown]>([
    ["no value at all", undefined],
    ["an explicit null", null],
    ["a scalar", 1000],
    ["an empty array", []],
    ["a one-element array", [1000]],
    ["a three-element array", [1, 2, 3]],
    ["a null upper bound", [1000, null]],
    ["an undefined upper bound", [1000, undefined]],
    ["a null lower bound", [null, 2000]],
  ])("is incomplete with %s", (_label, value) => {
    expect(isConditionComplete({ op: "between", value })).toBe(false);
  });
});

describe("the six LIST_OPS take a non-empty array", () => {
  it.each(LIST_OPERATORS)("%s is complete with a one-element array", (op) => {
    expect(isConditionComplete({ op, value: ["on_track"] })).toBe(true);
  });

  it.each(LIST_OPERATORS)("%s is incomplete with an empty array", (op) => {
    expect(isConditionComplete({ op, value: [] })).toBe(false);
  });

  it.each(LIST_OPERATORS)("%s is incomplete with no value", (op) => {
    expect(isConditionComplete({ op })).toBe(false);
  });

  it.each(LIST_OPERATORS)("%s is incomplete carrying a scalar rather than an array", (op) => {
    // The defect this guards, for `linked_to_any` specifically: a relation widget that hands the
    // builder a string where the API requires an array.
    expect(isConditionComplete({ op, value: "PER-1" })).toBe(false);
  });

  it.each(LIST_OPERATORS)("%s is incomplete with a null member", (op) => {
    expect(isConditionComplete({ op, value: ["a", null] })).toBe(false);
  });
});

describe("every other operator takes one non-null scalar", () => {
  it.each(SCALAR_OPERATORS)("%s is complete with a string", (op) => {
    expect(isConditionComplete({ op, value: "Alpha" })).toBe(true);
  });

  it.each(SCALAR_OPERATORS)("%s is complete with a falsy-but-present value", (op) => {
    // `filters.py:187` tests `is None`, not truthiness: `0`, `false` and `""` are all values, and
    // an empty-string `contains` is a filter a person can legitimately mean.
    expect(isConditionComplete({ op, value: 0 })).toBe(true);
    expect(isConditionComplete({ op, value: false })).toBe(true);
    expect(isConditionComplete({ op, value: "" })).toBe(true);
  });

  it.each(SCALAR_OPERATORS)("%s is incomplete with the value key absent", (op) => {
    expect(isConditionComplete({ op })).toBe(false);
  });

  it.each(SCALAR_OPERATORS)("%s is incomplete with an explicit null", (op) => {
    expect(isConditionComplete({ op, value: null })).toBe(false);
  });
});

describe("an and/or group with no children is unsendable (filters.py:158-160)", () => {
  it.each<[string, FilterNode]>([
    ["and", { and: [] }],
    ["or", { or: [] }],
  ])("refuses an empty %s group at the root, and reports the group itself", (_kind, tree) => {
    expect(isFilterComplete(tree)).toBe(false);
    expect(incompleteNodes(tree)).toEqual([tree]);
  });

  it("distinguishes the empty filter from an empty group", () => {
    // `null` is how the grammar spells "no filter" and is sendable; `{and: []}` is a malformed
    // node and is not. One click on `+ Group (AND)` used to send the second.
    expect(isFilterComplete(null)).toBe(true);
    expect(isFilterComplete({ and: [] })).toBe(false);
  });

  it("accepts a group the moment it holds one complete child", () => {
    expect(isFilterComplete({ and: [{ field: "status", op: "eq", value: "on_track" }] })).toBe(
      true,
    );
  });

  it("still refuses a group whose only child is incomplete", () => {
    expect(isFilterComplete({ and: [{ field: "status", op: "eq" }] })).toBe(false);
  });

  it("finds an empty group nested inside a populated one (the recursive case)", () => {
    const tree: FilterNode = {
      and: [{ field: "status", op: "eq", value: "on_track" }, { or: [] }],
    };

    expect(incompleteNodes(tree)).toEqual([{ or: [] }]);
    expect(isFilterComplete(tree)).toBe(false);
  });

  it("finds an empty group two levels down, behind a not wrapper", () => {
    const tree: FilterNode = { and: [{ not: { or: [] } }] };

    expect(isFilterComplete(tree)).toBe(false);
    expect(incompleteNodes(tree)).toEqual([{ or: [] }]);
  });

  it("reports an empty group and an incomplete condition together", () => {
    const tree: FilterNode = { and: [{ field: "name", op: "eq" }, { and: [] }] };

    expect(incompleteNodes(tree)).toEqual([{ field: "name", op: "eq" }, { and: [] }]);
  });
});

describe("the tree walk finds an incomplete condition at any depth", () => {
  it("calls the empty filter complete", () => {
    expect(isFilterComplete(null)).toBe(true);
    expect(incompleteNodes(null)).toEqual([]);
  });

  it("judges a bare leaf condition", () => {
    expect(isFilterComplete({ field: "status", op: "eq", value: "on_track" })).toBe(true);
    expect(isFilterComplete({ field: "status", op: "eq" })).toBe(false);
  });

  it("descends through a not wrapper", () => {
    expect(isFilterComplete({ not: { field: "status", op: "eq" } })).toBe(false);
    expect(isFilterComplete({ not: { field: "status", op: "eq", value: "lost" } })).toBe(true);
  });

  it("finds the one incomplete condition inside a nested and/or/not tree, and names it", () => {
    const tree: FilterNode = {
      and: [
        { field: "status", op: "eq", value: "on_track" },
        {
          or: [
            { field: "name", op: "contains", value: "Alpha" },
            { field: "amount", op: "between", value: [1000, null] },
          ],
        },
        { not: { field: "owner", op: "linked_to_any", value: ["PER-1"] } },
      ],
    };

    expect(incompleteNodes(tree)).toEqual([
      { field: "amount", op: "between", value: [1000, null] },
    ]);
    expect(isFilterComplete(tree)).toBe(false);
  });

  it("calls the same tree complete once the missing bound arrives", () => {
    const tree: FilterNode = {
      and: [
        { field: "status", op: "eq", value: "on_track" },
        {
          or: [
            { field: "name", op: "contains", value: "Alpha" },
            { field: "amount", op: "between", value: [1000, 2000] },
          ],
        },
        { not: { field: "owner", op: "linked_to_any", value: ["PER-1"] } },
      ],
    };

    expect(incompleteNodes(tree)).toEqual([]);
    expect(isFilterComplete(tree)).toBe(true);
  });

  it("reports every incomplete condition, not only the first", () => {
    const tree: FilterNode = {
      or: [
        { field: "status", op: "in", value: [] },
        { field: "name", op: "eq" },
      ],
    };

    expect(incompleteNodes(tree)).toEqual([
      { field: "status", op: "in", value: [] },
      { field: "name", op: "eq" },
    ]);
  });
});
