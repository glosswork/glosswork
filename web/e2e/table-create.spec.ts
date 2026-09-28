import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAsE2eAdmin } from "./constants";

/**
 * DD-44: a person creates a record by hand.
 *
 * Against a real browser, a real uvicorn and a real database, because three of the four things
 * this spec is about are unprovable anywhere else. The `<dialog>` shim in `web/src/test/setup.ts`
 * implements `close` and no `cancel`, and provides no focus containment, so "Escape dismisses it"
 * and "the modal is modal" are Playwright's to assert. And the control's *position* — outside the
 * toolbar, on the title line DD-44 names — is geometry, which is never proven in jsdom.
 *
 * The fixture is its own object type rather than a shared one: this spec creates records, and a
 * type whose `record_count` moves under another spec's feet is how a fixture becomes flaky.
 */

const TYPE_KEY = "e2e_create";
const TYPE_NAME = "Create Probe";

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  // Idempotent: Playwright reloads the config per worker, and a second POST of the same
  // `key_prefix` is refused outright.
  const existing = await apiContext.get(`/api/v1/object-types/${TYPE_KEY}`);
  if (existing.ok()) return;

  const createType = await apiContext.post("/api/v1/object-types", {
    data: {
      key: TYPE_KEY,
      name: TYPE_NAME,
      name_plural: "Create Probes",
      description: "An end-to-end fixture: one required field, one defaulted select, "
        + "one relation that the create form must refuse to offer.",
      key_prefix: "CRP",
      fields: [
        {
          key: "title",
          name: "Title",
          type: "short_text",
          description: "The probe record's name. Required, and carries no default, so it is the "
            + "field the required pre-flight is about.",
          required: true,
        },
        {
          key: "stage",
          name: "Stage",
          type: "single_select",
          description: "Where the probe sits. Defaulted, so omitting it must still succeed.",
          default: "draft",
          config: {
            options: [
              { value: "draft", label: "Draft", description: "Not started." },
              { value: "live", label: "Live", description: "In flight." },
            ],
          },
        },
        {
          key: "related",
          name: "Related",
          type: "relation",
          description: "A link to another probe, which the create route refuses outright and the "
            + "form therefore must not offer.",
          config: { target_type_key: TYPE_KEY, cardinality: "many" },
        },
      ],
    },
  });
  expect(createType.ok(), await createType.text()).toBeTruthy();
});

test.afterAll(async () => {
  await apiContext.dispose();
});

test.beforeEach(async ({ page }) => {
  await signInAsE2eAdmin(page);
});

test("creates a record and lands on it, with the dialog open as the route changes", async ({
  page,
}) => {
  await page.goto(`/${TYPE_KEY}`);
  await page.getByTestId("new-record").click();

  const dialog = page.getByTestId("new-record-dialog");
  await expect(dialog).toBeVisible();

  // The relation field is absent, and that is the write path's rule rather than this form's
  // scope: `create_record` refuses a relation value outright.
  await expect(dialog.getByLabel("Related")).toHaveCount(0);
  await expect(dialog.getByTestId("new-record-deferred-fields")).toBeVisible();

  const title = `Created by hand ${Date.now()}`;
  await dialog.getByLabel("Title").fill(title);
  await dialog.getByRole("button", { name: `Create ${TYPE_NAME}` }).click();

  // The record page, at the same URL shape the table's key column builds. The key is read from
  // the page rather than assumed: `key_seq` is per type and other scenarios share this database.
  await expect(page).toHaveURL(new RegExp(`/${TYPE_KEY}/CRP-\\d+$`));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);

  // The dialog was open at the moment the route changed — no other `ui/Dialog` call site
  // navigates on success, so nothing had exercised `Dialog`'s unmount-closes cleanup that way.
  // If it had leaked, the `<dialog>` would still be in the top layer over the record page.
  await expect(page.getByTestId("new-record-dialog")).toHaveCount(0);

  // And the write really landed, read back over REST rather than inferred from the screen.
  const key = new URL(page.url()).pathname.split("/").pop()!;
  const fetched = await apiContext.get(`/api/v1/records/${key}`);
  expect(fetched.ok(), await fetched.text()).toBeTruthy();
  const body = (await fetched.json()) as { data: Record<string, unknown> };
  expect(body.data.title).toBe(title);
  // `stage` was never touched, so the form omitted it and the SERVER applied its default. Had the
  // form posted every field, this would read null and the default would never have run.
  expect(body.data.stage).toBe("draft");
});

