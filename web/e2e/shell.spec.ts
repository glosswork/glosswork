import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
  type Locator,
} from "@playwright/test";

import { SHELL_NAV } from "../src/app/shellNav";
import {
  E2E_AUTH_HEADER,
  E2E_BASE_URL,
  E2E_WORKSPACE_NAME,
  signInAsE2eAdmin,
} from "./constants";

/**
 * The shell is a sidebar (docs/DESIGN.md 8.1, 9).
 *
 * One case per claim, each named so `-g` can run it alone: a single result for several claims
 * cannot say which one failed.
 *
 * **Everything here is a Playwright assertion because none of it is provable anywhere else.**
 * AGENTS.md: layout is never proven in jsdom, where `getBoundingClientRect` returns zeroes. The
 * 224px width, the 960px breakpoint and "nothing reachable at 1280 is unreachable at 800" are
 * assertions in a real browser or they are not proven. The visual project cannot stand in for
 * them either: `toHaveScreenshot` runs at `maxDiffPixelRatio: 0.001`, about 1,024 pixels on a
 * 1280x800 shot, which is more ink than a heading contains.
 *
 * **Every case asserts a non-empty subject before asserting anything about its contents.** A
 * `toHaveCount(0)` on a locator that matched nothing, or a loop over an empty list, passes
 * vacuously while looking like proof (`heading-outline.spec.ts` records the same lesson).
 */

const WIDE = { width: 1280, height: 800 };
/** Below the 960px breakpoint (docs/DESIGN.md 9). 800 rather than 390: this is the breakpoint
 * the shell implements, and the phone widths below 640 are explicitly not decided (DD-41). */
const NARROW = { width: 800, height: 800 };

/** docs/DESIGN.md 8.1. The number is the assertion, so it is written once here. */
const SIDEBAR_WIDTH = 224;

