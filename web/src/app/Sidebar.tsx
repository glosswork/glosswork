/**
 * The app shell's sidebar (docs/DESIGN.md 8.1 and 4.3).
 *
 * 224px on `ground`, at or above the 960px breakpoint; below it `ShellTopBar` takes over and
 * this is not rendered. Both draw their links from `shellNav.ts`, which is what makes "nothing
 * reachable at 1280 is unreachable at 800" (DD-42 applied to viewports) an assertion rather than
 * a promise.
 *
 * **It sits outside `<main>` and renders no heading element.** `heading-outline.spec.ts` walks
 * `main h1..h6` and `theme.spec.ts` reads `document.querySelector("h1")`; a workspace name marked
 * up as a heading would silently redirect the second and put a second `h1` in the document. The
 * name labels the navigation landmark through `aria-label` instead, which is what it is for.
 *
 * **It scrolls its own middle.** With eleven object types the Tracking list alone is taller than
 * a 800px viewport has room for, and the block it would push out is the signed-in person's --
 * which every spec in the suite waits on through `signInAs`. The workspace block and the person
 * block are pinned; only the navigation between them scrolls.
 */
import { NavLink } from "react-router-dom";

import tileUrl from "../brand/mark-tile.svg";
import type { ObjectTypeSummary } from "../api/objectTypes";
import type { CurrentPrincipal } from "../api/auth";
import type { WorkspaceDoc } from "../api/workspace";
import { Badge, type BadgeTone } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Hand } from "../ui/Avatar";
import { ThemeControl } from "../ui/ThemeControl";
import { cx } from "../ui/cx";
import { shellNavSection, type ShellNavEntry } from "./shellNav";
import {
  sidebarCountClass,
  sidebarLinkActiveClass,
  sidebarLinkClass,
  sidebarLinkRestClass,
  sidebarSectionLabelClass,
} from "../ui/classes";

/** One tone per system role. A
 * `Record` rather than a ternary, so a fourth role fails to compile here instead of rendering
 * as `neutral`. */
const ROLE_BADGE_TONE: Record<CurrentPrincipal["role"], BadgeTone> = {
  admin: "accent",
  creator: "info",
  member: "neutral",
};

export interface SidebarProps {
  workspace: WorkspaceDoc | undefined;
  objectTypes: ObjectTypeSummary[];
  principal: CurrentPrincipal | null;
  /** `null` when this caller's credential cannot read the proposals route: the badge is omitted
   * rather than drawn as a zero. */
  pendingCount: number | null;
  onSignOut: () => void;
}

/** The "6 people · 3 agents" line (docs/DESIGN.md 4.3).
 *
 * Set in `ink-2`, not the `ink-3` 4.3 originally named: measured against the shipped tokens,
 * `ink-3` on `ground` is 2.79:1 light and 4.00:1 dark, against the 4.5:1 floor 2.1 states for
 * any text pair. `ink-2` measures 5.50 and 7.73, and 4.3 names it.
 *
 * Singulars matter here because the line is a sentence, not a readout, and "1 people" is the
 * kind of thing a reader notices every single day.
 */
function peopleAndAgents(workspace: WorkspaceDoc): string {
  const people = `${workspace.people} ${workspace.people === 1 ? "person" : "people"}`;
  const agents = `${workspace.agents} ${workspace.agents === 1 ? "agent" : "agents"}`;
  return `${people} · ${agents}`;
}

function navLinkClassName({ isActive }: { isActive: boolean }): string {
  // The resting colour is a THIRD class, never folded into the base one: `text-human-ink` and
  // `text-ink-2` are both single-class selectors, so specificity ties and source order in the
  // built stylesheet decides the winner (`ui/classes.ts` explains the collision).
  return cx(sidebarLinkClass, isActive ? sidebarLinkActiveClass : sidebarLinkRestClass);
}

function ShellNavLink({ entry, count }: { entry: ShellNavEntry; count: number | null }) {
  return (
    <li>
      <NavLink to={entry.to} className={navLinkClassName} data-testid={`nav-${entry.id}`}>
        <span>{entry.label}</span>
        {entry.showsCount && count !== null && (
          <span data-testid="inbox-count" className={sidebarCountClass}>
            {count}
          </span>
        )}
      </NavLink>
    </li>
  );
}

/** The workspace block and the signed-in person's block are exported so `ShellTopBar` renders the
 * same markup below the breakpoint rather than a second version of it. */
