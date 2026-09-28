/**
 * Thin fetch wrapper for the REST API.
 *
 * The UI authenticates with a server-side session cookie (DD-9), not a bearer token.
 * Every request carries `credentials: "include"` so the browser attaches `gw_session`
 * automatically; there is no `Authorization` header to construct here at all, which is what
 * keeps a token out of the built bundle.
 *
 * Every non-safe request (anything but GET/HEAD/OPTIONS) also echoes the `gw_csrf` cookie back
 * as `X-GW-CSRF` (DD-10): the cookie is deliberately not `HttpOnly` so this can read it, and the
 * server rejects a cookie-authenticated non-safe request whose header is absent or mismatched.
 */

/**
 * Same-origin by default, because the built frontend is served by the same FastAPI process
 * (DD-5). Overridable via `VITE_API_BASE_URL` for a dev server running on a different port.
 */
/**
 * Exported because `attachmentDownloadUrl` builds a plain `<a href>` the browser follows
 * itself, so it needs the same base this module fetches against rather than a second copy
 * of the rule.
 */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

const CSRF_COOKIE_NAME = "gw_csrf";
const CSRF_HEADER_NAME = "X-GW-CSRF";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export class ApiError extends Error {
  readonly status: number;
  readonly body: string;

  constructor(status: number, body: string) {
    super(`API request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export type ApiRequestOptions = Omit<RequestInit, "headers"> & {
  headers?: HeadersInit;
};

/**
 * The `gw_csrf` cookie's current value, or `null` before a session exists. Not a credential on
 * its own (DD-10) — reading it is exactly what the double-submit pattern requires the SPA to do.
 */
function readCsrfCookie(): string | null {
  const match = document.cookie.match(
    new RegExp(`(?:^|; )${CSRF_COOKIE_NAME}=([^;]*)`),
  );
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * Issues a request against the REST API with the session cookie attached, and returns the raw
 * `Response` without assuming a JSON body. Callers pass a path relative to the API base (e.g.
 * `/object-types`). `Content-Type` is left to the caller (or the browser, for `FormData`
 * bodies) whenever the body is a `FormData` instance, since a multipart upload's boundary must
 * be set by `fetch` itself, not forced to `application/json`.
 */
export async function apiFetch(path: string, options: ApiRequestOptions = {}): Promise<Response> {
  const headers = new Headers(options.headers);
  const method = (options.method ?? "GET").toUpperCase();
  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCsrfCookie();
    if (csrfToken) {
      headers.set(CSRF_HEADER_NAME, csrfToken);
    }
  }
  if (
    options.body !== undefined &&
    !headers.has("Content-Type") &&
    !(options.body instanceof FormData)
  ) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    credentials: "include",
    headers,
  });

  if (!response.ok) {
    const body = await response.text();
    throw new ApiError(response.status, body);
  }

  return response;
}

/**
 * Issues a request against the REST API and parses the response body as JSON (or `undefined`
 * for a `204 No Content`). Built on `apiFetch`; use `apiFetch` directly for multipart uploads or
 * non-JSON responses (e.g. CSV export).
 */
export async function apiRequest<T>(
  path: string,
  options: ApiRequestOptions = {},
): Promise<T> {
  const response = await apiFetch(path, options);
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}
