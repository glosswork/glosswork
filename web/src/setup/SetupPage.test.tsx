/**
 * `/setup`: the personal access tokens panel, the search index panel, the export link and the
 * password panel.
 *
 * The access-token cases assert against table markup.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { SetupPage } from "./SetupPage";
import { renderWithProviders } from "../test/renderWithProviders";
import {
  CREATOR_PRINCIPAL,
  CREATOR_WRITE_PAT_PRINCIPAL,
  MEMBER_PRINCIPAL,
  TEST_PRINCIPAL_ID,
  accessTokens,
  identityHandlers,
  resetIdentityStores,
  stores,
} from "../test/identityFixtures";

const server = setupServer(...identityHandlers);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => resetIdentityStores());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** The one card every role sees, and so what a test waits on before asserting an absence. */
function tokensCard() {
  return screen.findByRole("region", { name: "Personal access tokens" });
}

describe("SetupPage", () => {
  describe("Personal access tokens", () => {
    it("mints a token, shows the plaintext exactly once, then it is gone from the DOM after dismissal", async () => {
      const user = userEvent.setup();
      vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
      renderWithProviders(<SetupPage />);

      const card = await tokensCard();
      await user.type(within(card).getByLabelText("Name"), "ci-runner");
      await user.click(within(card).getByRole("button", { name: "Mint token" }));

      const dialog = await screen.findByTestId("minted-token-dialog");
      // The dialog shows the minted token once; the mint form itself is inline rather than a
      // dialog of its own.
      expect(screen.getByRole("dialog", { name: "New access token" })).toBe(dialog);
      const plaintext = within(dialog).getByTestId("minted-token-plaintext").textContent;
      expect(plaintext).toMatch(/^gw_pat_/);
      expect(screen.getByText(/will not see this token again/)).toBeInTheDocument();

      await user.click(within(dialog).getByRole("button", { name: "Copy to clipboard" }));
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(plaintext);

      await user.click(within(dialog).getByRole("button", { name: "Done" }));
      expect(screen.queryByTestId("minted-token-dialog")).not.toBeInTheDocument();
      expect(screen.queryByText(plaintext as string)).not.toBeInTheDocument();

      // The table is the only other read of this token, and it only ever carries the prefix
      // (mirrors `AccessTokenService.list`, which never selects the secret column).
      const newRow = await within(card).findByTestId(
        `access-token-${stores.accessTokens[stores.accessTokens.length - 1].id}`,
      );
      expect(within(newRow).queryByText(plaintext as string)).not.toBeInTheDocument();
      expect(within(newRow).getByText("gw_pat_z")).toBeInTheDocument();
    });

    it(
      "surfaces the server's FR-A4 envelope beside the fixed title when minting a token " +
        "fails, with the fixed string kept as the title",
      async () => {
        const user = userEvent.setup();
        server.use(
          http.post(
            "/api/v1/access-tokens",
            () =>
              HttpResponse.json(
                {
                  error: {
                    code: "validation_failed",
                    message: "A token with that name already exists.",
                    details: {},
                  },
                },
                { status: 422 },
              ),
            { once: true },
          ),
        );
        renderWithProviders(<SetupPage />);

        const card = await tokensCard();
        await user.type(within(card).getByLabelText("Name"), "laptop");
        await user.click(within(card).getByRole("button", { name: "Mint token" }));

        const alert = await within(card).findByRole("alert");
        expect(within(alert).getByText("Could not mint the token.")).toBeInTheDocument();
        expect(alert.textContent).toContain("validation_failed");
        expect(alert.textContent).toContain("A token with that name already exists.");
        expect(screen.queryByTestId("minted-token-dialog")).not.toBeInTheDocument();
      },
    );

    it("offers only scopes the caller may mint: a 'member' role and 'write'-scope credential never offers 'admin'", async () => {
      // This only asserts what the UI *offers*; the server enforces both ceilings independently
      // (`AccessTokenService._check_ceilings`, already covered by backend tests).
      renderWithProviders(<SetupPage />, { principal: MEMBER_PRINCIPAL });

      const card = await tokensCard();
      const options = within(within(card).getByLabelText("Scope"))
        .getAllByRole("option")
        .map((option) => option.textContent);
      expect(options).toEqual(["read", "write"]);
      expect(options).not.toContain("admin");
    });

    it("offers 'admin' to an admin-role, admin-scope caller", async () => {
      renderWithProviders(<SetupPage />);

      const card = await tokensCard();
      const options = within(within(card).getByLabelText("Scope"))
        .getAllByRole("option")
        .map((option) => option.textContent);
      expect(options).toEqual(["read", "write", "admin"]);
    });

    it("offers 'admin' to a 'creator' role, because role_scope maps creator -> admin", async () => {
      // `mintableScopes` once branched on `role === "admin"`, so the third role could not obtain
      // from this panel the one credential it needs to reach the schema routes at all.
      renderWithProviders(<SetupPage />, { principal: CREATOR_PRINCIPAL });

      const card = await tokensCard();
      const options = within(within(card).getByLabelText("Scope"))
        .getAllByRole("option")
        .map((option) => option.textContent);
      expect(options).toEqual(["read", "write", "admin"]);
    });

    it("caps a creator's offer at its own credential's scope, below the role ceiling", async () => {
      renderWithProviders(<SetupPage />, { principal: CREATOR_WRITE_PAT_PRINCIPAL });

      const card = await tokensCard();
      const options = within(within(card).getByLabelText("Scope"))
        .getAllByRole("option")
        .map((option) => option.textContent);
      expect(options).toEqual(["read", "write"]);
      expect(options).not.toContain("admin");
    });

    it("revokes a token through a confirmation step, and the Status column says so", async () => {
      const user = userEvent.setup();
      renderWithProviders(<SetupPage />);

      const row = await screen.findByTestId("access-token-token-1");
      expect(screen.getByTestId("access-token-status-token-1")).toHaveTextContent("Active");
      await user.click(within(row).getByRole("button", { name: "Revoke" }));
      await user.click(within(row).getByRole("button", { name: "Confirm revoke" }));

      expect(within(row).queryByRole("button", { name: "Revoke" })).not.toBeInTheDocument();
      // Was a `<dl>` row reading "Revoked: no", which is a double negative a reader has to
      // unpack. A `Status` column is one word per row and comparable down the table.
      expect(await screen.findByTestId("access-token-status-token-1")).toHaveTextContent(
        "Revoked",
      );
      expect(
        stores.accessTokens.find((token) => token.id === "token-1")?.revoked_at,
      ).not.toBeNull();
    });

    it("mints with an agent label and sends it in the body", async () => {
      // The label is what makes a connector added through Claude's dialog
      // attributable at all, so the form has to be able to set it.
      const user = userEvent.setup();
      vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
      let capturedBody: unknown = null;
      server.use(
        http.post(
          "/api/v1/access-tokens",
          async ({ request }) => {
            capturedBody = await request.json();
            return HttpResponse.json(
              {
                id: "token-2",
                principal_id: TEST_PRINCIPAL_ID,
                name: "claude desktop",
                token_prefix: "gw_pat_b",
                scope: "read",
                expires_at: null,
                last_used_at: null,
                revoked_at: null,
                created_at: "2026-09-16T10:00:00",
                created_by: TEST_PRINCIPAL_ID,
                agent_label: "claude-desktop",
                token: "gw_pat_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              },
              { status: 201 },
            );
          },
          { once: true },
        ),
      );
      renderWithProviders(<SetupPage />);

      const card = await tokensCard();
      await user.type(within(card).getByLabelText("Name"), "claude desktop");
      await user.type(within(card).getByLabelText("Agent label (optional)"), "claude-desktop");
      await user.click(within(card).getByRole("button", { name: "Mint token" }));

      await screen.findByTestId("minted-token-dialog");
      expect(capturedBody).toMatchObject({ agent_label: "claude-desktop" });
    });

    it("sends an expiry as the end of the picked day UTC, with no fractional seconds", async () => {
      // Routing the picked date through `Date` sends the browser's own ISO rendering, which
      // always carries three fractional digits, and the server's strict `parse_datetime` refuses
      // them, so every date a user picked would come back `validation_failed`. This is the case
      // that localises such a regression to the frontend in two seconds rather than seventeen.
      //
      // The literal is fixed rather than derived, and the instant is the end of the picked day
      // **UTC**, so the expected string does not depend on the machine's timezone.
      const user = userEvent.setup();
      vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue(undefined);
      let capturedBody: unknown = null;
      server.use(
        http.post(
          "/api/v1/access-tokens",
          async ({ request }) => {
            capturedBody = await request.json();
            return HttpResponse.json(
              {
                id: "token-3",
                principal_id: TEST_PRINCIPAL_ID,
                name: "expiring token",
                token_prefix: "gw_pat_c",
                scope: "read",
                expires_at: "2027-01-31T23:59:59Z",
                last_used_at: null,
                revoked_at: null,
                created_at: "2026-09-24T10:00:00",
                created_by: TEST_PRINCIPAL_ID,
                agent_label: null,
                token: "gw_pat_cccccccccccccccccccccccccccccccc",
              },
              { status: 201 },
            );
          },
          { once: true },
        ),
      );
      renderWithProviders(<SetupPage />);

      const card = await tokensCard();
      await user.type(within(card).getByLabelText("Name"), "expiring token");
      await user.type(within(card).getByLabelText("Expires at (optional)"), "2027-01-31");
      await user.click(within(card).getByRole("button", { name: "Mint token" }));

      await screen.findByTestId("minted-token-dialog");
      const sent = (capturedBody as { expires_at?: string } | null)?.expires_at;
      expect(sent).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
      expect(sent).toBe("2027-01-31T23:59:59Z");
    });

    it("offers no date the server would refuse", async () => {
      // Without a `min` the picker offers dates that mint as `validation_failed`. The floor is the
      // current **UTC** date, because the instant sent is the end of the picked day UTC: a viewer
      // in New York at 21:00 has a local "today" that is already tomorrow in UTC, and offering it
      // their local today would refuse it again.
      renderWithProviders(<SetupPage />);

      const card = await tokensCard();
      const expiresAt = within(card).getByLabelText("Expires at (optional)");
      // Spelled out from UTC getters rather than through the helper under test, so this asserts
      // the floor rather than restating whatever the helper returned.
      const now = new Date();
      const pad = (value: number) => String(value).padStart(2, "0");
      const utcToday = `${now.getUTCFullYear()}-${pad(now.getUTCMonth() + 1)}-${pad(now.getUTCDate())}`;

      expect(expiresAt).toHaveAttribute("min", utcToday);
    });

    it("shows which tool a token belongs to in an Agent column", async () => {
      // The question a person asks right before revoking one, which is why it is a
      // column rather than something you open a row to see.
      renderWithProviders(<SetupPage />);

      const card = await tokensCard();
      // The row first: the table renders only once the query resolves, so querying the
      // headers synchronously reads a card that is still loading.
      const row = await within(card).findByTestId("access-token-token-1");
      const headers = within(card)
        .getAllByRole("columnheader")
        .map((header) => header.textContent);
      expect(headers).toContain("Agent");

      expect(within(row).getByTestId("access-token-agent-token-1")).toHaveTextContent(
        "claude-code",
      );
    });

    it("is visible to a member, because a token is a fact about your own access", async () => {
      renderWithProviders(<SetupPage />, { principal: MEMBER_PRINCIPAL });
      expect(await tokensCard()).toBeInTheDocument();
    });
  });

  describe("Password", () => {
    // `auth_provider` on `CurrentPrincipal` is what decides whether the panel offers a password
    // change, so each principal spreads it in explicitly rather than relying on the default.
    const LOCAL_PRINCIPAL = { ...MEMBER_PRINCIPAL, auth_provider: "local" as const };
    const OIDC_PRINCIPAL = { ...MEMBER_PRINCIPAL, auth_provider: "oidc" as const };

    function passwordCard() {
      return screen.findByRole("region", { name: "Password" });
    }

    it("offers no password change on a workspace that signs people in by emailed code", async () => {
      // Change 9, DQ1. The control is the case below it: the same local person on a password
      // workspace gets the form.
      stores.modes = { standalone: false, oidc: false, email_code: true };
      renderWithProviders(<SetupPage />, { principal: LOCAL_PRINCIPAL });

      const card = await passwordCard();
      expect(await within(card).findByTestId("password-email-code")).toHaveTextContent(
        "You sign in to this workspace with a code sent to your email",
      );
      expect(within(card).queryByLabelText("Current password")).not.toBeInTheDocument();
    });

    it("shows Current password, New password and Confirm new password fields with the right autocomplete", async () => {
      renderWithProviders(<SetupPage />, { principal: LOCAL_PRINCIPAL });

      const card = await passwordCard();
      expect(within(card).getByLabelText("Current password")).toHaveAttribute(
        "autocomplete",
        "current-password",
      );
      expect(within(card).getByLabelText("New password")).toHaveAttribute(
        "autocomplete",
        "new-password",
      );
      expect(within(card).getByLabelText("Confirm new password")).toHaveAttribute(
        "autocomplete",
        "new-password",
      );
    });

    it("shows the revocation sentence before anything is submitted", async () => {
      renderWithProviders(<SetupPage />, { principal: LOCAL_PRINCIPAL });

      const card = await passwordCard();
      expect(
        within(card).getByText(
          "Changing it signs you out everywhere else and revokes all of your personal access tokens.",
        ),
      ).toBeInTheDocument();
    });

    it("changes the password, posting {current_password, new_password}, then shows success and clears all three fields", async () => {
      const user = userEvent.setup();
      let capturedBody: unknown = null;
      server.use(
        http.post("/api/v1/me/password", async ({ request }) => {
          capturedBody = await request.json();
          return HttpResponse.json({
            id: LOCAL_PRINCIPAL.id,
            display_name: LOCAL_PRINCIPAL.display_name,
            email: LOCAL_PRINCIPAL.email,
            type: LOCAL_PRINCIPAL.type,
            role: LOCAL_PRINCIPAL.role,
            scope: LOCAL_PRINCIPAL.scope,
            auth_method: LOCAL_PRINCIPAL.auth_method,
            auth_provider: "local",
          });
        }),
      );
      renderWithProviders(<SetupPage />, { principal: LOCAL_PRINCIPAL });

      const card = await passwordCard();
      await user.type(within(card).getByLabelText("Current password"), "old-password-1");
      await user.type(within(card).getByLabelText("New password"), "new-password-1");
      await user.type(within(card).getByLabelText("Confirm new password"), "new-password-1");
      await user.click(within(card).getByRole("button", { name: "Change password" }));

      expect(
        await within(card).findByText(
          "Password changed. Your other sessions and personal access tokens were revoked.",
        ),
      ).toBeInTheDocument();
      expect(capturedBody).toEqual({
        current_password: "old-password-1",
        new_password: "new-password-1",
      });
      expect(within(card).getByLabelText("Current password")).toHaveValue("");
      expect(within(card).getByLabelText("New password")).toHaveValue("");
      expect(within(card).getByLabelText("Confirm new password")).toHaveValue("");
    });

    it("renders a wrong current password as the Current password field's accessible description", async () => {
      const user = userEvent.setup();
      server.use(
        http.post("/api/v1/me/password", () =>
          HttpResponse.json(
            {
              error: {
                code: "validation_failed",
                message: "The current password is not correct.",
                details: { field_key: "current_password" },
              },
            },
            { status: 422 },
          ),
        ),
      );
      renderWithProviders(<SetupPage />, { principal: LOCAL_PRINCIPAL });

      const card = await passwordCard();
      await user.type(within(card).getByLabelText("Current password"), "wrong-password");
      await user.type(within(card).getByLabelText("New password"), "new-password-1");
      await user.type(within(card).getByLabelText("Confirm new password"), "new-password-1");
      await user.click(within(card).getByRole("button", { name: "Change password" }));

      await within(card).findByText("The current password is not correct.");
      expect(within(card).getByLabelText("Current password")).toHaveAccessibleDescription(
        "The current password is not correct.",
      );
    });

    it("issues no request when the new password and its confirmation do not match, and shows an error", async () => {
      const user = userEvent.setup();
      let requestCount = 0;
      server.use(
        http.post("/api/v1/me/password", () => {
          requestCount += 1;
          return HttpResponse.json(
            { error: { code: "validation_failed", message: "unused", details: {} } },
            { status: 422 },
          );
        }),
      );
      renderWithProviders(<SetupPage />, { principal: LOCAL_PRINCIPAL });

      const card = await passwordCard();
      await user.type(within(card).getByLabelText("Current password"), "old-password-1");
      await user.type(within(card).getByLabelText("New password"), "new-password-1");
      await user.type(within(card).getByLabelText("Confirm new password"), "something-else");
      await user.click(within(card).getByRole("button", { name: "Change password" }));

      expect(await within(card).findByRole("alert")).toBeInTheDocument();
      expect(requestCount).toBe(0);
    });

    it("shows an OIDC principal a sentence instead of a form, with no password inputs", async () => {
      renderWithProviders(<SetupPage />, { principal: OIDC_PRINCIPAL });

      const card = await passwordCard();
      expect(
        within(card).getByText(
          "Your password is managed by your identity provider, not by this deployment.",
        ),
      ).toBeInTheDocument();
      expect(within(card).queryByLabelText("Current password")).not.toBeInTheDocument();
      expect(within(card).queryByLabelText("New password")).not.toBeInTheDocument();
      expect(within(card).queryByLabelText("Confirm new password")).not.toBeInTheDocument();
    });
  });

  describe("Search index (admin-only)", () => {
    it("renders for an admin with counts and the embedding model", async () => {
      renderWithProviders(<SetupPage />);

      const card = await screen.findByRole("region", { name: "Search index" });
      await within(card).findByText("bge-small-en-v1.5");
      expect(within(card).getByText("3")).toBeInTheDocument(); // Pending jobs
      expect(within(card).getByText("1")).toBeInTheDocument(); // Running jobs
      expect(within(card).getByText("128")).toBeInTheDocument(); // Indexed chunks
      expect(within(card).getByText("enabled")).toBeInTheDocument();
    });

    it("is not rendered at all for a member, not merely rendered and left to a 403", async () => {
      renderWithProviders(<SetupPage />, { principal: MEMBER_PRINCIPAL });

      await tokensCard();
      expect(screen.queryByRole("region", { name: "Search index" })).not.toBeInTheDocument();
    });

    it("renders the failed-job list", async () => {
      renderWithProviders(<SetupPage />);

      const card = await screen.findByRole("region", { name: "Search index" });
      const failedJobs = await within(card).findByTestId("failed-jobs");
      expect(within(failedJobs).getByText("INIT-014")).toBeInTheDocument();
      expect(within(failedJobs).getByText("field scope_summary")).toBeInTheDocument();
      expect(within(failedJobs).getByText(/attempts: 2/)).toBeInTheDocument();
      expect(within(failedJobs).getByText("embedding provider timed out")).toBeInTheDocument();
    });

    it("shows 'No failed jobs.' when there are none", async () => {
      stores.searchIndexStatus = { ...stores.searchIndexStatus, failed_jobs: [] };
      renderWithProviders(<SetupPage />);

      const card = await screen.findByRole("region", { name: "Search index" });
      expect(await within(card).findByText("No failed jobs.")).toBeInTheDocument();
      expect(within(card).queryByTestId("failed-jobs")).not.toBeInTheDocument();
    });

    it("shows the stale-chunk warning only when stale_chunks > 0", async () => {
      renderWithProviders(<SetupPage />);

      const card = await screen.findByRole("region", { name: "Search index" });
      await within(card).findByText("bge-small-en-v1.5");
      expect(within(card).queryByTestId("stale-warning")).not.toBeInTheDocument();
    });

    it("keeps the warn family for the stale-chunk alert, which is a caution about data", async () => {
      // The agent label does not use `tone="warning"`: a label nobody has named yet is not in the
      // family docs/DESIGN.md rule 3 reserves for overdue and failed. This is the other kind:
      // chunks embedded by a model the deployment no longer runs. The distinction is the whole
      // of rule 3, so it is asserted rather than left to a blanket ban that would fail correct
      // code.
      stores.searchIndexStatus = { ...stores.searchIndexStatus, stale_chunks: 40 };
      renderWithProviders(<SetupPage />);

      const card = await screen.findByRole("region", { name: "Search index" });
      const warning = await within(card).findByTestId("stale-warning");
      expect(warning).toBeInTheDocument();
      expect(warning.className).toContain("warn");
    });

    it("shows the disabled note when semantic_enabled is false", async () => {
      stores.searchIndexStatus = { ...stores.searchIndexStatus, semantic_enabled: false };
      renderWithProviders(<SetupPage />);

      const card = await screen.findByRole("region", { name: "Search index" });
      expect(await within(card).findByTestId("semantic-disabled")).toBeInTheDocument();
      expect(within(card).getByText("disabled")).toBeInTheDocument();
    });

    it("re-indexes through a confirm step and shows the enqueued count", async () => {
      const user = userEvent.setup();
      renderWithProviders(<SetupPage />);

      const card = await screen.findByRole("region", { name: "Search index" });
      await within(card).findByText("bge-small-en-v1.5");
      await user.click(within(card).getByRole("button", { name: "Re-index everything" }));
      expect(
        within(card).getByText(/Every indexed field and comment will be re-embedded/),
      ).toBeInTheDocument();

      await user.click(within(card).getByRole("button", { name: "Confirm re-index" }));

      expect(await within(card).findByTestId("reindex-result")).toHaveTextContent(
        "Enqueued 42 jobs.",
      );
    });

    it("cancels the confirm step without calling the trigger", async () => {
      const user = userEvent.setup();
      renderWithProviders(<SetupPage />);

      const card = await screen.findByRole("region", { name: "Search index" });
      await within(card).findByText("bge-small-en-v1.5");
      await user.click(within(card).getByRole("button", { name: "Re-index everything" }));
      await user.click(within(card).getByRole("button", { name: "Cancel" }));

      expect(
        within(card).queryByRole("button", { name: "Confirm re-index" }),
      ).not.toBeInTheDocument();
      expect(within(card).queryByTestId("reindex-result")).not.toBeInTheDocument();
    });

    it("shows a 409 feature_disabled response's message in a role=alert", async () => {
      server.use(
        http.post("/api/v1/admin/search-index/reindex", () =>
          HttpResponse.json(
            {
              error: {
                code: "feature_disabled",
                message: "Semantic search is disabled on this deployment.",
                details: { feature: "reindex", setting: "GW_EMBEDDING_ENABLED" },
              },
            },
            { status: 409 },
          ),
        ),
      );
      const user = userEvent.setup();
      renderWithProviders(<SetupPage />);

      const card = await screen.findByRole("region", { name: "Search index" });
      await within(card).findByText("bge-small-en-v1.5");
      await user.click(within(card).getByRole("button", { name: "Re-index everything" }));
      await user.click(within(card).getByRole("button", { name: "Confirm re-index" }));

      expect(await within(card).findByRole("alert")).toHaveTextContent(
        "Semantic search is disabled on this deployment.",
      );
    });
  });

  describe("Export (admin-only)", () => {
    it("offers the full-deployment export as a link at the route that serves it (FR-E5)", async () => {
      renderWithProviders(<SetupPage />);

      const card = await screen.findByRole("region", { name: "Export" });
      const link = within(card).getByTestId("export-link");
      // An `<a>`, not a button, and asserted as one: `GET` is a safe method, so the browser
      // sends the session cookie and streams the response to disk under the route's own
      // `Content-Disposition`. A button would mean `fetch()` and a Blob, which is the reason
      // backup is not here (DD-36).
      expect(link.tagName).toBe("A");
      expect(link).toHaveAttribute("href", "/api/v1/admin/export");
      expect(link).toHaveAttribute("download");
    });

    it("says that a backup is an operator procedure rather than offering one", async () => {
      renderWithProviders(<SetupPage />);

      const card = await screen.findByRole("region", { name: "Export" });
      expect(within(card).getByText(/operator procedure/)).toBeInTheDocument();
      expect(
        within(card).queryByRole("button", { name: /backup/i }),
      ).not.toBeInTheDocument();
    });

    it("is not rendered at all for a member", async () => {
      renderWithProviders(<SetupPage />, { principal: MEMBER_PRINCIPAL });

      await tokensCard();
      expect(screen.queryByRole("region", { name: "Export" })).not.toBeInTheDocument();
    });
  });

  describe("every mutation has an error surface", () => {
    it("revoking an access token", async () => {
      const user = userEvent.setup();
      server.use(
        http.delete(
          "/api/v1/access-tokens/:id",
          () =>
            HttpResponse.json(
              {
                error: {
                  code: "validation_failed",
                  message: "That token is already revoked.",
                  details: {},
                },
              },
              { status: 422 },
            ),
          { once: true },
        ),
      );
      renderWithProviders(<SetupPage />);

      const card = await tokensCard();
      const row = await within(card).findByTestId(`access-token-${accessTokens[0].id}`);
      await user.click(within(row).getByRole("button", { name: "Revoke" }));
      await user.click(within(row).getByRole("button", { name: "Confirm revoke" }));

      const alert = await within(card).findByRole("alert");
      expect(within(alert).getByText("Could not revoke the token.")).toBeInTheDocument();
      expect(alert.textContent).toContain("validation_failed");
      expect(alert.textContent).toContain("That token is already revoked.");
    });
  });
});
