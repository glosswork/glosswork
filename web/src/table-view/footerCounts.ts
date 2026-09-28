/**
 * The two sentences `docs/DESIGN.md` 8.2 puts in the table's footer, as pure functions.
 *
 * 8.2: `"Showing 10 of 13 · 3 hidden by your filter"` left, `"4 rows last touched by an agent"`
 * right. `pageRangeLabel` already owns the first half; these own what 8.2 adds to it.
 *
 * **Why "hidden by your filter" is a subtraction of two separately-fetched numbers.**
 * `total_count` rides the query response and is computed from the **compiled, filtered**
 * query; `record_count` rides the object-type document and is the **unfiltered** live count. So
 * the difference is the answer, and it is the difference of two numbers fetched by two requests
 * at two different times — between them a record can be created or deleted, which is what makes
 * `max(0, …)` a rule rather than defensive arithmetic. This was chosen over a second unfiltered
 * query per render and over a read-path change to add the number server-side.
 *
 * **Why the agent sentence counts this page and says so.** Only the fetched page is knowable
 * client-side: the filtered set is not, and neither is the whole type. 8.2's wording,
 * "4 rows last touched by an agent", reads as a claim about the whole table, so the sentence is
 * worded as the page instead — "4 of these rows were last touched by an agent" — which is true
 * and needs no request. `updated_by_agent_label_id` is already on every row.
 */
import type { RecordDoc } from "../api/records";

/**
 * 8.2's `3` in "3 hidden by your filter".
 *
 * `recordCount` is the type document's unfiltered count; `totalCount` is the query response's
 * filtered one. Clamped at zero: the two are fetched separately, so a deletion between them can
 * make the subtraction negative, and "-1 hidden" is worse than saying nothing.
 */
export function hiddenByFilterCount(recordCount: number, totalCount: number): number {
  return Math.max(0, recordCount - totalCount);
}

/**
 * The clause itself, or `null` when there is nothing to say. The caller renders it **only when a
 * filter is active**: with no filter the two counts are the same number and "0 hidden"
 * is not news, and with a filter that hides nothing it is still not news.
 */
export function hiddenByFilterLabel(hidden: number): string | null {
  if (hidden <= 0) return null;
  return `${hidden} hidden by your filter`;
}

/** How many of the rows on this page were last written by an agent rather than by a person. */
export function agentTouchedCount(records: RecordDoc[]): number {
  return records.filter((record) => record.updated_by_agent_label_id !== null).length;
}

/**
 * The page's agent sentence, or `null` when no row on this page was. Singular is spelled out rather
 * than bolted on with "(s)": this line is prose, and 8.2 puts it on the screen as prose.
 */
export function agentTouchedLabel(count: number): string | null {
  if (count <= 0) return null;
  if (count === 1) return "1 of these rows was last touched by an agent";
  return `${count} of these rows were last touched by an agent`;
}

/**
 * The title line's count (`docs/DESIGN.md` 8.2: "the count and view name in `ink-3` on the same
 * line").
 *
 * **Which count, recorded because 8.2 says only "the count".** This is `record_count`, the type's
 * own unfiltered live count — the same number the sidebar shows beside the type's name (8.1) — not
 * the filtered `total_count`. Two readings were available; this one makes every number on the page
 * a different number: the title says how big the type is, and the footer says how much of it this
 * view is showing and how much the filter is holding back. Reading it as `total_count` instead is a
 * one-line change here.
 */
export function recordCountLabel(recordCount: number): string {
  return recordCount === 1 ? "1 record" : `${recordCount} records`;
}

/** The current view's name for the title line and the View menu's trigger. */
export function currentViewLabel(name: string | undefined | null): string {
  return name && name.trim() !== "" ? name : "Unsaved view";
}
