/**
 * The Details card. Every field in schema order (including relation), the click-to-edit
 * button, the gloss disclosure per field, the agent bar, relation pills with no status, and the
 * date relative hint plus truncation hover.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { FieldDoc, ObjectTypeDetail, ObjectTypeSummary } from "../api/objectTypes";
import type { AuditEventDoc, LinkedRecordRef, QueryResult, RecordWithIncludes } from "../api/records";
import { renderWithProviders } from "../test/renderWithProviders";
import { DetailsCard } from "./DetailsCard";

let historyEvents: AuditEventDoc[] = [];
let historyRequestCount = 0;
let patches: Array<{ ref: string; body: Record<string, unknown> }> = [];

/**
 * Every relation field below (`ownerField`, and `manyOwner` derived from it) targets
 * `person`. The picker the field renders needs real answers for the three requests it makes:
 * the orientation list, the target's own detail document, and its query.
 */
const personObjectType: ObjectTypeDetail = {
  key: "person",
  name: "Person",
  name_plural: "People",
  description: "Someone the workspace coordinates with.",
  key_prefix: "PER",
  record_count: 2,
  field_count: 1,
  your_access: "read",
  display_field_key: "name",
  effective_display_field_key: "name",
  fields: [
    {
      key: "name",
      name: "Name",
      type: "short_text",
      description: "The person's name.",
      required: true,
      unique: false,
      indexed: true,
      embed: false,
      default: null,
      config: {},
      position: 0,
      operators: ["eq", "neq", "contains", "is_null", "is_not_null"],
      display_eligible: true,
    },
  ],
  system_fields: [],
};

/** The orientation list (`useObjectTypes`): the picker's first read, deciding whether `person`
 * is a target this caller can read at all. */
const objectTypesList: ObjectTypeSummary[] = [
  {
    key: personObjectType.key,
    name: personObjectType.name,
    description: personObjectType.description,
    key_prefix: personObjectType.key_prefix,
    record_count: personObjectType.record_count,
    field_count: personObjectType.field_count,
    your_access: personObjectType.your_access,
  },
];

/** The picker's search result: one candidate, `PER-002`, distinct from the already-linked
 * `PER-001` (`linkedOwner` below) so a picked option is unambiguous. */
const personQueryResult: QueryResult = {
  records: [
    {
      id: "per-002-id",
      key: "PER-002",
      version: 1,
      created_at: "2026-09-01T10:00:00Z",
      created_by: "p-1",
      updated_at: "2026-09-01T10:00:00Z",
      updated_by: "p-1",
      updated_by_agent_label_id: null,
      deleted_at: null,
      comment_count: 0,
      last_comment_at: null,
      data: { name: "Priya Shah" },
    },
  ],
  total_count: 1,
  next_cursor: null,
  truncated: false,
};

const server = setupServer(
  http.get("/api/v1/records/:ref/history", () => {
    historyRequestCount += 1;
    return HttpResponse.json({ events: historyEvents, next_cursor: null });
  }),
  http.get("/api/v1/records/:ref/comments", () => {
    return HttpResponse.json({ comments: [], next_cursor: null });
  }),
  http.patch("/api/v1/records/:ref", async ({ params, request }) => {
    const body = (await request.json()) as { values: Record<string, unknown> };
    patches.push({ ref: String(params.ref), body: body.values });
    return HttpResponse.json({ key: String(params.ref), version: 5, data: body.values });
  }),
  http.post("/api/v1/records/:ref/links/:fieldKey", () => HttpResponse.json({})),
  http.delete("/api/v1/records/:ref/links/:fieldKey", () => HttpResponse.json({})),
  // The relation picker's three requests (the orientation check, the target's own
  // detail document, and its search), all answering `person` since every relation field in this
  // file targets it.
  http.get("/api/v1/object-types", () => HttpResponse.json(objectTypesList)),
  http.get("/api/v1/object-types/person", () => HttpResponse.json(personObjectType)),
  http.post("/api/v1/object-types/:key/query", () => HttpResponse.json(personQueryResult)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  historyEvents = [];
  historyRequestCount = 0;
  patches = [];
});
afterAll(() => server.close());

