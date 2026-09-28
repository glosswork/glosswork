/**
 * The attachment widget on the record card. Without it the field falls through to
 * `formatFieldValue` and the card shows a raw id.
 *
 * Rendered through the Details card rather than through `AttachmentField` alone, deliberately.
 * The two rules worth proving are about what reaches the *server*: that a mutation composes its
 * array from `record.data` and not from the resolved sidecar, and that the field
 * is written through `commitCell` and nothing else. Both are claims about the
 * `PATCH` body, so the test asserts the `PATCH` body. `DetailsCard` takes the three props this
 * field needs (`fields`, `record`, `canWrite`).
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { FieldDoc } from "../api/objectTypes";
import type { AttachmentRef, RecordWithIncludes } from "../api/records";
import { renderWithProviders } from "../test/renderWithProviders";
import { DetailsCard } from "./DetailsCard";

const RESOLVED_ID = "4298b530-0e2c-4c8f-9a1a-2b6ad5b0d001";
/** Stored on the record and absent from the sidecar. The client cannot tell whether it names
 * no row or names one the attachment read rule withholds, which is the whole point. */
const UNRESOLVED_ID = "00000000-0000-4000-8000-0000000000ff";
const UPLOADED_ID = "9f1c77aa-51d5-4a2e-8f0b-7c1d2e3f4a5b";

function attachmentField(config: Record<string, unknown> = {}): FieldDoc {
  return {
    key: "files",
    name: "Files",
    type: "attachment",
    description: "Supporting documents.",
    required: false,
    unique: false,
    indexed: false,
    embed: false,
    default: null,
    config,
    position: 0,
    operators: [],
    display_eligible: false,
  };
}

const resolvedRef: AttachmentRef = {
  id: RESOLVED_ID,
  filename: "eval-doc.pdf",
  content_type: "application/pdf",
  byte_size: 20480,
};

function makeRecord(ids: string[], resolved: AttachmentRef[]): RecordWithIncludes {
  return {
    id: "rec-1",
    key: "VDET-001",
    version: 3,
    created_at: "2026-09-01T10:00:00Z",
    created_by: "p1",
    updated_at: "2026-09-01T10:00:00Z",
    updated_by: "p1",
    updated_by_agent_label_id: null,
    deleted_at: null,
    comment_count: 0,
    last_comment_at: null,
    data: { files: ids },
    attachments: { files: resolved },
  };
}

interface CapturedPatch {
  ref: string;
  body: { values: Record<string, unknown>; expected_version: number };
}

let patches: CapturedPatch[] = [];
let uploadedNames: string[] = [];

const server = setupServer(
  http.post("/api/v1/attachments", async ({ request }) => {
    const form = await request.formData();
    const file = form.get("file") as File;
    uploadedNames.push(file.name);
    return HttpResponse.json({
      id: UPLOADED_ID,
      sha256: "0".repeat(64),
      filename: file.name,
      content_type: "text/plain",
      byte_size: 11,
      uploaded_at: "2026-09-05T09:00:00Z",
      uploaded_by: "p1",
    });
  }),
  http.patch("/api/v1/records/:ref", async ({ params, request }) => {
    const body = (await request.json()) as CapturedPatch["body"];
    patches.push({ ref: String(params.ref), body });
    return HttpResponse.json({ key: String(params.ref), version: 4, data: body.values });
  }),
  // `DetailsCard` reads the record's activity for the agent bar regardless of
  // which field is under test; empty pages are enough to satisfy the walk.
  http.get("/api/v1/records/:ref/history", () => HttpResponse.json({ events: [], next_cursor: null })),
  http.get("/api/v1/records/:ref/comments", () => HttpResponse.json({ comments: [], next_cursor: null })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  patches = [];
  uploadedNames = [];
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderField(
  record: RecordWithIncludes,
  { canWrite = true, config = {} }: { canWrite?: boolean; config?: Record<string, unknown> } = {},
) {
  return renderWithProviders(
    <DetailsCard fields={[attachmentField(config)]} record={record} canWrite={canWrite} />,
  );
}

function rows() {
  return within(screen.getByTestId("attachment-field-files")).queryAllByRole("listitem");
}

describe("AttachmentField", () => {
  it("renders a resolved file as a download link with its size", () => {
    renderField(makeRecord([RESOLVED_ID], [resolvedRef]));

    const link = screen.getByRole("link", { name: "eval-doc.pdf" });
    expect(link).toHaveAttribute(
      "href",
      `/api/v1/attachments/${RESOLVED_ID}/download`,
    );
    expect(rows()[0]).toHaveTextContent("20 KB");
  });

  it("gives an unresolved id its own row, so the row count equals the stored id count", () => {
    renderField(makeRecord([RESOLVED_ID, UNRESOLVED_ID], [resolvedRef]));

    // Two ids stored, one resolved by the sidecar, two rows on screen.
    expect(rows()).toHaveLength(2);
    expect(rows()[1]).toHaveTextContent(
      "Unavailable file (you may not have access, or it was removed)",
    );
    // Withheld, not fetched: the placeholder offers no download.
    expect(within(rows()[1]).queryByRole("link")).toBeNull();
  });

  it("removes the resolved file without dropping the unresolved id", async () => {
    const user = userEvent.setup();
    renderField(makeRecord([RESOLVED_ID, UNRESOLVED_ID], [resolvedRef]));

    await user.click(screen.getByRole("button", { name: "Remove eval-doc.pdf" }));

    await waitFor(() => expect(patches).toHaveLength(1));
    // Rebuilt from `record.data`, not from the one row that resolved. Rebuilding from the
    // rendered rows would have written `[]` here and destroyed an id the user cannot see.
    expect(patches[0].body.values).toEqual({ files: [UNRESOLVED_ID] });
    // Through `commitCell`, so `expected_version` rides along.
    expect(patches[0].body.expected_version).toBe(3);
    expect(patches[0].ref).toBe("VDET-001");
  });

  it("uploads the picked file, then commits the appended id array", async () => {
    const user = userEvent.setup();
    renderField(makeRecord([RESOLVED_ID], [resolvedRef]));

    await user.upload(
      screen.getByLabelText("Add a file"),
      new File(["hello world"], "notes.txt", { type: "text/plain" }),
    );

    await waitFor(() => expect(patches).toHaveLength(1));
    expect(uploadedNames).toEqual(["notes.txt"]);
    expect(patches[0].body.values).toEqual({ files: [RESOLVED_ID, UPLOADED_ID] });
  });

  it("below write, keeps the download and offers no upload or remove", () => {
    renderField(makeRecord([RESOLVED_ID], [resolvedRef]), { canWrite: false });

    expect(screen.getByRole("link", { name: "eval-doc.pdf" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Add a file")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
  });

  it("disables the upload control once max_files is reached, and says why", () => {
    renderField(makeRecord([RESOLVED_ID], [resolvedRef]), { config: { max_files: 1 } });

    expect(screen.getByLabelText("Add a file")).toBeDisabled();
    expect(
      screen.getByText("This field holds at most 1 file. Remove one to add another."),
    ).toBeInTheDocument();
  });

  it("leaves the control enabled below max_files", () => {
    renderField(makeRecord([RESOLVED_ID], [resolvedRef]), { config: { max_files: 3 } });

    expect(screen.getByLabelText("Add a file")).toBeEnabled();
  });

  it("shows an empty state, and an enabled control, on a field holding nothing", () => {
    renderField(makeRecord([], []));

    expect(rows()).toHaveLength(0);
    expect(screen.getByText("No files.")).toBeInTheDocument();
    expect(screen.getByLabelText("Add a file")).toBeEnabled();
  });
});
