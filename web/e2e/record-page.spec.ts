import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAsE2eAdmin } from "./constants";

/**
 * The record page is about a thing, every field's gloss is reachable from it, and the two cards
 * sit beside each other.
 *
 * **This is a functional spec, and that is the whole point of where it lives.** The
 * obvious home for these assertions is `ui-visual.spec.ts`, which already seeds a rich record
 * fixture — but `playwright.config.ts` splits the two projects and `.github/workflows/ci.yml`
 * runs `--project=e2e` only, so anything seeded against `vis_detail` is macOS-only and invisible to
 * every pipeline. The record page's two main claims would then be checked by nobody but the
 * person who wrote them, on their own laptop.
 *
 * It therefore seeds its own type. That costs one more fixture type in the functional project's
 * database and buys assertions that run on every push.
 *
 * **Every count below comes from the API in the same run**, never from a literal. A criterion
 * that hard-codes "five fields" stops meaning anything the moment somebody edits the fixture,
 * and the failure it then produces is about the constant rather than about the product.
 */

const OBJECT_TYPE_KEY = "e2e_record_page";
/** The relation field's target. It exists so the gloss test is exercised against a type that
 * HAS a relation field: every field gets a row and a `?`, including relations, and a fixture
 * with no relation field cannot tell that apart from "non-relation fields only". */
const TARGET_TYPE_KEY = "e2e_record_page_target";
const DISPLAY_VALUE = "Northwind Traders";
const TITLE_FIELD_KEY = "company";

interface FieldSpec {
  key: string;
  name: string;
  type: string;
  description: string;
}

interface ObjectTypeBody {
  fields: FieldSpec[];
  effective_display_field_key: string | null;
}

interface RecordBody {
  key: string;
  data: Record<string, unknown>;
}

/** `services/records.py::list_link_summaries`, an entry in `GET .../records/{ref}?include=links`'s
 * `links.<field_key>` array: `{key, id, display}` for a readable target, or `{redacted: true}` for
 * one the caller cannot read (envelopes.py's `record_with_includes_doc`). */
interface LinkSummary {
  key?: string;
  id?: string;
  display?: unknown;
  redacted?: boolean;
}

interface RecordWithLinksBody {
  links?: Record<string, LinkSummary[]>;
}

/** Deterministic: this type's own `key_prefix` sequence, and it holds exactly one record. There
 * is no GET route that lists a type's records, so the record's own existence is what a retried
 * worker checks — the same idempotence gate `heading-outline.spec.ts` uses. */
const RECORD_KEY = "RPG-001";

