/**
 * The record card's attachment widget, against a real server, a real database and a real blob
 * store, never in jsdom, where the sidecar is whatever a fixture literal says.
 *
 * The claim that needs a real backend is the one the component test can only assert about a
 * request body: `AttachmentService.get_many` genuinely omits an id it cannot resolve, so the
 * card genuinely renders more rows than the sidecar has entries, and a remove genuinely leaves
 * the withheld id on disk. Every record here is seeded through the same REST routes a person's
 * browser uses.
 *
 * Seeded in the **functional** project, never the visual one: a new visual fixture is never
 * free, and a second principal in the visual project would repaint
 * baselines that have nothing to do with attachments. `ui-visual.spec.ts` carries the
 * appearance; this file carries the behaviour, and it never touches the records that file
 * shoots.
 */
import {
  expect,
  request as apiRequestModule,
  test,
  type APIRequestContext,
} from "@playwright/test";
import { E2E_AUTH_HEADER, E2E_BASE_URL, signInAs, signInAsE2eAdmin } from "./constants";

const OBJECT_TYPE_KEY = "e2e_attach";
const READER_EMAIL = "e2e-attach-reader@example.com";
const PASSWORD = "e2e-attach-passw0rd!";

const FILENAME = "vendor-terms.txt";
const FILE_BODY = "Vendor terms, clause by clause.\n";
/** A well-formed id naming no `attachments` row. `fieldtypes.py` validates an attachment value
 * for shape only and `_check_attachment_refs` stores an unresolved id rather than refusing it,
 * so this is a value the product itself produces, and from the client it is indistinguishable
 * from an id the read rule withholds, which is why the placeholder row claims neither. */
const MISSING_ID = "00000000-0000-4000-8000-0000000000ff";

let apiContext: APIRequestContext;
let attachmentId: string;
/** Two stored ids, one resolvable. Only the remove scenario touches it. */
let mixedRecordKey: string;
/** No files: the upload scenario's record. */
let emptyRecordKey: string;
/** One resolvable id: the read-level scenario's record. Nothing ever mutates it. */
let readerRecordKey: string;

