import { describe, expect, it } from "vitest";
import type { ObjectTypeSummary } from "../api/objectTypes";
import { resolveIndexRoute } from "./resolveIndexRoute";

function objectType(key: string): ObjectTypeSummary {
  return {
    key,
    name: key,
    description: "",
    key_prefix: key.toUpperCase(),
    record_count: 0,
    field_count: 0,
    your_access: "admin",
  };
}

describe("resolveIndexRoute", () => {
  it("returns null when no object type exists (empty state)", () => {
    expect(resolveIndexRoute([])).toBeNull();
  });

  it("returns the first object type's route, preserving list order", () => {
    const objectTypes = [objectType("initiative"), objectType("task")];

    expect(resolveIndexRoute(objectTypes)).toBe("/initiative");
  });
});

/**
 * A scope fence. The role distinction lives in `IndexRoute`'s render and deliberately not
 * here: `resolveIndexRoute` decides *where `/` redirects*, which does not
 * depend on who is asking, and giving it a role would make a pure function over a list into a
 * function over the session. Arity is the cheap way to pin that — a second parameter is the
 * shape this fence exists to catch.
 */
describe("resolveIndexRoute learns nothing about roles", () => {
  it("still takes exactly one argument, the list", () => {
    expect(resolveIndexRoute.length).toBe(1);
  });
});
