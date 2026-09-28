/**
 * The gloss panel (`docs/DESIGN.md` 7.11): a disclosure, not a popover, opening directly beneath
 * the thing it explains. Generalized out of `table-view/AboutDisclosure.tsx`, which is now a
 * thin call site passing "About" as its label; the record page's Details
 * card passes "?" per field. One implementation, one set of decisions, two callers.
 *
 * **Why a disclosure and not a popover.** The description is multi-line prose, and a transient
 * anchored surface is the wrong container for prose. A disclosure also keeps the description
 * reachable at every viewport, which DD-42 applied to viewports requires of anything that was
 * reachable at a wider one.
 *
 * **`aria-controls` is omitted while the panel is closed**, matching `Popover`: the panel is
 * unmounted rather than hidden, and an `aria-controls` pointing at an id that names no element
 * is worse than none.
 *
 * **It emits no heading of its own.** A panel that is unmounted while closed cannot carry a
 * page's outline, so a heading here would appear and disappear from that outline as the
 * disclosure opens and closes; the caller's own heading (or `h1`) stays the page's only one.
 *
 * **Three exports, one panel.** `GlossDisclosure` owns its own open state and renders both halves
 * adjacently, which is all the table page's title line needs. The Details card cannot use it: its
 * rows are a `130px 1fr` grid, and a panel rendered inside the label cell is trapped in a 130px
 * column, where the approved comp (`docs/design/counterpart-record-light.png`) shows it spanning
 * the full width of the row it explains. So the halves are exported too — `GlossToggle` and
 * `GlossPanel` — and that caller holds the state and places the panel as a grid item of its own.
 * The panel's markup exists once either way, which is the whole point of one primitive; what varies
 * is where it is allowed to sit.
 */
import { useId, useState } from "react";
import type { ReactNode } from "react";

export interface GlossToggleProps {
  /** The control's visible content — "About" on the table page, "?" per field on the record page. */
  label: ReactNode;
  /**
   * The control's accessible name, when `label` is not one.
   *
   * The table page's control reads "About", which names itself. The record page's reads "?",
   * which does not: a screen-reader user meeting four buttons all named "?" learns nothing about
   * which field each one explains. That caller passes `About <Field name>` here, and
   * `docs/DESIGN.md` 10's "every interactive element has a name" is then true of both.
   */
  ariaLabel?: string;
  open: boolean;
  onToggle: () => void;
  /** The panel's element id, referenced only while open. */
  panelId: string;
  testId?: string;
}

export function GlossToggle({
  label,
  ariaLabel,
  open,
  onToggle,
  panelId,
  testId = "gloss-toggle",
}: GlossToggleProps) {
  return (
    <button
      type="button"
      data-testid={testId}
      aria-label={ariaLabel}
      aria-expanded={open}
      aria-controls={open ? panelId : undefined}
      onClick={onToggle}
      className="cursor-pointer rounded-ctl px-1.5 py-0.5 text-sm text-ink-2 underline decoration-line-2 underline-offset-2 hover:text-ink"
    >
      {label}
    </button>
  );
}

export interface GlossPanelProps {
  id: string;
  /** The description shown when open (AGENTS.md: descriptions are required and agent-facing). */
  description: string;
  testId?: string;
  className?: string;
}

export function GlossPanel({
  id,
  description,
  testId = "gloss-panel",
  className,
}: GlossPanelProps) {
  return (
    <div
      id={id}
      data-testid={testId}
      // docs/DESIGN.md 2.3 lists the gloss panel with `--radius-card`, and 7.11 calls it a
      // `sunk` panel. `basis-full` so it drops below the control it sits beside rather than
      // stretching that line; a grid caller overrides the placement through `className`.
      className={className ?? "basis-full rounded-card bg-sunk px-3 py-2 text-sm text-ink-2 max-w-3xl"}
    >
      {description}
    </div>
  );
}

export interface GlossDisclosureProps {
  label: ReactNode;
  description: string;
  ariaLabel?: string;
  /** Test hook for the toggle button. */
  toggleTestId?: string;
  /** Test hook for the panel. */
  panelTestId?: string;
}

/**
 * Toggle and panel, adjacent, with the open state held here.
 *
 * **Test ids default to `gloss-toggle` / `gloss-panel` and are overridable.**
 * `AboutDisclosure` overrides both to `about-toggle` / `about-panel`, the names its own e2e and
 * unit tests already query (DD-41: surviving test ids keep their names); a caller that does not
 * care takes the default.
 */
export function GlossDisclosure({
  label,
  description,
  ariaLabel,
  toggleTestId = "gloss-toggle",
  panelTestId = "gloss-panel",
}: GlossDisclosureProps) {
  const [open, setOpen] = useState(false);
  const panelId = useId();

  return (
    <>
      <GlossToggle
        label={label}
        ariaLabel={ariaLabel}
        open={open}
        onToggle={() => setOpen((previous) => !previous)}
        panelId={panelId}
        testId={toggleTestId}
      />
      {open && <GlossPanel id={panelId} description={description} testId={panelTestId} />}
    </>
  );
}
