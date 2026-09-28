/**
 * DD-44. The dialog's own behaviour, in jsdom; the things jsdom cannot prove — that the
 * modal really is modal, and that navigating away while it is open is safe — are in
 * `e2e/table-create.spec.ts` instead, because `test/setup.ts`'s `<dialog>` shim implements neither
 * the top layer nor focus containment.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { renderWithProviders } from "../test/renderWithProviders";
import type { FieldDoc, ObjectTypeDetail } from "../api/objectTypes";
import { NewRecordDialog } from "./NewRecordDialog";

function field(overrides: Partial<FieldDoc> & { key: string; type: string }): FieldDoc {
  return {
    name: overrides.key,
    description: `The ${overrides.key} field.`,
    required: false,
    unique: false,
    indexed: false,
    embed: false,
    default: null,
    config: {},
    position: 0,
    operators: [],
    display_eligible: true,
    ...overrides,
  };
}

const objectType: ObjectTypeDetail = {
  key: "prospect",
  name: "Prospect",
  name_plural: "Prospects",
  description: "A company we might sell to.",
  key_prefix: "PRO",
  record_count: 3,
  field_count: 5,
  your_access: "write",
  display_field_key: null,
  effective_display_field_key: "title",
  system_fields: [],
  fields: [
    field({ key: "title", name: "Title", type: "short_text", required: true }),
    field({
      key: "stage",
      name: "Stage",
      type: "single_select",
      default: "prospecting",
      options: [
        { value: "prospecting", label: "Prospecting", description: "Early." },
        { value: "negotiating", label: "Negotiating", description: "Late." },
      ],
    }),
    field({ key: "amount", name: "Amount", type: "decimal" }),
    field({ key: "owner", name: "Owner", type: "relation", display_eligible: false }),
    field({ key: "files", name: "Files", type: "attachment", display_eligible: false }),
  ],
};

let createBodies: unknown[] = [];
let nextFailure: { status: number; body: Record<string, unknown> } | null = null;

const server = setupServer(
  http.post("/api/v1/object-types/:key/records", async ({ request }) => {
    createBodies.push(await request.json());
    if (nextFailure) {
      const failure = nextFailure;
      nextFailure = null;
      return HttpResponse.json(failure.body, { status: failure.status });
    }
    return HttpResponse.json({
      id: "rec-1",
      key: "PRO-014",
      version: 1,
      created_at: "2026-09-13T09:00:00",
      created_by: "principal-1",
      updated_at: "2026-09-13T09:00:00",
      updated_by: "principal-1",
      updated_by_agent_label_id: null,
      deleted_at: null,
      comment_count: 0,
      last_comment_at: null,
      data: { title: "Acme renewal" },
    });
  }),
  http.get("/api/v1/object-types", () => HttpResponse.json([])),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  createBodies = [];
  nextFailure = null;
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderDialog(overrides: Partial<ObjectTypeDetail> = {}) {
  const onCreated = vi.fn();
  const onCancel = vi.fn();
  renderWithProviders(
    <NewRecordDialog
      objectType={{ ...objectType, ...overrides }}
      onCreated={onCreated}
      onCancel={onCancel}
    />,
  );
  return { onCreated, onCancel };
}

describe("NewRecordDialog: which fields it offers", () => {
  it("offers every field the create route accepts, and neither of the two it refuses", () => {
    renderDialog();

    expect(screen.getByLabelText("Title")).toBeInTheDocument();
    expect(screen.getByLabelText("Stage")).toBeInTheDocument();
    expect(screen.getByLabelText("Amount")).toBeInTheDocument();

    // Not a scope decision: the server refuses a relation value outright, and an attachment value
    // must name ids that only an upload produces.
    expect(screen.queryByLabelText("Owner")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Files")).not.toBeInTheDocument();
  });

  it("says where the fields it cannot offer are filled in", () => {
    renderDialog();

    expect(screen.getByTestId("new-record-deferred-fields")).toHaveTextContent(
      "Linked records and files are added on the record page once it exists.",
    );
  });

  it("says nothing about them when the type has none", () => {
    renderDialog({ fields: objectType.fields.filter((f) => f.type === "short_text") });

    expect(screen.queryByTestId("new-record-deferred-fields")).not.toBeInTheDocument();
  });

  it("starts each field from its own default", () => {
    renderDialog();

    expect(screen.getByLabelText("Stage")).toHaveValue("prospecting");
    expect(screen.getByLabelText("Title")).toHaveValue("");
  });
});

describe("NewRecordDialog: the required-field pre-flight", () => {
  it("reports a missing required field beside it and sends no request at all", async () => {
    const user = userEvent.setup();
    const { onCreated } = renderDialog();

    await user.click(screen.getByRole("button", { name: "Create Prospect" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Title is required.");
    // The half that matters: the refusal happened in front of the person, not behind a 422.
    expect(createBodies).toEqual([]);
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("does not demand a required field that carries a default", async () => {
    // The server applies defaults before it computes which required fields are missing, so a
    // required-and-defaulted field is satisfiable by omission. A pre-flight that refused here
    // would refuse a write the server accepts.
    const user = userEvent.setup();
    renderDialog({
      fields: [
        field({ key: "title", name: "Title", type: "short_text", required: true }),
        field({ key: "stage", name: "Stage", type: "short_text", required: true, default: "draft" }),
      ],
    });

    await user.type(screen.getByLabelText("Title"), "Acme renewal");
    await user.click(screen.getByRole("button", { name: "Create Prospect" }));

    await waitFor(() => expect(createBodies).toHaveLength(1));
  });

  it("clears the complaint once the field is filled and the write goes out", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("button", { name: "Create Prospect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Title is required.");

    await user.type(screen.getByLabelText("Title"), "Acme renewal");
    await user.click(screen.getByRole("button", { name: "Create Prospect" }));

    await waitFor(() => expect(createBodies).toHaveLength(1));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("NewRecordDialog: what it posts", () => {
  it("posts only the fields the person touched", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText("Title"), "Acme renewal");
    await user.click(screen.getByRole("button", { name: "Create Prospect" }));

    await waitFor(() => expect(createBodies).toHaveLength(1));
    // `stage` is untouched and carries a default, so it is omitted and the SERVER applies the
    // default. `amount` is untouched and empty. Neither rides along.
    expect(createBodies[0]).toEqual({ title: "Acme renewal" });
  });

  it("posts the values object itself, not a {values: ...} envelope", async () => {
    // The one thing about this route that is easy to get wrong: it differs from `update_record`.
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText("Title"), "Acme renewal");
    await user.click(screen.getByRole("button", { name: "Create Prospect" }));

    await waitFor(() => expect(createBodies).toHaveLength(1));
    expect(createBodies[0]).not.toHaveProperty("values");
  });

  it("hands the created record back to its caller", async () => {
    const user = userEvent.setup();
    const { onCreated } = renderDialog();

    await user.type(screen.getByLabelText("Title"), "Acme renewal");
    await user.click(screen.getByRole("button", { name: "Create Prospect" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
    expect(onCreated.mock.calls[0][0]).toMatchObject({ key: "PRO-014" });
  });
});

describe("NewRecordDialog: what the server refuses", () => {
  it("puts a field-named refusal beside that field, not in a page-level alert", async () => {
    nextFailure = {
      status: 422,
      body: {
        error: {
          code: "validation_failed",
          message: "Title must be unique; PRO-002 already has this value.",
          details: { field_key: "title" },
        },
      },
    };
    const user = userEvent.setup();
    const { onCreated } = renderDialog();

    await user.type(screen.getByLabelText("Title"), "Acme renewal");
    await user.click(screen.getByRole("button", { name: "Create Prospect" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Title must be unique; PRO-002 already has this value.",
    );
    expect(onCreated).not.toHaveBeenCalled();
  });

  it("renders a refusal that names no field once, at the form level", async () => {
    nextFailure = {
      status: 422,
      body: {
        error: { code: "validation_failed", message: "That record cannot be created.", details: {} },
      },
    };
    const user = userEvent.setup();
    renderDialog();

    await user.type(screen.getByLabelText("Title"), "Acme renewal");
    await user.click(screen.getByRole("button", { name: "Create Prospect" }));

    const alerts = await screen.findAllByRole("alert");
    expect(alerts).toHaveLength(1);
    expect(alerts[0]).toHaveTextContent("That record cannot be created.");
  });
});

describe("NewRecordDialog: dismissal", () => {
  it("does not close from a field's own Escape handler", async () => {
    /**
     * Half of the Escape story, and the only half jsdom can see.
     *
     * `FieldInput` calls `onCancel` on Escape and does not `preventDefault`, while `Dialog` turns
     * the native `cancel` event into the same callback — so wiring both to the close would run it
     * twice. This dialog passes `FieldInput` a no-op, leaving the `<dialog>` element to own the
     * key by itself, and what that looks like from here is a field Escape that closes nothing.
     *
     * The other half — that Escape really does close it, exactly once — is **not provable here**:
     * `test/setup.ts`'s shim implements `close` and no `cancel`, so jsdom fires no cancel event on
     * Escape at all and this assertion would pass against a dialog that had become impossible to
     * dismiss. `e2e/table-create.spec.ts` asserts it against a real engine, which is the same
     * division of labour the shim's own comment describes for focus containment.
     */
    const user = userEvent.setup();
    const { onCancel } = renderDialog();

    await user.click(screen.getByLabelText("Title"));
    await user.keyboard("{Escape}");

    expect(onCancel).not.toHaveBeenCalled();
    expect(screen.getByTestId("new-record-dialog")).toBeInTheDocument();
  });

  it("cancels from the Cancel button without writing anything", async () => {
    const user = userEvent.setup();
    const { onCancel } = renderDialog();

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(createBodies).toEqual([]);
  });
});
