/**
 * Change 32: the workspace screens live under `/workspace`, and an old top-level address is decided
 * below the object type routes.
 *
 * **Two servers of its own, not the shared `e2e` one.** Type keys are unique and cannot be retired
 * there, a type keyed `activity` sorts first and changes where `/` lands for other specs, and a
 * sidebar link named "Activity" breaks their strict locators. `ROUTES_PLAIN` holds no clashing type
 * and `ROUTES_CLASH` holds three.
 *
 * Paths are asserted exactly with `new URL(page.url()).pathname`, because `/\/activity$/` matches
 * the old and the new address alike.
 */
import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import {
  E2E_ADMIN_EMAIL,
  E2E_ADMIN_PASSWORD,
  ROUTES_CLASH_AUTH_HEADER,
  ROUTES_CLASH_BASE_URL,
  ROUTES_PLAIN_AUTH_HEADER,
  ROUTES_PLAIN_BASE_URL,
  signInAs,
} from "./constants";

const PLAIN_TYPE_KEY = "e2e_plain_plain";
const MEMBER_EMAIL = "e2e-routes-member@example.com";
const MEMBER_PASSWORD = "e2e-routes-member-pw1!";

const pathnameOf = (page: Page) => new URL(page.url()).pathname;

async function seedType(
  api: APIRequestContext,
  key: string,
  name: string,
  keyPrefix: string,
): Promise<void> {
  const existing = await api.get(`/api/v1/object-types/${key}`);
  if (existing.ok()) return;
  const created = await api.post("/api/v1/object-types", {
    data: {
      key,
      name,
      name_plural: `${name}s`,
      description: "An end-to-end fixture type for the workspace routes scenarios.",
      key_prefix: keyPrefix,
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "The record's short display name.",
          required: true,
        },
      ],
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  const record = await api.post(`/api/v1/object-types/${key}/records`, {
    data: { name: `A ${name} record` },
  });
  expect(record.ok(), await record.text()).toBeTruthy();
}

test.describe("no clashing type", () => {
  test.use({ baseURL: ROUTES_PLAIN_BASE_URL });

  test.beforeAll(async () => {
    const api = await apiRequestModule.newContext({
      baseURL: ROUTES_PLAIN_BASE_URL,
      extraHTTPHeaders: ROUTES_PLAIN_AUTH_HEADER,
    });
    await seedType(api, PLAIN_TYPE_KEY, "Plain routes", "PLR");
    await api.dispose();
  });

  test.beforeEach(async ({ page }) => {
    await signInAs(page, E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD);
  });

  for (const [oldAddress, newAddress] of [
    ["/activity", "/workspace/activity"],
    ["/audit", "/workspace/activity"],
    ["/people", "/workspace/people"],
    ["/settings", "/workspace/people"],
    ["/setup", "/workspace/setup"],
    ["/schema", "/workspace/schema"],
    ["/schema/new", "/workspace/schema/new"],
    [`/schema/${PLAIN_TYPE_KEY}`, `/workspace/schema/${PLAIN_TYPE_KEY}`],
  ]) {
    test(`${oldAddress} lands on ${newAddress}`, async ({ page }) => {
      await page.goto(oldAddress);
      await expect.poll(() => pathnameOf(page)).toBe(newAddress);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      expect(pathnameOf(page)).toBe(newAddress);
    });
  }

  test("Back from a redirected address leaves rather than bouncing", async ({ page }) => {
    await page.goto("/inbox");
    await expect(page.getByTestId("nav-inbox")).toHaveAttribute("aria-current", "page");

    await page.goto("/activity");
    await expect.poll(() => pathnameOf(page)).toBe("/workspace/activity");

    await page.goBack();
    await expect.poll(() => pathnameOf(page)).toBe("/inbox");
  });

  test("the sidebar reaches every moved screen at its new address", async ({ page }) => {
    await page.goto("/inbox");
    for (const [entry, address, heading] of [
      ["nav-people", "/workspace/people", "People & agents"],
      ["nav-activity", "/workspace/activity", "Activity"],
      ["nav-schema", "/workspace/schema", "Schema"],
      ["nav-setup", "/workspace/setup", "Setup"],
    ]) {
      await page.getByTestId(entry).click();
      await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
      expect(pathnameOf(page)).toBe(address);
    }
  });
});

test.describe("types keyed activity, schema and workspace", () => {
  test.use({ baseURL: ROUTES_CLASH_BASE_URL });

  test.beforeAll(async () => {
    const api = await apiRequestModule.newContext({
      baseURL: ROUTES_CLASH_BASE_URL,
      extraHTTPHeaders: ROUTES_CLASH_AUTH_HEADER,
    });
    await seedType(api, "activity", "Clashing activity", "ACT");
    await seedType(api, "schema", "Clashing schema", "SCH");
    await seedType(api, "workspace", "Clashing workspace", "WSP");
    const existing = await api.get("/api/v1/principals");
    const body = (await existing.json()) as { principals: { email: string | null }[] };
    if (!body.principals.some((principal) => principal.email === MEMBER_EMAIL)) {
      const member = await api.post("/api/v1/principals", {
        data: {
          type: "user",
          display_name: "E2E Routes Member",
          email: MEMBER_EMAIL,
          role: "member",
          password: MEMBER_PASSWORD,
        },
      });
      expect(member.ok(), await member.text()).toBeTruthy();
    }
    await api.dispose();
  });

  test.describe("as an administrator", () => {
    test.beforeEach(async ({ page }) => {
      await signInAs(page, E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD);
    });

    test("a type keyed activity has its table and record page, with the feed at its new address", async ({
      page,
    }) => {
      await page.goto("/activity");
      await expect(page.getByRole("heading", { name: "Clashing activity" })).toBeVisible();
      expect(pathnameOf(page)).toBe("/activity");

      await page.goto("/activity/ACT-001");
      await expect(page.getByText("A Clashing activity record").first()).toBeVisible();
      expect(pathnameOf(page)).toBe("/activity/ACT-001");

      await page.goto("/workspace/activity");
      await expect(page.getByRole("heading", { level: 1, name: "Activity", exact: true })).toBeVisible();
      expect(pathnameOf(page)).toBe("/workspace/activity");
    });

    test("a type keyed schema has its table, record page and import wizard", async ({ page }) => {
      await page.goto("/schema");
      await expect(page.getByRole("heading", { name: "Clashing schema" })).toBeVisible();
      expect(pathnameOf(page)).toBe("/schema");

      await page.goto("/schema/SCH-001");
      await expect(page.getByText("A Clashing schema record").first()).toBeVisible();
      expect(pathnameOf(page)).toBe("/schema/SCH-001");

      await page.goto("/schema/import");
      await expect(page.getByRole("heading", { name: /Import/ })).toBeVisible();
      expect(pathnameOf(page)).toBe("/schema/import");
    });

    test("a lowercase second segment under schema still goes to the editor", async ({ page }) => {
      await page.goto("/schema/activity");
      await expect.poll(() => pathnameOf(page)).toBe("/workspace/schema/activity");
      await expect(page.getByRole("heading", { name: "Clashing activity" })).toBeVisible();
    });

    // Fences: a type keyed `workspace` works on the unfixed tree, and no route under `/workspace`
    // may take that away.
    test("a type keyed workspace keeps its table, record page and import page", async ({ page }) => {
      await page.goto("/workspace");
      await expect(page.getByRole("heading", { name: "Clashing workspace" })).toBeVisible();

      await page.goto("/workspace/WSP-001");
      await expect(page.getByText("A Clashing workspace record").first()).toBeVisible();

      await page.goto("/workspace/import");
      await expect(page.getByRole("heading", { name: /Import/ })).toBeVisible();
    });

    test("a type keyed workspace does not light up beside /workspace/people", async ({ page }) => {
      await page.goto("/workspace/people");
      await expect(page.getByRole("heading", { level: 1, name: "People & agents" })).toBeVisible();
      await expect(page.locator('[aria-current="page"]')).toHaveCount(1);
      await expect(page.getByTestId("nav-people")).toHaveAttribute("aria-current", "page");
    });
  });

  test("a member with no grant on the type lands on the feed at /activity", async ({ page }) => {
    await signInAs(page, MEMBER_EMAIL, MEMBER_PASSWORD);

    await page.goto("/activity");
    await expect.poll(() => pathnameOf(page)).toBe("/workspace/activity");
    await expect(page.getByRole("heading", { level: 1, name: "Activity", exact: true })).toBeVisible();
    await expect(page.getByText("Clashing activity")).toHaveCount(0);
  });
});
