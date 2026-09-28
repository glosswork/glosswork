import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAsE2eAdmin } from "./constants";

/**
 * A heading outline never skips a level.
 *
 * Read off the live page during a design review, the complete heading list on a table view
 * was once `["H1 <type name>", "H3 Sort"]`, with no `h2`, so a screen-reader user navigating by
 * heading got a broken outline on the most-used screen in the product. This runs in the functional
 * `e2e` project against the real built frontend and a real uvicorn process, because the
 * question is what the rendered document actually contains.
 *
 * It seeds its own object type rather than reading whichever type another spec's fixture
 * happened to create first: the assertion walks every heading on the page, and a shared type
 * whose field set changes underneath it would make the result depend on run order.
 *
 * **The table-page walk opens the Sort chip first.** Group, sort and columns sit behind
 * popovers, and `Popover` unmounts its panel rather than hiding it, so `SortControls`' heading,
 * the `H3 Sort` this spec was written about, is not in the document at all while the chip is
 * closed. Measured on the built page: the closed table view's complete outline is
 * `["H1 <type name>"]`, a single heading. The walk below still asserts two true things about
 * that page (a heading exists in `main`, and the first is an `h1`), but its skip loop has
 * nothing to iterate: **over the closed page it can never fail**, on the one screen this spec
 * exists to protect. So the test opens the chip, which puts the real `H2 Sort` back into the
 * outline and gives the walk a second level to check.
 *
 * The alternative — giving the About disclosure its own `h2` — was considered and does not
 * replace this: that panel is unmounted while closed too, so it would leave the default page at
 * one heading while adding markup. If About later carries a heading on its own merits, the two
 * are compatible and this test opens both; they are not alternatives.
 *
 * A walk over a table view alone cannot see a skip authored through CONTENT:
 * `react-markdown` emits real `<h1>`…`<h6>` for user text, the record detail page's outline is
 * `h1` (the record's title) then `h2` ("Activity"), and a comment containing `###### note` would
 * insert an `h6` straight after that `h2`, a four-level skip. The record page test below walks
 * the record detail page with exactly that comment in place. Unlike the scope fences in the
 * component suite, it can fail: run against a tree whose comments render heading elements as
 * the library emits them, it reports the h2 -> h6 jump by name.
 */

const OBJECT_TYPE_KEY = "e2e_outline";

/** The hostile shape, in the sense that matters here: the deepest level a user can author, so
 * the skip it would produce is the largest one. */
const HEADING_COMMENT = "###### note";

/** Deterministic: the type's own `key_prefix` sequence, and this type has exactly one record.
 * It is the idempotence gate too — there is no GET route that lists a type's records, so the
 * record's own existence is what a retried worker checks. */
const RECORD_KEY = "OTL-001";

/** The record's display value, which is the page's `h1`. The fixture type has one
 * field, so it is the display field whether or not one is pinned (DD-23's fallback). */
