/**
 * One display-field rule, asserted as an absence rather than a convention:
 * `services/base.py::display_field` is the only place that answers "which field labels a
 * record", and `fieldtypes.py::is_display_eligible` the only place that answers "may this
 * field be one". The frontend gets both answers on the wire — `effective_display_field_key`
 * on the describe document, `display_eligible` on every `FieldDoc` — precisely so the
 * constraint is enforceable here at all: a TypeScript module cannot import a Python one, so
 * a rule that is not on the wire is a rule the frontend must re-derive.
 *
 * The two re-derivations this guards are the two that are easy to write and impossible to
 * notice: sorting fields by `position` to pick the first one, and testing `field.type`
 * against the ineligible list. Both would work today and both would drift the first time the
 * backend rule changes.
 *
 * Grep-backed, in the pattern of `access/hidingIsNeverTheOnlySignal.test.ts` and
 * `filters/noHardcodedOperators.test.ts`.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const srcRoot = dirname(dirname(fileURLToPath(import.meta.url)));

function collectSourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry);
    if (statSync(fullPath).isDirectory()) {
      if (entry !== "node_modules" && entry !== "dist") files.push(...collectSourceFiles(fullPath));
      continue;
    }
    if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) files.push(fullPath);
  }
  return files;
}

function relPath(file: string): string {
  return relative(srcRoot, file).split(sep).join("/");
}

/** Declares the wire shape itself, including the `position` and `display_eligible` fields of
 * `FieldDoc`. Declaring a field is not reading it to decide anything. */
const DECLARATION_SITES = new Set(["api/objectTypes.ts", "api/schema.ts"]);

const files = collectSourceFiles(srcRoot).filter((file) => !DECLARATION_SITES.has(relPath(file)));

/** The three types `fieldtypes.py::is_display_eligible` excludes. Naming one of them is
 * ordinary — `relation` appears wherever links are rendered, `attachment` wherever a file
 * cell is. Naming **all three together** is the shape of the predicate itself, and that is
 * what this file forbids outside the sites that legitimately declare the whole field-type
 * vocabulary. */
const INELIGIBLE_TYPES = ["relation", "attachment", "user_ref"];

/**
 * The modules that name the full field-type vocabulary for reasons that have nothing to do
 * with display eligibility, pinned by name rather than passing by omission: the operator
 * matrix's own guard, the filter builder's test fixture, the schema editor's type picker, and
 * the table cell's own inline-editing scope. `EditableCell.tsx` excludes
 * `relation`/`attachment` from inline editing — a display-affordance decision,
 * not a display-*field*-eligibility one — and has a `user_ref` branch that renders through
 * `PrincipalName` instead of `formatFieldValue` for the same, unrelated reason: none of the
 * three is there because it cannot label a record.
 */
const VOCABULARY_SITES = new Set([
  "filters/noHardcodedOperators.ts",
  "filters/__fixtures__/filterBuilderObjectType.ts",
  "schema-editor/fieldTypes.ts",
  "table-view/EditableCell.tsx",
]);

function namesEveryIneligibleType(source: string): boolean {
  return INELIGIBLE_TYPES.every((type) =>
    new RegExp(`["'\`]${type}["'\`]`).test(source),
  );
}

describe("one rule, one eligibility predicate", () => {
  it("no module derives a display field from FieldDoc.position", () => {
    const offenders = files
      .filter((file) => /\.position\b/.test(readFileSync(file, "utf-8")))
      .map(relPath);

    // Empty today, and the point is that it stays empty: sorting fields by `position` to
    // pick "the first one" is exactly the derivation the backend now owns.
    expect(offenders).toEqual([]);
  });

  it("no module outside the vocabulary sites restates the ineligible set", () => {
    const offenders = files
      .filter((file) => !VOCABULARY_SITES.has(relPath(file)))
      .filter((file) => namesEveryIneligibleType(readFileSync(file, "utf-8")))
      .map(relPath)
      .sort();

    // Empty today. Pasting `["relation", "attachment", "user_ref"]` into any module — the
    // shape a re-derivation of `is_display_eligible` takes — puts that module here.
    expect(offenders).toEqual([]);
  });

  it("the schema editor's select reads display_eligible off the wire", () => {
    const source = readFileSync(join(srcRoot, "schema-editor/SchemaEditorPage.tsx"), "utf-8");

    expect(source).toContain("candidate.display_eligible");
    // And does not reconstruct the predicate it just read.
    for (const type of INELIGIBLE_TYPES) {
      expect(source).not.toContain(`"${type}"`);
    }
  });
});
