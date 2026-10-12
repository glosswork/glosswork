/**
 * Seven findings about the records table and CSV import, one `describe` each, in the functional
 * `e2e` project: the real built frontend, a real `uvicorn` process and a real SQLite database.
 *
 * Every geometric claim here is a browser measurement, because none of it can be made in jsdom
 * (AGENTS.md, Traps). Every count is compared with what the API answers for the same filter in
 * the same run, never with a constant, so the assertions hold when the fixture changes.
 *
 * **Typed values are typed key by key.** `pressSequentially`, never `fill`: two of the seven
 * findings were inputs that lost what was typed between one key and the next, and a value set
 * whole never passes through the states that broke.
 *
 * **The fixture is this spec's own**, a type wider and taller than the viewport, and it is
 * seeded idempotently because `playwright.config.ts` retries a worker death once into the same
 * data directory. A new type in the functional database costs no baselines.
 *
 * The browser's timezone is pinned, so "the viewer's own calendar day" is the same day on every
 * machine that runs this, and it is deliberately not UTC: a day boundary that coincides with
 * UTC's cannot tell the viewer's day from the server's.
 */
import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
  type Locator,
  type Page,
  type Request,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAs, signInAsE2eAdmin } from "./constants";

test.use({ timezoneId: "America/New_York", viewport: { width: 1280, height: 800 } });

const TYPE_KEY = "e2e_pilot";
const TYPE_PLURAL = "Pilot findings";
const OTHER_TYPE_KEY = "e2e_pilot_other";
const OTHER_TYPE_PLURAL = "Pilot others";
const RECORD_COUNT = 60;
const FILLER_COUNT = 13;

/** The day the date-filter tests are about, and the viewer's zone offset on it (EST, UTC-5). */
const DAY = "2026-01-07";
const DAY_FIRST_SECOND = "2026-01-07T05:00:00Z";
const DAY_LAST_SECOND = "2026-01-08T04:59:59Z";

const fillerKey = (index: number) => `filler_${String(index + 1).padStart(2, "0")}`;
const fillerName = (index: number) => `Filler column ${String(index + 1).padStart(2, "0")}`;

let apiContext: APIRequestContext;

interface QueryAnswer {
  total_count: number;
  records: { key: string; data: Record<string, unknown> }[];
}

