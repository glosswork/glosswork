/**
 * The table view's one data fetch: `query_records` with the current filter/sort/groupBy state.
 * `queryKey` includes every input that should trigger a refetch (FR-U1's live filter/sort/group
 * controls); `refetchInterval` is FR-U10's "refresh on an interval" half of the no-websockets
 * requirement (the other half, "refresh on save", is a `queryClient.invalidateQueries` call
 * after each successful write — see the mutation hooks).
 */
import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { queryRecords, type QueryResult } from "../api/records";
import type { FieldDoc } from "../api/objectTypes";
import type { FilterNode } from "../filters/types";
import type { SortKey } from "./sortSpec";
import { TABLE_VIEW_PAGE_SIZE, TABLE_VIEW_POLL_INTERVAL_MS } from "./constants";

/**
 * Everything that makes this a *different* query rather than a different page of the same one.
 * `TableView` restarts its cursor walk, its page index, and its row
 * selection whenever this changes — derived during render, so no control has to remember to
 * fire a reset and a control added later inherits it by construction.
 */
export function tableRecordsQueryIdentity(
  objectTypeKey: string,
  filter: FilterNode | null,
  sort: SortKey[],
  groupBy: string | null,
) {
  return ["table-view-records", objectTypeKey, filter, sort, groupBy] as const;
}

/** The identity plus the page's cursor: the cache key for one page. */
export function tableRecordsQueryKey(
  objectTypeKey: string,
  filter: FilterNode | null,
  sort: SortKey[],
  groupBy: string | null,
  cursor: string | null,
) {
  return [...tableRecordsQueryIdentity(objectTypeKey, filter, sort, groupBy), cursor] as const;
}

export interface UseTableRecordsQueryOptions {
  objectTypeKey: string;
  filter: FilterNode | null;
  sort: SortKey[];
  groupBy: string | null;
  fields: FieldDoc[];
  /** The keyset cursor for the page to show; null is the first page. */
  cursor: string | null;
}

/** Only the relation field currently driving grouping is expanded (FR-L6 scoped to what the
 * view actually needs), never every relation field on the type. */
function expandRelationsFor(groupBy: string | null, fields: FieldDoc[]): string[] | undefined {
  if (groupBy === null) return undefined;
  const field = fields.find((candidate) => candidate.key === groupBy);
  return field?.type === "relation" ? [groupBy] : undefined;
}

export function useTableRecordsQuery({
  objectTypeKey,
  filter,
  sort,
  groupBy,
  fields,
  cursor,
}: UseTableRecordsQueryOptions): UseQueryResult<QueryResult> {
  return useQuery({
    queryKey: tableRecordsQueryKey(objectTypeKey, filter, sort, groupBy, cursor),
    queryFn: () =>
      queryRecords(objectTypeKey, {
        filter: (filter ?? undefined) as Record<string, unknown> | undefined,
        sort: (sort.length ? sort : undefined) as Record<string, unknown>[] | undefined,
        limit: TABLE_VIEW_PAGE_SIZE,
        cursor: cursor ?? undefined,
        fields: "*",
        expand_relations: expandRelationsFor(groupBy, fields),
        include_deleted: false,
      }),
    refetchInterval: TABLE_VIEW_POLL_INTERVAL_MS,
    // A page turn keeps the previous page on screen until the next one arrives, so the
    // table does not blank between pages.
    placeholderData: keepPreviousData,
  });
}
