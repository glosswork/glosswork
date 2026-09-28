import type { AuditEventDoc } from "../api/records";

export interface CommentBodyVersion {
  ts: string;
  body: string;
}

/**
 * Prior bodies of one comment, oldest first, reconstructed from its `entity_type: "comment"`
 * audit events (FR-C6). Comments are mutated in place, so the audit store is the only place a
 * prior body survives an edit: `add_comment`/`update_comment` both record the resulting body as
 * `new_value` on a `comment` entity event (`services/comments.py`).
 */
export function commentBodyVersions(
  events: AuditEventDoc[],
  commentId: string,
): CommentBodyVersion[] {
  return events
    .filter((event) => event.entity_type === "comment" && event.entity_id === commentId)
    .filter((event) => event.action === "create" || event.action === "update")
    .sort((a, b) => a.id - b.id)
    .map((event) => ({
      ts: event.ts,
      body: typeof event.new_value === "string" ? event.new_value : "",
    }));
}
