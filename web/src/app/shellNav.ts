/**
 * The shell's navigation entries, declared once (docs/DESIGN.md 8.1).
 *
 * **Why this is a module and not JSX in two components.** The sidebar at or above 960px and the
 * collapsed menu below it must offer the same set of destinations: DD-42's rule applied to
 * viewports (DD-41) is that nothing reachable at 1280 is unreachable at 800. Two hand-written
 * lists satisfy that on the day they are written and drift the first time someone adds a link to
 * one of them. One list is what makes `e2e/shell.spec.ts`'s menu comparison an assertion rather
 * than a claim.
 *
 * It is also where the business logic goes rather than into a render body (AGENTS.md
 * non-negotiable 3, frontend clause).
 *
 * **Object types are deliberately not in here.** The Tracking section comes from
 * `list_object_types` at run time and cannot be a constant; the sidebar composes these static
 * entries with that live list, and `e2e/shell.spec.ts` asserts the union survives the breakpoint
 * rather than only this half.
 */

/** Which block of the sidebar an entry belongs to (docs/DESIGN.md 8.1). `Tracking` is absent
 * because its entries are the live object types, not these. */
export type ShellNavSection = "primary" | "workspace";

export interface ShellNavEntry {
  /** Stable identity, for test ids and React keys. Never the label, which is copy. */
  id: string;
  /** The accessible name, and the visible text. */
  label: string;
  to: string;
  section: ShellNavSection;
  /**
   * Whether this entry can carry a count badge. Only `Inbox` does today. The count itself is
   * not here: it is data, fetched per render, and a constant that pretended otherwise is exactly
   * the "client-side guess" the shell must never make.
   */
  showsCount?: boolean;
}

/**
 * Notes on this list:
 *
 * - **`Inbox`** points at `/inbox`: the only thing the count counts is pending schema proposals,
 *   so the badge lands on the screen the badge is about.
 * - **`People & agents` and `Setup` are two entries, as docs/DESIGN.md 8.1 says**, each with its
 *   own route, so no two links point at one route and put two `aria-current="page"` elements on
 *   it.
 *
 * **`Schema` is in this list although 8.1's Workspace list omits it**, which 8.1 notes: this
 * entry is the schema editor's only entry point, and `/schema/new` is linked only from `/schema`
 * itself. Implementing that list literally would leave both routes alive and neither navigable.
 */
export const SHELL_NAV: readonly ShellNavEntry[] = [
  { id: "inbox", label: "Inbox", to: "/inbox", section: "primary", showsCount: true },
  { id: "search", label: "Search", to: "/search", section: "primary" },
  // 8.1's Workspace order is People & agents, Activity, Setup. `Schema` is an addition to
  // that list (see the note above), so it keeps the slot it already holds and Setup goes last.
  { id: "people", label: "People & agents", to: "/people", section: "workspace" },
  { id: "activity", label: "Activity", to: "/activity", section: "workspace" },
  { id: "schema", label: "Schema", to: "/schema", section: "workspace" },
  { id: "setup", label: "Setup", to: "/setup", section: "workspace" },
] as const;

/** The entries of one section, in declared order. A helper rather than a `.filter()` at each
 * call site, so the sidebar and the collapsed menu cannot disagree about what a section holds. */
export function shellNavSection(section: ShellNavSection): readonly ShellNavEntry[] {
  return SHELL_NAV.filter((entry) => entry.section === section);
}
