import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { DEFAULT_TEST_PRINCIPAL, renderWithProviders } from "../test/renderWithProviders";
import type { CurrentPrincipal } from "../api/auth";
import type { GrantDoc, ObjectTypeGrants } from "../api/grants";
import type { PrincipalDoc } from "../api/principals";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { ObjectTypeDetail, FieldDoc } from "../api/objectTypes";
import { SchemaEditorPage } from "./SchemaEditorPage";

const pointsField: FieldDoc = {
  key: "points",
  name: "Points",
  type: "integer",
  description: "Effort estimate in story points.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 0,
  operators: ["eq", "gt", "lt"],
  display_eligible: true,
};

/** The three types that cannot be a display field, so the select's options can be asserted as an
 * exclusion rather than as a coincidence of the fixture having only one field. */
function ineligibleField(key: string, type: string): FieldDoc {
  return {
    key,
    name: key.replace("_", " ").replace(/^./, (c) => c.toUpperCase()),
    type,
    description: `The ${key} field.`,
    required: false,
    unique: false,
    indexed: false,
    embed: false,
    default: null,
    config: {},
    position: 1,
    operators: [],
    display_eligible: false,
  };
}

const labelField: FieldDoc = {
  key: "label",
  name: "Label",
  type: "short_text",
  description: "A human-readable name for the artifact.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 4,
  operators: ["eq"],
  display_eligible: true,
};

function objectTypeFixture(overrides: Partial<ObjectTypeDetail> = {}): ObjectTypeDetail {
  return {
    key: "artifact",
    name: "Artifact",
    name_plural: "Artifacts",
    description: "A tracked work artifact.",
    key_prefix: "ART",
    record_count: 3,
    field_count: 1,
    your_access: "admin",
    display_field_key: null,
    effective_display_field_key: "points",
    fields: [pointsField],
    system_fields: [],
    ...overrides,
  };
}

let currentObjectType = objectTypeFixture();
let lastFieldUpdateBody: unknown = null;
let lastProposalBody: { change_type: string; object_type: string; field_key?: string } | null =
  null;
let addFieldCallCount = 0;
let lastAddFieldBody: Record<string, unknown> | null = null;

const ADMIN_PRINCIPAL_ID = DEFAULT_TEST_PRINCIPAL.id;
const OTHER_PRINCIPAL_ID = "00000000-0000-4000-8000-000000000002";

const directory: PrincipalDoc[] = [
  {
    id: ADMIN_PRINCIPAL_ID,
    type: "user",
    display_name: "Test Admin",
    email: "test-admin@example.com",
    role: "admin",
    auth_provider: "local",
    external_id: null,
    is_active: true,
    description: null,
    created_at: "2026-08-01T10:00:00",
    created_by: null,
  },
  {
    id: OTHER_PRINCIPAL_ID,
    type: "user",
    display_name: "Other User",
    email: "other-user@example.com",
    role: "member",
    auth_provider: "local",
    external_id: null,
    is_active: true,
    description: null,
    created_at: "2026-08-02T10:00:00",
    created_by: ADMIN_PRINCIPAL_ID,
  },
];

/** The grants document's principals sidecar. `DEPARTED_PRINCIPAL_ID` is
 * deliberately **not** in `directory`: it is the row the picker cannot see and the label must
 * still name. */
const DEPARTED_PRINCIPAL_ID = "00000000-0000-4000-8000-000000000003";

const grantSidecar: ObjectTypeGrants["principals"] = {
  [ADMIN_PRINCIPAL_ID]: {
    display_name: "Test Admin",
    email: "test-admin@example.com",
    is_active: true,
    type: "user",
  },
  [OTHER_PRINCIPAL_ID]: {
    display_name: "Other User",
    email: "other-user@example.com",
    is_active: true,
    type: "user",
  },
  [DEPARTED_PRINCIPAL_ID]: {
    display_name: "Dana Departed",
    email: "dana@example.com",
    is_active: false,
    type: "user",
  },
};

function grantRow(principalId: string, level: GrantDoc["level"]): GrantDoc {
  return {
    object_type_id: "type-artifact",
    principal_id: principalId,
    level,
    created_at: "2026-08-01T10:00:00",
    created_by: ADMIN_PRINCIPAL_ID,
    updated_at: null,
    updated_by: null,
  };
}

/** `create_object_type` inserts an `admin` grant for the creating principal in the same
 * transaction, so a freshly created type has exactly one row. That is the shape here. */
let grantsStore: ObjectTypeGrants;
let putGrants: { principalId: string; level: string }[] = [];
let deletedGrants: string[] = [];
let patchedTypes: Record<string, unknown>[] = [];

