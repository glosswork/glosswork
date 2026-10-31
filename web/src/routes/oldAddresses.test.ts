import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import {
  lookupHidesType,
  oldAddressTarget,
  oldSchemaEditorTarget,
} from "./oldAddresses";

const NO_EXTRAS = { search: "", hash: "" };

describe("oldAddressTarget", () => {
  it.each([
    ["activity", "/workspace/activity"],
    ["audit", "/workspace/activity"],
    ["people", "/workspace/people"],
    ["settings", "/workspace/people"],
    ["setup", "/workspace/setup"],
    ["schema", "/workspace/schema"],
  ])("maps %s to %s", (key, target) => {
    expect(oldAddressTarget(key, NO_EXTRAS)).toBe(target);
  });

  it.each(["activity", "audit", "people", "settings", "setup", "schema"])(
    "keeps the query string and hash of %s",
    (key) => {
      expect(oldAddressTarget(key, { search: "?a=1", hash: "#b" })).toMatch(/\?a=1#b$/);
    },
  );

  it.each(["initiative", "ACTIVITY", "workspace", "inbox", "constructor", "__proto__"])(
    "does not map %s",
    (key) => {
      expect(oldAddressTarget(key, NO_EXTRAS)).toBeNull();
    },
  );
});

describe("oldSchemaEditorTarget", () => {
  it.each(["new", "initiative", "a", "a_1"])("sends /schema/%s to the editor", (segment) => {
    expect(oldSchemaEditorTarget("schema", segment, NO_EXTRAS)).toBe(
      `/workspace/schema/${segment}`,
    );
  });

  it("keeps the query string and hash", () => {
    expect(oldSchemaEditorTarget("schema", "new", { search: "?a=1", hash: "#b" })).toBe(
      "/workspace/schema/new?a=1#b",
    );
  });

  it.each(["SCH-1", "0b6f1f6a-3f0e-4a57-9a0c-7d0a0d7c3b11", "1abc", "Abc", "a".repeat(64)])(
    "leaves %s to the record route",
    (segment) => {
      expect(oldSchemaEditorTarget("schema", segment, NO_EXTRAS)).toBeNull();
    },
  );

  it("applies only under schema", () => {
    expect(oldSchemaEditorTarget("activity", "new", NO_EXTRAS)).toBeNull();
  });
});

describe("lookupHidesType", () => {
  it.each([403, 404])("is true for a %i", (status) => {
    expect(lookupHidesType(new ApiError(status, ""))).toBe(true);
  });

  it.each([401, 500, 503])("is false for a %i", (status) => {
    expect(lookupHidesType(new ApiError(status, ""))).toBe(false);
  });

  it("is false for a network failure and for no error at all", () => {
    expect(lookupHidesType(new TypeError("Failed to fetch"))).toBe(false);
    expect(lookupHidesType(null)).toBe(false);
  });
});
