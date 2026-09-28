import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { invalidateOnForbidden } from "../access/forbiddenRecovery";

/**
 * One factory so the real app (`main.tsx`) and tests (`test/renderWithProviders.tsx`) construct
 * equivalent clients. Retries are disabled: the UI has no offline story, and retrying
 * makes component tests slower and flakier for no benefit.
 *
 * Both caches carry a `forbidden` handler, so **no call site can forget it**.
 * A 403 `forbidden` anywhere proves this client's `your_access` is stale, and the response is
 * always the same — refetch the orientation document. Wiring that per mutation would have been
 * a dozen sites and a standing invitation to miss the thirteenth. `invalidateOnForbidden`
 * ignores every other error, so nothing else changes shape.
 */
export function createQueryClient(): QueryClient {
  const queryClient: QueryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
    queryCache: new QueryCache({
      // The erroring query's own hash is excluded from the invalidation this triggers
      // (`forbiddenRecovery.ts`'s own comment): otherwise a query whose own fetch is what just
      // reported `forbidden` would invalidate and refetch itself forever.
      onError: (error, query) => invalidateOnForbidden(queryClient, error, query.queryHash),
    }),
    mutationCache: new MutationCache({
      onError: (error) => invalidateOnForbidden(queryClient, error),
    }),
  });
  return queryClient;
}
