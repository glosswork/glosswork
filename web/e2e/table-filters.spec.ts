/**
 * The table's filter chips and value rendering, in the functional `e2e` project: against the real
 * built frontend, a real `uvicorn` process and a real SQLite database.
 *
 * **Values render as values.** Asserted with locators, never with a screenshot: the visual
 * tolerance is loose enough to hide a short text change (AGENTS.md, Traps), so a passing visual
 * suite is evidence about layout only. Rendered as stored, the cells would read `68000`,
 * `2026-09-13`, `2026-09-11T09:14:00Z`, a bare `<button>` with no pill, and `title` null.
 *
 * **Nothing goes out before a condition is complete, and a chip serialises to the AST the
 * builder sent.** A request for an incomplete condition comes back 422 and empties the table,
 * which is what a `+ Condition` tree builder that sent on every change did. Against
 * docs/DESIGN.md 7.4's chips this asserts the absence of that request, plus the request body's
 * `filter` against a literal, plus the other half: a saved view holding a tree no chip row can
 * express renders as one `Advanced filter` chip and rides the wire byte-for-byte as stored.
 *
 * **The "no alert" assertion's shape is the measurement.** Written as
 * `expect(page.getByRole("alert")).toHaveCount(0)` it PASSED against a tree that raised the
 * alert, because
 * `waitForRequest` resolves when a request is *issued* — before the 422 comes back and the alert
 * renders — so a web-first assertion that is already satisfied returns immediately. An absence
 * assertion has to wait for the thing it denies.
 *
 * **The fixture is this spec's own.** The `e2e` database holds nothing else carrying money, a
 * date, a datetime or a select option to assert on: `table-end-to-end.spec.ts`'s `e2e_widget` is
 * one `short_text` and one `single_select`, and the twelve-field `vis_wide` type is declared inside
 * `ui-visual.spec.ts`, which `playwright.config.ts` `testIgnore`s from this project and which
 * runs against a different server and a different database. A new type in the FUNCTIONAL
 * database costs no baselines, so AGENTS.md's "a new visual fixture type is never free" does not
 * apply here.
 *
 * Seeding is idempotent in `heading-outline.spec.ts`'s shape — GET first, return early if the
 * type exists — because `playwright.config.ts` retries a worker death once into the same data
 * directory, and a second POST would fail on the `key_prefix` uniqueness rule.
 */
import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAsE2eAdmin } from "./constants";

const OBJECT_TYPE_KEY = "e2e_filters";
/** Deterministic: the type's own `key_prefix` sequence, and this type has exactly one record. */
const RECORD_KEY = "FLT-001";

/** The five values the rendering test is about, each chosen so the stored form and the displayed
 * form differ. */
const AMOUNT = 68000;
const AMOUNT_DISPLAY = "68,000";

/** `2026-09-13`, stored date-only. **13 September 2026 is a Sunday**, so the rendering under
 * `docs/DESIGN.md` 5's date rule (`Sat 13 Sep` is that section's FORMAT example, weekday
 * included) is `Sun 13 Sep`. The weekday is arithmetic, not a choice: copying DESIGN.md's example
 * `Sat 13 Sep` literally against this value would assert the wrong day. The other trap:
 * `new Date("2026-09-13")` is UTC midnight,
 * so every reader west of Greenwich gets the 12th — which is why the value is written out here
 * from its parts rather than derived by parsing it. */
const DUE = "2026-09-13";
const DUE_DISPLAY = "Sun 13 Sep";

/**
 * A `datetime`, which the API requires in `YYYY-MM-DDTHH:MM:SSZ` — a value without the `Z` is
 * rejected. Anchored to the run's own local day at 09:14 so `formatTimestamp` (`ui/datetime.ts`)
 * has a deterministic answer, `Today 09:14`: that function's relative words are a
 * function of the viewer's clock and timezone, so a fixed calendar date in the fixture would
 * assert a different string on every day the suite runs.
 */
const TOUCHED_AT_LOCAL = new Date();
TOUCHED_AT_LOCAL.setHours(9, 14, 0, 0);
const TOUCHED_AT = `${TOUCHED_AT_LOCAL.toISOString().slice(0, 19)}Z`;
const TOUCHED_AT_DISPLAY = "Today 09:14";

const STAGE_VALUE = "negotiating";
const STAGE_DISPLAY = "Negotiating";

/** Longer than the 180px cell, so the cell truncates and the full value is only reachable on
 * hover: the half of `docs/DESIGN.md` 5's long-text rule that a truncating cell with no title
 * lacks (measured as `clientWidth 156, scrollWidth 426, title null`). */
const NOTES =
  "A long note that will be truncated in the table cell, several words long, and whose whole "
  + "text is reachable only from the cell's own title attribute.";

