/**
 * Which values carry the agent bar (`docs/DESIGN.md` 3).
 *
 * 3 asks for "a 3px `agent` bar in the left gutter of the value, for the field(s) changed in the
 * record's most recent agent-authored version". Two things about that sentence are not obvious
 * until you try to implement it.
 *
 * **The record row cannot answer it.** `records.updated_by_agent_label_id` says the write that
 * last changed a value carried a label; it never says *which keys* that write touched. The
 * answer lives in the audit history, which is why this takes the grouped history rather than the
 * record.
 *
 * **"Changed in the most recent agent-authored version" is not the same as "an agent wrote the
 * value you are looking at".** If an agent moved `stage` and `notes` at version 2 and a
 * person then rewrote `notes` at version 3, the value of `notes` on screen is the person's. A
 * bar beside it would attribute a person's sentence to an agent, which is the one thing the
 * colour rule exists to get right — 3's whole premise is that amber means an agent did this. So
 * the set is the agent version's fields **minus every field a later version changed**.
 *
 * **The input must be the whole history, not one page.** `groupAuditEvents` derives versions by
 * counting forward from the record's creation, and the server pages forward from the oldest row,
 * so "most recent" is only correct once the cursor is drained. `useRecordActivity` does the
 * draining; this function trusts what it is handed and says so here rather than discovering it as a
 * bug on the first record with more than a hundred events.
 */
import type { AuditWriteGroup } from "./auditGroups";

/** The keys one write changed on the record itself. A `create` counts: the create is a value
 * change, and an agent that created the record wrote every value on it. */
function changedFieldKeys(group: AuditWriteGroup): string[] {
  return group.events
    .filter((event) => event.entity_type === "record" && event.field_key !== null)
    .map((event) => event.field_key as string);
}

/** True when the write carried an agent label. One write is one request id and one actor, so the
 * first event answers for the group — the same assumption `AuditTimeline` already makes when it
 * renders `handInputsFor(first)`. */
function isAgentAuthored(group: AuditWriteGroup): boolean {
  const first = group.events[0];
  return first !== undefined && first.agent_label_id !== null;
}

/**
 * The field keys whose values an agent wrote and that still stand.
 *
 * `groups` is the record's whole history, oldest first, as `groupAuditEvents` returns it. The
 * result is a `Set` because the only question asked of it is per-field membership, once per
 * rendered row.
 */
export function agentBarFieldKeys(groups: AuditWriteGroup[]): Set<string> {
  let index = -1;
  for (let i = groups.length - 1; i >= 0; i -= 1) {
    const group = groups[i];
    if (group.version !== null && isAgentAuthored(group)) {
      index = i;
      break;
    }
  }
  if (index === -1) return new Set();

  const overwritten = new Set<string>();
  for (const later of groups.slice(index + 1)) {
    if (later.version === null) continue;
    for (const key of changedFieldKeys(later)) overwritten.add(key);
  }

  return new Set(changedFieldKeys(groups[index]).filter((key) => !overwritten.has(key)));
}
