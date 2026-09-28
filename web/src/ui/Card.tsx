/**
 * The card (`docs/DESIGN.md` 7.6): "`surface`, `line` border, radius `card`. Cards have a header
 * row (sans 600 14px, optional `ink-3` hint right-aligned) separated by a `line` rule. A table is
 * one card; rows are not cards."
 *
 * **A primitive because the record page puts two of them side by side**, and because a heading
 * placed *above* its bordered box rather than in a header row inside it reads as a section label
 * with a table under it, not as a card, and it is a structural difference from the approved comp
 * (`docs/design/counterpart-record-light.png`) rather than a pixel one — which `docs/DESIGN.md`'s
 * own preamble says is a defect.
 *
 * **The heading is an element, not a string, and the caller supplies it.** 7.6 gives the header a
 * size and a weight and says nothing about its level, because that depends on the page: on the
 * record page these are the `h2`s beneath the record's `h1`, and `heading-outline.spec.ts`
 * walks that outline. A card that hard-coded `h2` would be wrong on the first page that needs
 * something else, and one that emitted no heading at all would leave that walk with a single
 * heading and nothing to check — the vacuity 7.11's gloss panel is deliberately kept out of.
 *
 * **`aria-label` on the `<section>` rather than `aria-labelledby` pointing at the heading.** Both
 * name the region; the explicit label is what the e2e suite queries (`getByRole("region", { name:
 * "Details" })`) and it stays correct if a caller ever passes a heading with more in it than the
 * name.
 */
import type { ReactNode } from "react";
import { cx } from "./cx";

export interface CardProps {
  /** Names the landmark. Usually the same words as `heading`. */
  label: string;
  /** The header row's own heading element, at whatever level the page's outline needs. */
  heading: ReactNode;
  /** 7.6's optional right-aligned hint: what the card lets you do, or how it is ordered. */
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Card({ label, heading, hint, children, className }: CardProps) {
  return (
    <section
      aria-label={label}
      className={cx("rounded-card border border-line bg-surface", className)}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line px-3.5 py-2.5">
        {heading}
        {hint && <span className="text-xs text-ink-3">{hint}</span>}
      </div>
      {children}
    </section>
  );
}

/** 7.6's header text: sans 600 14px. Exported so every card's heading is the same words' worth of
 * weight and size whatever element the page needs it to be. */
export const cardHeadingClass = "text-sm font-semibold text-ink";
