import { describe, expect, it } from "vitest";
import type { FilterNode } from "../filters/types";
import { DEFAULT_SEARCH_MODE, parseSearchParams, serializeSearchParams } from "./searchParams";

describe("parseSearchParams", () => {
  it("defaults mode to hybrid and types/filter to empty when absent", () => {
    const parsed = parseSearchParams(new URLSearchParams("q=deal+desk"));
    expect(parsed).toEqual({ q: "deal desk", mode: "hybrid", types: [], filter: null });
  });

  it("reads an explicit mode", () => {
    expect(parseSearchParams(new URLSearchParams("q=x&mode=semantic")).mode).toBe("semantic");
    expect(parseSearchParams(new URLSearchParams("q=x&mode=keyword")).mode).toBe("keyword");
  });

  it("falls back to the default mode for an unknown value", () => {
    expect(parseSearchParams(new URLSearchParams("q=x&mode=bogus")).mode).toBe(
      DEFAULT_SEARCH_MODE,
    );
  });

  it("splits a comma-separated types list and drops empty entries", () => {
    const parsed = parseSearchParams(new URLSearchParams("q=x&types=initiative,decision"));
    expect(parsed.types).toEqual(["initiative", "decision"]);
  });

  it("parses a filter only when exactly one type is present", () => {
    const filter: FilterNode = { field: "status", op: "eq", value: "active" };
    const params = new URLSearchParams({
      q: "x",
      types: "initiative",
      filter: JSON.stringify(filter),
    });
    expect(parseSearchParams(params).filter).toEqual(filter);
  });

  it("drops a filter when zero types are selected", () => {
    const filter: FilterNode = { field: "status", op: "eq", value: "active" };
    const params = new URLSearchParams({ q: "x", filter: JSON.stringify(filter) });
    expect(parseSearchParams(params).filter).toBeNull();
  });

  it("drops a filter when more than one type is selected", () => {
    const filter: FilterNode = { field: "status", op: "eq", value: "active" };
    const params = new URLSearchParams({
      q: "x",
      types: "initiative,decision",
      filter: JSON.stringify(filter),
    });
    expect(parseSearchParams(params).filter).toBeNull();
  });

  it("drops a filter that fails to parse as JSON rather than throwing", () => {
    const params = new URLSearchParams({ q: "x", types: "initiative", filter: "{not json" });
    expect(parseSearchParams(params).filter).toBeNull();
  });
});

describe("serializeSearchParams", () => {
  it("omits the default mode and empty types/filter", () => {
    const params = serializeSearchParams({ q: "deal desk", mode: "hybrid", types: [], filter: null });
    expect(params.toString()).toBe(new URLSearchParams({ q: "deal desk" }).toString());
  });

  it("writes a non-default mode", () => {
    const params = serializeSearchParams({ q: "x", mode: "semantic", types: [], filter: null });
    expect(params.get("mode")).toBe("semantic");
  });

  it("writes a comma-separated types list", () => {
    const params = serializeSearchParams({
      q: "x",
      mode: "hybrid",
      types: ["initiative", "decision"],
      filter: null,
    });
    expect(params.get("types")).toBe("initiative,decision");
  });

  it("writes a filter when exactly one type is present", () => {
    const filter: FilterNode = { field: "status", op: "eq", value: "active" };
    const params = serializeSearchParams({ q: "x", mode: "hybrid", types: ["initiative"], filter });
    expect(JSON.parse(params.get("filter") as string)).toEqual(filter);
  });

  it("drops a filter when types does not name exactly one key", () => {
    const filter: FilterNode = { field: "status", op: "eq", value: "active" };
    const params = serializeSearchParams({
      q: "x",
      mode: "hybrid",
      types: ["initiative", "decision"],
      filter,
    });
    expect(params.get("filter")).toBeNull();
  });

  it("round-trips through parseSearchParams", () => {
    const filter: FilterNode = { field: "status", op: "eq", value: "active" };
    const original = { q: "deal desk", mode: "semantic" as const, types: ["initiative"], filter };
    const roundTripped = parseSearchParams(serializeSearchParams(original));
    expect(roundTripped).toEqual(original);
  });
});
