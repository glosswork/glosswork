import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { findHardcodedOperatorTableTypeNames } from "./noHardcodedOperators";

describe("findHardcodedOperatorTableTypeNames (detector unit tests)", () => {
  it("flags a hardcoded type-to-operators lookup table", () => {
    const source = `
      const OPERATORS_BY_TYPE = {
        short_text: ["eq", "neq", "contains"],
        integer: ["eq", "neq", "gt", "gte"],
        boolean: ["eq"],
      };
    `;
    expect(findHardcodedOperatorTableTypeNames(source)).not.toBeNull();
  });

  it("flags the same shape even under a differently named identifier (not a rename-proof no-op)", () => {
    const source = `
      const whateverThisIsCalled: Record<string, string[]> = {
        single_select: ["eq", "neq", "in", "not_in"],
        multi_select: ["has_any", "has_all", "has_none"],
      };
    `;
    expect(findHardcodedOperatorTableTypeNames(source)).not.toBeNull();
  });

  it("does not flag a single type mapped to an array (no table, just one entry)", () => {
    const source = `const x = { short_text: ["eq", "neq"] };`;
    expect(findHardcodedOperatorTableTypeNames(source)).toBeNull();
  });

  it("does not flag a real field-descriptor object (type as a value, not a key)", () => {
    const source = `
      const field = {
        key: "status",
        type: "single_select",
        operators: ["eq", "neq", "in", "not_in", "is_null", "is_not_null"],
        display_eligible: true,
      };
      const other = {
        key: "priority",
        type: "integer",
        operators: ["eq", "neq", "gt", "gte", "lt", "lte", "between", "in"],
        display_eligible: true,
      };
    `;
    expect(findHardcodedOperatorTableTypeNames(source)).toBeNull();
  });

  it("does not flag type-name equality checks used to pick an input widget", () => {
    const source = `
      if (field.type === "single_select" || field.type === "multi_select") {
        return <SelectValueInput options={field.options} />;
      }
      if (field.type === "integer" || field.type === "decimal") {
        return <NumberInput />;
      }
    `;
    expect(findHardcodedOperatorTableTypeNames(source)).toBeNull();
  });
});

describe("no file under web/src/ hardcodes an operator-by-field-type table", () => {
  const srcRoot = dirname(dirname(fileURLToPath(import.meta.url)));
  const excludedFiles = new Set([
    join(srcRoot, "filters", "noHardcodedOperators.ts"),
    join(srcRoot, "filters", "noHardcodedOperators.test.ts"),
    join(srcRoot, "api", "schema.ts"),
  ]);
  const excludedDirNames = new Set(["node_modules", "dist"]);

  function collectSourceFiles(dir: string): string[] {
    const files: string[] = [];
    for (const entry of readdirSync(dir)) {
      const fullPath = join(dir, entry);
      const stats = statSync(fullPath);
      if (stats.isDirectory()) {
        if (!excludedDirNames.has(entry)) {
          files.push(...collectSourceFiles(fullPath));
        }
        continue;
      }
      if (/\.(ts|tsx)$/.test(entry) && !excludedFiles.has(fullPath)) {
        files.push(fullPath);
      }
    }
    return files;
  }

  it("scans every .ts/.tsx file under web/src (except the generated schema and this detector)", () => {
    const files = collectSourceFiles(srcRoot);
    expect(files.length).toBeGreaterThan(0);

    const offenders: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf-8");
      const matched = findHardcodedOperatorTableTypeNames(source);
      if (matched) {
        offenders.push(`${relative(srcRoot, file)}: ${matched.join(", ")}`);
      }
    }

    expect(offenders).toEqual([]);
  });
});
