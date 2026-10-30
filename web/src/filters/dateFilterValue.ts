/**
 * What a person types into a `date` or `datetime` filter, and what the filter then carries.
 *
 * The server accepts exactly three things for these two field types (`filters.py`): a relative
 * token, `YYYY-MM-DD`, and for a `datetime` field `YYYY-MM-DDTHH:MM:SSZ`. Anything else it
 * refuses. This module decides what the box commits for each thing typed, so that a value the
 * server would refuse is held back with a line saying what is accepted, and never sent.
 *
 * **It is not a mirror of the server's rule, in one direction and on purpose.** Text starting
 * with `@` is passed through untouched: the token grammar lives on the server and is not copied
 * here (`completeness.ts`'s header says why a second copy is the thing to avoid), so a mistyped
 * token is the server's to refuse and the refusal comes back onto the chip.
 *
 * **A day on a `datetime` field becomes an instant chosen by the operator**, so that the
 * sentence on the chip is true. A day sent as its first second is right for "is before" and
 * wrong for three others: "is on or before 7 Jan" would stop at the first second of the 7th and
 * leave the whole day out, "is after 7 Jan" would include it, and "is 7 Jan" would match only a
 * record stamped at midnight to the second. So:
 *
 * | Operator | A day D becomes |
 * | --- | --- |
 * | `lt`, `gte`, the lower end of `between` | the first second of D |
 * | `lte`, `gt`, the upper end of `between` | the last second of D |
 * | `eq`, `neq` | nothing: held back, with a hint |
 *
 * The day is the **viewer's** calendar day (`ui/datetime.ts::instantForLocalDay`), because the
 * column being filtered shows its timestamps in the viewer's timezone.
 *
 * `eq` and `neq` with a bare day are the one place this module holds back a value the server
 * would accept. The server reads it as that day's first second in UTC, which is a single
 * second, and a chip reading "is 7 Jan" over zero rows is the wrong answer presented as right.
 */
import {
  formatInstantWithTime,
  formatLocalDay,
  instantForLocalDay,
  isCalendarDay,
  isCanonicalInstant,
  localDayOfInstant,
  type DayEdge,
} from "../ui/datetime";

/** Which end of a `between` a value is, or `null` for an operator that takes one value. */
export type RangeBound = "lower" | "upper" | null;

/** Why a typed value is not sent. `null` when it is sent, or when the box is simply empty. */
export type DateFilterProblem = "not-a-date" | "needs-exact-time";

export interface DateFilterCommit {
  /** What the filter carries. `undefined` is "do not send", which `completeness.ts` gates on. */
  value: string | undefined;
  problem: DateFilterProblem | null;
}

export const DATE_FILTER_HINT = "A date like 2026-01-07, or a relative date like @today-7d.";

const PROBLEM_TEXT: Record<DateFilterProblem, string> = {
  "not-a-date": "Not a date this filter understands. Use 2026-01-07 or @today-7d.",
  "needs-exact-time":
    '"is" and "is not" need an exact time here. For a whole day, use "is between" with the '
    + "same day twice.",
};

/** The line under the box: what is accepted, or why what was typed is not being sent. */
export function dateFilterHint(problem: DateFilterProblem | null): string {
  return problem === null ? DATE_FILTER_HINT : PROBLEM_TEXT[problem];
}

/** Which second of a day stands for the day under this operator, or `null` for none. */
const EDGE_BY_OPERATOR: Record<string, DayEdge> = {
  lt: "first",
  gte: "first",
  lte: "last",
  gt: "last",
};

export function dayEdgeFor(op: string, bound: RangeBound): DayEdge | null {
  if (op === "between") {
    if (bound === "lower") return "first";
    if (bound === "upper") return "last";
    return null;
  }
  return EDGE_BY_OPERATOR[op] ?? null;
}

function isToken(text: string): boolean {
  return text.startsWith("@");
}

/** What `text` commits for a field of `fieldType` under `op`. */
export function commitDateFilterText(
  fieldType: string,
  op: string,
  bound: RangeBound,
  text: string,
): DateFilterCommit {
  const typed = text.trim();
  if (typed === "") return { value: undefined, problem: null };
  if (isToken(typed)) return { value: typed, problem: null };

  if (fieldType === "date") {
    return isCalendarDay(typed)
      ? { value: typed, problem: null }
      : { value: undefined, problem: "not-a-date" };
  }

  if (isCanonicalInstant(typed)) return { value: typed, problem: null };
  if (!isCalendarDay(typed)) return { value: undefined, problem: "not-a-date" };
  const edge = dayEdgeFor(op, bound);
  if (edge === null) return { value: undefined, problem: "needs-exact-time" };
  return { value: instantForLocalDay(typed, edge) ?? undefined, problem: null };
}

/**
 * The day a committed `datetime` value stands for under this operator, or `null` when it is
 * not one: a token, an instant that is not this operator's boundary here, or anything else.
 */
function dayOfCommitted(op: string, bound: RangeBound, committed: string): string | null {
  const edge = dayEdgeFor(op, bound);
  return edge === null ? null : localDayOfInstant(committed, edge);
}

/**
 * The text the box shows for a committed value that was not typed into it: a saved view's, or
 * a chip being reopened. A boundary instant shows as its day; anything else shows as stored.
 */
export function dateFilterText(
  fieldType: string,
  op: string,
  bound: RangeBound,
  committed: unknown,
): string {
  if (typeof committed !== "string") return "";
  if (fieldType !== "datetime") return committed;
  return dayOfCommitted(op, bound, committed) ?? committed;
}

/**
 * A `datetime` filter value as a chip's sentence says it. An instant that is this operator's
 * boundary of a day for the viewer reads as the day alone; any other instant reads as day and
 * time, which is what a view saved in another timezone holds; a token reads as typed.
 */
export function dateFilterWord(
  op: string,
  bound: RangeBound,
  committed: string,
  now: Date = new Date(),
): string {
  if (!isCanonicalInstant(committed)) return committed;
  const day = dayOfCommitted(op, bound, committed);
  return day !== null ? formatLocalDay(day, now) : formatInstantWithTime(committed, now);
}
