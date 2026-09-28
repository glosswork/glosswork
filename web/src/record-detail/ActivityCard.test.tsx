/**
 * The Activity card. The merge (already proven by `activityEvents.test.ts`) rendered through
 * `ui/ActivityEvent.tsx`, both revert affordances, the composer's exact copy, and
 * comment editing/deleting/history (FR-C5, FR-C6) all still reachable.
 */
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import type { FieldDoc } from "../api/objectTypes";
import type { AuditEventDoc } from "../api/records";
import type { CommentDoc } from "../api/comments";
import { DEFAULT_TEST_PRINCIPAL, renderWithProviders } from "../test/renderWithProviders";
import { ActivityCard } from "./ActivityCard";

const OWNER_ID = DEFAULT_TEST_PRINCIPAL.id;

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
    { value: "proposal_sent", label: "Proposal sent", description: "" },
    { value: "negotiating", label: "Negotiating", description: "" },
  ],
};

const fieldsByKey = { stage: stageField };

let nextEventId = 0;
function event(overrides: Partial<AuditEventDoc>): AuditEventDoc {
  nextEventId += 1;
  return {
    id: nextEventId,
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

function comment(overrides: Partial<CommentDoc> = {}): CommentDoc {
  return {
    id: "c-1",
    record_id: "rec-1",
    body: "Looks good.",
    author_id: OWNER_ID,
    agent_label_id: null,
    agent_label: null,
    created_at: "2026-09-12T02:46:00Z",
    updated_at: "2026-09-12T02:46:00Z",
    edited: false,
    deleted_at: null,
    principal_display_name: "Test Admin",
    ...overrides,
  };
}

let historyEvents: AuditEventDoc[] = [];
let comments: CommentDoc[] = [];
let posts: Array<{ body: string }> = [];
let patches: Array<{ ref: string; values: Record<string, unknown>; version: number }> = [];
let reverts: Array<{ eventId: number; version: number }> = [];
let versionReverts: Array<{ ref: string; target: number; version: number }> = [];
let respondConflict = false;

const server = setupServer(
  http.get("/api/v1/records/:ref/history", () => {
    return HttpResponse.json({ events: historyEvents, next_cursor: null });
  }),
  http.get("/api/v1/records/:ref/comments", () => {
    return HttpResponse.json({ comments, next_cursor: null });
  }),
  http.post("/api/v1/records/:ref/comments", async ({ request }) => {
    const body = (await request.json()) as { body: string };
    posts.push(body);
    return HttpResponse.json(comment({ id: "c-new", body: body.body }));
  }),
  http.patch("/api/v1/comments/:id", async ({ params, request }) => {
    const body = (await request.json()) as { body: string };
    const updated = comment({ id: String(params.id), body: body.body, edited: true });
    // A real `PATCH` changes what a later `GET` returns; the fixture array has to move too, or
    // the refetch this test drives through `refreshActivity` would show stale data and the
    // assertion would prove nothing about the wiring.
    comments = comments.map((existing) => (existing.id === params.id ? updated : existing));
    return HttpResponse.json(updated);
  }),
  http.delete("/api/v1/comments/:id", ({ params }) => {
    const existing = comments.find((candidate) => candidate.id === params.id);
    comments = comments.filter((candidate) => candidate.id !== params.id);
    return HttpResponse.json(
      existing
        ? { ...existing, deleted_at: "2026-09-12T03:00:00Z" }
        : comment({ id: String(params.id), deleted_at: "2026-09-12T03:00:00Z" }),
    );
  }),
  http.patch("/api/v1/records/:ref", async ({ params, request }) => {
    const body = (await request.json()) as { values: Record<string, unknown>; expected_version: number };
    patches.push({ ref: String(params.ref), values: body.values, version: body.expected_version });
    return HttpResponse.json({ key: String(params.ref), version: body.expected_version + 1 });
  }),
  http.post("/api/v1/audit-events/:eventId/revert", async ({ params, request }) => {
    const body = (await request.json()) as { expected_version: number };
    reverts.push({ eventId: Number(params.eventId), version: body.expected_version });
    if (respondConflict) {
      return HttpResponse.json(
        {
          error: {
            code: "version_conflict",
            message: "Someone else changed this record.",
            details: {
              record_key: "PROS-001",
              current_version: 9,
              supplied_version: body.expected_version,
              conflicting_fields: { stage: { your_value: "on_track", current_value: "at_risk" } },
              changed_since_your_version: ["stage"],
            },
          },
        },
        { status: 409 },
      );
    }
    return HttpResponse.json({ key: "PROS-001", version: body.expected_version + 1 });
  }),
  http.post("/api/v1/records/:ref/revert-to-version", async ({ params, request }) => {
    const body = (await request.json()) as { target_version: number; expected_version: number };
    versionReverts.push({ ref: String(params.ref), target: body.target_version, version: body.expected_version });
    return HttpResponse.json({ key: String(params.ref), version: body.expected_version + 1 });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  historyEvents = [];
  comments = [];
  posts = [];
  patches = [];
  reverts = [];
  versionReverts = [];
  respondConflict = false;
});
afterAll(() => server.close());

function renderCard(canWrite = true) {
  return renderWithProviders(
    <ActivityCard recordRef="PROS-001" recordVersion={3} fieldsByKey={fieldsByKey} canWrite={canWrite} />,
  );
}

describe("ActivityCard", () => {
  it("renders the Activity section and its heading", async () => {
    renderCard();
    expect(screen.getByRole("region", { name: "Activity" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 2, name: "Activity" })).toBeInTheDocument();
  });

  it("merges comments and version-producing writes, newest first", async () => {
    historyEvents = [
      event({ request_id: "w1", action: "create", field_key: null, ts: "2026-09-12T02:40:00Z" }),
      event({ request_id: "w1", action: "create", field_key: "stage", ts: "2026-09-12T02:40:00Z" }),
      event({
        request_id: "w2",
        action: "update",
        field_key: "stage",
        old_value: "proposal_sent",
        new_value: "negotiating",
        ts: "2026-09-12T02:50:00Z",
      }),
    ];
    comments = [comment({ id: "c-early", created_at: "2026-09-12T02:41:00Z", body: "Early." })];

    renderCard();

    await waitFor(() => expect(screen.getAllByTestId(/^activity-event-/)).toHaveLength(3));
    const ids = screen.getAllByTestId(/^activity-event-/).map((el) => el.dataset.testid);
    expect(ids).toEqual([
      "activity-event-version-w2",
      "activity-event-comment-c-early",
      "activity-event-version-w1",
    ]);
  });

  it("renders a version event's changes as pills with vocabulary-formatted values", async () => {
    historyEvents = [
      event({ request_id: "w1", action: "create", field_key: null }),
      event({ request_id: "w1", action: "create", field_key: "stage" }),
      event({
        request_id: "w2",
        action: "update",
        field_key: "stage",
        old_value: "proposal_sent",
        new_value: "negotiating",
      }),
    ];

    renderCard();

    const pill = await screen.findByTestId(`activity-change-${historyEvents[2].id}`);
    expect(pill).toHaveTextContent("Stage: Proposal sent");
    expect(pill).toHaveTextContent("Negotiating");
  });

  it("reverts one field change through the same endpoint AuditTimeline used", async () => {
    const user = userEvent.setup();
    historyEvents = [
      event({ request_id: "w1", action: "create", field_key: null }),
      event({ request_id: "w1", action: "create", field_key: "stage" }),
      event({
        request_id: "w2",
        action: "update",
        field_key: "stage",
        old_value: "proposal_sent",
        new_value: "negotiating",
      }),
    ];

    renderCard();

    await user.click(await screen.findByRole("button", { name: "Revert this change" }));
    await waitFor(() => expect(reverts).toHaveLength(1));
    expect(reverts[0]).toEqual({ eventId: historyEvents[2].id, version: 3 });
  });

  it("reverts the whole record to a prior version through the same endpoint AuditTimeline used", async () => {
    const user = userEvent.setup();
    historyEvents = [
      event({ request_id: "w1", action: "create", field_key: null }),
      event({ request_id: "w1", action: "create", field_key: "stage" }),
      event({ request_id: "w2", action: "update", field_key: "stage" }),
    ];

    renderCard();

    // Both w1 (version 1) and w2 (version 2) are below the loaded record's version 3, so each
    // carries its own "revert to this version" action (the control is on the entry, not
    // once per card) — this asserts the earlier one, scoped by its own event's test id.
    const oldest = await screen.findByTestId("activity-event-version-w1");
    await user.click(within(oldest).getByRole("button", { name: "Revert record to this version" }));
    await waitFor(() => expect(versionReverts).toHaveLength(1));
    expect(versionReverts[0]).toEqual({ ref: "PROS-001", target: 1, version: 3 });
  });

  it("opens MergeConflictDialog on a 409 from a revert, exactly as the write path does elsewhere", async () => {
    const user = userEvent.setup();
    respondConflict = true;
    historyEvents = [
      event({ request_id: "w1", action: "create", field_key: null }),
      event({ request_id: "w1", action: "create", field_key: "stage" }),
      event({ request_id: "w2", action: "update", field_key: "stage" }),
    ];

    renderCard();

    await user.click(await screen.findByRole("button", { name: "Revert this change" }));
    expect(await screen.findByTestId("merge-conflict-dialog")).toBeInTheDocument();
  });

  it("says older activity is not shown when the bounded walk was truncated", async () => {
    // 101 pages' worth is impractical to fixture; the walk's own unit test
    // (`recordActivityPages.test.ts`) proves the cap. This proves the card's own statement wires
    // to that flag, by intercepting every page as full and truncated.
    server.use(
      http.get("/api/v1/records/:ref/history", () =>
        HttpResponse.json({ events: [], next_cursor: "more" }),
      ),
    );

    renderCard();

    expect(await screen.findByText("Older activity is not shown.")).toBeInTheDocument();
  });

  it("posts through the composer with its exact copy", async () => {
    const user = userEvent.setup();
    renderCard();

    const composer = screen.getByPlaceholderText("Reply. Agents read this too.");
    await user.type(composer, "Sounds good.");
    await user.click(screen.getByRole("button", { name: "Post" }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({ body: "Sounds good." });
  });

  it("lets the author edit and delete their own comment, and shows its edit history", async () => {
    const user = userEvent.setup();
    comments = [comment({ edited: true })];
    historyEvents = [
      event({
        request_id: "w-c",
        entity_type: "comment",
        entity_id: "c-1",
        action: "update",
        field_key: null,
        old_value: "Looked good.",
        new_value: "Looks good.",
      }),
    ];

    renderCard();

    expect(await screen.findByTestId("body-c-1")).toHaveTextContent("Looks good.");

    await user.click(screen.getByRole("button", { name: "edited" }));
    expect(await screen.findByRole("list", { name: "Prior versions" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit" }));
    const textbox = screen.getByRole("textbox", { name: "Edit comment" });
    await user.clear(textbox);
    await user.type(textbox, "Looks great.");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByTestId("body-c-1")).toHaveTextContent("Looks great."));

    await user.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.queryByTestId("body-c-1")).not.toBeInTheDocument());
  });

  it("hides the composer and every action below write", async () => {
    comments = [comment()];
    renderCard(false);

    await screen.findByTestId(`body-${comments[0].id}`);
    expect(screen.queryByPlaceholderText("Reply. Agents read this too.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
  });
});
