import { describe, expect, it } from "vitest";
import {
  addChild,
  createCondition,
  createGroup,
  getNodeAtPath,
  removeNodeAtPath,
  replaceCondition,
  setGroupKind,
  setNodeAtPath,
  toggleNot,
} from "./filterTree";
import type { FilterNode } from "./types";

describe("createCondition / createGroup", () => {
  it("omits value entirely when not supplied (unary operators)", () => {
    expect(createCondition("owner", "is_null")).toEqual({ field: "owner", op: "is_null" });
  });

  it("includes value when supplied, including falsy values", () => {
    expect(createCondition("active", "eq", false)).toEqual({
      field: "active",
      op: "eq",
      value: false,
    });
  });

  it("builds an empty and/or group", () => {
    expect(createGroup("and")).toEqual({ and: [] });
    expect(createGroup("or")).toEqual({ or: [] });
  });
});

describe("getNodeAtPath", () => {
  const tree: FilterNode = {
    and: [
      { field: "status", op: "eq", value: "active" },
      { or: [
        { field: "priority", op: "gt", value: 5 },
        { not: { field: "title", op: "contains", value: "foo" } },
      ] },
    ],
  };

  it("resolves the root at an empty path", () => {
    expect(getNodeAtPath(tree, [])).toBe(tree);
  });

  it("descends through and/or groups by index", () => {
    expect(getNodeAtPath(tree, [0])).toEqual({ field: "status", op: "eq", value: "active" });
    expect(getNodeAtPath(tree, [1, 0])).toEqual({ field: "priority", op: "gt", value: 5 });
  });

  it("addresses a not-wrapped node as the not node itself, not its unwrapped inner node", () => {
    // [1, 1] is "the second child of the group at [1]" regardless of whether that slot holds a
    // bare condition or a not-wrapped one; toggleNot relies on seeing the wrapper itself here.
    expect(getNodeAtPath(tree, [1, 1])).toEqual({
      not: { field: "title", op: "contains", value: "foo" },
    });
  });

  it("skips a not wrapper transparently, without consuming a path step, when a deeper index descent follows", () => {
    const wrapped: FilterNode = { not: { and: [createCondition("a", "eq", 1), createCondition("b", "eq", 2)] } };
    expect(getNodeAtPath(wrapped, [1])).toEqual(createCondition("b", "eq", 2));
  });

  it("returns undefined for an out-of-range or invalid path", () => {
    expect(getNodeAtPath(tree, [5])).toBeUndefined();
    expect(getNodeAtPath(tree, [0, 0])).toBeUndefined();
  });
});

describe("setNodeAtPath", () => {
  it("replaces the root when the path is empty", () => {
    const root: FilterNode = { field: "a", op: "eq", value: 1 };
    const next = setNodeAtPath(root, [], { field: "b", op: "eq", value: 2 });
    expect(next).toEqual({ field: "b", op: "eq", value: 2 });
  });

  it("replaces a nested child without mutating the original tree", () => {
    const root: FilterNode = { and: [{ field: "a", op: "eq", value: 1 }] };
    const next = setNodeAtPath(root, [0], { field: "a", op: "eq", value: 2 });

    expect(next).toEqual({ and: [{ field: "a", op: "eq", value: 2 }] });
    expect(root).toEqual({ and: [{ field: "a", op: "eq", value: 1 }] });
  });

  it("throws when trying to descend into a leaf condition", () => {
    const root: FilterNode = { field: "a", op: "eq", value: 1 };
    expect(() => setNodeAtPath(root, [0], { field: "b", op: "eq" })).toThrow();
  });

  it("skips a not wrapper transparently on the way to a deeper index descent", () => {
    const root: FilterNode = { not: { and: [createCondition("a", "eq", 1), createCondition("b", "eq", 2)] } };
    const next = setNodeAtPath(root, [1], createCondition("b", "eq", 99));

    expect(next).toEqual({
      not: { and: [createCondition("a", "eq", 1), createCondition("b", "eq", 99)] },
    });
  });
});

describe("addChild", () => {
  it("starts a tree from an empty (null) root", () => {
    const condition = createCondition("status", "eq", "active");
    expect(addChild(null, [], condition)).toEqual(condition);
  });

  it("rejects a non-empty path against a null root", () => {
    expect(() => addChild(null, [0], createCondition("a", "eq"))).toThrow();
  });

  it("appends to an and group's children in order", () => {
    const root = createGroup("and");
    const withOne = addChild(root, [], createCondition("a", "eq", 1));
    const withTwo = addChild(withOne, [], createCondition("b", "eq", 2));

    expect(withTwo).toEqual({
      and: [
        { field: "a", op: "eq", value: 1 },
        { field: "b", op: "eq", value: 2 },
      ],
    });
  });

  it("appends into a nested group addressed by path", () => {
    const root: FilterNode = { and: [createGroup("or")] };
    const next = addChild(root, [0], createCondition("x", "eq", 1));

    expect(next).toEqual({ and: [{ or: [{ field: "x", op: "eq", value: 1 }] }] });
  });

  it("rejects adding to a node that is not a group", () => {
    const root: FilterNode = { and: [createCondition("a", "eq", 1)] };
    expect(() => addChild(root, [0], createCondition("b", "eq", 2))).toThrow();
  });
});

