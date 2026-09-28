/**
 * `/people`: people, agent labels and service accounts, asserted against the table markup.
 *
 * Four shapes are worth knowing, and each is noted where it sits:
 * `(unverified)` is a `Verified` column, `(inactive)` is a `Status` column, the agent labels are
 * one grouped table, and the two admin create forms are native dialogs.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { PeoplePage } from "./PeoplePage";
import { renderWithProviders } from "../test/renderWithProviders";
import {
  CREATOR_PRINCIPAL,
  MEMBER_PRINCIPAL,
  TEST_PRINCIPAL_ID,
  identityHandlers,
  otherPrincipalId,
  resetIdentityStores,
  stores,
} from "../test/identityFixtures";

const server = setupServer(...identityHandlers);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => resetIdentityStores());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/** The agent-label card is the one card every role sees, so it is what a test waits on to know
 * the page has finished its first render before asserting that something else is absent. */
function agentLabelsCard() {
  return screen.findByRole("region", { name: "Agent labels" });
}

describe("PeoplePage", () => {
  describe("Agent labels", () => {
    it("renders the caller's own agent labels", async () => {
      renderWithProviders(<PeoplePage />);

      const row = await screen.findByTestId("agent-label-label-1");
      expect(within(row).getByText("claude-code")).toBeInTheDocument();
      expect(within(row).getByText("Claude Code")).toBeInTheDocument();
      expect(within(row).getByText("The CLI agent.")).toBeInTheDocument();
      expect(within(row).getByText("42")).toBeInTheDocument();
    });

    it("renders verification as a column value, never as a warning pill", async () => {
      // Not `<Badge tone="warning">(unverified)`: `ui/Badge.tsx` paints that tone in the `warn`
      // family, which docs/DESIGN.md rule 3
      // reserves for semantic status "so a warning cannot be read as an agent". FR-I6 is
      // explicit that an unknown label is accepted and never rejected, so it is not a warning.
      renderWithProviders(<PeoplePage />);

      await screen.findByTestId("agent-label-label-1");
      expect(screen.getByTestId("agent-label-verified-label-1")).toHaveTextContent("Yes");
      expect(screen.getByTestId("agent-label-verified-label-2")).toHaveTextContent("No");
      expect(screen.queryByText("(unverified)")).not.toBeInTheDocument();
    });

    it("edits a label's display name and description, and the row updates from the response", async () => {
      const user = userEvent.setup();
      renderWithProviders(<PeoplePage />);

      const row = await screen.findByTestId("agent-label-label-2");
      await user.click(within(row).getByRole("button", { name: "Edit" }));
      await user.clear(within(row).getByLabelText("Display name"));
      await user.type(within(row).getByLabelText("Display name"), "My Renamed Agent");
      await user.clear(within(row).getByLabelText("Description"));
      await user.type(within(row).getByLabelText("Description"), "A freshly described agent.");
      await user.click(within(row).getByRole("button", { name: "Save" }));

      expect(await within(row).findByText("My Renamed Agent")).toBeInTheDocument();
      expect(within(row).getByText("A freshly described agent.")).toBeInTheDocument();
      // Naming a label verifies it (FR-I6), which the column now says out loud.
      expect(screen.getByTestId("agent-label-verified-label-2")).toHaveTextContent("Yes");
    });

    it("groups every principal's labels by owner for an admin, and only the caller's own are editable", async () => {
      // Replaces two assertions on two panels. The authority is a property of a row rather than
      // of a panel: `PATCH /agent-labels/{id}` takes a label the caller owns and refuses any
      // other, so "editable" is `principal_id === me` and the table can hold both.
      renderWithProviders(<PeoplePage />);

      const ownGroup = await screen.findByTestId(`agent-label-group-${TEST_PRINCIPAL_ID}`);
      expect(within(ownGroup).getByText("claude-code")).toBeInTheDocument();
      expect(within(ownGroup).getByText("unnamed-agent")).toBeInTheDocument();
      expect(within(ownGroup).getAllByRole("button", { name: "Edit" })).toHaveLength(2);

      const otherGroup = screen.getByTestId(`agent-label-group-${otherPrincipalId}`);
      expect(within(otherGroup).getByText("other-agent")).toBeInTheDocument();
      expect(within(otherGroup).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    });

    it("names the owner through the attribution primitive, not as a raw id (FR-I7)", async () => {
      renderWithProviders(<PeoplePage />);

      const ownGroup = await screen.findByTestId(`agent-label-group-${TEST_PRINCIPAL_ID}`);
      expect(within(ownGroup).getByText("Test Admin")).toBeInTheDocument();

      const otherGroup = screen.getByTestId(`agent-label-group-${otherPrincipalId}`);
      expect(within(otherGroup).getByText("Other User")).toBeInTheDocument();
      // docs/DESIGN.md 6: one primitive renders every principal anywhere in the product.
      expect(within(otherGroup).getAllByTestId("hand").length).toBeGreaterThan(0);
    });

    it("falls back to the raw id when a group's principal has no matching directory entry", async () => {
      stores.principals = stores.principals.filter(
        (principal) => principal.id !== otherPrincipalId,
      );
      renderWithProviders(<PeoplePage />);

      const otherGroup = await screen.findByTestId(`agent-label-group-${otherPrincipalId}`);
      expect(within(otherGroup).getByText(otherPrincipalId)).toBeInTheDocument();
    });

    it("issues no admin request for a member, who reads their own labels alone", async () => {
      // A panel with no role gate fires `GET /admin/agent-labels` and `GET /principals` for
      // every caller, both of which declare the `admin` role (FR-I10), so a member takes two
      // guaranteed 403s and reads "Could not load agent labels." Against such a panel this
      // assertion reports exactly those two paths.
      renderWithProviders(<PeoplePage />, { principal: MEMBER_PRINCIPAL });

      await screen.findByTestId("agent-label-label-1");
      expect(stores.requests).toEqual(["/agent-labels"]);
    });

    it("tells a non-admin why the rest of the page is not there", async () => {
      // DD-42 applied to a role rather than to a grant: the page is called "People & agents"
      // and a member sees no people, so the absence gets a reason. docs/DESIGN.md 8.1 set the
      // precedent for the Inbox badge, which is shown without its count rather than hidden.
      renderWithProviders(<PeoplePage />, { principal: MEMBER_PRINCIPAL });

      await agentLabelsCard();
      expect(screen.getByTestId("people-admin-only-note")).toHaveTextContent(
        /administrator-only/,
      );
    });
  });

  describe("People and Service accounts (admin-only)", () => {
    it("renders for an admin", async () => {
      renderWithProviders(<PeoplePage />);
      expect(await screen.findByRole("region", { name: "People" })).toBeInTheDocument();
      expect(screen.getByRole("region", { name: "Service accounts" })).toBeInTheDocument();
    });

    it("is not rendered at all for a member, not merely rendered and left to a 403", async () => {
      renderWithProviders(<PeoplePage />, { principal: MEMBER_PRINCIPAL });

      await agentLabelsCard();
      expect(screen.queryByRole("region", { name: "People" })).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Service accounts" })).not.toBeInTheDocument();
    });

    it("is not rendered for a creator either, whose role reaches schema but not principals", async () => {
      // FR-I10: only an `admin`-role principal reaches `/api/v1/principals*`. A creator holds
      // the `admin` SCOPE through `role_scope`, which is the axis that gates the schema routes,
      // and the two are deliberately different questions (DD-11).
      renderWithProviders(<PeoplePage />, { principal: CREATOR_PRINCIPAL });

      await agentLabelsCard();
      expect(screen.queryByRole("region", { name: "People" })).not.toBeInTheDocument();
    });

    it("invites a new user through a dialog, who then appears in the table", async () => {
      const user = userEvent.setup();
      renderWithProviders(<PeoplePage />);

      const peopleCard = await screen.findByRole("region", { name: "People" });
      await user.click(within(peopleCard).getByRole("button", { name: "Invite" }));

      const dialog = await screen.findByTestId("invite-user-dialog");
      expect(screen.getByRole("dialog", { name: "Invite user" })).toBe(dialog);
      const inviteForm = within(dialog).getByRole("form", { name: "Invite user" });
      await user.type(within(inviteForm).getByLabelText("Display name"), "New Person");
      await user.type(within(inviteForm).getByLabelText("Email"), "new-person@example.com");
      await user.selectOptions(within(inviteForm).getByLabelText("Role"), "member");
      await user.click(within(inviteForm).getByRole("button", { name: "Create user" }));

      const newRow = await within(peopleCard).findByTestId("user-principal-3");
      expect(within(newRow).getByText("New Person")).toBeInTheDocument();
      expect(within(newRow).getByText("new-person@example.com")).toBeInTheDocument();
      expect(stores.principals.some((p) => p.email === "new-person@example.com")).toBe(true);
      // The dialog closes on success, so the table is what reports the result.
      expect(screen.queryByTestId("invite-user-dialog")).not.toBeInTheDocument();
    });

    it(
      "surfaces the server's FR-A4 envelope beside the fixed title when inviting a user " +
        "fails, with the fixed string kept as the title",
      async () => {
        const user = userEvent.setup();
        server.use(
          http.post(
            "/api/v1/principals",
            () =>
              HttpResponse.json(
                {
                  error: {
                    code: "validation_failed",
                    message: "That email is already in use.",
                    details: {},
                  },
                },
                { status: 422 },
              ),
            { once: true },
          ),
        );
        renderWithProviders(<PeoplePage />);

        const peopleCard = await screen.findByRole("region", { name: "People" });
        await user.click(within(peopleCard).getByRole("button", { name: "Invite" }));
        const dialog = await screen.findByTestId("invite-user-dialog");
        await user.type(within(dialog).getByLabelText("Display name"), "Dup Person");
        await user.type(within(dialog).getByLabelText("Email"), "dup@example.com");
        await user.click(within(dialog).getByRole("button", { name: "Create user" }));

        // The error stays in the dialog: closing it on a failure would take the message with it.
        const alert = await within(dialog).findByRole("alert");
        expect(within(alert).getByText("Could not create the user.")).toBeInTheDocument();
        expect(alert.textContent).toContain("validation_failed");
        expect(alert.textContent).toContain("That email is already in use.");
      },
    );

    it("changes a user's role", async () => {
      const user = userEvent.setup();
      renderWithProviders(<PeoplePage />);

      const row = await screen.findByTestId(`user-${otherPrincipalId}`);
      // The row's select is named for its row (docs/DESIGN.md 10): a table of selects all called
      // "Role" tells a screen-reader user nothing, which is the argument 7.11 already made about
      // four buttons called "?".
      await user.selectOptions(within(row).getByLabelText("Role for Other User"), "admin");

      await within(row).findByDisplayValue("admin");
      expect(stores.principals.find((p) => p.id === otherPrincipalId)?.role).toBe("admin");
    });

    it("promotes a user to 'creator', the third system role", async () => {
      const user = userEvent.setup();
      renderWithProviders(<PeoplePage />);

      const row = await screen.findByTestId(`user-${otherPrincipalId}`);
      const select = within(row).getByLabelText("Role for Other User");
      expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
        "admin",
        "creator",
        "member",
      ]);

      await user.selectOptions(select, "creator");

      await within(row).findByDisplayValue("creator");
      expect(stores.principals.find((p) => p.id === otherPrincipalId)?.role).toBe("creator");
    });

    it("offers 'creator' in the invite dialog's role select too", async () => {
      const user = userEvent.setup();
      renderWithProviders(<PeoplePage />);

      const peopleCard = await screen.findByRole("region", { name: "People" });
      await user.click(within(peopleCard).getByRole("button", { name: "Invite" }));
      const form = await screen.findByRole("form", { name: "Invite user" });
      const select = within(form).getByLabelText("Role");
      expect(within(select).getAllByRole("option").map((option) => option.textContent)).toEqual([
        "admin",
        "creator",
        "member",
      ]);
    });

    it("deactivates a user through a confirmation step, and the Status column says so", async () => {
      const user = userEvent.setup();
      renderWithProviders(<PeoplePage />);

      const row = await screen.findByTestId(`user-${otherPrincipalId}`);
      expect(within(row).getByText("Active")).toBeInTheDocument();
      await user.click(within(row).getByRole("button", { name: "Deactivate" }));
      expect(within(row).getByText(/Deactivate Other User\?/)).toBeInTheDocument();
      await user.click(within(row).getByRole("button", { name: "Confirm deactivate" }));

      // Was `(inactive)`, a neutral Badge rendered only in the negative case: a row said nothing
      // at all when the person was active, so "is this person still here" was a question you
      // answered by noticing an absence.
      expect(await within(row).findByText("Inactive")).toBeInTheDocument();
      expect(stores.principals.find((p) => p.id === otherPrincipalId)?.is_active).toBe(false);
    });

    it("disables role and deactivate controls for the only active administrator, with an explanation", async () => {
      // Fence: the guard is on the whole `<Select>`, so `creator` is refused for this row for
      // free. A fence: it guards behaviour nothing here alters.
      renderWithProviders(<PeoplePage />);

      const row = await screen.findByTestId(`user-${TEST_PRINCIPAL_ID}`);
      const select = within(row).getByLabelText("Role for Test Admin");
      expect(select).toBeDisabled();
      expect(
        within(select).getAllByRole("option").map((option) => option.textContent),
      ).toContain("creator");
      expect(within(row).queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();

      // The sentence moved out of the row and under the table, where it explains a disabled
      // control a reader can see rather than being a third line inside one card among six.
      const peopleCard = screen.getByRole("region", { name: "People" });
      expect(
        within(peopleCard).getByText("This is the only active administrator."),
      ).toBeInTheDocument();
    });

    it("does not submit a service account with an empty description, and validates client-side", async () => {
      const user = userEvent.setup();
      renderWithProviders(<PeoplePage />);

      const card = await screen.findByRole("region", { name: "Service accounts" });
      await user.click(within(card).getByRole("button", { name: "New service account" }));
      const dialog = await screen.findByTestId("create-service-account-dialog");
      await user.type(within(dialog).getByLabelText("Display name"), "CI Bot");
      await user.click(within(dialog).getByRole("button", { name: "Create service account" }));

      expect(
        await within(dialog).findByText(
          "A service account needs a description naming its purpose.",
        ),
      ).toBeInTheDocument();
      expect(stores.principals.some((p) => p.type === "service_account")).toBe(false);
    });

    it("creates a service account with a description, which round-trips to the server", async () => {
      const user = userEvent.setup();
      renderWithProviders(<PeoplePage />);

      const card = await screen.findByRole("region", { name: "Service accounts" });
      await user.click(within(card).getByRole("button", { name: "New service account" }));
      const dialog = await screen.findByTestId("create-service-account-dialog");
      await user.type(within(dialog).getByLabelText("Display name"), "CI Bot");
      await user.type(
        within(dialog).getByLabelText("Description"),
        "Runs the nightly CSV export.",
      );
      await user.click(within(dialog).getByRole("button", { name: "Create service account" }));

      const newRow = await within(card).findByTestId("service-account-principal-3");
      expect(within(newRow).getByText(/Runs the nightly CSV export\./)).toBeInTheDocument();
      expect(stores.principals.find((p) => p.type === "service_account")?.description).toBe(
        "Runs the nightly CSV export.",
      );
    });

    it("draws a service account as an agent, not as a person (docs/DESIGN.md 6.5)", async () => {
      stores.principals = [
        ...stores.principals,
        {
          id: "svc-1",
          type: "service_account",
          display_name: "Nightly importer",
          email: null,
          role: "member",
          auth_provider: null,
          external_id: null,
          is_active: true,
          description: "Imports the nightly CSV drop.",
          created_at: "2026-08-03T10:00:00",
          created_by: TEST_PRINCIPAL_ID,
        },
      ];
      renderWithProviders(<PeoplePage />);

      // 6.1: shape carries the kind, so it survives greyscale and colour-blindness. The avatar's
      // accessible name is what a test can read it off, and it is what a screen reader gets.
      const row = await screen.findByTestId("service-account-svc-1");
      expect(within(row).getByLabelText("Nightly importer (agent)")).toBeInTheDocument();

      const personRow = screen.getByTestId(`user-${otherPrincipalId}`);
      expect(within(personRow).getByLabelText("Other User")).toBeInTheDocument();
    });
  });

  describe("Reset password", () => {
    it(
      "offers Reset password on an active local user's row (not the caller), opening a dialog " +
        "naming them with the revocation sentence",
      async () => {
        const user = userEvent.setup();
        renderWithProviders(<PeoplePage />);

        const row = await screen.findByTestId(`user-${otherPrincipalId}`);
        await user.click(within(row).getByRole("button", { name: "Reset password" }));

        const dialog = await screen.findByRole("dialog", { name: "Reset password for Other User" });
        expect(
          within(dialog).getByText(
            "Other User will be signed out everywhere, and every personal access token they " +
              "hold stops working, including any an agent is using.",
          ),
        ).toBeInTheDocument();
      },
    );

    it(
      "resets the password, posting {password} to /api/v1/principals/{id}/password, then " +
        "closes the dialog and shows the result",
      async () => {
        const user = userEvent.setup();
        let capturedBody: unknown = null;
        let capturedId: string | undefined;
        server.use(
          http.post("/api/v1/principals/:id/password", async ({ request, params }) => {
            capturedBody = await request.json();
            capturedId = params.id as string;
            return HttpResponse.json(stores.principals.find((p) => p.id === params.id));
          }),
        );
        renderWithProviders(<PeoplePage />);

        const row = await screen.findByTestId(`user-${otherPrincipalId}`);
        await user.click(within(row).getByRole("button", { name: "Reset password" }));
        const dialog = await screen.findByRole("dialog", { name: "Reset password for Other User" });
        await user.type(within(dialog).getByLabelText("New password"), "fresh-password-1");
        await user.type(within(dialog).getByLabelText("Confirm new password"), "fresh-password-1");
        await user.click(within(dialog).getByRole("button", { name: "Reset password" }));

        expect(
          screen.queryByRole("dialog", { name: "Reset password for Other User" }),
        ).not.toBeInTheDocument();
        expect(capturedId).toBe(otherPrincipalId);
        expect(capturedBody).toEqual({ password: "fresh-password-1" });

        const peopleCard = screen.getByRole("region", { name: "People" });
        expect(
          await within(peopleCard).findByText("Password reset for Other User."),
        ).toBeInTheDocument();
      },
    );

    it("offers no Reset password on the caller's own row, with a positive control on another row", async () => {
      renderWithProviders(<PeoplePage />);

      const callerRow = await screen.findByTestId(`user-${TEST_PRINCIPAL_ID}`);
      const otherRow = screen.getByTestId(`user-${otherPrincipalId}`);
      expect(
        within(callerRow).queryByRole("button", { name: "Reset password" }),
      ).not.toBeInTheDocument();
      expect(within(otherRow).getByRole("button", { name: "Reset password" })).toBeInTheDocument();
    });

    it("offers no Reset password on an OIDC user's row, with a positive control on another row", async () => {
      stores.principals = [
        ...stores.principals,
        {
          id: "oidc-1",
          type: "user",
          display_name: "OIDC User",
          email: "oidc-user@example.com",
          role: "member",
          auth_provider: "oidc",
          external_id: "oidc-subject-1",
          is_active: true,
          description: null,
          created_at: "2026-08-05T10:00:00",
          created_by: TEST_PRINCIPAL_ID,
        },
      ];
      renderWithProviders(<PeoplePage />);

      const oidcRow = await screen.findByTestId("user-oidc-1");
      const otherRow = screen.getByTestId(`user-${otherPrincipalId}`);
      expect(
        within(oidcRow).queryByRole("button", { name: "Reset password" }),
      ).not.toBeInTheDocument();
      expect(within(otherRow).getByRole("button", { name: "Reset password" })).toBeInTheDocument();
    });

    it("offers no Reset password on an inactive user's row, with a positive control on another row", async () => {
      stores.principals = [
        ...stores.principals,
        {
          id: "inactive-1",
          type: "user",
          display_name: "Former User",
          email: "former-user@example.com",
          role: "member",
          auth_provider: "local",
          external_id: null,
          is_active: false,
          description: null,
          created_at: "2026-08-06T10:00:00",
          created_by: TEST_PRINCIPAL_ID,
        },
      ];
      renderWithProviders(<PeoplePage />);

      const inactiveRow = await screen.findByTestId("user-inactive-1");
      const otherRow = screen.getByTestId(`user-${otherPrincipalId}`);
      expect(
        within(inactiveRow).queryByRole("button", { name: "Reset password" }),
      ).not.toBeInTheDocument();
      expect(within(otherRow).getByRole("button", { name: "Reset password" })).toBeInTheDocument();
    });

    it("Cancel closes the dialog, and reopening it shows empty password fields", async () => {
      const user = userEvent.setup();
      renderWithProviders(<PeoplePage />);

      const row = await screen.findByTestId(`user-${otherPrincipalId}`);
      await user.click(within(row).getByRole("button", { name: "Reset password" }));
      let dialog = await screen.findByRole("dialog", { name: "Reset password for Other User" });
      await user.type(within(dialog).getByLabelText("New password"), "typed-but-abandoned");
      await user.type(
        within(dialog).getByLabelText("Confirm new password"),
        "typed-but-abandoned",
      );
      await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

      expect(
        screen.queryByRole("dialog", { name: "Reset password for Other User" }),
      ).not.toBeInTheDocument();

      await user.click(within(row).getByRole("button", { name: "Reset password" }));
      dialog = await screen.findByRole("dialog", { name: "Reset password for Other User" });
      expect(within(dialog).getByLabelText("New password")).toHaveValue("");
      expect(within(dialog).getByLabelText("Confirm new password")).toHaveValue("");
    });

    it(
      "clears a failed reset's error on Cancel, so reopening the dialog -- even for a " +
        "different person -- starts with no error",
      async () => {
        const user = userEvent.setup();
        // A second active local user besides `otherPrincipalId`, so reopening on "a different
        // person's row" is a distinct row rather than the same one that failed.
        stores.principals = [
          ...stores.principals,
          {
            id: "third-user-1",
            type: "user",
            display_name: "Third User",
            email: "third-user@example.com",
            role: "member",
            auth_provider: "local",
            external_id: null,
            is_active: true,
            description: null,
            created_at: "2026-08-07T10:00:00",
            created_by: TEST_PRINCIPAL_ID,
          },
        ];
        server.use(
          http.post(
            "/api/v1/principals/:id/password",
            () =>
              HttpResponse.json(
                {
                  error: {
                    code: "validation_failed",
                    message: "That password is too common.",
                    details: {},
                  },
                },
                { status: 422 },
              ),
            { once: true },
          ),
        );
        renderWithProviders(<PeoplePage />);

        const otherRow = await screen.findByTestId(`user-${otherPrincipalId}`);
        await user.click(within(otherRow).getByRole("button", { name: "Reset password" }));
        let dialog = await screen.findByRole("dialog", { name: "Reset password for Other User" });
        await user.type(within(dialog).getByLabelText("New password"), "attempt-one-1");
        await user.type(within(dialog).getByLabelText("Confirm new password"), "attempt-one-1");
        await user.click(within(dialog).getByRole("button", { name: "Reset password" }));

        expect(
          await within(dialog).findByText("Could not reset the password."),
        ).toBeInTheDocument();

        await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        expect(
          screen.queryByRole("dialog", { name: "Reset password for Other User" }),
        ).not.toBeInTheDocument();

        const thirdRow = await screen.findByTestId("user-third-user-1");
        await user.click(within(thirdRow).getByRole("button", { name: "Reset password" }));
        // The positive check the dialog actually opened, before the absence assertion below --
        // so the absence cannot pass merely because nothing rendered.
        dialog = await screen.findByRole("dialog", { name: "Reset password for Third User" });

        expect(within(dialog).queryByText("Could not reset the password.")).not.toBeInTheDocument();
      },
    );
  });

  /**
   * Every mutation has an error surface. The FR-A4 envelope renders wherever a mutation's
   * `isError` renders a fixed literal; the wider class is mutations that render *nothing* -- a
   * control that appears to do its job and silently does not.
   */
  describe("every mutation has an error surface", () => {
    function failWith(
      method: "post" | "patch" | "delete",
      path: string,
      code: string,
      message: string,
    ) {
      server.use(
        http[method](
          path,
          () => HttpResponse.json({ error: { code, message, details: {} } }, { status: 422 }),
          { once: true },
        ),
      );
    }

    async function expectAlert(region: HTMLElement, title: string, code: string, message: string) {
      const alert = await within(region).findByRole("alert");
      expect(within(alert).getByText(title)).toBeInTheDocument();
      expect(alert.textContent).toContain(code);
      expect(alert.textContent).toContain(message);
    }

    it("renaming an agent label", async () => {
      const user = userEvent.setup();
      failWith("patch", "/api/v1/agent-labels/:id", "validation_failed", "That name is taken.");
      renderWithProviders(<PeoplePage />);

      const card = await agentLabelsCard();
      const row = await within(card).findByTestId("agent-label-label-1");
      await user.click(within(row).getByRole("button", { name: "Edit" }));
      await user.click(within(row).getByRole("button", { name: "Save" }));

      await expectAlert(
        card,
        "Could not save the agent label.",
        "validation_failed",
        "That name is taken.",
      );
    });

    it("changing a user's role", async () => {
      const user = userEvent.setup();
      failWith("patch", "/api/v1/principals/:id", "validation_failed", "That role is not allowed.");
      renderWithProviders(<PeoplePage />);

      const card = await screen.findByRole("region", { name: "People" });
      const row = await within(card).findByTestId(`user-${otherPrincipalId}`);
      await user.selectOptions(within(row).getByLabelText("Role for Other User"), "admin");

      await expectAlert(
        card,
        "Could not change the user's role.",
        "validation_failed",
        "That role is not allowed.",
      );
    });

    it("deactivating a user", async () => {
      const user = userEvent.setup();
      failWith(
        "delete",
        "/api/v1/principals/:id",
        "validation_failed",
        "That user owns live records.",
      );
      renderWithProviders(<PeoplePage />);

      const card = await screen.findByRole("region", { name: "People" });
      const row = await within(card).findByTestId(`user-${otherPrincipalId}`);
      await user.click(within(row).getByRole("button", { name: "Deactivate" }));
      await user.click(within(row).getByRole("button", { name: "Confirm deactivate" }));

      await expectAlert(
        card,
        "Could not deactivate the user.",
        "validation_failed",
        "That user owns live records.",
      );
    });

    it("deactivating a service account", async () => {
      const user = userEvent.setup();
      failWith(
        "delete",
        "/api/v1/principals/:id",
        "validation_failed",
        "That account still holds tokens.",
      );
      // The base fixture has no service account (one case above asserts none is ever created),
      // so this one seeds it into the store the handlers read.
      stores.principals = [
        ...stores.principals,
        {
          id: "svc-1",
          type: "service_account",
          display_name: "Nightly importer",
          email: null,
          role: "member",
          auth_provider: null,
          external_id: null,
          is_active: true,
          description: "Imports the nightly CSV drop.",
          created_at: "2026-08-03T10:00:00",
          created_by: TEST_PRINCIPAL_ID,
        },
      ];
      renderWithProviders(<PeoplePage />);

      const card = await screen.findByRole("region", { name: "Service accounts" });
      const row = await within(card).findByTestId("service-account-svc-1");
      await user.click(within(row).getByRole("button", { name: "Deactivate" }));
      await user.click(within(row).getByRole("button", { name: "Confirm deactivate" }));

      await expectAlert(
        card,
        "Could not deactivate the service account.",
        "validation_failed",
        "That account still holds tokens.",
      );
    });
  });
});
