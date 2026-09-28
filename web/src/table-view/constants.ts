/**
 * Table-view tuning constants, named so the "refresh on an interval" and
 * "bounded page for human browsing" choices are visible in one place rather than inlined as
 * magic numbers.
 */

/** FR-U10: no websockets. The table view refetches its query on this fixed interval so it
 * eventually reflects other actors' writes without user action. */
export const TABLE_VIEW_POLL_INTERVAL_MS = 30_000;

/** A human browsing a table wants "the whole filtered set" more than an agent context-window
 * budget does; this is a page size, not a hard cap — the
 * `truncated` flag from `query_records` still drives an on-screen notice past this size. */
export const TABLE_VIEW_PAGE_SIZE = 200;
