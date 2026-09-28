import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAsE2eAdmin } from "./constants";

/**
 * The table can reach every record.
 *
 * `TABLE_VIEW_PAGE_SIZE` is 200, so a type with 205 records has a second page: record 201 exists,
 * is counted in the `truncated` notice, and must be reachable through the UI. This runs against the
 * real built frontend served by a real uvicorn process (see `playwright.config.ts`), so the cursor
 * the browser sends is the one `query_records` actually issued.
 * 205 is deliberately just past the boundary: page one is a full 200 and page two holds 5, so
 * the count line and both pager ends are exercised without seeding a corpus.
 */

const OBJECT_TYPE_KEY = "e2e_paged";
const SEED_COUNT = 205;
const PAGE_SIZE = 200;

let apiContext: APIRequestContext;
const seededKeys: string[] = [];

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  const createTypeResponse = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "Paged widget",
      name_plural: "Paged widgets",
      description:
        "An end-to-end fixture object type with more records than one table page "
        + "holds, so the pager has a second page to reach.",
      key_prefix: "PGD",
      fields: [
        {
          key: "title",
          name: "Title",
          type: "short_text",
          description: "The paged widget's short display name.",
          required: true,
        },
        {
          key: "seq",
          name: "Seq",
          type: "integer",
          description: "The record's creation index, so a sort has something deterministic to "
            + "order by.",
        },
      ],
    },
  });
  expect(createTypeResponse.ok(), await createTypeResponse.text()).toBeTruthy();

  for (let index = 1; index <= SEED_COUNT; index += 1) {
    const response = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
      data: { title: `Paged widget ${String(index).padStart(3, "0")}`, seq: index },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    const body = (await response.json()) as { key: string };
    seededKeys.push(body.key);
  }
});

test.afterAll(async () => {
  await apiContext.dispose();
});

test.beforeEach(async ({ page }) => {
  await signInAsE2eAdmin(page);
});

test("the pager reaches record 201 and walks back to page one", async ({ page }) => {
  await page.goto(`/${OBJECT_TYPE_KEY}`);

  const rows = page.locator('[data-testid^="row-"]');
  /** The rendered rows' testids. The default sort is the server's, not this spec's business,
   * so every assertion below is about which *set* of records a page holds, never which key. */
  const rowIds = async () =>
    rows.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-testid") ?? ""));
  const status = page.getByRole("status");
  const next = page.getByRole("button", { name: "Next" });
  const previous = page.getByRole("button", { name: "Previous" });

  let pageOneIds: string[] = [];

  await test.step("page one holds the first 200 and cannot go back", async () => {
    await expect.poll(() => rows.count()).toBe(PAGE_SIZE);
    await expect(status).toHaveText(`Showing 1-${PAGE_SIZE} of ${SEED_COUNT}.`);
    await expect(previous).toBeDisabled();
    await expect(next).toBeEnabled();
    pageOneIds = await rowIds();
    expect(new Set(pageOneIds).size).toBe(PAGE_SIZE);
  });

  await test.step("Next reaches the five records page one could not", async () => {
    await next.click();
    await expect.poll(() => rows.count()).toBe(SEED_COUNT - PAGE_SIZE);
    await expect(status).toHaveText(`Showing ${PAGE_SIZE + 1}-${SEED_COUNT} of ${SEED_COUNT}.`);
    await expect(next).toBeDisabled();
    await expect(previous).toBeEnabled();

    // The point of the entry: page two is records page one did not contain, and together the
    // two pages reach every seeded record.
    const pageTwoIds = await rowIds();
    expect(pageTwoIds.some((id) => pageOneIds.includes(id))).toBe(false);
    expect(new Set([...pageOneIds, ...pageTwoIds]).size).toBe(SEED_COUNT);
    expect(new Set(seededKeys.map((key) => `row-${key}`))).toEqual(
      new Set([...pageOneIds, ...pageTwoIds]),
    );
  });

  await test.step("Previous walks back, and the server never rejects a cursor", async () => {
    await previous.click();
    await expect.poll(() => rows.count()).toBe(PAGE_SIZE);
    await expect(status).toHaveText(`Showing 1-${PAGE_SIZE} of ${SEED_COUNT}.`);
    await expect(previous).toBeDisabled();
    expect(await rowIds()).toEqual(pageOneIds);
    // A cursor whose sort spec no longer matched would come back 400 and render an Alert.
    await expect(page.getByRole("alert")).toHaveCount(0);
  });

  await test.step("changing the sort restarts the walk at page one", async () => {
    await next.click();
    await expect(previous).toBeEnabled();

    await page.getByRole("button", { name: /^Seq/ }).click();

    await expect(previous).toBeDisabled();
    await expect(status).toHaveText(`Showing 1-${PAGE_SIZE} of ${SEED_COUNT}.`);
    // The reset is what keeps a stale cursor from reaching `decode_cursor`, which would reject
    // it with a 400.
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
});

test("a row's key is a link through to that record's detail page", async ({ page }) => {
  await page.goto(`/${OBJECT_TYPE_KEY}`);

  const firstRow = page.locator('[data-testid^="row-"]').first();
  await expect(firstRow).toBeVisible();
  const key = (await firstRow.getAttribute("data-testid"))!.replace(/^row-/, "");

  await firstRow.getByRole("link", { name: key }).click();

  await expect(page).toHaveURL(new RegExp(`/${OBJECT_TYPE_KEY}/${key}$`));
  // The record page is titled by its display value, and the key is a mono chip. What this step
  // needs is "we are on this record's page", which the chip says exactly; `record-page.spec.ts`
  // owns the claim about what the title contains.
  await expect(page.getByTestId("record-key-chip")).toHaveText(key);
});