const server = setupServer(
  http.get("/api/v1/object-types/:key/grants", () => HttpResponse.json(grantsStore)),
  http.put("/api/v1/object-types/:key/grants/:principalId", async ({ params, request }) => {
    const body = (await request.json()) as { level: GrantDoc["level"] };
    const principalId = params.principalId as string;
    putGrants.push({ principalId, level: body.level });
    grantsStore = {
      ...grantsStore,
      grants: [
        ...grantsStore.grants.filter((g) => g.principal_id !== principalId),
        grantRow(principalId, body.level),
      ],
    };
    return HttpResponse.json(grantRow(principalId, body.level));
  }),
  http.delete("/api/v1/object-types/:key/grants/:principalId", ({ params }) => {
    const principalId = params.principalId as string;
    deletedGrants.push(principalId);
    grantsStore = {
      ...grantsStore,
      grants: grantsStore.grants.filter((g) => g.principal_id !== principalId),
    };
    return HttpResponse.json({ status: "revoked" });
  }),
  http.get("/api/v1/principals", () => HttpResponse.json({ principals: directory })),
  // The picker's source. Active principals only, which is the whole point of it being a
  // different read from the row labels' sidecar.
  http.get("/api/v1/principals/directory", () =>
    HttpResponse.json({
      principals: directory
        .filter((p) => p.is_active)
        .map(({ id, display_name, email, type, is_active }) => ({
          id,
          display_name,
          email,
          type,
          is_active,
        })),
    }),
  ),
  http.get("/api/v1/object-types", () => HttpResponse.json([currentObjectType])),
  http.get("/api/v1/object-types/:key", () => HttpResponse.json(currentObjectType)),
  http.patch("/api/v1/object-types/:key", async ({ request }) => {
    const changes = (await request.json()) as Record<string, unknown>;
    patchedTypes.push(changes);
    if (typeof changes.default_level === "string") {
      grantsStore = { ...grantsStore, default_level: changes.default_level as GrantDoc["level"] };
    }
    currentObjectType = { ...currentObjectType, ...changes };
    return HttpResponse.json(currentObjectType);
  }),
  http.patch("/api/v1/object-types/:key/fields/:fieldKey", async ({ request, params }) => {
    const body = (await request.json()) as { changes: Record<string, unknown> };
    lastFieldUpdateBody = body;
    // A `type` change is destructive: return a pending proposal, never apply it.
    if ("type" in body.changes) {
      return HttpResponse.json({
        status: "pending_human_approval",
        proposal_id: "prop-1",
        change_type: "change_field_type",
        impact: {
          change_type: "change_field_type",
          affected_records: 3,
          non_empty_values: 2,
          sample_values: [1, 2],
          coercion_failures: [{ record_key: "ART-002", value: 1, reason: "not coercible" }],
        },
        message: "Change is destructive (change_field_type) and requires human approval.",
      });
    }
    // Additive: applies immediately.
    const updatedField = { ...pointsField, ...body.changes };
    currentObjectType = {
      ...currentObjectType,
      fields: currentObjectType.fields.map((f) =>
        f.key === params.fieldKey ? updatedField : f,
      ),
    };
    return HttpResponse.json({
      status: "applied",
      field: updatedField,
      message: "Change was additive and has been applied immediately.",
    });
  }),
  http.post("/api/v1/object-types/:key/fields", async ({ request }) => {
    addFieldCallCount += 1;
    const body = (await request.json()) as FieldDoc;
    lastAddFieldBody = body as unknown as Record<string, unknown>;
    return HttpResponse.json({ status: "applied", field: { ...body, position: 1, operators: [] } });
  }),
  http.post("/api/v1/schema-proposals", async ({ request }) => {
    const body = (await request.json()) as {
      change_type: string;
      object_type: string;
      field_key?: string;
    };
    lastProposalBody = body;
    if (body.change_type === "delete_object_type") {
      return HttpResponse.json({
        status: "pending_human_approval",
        proposal_id: "prop-delete-type",
        change_type: "delete_object_type",
        impact: { change_type: "delete_object_type", affected_records: 3, sample_values: ["ART-001"] },
        message: "Change is destructive (delete_object_type) and requires human approval.",
      });
    }
    return HttpResponse.json({
      status: "pending_human_approval",
      proposal_id: "prop-delete-field",
      change_type: "delete_field",
      impact: { change_type: "delete_field", affected_records: 2, non_empty_values: 2, sample_values: [1, 2] },
      message: "Change is destructive (delete_field) and requires human approval.",
    });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  grantsStore = {
    object_type: "artifact",
    default_level: "none",
    grants: [grantRow(ADMIN_PRINCIPAL_ID, "admin")],
    principals: grantSidecar,
  };
  putGrants = [];
  deletedGrants = [];
  patchedTypes = [];
});
afterEach(() => {
  server.resetHandlers();
  currentObjectType = objectTypeFixture();
  grantsStore = {
    object_type: "artifact",
    default_level: "none",
    grants: [grantRow(ADMIN_PRINCIPAL_ID, "admin")],
    principals: grantSidecar,
  };
  putGrants = [];
  deletedGrants = [];
  patchedTypes = [];
  lastFieldUpdateBody = null;
  lastProposalBody = null;
  addFieldCallCount = 0;
  lastAddFieldBody = null;
});
afterAll(() => server.close());

/** The permissions panel reads `useAuth()`, so the editor renders under an `AuthProvider`.
 * `renderWithProviders` supplies the same providers `main.tsx` does; the default principal is an
 * `admin`, which every test in this file that does not set a level assumes. */
function renderEditor(principal: CurrentPrincipal = DEFAULT_TEST_PRINCIPAL) {
  return renderWithProviders(
    <Routes>
      <Route path="/schema/:objectTypeKey" element={<SchemaEditorPage />} />
    </Routes>,
    { route: "/schema/artifact", principal },
  );
}

describe("SchemaEditorPage", () => {
  it("applies an additive object-type header edit immediately, with no confirmation step", async () => {
    const user = userEvent.setup();
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact" });
    const descriptionBox = screen.getByLabelText("Description") as HTMLTextAreaElement;
    await user.clear(descriptionBox);
    await user.type(descriptionBox, "An updated, agent-facing description of this type.");
    await user.click(screen.getByRole("button", { name: "Save" }));

    // No confirmation dialog or extra step — the textarea just reflects the applied value.
    await waitFor(() =>
      expect((screen.getByLabelText("Description") as HTMLTextAreaElement).value).toBe(
        "An updated, agent-facing description of this type.",
      ),
    );
    expect(screen.queryByTestId("blast-radius-panel")).not.toBeInTheDocument();
  });

  it(
    "surfaces the backend's validation_failed rejection of an object-type header edit " +
      "rather than silently swallowing it (e.g. an attempted key/key_prefix change)",
    async () => {
      const user = userEvent.setup();
      server.use(
        http.patch(
          "/api/v1/object-types/:key",
          () =>
            HttpResponse.json(
              {
                error: {
                  code: "validation_failed",
                  message: "key and key_prefix are immutable after creation.",
                  details: {},
                },
              },
              { status: 422 },
            ),
          { once: true },
        ),
      );
      renderEditor();

      await screen.findByRole("heading", { name: "Artifact" });
      await user.clear(screen.getByLabelText("Description"));
      await user.type(screen.getByLabelText("Description"), "A different description entirely.");
      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByText(/immutable after creation/i)).toBeInTheDocument();
    },
  );

  it("rejects an empty field description client-side, before any request is sent", async () => {
    const user = userEvent.setup();
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact" });
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const fieldForm = await screen.findByRole("form", { name: "Edit field" });
    const description = within(fieldForm).getByLabelText("Description");
    await user.clear(description);
    await user.click(within(fieldForm).getByRole("button", { name: "Save field" }));

    expect(await within(fieldForm).findByText(/requires a non-empty description/i)).toBeInTheDocument();
    expect(within(fieldForm).getByText(/how agents interpret the schema/i)).toBeInTheDocument();
    // Nothing was sent: the field-update handler never recorded a body.
    expect(lastFieldUpdateBody).toBeNull();
  });

  it(
    "shows the exact blast radius for a destructive field-type change before the flow can be " +
      "dismissed, and leaves the schema unchanged (still pending) until acknowledged",
    async () => {
      const user = userEvent.setup();
      renderEditor();

      await screen.findByRole("heading", { name: "Artifact" });
      await user.click(screen.getByRole("button", { name: "Edit" }));
      const fieldForm = await screen.findByRole("form", { name: "Edit field" });
      const typeSelect = within(fieldForm).getByLabelText("Type");
      await user.selectOptions(typeSelect, "long_text");
      await user.click(within(fieldForm).getByRole("button", { name: "Save field" }));

      const panel = await screen.findByTestId("blast-radius-panel");
      expect(within(panel).getByTestId("blast-radius-affected-records")).toHaveTextContent("3");
      expect(within(panel).getByTestId("blast-radius-sample-values")).toHaveTextContent("1, 2");
      expect(within(panel).getByText("not coercible")).toBeInTheDocument();
      // The destination is the proposal's own Inbox page, not Settings. Asserted as
      // the LINK'S HREF rather than as its text, because the text is copy and the href is the
      // claim -- a link whose words changed and whose target did not is exactly the defect the
      // old `/settings/i` match would have kept passing through.
      const link = within(panel).getByRole("link", { name: /inbox/i });
      expect(link).toHaveAttribute("href", "/inbox/prop-1");

      // The impact must render before the flow can be dismissed at all: acknowledging is the
      // only way off this panel (no field list, no field-editor form to fall back to).
      expect(screen.queryByRole("form", { name: "Edit field" })).not.toBeInTheDocument();
      expect(screen.queryByTestId("field-row-points")).not.toBeInTheDocument();

      await user.click(within(panel).getByRole("button", { name: "Acknowledge" }));

      // Afterward: nothing applied. The schema is still exactly what it was before submission.
      expect(screen.queryByTestId("blast-radius-panel")).not.toBeInTheDocument();
      expect(screen.getByTestId("field-row-points")).toHaveTextContent("integer");
    },
  );

  it("rejects an empty enum option description client-side, before any request is sent", async () => {
    const user = userEvent.setup();
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact" });
    await user.click(screen.getByRole("button", { name: "Add field" }));
    const addForm = await screen.findByRole("form", { name: "Add field" });
    await user.type(within(addForm).getByLabelText("Key", { selector: "input" }), "status");
    await user.type(within(addForm).getByLabelText("Name"), "Status");
    await user.type(within(addForm).getByLabelText("Description"), "Delivery state of the artifact.");
    await user.selectOptions(within(addForm).getByLabelText("Type"), "single_select");

    await user.click(within(addForm).getByRole("button", { name: "Add option" }));
    const option = within(addForm).getByTestId("enum-option-0");
    await user.type(within(option).getByLabelText("Value"), "todo");
    await user.type(within(option).getByLabelText("Label"), "To do");
    // Description deliberately left empty.

    await user.click(within(addForm).getByRole("button", { name: "Add field" }));

    expect(
      await within(option).findByText(/requires a non-empty description/i),
    ).toBeInTheDocument();
    expect(addFieldCallCount).toBe(0);
  });

  it(
    "the embed checkbox's label and helper both name the keyword and semantic search " +
      "consequence",
    async () => {
      const user = userEvent.setup();
      renderEditor();

      await screen.findByRole("heading", { name: "Artifact" });
      await user.click(screen.getByRole("button", { name: "Add field" }));
      const addForm = await screen.findByRole("form", { name: "Add field" });
      await user.selectOptions(within(addForm).getByLabelText("Type"), "long_text");

      const checkbox = within(addForm).getByRole("checkbox", {
        name: "Include in search indexes (keyword and semantic)",
      });
      expect(checkbox).toBeInTheDocument();
      // Both the label and the helper text name both indexes:
      // the label itself names "keyword and semantic", and the helper additionally states
      // the operator-facing consequence of turning the flag off.
      const helper = within(addForm).getByText(/unsearchable/i);
      expect(helper.textContent).toMatch(/keyword/i);
      expect(helper.textContent).toMatch(/semantic/i);
      // The checkbox's accessible name above is exact ("Include in search indexes (keyword
      // and semantic)"), which already proves the older label is gone from this control,
      // asserted without spelling that old string out here, so it doesn't trip the literal
      // sweep for it.
    },
  );

  it("applies an additive field edit immediately and reflects it in the field list", async () => {
    const user = userEvent.setup();
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact" });
    await user.click(screen.getByRole("button", { name: "Edit" }));
    const fieldForm = await screen.findByRole("form", { name: "Edit field" });
    const name = within(fieldForm).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Story points");
    await user.click(within(fieldForm).getByRole("button", { name: "Save field" }));

    await waitFor(() =>
      expect(screen.getByTestId("field-row-points")).toHaveTextContent("Story points"),
    );
    expect(screen.queryByTestId("blast-radius-panel")).not.toBeInTheDocument();
  });

  it("deleting a field proposes delete_field and shows its blast radius, unapplied", async () => {
    const user = userEvent.setup();
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact" });
    await user.click(
      within(screen.getByTestId("field-row-points")).getByRole("button", { name: "Delete" }),
    );

    expect(lastProposalBody).toEqual({
      change_type: "delete_field",
      object_type: "artifact",
      field_key: "points",
    });
    const panel = await screen.findByTestId("blast-radius-panel");
    expect(within(panel).getByTestId("blast-radius-affected-records")).toHaveTextContent("2");
    expect(within(panel).getByText("prop-delete-field")).toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "Acknowledge" }));
    // Still pending: the field is still in the list, not removed.
    expect(screen.getByTestId("field-row-points")).toBeInTheDocument();
  });

  it("deleting the object type proposes delete_object_type and shows its blast radius, unapplied", async () => {
    const user = userEvent.setup();
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact" });
    await user.click(screen.getByRole("button", { name: "Delete object type" }));

    expect(lastProposalBody).toEqual({ change_type: "delete_object_type", object_type: "artifact" });
    const panel = await screen.findByTestId("blast-radius-panel");
    expect(within(panel).getByTestId("blast-radius-affected-records")).toHaveTextContent("3");
    expect(within(panel).getByTestId("blast-radius-sample-values")).toHaveTextContent("ART-001");

    await user.click(within(panel).getByRole("button", { name: "Acknowledge" }));
    // Still pending: the object type editor is still showing this type, not redirected away.
    expect(screen.getByRole("heading", { name: "Artifact" })).toBeInTheDocument();
  });
  // `services/schema.py:584` derives `embed` from
  // `field_type == "long_text"` and honors an explicit value over that default, so a form that
  // always sends `embed: false` silently makes every UI-created long_text field unsearchable.
  // The form now mirrors the service rule until the user touches the control.
  async function openAddForm(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByRole("heading", { name: "Artifact" });
    await user.click(screen.getByRole("button", { name: "Add field" }));
    const addForm = await screen.findByRole("form", { name: "Add field" });
    await user.type(within(addForm).getByLabelText("Key", { selector: "input" }), "notes");
    await user.type(within(addForm).getByLabelText("Name"), "Notes");
    await user.type(
      within(addForm).getByLabelText("Description"),
      "Free-form operator notes about the artifact.",
    );
    return addForm;
  }

  const EMBED_LABEL = "Include in search indexes (keyword and semantic)";

  it("choosing long_text turns the embed control on, and the add payload sends embed: true", async () => {
    const user = userEvent.setup();
    renderEditor();
    const addForm = await openAddForm(user);

    await user.selectOptions(within(addForm).getByLabelText("Type"), "long_text");
    expect(within(addForm).getByRole("checkbox", { name: EMBED_LABEL })).toBeChecked();

    await user.click(within(addForm).getByRole("button", { name: "Add field" }));
    await waitFor(() => expect(addFieldCallCount).toBe(1));
    expect(lastAddFieldBody).toMatchObject({ type: "long_text", embed: true });
  });

  it("moving back off long_text turns the embed control off again", async () => {
    const user = userEvent.setup();
    renderEditor();
    const addForm = await openAddForm(user);

    await user.selectOptions(within(addForm).getByLabelText("Type"), "long_text");
    expect(within(addForm).getByRole("checkbox", { name: EMBED_LABEL })).toBeChecked();
    await user.selectOptions(within(addForm).getByLabelText("Type"), "short_text");
    expect(within(addForm).getByRole("checkbox", { name: EMBED_LABEL })).not.toBeChecked();

    await user.click(within(addForm).getByRole("button", { name: "Add field" }));
    await waitFor(() => expect(addFieldCallCount).toBe(1));
    expect(lastAddFieldBody).toMatchObject({ type: "short_text", embed: false });
  });

  it("an explicit choice by the user survives a later type change", async () => {
    const user = userEvent.setup();
    renderEditor();
    const addForm = await openAddForm(user);

    // The user opts a short_text field into the indexes, then changes the type.
    await user.selectOptions(within(addForm).getByLabelText("Type"), "short_text");
    await user.click(within(addForm).getByRole("checkbox", { name: EMBED_LABEL }));
    expect(within(addForm).getByRole("checkbox", { name: EMBED_LABEL })).toBeChecked();
    await user.selectOptions(within(addForm).getByLabelText("Type"), "long_text");
    // Still on: the derived default never overwrites a touched control.
    expect(within(addForm).getByRole("checkbox", { name: EMBED_LABEL })).toBeChecked();

    // And the opposite direction: touched-off stays off when the type becomes long_text.
    await user.click(within(addForm).getByRole("checkbox", { name: EMBED_LABEL }));
    await user.selectOptions(within(addForm).getByLabelText("Type"), "short_text");
    await user.selectOptions(within(addForm).getByLabelText("Type"), "long_text");
    expect(within(addForm).getByRole("checkbox", { name: EMBED_LABEL })).not.toBeChecked();

    await user.click(within(addForm).getByRole("button", { name: "Add field" }));
    await waitFor(() => expect(addFieldCallCount).toBe(1));
    expect(lastAddFieldBody).toMatchObject({ type: "long_text", embed: false });
  });

  it("editing an untouched field's other attributes never sends an embed change", async () => {
    const user = userEvent.setup();
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact" });
    await user.click(within(screen.getByTestId("field-row-points")).getByRole("button", { name: "Edit" }));
    const editForm = await screen.findByRole("form", { name: "Edit field" });
    await user.clear(within(editForm).getByLabelText("Name"));
    await user.type(within(editForm).getByLabelText("Name"), "Story points");
    await user.click(within(editForm).getByRole("button", { name: "Save field" }));

    await waitFor(() => expect(lastFieldUpdateBody).not.toBeNull());
    expect((lastFieldUpdateBody as { changes: Record<string, unknown> }).changes).toEqual({
      name: "Story points",
    });
  });
});

