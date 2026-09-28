import { describe, expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "../api/client";
import { invalidateOnForbidden } from "./forbiddenRecovery";

/**
 * `useObjectType(target)` lives under this module's own `["object-types"]`
 * prefix, so a target whose own detail read is `forbidden` — the orientation list still names
 * it, the detail fetch refuses it (`RelationPicker`'s `ReadableTarget`, the exact stale-grant
 * race this module's own comment describes) — would otherwise invalidate itself on every one of
 * its own errors and loop forever (measured at over 5,000 cycles in under three seconds, ending
 * in an out-of-memory crash). The fix is `excludeQueryHash`; this pins both halves of it: the
 * erroring query does not requeue itself, and every other query under the prefix still refreshes.
 */

const FORBIDDEN_BODY = JSON.stringify({
  error: {
    code: "forbidden",
    message:
      "Your access to object type 'task' is 'none'; this call needs at least 'read'. Ask an " +
      "administrator of 'task', or a system administrator, to raise it.",
    details: { object_type: "task", held: "none", required: "read" },
  },
});

/** Built the way `apiFetch` itself throws (`api/client.ts`), not a hand-rolled shape: the raw
 * response text lives in `.body`, parsed back out by `parseForbidden` (`table-view/apiErrors.ts`). */
function forbiddenError(): ApiError {
  return new ApiError(403, FORBIDDEN_BODY);
}

const ORIENTATION_KEY = ["object-types"] as const;
const TASK_DETAIL_KEY = ["object-types", "task", { includeSamples: false }] as const;

/** Seeds two real queries under the shared prefix with `setQueryData` (no fetch involved) and
 * returns the second one's `queryHash` — what `QueryCache.onError` hands `invalidateOnForbidden`
 * for the query whose own error this is. */
function seedTwoQueries(queryClient: QueryClient): string {
  queryClient.setQueryData(ORIENTATION_KEY, []);
  queryClient.setQueryData(TASK_DETAIL_KEY, { key: "task" });
  const taskDetailHash = queryClient.getQueryCache().find({ queryKey: TASK_DETAIL_KEY })?.queryHash;
  if (taskDetailHash === undefined) {
    throw new Error("setQueryData did not seed the second query");
  }
  return taskDetailHash;
}

describe("invalidateOnForbidden", () => {
  it("excludes the named hash from the invalidation, but still invalidates every other query under the prefix", () => {
    const queryClient = new QueryClient();
    const taskDetailHash = seedTwoQueries(queryClient);

    invalidateOnForbidden(queryClient, forbiddenError(), taskDetailHash);

    expect(queryClient.getQueryState(TASK_DETAIL_KEY)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(ORIENTATION_KEY)?.isInvalidated).toBe(true);
  });

  it("invalidates every match under the prefix when called with no hash, exactly as before this parameter existed", () => {
    const queryClient = new QueryClient();
    seedTwoQueries(queryClient);

    invalidateOnForbidden(queryClient, forbiddenError());

    expect(queryClient.getQueryState(ORIENTATION_KEY)?.isInvalidated).toBe(true);
    expect(queryClient.getQueryState(TASK_DETAIL_KEY)?.isInvalidated).toBe(true);
  });

  it("invalidates nothing and returns null for a non-forbidden error", () => {
    const queryClient = new QueryClient();
    const taskDetailHash = seedTwoQueries(queryClient);

    const result = invalidateOnForbidden(queryClient, new Error("network down"), taskDetailHash);

    expect(result).toBeNull();
    expect(queryClient.getQueryState(ORIENTATION_KEY)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(TASK_DETAIL_KEY)?.isInvalidated).toBe(false);
  });
});
