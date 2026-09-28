/**
 * `/activity`'s feed, as data.
 *
 * Separate from `ActivityFeed.tsx` because business logic does not live in a component
 * (`AGENTS.md` non-negotiable 3, frontend clause) — and because `react-refresh/only-export-components`
 * enforces exactly that boundary for a file that also exports a component.
 */
import type { AuditEventDoc } from "../api/records";
import { groupAuditEvents, type AuditWriteGroup } from "../record-detail/auditGroups";

/**
 * The only audit rows a revert can target (DD-21's scope boundary): a field-level update on a
 * record, never a `create` row, a `link` event, or a `comment` event. Revert must never be
 * offered where the backend would refuse it.
 */
export function isRevertible(event: AuditEventDoc): boolean {
  return event.entity_type === "record" && event.action === "update" && event.field_key !== null;
}

/**
 * The feed's entries: newest first, each write's own events restored to ascending order.
 *
 * `GET /api/v1/audit-events` orders `id DESC`, while `AuditWriteGroup.events` is documented as
 * ascending and `summarizeWrite` reads `events[0]`. Without the reversal a two-event write would
 * be described by its last event — a link-then-unlink pair would read "Unlinked record".
 */
export function feedGroups(events: AuditEventDoc[]): AuditWriteGroup[] {
  return groupAuditEvents(events).map((group) => ({
    ...group,
    events: [...group.events].reverse(),
  }));
}