/**
 * The schema editor, the one screen with two thresholds. `propose_schema_change` is at `write`
 * (`services/schema.py:439`) and both destructive controls go through it, while `add_field`,
 * `update_field` and `update_object_type` are `admin` (`:345`, `:371`, `:308`). Gating the two
 * Delete controls at `admin` because they look dangerous would hide from a `write` caller a
 * control the server accepts, a failure in the direction nobody notices.
 */
describe("SchemaEditorPage: level gating", () => {
  function renderAt(level: "read" | "write" | "admin") {
    currentObjectType = { ...objectTypeFixture(), your_access: level };
    return renderEditor();
  }

  it("at 'write': the two destructive controls stay, because proposing is not deciding", async () => {
    renderAt("write");
    await screen.findByRole("heading", { name: "Artifact", level: 1 });

    // Present. Asserted explicitly, not by the absence of something else.
    expect(screen.getByRole("button", { name: "Delete object type" })).toBeInTheDocument();
    const fieldRow = screen.getByTestId("field-row-points");
    expect(within(fieldRow).getByRole("button", { name: "Delete" })).toBeInTheDocument();

    // Absent: the three `admin` controls.
    expect(screen.queryByRole("button", { name: "Add field" })).not.toBeInTheDocument();
    expect(within(fieldRow).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Description")).not.toBeInTheDocument();

    const banners = screen.getAllByTestId("read-only-banner");
    expect(banners).toHaveLength(1);
    expect(banners[0]).toHaveTextContent(
      "You hold write on Artifact. Editing this object type and its fields needs admin on " +
        "Artifact. Ask an administrator of Artifact, or a system administrator, for admin access.",
    );
  });

  it("at 'read': nothing is offered, and the banner names both thresholds", async () => {
    renderAt("read");
    await screen.findByRole("heading", { name: "Artifact", level: 1 });

    expect(screen.queryByRole("button", { name: "Delete object type" })).not.toBeInTheDocument();
    const fieldRow = screen.getByTestId("field-row-points");
    expect(within(fieldRow).queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(within(fieldRow).queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add field" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();

    // The fields themselves stay readable: this is a `read` caller, not a locked door.
    expect(within(fieldRow).getByText("points")).toBeInTheDocument();

    expect(screen.getByTestId("read-only-banner")).toHaveTextContent(
      "Read-only. You hold read on Artifact. Proposing a schema change needs write on " +
        "Artifact, and editing this object type or its fields needs admin. Ask an " +
        "administrator of Artifact, or a system administrator.",
    );
  });

  it("at 'admin': every control, and no banner (fence)", async () => {
    renderAt("admin");
    await screen.findByRole("heading", { name: "Artifact", level: 1 });

    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add field" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete object type" })).toBeInTheDocument();
    expect(screen.queryByTestId("read-only-banner")).not.toBeInTheDocument();
  });
});



