/**
 * `/activity`, against a real uvicorn process and the real built frontend.
 *
 * **The spec exists for one claim**: filtering by an agent chosen from the picker returns only
 * rows with that label. Two agent labels are seeded, not one, because a
 * one-label fixture passes with the filter ignored entirely — the assertion would be about
 * nothing.
 *
 * It also carries the claims jsdom cannot make: the chip row at 640px (docs/DESIGN.md 9), and
 * the redirect from the old URL.
 */
import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAsE2eAdmin } from "./constants";

const OBJECT_TYPE_KEY = "e2e_activity";
/** The fixture type's display name, as the Type picker lists it. */
const OBJECT_TYPE_NAME = "Activity widget";
const KEY_PREFIX = "ACT";

/** Two labels, so "only rows with that label" has something to exclude. */
const PICKED_AGENT = "e2e-activity-picked";
const OTHER_AGENT = "e2e-activity-other";

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  // Idempotent: `playwright.config.ts` retries a worker death once into the same data directory,
  // and a second POST would fail on the key-prefix uniqueness rule.
  const existing = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}`);
  if (!existing.ok()) {
    const created = await apiContext.post("/api/v1/object-types", {
      data: {
        key: OBJECT_TYPE_KEY,
        name: OBJECT_TYPE_NAME,
        name_plural: "Activity widgets",
        description: "Seeds two agents' writes for the agent-filter scenario.",
        key_prefix: KEY_PREFIX,
        fields: [
          {
            key: "note",
            name: "Note",
            type: "short_text",
            description: "What the agent wrote.",
            required: false,
          },
        ],
      },
    });
    expect(created.ok(), await created.text()).toBeTruthy();
  }

  // One record per agent, each written under its own label. The REST edge auto-registers a
  // label for a bearer credential on any request carrying `X-Agent-Label` (FR-I6, DD-17), and
  // the write is what puts it on an audit event — which is what the directory reads and what
  // the filter matches.
  for (const [label, note] of [
    [PICKED_AGENT, "written by the picked agent"],
    [OTHER_AGENT, "written by the other agent"],
  ]) {
    const written = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
      data: { note },
      headers: { ...E2E_AUTH_HEADER, "X-Agent-Label": label },
    });
    expect(written.ok(), await written.text()).toBeTruthy();
  }
});

test.afterAll(async () => {
  await apiContext.dispose();
});

test("/audit redirects to /activity", async ({ page }) => {
  await signInAsE2eAdmin(page);
  await page.goto("/audit");
  await expect(page).toHaveURL(/\/activity$/);
  await expect(page.getByRole("heading", { level: 1, name: "Activity" })).toBeVisible();
});

test("filtering by an agent chosen from the picker returns only rows with that label", async ({
  page,
}) => {
  await signInAsE2eAdmin(page);
  await page.goto("/activity");

  const chips = page.getByTestId("activity-filters");
  const feed = page.getByTestId("activity-feed");

  // Both agents are visible before the agent filter, which is what makes the exclusion below mean
  // something: a feed that never showed the other agent would pass with the filter ignored.
  await chips.getByRole("button", { name: "Add filter" }).click();
  await page.getByRole("menuitem", { name: "Record" }).click();
  await page.getByRole("textbox", { name: "Record" }).fill(`${KEY_PREFIX}-001`);
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(feed.getByText(PICKED_AGENT).first()).toBeVisible();
  await chips.getByRole("button", { name: "Remove Record filter" }).click();

  /**
   * **Scoped to this fixture's own type, and that is load-bearing rather than tidy.**
   *
   * The bare, unfiltered feed is the newest **50** events across the whole deployment
   * (`services/audit.py::DEFAULT_LIMIT`), so whether this fixture's rows appeared in it would
   * depend on how much every *other* spec in the suite had written. When nineteen files shared
   * this database with just enough room, one more spec's writes made this scenario fail roughly
   * one run in two, in a file nobody had touched, reporting an agent-picker bug that did not
   * exist.
   *
   * Restoring a margin would only hand the same failure to whoever writes the next spec. The
   * assertion's actual subject is "both agents are present here, and then only one is", and a
   * type-scoped feed says that just as strongly while depending on nothing but this file's own
   * fixtures. The agent filter below is still applied to the unscoped feed.
   */
  await chips.getByRole("button", { name: "Add filter" }).click();
  await page.getByRole("menuitem", { name: "Type" }).click();
  await page
    .getByRole("listbox", { name: "Type options" })
    .getByRole("option", { name: OBJECT_TYPE_NAME })
    .click();
  await expect(feed.getByText(OTHER_AGENT).first()).toBeVisible();
  await chips.getByRole("button", { name: "Remove Type filter" }).click();

  // Now the picker itself. The option is chosen by name; the id rides the wire.
  await chips.getByRole("button", { name: "Add filter" }).click();
  await page.getByRole("menuitem", { name: "Agent" }).click();
  const options = page.getByRole("listbox", { name: "Agent options" });
  await expect(options).toBeVisible();
  await options.getByRole("option", { name: new RegExp(PICKED_AGENT) }).click();

  await expect(chips.getByText(`Agent is ${PICKED_AGENT}`)).toBeVisible();
  await expect(feed.getByText(PICKED_AGENT).first()).toBeVisible();
  // The claim: only rows with that label. Read in one call rather than per element, because a
  // refetch mid-loop leaves later handles unresolvable (AGENTS.md, Traps).
  await expect(feed.getByText(OTHER_AGENT)).toHaveCount(0);
});

test("the agent picker offers no free-text identifier", async ({ page }) => {
  await signInAsE2eAdmin(page);
  await page.goto("/activity");
  await page.getByTestId("activity-filters").getByRole("button", { name: "Add filter" }).click();
  await page.getByRole("menuitem", { name: "Agent" }).click();

  const options = page.getByRole("listbox", { name: "Agent options" });
  await expect(options).toBeVisible();
  // The screen this replaces rendered `Agent label ID` as an input taking a UUID.
  await expect(options.getByRole("textbox")).toHaveCount(0);
  await expect(page.getByLabel(/agent label id/i)).toHaveCount(0);
});

test("the chip row stays reachable at 640px (docs/DESIGN.md 9)", async ({ page }) => {
  // A Playwright assertion because `getBoundingClientRect` returns zeroes in jsdom (AGENTS.md),
  // so nothing in the component suite claims to measure this.
  await signInAsE2eAdmin(page);
  await page.goto("/activity");

  const chips = page.getByTestId("activity-filters");
  await chips.getByRole("button", { name: "Add filter" }).click();
  await page.getByRole("menuitem", { name: "Field" }).click();
  await page.getByRole("textbox", { name: "Field" }).fill("note");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(chips.getByText("Field is note")).toBeVisible();

  await page.setViewportSize({ width: 640, height: 900 });
  // Section 9: nothing reachable at 1280 is unreachable at 800. Both chips stay on screen and
  // the page does not scroll sideways to reach them.
  await expect(chips.getByText("Field is note")).toBeVisible();
  await expect(chips.getByRole("button", { name: "Add filter" })).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
});
