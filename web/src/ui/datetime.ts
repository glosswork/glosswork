/**
 * Timestamps as docs/DESIGN.md 5 writes them: `Today 09:14`, `Yesterday 17:02`, then `5 Sep`.
 *
 * **Without it a timestamp renders as raw ISO.** Measured before this module existed:
 * `grep -rn "toLocaleString\|Intl.DateTime" web/src` returned nothing, and the one non-test
 * render of a timestamp was `record-detail/CommentItem.tsx`, which printed `comment.created_at`
 * inside a `<time>` — so a person read `2026-09-10T21:47:21Z`.
 *
 * **It was first used on `/inbox` alone, deliberately.** Applying it to `CommentItem` at the same
 * time would have repainted two record-detail baselines for a screen that was not otherwise
 * changing. The module was written to be shared from the start, so each later adoption is an
 * import rather than a second implementation.
 *
 * **A pure function over an explicit `now`.** The relative words make this the one formatter in
 * the product whose output depends on when it is called, which is a test-determinism problem
 * before it is anything else: `now` is a parameter so the unit test can stand on a day boundary,
 * and the caller passes `new Date()`.
 *
 * Timezone is the viewer's, which is the only answer that makes "Today" mean anything. The
 * backend stores UTC at second precision (`timeutil.format_datetime`), so the same instant is
 * "Today" for one reader and "Yesterday" for another, and both are correct.
 *
 * **It also holds the other direction**: what this frontend *sends* when a person picks a
 * date. That is the reverse of everything above, and there are two answers, because a picked day
 * is asked two different questions:
 *
 * - **A deadline is UTC** (`endOfPickedDayUtc`, the token expiry picker). "Expires 31 January"
 *   has to be one instant for everybody, so the day is UTC's.
 * - **A filter over a column is the viewer's day** (`instantForLocalDay`). The table shows each
 *   timestamp in the viewer's timezone, so "before 7 Jan" has to cut where the viewer's 7 January
 *   starts, or the filter disagrees with the rows beside it by up to a day's worth of them.
 *
 * So a saved view holds an instant, and the same view opened in another timezone holds an
 * instant that is no longer a day boundary there. `localDayOfInstant` is what tells the two
 * apart, so a filter chip can say a day when it means one and a time when it does not.
 */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Local midnight for a date, which is what "the same day" has to mean for a relative word. */
