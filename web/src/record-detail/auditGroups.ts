/**
 * One write is one row.
 *
 * `audit_events` is field-level: creating a twelve-field record appends twelve rows, so the
 * timeline showed twelve stacked entries before any later edit appeared. `audit_events.request_id`
 * plus `record_id` identify one write exactly, so grouping needs no heuristic and no clock
 * precision to reason about.
 *
 * **The request id alone does not.** A request id identifies an HTTP request:
 * `RecordService.write_batch` (DD-22) and a CSV import are one request across many records. That
 * is invisible on a record page, because every group there shares one record; the cross-record
 * feed is where it bites, and the key carries `record_id` rather than a second grouping
 * implementation existing.
 */
import type { AuditEventDoc } from "../api/records";

export interface AuditWriteGroup {
  requestId: string;
  /** The record this write touched, or `null` for an event about no record. Carried so a
   * cross-record feed can key on it; the record page's groups all share one. */
  recordId: string | null;
  /** The write's own events, in the server's ascending-id order. */
  events: AuditEventDoc[];
  /** The first event's timestamp; every event in one write shares it. */
  ts: string;
  /** The record version this write produced, or null for a write that produced none. */
  version: number | null;
}

/** True for an event that describes a change to one of the record's own fields. */
function isFieldChange(event: AuditEventDoc): boolean {
  return event.entity_type === "record" && event.field_key !== null;
}

/**
 * The events grouped into one entry per write, in the order they arrived, each carrying the
 * record version it produced.
 *
 * **The version rule, and why it is derivable.** `services/records.py` bumps `records.version`
 * in exactly two places — `update_record` and `bulk_update` — and `create_record` sets it to 1.
 * Nothing else does: comments, links, and soft deletes leave the version alone. So walking the
 * groups oldest-first, a group containing a `record`/`create` event produced version 1, a group
 * containing a `record`/`update` event produced the previous version plus one, and every other
 * group produced no version at all. `get_record_history` always starts at the record's earliest
 * event, so counting forward is exact even when later pages have not been fetched — which
 * counting backward from the loaded record's version would not be.
 *
 * Verified against the running service layer rather than read off the source: a create, an
 * update, a comment, a two-field update, and a comment delete produced actual versions
 * 1, 2, 2, 3, 3 and derived versions 1, 2, null, 3, null.
 */
export function groupAuditEvents(events: AuditEventDoc[]): AuditWriteGroup[] {
  const groups: AuditWriteGroup[] = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    // **The key is the request id AND the record**, which on this page is a distinction
    // without a difference and on `/activity` is not. A request id identifies an
    // HTTP request, not a write: `RecordService.write_batch` (DD-22) and a CSV import are one
    // request across many records, so on a cross-record feed the request id alone would
    // collapse an entire import into a single entry with an unbounded number of change pills.
    // Every group on the record page has one record id, so there the record adds nothing.
    if (
      last !== undefined &&
      last.requestId === event.request_id &&
      last.recordId === event.record_id
    ) {
      last.events.push(event);
    } else {
      groups.push({
        requestId: event.request_id,
        recordId: event.record_id,
        events: [event],
        ts: event.ts,
        version: null,
      });
    }
  }

  // The count is anchored on the record's creation. `get_record_history` always starts at the
  // record's earliest event, so page one carries it; if it somehow does not, there is nothing to
  // count from and every version stays null rather than being invented. A write with no version
  // offers no revert-to-version action, which is the correct refusal.
  const anchored = groups.some((group) =>
    group.events.some((event) => event.entity_type === "record" && event.action === "create"),
  );
  if (!anchored) return groups;

  let version = 0;
  for (const group of groups) {
    const created = group.events.some(
      (event) => event.entity_type === "record" && event.action === "create",
    );
    const updated = group.events.some(
      (event) => event.entity_type === "record" && event.action === "update",
    );
    if (created) {
      version = 1;
      group.version = version;
    } else if (updated) {
      version += 1;
      group.version = version;
    }
  }
  return groups;
}

/** The number of the record's own fields this write touched — what the collapsed row shows. */
export function fieldChangeCount(group: AuditWriteGroup): number {
  return group.events.filter(isFieldChange).length;
}

/** Past tense for each audit action. Spelled out rather than derived: appending "d" to the
 * action turns `link` into "Linkd". */
const ACTION_VERBS: Record<string, string> = {
  create: "Created",
  update: "Updated",
  delete: "Deleted",
  link: "Linked",
  unlink: "Unlinked",
};

/** The one-line description of a write, for the collapsed row. */
export function summarizeWrite(group: AuditWriteGroup): string {
  const fields = fieldChangeCount(group);
  if (fields > 0) {
    const verb = group.events.some((event) => event.action === "create") ? "Created" : "Updated";
    return `${verb} ${fields} ${fields === 1 ? "field" : "fields"}`;
  }
  const first = group.events[0];
  // A `link` event is about the record at the other end of the relation, not about an entity
  // called "link", so it reads as "Linked record".
  const noun = first.entity_type === "link" ? "record" : first.entity_type;
  const verb =
    ACTION_VERBS[first.action] ??
    `${first.action[0].toUpperCase()}${first.action.slice(1)}`;
  return `${verb} ${noun}`;
}
