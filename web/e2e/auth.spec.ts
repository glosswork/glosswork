import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
} from "@playwright/test";
import {
  E2E_AUTH_HEADER,
  E2E_BASE_URL,
  E2E_PRINCIPAL_ID,
  OIDC_IDENTITY_NAME,
  signInAs,
  signInAsE2eAdmin,
} from "./constants";

/**
 * The browser authenticates through a real login screen against a server-side session cookie,
 * not a build-time bearer token (DD-9). This spec covers what `table-end-to-end.spec.ts` and
 * `schema-editor-end-to-end.spec.ts` do not, though their own `beforeEach` drives the login form
 * too:
 *
 *   - sign in and sign out through the actual login screen, in both modes: a local password
 *     account, and an OIDC identity against `e2e/fake-idp.mjs` (a real local provider process,
 *     not a mock inside this test — see its own doc comment for what it does and does not
 *     validate);
 *   - minting and then revoking a PAT through `/setup`'s "Personal access tokens" card
 *     (`web/src/setup/AccessTokensPanel.tsx`), the plaintext shown exactly once;
 *   - a second, independently-signed-in principal's writes are attributed to *that* principal,
 *     not to whichever credential happens to be in the browser (proving sessions, not shared
 *     bearer state, drive attribution).
 */

const OBJECT_TYPE_KEY = "e2e_auth_probe";

let apiContext: APIRequestContext;
let recordKey = "";

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  const createTypeResponse = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "Auth Probe",
      name_plural: "Auth Probes",
      description: "An end-to-end fixture object type for auth/session scenarios.",
      key_prefix: "AUTHP",
      fields: [
        {
          key: "title",
          name: "Title",
          type: "short_text",
          description: "A minimal field so the object type has something to seed.",
          required: true,
        },
      ],
    },
  });
  expect(createTypeResponse.ok(), await createTypeResponse.text()).toBeTruthy();

  const createRecordResponse = await apiContext.post(
    `/api/v1/object-types/${OBJECT_TYPE_KEY}/records`,
    { data: { title: "Probe record" } },
  );
  expect(createRecordResponse.ok(), await createRecordResponse.text()).toBeTruthy();
  const body = (await createRecordResponse.json()) as { key: string };
  recordKey = body.key;
});

test.afterAll(async () => {
  await apiContext.dispose();
});

test("sign in through the login form and sign out again", async ({ page }) => {
  await signInAsE2eAdmin(page);

  // The header renders the principal through the attribution primitive, so this element
  // carries the avatar's initials as well as the name. The substance is the name reaching the
  // header, which is what is asserted.
  await expect(page.getByTestId("current-principal-name")).toContainText("E2E Admin");
  await expect(page.getByTestId("current-principal-role")).toHaveText("admin");

  await page.getByRole("button", { name: "Sign out" }).click();

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByTestId("login-email")).toBeVisible();
});

test("signs in through the OIDC redirect flow against the local fake IdP", async ({ page }) => {
  // The whole authorization-code + PKCE round trip happens as real browser navigations against
  // real processes (`e2e/fake-idp.mjs` and the app server) — nothing is stubbed inside this test
  // process. `GW_AUTH_MODE=both` (playwright.config.ts) is what makes `/login` render the "Sign
  // in with Okta" button at all (`GET /api/v1/auth/modes` reports `oidc: true`).
  await page.goto("/login");
  await page.getByTestId("login-oidc").click();

  await expect(page.getByTestId("current-principal-name")).toContainText(OIDC_IDENTITY_NAME, {
    timeout: 15_000,
  });
  await expect(page.getByTestId("current-principal-role")).toHaveText("admin");
  // Not `toHaveURL("/")`: the app shell redirects "/" to the first object type as soon as the
  // type list resolves, so an exact-"/" assertion is a race against that navigation and passes
  // or fails on whether the first poll lands before or after it. (Observed with an exact-"/"
  // assertion: this spec failed 5 runs out of 5 in isolation and the full suite roughly one run
  // in three, always with `Received: ".../e2e_auth_probe"`.) What this step
  // actually means is that the OIDC round trip finished *inside* the authenticated app rather
  // than back on /login, which is what is asserted here; the two principal assertions above
  // already establish which identity it finished as.
  await expect(page).not.toHaveURL(/\/login/);

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("mints and then revokes a PAT through /setup, plaintext shown exactly once", async ({
  page,
}) => {
  await signInAsE2eAdmin(page);
  await page.goto("/setup");

  // The mint carries an expiry, because that is the path that once broke. The picked date
  // becomes the end of that day UTC, one instant per date and not one per viewer, so the
  // expected string is a literal rather than a function of this machine's timezone.
  const pickedDate = "2027-01-31";
  const expectedExpiry = "2027-01-31T23:59:59Z";
  let mintBody: unknown = null;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/api/v1/access-tokens")) {
      mintBody = JSON.parse(request.postData() ?? "{}");
    }
  });

  const mintForm = page.getByRole("form", { name: "Mint access token" });
  await mintForm.getByLabel("Name").fill("e2e-minted-token");
  await mintForm.getByLabel("Expires at (optional)").fill(pickedDate);
  await expect(mintForm.getByLabel("Expires at (optional)")).toHaveValue(pickedDate);
  await mintForm.getByRole("button", { name: "Mint token" }).click();

  const dialog = page.getByTestId("minted-token-dialog");
  await expect(dialog).toBeVisible();
  expect((mintBody as { expires_at?: string } | null)?.expires_at).toBe(expectedExpiry);
  const plaintext = page.getByTestId("minted-token-plaintext");
  await expect(plaintext).toBeVisible();
  await expect(plaintext).toContainText("gw_pat_");
  const fullPlaintext = await plaintext.innerText();

  await dialog.getByRole("button", { name: "Done" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId("minted-token-plaintext")).toHaveCount(0);

  // The row the mint just added, found by its name. It legitimately shows an 8-char
  // `token_prefix` starting with the same "gw_pat_" literal (that prefix is what lets a human
  // recognize the token later) — what must never render is the *full* plaintext secret.
  //
  // `tr`, not `li`: the token list is a table.
  const row = page.locator("tr", { hasText: "e2e-minted-token" });
  await expect(row).toBeVisible();
  await expect(row).not.toContainText(fullPlaintext);
  // The round trip the request body cannot see: the expiry was stored and came back. Asserted as
  // "not never" rather than as a date, because the Expires cell renders in the viewer's timezone
  // and a literal date here passes on this laptop and fails elsewhere. Column 5 of Name, Agent,
  // Prefix, Scope, Expires, Last used, Status.
  await expect(row.locator("td").nth(4)).not.toHaveText("never");
  await row.getByRole("button", { name: "Revoke" }).click();
  await row.getByRole("button", { name: "Confirm revoke" }).click();
  // `AccessTokenRow` renders no Revoke control at all once `revoked_at` is set, and its Status
  // cell turns over from "Active" to "Revoked": either is proof the revoke round-tripped.
  await expect(row.getByRole("button", { name: "Revoke" })).toHaveCount(0);
  await expect(row.getByText("Revoked", { exact: true })).toBeVisible();
});

