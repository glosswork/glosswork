import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAs, signInAsE2eAdmin } from "./constants";

/**
 * The level gating proven end to end against a real server, a real database, and real grants,
 * not in jsdom, where `your_access` is whatever a fixture literal says. The distinction matters
 * because the gating rests on a value the backend composes
 * (`min(credential scope, granted level)`), and a component test can only assert what the UI
 * does with a number someone typed.
 *
 * Everything here is seeded in the **functional** project, never the visual one: a new visual
 * fixture is never free, and a second principal in the visual project would repaint baselines
 * that have nothing to do with access levels.
 *
 * Four principals, because four different questions are being asked:
 *
 *   - a `read`-granted member: the read-only screens
 *   - a `write`-granted member: the schema editor's two thresholds, and the 403 of a grant
 *     narrowed mid-session
 *   - a member with no grant: the index empty state
 *   - a `creator` with an `admin` grant: the permissions panel, offered on the strength of the
 *     type-level grant alone (DD-11)
 */

const OBJECT_TYPE_KEY = "e2e_access";
/** The fixture type's display name. Named here rather than repeated, because the title line's
 * `New <type>` primary renders it and the assertion below has to match the seed exactly. */
const TYPE_NAME = "Access Probe";
const PASSWORD = "e2e-access-passw0rd!";

const READER_EMAIL = "e2e-reader@example.com";
const WRITER_EMAIL = "e2e-writer@example.com";
const OUTSIDER_EMAIL = "e2e-outsider@example.com";
const CREATOR_EMAIL = "e2e-access-creator@example.com";

let apiContext: APIRequestContext;
let writerPrincipalId: string;
let creatorPrincipalId: string;
let outsiderPrincipalId: string;
let recordKey: string;

async function createPrincipal(email: string, role: string, name: string): Promise<string> {
  const response = await apiContext.post("/api/v1/principals", {
    data: { type: "user", display_name: name, email, role, password: PASSWORD },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { id: string }).id;
}

async function grant(principalId: string, level: string): Promise<void> {
  const response = await apiContext.put(
    `/api/v1/object-types/${OBJECT_TYPE_KEY}/grants/${principalId}`,
    { data: { level } },
  );
  expect(response.ok(), await response.text()).toBeTruthy();
}

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  const createType = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: TYPE_NAME,
      name_plural: "Access Probes",
      description: "An end-to-end fixture object type for per-object-type level gating.",
      key_prefix: "ACCP",
      fields: [
        {
          key: "title",
          name: "Title",
          type: "short_text",
          description: "The probe record's name, edited inline to produce an audit row.",
        },
      ],
    },
  });
  expect(createType.ok(), await createType.text()).toBeTruthy();

  const createRecord = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
    data: { title: "First title" },
  });
  expect(createRecord.ok(), await createRecord.text()).toBeTruthy();
  recordKey = ((await createRecord.json()) as { key: string }).key;

  // One field update as the admin, so a revertible audit row exists. Without it the "no revert
  // control" assertion below would pass whether or not anything were gated.
  const update = await apiContext.patch(`/api/v1/records/${recordKey}`, {
    data: { values: { title: "Second title" }, expected_version: 1, force: false },
  });
  expect(update.ok(), await update.text()).toBeTruthy();

  const readerId = await createPrincipal(READER_EMAIL, "member", "E2E Reader");
  writerPrincipalId = await createPrincipal(WRITER_EMAIL, "member", "E2E Writer");
  outsiderPrincipalId = await createPrincipal(OUTSIDER_EMAIL, "member", "E2E Outsider");
  creatorPrincipalId = await createPrincipal(CREATOR_EMAIL, "creator", "E2E Access Creator");

  await grant(readerId, "read");
  await grant(writerPrincipalId, "write");
  await grant(creatorPrincipalId, "admin");
});

test.afterAll(async () => {
  await apiContext.dispose();
});

const READ_BANNER =
  "Read-only. You hold read on Access Probe. Ask an administrator of Access Probe, or a " +
  "system administrator, for write access.";

