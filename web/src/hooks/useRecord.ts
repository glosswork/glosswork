import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { getRecord, type GetRecordOptions, type RecordWithIncludes } from "../api/records";

export function recordQueryKey(ref: string | undefined, options: GetRecordOptions = {}) {
  return ["records", ref, options] as const;
}

/** One record by human key or UUID (`get_record`), keyed by that ref and its `include` options. */
export function useRecord(
  ref: string | undefined,
  options: GetRecordOptions = {},
): UseQueryResult<RecordWithIncludes> {
  return useQuery({
    queryKey: recordQueryKey(ref, options),
    queryFn: () => getRecord(ref as string, options),
    enabled: ref !== undefined,
  });
}
