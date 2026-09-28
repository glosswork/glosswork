/**
 * The record page, rendered end to end through `RecordPage` with a real router and a
 * mocked API: `RecordHeader`, `DetailsCard` and `ActivityCard` assembled by `RecordDetailView`,
 * exactly as a browser would load them.
 *
 * This file is the integration layer. It does not re-prove what a component's own unit test
 * already proves in isolation (`RecordHeader.test.tsx`, `DetailsCard.test.tsx`,
 * `ActivityCard.test.tsx`, `activityEvents.test.ts`, `agentBar.test.ts`,
 * `recordActivityPages.test.ts`, `ui/Markdown.test.tsx`); it proves the wiring those tests cannot
 * see from inside a single component: that `RecordPage`'s real fetches reach the right props,
 * that `canWrite` is derived once in `RecordDetailView` and reaches both cards and the
 * `ReadOnlyBanner` together, and that the merge, the write path and both reverts survive a real
 * mount rather than only a hand-built prop.
 *
 * Deliberately not asserted here:
 *
 * - The `<h1>` being the record key, `Fields`/`Linked records`/`Comments`/`Audit timeline` as
 *   separate sections, and `AuditTimeline`'s manual "Load more": shapes the record page does not
 *   have.
 * - `<br>`-counting and heading-appearance assertions. Both are about `ui/Markdown.tsx`'s own
 *   mechanism, which `ui/Markdown.test.tsx` already covers exhaustively and independently.
 *   Re-deriving those assertions against this page's specific DOM would not measure anything
 *   `Markdown.test.tsx` doesn't already measure; the two markdown-wiring tests below (a field
 *   value, a comment body) prove the thing this file is actually responsible for — that the real
 *   page routes real content through `Markdown` at all — without duplicating the component's own
 *   contract.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { Route, Routes } from "react-router-dom";
import { RecordPage } from "../routes/RecordPage";
import { DEFAULT_TEST_PRINCIPAL, renderWithProviders } from "../test/renderWithProviders";
import type { ObjectTypeDetail, ObjectTypeSummary } from "../api/objectTypes";
import type { CommentDoc } from "../api/comments";
import type {
  AuditEventDoc,
  LinkBody,
  LinkedRecordRef,
  QueryBody,
  QueryResult,
  RecordDoc,
} from "../api/records";

const TEST_PRINCIPAL_ID = DEFAULT_TEST_PRINCIPAL.id;

const objectType: ObjectTypeDetail = {
  key: "initiative",
  name: "Initiative",
  name_plural: "Initiatives",
  description: "A funded, sponsored workstream.",
  key_prefix: "INIT",
  record_count: 1,
  field_count: 4,
  your_access: "admin",
  // No display field chosen, so the header falls back to the record key. The dedicated
  // display-value test below overrides this per-test rather than changing the shared fixture,
  // which would force every other assertion in this file to be rewritten around a second title.
  display_field_key: null,
  effective_display_field_key: null,
  fields: [
    {
      key: "status",
      name: "Status",
      type: "single_select",
      description: "Where the initiative stands.",
      required: true,
      unique: false,
      indexed: true,
      embed: false,
      default: null,
      config: {},
      position: 0,
      operators: ["eq", "neq", "in"],
      display_eligible: true,
      options: [
        { value: "on_track", label: "On Track", description: "Progressing as planned." },
        { value: "at_risk", label: "At Risk", description: "Needs attention." },
      ],
    },
    {
      key: "owner",
      name: "Owner",
      type: "relation",
      description: "Who owns this initiative.",
      required: false,
      unique: false,
      indexed: false,
      embed: false,
      default: null,
      config: {},
      position: 1,
      operators: [],
      display_eligible: false,
      target_type_key: "person",
      cardinality: "one",
      inverse_field_key: "owned_initiatives",
    },
    {
      key: "tasks",
      name: "Tasks",
      type: "relation",
      description: "Tasks under this initiative.",
      required: false,
      unique: false,
      indexed: false,
      embed: false,
      default: null,
      config: {},
      position: 2,
      operators: [],
      display_eligible: false,
      target_type_key: "task",
      cardinality: "many",
      inverse_field_key: "initiative",
    },
    {
      key: "notes",
      name: "Notes",
      type: "long_text",
      description: "Free-form operator notes about the initiative.",
      required: false,
      unique: false,
      indexed: false,
      embed: true,
      default: null,
      config: {},
      position: 3,
      operators: ["contains", "is_null", "is_not_null"],
      display_eligible: true,
    },
  ],
  system_fields: [],
};

/**
 * The picker's search target: `tasks`' `target_type_key` (`objectType.fields[2]`). `title`
 * carries `contains` so the picker's search input has something to show, and
 * `effective_display_field_key` names it, exactly as DD-23 requires this test file to read rather
 * than re-derive.
 */
