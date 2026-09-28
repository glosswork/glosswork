import { keepPreviousData, useQuery, type UseQueryResult } from "@tanstack/react-query";
import { queryRecords, type QueryBody, type QueryResult, type RecordDoc } from "../api/records";

/** One page of the relation picker's listbox. The picker always
 * asks for `limit` candidates and slices the excluded-filtered result down to this many, so a
 * field that already holds several links still offers a full page of choices rather than a
 * partial one. */
export const PICKER_PAGE = 20;

/** The query route's own ceiling (`src/glosswork/services/records.py:93`,
 * `MAX_QUERY_LIMIT = 1000`). The picker's `limit` grows with the number of excluded ids, so this
 * is the backstop that keeps a field with an implausibly large number of existing links from
 * asking for more than the backend will serve. */
const MAX_QUERY_LIMIT = 1000;

/**
 * The cache key for one relation picker's candidate page. This is deliberately its own prefix,
 * never `["object-types", ...]` or `["records", ...]`: a 403 anywhere invalidates the
 * `["object-types"]` prefix wholesale (`invalidateOnForbidden`,
 * `web/src/access/forbiddenRecovery.ts`), and `useLinkMutations` invalidates `["records", ref]`
 * after a link or unlink. Either prefix matching this key would refetch the candidate list on
 * events that have nothing to do with it; worse, `useObjectType(targetTypeKey)` already
 * lives under `["object-types"]`, so a shared prefix on an unreadable target would 403, invalidate
 * itself, refetch and 403 again.
 */
export function relationCandidatesQueryKey(
  targetTypeKey: string,
  term: string,
  limit: number,
): readonly ["relation-candidates", string, string, number] {
  return ["relation-candidates", targetTypeKey, term, limit] as const;
}

export interface UseRelationCandidatesOptions {
  /** The relation field's target object type. */
  targetTypeKey: string;
  /** The target type's `effective_display_field_key`, read off the wire and never
   * re-derived. Null when the target type has no display-eligible field, in which case the
   * candidate list sorts and is keyed on `key` alone. */
  titleFieldKey: string | null;
  /** The already-debounced search text. This hook does not debounce; the caller (`RelationPicker`)
   * owns that, through `useDebouncedValue`. */
  term: string;
  /** Whether the title field's `operators` include `contains`, read by the caller off the
   * target type's `FieldDoc` and passed down as a boolean. This hook reads no `FieldDoc` and
   * makes no operator decision of its own. */
  titleSupportsContains: boolean;
  /** Ids to remove from the candidate list client-side (already-linked records, plus one just
   * chosen and not yet reflected by a `links` refetch). */
  excludedIds: readonly string[];
  enabled: boolean;
}

export interface UseRelationCandidatesResult {
  query: UseQueryResult<QueryResult>;
  /** The current page's records, `excludedIds` removed and sliced to `PICKER_PAGE`. `[]` while
   * there is no data yet. */
  candidates: RecordDoc[];
}

/**
 * One read of a relation field's candidate target records, for the link picker's listbox. This
 * is the only request the picker makes; `RelationPicker` renders it inside the
 * popover's panel so a closed picker issues none.
 *
 * The term is searched only when it is non-empty **and** the title field supports `contains`:
 * sending `contains ""` would match every row for no reason, so an empty or
 * whitespace-only term omits `filter` entirely rather than sending one. A title field that lacks
 * `contains` never gets a `filter` either, no matter what the caller typed, which keeps typing
 * into an unsearchable picker from producing new cache entries per keystroke.
 *
 * **This is not a filter composer** (DD-43). `everyFilterComposerGates.test.ts`'s proxy for
 * "composes a filter" is a module naming the filter-tree wire type or the chip condition shape
 * alongside a change callback; this module names neither. It never assembles a tree, never sees
 * an incomplete condition, and takes no `onChange`/`onCommit` callback: its one possible filter is
 * a single complete `contains` condition it either sends whole or omits, decided entirely from
 * `effectiveTerm` and `titleSupportsContains` before the query function is called. DD-43's gate
 * exists to keep a half-typed condition off the wire; there is no such thing here to keep off it.
 */
export function useRelationCandidates(
  options: UseRelationCandidatesOptions,
): UseRelationCandidatesResult {
  const { targetTypeKey, titleFieldKey, term, titleSupportsContains, excludedIds, enabled } =
    options;

  const effectiveTerm = titleSupportsContains && titleFieldKey !== null ? term.trim() : "";
  const limit = Math.min(PICKER_PAGE + excludedIds.length, MAX_QUERY_LIMIT);

  const body: QueryBody = {
    filter: (effectiveTerm !== ""
      ? { field: titleFieldKey, op: "contains", value: effectiveTerm }
      : undefined) as Record<string, unknown> | undefined,
    sort: (titleFieldKey !== null
      ? [{ field: titleFieldKey, dir: "asc" }]
      : [{ field: "key", dir: "asc" }]) as Record<string, unknown>[],
    fields: titleFieldKey !== null ? ["key", titleFieldKey] : ["key"],
    limit,
    include_deleted: false,
  };

  const query = useQuery({
    queryKey: relationCandidatesQueryKey(targetTypeKey, effectiveTerm, limit),
    queryFn: () => queryRecords(targetTypeKey, body),
    enabled,
    placeholderData: keepPreviousData,
  });

  const excluded = new Set(excludedIds);
  const candidates = (query.data?.records ?? [])
    .filter((record) => !excluded.has(record.id))
    .slice(0, PICKER_PAGE);

  return { query, candidates };
}
