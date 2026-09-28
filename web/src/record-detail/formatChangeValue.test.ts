import { describe, expect, it } from "vitest";
import type { FieldDoc } from "../api/objectTypes";
import { formatChangeValue } from "./formatChangeValue";

function field(overrides: Partial<FieldDoc> & Pick<FieldDoc, "key" | "type">): FieldDoc {
  return {
    name: overrides.key,
    description: `The ${overrides.key} field.`,
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
  } as FieldDoc;
}

describe("formatChangeValue", () => {
  it("is null in, null out — the caller decides 'edited' from that, not from an em dash", () => {
    expect(formatChangeValue(field({ key: "notes", type: "long_text" }), null)).toBeNull();
  });

  it("resolves a single_select's label, never its stored key", () => {
    const stage = field({
      key: "stage",
      type: "single_select",
      options: [{ value: "on_track", label: "On Track", description: "" }],
    });
    expect(formatChangeValue(stage, "on_track")).toBe("On Track");
  });

  it("groups a number the same way the field's live value would be", () => {
    const amount = field({ key: "amount", type: "integer", config: { min: 0 } });
    expect(formatChangeValue(amount, 68000)).toBe("68,000");
  });

  it("writes a date in words, with NO relative hint (it describes the past, not now)", () => {
    const due = field({ key: "due", type: "date" });
    expect(formatChangeValue(due, "2026-01-01", undefined)).not.toContain("(");
  });

  it("resolves a user_ref through the principals sidecar", () => {
    const owner = field({ key: "owner", type: "user_ref" });
    expect(
      formatChangeValue(owner, "p-1", { "p-1": { display_name: "Sam Okafor", email: null, is_active: true, type: "user" } }),
    ).toBe("Sam Okafor");
  });

  it("falls back to the raw id when a user_ref is not in the sidecar", () => {
    const owner = field({ key: "owner", type: "user_ref" });
    expect(formatChangeValue(owner, "gone-1")).toBe("gone-1");
  });

  it("counts an attachment field's files rather than printing the raw id array", () => {
    const files = field({ key: "files", type: "attachment" });
    expect(formatChangeValue(files, ["a", "b", "c"])).toBe("3 files");
    expect(formatChangeValue(files, ["a"])).toBe("1 file");
  });

  it("falls through to formatFieldValue for a plain text field", () => {
    const notes = field({ key: "notes", type: "short_text" });
    expect(formatChangeValue(notes, "Hello")).toBe("Hello");
  });
});