/**
 * The permissions panel (DD-11). One threshold: `admin` **on the object type**, which is the
 * only authority the three grant routes check. No system-role clause is conjoined onto it, so a
 * `creator` administering the type it defined gets the panel — and the two reads behind it stay
 * different on purpose, which the last two cases in this block are what pin.
 */
describe("PermissionsPanel", () => {
  const CREATOR: CurrentPrincipal = {
    ...DEFAULT_TEST_PRINCIPAL,
    id: "00000000-0000-4000-8000-000000000009",
    display_name: "Test Creator",
    role: "creator",
    scope: "admin",
  };

  async function openPanelAsAdmin() {
    currentObjectType = { ...objectTypeFixture(), your_access: "admin" };
    renderEditor();
    // The section renders before its two queries resolve, so wait on real content rather than
    // on the region itself.
    await screen.findByLabelText("Default access");
    return screen.getByRole("region", { name: "Permissions" });
  }

  it("saves the default access through PATCH /object-types/{key}", async () => {
    const user = userEvent.setup();
    const panel = await openPanelAsAdmin();

    const select = within(panel).getByLabelText("Default access");
    expect(select).toHaveValue("none");
    expect(within(select).getByRole("option", { name: "No access (closed)" })).toBeInTheDocument();

    await user.selectOptions(select, "read");

    await waitFor(() => expect(patchedTypes).toEqual([{ default_level: "read" }]));
  });

  it("PUTs a grant row's level change", async () => {
    const user = userEvent.setup();
    const panel = await openPanelAsAdmin();

    await user.selectOptions(within(panel).getByLabelText("Level for Test Admin"), "write");

    await waitFor(() =>
      expect(putGrants).toEqual([{ principalId: ADMIN_PRINCIPAL_ID, level: "write" }]),
    );
  });

  it("grants a second principal, resolving the picker from the principal directory", async () => {
    const user = userEvent.setup();
    const panel = await openPanelAsAdmin();

    await user.selectOptions(within(panel).getByLabelText("Grant access to"), OTHER_PRINCIPAL_ID);
    await user.selectOptions(within(panel).getByLabelText("Level to grant"), "write");
    await user.click(within(panel).getByRole("button", { name: "Grant" }));

    await waitFor(() =>
      expect(putGrants).toEqual([{ principalId: OTHER_PRINCIPAL_ID, level: "write" }]),
    );
  });

  it("DELETEs a grant on Remove", async () => {
    const user = userEvent.setup();
    const panel = await openPanelAsAdmin();

    await user.click(within(panel).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(deletedGrants).toEqual([ADMIN_PRINCIPAL_ID]));
  });

  it("renders a level 'none' row as 'Denied' rather than hiding it", async () => {
    // `none` is an explicit deny that overrides a permissive default. A row omitted
    // because it grants nothing would misrepresent the model in the direction that matters.
    grantsStore = {
      object_type: "artifact",
      default_level: "read",
      grants: [grantRow(OTHER_PRINCIPAL_ID, "none")],
      principals: grantSidecar,
    };
    const panel = await openPanelAsAdmin();

    const row = within(panel).getByTestId(`grant-${OTHER_PRINCIPAL_ID}`);
    expect(row).toBeInTheDocument();
    expect(within(row).getByText("Other User")).toBeInTheDocument();
    expect(within(row).getByLabelText("Level for Other User")).toHaveValue("none");
    expect(within(row).getByRole("option", { name: "Denied" })).toBeInTheDocument();
  });

  it("does not render for a caller below 'admin' on the type", async () => {
    currentObjectType = { ...objectTypeFixture(), your_access: "write" };
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact", level: 1 });
    expect(screen.queryByRole("region", { name: "Permissions" })).not.toBeInTheDocument();
  });

  /** What a `creator` really gets from `GET /api/v1/principals`: it keeps both `admin` gates
   * (it is not widened for them), so a panel that depended on it would render an empty picker
   * for exactly the principal these tests are about. Installed in the two tests below so they
   * assert the panel's real sources rather than passing on that route being reachable. */
  function refusePrincipalManagementRoute() {
    server.use(
      http.get("/api/v1/principals", () =>
        HttpResponse.json(
          { error: { code: "forbidden", message: "Needs the admin role." } },
          { status: 403 },
        ),
      ),
    );
  }

  it("renders for a creator holding 'admin' on the type, with names and a picker", async () => {
    // A `creator` holding `admin` on the type it defined passes every check on all three grant
    // routes and is offered the screen: labels come from the grants document's own
    // `principals` sidecar, the picker from `GET /principals/directory`.
    refusePrincipalManagementRoute();
    currentObjectType = { ...objectTypeFixture(), your_access: "admin" };
    renderEditor(CREATOR);

    await screen.findByLabelText("Default access");
    const panel = screen.getByRole("region", { name: "Permissions" });
    expect(within(panel).getByTestId(`grant-${ADMIN_PRINCIPAL_ID}`)).toHaveTextContent(
      "Test Admin",
    );
    expect(within(panel).getByLabelText("Grant access to")).toBeInTheDocument();
  });

  it("names a granted principal the directory does not carry, from the sidecar", async () => {
    // The case the sidecar exists for. `Dana Departed` is deactivated and therefore absent from
    // the picker's directory read; the row still names her rather than printing a UUID. Reading
    // both from one source would lose this in one direction or the other.
    grantsStore = {
      object_type: "artifact",
      default_level: "none",
      grants: [grantRow(DEPARTED_PRINCIPAL_ID, "read")],
      principals: grantSidecar,
    };
    const panel = await openPanelAsAdmin();

    const row = within(panel).getByTestId(`grant-${DEPARTED_PRINCIPAL_ID}`);
    expect(within(row).getByText("Dana Departed")).toBeInTheDocument();
    expect(within(row).queryByText(DEPARTED_PRINCIPAL_ID)).not.toBeInTheDocument();
  });

  it("offers the directory's active entries in the picker, and only those", async () => {
    // `GET /api/v1/principals` is refused, so a picker that still read it would be empty. This
    // is what makes the assertion below about the *source*, not just about the labels.
    refusePrincipalManagementRoute();
    const panel = await openPanelAsAdmin();

    const picker = within(panel).getByLabelText("Grant access to") as HTMLSelectElement;
    await waitFor(() => expect(picker.options.length).toBeGreaterThan(1));
    const labels = Array.from(picker.options).map((option) => option.textContent);
    // `Test Admin` already holds a grant, so it is not offered again; `Dana Departed` is
    // deactivated and never offered at all.
    expect(labels).toEqual(["Choose someone...", "Other User"]);
  });
});

