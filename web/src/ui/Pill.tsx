/**
 * A select value, rendered as a status pill (docs/DESIGN.md 7.3).
 *
 * **Not `Badge`, and the difference is not cosmetic.** `Badge` carries a tone the
 * *code* chose — an admin scope, an alert severity — from a closed set of six names. A `Pill`
 * carries a tone nothing chose: it is a hash of a user-defined select option's key, and
 * the palette it maps into is docs/DESIGN.md 3's four status families plus the neutral default.
 * A badge is also a rectangle at `radius-card` with a border; 7.3's pill is a 24px capsule with
 * a fill and no border. Merging the two would mean one component whose tone means "the code
 * decided" on half its call sites and "a hash decided" on the other half.
 *
 * **The tone is decoration; the text carries the meaning** (docs/DESIGN.md 10, "kind is never
 * colour alone"). The pill always renders the option's display label, so a reader who cannot
 * distinguish the fills — or is reading the accessibility tree, where the fill does not exist —
 * loses nothing. Nothing about this component is conveyed by colour alone, which is why there is
 * no `aria-label` restating the tone: there is no tone to restate.
 *
 * **Geometry is unproven here.** 7.3's 24px height and 999px radius are written into the class
 * list (`h-6`, `rounded-full`) and are deliberately NOT asserted in this directory's tests:
 * `getBoundingClientRect` returns zeroes under jsdom, so a height assertion there passes against
 * zero (AGENTS.md, Traps). They get a real assertion from Playwright once a page renders one.
 */
import type { ComponentPropsWithoutRef } from "react";

import { cx } from "./cx";
import { selectTone, type PillTone } from "./vocabulary";

/**
 * docs/DESIGN.md 3's status-pill palette, one entry per tone `selectTone` can return.
 *
 * Fill and text only — 3 specifies the pill as `ok-soft/ok`, `warn-soft/warn`, `bad-soft/bad`
 * and `human-soft/human-ink`, with no border, and `sunk/ink-2` for an option the hash does not
 * colour. `human-ink` rather than `human` for the text is 3's own measured rule: `human` on
 * `human-soft` is 4.47:1 and misses the AA floor.
 */
const TONE_CLASSES: Record<PillTone, string> = {
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
  human: "bg-human-soft text-human-ink",
  neutral: "bg-sunk text-ink-2",
};

export interface PillProps extends Omit<ComponentPropsWithoutRef<"span">, "children" | "title"> {
  /** The option's display label. This is the pill's visible text and its whole meaning. */
  label: string;
  /**
   * The option's key. 7.3: "The pill shows the option's display label; its key is on hover."
   * It is also what chooses the tone (the key alone, so one value is one colour on every
   * screen). Absent or blank renders the neutral pill with no `title`.
   */
  optionKey?: string | null;
  /**
   * Overrides the hashed tone. For the schema-marked tone docs/DESIGN.md 3 defers to its own
   * issue, and for a caller that already knows the state (a proposal's verdict, say). Callers
   * rendering a plain select value pass no tone and let the hash decide.
   */
  tone?: PillTone;
}

export function Pill({ label, optionKey, tone, className, ...rest }: PillProps) {
  const resolved = tone ?? selectTone(optionKey);
  const key = typeof optionKey === "string" && optionKey.trim() !== "" ? optionKey : undefined;

  return (
    <span
      title={key}
      className={cx(
        "inline-flex h-6 items-center rounded-full px-2.5 text-xs font-medium",
        TONE_CLASSES[resolved],
        className,
      )}
      {...rest}
      // After the spread, not before: this is the hook the browser tests assert "is a pill"
      // with, and a call site that could overwrite it could make that assertion vacuous
      // without anything failing.
      data-testid="pill"
    >
      {label}
    </span>
  );
}
