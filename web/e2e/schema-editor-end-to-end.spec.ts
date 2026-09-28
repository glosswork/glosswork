import { expect, request as apiRequestModule, test, type APIRequestContext } from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAsE2eAdmin } from "./constants";

/**
 * The schema-editor end-to-end scenario: a browser test creates an object type with a described
 * field through the schema editor, edits that field destructively (a type change) and sees the
 * blast radius before the change is left pending, imports a CSV through the wizard with at least
 * one auto-suggested column mapping, exports the resulting view as CSV, reverts a single field
 * change from `/activity` and confirms the record's value and version reflect it, and triggers a
 * 409 on a revert by racing an update to the same record, resolving it through the existing
 * merge prompt.
 *
 * Runs against the real built frontend served by a real `uvicorn` process (see
 * `playwright.config.ts`), never a mocked backend. A few steps seed or race writes directly over
 * the REST API with a personal access token (DD-8). Inline cell editing and the 409-merge-prompt
 * mechanics are already end-to-end proven by `table-end-to-end.spec.ts`; this spec's own job is
 * four other surfaces (schema editor, CSV wizard/export, `/activity`, and the Activity card's
 * revert-to-a-race), not re-proving the table.
 */

const OBJECT_TYPE_KEY = "e2e_gadget";

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });
});

test.afterAll(async () => {
  await apiContext.dispose();
});

// The served bundle carries no credential at all (`playwright.config.ts`'s doc comment).
// Every spec that drives the UI signs the browser in through the real `/login` form first.
test.beforeEach(async ({ page }) => {
  await signInAsE2eAdmin(page);
});

