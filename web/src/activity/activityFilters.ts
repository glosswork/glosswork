/**
 * `/activity`'s filter state: the six FR-U8 dimensions, as a flat set of chips.
 *
 * **This is deliberately not `filters/chipFilter.ts`.** That module converts between a flat AND
 * of sentence chips and the record **filter AST** — fields of one object type, an operator
 * vocabulary, a value whose cardinality depends on the operator — and compiles to JSON that
 * `filters.py` parses. These six are fixed query parameters on `GET /api/v1/audit-events`
 * (`record`, `principal_id`, `agent_label_id`, `object_type`, `field_key`, `since`/`until`) with
 * no operator and no field vocabulary at all. Sharing `ui/Chip.tsx` is right, because 7.4's
 * sentence chip is a shape; sharing the grammar would mean teaching an AST compiler about
 * `since`.
 *
 * **Two of the six are pickers, and that is the whole issue.** `principal_id` and
 * `agent_label_id` are UUIDs, and the screen this replaces asked for them as free text — a
 * filter that cannot be used by anyone who does not already know the answer. The chip's value is
 * chosen from a directory; the id still rides the wire.
 *
 * **A chip commits when it is complete, which is what replaces the debounce.** docs/DESIGN.md
 * 7.4: "the query does not run until the condition is complete". The screen this replaces
 * debounced four free-text inputs into the query key and left `since`/`until`
 * undebounced, so a half-typed date issued a request per keystroke. An incomplete chip is shown
 * in its popover and never in the chip row, and never reaches the network.
 */
import type { AuditSearchOptions } from "../api/audit";

/** The dimensions FR-U8 names, in the order the chip row offers them. */
export const ACTIVITY_DIMENSIONS = [
  "record",
  "person",
  "agent",
  "objectType",
  "field",
  "date",
] as const;

export type ActivityDimension = (typeof ACTIVITY_DIMENSIONS)[number];

/** What the row shows for a dimension that is not set, and what names it in the add menu. */
export const DIMENSION_LABELS: Record<ActivityDimension, string> = {
  record: "Record",
  person: "Person",
  agent: "Agent",
  objectType: "Type",
  field: "Field",
  date: "Date",
};

/**
 * One set filter. `value` is what rides the wire; `display` is what the chip's sentence says.
 *
 * The two are separate because for the two pickers they differ: the value is a UUID and the
 * display is a person's name or a label. Storing the display alongside the value is what lets
 * the chip render without re-reading a directory — and it is not a cache of a resolvable fact,
 * it is what the person chose.
 */
export interface ActivityFilterValue {
  value: string;
  display: string;
}

/** The date dimension is the one with two ends, and either may stand alone. */
export interface ActivityDateValue {
  since: string;
  until: string;
}

export interface ActivityFilters {
  record?: ActivityFilterValue;
  person?: ActivityFilterValue;
  agent?: ActivityFilterValue;
  objectType?: ActivityFilterValue;
  field?: ActivityFilterValue;
  date?: ActivityDateValue;
}

/** The dimensions currently set, in `ACTIVITY_DIMENSIONS` order. */
export function setDimensions(filters: ActivityFilters): ActivityDimension[] {
  return ACTIVITY_DIMENSIONS.filter((dimension) => filters[dimension] !== undefined);
}

/** The dimensions the `+ Add filter` chip offers, in the same order. */
export function unsetDimensions(filters: ActivityFilters): ActivityDimension[] {
  return ACTIVITY_DIMENSIONS.filter((dimension) => filters[dimension] === undefined);
}

/**
 * 7.4's sentence for one chip.
 *
 * The date chip reads as a sentence with one clause or two rather than as a pair of fields,
 * because "Date since 1 Sep" and "Date 1 Sep to 5 Sep" are things a person says and
 * `since=…&until=…` is not.
 */
export function chipSentence(
  dimension: ActivityDimension,
  filters: ActivityFilters,
): string {
  if (dimension === "date") {
    const date = filters.date;
    if (date === undefined) return DIMENSION_LABELS.date;
    if (date.since && date.until) return `Date ${date.since} to ${date.until}`;
    if (date.since) return `Date since ${date.since}`;
    return `Date until ${date.until}`;
  }
  const filter = filters[dimension];
  if (filter === undefined) return DIMENSION_LABELS[dimension];
  return `${DIMENSION_LABELS[dimension]} is ${filter.display}`;
}

/**
 * True when a dimension's draft is complete enough to run (7.4).
 *
 * A date needs at least one end; everything else needs a non-empty value. This is the gate that
 * replaced the debounce: an incomplete draft stays in the popover.
 */
export function isComplete(
  dimension: ActivityDimension,
  draft: ActivityFilterValue | ActivityDateValue | undefined,
): boolean {
  if (draft === undefined) return false;
  if (dimension === "date") {
    const date = draft as ActivityDateValue;
    return Boolean(date.since || date.until);
  }
  return Boolean((draft as ActivityFilterValue).value.trim());
}

/**
 * The filters as the audit route's query parameters.
 *
 * Every key is `undefined` when unset rather than an empty string, because `useAuditSearch`
 * puts each one in the query key: an empty string and `undefined` are two different cache
 * entries for the same question.
 */
export function toSearchOptions(filters: ActivityFilters): AuditSearchOptions {
  return {
    record: filters.record?.value || undefined,
    principalId: filters.person?.value || undefined,
    agentLabelId: filters.agent?.value || undefined,
    objectType: filters.objectType?.value || undefined,
    fieldKey: filters.field?.value || undefined,
    since: filters.date?.since || undefined,
    until: filters.date?.until || undefined,
  };
}
