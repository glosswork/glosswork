/**
 * `/people` on a workspace that signs people in by emailed code (change 9): "Invite" sends an
 * email invite, pending invites are listed with "Revoke", and no row offers "Reset password".
 *
 * Each case that asserts an absence carries its positive control in the same file: the same page
 * with `email_code` off still shows the password dialog and still offers the reset.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupServer } from "msw/node";
import { PeoplePage } from "./PeoplePage";
import { renderWithProviders } from "../test/renderWithProviders";
import {
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

function useEmailCodes() {
  stores.modes = { standalone: false, oidc: false, email_code: true };
}

async function inviteLin() {
  const user = userEvent.setup();
  renderWithProviders(<PeoplePage />);
  const peopleCard = await screen.findByRole("region", { name: "People" });
  await user.click(within(peopleCard).getByRole("button", { name: "Invite" }));
  const dialog = await screen.findByRole("dialog", { name: "Invite by email" });
  const form = within(dialog).getByRole("form", { name: "Invite by email" });
  expect(within(form).queryByLabelText(/Password/)).not.toBeInTheDocument();
  await user.type(within(form).getByLabelText("Display name"), "Lin Chen");
  await user.type(within(form).getByLabelText("Email"), "lin@example.com");
  await user.selectOptions(within(form).getByLabelText("Role"), "creator");
  await user.click(within(form).getByRole("button", { name: "Send invite" }));
  return { user, peopleCard };
}

describe("PeoplePage with sign-in by emailed code", () => {
  it("invites by email, with no password field, and lists the pending invite", async () => {
    useEmailCodes();
    const { peopleCard } = await inviteLin();

    expect(await within(peopleCard).findByText("Invite sent.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Invite by email" })).not.toBeInTheDocument();
    const pending = await screen.findByRole("region", { name: "Pending invites" });
    const row = await within(pending).findByTestId("invite-invite-1");
    expect(within(row).getByText("Lin Chen")).toBeInTheDocument();
    expect(within(row).getByText("lin@example.com")).toBeInTheDocument();
    expect(within(row).getByText("creator")).toBeInTheDocument();
    expect(stores.invites.map((invite) => invite.email)).toEqual(["lin@example.com"]);
  });

  it("shows an email the relay refused as a warning, with the server's own sentence", async () => {
    useEmailCodes();
    stores.inviteEmail = {
      outcome: "refused_fields",
      message:
        "The invite is saved, but the email could not be sent. Your display name may be the reason.",
    };
    const { peopleCard } = await inviteLin();

    expect(
      await within(peopleCard).findByText(/Your display name may be the reason\./),
    ).toBeInTheDocument();
    expect(screen.queryByText("Invite sent.")).not.toBeInTheDocument();
  });

  it("revokes a pending invite", async () => {
    useEmailCodes();
    const { user } = await inviteLin();
    const pending = await screen.findByRole("region", { name: "Pending invites" });
    const row = await within(pending).findByTestId("invite-invite-1");
    await user.click(within(row).getByRole("button", { name: "Revoke" }));

    expect(await within(pending).findByText("No pending invites.")).toBeInTheDocument();
    expect(stores.invites).toEqual([]);
  });

  it("offers no Reset password, because nobody here has a password", async () => {
    useEmailCodes();
    renderWithProviders(<PeoplePage />);
    const row = await screen.findByTestId(`user-${otherPrincipalId}`);
    await screen.findByRole("region", { name: "Pending invites" });
    expect(within(row).queryByRole("button", { name: "Reset password" })).not.toBeInTheDocument();
  });

  it("is unchanged with codes off: the password dialog, the reset, and no pending invites", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PeoplePage />);
    const row = await screen.findByTestId(`user-${otherPrincipalId}`);
    expect(within(row).getByRole("button", { name: "Reset password" })).toBeInTheDocument();

    const peopleCard = screen.getByRole("region", { name: "People" });
    await user.click(within(peopleCard).getByRole("button", { name: "Invite" }));
    const dialog = await screen.findByRole("dialog", { name: "Invite user" });
    expect(within(dialog).getByLabelText("Password (optional)")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Pending invites" })).not.toBeInTheDocument();
    expect(stores.requests).not.toContain("/invites");
  });
});
