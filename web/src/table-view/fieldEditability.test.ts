import { describe, expect, it } from "vitest";
import { isEditableFieldType, isGroupableFieldType } from "./fieldEditability";

describe("isEditableFieldType", () => {
  it("excludes relation and attachment", () => {
    expect(isEditableFieldType("relation")).toBe(false);
    expect(isEditableFieldType("attachment")).toBe(false);
  });

  it("includes every other field type", () => {
    for (const type of [
      "short_text",
      "long_text",
      "url",
      "integer",
      "decimal",
      "boolean",
      "date",
      "datetime",
      "single_select",
      "multi_select",
      "user_ref",
    ]) {
      expect(isEditableFieldType(type)).toBe(true);
    }
  });
});

describe("isGroupableFieldType", () => {
  it("allows single_select and relation only", () => {
    expect(isGroupableFieldType("single_select")).toBe(true);
    expect(isGroupableFieldType("relation")).toBe(true);
    expect(isGroupableFieldType("multi_select")).toBe(false);
    expect(isGroupableFieldType("short_text")).toBe(false);
  });
});
