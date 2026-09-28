/**
 * Inline cell edit's write path: `PATCH /api/v1/records/{key}` with `expected_version` from the
 * currently displayed row. Success and 409 handling are reported through callbacks so
 * `TableView.tsx` decides what state changes (patch the row, open the merge dialog) — this hook
 * only owns the API call and the error-shape parsing.
 */
import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { invalidateOnForbidden } from "../access/forbiddenRecovery";
import { updateRecord } from "../api/records";
import type { FieldDoc } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import { parseVersionConflict } from "./apiErrors";
import type { ConflictDetails } from "./mergeConflict";

export interface InlineEditConflict {
  record: RecordDoc;
  field: FieldDoc;
  attemptedValue: unknown;
  conflict: ConflictDetails;
}

export interface UseInlineCellEditOptions {
  onSuccess: (updated: RecordDoc) => void;
  onConflict: (conflict: InlineEditConflict) => void;
}

export interface UseInlineCellEditResult {
  commitCell: (record: RecordDoc, field: FieldDoc, value: unknown) => Promise<void>;
  error: string | null;
}

export function useInlineCellEdit({
  onSuccess,
  onConflict,
}: UseInlineCellEditOptions): UseInlineCellEditResult {
  const [error, setError] = useState<string | null>(null);
  // This hook predates React Query's mutation API and makes its `PATCH` by hand, so the query
  // client's global `forbidden` handler never sees it. It calls the same function directly.
  const queryClient = useQueryClient();

  const commitCell = useCallback(
    async (record: RecordDoc, field: FieldDoc, value: unknown) => {
      setError(null);
      try {
        const updated = await updateRecord(record.key, {
          values: { [field.key]: value },
          expected_version: record.version,
          force: false,
        });
        onSuccess(updated);
      } catch (caught) {
        const conflict = parseVersionConflict(caught);
        if (conflict) {
          onConflict({ record, field, attemptedValue: value, conflict });
          return;
        }
        // A forbidden refusal: verbatim, and refetch. `ApiError.message` is only "API request
        // failed with status 403", so without this the one error the user could act on is
        // the one that says least.
        const forbidden = invalidateOnForbidden(queryClient, caught);
        if (forbidden) {
          setError(forbidden.message);
          return;
        }
        setError(caught instanceof Error ? caught.message : "Failed to save the edit.");
      }
    },
    [onConflict, onSuccess, queryClient],
  );

  return { commitCell, error };
}