async function createRecord(files: string[]): Promise<string> {
  const created = await apiContext.post(`/api/v1/object-types/${OBJECT_TYPE_KEY}/records`, {
    data: { title: "Contract review", files },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  return ((await created.json()) as { key: string }).key;
}

/** The record's stored ids, read back over REST: the record's own truth, not the screen's. */
async function storedFileIds(recordKey: string): Promise<string[]> {
  const response = await apiContext.get(`/api/v1/records/${recordKey}`);
  expect(response.ok(), await response.text()).toBeTruthy();
  const body = (await response.json()) as { data: { files?: string[] } };
  return body.data.files ?? [];
}

test.beforeAll(async () => {
  apiContext = await apiRequestModule.newContext({
    baseURL: E2E_BASE_URL,
    extraHTTPHeaders: E2E_AUTH_HEADER,
  });

  const createType = await apiContext.post("/api/v1/object-types", {
    data: {
      key: OBJECT_TYPE_KEY,
      name: "Attachment Probe",
      name_plural: "Attachment Probes",
      description: "An end-to-end fixture object type for the record card's file widget.",
      key_prefix: "ATCH",
      fields: [
        {
          key: "title",
          name: "Title",
          type: "short_text",
          description: "What this record is about.",
        },
        {
          key: "files",
          name: "Files",
          type: "attachment",
          description: "Supporting documents for this record.",
        },
      ],
    },
  });
  expect(createType.ok(), await createType.text()).toBeTruthy();

  const upload = await apiContext.post("/api/v1/attachments", {
    multipart: {
      file: { name: FILENAME, mimeType: "text/plain", buffer: Buffer.from(FILE_BODY, "utf-8") },
    },
  });
  expect(upload.ok(), await upload.text()).toBeTruthy();
  attachmentId = ((await upload.json()) as { id: string }).id;

  mixedRecordKey = await createRecord([attachmentId, MISSING_ID]);
  emptyRecordKey = await createRecord([]);
  readerRecordKey = await createRecord([attachmentId]);

  const reader = await apiContext.post("/api/v1/principals", {
    data: {
      type: "user",
      display_name: "E2E Attachment Reader",
      email: READER_EMAIL,
      role: "member",
      password: PASSWORD,
    },
  });
  expect(reader.ok(), await reader.text()).toBeTruthy();
  const readerId = ((await reader.json()) as { id: string }).id;

  const grant = await apiContext.put(
    `/api/v1/object-types/${OBJECT_TYPE_KEY}/grants/${readerId}`,
    { data: { level: "read" } },
  );
  expect(grant.ok(), await grant.text()).toBeTruthy();
});

test.afterAll(async () => {
  await apiContext.dispose();
});

test.describe("attachments on the record card", () => {
  test("uploads a file from the card and stores its id on the record", async ({ page }) => {
    await signInAsE2eAdmin(page);
    await page.goto(`/${OBJECT_TYPE_KEY}/${emptyRecordKey}`);
    const widget = page.getByTestId("attachment-field-files");
    await expect(widget.getByText("No files.")).toBeVisible();

    await widget.locator('input[type="file"]').setInputFiles({
      name: "kickoff-notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("Kickoff notes.\n", "utf-8"),
    });

    // The card refetches after the commit, so the filename arrives from the server rather than
    // from an optimistic row: seeing it proves the whole upload-then-`update_record` path ran.
    await expect(widget.getByRole("link", { name: "kickoff-notes.txt" })).toBeVisible();

    const stored = await storedFileIds(emptyRecordKey);
    expect(stored).toHaveLength(1);
  });

  /**
   * The widget's sharpest correctness rule. `get_many` omits the unresolvable id, so
   * the sidecar has one entry while the record holds two; a widget that rebuilt the array from
   * the rendered rows would write `[]` here and destroy an id the user cannot see.
   */
  test("removing a resolved file leaves an unresolvable id on the record", async ({ page }) => {
    expect(await storedFileIds(mixedRecordKey)).toEqual([attachmentId, MISSING_ID]);

    await signInAsE2eAdmin(page);
    await page.goto(`/${OBJECT_TYPE_KEY}/${mixedRecordKey}`);
    const widget = page.getByTestId("attachment-field-files");

    // One row per STORED id, not per resolved one.
    await expect(widget.getByRole("listitem")).toHaveCount(2);
    await expect(
      widget.getByText("Unavailable file (you may not have access, or it was removed)"),
    ).toBeVisible();

    await widget.getByRole("button", { name: `Remove ${FILENAME}` }).click();

    await expect(widget.getByRole("listitem")).toHaveCount(1);
    // The record's own truth, read back over REST rather than inferred from the screen.
    expect(await storedFileIds(mixedRecordKey)).toEqual([MISSING_ID]);
  });

  /** `read` is enough to follow the download; the write controls are simply absent, and the
   * screen's one `ReadOnlyBanner` is what explains their absence. */
  test("a read-level principal downloads but cannot upload or remove", async ({ page }) => {
    await signInAs(page, READER_EMAIL, PASSWORD);
    await page.goto(`/${OBJECT_TYPE_KEY}/${readerRecordKey}`);
    const widget = page.getByTestId("attachment-field-files");

    const link = widget.getByRole("link", { name: FILENAME });
    await expect(link).toBeVisible();
    await expect(widget.locator('input[type="file"]')).toHaveCount(0);
    await expect(widget.getByRole("button", { name: /^Remove/ })).toHaveCount(0);

    // "Working", not merely "present": followed with the browser context's own session cookie,
    // which is exactly what clicking it does.
    const href = await link.getAttribute("href");
    const download = await page.request.get(href as string);
    expect(download.status()).toBe(200);
    expect(download.headers()["content-disposition"]).toContain("attachment");
    expect(await download.text()).toBe(FILE_BODY);

    // The one-banner rule still holds on a screen that carries a widget.
    await expect(page.getByTestId("read-only-banner")).toHaveCount(1);
  });
});
