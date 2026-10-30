/**
 * Change 32: the workspace screens live under `/workspace`, and an old top-level address is decided
 * below the object type routes, so a type with that key keeps its table.
 *
 * The harness is local rather than `renderWithProviders` because these tests need a history
 * with an entry behind the current one, and a way to navigate after the first render, and that
 * helper seeds one entry. `LocationProbe` reads where the app landed; a heading cannot tell a
 * redirect from a route that happens to render the same page.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { delay, http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { MemoryRouter, useLocation, useNavigate, useNavigationType } from "react-router-dom";
import { App } from "./App";
import type { CurrentPrincipal } from "./api/auth";
import type { ObjectTypeDetail } from "./api/objectTypes";
import { createQueryClient } from "./app/queryClient";
import { AuthProvider } from "./auth/AuthProvider";
import { DEFAULT_TEST_PRINCIPAL } from "./test/renderWithProviders";

function typeDetail(key: string, name: string): ObjectTypeDetail {
  return {
    key,
    name,
    name_plural: `${name}s`,
    description: `The ${name} fixture type.`,
    key_prefix: key.slice(0, 3).toUpperCase(),
    record_count: 0,
    field_count: 0,
    your_access: "admin",
    display_field_key: null,
    effective_display_field_key: null,
    fields: [],
    system_fields: [],
  };
}

const INITIATIVE = typeDetail("initiative", "Initiative");
/** Named so that its table's heading cannot be mistaken for the feed's, which reads `Activity`. */
const ACTIVITY_TYPE = typeDetail("activity", "Clashing activity");
const WORKSPACE_TYPE = typeDetail("workspace", "Clashing workspace");

const RECORD_UUID = "0b6f1f6a-3f0e-4a57-9a0c-7d0a0d7c3b11";

const FORBIDDEN_BODY = {
  error: {
    code: "forbidden",
    message: "Your access to object type 'activity' is 'none'; this call needs at least 'read'.",
    details: { object_type: "activity", held: "none", required: "read" },
  },
};

/** The server's object types, replaced per test by `serveTypes`. */
let servedTypes: ObjectTypeDetail[] = [INITIATIVE];
/** Every record lookup the app sent, so a test can assert one was or was not. */
let recordLookups: string[] = [];

function serveTypes(...types: ObjectTypeDetail[]) {
  servedTypes = [INITIATIVE, ...types];
}

const server = setupServer(
  http.get("/api/v1/object-types", () => HttpResponse.json(servedTypes)),
  http.get("/api/v1/object-types/:key", ({ params }) => {
    const detail = servedTypes.find((type) => type.key === params.key);
    return detail ? HttpResponse.json(detail) : new HttpResponse(null, { status: 404 });
  }),
  http.get("/api/v1/records/:key", ({ params }) => {
    recordLookups.push(params.key as string);
    return new HttpResponse(null, { status: 404 });
  }),
  http.post("/api/v1/object-types/:key/query", () =>
    HttpResponse.json({ records: [], total_count: 0, next_cursor: null, truncated: false }),
  ),
  http.get("/api/v1/object-types/:key/saved-views", () => HttpResponse.json([])),
  http.get("/api/v1/object-types/:key/grants", () => HttpResponse.json({ grants: [] })),
  http.get("/api/v1/auth/modes", () =>
    HttpResponse.json({ standalone: true, oidc: false, email_code: false }),
  ),
  http.get("/api/v1/workspace", () =>
    HttpResponse.json({ name: "Northwind", people: 6, agents: 3 }),
  ),
  http.get("/api/v1/schema-proposals", () => HttpResponse.json({ proposals: [] })),
  http.get("/api/v1/agent-labels", () => HttpResponse.json({ labels: [] })),
  http.get("/api/v1/admin/agent-labels", () => HttpResponse.json({ labels: [] })),
  http.get("/api/v1/principals", () => HttpResponse.json({ principals: [] })),
  http.get("/api/v1/access-tokens", () => HttpResponse.json({ access_tokens: [] })),
  http.get("/api/v1/audit-events", () => HttpResponse.json({ events: [], next_cursor: null })),
  http.get("/api/v1/principals/directory", () => HttpResponse.json({ principals: [] })),
  http.get("/api/v1/agent-labels/directory", () => HttpResponse.json({ agent_labels: [] })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  servedTypes = [INITIATIVE];
  recordLookups = [];
});
afterAll(() => server.close());

function Probe() {
  const location = useLocation();
  const navigationType = useNavigationType();
  const navigate = useNavigate();
  return (
    <>
      <span data-testid="pathname">{location.pathname}</span>
      <span data-testid="full-address">{`${location.pathname}${location.search}${location.hash}`}</span>
      <span data-testid="navigation-type">{navigationType}</span>
      <button onClick={() => navigate(-1)}>probe back</button>
      <button onClick={() => navigate("/activity")}>probe to old activity</button>
    </>
  );
}

function renderAt(
  entries: string[],
  { principal = DEFAULT_TEST_PRINCIPAL }: { principal?: CurrentPrincipal } = {},
) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <AuthProvider initialPrincipal={principal}>
        <MemoryRouter initialEntries={entries} initialIndex={entries.length - 1}>
          <App />
          <Probe />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

const pathname = () => screen.getByTestId("pathname").textContent;
const feedHeading = () => screen.findByRole("heading", { level: 1, name: "Activity" });

describe("the screens at their new addresses", () => {
  it.each([
    ["/workspace/people", "People & agents"],
    ["/workspace/activity", "Activity"],
    ["/workspace/setup", "Setup"],
    ["/workspace/schema", "Schema"],
  ])("serves %s under its own heading", async (route, heading) => {
    renderAt([route]);

    expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
    expect(pathname()).toBe(route);
  });

  it("keeps bare /workspace as the not-found every unknown address shows", async () => {
    renderAt(["/workspace"]);

    expect(await screen.findByText("Object type not found.")).toBeInTheDocument();
    expect(pathname()).toBe("/workspace");
  });
});

