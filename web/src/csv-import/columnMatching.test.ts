import { describe, expect, it } from "vitest";
import type { FieldDoc } from "../api/objectTypes";
import { suggestFieldForHeader } from "./columnMatching";

function field(overrides: Partial<FieldDoc> & { key: string; name: string }): FieldDoc {
  return {
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

const fields: FieldDoc[] = [
  field({ key: "target_date", name: "Target Date", type: "date" }),
  field({ key: "owner", name: "Owner", type: "relation" }),
  field({ key: "status", name: "Status", type: "single_select" }),
];

describe("suggestFieldForHeader", () => {
  it("matches a header against a field's name after normalization", () => {
    expect(suggestFieldForHeader("Target Date", fields)).toBe("target_date");
  });

  it("matches a header that is already the field key", () => {
    expect(suggestFieldForHeader("target_date", fields)).toBe("target_date");
  });

  it("matches regardless of hyphen/underscore/case variation", () => {
    expect(suggestFieldForHeader("Target-Date", fields)).toBe("target_date");
    expect(suggestFieldForHeader("TARGET_DATE", fields)).toBe("target_date");
    expect(suggestFieldForHeader("  target   date  ", fields)).toBe("target_date");
  });

  it("returns null when no field matches", () => {
    expect(suggestFieldForHeader("Unrelated Column", fields)).toBeNull();
  });

  it("does not branch on field type", () => {
    // A relation field ("Owner") is matched purely by name/key text, same as any other type.
    expect(suggestFieldForHeader("Owner", fields)).toBe("owner");
  });
});