const taskObjectType: ObjectTypeDetail = {
  key: "task",
  name: "Task",
  name_plural: "Tasks",
  description: "A unit of work under an initiative.",
  key_prefix: "TASK",
  record_count: 2,
  field_count: 1,
  your_access: "read",
  display_field_key: "title",
  effective_display_field_key: "title",
  fields: [
    {
      key: "title",
      name: "Title",
      type: "short_text",
      description: "What needs doing.",
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

/**
 * `owner`'s target (`objectType.fields[1]`). The picker tests all address `tasks`, not `owner`;
 * this fixture exists so the orientation list and the object-type route have a real answer for
 * `owner`'s target too. The two `owner` tests below (the empty-cardinality-one link and the
 * link-error path) pick a `peopleStore` option through this fixture's `contains`-capable `name`
 * field.
 */
const personObjectType: ObjectTypeDetail = {
  key: "person",
  name: "Person",
  name_plural: "People",
  description: "Someone the workspace coordinates with.",
  key_prefix: "PERSON",
  record_count: 1,
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

/** The orientation list (`useObjectTypes`, `GET /api/v1/object-types`): every type this caller
 * can read, `task` and `person` included, so the picker's "is the target in the list I can
 * already see" check has a real answer rather than an empty one. */
const objectTypesList: ObjectTypeSummary[] = [
  {
    key: objectType.key,
    name: objectType.name,
    description: objectType.description,
    key_prefix: objectType.key_prefix,
    record_count: objectType.record_count,
    field_count: objectType.field_count,
    your_access: objectType.your_access,
  },
  {
    key: taskObjectType.key,
    name: taskObjectType.name,
    description: taskObjectType.description,
    key_prefix: taskObjectType.key_prefix,
    record_count: taskObjectType.record_count,
    field_count: taskObjectType.field_count,
    your_access: taskObjectType.your_access,
  },
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

/** `taskObjectType`, but `title` no longer declares `contains`: the picker must still
 * list options, only without the search input the field can no longer serve. */
function taskObjectTypeWithoutContains(): ObjectTypeDetail {
  return {
    ...taskObjectType,
    fields: [{ ...taskObjectType.fields[0], operators: ["eq", "neq", "is_null", "is_not_null"] }],
  };
}

/** The `forbidden` envelope shape the error-surface tests' `failWith` uses below, copied rather
 * than shared because that helper is scoped to its own `describe` and fixes `422`: the picker's
 * unreadable-target test needs a `403`. */
const FORBIDDEN_ENVELOPE = {
  error: {
    code: "forbidden",
    message:
      "Your access to object type 'task' is 'none'; this call needs at least 'read'. Ask an " +
      "administrator of 'task', or a system administrator, to raise it.",
    details: { object_type: "task", held: "none", required: "read" },
  },
};

/**
 * `ui/Alert.tsx:52` renders a `forbidden` envelope's own `message` in place of whatever `title`
 * the caller passed, so a `forbidden` 403 can never show the picker's fixed "Could not load
 * records." copy. The picker's error test needs an error `Alert` treats like any other code (a
 * non-`forbidden` envelope) to exercise that branch instead.
 */
const INTERNAL_ERROR_ENVELOPE = {
  error: {
    code: "internal",
    message: "Something went wrong.",
    details: {},
  },
};

/**
 * Both shapes the markdown rendering distinguishes, in one value: a SINGLE newline between
 * lines one and two (which `remark-breaks` keeps as a visible line break) and a BLANK line before
 * line three (a paragraph break under either). Kept here only as the fixture the two markdown-
 * wiring tests below exercise; the mechanism itself is `ui/Markdown.test.tsx`'s job now.
 */
const MULTILINE_NOTE = "First line.\nSecond line.\n\nThird line, after a blank one.";

/** A real comment body observed in the audit trail, written as markdown. */
const MARKDOWN_COMMENT = "Another comment\n\n- with\n- markdown\n\n## title";

/** End to end rather than at the primitive: hostile content in a real comment. */
const HOSTILE_COMMENT = '<script>alert(1)</script> and <img src=x onerror="alert(2)">';

const initialComments: CommentDoc[] = [
  {
    id: "c1",
    record_id: "rec-1",
    body: "First comment.",
    author_id: TEST_PRINCIPAL_ID,
    agent_label_id: null,
    agent_label: null,
    created_at: "2026-08-20T09:00:00",
    updated_at: "2026-08-20T09:00:00",
    edited: false,
    deleted_at: null,
    principal_display_name: null,
  },
  {
    id: "c2",
    record_id: "rec-1",
    body: "Second comment (edited).",
    author_id: TEST_PRINCIPAL_ID,
    agent_label_id: "label-uuid-1",
    agent_label: "claude-code",
    created_at: "2026-08-21T09:00:00",
    updated_at: "2026-08-22T09:00:00",
    edited: true,
    deleted_at: null,
    principal_display_name: null,
  },
];

const initialLinks: Record<string, LinkedRecordRef[]> = {
  owner: [],
  tasks: [{ key: "TASK-1", id: "task-1-id", display: "Draft the migration plan" }],
};

/**
 * `{id, key, data}`, the shape both the query handler and the link handler below read.
 * `TASK-1`'s id matches `initialLinks.tasks` above, so a query result and an already-linked pill
 * agree about which record that is. Reused for `peopleStore` below: both stores answer the same
 * query and link handlers, keyed only by object type.
 */
interface FixtureRecordRow {
  id: string;
  key: string;
  data: Record<string, unknown>;
}

const tasksStore: FixtureRecordRow[] = [
  { id: "task-1-id", key: "TASK-1", data: { title: "Draft the migration plan" } },
  { id: "task-2-id", key: "TASK-2", data: { title: "Write the rollback notes" } },
];

/**
 * `owner` targets `person`, and its two tests below (the empty-cardinality-one link and the
 * link-error path) pick a person option rather than a task one. `PERSON-1` is an id/key no other
 * fixture in this suite uses.
 */
const peopleStore: FixtureRecordRow[] = [
  { id: "person-1-id", key: "PERSON-1", data: { name: "Dana Okonkwo" } },
];

function fixtureRecordDoc(row: FixtureRecordRow): RecordDoc {
  return {
    id: row.id,
    key: row.key,
    version: 1,
    created_at: "2026-08-20T09:00:00",
    created_by: TEST_PRINCIPAL_ID,
    updated_at: "2026-08-20T09:00:00",
    updated_by: TEST_PRINCIPAL_ID,
    updated_by_agent_label_id: null,
    deleted_at: null,
    comment_count: 0,
    last_comment_at: null,
    data: row.data,
  };
}

/**
 * The link handler's `display` resolution: `display` is resolved from the task store and
 * `peopleStore` rather than always null. `PERSON-1` has a real title, so the "links a record
 * into an empty cardinality-one relation" test below asserts a key-plus-title pill rather than the
 * bare-key fallback. A key neither store ever seeded (`NOPE-1`) still resolves to `null`.
 */
function resolveLinkDisplay(key: string): unknown {
  const task = tasksStore.find((row) => row.key === key);
  if (task) return task.data.title;
  const person = peopleStore.find((row) => row.key === key);
  if (person) return person.data.name;
  return null;
}

let nextEventId = 0;
/**
 * One audit row, with the fields no test below varies defaulted. Mirrors the `event()` helper
 * already established in `DetailsCard.test.tsx` / `ActivityCard.test.tsx`, so a reader who has
 * seen either already recognizes this one.
 */
function event(requestId: string, overrides: Partial<AuditEventDoc> = {}): AuditEventDoc {
  nextEventId += 1;
  return {
    id: nextEventId,
    ts: "2026-08-20T10:00:00",
    request_id: requestId,
    principal_id: TEST_PRINCIPAL_ID,
    principal_type: "service_account",
    agent_label_id: null,
    agent_label: null,
    auth_method: "pat",
    surface: "rest",
    entity_type: "record",
    entity_id: "rec-1",
    record_id: "rec-1",
    object_type_id: "obj-1",
    action: "update",
    field_key: null,
    old_value: null,
    new_value: null,
    note: null,
    principal_display_name: null,
    record_key: "INIT-1",
    ...overrides,
  };
}

/**
 * Two pages, anchored by a `create` (so `groupAuditEvents` can count versions at all) and carrying
 * one write the Activity card must drop: `req-comment-edit` is a `comment`-entity audit row (the
 * server logs one for every comment edit), which produced no version and must not become a bodyless
 * third event alongside `c2`'s own row.
 *
 * created (v1) -> req-update1 moves status to `at_risk` (v2) -> a comment edit is logged and
 * produces no version -> (page 2) req-update2 moves status back to `on_track` (v3), matching the
 * live record's own `data.status`.
 */
const historyPage1: AuditEventDoc[] = [
  event("req-create", { action: "create", field_key: null, ts: "2026-08-20T10:00:00" }),
  event("req-create", {
    action: "create",
    field_key: "status",
    old_value: null,
    new_value: "on_track",
    ts: "2026-08-20T10:00:00",
  }),
  event("req-update1", {
    action: "update",
    field_key: "status",
    old_value: "on_track",
    new_value: "at_risk",
    ts: "2026-08-21T10:00:00",
  }),
  event("req-comment-edit", {
    entity_type: "comment",
    entity_id: "c2",
    action: "update",
    field_key: null,
    old_value: "Second comment.",
    new_value: "Second comment (edited).",
    ts: "2026-08-22T10:00:00",
  }),
];

const historyPage2: AuditEventDoc[] = [
  event("req-update2", {
    action: "update",
    field_key: "status",
    old_value: "at_risk",
    new_value: "on_track",
    ts: "2026-08-23T10:00:00",
  }),
];

let commentsStore: CommentDoc[] = [];
let recordData: Record<string, unknown> = {};
let recordVersion = 3;
let patchRequests: { ref: string; body: unknown }[] = [];
let versionConflictOnNextPatch = false;
let linksStore: Record<string, LinkedRecordRef[]> = {};
let nextCommentId = 0;
let fieldReverts: { eventId: number; expectedVersion: number }[] = [];
let versionReverts: { ref: string; target: number; expectedVersion: number }[] = [];
/** Every `link_records` request, in order. */
let linkRequests: { fieldKey: string; body: LinkBody }[] = [];
/** Every `query_records` request against any object type, in order. */
let queryRequests: { objectTypeKey: string; body: QueryBody }[] = [];
/** Every `describe_object_type` request's key, in order. */
let objectTypeKeyRequests: string[] = [];

const server = setupServer(
  // Branches on `params.key` so `task` and `person` answer as themselves; `initiative` (and
  // anything else) answers with the shared object type fixture.
  http.get("/api/v1/object-types/:key", ({ params }) => {
    const key = params.key as string;
    objectTypeKeyRequests.push(key);
    if (key === "task") return HttpResponse.json(taskObjectType);
    if (key === "person") return HttpResponse.json(personObjectType);
    return HttpResponse.json(objectType);
  }),
  // The orientation list (`useObjectTypes`), which the picker reads to decide whether a
  // relation's target is one this caller can read at all.
  http.get("/api/v1/object-types", () => HttpResponse.json(objectTypesList)),
  // The picker's search, naive case-insensitive `contains` over whichever store matches the
  // object type key. Every request body is recorded so the picker tests can assert on it.
  http.post("/api/v1/object-types/:key/query", async ({ request, params }) => {
    const objectTypeKey = params.key as string;
    const body = (await request.json()) as QueryBody;
    queryRequests.push({ objectTypeKey, body });
    const rows =
      objectTypeKey === "task" ? tasksStore : objectTypeKey === "person" ? peopleStore : [];
    const filter = body.filter as { field?: string; value?: unknown } | null | undefined;
    const term = filter && typeof filter.value === "string" ? filter.value.toLowerCase() : null;
    const matched =
      term === null
        ? rows
        : rows.filter((row) =>
            String((row.data as Record<string, unknown>)[filter?.field ?? ""] ?? "")
              .toLowerCase()
              .includes(term),
          );
    const result: QueryResult = {
      records: matched.slice(0, body.limit).map(fixtureRecordDoc),
      total_count: matched.length,
      next_cursor: null,
      truncated: false,
    };
    return HttpResponse.json(result);
  }),
  http.get("/api/v1/records/:ref", () =>
    HttpResponse.json({
      id: "rec-1",
      key: "INIT-1",
      version: recordVersion,
      created_at: "2026-08-20T09:00:00",
      created_by: TEST_PRINCIPAL_ID,
      updated_at: "2026-08-23T10:00:00",
      updated_by: TEST_PRINCIPAL_ID,
      updated_by_agent_label_id: null,
      deleted_at: null,
      comment_count: commentsStore.length,
      last_comment_at: null,
      data: recordData,
      links: linksStore,
    }),
  ),
  // The one write path: the record page's per-field edit and the
  // table's inline cell edit are the same `useInlineCellEdit`, ending here.
  http.patch("/api/v1/records/:ref", async ({ request, params }) => {
    const body = (await request.json()) as {
      values: Record<string, unknown>;
      expected_version: number;
    };
    const ref = params.ref as string;
    patchRequests.push({ ref, body });
    if (versionConflictOnNextPatch) {
      versionConflictOnNextPatch = false;
      return HttpResponse.json(
        {
          error: {
            code: "version_conflict",
            message: "stale write",
            details: {
              record_key: ref,
              current_version: 9,
              supplied_version: body.expected_version,
              conflicting_fields: {
                status: { your_value: body.values.status, current_value: "at_risk" },
              },
              changed_since_your_version: ["status"],
            },
          },
        },
        { status: 409 },
      );
    }
    recordData = { ...recordData, ...body.values };
    recordVersion = body.expected_version + 1;
    return HttpResponse.json({ id: "rec-1", key: ref, version: recordVersion, data: recordData });
  }),
  http.get("/api/v1/records/:ref/comments", () =>
    HttpResponse.json({ comments: commentsStore, next_cursor: null }),
  ),
  http.post("/api/v1/records/:ref/comments", async ({ request }) => {
    const body = (await request.json()) as { body: string };
    nextCommentId += 1;
    const created: CommentDoc = {
      id: `new-${nextCommentId}`,
      record_id: "rec-1",
      body: body.body,
      author_id: TEST_PRINCIPAL_ID,
      agent_label_id: null,
      agent_label: null,
      created_at: "2026-08-24T10:00:00",
      updated_at: "2026-08-24T10:00:00",
      edited: false,
      deleted_at: null,
      principal_display_name: null,
    };
    commentsStore = [...commentsStore, created];
    return HttpResponse.json(created);
  }),
  http.patch("/api/v1/comments/:commentId", async ({ request, params }) => {
    const body = (await request.json()) as { body: string };
    commentsStore = commentsStore.map((comment) =>
      comment.id === params.commentId
        ? { ...comment, body: body.body, edited: true, updated_at: "2026-08-24T11:00:00" }
        : comment,
    );
    const updated = commentsStore.find((comment) => comment.id === params.commentId);
    return HttpResponse.json(updated);
  }),
  http.delete("/api/v1/comments/:commentId", ({ params }) => {
    const deleted = commentsStore.find((comment) => comment.id === params.commentId);
    commentsStore = commentsStore.filter((comment) => comment.id !== params.commentId);
    return HttpResponse.json({ ...deleted, deleted_at: "2026-08-24T12:00:00" });
  }),
  // Both pages, forward-only, exactly as the server paginates.
  http.get("/api/v1/records/:ref/history", ({ request }) => {
    const url = new URL(request.url);
    const cursor = url.searchParams.get("cursor");
    if (cursor === "cursor-2") {
      return HttpResponse.json({ events: historyPage2, next_cursor: null });
    }
    return HttpResponse.json({ events: historyPage1, next_cursor: "cursor-2" });
  }),
  http.post("/api/v1/records/:ref/links/:fieldKey", async ({ request, params }) => {
    const body = (await request.json()) as LinkBody;
    const fieldKey = params.fieldKey as string;
    linkRequests.push({ fieldKey, body });
    const existing = linksStore[fieldKey] ?? [];
    linksStore = {
      ...linksStore,
      [fieldKey]: [
        ...existing,
        // Resolved from `tasksStore`/`peopleStore` rather than always
        // `null`. A key neither store seeded (`NOPE-1`) still resolves to `null`.
        ...body.to_records.map((ref) => ({ key: ref, id: `${ref}-id`, display: resolveLinkDisplay(ref) })),
      ],
    };
    return HttpResponse.json({ field_key: fieldKey, linked: body.to_records });
  }),
  http.delete("/api/v1/records/:ref/links/:fieldKey", async ({ request, params }) => {
    const body = (await request.json()) as { to_records: string[] };
    const fieldKey = params.fieldKey as string;
    const existing = linksStore[fieldKey] ?? [];
    linksStore = {
      ...linksStore,
      [fieldKey]: existing.filter((item) => !body.to_records.includes(item.key)),
    };
    return HttpResponse.json({ field_key: fieldKey, unlinked_count: body.to_records.length });
  }),
  // The two reverts, both `revert_field_change` / `revert_to_version` (`api/audit.ts`), exercised
  // end to end: clicking them must reach the write path, not only render the controls.
  http.post("/api/v1/audit-events/:eventId/revert", async ({ request, params }) => {
    const body = (await request.json()) as { expected_version: number };
    fieldReverts.push({ eventId: Number(params.eventId), expectedVersion: body.expected_version });
    recordVersion = body.expected_version + 1;
    return HttpResponse.json({ id: "rec-1", key: "INIT-1", version: recordVersion, data: recordData });
  }),
  http.post("/api/v1/records/:ref/revert-to-version", async ({ request, params }) => {
    const body = (await request.json()) as { target_version: number; expected_version: number };
    versionReverts.push({
      ref: params.ref as string,
      target: body.target_version,
      expectedVersion: body.expected_version,
    });
    recordVersion = body.expected_version + 1;
    return HttpResponse.json({ id: "rec-1", key: "INIT-1", version: recordVersion, data: recordData });
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
beforeEach(() => {
  recordData = { status: "on_track", notes: MULTILINE_NOTE };
  recordVersion = 3;
  patchRequests = [];
  versionConflictOnNextPatch = false;
  commentsStore = initialComments.map((comment) => ({ ...comment }));
  linksStore = { owner: [...initialLinks.owner], tasks: [...initialLinks.tasks] };
  nextCommentId = 0;
  fieldReverts = [];
  versionReverts = [];
  linkRequests = [];
  queryRequests = [];
  objectTypeKeyRequests = [];
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderRecordPage() {
  return renderWithProviders(
    <Routes>
      <Route path="/:objectTypeKey/:recordKey" element={<RecordPage />} />
    </Routes>,
    { route: "/initiative/INIT-1" },
  );
}

describe("RecordDetailView", () => {
  it("renders every field with its current value, honoring select option labels", async () => {
    renderRecordPage();

    expect(await screen.findByRole("heading", { name: "INIT-1", level: 1 })).toBeInTheDocument();
    const fieldList = screen.getByTestId("field-list");
    expect(within(fieldList).getByText("On Track")).toBeInTheDocument();
    expect(within(fieldList).queryByText("on_track")).not.toBeInTheDocument();
  });

  /**
   * Through the real API round trip rather than a hand-built `RecordHeader` prop: proves
   * `RecordPage`'s fetched `effective_display_field_key` and `record.data` actually reach the
   * title, which no unit test can see from inside one component.
   */
  it("titles the page with the object type's chosen display field value, read through the real API (DD-23)", async () => {
    server.use(
      http.get("/api/v1/object-types/:key", () =>
        HttpResponse.json({ ...objectType, display_field_key: "status", effective_display_field_key: "status" }),
      ),
    );
    renderRecordPage();

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("On Track");
    expect(heading).not.toHaveTextContent("INIT-1");
    // The key does not vanish; it demotes to the chip beside the version (RecordHeader.tsx).
    expect(screen.getByTestId("record-key-chip")).toHaveTextContent("INIT-1");
  });

  describe("relation fields (one row per field, a pill per link)", () => {
    it("shows an empty-state affordance for an unlinked cardinality-one relation", async () => {
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const ownerField = screen.getByTestId("relation-field-owner");
      expect(within(ownerField).getByText("No linked record.")).toBeInTheDocument();
      expect(within(ownerField).getByRole("button", { name: "Link a record" })).toBeInTheDocument();
    });

    it("lists linked records for a cardinality-many relation with an add-another affordance", async () => {
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const tasksField = screen.getByTestId("relation-field-tasks");
      // The pill carries the key and the target's display value together: the
      // pill is the only place on this page either appears for a linked record, so dropping the
      // key would take the record's handle off the one screen that is about it.
      expect(
        within(tasksField).getByRole("link", { name: "TASK-1 Draft the migration plan" }),
      ).toBeInTheDocument();
      expect(within(tasksField).getByRole("button", { name: "Add another" })).toBeInTheDocument();
    });

    it("links a record into an empty cardinality-one relation", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const ownerField = screen.getByTestId("relation-field-owner");
      await user.click(within(ownerField).getByRole("button", { name: "Link a record" }));
      await user.click(
        await within(ownerField).findByRole("option", { name: "PERSON-1 Dana Okonkwo" }),
      );

      expect(
        await within(ownerField).findByRole("link", { name: "PERSON-1 Dana Okonkwo" }),
      ).toBeInTheDocument();
      expect(
        within(ownerField).queryByRole("button", { name: "Link a record" }),
      ).not.toBeInTheDocument();
    });

    it("unlinks a record from a cardinality-many relation", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const tasksField = screen.getByTestId("relation-field-tasks");
      await user.click(
        within(tasksField).getByRole("button", { name: "Unlink TASK-1 Draft the migration plan" }),
      );

      expect(await within(tasksField).findByText("No linked record.")).toBeInTheDocument();
    });

    it("offers no edit affordance on a relation field, which its own widget owns", async () => {
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      expect(screen.queryByRole("button", { name: "Edit Owner" })).not.toBeInTheDocument();
    });
  });

  /**
   * The record page edits through `useInlineCellEdit` — the same write path the table's
   * `EditableCell` uses, including its 409 handling — rather than a second one.
   */
  describe("editing through the write path", () => {
    it("edits a select field in place and shows the new value", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const fieldList = screen.getByTestId("field-list");
      expect(within(fieldList).getByText("On Track")).toBeInTheDocument();

      await user.click(within(fieldList).getByRole("button", { name: "Edit Status" }));
      await user.selectOptions(screen.getByLabelText("Status value for INIT-1"), "at_risk");
      await user.click(within(fieldList).getByRole("button", { name: "Save" }));

      await waitFor(() => expect(patchRequests).toHaveLength(1));
      // The shared write path: `expected_version` from the loaded record, `force: false`.
      expect(patchRequests[0]).toEqual({
        ref: "INIT-1",
        body: { values: { status: "at_risk" }, expected_version: 3, force: false },
      });
      expect(await within(screen.getByTestId("field-list")).findByText("At Risk")).toBeInTheDocument();
    });

    it("edits a long_text field in place, in a full-width textarea", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      await user.click(
        within(screen.getByTestId("field-list")).getByRole("button", { name: "Edit Notes" }),
      );
      const textarea = screen.getByLabelText("Notes value for INIT-1");
      expect(textarea.tagName).toBe("TEXTAREA");
      expect(textarea).toHaveClass("w-full");
      expect(textarea).toHaveValue(MULTILINE_NOTE);

      await user.clear(textarea);
      await user.type(textarea, "Rewritten.");
      await user.click(within(screen.getByTestId("field-list")).getByRole("button", { name: "Save" }));

      await waitFor(() => expect(patchRequests).toHaveLength(1));
      expect(patchRequests[0].body).toEqual({
        values: { notes: "Rewritten." },
        expected_version: 3,
        force: false,
      });
    });

    it("cancels an edit without writing anything", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const fieldList = screen.getByTestId("field-list");
      await user.click(within(fieldList).getByRole("button", { name: "Edit Status" }));
      await user.selectOptions(screen.getByLabelText("Status value for INIT-1"), "at_risk");
      await user.click(within(fieldList).getByRole("button", { name: "Cancel" }));

      expect(patchRequests).toHaveLength(0);
      expect(within(screen.getByTestId("field-list")).getByText("On Track")).toBeInTheDocument();
    });

    it("routes a 409 to the same MergeConflictDialog the table uses, and resubmits through it", async () => {
      const user = userEvent.setup();
      versionConflictOnNextPatch = true;
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const fieldList = screen.getByTestId("field-list");
      await user.click(within(fieldList).getByRole("button", { name: "Edit Status" }));
      await user.selectOptions(screen.getByLabelText("Status value for INIT-1"), "at_risk");
      await user.click(within(fieldList).getByRole("button", { name: "Save" }));

      const dialog = await screen.findByTestId("merge-conflict-dialog");
      expect(within(dialog).getByTestId("conflict-field-status")).toBeInTheDocument();

      await user.click(within(dialog).getByRole("button", { name: "Resubmit" }));
      await waitFor(() => expect(screen.queryByTestId("merge-conflict-dialog")).not.toBeInTheDocument());
      // Two writes: the one that conflicted, and the resubmit at the server's current_version.
      expect(patchRequests).toHaveLength(2);
      expect((patchRequests[1].body as { expected_version: number }).expected_version).toBe(9);
    });
  });

  /**
   * The Activity card's feed is `comments + versionProducingGroups`, never a concatenation.
   * `activityEvents.test.ts` already proves the merge as a pure function; these two prove it
   * survives a real mount, over a fixture with a real second page and a real comment-edit audit
   * row that must not become a second, bodyless event.
   */
  describe("the Activity card: comments and versions merged, newest first", () => {
    it("drains both history pages automatically and orders every event newest first, with no manual pagination control", async () => {
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      // req-update2 lives on page two; its presence with no click proves the bounded walk
      // ran to the end before anything rendered, with no "Load more" affordance.
      await waitFor(() =>
        expect(screen.getAllByTestId(/^activity-event-/)).toHaveLength(5),
      );
      const ids = screen
        .getAllByTestId(/^activity-event-/)
        .map((node) => node.dataset.testid);
      expect(ids).toEqual([
        "activity-event-version-req-update2",
        "activity-event-version-req-update1",
        "activity-event-comment-c2",
        "activity-event-version-req-create",
        "activity-event-comment-c1",
      ]);
      expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
    });

    it("counts events as exactly comments plus version-producing writes, never a bodyless duplicate of an edited comment", async () => {
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      // 2 live comments + 3 version-producing groups (create, req-update1, req-update2).
      // `req-comment-edit` produced no version and must contribute nothing of its own.
      await waitFor(() => expect(screen.getAllByTestId(/^activity-event-/)).toHaveLength(2 + 3));
      expect(screen.queryByTestId("activity-event-version-req-comment-edit")).not.toBeInTheDocument();

      const c2Row = screen.getByTestId("activity-event-comment-c2");
      expect(within(c2Row).getByText("Second comment (edited).")).toBeInTheDocument();
      expect(within(c2Row).getByRole("button", { name: "edited" })).toBeInTheDocument();
      const c1Row = screen.getByTestId("activity-event-comment-c1");
      expect(within(c1Row).queryByRole("button", { name: "edited" })).not.toBeInTheDocument();
    });

    it("shows prior-body history for an edited comment when its marker is clicked (FR-C6)", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      const c2Row = await screen.findByTestId("activity-event-comment-c2");

      await user.click(within(c2Row).getByRole("button", { name: "edited" }));

      const historyList = await within(c2Row).findByRole("list", { name: "Prior versions" });
      expect(within(historyList).getByText(/Second comment \(edited\)\./)).toBeInTheDocument();
    });
  });

  describe("comment CRUD from the Activity card (FR-C5, FR-C6)", () => {
    it("adds a comment and shows it in the thread without a full page reload", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const composer = screen.getByPlaceholderText("Reply. Agents read this too.");
      await user.type(composer, "A brand new comment.");
      await user.click(screen.getByRole("button", { name: "Post" }));

      expect(await screen.findByText("A brand new comment.")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("Reply. Agents read this too.")).toHaveValue("");
    });

    it("edits the caller's own comment in place", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      const c1Row = await screen.findByTestId("activity-event-comment-c1");

      await user.click(within(c1Row).getByRole("button", { name: "Edit" }));
      const textbox = within(c1Row).getByLabelText("Edit comment");
      await user.clear(textbox);
      await user.type(textbox, "First comment, revised.");
      await user.click(within(c1Row).getByRole("button", { name: "Save" }));

      expect(await screen.findByText("First comment, revised.")).toBeInTheDocument();
      expect(screen.queryByText("First comment.")).not.toBeInTheDocument();
    });

    it("deletes a comment and removes it from the thread", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      const c1Row = await screen.findByTestId("activity-event-comment-c1");

      await user.click(within(c1Row).getByRole("button", { name: "Delete" }));

      await waitFor(() => expect(screen.queryByTestId("activity-event-comment-c1")).not.toBeInTheDocument());
      expect(screen.getByTestId("activity-event-comment-c2")).toBeInTheDocument();
    });
  });

  /**
   * Markdown rendering, kept as wiring proof: does the real page actually route a
   * field value and a comment body through `ui/Markdown.tsx`, rather than printing them as plain
   * text. The mechanism itself (line breaks, headings, sanitization) is `ui/Markdown.test.tsx`'s
   * job and is not re-measured here.
   */
  describe("markdown rendering is wired to the real page", () => {
    it("renders a long_text field value's markdown as structure, not as syntax", async () => {
      recordData = { status: "on_track", notes: MARKDOWN_COMMENT };
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const value = await screen.findByTestId("field-value-notes");
      expect(value.querySelectorAll("li")).toHaveLength(2);
      expect(value.querySelector(".md-h")?.textContent).toBe("title");
      expect(value.textContent).not.toContain("## title");
    });

    it("renders a comment body's markdown as structure, in a container a <p> could not be", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      await user.click(screen.getByPlaceholderText("Reply. Agents read this too."));
      await user.paste(MARKDOWN_COMMENT);
      await user.click(screen.getByRole("button", { name: "Post" }));

      const body = await screen.findByTestId("body-new-1");
      expect(body.querySelectorAll("li")).toHaveLength(2);
      expect(body.querySelector(".md-h")?.textContent).toBe("title");
      expect(body.tagName).not.toBe("P");
      expect(body.querySelector("ul")).not.toBeNull();
    });

    it("a hostile comment renders as literal text on the real page", async () => {
      const user = userEvent.setup();
      const { container } = renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      await user.click(screen.getByPlaceholderText("Reply. Agents read this too."));
      await user.paste(HOSTILE_COMMENT);
      await user.click(screen.getByRole("button", { name: "Post" }));

      const body = await screen.findByTestId("body-new-1");
      expect(body.textContent).toContain("<script>alert(1)</script>");
      expect(container.querySelector("script")).toBeNull();
      expect(container.querySelector("img")).toBeNull();
      expect(container.querySelectorAll("[onerror]")).toHaveLength(0);
    });
  });

  /**
   * Both reverts live on the version entry — "Revert record to this version" on the
   * event itself, "Revert this change" on each change pill — and both still end at
   * `revert_field_change` / `revert_to_version` (`api/audit.ts`). Asserting only the controls'
   * *presence* would leave the write path unproven at this level, so both are clicked here.
   */
  describe("reverts from the Activity card (DD-21)", () => {
    it("reverts one field change through revert_field_change, the same endpoint as before", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      const writeRow = await screen.findByTestId("activity-event-version-req-update1");

      await user.click(within(writeRow).getByRole("button", { name: "Revert this change" }));

      await waitFor(() => expect(fieldReverts).toHaveLength(1));
      // req-update1's own field-change event carries id 3 (create's two events are 1 and 2).
      expect(fieldReverts[0]).toEqual({ eventId: 3, expectedVersion: 3 });
      expect(await screen.findByText("Reverted status.")).toBeInTheDocument();
    });

    it("reverts the whole record to a prior version through revert_to_version, the same endpoint as before", async () => {
      const user = userEvent.setup();
      renderRecordPage();
      const writeRow = await screen.findByTestId("activity-event-version-req-update1");

      await user.click(within(writeRow).getByRole("button", { name: "Revert record to this version" }));

      await waitFor(() => expect(versionReverts).toHaveLength(1));
      // req-update1 produced version 2, which is below the loaded record's version 3.
      expect(versionReverts[0]).toEqual({ ref: "INIT-1", target: 2, expectedVersion: 3 });
      expect(await screen.findByText("Reverted record to version 2.")).toBeInTheDocument();
    });
  });

  // Every mutation on this page carries an error surface: the control
  // runs, nothing silently no-ops, and the reader is told what happened.
  describe("every mutation has an error surface", () => {
    function failWith(
      method: "post" | "patch" | "delete",
      path: string,
      code: string,
      message: string,
    ) {
      server.use(
        http[method](
          path,
          () => HttpResponse.json({ error: { code, message, details: {} } }, { status: 422 }),
          { once: true },
        ),
      );
    }

    async function expectAlert(title: string, code: string, message: string) {
      const alert = await screen.findByRole("alert");
      expect(within(alert).getByText(title)).toBeInTheDocument();
      expect(alert.textContent).toContain(code);
      expect(alert.textContent).toContain(message);
    }

    it("adding a comment", async () => {
      const user = userEvent.setup();
      failWith("post", "/api/v1/records/:ref/comments", "validation_failed", "The body is empty.");
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      await user.type(screen.getByPlaceholderText("Reply. Agents read this too."), "A new comment.");
      await user.click(screen.getByRole("button", { name: "Post" }));

      // The composer's own fixed title.
      await expectAlert("Could not post the reply.", "validation_failed", "The body is empty.");
    });

    /**
     * DEFECT found while repointing this file (see report): `ActivityCard.tsx` surfaced no error
     * at all for a failed comment edit or delete — `updateComment`/`deleteComment` carried no
     * `onError`, so the mutation ran, failed, and nothing on screen said so. Fixed in
     * `ActivityCard.tsx` by routing both through the same `error` state the reverts already use.
     * Run against the unfixed tree first: this assertion timed out waiting for `role="alert"`.
     */
    it("editing a comment", async () => {
      const user = userEvent.setup();
      failWith("patch", "/api/v1/comments/:commentId", "validation_failed", "That comment is deleted.");
      renderRecordPage();

      const c1Row = await screen.findByTestId("activity-event-comment-c1");
      await user.click(within(c1Row).getByRole("button", { name: "Edit" }));
      await user.clear(within(c1Row).getByLabelText("Edit comment"));
      await user.type(within(c1Row).getByLabelText("Edit comment"), "Revised.");
      await user.click(within(c1Row).getByRole("button", { name: "Save" }));

      await expectAlert("Could not save the comment.", "validation_failed", "That comment is deleted.");
    });

    /** Same defect as "editing a comment" above; see that test's note. */
    it("deleting a comment", async () => {
      const user = userEvent.setup();
      failWith("delete", "/api/v1/comments/:commentId", "validation_failed", "That comment is already deleted.");
      renderRecordPage();

      const c1Row = await screen.findByTestId("activity-event-comment-c1");
      await user.click(within(c1Row).getByRole("button", { name: "Delete" }));

      await expectAlert(
        "Could not delete the comment.",
        "validation_failed",
        "That comment is already deleted.",
      );
    });

    it("linking a record", async () => {
      const user = userEvent.setup();
      failWith("post", "/api/v1/records/:ref/links/:fieldKey", "not_found", "No such record: PERSON-1.");
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const ownerField = screen.getByTestId("relation-field-owner");
      await user.click(within(ownerField).getByRole("button", { name: "Link a record" }));
      await user.click(
        await within(ownerField).findByRole("option", { name: "PERSON-1 Dana Okonkwo" }),
      );

      await expectAlert("Could not link the record.", "not_found", "No such record: PERSON-1.");
    });

    it("unlinking a record", async () => {
      const user = userEvent.setup();
      failWith("delete", "/api/v1/records/:ref/links/:fieldKey", "validation_failed", "That link is required.");
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const tasksField = screen.getByTestId("relation-field-tasks");
      await user.click(
        within(tasksField).getByRole("button", { name: "Unlink TASK-1 Draft the migration plan" }),
      );

      await expectAlert("Could not unlink the record.", "validation_failed", "That link is required.");
    });
  });

  // DD-25: the envelope carries `principal_display_name`, and the Activity card renders it,
  // falling back to the raw id.
  describe("activity rows name their principal", () => {
    it("renders a comment author's display name, falling back to the id", async () => {
      commentsStore = [
        { ...initialComments[0], principal_display_name: "Dana Okonkwo" },
        { ...initialComments[1], principal_display_name: null },
      ];
      renderRecordPage();

      const named = await screen.findByTestId("activity-event-comment-c1");
      expect(within(named).getByText("Dana Okonkwo")).toBeInTheDocument();
      expect(within(named).queryByText(TEST_PRINCIPAL_ID)).not.toBeInTheDocument();

      const unnamed = screen.getByTestId("activity-event-comment-c2");
      expect(within(unnamed).getByText(TEST_PRINCIPAL_ID)).toBeInTheDocument();
    });
  });
});

