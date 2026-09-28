import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { createQueryClient } from "../app/queryClient";
import type { ObjectTypeDetail } from "../api/objectTypes";
import type { CsvImportError } from "../api/csv";
import { CsvImportWizardPage } from "./CsvImportWizardPage";

const objectType: ObjectTypeDetail = {
  key: "initiative",
  name: "Initiative",
  name_plural: "Initiatives",
  description: "A funded, sponsored workstream.",
  key_prefix: "INIT",
  record_count: 2,
  field_count: 1,
  your_access: "admin",
  display_field_key: null,
  effective_display_field_key: null,
  fields: [
    {
      key: "target_date",
      name: "Target Date",
      type: "date",
      description: "When this initiative should land.",
      required: false,
      unique: false,
      indexed: true,
      embed: false,
      default: null,
      config: {},
      position: 0,
      operators: ["eq", "gt", "lt"],
      display_eligible: true,
    },
  ],
  system_fields: [
    { key: "key", type: "short_text", description: "Human key.", operators: ["eq", "in"] },
  ],
};

interface CapturedImportRequest {
  fileText: string;
  dryRun: string | null;
  mode: string | null;
}

let importRequests: CapturedImportRequest[] = [];
let dryRunErrorQueue: CsvImportError[][] = [];
let dryRunCallIndex = 0;

