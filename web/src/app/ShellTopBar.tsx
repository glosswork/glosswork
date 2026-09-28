/**
 * The shell below the 960px breakpoint (docs/DESIGN.md 8.1 and 9).
 *
 * A top bar with a menu that reaches **every** destination the sidebar offers above the
 * breakpoint. That is DD-42's rule applied to viewports, which DD-41 adopts in terms: nothing is
 * hidden at a smaller width that was reachable at a larger one. It holds structurally rather
 * than by care, because this renders `ShellNavLists` — the same component the sidebar renders —
 * rather than a second copy of the list.
 *
 * **The control is the word `Menu`, not a glyph.** docs/DESIGN.md rule 4 says the design has no
 * icon set and that introducing one for mobile navigation is a separate decision. Answering that
 * silently inside a shell change is how a deferred decision stops being one.
 */
import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";

import type { CurrentPrincipal } from "../api/auth";
import type { ObjectTypeSummary } from "../api/objectTypes";
import type { WorkspaceDoc } from "../api/workspace";
import { Button } from "../ui/Button";
import { ThemeControl } from "../ui/ThemeControl";
import { ShellNavLists, SignedInBlock, WorkspaceBlock } from "./Sidebar";

export interface ShellTopBarProps {
  workspace: WorkspaceDoc | undefined;
  objectTypes: ObjectTypeSummary[];
  principal: CurrentPrincipal | null;
  pendingCount: number | null;
  onSignOut: () => void;
}

export function ShellTopBar({
  workspace,
  objectTypes,
  principal,
  pendingCount,
  onSignOut,
}: ShellTopBarProps) {
  const [open, setOpen] = useState(false);
  const { pathname } = useLocation();
  const firstRender = useRef(true);

  // Navigating closes the menu. Without this, following a link leaves the panel covering the
  // page it just moved to, which reads as a broken link rather than as an open menu. Skipped on
  // the first render so mounting on a route does not fight the initial state.
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    setOpen(false);
  }, [pathname]);

  return (
    <div
      data-testid="shell-top-bar"
      className="flex flex-col border-b border-line bg-ground"
    >
      <div className="flex items-center justify-between gap-2 pr-3">
        <WorkspaceBlock workspace={workspace} />
        <Button
          type="button"
          variant="quiet"
          className="px-2.5 py-1 text-sm"
          aria-expanded={open}
          aria-controls="shell-menu"
          onClick={() => setOpen((previous) => !previous)}
        >
          Menu
        </Button>
      </div>

      {open && (
        <div id="shell-menu" data-testid="shell-menu" className="border-t border-line pb-2">
          {/* The same component the sidebar renders, not a second list of the same links. */}
          <nav aria-label="Workspace navigation">
            <ShellNavLists objectTypes={objectTypes} pendingCount={pendingCount} />
          </nav>
          <div className="mt-2 border-t border-line px-3 py-2">
            <ThemeControl />
          </div>
          {principal && <SignedInBlock principal={principal} onSignOut={onSignOut} />}
        </div>
      )}
    </div>
  );
}
