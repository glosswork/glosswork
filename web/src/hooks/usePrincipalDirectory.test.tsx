import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { render, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { createQueryClient } from "../app/queryClient";
import { principalDirectoryQueryKey, usePrincipalDirectory } from "./usePrincipalDirectory";
import type { PrincipalDirectoryEntry } from "../api/principals";

let requestCount = 0;
const DIRECTORY: PrincipalDirectoryEntry[] = [
  { id: "p1", display_name: "Sarah Okonjo", email: "sarah@example.com", type: "user", is_active: true },
];

const server = setupServer(
  http.get("/api/v1/principals/directory", () => {
    requestCount += 1;
    return HttpResponse.json({ principals: DIRECTORY });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  requestCount = 0;
});
afterAll(() => server.close());

function Consumer() {
  usePrincipalDirectory();
  return null;
}

describe("usePrincipalDirectory", () => {
  it("caches under one stable query key, so many simultaneous callers share one entry", async () => {
    const queryClient = createQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <Consumer />
        <Consumer />
        <Consumer />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(queryClient.getQueryData(principalDirectoryQueryKey)).toBeDefined();
    });

    // Asserting the query key rather than the network, per the checklist: exactly one cache
    // entry exists for the directory regardless of how many components asked for it, which is
    // what makes the request itself single rather than per-caller.
    const directoryQueries = queryClient
      .getQueryCache()
      .findAll({ queryKey: principalDirectoryQueryKey });
    expect(directoryQueries).toHaveLength(1);
    expect(requestCount).toBe(1);
  });

  it("resolves the directory entries fetched from GET /api/v1/principals/directory", async () => {
    const queryClient = createQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <Consumer />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(queryClient.getQueryData(principalDirectoryQueryKey)).toEqual(DIRECTORY);
    });
  });
});
