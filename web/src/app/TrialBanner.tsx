/**
 * The trial banner: how long a hosted trial has left and where to subscribe, then that the
 * trial has ended. One strip across the head of the main column, on every signed-in page.
 *
 * `Shell` mounts it only for a workspace document whose `trial` is an object, so a self-hosted
 * workspace renders none of this markup and runs no timer. Everyone signed in sees it, not
 * only administrators: a trial that ends freezes everyone's work. So nothing here reads a
 * role, a scope or an access level.
 *
 * **What it says is decided in `trialCountdown.ts`.** This file only lays the answer out.
 *
 * **A labelled region and not a live region**, so a screen reader can find it and is not
 * interrupted every minute. The visible sentence shows the time as digits; a screen reader is
 * given the same sentence with the time in words, as a second, visually hidden sentence. An
 * `aria-label` on the digits would not do it: measured in Chromium's accessibility tree, a
 * label on a plain `span` is not there at all, and the paragraph reads "23:59".
 *
 * **Not the access banner** (`access/ReadOnlyBanner.tsx`, DD-42), which is the only banner in
 * the human colour family. This one is `warn` while the trial runs and neutral once it has
 * ended.
 *
 * In the page's normal flow and not pinned: on a long page it scrolls away with the page
 * heading. The negative margins cancel `<main>`'s own padding (`px-6 py-5` in `App.tsx`) so the
 * strip runs edge to edge of the column, and the two must change together. Nothing about the
 * sidebar or the top bar is touched: the sidebar is exactly as tall as the window, so anything
 * placed above the shell adds its height to every page.
 */
import type { WorkspaceTrial } from "../api/workspace";
import { cx } from "../ui/cx";
import { TRIAL_COPY, trialCountdown } from "./trialCountdown";
import { useTrialClock } from "./useTrialClock";

const RUNNING_CLASS = "border-warn-line bg-warn-soft text-warn";
const ENDED_CLASS = "border-line bg-sunk text-ink-2";

export function TrialBanner({ trial }: { trial: WorkspaceTrial }) {
  const nowMs = useTrialClock();
  const countdown = trialCountdown(trial.ends_at, nowMs);
  if (countdown === null) return null;

  const running = countdown.kind === "running";
  return (
    <section
      aria-label={TRIAL_COPY.regionLabel}
      data-testid="trial-banner"
      className={cx(
        "-mx-6 -mt-5 mb-5 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b px-6 py-2 text-base",
        running ? RUNNING_CLASS : ENDED_CLASS,
      )}
    >
      {running ? (
        <p>
          <span aria-hidden="true" data-testid="trial-message">
            {TRIAL_COPY.runningLead}{" "}
            <span data-testid="trial-time-left" className="font-semibold tabular-nums">
              {countdown.timeLeft}
            </span>{" "}
            {TRIAL_COPY.runningTail}
          </span>
          <span className="sr-only" data-testid="trial-message-spoken">
            {TRIAL_COPY.runningLead} {countdown.timeLeftInWords}.
          </span>
        </p>
      ) : (
        <p data-testid="trial-message">{TRIAL_COPY.ended}</p>
      )}
      {trial.subscribe_url !== null && (
        <a
          href={trial.subscribe_url}
          data-testid="trial-subscribe"
          className="font-semibold underline underline-offset-2"
        >
          {TRIAL_COPY.subscribe}
        </a>
      )}
    </section>
  );
}
