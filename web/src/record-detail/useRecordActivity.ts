/**
 * React Query's wrapping of `fetchRecordActivityPages`, one query shared by two callers: the
 * Details card's agent bar needs the record's whole drained history, and the Activity card needs
 * the same history plus the comment thread.
 *
 * **Both cards call this hook independently, and that costs one request, not two.** React Query
 * dedupes by query key against the one `QueryClient` the app provides, so two components mounted
 * together — exactly what `RecordDetailView`'s two-column layout does — resolve from the same
 * in-flight request and the same cache entry, so the cards fetch independently rather than
 * through a prop threaded down from `RecordDetailView`; only `canWrite` is derived once and
 * passed down,
 * because it is an authorization decision, not a data fetch.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { fetchRecordActivityPages, type RecordActivityPages } from "./recordActivityPages";

export function recordActivityQueryKey(ref: string) {
  return ["record-activity", ref] as const;
}

export function useRecordActivity(ref: string): UseQueryResult<RecordActivityPages> {
  return useQuery({
    queryKey: recordActivityQueryKey(ref),
    queryFn: () => fetchRecordActivityPages(ref),
  });
}
