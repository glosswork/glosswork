/**
 * docs/DESIGN.md 7.4's sentence chip, and the dashed add-chip.
 *
 * 7.4: filters render as sentence chips — `Stage is not Lost ×` — 28px tall, `999px` radius,
 * `line-2` border; the `+ Add filter` chip is dashed; clicking a chip opens a popover; group and
 * sort are the same grammar (`Group by Stage`, `Sort: Next action`).
 *
 * **The chip and the popover compose here, once.** A chip that opens a popover *is* the trigger
 * described in `Popover.tsx`, so this component hands `Popover` the chip recipe as its
 * `triggerClassName` and gets Escape, click-outside, focus-in, focus-return and `aria-expanded`
 * from it. That is the whole reason the composition lives in the primitive: that behaviour is
 * required on *every* popover control, and a rule obeyed at each call site is a
 * rule that is one call site away from being broken silently.
 *
 * **A chip is two controls, not one, which is what decides its DOM.** The sentence opens the
 * popover and the `×` removes the filter, and a button may not contain a button. So the border,
 * the radius and the height live on a wrapping `<span>` — the chip's frame — and the two
 * controls sit inside it. The frame stretches its children (`items-stretch`) so the hit area of
 * each control is the full 28px rather than the height of its text.
 *
 * **An open chip paints `human-soft`**, which is docs/DESIGN.md 3's rule for selection: "selected
 * row, active sidebar item, focused chip". The chip learns it is open from `Popover`'s
 * `onOpenChange` rather than owning the open state, so there is still exactly one copy of that
 * state and it is in `Popover`.
 *
 * **Geometry is unproven here.** 7.4's 28px height and 999px radius are in the class list
 * (`h-7`, `rounded-full`) and are deliberately not asserted in this directory: jsdom returns
 * zeroes from `getBoundingClientRect`, so such an assertion passes against zero (AGENTS.md,
 * Traps). They get a real assertion from Playwright once a page renders a chip row.
 */
import { useState } from "react";
import type { ReactNode } from "react";

import { cx } from "./cx";
import { Popover } from "./Popover";

/**
 * The frame: 7.4's height, radius and border. The text size is 13.5px (`text-sm`), which 7.4
 * does not specify — it is the size 7.1 gives the 28px `sm` button, and a chip row sits beside
 * those buttons in the toolbar.
 */
const FRAME = "inline-flex h-7 items-stretch rounded-full border text-sm";

/**
 * The chip's colours, **one class per property and never two**.
 *
 * A chip's state used to be layered: the variant's colours, then the open state's, then the
 * caller's `className`, all in one class list. Two utilities that set the same property do not
 * resolve by their order in that list. They resolve by their order in the generated stylesheet,
 * which nobody chooses, and here the variant's won both times. Measured in a browser: an open
 * chip's `bg-human-soft` lost to `bg-surface`, so no chip ever painted as open, and a refused
 * filter chip's `border-bad text-bad` lost to `border-line-2 text-ink`, so a filter the server
 * had refused looked exactly like one it had applied. Both sets of classes were in the DOM and
 * every test that read the class list passed.
 *
 * So each property is chosen once, from the chip's whole state, by `chipColours` below.
 * Precedence: a refusal owns the border and the text; open owns the fill, and the text unless
 * the chip is refused; the variant supplies whatever is left.
 */
const VARIANT_CLASSES = {
  /** A filter, group or sort chip: a condition that exists. */
  filter: { border: "border-line-2", text: "text-ink" },
  /** 7.4's `+ Add filter`: dashed, and quieter, because it names an absence. */
  add: { border: "border-dashed border-line-2", text: "text-ink-2" },
} as const;

/** `border-dashed` is a style, not a colour, so the add chip keeps it when it is refused. */
const DASHED = "border-dashed";

function chipColours(variant: ChipVariant, open: boolean, tone: ChipTone): string {
  const base = VARIANT_CLASSES[variant];
  const refused = tone === "refused";
  const border = refused ? cx(variant === "add" && DASHED, "border-bad") : base.border;
  // docs/DESIGN.md 3: a focused chip is `human-soft`, painted while its popover is open.
  const fill = open ? "bg-human-soft" : "bg-surface";
  const text = refused ? "text-bad" : open ? "text-ink" : base.text;
  return cx(border, fill, text);
}

/** The sentence itself, whether it is a button, a popover trigger, or plain text. */
const BODY = "inline-flex items-center rounded-full px-3";

export type ChipVariant = keyof typeof VARIANT_CLASSES;

/**
 * `refused`: the chip states something that was not accepted, such as a filter the server
 * would not apply. It is painted in `bad`, border and sentence. Colour is never the only
 * signal (docs/DESIGN.md 10): the caller says what was refused in words, on the page.
 */
export type ChipTone = "plain" | "refused";

export interface ChipProps {
  /** The sentence: `Stage is not Lost`, `Group by Stage`, `+ Add filter`. */
  children: ReactNode;
  variant?: ChipVariant;
  /** See `ChipTone`. A prop and not a `className`, because a colour passed as a class loses
   * to the chip's own (see the note above `VARIANT_CLASSES`). */
  tone?: ChipTone;
  /**
   * The chip's accessible name, when the sentence alone does not read as one. Passed to the
   * popover trigger or the button; ignored by a chip that is neither.
   */
  label?: string;
  /**
   * The popover's content. Given, the chip's sentence becomes a popover trigger and inherits
   * every keyboard and focus behaviour asserted in `Popover.test.tsx`. Receives `close` so a
   * control inside can commit and dismiss.
   */
  popover?: ReactNode | ((close: () => void) => ReactNode);
  /** Called on every open and close of that popover. */
  onOpenChange?: (open: boolean) => void;
  /** A chip that acts rather than opening a popover. Ignored when `popover` is given. */
  onClick?: () => void;
  /** Given, the chip renders 7.4's trailing `×`. */
  onRemove?: () => void;
  /**
   * The `×`'s accessible name. A row of chips is a row of identical `×`es otherwise, which is
   * the case docs/DESIGN.md 10's "every interactive element has a name" exists for: the name
   * says which filter it removes.
   */
  removeLabel?: string;
  /** Layout only. A colour class here loses to the chip's own; use `tone`. */
  className?: string;
  "data-testid"?: string;
}

export function Chip({
  children,
  variant = "filter",
  tone = "plain",
  label,
  popover,
  onOpenChange,
  onClick,
  onRemove,
  removeLabel = "Remove",
  className,
  "data-testid": testId,
}: ChipProps) {
  const [open, setOpen] = useState(false);
  const body = cx(BODY, onRemove && "pr-1.5");

  return (
    <span
      data-testid={testId}
      data-tone={tone === "plain" ? undefined : tone}
      className={cx(FRAME, chipColours(variant, open, tone), className)}
    >
      {popover ? (
        <Popover
          trigger={children}
          triggerLabel={label}
          triggerClassName={body}
          onOpenChange={(next) => {
            setOpen(next);
            onOpenChange?.(next);
          }}
        >
          {popover}
        </Popover>
      ) : onClick ? (
        <button type="button" aria-label={label} className={body} onClick={onClick}>
          {children}
        </button>
      ) : (
        <span className={body}>{children}</span>
      )}
      {onRemove && (
        <button
          type="button"
          aria-label={removeLabel}
          onClick={onRemove}
          className="inline-flex items-center rounded-full pl-1 pr-2.5 text-ink-2 hover:text-ink"
        >
          <span aria-hidden="true">&times;</span>
        </button>
      )}
    </span>
  );
}