describe("a type keyed like an old address", () => {
  it("renders its table at /activity", async () => {
    serveTypes(ACTIVITY_TYPE);
    renderAt(["/activity"]);

    expect(await screen.findByRole("heading", { name: "Clashing activity" })).toBeInTheDocument();
    expect(pathname()).toBe("/activity");
  });

  it("leaves the feed at /workspace/activity", async () => {
    serveTypes(ACTIVITY_TYPE);
    renderAt(["/workspace/activity"]);

    expect(await feedHeading()).toBeInTheDocument();
    expect(pathname()).toBe("/workspace/activity");
  });

  it("returns to its table after a cached 404 and a redirect, once it exists", async () => {
    const user = userEvent.setup();
    renderAt(["/activity"]);

    await waitFor(() => expect(pathname()).toBe("/workspace/activity"));
    expect(await feedHeading()).toBeInTheDocument();

    // Created while this tab held the 404 in its cache.
    serveTypes(ACTIVITY_TYPE);
    await user.click(screen.getByRole("button", { name: "probe to old activity" }));

    expect(await screen.findByRole("heading", { name: "Clashing activity" })).toBeInTheDocument();
    expect(pathname()).toBe("/activity");
  });
});

describe("an old address with no such type", () => {
  it("redirects /activity with replace, so Back leaves rather than bouncing", async () => {
    const user = userEvent.setup();
    renderAt(["/inbox", "/activity"]);

    expect(await feedHeading()).toBeInTheDocument();
    expect(pathname()).toBe("/workspace/activity");
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");

    await user.click(screen.getByRole("button", { name: "probe back" }));

    await waitFor(() => expect(pathname()).toBe("/inbox"));
  });

  it("keeps the query string and the hash", async () => {
    renderAt(["/people?from=bookmark#agents"]);

    await waitFor(() =>
      expect(screen.getByTestId("full-address")).toHaveTextContent(
        "/workspace/people?from=bookmark#agents",
      ),
    );
  });

  it("redirects on a forbidden envelope for a member, as on a 404", async () => {
    server.use(
      http.get("/api/v1/object-types/activity", () =>
        HttpResponse.json(FORBIDDEN_BODY, { status: 403 }),
      ),
    );
    renderAt(["/activity"], {
      principal: { ...DEFAULT_TEST_PRINCIPAL, role: "member", scope: "write" },
    });

    expect(await feedHeading()).toBeInTheDocument();
    expect(pathname()).toBe("/workspace/activity");
  });

  it("does not redirect on a 500: it stops spinning and shows the existing not-found", async () => {
    let answered = false;
    server.use(
      http.get("/api/v1/object-types/activity", () => {
        answered = true;
        return new HttpResponse(null, { status: 500 });
      }),
    );
    renderAt(["/activity"]);

    await waitFor(() => expect(answered).toBe(true));
    expect(await screen.findByText("Object type not found.")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(pathname()).toBe("/activity");
  });

  // A fence: nothing redirects on the unfixed tree, so this cannot fail there.
  it("does not redirect while the lookup is pending", async () => {
    server.use(
      http.get("/api/v1/object-types/activity", async () => {
        await delay("infinite");
        return new HttpResponse(null, { status: 404 });
      }),
    );
    renderAt(["/activity"]);

    await delay(50);
    expect(pathname()).toBe("/activity");
  });
});

describe("old two-segment schema addresses", () => {
  it("redirects /schema/new to the new editor address", async () => {
    renderAt(["/schema/new"]);

    await waitFor(() => expect(pathname()).toBe("/workspace/schema/new"));
  });

  it("redirects /schema/new with replace, so Back leaves rather than bouncing", async () => {
    const user = userEvent.setup();
    renderAt(["/inbox", "/schema/new"]);

    await waitFor(() => expect(pathname()).toBe("/workspace/schema/new"));
    expect(screen.getByTestId("navigation-type")).toHaveTextContent("REPLACE");

    await user.click(screen.getByRole("button", { name: "probe back" }));

    await waitFor(() => expect(pathname()).toBe("/inbox"));
  });

  it("redirects /schema/<key> whether or not a type keyed schema exists", async () => {
    serveTypes(typeDetail("schema", "Clashing schema"));
    renderAt(["/schema/initiative"]);

    await waitFor(() => expect(pathname()).toBe("/workspace/schema/initiative"));
  });

  // A fence: the unfixed tree serves `/schema/new` from a static route, so no record lookup
  // happens there either. Measured by removing the wrapper route after the fix instead.
  it("sends no request to /api/v1/records/new", async () => {
    renderAt(["/schema/new"]);

    await screen.findByRole("heading", { level: 1 });
    expect(recordLookups).toEqual([]);
  });

  it("stays on the record route at /schema/<uuid>", async () => {
    renderAt([`/schema/${RECORD_UUID}`]);

    await waitFor(() => expect(recordLookups).toEqual([RECORD_UUID]));
    expect(pathname()).toBe(`/schema/${RECORD_UUID}`);
  });
});

describe("a type keyed workspace", () => {
  it("puts one aria-current on the page at /workspace/people, on People & agents", async () => {
    serveTypes(WORKSPACE_TYPE);
    renderAt(["/workspace/people"]);

    await screen.findByTestId("nav-type-workspace");
    const current = document.querySelectorAll('[aria-current="page"]');
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveAttribute("data-testid", "nav-people");
  });
});
