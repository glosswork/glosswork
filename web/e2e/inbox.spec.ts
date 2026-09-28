import { expect, request as apiRequestModule, test, type APIRequestContext } from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAs, signInAsE2eAdmin } from "./constants";

/**
 * The Inbox approval flow: FR-U9's check that a test approves a proposal and asserts the
 * underlying schema change applied, driven against `/inbox`.
 *
 * Two things about how it is written:
 *
 * **Everything is scoped to a proposal's own id.** The functional project shares one
 * server and one database across spec files, `shell.spec.ts` seeds three proposals it never
 * decides, and Playwright spreads files across workers — so "the first row in the list" is not
 * a thing any spec here may assert. `proposed_at` is second-precision besides, so two proposals
 * raised in the same second have no defined order without the id tiebreak.
 *
 * **The no-request assertion has a positive control.** `expect(seen).toEqual([])` over a
 * filtered list passes identically when the filter is wrong, when the listener was registered
 * too late, and when the page never rendered. An assertion that cannot fail has not been
 * measured, so this one also proves the listener was working.
 */

const OBJECT_TYPE_KEY = "e2e_inbox_probe";
const AGENT_LABEL = "inbox-spec-agent";
const PASSWORD = "correct-horse-battery-staple";
const MEMBER_EMAIL = "e2e-inbox-member@example.com";

let apiContext: APIRequestContext;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  // Idempotent across retries, in two independently gated steps. A retry lands in a fresh worker
  // whose `beforeAll` runs again against the same database, and `key_prefix` is unique per
  // deployment -- so a blind re-POST fails with `validation_failed` and takes every test in the
  // file down with it. Measured, not predicted: that is how this spec once failed. The two steps
  // are gated separately because a retry can arrive with the type already present and the principal
  // not, or the reverse.
  const existingType = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}`);
  if (!existingType.ok()) {
    const createType = await apiContext.post("/api/v1/object-types", {
      data: {
        key: OBJECT_TYPE_KEY,
        name: "Inbox Probe",
        name_plural: "Inbox Probes",
        description: "An end-to-end fixture for the Inbox approval flow.",
        key_prefix: "INBX",
        fields: [
          {
            key: "title",
            name: "Title",
            type: "short_text",
            description: "The display value.",
            required: true,
          },
        ],
      },
    });
    expect(createType.ok(), await createType.text()).toBeTruthy();

    // Two records, so every field this spec adds below has real values to compute an impact
    // over rather than zeroes.
    for (const title of ["Inbox one", "Inbox two"]) {
      const row = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
        data: { title },
      });
      expect(row.ok(), await row.text()).toBeTruthy();
    }
  }

  // This spec's OWN member, created here rather than borrowed from `access-levels.spec.ts`:
  // that file creates its accounts in its own `beforeAll`, and with no `fullyParallel`
  // set Playwright still spreads spec FILES across workers, so this one may run first.
  const createMember = await apiContext.post("/api/v1/principals", {
    data: {
      type: "user",
      display_name: "E2E Inbox Member",
      email: MEMBER_EMAIL,
      role: "member",
      password: PASSWORD,
    },
  });
  expect(
    createMember.ok() || createMember.status() === 409 || createMember.status() === 422,
    await createMember.text(),
  ).toBeTruthy();
});

test.afterAll(async () => {
  await apiContext.dispose();
});

/**
 * Add a field with a unique key, fill it on every record, and propose deleting it — returning
 * the proposal id and the field's display name.
 *
 * **A fresh field per test, not one shared fixture field.** The approval test *approves* its
 * proposal, which
 * really does delete the field; a second test (or a retry of the first) proposing against the
 * same key would then fail with `unknown_field`, and the failure would look like a bug in the
 * screen rather than in the fixture.
 *
 * The proposal carries **`X-Agent-Label`**, which is what puts an agent square on the screen.
 * REST honours that header for a bearer credential (DD-17).
 */
async function proposeFieldRemoval(
  fieldKey: string,
  fieldName: string,
  reason: string,
): Promise<string> {
  const addField = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/fields`, {
    data: {
      key: fieldKey,
      name: fieldName,
      type: "long_text",
      description: "Added by e2e/inbox.spec.ts so it can be proposed away again.",
    },
  });
  expect(addField.ok(), await addField.text()).toBeTruthy();

  // Give it values on every record, so `affected_records` and `non_empty_values` are non-zero
  // and the sample list has something to strike through.
  const query = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/query`, {
    data: { fields: "*" },
  });
  const rows = ((await query.json()) as { records: { key: string }[] }).records;
  for (const [index, row] of rows.entries()) {
    const patch = await apiContext.patch(`/api/v1/records/${row.key}`, {
      data: { values: { [fieldKey]: `${fieldName} note ${index}` }, force: true },
    });
    expect(patch.ok(), await patch.text()).toBeTruthy();
  }

  const response = await apiContext.post("/api/v1/schema-proposals", {
    headers: { "X-Agent-Label": AGENT_LABEL },
    data: {
      change_type: "delete_field",
      object_type: OBJECT_TYPE_KEY,
      field_key: fieldKey,
      reason,
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return ((await response.json()) as { proposal_id: string }).proposal_id;
}

/** A key no other run of this file has used, so a retry never collides with itself. */
function uniqueKey(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}${Math.floor(Math.random() * 1e4)}`;
}