/**
 * Hiding the link on the schema index gates the link, not the route, so the route itself is
 * reached here by URL.
 */
describe("SchemaEditorPage: the create route", () => {
  function renderCreate(principal: CurrentPrincipal) {
    return renderWithProviders(
      <Routes>
        <Route path="/schema/new" element={<SchemaEditorPage />} />
      </Routes>,
      { route: "/schema/new", principal },
    );
  }

  it("gives a member reaching /schema/new by URL no form, and says who creates types", async () => {
    renderCreate({ ...DEFAULT_TEST_PRINCIPAL, role: "member", scope: "write" });

    expect(
      await screen.findByText(
        "Object types are created by administrators. Ask one if you need a new type.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create" })).not.toBeInTheDocument();
  });

  it("gives a creator the form (fence)", async () => {
    renderCreate({ ...DEFAULT_TEST_PRINCIPAL, role: "creator", scope: "admin" });

    expect(await screen.findByRole("heading", { name: "Create object type" })).toBeInTheDocument();
    expect(screen.getByLabelText("Key")).toBeInTheDocument();
  });
});


/**
 * The "Display field" select on the object type settings form.
 *
 * Its options come from each field's `display_eligible` flag **on the wire** — the one
 * implementation of the rule is `fieldtypes.py::is_display_eligible`, so it is not restated
 * here. Its current value is the **stored** `display_field_key`, not the
 * effective one, so saving an unrelated setting on this form cannot silently convert an
 * implicit null into an explicit pin the user never chose.
 */
