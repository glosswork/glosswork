import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { apiRequest } from "./client";

let capturedAuthorization: string | null = null;
let capturedCsrfHeader: string | null = null;

const server = setupServer(
  http.get("/api/v1/object-types", ({ request }) => {
    capturedAuthorization = request.headers.get("Authorization");
    return HttpResponse.json({ items: [] });
  }),
  http.post("/api/v1/object-types", ({ request }) => {
    capturedCsrfHeader = request.headers.get("X-GW-CSRF");
    return HttpResponse.json({});
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  capturedAuthorization = null;
  capturedCsrfHeader = null;
  document.cookie = "gw_csrf=; Max-Age=0; path=/";
});
afterAll(() => server.close());

describe("apiRequest", () => {
  /**
   * The session cookie is the credential (DD-9), and the browser attaches it on its own
   * via `credentials: "include"` — there is no `Authorization` header for this client to
   * construct any more.
   */
  it("attaches no Authorization header; the cookie is the credential", async () => {
    await apiRequest("/object-types");
    expect(capturedAuthorization).toBeNull();
  });

  it("sends credentials so the session cookie is attached", async () => {
    const originalFetch = window.fetch;
    let seenInit: RequestInit | undefined;
    window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
      seenInit = init;
      return originalFetch(input, init);
    }) as typeof fetch;
    try {
      await apiRequest("/object-types");
    } finally {
      window.fetch = originalFetch;
    }
    expect(seenInit?.credentials).toBe("include");
  });

  it("echoes the gw_csrf cookie as X-GW-CSRF on a non-safe request (DD-10)", async () => {
    document.cookie = "gw_csrf=the-csrf-value; path=/";
    await apiRequest("/object-types", { method: "POST", body: JSON.stringify({}) });
    expect(capturedCsrfHeader).toBe("the-csrf-value");
  });

  it("sends no X-GW-CSRF header before a session exists", async () => {
    await apiRequest("/object-types", { method: "POST", body: JSON.stringify({}) });
    expect(capturedCsrfHeader).toBeNull();
  });
});
