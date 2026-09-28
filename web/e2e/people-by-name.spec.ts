import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
} from "@playwright/test";
import {
  E2E_ADMIN_EMAIL,
  E2E_AUTH_HEADER,
  E2E_BASE_URL,
  E2E_PRINCIPAL_ID,
  signInAsE2eAdmin,
} from "./constants";

/**
 * A `user_ref` field is set from a picker, and the value comes back as a **name** on both
 * surfaces that render one.
 *
 * This is the functional half of the picker's browser coverage, deliberately kept out of the
 * visual project
 * (`ui-visual.spec.ts`): what it proves is a round trip through a real uvicorn process and a real
 * database, not a set of pixels. Three things have to hold together for it to pass, and none of
 * them is provable in jsdom:
 *
 * 1. `GET /api/v1/principals/directory` answers a **browser session** (a `gw_session` cookie, not
 *    a PAT), which is the credential the SPA actually holds. The route is the only one in that
 *    namespace with no `require_role("admin")`, so a bug that reinstated the role gate would be
 *    invisible to a component test and fatal here.
 * 2. The picker submits the principal **id**, so `parseEditedValue` needs no `user_ref` case and
 *    the stored value stays a bare UUID (DD-24).
 * 3. The `principals` sidecar rides back on `get_record` **and** on `query_records`, which are two
 *    different composition sites in `envelopes.py`. The detail card reads the first and the table
 *    cell reads the second through the table's `meta`, so asserting only one would leave half the
 *    wiring untested.
 *
 * The value chosen is the signed-in admin's own principal, whose display name is the fixed literal
 * "E2E Admin" (`constants.ts`), so every assertion below is against deterministic text.
 */

const OBJECT_TYPE_KEY = "e2e_people";
const RECORD_TITLE = "Assign me to someone";
const OWNER_FIELD_NAME = "Owner";
const ADMIN_DISPLAY_NAME = "E2E Admin";

let apiContext: APIRequestContext;
let recordKey: string;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  const createType = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "People fixture",
      name_plural: "People fixtures",
      description:
        "An end-to-end fixture object type carrying a user_ref field, so the picker "
        + "and the principals sidecar can be driven through the real browser.",
      key_prefix: "PPL",
      fields: [
        {
          key: "title",
          name: "Title",
          type: "short_text",
          description: "The fixture record's short display name.",
          required: true,
        },
        {
          key: "owner",
          name: OWNER_FIELD_NAME,
          type: "user_ref",
          description: "Which person is accountable for this fixture record.",
        },
      ],
    },
  });
  expect(createType.ok(), await createType.text()).toBeTruthy();

  // Seeded with **no** owner: the point of the spec is that the browser sets it.
  const created = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
    data: { title: RECORD_TITLE },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  recordKey = ((await created.json()) as { key: string }).key;
});

test.afterAll(async () => {
  await apiContext.dispose();
});

test("a user_ref field is set from the directory picker and renders as a name", async ({
  page,
}) => {
  await signInAsE2eAdmin(page);

  await test.step("the detail card shows the empty placeholder before anything is assigned", async () => {
    await page.goto(`/${OBJECT_TYPE_KEY}/${recordKey}`);
    await expect(page.getByTestId("field-list")).toBeVisible();
    await expect(page.getByTestId("field-value-owner")).toHaveText("—");
  });

  await test.step("the picker is a directory of people, not a UUID text box", async () => {
    await page.getByRole("button", { name: `Edit ${OWNER_FIELD_NAME}` }).click();
    const picker = page.getByRole("combobox", {
      name: `${OWNER_FIELD_NAME} value for ${recordKey}`,
    });
    await expect(picker).toBeVisible();
    // The signed-in admin is in the directory by display name and email. This is the assertion
    // that would fail if the route had kept `require_role("admin")` for a session credential, or
    // if the widget had stayed a plain text input.
    await expect(
      picker.locator("option", { hasText: ADMIN_DISPLAY_NAME }),
    ).toHaveCount(1);
    await expect(picker.locator("option", { hasText: E2E_ADMIN_EMAIL })).toHaveCount(1);

    await picker.selectOption(E2E_PRINCIPAL_ID);
    await page.getByRole("button", { name: "Save" }).click();
  });

  await test.step("the detail card renders the name, and the stored value is still a bare id", async () => {
    // A `user_ref` renders through the attribution primitive (docs/DESIGN.md 6), so the cell
    // carries an avatar beside the name. The pair of assertions below is the point of this step:
    // the NAME renders, and the id does not.
    await expect(page.getByTestId("field-value-owner")).toContainText(ADMIN_DISPLAY_NAME);
    await expect(page.getByTestId("field-value-owner")).not.toContainText(E2E_PRINCIPAL_ID);

    // DD-24's load-bearing claim, checked against the API rather than the screen: the picker
    // submitted an id, so nothing about the stored shape changed.
    const fetched = await apiContext.get(`/api/v1/records/${recordKey}`);
    expect(fetched.ok(), await fetched.text()).toBeTruthy();
    const body = (await fetched.json()) as {
      data: Record<string, unknown>;
      principals: Record<string, { display_name: string }>;
    };
    expect(body.data.owner).toBe(E2E_PRINCIPAL_ID);
    expect(body.principals[E2E_PRINCIPAL_ID].display_name).toBe(ADMIN_DISPLAY_NAME);
  });

  await test.step("the table cell renders the same name, through the query sidecar", async () => {
    await page.goto(`/${OBJECT_TYPE_KEY}`);
    const cell = page.getByRole("button", { name: `Edit ${OWNER_FIELD_NAME} for ${recordKey}` });
    // As in the detail-card step above: the cell carries the avatar's initials too.
    await expect(cell).toContainText(ADMIN_DISPLAY_NAME);
    await expect(cell).not.toContainText(E2E_PRINCIPAL_ID);
  });
});
