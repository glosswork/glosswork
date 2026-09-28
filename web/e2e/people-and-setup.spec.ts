/**
 * `/people` and `/setup`, against a real uvicorn process and the real built frontend.
 *
 * **This spec exists for one claim**: every action the old Settings page offered is reachable
 * on one of the two, and the e2e suite enumerates them. A redesign that quietly drops a control is
 * the failure mode here, and no per-screen test catches it, because the test nobody wrote is the
 * one for the control nobody remembered. So `OLD_PAGE_ACTIONS` is a literal list and the walk
 * below asserts that every entry in it was actually driven to completion.
 *
 * It also carries the assertions that cannot live in a grep:
 *
 * - **The nested-card count.** The card recipe lives in `ui/tableClasses.ts` and
 *   `ui/classes.ts` and never appears as a literal in a call site, so a source search of the new
 *   directories can never see a `rounded-card` inside a `rounded-card`. The live DOM can.
 * - **The nav destinations.** `shellNav.test.ts` compares the entries against a
 *   hand-written `served` set, which a change could satisfy without `App.tsx` serving anything.
 *   Clicking each one and reading the `h1` cannot be satisfied that way.
 * - **The export link's response.** Asserting an `href` proves nothing: an unknown path
 *   under `/api/v1/` returns `200 text/html` from the SPA's static fallback whenever `web/dist`
 *   exists, so a link to a route nobody wrote would pass. The content type is the claim.
 */
import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import {
  E2E_AUTH_HEADER,
  E2E_BASE_URL,
  signInAs,
  signInAsE2eAdmin,
} from "./constants";

/**
 * Every action the old `/settings` page offered, as a list rather than as prose, because the walk
 * below reports which of them it failed to reach.
 */
const OLD_PAGE_ACTIONS = [
  "rename or describe one of my agent labels",
  "read every principal's agent labels",
  "mint a PAT",
  "revoke a PAT",
  "invite a user",
  "change a user's role",
  "deactivate a user",
  "create a service account",
  "deactivate a service account",
  "read search-index status",
  "trigger a re-index",
] as const;

const MEMBER_EMAIL = "e2e-people-member@example.com";
const MEMBER_PASSWORD = "people-member-passw0rd!";

/** Seeded by a PAT request carrying `X-Agent-Label`, which is how a label comes into existence
 * at all (FR-I6: auto-registration on first use). The REST edge resolves the header for a bearer
 * credential and never for a session cookie (DD-17), and it resolves it on *any* request, so no
 * record has to be written to bring one into being -- which matters, because writing one would
 * put an agent square in the `By` column of whatever record it touched. */
const SEEDED_AGENT_LABEL = "e2e-people-agent";

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  // Idempotent: `playwright.config.ts` retries a worker death once into the same data
  // directory, and a second POST would fail on the email uniqueness rule.
  const existing = await apiContext.get(
    `/api/v1/principals?include_inactive=true&type=user`,
  );
  const body = (await existing.json()) as { principals: { email: string | null }[] };
  if (!body.principals.some((p) => p.email === MEMBER_EMAIL)) {
    const created = await apiContext.post("/api/v1/principals", {
      data: {
        type: "user",
        display_name: "E2E People Member",
        email: MEMBER_EMAIL,
        role: "member",
        password: MEMBER_PASSWORD,
      },
    });
    expect(created.ok(), await created.text()).toBeTruthy();
  }

  // Registering the label is idempotent by construction: `register_use` upserts.
  const seeded = await apiContext.get("/api/v1/me", {
    headers: { "X-Agent-Label": SEEDED_AGENT_LABEL },
  });
  expect(seeded.ok(), await seeded.text()).toBeTruthy();
});

test.afterAll(async () => {
  await apiContext.dispose();
});

/** A user this test may freely demote and deactivate, created per scenario so no two scenarios
 * fight over one row. The email carries the scenario name for exactly that reason. */
