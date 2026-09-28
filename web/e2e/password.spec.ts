/**
 * Password self-service and admin reset (FR-I17), against a real uvicorn process and the real
 * built frontend.
 *
 * FR-I17 has two halves: a signed-in person changes their own password from `/setup`'s
 * `Password` card, and an administrator resets anyone else's from `/people`. Both revoke the same
 * set -- every other session and every personal access token the account holds -- which is what
 * the self-change and admin-reset scenarios below prove from the *outside*: not by reading an
 * audit row, but by watching a second, already-signed-in browser context lose its session.
 *
 * **A revoked session is noticed only on a full page load.** `AuthProvider`'s mount effect is
 * the only thing that reads `/me`; an in-app navigation shows a query error and stays put. So
 * every "did this session get revoked" assertion below drives `page.goto("/")` -- a real
 * navigation -- rather than trusting a client-side route change or a component re-render to
 * notice on its own.
 *
 * Per-scenario disposable local users are created directly through the REST API
 * (`createDisposableUser`, below -- `people-and-setup.spec.ts`'s pattern, with a distinct email
 * prefix so the two specs' fixtures cannot collide), with a password well over the
 * 12-character floor, so no scenario here is a test of `PasswordPolicy` itself; that is
 * `tests/test_own_password.py`'s job.
 */
import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import {
  E2E_AUTH_HEADER,
  E2E_BASE_URL,
  E2E_PRINCIPAL_ID,
  signInAs,
  signInAsE2eAdmin,
} from "./constants";

/** Every disposable user this spec creates starts with this password (>= the 12-char floor). */
const INITIAL_PASSWORD = "e2e-password-initial-pw1!";
/** The new password the self-change scenario submits. */
const CHANGED_PASSWORD = "e2e-password-changed-pw2!";
/** The new password the admin-reset scenario submits. */
const ADMIN_RESET_PASSWORD = "e2e-password-reset-pw3!";
/** Deliberately wrong, for the wrong-current-password scenario. Not the disposable user's real
 * password. */
const WRONG_CURRENT_PASSWORD = "not-the-real-password-at-all!";

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });
});

test.afterAll(async () => {
  await apiContext.dispose();
});

/** A local `member` this test may freely sign in as and change the password of, created per
 * scenario so no two scenarios fight over one account (`people-and-setup.spec.ts`'s
 * `createDisposableUser` pattern, with this spec's own email prefix). */
