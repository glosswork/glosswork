/**
 * Bulk delete is client-side (there is no bulk-delete route): loop `DELETE /records/{ref}` over
 * every selected key, collecting a per-record outcome so `relation_blocked` failures (FR-L4)
 * surface their blocking keys instead of silently failing the whole batch or the whole delete
 * appearing to have no effect.
 */
import { useCallback, useState } from "react";
import { deleteRecord } from "../api/records";
import { parseRelationBlocked } from "./apiErrors";

export type BulkDeleteStatus = "deleted" | "blocked" | "failed";

export interface BulkDeleteOutcome {
  recordKey: string;
  status: BulkDeleteStatus;
  blockingRecordKeys?: string[];
  message?: string;
}

export interface UseBulkDeleteResult {
  running: boolean;
  results: BulkDeleteOutcome[] | null;
  run: (recordKeys: string[]) => Promise<BulkDeleteOutcome[]>;
  clearResults: () => void;
}

export function useBulkDelete(onAnyDeleted: () => void): UseBulkDeleteResult {
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<BulkDeleteOutcome[] | null>(null);

  const run = useCallback(
    async (recordKeys: string[]) => {
      setRunning(true);
      const outcomes: BulkDeleteOutcome[] = [];
      for (const recordKey of recordKeys) {
        try {
          await deleteRecord(recordKey);
          outcomes.push({ recordKey, status: "deleted" });
        } catch (caught) {
          const blockingRecordKeys = parseRelationBlocked(caught);
          if (blockingRecordKeys !== null) {
            outcomes.push({ recordKey, status: "blocked", blockingRecordKeys });
          } else {
            outcomes.push({
              recordKey,
              status: "failed",
              message: caught instanceof Error ? caught.message : "Delete failed.",
            });
          }
        }
      }
      setResults(outcomes);
      setRunning(false);
      if (outcomes.some((outcome) => outcome.status === "deleted")) {
        onAnyDeleted();
      }
      return outcomes;
    },
    [onAnyDeleted],
  );

  const clearResults = useCallback(() => setResults(null), []);

  return { running, results, run, clearResults };
}
