/**
 * An anchored, non-modal popover: a positioned element rendered from React state
 * (docs/DESIGN.md 7.4).
 *
 * **Why this is not `Dialog`.** `Dialog` is a native `<dialog>` opened with `showModal()`,
 * which is the right answer for a modal: the engine supplies focus containment, Escape,
 * `aria-modal`, the top layer and focus return, and none of it has to be written or tested
 * here (DD-41). A filter popover is the opposite shape — non-modal, anchored to the chip that
 * opened it, and dismissed by clicking somewhere else — so the top layer works against it
 * (`<dialog>` is not anchored and would have to be positioned by hand anyway) and the focus
 * containment that makes `showModal()` worth having is the one part it must not have.
 *
 * **Why not the platform Popover API.** jsdom 30.0.1 does not implement it: measured,
 * `'showPopover' in HTMLElement.prototype === false`. `test/setup.ts` already
 * shims `showModal`/`close` for the same reason, and a second shim would mean the component
 * suite asserting a browser behaviour against a hand-written imitation of it. React state needs
 * no shim and leaves the DOM load-bearing (DD-41), so `getByRole`/`getByLabel` keep working in
 * both suites.
 *
 * **What this component therefore owes, because no engine supplies it** (docs/DESIGN.md 10). Each
 * is asserted once here rather than at every chip:
 *
 * - Escape closes it, from anywhere — the listener is on `document`, because focus may be on the
 *   trigger, inside the panel, or (after a click) on neither.
 * - A click outside closes it. The listener is on `mousedown` rather than `click`, so a drag
 *   that starts inside the panel and ends outside does not dismiss it mid-gesture.
 * - Focus moves into the panel on open and **returns to the trigger** on close. Without the
 *   return, dismissing with Escape drops focus onto `<body>` and the keyboard user is back at
 *   the top of the document.
 * - The trigger carries `aria-expanded` in both states and `aria-controls` while the panel
 *   exists. `aria-controls` is omitted when closed rather than pointing at an id that names
 *   nothing: the panel is unmounted, not hidden.
 *
 * **The panel carries no ARIA role.** This is the disclosure shape — a button with `aria-expanded`
 * and the region it controls — and a filter popover is deliberately not a dialog. `role="dialog"`
 * would announce it as one. The accessible name lives on the trigger, where the reader is; the
 * panel's own contents carry theirs.
 *
 * **The panel stays inside the viewport.** It is anchored to its trigger's left edge, so a
 * trigger near the right edge of the window (the table's View menu) put a 256px panel 165px
 * past it. On opening, the panel is measured and shifted left by exactly what overhangs, and
 * never further than its own left edge allows, so a panel wider than the window stays reachable
 * from its start. One implementation here, so every popover in the product behaves the same
 * and no caller positions anything. It is measured when it opens and not again if the window is
 * resized while it is open.
 *
 * **Geometry is unproven here.** The offset, the width floor, `shadow-float` and the shift
 * above are not asserted in this directory: jsdom returns zeroes from `getBoundingClientRect`
 * (AGENTS.md, Traps). Position is a Playwright assertion (`e2e/table-pilot-findings.spec.ts`).
 */
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { cx } from "./cx";

/**
 * What counts as "the first thing in the panel" for focus-on-open. Deliberately the plain list
 * rather than a general tabbability test: the panel holds form controls and buttons, and a
 * dependency-free `:not([disabled])` selector is exactly right for those and honest about
 * covering nothing else. A panel with no focusable content at all falls back to the panel,
 * which is why it carries `tabIndex={-1}`.
 */
/** How close to the viewport's edge a shifted panel is allowed to sit, in pixels. */
const VIEWPORT_GUTTER = 8;

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