/**
 * Record detail. `canWrite` is derived once in `RecordDetailView` (DD-42) and reaches
 * every write affordance on both cards plus the `ReadOnlyBanner`. A comment's write routes check
 * `write` on the parent record's object type, and both reverts delegate to `update_record`
 * (`revert_field_change` at `services/records.py:817`, `revert_to_version` at `:855`), so all
 * four are `write`, not something stricter.
 */
describe("RecordDetailView: level gating (DD-42)", () => {
  function renderAsReadOnly() {
    server.use(
      http.get("/api/v1/object-types/:key", () =>
        HttpResponse.json({ ...objectType, your_access: "read" }),
      ),
    );
    return renderRecordPage();
  }

  it("hides comment, link and revert affordances for a 'read' caller, and explains it once", async () => {
    renderAsReadOnly();
    await screen.findByRole("heading", { name: "INIT-1", level: 1 });
    // Non-vacuity: the same history fixture that proves both reverts render for a writer (above)
    // is loaded here too, so their absence below is a real gate, not an empty feed.
    await waitFor(() => expect(screen.getAllByTestId(/^activity-event-/)).toHaveLength(5));

    // The per-field editor: hidden, not disabled (DD-42).
    expect(screen.queryByRole("button", { name: "Edit Status" })).not.toBeInTheDocument();
    expect(screen.getByTestId("field-value-status")).toBeInTheDocument();

    // The composer, and a comment of the caller's own with its Edit / Delete.
    expect(screen.queryByPlaceholderText("Reply. Agents read this too.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Post" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    // Reading the feed is untouched.
    expect(screen.getByRole("region", { name: "Activity" })).toBeInTheDocument();

    // Link and unlink.
    expect(screen.queryByRole("button", { name: "Link a record" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Unlink/ })).not.toBeInTheDocument();

    // Both revert controls.
    expect(screen.queryByRole("button", { name: "Revert this change" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Revert record to this version" }),
    ).not.toBeInTheDocument();

    const banners = screen.getAllByTestId("read-only-banner");
    expect(banners).toHaveLength(1);
    expect(banners[0]).toHaveTextContent(
      "Read-only. You hold read on Initiative. Ask an administrator of Initiative, or a " +
        "system administrator, for write access.",
    );
  });

  // A fence: every one of these is also exercised, positively, by an earlier test in this file
  // under the default `your_access: "admin"` fixture, so nothing here can fail independently of
  // the read-only test above failing. Kept because it is the one place that states "and a writer
  // loses none of this" in one place.
  it("keeps every one of them for a 'write' caller, and carries no banner (fence)", async () => {
    server.use(
      http.get("/api/v1/object-types/:key", () =>
        HttpResponse.json({ ...objectType, your_access: "write" }),
      ),
    );
    renderRecordPage();
    await screen.findByRole("heading", { name: "INIT-1", level: 1 });

    expect(screen.getByPlaceholderText("Reply. Agents read this too.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Link a record" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Status" })).toBeInTheDocument();
    expect(screen.queryByTestId("read-only-banner")).not.toBeInTheDocument();
  });
});

/**
 * The relation field's picker. All four tests address `tasks`, the cardinality-many field whose
 * target (`task`) has a real, `contains`-capable title to search; `owner`'s target (`person`) is
 * fixture-only here.
 *
 * Every test opens the trigger by its accessible name and expects a `role="listbox"` inside it,
 * so without the picker each fails for that reason (no such button, no such listbox), not for a
 * missing fixture or an unhandled request.
 */
describe("relation link picker", () => {
  it("picking an option links it, and the pill appears", async () => {
    const user = userEvent.setup();
    renderRecordPage();
    await screen.findByRole("heading", { name: "INIT-1", level: 1 });

    const tasksField = screen.getByTestId("relation-field-tasks");
    await user.click(within(tasksField).getByRole("button", { name: "Add another" }));
    await user.click(
      await within(tasksField).findByRole("option", { name: "TASK-2 Write the rollback notes" }),
    );

    await waitFor(() => expect(linkRequests).toHaveLength(1));
    expect(linkRequests[0]).toEqual({ fieldKey: "tasks", body: { to_records: ["TASK-2"] } });
    expect(
      await within(tasksField).findByRole("link", { name: "TASK-2 Write the rollback notes" }),
    ).toBeInTheDocument();
  });

  it("typing searches the title, debounced, and an empty term sends no filter", async () => {
    const user = userEvent.setup();
    renderRecordPage();
    await screen.findByRole("heading", { name: "INIT-1", level: 1 });

    const tasksField = screen.getByTestId("relation-field-tasks");
    await user.click(within(tasksField).getByRole("button", { name: "Add another" }));

    // Opening sends one body with no filter.
    await waitFor(() => expect(queryRequests).toHaveLength(1));
    expect(queryRequests[0].body.filter).toBeFalsy();

    const searchInput = within(tasksField).getByLabelText("Search Tasks by title");
    await user.type(searchInput, "rollb");

    // Exactly one further body once the debounce settles, never one per keystroke: five
    // keystrokes would already have pushed the count past 2 by the time this resolves.
    await waitFor(() => expect(queryRequests).toHaveLength(2));
    expect(queryRequests[1].body.filter).toEqual({
      field: "title",
      op: "contains",
      value: "rollb",
    });

    // Nothing further arrives on its own once the debounce has settled.
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(queryRequests).toHaveLength(2);

    await user.clear(searchInput);
    await new Promise((resolve) => setTimeout(resolve, 400));
    const emptyValueFilterSent = queryRequests.some((sent) => {
      const filter = sent.body.filter as { value?: unknown } | null | undefined;
      return Boolean(filter) && filter?.value === "";
    });
    expect(emptyValueFilterSent).toBe(false);
  });

  it("already-linked records are never offered", async () => {
    const user = userEvent.setup();
    renderRecordPage();
    await screen.findByRole("heading", { name: "INIT-1", level: 1 });

    const tasksField = screen.getByTestId("relation-field-tasks");
    await user.click(within(tasksField).getByRole("button", { name: "Add another" }));

    const listbox = await within(tasksField).findByRole("listbox", { name: "Tasks" });
    expect(
      within(listbox).queryByRole("option", { name: "TASK-1 Draft the migration plan" }),
    ).not.toBeInTheDocument();
    expect(
      within(listbox).getByRole("option", { name: "TASK-2 Write the rollback notes" }),
    ).toBeInTheDocument();

    // 20 (PICKER_PAGE) + 1 excluded id (TASK-1, already linked).
    await waitFor(() => expect(queryRequests).toHaveLength(1));
    expect(queryRequests[0].body.limit).toBe(21);
  });

  describe("non-text titles, unreadable targets and errors are all said, not hidden", () => {
    it("a target whose title field lacks `contains` renders no search input but still lists options", async () => {
      const user = userEvent.setup();
      server.use(
        http.get("/api/v1/object-types/:key", ({ params }) => {
          const key = params.key as string;
          objectTypeKeyRequests.push(key);
          if (key === "task") return HttpResponse.json(taskObjectTypeWithoutContains());
          if (key === "person") return HttpResponse.json(personObjectType);
          return HttpResponse.json(objectType);
        }),
      );
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const tasksField = screen.getByTestId("relation-field-tasks");
      await user.click(within(tasksField).getByRole("button", { name: "Add another" }));

      expect(
        await within(tasksField).findByRole("option", { name: "TASK-2 Write the rollback notes" }),
      ).toBeInTheDocument();
      expect(within(tasksField).queryByLabelText("Search Tasks by title")).not.toBeInTheDocument();
    });

    it("a target absent from the orientation list is named as unreadable, with no further request", async () => {
      const user = userEvent.setup();
      server.use(
        http.get("/api/v1/object-types", () =>
          HttpResponse.json(objectTypesList.filter((entry) => entry.key !== "task")),
        ),
      );
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const tasksField = screen.getByTestId("relation-field-tasks");
      await user.click(within(tasksField).getByRole("button", { name: "Add another" }));

      expect(
        await within(tasksField).findByText("You can't read the records this field links to."),
      ).toBeInTheDocument();
      expect(objectTypeKeyRequests.filter((key) => key === "task")).toHaveLength(0);
      expect(queryRequests.filter((sent) => sent.objectTypeKey === "task")).toHaveLength(0);
    });

    it("a non-forbidden error on the query renders \"Could not load records.\"", async () => {
      const user = userEvent.setup();
      server.use(
        http.post(
          "/api/v1/object-types/:key/query",
          () => HttpResponse.json(INTERNAL_ERROR_ENVELOPE, { status: 500 }),
          { once: true },
        ),
      );
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const tasksField = screen.getByTestId("relation-field-tasks");
      await user.click(within(tasksField).getByRole("button", { name: "Add another" }));

      expect(await within(tasksField).findByText("Could not load records.")).toBeInTheDocument();
    });

    it("a 403 on the target's type read shows the forbidden message, and requests stay bounded", async () => {
      const user = userEvent.setup();
      server.use(
        http.get("/api/v1/object-types/:key", ({ params }) => {
          const key = params.key as string;
          objectTypeKeyRequests.push(key);
          if (key === "task") return HttpResponse.json(FORBIDDEN_ENVELOPE, { status: 403 });
          if (key === "person") return HttpResponse.json(personObjectType);
          return HttpResponse.json(objectType);
        }),
      );
      renderRecordPage();
      await screen.findByRole("heading", { name: "INIT-1", level: 1 });

      const tasksField = screen.getByTestId("relation-field-tasks");
      await user.click(within(tasksField).getByRole("button", { name: "Add another" }));

      // `Alert` renders a `forbidden` envelope's own message, not the picker's fixed title.
      expect(
        await within(tasksField).findByText(FORBIDDEN_ENVELOPE.error.message),
      ).toBeInTheDocument();

      // Only a settle can see a refetch loop; counting the moment the message appears cannot.
      await new Promise((resolve) => setTimeout(resolve, 500));
      expect(objectTypeKeyRequests.filter((key) => key === "task").length).toBeLessThanOrEqual(2);
    });
  });
});
