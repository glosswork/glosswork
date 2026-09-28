import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { fetchPrincipalDirectory, type PrincipalDirectoryEntry } from "../api/principals";

/**
 * The one stable cache key every `user_ref` picker shares:
 * `EditableCell` (via `FieldInput`), `FieldList` (via the same `FieldInput`) and
 * `filters/ValueInput.tsx` all call `usePrincipalDirectory()` independently, but React Query
 * dedupes on this key, so a table of 50 rows each opening its own picker still makes exactly one
 * `GET /api/v1/principals/directory` request rather than one per cell.
 */
export const principalDirectoryQueryKey = ["principal-directory"] as const;

/** The directory route's own cap. The picker wants the whole org, not the
 * search-box default of 50 a partial query would use. */
const PICKER_DIRECTORY_LIMIT = 200;

/**
 * The full active principal directory, for a `user_ref` picker to choose from. No `q`/`type`
 * argument is threaded through here on purpose: every caller wants the same unfiltered list, and
 * a per-call variant would be exactly the per-row fan-out this hook's one query key exists to
 * prevent.
 */
export function usePrincipalDirectory(): UseQueryResult<PrincipalDirectoryEntry[]> {
  return useQuery({
    queryKey: principalDirectoryQueryKey,
    queryFn: () => fetchPrincipalDirectory({ limit: PICKER_DIRECTORY_LIMIT }),
  });
}
