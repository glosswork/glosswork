/**
 * `/activity`'s feed: one entry per write, at full width (docs/DESIGN.md 7.8, 8.7).
 *
 * **One entry is one write on one record.** `groupAuditEvents` keys on `(request_id, record_id)`
 * — a request id alone identifies an HTTP request, and a CSV import or a `write_batch` (DD-22)
 * is one request across many records, which on this screen would collapse an import into a
 * single entry with an unbounded number of pills.
 *
 * **No version number, and that is a refusal rather than an omission.** `auditGroups`' version
 * pass counts forward from a record's `create` event, which is exact only when the history has
 * been walked from its start; a cross-record keyset walk newest-first never is. docs/DESIGN.md
 * 8.7 says "events for one record version group into one entry", and on this screen that reads
 * as one *write*: the same sentence for every write that produces a version, and a defined
 * answer for the ones that do not. A number that would be right on the record page and wrong
 * here is worse than its absence.
 *
 * **The events arrive newest-first and each group is reversed before rendering.**
 * `GET /api/v1/audit-events` orders `id DESC`; `AuditWriteGroup.events` is documented as
 * ascending, and `summarizeWrite` reads `events[0]`.
 *
 * **Revert is per change.** 8.7 says "revert stays per entry"; an entry is now a write, and
 * there is no whole-write revert route — `revert_to_version` needs a version this screen does
 * not have. So the affordance sits on each change pill, which is the same one the table offered
 * per row, on exactly the events `isRevertible` already named.
 */
import { Link } from "react-router-dom";

import type { ObjectTypeSummary } from "../api/objectTypes";
import type { AuditEventDoc } from "../api/records";
import { summarizeWrite } from "../record-detail/auditGroups";
import { formatChangeValue } from "../record-detail/formatChangeValue";
import { ActivityEvent, type ActivityChange } from "../ui/ActivityEvent";
import { Button } from "../ui/Button";
import { handInputsFor } from "../ui/attributionDerivation";
import { btnSmClass } from "../ui/classes";
import { cx } from "../ui/cx";
import { feedGroups, isRevertible } from "./feedGroups";
import { objectTypeKeyForRecordKey } from "./objectTypeKeyForRecordKey";
import type { FieldsByType } from "./useFieldsByType";

/** The revert affordance is a subdued danger action: it undoes one audited change rather than
 * deleting anything, so it takes the quiet variant tinted with the danger tokens, exactly as the
 * record page's Activity card and the table this screen replaces both do. */
const quietDangerClass = cx(btnSmClass, "text-bad hover:bg-bad-soft hover:text-bad");

interface ActivityFeedProps {
  events: AuditEventDoc[];
  objectTypes: ObjectTypeSummary[];
  fieldsByType: FieldsByType;
  onRevert: (event: AuditEventDoc) => void;
  revertingEventId: number | null;
}

export function ActivityFeed({
  events,
  objectTypes,
  fieldsByType,
  onRevert,
  revertingEventId,
}: ActivityFeedProps) {
  return (
    <div data-testid="activity-feed">
      {feedGroups(events).map((group) => {
        const first = group.events[0];
        const objectTypeKey = objectTypeKeyForRecordKey(first.record_key, objectTypes);
        const fields = objectTypeKey ? (fieldsByType[objectTypeKey] ?? {}) : {};

        const changes: ActivityChange[] = group.events.filter(isRevertible).map((change) => {
          const field = fields[change.field_key as string];
          // `user_ref` and `relation` resolve an id through a sidecar the audit response does
          // not carry (the record page gets its from the record document), so formatting one
          // here would print a raw UUID -- the defect this screen exists to remove. They take
          // the "<Field> edited" shape instead, which is what `oldValue: null` renders.
          const formattable =
            field !== undefined && field.type !== "user_ref" && field.type !== "relation";
          return {
            id: String(change.id),
            fieldLabel: field?.name ?? (change.field_key as string),
            oldValue: formattable ? formatChangeValue(field, change.old_value) : null,
            newValue: formattable ? (formatChangeValue(field, change.new_value) ?? "") : "",
            action: (
              <Button
                type="button"
                variant="quiet"
                className={quietDangerClass}
                disabled={revertingEventId === change.id}
                onClick={() => onRevert(change)}
                aria-label={`Revert ${field?.name ?? change.field_key} on ${first.record_key}`}
              >
                Revert
              </Button>
            ),
          };
        });

        const hand = handInputsFor(first);

        return (
          <ActivityEvent
            key={`${group.requestId}:${group.recordId ?? "none"}`}
            data-testid={`activity-event-${first.id}`}
            hand={hand}
            timestamp={group.ts}
            isAgentAuthored={first.agent_label !== null}
            body={
              <span>
                {summarizeWrite(group)}
                {first.record_key !== null && (
                  <>
                    {" on "}
                    {objectTypeKey !== null ? (
                      <Link
                        className="font-mono text-xs text-human-ink hover:underline"
                        to={`/${objectTypeKey}/${first.record_key}`}
                      >
                        {first.record_key}
                      </Link>
                    ) : (
                      <span className="font-mono text-xs text-ink-2">{first.record_key}</span>
                    )}
                  </>
                )}
              </span>
            }
            changes={changes}
          />
        );
      })}
    </div>
  );
}