async function apiQuery(typeKey: string, filter: unknown): Promise<QueryAnswer> {
  const response = await apiContext.post(`/api/v1/object-types/${typeKey}/query`, {
    data: { filter, limit: 200, fields: "*" },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as QueryAnswer;
}

async function apiCount(typeKey: string, filter: unknown): Promise<number> {
  return (await apiQuery(typeKey, filter)).total_count;
}

async function seedType(): Promise<void> {
  const existing = await apiContext.get(`/api/v1/object-types/${TYPE_KEY}`);
  if (existing.ok()) return;

  const created = await apiContext.post("/api/v1/object-types", {
    data: {
      key: TYPE_KEY,
      name: "Pilot finding",
      name_plural: TYPE_PLURAL,
      description:
        "An end-to-end fixture type wider and taller than the viewport, for the table's "
        + "layout, filter and pinning scenarios.",
      key_prefix: "PLF",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "The finding's short display name.",
          required: true,
        },
        {
          key: "stage",
          name: "Stage",
          type: "single_select",
          description: "Whether the finding is being worked.",
          config: {
            options: [
              { value: "active", label: "Active", description: "Being worked." },
              { value: "paused", label: "Paused", description: "Set aside." },
            ],
          },
        },
        {
          key: "variant",
          name: "Variant",
          type: "short_text",
          description: "A free-text grouping label, one of three values in this fixture.",
        },
        {
          key: "last_used",
          name: "Last used",
          type: "datetime",
          description: "When the finding was last exercised.",
        },
        {
          key: "due",
          name: "Due",
          type: "date",
          description: "The calendar date the finding is due.",
        },
        ...Array.from({ length: FILLER_COUNT }, (_, index) => ({
          key: fillerKey(index),
          name: fillerName(index),
          type: "short_text",
          description: `Filler text column ${index + 1}, present to make the table wide.`,
        })),
      ],
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();

  // One record every six hours from 2026-01-01T00:00:00Z: fifteen days of them, so the day under
  // test has records before it, after it and (four of them) inside it for a viewer at UTC-5.
  const start = Date.UTC(2026, 0, 1, 0, 0, 0);
  for (let index = 0; index < RECORD_COUNT; index += 1) {
    const lastUsed = `${new Date(start + index * 6 * 3600 * 1000).toISOString().slice(0, 19)}Z`;
    const response = await apiContext.post(`/api/v1/object-types/${TYPE_KEY}/records`, {
      data: {
        name: `Finding ${String(index + 1).padStart(2, "0")}`,
        stage: index % 2 === 0 ? "active" : "paused",
        variant: `v${(index % 3) + 1}`,
        last_used: lastUsed,
        due: lastUsed.slice(0, 10),
        ...Object.fromEntries(
          Array.from({ length: FILLER_COUNT }, (_, filler) => [
            fillerKey(filler),
            `value ${index + 1}.${filler + 1}`,
          ]),
        ),
      },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
}

async function seedOtherType(): Promise<void> {
  const existing = await apiContext.get(`/api/v1/object-types/${OTHER_TYPE_KEY}`);
  if (existing.ok()) return;
  const created = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OTHER_TYPE_KEY,
      name: "Pilot other",
      name_plural: OTHER_TYPE_PLURAL,
      description: "A second end-to-end fixture type, to show table state is kept per type.",
      key_prefix: "PLO",
      fields: [
        {
          key: "name",
          name: "Name",
          type: "short_text",
          description: "The record's short display name.",
          required: true,
        },
        {
          key: "stage",
          name: "Stage",
          type: "single_select",
          description: "Whether the record is being worked.",
          config: {
            options: [
              { value: "active", label: "Active", description: "Being worked." },
              { value: "paused", label: "Paused", description: "Set aside." },
            ],
          },
        },
      ],
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  for (const name of ["Other one", "Other two"]) {
    const response = await apiContext.post(`/api/v1/object-types/${OTHER_TYPE_KEY}/records`, {
      data: { name, stage: "active" },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
  }
}

test.beforeAll(async () => {
  // Seeding sixty records is sixty requests; the default hook timeout is the test's.
  test.setTimeout(120_000);
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });
  await seedType();
  await seedOtherType();
});

test.afterAll(async () => {
  await apiContext.dispose();
});

const isQueryFor = (typeKey: string) => (request: Request) =>
  request.method() === "POST" && request.url().includes(`/api/v1/object-types/${typeKey}/query`);
const isQueryRequest = isQueryFor(TYPE_KEY);

/** The `filter` of one captured `/query` request body. */
function sentFilter(request: Request): unknown {
  return (JSON.parse(request.postData() ?? "{}") as { filter?: unknown }).filter ?? null;
}

async function openTable(page: Page, typeKey: string = TYPE_KEY): Promise<void> {
  await page.goto(`/${typeKey}`);
  await expect(page.getByTestId("records-table").locator("tbody tr").first()).toBeVisible();
}

async function signInAndOpenTable(page: Page): Promise<void> {
  await signInAsE2eAdmin(page);
  await openTable(page);
}

/** The footer's own sentence for a result of `count` rows that fits one page. */
async function expectRowCount(page: Page, count: number): Promise<void> {
  if (count === 0) {
    await expect(page.getByTestId("table-footer")).toHaveCount(0);
    return;
  }
  await expect(page.getByTestId("table-footer").getByRole("status")).toContainText(
    `Showing all ${count}.`,
  );
  await expect(page.locator('[data-testid^="row-PLF-"]')).toHaveCount(count);
}

/** Opens `+ Add filter`, chooses a field and an operator, and returns the popover. */
async function startChip(page: Page, fieldKey: string, op: string): Promise<Locator> {
  await page.getByRole("button", { name: "+ Add filter" }).click();
  const popover = page.getByTestId("condition-popover");
  await popover.getByLabel("Field").selectOption(fieldKey);
  await popover.getByLabel("Operator").selectOption(op);
  return popover;
}

/**
 * Opens the Advanced builder on an unfiltered table and adds one condition on `fieldKey` and
 * `op`, its value still empty. With no filter the builder's first condition is its root node.
 */
async function startAdvancedCondition(page: Page, fieldKey: string, op: string): Promise<Locator> {
  await page.getByRole("button", { name: "Advanced", exact: true }).click();
  await page.getByTestId("filter-builder").getByRole("button", { name: "+ Condition" }).click();
  const row = page.getByTestId("filter-node-root");
  await row.getByLabel("Field").selectOption(fieldKey);
  await row.getByLabel("Operator").selectOption(op);
  return row;
}

// --------------------------------------------------------------------------------------------
// #17: a multi-value text filter keeps what is typed
// --------------------------------------------------------------------------------------------

test.describe("#17 a multi-value text filter", () => {
  const expected = { field: "variant", op: "in", value: ["v1", "v2"] };

  test("typed key by key into a chip, two values reach the query as an array", async ({ page }) => {
    await signInAndOpenTable(page);
    const popover = await startChip(page, "variant", "in");
    const box = popover.getByLabel("Variant value");
    await box.pressSequentially("v1, v2");
    // What the person typed is what the box shows, comma and space included.
    await expect.soft(box).toHaveValue("v1, v2");

    const committed = page.waitForRequest(isQueryRequest, { timeout: 5_000 });
    await popover.getByRole("button", { name: "Apply" }).click();
    expect(sentFilter(await committed)).toEqual(expected);
    await expectRowCount(page, await apiCount(TYPE_KEY, expected));
  });

  test("typed key by key into the Advanced builder, two values reach the query as an array", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    const row = await startAdvancedCondition(page, "variant", "in");
    const box = row.getByLabel("Variant value");
    // The builder sends on every complete change, so the request that matters is the last one:
    // registered before the final key, it is the one carrying both values.
    await box.pressSequentially("v1, v");
    const last = page.waitForRequest(
      (request) =>
        isQueryRequest(request)
        && JSON.stringify(sentFilter(request)).includes('"v2"'),
      { timeout: 5_000 },
    );
    await box.pressSequentially("2");
    await expect.soft(box).toHaveValue("v1, v2");
    expect(sentFilter(await last)).toEqual(expected);
    await expectRowCount(page, await apiCount(TYPE_KEY, expected));
  });
});

// --------------------------------------------------------------------------------------------
// #15: a date filter either filters correctly or is refused, and never reads as applied when
// it is not
// --------------------------------------------------------------------------------------------

const REFUSED_VIEW_NAME = "Refused date";
const REFUSED_FILTER = { field: "last_used", op: "lt", value: "7 Jan 2026" };
const REFUSED_TYPE_KEY = "e2e_pilot_refused";
/** Part of the server's own sentence for a refused datetime literal (`filters.py`). */
const SERVER_REFUSAL = "compares against YYYY-MM-DDTHH:MM:SSZ";
const BOX_REFUSAL = "Not a date this filter understands. Use 2026-01-07 or @today-7d.";
const EXACT_TIME_HINT =
  '"is" and "is not" need an exact time here. For a whole day, use "is between" with the same '
  + "day twice.";

async function seedSavedView(
  typeKey: string,
  name: string,
  filter: unknown,
  order: string[],
  isDefault: boolean,
): Promise<void> {
  const listed = await apiContext.get(`/api/v1/object-types/${typeKey}/saved-views`);
  expect(listed.ok(), await listed.text()).toBeTruthy();
  if (((await listed.json()) as { name: string }[]).some((view) => view.name === name)) return;
  const created = await apiContext.post(`/api/v1/object-types/${typeKey}/saved-views`, {
    data: {
      name,
      mode: "table",
      is_default: isDefault,
      config: {
        filter,
        sort: [],
        groupBy: null,
        columns: { order, visibility: {}, sizing: {} },
        mode: "table",
      },
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
}

/**
 * A type whose **default** view holds a date the server refuses. Its own type, because a
 * default view applies itself on every load of the table and every other test on `e2e_pilot`
 * starts from an unfiltered one.
 */
async function seedRefusedDefaultType(): Promise<void> {
  const existing = await apiContext.get(`/api/v1/object-types/${REFUSED_TYPE_KEY}`);
  if (!existing.ok()) {
    const created = await apiContext.post("/api/v1/object-types", {
      data: {
        key: REFUSED_TYPE_KEY,
        name: "Pilot refused",
        name_plural: "Pilot refuseds",
        description: "An end-to-end fixture type whose default saved view holds a refused date.",
        key_prefix: "PLR",
        fields: [
          {
            key: "name",
            name: "Name",
            type: "short_text",
            description: "The record's short display name.",
            required: true,
          },
          {
            key: "last_used",
            name: "Last used",
            type: "datetime",
            description: "When the record was last exercised.",
          },
        ],
      },
    });
    expect(created.ok(), await created.text()).toBeTruthy();
    const record = await apiContext.post(`/api/v1/object-types/${REFUSED_TYPE_KEY}/records`, {
      data: { name: "Only one", last_used: "2026-01-05T12:00:00Z" },
    });
    expect(record.ok(), await record.text()).toBeTruthy();
  }
  await seedSavedView(REFUSED_TYPE_KEY, REFUSED_VIEW_NAME, REFUSED_FILTER, ["name", "last_used"], true);
}

/** Waits the whole window for a `/query` request and answers `null` when none was issued. */
function noQueryWithin(page: Page, ms: number): Promise<Request | null> {
  return page.waitForRequest(isQueryRequest, { timeout: ms }).catch(() => null);
}

/** Adds the `Stage is Active` chip the reported case starts from, and waits for its rows. */
async function addStageChip(page: Page): Promise<void> {
  const popover = await startChip(page, "stage", "eq");
  const committed = page.waitForRequest(isQueryRequest, { timeout: 5_000 });
  await popover.getByLabel("Stage value").selectOption("active");
  await committed;
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Stage is Active", exact: true })).toBeVisible();
}

const stageIsActive = { field: "stage", op: "eq", value: "active" };

test.describe("#15 a date filter", () => {
  test("a date typed the way the table displays it is held back, with the accepted formats named", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    await addStageChip(page);
    const before = await apiCount(TYPE_KEY, stageIsActive);
    await expectRowCount(page, before);

    const popover = await startChip(page, "last_used", "lt");
    const box = popover.getByLabel("Last used value", { exact: true });
    const sent = noQueryWithin(page, 2_500);
    await box.pressSequentially("7 Jan 2026");
    await expect.soft(box).toHaveValue("7 Jan 2026");
    await expect.soft(popover.getByRole("button", { name: "Apply" })).toBeDisabled();
    await expect.soft(popover.getByTestId("date-value-hint")).toHaveText(BOX_REFUSAL);
    // Enter is a commit point too; it must commit nothing either.
    await box.press("Enter");
    expect
      .soft((await sent)?.postData() ?? null, "a /query request went out for an unreadable date")
      .toBeNull();
    // The reported defect, stated as what the person sees: the rows are the select chip's
    // rows, not a wrong answer wearing a second chip.
    await expectRowCount(page, before);
    await expect(page.getByTestId("filter-chips").getByText("7 Jan 2026")).toHaveCount(0);
  });

  test("the reported case, typed as a day: a select chip plus 'is before' filters to the API's own count", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    await addStageChip(page);

    const popover = await startChip(page, "last_used", "lt");
    const box = popover.getByLabel("Last used value", { exact: true });
    await box.pressSequentially(DAY);
    await expect.soft(box).toHaveValue(DAY);
    await expect.soft(popover.getByTestId("date-value-hint")).toHaveText(
      "A date like 2026-01-07, or a relative date like @today-7d.",
    );
    const committed = page.waitForRequest(isQueryRequest, { timeout: 5_000 });
    await popover.getByRole("button", { name: "Apply" }).click();
    const filter = sentFilter(await committed);
    // The viewer's day, not UTC's: the first second of 7 January at UTC-5.
    expect(filter).toEqual({
      and: [stageIsActive, { field: "last_used", op: "lt", value: DAY_FIRST_SECOND }],
    });
    const expected = await apiCount(TYPE_KEY, filter);
    expect(expected).toBeGreaterThan(0);
    expect(expected).toBeLessThan(await apiCount(TYPE_KEY, stageIsActive));
    await expectRowCount(page, expected);
    // The chip reads the day the way the column does, not the instant that was sent.
    await expect(
      page.getByRole("button", { name: "Last used is before 7 Jan", exact: true }),
    ).toBeVisible();
  });

  test("a day picked from the calendar filters to the API's own count", async ({ page }) => {
    await signInAndOpenTable(page);
    const popover = await startChip(page, "last_used", "lt");
    await popover.getByLabel("Last used value: pick a day").fill(DAY);
    await expect(popover.getByLabel("Last used value", { exact: true })).toHaveValue(DAY);
    const committed = page.waitForRequest(isQueryRequest, { timeout: 5_000 });
    await popover.getByRole("button", { name: "Apply" }).click();
    const filter = sentFilter(await committed);
    expect(filter).toEqual({ field: "last_used", op: "lt", value: DAY_FIRST_SECOND });
    await expectRowCount(page, await apiCount(TYPE_KEY, filter));
  });

  test("'is on or before' a day includes a record stamped in the middle of it, and 'is after' does not", async ({
    page,
  }) => {
    // The fixture stamps a record at 12:00:00Z on the 7th: 07:00 that morning for the viewer.
    const midDay = "2026-01-07T12:00:00Z";
    const stamped = await apiQuery(TYPE_KEY, { field: "last_used", op: "eq", value: midDay });
    expect(stamped.total_count).toBe(1);
    const midDayKey = stamped.records[0].key;

    await signInAndOpenTable(page);
    let popover = await startChip(page, "last_used", "lte");
    await popover.getByLabel("Last used value", { exact: true }).pressSequentially(DAY);
    let committed = page.waitForRequest(isQueryRequest, { timeout: 5_000 });
    await popover.getByRole("button", { name: "Apply" }).click();
    let filter = sentFilter(await committed);
    expect(filter).toEqual({ field: "last_used", op: "lte", value: DAY_LAST_SECOND });
    await expectRowCount(page, await apiCount(TYPE_KEY, filter));
    await expect(page.getByTestId(`row-${midDayKey}`)).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Last used is on or before 7 Jan", exact: true }),
    ).toBeVisible();

    // Change the operator on the same chip: the instant is derived again from the same day.
    await page.getByRole("button", { name: "Last used is on or before 7 Jan", exact: true }).click();
    popover = page.getByTestId("condition-popover");
    await expect(popover.getByLabel("Last used value", { exact: true })).toHaveValue(DAY);
    await popover.getByLabel("Operator").selectOption("gt");
    await expect(popover.getByLabel("Last used value", { exact: true })).toHaveValue(DAY);
    committed = page.waitForRequest(isQueryRequest, { timeout: 5_000 });
    await popover.getByRole("button", { name: "Apply" }).click();
    filter = sentFilter(await committed);
    expect(filter).toEqual({ field: "last_used", op: "gt", value: DAY_LAST_SECOND });
    await expectRowCount(page, await apiCount(TYPE_KEY, filter));
    await expect(page.getByTestId(`row-${midDayKey}`)).toHaveCount(0);
  });

  test("'is' with a bare day is held back with a hint, and sends nothing", async ({ page }) => {
    await signInAndOpenTable(page);
    const popover = await startChip(page, "last_used", "eq");
    const sent = noQueryWithin(page, 2_500);
    await popover.getByLabel("Last used value", { exact: true }).pressSequentially(DAY);
    await expect.soft(popover.getByRole("button", { name: "Apply" })).toBeDisabled();
    await expect.soft(popover.getByTestId("date-value-hint")).toHaveText(EXACT_TIME_HINT);
    expect((await sent)?.postData() ?? null).toBeNull();
  });

  test("a relative token is sent as typed, and a mistyped one comes back onto its chip with the rows still there", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    const unfiltered = await apiCount(TYPE_KEY, null);
    await expectRowCount(page, unfiltered);

    let popover = await startChip(page, "last_used", "gte");
    await popover.getByLabel("Last used value", { exact: true }).pressSequentially("@today-7d");
    let committed = page.waitForRequest(isQueryRequest, { timeout: 5_000 });
    await popover.getByRole("button", { name: "Apply" }).click();
    expect(sentFilter(await committed)).toEqual({
      field: "last_used",
      op: "gte",
      value: "@today-7d",
    });
    // Every fixture record is from January 2026, long before the last seven days.
    await expect(page.getByTestId("table-empty-filtered")).toBeVisible();

    // Now the typo, on a fresh table so there are rows to keep.
    await page.getByRole("button", { name: /^Remove filter: Last used/ }).click();
    await expectRowCount(page, unfiltered);
    popover = await startChip(page, "last_used", "lt");
    await popover.getByLabel("Last used value", { exact: true }).pressSequentially("@tod");
    committed = page.waitForRequest(isQueryRequest, { timeout: 5_000 });
    await popover.getByRole("button", { name: "Apply" }).click();
    expect(sentFilter(await committed)).toEqual({ field: "last_used", op: "lt", value: "@tod" });

    const chip = page.getByTestId("filter-chip-0");
    await expect(chip.getByRole("button", { name: /Unknown date token '@tod'/ })).toBeVisible();
    // The rows the person had are still the rows on screen, and nothing at page level says
    // the load failed.
    await expectRowCount(page, unfiltered);
    await expect(page.getByText("Could not load records.")).toHaveCount(0);
  });

  test("the Advanced builder holds back the same date and sends the same instant", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    const unfiltered = await apiCount(TYPE_KEY, null);
    const row = await startAdvancedCondition(page, "last_used", "lt");
    const box = row.getByLabel("Last used value", { exact: true });

    const sent = noQueryWithin(page, 2_500);
    await box.pressSequentially("7 Jan 2026");
    await expect.soft(box).toHaveValue("7 Jan 2026");
    await expect.soft(row.getByTestId("date-value-hint")).toHaveText(BOX_REFUSAL);
    expect
      .soft((await sent)?.postData() ?? null, "the builder sent an unreadable date")
      .toBeNull();
    await expectRowCount(page, unfiltered);

    // Ten keys, one request: nine of them commit nothing.
    await box.fill("");
    const requests: Request[] = [];
    const record = (request: Request) => {
      if (isQueryRequest(request)) requests.push(request);
    };
    page.on("request", record);
    await box.pressSequentially(DAY);
    const expectedFilter = { field: "last_used", op: "lt", value: DAY_FIRST_SECOND };
    await expectRowCount(page, await apiCount(TYPE_KEY, expectedFilter));
    page.off("request", record);
    expect(requests.map(sentFilter)).toEqual([expectedFilter]);
  });

  test("a saved view holding an unreadable date shows the server's message on its chip and keeps the rows", async ({
    page,
  }) => {
    await seedSavedView(
      TYPE_KEY,
      REFUSED_VIEW_NAME,
      REFUSED_FILTER,
      ["name", "stage", "variant", "last_used", "due"],
      false,
    );
    await signInAndOpenTable(page);
    const unfiltered = await apiCount(TYPE_KEY, null);
    await expectRowCount(page, unfiltered);

    await page.getByRole("button", { name: "Current view", exact: true }).click();
    const refused = page.waitForResponse(
      (response) => isQueryRequest(response.request()) && response.status() === 422,
      { timeout: 5_000 },
    );
    await page.getByLabel("Saved view").selectOption({ label: REFUSED_VIEW_NAME });
    await refused;
    await page.keyboard.press("Escape");

    // On the chip: its accessible name carries the refusal, and its popover shows it.
    const chip = page.getByTestId("filter-chip-0");
    await expect(chip.getByRole("button", { name: new RegExp(SERVER_REFUSAL) })).toBeVisible();
    await expect.soft(page.getByText("Could not load records.")).toHaveCount(0);
    await expectRowCount(page, unfiltered);

    // And in the Advanced popover, although this filter is one a chip can express.
    await page.getByRole("button", { name: "Advanced", exact: true }).click();
    await expect(
      page.getByTestId("filter-chip-advanced").getByRole("alert"),
    ).toContainText(SERVER_REFUSAL);
    // The view is intact: the builder still holds the value as stored.
    await expect(
      page.getByTestId("filter-node-root").getByLabel("Last used value", { exact: true }),
    ).toHaveValue("7 Jan 2026");
  });

  test("the same view as everybody's default says the filter could not be applied, not that nothing matches", async ({
    page,
  }) => {
    await seedRefusedDefaultType();
    await signInAsE2eAdmin(page);
    const refused = page.waitForResponse(
      (response) =>
        isQueryFor(REFUSED_TYPE_KEY)(response.request()) && response.status() === 422,
      { timeout: 10_000 },
    );
    await page.goto(`/${REFUSED_TYPE_KEY}`);
    await refused;

    const notice = page.getByTestId("table-filter-refused");
    await expect.soft(notice).toContainText("This filter could not be applied.");
    await expect.soft(notice).toContainText(SERVER_REFUSAL);
    await expect.soft(notice.getByRole("button", { name: "Clear filter" })).toBeVisible();
    // The absence assertions come after the wait above, so they deny something that had every
    // chance to render.
    await expect.soft(page.getByText("No records match this filter")).toHaveCount(0);
    await expect.soft(page.getByText("Could not load records.")).toHaveCount(0);
    // The view opened and its chip is marked; nothing was dropped from it.
    await expect
      .soft(
        page.getByTestId("filter-chip-0").getByRole("button", { name: new RegExp(SERVER_REFUSAL) }),
      )
      .toBeVisible();

    // Export under the refused filter says so. Before, the click did nothing at all.
    await page.getByRole("button", { name: `Current view: ${REFUSED_VIEW_NAME}` }).click();
    const exportRefused = page.waitForResponse(
      (response) => response.url().includes(`/object-types/${REFUSED_TYPE_KEY}/export`),
      { timeout: 5_000 },
    );
    await page.getByRole("button", { name: "Export CSV" }).click();
    expect((await exportRefused).status()).toBe(422);
    const exportAlert = page.getByRole("alert").filter({ hasText: "Could not export." });
    await expect(exportAlert).toBeVisible();
    await expect(exportAlert).toContainText(SERVER_REFUSAL);

    // Clearing the filter is the way out, and it works.
    await page.keyboard.press("Escape");
    await notice.getByRole("button", { name: "Clear filter" }).click();
    await expect(page.getByTestId("row-PLR-001")).toBeVisible();
  });
});

// --------------------------------------------------------------------------------------------
// #13: a popover stays inside the viewport
// --------------------------------------------------------------------------------------------

/** `scrollWidth - clientWidth` for the page and for the shell's content area: 0 is "no sideways
 * scroll". Read in one call so the two numbers describe one layout. */
async function sidewaysOverflow(page: Page): Promise<{ page: number; main: number }> {
  return page.evaluate(() => {
    const root = document.documentElement;
    const main = document.querySelector("main");
    return {
      page: root.scrollWidth - root.clientWidth,
      main: main === null ? 0 : main.scrollWidth - main.clientWidth,
    };
  });
}

async function rightEdgePastViewport(page: Page, panel: Locator): Promise<number> {
  const box = await panel.boundingBox();
  expect(box, "the panel has no box").not.toBeNull();
  const viewport = await page.evaluate(() => document.documentElement.clientWidth);
  return Math.max(0, box!.x + box!.width - viewport);
}

test.describe("#13 a popover on a wide table", () => {
  test("the View menu opens inside the viewport and adds no sideways scroll", async ({ page }) => {
    await signInAndOpenTable(page);
    expect(await sidewaysOverflow(page)).toEqual({ page: 0, main: 0 });

    await page.getByRole("button", { name: "Current view", exact: true }).click();
    const panel = page.getByTestId("view-menu");
    await expect(panel).toBeVisible();
    expect.soft(await rightEdgePastViewport(page, panel), "pixels past the right edge").toBe(0);
    expect.soft(await sidewaysOverflow(page)).toEqual({ page: 0, main: 0 });
    // It has not been pushed off the other side either.
    expect((await panel.boundingBox())!.x).toBeGreaterThanOrEqual(0);
  });

  test("at 800 wide the View menu and the Columns popover open inside the viewport", async ({
    page,
  }) => {
    // Signed in at the wide width: the sign-in helper waits for the identity block, which the
    // narrow top bar keeps inside its closed menu.
    await signInAsE2eAdmin(page);
    await page.setViewportSize({ width: 800, height: 800 });
    await openTable(page);
    const before = await sidewaysOverflow(page);

    await page.getByRole("button", { name: "Current view", exact: true }).click();
    const menu = page.getByTestId("view-menu");
    await expect(menu).toBeVisible();
    expect.soft(await rightEdgePastViewport(page, menu), "View menu past the right edge").toBe(0);
    expect.soft(await sidewaysOverflow(page)).toEqual(before);
    expect.soft((await menu.boundingBox())!.x).toBeGreaterThanOrEqual(0);
    await page.keyboard.press("Escape");

    // A fence at this width: the Columns chip sits far enough left that its panel fits
    // without help, on the unfixed tree too. It is here so a shift is never applied to a panel
    // that did not need one.
    await page.getByTestId("columns-chip").getByRole("button").click();
    const panel = page.getByTestId("columns-chip").getByTestId("popover-panel");
    await expect(panel).toBeVisible();
    expect(await rightEdgePastViewport(page, panel), "Columns past the right edge").toBe(0);
    expect(await panel.evaluate((element) => (element as HTMLElement).style.left)).toBe("");
  });
});

// --------------------------------------------------------------------------------------------
// #16: the sidebar and the table toolbar stay in view on a tall table
// --------------------------------------------------------------------------------------------

/** Whether a locator's box lies wholly inside the viewport's height. */
async function insideViewportVertically(page: Page, locator: Locator): Promise<boolean> {
  const box = await locator.boundingBox();
  if (box === null) return false;
  const height = await page.evaluate(() => window.innerHeight);
  return box.y >= 0 && box.y + box.height <= height;
}

/** Whether `locator` (or something inside it) is the topmost element at its own centre. */
async function isTopmostAtCentre(locator: Locator): Promise<boolean> {
  return locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return top !== null && element.contains(top);
  });
}

const SHELL_ROUTES = [
  ["a table", `/${TYPE_KEY}`],
  ["a record", `/${TYPE_KEY}/PLF-001`],
  ["the import wizard", `/${TYPE_KEY}/import`],
  ["Inbox", "/inbox"],
  ["Schema", `/schema/${TYPE_KEY}`],
  ["Activity", "/activity"],
  ["People", "/people"],
  ["Setup", "/setup"],
  ["Search", "/search"],
] as const;

test.describe("#16 a table taller than the viewport", () => {
  test("after scrolling, the sidebar and the filter chips are still in view", async ({ page }) => {
    await signInAndOpenTable(page);
    await page.evaluate(() => window.scrollTo(0, 1500));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(1500);

    const firstLink = page.getByTestId("sidebar").getByRole("link").first();
    expect.soft(await insideViewportVertically(page, firstLink), "sidebar's first link in view").toBe(true);
    expect
      .soft(await insideViewportVertically(page, page.getByTestId("filter-chips")), "chip row in view")
      .toBe(true);
    expect
      .soft(await insideViewportVertically(page, page.getByTestId("table-toolbar")), "toolbar in view")
      .toBe(true);
    // The sign-out control at the foot of the sidebar is reachable too.
    expect
      .soft(await insideViewportVertically(page, page.getByTestId("current-principal")), "identity block in view")
      .toBe(true);

    // A popover opened from the stuck toolbar sits above the rows scrolling under it.
    await page.getByTestId("columns-chip").getByRole("button").click();
    const panel = page.getByTestId("columns-chip").getByTestId("popover-panel");
    await expect(panel).toBeVisible();
    expect(await isTopmostAtCentre(panel), "the Columns popover is topmost at its centre").toBe(true);
    // The window is still the scroller, and scrolling did not move the page sideways.
    expect((await sidewaysOverflow(page)).page).toBe(0);
  });

  test("at 800 wide the top bar scrolls away as it always has (a fence)", async ({ page }) => {
    // Passes on the unfixed tree by construction: below the `shell` breakpoint nothing changes.
    await signInAsE2eAdmin(page);
    await page.setViewportSize({ width: 800, height: 800 });
    await openTable(page);
    await page.evaluate(() => window.scrollTo(0, 1500));
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(1500);
    const bar = await page.getByTestId("shell-top-bar").boundingBox();
    expect(bar).not.toBeNull();
    expect(bar!.y + bar!.height).toBeLessThanOrEqual(0);
  });

  for (const width of [1280, 960]) {
    test(`at ${width} wide no shell route scrolls the page sideways`, async ({ page }) => {
      // Here because the content area stops absorbing sideways overflow: anything wider than
      // it would now scroll the page, and the sticky sidebar with it.
      await page.setViewportSize({ width, height: 800 });
      await signInAsE2eAdmin(page);
      for (const [name, route] of SHELL_ROUTES) {
        await page.goto(route);
        await expect(page.locator("main")).toBeVisible();
        await page.waitForLoadState("networkidle");
        expect.soft((await sidewaysOverflow(page)).page, `${name} (${route}) at ${width}`).toBe(0);
      }
    });
  }
});