function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function twoDigits(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

function clockTime(date: Date): string {
  return `${twoDigits(date.getHours())}:${twoDigits(date.getMinutes())}`;
}

/**
 * Parse a stored timestamp.
 *
 * The backend emits `2026-09-10T21:47:21Z`, which `Date` handles. An unparseable value returns
 * `null` and every function here then returns the original string: a screen showing a raw
 * timestamp is bad, and a screen showing `Invalid Date` or `NaN:NaN` is worse.
 */
function parse(value: string): Date | null {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * `Today 09:14`, `Yesterday 17:02`, `5 Sep`, `5 Sep 2025`.
 *
 * The year appears only outside the current one, per docs/DESIGN.md 5's rule for dates — a year
 * on every row is noise, and its absence on a row from two years ago is a lie.
 */
export function formatTimestamp(value: string, now: Date = new Date()): string {
  const date = parse(value);
  if (date === null) return value;

  const days = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (days === 0) return `Today ${clockTime(date)}`;
  if (days === 1) return `Yesterday ${clockTime(date)}`;

  const day = `${date.getDate()} ${MONTHS[date.getMonth()]}`;
  return date.getFullYear() === now.getFullYear() ? day : `${day} ${date.getFullYear()}`;
}

/**
 * The proposal detail's raised-at line (docs/DESIGN.md 8.4): "Raised today at 09:28".
 *
 * A separate function rather than string-concatenating around `formatTimestamp`, because the
 * sentence needs lowercase "today" mid-phrase and "at" between the day and the clock, and
 * assembling that at the call site is how two spellings of the same fact appear on one screen.
 *
 * A future timestamp falls through to the dated form rather than saying "today": clock skew
 * between a server and a browser is real, and "Raised tomorrow" is a sentence nobody should
 * ever read.
 */
export function formatRaisedAt(value: string, now: Date = new Date()): string {
  const date = parse(value);
  if (date === null) return `Raised ${value}`;

  const days = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (days === 0) return `Raised today at ${clockTime(date)}`;
  if (days === 1) return `Raised yesterday at ${clockTime(date)}`;

  const day = `${date.getDate()} ${MONTHS[date.getMonth()]}`;
  const dated = date.getFullYear() === now.getFullYear() ? day : `${day} ${date.getFullYear()}`;
  return `Raised ${dated} at ${clockTime(date)}`;
}

/**
 * The canonical timestamp a picked date sends: the end of that day UTC.
 *
 * `<input type="date">` has day granularity, so a chosen day has to become an instant. It is the
 * **end** of the day, because "Expires at 31 January" reads as "good through 31 January" and a
 * token that dies at that morning's midnight is dead for the whole day the person named. It is
 * **UTC**, so one picked date is one instant for everybody rather than one per viewer. The cost,
 * accepted deliberately: a viewer far enough east reads the day after the one they picked in a
 * rendered cell, because that cell renders in their own timezone.
 *
 * **It takes the picker's `YYYY-MM-DD` string and never a `Date`.** Built instead from a `Date`'s
 * local components, it returns 30 January for a picked 31 January in every timezone west of UTC;
 * `valueFormat.ts` documents the same trap. Routing through `Date` also throws `RangeError` on
 * the six-digit year `<input type="date">` accepts, which would reach the person as a generic
 * failure instead of the server's own `validation_failed`.
 *
 * The output is not validated here. An impossible date reaches the server and is refused there,
 * with the message that names the expected form, rather than being second-guessed in the browser.
 */
export function endOfPickedDayUtc(pickedDate: string): string {
  return `${pickedDate}T23:59:59Z`;
}

/**
 * Today's date in UTC as `YYYY-MM-DD`: the earliest date a picker may offer when what it sends is
 * `endOfPickedDayUtc`.
 *
 * The **UTC** date, not the viewer's local one. A viewer in New York at 21:00 is already on
 * tomorrow in UTC, so their local today would send an instant the server refuses as past, which
 * is the shape of the bug this exists to close. Built from UTC getters, so the answer does not
 * depend on where the browser is.
 */
export function todayUtc(now: Date = new Date()): string {
  const month = twoDigits(now.getUTCMonth() + 1);
  return `${now.getUTCFullYear()}-${month}-${twoDigits(now.getUTCDate())}`;
}

/** Which end of a calendar day: its first second, or its last (23:59:59). */
export type DayEdge = "first" | "last";

/** `YYYY-MM-DD`, ASCII digits only. `\d` would also match digits a server refuses. */
const CALENDAR_DAY = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/;
const CANONICAL_INSTANT = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/;

/** The earliest year a typed day may carry. Below it a four-digit year is a typo (`0026`), and
 * `Date` reads a year under 100 as 19xx when given as a number. */
const MIN_YEAR = 1000;

function canonicalInstant(ms: number): string {
  return `${new Date(ms).toISOString().slice(0, 19)}Z`;
}

/**
 * The numbers of a real calendar day written as `YYYY-MM-DD`, or `null`.
 *
 * Built from the day's own numbers and **checked by turning the result back into a day**:
 * `new Date(2026, 1, 31)` does not fail, it silently becomes 3 March, so a parse that succeeds
 * proves nothing. Also refused: a year below 1000, and anything that is not exactly four, two
 * and two ASCII digits, which covers the six-digit year `<input type="date">` allows.
 */
function calendarDayParts(day: string): [number, number, number] | null {
  const match = CALENDAR_DAY.exec(day);
  if (match === null) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const date = Number(match[3]);
  if (year < MIN_YEAR) return null;
  const built = new Date(year, month - 1, date);
  if (built.getFullYear() !== year || built.getMonth() !== month - 1 || built.getDate() !== date) {
    return null;
  }
  return [year, month, date];
}

/** Whether `day` is exactly `YYYY-MM-DD` and names a real calendar day from year 1000 on. */
export function isCalendarDay(day: string): boolean {
  return calendarDayParts(day) !== null;
}

/**
 * Whether `value` is exactly `YYYY-MM-DDTHH:MM:SSZ` and names a real instant: the one datetime
 * form the API stores, emits and accepts in a filter.
 */
export function isCanonicalInstant(value: string): boolean {
  if (!CANONICAL_INSTANT.test(value)) return false;
  const ms = Date.parse(value);
  // `Date.parse` rolls `2026-02-30T00:00:00Z` into March; the round trip is the real check.
  return !Number.isNaN(ms) && canonicalInstant(ms) === value;
}

/**
 * The canonical UTC instant at the first or the last second of a calendar day **in the viewer's
 * timezone**, or `null` when `day` is not a real day (`isCalendarDay`).
 *
 * Stored instants have whole seconds, so the last second of a day is exact: it is one second
 * before the next day starts, not `23:59:59` assembled from parts, which would be wrong on a day
 * that is 23 or 25 hours long.
 *
 * Where a zone's clocks skip midnight, `Date` resolves the missing local time forward, so the
 * first second of that day is the first one that exists.
 */
export function instantForLocalDay(day: string, edge: DayEdge): string | null {
  const parts = calendarDayParts(day);
  if (parts === null) return null;
  const [year, month, date] = parts;
  if (edge === "first") return canonicalInstant(new Date(year, month - 1, date).getTime());
  return canonicalInstant(new Date(year, month - 1, date + 1).getTime() - 1000);
}

/**
 * The viewer's calendar day that `instant` is the first or the last second of, or `null` when it
 * is neither: the inverse of `instantForLocalDay`.
 *
 * `null` is the ordinary answer for a view saved by a colleague in another timezone. Their
 * first second of 7 January is some hour of the 6th here, and that is what the chip should say.
 */
export function localDayOfInstant(instant: string, edge: DayEdge): string | null {
  if (!isCanonicalInstant(instant)) return null;
  const local = new Date(Date.parse(instant));
  const day = `${local.getFullYear()}-${twoDigits(local.getMonth() + 1)}-${twoDigits(local.getDate())}`;
  return instantForLocalDay(day, edge) === instant ? day : null;
}

/** `7 Jan`, or `7 Jan 2025` outside the current year: `formatTimestamp`'s own dated form. */
function datedWords(date: Date, now: Date): string {
  const day = `${date.getDate()} ${MONTHS[date.getMonth()]}`;
  return date.getFullYear() === now.getFullYear() ? day : `${day} ${date.getFullYear()}`;
}

/**
 * A calendar day in the viewer's timezone, in the words a datetime column uses for it: `7 Jan`,
 * `7 Jan 2025`. The value comes back unchanged when it is not a real day.
 */
export function formatLocalDay(day: string, now: Date = new Date()): string {
  const parts = calendarDayParts(day);
  if (parts === null) return day;
  return datedWords(new Date(parts[0], parts[1] - 1, parts[2]), now);
}

/**
 * An instant as day **and** time in the viewer's timezone: `6 Jan 21:00`. For a filter value
 * that is not a day boundary here, where the time is the part that makes the sentence true.
 * `formatTimestamp` drops the time for anything older than yesterday, which is right for a
 * column and wrong for a bound.
 */
export function formatInstantWithTime(value: string, now: Date = new Date()): string {
  const date = parse(value);
  if (date === null) return value;
  return `${datedWords(date, now)} ${clockTime(date)}`;
}