let apiContext: APIRequestContext;
let recordKey: string;
let objectType: ObjectTypeBody;

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  // Idempotent: `playwright.config.ts` retries a worker death once and the retry lands in the
  // same data directory, where a second POST fails the `key_prefix` uniqueness rule. Measured
  // the hard way: a run of this spec once reported `key_prefix 'RPG' is already used` on retry,
  // which is a seeding failure wearing the costume of a product failure.
  const existing = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}`);
  if (existing.ok()) {
    recordKey = RECORD_KEY;
    objectType = (await existing.json()) as ObjectTypeBody;
    return;
  }

  const createTarget = await apiContext.post("/api/v1/object-types", {
    data: {
      key: TARGET_TYPE_KEY,
      name: "Record page target",
      name_plural: "Record page targets",
      description: "An end-to-end fixture: the far end of the relation field below.",
      key_prefix: "RPT",
      fields: [
        {
          key: "title",
          name: "Title",
          type: "short_text",
          description: "The target record's short display name.",
        },
      ],
    },
  });
  expect(createTarget.ok(), await createTarget.text()).toBeTruthy();

  // Seeded here, between `createTarget` and `createType`, so the
  // idempotence gate above (`existing.ok()`) implies these two exist on a retried worker too.
  // Seeding them after `createType` would leave a retry with a source type but no targets, since
  // the gate returns early and this whole block never runs again.
  const createAcme = await apiContext.post(`/api/v1/object-types/${TARGET_TYPE_KEY}/records`, {
    data: { title: "Acme Rollout" },
  });
  expect(createAcme.ok(), await createAcme.text()).toBeTruthy();

  const createBorealis = await apiContext.post(`/api/v1/object-types/${TARGET_TYPE_KEY}/records`, {
    data: { title: "Borealis Audit" },
  });
  expect(createBorealis.ok(), await createBorealis.text()).toBeTruthy();

  const createType = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "Record page fixture",
      name_plural: "Record page fixtures",
      description: "An end-to-end fixture: a record page with a chosen display field.",
      key_prefix: "RPG",
      // Chosen, not guessed (DD-23). The title assertion below is only meaningful because this
      // names a field that is NOT the first one by position — a page that titled itself from
      // `fields[0]` would pass a weaker version of the same test.
      display_field_key: TITLE_FIELD_KEY,
      fields: [
        {
          key: "reference",
          name: "Reference",
          type: "short_text",
          description: "An internal reference number, first by position and not the title.",
        },
        {
          key: TITLE_FIELD_KEY,
          name: "Company",
          type: "short_text",
          description: "The company's legal name, as it appears on the contract.",
        },
        {
          key: "stage",
          name: "Stage",
          type: "single_select",
          description: "Where the deal stands in the pipeline right now.",
          config: {
            options: [
              { value: "proposal_sent", label: "Proposal sent", description: "Waiting on them." },
              { value: "negotiating", label: "Negotiating", description: "Talking terms." },
            ],
          },
        },
        {
          key: "notes",
          name: "Notes",
          type: "long_text",
          description: "Anything worth remembering about this account.",
        },
        {
          key: "engagements",
          name: "Engagements",
          type: "relation",
          description: "The pieces of work this prospect has in flight.",
          config: {
            target_type_key: TARGET_TYPE_KEY,
            cardinality: "many",
            inverse_field_key: "prospects",
          },
        },
      ],
    },
  });
  expect(createType.ok(), await createType.text()).toBeTruthy();

  const created = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
    data: { reference: "REF-4417", company: DISPLAY_VALUE, stage: "proposal_sent", notes: "Hi." },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  recordKey = ((await created.json()) as RecordBody).key;
  expect(recordKey).toBe(RECORD_KEY);

  const fetchedType = await apiContext.get(`/api/v1/object-types/${OBJECT_TYPE_KEY}`);
  expect(fetchedType.ok(), await fetchedType.text()).toBeTruthy();
  objectType = (await fetchedType.json()) as ObjectTypeBody;
});

test.afterAll(async () => {
  await apiContext.dispose();
});

test("the page is titled by the display value, not by the record key", async ({
  page,
}) => {
  await signInAsE2eAdmin(page);
  await page.goto(`/${OBJECT_TYPE_KEY}/${recordKey}`);

  const fetched = await apiContext.get(`/api/v1/records/${recordKey}`);
  expect(fetched.ok(), await fetched.text()).toBeTruthy();
  const record = (await fetched.json()) as RecordBody;

  // Read from the run: the API says which field is the display field and what its value is.
  const displayKey = objectType.effective_display_field_key;
  expect(displayKey, "the fixture pins a display field, so the API must name one").not.toBeNull();
  const expectedTitle = record.data[displayKey as string];
  expect(typeof expectedTitle).toBe("string");

  const heading = page.getByRole("heading", { level: 1 });
  // A locator assertion, not a screenshot: `toHaveScreenshot` runs at a tolerance that hides a
  // whole heading's worth of ink, which is how a renamed product survived 38 green baselines.
  await expect(heading).toHaveText(expectedTitle as string);
  await expect(heading).not.toHaveText(recordKey);

  // The key does not vanish; it stops being the title. 8.3 puts it in a mono chip.
  await expect(page.getByTestId("record-key-chip")).toHaveText(recordKey);
});

test("every field's description is reachable from the page", async ({ page }) => {
  await signInAsE2eAdmin(page);
  await page.goto(`/${OBJECT_TYPE_KEY}/${recordKey}`);

  const fields = objectType.fields;
  // Non-vacuity. A run that found no fields would otherwise satisfy every assertion below by
  // iterating nothing, which is a shape other specs in this suite have had to escape.
  expect(fields.length).toBeGreaterThan(0);
  // And the count includes relation fields, so this asserts something a "non-relation fields
  // only" reading would not. A fixture with no relation field could not tell the two readings
  // apart.
  expect(fields.filter((field) => field.type === "relation").length).toBeGreaterThan(0);

  const toggles = page.getByTestId(/^gloss-toggle-/);
  await expect(toggles).toHaveCount(fields.length);

  for (const field of fields) {
    const toggle = page.getByTestId(`gloss-toggle-${field.key}`);
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await toggle.click();
    // Read in one call rather than looping over `.all()` handles: this card re-renders on a
    // toggle, and a stale handle stops resolving mid-loop and times out the test instead of
    // failing the assertion it is about.
    await expect(page.getByTestId(`gloss-panel-${field.key}`)).toHaveText(field.description);
    await toggle.click();
  }
});

test("the composer says agents read this too", async ({ page }) => {
  await signInAsE2eAdmin(page);
  await page.goto(`/${OBJECT_TYPE_KEY}/${recordKey}`);

  const activity = page.getByRole("region", { name: "Activity" });
  await expect(activity.getByPlaceholder("Reply. Agents read this too.")).toBeVisible();
  await expect(activity.getByRole("button", { name: "Post" })).toBeVisible();
});

test("the two cards sit side by side, and stack Activity-first when narrow", async ({
  page,
}) => {
  await signInAsE2eAdmin(page);

  await test.step("side by side at 1280px", async () => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto(`/${OBJECT_TYPE_KEY}/${recordKey}`);

    // Geometry is a Playwright assertion or it is not proven: `getBoundingClientRect` returns
    // zeroes in jsdom, so no component test can make this claim.
    const details = await page.getByRole("region", { name: "Details" }).boundingBox();
    const activity = await page.getByRole("region", { name: "Activity" }).boundingBox();
    expect(details).not.toBeNull();
    expect(activity).not.toBeNull();
    expect(details!.x).not.toBe(activity!.x);
    // Overlapping vertically is what "beside" means; equal `y` would be a stronger claim than
    // the layout makes, since the two cards are different heights.
    expect(details!.y).toBeLessThan(activity!.y + activity!.height);
    expect(activity!.y).toBeLessThan(details!.y + details!.height);
  });

  await test.step("stacked, Activity first, below the shell breakpoint", async () => {
    await page.setViewportSize({ width: 900, height: 900 });
    await page.goto(`/${OBJECT_TYPE_KEY}/${recordKey}`);

    const details = await page.getByRole("region", { name: "Details" }).boundingBox();
    const activity = await page.getByRole("region", { name: "Activity" }).boundingBox();
    expect(details).not.toBeNull();
    expect(activity).not.toBeNull();
    expect(details!.x).toBe(activity!.x);
    expect(activity!.y).toBeLessThan(details!.y);
  });
});

test("the relation picker finds a target by a lower-case search and links it", async ({
  page,
}) => {
  // The target's key comes from the API in this run, never a hardcoded RPT-001.
  const targetQuery = await apiContext.post(`/api/v1/object-types/${TARGET_TYPE_KEY}/query`, {
    data: { filter: { field: "title", op: "eq", value: "Acme Rollout" } },
  });
  expect(targetQuery.ok(), await targetQuery.text()).toBeTruthy();
  const targetResult = (await targetQuery.json()) as { records: RecordBody[] };
  expect(targetResult.records).toHaveLength(1);
  const targetKey = targetResult.records[0].key;

  // The target type's `name_plural` names the search input; read it rather than assuming it.
  const targetType = await apiContext.get(`/api/v1/object-types/${TARGET_TYPE_KEY}`);
  expect(targetType.ok(), await targetType.text()).toBeTruthy();
  const { name_plural: targetNamePlural } = (await targetType.json()) as { name_plural: string };

  // Idempotent for a retried worker. Unlinking a link that does not exist is a 404
  // (services/records.py:1138-1140), so this only unlinks when the read says it is already
  // there — never unconditionally.
  const before = await apiContext.get(`/api/v1/records/${recordKey}?include=links`);
  expect(before.ok(), await before.text()).toBeTruthy();
  const beforeBody = (await before.json()) as RecordWithLinksBody;
  const alreadyLinked = (beforeBody.links?.engagements ?? []).some(
    (entry) => entry.key === targetKey,
  );
  if (alreadyLinked) {
    const unlink = await apiContext.delete(`/api/v1/records/${recordKey}/links/engagements`, {
      data: { to_records: [targetKey] },
    });
    expect(unlink.ok(), await unlink.text()).toBeTruthy();
  }

  await signInAsE2eAdmin(page);
  await page.goto(`/${OBJECT_TYPE_KEY}/${recordKey}`);

  const engagementsField = page.getByTestId("relation-field-engagements");
  // The relation picker: a trigger named "Add another" for a `many` field opens the search
  // panel.
  await engagementsField.getByRole("button", { name: "Add another" }).click();

  // Lower case on purpose: the title is "Acme Rollout", and SQLite `LIKE` folds ASCII case.
  // This proves it through the real API, not the compiler's own unit tests.
  await page.getByLabel(`Search ${targetNamePlural} by title`).fill("acme");

  // Borealis leaving the list proves the filtered result actually arrived (an unfiltered list
  // could still be on screen via `keepPreviousData`), and Acme still being there next proves
  // "acme" matched "Acme Rollout" through the real API's case-folded `LIKE`, not an unfiltered list.
  await expect(page.getByRole("option", { name: /Borealis Audit$/ })).toHaveCount(0);

  await expect(page.getByRole("option", { name: `${targetKey} Acme Rollout` })).toBeVisible();
  await page.getByRole("option", { name: `${targetKey} Acme Rollout` }).click();

  await expect(
    engagementsField.getByRole("link", { name: `${targetKey} Acme Rollout` }),
  ).toBeVisible();

  const after = await apiContext.get(`/api/v1/records/${recordKey}?include=links`);
  expect(after.ok(), await after.text()).toBeTruthy();
  const afterBody = (await after.json()) as RecordWithLinksBody;
  const linked = (afterBody.links?.engagements ?? []).some((entry) => entry.key === targetKey);
  expect(linked).toBeTruthy();
});
