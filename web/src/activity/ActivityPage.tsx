/**
 * `/activity` (docs/DESIGN.md 8.7): every write on this deployment, as a feed you can
 * filter by the person or the agent that made it.
 *
 * **Replaces `/audit`**, which asked for `Principal ID` and `Agent label ID` as free text. Both
 * are UUIDs, nothing on that screen told you one, and so in practice neither filter could be
 * used from the UI at all. The two are pickers now, over `GET /principals/directory` and
 * `GET /agent-labels/directory`; the id still rides the wire, but nobody has to know it.
 *
 * **Sort retired with the columns.** The old screen had eight sortable headers so a record's events
 * could be brought together; the record chip and the per-write grouping do that, and the sort
 * only ever ordered the pages already fetched, because the route takes no sort spec. What it
 * costs is written into 8.7 rather than left to be rediscovered: the feed can no longer be
 * ordered by surface or action.
 *
 * **Revert keeps every part of its behaviour** (DD-21, FR-D6): the same `revertFieldChange`
 * call, the same `expected_version` read, and the same `MergeConflictDialog` on a 409, which is
 * the existing merge prompt rather than a second conflict UI.
 */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { revertFieldChange } from "../api/audit";
import { getRecord, updateRecord, type AuditEventDoc } from "../api/records";
import { useAuditSearch } from "../hooks/useAuditSearch";
import { useObjectTypes } from "../hooks/useObjectTypes";
import { MergeConflictDialog } from "../table-view/MergeConflictDialog";
import { parseVersionConflict } from "../table-view/apiErrors";
import type { ConflictDetails, ResubmitPayload } from "../table-view/mergeConflict";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { EmptyState } from "../ui/EmptyState";
import { Spinner } from "../ui/Spinner";
import { btnSmClass } from "../ui/classes";
import { ActivityFeed } from "./ActivityFeed";
import { ActivityFilterChips } from "./ActivityFilterChips";
import { toSearchOptions, type ActivityFilters } from "./activityFilters";
import { useFieldsByType } from "./useFieldsByType";

interface RevertConflict {
  event: AuditEventDoc;
  conflict: ConflictDetails;
}

export function ActivityPage() {
  const [filters, setFilters] = useState<ActivityFilters>({});
  const [conflict, setConflict] = useState<RevertConflict | null>(null);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  // `cause` carries the caught `ApiError` so `Alert` renders its FR-A4 envelope beside the fixed
  // `title`: the title stays the same fixed string this flow has always shown.
  const [error, setError] = useState<{ title: string; cause: unknown } | null>(null);
  const [revertingEventId, setRevertingEventId] = useState<number | null>(null);

  const queryClient = useQueryClient();
  const { data: objectTypes } = useObjectTypes();
  const { data, isLoading, isError, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useAuditSearch(toSearchOptions(filters));

  const events = data?.pages.flatMap((page) => page.events) ?? [];
  const fieldsByType = useFieldsByType(events, objectTypes ?? []);

  const invalidateAfterRevert = (recordId: string | null) => {
    void queryClient.invalidateQueries({ queryKey: ["audit-events"] });
    if (recordId) {
      void queryClient.invalidateQueries({ queryKey: ["records", recordId] });
      void queryClient.invalidateQueries({ queryKey: ["record-history", recordId] });
    }
  };

  const handleRevert = async (event: AuditEventDoc) => {
    setError(null);
    setConfirmation(null);
    setRevertingEventId(event.id);
    try {
      const record = await getRecord(event.record_id as string, {});
      await revertFieldChange(event.id, record.version);
      setConfirmation(`Reverted event ${event.id}.`);
      invalidateAfterRevert(event.record_id);
    } catch (caught) {
      const nextConflict = parseVersionConflict(caught);
      if (nextConflict) {
        setConflict({ event, conflict: nextConflict });
      } else {
        setError({ title: `Could not revert event ${event.id}.`, cause: caught });
      }
    } finally {
      setRevertingEventId(null);
    }
  };

  const handleResubmit = async (payload: ResubmitPayload) => {
    if (!conflict) return;
    try {
      await updateRecord(conflict.event.record_id as string, {
        values: payload.values,
        expected_version: payload.expected_version,
        force: false,
      });
      setConfirmation(`Reverted event ${conflict.event.id}.`);
      invalidateAfterRevert(conflict.event.record_id);
      setConflict(null);
    } catch (caught) {
      const nextConflict = parseVersionConflict(caught);
      setConflict(nextConflict ? { event: conflict.event, conflict: nextConflict } : null);
    }
  };

  return (
    <section className="max-w-4xl space-y-4">
      <h1 className="font-display text-2xl font-semibold text-ink">Activity</h1>

      <ActivityFilterChips
        filters={filters}
        onChange={setFilters}
        objectTypes={objectTypes ?? []}
      />

      {confirmation && <Alert tone="success" title={confirmation} />}
      {error && <Alert tone="error" title={error.title} error={error.cause} />}
      {isLoading && <Spinner label="Loading activity..." />}
      {isError && <Alert tone="error" title="Could not load activity." />}
      {!isLoading && events.length === 0 && (
        <EmptyState title="No activity matches these filters." />
      )}

      {events.length > 0 && (
        <div className="rounded-card border border-line bg-surface">
          <ActivityFeed
            events={events}
            objectTypes={objectTypes ?? []}
            fieldsByType={fieldsByType}
            onRevert={(event) => void handleRevert(event)}
            revertingEventId={revertingEventId}
          />
        </div>
      )}

      {hasNextPage && (
        <Button
          type="button"
          variant="secondary"
          className={btnSmClass}
          onClick={() => void fetchNextPage()}
          disabled={isFetchingNextPage}
        >
          Load more
        </Button>
      )}

      {conflict && (
        <MergeConflictDialog
          conflict={conflict.conflict}
          pendingValues={{ [conflict.event.field_key as string]: conflict.event.old_value }}
          fieldsByKey={{}}
          onResubmit={(payload) => void handleResubmit(payload)}
          onCancel={() => setConflict(null)}
        />
      )}
    </section>
  );
}
