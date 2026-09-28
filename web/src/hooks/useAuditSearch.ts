import { useInfiniteQuery, type UseInfiniteQueryResult } from "@tanstack/react-query";
import { searchAuditEvents, type AuditSearchOptions } from "../api/audit";
import type { HistoryPage } from "../api/records";

export type AuditSearchQueryResult = UseInfiniteQueryResult<{
  pages: HistoryPage[];
  pageParams: (string | null)[];
}>;

/** `audit-events` search, filtered and paginated exactly like `useRecordHistory` (FR-U8); every
 * filter value is part of the query key so changing a filter starts a fresh keyset walk. */
export function useAuditSearch(options: AuditSearchOptions = {}): AuditSearchQueryResult {
  const { record, principalId, agentLabelId, objectType, fieldKey, since, until } = options;
  return useInfiniteQuery({
    queryKey: [
      "audit-events",
      record,
      principalId,
      agentLabelId,
      objectType,
      fieldKey,
      since,
      until,
    ],
    queryFn: ({ pageParam }) =>
      searchAuditEvents({
        record,
        principalId,
        agentLabelId,
        objectType,
        fieldKey,
        since,
        until,
        cursor: pageParam ?? undefined,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage: HistoryPage) => lastPage.next_cursor,
  });
}
