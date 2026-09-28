/**
 * The create-record write path (DD-44): `POST /api/v1/object-types/{key}/records`.
 *
 * **A `useMutation`, deliberately, where `useInlineCellEdit` is a hand-rolled `fetch`.** That hook
 * says in its own header that it predates React Query's mutation API, and it pays for that by
 * calling `invalidateOnForbidden` by hand — because the query client's global handlers never see a
 * request it made itself. A mutation gets that handler from `mutationCache.onError` for free
 * (`app/queryClient.ts`), which is the whole point of it living there: "no mutation anywhere can
 * forget". Copying the older shape would have been copying a workaround for a problem this code
 * does not have.
 *
 * The refusal this surface actually has to render is `validation_failed`, which names the
 * offending field in `details.field_key` — a required field the pre-flight could not know about, a
 * uniqueness collision, a value the server coerces differently. `parseApiError` stays the one
 * reader of that envelope; this is its third consumer.
 */
import { useCallback, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createRecord, type RecordDoc } from "../api/records";
import { objectTypesQueryKey } from "../hooks/useObjectTypes";
import { parseValidationFailed } from "./apiErrors";

export interface CreateRecordFailure {
  /** The server's own message, rendered verbatim. */
  message: string;
  /** The field the server named, when it named one. Null for a refusal about no single field. */
  fieldKey: string | null;
}

export interface UseCreateRecordResult {
  create: (values: Record<string, unknown>) => Promise<RecordDoc | null>;
  failure: CreateRecordFailure | null;
  clearFailure: () => void;
  isPending: boolean;
}

export function useCreateRecord(objectTypeKey: string): UseCreateRecordResult {
  const queryClient = useQueryClient();
  const [failure, setFailure] = useState<CreateRecordFailure | null>(null);

  const mutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => createRecord(objectTypeKey, values),
    onSuccess: () => {
      // Not `exact: true`. The record count on the sidebar and in this page's own title line come
      // from `["object-types"]`, and the describe document this screen renders is keyed
      // `["object-types", key, …]` — a prefix match, which an exact invalidation would miss,
      // leaving the title line one behind for as long as the page stays mounted.
      void queryClient.invalidateQueries({ queryKey: objectTypesQueryKey });
    },
  });

  const create = useCallback(
    async (values: Record<string, unknown>): Promise<RecordDoc | null> => {
      setFailure(null);
      try {
        return await mutation.mutateAsync(values);
      } catch (caught) {
        const validation = parseValidationFailed(caught);
        if (validation) {
          setFailure({ message: validation.message, fieldKey: validation.fieldKey });
          return null;
        }
        // Anything else — a 403 whose recovery `mutationCache.onError` has already run, a network
        // failure — reports its own message. The 403's copy is the backend's, written to name what
        // to do about it, and is not paraphrased here (DD-42).
        setFailure({
          message: caught instanceof Error ? caught.message : "Could not create the record.",
          fieldKey: null,
        });
        return null;
      }
    },
    [mutation],
  );

  const clearFailure = useCallback(() => setFailure(null), []);

  return { create, failure, clearFailure, isPending: mutation.isPending };
}
