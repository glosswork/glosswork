import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { RequireAuth } from "./RequireAuth";
import { LoginPage } from "./LoginPage";
import tileUrl from "../brand/mark-tile.svg";

function ProtectedPage() {
  return <h1>Protected content</h1>;
}

function TestApp() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/protected"
        element={
          <RequireAuth>
            <ProtectedPage />
          </RequireAuth>
        }
      />
    </Routes>
  );
}

let modes = { standalone: true, oidc: false };

const server = setupServer(
  http.get("/api/v1/auth/modes", () => HttpResponse.json(modes)),
  http.post("/api/v1/auth/login", async ({ request }) => {
    const body = (await request.json()) as { email: string; password: string };
    if (body.email === "dana@example.com" && body.password === "correct-horse-battery-staple") {
      return HttpResponse.json({
        id: "p1",
        display_name: "Dana",
        email: "dana@example.com",
        type: "user",
        role: "member",
        scope: "write",
        auth_method: "session",
      });
    }
    return HttpResponse.json(
      {
        error: {
          code: "invalid_credentials",
          message: "Email or password is incorrect.",
          details: {},
        },
      },
      { status: 401 },
    );
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  modes = { standalone: true, oidc: false };
});
afterAll(() => server.close());

describe("LoginPage", () => {
  it("redirects an unauthenticated visitor from a protected route to /login", async () => {
    renderWithProviders(<TestApp />, { route: "/protected", principal: null });

    expect(await screen.findByTestId("login-email")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  it("signs in and returns to the originally requested route", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TestApp />, { route: "/protected", principal: null });

    await user.type(await screen.findByTestId("login-email"), "dana@example.com");
    await user.type(screen.getByTestId("login-password"), "correct-horse-battery-staple");
    await user.click(screen.getByTestId("login-submit"));

    expect(await screen.findByText("Protected content")).toBeInTheDocument();
  });

  it("renders the server's failure message without distinguishing which half was wrong", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TestApp />, { route: "/protected", principal: null });

    await user.type(await screen.findByTestId("login-email"), "nobody@example.com");
    await user.type(screen.getByTestId("login-password"), "wrong-password");
    await user.click(screen.getByTestId("login-submit"));

    const error = await screen.findByTestId("login-error");
    expect(error).toHaveTextContent("Email or password is incorrect.");
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  it("shows only the password form in standalone mode", async () => {
    modes = { standalone: true, oidc: false };
    renderWithProviders(<TestApp />, { route: "/protected", principal: null });

    await screen.findByTestId("login-email");
    expect(screen.queryByTestId("login-oidc")).not.toBeInTheDocument();
  });

  it("shows only the Okta button in oidc mode", async () => {
    modes = { standalone: false, oidc: true };
    renderWithProviders(<TestApp />, { route: "/protected", principal: null });

    expect(await screen.findByTestId("login-oidc")).toBeInTheDocument();
    expect(screen.queryByTestId("login-email")).not.toBeInTheDocument();
  });

  it("shows both in both mode", async () => {
    modes = { standalone: true, oidc: true };
    renderWithProviders(<TestApp />, { route: "/protected", principal: null });

    expect(await screen.findByTestId("login-email")).toBeInTheDocument();
    expect(await screen.findByTestId("login-oidc")).toBeInTheDocument();
  });

  /**
   * DD-41 4.3: the login page shows the tile plus the wordmark, and nothing else.
   *
   * A locator assertion, not a screenshot, and deliberately so. `toHaveScreenshot` runs at
   * maxDiffPixelRatio 0.001, about 1,024 pixels on a 1280x800 shot, which is more ink than a
   * heading holds: renaming the product changed this exact `<h1>` and all 38 baselines still
   * passed, leaving a committed baseline of the old name. A passing visual suite is evidence
   * about layout only.
   */
  it("leads with the wordmark lockup", async () => {
    modes = { standalone: true, oidc: false };
    renderWithProviders(<TestApp />, { route: "/protected", principal: null });

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent(/^Glosswork$/);
    // The tile is decorative beside its own name, so it carries an empty alt. Compare against
    // the imported asset rather than a filename: Vite inlines a small SVG as a data URI, and
    // the rule that matters is that the mark is the committed file and never redrawn in a
    // component (web/src/brand/README.md).
    const tile = heading.querySelector("img");
    expect(tile).not.toBeNull();
    expect(tile?.getAttribute("src")).toBe(tileUrl);
    expect(tile?.getAttribute("alt")).toBe("");
  });
});