test("schema editor, CSV import/export, and audit revert (plain and 409)", async ({
  page,
  request,
}) => {
  await test.step("create an object type with a described field through the schema editor", async () => {
    await page.goto("/schema/new");
    await page.getByLabel("Key", { exact: true }).fill(OBJECT_TYPE_KEY);
    await page.getByLabel("Name", { exact: true }).fill("Gadget");
    await page.getByLabel("Plural name").fill("Gadgets");
    await page.getByLabel("Key prefix").fill("GAD");
    await page
      .getByLabel("Description")
      .fill("An end-to-end fixture object type, seeded fresh for this test run.");
    await page.getByRole("button", { name: "Create" }).click();

    await expect(page).toHaveURL(new RegExp(`/schema/${OBJECT_TYPE_KEY}$`));
    await expect(page.getByRole("heading", { name: "Gadget" })).toBeVisible();

    await page.getByRole("button", { name: "Add field" }).click();
    const addForm = page.getByRole("form", { name: "Add field" });
    await addForm.getByLabel("Key", { exact: true }).fill("target_date");
    await addForm.getByLabel("Name", { exact: true }).fill("Target Date");
    await addForm.getByLabel("Description").fill("When this gadget should land.");
    await addForm.getByLabel("Type").selectOption("date");
    await addForm.getByRole("button", { name: "Add field" }).click();

    await expect(page.getByTestId("field-row-target_date")).toContainText("date");
  });

  let seededKey = "";

  await test.step("seed one record with the new field set, over the REST API", async () => {
    const response = await request.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
      headers: E2E_AUTH_HEADER,
      data: { target_date: "2026-08-01" },
    });
    expect(response.ok(), await response.text()).toBeTruthy();
    const body = (await response.json()) as { key: string };
    seededKey = body.key;
  });

  await test.step("a destructive field-type change shows the blast radius before it is pending", async () => {
    await page.goto(`/schema/${OBJECT_TYPE_KEY}`);
    await page.getByTestId("field-row-target_date").getByRole("button", { name: "Edit" }).click();

    const editForm = page.getByRole("form", { name: "Edit field" });
    await editForm.getByLabel("Type").selectOption("short_text");
    await editForm.getByRole("button", { name: "Save field" }).click();

    const panel = page.getByTestId("blast-radius-panel");
    await expect(panel).toBeVisible();
    await expect(panel.getByTestId("blast-radius-affected-records")).toHaveText("1");
    await expect(panel.getByTestId("blast-radius-sample-values")).toContainText("2026-08-01");
    // The destination is the proposal's own Inbox page. Asserted on the HREF, not the link text
    // -- the text is copy, the href is the claim, and a link whose words changed while its
    // target did not is exactly what a text match would keep passing.
    const inboxLink = panel.getByRole("link", { name: /inbox/i });
    await expect(inboxLink).toBeVisible();
    await expect(inboxLink).toHaveAttribute("href", /^\/inbox\/prop/);

    // Nothing applied yet: the field editor and field list are hidden behind the panel, and the
    // schema itself is unchanged once we dismiss it.
    await expect(page.getByRole("form", { name: "Edit field" })).toHaveCount(0);
    await panel.getByRole("button", { name: "Acknowledge" }).click();
    await expect(page.getByTestId("blast-radius-panel")).toHaveCount(0);
    await expect(page.getByTestId("field-row-target_date")).toContainText("date");
  });

  await test.step("imports a CSV with an auto-suggested column mapping", async () => {
    await page.goto(`/${OBJECT_TYPE_KEY}/import`);
    await page
      .getByLabel("Upload CSV file")
      .setInputFiles({
        name: "gadgets.csv",
        mimeType: "text/csv",
        buffer: Buffer.from("Target Date\n2026-09-02\n"),
      });

    // "Target Date" auto-suggests the target_date field by name similarity (no user action).
    await expect(page.getByLabel("Column mapping for Target Date")).toHaveValue("target_date");

    await page.getByRole("button", { name: "Run dry-run" }).click();
    await expect(page.getByText(/Dry run passed/)).toBeVisible();

    await page.getByRole("button", { name: "Commit" }).click();
    await expect(page.getByText(/Import complete: 1 created/)).toBeVisible();
  });

  await test.step("exports the current view as CSV", async () => {
    await page.goto(`/${OBJECT_TYPE_KEY}`);
    // FR-U7's entry point is in the View menu (docs/DESIGN.md 8.2 sends CSV export there). The
    // export itself (the filename, the cells, the honoured view) is what this step asserts.
    await page.getByRole("button", { name: /^Current view/ }).click();
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: "Export CSV" }).click(),
    ]);
    expect(download.suggestedFilename()).toBe(`${OBJECT_TYPE_KEY}.csv`);
    const stream = await download.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(chunk as Buffer);
    const csvText = Buffer.concat(chunks).toString("utf-8");
    expect(csvText).toContain("2026-08-01");
    expect(csvText).toContain("2026-09-02");
  });

  await test.step("reverts a single field change from /activity", async () => {
    const patchResponse = await request.patch(`/api/v1/records/${seededKey}`, {
      headers: E2E_AUTH_HEADER,
      data: { values: { target_date: "2026-09-01" }, expected_version: 1, force: false },
    });
    expect(patchResponse.ok(), await patchResponse.text()).toBeTruthy();

    // On `/activity` the filters are chips rather than labelled inputs, and a write is one entry
    // rather than one row per field. The values are formatted for a reader rather than
    // `JSON.stringify`d, so `"2026-08-01"` is `1 Aug`, as on the record page.
    await page.goto("/activity");
    const chips = page.getByTestId("activity-filters");
    await chips.getByRole("button", { name: "Add filter" }).click();
    await page.getByRole("menuitem", { name: "Record" }).click();
    // `getByRole`, not `getByLabel`: the popover's form is named "Record filter", which a
    // substring label match also resolves to.
    await page.getByRole("textbox", { name: "Record" }).fill(seededKey);
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(chips.getByText(`Record is ${seededKey}`)).toBeVisible();

    const pill = page
      .locator('[data-testid^="activity-change-"]')
      .filter({ hasText: "1 Aug" })
      .filter({ hasText: "1 Sep" });
    await expect(pill).toBeVisible();
    await pill.getByRole("button", { name: /^Revert/ }).click();
    await expect(page.getByText(/^Reverted event \d+\.$/)).toBeVisible();

    const after = await request.get(`/api/v1/records/${seededKey}`, {
      headers: E2E_AUTH_HEADER,
    });
    const afterBody = (await after.json()) as { version: number; data: { target_date: string } };
    expect(afterBody.data.target_date).toBe("2026-08-01");
    expect(afterBody.version).toBe(3);
  });

  await test.step("triggers a 409 on a revert by racing an update, resolved through the merge prompt", async () => {
    const setup = await request.patch(`/api/v1/records/${seededKey}`, {
      headers: E2E_AUTH_HEADER,
      data: { values: { target_date: "2026-10-01" }, expected_version: 3, force: false },
    });
    expect(setup.ok(), await setup.text()).toBeTruthy();
    const setupBody = (await setup.json()) as { version: number };
    expect(setupBody.version).toBe(4);

    await page.goto(`/${OBJECT_TYPE_KEY}/${seededKey}`);
    // The record page is titled by its display value, and the key is a mono chip. What this
    // step needs is "we are on this record's page", which the chip says exactly;
    // `record-page.spec.ts` owns the claim about what the title contains.
    await expect(page.getByTestId("record-key-chip")).toHaveText(seededKey);

    // In the Activity card a version's field changes are change pills that are always visible, so
    // there is no per-write disclosure to open. The values are formatted for a reader rather than
    // `JSON.stringify`d, so `"2026-10-01"` is `1 Oct`; the substring is asserted rather than the
    // whole string because `formatDate` appends the year only outside the current one.
    const changed = page
      .locator('[data-testid^="activity-change-"]')
      .filter({ hasText: "1 Aug" })
      .filter({ hasText: "1 Oct" });
    await expect(changed).toHaveCount(1);

    // The race: an external write lands after the record-detail page (and its AuditTimeline's
    // captured `recordVersion`) loaded, but before the revert button below is clicked.
    const racingPatch = await request.patch(`/api/v1/records/${seededKey}`, {
      headers: E2E_AUTH_HEADER,
      data: { values: { target_date: "2026-11-01" }, expected_version: 4, force: false },
    });
    expect(racingPatch.ok(), await racingPatch.text()).toBeTruthy();

    // The per-field revert rides the change pill itself, so the pill located above is the row
    // this step acts on.
    await changed.getByRole("button", { name: "Revert this change" }).click();

    const dialog = page.getByTestId("merge-conflict-dialog");
    await expect(dialog).toBeVisible();
    const conflictRow = dialog.getByTestId("conflict-field-target_date");
    await expect(conflictRow).toContainText("2026-11-01");

    // Resolve via an edited merge value rather than "Theirs" verbatim: "Theirs" would resubmit
    // the value the record already holds, a no-op the service correctly declines to bump the
    // version for, which would leave this step unable to tell "the resubmit landed" apart from
    // "the resubmit was silently dropped" (mirrors table-end-to-end.spec.ts's 409 step).
    await conflictRow.getByLabel("Edit").check();
    await conflictRow.getByLabel(/^Merged value for/).fill("2026-12-01");
    await dialog.getByRole("button", { name: "Resubmit" }).click();
    await expect(dialog).toHaveCount(0);

    const after = await request.get(`/api/v1/records/${seededKey}`, {
      headers: E2E_AUTH_HEADER,
    });
    const afterBody = (await after.json()) as { version: number; data: { target_date: string } };
    expect(afterBody.data.target_date).toBe("2026-12-01");
    // 1 (create) -> 2 (first patch) -> 3 (plain revert) -> 4 (setup patch) -> 5 (racing patch,
    // the one that made the revert stale) -> 6 (the resubmit that resolved the conflict).
    expect(afterBody.version).toBe(6);
  });
});
