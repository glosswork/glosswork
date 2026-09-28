import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAsE2eAdmin } from "./constants";

/**
 * The comment-search end-to-end scenario: seed an object type, a record, and a comment over
 * REST, poll the admin search-index status until the worker has drained the queue (a search
 * issued before the drain would find the comment by keyword alone, since its FTS row lands
 * synchronously in the comment's own write transaction, and prove nothing about the semantic
 * arm, whose embedding job the worker has not processed yet), then search the comment-only
 * phrase through the REAL search box and assert the result names the comment as its hit source
 * with a `<mark>` in the snippet, in both hybrid and semantic mode. The Playwright `webServer`
 * env carries `GW_MODEL_DIR`, so this runs against the real ONNX provider and worker thread,
 * never a fake one.
 */

const OBJECT_TYPE_KEY = "e2e_note";
const COMMENT_BODY = "The wombat migration timeline slipped by a fortnight.";

let apiContext: APIRequestContext;
let recordKey: string;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  const createTypeResponse = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "E2E Note",
      name_plural: "E2E Notes",
      description: "An end-to-end fixture object type, seeded fresh for this test run.",
      key_prefix: "ENOTE",
      fields: [
        {
          key: "body",
          name: "Body",
          type: "long_text",
          description: "The note's free-text body.",
        },
      ],
    },
  });
  expect(createTypeResponse.ok(), await createTypeResponse.text()).toBeTruthy();

  const createRecordResponse = await apiContext.post(
    `/api/v1/object-types/${OBJECT_TYPE_KEY}/records`,
    { data: { body: "The quarterly onboarding checklist was reviewed on Monday." } },
  );
  expect(createRecordResponse.ok(), await createRecordResponse.text()).toBeTruthy();
  const record = (await createRecordResponse.json()) as { key: string };
  recordKey = record.key;

  const createCommentResponse = await apiContext.post(`/api/v1/records/${recordKey}/comments`, {
    data: { body: COMMENT_BODY },
  });
  expect(createCommentResponse.ok(), await createCommentResponse.text()).toBeTruthy();

  await expect
    .poll(
      async () => {
        const response = await apiContext.get("/api/v1/admin/search-index");
        expect(response.ok()).toBeTruthy();
        const body = (await response.json()) as { pending_jobs: number };
        return body.pending_jobs;
      },
      { timeout: 30_000 },
    )
    .toBe(0);
});

test.afterAll(async () => {
  await apiContext.dispose();
});

// The served bundle carries no credential at all. Every spec that drives the UI signs the
// browser in through the real `/login` form first.
test.beforeEach(async ({ page }) => {
  await signInAsE2eAdmin(page);
});

/**
 * A real browser search finds a phrase that exists only in a comment. The query box lives on the
 * search page, reached from the sidebar's Search item or the `/` key.
 *
 * The `/` key is the entry point exercised here, deliberately: driving it end to end is what
 * proves the shortcut reaches a focused box rather than merely a URL.
 */
test("search finds a comment-only phrase, in hybrid and semantic mode", async ({
  page,
}) => {
  await test.step("hybrid search from the shell finds the comment", async () => {
    await page.goto("/");
    // Wait for the shell before pressing: `/` redirects to the first object type, and the key
    // handler is attached by a React effect, so a keypress issued straight after `goto` can land
    // mid-redirect with no listener yet. The same fix as `shell.spec.ts`'s slash-shortcut
    // scenario; a full run catches this where a single-spec run may not.
    await expect(page.getByTestId("sidebar")).toBeVisible();
    // `body` rather than the document: the key handler ignores keystrokes aimed at a text entry,
    // so pressing this on an input would correctly do nothing.
    await page.locator("body").press("/");
    await expect(page).toHaveURL(/\/search/);

    await page.getByTestId("search-page-input").fill("wombat migration timeline");
    await page.getByTestId("search-page-input").press("Enter");

    await expect(page).toHaveURL(/\/search\?q=/);

    const result = page.getByTestId(`search-result-${recordKey}`);
    await expect(result).toBeVisible();
    await expect(result.getByTestId("hit-source")).toHaveText("comment by E2E Admin");
    expect(await result.locator("mark").count()).toBeGreaterThanOrEqual(1);
  });

  await test.step("switching to Semantic mode keeps the comment-sourced record visible", async () => {
    await page.getByRole("radio", { name: "Semantic" }).click();

    const result = page.getByTestId(`search-result-${recordKey}`);
    await expect(result).toBeVisible();
    await expect(result.getByTestId("hit-source")).toHaveText("comment by E2E Admin");
  });
});