test.describe("a read-granted member", () => {
  test.beforeEach(async ({ page }) => {
    await signInAs(page, READER_EMAIL, PASSWORD);
  });

  test("sees the table view with no write affordance, and exactly one banner", async ({ page }) => {
    await page.goto(`/${OBJECT_TYPE_KEY}`);
    await expect(page.getByTestId(`row-${recordKey}`)).toBeVisible();

    // The one statement of the caller's level, and only one of it.
    const banner = page.getByTestId("read-only-banner");
    await expect(banner).toHaveCount(1);
    await expect(banner).toHaveText(READ_BANNER);

    await expect(page.getByRole("button", { name: `Edit Title for ${recordKey}` })).toHaveCount(0);
    await expect(page.getByTestId("bulk-toolbar")).toHaveCount(0);
    await expect(page.getByLabel(`Select row ${recordKey}`)).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Import CSV" })).toHaveCount(0);

    /**
     * The title line's `New <type>` primary. Creating a record is a write, so it is
     * gated like everything else here, and the banner above is its one explanation (DD-42).
     *
     * Added to **this** scenario rather than to the new create spec deliberately. This list is the
     * canonical statement of what a reader does not get, and a new write affordance that is not in
     * it is a control this scenario walks straight past while still reporting green — which is the
     * failure mode the list exists to prevent, not a gap the new spec could cover from outside.
     */
    await expect(page.getByRole("button", { name: `New ${TYPE_NAME}` })).toHaveCount(0);

    /**
     * **The View menu is opened before anything inside it is asserted.** `Save as new`,
     * `Set as default`, `Export CSV` and `Saved view` live in one popover on the right of the
     * toolbar, and behind a closed popover the two absence assertions below would pass because
     * the control is **unmounted**, not because access was denied (a vacuous pass on the one spec
     * whose whole subject is denial), while the two presence assertions would fail for a reason
     * that has nothing to do with access.
     */
    await page.getByRole("button", { name: /^Current view/ }).click();
    await expect(page.getByTestId("view-menu")).toBeVisible();

    await expect(page.getByRole("button", { name: "Save as new" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Set as default" })).toHaveCount(0);

    // Reading is untouched: the export and the saved-view selector are `read` routes.
    await expect(page.getByRole("button", { name: "Export CSV" })).toBeVisible();
    await expect(page.getByLabel("Saved view")).toBeVisible();
  });

  test("sees the record detail with no comment, link or revert affordance", async ({ page }) => {
    await page.goto(`/${OBJECT_TYPE_KEY}/${recordKey}`);
    // The record page is titled by its display value, and the key is a mono chip. What this
    // step needs is "we are on this record's page", which the chip says exactly;
    // `record-page.spec.ts` owns the claim about what the title contains.
    await expect(page.getByTestId("record-key-chip")).toHaveText(recordKey);
    await expect(page.getByTestId("read-only-banner")).toHaveCount(1);
    // The composer's two strings are the Activity card's (docs/DESIGN.md 8.3). A reader is
    // offered no way to write.
    await expect(page.getByPlaceholder("Reply. Agents read this too.")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Post" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Revert this change" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Revert record to this version" })).toHaveCount(0);
    // The value is the edit control, so a reader must not be offered one.
    await expect(page.getByRole("button", { name: "Edit Title" })).toHaveCount(0);

    // Comments and the audit timeline are one region. The feed renders for a reader: this is a
    // reader, not a locked door.
    await expect(page.getByRole("region", { name: "Activity" })).toBeVisible();
  });

  test("cannot reach the CSV import wizard by URL, only the link is not enough", async ({ page }) => {
    await page.goto(`/${OBJECT_TYPE_KEY}/import`);

    await expect(page.getByTestId("read-only-banner")).toHaveText(READ_BANNER);
    await expect(page.getByLabel("Upload CSV file")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Run dry-run" })).toHaveCount(0);
  });
});

test.describe("a write-granted member", () => {
  test.beforeEach(async ({ page }) => {
    await signInAs(page, WRITER_EMAIL, PASSWORD);
  });

  test("keeps every record affordance the reader lost", async ({ page }) => {
    await page.goto(`/${OBJECT_TYPE_KEY}`);
    await expect(page.getByTestId(`row-${recordKey}`)).toBeVisible();

    await expect(page.getByTestId("read-only-banner")).toHaveCount(0);
    await expect(page.getByRole("button", { name: `Edit Title for ${recordKey}` })).toBeVisible();
    await expect(page.getByRole("link", { name: "Import CSV" })).toBeVisible();

    // The View menu again, in the direction that matters just as much: a writer's `Save as new`
    // is inside the View menu, so without this click the assertion below would fail for a caller
    // who is allowed everything it is about.
    await page.getByRole("button", { name: /^Current view/ }).click();
    await expect(page.getByRole("button", { name: "Save as new" })).toBeVisible();
  });

  test("may propose a destructive schema change but not edit the schema", async ({ page }) => {
    await page.goto(`/schema/${OBJECT_TYPE_KEY}`);
    await expect(page.getByRole("heading", { name: "Access Probe", level: 1 })).toBeVisible();

    // Asserted explicitly, not by the absence of a hidden control: both go through
    // `propose_schema_change`, which needs only `write` because proposing is not deciding.
    // Gating them at `admin` would hide a control the server accepts.
    await expect(page.getByRole("button", { name: "Delete object type" })).toBeVisible();
    await expect(
      page.getByTestId("field-row-title").getByRole("button", { name: "Delete" }),
    ).toBeVisible();

    await expect(page.getByRole("button", { name: "Add field" })).toHaveCount(0);
    await expect(
      page.getByTestId("field-row-title").getByRole("button", { name: "Edit" }),
    ).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Save" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Permissions" })).toHaveCount(0);

    await expect(page.getByTestId("read-only-banner")).toHaveText(
      "You hold write on Access Probe. Editing this object type and its fields needs admin " +
        "on Access Probe. Ask an administrator of Access Probe, or a system administrator, " +
        "for admin access.",
    );
  });
});

test.describe("a member with no grant", () => {
  test("is told it has no access, not that no object types exist", async ({ page }) => {
    await signInAs(page, OUTSIDER_EMAIL, PASSWORD);
    await page.goto("/");

    await expect(
      page.getByText(
        "You do not have access to any object types yet. Ask an administrator to grant you access.",
      ),
    ).toBeVisible();
    // A member is never handed the setup screen, whose prompt their token would be refused
    // for.
    await expect(
      page.getByRole("heading", { level: 1, name: "What do you want to keep track of?" }),
    ).toHaveCount(0);
  });
});

test.describe("a creator holding admin on the type (DD-11)", () => {
  /**
   * A creator holding admin on one type administers that type's grants. No extra fixture:
   * the `creator` and its `admin` grant on `e2e_access` are seeded in `beforeAll`.
   *
   * This is the one scenario a component test cannot reach. The panel's picker reads
   * `GET /principals/directory` and its labels read the grants document's `principals` sidecar,
   * and both of those are routes a `creator` session really hits here — where `GET
   * /api/v1/principals`, which this principal is genuinely refused, is not mocked into
   * cooperating.
   */
  test("gets the permissions panel, grants an outsider, and sees the row named", async ({
    page,
  }) => {
    await signInAs(page, CREATOR_EMAIL, PASSWORD);
    await page.goto(`/schema/${OBJECT_TYPE_KEY}`);
    await expect(page.getByRole("heading", { name: "Access Probe", level: 1 })).toBeVisible();

    // It holds `admin` on the type, so every schema control is offered and no banner shows...
    await expect(page.getByRole("button", { name: "Add field" })).toBeVisible();
    await expect(page.getByTestId("read-only-banner")).toHaveCount(0);

    // ...and the permissions panel is offered too, on the strength of that level alone. This
    // principal holds the `creator` role, not `admin`, and never could read
    // `GET /api/v1/principals`.
    const panel = page.getByRole("region", { name: "Permissions" });
    await expect(panel.getByLabel("Default access")).toBeVisible();
    // Its own grant row, named from the sidecar rather than printed as a UUID.
    await expect(panel.getByTestId(`grant-${creatorPrincipalId}`)).toContainText(
      "E2E Access Creator",
    );

    // Grant the outsider `read` from the browser, as this principal, over the real routes.
    await panel.getByLabel("Grant access to").selectOption(outsiderPrincipalId);
    await panel.getByLabel("Level to grant").selectOption("read");
    await panel.getByRole("button", { name: "Grant" }).click();

    const row = panel.getByTestId(`grant-${outsiderPrincipalId}`);
    await expect(row).toContainText("E2E Outsider");
    await expect(row.getByLabel("Level for E2E Outsider")).toHaveValue("read");

    // And revoke it again, which also restores the state the "no grant" describe seeds.
    await row.getByRole("button", { name: "Remove" }).click();
    await expect(panel.getByTestId(`grant-${outsiderPrincipalId}`)).toHaveCount(0);
  });
});

test.describe("a system administrator's permissions panel", () => {
  test("changes the default access, grants, denies, and removes", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/schema/${OBJECT_TYPE_KEY}`);
    const panel = page.getByRole("region", { name: "Permissions" });
    await expect(panel.getByLabel("Default access")).toBeVisible();

    // Default access: closed by default (DD-11), raised to `read`.
    await expect(panel.getByLabel("Default access")).toHaveValue("none");
    await panel.getByLabel("Default access").selectOption("read");
    await expect
      .poll(async () => {
        const response = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}/grants`);
        return ((await response.json()) as { default_level: string }).default_level;
      })
      .toBe("read");

    // The reader's grant is already there; move the writer's to `none` and confirm it renders as
    // an explicit deny rather than disappearing: `none` overrides a permissive default.
    const writerLevel = panel.getByLabel("Level for E2E Writer");
    await writerLevel.selectOption("none");
    await expect(writerLevel).toHaveValue("none");
    await expect(panel.getByTestId(`grant-${writerPrincipalId}`)).toContainText("E2E Writer");

    // Remove it entirely, which is a different fact: the principal falls back to the default.
    await panel
      .getByTestId(`grant-${writerPrincipalId}`)
      .getByRole("button", { name: "Remove" })
      .click();
    await expect(panel.getByTestId(`grant-${writerPrincipalId}`)).toHaveCount(0);

    // Restore the state the earlier describes seeded, so this file stays order-independent.
    await grant(writerPrincipalId, "write");
    const reset = await apiContext.patch(`/api/v1/object-types/${OBJECT_TYPE_KEY}`, {
      data: { default_level: "none" },
    });
    expect(reset.ok(), await reset.text()).toBeTruthy();
  });
});

