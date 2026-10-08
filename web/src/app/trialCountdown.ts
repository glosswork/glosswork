/**
 * What the trial banner says, decided outside the component (AGENTS.md, non-negotiable 3).
 *
 * The workspace reports when its trial ends (`WorkspaceDoc.trial.ends_at`); the browser
 * subtracts its own clock from that. So this is a pure function of two instants, and what it
 * returns is a **length of time**, never a clock time or a date: no time zone and no
 * daylight-saving rule can change what the banner says.
 *
 * Named for what it computes and deliberately not `trialBanner.ts`: beside `TrialBanner.tsx`
 * that name differs only in the case of one letter, which does not build on macOS (TS1261)
 * and resolves differently on Linux.
 */

/** The banner's words, approved by the maintainer as customer-facing text. One constant, so
 * a wording change is one line here and one in `trialCountdown.test.ts`, which pins it. */
export const TRIAL_COPY = {
  /** The strip's accessible name, so a screen reader can find it among the page's regions. */
  regionLabel: "Trial",
  /** "Your trial has 23:59 left.": the time sits between these two. "Has ... left" and not
   * "ends in": "ends in 23:59" can be read as a time of day, one minute before midnight. */
  runningLead: "Your trial has",
  runningTail: "left.",
  /** Only that the trial ended. Not that the workspace is read-only: the freeze is the
   * operator's restart, which follows the end time by however long that takes. */
  ended: "Trial ended.",
  subscribe: "Subscribe",
} as const;

export type TrialCountdown =
  | {
      kind: "running";
      /** Hours and minutes left, as `HH:MM`, each at least two digits. */
      timeLeft: string;
      /** The same length of time for a screen reader: "23 hours 59 minutes left". */
      timeLeftInWords: string;
    }
  | { kind: "ended" };

const MS_PER_MINUTE = 60_000;
const MINUTES_PER_HOUR = 60;

function plural(count: number, unit: string): string {
  return `${count} ${count === 1 ? unit : `${unit}s`}`;
}

/**
 * The banner's state at `nowMs`, for a trial ending at `endsAt` (ISO 8601).
 *
 * - **Rounded up to the minute**, so a trial with 30 seconds left reads `00:01` and never
 *   `00:00`: `00:00` beside a workspace that still works reads as a broken banner.
 * - At the end time and after it, `ended`.
 * - Hours are never capped or wrapped: a hundred hours is `100:00`, so an operator who
 *   mistypes the year gets a banner that says so.
 * - `null` for an end time the browser cannot parse: no banner, rather than `NaN:NaN`.
 */
export function trialCountdown(endsAt: string, nowMs: number): TrialCountdown | null {
  const endMs = Date.parse(endsAt);
  if (Number.isNaN(endMs)) return null;
  const msLeft = endMs - nowMs;
  if (msLeft <= 0) return { kind: "ended" };

  const totalMinutes = Math.ceil(msLeft / MS_PER_MINUTE);
  const hours = Math.floor(totalMinutes / MINUTES_PER_HOUR);
  const minutes = totalMinutes % MINUTES_PER_HOUR;
  return {
    kind: "running",
    timeLeft: `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`,
    timeLeftInWords: `${plural(hours, "hour")} ${plural(minutes, "minute")} left`,
  };
}
