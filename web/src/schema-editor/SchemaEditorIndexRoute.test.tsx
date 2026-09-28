/**
 * "Create object type" is the one affordance gated by a **role** rather than a level, and
 * necessarily so: no object type exists yet to hold a level on. `create_object_type`'s rule is
 * `role >= creator`, which is why `GW_OIDC_CREATOR_GROUPS` matters — a creator can arrive from an
 * identity provider with no operator involved and must find the button waiting.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { DEFAULT_TEST_PRINCIPAL, renderWithProviders } from "../test/renderWithProviders";
import { SchemaEditorIndexRoute } from "./SchemaEditorIndexRoute";
import type { CurrentPrincipal } from "../api/auth";
import type { ObjectTypeSummary } from "../api/objectTypes";

const objectTypes: ObjectTypeSummary[] = [
  {
    key: "initiative",
    name: "Initiative",
    description: "A funded, sponsored workstream.",
    key_prefix: "INIT",
    record_count: 2,
    field_count: 3,
    your_access: "read",
  },
];

const server = setupServer(
  http.get("/api/v1/object-types", () => HttpResponse.json(objectTypes)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function principalWithRole(role: CurrentPrincipal["role"]): CurrentPrincipal {
  return { ...DEFAULT_TEST_PRINCIPAL, role, scope: role === "member" ? "write" : "admin" };
}

describe("SchemaEditorIndexRoute: the create gate", () => {
  it("offers 'Create object type' to an admin (fence)", async () => {
    renderWithProviders(<SchemaEditorIndexRoute />, { principal: principalWithRole("admin") });

    expect(
      await screen.findByRole("link", { name: "Create object type" }),
    ).toBeInTheDocument();
  });

  it("offers it to a creator, which is the role that exists for exactly this", async () => {
    renderWithProviders(<SchemaEditorIndexRoute />, { principal: principalWithRole("creator") });

    expect(
      await screen.findByRole("link", { name: "Create object type" }),
    ).toBeInTheDocument();
  });

  it("withholds it from a member, and says who creates object types instead", async () => {
    renderWithProviders(<SchemaEditorIndexRoute />, { principal: principalWithRole("member") });

    await screen.findByRole("heading", { name: "Schema", level: 1 });
    expect(screen.queryByRole("link", { name: "Create object type" })).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "Object types are created by administrators. Ask one if you need a new type.",
      ),
    ).toBeInTheDocument();
  });

  it("still lists every type the caller can read, whatever their role", async () => {
    renderWithProviders(<SchemaEditorIndexRoute />, { principal: principalWithRole("member") });

    expect(await screen.findByRole("link", { name: "Initiative" })).toBeInTheDocument();
  });
});
