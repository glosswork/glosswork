/**
 * The two cases that matter here are the reason this module exists as something other than a
 * one-line `Object.fromEntries`: both were found by posting to a real server, and both pass a
 * type-checker and a casual reading.
 */
import { describe, expect, it } from "vitest";
import type { FieldDoc } from "../api/objectTypes";
import {
  creatableFields,
  draftToValues,
  initialDraft,
  missingRequired,
} from "./newRecordDraft";

function field(overrides: Partial<FieldDoc> & { key: string; type: string }): FieldDoc {
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
  };
}

const name = field({ key: "name", type: "short_text", required: true });
const stage = field({
  key: "stage",
  type: "single_select",
  required: true,
  default: "draft",
  options: [
    { value: "draft", label: "Draft", description: "Not started." },
    { value: "live", label: "Live", description: "In flight." },
  ],
});
const flag = field({ key: "flag", type: "boolean", default: true });
const tags = field({ key: "tags", type: "multi_select", options: [] });
const owner = field({ key: "owner", type: "relation", display_eligible: false });
const files = field({ key: "files", type: "attachment", display_eligible: false });

const allFields = [name, stage, flag, tags, owner, files];

describe("creatableFields", () => {
  it("drops the two field types the create route cannot accept", () => {
    expect(creatableFields(allFields).map((f) => f.key)).toEqual([
      "name",
      "stage",
      "flag",
      "tags",
    ]);
  });

  it("preserves the order the wire delivered, rather than re-deriving it", () => {
    // `list_fields` is `ORDER BY position, key`, so schema order arrives already applied. Two
    // fields carrying the same `position` (the default, 0) must come back in wire order — which
    // a defensive sort on `.position` would be free to reverse, besides being banned outright by
    // `api/oneDisplayFieldRule.test.ts`.
    const reversed = [tags, flag, stage, name];
    expect(creatableFields(reversed).map((f) => f.key)).toEqual(["tags", "flag", "stage", "name"]);
  });
});

describe("initialDraft", () => {
  it("starts each field from its own default", () => {
    expect(initialDraft(allFields)).toEqual({
      name: "",
      stage: "draft",
      flag: true,
      tags: [],
    });
  });
});

describe("draftToValues: omit what is unchanged, not what is null", () => {
  it("posts nothing at all for a wholly untouched form", () => {
    expect(draftToValues(allFields, initialDraft(allFields))).toEqual({});
  });

  it("does not overwrite a boolean default with false just because the box is unchecked", () => {
    // The bug this pins: `parseEditedValue` returns `draft === true` for a boolean and so can
    // never return null. An omit-nulls rule posts `flag: false` here, and the server then skips
    // applying `default_value: true` because the key was present. Measured against a real server.
    const draft = { ...initialDraft(allFields), name: "Acme" };
    const values = draftToValues(allFields, draft);

    expect(values).not.toHaveProperty("flag");
    expect(values).toEqual({ name: "Acme" });
  });

  it("does not store an empty array for an untouched multi-select", () => {
    // Same shape, second type: `parseEditedValue` returns `[]`, never null, and
    // `fieldtypes.py` is explicit that an absent optional value omits the key entirely.
    const values = draftToValues(allFields, { ...initialDraft(allFields), name: "Acme" });

    expect(values).not.toHaveProperty("tags");
  });

  it("still posts a boolean the person deliberately turned off", () => {
    // The other side of the same rule: unchanged is omitted, changed is sent — including a change
    // away from a default. Without this the rule would be indistinguishable from "never post a
    // boolean", which is a different bug in the opposite direction.
    const values = draftToValues(allFields, { ...initialDraft(allFields), flag: false });

    expect(values).toEqual({ flag: false });
  });

  it("still posts a defaulted select the person deliberately changed", () => {
    const values = draftToValues(allFields, { ...initialDraft(allFields), stage: "live" });

    expect(values).toEqual({ stage: "live" });
  });

  it("posts a multi-select the person actually chose values in", () => {
    const values = draftToValues(allFields, { ...initialDraft(allFields), tags: ["a", "b"] });

    expect(values).toEqual({ tags: ["a", "b"] });
  });

  it("never posts a relation or an attachment, whatever the draft holds", () => {
    const values = draftToValues(allFields, {
      ...initialDraft(allFields),
      owner: "REC-001",
      files: ["attachment-id"],
    });

    expect(values).toEqual({});
  });
});

describe("missingRequired: a default satisfies required", () => {
  it("names a required field left empty", () => {
    expect(missingRequired(allFields, initialDraft(allFields))).toEqual(["name"]);
  });

  it("does not name a required field that carries a default", () => {
    // `stage` is required AND empty-able, but it defaults to "draft". The server applies defaults
    // BEFORE computing which required fields are missing, so omitting it succeeds — measured:
    // POST {"name": "C"} returned 200 and stored stage "draft". A pre-flight that refused here
    // would refuse, in the browser, a write the server accepts.
    const draft = { ...initialDraft(allFields), name: "Acme", stage: "" };

    expect(missingRequired(allFields, draft)).toEqual([]);
  });

  it("names nothing once every defaultless required field is filled", () => {
    expect(missingRequired(allFields, { ...initialDraft(allFields), name: "Acme" })).toEqual([]);
  });

  it("treats whitespace as empty", () => {
    expect(missingRequired(allFields, { ...initialDraft(allFields), name: "   " })).toEqual([
      "name",
    ]);
  });

  it("never treats an unchecked required boolean as missing", () => {
    // Both states of a checkbox are a value a person chose; "false" is not "blank". A required
    // boolean with no default is satisfied by either state.
    const required = [field({ key: "agreed", type: "boolean", required: true })];

    expect(missingRequired(required, { agreed: false })).toEqual([]);
  });
});
