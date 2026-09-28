import { useMutation, useQueryClient, type UseMutationResult } from "@tanstack/react-query";
import { linkRecords, unlinkRecords } from "../api/records";

/** Links one or more records into a relation field and refreshes the record's `links`
 * include in place (FR-L1 through FR-L6, FR-U2's linked-records panel). */
export function useLinkRecords(
  ref: string,
  fieldKey: string,
): UseMutationResult<void, unknown, string[]> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (toRecords: string[]) => linkRecords(ref, fieldKey, toRecords),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["records", ref] });
    },
  });
}

/** Unlinks one or more records from a relation field and refreshes the record's `links`
 * include in place. */
export function useUnlinkRecords(
  ref: string,
  fieldKey: string,
): UseMutationResult<void, unknown, string[]> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (toRecords: string[]) => unlinkRecords(ref, fieldKey, toRecords),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["records", ref] });
    },
  });
}
