import { describe, expect, it } from "vitest";

import { TRIAL_COPY, trialCountdown } from "./trialCountdown";

const ENDS_AT = "2030-01-02T00:00:00Z";
const END_MS = Date.parse(ENDS_AT);
const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;

function timeLeftAt(msBeforeEnd: number): string | null {
  const state = trialCountdown(ENDS_AT, END_MS - msBeforeEnd);
  return state !== null && state.kind === "running" ? state.timeLeft : null;
}

function wordsAt(msBeforeEnd: number): string | null {
  const state = trialCountdown(ENDS_AT, END_MS - msBeforeEnd);
  return state !== null && state.kind === "running" ? state.timeLeftInWords : null;
}

describe("trialCountdown", () => {
  it("reads 24:00 at exactly a day", () => {
    expect(timeLeftAt(24 * HOUR)).toBe("24:00");
  });

  it("reads 23:59 one minute in", () => {
    expect(timeLeftAt(24 * HOUR - MINUTE)).toBe("23:59");
  });

  it("rounds up to the minute, so 30 seconds left reads 00:01 and never 00:00", () => {
    expect(timeLeftAt(30 * SECOND)).toBe("00:01");
    expect(timeLeftAt(1)).toBe("00:01");
  });

  it("does not round a whole minute up to the next one", () => {
    expect(timeLeftAt(MINUTE)).toBe("00:01");
    expect(timeLeftAt(MINUTE + 1)).toBe("00:02");
    expect(timeLeftAt(HOUR)).toBe("01:00");
  });

  it("has ended at the end time and after it", () => {
    expect(trialCountdown(ENDS_AT, END_MS)).toEqual({ kind: "ended" });
    expect(trialCountdown(ENDS_AT, END_MS + 1)).toEqual({ kind: "ended" });
    expect(trialCountdown(ENDS_AT, END_MS + 365 * 24 * HOUR)).toEqual({ kind: "ended" });
  });

  it("shows more than 99 hours unpadded and uncapped", () => {
    expect(timeLeftAt(100 * HOUR)).toBe("100:00");
    expect(timeLeftAt(365 * 24 * HOUR)).toBe("8760:00");
  });

  it("yields nothing for an end time the browser cannot parse", () => {
    expect(trialCountdown("not-a-time", END_MS)).toBeNull();
    expect(trialCountdown("", END_MS)).toBeNull();
  });

  it("says the time left in words, with one hour and one minute in the singular", () => {
    expect(wordsAt(24 * HOUR - MINUTE)).toBe("23 hours 59 minutes left");
    expect(wordsAt(HOUR + MINUTE)).toBe("1 hour 1 minute left");
    expect(wordsAt(2 * HOUR + MINUTE)).toBe("2 hours 1 minute left");
    expect(wordsAt(HOUR + 2 * MINUTE)).toBe("1 hour 2 minutes left");
    expect(wordsAt(30 * SECOND)).toBe("0 hours 1 minute left");
    expect(wordsAt(24 * HOUR)).toBe("24 hours 0 minutes left");
  });
});

describe("TRIAL_COPY", () => {
  // The one place the approved words are written out beside the constant, so a change to the
  // constant is a change somebody made twice on purpose. Every other unit test reads the
  // constant; `e2e/trial-banner.spec.ts` writes the sentences out as a customer reads them.
  it("is the wording the maintainer approved", () => {
    expect(TRIAL_COPY).toEqual({
      regionLabel: "Trial",
      runningLead: "Your trial has",
      runningTail: "left.",
      ended: "Trial ended.",
      subscribe: "Subscribe",
    });
  });
});
