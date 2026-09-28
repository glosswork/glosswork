/**
 * The first-run screen (`docs/DESIGN.md` 8.5): what a workspace with nothing in it opens on.
 *
 * **Why this spec signs in as a principal it creates rather than as the e2e admin.** The screen
 * renders when `list_object_types` comes back empty, and this server's database is full of
 * fixture types by the time any spec runs. That list is filtered per principal to the
 * types the caller holds `read` on (FR-I11), and every object type is closed by default, so a
 * **creator with no grants** sees a genuinely empty list from the real server. No mock, no second
 * database, no fourth port. Intercepting the route with `page.route` would be simpler, but this
 * way the server actually returns the empty list, which is the one leg interception could not
 * cover.
 *
 * A `creator` rather than an `admin`, because an admin is implicitly admin on every type and
 * would see all of them.
 */
import { expect, test, request as apiRequestModule, type APIRequestContext } from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAs } from "./constants";

const FRESH_EMAIL = "e2e-first-run@example.com";
const FRESH_PASSWORD = "e2e-first-run-passw0rd!";
const QUESTION = "What do you want to keep track of?";

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });
  // Idempotent across retries: a second POST for the same email is refused, and the account
  // from the first attempt is the one this spec then signs in as.
  const response = await apiContext.post("/api/v1/principals", {
    data: {
      type: "user",
      display_name: "E2E First Run",
      email: FRESH_EMAIL,
      role: "creator",
      password: FRESH_PASSWORD,
    },
  });
  expect(
    response.ok() || response.status() === 409 || response.status() === 422,
    await response.text(),
  ).toBeTruthy();
});

test.afterAll(async () => {
  await apiContext.dispose();
});

test("a workspace with nothing in it asks the question", async ({ page }) => {
  await signInAs(page, FRESH_EMAIL, FRESH_PASSWORD);
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1, name: QUESTION })).toBeVisible();
  await expect(page.getByRole("link", { name: "set up a type by hand" })).toHaveAttribute(
    "href",
    "/schema/new",
  );
});

test("Copy places the prompt, carrying this deployment's own /mcp URL, on the clipboard", async ({
  page,
  context,
}) => {
  // Chromium gates `navigator.clipboard.readText` on a permission the browser will not prompt
  // for in a test. Granting both is what lets this assert the clipboard's real contents rather
  // than that a handler was called.
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await signInAs(page, FRESH_EMAIL, FRESH_PASSWORD);
  await page.goto("/");

  await expect(page.getByRole("heading", { level: 1, name: QUESTION })).toBeVisible();

  // Read from the run rather than written as a constant (docs/changes/README.md: "a count in an
  // assertion comes from the run, not from a constant"). `GW_BASE_URL` is set to this server's
  // own origin in playwright.config.ts, so the URL the screen prints must be that origin's /mcp.
  const expectedUrl = new URL("/mcp", page.url()).href;
  await expect(page.getByTestId("prompt-block-text")).toContainText(expectedUrl);

  await page.getByRole("button", { name: "Copy" }).click();

  const clipboard = await page.evaluate(() => navigator.clipboard.readText());
  expect(clipboard).toContain(expectedUrl);
  // The copied text is the displayed text, asserted across the process boundary rather than
  // only in jsdom.
  expect(clipboard).toBe(await page.getByTestId("prompt-block-text").textContent());
});
