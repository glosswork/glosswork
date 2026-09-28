/**
 * docs/DESIGN.md 6: **one component family renders every principal and every agent label**,
 * anywhere in the product.
 *
 * Grep-backed, in the pattern of `noOrphanedTokens.test.ts` and
 * `access/hidingIsNeverTheOnlySignal.test.ts`, and for the reason AGENTS.md gives: the
 * load-bearing property of this codebase is that an invariant has exactly one implementation
 * and a meta-test fails when a second appears. Attribution earned one because the second
 * implementations were the defect: the comment header, the record timeline and the audit
 * browser once each rendered a principal and a label their own way, and each got it wrong
 * differently -- a raw UUID here, a bare id there, a service account drawn as a person.
 *
 * It fails CLOSED. A module that renders one of these fields and is not in `ALLOWED` is
 * reported, so a new screen joins the list deliberately rather than by drifting.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const srcRoot = dirname(dirname(fileURLToPath(import.meta.url)));

/**
 * A JSX expression rendering one of the attribution fields: `{x.principal_display_name}`,
 * `{event.agent_label}`, `{c.author_id}`, `{p.display_name}` and their `??` fallbacks.
 *
 * Deliberately narrow: it matches a field *rendered as a JSX child*, optionally with a `??`
 * fallback, and nothing else. A map lookup, a `data-testid`, an `aria-label`, a prop expression or
 * a sort key mentioning one of these names is not a second renderer, and a rule that flagged those
 * would be a rule people learn to work around. This is why a grep for `principal_id` across
 * `web/src` is not the check: it returned 60 non-test hits, almost none of them defects. A broader
 * form of THIS regex made the same mistake one size smaller, matching any `{...}` containing the
 * field, which flagged four prop expressions and no defects. The lookbehind is what separates a JSX
 * *child* -- `<td>{x.f}</td>`, a render -- from a prop -- `fallbackId={x.f}`, which is handing the
 * value TO the primitive.
 */
const RENDER_RE =
  /(?<!=)\{\s*[A-Za-z_$][\w$?.]*\.(principal_display_name|agent_label|author_id|display_name)\s*(?:\?\?\s*[\w$?."'`]+\s*)?\}/;

/**
 * The modules allowed to render these fields, each with the reason it is not a second
 * implementation. Anything else must go through `ui/Avatar.tsx`.
 */
const ALLOWED: Record<string, string> = {
  "ui/Avatar.tsx": "the primitive itself",
  "ui/attributionDerivation.ts": "the primitive's pure half",
  "principals/PrincipalName.tsx": "delegates to the primitive; the one `user_ref` renderer",
  // --- stated exemptions, not oversights ---
  // The three pickers. An `<option>` may contain text only, so an avatar cannot go inside one:
  // a permanent exception to "one family", recorded rather than hidden. docs/DESIGN.md 6 lists
  // pickers among the surfaces the primitive covers, which it cannot while they are `<select>`s.
  "table-view/FieldInput.tsx": "renders `<option>` text in the `user_ref` picker",
  "filters/ValueInput.tsx": "renders `<option>` text in the filter builder's principal picker",
  "schema-editor/PermissionsPanel.tsx":
    "renders `<option>` text in the grant picker; its grant ROWS go through `PrincipalName`",
  // `SettingsPage.tsx` has no exemption: `/people`'s three cards render every principal through
  // `Hand`, and `AgentLabelsTable`'s group header names the owner through it too. An agent label's
  // own `display_name` column is a label's name, not a principal's, and is bare text on purpose.
  //
  // **Two narrow exemptions survive, for prose rather than for attribution.** Both tables
  // name the subject of a destructive action inside a confirmation sentence -- "Deactivate Dana
  // Reyes?" -- and the regex above cannot tell that from a row rendering a name where an avatar
  // belongs. An avatar inside that sentence would be worse than the thing this file exists to
  // prevent, and dropping the name would make a two-step confirm stop saying what it is about.
  // The ROWS in both files go through `Hand`, which is what this file is about.
  "people/PeopleTable.tsx": "names the subject inside the deactivate confirmation sentence",
  "people/ServiceAccountsTable.tsx":
    "names the subject inside the deactivate confirmation sentence",
  // A third instance of the same prose exemption, not attribution. The reset dialog
  // names the person the reset targets in its own heading and revocation sentence ("Reset
  // password for Dana Reyes", "Dana Reyes will be signed out everywhere..."), the same "who is
  // this action about" prose the two rows above already carry, and an avatar inline in either
  // sentence would read no better there than in a deactivate confirmation.
  "people/ResetPasswordDialog.tsx": "names the subject inside its heading and revocation sentence",
  // The Setup page's token table has an Agent column, and this is the one screen to render the
  // `agent_label` field itself rather than a principal. It is exempt because the cell describes a
  // **credential**, not an actor: the string is stored on the token at mint time, and until that
  // token's first call there may be no `agent_labels` row behind it at all, so there is no display
  // name and no verified flag for the primitive to draw. Drawing an avatar there would make the
  // avatar's kind load-bearing for a token that may never be used. The rule this file protects is
  // that no second screen invents its own way of drawing an actor; this cell draws none.
  "setup/AccessTokensPanel.tsx":
    "renders a token's stored label, which is a property of a credential and not an actor",
};

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === "dist") continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.tsx?$/.test(full) && !/\.test\.tsx?$/.test(full)) out.push(full);
  }
  return out;
}

describe("docs/DESIGN.md 6: one attribution primitive", () => {
  it("has no module rendering a principal or an agent label outside the primitive", () => {
    const offenders: string[] = [];
    for (const file of walk(srcRoot)) {
      const rel = relative(srcRoot, file).split(sep).join("/");
      if (rel in ALLOWED) continue;
      const source = readFileSync(file, "utf8");
      if (RENDER_RE.test(source)) offenders.push(rel);
    }
    expect(offenders).toEqual([]);
  });

  it("pins the exemption list by equality, so widening it is a deliberate act", () => {
    expect(Object.keys(ALLOWED).sort()).toEqual([
      "filters/ValueInput.tsx",
      "people/PeopleTable.tsx",
      "people/ResetPasswordDialog.tsx",
      "people/ServiceAccountsTable.tsx",
      "principals/PrincipalName.tsx",
      "schema-editor/PermissionsPanel.tsx",
      "setup/AccessTokensPanel.tsx",
      "table-view/FieldInput.tsx",
      "ui/Avatar.tsx",
      "ui/attributionDerivation.ts",
    ]);
  });
});
