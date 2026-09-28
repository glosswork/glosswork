import { describe, expect, it } from "vitest";
import type { FieldDoc } from "../api/objectTypes";
import { EMPTY_FIELD_VALUE, formatFieldValue } from "./fieldDisplay";

function makeField(overrides: Partial<FieldDoc>): FieldDoc {
  return {
    key: "status",
    name: "Status",
    type: "short_text",
    description: "A field.",
    required: false,
    unique: false,
    indexed: false,
    embed: false,
    default: null,
    config: {},
    position: 0,
    operators: [],
    display_eligible: true,
    ...overrides,
  };
}

describe("formatFieldValue", () => {
  it("renders the null placeholder for null and undefined values", () => {
    const field = makeField({});
    expect(formatFieldValue(field, null)).toBe(EMPTY_FIELD_VALUE);
    expect(formatFieldValue(field, undefined)).toBe(EMPTY_FIELD_VALUE);
  });

  it("shows a single_select option's label, not its raw value", () => {
    const field = makeField({
      type: "single_select",
      options: [
        { value: "on_track", label: "On Track", description: "Progressing as planned." },
        { value: "at_risk", label: "At Risk", description: "Needs attention." },
      ],
    });
    expect(formatFieldValue(field, "on_track")).toBe("On Track");
  });

  it("falls back to the raw value if a single_select option is unrecognized", () => {
    const field = makeField({ type: "single_select", options: [] });
    expect(formatFieldValue(field, "mystery")).toBe("mystery");
  });

  it("shows multi_select option labels joined, not raw values", () => {
    const field = makeField({
      type: "multi_select",
      options: [
        { value: "eng", label: "Engineering", description: "" },
        { value: "ops", label: "Operations", description: "" },
      ],
    });
    expect(formatFieldValue(field, ["eng", "ops"])).toBe("Engineering, Operations");
  });

  it("renders an empty multi_select as the empty placeholder", () => {
    const field = makeField({ type: "multi_select", options: [] });
    expect(formatFieldValue(field, [])).toBe(EMPTY_FIELD_VALUE);
  });

  it("renders booleans as Yes/No", () => {
    const field = makeField({ type: "boolean" });
    expect(formatFieldValue(field, true)).toBe("Yes");
    expect(formatFieldValue(field, false)).toBe("No");
  });

  it("stringifies plain scalar values", () => {
    const field = makeField({ type: "short_text" });
    expect(formatFieldValue(field, "Hello")).toBe("Hello");
  });

  it("SCOPE FENCE: still returns the raw id for a user_ref field", () => {
    // `formatFieldValue` deliberately never resolves a name — `FieldValue` decides whether to
    // resolve a name and calls `PrincipalName` instead of this function for `user_ref`, but
    // `table-view/groupLabel.ts::groupRowLabel` and `table-view/MergeConflictDialog.tsx` both
    // still consume this function's `string` return directly and must keep getting the id. This
    // is a fence, not a proof that a defect was fixed.
    const field = makeField({ type: "user_ref" });
    expect(formatFieldValue(field, "9f3c-a1")).toBe("9f3c-a1");
  });
});