let nextId = 0;
function event(overrides: Partial<AuditEventDoc>): AuditEventDoc {
  nextId += 1;
  return {
    id: nextId,
    ts: "2026-09-12T02:45:00Z",
    request_id: "req-1",
    principal_id: "p-1",
    principal_type: "user",
    agent_label_id: null,
    agent_label: null,
    auth_method: "pat",
    surface: "api",
    entity_type: "record",
    entity_id: "rec-1",
    record_id: "rec-1",
    object_type_id: "obj-1",
    action: "create",
    field_key: null,
    old_value: null,
    new_value: null,
    note: null,
    principal_display_name: "Dana Reyes",
    record_key: "PROS-001",
    ...overrides,
  };
}

const stageField: FieldDoc = {
  key: "stage",
  name: "Stage",
  type: "single_select",
  description: "Where the deal stands.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 0,
  operators: [],
  display_eligible: true,
  options: [
    { value: "proposal_sent", label: "Proposal sent", description: "Waiting on them." },
    { value: "negotiating", label: "Negotiating", description: "Talking terms." },
  ],
};

const dueField: FieldDoc = {
  key: "next_action_at",
  name: "Next action",
  type: "date",
  description: "When to follow up next.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 1,
  operators: [],
  display_eligible: true,
};

const notesField: FieldDoc = {
  key: "notes",
  name: "Notes",
  type: "long_text",
  description: "Freeform notes about this deal, some of them quite long indeed.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 2,
  operators: [],
  display_eligible: true,
};

const ownerField: FieldDoc = {
  key: "owner",
  name: "Owner",
  type: "relation",
  description: "Who owns this deal.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 3,
  operators: [],
  display_eligible: false,
  target_type_key: "person",
  cardinality: "one",
  inverse_field_key: "owned_deals",
};

const linkedOwner: LinkedRecordRef = { key: "PER-001", id: "link-1", display: "Sam Okafor" };

function makeRecord(overrides: Partial<RecordWithIncludes> = {}): RecordWithIncludes {
  return {
    id: "rec-1",
    key: "PROS-001",
    version: 3,
    created_at: "2026-09-01T10:00:00Z",
    created_by: "p-1",
    updated_at: "2026-09-01T10:00:00Z",
    updated_by: "p-1",
    updated_by_agent_label_id: null,
    deleted_at: null,
    comment_count: 0,
    last_comment_at: null,
    data: { stage: "proposal_sent", next_action_at: "2026-09-20", notes: "A note.\n".repeat(20) },
    links: { owner: [linkedOwner] },
    ...overrides,
  };
}