describe("SchemaEditorPage: the display field select", () => {
  function optionLabels() {
    const select = screen.getByLabelText("Display field") as HTMLSelectElement;
    return Array.from(select.options).map((option) => option.textContent);
  }

  it("offers only the display-eligible fields, plus the explicit null option", async () => {
    currentObjectType = objectTypeFixture({
      fields: [
        pointsField,
        ineligibleField("owner", "relation"),
        ineligibleField("spec", "attachment"),
        ineligibleField("reviewer", "user_ref"),
        labelField,
      ],
    });
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact", level: 1 });
    expect(optionLabels()).toEqual(["(first field)", "Points", "Label"]);
  });

  it("shows (first field) selected for a type whose stored column is null", async () => {
    currentObjectType = objectTypeFixture({
      display_field_key: null,
      // The type resolves to `points` today; the select must still show the null option,
      // because the user has chosen nothing and saving must not pin what nobody picked.
      effective_display_field_key: "points",
      fields: [pointsField, labelField],
    });
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact", level: 1 });
    expect((screen.getByLabelText("Display field") as HTMLSelectElement).value).toBe("");
  });

  it("shows the stored key selected when the type has chosen one", async () => {
    currentObjectType = objectTypeFixture({
      display_field_key: "label",
      effective_display_field_key: "label",
      fields: [pointsField, labelField],
    });
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact", level: 1 });
    expect((screen.getByLabelText("Display field") as HTMLSelectElement).value).toBe("label");
  });

  it("submits display_field_key on save, and null when returned to (first field)", async () => {
    const user = userEvent.setup();
    currentObjectType = objectTypeFixture({
      display_field_key: null,
      effective_display_field_key: "points",
      fields: [pointsField, labelField],
    });
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact", level: 1 });
    await user.selectOptions(screen.getByLabelText("Display field"), "label");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchedTypes).toEqual([{ display_field_key: "label" }]));

    // And back: an explicit `null` is how a type returns to the derived rule.
    // `""` is the option's value but is not a field key the server would accept.
    await user.selectOptions(screen.getByLabelText("Display field"), "");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(patchedTypes).toEqual([
        { display_field_key: "label" },
        { display_field_key: null },
      ]),
    );
  });

  it("is absent for a write-level caller, along with the rest of the settings form", async () => {
    currentObjectType = objectTypeFixture({ your_access: "write", fields: [pointsField] });
    renderEditor();

    await screen.findByRole("heading", { name: "Artifact", level: 1 });
    expect(screen.queryByLabelText("Display field")).not.toBeInTheDocument();
    // It needs no gate of its own: it inherits the form's `canAdmin`, and the access banner is
    // what keeps the absence from being silent.
    expect(screen.getByRole("button", { name: "Delete object type" })).toBeInTheDocument();
  });
});