const server = setupServer(
  http.get("/api/v1/object-types/:key", () => HttpResponse.json(objectType)),
  http.post("/api/v1/object-types/:key/import", async ({ request }) => {
    const formData = await request.formData();
    const file = formData.get("file") as File;
    const fileText = await file.text();
    const dryRun = formData.get("dry_run") as string | null;
    const mode = formData.get("mode") as string | null;
    importRequests.push({ fileText, dryRun, mode });

    if (dryRun === "true") {
      const errors = dryRunErrorQueue[dryRunCallIndex] ?? [];
      dryRunCallIndex += 1;
      return HttpResponse.json({ dry_run: true, created: 0, updated: 0, errors });
    }
    return HttpResponse.json({ dry_run: false, created: 1, updated: 0, errors: [] });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  importRequests = [];
  dryRunCallIndex = 0;
  dryRunErrorQueue = [[{ row: 2, field: "target_date", reason: "Invalid date." }], []];
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderWizard() {
  const queryClient = createQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/initiative/import"]}>
        <Routes>
          <Route path="/:objectTypeKey/import" element={<CsvImportWizardPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function uploadCsv(user: ReturnType<typeof userEvent.setup>, csvText: string) {
  const input = await screen.findByLabelText("Upload CSV file");
  const file = new File([csvText], "data.csv", { type: "text/csv" });
  await user.upload(input, file);
}

describe("CsvImportWizardPage", () => {
  it("pre-selects a mapping suggestion by header name similarity", async () => {
    const user = userEvent.setup();
    renderWizard();

    await uploadCsv(user, "Target Date,key\r\n2026-01-05,INIT-9");

    expect(screen.getByLabelText("Column mapping for Target Date")).toHaveValue("target_date");
    expect(screen.getByLabelText("Column mapping for key")).toHaveValue("key");
  });

  it("sends the rewritten, field-key header to the import route, not the original", async () => {
    const user = userEvent.setup();
    renderWizard();

    await uploadCsv(user, "Target Date,key\r\n2026-01-05,INIT-9");
    await user.click(screen.getByRole("button", { name: "Run dry-run" }));

    await waitFor(() => expect(importRequests).toHaveLength(1));
    const sent = importRequests[0].fileText;
    expect(sent).toContain("target_date,key");
    expect(sent).not.toContain("Target Date");
    expect(importRequests[0].dryRun).toBe("true");
    expect(importRequests[0].mode).toBe("create");
  });

  it("renders one row per per-row validation error from a dry run, and disables Commit", async () => {
    const user = userEvent.setup();
    renderWizard();

    await uploadCsv(user, "Target Date,key\r\n2026-01-05,INIT-9");
    await user.click(screen.getByRole("button", { name: "Run dry-run" }));

    const errorsTable = await screen.findByTestId("dry-run-errors");
    const row = within(errorsTable).getByTestId("dry-run-error-0");
    expect(row).toHaveTextContent("2");
    expect(row).toHaveTextContent("target_date");
    expect(row).toHaveTextContent("Invalid date.");

    expect(screen.getByRole("button", { name: "Commit" })).toBeDisabled();
  });

  it(
    "renders one error row per offending case for a fixture spanning all four documented " +
      "error kinds (missing required field, invalid enum value, malformed date, unresolvable " +
      "relation key), mirroring the backend's bad-CSV dry-run fixture",
    async () => {
      const user = userEvent.setup();
      dryRunErrorQueue = [
        [
          { row: 2, field: "title", reason: "Field 'title' is required." },
          { row: 3, field: "status", reason: "Unknown option 'archived' for field 'status'." },
          { row: 4, field: "target_date", reason: "'not-a-date' is not a valid date." },
          { row: 5, field: "owner", reason: "No record found for key 'INIT-999'." },
        ],
      ];
      renderWizard();

      await uploadCsv(user, "Target Date,key\r\n2026-01-05,INIT-9");
      await user.click(screen.getByRole("button", { name: "Run dry-run" }));

      const errorsTable = await screen.findByTestId("dry-run-errors");
      expect(within(errorsTable).getByTestId("dry-run-error-0")).toHaveTextContent(
        "Field 'title' is required.",
      );
      expect(within(errorsTable).getByTestId("dry-run-error-1")).toHaveTextContent(
        "Unknown option 'archived' for field 'status'.",
      );
      expect(within(errorsTable).getByTestId("dry-run-error-2")).toHaveTextContent(
        "'not-a-date' is not a valid date.",
      );
      expect(within(errorsTable).getByTestId("dry-run-error-3")).toHaveTextContent(
        "No record found for key 'INIT-999'.",
      );
      expect(screen.getByRole("button", { name: "Commit" })).toBeDisabled();
    },
  );

  it(
    "renders the parsed FR-A4 envelope beside the fixed title on a request failure, never the " +
      "raw JSON body",
    async () => {
      const user = userEvent.setup();
      server.use(
        http.post("/api/v1/object-types/:key/import", () =>
          HttpResponse.json(
            {
              error: {
                code: "validation_failed",
                message: "The uploaded file could not be processed.",
                details: {},
              },
            },
            { status: 400 },
          ),
        ),
      );
      renderWizard();

      await uploadCsv(user, "Target Date,key\r\n2026-01-05,INIT-9");
      await user.click(screen.getByRole("button", { name: "Run dry-run" }));

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("The import request failed.");
      expect(alert).toHaveTextContent("validation_failed");
      expect(alert).toHaveTextContent("The uploaded file could not be processed.");
      // The raw envelope text must never reach the screen as an unparsed string.
      expect(alert).not.toHaveTextContent('{"error"');
      expect(screen.queryByText(/"code":"validation_failed"/)).not.toBeInTheDocument();
    },
  );

  it("falls back to the fixed title alone when the error body is not the FR-A4 envelope", async () => {
    const user = userEvent.setup();
    server.use(
      http.post("/api/v1/object-types/:key/import", () =>
        HttpResponse.text("Internal Server Error", { status: 500 }),
      ),
    );
    renderWizard();

    await uploadCsv(user, "Target Date,key\r\n2026-01-05,INIT-9");
    await user.click(screen.getByRole("button", { name: "Run dry-run" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The import request failed.");
    expect(alert).not.toHaveTextContent("Internal Server Error");
  });

  it("enables Commit only after a clean dry run, and commit calls the route without dry_run", async () => {
    const user = userEvent.setup();
    renderWizard();

    await uploadCsv(user, "Target Date,key\r\n2026-01-05,INIT-9");

    // First dry run: errors queued, Commit stays disabled.
    await user.click(screen.getByRole("button", { name: "Run dry-run" }));
    await screen.findByTestId("dry-run-errors");
    expect(screen.getByRole("button", { name: "Commit" })).toBeDisabled();

    // Second dry run (queue now returns no errors): Commit becomes enabled.
    await user.click(screen.getByRole("button", { name: "Run dry-run" }));
    await waitFor(() => expect(screen.queryByTestId("dry-run-errors")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Commit" })).not.toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Commit" }));

    await waitFor(() => expect(importRequests).toHaveLength(3));
    expect(importRequests[2].dryRun).toBe("false");
    expect(await screen.findByText("Import complete: 1 created, 0 updated.")).toBeInTheDocument();
  });
});

/**
 * Hiding the Import CSV link on the table view does not gate the route: a
 * bookmark, a back button, or a typed URL reaches this page directly, and `csv.import_csv` is
 * `write`. So the wizard gates itself.
 */
describe("CsvImportWizardPage: level gating", () => {
  it("renders no wizard at all for a 'read' caller, and says why", async () => {
    server.use(
      http.get("/api/v1/object-types/:key", () =>
        HttpResponse.json({ ...objectType, your_access: "read" }),
      ),
    );
    renderWizard();

    const banner = await screen.findByTestId("read-only-banner");
    expect(banner).toHaveTextContent(
      "Read-only. You hold read on Initiative. Ask an administrator of Initiative, or a " +
        "system administrator, for write access.",
    );

    expect(screen.queryByLabelText("Upload CSV file")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run dry-run" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Commit" })).not.toBeInTheDocument();
  });
});

describe("CsvImportWizardPage: attachment columns", () => {
  const withAttachment: ObjectTypeDetail = {
    ...objectType,
    field_count: 2,
    fields: [
      ...objectType.fields,
      {
        key: "files",
        name: "Files",
        type: "attachment",
        description: "Attachments carried by this record.",
        required: false,
        unique: false,
        indexed: false,
        embed: false,
        default: null,
        config: {},
        position: 1,
        operators: ["is_empty", "is_not_empty"],
        display_eligible: false,
      },
    ],
  };

  beforeEach(() => {
    server.use(http.get("/api/v1/object-types/:key", () => HttpResponse.json(withAttachment)));
  });

  it("offers no attachment field as a mapping target", async () => {
    const user = userEvent.setup();
    renderWizard();

    await uploadCsv(user, "Target Date\r\n2026-01-05");

    const select = await screen.findByLabelText("Column mapping for Target Date");
    const offered = within(select)
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value);
    expect(offered).toContain("target_date");
    expect(offered).not.toContain("files");
  });

  it("does not auto-suggest an attachment field for a column named after it", async () => {
    const user = userEvent.setup();
    renderWizard();

    // Unfiltered, `suggestFieldForHeader` would match this header to the attachment field and
    // pre-select it, so a user who changed nothing would upload a column the backend drops.
    await uploadCsv(user, "Files\r\nsome-attachment-id");

    expect(screen.getByLabelText("Column mapping for Files")).toHaveValue("");
  });
});
