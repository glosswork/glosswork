/**
 * The Activity event (`docs/DESIGN.md` 7.8): the one component a comment and a version-producing
 * write both render through, on the record page's Activity card and at full width on
 * `/activity`.
 *
 * **Presentational only** (DD-3's frontend clause). Every value it prints — the timestamp, the
 * body, each change's old and new strings — arrives as a prop; it fetches nothing, formats no field
 * value, and decides nothing about which events exist. That is also why it must not assume the
 * record page: the Activity page renders the same component at the top of a cross-record feed,
 * where this file owns none of the surrounding layout.
 *
 * **The header is one `Hand`, never split into an avatar and a hand-written name.** 7.8 reads
 * "Header: `Hand`, then context ('for Sam'), then time right-aligned" — and `Hand` already
 * renders exactly that "for Sam Okafor" clause when an agent acts on a person's credential
 * (`docs/DESIGN.md` 6.2). Reproducing that text here, even from the same sidecar fields, would be
 * the second attribution primitive `oneAttributionPrimitive.test.ts` exists to refuse, so this
 * component takes no separate "context" prop: `hand` is the whole header, save for the time.
 *
 * **The grid is `24px 1fr` (7.8), and the header spans both columns.** `Hand`'s own footprint — a
 * 24px avatar plus its name — does not fit inside a 24px column, so the header row spans the full
 * grid width and sits flush with the card's edge; the body and its change pills start at column
 * two, indented to where the name began rather than under the avatar. That is what the record
 * page's reference still (`docs/design/counterpart-record-light.png`) shows: the first line runs
 * edge to edge, and everything beneath it is indented past the avatar rather than under it.
 *
 * **A change pill is not `ui/Pill.tsx`.** That component's tone is a hash of a select option's key
 * (docs/DESIGN.md section 3) and its shape is a 999px capsule sized for one short label. A
 * field-diff summary is not a select value — there is no option key to hash — and the reference
 * still shows a two-line entry ("Next action: 11 Sep → 13 Sep") that a capsule would draw as a
 * stretched stadium around wrapped text. So this component builds its own small chip, in `Pill`'s
 * neutral (`sunk`/`ink-2`) palette, without borrowing its shape or its tone logic. `oldValue: null`
 * collapses a pill to "`<Field>` edited" rather than an arrow with nothing on one side of it — the
 * shape the still uses for a change too long to diff usefully (a caller's choice; this component
 * does not decide when that is true).
 *
 * **The wash is a prop, not a derivation.** `isAgentAuthored` is passed rather than read off
 * `hand.agentLabel`: docs/DESIGN.md frames the feed wash (section 3) as keyed on whether the
 * *write* carried a label, a fact the caller already holds from the event data, not one this
 * presentational component re-derives.
 */
import type { ReactNode } from "react";
import { Hand, type HandProps } from "./Avatar";
import { formatTimestamp } from "./datetime";

/** `Hand`'s inputs, minus the props this component decides itself: the header always renders the
 * full hand (never `avatarOnly`) at its default size. */
export type ActivityHandInput = Pick<HandProps, "principal" | "agentLabel" | "fallbackId">;

/** One field this event's write changed, rendered as a chip beneath the body. */
export interface ActivityChange {
  /** Unique within the event; also what a caller's own per-change revert keys off. */
  id: string;
  /** The field's display label — never its API key (`docs/DESIGN.md` 5's vocabulary layer is the
   * caller's job, not this component's). */
  fieldLabel: string;
  /** Already formatted by the caller as display strings: this component holds no field-type
   * logic and does not stringify a value itself. `null` renders "`<Field>` edited". */
  oldValue: string | null;
  newValue: string;
  /** The pill's own revert control (the per-change affordance). */
  action?: ReactNode;
}

export interface ActivityEventProps {
  /** Rendered through `Hand`, never rebuilt from its fields directly. */
  hand: ActivityHandInput;
  /** ISO 8601. Rendered through `ui/datetime.ts`; the ISO form rides only in `<time dateTime>`
   * and on hover (`docs/DESIGN.md` 5), never as the visible text. */
  timestamp: string;
  /** A comment's body, or a one-line summary of a write ("Updated 2 fields") — the caller's
   * choice, since deciding which is business logic this component does not hold. */
  body: ReactNode;
  /** The fields this version's write changed, if any. Absent or empty renders no pills. */
  changes?: ActivityChange[];
  /** Keys section 3's feed wash. See the header comment for why this is a prop. */
  isAgentAuthored: boolean;
  /** The whole-event action ("Revert record to this version"). Absent on `/activity`. */
  action?: ReactNode;
  "data-testid"?: string;
}

/** Section 3: "the event row gets a wash of `agent-soft` mixed at 40% over `surface`." Painted as
 * an inline style rather than a Tailwind arbitrary-value class, matching `ProposalDetail.tsx`'s
 * own `style={{ gridTemplateColumns: ... }}` for a CSS value with nested parens and commas — a
 * literal transcription of section 3's formula is plainer than escaping it into a class name. */
const AGENT_WASH_STYLE = {
  backgroundColor: "color-mix(in srgb, var(--color-agent-soft) 40%, transparent)",
};

export function ActivityEvent({
  hand,
  timestamp,
  body,
  changes = [],
  isAgentAuthored,
  action,
  "data-testid": testId = "activity-event",
}: ActivityEventProps) {
  return (
    <article
      data-testid={testId}
      className="grid grid-cols-[24px_1fr] gap-x-3 gap-y-1 border-b border-line px-4 py-3"
      style={isAgentAuthored ? AGENT_WASH_STYLE : undefined}
    >
      <div className="col-span-2 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5">
        <Hand principal={hand.principal} agentLabel={hand.agentLabel} fallbackId={hand.fallbackId} />
        {/* `ml-auto` rather than a `justify-between` wrapper: the reference still wraps this row
            at narrower widths (an agent's "for X" clause is the long case), and `ml-auto` keeps
            the time flush right on whichever line it lands on, rather than only on a first line
            that no longer exists once the row wraps. */}
        <time
          className="ml-auto shrink-0 text-[12px] text-ink-3"
          dateTime={timestamp}
          title={timestamp}
        >
          {formatTimestamp(timestamp)}
        </time>
      </div>

      <div className="col-start-2 text-sm text-ink">{body}</div>

      {changes.length > 0 && (
        <div className="col-start-2 flex flex-wrap gap-1.5">
          {changes.map((change) => (
            <span
              key={change.id}
              data-testid={`activity-change-${change.id}`}
              className="inline-flex max-w-full items-center gap-1 rounded-card bg-sunk px-2.5 py-1.5 text-xs text-ink-2"
            >
              {change.oldValue !== null ? (
                <span>
                  {change.fieldLabel}: {change.oldValue}{" "}
                  {/* The arrow carries the whole relationship between the two values, so it
                      needs a word behind it: `aria-hidden` alone leaves a screen reader saying
                      "Stage: Proposal sent Negotiating", two values and no claim about which
                      replaced which. docs/DESIGN.md 10: kind is never carried by a glyph alone. */}
                  <span aria-hidden="true">{"→"}</span>
                  <span className="sr-only"> changed to </span>{" "}
                  <strong className="font-semibold text-ink">{change.newValue}</strong>
                </span>
              ) : (
                <span>{change.fieldLabel} edited</span>
              )}
              {change.action}
            </span>
          ))}
        </div>
      )}

      {action && <div className="col-start-2">{action}</div>}
    </article>
  );
}