test("the Inbox names the agent and the change, and Approve applies it", async ({ page }) => {
  await signInAsE2eAdmin(page);
  const fieldKey = uniqueKey("notes");
  const proposalId = await proposeFieldRemoval(fieldKey, "Notes", "The team stopped filling this in.");

  // Nothing applied yet.
  const before = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}`);
  const beforeBody = (await before.json()) as { fields: { key: string }[] };
  expect(beforeBody.fields.map((f) => f.key)).toContain(fieldKey);

  await page.goto(`/inbox/${proposalId}`);

  const detail = page.getByTestId(`proposal-detail-${proposalId}`);
  await expect(detail).toBeVisible();

  // The headline sentence, asserted with a locator rather than left to a screenshot: the visual
  // tolerance is looser than a heading's worth of ink (AGENTS.md, Traps).
  await expect(detail.getByTestId("proposal-headline")).toHaveText(
    `${AGENT_LABEL} wants to remove the Notes field from Inbox Probes`,
  );
  await expect(detail.getByTestId("proposal-reason")).toContainText(
    "The team stopped filling this in.",
  );

  // The agent square: `Hand` renders an agent as a square with a two-letter code, and the
  // accessible name says the kind out loud (docs/DESIGN.md 6.1, 10).
  await expect(detail.getByLabel(`${AGENT_LABEL} (agent)`).first()).toBeVisible();

  // The sample count is compared against the API's own answer read in THIS run, never against a
  // constant, so the assertion still holds when the fixture changes.
  const listed = await apiContext.get(
    `/api/v1/schema-proposals?status=pending&limit=${200}`,
  );
  const listedBody = (await listed.json()) as {
    proposals: { id: string; impact: Record<string, number> }[];
  };
  const impact = listedBody.proposals.find((p) => p.id === proposalId)?.impact;
  expect(impact, "the seeded proposal must be in the pending list").toBeTruthy();
  await expect(detail.getByTestId("proposal-sample-count")).toContainText(
    `of ${impact!.non_empty_values} values shown`,
  );
  await expect(detail.getByTestId("impact-affected")).toContainText(
    String(impact!.affected_records),
  );

  await detail.getByRole("button", { name: "Approve" }).click();

  // Applied: the field is gone from the schema.
  await expect
    .poll(async () => {
      const after = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}`);
      const body = (await after.json()) as { fields: { key: string }[] };
      return body.fields.map((f) => f.key);
    })
    .not.toContain(fieldKey);

  const decided = await apiContext.get(`/api/v1/schema-proposals/${proposalId}`);
  expect(((await decided.json()) as { status: string }).status).toBe("approved");
});

