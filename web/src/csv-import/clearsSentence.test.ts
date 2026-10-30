import { describe, expect, it } from "vitest";

import type { FieldDoc } from "../api/objectTypes";
import { clearsSentence } from "./clearsSentence";

const field = (key: string, name: string) => ({ key, name }) as FieldDoc;
const fields = [field("title", "Title"), field("superseded_by", "Superseded by"), field("notes", "Notes")];

describe("clearsSentence", () => {
  it("says how many values and which fields, before and after a commit", () => {
    const result = { cleared: 3, cleared_by_field: { superseded_by: 3 } };
    expect(clearsSentence(result, fields, "will be cleared")).toBe(
      "3 values will be cleared: Superseded by (3).",
    );
    expect(clearsSentence(result, fields, "cleared")).toBe("3 values cleared: Superseded by (3).");
  });

  it("lists fields in the object type's own order, by name", () => {
    const result = { cleared: 5, cleared_by_field: { notes: 2, superseded_by: 3 } };
    expect(clearsSentence(result, fields, "will be cleared")).toBe(
      "5 values will be cleared: Superseded by (3), Notes (2).",
    );
  });

  it("uses the singular for one value", () => {
    expect(clearsSentence({ cleared: 1, cleared_by_field: { notes: 1 } }, fields, "cleared")).toBe(
      "1 value cleared: Notes (1).",
    );
  });

  it("says zero plainly when nothing is cleared", () => {
    expect(clearsSentence({ cleared: 0, cleared_by_field: {} }, fields, "will be cleared")).toBe(
      "0 values will be cleared.",
    );
  });
});
