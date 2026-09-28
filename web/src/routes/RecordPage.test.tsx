/**
 * `RecordPage` keeps `useObjectType` mounted while it renders "Record not found." for an error
 * (`RecordPage.tsx`), so a record page whose *type* read is `forbidden` can loop:
 * `invalidateOnForbidden` invalidates its own `["object-types"]` prefix,
 * `useObjectType(objectTypeKey)` is still mounted and still under that prefix, so it refetches
 * itself, gets a 403 again, and repeats with no macrotask boundary to ever break out on (measured
 * without the fix at over 5,000 cycles in under three seconds, ending in an out-of-memory worker
 * crash). `forbiddenRecovery.ts`'s `excludeQueryHash` fixes it for every caller, this file
 * included, and this is its regression test.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { Route, Routes } from "react-router-dom";
import { RecordPage } from "./RecordPage";
import { DEFAULT_TEST_PRINCIPAL, renderWithProviders } from "../test/renderWithProviders";

/** Counts every `GET /api/v1/object-types/:key`: the query whose own 403 must not cause it to
 * re-ask the question it was just refused. */
let objectTypeRequestCount = 0;

const FORBIDDEN_ENVELOPE = {
  error: {
    code: "forbidden",
    message:
      "Your access to object type 'initiative' is 'none'; this call needs at least 'read'. " +
      "Ask an administrator of 'initiative', or a system administrator, to raise it.",
    details: { object_type: "initiative", held: "none", required: "read" },
  },
};

const server = setupServer(
  http.get("/api/v1/object-types/:key", () => {
    objectTypeRequestCount += 1;
    return HttpResponse.json(FORBIDDEN_ENVELOPE, { status: 403 });
  }),
  // The orientation list nobody in this render calls (`RecordPage` mounts no `useObjectTypes`),
  // mocked anyway so a stray invalidation that did try to refetch it would resolve rather than
  // trip `onUnhandledRequest: "error"`.
  http.get("/api/v1/object-types", () => HttpResponse.json([])),
  // A minimal valid `get_record` response (`api/records.ts`'s `RecordDoc`): `RecordPage` renders
  // "Record not found." off the object-type read alone, so this only needs to resolve, not to
  // carry real field data.
  http.get("/api/v1/records/:ref", () =>
    HttpResponse.json({
      id: "rec-1",
      key: "INIT-1",
      version: 1,
      created_at: "2026-08-20T09:00:00",
      created_by: DEFAULT_TEST_PRINCIPAL.id,
      updated_at: "2026-08-20T09:00:00",
      updated_by: DEFAULT_TEST_PRINCIPAL.id,
      updated_by_agent_label_id: null,
      deleted_at: null,
      comment_count: 0,
      last_comment_at: null,
      data: {},
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  objectTypeRequestCount = 0;
});
afterAll(() => server.close());

function renderRecordPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/:objectTypeKey/:recordKey" element={<RecordPage />} />
    </Routes>,
    { route: "/initiative/INIT-1" },
  );
}

describe("RecordPage", () => {
  it(
    "a 403 on the type read does not loop-refetch itself",
    async () => {
      renderRecordPage();

      await screen.findByText("Record not found.");

      // A real wall-clock wait, not vitest's fake timers: the defect is a synchronous
      // invalidate-and-refetch cycle with no macrotask boundary to break out on, so only real
      // elapsed time distinguishes "settled at one request" from "still looping".
      await new Promise<void>((resolve) => setTimeout(resolve, 500));

      expect(objectTypeRequestCount).toBeLessThanOrEqual(2);
    },
    10000,
  );
});
