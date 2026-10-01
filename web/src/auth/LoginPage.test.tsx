import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
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

let modes = { standalone: true, oidc: false, email_code: false };
/** Every code the fake workspace sent, by address; `verify` accepts only the last one. */
let sentCodes: Record<string, string> = {};
let codeRequests = 0;

const REQUEST_MESSAGE =
  "If this address can sign in here, a code is on its way. It works for 10 minutes.";
const VERIFY_FAILURE = "That code is not right, or it has expired. Ask for a new code.";

const server = setupServer(
  http.get("/api/v1/auth/modes", () => HttpResponse.json(modes)),
  http.post("/api/v1/auth/code/request", async ({ request }) => {
    const body = (await request.json()) as { email: string };
    codeRequests += 1;
    sentCodes[body.email] = String(100000 + codeRequests);
    return HttpResponse.json({ message: REQUEST_MESSAGE }, { status: 202 });
  }),
  http.post("/api/v1/auth/code/verify", async ({ request }) => {
    const body = (await request.json()) as { email: string; code: string };
    if (sentCodes[body.email] === body.code) {
      return HttpResponse.json({
        id: "p2",
        display_name: "Ada",
        email: body.email,
        type: "user",
        role: "member",
        scope: "write",
        auth_method: "session",
        auth_provider: "local",
      });
    }
    return HttpResponse.json(
      { error: { code: "invalid_credentials", message: VERIFY_FAILURE, details: {} } },
      { status: 401 },
    );
  }),
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
  modes = { standalone: true, oidc: false, email_code: false };
  sentCodes = {};
  codeRequests = 0;
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
    modes = { standalone: true, oidc: false, email_code: false };
    renderWithProviders(<TestApp />, { route: "/protected", principal: null });

    await screen.findByTestId("login-email");
    expect(screen.queryByTestId("login-oidc")).not.toBeInTheDocument();
  });

  it("shows only the Okta button in oidc mode", async () => {
    modes = { standalone: false, oidc: true, email_code: false };
    renderWithProviders(<TestApp />, { route: "/protected", principal: null });

    expect(await screen.findByTestId("login-oidc")).toBeInTheDocument();
    expect(screen.queryByTestId("login-email")).not.toBeInTheDocument();
  });

  it("shows both in both mode", async () => {
    modes = { standalone: true, oidc: true, email_code: false };
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
    modes = { standalone: true, oidc: false, email_code: false };
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

  describe("sign-in by emailed code (change 9)", () => {
    function useCodes() {
      modes = { standalone: false, oidc: false, email_code: true };
    }

    it("asks for an email, shows the server's sentence verbatim, and signs in with the code", async () => {
      useCodes();
      const user = userEvent.setup();
      renderWithProviders(<TestApp />, { route: "/protected", principal: null });

      await user.type(await screen.findByTestId("code-email"), "ada@example.com");
      expect(screen.queryByTestId("login-password")).not.toBeInTheDocument();
      await user.click(screen.getByTestId("code-send"));

      expect(await screen.findByTestId("code-sent")).toHaveTextContent(REQUEST_MESSAGE);
      await user.type(screen.getByTestId("code-value"), sentCodes["ada@example.com"]);
      await user.click(screen.getByTestId("code-submit"));

      expect(await screen.findByText("Protected content")).toBeInTheDocument();
    });

    it("renders the server's one failure message for a wrong code", async () => {
      useCodes();
      const user = userEvent.setup();
      renderWithProviders(<TestApp />, { route: "/protected", principal: null });

      await user.type(await screen.findByTestId("code-email"), "ada@example.com");
      await user.click(screen.getByTestId("code-send"));
      await user.type(await screen.findByTestId("code-value"), "999999");
      await user.click(screen.getByTestId("code-submit"));

      expect(await screen.findByTestId("login-error")).toHaveTextContent(VERIFY_FAILURE);
      expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    });

    it("sends another code, and the newest one signs in", async () => {
      useCodes();
      const user = userEvent.setup();
      renderWithProviders(<TestApp />, { route: "/protected", principal: null });

      await user.type(await screen.findByTestId("code-email"), "ada@example.com");
      await user.click(screen.getByTestId("code-send"));
      await screen.findByTestId("code-value");
      await user.click(screen.getByTestId("code-resend"));
      await vi.waitFor(() => expect(codeRequests).toBe(2));
      await user.type(screen.getByTestId("code-value"), sentCodes["ada@example.com"]);
      await user.click(screen.getByTestId("code-submit"));

      expect(await screen.findByText("Protected content")).toBeInTheDocument();
    });

    it("shows no form at all while the modes are loading, so no password form flashes", async () => {
      let release: () => void = () => {};
      const held = new Promise<void>((resolve) => {
        release = resolve;
      });
      useCodes();
      server.use(
        http.get("/api/v1/auth/modes", async () => {
          await held;
          return HttpResponse.json(modes);
        }),
      );
      renderWithProviders(<TestApp />, { route: "/protected", principal: null });

      await screen.findByRole("heading", { level: 1 });
      expect(screen.queryByTestId("login-email")).not.toBeInTheDocument();
      expect(screen.queryByTestId("login-password")).not.toBeInTheDocument();
      expect(screen.queryByTestId("code-email")).not.toBeInTheDocument();
      release();
      expect(await screen.findByTestId("code-email")).toBeInTheDocument();
      expect(screen.queryByTestId("login-password")).not.toBeInTheDocument();
    });
  });
});
