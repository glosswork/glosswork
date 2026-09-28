/**
 * `useObjectTypeAccess` reads `your_access` out of the orientation list already in the query
 * cache, so gating an affordance costs no additional request on any screen.
 * What is worth testing here is not the lookup but its two fail-safe edges: an unknown key and
 * a list that has not arrived yet both resolve to `none`, and the second is distinguishable
 * from the first so a screen never flashes "Read-only" before it knows.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import type { ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { createQueryClient } from "../app/queryClient";
import { useObjectTypeAccess } from "./useObjectTypeAccess";
import type { ObjectTypeSummary } from "../api/objectTypes";

const objectTypes: ObjectTypeSummary[] = [
  {
    key: "initiative",
    name: "Initiative",
    description: "A tracked initiative.",
    key_prefix: "INI",
    record_count: 3,
    field_count: 4,
    your_access: "read",
  },
  {
    key: "task",
    name: "Task",
    description: "A unit of work.",
    key_prefix: "TSK",
    record_count: 9,
    field_count: 6,
    your_access: "admin",
  },
];

const server = setupServer(
  http.get("/api/v1/object-types", () => HttpResponse.json(objectTypes)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={createQueryClient()}>{children}</QueryClientProvider>;
}

describe("useObjectTypeAccess", () => {
  it("is loading, and holds no level, before the orientation list arrives", () => {
    const { result } = renderHook(() => useObjectTypeAccess("initiative"), { wrapper });

    expect(result.current.isLoading).toBe(true);
    expect(result.current.level).toBe("none");
  });

  it("resolves the caller's level for a key in the list", async () => {
    const { result } = renderHook(() => useObjectTypeAccess("initiative"), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.level).toBe("read");
  });

  it("resolves a second key from the same one fetch", async () => {
    const { result } = renderHook(() => useObjectTypeAccess("task"), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.level).toBe("admin");
  });

  it("holds 'none' for a key the list does not contain, which is what a filtered-out type is", async () => {
    const { result } = renderHook(() => useObjectTypeAccess("decision"), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.level).toBe("none");
  });

  it("holds 'none' and is not loading for an undefined key", () => {
    const { result } = renderHook(() => useObjectTypeAccess(undefined), { wrapper });

    expect(result.current.level).toBe("none");
    expect(result.current.isLoading).toBe(false);
  });
});
