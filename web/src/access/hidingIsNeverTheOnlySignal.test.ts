/**
 * "Hiding is never the only signal", asserted as a **pairing** rather than once per screen: any
 * screen that hides a control because of the caller's level renders `ReadOnlyBanner`. The
 * failure mode
 * this guards is a silent screen — controls quietly absent, nothing said — and it is a failure
 * a per-screen test cannot catch, because the screen that forgets the banner is by definition
 * the one nobody wrote a test for.
 *
 * Grep-backed, per the pattern in `filters/noHardcodedOperators.test.ts` and
 * `table-view/noWebsocket.test.ts`: `levelAllows` is the only way a component asks the level
 * question, so "imports `levelAllows`" is a sound proxy for "gates on level".
 *
 * **There is a second key, and the reason is worth stating.**
 * DD-11 has three axes, and this file only ever watched one of them. `levelAllows` catches a
 * screen gating on an object-type *level*; `/inbox` gates on the credential's *scope*, because
 * all five proposal routes declare `require_scope("admin")` and a member cannot read the list
 * at all. So the Inbox hides controls for an access reason and trips none of the checks above.
 * Watching only levels would leave a scope-gated screen outside the meta-test entirely, covered
 * only by its own per-screen tests, which is precisely what a meta-test exists not to depend on.
 *
 * `inboxAccessMessage` is the scope-gate's equivalent of `levelAllows`: one module, one
 * question, and a screen that asks it must render the answer.
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

/** Declares the level vocabulary, the comparator, or the banner itself; none is a screen. */
const DECLARATION_SITES = new Set([
  "api/objectTypes.ts",
  "access/ReadOnlyBanner.tsx",
  "access/readOnlyBannerMessage.ts",
  // The scope-gate's own vocabulary, the sibling of `readOnlyBannerMessage.ts`.
  "inbox/inboxAccessMessage.ts",
]);

const files = collectSourceFiles(srcRoot);

describe("hiding is never the only signal", () => {
  it("every module that gates on level also renders the banner", () => {
    const offenders = files
      .filter((file) => !DECLARATION_SITES.has(relPath(file)))
      .filter((file) => {
        const source = readFileSync(file, "utf-8");
        return source.includes("levelAllows(") && !source.includes("ReadOnlyBanner");
      })
      .map(relPath);

    expect(offenders).toEqual([]);
  });

  it("pins which screens gate on level, so a new one is a deliberate addition", () => {
    const gating = files
      .filter((file) => !DECLARATION_SITES.has(relPath(file)))
      .filter((file) => readFileSync(file, "utf-8").includes("levelAllows("))
      .map(relPath)
      .sort();

    expect(gating).toEqual([
      "csv-import/CsvImportWizardPage.tsx",
      "record-detail/RecordDetailView.tsx",
      "schema-editor/SchemaEditorPage.tsx",
      "table-view/TableView.tsx",
    ]);
  });

  /**
   * The scope axis. Same pairing rule, different question: a screen that
   * asks `inboxAccessMessage` hides something for an access reason and must render what it
   * gets back.
   */
  it("every module that gates on scope also renders the statement", () => {
    const offenders = files
      .filter((file) => !DECLARATION_SITES.has(relPath(file)))
      .filter((file) => {
        const source = readFileSync(file, "utf-8");
        return source.includes("inboxAccessMessage(") && !source.includes("accessMessage");
      })
      .map(relPath);

    expect(offenders).toEqual([]);
  });

  it("pins which screens gate on scope, so a new one is a deliberate addition", () => {
    const gating = files
      .filter((file) => !DECLARATION_SITES.has(relPath(file)))
      .filter((file) => readFileSync(file, "utf-8").includes("inboxAccessMessage("))
      .map(relPath)
      .sort();

    expect(gating).toEqual(["inbox/InboxPage.tsx"]);
  });

  /**
   * The one deliberate exception to the *banner*, which moved with the screen it is about.
   *
   * The Inbox lists proposals across every type the caller can see, so it has no single object
   * type to name in `ReadOnlyBanner`'s sentence, and the row's own presence is the
   * explanation. It carries its own scope-shaped statement rather than the type-shaped banner,
   * and its per-row `canDecide` gate is the `admin` role rather than a level.
   *
   * Asserted at its new home, and asserted *absent* from its old one, so the exemption cannot
   * quietly exist in two places.
   */
  it("names inbox/InboxPage.tsx as the banner exception, and it is genuinely one", () => {
    const inbox = readFileSync(join(srcRoot, "inbox/InboxPage.tsx"), "utf-8");

    expect(inbox).not.toContain("levelAllows(");
    expect(inbox).not.toContain("ReadOnlyBanner");
    // Exempt from the banner because it is cross-type, not because it gates nothing.
    expect(inbox).toContain("canDecide");
    expect(inbox).toContain("inboxAccessMessage(");

    // And the screen that used to hold proposals no longer does, so it is no longer an exception
    // to anything. `/settings` is now `/people` and `/setup`, and the paragraph explaining
    // where proposals went moved to `PeoplePage.tsx`, which is why the path below changed and
    // the assertions did not (AGENTS.md, Traps: a deletion that takes its explanation with it).
    //
    // **These two are FENCES.** They assert the absence of a thing in a file that never
    // had it, so they cannot fail here by construction; they are kept because the paragraph they
    // guard is now a paragraph about a panel this file is the index of, and a future change that
    // reintroduces the mount should trip something. The load-bearing half of this test is the
    // `inbox/InboxPage.tsx` block above.
    const people = readFileSync(join(srcRoot, "people/PeoplePage.tsx"), "utf-8");
    expect(people).not.toContain("canDecide");
    expect(people).not.toContain("<PendingProposalsPanel");
  });
});
