import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { IndexRoute } from "./IndexRoute";
import { DEFAULT_TEST_PRINCIPAL, renderWithProviders } from "../test/renderWithProviders";
import type { CurrentPrincipal } from "../api/auth";

/**
 * What `/` renders when `list_object_types` comes back empty.
 *
 * A component test rather than a screenshot, because the visual database always holds fixture
 * types by the time the visual suite runs and the state is not reachable by a screenshot. Everyone
 * except a member gets the first-run screen (`docs/DESIGN.md` 8.5), and this is the place the
 * branch is proven.
 */
const QUESTION = "What do you want to keep track of?";
const MEMBER_SENTENCE =
  "You do not have access to any object types yet. Ask an administrator to grant you access.";

const server = setupServer(
  http.get("/api/v1/object-types", () => HttpResponse.json([])),
  http.get("/api/v1/workspace", () =>
    HttpResponse.json({ name: "Northwind", people: 1, agents: 0, mcp_url: null }),
  ),
);

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/**
 * `resolveIndexRoute` is a pure function over the list and cannot tell "none exist" from "none you
 * can see" -- both are the same empty array, because `list_object_types` is filtered to types the
 * caller holds `read` on (FR-I11) and every object type is closed by default. The distinction is
 * available on the client without a backend change, from the role, and it is why first run does not
 * simply replace the empty state: a member told to paste a setup prompt has been told to do
 * something their token cannot do.
 */
describe("IndexRoute", () => {
  function principalWithRole(role: CurrentPrincipal["role"]): CurrentPrincipal {
    return { ...DEFAULT_TEST_PRINCIPAL, role, scope: role === "member" ? "write" : "admin" };
  }

  it("asks an admin the question, for whom an empty list really does mean an empty deployment", async () => {
    renderWithProviders(<IndexRoute />, { principal: principalWithRole("admin") });

    expect(await screen.findByRole("heading", { level: 1, name: QUESTION })).toBeInTheDocument();
  });

  it("asks a creator the question too, which is the role a trial workspace is set up from", async () => {
    renderWithProviders(<IndexRoute />, { principal: principalWithRole("creator") });

    expect(await screen.findByRole("heading", { level: 1, name: QUESTION })).toBeInTheDocument();
  });

  it("tells a member the truth instead: no access, not no types", async () => {
    renderWithProviders(<IndexRoute />, { principal: principalWithRole("member") });

    expect(await screen.findByText(MEMBER_SENTENCE)).toBeInTheDocument();
  });

  it("never asks a member the question, whose token could not answer it", async () => {
    // Asserted as the question's absence, not as the absence of an older admin sentence: that
    // string exists nowhere, so an assertion on it could never fail.
    renderWithProviders(<IndexRoute />, { principal: principalWithRole("member") });

    await screen.findByText(MEMBER_SENTENCE);
    expect(screen.queryByRole("heading", { level: 1, name: QUESTION })).not.toBeInTheDocument();
  });

  it("redirects rather than asking anything when an object type exists", async () => {
    server.use(
      http.get("/api/v1/object-types", () =>
        HttpResponse.json([{ key: "prospects", name: "Prospects", description: null }]),
      ),
    );
    renderWithProviders(<IndexRoute />, { principal: principalWithRole("admin") });

    // A fence for the redirect itself: what it proves is that the
    // question appears only on the empty branch.
    expect(screen.queryByRole("heading", { level: 1, name: QUESTION })).not.toBeInTheDocument();
  });
});