export function WorkspaceBlock({ workspace }: { workspace: WorkspaceDoc | undefined }) {
  return (
    <div className="flex items-center gap-2.5 px-3 py-3">
      <img src={tileUrl} alt="" width={28} height={28} className="shrink-0 rounded-frame" />
      {/*
        The NAME and the COUNTS are independent; it is easy to make the counts wrongly
        conditional on the name. `GW_WORKSPACE_NAME` is optional by design (DD-28) -- an unnamed
        deployment renders the mark alone rather than falling back to the product name -- but the
        counts always exist, and hiding them because nobody named the workspace hides the line
        that carries the product's whole thesis. The visual project's server sets no name, so
        if the counts were hidden its contrast assertion would find no element to measure.
      */}
      {workspace !== undefined && (
        <span className="min-w-0">
          {workspace.name !== null && (
            <span
              data-testid="workspace-name"
              className="block truncate font-display text-[15px] font-semibold text-ink"
            >
              {workspace.name}
            </span>
          )}
          <span data-testid="workspace-people-agents" className="block truncate text-xs text-ink-2">
            {peopleAndAgents(workspace)}
          </span>
        </span>
      )}
    </div>
  );
}

export function SignedInBlock({
  principal,
  onSignOut,
}: {
  principal: CurrentPrincipal;
  onSignOut: () => void;
}) {
  return (
    <div
      className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-3 text-xs text-ink-2"
      data-testid="current-principal"
    >
      {/* The signed-in principal renders through the primitive like every other one.
          The test id stays on the element carrying the name, so the specs that address it are
          unaffected -- `signInAs` waits on the wrapper above in every spec in the suite. */}
      <span data-testid="current-principal-name">
        <Hand
          principal={{ display_name: principal.display_name, type: principal.type }}
          size="row"
        />
      </span>
      <Badge tone={ROLE_BADGE_TONE[principal.role]} data-testid="current-principal-role">
        {principal.role}
      </Badge>
      <Button
        type="button"
        variant="quiet"
        className="px-2 py-1 text-sm"
        onClick={onSignOut}
      >
        Sign out
      </Button>
    </div>
  );
}

/** The navigation itself: the two static sections plus the live Tracking list. Shared with
 * `ShellTopBar`'s menu, so a link cannot be added to one and forgotten in the other. */
export function ShellNavLists({
  objectTypes,
  pendingCount,
}: {
  objectTypes: ObjectTypeSummary[];
  pendingCount: number | null;
}) {
  return (
    <>
      <ul className="space-y-0.5 px-2">
        {shellNavSection("primary").map((entry) => (
          <ShellNavLink key={entry.id} entry={entry} count={pendingCount} />
        ))}
      </ul>

      <p className={sidebarSectionLabelClass}>Tracking</p>
      <ul className="space-y-0.5 px-2">
        {objectTypes.map((objectType) => (
          <li key={objectType.key}>
            <NavLink
              to={`/${objectType.key}`}
              className={navLinkClassName}
              data-testid={`nav-type-${objectType.key}`}
            >
              <span className="truncate">{objectType.name}</span>
              <span className={sidebarCountClass}>{objectType.record_count}</span>
            </NavLink>
          </li>
        ))}
      </ul>

      <p className={sidebarSectionLabelClass}>Workspace</p>
      <ul className="space-y-0.5 px-2">
        {shellNavSection("workspace").map((entry) => (
          <ShellNavLink key={entry.id} entry={entry} count={pendingCount} />
        ))}
      </ul>
    </>
  );
}

export function Sidebar({
  workspace,
  objectTypes,
  principal,
  pendingCount,
  onSignOut,
}: SidebarProps) {
  return (
    <nav
      data-testid="sidebar"
      // `aria-label` rather than a heading: see the file docstring. It names the workspace when
      // there is one, so a screen-reader user hears which deployment this navigation belongs to.
      aria-label={workspace?.name != null ? `${workspace.name} navigation` : "Workspace navigation"}
      // `w-56` is 224px, and Tailwind's preflight makes that the BORDER box, so the right-hand
      // rule is inside the 224 rather than a 225th pixel (docs/DESIGN.md 8.1).
      //
      // No `shell:` variant here: `Shell` renders this OR `ShellTopBar`, never both, because two
      // CSS-hidden shells would put two `current-principal` test ids in the DOM and fail
      // `signInAs` in every spec (`hooks/useIsWideViewport.ts`).
      className="flex h-screen w-56 shrink-0 flex-col border-r border-line bg-ground"
    >
      <WorkspaceBlock workspace={workspace} />

      {/* The only scrolling region. The blocks above and below it are pinned, so the sign-out
          control stays reachable however many object types this deployment has. */}
      <div className="min-h-0 flex-1 overflow-y-auto pb-2">
        <ShellNavLists objectTypes={objectTypes} pendingCount={pendingCount} />
      </div>

      <div className="border-t border-line px-3 py-2">
        <ThemeControl />
      </div>
      {principal && <SignedInBlock principal={principal} onSignOut={onSignOut} />}
    </nav>
  );
}

