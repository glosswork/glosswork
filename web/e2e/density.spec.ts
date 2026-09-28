import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAsE2eAdmin } from "./constants";

/**
 * docs/DESIGN.md 2.4's density presets, measured in a real browser.
 *
 * **This cannot be a unit test.** `getBoundingClientRect` returns zeroes in jsdom, so a row
 * height asserted there is asserted against zero and passes for the wrong reason. The numbers
 * 2.4 states — 38px and 32px rows, 34px and 30px headers — are Playwright assertions or they
 * are not proven.
 */

const OBJECT_TYPE_KEY = "e2e_density";

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  const created = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "Density widget",
      name_plural: "Density widgets",
      description:
        "An end-to-end fixture with a couple of rows, so a row height can be "
        + "measured at both density presets.",
      key_prefix: "DEN",
      fields: [
        {
          key: "title",
          name: "Title",
          type: "short_text",
          description: "The widget's short display name.",
          required: true,
        },
      ],
    },
  });
  expect(created.ok()).toBeTruthy();

  for (const title of ["First row", "Second row"]) {
    const row = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
      data: { title },
    });
    expect(row.ok()).toBeTruthy();
  }
});

test.afterAll(async () => {
  await apiContext.dispose();
});

/**
 * Density is a chip with a popover (docs/DESIGN.md 8.2's one-row toolbar), so the two preset
 * buttons live one click behind it. This spec is about 2.4's row geometry, so the click is its
 * only concern with the toolbar.
 *
 * Idempotent on purpose: the popover stays open after a preset is chosen, and clicking the chip
 * again would CLOSE it. So the click happens only when the panel is not already mounted.
 */
async function openDensityChip(page: Page): Promise<void> {
  if ((await page.getByTestId("density-toggle").count()) > 0) return;
  await page.getByTestId("density-chip").getByRole("button").first().click();
  await expect(page.getByTestId("density-toggle")).toBeVisible();
}

async function rowHeight(row: { boundingBox: () => Promise<{ height: number } | null> }) {
  const box = await row.boundingBox();
  expect(box).not.toBeNull();
  return Math.round(box!.height);
}

test("comfortable is the default, and compact is one click away", async ({ page }) => {
  await signInAsE2eAdmin(page);
  await page.goto(`/${OBJECT_TYPE_KEY}`);

  const firstRow = page.getByTestId("row-DEN-001");
  await expect(firstRow).toBeVisible();
  const header = page.getByTestId("records-table").locator("thead tr");

  // DD-41 makes comfortable the default: the beachhead persona reads more of each row
  // than it scans rows. Comfortable is what an untouched browser gets.
  expect(await rowHeight(firstRow)).toBe(38);
  expect(await rowHeight(header)).toBe(34);

  await openDensityChip(page);
  await page.getByTestId("density-compact").click();
  expect(await rowHeight(firstRow)).toBe(32);
  expect(await rowHeight(header)).toBe(30);

  // The in-row avatar tracks the preset too (2.4's fourth row, and 6.1's sizes).
  const avatar = firstRow.getByTestId("avatar").first();
  const avatarBox = await avatar.boundingBox();
  expect(Math.round(avatarBox!.height)).toBe(18);
});

test("the choice survives a reload", async ({ page }) => {
  await signInAsE2eAdmin(page);
  await page.goto(`/${OBJECT_TYPE_KEY}`);
  await expect(page.getByTestId("row-DEN-001")).toBeVisible();

  await openDensityChip(page);
  await page.getByTestId("density-compact").click();
  expect(await rowHeight(page.getByTestId("row-DEN-001"))).toBe(32);

  await page.reload();
  await expect(page.getByTestId("row-DEN-001")).toBeVisible();
  expect(await rowHeight(page.getByTestId("row-DEN-001"))).toBe(32);

  // Put it back, so this spec leaves the shared browser profile as it found it for whichever
  // worker runs next.
  await openDensityChip(page);
  await page.getByTestId("density-comfortable").click();
  expect(await rowHeight(page.getByTestId("row-DEN-001"))).toBe(38);
});
