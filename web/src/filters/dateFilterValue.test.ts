/**
 * What a typed date commits, per operator, in a timezone west of UTC and one east of it.
 *
 * `process.env.TZ` is set per case, as `ui/valueFormat.test.ts` does: Node re-reads it on every
 * `Date` call, and a test that inherits the developer's zone passes or fails by accident. The
 * expected instants are written out as literals, worked from each zone's offset on that day,
 * never computed with the function under test.
 */
import { afterEach, describe, expect, it } from "vitest";

import {
  formatInstantWithTime,
  formatLocalDay,
  instantForLocalDay,
  isCalendarDay,
  isCanonicalInstant,
  localDayOfInstant,
} from "../ui/datetime";
import {
  commitDateFilterText,
  DATE_FILTER_HINT,
  dateFilterHint,
  dateFilterText,
  dateFilterWord,
  dayEdgeFor,
} from "./dateFilterValue";

const originalTz = process.env.TZ;
afterEach(() => {
  if (originalTz === undefined) delete process.env.TZ;
  else process.env.TZ = originalTz;
});

/** 7 January 2026 in each zone: New York is UTC-5 then, Kolkata UTC+5:30, and UTC is itself. */
const ZONES = [
  {
    tz: "America/New_York",
    first: "2026-01-07T05:00:00Z",
    last: "2026-01-08T04:59:59Z",
  },
  {
    tz: "Asia/Kolkata",
    first: "2026-01-06T18:30:00Z",
    last: "2026-01-07T18:29:59Z",
  },
  {
    tz: "UTC",
    first: "2026-01-07T00:00:00Z",
    last: "2026-01-07T23:59:59Z",
  },
] as const;

describe.each(ZONES)("a day typed into a datetime filter in $tz", ({ tz, first, last }) => {
  const commit = (op: string, bound: "lower" | "upper" | null = null) => {
    process.env.TZ = tz;
    return commitDateFilterText("datetime", op, bound, "2026-01-07");
  };

  it.each([
    ["lt", null, first],
    ["gte", null, first],
    ["lte", null, last],
    ["gt", null, last],
    ["between", "lower", first],
    ["between", "upper", last],
  ] as const)("%s (%s) commits the right second of the viewer's day", (op, bound, expected) => {
    expect(commit(op, bound)).toEqual({ value: expected, problem: null });
  });

  it.each(["eq", "neq"])("%s holds a bare day back and says why", (op) => {
    expect(commit(op)).toEqual({ value: undefined, problem: "needs-exact-time" });
  });

  it("turns each boundary back into the day, and nothing else into one", () => {
    process.env.TZ = tz;
    expect(localDayOfInstant(first, "first")).toBe("2026-01-07");
    expect(localDayOfInstant(last, "last")).toBe("2026-01-07");
    expect(localDayOfInstant(first, "last")).toBeNull();
    expect(localDayOfInstant(last, "first")).toBeNull();
    expect(localDayOfInstant("2026-01-07T12:34:56Z", "first")).toBeNull();
  });

  it("shows a reopened boundary as its day and reads it as a day on the chip", () => {
    process.env.TZ = tz;
    const now = new Date(2026, 5, 1);
    expect(dateFilterText("datetime", "lt", null, first)).toBe("2026-01-07");
    expect(dateFilterText("datetime", "lte", null, last)).toBe("2026-01-07");
    expect(dateFilterWord("lt", null, first, now)).toBe("7 Jan");
    expect(dateFilterWord("gt", null, last, now)).toBe("7 Jan");
    expect(dateFilterWord("between", "upper", last, now)).toBe("7 Jan");
    // The same instant under the operator whose day it is not: the time is what is true.
    expect(dateFilterText("datetime", "lte", null, first)).toBe(first);
    expect(dateFilterWord("lte", null, first, now)).toBe(formatInstantWithTime(first, now));
    expect(dateFilterWord("eq", null, first, now)).toBe(formatInstantWithTime(first, now));
  });
});

describe("a view saved in another timezone", () => {
  it("reads as day and time, because here it is not a day boundary", () => {
    // Saved in Kolkata as "is before 7 Jan": the first second of the 7th there.
    process.env.TZ = "America/New_York";
    const now = new Date(2026, 5, 1);
    expect(dateFilterWord("lt", null, "2026-01-06T18:30:00Z", now)).toBe("6 Jan 13:30");
    expect(dateFilterText("datetime", "lt", null, "2026-01-06T18:30:00Z")).toBe(
      "2026-01-06T18:30:00Z",
    );
  });

  it("carries the year outside the current one", () => {
    process.env.TZ = "UTC";
    const now = new Date(2027, 5, 1);
    expect(dateFilterWord("lt", null, "2026-01-07T00:00:00Z", now)).toBe("7 Jan 2026");
    expect(dateFilterWord("lt", null, "2026-01-07T09:05:00Z", now)).toBe("7 Jan 2026 09:05");
  });
});