test("the sidebar badge follows the decision without a reload", async ({ page }) => {
  // The badge and the list are deliberately one cache entry, kept in step by writing through
  // on a decision.
  await signInAsE2eAdmin(page);
  const proposalId = await proposeFieldRemoval(
    uniqueKey("badge"),
    "Badge probe",
    "Second probe, declined by this spec.",
  );

  await page.goto("/inbox");
  const badge = page.getByTestId("inbox-count");

  // The badge is read against ITSELF, not against a live `total_count` fetched at assertion
  // time. Doing the latter was flaky on its very first full-suite run: expected "2", received
  // "1". The functional project shares one database and Playwright runs
  // spec files in parallel, so between the decision and the assertion another spec can seed a
  // proposal and move the server's count -- and the badge, which is served from a cache this
  // page writes through rather than refetched, correctly does not follow it.
  //
  // So the number still comes from the run and never from a constant (docs/changes/README.md),
  // but the invariant asserted is the one this test is actually about: deciding a proposal
  // decrements the badge **without a reload**, which is the write-through.
  const before = Number((await badge.textContent())?.trim());
  expect(before, "the seeded proposal should be counted").toBeGreaterThan(0);

  await page.getByTestId(`proposal-${proposalId}`).click();
  await page
    .getByTestId(`proposal-detail-${proposalId}`)
    .getByRole("button", { name: "Decline" })
    .click();

  await expect(badge).toHaveText(String(before - 1));

  // And the decision really reached the server, so the badge is not merely lying faster.
  const decided = await apiContext.get(`/api/v1/schema-proposals/${proposalId}`);
  expect(((await decided.json()) as { status: string }).status).toBe("rejected");
});

test("a member sees the page and one sentence, and fires no request for proposals", async ({
  page,
}) => {
  // The listener is installed BEFORE the navigation, because a listener registered after the
  // page has loaded records nothing and an empty list then proves nothing.
  const seen: string[] = [];
  page.on("request", (request) => seen.push(request.url()));

  await signInAs(page, MEMBER_EMAIL, PASSWORD);
  await page.goto("/inbox");

  // The settle point: something on the page has actually rendered, so the assertions below are
  // not merely racing an empty document.
  const banner = page.getByTestId("inbox-access-banner");
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("Schema proposals are shown to administrators.");

  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Decline" })).toHaveCount(0);

  // The positive control: the listener demonstrably recorded requests this page did make.
  expect(seen.some((url) => url.includes("/api/v1/me"))).toBeTruthy();
  // And none of them was the one the credential would have been refused.
  expect(seen.filter((url) => url.includes("/api/v1/schema-proposals"))).toEqual([]);
});

test("the Inbox is reachable below the breakpoint, and Back returns to the list", async ({
  page,
}) => {
  // docs/DESIGN.md 9: "Inbox becomes list then detail" below 960px. This is the assertion that
  // makes the selection a route rather than component state: with in-memory state, Back leaves
  // the Inbox entirely.
  await signInAsE2eAdmin(page);
  const proposalId = await proposeFieldRemoval(
    uniqueKey("narrow"),
    "Narrow probe",
    "Narrow-viewport probe, left pending by this spec.",
  );

  await page.setViewportSize({ width: 800, height: 800 });
  await page.goto("/inbox");

  await page.getByTestId(`proposal-${proposalId}`).click();
  await expect(page.getByTestId(`proposal-detail-${proposalId}`)).toBeVisible();
  await expect(page.getByTestId("inbox-list")).toHaveCount(0);

  await page.goBack();
  await expect(page.getByTestId("inbox-list")).toBeVisible();

  // Left pending deliberately: this spec asserts navigation, not a decision.
  const still = await apiContext.get(`/api/v1/schema-proposals/${proposalId}`);
  expect(((await still.json()) as { status: string }).status).toBe("pending");
});