/**
 * A filter no chip row can express, in the exact shape `TableView.test.tsx` builds and asserts
 * as one AST literal: an AND of a condition, an OR of two, and a NOT of a third. It is seeded
 * into a **saved view** rather than built here because `SavedViewService` `_validate_config`
 * asserts only that a config is a dict, so a tree like this can already be sitting in one, and
 * it needs a defined rendering.
 */
const NESTED_VIEW_NAME = "Nested grammar";
const NESTED_FILTER = {
  and: [
    { field: "stage", op: "eq", value: STAGE_VALUE },
    {
      or: [
        { field: "name", op: "contains", value: "Meridian" },
        { field: "key", op: "eq", value: RECORD_KEY },
      ],
    },
    { not: { field: "stage", op: "eq", value: "won" } },
  ],
};

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  const existing = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}`);
  if (existing.ok()) return;

  const response = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "Filter prospect",
      name_plural: "Filter prospects",
      description:
        "An end-to-end fixture type carrying one value of every kind the table page "
        + "has to render as a value rather than as a stored string.",
      key_prefix: "FLT",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "The prospect's short display name.",
          required: true,
        },
        {
          key: "stage",
          name: "Stage",
          type: "single_select",
          description: "Where the prospect stands in the pipeline.",
          config: {
            options: [
              { value: "prospecting", label: "Prospecting", description: "First contact made." },
              { value: STAGE_VALUE, label: STAGE_DISPLAY, description: "Terms under discussion." },
              { value: "won", label: "Won", description: "Signed." },
            ],
          },
        },
        {
          key: "amount",
          name: "Amount",
          type: "integer",
          description: "The contract value, in whole units of the deal's currency.",
        },
        {
          key: "due",
          name: "Due",
          type: "date",
          description: "The date a decision is expected by.",
        },
        {
          key: "touched_at",
          name: "Touched at",
          type: "datetime",
          description: "When someone last worked this prospect.",
        },
        {
          key: "notes",
          name: "Notes",
          type: "long_text",
          description: "Free-form notes about the prospect, longer than one table cell.",
        },
      ],
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();

  const record = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
    data: {
      name: "Meridian expansion",
      stage: STAGE_VALUE,
      amount: AMOUNT,
      due: DUE,
      touched_at: TOUCHED_AT,
      notes: NOTES,
    },
  });
  expect(record.ok(), await record.text()).toBeTruthy();
  expect(((await record.json()) as { key: string }).key).toBe(RECORD_KEY);
});

test.afterAll(async () => {
  await apiContext.dispose();
});

/**
 * The saved view carrying `NESTED_FILTER`, created once. Idempotent in the same shape the type
 * seeding above uses — list first, return early — because `playwright.config.ts` retries a worker
 * death once into the same data directory, and a second view of the same name would leave the
 * `Saved view` select with two options that read identically.
 *
 * `is_default` stays false deliberately: a default view applies itself on mount, and the
 * rendering test in this file asserts the cells of an **unfiltered** table.
 */
async function seedNestedView(): Promise<void> {
  const listed = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}/saved-views`);
  expect(listed.ok(), await listed.text()).toBeTruthy();
  const views = (await listed.json()) as { name: string }[];
  if (views.some((view) => view.name === NESTED_VIEW_NAME)) return;

  const created = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/saved-views`, {
    data: {
      name: NESTED_VIEW_NAME,
      mode: "table",
      is_default: false,
      config: {
        filter: NESTED_FILTER,
        sort: [],
        groupBy: null,
        columns: {
          order: ["name", "stage", "amount", "due", "touched_at", "notes"],
          visibility: {},
          sizing: {},
        },
        mode: "table",
      },
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
}

/** The cell for one field on the fixture record. Addressed by the accessible name
 * `EditableCell` already gives it, rather than by column index: this type's field order is this
 * file's own, but the name is the contract the rest of the suite already uses. */
function cell(page: Page, fieldName: string) {
  return page.getByRole("button", { name: `Edit ${fieldName} for ${RECORD_KEY}` });
}

async function openTable(page: Page): Promise<void> {
  await signInAsE2eAdmin(page);
  await page.goto(`/${OBJECT_TYPE_KEY}`);
  await expect(page.getByTestId(`row-${RECORD_KEY}`)).toBeVisible();
}

test("the table renders values as values, not as stored strings", async ({ page }) => {
  await openTable(page);

  // Soft, every one of them: these are five independent facts about five cells, and a hard
  // failure on the first would leave the other four unmeasured — an assertion that only ever
  // executes after a failing one has never been measured (AGENTS.md, Traps).
  await expect.soft(cell(page, "Amount")).toHaveText(AMOUNT_DISPLAY);
  await expect.soft(cell(page, "Due")).toHaveText(DUE_DISPLAY);
  await expect.soft(cell(page, "Touched at")).toHaveText(TOUCHED_AT_DISPLAY);

  // A pill is an ELEMENT carrying the text, not a class on the cell: `docs/DESIGN.md` 7.3's
  // Pill, whose tone comes from the option key. `data-testid="pill"` is the primitive's contract;
  // the pill is asserted to exist and to carry its own text, because kind is never colour alone
  // (`docs/DESIGN.md` 10).
  const stagePill = cell(page, "Stage").getByTestId("pill");
  await expect.soft(stagePill).toHaveCount(1);
  await expect.soft(stagePill).toHaveText(STAGE_DISPLAY);

  // The other half of the long-text rule: the cell truncates, and the full value is on hover.
  await expect.soft(cell(page, "Notes")).toHaveAttribute("title", NOTES);
});

const isQueryRequest = (request: { method(): string; url(): string }) =>
  request.method() === "POST"
  && request.url().includes(`/api/v1/object-types/${OBJECT_TYPE_KEY}/query`);

/** The `filter` of one captured `/query` request body. */
function sentFilter(request: { postData(): string | null }): unknown {
  return (JSON.parse(request.postData() ?? "{}") as { filter?: unknown }).filter ?? null;
}

test("nothing goes out between choosing a field and committing the value, and the chip sends exactly that AST", async ({
  page,
}) => {
  await openTable(page);

  // Registered BEFORE the first click, so a request fired during the interactions cannot be
  // missed, and resolving to `null` after the window is what "no request" has to mean for an
  // absence assertion: it waits the whole window rather than sampling once. The window is a
  // fixed bound rather than a debounce to race: the popover commits on Apply/Enter/close and adds
  // no debounce anywhere, so there is nothing here to tune against.
  const firstQuery = page
    .waitForRequest(isQueryRequest, { timeout: 2_500 })
    .catch(() => null);

  await page.getByRole("button", { name: "+ Add filter" }).click();
  const popover = page.getByTestId("condition-popover");
  await popover.getByLabel("Field").selectOption("stage");

  const offending = await firstQuery;
  // Soft for the same reason the rendering test's are: the alert assertion below is a separate fact
  // about what the person sees, and it must be measured whether or not a request went out.
  expect
    .soft(offending?.postData() ?? null, "a /query request was sent for an incomplete condition")
    .toBeNull();

  // NOT `expect(getByRole("alert")).toHaveCount(0)` — see the header. This waits up to the same
  // window FOR an alert and reports its text when one arrives.
  const alertText = await page
    .getByRole("alert")
    .first()
    .textContent({ timeout: 2_500 })
    .catch(() => null);
  expect.soft(alertText, "an alert appeared before the condition had a value").toBeNull();

  // Now finish it. A `<select>` cannot be half-typed, so it commits on change: one
  // deterministic request, which is what lets the body be asserted against a literal.
  const committed = page.waitForRequest(isQueryRequest, { timeout: 5_000 });
  await popover.getByLabel("Stage value").selectOption(STAGE_VALUE);

  expect(sentFilter(await committed)).toEqual({ field: "stage", op: "eq", value: STAGE_VALUE });
  // The sentence, through the display vocabulary and the option's label — not `stage eq
  // negotiating`, which is what an operator `<select>` rendering the API's own name would say.
  await expect(
    page.getByRole("button", { name: `Stage is ${STAGE_DISPLAY}`, exact: true }),
  ).toBeVisible();
});

