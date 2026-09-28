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
 * date. That is the reverse of everything above, and it is UTC rather than the viewer's
 * timezone, because a value crossing the API is one instant for everybody.
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
