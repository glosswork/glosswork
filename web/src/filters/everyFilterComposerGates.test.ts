/**
 * **An incomplete condition never reaches the network**, asserted as a property of the
 * *product* rather than of one component.
 *
 * **Why this file exists.** The gate lives in `FilterBuilder` itself, so both screens that
 * render that builder inherit it, and a gate in a consumer is "a gate the next consumer will not
 * have". That would be enough with one composer, but there are two: a filter chip's condition is
 * built in `table-view/ConditionPopover.tsx`, which does not route through `FilterBuilder` at all,
 * so it
 * holds its own copy of the gate — the same predicate, imported from the same module, called at
 * its own commit boundary.
 *
 * Two composers is a decision. **Three would be an accident**, and that is what this file is
 * defending: a saved-view quick filter, a column-header filter, a future card view — anything
 * that composes a filter tree and hands it to something that queries — could arrive with no gate
 * at all, every existing test would stay green, and the rule would quietly stop being true of
 * the product while remaining true of the two components that happen to have tests about it. The
 * screen that forgets the gate is by definition the one nobody wrote a test for.
 *
 * Grep-backed and pinned by path, in the shape `access/hidingIsNeverTheOnlySignal.test.ts`,
 * `filters/noHardcodedOperators.test.ts` and `table-view/noWebsocket.test.ts` already use: the
 * assertion is not "this is correct" but "this is the set, and changing it is an edit somebody
 * made on purpose". A new composer turns this red and names itself; whoever added it then writes
 * down which of the four roles below it plays, or gives it a gate.
 *
 * **The proxy, stated as a proxy.** A module "composes a filter" if it mentions the wire type
 * (`FilterNode`) or the chip form of one condition (`ChipCondition`) *and* mentions a change
 * callback (`onChange`/`onCommit`) — that is, a filter leaves it through somebody else's hands.
 * It is a grep; it can be fooled. What it cannot be is silent about a new file.
 *
 * **The gate is keyed on the three predicate names, not on the module path**, and the difference
 * is load-bearing. `completeness.ts` exports two things: the shape rule
 * (`isConditionComplete`, `isFilterComplete`, `incompleteNodes`) and the operator vocabulary it is
 * written from (`NO_VALUE_OPS`, `LIST_OPS`, `valueCardinality`). `ValueInput.tsx` imports the
 * vocabulary to pick a widget and `chipFilter.ts` imports it to write a sentence; neither holds a
 * gate, and pinning "imports from `completeness`" would put both in a list that is supposed to
 * mean "decides what may be sent".
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

/** Repo-relative, forward-slashed, so the assertion messages read the same on every platform. */
function relPath(file: string): string {
  return relative(srcRoot, file).split(sep).join("/");
}

/** Declares the rule; it is not a composer and not a holder of itself. */
const DECLARATION_SITES = new Set(["filters/completeness.ts"]);

/** The three exports that decide whether a filter may be sent. */
const GATE_PREDICATES = /\b(isConditionComplete|isFilterComplete|incompleteNodes)\b/;

/** The same three, called rather than merely named. */
const GATE_CALLS = /\b(isConditionComplete|isFilterComplete|incompleteNodes)\(/;

const files = collectSourceFiles(srcRoot).filter((file) => !DECLARATION_SITES.has(relPath(file)));

function sourcesMatching(pattern: RegExp): string[] {
  return files
    .filter((file) => pattern.test(readFileSync(file, "utf-8")))
    .map(relPath)
    .sort();
}

function composes(source: string): boolean {
  return /\b(FilterNode|ChipCondition)\b/.test(source) && /\bon(Change|Commit)\b/.test(source);
}

describe("every filter composer reaches a gate", () => {
  /**
   * The tripwire. Each entry carries the role it plays, because the list is only useful if the
   * next person can tell at a glance whether their new file belongs in it:
   *
   * - **holds the gate** — calls a predicate on its own commit path.
   * - **inside a gated builder** — a view `FilterBuilder` renders, which hands its condition
   *   *up* to that builder's `commit`; it never reaches a consumer on its own.
   * - **composed of gated composers** — builds its tree only out of conditions the two gate
   *   holders produced.
   * - **consumer** — puts a filter into a query. It receives trees, it does not build them.
   *
   * A file that is none of these four and appears here anyway is the accident this file exists
   * to catch.
   */
  it("pins every module that composes a filter, so a third gate is a decision rather than an accident", () => {
    const composers = files
      .filter((file) => composes(readFileSync(file, "utf-8")))
      .map(relPath)
      .sort();

    expect(composers).toEqual([
      "filters/FilterBuilder.tsx", //      holds the gate; shared with /search
      "filters/FilterNodeView.tsx", //     inside a gated builder
      "filters/GroupView.tsx", //          inside a gated builder
      "search/SearchPage.tsx", //          consumer
      "table-view/ConditionPopover.tsx", // holds the gate, at its own commit boundary
      "table-view/FilterChipRow.tsx", //   composed of gated composers
      "table-view/TableView.tsx", //       consumer
    ]);
  });

  it("pins the two modules that hold the gate", () => {
    expect(sourcesMatching(GATE_PREDICATES)).toEqual([
      "filters/FilterBuilder.tsx",
      "table-view/ConditionPopover.tsx",
    ]);
  });

  /**
   * Imported and never called is the one drift a path pin cannot see, and it is not theoretical:
   * both gate-bypass mutations measured against `ConditionPopover` were written as
   * `void isFilterComplete;`
   * precisely so the import stayed used and `tsc` stayed green.
   *
   * What this does **not** claim: that the predicate is called on the commit path. A module could
   * call it somewhere harmless and send anyway. That is what `TableView.incompleteFilter.test.tsx`
   * asserts, one case per incomplete shape, and its header says which of its six cases measures
   * which of the two gates.
   */
  it("and each of them calls the predicate rather than only importing it", () => {
    expect(sourcesMatching(GATE_CALLS)).toEqual([
      "filters/FilterBuilder.tsx",
      "table-view/ConditionPopover.tsx",
    ]);
  });
});
