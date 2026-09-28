import { describe, expect, it } from "vitest";
import type { FieldDoc } from "../api/objectTypes";
import { draftFromStoredValue, parseEditedValue, widgetKindFor } from "./fieldWidgets";

function field(type: string): FieldDoc {
  return {
    key: "f",
    name: "F",
    type,
    description: "d",
    required: false,
    unique: false,
    indexed: false,
    embed: false,
    default: null,
    config: {},
    position: 0,
    operators: [],
    display_eligible: !["relation", "attachment", "user_ref"].includes(type),
  };
}

describe("widgetKindFor", () => {
  it("maps field types to the expected widget", () => {
    expect(widgetKindFor(field("integer"))).toBe("number");
    expect(widgetKindFor(field("boolean"))).toBe("boolean");
    expect(widgetKindFor(field("single_select"))).toBe("select");
    expect(widgetKindFor(field("multi_select"))).toBe("multi_select");
    expect(widgetKindFor(field("long_text"))).toBe("textarea");
    expect(widgetKindFor(field("short_text"))).toBe("text");
  });

  it("maps user_ref to its own picker widget, not plain text", () => {
    expect(widgetKindFor(field("user_ref"))).toBe("user_ref");
  });
});

describe("draftFromStoredValue / parseEditedValue round-trip", () => {
  it("round-trips a number", () => {
    const draft = draftFromStoredValue(field("integer"), 42);
    expect(parseEditedValue(field("integer"), draft)).toBe(42);
  });

  it("round-trips a boolean", () => {
    expect(parseEditedValue(field("boolean"), draftFromStoredValue(field("boolean"), true))).toBe(true);
    expect(parseEditedValue(field("boolean"), draftFromStoredValue(field("boolean"), false))).toBe(false);
  });

  it("round-trips a multi_select array", () => {
    const draft = draftFromStoredValue(field("multi_select"), ["a", "b"]);
    expect(parseEditedValue(field("multi_select"), draft)).toEqual(["a", "b"]);
  });

  it("commits an empty text edit as null, not an empty string", () => {
    expect(parseEditedValue(field("short_text"), "")).toBeNull();
  });

  it("commits an empty number edit as null", () => {
    expect(parseEditedValue(field("integer"), "")).toBeNull();
  });

  it("parses a numeric string to a number", () => {
    expect(parseEditedValue(field("decimal"), "3.5")).toBe(3.5);
  });

  it("round-trips a user_ref value as its bare id string, with no special case needed", () => {
    const draft = draftFromStoredValue(field("user_ref"), "9f3c-a1");
    expect(draft).toBe("9f3c-a1");
    expect(parseEditedValue(field("user_ref"), draft)).toBe("9f3c-a1");
  });

  it("commits an empty user_ref selection as null, same as any other empty text edit", () => {
    expect(parseEditedValue(field("user_ref"), "")).toBeNull();
  });
});
