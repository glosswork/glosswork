/**
 * The walk drains both cursors, stops at the named cap, and says so when
 * it stopped early.
 *
 * The API modules are mocked rather than the network, because what is under test is the *walk*
 * — how many times it calls, with which cursor, and what it concludes — not the request shape,
 * which `api/comments.ts` and `api/records.ts` already own.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CommentDoc } from "../api/comments";
import type { AuditEventDoc, HistoryPage } from "../api/records";
import type { CommentPage } from "../api/comments";

vi.mock("../api/comments", () => ({ listComments: vi.fn() }));
vi.mock("../api/records", () => ({ getRecordHistory: vi.fn() }));

const { listComments } = await import("../api/comments");
const { getRecordHistory } = await import("../api/records");
const { fetchRecordActivityPages, MAX_ACTIVITY_PAGES } = await import("./recordActivityPages");

const mockedComments = vi.mocked(listComments);
const mockedHistory = vi.mocked(getRecordHistory);

function comment(id: string): CommentDoc {
  return {
    id,
    record_id: "rec-1",
    body: "Body.",
    author_id: "p-1",
    agent_label_id: null,
    agent_label: null,
    created_at: "2026-09-12T02:45:03Z",
    updated_at: "2026-09-12T02:45:03Z",
    edited: false,
    deleted_at: null,
    principal_display_name: "Dana Reyes",
  };
}

function auditEvent(id: number): AuditEventDoc {
  return {
    id,
    ts: "2026-09-12T02:45:03Z",
    request_id: `req-${id}`,
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
    action: "update",
    field_key: "company",
    old_value: null,
    new_value: null,
    note: null,
    principal_display_name: "Dana Reyes",
    record_key: "PROS-001",
  };
}

/** A responder that hands out `pageCount` pages and then stops, recording the cursor it was
 * given each time so the test can prove the walk carried it forward rather than refetching
 * page one until the cap. */
function pager<T>(pageCount: number, make: (page: number) => T[], seen: (string | undefined)[]) {
  return (_ref: string, options: { cursor?: string } = {}) => {
    seen.push(options.cursor);
    const page = seen.length - 1;
    const last = page >= pageCount - 1;
    return Promise.resolve({ rows: make(page), next: last ? null : `cursor-${page + 1}` });
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("fetchRecordActivityPages", () => {
  it("drains both cursors and returns every row in order", async () => {
    const commentCursors: (string | undefined)[] = [];
    const eventCursors: (string | undefined)[] = [];
    const comments = pager(3, (page) => [comment(`c-${page}`)], commentCursors);
    const events = pager(2, (page) => [auditEvent(page)], eventCursors);

    mockedComments.mockImplementation(async (ref, options) => {
      const { rows, next } = await comments(ref, options);
      return { comments: rows, next_cursor: next } satisfies CommentPage;
    });
    mockedHistory.mockImplementation(async (ref, options) => {
      const { rows, next } = await events(ref, options);
      return { events: rows, next_cursor: next } satisfies HistoryPage;
    });

    const result = await fetchRecordActivityPages("PROS-001");

    expect(result.comments.map((c) => c.id)).toEqual(["c-0", "c-1", "c-2"]);
    expect(result.events.map((e) => e.id)).toEqual([0, 1]);
    expect(result.truncated).toBe(false);
    // The cursor was carried forward, not dropped: page one asks for none, each later page asks
    // for the one the previous page handed back.
    expect(commentCursors).toEqual([undefined, "cursor-1", "cursor-2"]);
    expect(eventCursors).toEqual([undefined, "cursor-1"]);
  });

  it("stops at the cap and reports the truncation", async () => {
    // A cursor that never ends. Without the cap this call does not return.
    mockedComments.mockResolvedValue({ comments: [comment("c")], next_cursor: "more" });
    mockedHistory.mockResolvedValue({ events: [], next_cursor: null });

    const result = await fetchRecordActivityPages("PROS-001");

    expect(mockedComments).toHaveBeenCalledTimes(MAX_ACTIVITY_PAGES);
    expect(result.comments).toHaveLength(MAX_ACTIVITY_PAGES);
    expect(result.truncated).toBe(true);
  });

  it("reports truncation when only the history walk hits the cap", async () => {
    // The flag is about the page's honesty, so either walk stopping early has to raise it.
    mockedComments.mockResolvedValue({ comments: [], next_cursor: null });
    mockedHistory.mockResolvedValue({ events: [auditEvent(1)], next_cursor: "more" });

    const result = await fetchRecordActivityPages("PROS-001");

    expect(mockedHistory).toHaveBeenCalledTimes(MAX_ACTIVITY_PAGES);
    expect(result.truncated).toBe(true);
  });

  it("makes exactly one call per stream when there is one page each", async () => {
    mockedComments.mockResolvedValue({ comments: [], next_cursor: null });
    mockedHistory.mockResolvedValue({ events: [], next_cursor: null });

    const result = await fetchRecordActivityPages("PROS-001");

    expect(mockedComments).toHaveBeenCalledTimes(1);
    expect(mockedHistory).toHaveBeenCalledTimes(1);
    expect(result.truncated).toBe(false);
  });
});
