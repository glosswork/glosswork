/**
 * Numbers and dates as docs/DESIGN.md 5 writes them, for the table cell.
 *
 * Measured before this module: a money column read `68000`, a date read `2026-09-13`. The cell
 * was printing the stored value, which is the defect this module fixes.
 *
 * **Timestamps are not here.** `ui/datetime.ts::formatTimestamp` already writes `Today 09:14` /
 * `Yesterday 17:02` / `5 Sep`, and the table cell consumes it rather than growing a second one. The
 * month names below are a deliberate second copy of a private array in that module, kept rather
 * than exported from `datetime.ts`. If a third formatter ever needs them, hoist them and delete
 * this paragraph.
 *
 * **No currency symbol.** docs/DESIGN.md 5 records that money is not formatted by "the field's
 * currency and locale (`$68,000`)", because **no field config can hold a currency**: `CONFIG_KEYS`
 * gives `decimal` exactly `{precision, scale}` and `integer` exactly `{min, max}`. A guessed `$` on
 * a euro column is worse than no symbol, so this formats grouping and the field's own `scale` and
 * nothing else; a currency field config is its own issue.
 *
 * **The separator is `,` and the decimal point is `.`, not the viewer's locale.** `Intl` would
 * make this module's output depend on the machine it runs on, which is the same determinism
 * problem `datetime.ts` avoided by writing its month names out. Locale-aware number formatting
 * belongs with the currency issue, where there is a field config to hang it on.
 *
 * **A pure function over an explicit `now`**, exactly as `datetime.ts` is, so the unit test can
 * stand on a year boundary rather than passing when the suite runs in December.
 *
 * **Two additions, both opt-in.** `formatDate`'s relative-hint argument
 * (`docs/DESIGN.md` 5: "with a relative hint ('in 6 days') on record pages") and
 * `storedValueTitle`, the same section's "any value that truncates carries the full stored value
 * on hover". Both are additions to the one formatter, not a second one: every existing caller of
 * `formatDate` passes no options and sees no change, which `valueFormat.test.ts`'s original
 * assertions are what prove.
 */
/** Just enough of a field to format its value. A `FieldDoc` satisfies it structurally (its
 * `type` is a `string` and its `config` a `Record<string, unknown>`); so does a fixture. */
export interface NumberFieldLike {
  type: string;
  config?: Record<string, unknown> | null;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** `decimal`'s `scale` (`fieldtypes.py::CONFIG_KEYS`), or `null` when the field has none. */
function scaleOf(field: NumberFieldLike): number | null {
  const raw = field.config?.["scale"];
  if (typeof raw !== "number" || !Number.isInteger(raw) || raw < 0 || raw > 20) return null;
  return raw;
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

/** Thousands separators into the integer part of an already-rendered digit string. */
function group(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/**
 * `68000` -> `68,000`; a `decimal` with `scale: 2` -> `1.50`.
 *
 * Anything this cannot read as a finite number comes back as its own string, unchanged. That
 * includes `null`, which comes back as the empty string: the em dash for an absent value is
 * `fieldDisplay.ts`'s `EMPTY_FIELD_VALUE` and this module does not invent a second one.
 */
export function formatNumber(field: NumberFieldLike, value: unknown): string {
  const numeric = toFiniteNumber(value);
  if (numeric === null) return value === null || value === undefined ? "" : String(value);
  // Past 1e21 `String` switches to exponent form, which grouping would mangle into nonsense.
  if (Math.abs(numeric) >= 1e21) return String(numeric);

  const scale = scaleOf(field);
  const rendered = scale === null ? String(numeric) : numeric.toFixed(scale);
  const negative = rendered.startsWith("-");
  const [whole, fraction] = (negative ? rendered.slice(1) : rendered).split(".");
  const body = fraction === undefined ? group(whole) : `${group(whole)}.${fraction}`;
  return negative ? `-${body}` : body;
}

/**
 * A stored `date` value read from its **parts**, never handed to `Date`'s string parser.
 *
 * This is a rule, and it is measured, not theoretical:
 *
 * ```
 * TZ=America/New_York  node -e 'new Date("2026-09-13").getDate()'  ->  12
 * TZ=UTC               node -e 'new Date("2026-09-13").getDate()'  ->  13
 * ```
 *
 * A `date` field stores a calendar day with no time and no zone. `Date` parses the ISO date-only
 * form as **UTC midnight**, so every reader west of Greenwich reads the day before — silently,
 * on every row, forever. `new Date(year, month - 1, day)` builds local midnight from integers
 * and has no such reading.
 *
 * `Sun 13 Sep` inside the current year, `13 Sep 2025` outside it (docs/DESIGN.md 5). The weekday
 * is only useful for a date near today, and the year is noise on every row until it is a lie on
 * one. An unparseable value comes back unchanged, as `datetime.ts` does with a bad timestamp:
 * a raw date on screen is bad and `Invalid Date` is worse.
 */
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})/;

export interface FormatDateOptions {
  /**
   * Appends docs/DESIGN.md 5's parenthetical — `(in 6 days)`, `(yesterday)` — for the record
   * page, the one screen that section names. `false` by default so every other caller (the table
   * cell, `MergeConflictDialog`, `groupLabel.ts`) keeps its exact output.
   */
  relative?: boolean;
}

/** Local midnight for a date, the same notion `ui/datetime.ts`'s own `startOfDay` is built from.
 * A second copy rather than an import, for the same reason as `MONTHS` (this module's header):
 * hoisting would mean exporting it from `datetime.ts`. */
function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/**
 * `docs/DESIGN.md` 5's parenthetical, or `null` for a date so far off that a day count would
 * read as noise rather than as a hint. There is no stated cutoff in the design, so this picks the
 * one the word "hint" implies: a year either way stops being a hint and starts being arithmetic
 * the reader has to do anyway, at which point the dated form beside it already says what a
 * reader needs.
 */
function relativeDayHint(date: Date, now: Date): string | null {
  const days = Math.round((startOfDay(date) - startOfDay(now)) / 86_400_000);
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  if (days > 365 || days < -365) return null;
  return days > 0 ? `in ${days} days` : `${-days} days ago`;
}

export function formatDate(
  value: string,
  now: Date = new Date(),
  options: FormatDateOptions = {},
): string {
  const match = DATE_ONLY.exec(value.trim());
  if (match === null) return value;

  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const date = new Date(year, month - 1, day);
  // Rejects `2026-02-31`, which the constructor rolls forward into March rather than refusing.
  if (date.getMonth() !== month - 1 || date.getDate() !== day) return value;

  const body = `${day} ${MONTHS[month - 1]}`;
  const dated = year === now.getFullYear() ? `${WEEKDAYS[date.getDay()]} ${body}` : `${body} ${year}`;
  if (!options.relative) return dated;

  const hint = relativeDayHint(date, now);
  return hint === null ? dated : `${dated} (${hint})`;
}

/**
 * The stored value as one line of hover text, or `undefined` when there is nothing to hover
 * (docs/DESIGN.md 5: "any value that truncates carries the full stored value on hover... the
 * stored value rather than the displayed one").
 *
 * `table-view/EditableCell.tsx` already has a function with this exact body, under the same
 * name, and this is deliberately a second copy rather than an import or an export of that one.
 * Both copies do nothing but classify a JSON value
 * into hover text, so there is no behaviour for the two to drift on.
 */
export function storedValueTitle(value: unknown): string | undefined {
  if (value === null || value === undefined) return undefined;
  if (typeof value === "string") return value === "" ? undefined : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}
