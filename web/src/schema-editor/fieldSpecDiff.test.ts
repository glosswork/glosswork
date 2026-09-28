import { describe, expect, it } from "vitest";
import type { FieldDoc } from "../api/objectTypes";
import { buildFieldChanges, editableFieldFrom } from "./fieldSpecDiff";

const baseField: FieldDoc = {
  key: "points",
  name: "Points",
  type: "integer",
  description: "Effort estimate in story points.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 0,
  operators: ["eq", "ne"],
  display_eligible: true,
};

describe("buildFieldChanges", () => {
  it("is empty when nothing changed", () => {
    expect(buildFieldChanges(baseField, editableFieldFrom(baseField))).toEqual({});
  });

  it("includes only the touched keys", () => {
    const edited = { ...editableFieldFrom(baseField), name: "Story points" };
    expect(buildFieldChanges(baseField, edited)).toEqual({ name: "Story points" });
  });

  it("detects a config change by deep value, not reference", () => {
    const field: FieldDoc = { ...baseField, config: { options: [{ value: "a" }] } };
    const edited = editableFieldFrom(field);
    edited.config = { options: [{ value: "a" }] }; // different object, same value
    expect(buildFieldChanges(field, edited)).toEqual({});

    edited.config = { options: [{ value: "b" }] };
    expect(buildFieldChanges(field, edited)).toEqual({ config: { options: [{ value: "b" }] } });
  });

  it("does not include an untouched field even alongside another real change", () => {
    const edited = { ...editableFieldFrom(baseField), type: "long_text" };
    const changes = buildFieldChanges(baseField, edited);
    expect(changes).toEqual({ type: "long_text" });
    expect(changes).not.toHaveProperty("name");
  });
});