describe("what the box refuses and what it passes through", () => {
  it.each([
    "2026-02-31",
    "2026-02-30",
    "2026-13-01",
    "26-01-07",
    "0999-01-07",
    "202600-01-07",
    "2026-1-7",
    "7 Jan 2026",
    "01/07/2026",
    "Yesterday 10:00",
    "2026-01-07T00:00",
    "2026-01-07T00:00:00.000Z",
    "2026-01-07T00:00:00+00:00",
    "2026-02-30T00:00:00Z",
    "2026-01-07T25:00:00Z",
    "2026-01-07T00:00:00z",
    "２０２６-０１-０７",
  ])("%s is not sent, on either field type", (typed) => {
    process.env.TZ = "America/New_York";
    for (const fieldType of ["date", "datetime"]) {
      expect(commitDateFilterText(fieldType, "lt", null, typed)).toEqual({
        value: undefined,
        problem: "not-a-date",
      });
    }
  });

  it("refuses a canonical instant on a date field, which holds days", () => {
    expect(commitDateFilterText("date", "lt", null, "2026-01-07T00:00:00Z").problem).toBe(
      "not-a-date",
    );
  });

  it("sends a day as typed on a date field, for every operator", () => {
    process.env.TZ = "Asia/Kolkata";
    for (const op of ["lt", "lte", "gt", "gte", "eq", "neq", "between"]) {
      expect(commitDateFilterText("date", op, op === "between" ? "upper" : null, "2026-01-07"))
        .toEqual({ value: "2026-01-07", problem: null });
    }
  });

  it("passes a canonical instant through for any operator", () => {
    for (const op of ["lt", "lte", "gt", "gte", "eq", "neq"]) {
      expect(commitDateFilterText("datetime", op, null, "2026-01-07T09:14:00Z")).toEqual({
        value: "2026-01-07T09:14:00Z",
        problem: null,
      });
    }
  });

  it.each(["@today-7d", "@now", "@tod", "@anything at all"])(
    "passes %s through untouched: the server judges tokens",
    (token) => {
      for (const fieldType of ["date", "datetime"]) {
        expect(commitDateFilterText(fieldType, "eq", null, token)).toEqual({
          value: token,
          problem: null,
        });
      }
      expect(dateFilterWord("lt", null, token)).toBe(token);
      expect(dateFilterText("datetime", "lt", null, token)).toBe(token);
    },
  );

  it("treats an empty box as missing, not as wrong", () => {
    expect(commitDateFilterText("datetime", "lt", null, "  ")).toEqual({
      value: undefined,
      problem: null,
    });
  });

  it("trims what was typed before judging it", () => {
    expect(commitDateFilterText("date", "lt", null, " 2026-01-07 ").value).toBe("2026-01-07");
  });

  it("names the accepted forms in every line it shows", () => {
    expect(dateFilterHint(null)).toBe(DATE_FILTER_HINT);
    expect(DATE_FILTER_HINT).toBe("A date like 2026-01-07, or a relative date like @today-7d.");
    expect(dateFilterHint("not-a-date")).toBe(
      "Not a date this filter understands. Use 2026-01-07 or @today-7d.",
    );
    expect(dateFilterHint("needs-exact-time")).toBe(
      '"is" and "is not" need an exact time here. For a whole day, use "is between" with the '
        + "same day twice.",
    );
  });

  it("gives no day edge to an operator that takes no day", () => {
    expect(dayEdgeFor("eq", null)).toBeNull();
    expect(dayEdgeFor("between", null)).toBeNull();
    expect(dayEdgeFor("is_null", null)).toBeNull();
  });
});

describe("the day arithmetic itself", () => {
  it("refuses a day that does not exist rather than rolling it forward", () => {
    process.env.TZ = "America/New_York";
    expect(isCalendarDay("2026-02-28")).toBe(true);
    expect(isCalendarDay("2024-02-29")).toBe(true);
    expect(isCalendarDay("2026-02-29")).toBe(false);
    expect(instantForLocalDay("2026-02-31", "first")).toBeNull();
    expect(instantForLocalDay("2026-02-31", "last")).toBeNull();
  });

  it("measures the last second from the next day's start, on a 23-hour and a 25-hour day", () => {
    process.env.TZ = "America/New_York";
    // 8 March 2026: clocks go forward, the day is 23 hours long (UTC-5 until 02:00, then UTC-4).
    expect(instantForLocalDay("2026-03-08", "first")).toBe("2026-03-08T05:00:00Z");
    expect(instantForLocalDay("2026-03-08", "last")).toBe("2026-03-09T03:59:59Z");
    // 1 November 2026: clocks go back, the day is 25 hours long.
    expect(instantForLocalDay("2026-11-01", "first")).toBe("2026-11-01T04:00:00Z");
    expect(instantForLocalDay("2026-11-01", "last")).toBe("2026-11-02T04:59:59Z");
  });

  it("starts a day whose midnight does not exist at its first second that does", () => {
    // Beirut moves its clocks forward at midnight: 29 March 2026 begins at 01:00 local,
    // which is 22:00 UTC the evening before (UTC+2 until then).
    process.env.TZ = "Asia/Beirut";
    expect(instantForLocalDay("2026-03-29", "first")).toBe("2026-03-28T22:00:00Z");
    expect(instantForLocalDay("2026-03-28", "last")).toBe("2026-03-28T21:59:59Z");
    expect(localDayOfInstant("2026-03-28T22:00:00Z", "first")).toBe("2026-03-29");
  });

  it("knows a canonical instant from everything that resembles one", () => {
    expect(isCanonicalInstant("2026-01-07T00:00:00Z")).toBe(true);
    expect(isCanonicalInstant("2026-02-30T00:00:00Z")).toBe(false);
    expect(isCanonicalInstant("2026-01-07T24:00:00Z")).toBe(false);
    expect(isCanonicalInstant("2026-01-07T00:00:00.000Z")).toBe(false);
    expect(isCanonicalInstant("2026-01-07")).toBe(false);
  });

  it("words a day the way a datetime column does", () => {
    process.env.TZ = "UTC";
    expect(formatLocalDay("2026-01-07", new Date(2026, 5, 1))).toBe("7 Jan");
    expect(formatLocalDay("2026-01-07", new Date(2027, 5, 1))).toBe("7 Jan 2026");
    expect(formatLocalDay("not a day")).toBe("not a day");
  });
});