describe("removeNodeAtPath", () => {
  it("collapses the whole tree to null when the root is removed", () => {
    const root: FilterNode = createCondition("a", "eq", 1);
    expect(removeNodeAtPath(root, [])).toBeNull();
  });

  it("removes a child from its parent group, shifting later siblings", () => {
    const root: FilterNode = {
      and: [createCondition("a", "eq", 1), createCondition("b", "eq", 2), createCondition("c", "eq", 3)],
    };
    const next = removeNodeAtPath(root, [1]);

    expect(next).toEqual({
      and: [createCondition("a", "eq", 1), createCondition("c", "eq", 3)],
    });
  });

  it("removes a nested node addressed through a not wrapper", () => {
    const root: FilterNode = { and: [{ not: createCondition("a", "eq", 1) }, createCondition("b", "eq", 2)] };
    const next = removeNodeAtPath(root, [0]);

    expect(next).toEqual({ and: [createCondition("b", "eq", 2)] });
  });
});

describe("setGroupKind", () => {
  it("switches and to or, preserving children", () => {
    const root: FilterNode = { and: [createCondition("a", "eq", 1)] };
    expect(setGroupKind(root, [], "or")).toEqual({ or: [createCondition("a", "eq", 1)] });
  });

  it("is a no-op shape change when already the target kind", () => {
    const root: FilterNode = { or: [createCondition("a", "eq", 1)] };
    expect(setGroupKind(root, [], "or")).toEqual(root);
  });

  it("throws on a non-group node", () => {
    expect(() => setGroupKind(createCondition("a", "eq"), [], "or")).toThrow();
  });
});

describe("replaceCondition", () => {
  it("replaces a leaf condition's field/op/value in place", () => {
    const root: FilterNode = { and: [createCondition("a", "eq", 1)] };
    const next = replaceCondition(root, [0], createCondition("b", "gt", 5));

    expect(next).toEqual({ and: [createCondition("b", "gt", 5)] });
  });

  it("throws when the target node is not a leaf condition", () => {
    const root: FilterNode = { and: [createGroup("or")] };
    expect(() => replaceCondition(root, [0], createCondition("a", "eq"))).toThrow();
  });
});

describe("toggleNot", () => {
  it("wraps a bare node in not", () => {
    const root: FilterNode = { and: [createCondition("a", "eq", 1)] };
    expect(toggleNot(root, [0])).toEqual({ and: [{ not: createCondition("a", "eq", 1) }] });
  });

  it("unwraps a not-wrapped node back to its inner node", () => {
    const root: FilterNode = { and: [{ not: createCondition("a", "eq", 1) }] };
    expect(toggleNot(root, [0])).toEqual({ and: [createCondition("a", "eq", 1)] });
  });

  it("toggles the root itself", () => {
    const root: FilterNode = createCondition("a", "eq", 1);
    expect(toggleNot(root, [])).toEqual({ not: createCondition("a", "eq", 1) });
  });
});

describe("nested build/edit/remove integration", () => {
  it("builds the docs/MCP_TOOLS.md section 4 worked example by composition", () => {
    let tree: FilterNode | null = createGroup("and");
    tree = addChild(tree, [], createCondition("status", "in", ["active", "at_risk"]));
    tree = addChild(tree, [], createCondition("target_date", "lte", "@today+30d"));
    tree = addChild(tree, [], createGroup("or"));
    tree = addChild(tree, [2], createCondition("owner", "eq", "@me"));
    tree = addChild(tree, [2], createCondition("sponsor", "is_null"));
    tree = addChild(tree, [], createCondition("tags", "has_any", ["deprioritized"]));
    tree = toggleNot(tree, [3]);

    expect(tree).toEqual({
      and: [
        { field: "status", op: "in", value: ["active", "at_risk"] },
        { field: "target_date", op: "lte", value: "@today+30d" },
        {
          or: [
            { field: "owner", op: "eq", value: "@me" },
            { field: "sponsor", op: "is_null" },
          ],
        },
        { not: { field: "tags", op: "has_any", value: ["deprioritized"] } },
      ],
    });

    // Editing survives structure: change the owner condition's operator/value.
    tree = replaceCondition(tree, [2, 0], createCondition("owner", "neq", "@me"));
    expect(getNodeAtPath(tree, [2, 0])).toEqual({ field: "owner", op: "neq", value: "@me" });

    // Removing the nested or group's second child leaves a one-element or group.
    tree = removeNodeAtPath(tree, [2, 1]) as FilterNode;
    expect(getNodeAtPath(tree, [2])).toEqual({ or: [{ field: "owner", op: "neq", value: "@me" }] });
  });
});
