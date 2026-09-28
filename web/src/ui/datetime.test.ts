/**
 * The day boundary and a non-UTC timezone, because those are the
 * two things a relative timestamp gets wrong.
 *
 * Every case constructs its dates with the **local** constructor rather than parsing a `Z`
 * string, so the test asserts what a viewer in some timezone sees rather than what UTC says.
 * That is the distinction the module is about: the backend stores one instant, and "Today"
 * is a different answer for two readers looking at it from different places.
 */
import { describe, expect, it } from "vitest";

import { endOfPickedDayUtc, formatRaisedAt, formatTimestamp, todayUtc } from "./datetime";

/** A local-time ISO string with an explicit offset, so `Date` reads it as that wall clock. */
function localIso(year: number, month: number, day: number, hour: number, minute: number): string {
  return new Date(year, month - 1, day, hour, minute).toISOString();
}

describe("formatTimestamp", () => {
  const now = new Date(2026, 8, 10, 14, 0); // 10 Sep 2026, local

  it("says Today with a 24-hour clock", () => {
    expect(formatTimestamp(localIso(2026, 9, 10, 9, 14), now)).toBe("Today 09:14");
  });

  it("says Yesterday", () => {
    expect(formatTimestamp(localIso(2026, 9, 9, 17, 2), now)).toBe("Yesterday 17:02");
  });

  it("drops to a date beyond yesterday, with no year inside the current one", () => {
    expect(formatTimestamp(localIso(2026, 9, 5, 11, 30), now)).toBe("5 Sep");
  });

  it("carries the year outside the current one", () => {
    expect(formatTimestamp(localIso(2025, 9, 13, 11, 30), now)).toBe("13 Sep 2025");
  });

  it("is about calendar days, not elapsed hours", () => {
    // 23:59 yesterday and 00:01 today are two minutes apart and must not both say "Today".
    // A naive implementation subtracting milliseconds and dividing by 86,400,000 says they are
    // the same day, which is the bug this case exists for.
    const justBeforeMidnight = localIso(2026, 9, 9, 23, 59);
    const justAfterMidnight = localIso(2026, 9, 10, 0, 1);

    expect(formatTimestamp(justBeforeMidnight, now)).toBe("Yesterday 23:59");
    expect(formatTimestamp(justAfterMidnight, now)).toBe("Today 00:01");
  });

  it("pads both halves of the clock", () => {
    expect(formatTimestamp(localIso(2026, 9, 10, 6, 5), now)).toBe("Today 06:05");
  });

  it("returns an unparseable value unchanged rather than rendering Invalid Date", () => {
    expect(formatTimestamp("not a timestamp", now)).toBe("not a timestamp");
    expect(formatTimestamp("", now)).toBe("");
  });

  it("reads a real backend timestamp", () => {
    // `timeutil.format_datetime` emits exactly this shape, second precision, UTC.
    const rendered = formatTimestamp("2026-09-10T21:47:21Z", now);

    // Asserted as a shape, not as a letter: asserting the string contains no "T" fails
    // "Today 17:47" for the wrong reason.
    expect(rendered).not.toMatch(/\d{4}-\d{2}-\d{2}T/);
    expect(rendered).toMatch(/^(Today|Yesterday|\d{1,2} \w{3})/);
  });
});

describe("formatRaisedAt", () => {
  const now = new Date(2026, 8, 10, 14, 0);

  it("writes the sentence docs/DESIGN.md 8.4 asks for", () => {
    expect(formatRaisedAt(localIso(2026, 9, 10, 9, 28), now)).toBe("Raised today at 09:28");
  });

  it("lowercases the relative word mid-sentence", () => {
    expect(formatRaisedAt(localIso(2026, 9, 9, 17, 2), now)).toBe("Raised yesterday at 17:02");
  });

  it("keeps the clock when it drops to a date", () => {
    expect(formatRaisedAt(localIso(2026, 9, 5, 11, 30), now)).toBe("Raised 5 Sep at 11:30");
    expect(formatRaisedAt(localIso(2025, 9, 13, 11, 30), now)).toBe("Raised 13 Sep 2025 at 11:30");
  });

  it("never says a proposal was raised tomorrow", () => {
    // Clock skew between a server and a browser is real, and "Raised tomorrow" is a sentence
    // nobody should read. A future timestamp falls through to the dated form.
    const rendered = formatRaisedAt(localIso(2026, 9, 11, 9, 0), now);

    expect(rendered).not.toContain("tomorrow");
    expect(rendered).toBe("Raised 11 Sep at 09:00");
  });
});

/**
 * These two are the send side, so every case is written against UTC and no
 * case uses the local constructor: that is the whole difference from the block above.
 */
describe("endOfPickedDayUtc", () => {
  it("is the end of the picked day, in the canonical no-milliseconds form", () => {
    expect(endOfPickedDayUtc("2027-01-31")).toBe("2027-01-31T23:59:59Z");
    expect(endOfPickedDayUtc("2027-01-31")).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
  });

  it("is the same instant wherever the browser is", () => {
    // The case that fails for every implementation built from a `Date`'s local components: a
    // date-only string parses as UTC midnight, whose local day is the day before anywhere west
    // of UTC, so such a helper answers "2027-01-30" in New York and "2027-01-31" in Tokyo. This
    // one takes the picker's own string, so the ambient timezone cannot reach it. The assertion
    // holds under any `TZ`; running the file under `TZ=America/New_York` is what proves it.
    const dates = ["2027-01-31", "2026-12-31", "2026-01-01", "2026-03-08"];

    for (const picked of dates) {
      expect(endOfPickedDayUtc(picked)).toBe(`${picked}T23:59:59Z`);
    }
  });

  it("lets the server refuse an impossible date rather than throwing in the browser", () => {
    // `<input type="date">` accepts a six-digit year, and `new Date("275760-09-13")` throws
    // `RangeError: Invalid time value`, which reaches the person as "Could not mint the token."
    // A string built from the picker's value gets the server's own `validation_failed` instead.
    expect(() => endOfPickedDayUtc("275760-09-13")).not.toThrow();
    expect(endOfPickedDayUtc("275760-09-13")).toBe("275760-09-13T23:59:59Z");
  });
});

describe("todayUtc", () => {
  it("reads the UTC date, not the viewer's", () => {
    // 03:30 UTC is still the previous day for every viewer west of UTC, which is exactly the
    // case a local-getters implementation gets wrong. The instant is explicit, so this case
    // discriminates under `TZ=America/New_York` and passes unchanged under `TZ=UTC`.
    expect(todayUtc(new Date("2026-09-24T03:30:00Z"))).toBe("2026-09-24");
    // And the mirror: 21:00 UTC is already tomorrow for a viewer far enough east.
    expect(todayUtc(new Date("2026-09-24T21:00:00Z"))).toBe("2026-09-24");
  });

  it("pads the month and the day", () => {
    expect(todayUtc(new Date("2026-01-05T12:00:00Z"))).toBe("2026-01-05");
  });

  it("answers a date the picker can hold", () => {
    expect(todayUtc()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});