async function createDisposableUser(slug: string): Promise<{ id: string; name: string }> {
  const name = `E2E Disposable ${slug}`;
  const email = `e2e-disposable-${slug}-${Date.now()}@example.com`;
  const response = await apiContext.post("/api/v1/principals", {
    data: { type: "user", display_name: name, email, role: "member", password: "disposable-pw1!" },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const created = (await response.json()) as { id: string };
  return { id: created.id, name };
}

async function rowFor(page: Page, testId: string) {
  const row = page.getByTestId(testId);
  await expect(row).toBeVisible();
  return row;
}

test("/settings redirects to /people and does not stay on the history stack", async ({ page }) => {
  await signInAsE2eAdmin(page);
  await page.goto("/people");
  await expect(page.getByRole("heading", { level: 1, name: "People & agents" })).toBeVisible();

  await page.goto("/settings");
  await expect(page).toHaveURL(/\/people$/);
  await expect(page.getByRole("heading", { level: 1, name: "People & agents" })).toBeVisible();
});

test("every action the old Settings page offered is reachable on /people or /setup", async ({
  page,
}) => {
  const completed = new Set<string>();
  await signInAsE2eAdmin(page);

  // ---- /people -----------------------------------------------------------------------------
  await page.goto("/people");
  await expect(page.getByRole("heading", { level: 1, name: "People & agents" })).toBeVisible();

  // 1. Rename or describe one of my agent labels, and 2. read every principal's labels. Both
  // land in one table now, which is the change: the row is the unit of authority, not the panel.
  const labelRow = page.locator("tr", { hasText: SEEDED_AGENT_LABEL });
  await expect(labelRow).toBeVisible();
  completed.add("read every principal's agent labels");

  await labelRow.getByRole("button", { name: "Edit" }).click();
  const renamed = `Renamed ${Date.now()}`;
  await labelRow.getByLabel("Display name").fill(renamed);
  await labelRow.getByLabel("Description").fill("Seeded by the people-and-setup walk.");
  await labelRow.getByRole("button", { name: "Save" }).click();
  await expect(page.locator("tr", { hasText: SEEDED_AGENT_LABEL })).toContainText(renamed);
  completed.add("rename or describe one of my agent labels");

  // The column that replaced the warning pill. Naming a label verifies it (FR-I6), so after the
  // rename above this row reads "Yes" -- and nothing anywhere reads "(unverified)".
  await expect(page.locator("tr", { hasText: SEEDED_AGENT_LABEL })).toContainText("Yes");
  await expect(page.getByText("(unverified)")).toHaveCount(0);

  // 3. Invite a user.
  const peopleCard = page.getByRole("region", { name: "People" });
  await peopleCard.getByRole("button", { name: "Invite" }).click();
  const inviteDialog = page.getByTestId("invite-user-dialog");
  await expect(inviteDialog).toBeVisible();
  const invitedEmail = `e2e-invited-${Date.now()}@example.com`;
  await inviteDialog.getByLabel("Display name").fill("E2E Invited Person");
  await inviteDialog.getByLabel("Email").fill(invitedEmail);
  await inviteDialog.getByRole("button", { name: "Create user" }).click();
  await expect(inviteDialog).toHaveCount(0);
  await expect(peopleCard.locator("tr", { hasText: invitedEmail })).toBeVisible();
  completed.add("invite a user");

  // 4. Change a user's role, on a row created for this scenario alone.
  const roleTarget = await createDisposableUser("role");
  await page.reload();
  const roleRow = await rowFor(page, `user-${roleTarget.id}`);
  await roleRow.getByLabel(`Role for ${roleTarget.name}`).selectOption("creator");
  await expect(roleRow.getByLabel(`Role for ${roleTarget.name}`)).toHaveValue("creator");
  completed.add("change a user's role");

  // 5. Deactivate a user, through the two-step confirm, and read it off the Status column.
  const deactivateTarget = await createDisposableUser("deactivate");
  await page.reload();
  const deactivateRow = await rowFor(page, `user-${deactivateTarget.id}`);
  await expect(deactivateRow).toContainText("Active");
  await deactivateRow.getByRole("button", { name: "Deactivate" }).click();
  await deactivateRow.getByRole("button", { name: "Confirm deactivate" }).click();
  await expect(deactivateRow).toContainText("Inactive");
  completed.add("deactivate a user");

  // 6. Create a service account, and 7. deactivate one.
  const serviceCard = page.getByRole("region", { name: "Service accounts" });
  await serviceCard.getByRole("button", { name: "New service account" }).click();
  const createDialog = page.getByTestId("create-service-account-dialog");
  await expect(createDialog).toBeVisible();
  const accountName = `E2E Importer ${Date.now()}`;
  await createDialog.getByLabel("Display name").fill(accountName);
  await createDialog.getByLabel("Description").fill("Created by the people-and-setup walk.");
  await createDialog.getByRole("button", { name: "Create service account" }).click();
  await expect(createDialog).toHaveCount(0);
  const accountRow = serviceCard.locator("tr", { hasText: accountName });
  await expect(accountRow).toBeVisible();
  completed.add("create a service account");

  await accountRow.getByRole("button", { name: "Deactivate" }).click();
  await accountRow.getByRole("button", { name: "Confirm deactivate" }).click();
  await expect(accountRow).toContainText("Inactive");
  completed.add("deactivate a service account");

  // ---- /setup ------------------------------------------------------------------------------
  await page.goto("/setup");
  await expect(page.getByRole("heading", { level: 1, name: "Setup" })).toBeVisible();

  // 8. Mint a PAT, and 9. revoke one. The mint form is inline, and the plaintext appears in the
  // minted-token dialog.
  const tokenName = `e2e-accept-token-${Date.now()}`;
  const mintForm = page.getByRole("form", { name: "Mint access token" });
  await mintForm.getByLabel("Name").fill(tokenName);
  await mintForm.getByRole("button", { name: "Mint token" }).click();
  const mintedDialog = page.getByTestId("minted-token-dialog");
  await expect(mintedDialog).toBeVisible();
  await expect(page.getByTestId("minted-token-plaintext")).toContainText("gw_pat_");
  await mintedDialog.getByRole("button", { name: "Done" }).click();
  completed.add("mint a PAT");

  const tokenRow = page.locator("tr", { hasText: tokenName });
  await expect(tokenRow).toBeVisible();
  await expect(tokenRow).toContainText("Active");
  await tokenRow.getByRole("button", { name: "Revoke" }).click();
  await tokenRow.getByRole("button", { name: "Confirm revoke" }).click();
  await expect(tokenRow).toContainText("Revoked");
  completed.add("revoke a PAT");

  // 10. Read search-index status, and 11. trigger a re-index.
  const searchCard = page.getByRole("region", { name: "Search index" });
  await expect(searchCard.getByText("bge-small-en-v1.5")).toBeVisible();
  completed.add("read search-index status");

  await searchCard.getByRole("button", { name: "Re-index everything" }).click();
  await searchCard.getByRole("button", { name: "Confirm re-index" }).click();
  await expect(searchCard.getByTestId("reindex-result")).toBeVisible();
  completed.add("trigger a re-index");

  // The enumeration itself. Sorted on both sides so the failure message names the missing
  // action rather than reporting two lists in different orders.
  expect([...completed].sort()).toEqual([...OLD_PAGE_ACTIONS].sort());
});

test("no card is nested inside another card on either page", async ({ page }) => {
  // docs/DESIGN.md 7.6: "a table is one card; rows are not cards". The old page's repeating unit
  // was a `rounded-card` row inside a `rounded-card` panel.
  await signInAsE2eAdmin(page);

  /**
   * The card recipe, not the radius token. `.rounded-card` alone also matches `ui/Avatar.tsx`'s
   * agent square (docs/DESIGN.md 6.1 gives it a 6px radius, which is the same token), `Badge`,
   * `Alert` and `EmptyState` -- measured: on `/people` this selector without the compound
   * returns one hit per service account on the page, and they are avatars, not cards.
   *
   * What 7.6 forbids is a bordered, filled BOX inside another one, and the
   * two recipes that produced it are `ui/tableClasses.ts`'s `tableWrapClass` and the old
   * `panelDlClass`, both `div.rounded-card.border.border-line`. The avatar's border is
   * `border-agent-line`, the alert's is `border-<tone>-line`, and the empty state's is
   * `border-dashed border-line-2`, so none of them is caught by accident.
   */
  const nested = "section.rounded-card div.rounded-card.border.border-line";

  for (const [path, heading] of [
    ["/people", "People & agents"],
    ["/setup", "Setup"],
  ] as const) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();

    // The page really rendered its cards, so a zero below cannot come from an empty `<main>`.
    await expect(page.locator("section.rounded-card")).not.toHaveCount(0);

    // And the selector can really fire: one is injected into this page's own first card,
    // counted, and removed. A positive control on some *other* page's markup would be a
    // control over that page -- it would go quietly vacuous the day that page is redesigned,
    // which on this branch is a thing that keeps happening. This tests the instrument.
    const injected = await page.evaluate(() => {
      const card = document.querySelector("section.rounded-card");
      if (!card) return false;
      const probe = document.createElement("div");
      probe.className = "rounded-card border border-line";
      probe.dataset.nestedCardProbe = "true";
      card.appendChild(probe);
      return true;
    });
    expect(injected, `${path} rendered no card to probe`).toBe(true);
    await expect(page.locator(nested)).toHaveCount(1);
    await page.evaluate(() =>
      document.querySelector("[data-nested-card-probe]")?.remove(),
    );

    await expect(page.locator(nested)).toHaveCount(0);
  }
});

test("every Workspace nav entry lands on a page that rendered", async ({ page }) => {
  // The half `shellNav.test.ts` cannot prove: it compares the entries against a hand-written
  // `served` set in the test file itself, which a change could satisfy while `App.tsx` serves
  // nothing. A route with no element renders a blank `<main>`.
  await signInAsE2eAdmin(page);
  await page.goto("/people");

  for (const [testId, heading] of [
    ["nav-people", "People & agents"],
    ["nav-activity", "Activity"],
    ["nav-schema", "Schema"],
    ["nav-setup", "Setup"],
  ] as const) {
    await page.getByTestId(testId).click();
    await expect(page.locator("main").getByRole("heading", { level: 1 })).toContainText(heading);
  }
});

test("the export link points at a route that answers with the export, and only for an admin", async ({
  page,
}) => {
  await signInAsE2eAdmin(page);
  await page.goto("/setup");

  const link = page.getByTestId("export-link");
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute("href", "/api/v1/admin/export");

  // The claim is not the attribute. An unknown path under `/api/v1/` returns `200 text/html`
  // from the SPA's static fallback whenever `web/dist` exists (AGENTS.md), so a status code
  // would pass against a route nobody had written. The content type and the disposition are
  // what distinguish the real route from the fallback.
  const response = await page.request.get("/api/v1/admin/export");
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toContain("application/json");
  expect(response.headers()["content-disposition"]).toContain("glosswork-export.json");
});

test("a member sees neither the people directory nor the admin cards, and is told why", async ({
  page,
}) => {
  await signInAs(page, MEMBER_EMAIL, MEMBER_PASSWORD);

  await page.goto("/people");
  await expect(page.getByRole("heading", { level: 1, name: "People & agents" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Agent labels" })).toBeVisible();
  await expect(page.getByRole("region", { name: "People" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Service accounts" })).toHaveCount(0);
  // DD-42 applied to a role: the absence gets a reason rather than a silence.
  await expect(page.getByTestId("people-admin-only-note")).toBeVisible();

  await page.goto("/setup");
  await expect(page.getByRole("region", { name: "Personal access tokens" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Search index" })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Export" })).toHaveCount(0);
  await expect(page.getByTestId("export-link")).toHaveCount(0);
});
