import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAsE2eAdmin } from "./constants";

/**
 * The table end-to-end scenario: a browser test opens the table view for a seeded object type,
 * edits a cell inline, adds a comment on a record's detail view, saves a filtered-and-sorted
 * view, reopens it and confirms the filter/sort/columns persisted, and triggers a 409 by racing
 * two updates to the same record, resolving it through the merge prompt.
 *
 * This runs against the real built frontend (`web/dist`) served by a real `uvicorn` process
 * (see `playwright.config.ts`'s `webServer`), never a mocked backend. Seeding goes straight over
 * the REST API with a personal access token (DD-8).
 */

const OBJECT_TYPE_KEY = "e2e_widget";

interface SeededRecord {
  key: string;
  title: string;
  status: "active" | "inactive";
}

// Titles are chosen so that filtering to `status: active` and sorting by `title` ascending
// produces a subset whose order actually changes from creation order (Alpha, Epsilon, Gamma —
// not Alpha, Gamma, Epsilon) and so the inline-edit and 409 steps can each pick an untouched,
// filtered-OUT (inactive) record without disturbing the filter/sort assertions.
const SEED_RECORDS: Omit<SeededRecord, "key">[] = [
  { title: "Widget Alpha", status: "active" },
  { title: "Widget Beta", status: "inactive" },
  { title: "Widget Gamma", status: "active" },
  { title: "Widget Delta", status: "inactive" },
  { title: "Widget Epsilon", status: "active" },
];

let seeded: SeededRecord[] = [];
let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  const createTypeResponse = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "Widget",
      name_plural: "Widgets",
      description: "An end-to-end fixture object type, seeded fresh for this test run.",
      key_prefix: "WID",
      fields: [
        {
          key: "title",
          name: "Title",
          type: "short_text",
          description: "The widget's short display name.",
          required: true,
        },
        {
          key: "status",
          name: "Status",
          type: "single_select",
          description: "Whether the widget is currently active.",
          config: {
            options: [
              { value: "active", label: "Active", description: "Currently in use." },
              { value: "inactive", label: "Inactive", description: "Retired." },
            ],
          },
        },
      ],
    },
  });
  expect(createTypeResponse.ok(), await createTypeResponse.text()).toBeTruthy();

  seeded = [];
  for (const record of SEED_RECORDS) {
    const response = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
      data: { title: record.title, status: record.status },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    const body = (await response.json()) as { key: string };
    seeded.push({ ...record, key: body.key });
  }
});

test.afterAll(async () => {
  await apiContext.dispose();
});

// The served bundle carries no credential at all (`playwright.config.ts`'s doc comment).
// Every spec that drives the UI signs the browser in through the real `/login` form first.
test.beforeEach(async ({ page }) => {
  await signInAsE2eAdmin(page);
});

function keyFor(title: string): string {
  const record = seeded.find((candidate) => candidate.title === title);
  if (!record) throw new Error(`No seeded record titled ${title}`);
  return record.key;
}

/** One opening click on a toolbar chip. Addressed by the chip's
 * test id and taking its first button, which is the popover trigger; the trailing `×`, where a
 * chip has one, is the second. */
async function openChip(page: Page, testId: string): Promise<void> {
  await page.getByTestId(testId).getByRole("button").first().click();
}

/** The same for the View menu: the saved-view select, `Save`, `Save as new`, `Set as default`,
 * `New view name` and `Export CSV` are one popover on the right of the toolbar, so the specs
 * driving them open it first. */
async function openViewMenu(page: Page): Promise<void> {
  await page.getByRole("button", { name: /^Current view/ }).click();
  await expect(page.getByTestId("view-menu")).toBeVisible();
}

/** The currently rendered data rows' `data-testid`s, in DOM order — used inside `expect.poll`
 * so a filter/sort change that hasn't finished re-rendering yet is retried rather than read
 * mid-flight. */
async function rowOrder(page: Page): Promise<(string | null)[]> {
  return page.locator('[data-testid^="row-"]').evaluateAll((rows) =>
    rows.map((row) => row.getAttribute("data-testid")),
  );
}