test("refuses an empty required field in front of the person, issuing no request", async ({
  page,
}) => {
  /**
   * The counter is driven to ≥1 on a real submission **first**, in this same scenario, and that
   * ordering is the point rather than setup. Asserting "zero POSTs" against a glob that never
   * matched is green for the wrong reason — the same vacuity `heading-outline.spec.ts` documents
   * for an empty parent match — so the glob has to be shown working before its silence means
   * anything.
   */
  let creates = 0;
  await page.route("**/api/v1/object-types/*/records", async (route) => {
    creates += 1;
    await route.continue();
  });

  await page.goto(`/${TYPE_KEY}`);
  await page.getByTestId("new-record").click();
  const dialog = page.getByTestId("new-record-dialog");

  // Part one: a real create, so the counter is known to be wired to the route it names.
  await dialog.getByLabel("Title").fill(`Counter proof ${Date.now()}`);
  await dialog.getByRole("button", { name: `Create ${TYPE_NAME}` }).click();
  await expect(page).toHaveURL(new RegExp(`/${TYPE_KEY}/CRP-\\d+$`));
  expect(creates, "the route glob never matched a real create").toBeGreaterThanOrEqual(1);

  // Part two: the same control, nothing entered. The refusal is the client's, and the network
  // stays silent.
  const countAfterRealCreate = creates;
  await page.goto(`/${TYPE_KEY}`);
  await page.getByTestId("new-record").click();
  await page.getByTestId("new-record-dialog").getByRole("button", { name: `Create ${TYPE_NAME}` }).click();

  await expect(page.getByRole("alert")).toHaveText("Title is required.");
  await expect(page.getByTestId("new-record-dialog")).toBeVisible();
  expect(creates).toBe(countAfterRealCreate);
});

test("dismisses on Escape, which only a real engine can show", async ({ page }) => {
  // jsdom's `<dialog>` shim fires no `cancel` event, so the component suite can only assert that a
  // field's own Escape handler closes nothing. This is the other half.
  await page.goto(`/${TYPE_KEY}`);
  await page.getByTestId("new-record").click();

  const dialog = page.getByTestId("new-record-dialog");
  await expect(dialog).toBeVisible();

  await dialog.getByLabel("Title").fill("Abandoned");
  await page.keyboard.press("Escape");

  await expect(dialog).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`/${TYPE_KEY}$`));
});

test("the create control is outside the toolbar (DD-44)", async ({ page }) => {
  /**
   * DD-44's title-line placement, asserted so it cannot quietly regress. The toolbar's children are a
   * constant 716.8px and its narrowest width across the viewport range is 688px at 960px, so a
   * control of any width wraps it, measured on the `vis_wide` fixture.
   *
   * `closest()`, not `parentElement`: a button nested one wrapper deep inside the toolbar is
   * exactly how a control rejoins it, and a parent check passes for that. Both forms are asserted
   * because they fail differently — the scoped count would also catch a second `New …` control
   * appearing inside the toolbar beside the real one.
   */
  await page.goto(`/${TYPE_KEY}`);
  const control = page.getByTestId("new-record");
  await expect(control).toBeVisible();

  const insideToolbar = await control.evaluate(
    (el) => el.closest('[data-testid="table-toolbar"]') !== null,
  );
  expect(insideToolbar, "the create control has moved into the toolbar").toBe(false);

  await expect(
    page.getByTestId("table-toolbar").getByRole("button", { name: /^New / }),
  ).toHaveCount(0);
});