test.describe("a grant revoked mid-session", () => {
  test("renders the backend's forbidden message verbatim and re-derives access", async ({
    page,
  }: {
    page: Page;
  }) => {
    await signInAs(page, WRITER_EMAIL, PASSWORD);
    await page.goto(`/${OBJECT_TYPE_KEY}`);

    // The affordance is shown legitimately: this caller holds `write` at page-load time.
    const cell = page.getByRole("button", { name: `Edit Title for ${recordKey}` });
    await expect(cell).toBeVisible();

    // ...and is then legitimately refused, because the grant moved underneath it.
    await grant(writerPrincipalId, "read");

    await cell.click();
    const input = page.getByLabel(`Title value for ${recordKey}`);
    await input.fill("Third title");
    await input.press("Enter");

    await expect(page.getByRole("alert")).toHaveText(
      "Your access to object type 'e2e_access' is 'read'; this call needs at least 'write'. " +
        "Ask an administrator of 'e2e_access', or a system administrator, to raise it.",
    );

    // The 403 proves this client's `your_access` is stale, so the orientation document is
    // refetched and the affordances go away without a reload.
    await expect(page.getByTestId("read-only-banner")).toHaveText(READ_BANNER);
    await expect(page.getByRole("button", { name: `Edit Title for ${recordKey}` })).toHaveCount(0);

    await grant(writerPrincipalId, "write");
  });
});
