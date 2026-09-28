/**
 * The date cases run in **`America/New_York`**, not in whatever zone the machine happens to be
 * in. That is the entire point: `new Date("2026-09-13").getDate()` is 12 there and 13 in
 * UTC, so a test that inherits the developer's zone passes or fails by accident. `process.env.TZ`
 * is set around each case and restored after it; Node re-reads it per `Date` operation, which is
 * what makes this work at all (verified on Node 22 while writing this file).
 */
import { afterEach, describe, expect, it } from "vitest";

import { formatDate, formatNumber, storedValueTitle } from "./valueFormat";

const originalTz = process.env.TZ;

afterEach(() => {
  if (originalTz === undefined) delete process.env.TZ;
  else process.env.TZ = originalTz;
});

function inTimeZone(tz: string): void {
  process.env.TZ = tz;
}

describe("formatDate", () => {
  const now = new Date(2026, 8, 11); // 11 Sep 2026, local

  it("reads a date-only value from its parts, west of Greenwich", () => {
    inTimeZone("America/New_York");
    // The trap, in one line: `new Date("2026-09-13")` is UTC midnight, which is the 12th here.
    expect(new Date("2026-09-13").getDate()).toBe(12);
    expect(formatDate("2026-09-13", now)).toBe("Sun 13 Sep");
  });

  it("says the same day east of Greenwich", () => {
    inTimeZone("Asia/Tokyo");
    expect(formatDate("2026-09-13", now)).toBe("Sun 13 Sep");
  });

  it("says the same day in UTC", () => {
    inTimeZone("UTC");
    expect(formatDate("2026-09-13", now)).toBe("Sun 13 Sep");
  });

  it("carries the weekday and no year inside the current year", () => {
    inTimeZone("America/New_York");
    expect(formatDate("2026-01-01", now)).toBe("Thu 1 Jan");
    expect(formatDate("2026-12-31", now)).toBe("Thu 31 Dec");
  });

  it("carries the year and no weekday outside the current year", () => {
    inTimeZone("America/New_York");
    // docs/DESIGN.md 5: `13 Sep 2025`. A year on every row is noise; its absence on a row from
    // last year is a lie.
    expect(formatDate("2025-09-13", now)).toBe("13 Sep 2025");
    expect(formatDate("2027-03-04", now)).toBe("4 Mar 2027");
  });

  it("does not fall a day back at the last instant of the year", () => {
    inTimeZone("America/New_York");
    // 1 Jan 2027 UTC-midnight is 31 Dec 2026 in New York, so a `new Date(iso)` implementation
    // prints "Thu 31 Dec" here — the current-year form — for a date in the next year.
    expect(formatDate("2027-01-01", now)).toBe("1 Jan 2027");
  });

  it("hands back anything it cannot read as a calendar day", () => {
    expect(formatDate("", now)).toBe("");
    expect(formatDate("not a date", now)).toBe("not a date");
    expect(formatDate("2026-02-31", now)).toBe("2026-02-31"); // rolls to 3 Mar if constructed
  });
});

/**
 * `docs/DESIGN.md` 5: dates on the record page carry a relative hint, `(in 6 days)`. The
 * argument is opt-in, so `describe("formatDate")` above — every case written before the hint
 * existed, unmodified — is what proves every existing caller's output is unchanged.
 */
describe("formatDate's relative hint (opt-in)", () => {
  const now = new Date(2026, 8, 11); // Fri 11 Sep 2026, local

  it("adds nothing when the caller does not ask for it", () => {
    expect(formatDate("2026-09-17", now)).toBe("Thu 17 Sep");
  });

  it("says 'in N days' for a future date", () => {
    expect(formatDate("2026-09-17", now, { relative: true })).toBe("Thu 17 Sep (in 6 days)");
  });

  it("says 'N days ago' for a past date", () => {
    expect(formatDate("2026-09-04", now, { relative: true })).toBe("Fri 4 Sep (7 days ago)");
  });

  it("says 'today', 'tomorrow' and 'yesterday' at the three near-term edges", () => {
    expect(formatDate("2026-09-11", now, { relative: true })).toBe("Fri 11 Sep (today)");
    expect(formatDate("2026-09-12", now, { relative: true })).toBe("Sat 12 Sep (tomorrow)");
    expect(formatDate("2026-09-10", now, { relative: true })).toBe("Thu 10 Sep (yesterday)");
  });

  it("still carries a hint outside the current year, beside the year form", () => {
    expect(formatDate("2025-09-13", now, { relative: true })).toBe("13 Sep 2025 (363 days ago)");
  });

  it("carries no hint a year or more off either way — a count, not a hint, at that range", () => {
    expect(formatDate("2027-09-20", now, { relative: true })).toBe("20 Sep 2027");
  });

  it("hands back the unparsed value unchanged, relative or not", () => {
    expect(formatDate("not a date", now, { relative: true })).toBe("not a date");
  });
});

describe("storedValueTitle", () => {
  it("gives an absent value no title at all, not an empty one", () => {
    expect(storedValueTitle(null)).toBeUndefined();
    expect(storedValueTitle(undefined)).toBeUndefined();
    expect(storedValueTitle("")).toBeUndefined();
  });

  it("hovers the raw stored string, not a formatted one", () => {
    expect(storedValueTitle("2026-09-13")).toBe("2026-09-13");
  });

  it("stringifies a number or a boolean", () => {
    expect(storedValueTitle(68000)).toBe("68000");
    expect(storedValueTitle(true)).toBe("true");
  });

  it("falls back to JSON for anything else, matching formatFieldValue's own fallthrough", () => {
    expect(storedValueTitle(["a", "b"])).toBe('["a","b"]');
  });
});

describe("formatNumber", () => {
  const amount = { type: "integer", config: { min: 0 } };
  const rate = { type: "decimal", config: { precision: 12, scale: 2 } };
  const unscaled = { type: "decimal", config: { precision: 12 } };

  it("groups an integer, which is the defect this fixes", () => {
    expect(formatNumber(amount, 68000)).toBe("68,000");
    expect(formatNumber(amount, 1234567)).toBe("1,234,567");
    expect(formatNumber(amount, 999)).toBe("999");
  });

  it("honours the field's own scale", () => {
    expect(formatNumber(rate, 1.5)).toBe("1.50");
    expect(formatNumber(rate, 68000)).toBe("68,000.00");
    expect(formatNumber(rate, "1.5")).toBe("1.50"); // the wire can carry a decimal as a string
    expect(formatNumber(rate, 2)).toBe("2.00");
  });

  it("invents no decimals for a field with no scale", () => {
    expect(formatNumber(unscaled, 1.5)).toBe("1.5");
    expect(formatNumber(unscaled, 68000)).toBe("68,000");
  });

  it("tolerates a field carrying no config at all", () => {
    expect(formatNumber({ type: "integer" }, 68000)).toBe("68,000");
    expect(formatNumber({ type: "decimal", config: null }, 1.5)).toBe("1.5");
  });

  it("keeps the sign outside the grouping", () => {
    expect(formatNumber(amount, -68000)).toBe("-68,000");
    expect(formatNumber(rate, -1.5)).toBe("-1.50");
  });

  it("carries no currency symbol (no field config can hold one)", () => {
    expect(formatNumber(amount, 68000)).not.toContain("$");
  });

  it("hands back anything it cannot read as a finite number", () => {
    expect(formatNumber(amount, "n/a")).toBe("n/a");
    expect(formatNumber(amount, null)).toBe("");
    expect(formatNumber(amount, undefined)).toBe("");
    expect(formatNumber(amount, Number.NaN)).toBe("NaN");
  });
});
