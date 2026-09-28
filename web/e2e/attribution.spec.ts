import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAsE2eAdmin } from "./constants";

/**
 * The table says whose hand touched each row, and no raw id reaches the attribution surfaces.
 *
 * Three rows, three hands, seeded through the real API against the real built frontend:
 *
 *   1. written over REST carrying `X-Agent-Label` — an agent
 *   2. written over REST with no label — a person
 *   3. row 1 then edited in the browser, through the UI's own session cookie — a person
 *
 * The third is an edit rather than a row created from the UI, because editing the
 * agent-written row is the stronger assertion: it proves end to end that a person's
 * edit CLEARS a previous agent's mark, which is the reason the column is written on every
 * value-changing write rather than only on a labelled one.
 *
 * **The MCP surface is deliberately not driven from the browser.** DD-17 resolves the label at
 * exactly one function for both surfaces, and `tests/test_one_agent_label_resolver.py` walks the
 * AST and fails if a second appears, so a third seeding path here would assert the resolver
 * twice and the rendering once. What is worth proving in a browser is that the *rendering*
 * distinguishes the kinds, which is what this does.
 */

const OBJECT_TYPE_KEY = "e2e_attribution";
const AGENT_LABEL = "sales-agent";

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  // Idempotent per type, in the idiom `ui-visual.spec.ts::ensureDetailFixtureSeeded` uses:
  // `beforeAll` runs once per worker AND again on a retry, and a second create is a 409 that
  // fails every test in the file for a reason that has nothing to do with what they assert.
  const existing = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}`);
  if (existing.ok()) return;

  const created = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "Attribution widget",
      name_plural: "Attribution widgets",
      description:
        "An end-to-end fixture whose rows are written by different hands, so the By "
        + "column has an agent and a person to tell apart.",
      key_prefix: "ATR",
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

  // 1. An agent write. `middleware.py` resolves the label at the REST edge (DD-17); an edge that
  //    hardcoded `agent_label_id=None` on every REST request would make this row a person's.
  const byAgent = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
    data: { title: "Written by an agent" },
    headers: { "X-Agent-Label": AGENT_LABEL },
  });
  expect(byAgent.ok()).toBeTruthy();

  // 2. A person's write over the same credential, with no label.
  const byPerson = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
    data: { title: "Written by a person over REST" },
  });
  expect(byPerson.ok()).toBeTruthy();
});

test.afterAll(async () => {
  await apiContext.dispose();
});

test("the By column tells an agent's row from a person's", async ({ page }) => {
  await signInAsE2eAdmin(page);
  await page.goto(`/${OBJECT_TYPE_KEY}`);

  const agentRow = page.getByTestId("row-ATR-001");
  const personRow = page.getByTestId("row-ATR-002");
  await expect(agentRow).toBeVisible();
  await expect(personRow).toBeVisible();

  // The By column is the second cell, after the always-first selection checkbox.
  const agentAvatar = agentRow.getByTestId("avatar").first();
  const personAvatar = personRow.getByTestId("avatar").first();

  // Shape carries the kind, so the distinction survives greyscale (docs/DESIGN.md 6.1, 10).
  await expect(agentAvatar).toHaveAttribute("data-kind", "agent");
  await expect(personAvatar).toHaveAttribute("data-kind", "person");

  // And the label's TEXT is there to read, not a UUID. Asserted with a locator because a
  // passing visual suite is evidence about layout only: `toHaveScreenshot` runs at
  // maxDiffPixelRatio 0.001, about 1,024 pixels on a 1280x800 shot, which is more ink than a
  // heading contains.
  await expect(agentAvatar).toHaveAttribute("aria-label", new RegExp(AGENT_LABEL));
  await expect(personAvatar).toHaveAttribute("aria-label", /\w/);
  await expect(personAvatar).not.toHaveAttribute("aria-label", /agent/);
});

test("a person's edit in the browser clears the agent's mark", async ({ page }) => {
  await signInAsE2eAdmin(page);
  await page.goto(`/${OBJECT_TYPE_KEY}`);

  const row = page.getByTestId("row-ATR-001");
  await expect(row.getByTestId("avatar").first()).toHaveAttribute("data-kind", "agent");

  // The browser writes through a session cookie, which DD-17 refuses an agent label for by
  // design: `surface="ui"` carrying one would be a contradiction, and the UI sends no header.
  const cell = page.getByRole("button", { name: "Edit Title for ATR-001" });
  await cell.click();
  const input = page.getByLabel("Title value for ATR-001");
  await input.fill("Taken over by a person");
  await input.press("Enter");
  await expect(page.getByRole("button", { name: "Edit Title for ATR-001" })).toHaveText(
    "Taken over by a person",
  );

  // The mark is written on EVERY value-changing write, including an unlabelled one. Left to
  // default, the agent's mark would outlive its involvement and the By column would keep naming
  // it long after a person took the record over.
  await expect(row.getByTestId("avatar").first()).toHaveAttribute("data-kind", "person");
});

test("no raw id reaches the attribution surfaces", async ({ page }) => {
  await signInAsE2eAdmin(page);

  /**
   * **Scoped to the attribution elements, not the whole page.** A page-wide "no text node
   * matches a UUID" is unsatisfiable here and would have to be defeated to go green: FR-D2
   * writes one audit row per changed field and the timeline renders each `old_value` and
   * `new_value` verbatim, so a `user_ref` or `attachment` field's value *is* a UUID on screen,
   * by design. The raw-id fallback (DD-25) for a referent that is gone is deliberate too.
   */
  const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/;

  /**
   * Read every hand's text in ONE call, then assert over the strings.
   *
   * A loop of `for (const hand of await locator.all())` with `await hand.textContent()` per
   * element hangs: `.all()` snapshots one element
   * handle per match, and the audit browser refetches — its filter debounces, and this runs
   * right after a `fill()` — so the table re-renders mid-loop and the handles for the later
   * rows stop resolving. Playwright then waits on `.nth(38)` until the 60s test timeout, and
   * reports a timeout rather than the mismatch the test is about.
   *
   * It survives locally because it only bites once the table is long enough for the refetch to
   * land inside the loop: it flaked once locally and passed on retry, and it failed on BOTH
   * attempts in CI, on a slower runner and against a database carrying more fixtures.
   *
   * `allTextContents()` resolves the locator once and returns the strings, so there are no
   * handles left to go stale and nothing that can wait. Reading a superset of the filtered rows
   * is fine and is strictly stronger: no hand anywhere in this table may show a raw id.
   */
  function expectNoRawId(texts: string[]): void {
    // Non-empty first: a loop over an empty array asserts nothing while looking like proof.
    expect(texts.length).toBeGreaterThan(0);
    for (const text of texts) {
      expect(text).not.toMatch(UUID);
    }
  }

  await page.goto(`/${OBJECT_TYPE_KEY}`);
  await expect(page.getByTestId("row-ATR-001")).toBeVisible();
  for (const key of ["ATR-001", "ATR-002"]) {
    const by = page.getByTestId(`row-${key}`).getByTestId("avatar").first();
    await expect(by).toBeVisible();
    expect(await by.getAttribute("aria-label")).not.toMatch(UUID);
  }

  // The record's own feed: its event headers name a hand, not an id. The audit timeline and the
  // comment thread are one Activity card, so this covers comment authors as well as write
  // authors.
  await page.goto(`/${OBJECT_TYPE_KEY}/ATR-001`);
  const timeline = page.getByRole("region", { name: "Activity" });
  await expect(timeline.getByTestId("hand").first()).toBeVisible();
  expectNoRawId(await timeline.getByTestId("hand").allTextContents());

  // `/activity`, where an audit of the UI once found raw principal ids rendered in attribution.
  // This covers the entry header's whole hand.
  await page.goto("/activity");
  // Scoped to this spec's own record. The feed is newest-first over every record in the
  // deployment, so an unscoped assertion is about whichever writes the rest of the suite
  // happened to make last, and this spec's agent label may not be on page one at all.
  const chips = page.getByTestId("activity-filters");
  await chips.getByRole("button", { name: "Add filter" }).click();
  await page.getByRole("menuitem", { name: "Record" }).click();
  await page.getByRole("textbox", { name: "Record" }).fill("ATR-001");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(chips.getByText("Record is ATR-001")).toBeVisible();

  const feed = page.getByTestId("activity-feed");
  await expect(feed.getByTestId("hand").first()).toBeVisible();
  expectNoRawId(await feed.getByTestId("hand").allTextContents());
  await expect(feed.getByText(AGENT_LABEL).first()).toBeVisible();
});