const RECORD_TITLE = "Outline subject";

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  // Idempotent, because `playwright.config.ts` retries a worker death once and the retry lands
  // in the same data directory: a second POST would fail on the `key_prefix` uniqueness rule.
  const existing = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}`);
  if (existing.ok()) return;

  const response = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "Outline widget",
      name_plural: "Outline widgets",
      description:
        "An end-to-end fixture object type whose table view exists only so the "
        + "page's heading outline can be walked in a real browser.",
      key_prefix: "OTL",
      fields: [
        {
          key: "title",
          name: "Title",
          type: "short_text",
          description: "The outline widget's short display name.",
          required: true,
        },
      ],
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();

  // The record page's fixture: one record carrying one comment written as a markdown
  // heading. Seeded inside the same gate rather than in a hook of its own, so the whole
  // fixture — type, record and comment — is created exactly once.
  const record = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
    data: { title: "Outline subject" },
  });
  expect(record.ok(), await record.text()).toBeTruthy();
  expect(((await record.json()) as { key: string }).key).toBe(RECORD_KEY);

  const comment = await apiContext.post(`/api/v1/records/${RECORD_KEY}/comments`, {
    data: { body: HEADING_COMMENT },
  });
  expect(comment.ok(), await comment.text()).toBeTruthy();
});

test.afterAll(async () => {
  await apiContext.dispose();
});

test("the table view's heading levels never skip", async ({ page }) => {
  await signInAsE2eAdmin(page);
  await page.goto(`/${OBJECT_TYPE_KEY}`);
  await expect(page.getByRole("heading", { level: 1, name: "Outline widget" })).toBeVisible();

  // **The Sort chip is opened first, and that is what makes this walk measure anything at all.**
  // See the note in this file's header: `SortControls`' heading lives inside a popover panel that
  // `Popover` UNMOUNTS while the chip is closed, so the closed table page carries exactly one
  // heading and a walk over it cannot catch a skip. One click puts the second level back on the
  // page.
  await page.getByTestId("sort-chip").getByRole("button").first().click();
  // The panel, addressed by a locator that says nothing about heading LEVEL, so the walk below
  // is the assertion that fails when the level is wrong rather than this one.
  await expect(page.getByTestId("sort-controls")).toBeVisible();

  // The walk runs FIRST, before the corroborating assertion under it: an assertion that only
  // ever executes after a failing one has never been measured (AGENTS.md, Traps). Measured by
  // setting `SortControls`' `h2` to an `h3`, where it fails naming the jump:
  // `heading level jumps from h1 "Outline widget" to h3 "Sort"`.
  await expectNoHeadingSkip(page);

  // And the outline really did gain that second level, rather than the click silently failing
  // and the walk passing over one heading again, which is the easiest way back into a walk that
  // cannot fail.
  await expect(page.getByRole("heading", { level: 2, name: "Sort" })).toBeVisible();
});

test("the two-pane Inbox's heading levels never skip", async ({ page }) => {
  // A two-pane screen carries a page `h1` and a detail `h2`, exactly the shape a skip hides in,
  // so its outline is asserted too, not only `/{type}` and `/{type}/{record}`.
  await signInAsE2eAdmin(page);
  await page.goto("/inbox");
  await expect(page.getByRole("heading", { level: 1, name: "Inbox" })).toBeVisible();

  await expectNoHeadingSkip(page);
});

test("a user-authored heading in a comment never enters the record page's outline", async ({
  page,
}) => {
  await signInAsE2eAdmin(page);
  await page.goto(`/${OBJECT_TYPE_KEY}/${RECORD_KEY}`);
  // The page is titled by its display value, not by its key, and the key is a mono chip
  // beside the version. The `h1` is the outline's root, which is all this test needs from it;
  // `record-page.spec.ts` is what asserts the title is the display value.
  await expect(page.getByRole("heading", { level: 1, name: RECORD_TITLE })).toBeVisible();

  // The comment is on the page and its text is rendered...
  // Comments and the audit timeline are one `Activity` card, so the thread is scoped to that
  // region.
  const thread = page.getByRole("region", { name: "Activity" });
  await expect(thread).toContainText("note");

  // ...and the page's outline is unchanged by the presence of user-authored heading syntax.
  // This runs FIRST on purpose: it is the regression the test exists for, and its failure
  // message names the jump ("heading level jumps from h2 \"Comments\" to h6 \"note\""), which
  // is what a future reader needs. Measured against a tree whose comments render real heading
  // elements.
  await expectNoHeadingSkip(page);

  // Then the mechanism: heading APPEARANCE, from a non-heading element.
  // Scoped to the COMMENT BODY, not to the section: the Activity card renders its own legitimate
  // `<h2>Activity</h2>` inside its region, so a region-scoped
  // `h1…h6` count can never reach 0 and would fail on every tree, fixed or not — an assertion
  // that proves nothing while looking like it proves everything. (Written that way once, and
  // caught by the `verify` agent running the checks rather than by the author.)
  // The count comes first: it is the safety property (no heading element reaches the document
  // from user text), and each of these was measured against a tree rendering real headings, in
  // the order written: an assertion that only ever runs after a failing one is unmeasured.
  const body = thread.locator("[data-testid^='body-']").first();
  // `body` is asserted to EXIST before anything is counted inside it. Playwright does not throw
  // for an empty parent match, so `toHaveCount(0)` on the children of a locator that matched
  // nothing would pass vacuously: the same failure mode as a section-scoped count, arriving by a
  // different route.
  await expect(body).toBeVisible();
  await expect(body.locator("h1, h2, h3, h4, h5, h6")).toHaveCount(0);
  await expect(body.locator(".md-h")).toHaveText("note");
});

/**
 * The walk itself. Scoped to `main`: the app shell's header carries no headings, and scoping
 * keeps this about the screen under review rather than about the chrome around it.
 */
async function expectNoHeadingSkip(page: Page): Promise<void> {
  const outline = await page.evaluate(() =>
    Array.from(document.querySelectorAll("main h1, main h2, main h3, main h4, main h5, main h6"))
      .map((el) => ({
        level: Number(el.tagName.slice(1)),
        text: (el.textContent ?? "").trim(),
      })),
  );

  expect(outline.length, "no headings found in <main>").toBeGreaterThan(0);
  expect(outline[0]?.level, `first heading is ${JSON.stringify(outline[0])}`).toBe(1);

  for (let i = 1; i < outline.length; i += 1) {
    const previous = outline[i - 1]!;
    const current = outline[i]!;
    expect(
      current.level - previous.level,
      `heading level jumps from h${previous.level} "${previous.text}" `
        + `to h${current.level} "${current.text}"`,
    ).toBeLessThanOrEqual(1);
  }
}
