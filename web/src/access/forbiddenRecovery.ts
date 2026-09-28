/**
 * What the UI does when the server refuses a control the UI offered.
 *
 * Gating is an affordance and the server is the boundary, so the two *will* disagree: a grant
 * can be revoked between a page load and a click, and then an affordance that was shown
 * legitimately is refused legitimately. A 403 `forbidden` is proof that this client's copy of
 * `your_access` is stale, and refetching the orientation document is the smallest correct
 * response to that proof — it re-filters the nav, re-derives every `your_access`, and the
 * affordances disappear on the next render without a reload.
 *
 * One implementation, two callers: the query client's global error handlers (so no mutation
 * anywhere can forget) and `useInlineCellEdit`, which predates React Query's mutation API and
 * makes its `PATCH` by hand.
 *
 * **A query's own `forbidden` excludes itself from the invalidation it triggers.**
 * `useObjectType` lives under this same `["object-types"]` prefix, and it is called for a
 * target whose own read can be `forbidden` while the orientation list still names it
 * (`record-detail/RelationPicker.tsx`'s `ReadableTarget`: the list says readable,
 * the detail fetch says otherwise — the exact stale-grant race this module's own comment above
 * describes). Left unguarded, that query invalidates itself on every one of its own errors,
 * refetches while still mounted (`invalidateQueries`'s default `refetchType: "active"`), 403s
 * again, and repeats with no macrotask boundary to ever break out on: measured at over 5,000
 * cycles in under three seconds against a mocked backend, an actual `RangeError`-free infinite
 * loop, since `retry: false` only governs a single fetch's own failure handling and does nothing
 * to stop a *new* invalidation-triggered fetch. `QueryCache.onError` is handed the erroring
 * `Query` itself; excluding its own hash from the invalidation leaves every other query under the
 * prefix free to refresh (the orientation list included) while the one query that just supplied
 * the freshest possible answer, its own 403, does not immediately re-ask the question it was just
 * refused. `MutationCache.onError` has no query of its own to name and passes none, which is a
 * no-op for this predicate and leaves that caller exactly as it was.
 */
import type { QueryClient } from "@tanstack/react-query";
import { objectTypesQueryKey } from "../hooks/useObjectTypes";
import { parseForbidden, type ForbiddenDetails } from "../table-view/apiErrors";

export function invalidateOnForbidden(
  queryClient: QueryClient,
  error: unknown,
  /** The `queryHash` of the query whose own error this is, when the caller is a query (not a
   * mutation) — omitted, the predicate below excludes nothing and every match under the prefix
   * is invalidated, exactly as before this parameter existed. */
  excludeQueryHash?: string,
): ForbiddenDetails | null {
  const details = parseForbidden(error);
  if (details === null) {
    return null;
  }
  void queryClient.invalidateQueries({
    queryKey: objectTypesQueryKey,
    predicate: (query) => query.queryHash !== excludeQueryHash,
  });
  return details;
}
