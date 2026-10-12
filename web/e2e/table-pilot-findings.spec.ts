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

/**
 * The first **record** row. Not `tbody tr`: while the first page is loading the table body
 * holds one skeleton row, and a test that took that for the table being ready scrolled a page
 * that was not yet tall enough to scroll.
 */
const firstDataRow = (page: Page) =>
  page.getByTestId("records-table").locator('tbody tr[data-testid^="row-"]').first();

/** Scrolls the window down and waits until it has actually moved that far. */
async function scrollWindowTo(page: Page, top: number): Promise<void> {
  await expect
    .poll(() =>
      page.evaluate((value) => {
        window.scrollTo(0, value);
        return window.scrollY;
      }, top),
    )
    .toBe(top);
}

async function openTable(page: Page, typeKey: string = TYPE_KEY): Promise<void> {
  await page.goto(`/${typeKey}`);
  await expect(firstDataRow(page)).toBeVisible();
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

/**
 * What a refused filter has to look like, read off the page the way a person reads it: the
 * visible sentence, no record rows at all, and no count. Never the accessible name, which is
 * what an earlier version of these scenarios read while the page showed every record under an
 * ordinary-looking chip.
 */
async function expectFilterRefused(page: Page, serverText: string | RegExp): Promise<void> {
  const notice = page.getByTestId("table-filter-refused");
  await expect(notice).toBeVisible();
  await expect(notice).toContainText("This filter could not be applied.");
  await expect(notice).toContainText(serverText);
  // No row from any earlier answer, and nothing that reads as a result.
  await expect(page.locator('tbody tr[data-testid^="row-"]')).toHaveCount(0);
  await expect(page.getByTestId("table-footer")).toHaveCount(0);
  await expect(page.getByText("No records match this filter")).toHaveCount(0);
  await expect(page.getByText("Could not load records.")).toHaveCount(0);
}

/** The computed colour the theme's `bad` token paints, as the browser reports a colour. */
async function badColour(page: Page): Promise<string> {
  return page.evaluate(() => {
    const probe = document.createElement("span");
    probe.style.color = "var(--color-bad)";
    document.body.appendChild(probe);
    const colour = getComputedStyle(probe).color;
    probe.remove();
    return colour;
  });
}

interface ChipPaint {
  border: string;
  text: string;
}

/** A chip's painted border and the painted colour of its sentence. */
async function chipPaint(chip: Locator): Promise<ChipPaint> {
  return chip.evaluate((frame) => ({
    border: getComputedStyle(frame).borderTopColor,
    text: getComputedStyle(frame.querySelector("button")!).color,
  }));
}

/** The refused chip is painted in the `bad` colour, border and sentence, and an accepted chip
 * beside it is not: the mark is a difference a person can see, not a class in a list. */
async function expectChipMarkedRefused(page: Page, refused: Locator, accepted?: Locator): Promise<void> {
  const bad = await badColour(page);
  expect(await chipPaint(refused), "the refused chip's painted colours").toEqual({
    border: bad,
    text: bad,
  });
  if (accepted !== undefined) {
    const paint = await chipPaint(accepted);
    expect(paint.border, "an accepted chip's border").not.toBe(bad);
    expect(paint.text, "an accepted chip's sentence").not.toBe(bad);
  }
}

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
    // The button draws an icon: ink on the page, not an empty square. The icon is this
    // product's own SVG, so it is measured as painted pixels inside the button's box, which
    // is the claim, and not as an element that merely exists.
    const icon = popover.getByTestId("date-value-picker-icon");
    await expect(icon.locator("svg")).toBeVisible();
    const picture = await icon.screenshot();
    const inked = await page.evaluate(async (base64) => {
      const image = new Image();
      image.src = `data:image/png;base64,${base64}`;
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = image.width;
      canvas.height = image.height;
      const context = canvas.getContext("2d")!;
      context.drawImage(image, 0, 0);
      // Only the middle of the button, clear of its own border.
      const inset = Math.round(image.width * 0.25);
      const { data } = context.getImageData(inset, inset, image.width - 2 * inset, image.height - 2 * inset);
      let dark = 0;
      for (let index = 0; index < data.length; index += 4) {
        if (data[index] + data[index + 1] + data[index + 2] < 600) dark += 1;
      }
      return dark;
    }, picture.toString("base64"));
    expect(inked, "pixels of icon drawn inside the calendar button").toBeGreaterThan(20);
    // The date input covers the whole button, so a click anywhere on it reaches the picker.
    const iconBox = (await icon.boundingBox())!;
    const inputBox = (await popover.getByLabel("Last used value: pick a day").boundingBox())!;
    expect(Math.round(inputBox.width)).toBe(Math.round(iconBox.width));
    expect(Math.round(inputBox.height)).toBe(Math.round(iconBox.height));
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

  test("a relative token is sent as typed, and a mistyped one empties the table and says why", async ({
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

    // The server refused it. The table shows that, and none of the rows it had before: rows
    // under a chip that was not applied are a wrong answer presented as right.
    await expectFilterRefused(page, "Unknown date token '@tod'");
    await expect(page.getByTestId("filter-chips").getByText("Last used is before @tod")).toBeVisible();
    await expectChipMarkedRefused(page, page.getByTestId("filter-chip-0"));

    // The tab remembers the filter, so a reload restores it, and it is refused again: still no
    // rows, and no unfiltered request on the way in.
    const requests: Request[] = [];
    page.on("request", (request) => {
      if (isQueryRequest(request)) requests.push(request);
    });
    await page.reload();
    await expectFilterRefused(page, "Unknown date token '@tod'");
    expect(requests.map(sentFilter)).toEqual(
      requests.map(() => ({ field: "last_used", op: "lt", value: "@tod" })),
    );

    // Removing the chip is the way out.
    await page.getByRole("button", { name: /^Remove filter: Last used/ }).click();
    await expectRowCount(page, unfiltered);
  });

  test("a mistyped token in the Advanced builder empties the table and says why", async ({ page }) => {
    await signInAndOpenTable(page);
    const unfiltered = await apiCount(TYPE_KEY, null);
    await expectRowCount(page, unfiltered);

    const row = await startAdvancedCondition(page, "last_used", "lt");
    // The builder sends a typed value once typing pauses, so this is one refused request, for
    // the token as typed.
    await row.getByLabel("Last used value", { exact: true }).pressSequentially("@tod");

    await expectFilterRefused(page, "Unknown date token '@tod'");
    await expect(page.getByTestId("filter-chip-advanced").getByRole("alert")).toContainText(
      "Unknown date token '@tod'",
    );
    await page.keyboard.press("Escape");
    await expectChipMarkedRefused(page, page.getByTestId("filter-chip-0"));
    await page.getByRole("button", { name: /^Remove filter: Last used/ }).click();
    await expectRowCount(page, unfiltered);
  });

  /**
   * Every `/query` request and its status for as long as the returned `stop` is not called, and
   * whether the refusal notice was ever in the page in that time. The notice is watched from
   * inside the page by a `MutationObserver`, so a blink between two samples cannot be missed.
   */
  async function watchQueries(page: Page): Promise<{
    filters: () => unknown[];
    statuses: () => number[];
    sawRefusal: () => Promise<boolean>;
  }> {
    const filters: unknown[] = [];
    const statuses: number[] = [];
    page.on("request", (request) => {
      if (isQueryRequest(request)) filters.push(sentFilter(request));
    });
    page.on("response", (response) => {
      if (isQueryRequest(response.request())) statuses.push(response.status());
    });
    await page.evaluate(() => {
      const flagged = window as unknown as { __sawRefusal: boolean };
      flagged.__sawRefusal = false;
      const look = () => {
        if (document.querySelector('[data-testid="table-filter-refused"]')) {
          flagged.__sawRefusal = true;
        }
      };
      new MutationObserver(look).observe(document.body, { childList: true, subtree: true });
      look();
    });
    return {
      filters: () => filters,
      statuses: () => statuses,
      sawRefusal: () =>
        page.evaluate(() => (window as unknown as { __sawRefusal: boolean }).__sawRefusal),
    };
  }

  const tokenFilter = { field: "last_used", op: "gte", value: "@today-7d" };

  test("a relative date typed key by key into the Advanced builder is sent once, whole, and never shows a refusal", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    const row = await startAdvancedCondition(page, "last_used", "gte");
    const watch = await watchQueries(page);

    // A person's pace: slower than a script, faster than the builder's pause. Seven of this
    // token's nine prefixes are tokens the server refuses (`@`, `@t`, ... `@today-`, `@today-7`),
    // and each used to be a request, a 422 and the table replaced by the refusal.
    const box = row.getByLabel("Last used value", { exact: true });
    await box.pressSequentially("@today-7d", { delay: 80 });
    // What is typed shows at once; only the sending waits.
    await expect(box).toHaveValue("@today-7d");

    // Every fixture record is from January 2026, so nothing is on or after seven days ago.
    const expected = await apiCount(TYPE_KEY, tokenFilter);
    expect(expected).toBe(0);
    await expect(page.getByTestId("table-empty-filtered")).toBeVisible();
    await expect(page.getByTestId("filter-chips").getByText("Last used is on or after @today-7d")).toBeVisible();

    // Soft, all three: they are three facts, and each has to be measured whether or not the
    // one before it holds.
    expect.soft(watch.filters(), "the filters sent while typing").toEqual([tokenFilter]);
    expect.soft(watch.statuses(), "their answers").toEqual([200]);
    expect.soft(await watch.sawRefusal(), "the refusal notice appeared while typing").toBe(false);
  });

  test("a value typed into the Advanced builder and committed at once is sent whole, by Enter, by leaving the box and by closing", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    const unfiltered = await apiCount(TYPE_KEY, null);
    const filtered = await apiCount(TYPE_KEY, { field: "variant", op: "eq", value: "v2" });
    expect(filtered).toBeGreaterThan(0);
    expect(filtered).toBeLessThan(unfiltered);

    const commits: [string, (box: Locator) => Promise<void>][] = [
      ["Enter", (box) => box.press("Enter")],
      ["leaving the box", (box) => box.press("Tab")],
      ["closing the builder", () => page.keyboard.press("Escape")],
    ];
    for (const [name, commitNow] of commits) {
      const row = await startAdvancedCondition(page, "variant", "eq");
      const box = row.getByLabel("Variant value", { exact: true });
      const watch = await watchQueries(page);
      // No pause at all between the last key and the commit: nothing has been sent yet.
      await box.pressSequentially("v2");
      const sent = page.waitForRequest(isQueryRequest, { timeout: 5_000 });
      await commitNow(box);
      expect(sentFilter(await sent), name).toEqual({ field: "variant", op: "eq", value: "v2" });
      await expectRowCount(page, filtered);
      // Sent once, whole: not `v` and then `v2`, and not a second time when the pause ends.
      await page.waitForTimeout(600);
      expect(watch.filters(), name).toEqual([{ field: "variant", op: "eq", value: "v2" }]);

      // What was typed is still what the builder holds.
      if (name !== "closing the builder") await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "Advanced", exact: true }).click();
      await expect(
        page.getByTestId("filter-node-root").getByLabel("Variant value", { exact: true }),
      ).toHaveValue("v2");
      await page.getByTestId("filter-builder").getByRole("button", { name: "Clear filter" }).click();
      await page.keyboard.press("Escape");
      await expectRowCount(page, unfiltered);
    }
  });

  test("clicking the Advanced chip right after typing closes the builder on that click, with the value sent whole and once", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    const unfiltered = await apiCount(TYPE_KEY, null);
    const typed = { field: "variant", op: "eq", value: "v2" };
    const filtered = await apiCount(TYPE_KEY, typed);

    // At once, and again part-way through the builder's pause. In both the typed value has not
    // been sent yet when the pointer goes down: leaving the box sends it, a chip for it appears
    // to the left, and the Advanced chip moves out from under the pointer before it comes up.
    for (const waitMs of [0, 150]) {
      const row = await startAdvancedCondition(page, "variant", "eq");
      const watch = await watchQueries(page);
      await row.getByLabel("Variant value", { exact: true }).pressSequentially("v2");
      if (waitMs > 0) await page.waitForTimeout(waitMs);

      // One click, as a person makes it: down and up at the same point, a moment apart.
      await page.getByTestId("filter-chip-advanced").getByRole("button").first().click({ delay: 60 });

      await expect(page.getByTestId("filter-builder"), `closed after one click (${waitMs} ms)`).toHaveCount(0);
      await expectRowCount(page, filtered);
      await page.waitForTimeout(600);
      expect(watch.filters(), `sent whole and once (${waitMs} ms)`).toEqual([typed]);
      // The click closed this popover and opened no other.
      await expect(page.getByTestId("popover-panel")).toHaveCount(0);
      await expect(page.getByTestId("condition-popover")).toHaveCount(0);

      // Nothing lost or reverted: the builder still holds what was typed.
      await page.getByRole("button", { name: "Advanced", exact: true }).click();
      await expect(
        page.getByTestId("filter-node-root").getByLabel("Variant value", { exact: true }),
      ).toHaveValue("v2");
      await page.getByTestId("filter-builder").getByRole("button", { name: "Clear filter" }).click();
      await page.keyboard.press("Escape");
      await expectRowCount(page, unfiltered);
    }
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

  test("a saved view holding an unreadable date, picked after the table has loaded, shows no rows and says why", async ({
    page,
  }) => {
    // The view the reported case would leave behind: a select chip plus the unreadable date.
    await seedSavedView(
      TYPE_KEY,
      REFUSED_VIEW_NAME,
      { and: [stageIsActive, REFUSED_FILTER] },
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

    // What the person sees: no rows from before, the server's sentence, and the refused chip
    // painted differently from the accepted one beside it.
    await expectFilterRefused(page, SERVER_REFUSAL);
    await expect(page.getByTestId("filter-chips").getByText("Stage is Active")).toBeVisible();
    await expectChipMarkedRefused(
      page,
      page.getByTestId("filter-chip-1"),
      page.getByTestId("filter-chip-0"),
    );

    // Export from here exports nothing: not the refused view, and not the rows from before.
    let downloads = 0;
    page.on("download", () => {
      downloads += 1;
    });
    await page.getByRole("button", { name: `Current view: ${REFUSED_VIEW_NAME}` }).click();
    const exportRefused = page.waitForResponse(
      (response) => response.url().includes(`/object-types/${TYPE_KEY}/export`),
      { timeout: 5_000 },
    );
    await page.getByRole("button", { name: "Export CSV" }).click();
    expect((await exportRefused).status()).toBe(422);
    await expect(page.getByRole("alert").filter({ hasText: "Could not export." })).toBeVisible();
    await page.keyboard.press("Escape");
    expect(downloads).toBe(0);

    // And in the Advanced popover, although this filter is one a chip row can express. The
    // view is intact: the builder still holds the value as stored.
    await page.getByRole("button", { name: "Advanced", exact: true }).click();
    const advanced = page.getByTestId("filter-chip-advanced");
    await expect(advanced.getByRole("alert")).toContainText(SERVER_REFUSAL);
    await expect(
      advanced.getByLabel("Last used value", { exact: true }),
    ).toHaveValue("7 Jan 2026");
    await page.keyboard.press("Escape");

    // Leaving the view brings the records back.
    await page.getByRole("button", { name: `Current view: ${REFUSED_VIEW_NAME}` }).click();
    await page.getByLabel("Saved view").selectOption({ index: 0 });
    await page.keyboard.press("Escape");
    await expectRowCount(page, unfiltered);
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
    // The view opened and its chip is marked, in paint; nothing was dropped from it.
    await expect(page.getByTestId("filter-chips").getByText("Last used is before 7 Jan 2026")).toBeVisible();
    await expectChipMarkedRefused(page, page.getByTestId("filter-chip-0"));

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
    await scrollWindowTo(page, 1500);

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
    await scrollWindowTo(page, 1500);
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

// --------------------------------------------------------------------------------------------
// #14: unsaved table state survives leaving the page
// --------------------------------------------------------------------------------------------

const SESSION_PREFIX = "gw.table-view.";
const PAUSED_VIEW_NAME = "Paused only";
const stageIsPaused = { field: "stage", op: "eq", value: "paused" };
const SECOND_EMAIL = "e2e-pilot-second@example.com";
const SECOND_PASSWORD = "e2e-pilot-second-passw0rd!";

async function sessionKeys(page: Page): Promise<string[]> {
  return page.evaluate((prefix) => {
    const keys: string[] = [];
    for (let index = 0; index < window.sessionStorage.length; index += 1) {
      const key = window.sessionStorage.key(index);
      if (key !== null && key.startsWith(prefix)) keys.push(key);
    }
    return keys.sort();
  }, SESSION_PREFIX);
}

async function seedSecondAccount(): Promise<void> {
  const created = await apiContext.post("/api/v1/principals", {
    data: {
      type: "user",
      display_name: "Pilot Second",
      email: SECOND_EMAIL,
      role: "member",
      password: SECOND_PASSWORD,
    },
  });
  // A retry into the same data directory finds the account already there.
  expect(created.ok() || created.status() === 409 || created.status() === 422, await created.text())
    .toBeTruthy();
}

/** Leaves a table by the sidebar and comes back by the sidebar: the reported journey. The link
 * back is a bare path, which is why state kept only in the URL would not survive it. */
async function leaveAndReturn(page: Page, typeKey: string): Promise<void> {
  const sidebar = page.getByTestId("sidebar");
  await sidebar.getByRole("link", { name: "Activity", exact: true }).click();
  await expect(page).toHaveURL(/\/activity$/);
  await sidebar.locator(`a[href="/${typeKey}"]`).click();
  await expect(page).toHaveURL(new RegExp(`/${typeKey}$`));
  await expect(firstDataRow(page)).toBeVisible();
}

async function addOtherTypeChip(page: Page): Promise<void> {
  await page.getByRole("button", { name: "+ Add filter" }).click();
  const popover = page.getByTestId("condition-popover");
  await popover.getByLabel("Field").selectOption("stage");
  const committed = page.waitForRequest(isQueryFor(OTHER_TYPE_KEY), { timeout: 5_000 });
  await popover.getByLabel("Stage value").selectOption("active");
  await committed;
  await page.keyboard.press("Escape");
}

test.describe("#14 unsaved table state", () => {
  test("a filter is still applied after leaving by the sidebar and coming back, per object type", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    await addStageChip(page);
    const filtered = await apiCount(TYPE_KEY, stageIsActive);
    await expectRowCount(page, filtered);

    await leaveAndReturn(page, TYPE_KEY);
    await expect
      .soft(page.getByRole("button", { name: "Stage is Active", exact: true }))
      .toBeVisible();
    await expectRowCount(page, filtered);

    // A reload keeps it too: it is the tab's state, not the page's.
    await page.reload();
    await expect(page.getByRole("button", { name: "Stage is Active", exact: true })).toBeVisible();
    await expectRowCount(page, filtered);

    // A second type's table is untouched by the first one's filter.
    await page.getByTestId("sidebar").locator(`a[href="/${OTHER_TYPE_KEY}"]`).click();
    await expect(page.getByTestId("row-PLO-001")).toBeVisible();
    await expect(page.getByTestId("filter-chip-0")).toHaveCount(0);
  });

  test("the restored filter is the first query: no unfiltered request goes out before it", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    await addStageChip(page);
    await page.getByTestId("sidebar").getByRole("link", { name: "Activity", exact: true }).click();
    await expect(page).toHaveURL(/\/activity$/);

    const requests: Request[] = [];
    page.on("request", (request) => {
      if (isQueryRequest(request)) requests.push(request);
    });
    await page.getByTestId("sidebar").locator(`a[href="/${TYPE_KEY}"]`).click();
    await expectRowCount(page, await apiCount(TYPE_KEY, stageIsActive));
    expect(requests.length).toBeGreaterThan(0);
    expect(requests.map(sentFilter)).toEqual(requests.map(() => stageIsActive));
  });

  test("choosing a saved view replaces the stored state", async ({ page }) => {
    await seedSavedView(
      TYPE_KEY,
      PAUSED_VIEW_NAME,
      stageIsPaused,
      ["name", "stage", "variant", "last_used", "due"],
      false,
    );
    await signInAndOpenTable(page);
    await addStageChip(page);

    await page.getByRole("button", { name: "Current view", exact: true }).click();
    const loaded = page.waitForRequest(isQueryRequest, { timeout: 5_000 });
    await page.getByLabel("Saved view").selectOption({ label: PAUSED_VIEW_NAME });
    expect(sentFilter(await loaded)).toEqual(stageIsPaused);
    await page.keyboard.press("Escape");
    const paused = await apiCount(TYPE_KEY, stageIsPaused);
    await expectRowCount(page, paused);

    await leaveAndReturn(page, TYPE_KEY);
    await expect
      .soft(page.getByRole("button", { name: `Current view: ${PAUSED_VIEW_NAME}` }))
      .toBeVisible();
    await expect.soft(page.getByRole("button", { name: "Stage is Paused", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Stage is Active", exact: true })).toHaveCount(0);
    await expectRowCount(page, paused);
  });

  test("after signing out and in again the table is unfiltered and the tab holds no table state", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    await addStageChip(page);
    // "Contains", not "equals": signing in lands on whichever table is first in the sidebar,
    // and that table keeps its state too. Which one it is depends on what else the run seeded.
    expect
      .soft(await sessionKeys(page), "state written while signed in")
      .toContain(`${SESSION_PREFIX}${TYPE_KEY}`);

    await page.getByTestId("current-principal").getByRole("button", { name: "Sign out" }).click();
    await expect(page.getByTestId("login-email")).toBeVisible();
    expect(await sessionKeys(page), "state left behind by sign-out").toEqual([]);

    await signInAndOpenTable(page);
    await expect(page.getByTestId("filter-chip-0")).toHaveCount(0);
    await expectRowCount(page, await apiCount(TYPE_KEY, null));
  });

  test("a session that ends without signing out leaves nothing for the next person who signs in", async ({
    page,
    context,
  }) => {
    await seedSecondAccount();
    await signInAndOpenTable(page);
    await addStageChip(page);
    await openTable(page, OTHER_TYPE_KEY);
    await addOtherTypeChip(page);
    // Two entries, both the first account's. This is what makes the absence below mean
    // something: without it, "no key is left" is true of a product that never stored one.
    // "Contains", for the reason given in the sign-out test above.
    expect(await sessionKeys(page), "state written by the first account").toEqual(
      expect.arrayContaining([
        `${SESSION_PREFIX}${TYPE_KEY}`,
        `${SESSION_PREFIX}${OTHER_TYPE_KEY}`,
      ]),
    );

    // The session ends without the sign-out route ever running: it expired, or it was ended
    // from another tab. The tab, and its storage, are still here.
    await context.clearCookies();
    await signInAs(page, SECOND_EMAIL, SECOND_PASSWORD);
    // The second account has opened no table at all, so nothing was removed by a read.
    expect(await sessionKeys(page), "the first account's state after the second signed in").toEqual(
      [],
    );
  });
});

// --------------------------------------------------------------------------------------------
// #18: columns can be pinned
// --------------------------------------------------------------------------------------------

const table = (page: Page) => page.getByTestId("records-table");
/** The table's own sideways scroller. */
const tableWrap = (page: Page) => table(page).locator("xpath=..");
/** A column's header cell, found from its resize handle, which every field column has. */
const headerCell = (page: Page, fieldKey: string) =>
  page.getByTestId(`resize-handle-${fieldKey}`).locator("xpath=..");
const bodyCell = (page: Page, fieldName: string, recordKey: string) =>
  page
    .getByRole("button", { name: `Edit ${fieldName} for ${recordKey}`, exact: true })
    .locator("xpath=ancestor::td[1]");

/** The key of the first row the table is showing: a row that is certainly inside the viewport,
 * which a geometric assertion needs and a fixed key does not promise. */
async function firstRowKey(page: Page): Promise<string> {
  const testId = await firstDataRow(page).getAttribute("data-testid");
  expect(testId).toMatch(/^row-PLF-/);
  return testId!.slice("row-".length);
}

async function leftOf(locator: Locator): Promise<number> {
  const box = await locator.boundingBox();
  expect(box, "the cell has no box").not.toBeNull();
  return Math.round(box!.x);
}

async function scrollTableSideways(page: Page, left: number): Promise<void> {
  await tableWrap(page).evaluate((element, value) => {
    element.scrollLeft = value;
  }, left);
  await expect.poll(() => tableWrap(page).evaluate((element) => element.scrollLeft)).toBe(left);
}

async function openColumns(page: Page): Promise<Locator> {
  await page.getByTestId("columns-chip").getByRole("button").first().click();
  const picker = page.getByTestId("column-picker");
  await expect(picker).toBeVisible();
  return picker;
}

async function headerOrder(page: Page): Promise<string[]> {
  return (await table(page).locator("thead th").allTextContents()).map((text) =>
    text.replace(/[▲▼]\d*/g, "").trim(),
  );
}

test.describe("#18 pinned columns", () => {
  test("the display field stays put while the table scrolls sideways, and a far column moves", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    const rowKey = await firstRowKey(page);
    const wrapLeft = await leftOf(tableWrap(page));
    const farBefore = await leftOf(headerCell(page, fillerKey(4)));

    await scrollTableSideways(page, 800);

    // One pixel inside the wrapper: its own left border.
    expect.soft(await leftOf(headerCell(page, "name")), "Name header").toBe(wrapLeft + 1);
    expect.soft(await leftOf(bodyCell(page, "Name", rowKey)), "Name cell").toBe(wrapLeft + 1);
    expect.soft(await leftOf(headerCell(page, fillerKey(4))), "a far column").toBe(farBefore - 800);
    // The stuck cell is what is painted there, not a cell scrolling under it.
    expect
      .soft(await isTopmostAtCentre(bodyCell(page, "Name", rowKey)), "Name cell is topmost")
      .toBe(true);
    // Only pinned fields stay: the Key column has scrolled away with the rest.
    const keyLink = page.getByTestId(`row-${rowKey}`).getByRole("link", { name: rowKey });
    expect((await keyLink.boundingBox())!.x).toBeLessThan(wrapLeft);
  });

  test("a second field can be pinned beside the first and unpinned again", async ({ page }) => {
    await signInAndOpenTable(page);
    const wrapLeft = await leftOf(tableWrap(page));
    let picker = await openColumns(page);
    await expect(picker.getByRole("button", { name: "Unpin Name", exact: true })).toBeVisible();
    await picker.getByRole("button", { name: "Pin Stage", exact: true }).click();
    await expect(picker.getByRole("button", { name: "Unpin Stage", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");

    await scrollTableSideways(page, 800);
    const nameBox = (await headerCell(page, "name").boundingBox())!;
    expect(Math.round(nameBox.x)).toBe(wrapLeft + 1);
    expect(await leftOf(headerCell(page, "stage")), "Stage header beside Name").toBe(
      Math.round(nameBox.x + nameBox.width),
    );
    expect(
      await leftOf(bodyCell(page, "Stage", await firstRowKey(page))),
      "Stage cell beside Name",
    ).toBe(Math.round(nameBox.x + nameBox.width));

    picker = await openColumns(page);
    await picker.getByRole("button", { name: "Unpin Stage", exact: true }).click();
    await page.keyboard.press("Escape");
    await scrollTableSideways(page, 0);
    const stageAtRest = await leftOf(headerCell(page, "stage"));
    await scrollTableSideways(page, 800);
    expect(await leftOf(headerCell(page, "stage")), "Stage released").toBe(stageAtRest - 800);
    expect(await leftOf(headerCell(page, "name")), "Name still pinned").toBe(wrapLeft + 1);
  });

  test("inline edit, resize and reorder work on a pinned column and on an unpinned one", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    const recordKey = await firstRowKey(page);

    // Inline edit: the pinned display field, then an unpinned field, on the last record.
    for (const [fieldName, value] of [
      ["Name", "A finding, edited in place"],
      ["Variant", "v-edited"],
    ] as const) {
      const cell = page.getByRole("button", { name: `Edit ${fieldName} for ${recordKey}`, exact: true });
      await cell.click();
      const input = page.getByTestId(`row-${recordKey}`).getByRole("textbox");
      await input.fill(value);
      await input.press("Enter");
      await expect(cell).toHaveText(value);
    }

    // Resize: drag each handle 40px to the right.
    for (const fieldKey of ["name", "variant"]) {
      const before = (await headerCell(page, fieldKey).boundingBox())!.width;
      const handle = (await page.getByTestId(`resize-handle-${fieldKey}`).boundingBox())!;
      await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
      await page.mouse.down();
      await page.mouse.move(handle.x + handle.width / 2 + 40, handle.y + handle.height / 2, {
        steps: 4,
      });
      await page.mouse.up();
      await expect
        .poll(
          async () => Math.round((await headerCell(page, fieldKey).boundingBox())!.width - before),
          { message: `${fieldKey} grew by the drag` },
        )
        .toBe(40);
    }

    // Reorder: move an unpinned column up, then the pinned one down. Pinning reorders nothing
    // by itself, and a pinned column that is moved stays pinned.
    expect((await headerOrder(page)).slice(3, 6)).toEqual(["Name", "Stage", "Variant"]);
    const picker = await openColumns(page);
    await picker.getByRole("button", { name: "Move Variant up", exact: true }).click();
    await picker.getByRole("button", { name: "Move Name down", exact: true }).click();
    await expect(picker.getByRole("button", { name: "Unpin Name", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
    expect((await headerOrder(page)).slice(3, 6)).toEqual(["Variant", "Name", "Stage"]);
    const wrapLeft = await leftOf(tableWrap(page));
    await scrollTableSideways(page, 800);
    expect(await leftOf(headerCell(page, "name")), "Name pinned after the move").toBe(wrapLeft + 1);
  });

  test("pins are saved with a view, and a view saved before pinning existed pins the display field", async ({
    page,
  }) => {
    await seedSavedView(
      TYPE_KEY,
      PAUSED_VIEW_NAME,
      stageIsPaused,
      ["name", "stage", "variant", "last_used", "due"],
      false,
    );
    const viewName = `Pinned ${Date.now()}`;
    await signInAndOpenTable(page);
    const wrapLeft = await leftOf(tableWrap(page));

    // Pin Stage, unpin Name, and save that as a view.
    const picker = await openColumns(page);
    await picker.getByRole("button", { name: "Pin Stage", exact: true }).click();
    await picker.getByRole("button", { name: "Unpin Name", exact: true }).click();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Current view", exact: true }).click();
    await page.getByLabel("New view name").fill(viewName);
    const saved = page.waitForResponse(
      (response) =>
        response.url().includes(`/object-types/${TYPE_KEY}/saved-views`)
        && response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Save as new" }).click();
    const body = (await (await saved).json()) as { config: { columns: { pinned?: string[] } } };
    expect(body.config.columns.pinned).toEqual(["stage"]);
    await page.keyboard.press("Escape");

    // A view with no `pinned` member at all: the display field is pinned.
    await page.getByRole("button", { name: `Current view: ${viewName}` }).click();
    await page.getByLabel("Saved view").selectOption({ label: PAUSED_VIEW_NAME });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Stage is Paused", exact: true })).toBeVisible();
    const pausedFirst = (await apiQuery(TYPE_KEY, stageIsPaused)).records[0].key;
    await scrollTableSideways(page, 800);
    expect(await leftOf(bodyCell(page, "Name", pausedFirst)), "display field pinned").toBe(
      wrapLeft + 1,
    );

    // Reload, select the saved view: its pins are back, exactly.
    await page.evaluate((prefix) => {
      for (const key of Object.keys(window.sessionStorage)) {
        if (key.startsWith(prefix)) window.sessionStorage.removeItem(key);
      }
    }, SESSION_PREFIX);
    await page.reload();
    await expect(firstDataRow(page)).toBeVisible();
    await page.getByRole("button", { name: "Current view", exact: true }).click();
    await page.getByLabel("Saved view").selectOption({ label: viewName });
    await page.keyboard.press("Escape");
    await scrollTableSideways(page, 0);
    const nameAtRest = await leftOf(headerCell(page, "name"));
    await scrollTableSideways(page, 400);
    expect(await leftOf(headerCell(page, "stage")), "Stage pinned by the view").toBe(wrapLeft + 1);
    expect(await leftOf(headerCell(page, "name")), "Name not pinned by the view").toBe(
      nameAtRest - 400,
    );
  });

  test("with the window scrolled too, the stuck toolbar is above the pinned column", async ({
    page,
  }) => {
    await signInAndOpenTable(page);
    await scrollTableSideways(page, 800);
    await scrollWindowTo(page, 1500);

    const crossing = await page.evaluate(() => {
      const toolbar = document.querySelector('[data-testid="table-toolbar"]')!;
      const bar = toolbar.getBoundingClientRect();
      // A pinned body cell that the toolbar currently overlaps, if there is one.
      const pinned = Array.from(document.querySelectorAll("tbody td")).filter(
        (cell) => getComputedStyle(cell).position === "sticky",
      );
      const under = pinned.find((cell) => {
        const rect = cell.getBoundingClientRect();
        return rect.top < bar.bottom && rect.bottom > bar.top;
      });
      const x = under ? under.getBoundingClientRect().left + 20 : -1;
      const top = x < 0 ? null : document.elementFromPoint(x, bar.top + bar.height / 2);
      return {
        pinnedCells: pinned.length,
        toolbarTop: Math.round(bar.top),
        overlapsPinned: under !== undefined,
        toolbarIsTopmost: top !== null && toolbar.contains(top),
      };
    });
    expect(crossing.pinnedCells, "sticky body cells").toBeGreaterThan(0);
    expect(crossing.toolbarTop).toBe(0);
    expect(crossing.overlapsPinned, "the toolbar crosses a pinned cell").toBe(true);
    expect(crossing.toolbarIsTopmost).toBe(true);
  });
});

// --------------------------------------------------------------------------------------------
// #19: the import wizard can clear a field, and only after a dry run said so
// --------------------------------------------------------------------------------------------

const IMPORT_TYPE_KEY = "e2e_pilot_import";
const CLEAR_LABEL = "Blank cells clear the field";

async function seedImportType(): Promise<void> {
  const existing = await apiContext.get(`/api/v1/object-types/${IMPORT_TYPE_KEY}`);
  if (!existing.ok()) {
    const created = await apiContext.post("/api/v1/object-types", {
      data: {
        key: IMPORT_TYPE_KEY,
        name: "Pilot import",
        name_plural: "Pilot imports",
        description: "An end-to-end fixture type for clearing a field by CSV import.",
        key_prefix: "PLI",
        fields: [
          {
            key: "name",
            name: "Name",
            type: "short_text",
            description: "The record's short display name.",
            required: true,
          },
          {
            key: "notes",
            name: "Notes",
            type: "long_text",
            description: "Free-form notes, the field the import clears.",
          },
        ],
      },
    });
    expect(created.ok(), await created.text()).toBeTruthy();
    const record = await apiContext.post(`/api/v1/object-types/${IMPORT_TYPE_KEY}/records`, {
      data: { name: "Imported one", notes: "remove me" },
    });
    expect(record.ok(), await record.text()).toBeTruthy();
  }
  // A retry starts from the value again.
  const reset = await apiContext.patch("/api/v1/records/PLI-001", {
    data: { values: { notes: "remove me" }, force: true },
  });
  expect(reset.ok(), await reset.text()).toBeTruthy();
}

test.describe("#19 the import wizard", () => {
  test("offers the clear only for an upsert, reports it in the dry run, and commits only what a dry run reported", async ({
    page,
  }) => {
    await seedImportType();
    await signInAsE2eAdmin(page);
    await page.goto(`/${IMPORT_TYPE_KEY}/import`);
    await page.getByLabel("Upload CSV file").setInputFiles({
      name: "clear.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("key,Name,Notes\nPLI-001,Imported one,\n"),
    });
    await page.getByLabel("Column mapping for key").selectOption("key");
    await page.getByLabel("Column mapping for Name").selectOption("name");
    await page.getByLabel("Column mapping for Notes").selectOption("notes");

    // Create mode: there is no existing value to clear, so there is no box.
    await expect(page.getByLabel("Import mode")).toHaveValue("create");
    await expect(page.getByRole("checkbox", { name: CLEAR_LABEL })).toHaveCount(0);

    await page.getByLabel("Import mode").selectOption("upsert");
    await page.getByLabel("Upsert key").selectOption("key");
    const box = page.getByRole("checkbox", { name: CLEAR_LABEL });
    await expect(box).toBeVisible();
    await expect(box).not.toBeChecked();
    await expect(page.getByText("Off: a blank cell leaves the record's value as it is.")).toBeVisible();

    // A dry run with the box off reports no clears and enables Commit.
    const commit = page.getByRole("button", { name: "Commit", exact: true });
    await page.getByRole("button", { name: "Run dry-run" }).click();
    await expect(page.getByText("Dry run passed: 0 would be created, 1 would be updated.")).toBeVisible();
    await expect(page.getByTestId("import-clears-dry-run")).toHaveCount(0);
    await expect(commit).toBeEnabled();

    // Ticking the box discards that dry run: Commit cannot clear what no dry run reported.
    await box.check();
    await expect(commit).toBeDisabled();
    await expect(page.getByText(/Dry run passed/)).toHaveCount(0);

    await page.getByRole("button", { name: "Run dry-run" }).click();
    await expect(page.getByTestId("import-clears-dry-run")).toHaveText(
      "1 value will be cleared: Notes (1).",
    );
    await expect(commit).toBeEnabled();
    // Nothing is written by a dry run.
    const untouched = await apiContext.get("/api/v1/records/PLI-001");
    expect(((await untouched.json()) as { data: { notes?: string } }).data.notes).toBe("remove me");

    await commit.click();
    await expect(page.getByTestId("import-clears-commit")).toHaveText("1 value cleared: Notes (1).");
    const cleared = await apiContext.get("/api/v1/records/PLI-001");
    expect(((await cleared.json()) as { data: { notes?: string } }).data.notes).toBeUndefined();
  });
});