describe("DetailsCard", () => {
  it("renders one row per field, in schema order, including the relation field", () => {
    renderWithProviders(
      <DetailsCard
        record={makeRecord()}
        fields={[stageField, dueField, notesField, ownerField]}
        canWrite
      />,
    );

    const list = screen.getByTestId("field-list");
    expect(within(list).getByText("Stage")).toBeInTheDocument();
    expect(within(list).getByText("Next action")).toBeInTheDocument();
    expect(within(list).getByText("Notes")).toBeInTheDocument();
    expect(within(list).getByText("Owner")).toBeInTheDocument();
    // No single combined "Linked" row: `Owner` is a field row like any other, not a section.
    expect(screen.queryByText("Linked")).not.toBeInTheDocument();
  });

  it("gives every field its own gloss toggle, opening to that field's own description", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <DetailsCard record={makeRecord()} fields={[stageField, ownerField]} canWrite />,
    );

    expect(screen.getByTestId("gloss-toggle-stage")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("gloss-toggle-owner")).toHaveAttribute("aria-expanded", "false");

    await user.click(screen.getByTestId("gloss-toggle-owner"));
    expect(screen.getByTestId("gloss-panel-owner")).toHaveTextContent(ownerField.description);
    expect(screen.queryByTestId("gloss-panel-stage")).not.toBeInTheDocument();

    expect(
      screen.getByRole("button", { name: `About ${ownerField.name}` }),
    ).toBeInTheDocument();
  });

  it("edits a plain field in place through the value button, its accessible name intact", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DetailsCard record={makeRecord()} fields={[stageField]} canWrite />);

    const button = screen.getByRole("button", { name: "Edit Stage" });
    expect(screen.getByTestId("field-value-stage")).toBe(button);

    await user.click(button);
    const select = screen.getByRole("combobox", { name: "Stage value for PROS-001" });
    await user.selectOptions(select, "negotiating");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toEqual({ ref: "PROS-001", body: { stage: "negotiating" } });
  });

  it("below write, shows the value as plain text with no Edit affordance", () => {
    renderWithProviders(
      <DetailsCard record={makeRecord()} fields={[stageField]} canWrite={false} />,
    );

    expect(screen.queryByRole("button", { name: "Edit Stage" })).not.toBeInTheDocument();
    expect(screen.getByTestId("field-value-stage")).toHaveTextContent("Proposal sent");
  });

  it("carries the full stored value on hover for a truncated field", () => {
    const longNote = "A note.\n".repeat(20);
    renderWithProviders(
      <DetailsCard record={makeRecord({ data: { notes: longNote } })} fields={[notesField]} canWrite />,
    );

    expect(screen.getByTestId("field-value-notes")).toHaveAttribute("title", longNote);
  });

  it("adds the relative date hint on a date value", () => {
    renderWithProviders(<DetailsCard record={makeRecord()} fields={[dueField]} canWrite />);

    const button = screen.getByRole("button", { name: "Edit Next action" });
    expect(button).toHaveTextContent("Sep");
    expect(button.textContent).toMatch(/\(.*\)/);
    // The hover stays the raw stored date, never the relative-hinted display text.
    expect(button).toHaveAttribute("title", "2026-09-20");
  });

  it("renders a relation field's linked records as title-only pills, no status", () => {
    renderWithProviders(<DetailsCard record={makeRecord()} fields={[ownerField]} canWrite={false} />);

    // The pill reads the key AND the title, as the approved comp does
    // (`docs/design/counterpart-record-light.png`: `ENG-004 Two-phase fulfillment review`). The
    // record page is the one screen where the pill is the only place either appears, so dropping
    // the key would take the linked record's handle off it entirely.
    const link = screen.getByRole("link", { name: "PER-001 Sam Okafor" });
    expect(link).toHaveAttribute("href", "/person/PER-001");
    // No status: `list_link_summaries` projects `{key, id, display}` and `display` is the
    // target's title, so nothing beside the name can read as a status word from the fixture's
    // options — and a pill that invented one would be guessing at a display role DD-23 exists to
    // stop being guessed.
    expect(link).not.toHaveTextContent("Proposal sent");
  });

  it("keeps the relation field's own link/unlink affordances (its current widget)", async () => {
    const manyOwner: FieldDoc = { ...ownerField, key: "reviewers", cardinality: "many" };
    const user = userEvent.setup();
    renderWithProviders(
      <DetailsCard
        record={makeRecord({ links: { reviewers: [linkedOwner] } })}
        fields={[manyOwner]}
        canWrite
      />,
    );

    expect(screen.getByRole("button", { name: "Unlink PER-001 Sam Okafor" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add another" }));
    await user.click(await screen.findByRole("option", { name: "PER-002 Priya Shah" }));
    // No assertion on the network call itself here — `useLinkMutations` already owns and tests
    // the request shape; this proves the affordance is still reachable from this card.
  });

  it("renders the attachment field through the existing AttachmentField widget", () => {
    const filesField: FieldDoc = {
      ...notesField,
      key: "files",
      name: "Files",
      type: "attachment",
    };
    renderWithProviders(
      <DetailsCard record={makeRecord({ data: { files: [] } })} fields={[filesField]} canWrite />,
    );

    expect(screen.getByTestId("attachment-field-files")).toBeInTheDocument();
  });

  it("marks a field the most recent agent-authored version changed with the agent bar", async () => {
    historyEvents = [
      event({ request_id: "w1", action: "create", field_key: null }),
      event({ request_id: "w1", action: "create", field_key: "stage" }),
      event({
        request_id: "w2",
        action: "update",
        field_key: "stage",
        agent_label_id: "a-1",
        agent_label: "sales-agent",
      }),
    ];

    renderWithProviders(<DetailsCard record={makeRecord()} fields={[stageField]} canWrite />);

    await waitFor(() =>
      expect(screen.getByTestId("field-value-stage").className).toContain("border-agent"),
    );
  });

  it("carries no agent bar when no version was agent-authored", async () => {
    historyEvents = [
      event({ request_id: "w1", action: "create", field_key: null }),
      event({ request_id: "w1", action: "create", field_key: "stage" }),
    ];

    renderWithProviders(<DetailsCard record={makeRecord()} fields={[stageField]} canWrite />);

    await waitFor(() => expect(historyRequestCount).toBeGreaterThan(0));
    expect(screen.getByTestId("field-value-stage").className).not.toContain("border-agent");
  });
});