async function createDisposableUser(
  slug: string,
): Promise<{ id: string; name: string; email: string; password: string }> {
  const name = `E2E Password ${slug}`;
  const email = `e2e-password-${slug}-${Date.now()}@example.com`;
  const response = await apiContext.post("/api/v1/principals", {
    data: {
      type: "user",
      display_name: name,
      email,
      role: "member",
      password: INITIAL_PASSWORD,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const created = (await response.json()) as { id: string };
  return { id: created.id, name, email, password: INITIAL_PASSWORD };
}

async function rowFor(page: Page, testId: string) {
  const row = page.getByTestId(testId);
  await expect(row).toBeVisible();
  return row;
}

test("a member changes their own password on /setup, and every other session and login notices", async ({
  page,
  browser,
}) => {
  const member = await createDisposableUser("self-change");

  // Context B: a second, independently signed-in session for the same member, established
  // *before* the change so it is one of the sessions that gets revoked.
  const contextB = await browser.newContext({ baseURL: E2E_BASE_URL });
  let contextC: BrowserContext | undefined;

  try {
    // Context A is the test's own `page`.
    await signInAs(page, member.email, member.password);
    const pageB = await contextB.newPage();
    await signInAs(pageB, member.email, member.password);

    await page.goto("/setup");
    const passwordRegion = page.getByRole("region", { name: "Password" });
    await expect(passwordRegion).toBeVisible();
    await passwordRegion.getByLabel("Current password").fill(member.password);
    await passwordRegion.getByLabel("New password", { exact: true }).fill(CHANGED_PASSWORD);
    await passwordRegion.getByLabel("Confirm new password").fill(CHANGED_PASSWORD);
    await passwordRegion.getByRole("button", { name: "Change password" }).click();

    await expect(
      passwordRegion.getByText(
        "Password changed. Your other sessions and personal access tokens were revoked.",
      ),
    ).toBeVisible();
    // A full page load is what notices a revoked session; reloading here is what makes
    // "A is still signed in" an assertion about the *kept* session rather than the untouched
    // client-side state the SPA carries between an action and its own response.
    await page.reload();
    // A is still on /setup, still signed in -- the change kept the session that made it.
    await expect(page).toHaveURL(/\/setup$/);
    await expect(page.getByTestId("current-principal")).toBeVisible();

    // B's session was revoked. Only a full page load notices.
    await pageB.goto("/");
    await expect(pageB).toHaveURL(/\/login$/);

    // A fresh context: the old password is refused, the new one is accepted.
    contextC = await browser.newContext({ baseURL: E2E_BASE_URL });
    const pageC = await contextC.newPage();
    await pageC.goto("/login");
    await pageC.getByTestId("login-email").fill(member.email);
    await pageC.getByTestId("login-password").fill(member.password);
    await pageC.getByTestId("login-submit").click();
    await expect(pageC.getByTestId("login-error")).toBeVisible();
    await expect(pageC.getByTestId("current-principal")).toHaveCount(0);

    await signInAs(pageC, member.email, CHANGED_PASSWORD);
  } finally {
    await contextB.close();
    if (contextC) {
      await contextC.close();
    }
  }
});

test("a wrong current password on /setup is refused under that field, and the page stays signed in", async ({
  page,
}) => {
  const member = await createDisposableUser("wrong-current");
  await signInAs(page, member.email, member.password);
  await page.goto("/setup");

  const passwordRegion = page.getByRole("region", { name: "Password" });
  await expect(passwordRegion).toBeVisible();
  await passwordRegion.getByLabel("Current password").fill(WRONG_CURRENT_PASSWORD);
  await passwordRegion.getByLabel("New password", { exact: true }).fill(CHANGED_PASSWORD);
  await passwordRegion.getByLabel("Confirm new password").fill(CHANGED_PASSWORD);
  await passwordRegion.getByRole("button", { name: "Change password" }).click();

  await expect(passwordRegion.getByLabel("Current password")).toHaveAccessibleDescription(
    /The current password is not correct\./,
  );
  // A full page load is what notices a revoked session; reload before checking that the page
  // stayed signed in, the same way the self-change scenario does after a successful change.
  await page.reload();
  // Fence: a wrong current password revokes nothing by design, so there is no mutation that can
  // turn this assertion red on its own.
  await expect(page.getByTestId("current-principal")).toBeVisible();
});

test("an administrator resets a member's password on /people, and the member's own session and login notice", async ({
  page,
  browser,
}) => {
  const member = await createDisposableUser("admin-reset");

  // The member's own, previously signed-in context -- established before the reset so its next
  // full page load can notice the reset, the only way a revoked session is noticed.
  const memberContext = await browser.newContext({ baseURL: E2E_BASE_URL });
  try {
    const memberPage = await memberContext.newPage();
    await signInAs(memberPage, member.email, member.password);

    await signInAsE2eAdmin(page);
    await page.goto("/people");
    const row = await rowFor(page, `user-${member.id}`);
    await row.getByRole("button", { name: "Reset password" }).click();

    const dialog = page.getByRole("dialog", { name: `Reset password for ${member.name}` });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText(
      `${member.name} will be signed out everywhere, and every personal access token they hold ` +
        "stops working, including any an agent is using.",
    );
    await dialog.getByLabel("New password", { exact: true }).fill(ADMIN_RESET_PASSWORD);
    await dialog.getByLabel("Confirm new password").fill(ADMIN_RESET_PASSWORD);
    await dialog.getByRole("button", { name: "Reset password" }).click();

    await expect(dialog).toHaveCount(0);
    await expect(page.getByText(`Password reset for ${member.name}.`)).toBeVisible();

    // The member's own context notices only on a full page load.
    await memberPage.goto("/");
    await expect(memberPage).toHaveURL(/\/login$/);
    await signInAs(memberPage, member.email, ADMIN_RESET_PASSWORD);
  } finally {
    await memberContext.close();
  }
});

test("the administrator's own row on /people offers no Reset password button", async ({
  page,
}) => {
  const member = await createDisposableUser("no-self-reset");
  await signInAsE2eAdmin(page);
  await page.goto("/people");

  // Positive control asserted FIRST: a missing table (a broken page, an empty query) would make
  // an absence assertion pass for the wrong reason if it ran alone. Asserting the button exists
  // on some row before asserting it is absent from another is what keeps the absence from being
  // vacuous.
  //
  // That said, this positive control only rules out "the whole table is missing"; it does not by
  // itself prove the *absence* assertion below can fail for its own reason (that a caller ever
  // gets `canResetPassword` wrong for their own row). That takes a mutation, and this scenario is
  // not one.
  const memberRow = await rowFor(page, `user-${member.id}`);
  await expect(memberRow.getByRole("button", { name: "Reset password" })).toBeVisible();

  const adminRow = await rowFor(page, `user-${E2E_PRINCIPAL_ID}`);
  await expect(adminRow.getByRole("button", { name: "Reset password" })).toHaveCount(0);
});