export interface PopoverProps {
  /** The trigger button's content. */
  trigger: ReactNode;
  /**
   * The trigger's accessible name, when its visible content does not already read as one
   * (docs/DESIGN.md 10: every interactive element has a name). Omitted, the content is the name.
   */
  triggerLabel?: string;
  /** Classes for the trigger button. `Chip` passes 7.4's chip recipe; other callers pass theirs. */
  triggerClassName?: string;
  /** Extra classes for the panel, appended to the recipe below. */
  className?: string;
  /** Test hook on the panel. */
  panelTestId?: string;
  /**
   * Called on every open and every close. This is the commit point: a filter
   * chip's popover commits its condition when it closes, and the chip learns that here rather
   * than by watching for a click.
   */
  onOpenChange?: (open: boolean) => void;
  /**
   * Panel content. Given `close` as a function argument so a control inside the panel (an
   * `Apply`, or Enter in a value field) can dismiss the popover without the call site having to
   * own the open state that this component already owns.
   */
  children: ReactNode | ((close: () => void) => ReactNode);
}

export function Popover({
  trigger,
  triggerLabel,
  triggerClassName,
  className,
  panelTestId = "popover-panel",
  onOpenChange,
  children,
}: PopoverProps) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  /**
   * Whether the panel was open the last time the focus effect ran. Focus returns to the trigger
   * on the open→closed *transition* rather than on "not open", because the first render is also
   * "not open" and must not pull focus out of wherever the page put it.
   *
   * It is a ref rather than a flag set by `close()` for a mechanical reason worth recording:
   * `close` is handed to the panel's children as a function argument, and `react-hooks/refs`
   * rejects passing a function that reads `ref.current` into the render tree. Reading the ref
   * inside the effect, which is where refs are allowed to be read, keeps `close` a pure state
   * update — and it also makes the focus return unconditional on closing, which is the
   * behaviour docs/DESIGN.md 10 asks for, rather than a property of which code path closed it.
   */
  const wasOpen = useRef(false);

  const close = useCallback(() => {
    setOpen(false);
    onOpenChange?.(false);
  }, [onOpenChange]);

  useEffect(() => {
    if (!open) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target)) return;
      // The trigger's own click toggles; closing here too would close and immediately reopen.
      if (triggerRef.current?.contains(target)) return;
      close();
    };

    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [open, close]);

  // A layout effect, so the shift is applied before the panel is first painted and before the
  // focus effect below scrolls anything into view. Written to the element's own style rather
  // than held in state: it is a correction to where the browser put the panel, read off the
  // DOM, and a state update here would render the panel twice to say the same thing.
  useLayoutEffect(() => {
    if (!open) return;
    const panel = panelRef.current;
    if (!panel) return;
    const viewportWidth = document.documentElement.clientWidth;
    if (viewportWidth === 0) return;
    const rect = panel.getBoundingClientRect();
    const overhang = rect.right - (viewportWidth - VIEWPORT_GUTTER);
    if (overhang <= 0) return;
    const room = Math.max(0, rect.left - VIEWPORT_GUTTER);
    panel.style.left = `${-Math.min(overhang, room)}px`;
  }, [open]);

  useEffect(() => {
    if (!open) {
      if (wasOpen.current) {
        wasOpen.current = false;
        triggerRef.current?.focus();
      }
      return;
    }
    wasOpen.current = true;
    const panel = panelRef.current;
    if (!panel) return;
    (panel.querySelector<HTMLElement>(FOCUSABLE) ?? panel).focus();
  }, [open]);

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        ref={triggerRef}
        aria-label={triggerLabel}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => {
          if (open) {
            close();
            return;
          }
          setOpen(true);
          onOpenChange?.(true);
        }}
        className={triggerClassName}
      >
        {trigger}
      </button>
      {open && (
        <div
          ref={panelRef}
          id={panelId}
          tabIndex={-1}
          data-testid={panelTestId}
          className={cx(
            "absolute left-0 top-full z-10 mt-1 min-w-56 rounded-card border border-line",
            "bg-surface p-3 text-ink shadow-float",
            className,
          )}
        >
          {typeof children === "function" ? children(close) : children}
        </div>
      )}
    </span>
  );
}
