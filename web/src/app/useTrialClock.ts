import { useEffect, useState } from "react";

const TICK_MS = 1000;

/**
 * The current time in milliseconds, re-rendering its caller once a second.
 *
 * **Called from `TrialBanner` and from nowhere else.** Called from the shell it re-renders the
 * shell and every page under it once a second; inside the banner it re-renders one strip. With
 * no trial the banner is not mounted, so no timer exists at all.
 *
 * The caller recomputes from this reading on every tick rather than counting down, so a tab
 * that was asleep is right again on its first tick: nothing accumulates.
 */
export function useTrialClock(): number {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), TICK_MS);
    return () => window.clearInterval(timer);
  }, []);
  return nowMs;
}