const OBJECT_TYPE_KEY = "e2e_shell";

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  // Idempotent for the same reason `heading-outline.spec.ts` is: `playwright.config.ts` retries
  // a worker death once into the same data directory, and a second POST would fail the
  // `key_prefix` uniqueness rule.
  const existing = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}`);
  if (existing.ok()) return;

  const response = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "Shell widget",
      name_plural: "Shell widgets",
      description:
        "An end-to-end fixture object type, so the sidebar's Tracking section has "
        + "at least one entry with a record count to assert against.",
      key_prefix: "SHL",
      fields: [
        {
          key: "title",
          name: "Title",
          type: "short_text",
          description: "The shell widget's short display name.",
          required: true,
        },
      ],
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
});

test.afterAll(async () => {
  await apiContext.dispose();
});

/**
 * Every destination a shell offers, by test id: the static entries (`nav-inbox`, ...) plus one
 * per live object type (`nav-type-e2e_shell`, ...). Read from the page rather than from a
 * constant, so a link added to the sidebar and forgotten in the menu is caught here rather than
 * in review.
 *
 * **By test id and not by rendered text**, because rendered text fails for a reason worth
 * keeping: a sidebar row contains its count, so `allInnerTexts()` returns `"Shell widget\n0"`
 * and no amount of trimming makes that equal to `"Shell widget"`. Identity is also the stronger
 * assertion -- it keeps holding when a count changes, which is the whole point of the counts
 * being live data.
 */
async function shellLinkIds(scope: Locator): Promise<string[]> {
  // Waits for the LAST-ARRIVING link, not the first. The static entries render synchronously
  // from `shellNav.ts` while the Tracking section arrives with `list_object_types`, so
  // `links.first()` is visible long before the list is complete -- and a snapshot taken then
  // reports the static five and calls the object types missing. Anchoring on the fixture's own
  // Tracking entry is what makes the snapshot a snapshot of the finished sidebar.
  await expect(scope.getByTestId(`nav-type-${OBJECT_TYPE_KEY}`)).toBeVisible();
  const links = scope.getByRole("link");
  const ids = await links.evaluateAll((elements) =>
    elements.map((el) => el.getAttribute("data-testid") ?? ""),
  );
  return ids.filter(Boolean);
}

test("sidebar geometry - 224px, outside main, and no heading of its own", async ({
  page,
}) => {
  await page.setViewportSize(WIDE);
  await signInAsE2eAdmin(page);

  const sidebar = page.getByTestId("sidebar");
  await expect(sidebar).toBeVisible();

  // The border box, which is what `w-56` sets: Tailwind's preflight applies
  // `box-sizing: border-box` globally, so the right-hand rule is inside the 224 rather than a
  // 225th pixel. Stated because an off-by-one here reads as a mystery rather than as a choice.
  const box = await sidebar.boundingBox();
  expect(box, "the sidebar has no box").not.toBeNull();
  expect(box!.width).toBe(SIDEBAR_WIDTH);

  // Outside `main`, which is what keeps `heading-outline.spec.ts`'s walk about the screen
  // rather than about the chrome around it.
  await expect(page.locator("main").getByTestId("sidebar")).toHaveCount(0);

  // The document keeps exactly one `h1` and it is the page's, not the workspace's:
  // `theme.spec.ts`'s faces test reads `document.querySelector("h1")` and would silently start
  // measuring the sidebar instead. Asserted as a count AND a containment, because a count of 1
  // alone would also pass if the page's own h1 disappeared and the sidebar's replaced it.
  await expect(page.locator("h1")).toHaveCount(1);
  await expect(page.locator("main h1")).toHaveCount(1);
});

test("the workspace block shows the configured name and the live counts", async ({
  page,
}) => {
  await page.setViewportSize(WIDE);
  await signInAsE2eAdmin(page);

  // Asserted with a LOCATOR, never by screenshot. `toHaveScreenshot` runs at a tolerance of
  // about 1,024 pixels on a 1280x800 shot -- more ink than this line contains -- so the picture
  // of the sidebar is evidence about layout and nothing else (AGENTS.md).
  await expect(page.getByTestId("workspace-name")).toHaveText(E2E_WORKSPACE_NAME);

  // The two section labels, and the signed-in person's role badge. Asserted here because
  // `ui-visual.spec.ts`, the visual project, cannot prove text, since its tolerance is looser
  // than a heading's worth of ink. The role badge is also asserted in `auth.spec.ts`; this
  // scenario is where the sidebar's own claims live.
  const sidebar = page.getByTestId("sidebar");
  await expect(sidebar.getByText("Tracking", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("Workspace", { exact: true })).toBeVisible();
  await expect(page.getByTestId("current-principal-role")).toHaveText("admin");

  // The counts come from `GET /api/v1/workspace` in the same run, so a hardcoded pair fails.
  const document = await apiContext.get("/api/v1/workspace");
  expect(document.ok(), await document.text()).toBeTruthy();
  const { people, agents } = (await document.json()) as { people: number; agents: number };
  expect(people).toBeGreaterThan(0);
  await expect(page.getByTestId("workspace-people-agents")).toHaveText(
    `${people} ${people === 1 ? "person" : "people"} · ${agents} ${agents === 1 ? "agent" : "agents"}`,
  );
});

test("every link survives the narrow breakpoint", async ({ page }) => {
  await page.setViewportSize(WIDE);
  await signInAsE2eAdmin(page);

  const wideIds = await shellLinkIds(page.getByTestId("sidebar"));
  // Non-empty, and it really contains what it should: a list of zero entries satisfies "every
  // entry is reachable" over nothing at all.
  expect(wideIds.length).toBeGreaterThan(0);
  expect(wideIds, "the Tracking section lists no object type").toContain(
    `nav-type-${OBJECT_TYPE_KEY}`,
  );
  for (const entry of SHELL_NAV) {
    expect(wideIds, `sidebar is missing the static entry ${entry.label}`).toContain(
      `nav-${entry.id}`,
    );
    // The label is copy and the id is identity, so both are asserted: an entry present under the
    // right id but rendering the wrong words would otherwise pass.
    await expect(page.getByTestId("sidebar").getByTestId(`nav-${entry.id}`)).toContainText(
      entry.label,
    );
  }

  // The sidebar is gone below the breakpoint, and the top bar has taken its place. Both halves
  // asserted: "the sidebar is hidden" alone is satisfied by a shell that renders nothing.
  await page.setViewportSize(NARROW);
  await expect(page.getByTestId("sidebar")).toBeHidden();
  await expect(page.getByTestId("shell-top-bar")).toBeVisible();

  // docs/DESIGN.md 1 rule 4 is why this is a word and not a glyph.
  const toggle = page.getByRole("button", { name: "Menu" });
  await expect(toggle).toBeVisible();
  await toggle.click();

  const menu = page.getByTestId("shell-menu");
  await expect(menu).toBeVisible();
  const narrowIds = await shellLinkIds(menu);

  // DD-42 applied to viewports (DD-41): nothing reachable at 1280 is unreachable at 800. Set
  // comparison rather than array equality, because the order is a design choice and this is an
  // assertion about reachability.
  expect(new Set(narrowIds)).toEqual(new Set(wideIds));
});

test("the Inbox count equals the API's pending count", async ({ page }) => {
  // Two proposals, so the assertion is about a number rather than about presence: a badge
  // hardcoded to 1 would pass against a single-proposal fixture.
  for (const fieldKey of ["title"]) {
    await apiContext.post("/api/v1/schema-proposals", {
      data: { change_type: "delete_field", object_type: OBJECT_TYPE_KEY, field_key: fieldKey },
    });
  }
  await apiContext.post("/api/v1/schema-proposals", {
    data: { change_type: "delete_object_type", object_type: OBJECT_TYPE_KEY },
  });

  /**
   * The count comes from THIS run, never from a constant (docs/changes/README.md), and it is
   * `total_count` rather than `proposals.length`.
   *
   * Each for its own reason. `proposals.length` would be exact only for an unbounded route; it
   * returns a bounded page, so a length is the page size once a deployment passes fifty pending
   * proposals, the same defect the sidebar hook itself once had to be fixed for.
   *
   * And the comparison **polls**, because a single read-then-assert races. The badge is a
   * deployment-wide count, the functional project shares one database, and Playwright runs spec
   * files in parallel — so between snapshotting the number and signing in, `inbox.spec.ts` can
   * approve or decline one and move it. That is exactly how this test flaked once the suite
   * gained a spec that decides proposals. Polling with a fresh
   * read on each attempt converges as soon as the other file's writes stop, and still fails for
   * a badge that is simply wrong.
   */
  const pendingTotal = async () => {
    const response = await apiContext.get("/api/v1/schema-proposals?status=pending");
    expect(response.ok(), await response.text()).toBeTruthy();
    return ((await response.json()) as { total_count: number }).total_count;
  };

  // Asserted non-zero first: `0 === 0` passes against a badge that renders nothing.
  expect(await pendingTotal()).toBeGreaterThan(0);

  await page.setViewportSize(WIDE);
  await signInAsE2eAdmin(page);

  const badge = page.getByTestId("inbox-count");
  await expect(badge).toBeVisible();
  await expect
    .poll(async () => {
      const total = await pendingTotal();
      // Reload so the badge refetches: it is served from a query cache, so a count another spec
      // moved after this page loaded would never reach it otherwise.
      await page.reload();
      await expect(badge).toBeVisible();
      return (await badge.textContent())?.trim() === String(total);
    })
    .toBe(true);
});

test("search has moved out of the header", async ({ page }) => {
  await page.setViewportSize(WIDE);
  await signInAsE2eAdmin(page);

  // A header search slot, `search-slot`, would fail this honestly.
  await expect(page.getByTestId("search-slot")).toHaveCount(0);

  // The sidebar item reaches the search page (an ordinary link, not an overlay).
  await page.getByTestId("sidebar").getByRole("link", { name: "Search" }).click();
  await expect(page).toHaveURL(/\/search$/);

  // And so does the `/` key, from a page that is not already the search page.
  await page.goto(`/${OBJECT_TYPE_KEY}`);
  // Wait for the shell to be mounted before pressing the key. The handler is attached by a React
  // effect, so a keypress immediately after `goto` can land before the listener exists -- which
  // is exactly what happened: this failed once and passed on retry, and Playwright reports that
  // as flaky rather than passed. A test-level flake is a bug to chase (`playwright.config.ts`),
  // and the bug was in the test.
  await expect(page.getByTestId("sidebar")).toBeVisible();
  await page.locator("body").press("/");
  await expect(page).toHaveURL(/\/search$/);
  await expect(page.getByTestId("search-page-input")).toBeFocused();

  // `/` typed INTO a text entry is a character, not a shortcut. Without this the
  // key handler navigates away mid-sentence out of any filter value or comment box in the
  // product. Asserted last because it is the half that a naive `keydown` listener fails.
  const input = page.getByTestId("search-page-input");
  await input.fill("");
  await input.press("/");
  await expect(input).toHaveValue("/");
});

test("the sidebar fits, and the signed-in person is reachable", async ({ page }) => {
  await page.setViewportSize(WIDE);
  await signInAsE2eAdmin(page);

  const sidebar = page.getByTestId("sidebar");
  const person = page.getByTestId("current-principal");
  await expect(person).toBeVisible();

  // `toBeVisible()` is satisfied by an element pushed below the fold, so it is the
  // weaker of the two failures this guards and cannot be the whole assertion. What matters is
  // that the person's block lies within the sidebar's own scrollable extent: a sidebar that
  // clips it (`overflow-hidden`) fails 74 tests at once through `signInAs`, and one that pushes
  // it off the page silently breaks DD-42 applied to viewports.
  const fits = await sidebar.evaluate((el, testId) => {
    const target = el.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
    if (target === null) return { found: false, drawn: false, overflowY: "" };
    const box = target.getBoundingClientRect();
    return {
      found: true,
      drawn: box.height > 0 && box.width > 0,
      // The axis that decides between "scrolls" and "clips" when the content is taller than
      // the column, which on this fixture's eleven object types it is.
      overflowY: getComputedStyle(el).overflowY,
    };
  }, "current-principal");

  expect(fits.found, "current-principal is not inside the sidebar").toBe(true);
  expect(fits.drawn, "current-principal is collapsed to zero size").toBe(true);
  expect(fits.overflowY, "the sidebar clips its overflow instead of scrolling it").not.toBe(
    "hidden",
  );

  // And it is genuinely reachable by a user, not merely present in the DOM: scrolling the
  // sidebar to its end brings the sign-out control into the viewport.
  await sidebar.evaluate((el) => el.scrollTo(0, el.scrollHeight));
  await expect(page.getByRole("button", { name: "Sign out" })).toBeInViewport();
});