test("table edit, comment, saved view persistence, and 409 merge resolution", async ({
  page,
  request,
}) => {
  const alphaKey = keyFor("Widget Alpha");
  const betaKey = keyFor("Widget Beta");
  const gammaKey = keyFor("Widget Gamma");
  const deltaKey = keyFor("Widget Delta");
  const epsilonKey = keyFor("Widget Epsilon");

  await test.step("table view opens and shows seeded records", async () => {
    await page.goto(`/${OBJECT_TYPE_KEY}`);
    await expect(page.getByRole("heading", { name: "Widget", level: 1 })).toBeVisible();
    for (const record of seeded) {
      await expect(
        page.getByRole("button", { name: `Edit Title for ${record.key}` }),
      ).toHaveText(record.title);
    }
  });

  await test.step("inline cell edit updates the row and persists on the backend", async () => {
    const cell = page.getByRole("button", { name: `Edit Title for ${betaKey}` });
    await cell.click();
    const input = page.getByLabel(`Title value for ${betaKey}`);
    await input.fill("Widget Beta (edited)");
    await input.press("Enter");

    await expect(page.getByRole("button", { name: `Edit Title for ${betaKey}` })).toHaveText(
      "Widget Beta (edited)",
    );

    const response = await request.get(`/api/v1/records/${betaKey}`, {
      headers: E2E_AUTH_HEADER,
    });
    expect(response.ok()).toBeTruthy();
    const record = (await response.json()) as { data: { title: string } };
    expect(record.data.title).toBe("Widget Beta (edited)");
  });

  await test.step("adding a comment on the record detail view persists it", async () => {
    await page.goto(`/${OBJECT_TYPE_KEY}/${alphaKey}`);
    // The record page is titled by its display value, and the key is a mono chip. What this
    // step needs is "we are on this record's page", which the chip says exactly;
    // `record-page.spec.ts` owns the claim about what the title contains.
    await expect(page.getByTestId("record-key-chip")).toHaveText(alphaKey);

    // The composer is the Activity card's, and DESIGN.md 8.3 names both strings:
    // placeholder "Reply. Agents read this too.", primary button "Post".
    await page.getByPlaceholder("Reply. Agents read this too.").fill("Reviewed and looks good.");
    await page.getByRole("button", { name: "Post" }).click();

    await expect(page.getByText("Reviewed and looks good.")).toBeVisible();

    // Polled, not read once: the visibility assertion above is satisfied by the detail view's
    // own optimistic render, which happens while the POST is still in flight, so a single
    // out-of-band read races the write that it is meant to verify. Server access logs from a
    // failing run show this directly -- the verification GET responded 0.3 ms *before* the
    // comment POST did. The race is wide because a comment write also maintains the keyword row
    // and enqueues an embedding job inside the same transaction (~8 ms rather than ~2 ms); the
    // write itself always succeeded and the comment was never lost. What this step
    // means is that the comment is durably persisted and readable out of band, which is what
    // polling asserts.
    await expect
      .poll(async () => {
        const response = await request.get(`/api/v1/records/${alphaKey}?include=comments`, {
          headers: E2E_AUTH_HEADER,
        });
        expect(response.ok()).toBeTruthy();
        const record = (await response.json()) as { comments: { body: string }[] };
        return record.comments.map((comment) => comment.body);
      })
      .toContain("Reviewed and looks good.");
  });

  await test.step("saving a filtered-and-sorted view persists filter, sort, and columns", async () => {
    await page.goto(`/${OBJECT_TYPE_KEY}`);
    await expect(page.getByRole("button", { name: `Edit Title for ${alphaKey}` })).toBeVisible();

    // Filter: status = Active, through docs/DESIGN.md 7.4's chips: `+ Add filter` opens the
    // condition popover, and a select commits on change with no Apply. A chip serialises to the
    // same filter AST a tree builder would send, so the view this step goes on to save is the
    // same.
    await page.getByRole("button", { name: "+ Add filter" }).click();
    const conditionRow = page.getByTestId("condition-popover");
    await conditionRow.getByLabel("Field").selectOption({ label: "Status" });
    await conditionRow.getByLabel("Status value").selectOption({ label: "Active" });
    // Dismissed before the next control is touched: the popover is anchored to its chip and a
    // click elsewhere would land on it rather than on the control the next step means.
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("condition-popover")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Status is Active", exact: true }),
    ).toBeVisible();

    // Sort: Title ascending, behind the `Sort` chip (docs/DESIGN.md 7.4).
    await openChip(page, "sort-chip");
    await page.getByLabel("Add sort key").selectOption({ label: "Title" });

    // Hide the Status column. This uncheck happens in a real browser, where Playwright requires
    // visibility; docs/DESIGN.md 8.2 makes the column picker a popover, so the spec pays one
    // opening click for it and the uncheck stays a real uncheck in a real browser. It is not
    // moved to a jsdom test, which would quietly lose that.
    await openChip(page, "columns-chip");
    const columnPicker = page.getByTestId("column-picker");
    await columnPicker.getByRole("checkbox", { name: "Status" }).uncheck();
    // Dismissed before the toolbar is touched again: the panel is anchored over the controls the
    // next steps use.
    await page.keyboard.press("Escape");
    await expect(columnPicker).toHaveCount(0);

    const theadLocator = page.getByTestId("records-table").locator("thead");
    await expect(page.getByTestId(`row-${betaKey}`)).toHaveCount(0);
    await expect(page.getByTestId(`row-${deltaKey}`)).toHaveCount(0);
    await expect(theadLocator).not.toContainText("Status");
    await expect(theadLocator).toContainText("Title");

    await expect
      .poll(async () => rowOrder(page))
      .toEqual([`row-${alphaKey}`, `row-${epsilonKey}`, `row-${gammaKey}`]);

    await openViewMenu(page);
    await page.getByLabel("New view name").fill("Active widgets by title");
    await page.getByRole("button", { name: "Save as new" }).click();
    await expect(page.getByLabel("Saved view")).toHaveValue(/.+/);

    // Reload from scratch: a full navigation, discarding all client state. The saved view was
    // the object type's first, so it was created as the default (`SavedViewsBar`/`TableView`'s
    // "save as new when no views exist yet -> is_default" path) and should load automatically.
    await page.reload();

    await expect(page.getByRole("button", { name: `Edit Title for ${alphaKey}` })).toBeVisible();
    // The saved filter came back and reads as a sentence. The popover is closed after a reload,
    // so the assertion is on the chip, which carries the same fact and the display vocabulary
    // besides.
    await expect(
      page.getByRole("button", { name: "Status is Active", exact: true }),
    ).toBeVisible();
    // The menu is closed after a reload, and a `<select>` behind a closed popover is absent
    // rather than empty — the same shape as the column checkbox two assertions down.
    await openViewMenu(page);
    await expect(page.getByLabel("Saved view")).not.toHaveValue("");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("view-menu")).toHaveCount(0);
    await expect(theadLocator).not.toContainText("Status");
    await expect(theadLocator).toContainText("Title");
    // Reopened, because a checkbox behind a closed popover is not unchecked — it is absent, and
    // an assertion about it would pass for the wrong reason.
    await openChip(page, "columns-chip");
    await expect(columnPicker.getByRole("checkbox", { name: "Status" })).not.toBeChecked();
    await page.keyboard.press("Escape");
    await expect(columnPicker).toHaveCount(0);

    await expect(page.getByTestId(`row-${betaKey}`)).toHaveCount(0);
    await expect(page.getByTestId(`row-${deltaKey}`)).toHaveCount(0);
    await expect
      .poll(async () => rowOrder(page))
      .toEqual([`row-${alphaKey}`, `row-${epsilonKey}`, `row-${gammaKey}`]);

    // Clear the view before the next step, which needs every record visible again.
    await openViewMenu(page);
    await page.getByLabel("Saved view").selectOption("");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("view-menu")).toHaveCount(0);
    await expect(page.getByTestId(`row-${deltaKey}`)).toBeVisible();
  });

  await test.step("a 409 from a racing update opens the merge prompt and resolves through it", async () => {
    const before = await request.get(`/api/v1/records/${deltaKey}`, { headers: E2E_AUTH_HEADER });
    expect(before.ok()).toBeTruthy();
    const beforeRecord = (await before.json()) as { version: number };
    const expectedVersion = beforeRecord.version;

    await page.getByRole("button", { name: `Edit Title for ${deltaKey}` }).click();
    const input = page.getByLabel(`Title value for ${deltaKey}`);
    await input.fill("Widget Delta (mine)");

    // The concurrent writer: beats the UI's own (still-pending) edit to the same expected_version.
    const racingPatch = await request.patch(`/api/v1/records/${deltaKey}`, {
      headers: E2E_AUTH_HEADER,
      data: {
        values: { title: "Widget Delta (external)" },
        expected_version: expectedVersion,
        force: false,
      },
    });
    expect(racingPatch.ok(), await racingPatch.text()).toBeTruthy();

    // Commit the UI's edit now: it still carries the now-stale expected_version, so this loses
    // the race and the backend returns 409.
    await input.press("Enter");

    const dialog = page.getByTestId("merge-conflict-dialog");
    await expect(dialog).toBeVisible();
    const conflictRow = dialog.getByTestId("conflict-field-title");
    await expect(conflictRow).toContainText("Widget Delta (mine)");
    await expect(conflictRow).toContainText("Widget Delta (external)");

    // Resolve via an edited merge value rather than "Theirs" verbatim: choosing "Theirs" would
    // resubmit the value the record already holds (a no-op the service correctly declines to
    // bump the version for), which would leave this assertion unable to tell "the resubmit
    // landed" apart from "the resubmit was silently dropped". An edited value makes the
    // resubmit's own write, and its version bump, unambiguous.
    await conflictRow.getByLabel("Edit").check();
    await conflictRow.getByLabel(/^Merged value for/).fill("Widget Delta (merged)");
    await dialog.getByRole("button", { name: "Resubmit" }).click();

    await expect(dialog).toHaveCount(0);
    await expect(page.getByRole("button", { name: `Edit Title for ${deltaKey}` })).toHaveText(
      "Widget Delta (merged)",
    );

    const after = await request.get(`/api/v1/records/${deltaKey}`, { headers: E2E_AUTH_HEADER });
    expect(after.ok()).toBeTruthy();
    const afterRecord = (await after.json()) as { version: number; data: { title: string } };
    expect(afterRecord.data.title).toBe("Widget Delta (merged)");
    expect(afterRecord.version).toBe(expectedVersion + 2);
  });
});
