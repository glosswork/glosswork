/**
 * Draining a forward-only keyset so a newest-first card can exist.
 *
 * **The problem, measured rather than assumed.** Both of the record page's streams
 * page *forward from the oldest row*: `sqlite.py:1260` orders history by `e.id` ascending and
 * walks with `e.id > :after_id`, `sqlite.py:881` orders comments by `created_at, rowid`
 * ascending, and each page holds at most 100 rows. So "render the first page, newest first" puts
 * **the oldest hundred events, reversed** at the top of any record busier than that, and the
 * write that actually happened most recently is several "Load more" clicks away.
 *
 * It is worse than a display defect. `auditGroups.ts` derives each write's version number by
 * counting forward from the record's creation, and `agentBar.ts` asks which fields the *most
 * recent* agent-authored version changed. Both answers are wrong on a partial walk, and wrong
 * quietly: the page would show a plausible version number and an agent bar beside the wrong
 * values.
 *
 * **So the walk is finished before anything is rendered.** The alternatives lose: reversing page
 * one (cheapest, wrong on exactly the records with the most history), and adding a descending order
 * to the history route (a backend change that would also force `auditGroups` to stop counting
 * forward, which is the property that makes its version numbers exact).
 *
 * **The walk is bounded** (DD-18: every input is bounded, and every bound is a named constant).
 * A record with a runaway history does not get to issue unbounded requests, and when the cap is
 * reached the caller is told so it can say as much, rather than silently presenting a truncated
 * history as a complete one.
 */
import { listComments, type CommentDoc } from "../api/comments";
import { getRecordHistory, type AuditEventDoc } from "../api/records";

/**
 * The most pages either walk will fetch.
 *
 * Ten pages is 1,000 audit rows or 1,000 comments at the server's own page sizes
 * (`services/comments.py::DEFAULT_COMMENT_LIMIT` is 100, and the history route's default
 * matches). That is far past any record a person reads top to bottom, and it bounds a cold load
 * at ten sequential round trips rather than an unknown number.
 */
export const MAX_ACTIVITY_PAGES = 10;

export interface RecordActivityPages {
  comments: CommentDoc[];
  events: AuditEventDoc[];
  /**
   * True when either walk stopped at the cap with a cursor still in hand.
   *
   * The card says so. An empty-handed truncation is the failure mode this flag exists to
   * prevent: a page showing 1,000 of 4,000 events and claiming nothing is a page that lies
   * about the record's history, and the version numbers and the agent bar it derives are then
   * wrong with no sign on screen that they might be.
   */
  truncated: boolean;
}

/**
 * Every comment and every audit event for one record, oldest first, both walks drained.
 *
 * Sequential rather than parallel *within* each walk, because a keyset cursor is only knowable
 * one page at a time; the two walks themselves run concurrently, since neither's cursor depends
 * on the other's.
 */
export async function fetchRecordActivityPages(ref: string): Promise<RecordActivityPages> {
  const [commentWalk, eventWalk] = await Promise.all([
    drainComments(ref),
    drainEvents(ref),
  ]);

  return {
    comments: commentWalk.rows,
    events: eventWalk.rows,
    truncated: commentWalk.truncated || eventWalk.truncated,
  };
}

interface Walk<T> {
  rows: T[];
  truncated: boolean;
}

async function drainComments(ref: string): Promise<Walk<CommentDoc>> {
  const rows: CommentDoc[] = [];
  let cursor: string | undefined;

  for (let page = 0; page < MAX_ACTIVITY_PAGES; page += 1) {
    const body = await listComments(ref, cursor === undefined ? {} : { cursor });
    rows.push(...body.comments);
    if (body.next_cursor === null) return { rows, truncated: false };
    cursor = body.next_cursor;
  }

  return { rows, truncated: true };
}

async function drainEvents(ref: string): Promise<Walk<AuditEventDoc>> {
  const rows: AuditEventDoc[] = [];
  let cursor: string | undefined;

  for (let page = 0; page < MAX_ACTIVITY_PAGES; page += 1) {
    const body = await getRecordHistory(ref, cursor === undefined ? {} : { cursor });
    rows.push(...body.events);
    if (body.next_cursor === null) return { rows, truncated: false };
    cursor = body.next_cursor;
  }

  return { rows, truncated: true };
}
