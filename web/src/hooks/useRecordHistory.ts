import { useInfiniteQuery, type UseInfiniteQueryResult } from "@tanstack/react-query";
import { getRecordHistory, type HistoryPage } from "../api/records";

export type RecordHistoryQueryResult = UseInfiniteQueryResult<{
  pages: HistoryPage[];
  pageParams: (string | null)[];
}>;

/** Field-level audit history for one record (`get_record_history`), paginated with the same
 * keyset `limit`/`cursor` the REST route exposes (FR-D5; FR-U2's audit timeline "load more"). */
export function useRecordHistory(ref: string | undefined): RecordHistoryQueryResult {
  return useInfiniteQuery({
    queryKey: ["record-history", ref],
    queryFn: ({ pageParam }) =>
      getRecordHistory(ref as string, { cursor: pageParam ?? undefined }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage: HistoryPage) => lastPage.next_cursor,
    enabled: ref !== undefined,
  });
}
