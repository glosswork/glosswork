/**
 * `shellNav.ts` is the single list the sidebar and the collapsed menu both render.
 * These are the properties that make it safe to be that, asserted here rather than left to the
 * end-to-end suite, which can only see the consequences.
 */
import { describe, expect, it } from "vitest";

import { SHELL_NAV, shellNavSection } from "./shellNav";

describe("the shell's navigation entries", () => {
  it("is not empty", () => {
    // The assertion every other one here depends on. A `for` loop over an empty list satisfies
    // "every entry has an id" perfectly.
    expect(SHELL_NAV.length).toBeGreaterThan(0);
  });

  it("gives every entry a unique id", () => {
    const ids = SHELL_NAV.map((entry) => entry.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("gives every entry a non-empty label and an absolute route", () => {
    for (const entry of SHELL_NAV) {
      expect(entry.label.trim(), `entry ${entry.id} has no label`).not.toBe("");
      expect(entry.to.startsWith("/"), `entry ${entry.id} has a relative route`).toBe(true);
    }
  });

  it("pins the entries by equality, so adding or moving one is a deliberate act", () => {
    // By equality and not by length: this list is the contract two components render from, and
    // it is the thing `e2e/shell.spec.ts` compares the collapsed menu against. A new destination
    // in the product
    // should fail here first, where the reason for it can be written down.
    expect(SHELL_NAV.map((entry) => [entry.id, entry.to, entry.section])).toEqual([
      ["inbox", "/inbox", "primary"],
      ["search", "/search", "primary"],
      ["people", "/people", "workspace"],
      ["activity", "/activity", "workspace"],
      ["schema", "/schema", "workspace"],
      ["setup", "/setup", "workspace"],
    ]);
  });

  it("routes every entry at a path the shell actually serves", () => {
    // Every entry points at a real route: a change that adds a route adds it to the served set
    // here, and a change that points an entry at an alias has to come here and say so. **No
    // alias is left.** A link to a route that does not exist renders a blank `<main>`, which is
    // why this is asserted at all -- and this half leans on the literal below, so
    // `e2e/people-and-setup.spec.ts` clicks every Workspace entry and asserts the page it lands
    // on.
    const served = new Set(["/inbox", "/people", "/setup", "/search", "/activity", "/schema"]);
    for (const entry of SHELL_NAV) {
      expect(served.has(entry.to), `${entry.id} points at an unserved route ${entry.to}`).toBe(
        true,
      );
    }
  });

  it("gives the count badge to Inbox alone", () => {
    expect(SHELL_NAV.filter((entry) => entry.showsCount).map((entry) => entry.id)).toEqual([
      "inbox",
    ]);
  });

  it("partitions cleanly into the two sections", () => {
    const primary = shellNavSection("primary").map((entry) => entry.id);
    const workspace = shellNavSection("workspace").map((entry) => entry.id);

    expect(primary).toEqual(["inbox", "search"]);
    expect(workspace).toEqual(["people", "activity", "schema", "setup"]);
    // Nothing is in both, and nothing is in neither: the sidebar renders the two sections and
    // an entry belonging to no section would silently never be drawn.
    expect(primary.length + workspace.length).toBe(SHELL_NAV.length);
  });

  it("keeps Schema reachable", () => {
    // Asserted rather than left as a comment. docs/DESIGN.md 8.1's Workspace list does not name
    // Schema, and this entry is the schema editor's only entry point -- `/schema/new` is linked
    // only from `/schema` itself. Removing this entry to match that list literally would leave
    // both routes alive and neither navigable.
    expect(SHELL_NAV.map((entry) => entry.to)).toContain("/schema");
  });
});
