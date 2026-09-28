/**
 * The Activity card's event list: one stream out of two.
 *
 * **A concatenation would double every comment.** `audit_events` already carries a row per comment
 * — a probe that made one comment, one edit and one delete got back three `entity_type: "comment"`
 * groups alongside the record's own. So a card that appended `comments` to
 * `groupAuditEvents(history)` would render each live comment twice: once with its body, once as a
 * bodyless row. Worse, a *deleted* comment leaves its three audit rows behind while dropping out of
 * `list_comments`, so the bodyless copy would outlive the thing it describes.
 *
 * The rule is therefore a **filter**, not a merge of equals:
 *
 * - every live comment, carrying its body, is one event;
 * - every write group that produced a record version is one event;
 * - everything else — comment groups, link and unlink groups, soft deletes — contributes
 *   nothing, because the comment groups are already represented by the comments and the rest
 *   produced no version to show.
 *
 * That makes the count exactly `comments.length + versionProducingGroups.length`, which is the
 * sentence the Activity card must make true, and `activityEvents.test.ts` is what pins it.
 *
 * **What the rule costs, stated rather than discovered later.** A link or unlink no longer
 * appears on the record page. It is still in `/activity` and still in the audit trail; it is not
 * lost, it is not shown here. That is chosen over a second kind of event whose body would read
 * "Linked record" and whose revert affordance does not exist.
 */
import type { CommentDoc } from "../api/comments";
import type { AuditEventDoc } from "../api/records";
import { groupAuditEvents, type AuditWriteGroup } from "./auditGroups";

/** A comment somebody wrote. The body is the event. */
export interface CommentActivityEvent {
  kind: "comment";
  /** `comment-<id>`, unique across both kinds. */
  id: string;
  ts: string;
  comment: CommentDoc;
}

/** One write that moved the record to a new version. Its field changes are the event. */
export interface VersionActivityEvent {
  kind: "version";
  /** `version-<requestId>`, unique across both kinds. */
  id: string;
  ts: string;
  group: AuditWriteGroup;
  /** Never null: a group with no version is not an event at all. */
  version: number;
}

export type ActivityEvent = CommentActivityEvent | VersionActivityEvent;

/** True for a write group the card renders: it moved the record to a version.
 *
 * `groupAuditEvents` sets `version` to null for every group that produced none — comments,
 * links, unlinks, soft deletes — and derives it by counting forward from the record's creation
 * (`auditGroups.ts`), which is exact only when the history has been walked from its start. That
 * is why `useRecordActivity` drains the cursor before calling this rather than reversing one
 * page. */
function producedAVersion(group: AuditWriteGroup): boolean {
  return group.version !== null;
}

/**
 * Both streams as one list, newest first.
 *
 * `comments` is the live thread (`list_comments`, deleted comments already absent) and `events`
 * is the record's audit history in the server's ascending order. Ties are broken by kind so the
 * order is total and the test is not hostage to two writes landing in the same second: a version
 * sorts above a comment written at the same instant, because the comment is usually *about* the
 * change.
 */
export function buildActivityEvents(
  comments: CommentDoc[],
  events: AuditEventDoc[],
): ActivityEvent[] {
  const merged: ActivityEvent[] = [];

  for (const comment of comments) {
    merged.push({
      kind: "comment",
      id: `comment-${comment.id}`,
      ts: comment.created_at,
      comment,
    });
  }

  for (const group of groupAuditEvents(events)) {
    if (!producedAVersion(group)) continue;
    merged.push({
      kind: "version",
      id: `version-${group.requestId}`,
      ts: group.ts,
      group,
      version: group.version as number,
    });
  }

  // **Sorted ascending, then reversed** — not sorted descending. The backend stores timestamps at
  // second precision (`timeutil.DATETIME_FORMAT`), so ties are the ordinary case rather than an
  // edge: one probe produced a create, an update and a comment all stamped
  // `02:45:03Z`. A descending comparator that returns 0 on a tie leaves those in the order they
  // were pushed, which is ascending — and the record page's first visual baseline showed exactly
  // that, a feed labelled "newest first" reading oldest first. Sorting ascending and reversing
  // makes every tie resolve the same way the untied case does, because both input streams arrive
  // ascending and `Array.prototype.sort` is stable.
  //
  // The one tie-break that is a judgement rather than an ordering fact: a version sorts *before*
  // a comment stamped in the same second, so after the reverse the comment sits above the change.
  // A comment written in the same second as a write is a response to it.
  merged.sort((a, b) => {
    if (a.ts !== b.ts) return a.ts < b.ts ? -1 : 1;
    if (a.kind === b.kind) return 0;
    return a.kind === "version" ? -1 : 1;
  });
  merged.reverse();
  return merged;
}

/** The number of events the card will render, without building them. Exported for the count
 * assertion, so the criterion reads the same arithmetic the card does rather than a constant. */
export function countActivityEvents(comments: CommentDoc[], events: AuditEventDoc[]): number {
  return comments.length + groupAuditEvents(events).filter(producedAVersion).length;
}