test("a saved view holding a nested tree renders as one Advanced filter chip and round-trips it", async ({
  page,
}) => {
  await seedNestedView();
  await openTable(page);

  const loaded = page.waitForRequest(
    (request) => isQueryRequest(request) && sentFilter(request) !== null,
    { timeout: 10_000 },
  );
  // The saved-view select is inside the View menu, so the menu is opened first.
  await page.getByRole("button", { name: /^Current view/ }).click();
  await page.getByLabel("Saved view").selectOption({ label: NESTED_VIEW_NAME });
  // Dismissed before the chips are read: the panel is anchored over the toolbar beneath it.
  await page.keyboard.press("Escape");

  // A filter that is not a flat AND is one chip, and it is the tree builder's own.
  await expect(page.getByTestId("filter-chip-advanced")).toHaveText(/Advanced filter/);
  await expect(page.getByTestId("filter-chip-0")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "+ Add filter" })).toHaveCount(0);

  // Byte-for-byte: the chips do not alter the filter AST on the wire, and a saved view is where
  // such a tree can already be sitting.
  expect(sentFilter(await loaded)).toEqual(NESTED_FILTER);

  // And the grammar is still reachable: the chip opens the builder, seeded with what was loaded.
  await page.getByRole("button", { name: "Advanced filter", exact: true }).click();
  await expect(page.getByTestId("filter-builder")).toBeVisible();
  await expect(page.getByTestId("filter-node-root").getByLabel("Group type").first()).toHaveValue(
    "and",
  );
});