test("a second principal's writes are attributed to that principal, not the E2E admin", async ({
  browser,
}) => {
  const secondEmail = "e2e-second-user@example.com";
  const secondPassword = "second-user-passw0rd!";

  const createUserResponse = await apiContext.post("/api/v1/principals", {
    data: {
      type: "user",
      display_name: "Second E2E User",
      email: secondEmail,
      role: "member",
      password: secondPassword,
    },
  });
  expect(createUserResponse.ok(), await createUserResponse.text()).toBeTruthy();
  const secondPrincipal = (await createUserResponse.json()) as { id: string };
  expect(secondPrincipal.id).not.toBe(E2E_PRINCIPAL_ID);

  // DD-11: object types are closed to everyone but a system administrator when
  // they are created, so a `member` principal needs a grant before it can comment on this
  // record at all. The subject of this test is attribution, not authorization -- without the
  // grant it would fail on `forbidden` and prove nothing about who the write is attributed to.
  const grantResponse = await apiContext.put(
    `/api/v1/object-types/${OBJECT_TYPE_KEY}/grants/${secondPrincipal.id}`,
    { data: { level: "write" } },
  );
  expect(grantResponse.ok(), await grantResponse.text()).toBeTruthy();

  // A separate browser context, not the E2E admin's `page`: two signed-in identities must not
  // share cookies.
  const secondContext = await browser.newContext({ baseURL: E2E_BASE_URL });
  try {
    const secondPage = await secondContext.newPage();
    await signInAs(secondPage, secondEmail, secondPassword);
    await expect(secondPage.getByTestId("current-principal-name")).toContainText(
      "Second E2E User",
    );

    await secondPage.goto(`/${OBJECT_TYPE_KEY}/${recordKey}`);
    // The record page is titled by its display value, and the key is a mono chip. What this
    // step needs is "we are on this record's page", which the chip says exactly;
    // `record-page.spec.ts` owns the claim about what the title contains.
    await expect(secondPage.getByTestId("record-key-chip")).toHaveText(recordKey);

    const commentBody = "A comment from the second principal.";
    // The composer is the Activity card's, and DESIGN.md 8.3 names both strings:
    // placeholder "Reply. Agents read this too.", primary button "Post".
    await secondPage.getByPlaceholder("Reply. Agents read this too.").fill(commentBody);
    await secondPage.getByRole("button", { name: "Post" }).click();
    await expect(secondPage.getByText(commentBody)).toBeVisible();

    // Polled, not read once. "The comment is visible in the browser" is not proof that "the
    // comment is readable over the API": those are two observations, not one. The request-log
    // timeline of a failing run showed the API read landing 1.4ms BEFORE the thread's own
    // refetch, so the visible assertion had resolved against something the POST had not yet
    // finished producing. Reading once passed for a long time because nothing made the window
    // wide enough to lose; a ninth spec file in the same parallel pool and one database opened
    // it.
    //
    // What is asserted: the comment exists and is attributed to the second principal.
    const written = await expect
      .poll(async () => {
        const after = await apiContext.get(`/api/v1/records/${recordKey}?include=comments`);
        expect(after.ok()).toBeTruthy();
        const afterBody = (await after.json()) as {
          comments: { body: string; author_id: string }[];
        };
        return afterBody.comments.find((comment) => comment.body === commentBody) ?? null;
      })
      .not.toBeNull()
      .then(async () => {
        const after = await apiContext.get(`/api/v1/records/${recordKey}?include=comments`);
        const afterBody = (await after.json()) as {
          comments: { body: string; author_id: string }[];
        };
        return afterBody.comments.find((comment) => comment.body === commentBody);
      });

    expect(written?.author_id).toBe(secondPrincipal.id);
    expect(written?.author_id).not.toBe(E2E_PRINCIPAL_ID);
  } finally {
    await secondContext.close();
  }
});
