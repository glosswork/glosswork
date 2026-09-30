/**
 * Sign-in by emailed code and invites (change 9), against a real uvicorn process configured as
 * a hosted workspace and a real fake relay process (`tests/fake_relay.py`), which is the one
 * enforcer of the relay request's definition. A code is read the way a person reads it: from
 * what the relay accepted for their address.
 *
 * **A fresh address per scenario where the scenario chooses the address**, so a retry
 * (`retries: 1`) never meets the per-address cap of five codes an hour. The first
 * administrator's address is fixed by the server's configuration; it is sent at most four
 * codes per run, retries included.
 */
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { CODE_BASE_URL, E2E_ADMIN_EMAIL, RELAY_BASE_URL } from "./constants";

test.use({ baseURL: CODE_BASE_URL });

const REQUEST_MESSAGE =
  "If this address can sign in here, a code is on its way. It works for 10 minutes.";

interface RelayMessage {
  template: string;
  to: string;
  fields: Record<string, string>;
}

async function sentTo(request: APIRequestContext, address: string): Promise<RelayMessage[]> {
  const response = await request.get(`${RELAY_BASE_URL}/sent`);
  const body = (await response.json()) as { sent: RelayMessage[] };
  return body.sent.filter((message) => message.to === address);
}

/** Ask for a code on the sign-in page and return the one the relay was sent for it. */
async function requestCode(page: Page, request: APIRequestContext, address: string) {
  const before = (await sentTo(request, address)).length;
  await page.goto("/login");
  await expect(page.getByTestId("login-password")).toHaveCount(0);
  await page.getByTestId("code-email").fill(address);
  await page.getByTestId("code-send").click();
  await expect(page.getByTestId("code-sent")).toHaveText(REQUEST_MESSAGE);
  await expect
    .poll(async () => (await sentTo(request, address)).length, { timeout: 10_000 })
    .toBe(before + 1);
  const messages = await sentTo(request, address);
  const latest = messages[messages.length - 1];
  expect(latest.template).toBe("sign_in_code");
  return latest.fields.code;
}

async function signInByCode(page: Page, request: APIRequestContext, address: string) {
  const code = await requestCode(page, request, address);
  await page.getByTestId("code-value").fill(code);
  await page.getByTestId("code-submit").click();
  await expect(page.getByTestId("current-principal")).toBeVisible();
}

test("the first administrator signs in with an emailed code, and there is no password form", async ({
  page,
  request,
}) => {
  await signInByCode(page, request, E2E_ADMIN_EMAIL);
  await page.goto("/setup");
  await expect(page.getByTestId("password-email-code")).toBeVisible();
});

test("a wrong code is refused with the one failure message", async ({ page, request }) => {
  const nobody = `nobody-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await page.goto("/login");
  await page.getByTestId("code-email").fill(nobody);
  await page.getByTestId("code-send").click();
  await expect(page.getByTestId("code-sent")).toHaveText(REQUEST_MESSAGE);
  await page.getByTestId("code-value").fill("000000");
  await page.getByTestId("code-submit").click();
  await expect(page.getByTestId("login-error")).toHaveText(
    "That code is not right, or it has expired. Ask for a new code.",
  );
  // An address that cannot sign in here is never sent a code.
  expect(await sentTo(request, nobody)).toEqual([]);
});

test("an administrator invites a person, whose first code sign-in creates them", async ({
  page,
  browser,
  request,
}) => {
  const invitee = `invitee-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
  await signInByCode(page, request, E2E_ADMIN_EMAIL);

  await page.goto("/people");
  const people = page.getByRole("region", { name: "People" });
  await people.getByRole("button", { name: "Invite" }).click();
  const dialog = page.getByRole("dialog", { name: "Invite by email" });
  await dialog.getByLabel("Display name").fill("Invited Person");
  await dialog.getByLabel("Email", { exact: true }).fill(invitee);
  await dialog.getByLabel("Role").selectOption("creator");
  await dialog.getByRole("button", { name: "Send invite" }).click();
  await expect(people.getByText("Invite sent.")).toBeVisible();
  const pending = page.getByRole("region", { name: "Pending invites" });
  await expect(pending.getByText(invitee)).toBeVisible();

  const invites = (await sentTo(request, invitee)).filter((m) => m.template === "invite");
  expect(invites).toHaveLength(1);

  const inviteeContext = await browser.newContext({ baseURL: CODE_BASE_URL });
  try {
    const inviteePage = await inviteeContext.newPage();
    await signInByCode(inviteePage, request, invitee);
    await expect(inviteePage.getByTestId("current-principal")).toContainText("Invited Person");
  } finally {
    await inviteeContext.close();
  }

  await page.goto("/people");
  await expect(page.getByRole("region", { name: "People" }).getByText(invitee)).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Pending invites" }).getByText(invitee),
  ).toHaveCount(0);
});
