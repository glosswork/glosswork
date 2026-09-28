/**
 * The fixture is recorded verbatim: the six audit rows a real
 * create + agent update + comment + comment edit + comment delete produced against a running
 * server, plus one live comment and one deleted one.
 *
 * The arithmetic under test is the sentence the Activity card must make true — "events equal
 * comments plus versions" — and the reason it needs a test is that the obvious implementation
 * (`comments.concat(groups)`) satisfies neither half: it renders a live comment twice and a
 * deleted one once.
 */
import { describe, expect, it } from "vitest";
import type { CommentDoc } from "../api/comments";
import type { AuditEventDoc } from "../api/records";
import { buildActivityEvents, countActivityEvents } from "./activityEvents";

let nextId = 0;
function event(overrides: Partial<AuditEventDoc>): AuditEventDoc {
  nextId += 1;
  return {
    id: nextId,
    ts: "2026-09-12T02:45:03Z",
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
    action: "update",
    field_key: "company",
    old_value: null,
    new_value: null,
    note: null,
    principal_display_name: "Dana Reyes",
    record_key: "PROS-001",
    ...overrides,
  };
}

function comment(overrides: Partial<CommentDoc>): CommentDoc {
  return {
    id: "c-1",
    record_id: "rec-1",
    body: "Looks good.",
    author_id: "p-1",
    agent_label_id: null,
    agent_label: null,
    created_at: "2026-09-12T02:45:03Z",
    updated_at: "2026-09-12T02:45:03Z",
    edited: false,
    deleted_at: null,
    principal_display_name: "Dana Reyes",
    ...overrides,
  };
}

/**
 * The recorded history, as it came back from `GET /api/v1/records/PROS-001/history`: two version-
 * producing write groups (`w-create`, `w-agent`) and three comment groups that produced none.
 */
function p4History(): AuditEventDoc[] {
  return [
    event({ request_id: "w-create", action: "create", field_key: null, ts: "2026-09-12T02:40:00Z" }),
    event({ request_id: "w-create", action: "create", field_key: "company", ts: "2026-09-12T02:40:00Z" }),
    event({
      request_id: "w-agent",
      action: "update",
      field_key: "company",
      agent_label: "sales-agent",
      agent_label_id: "a-1",
      ts: "2026-09-12T02:45:00Z",
    }),
    event({ request_id: "w-c1", entity_type: "comment", action: "create", field_key: null, ts: "2026-09-12T02:46:00Z" }),
    event({ request_id: "w-c2", entity_type: "comment", action: "update", field_key: null, ts: "2026-09-12T02:47:00Z" }),
    event({ request_id: "w-c3", entity_type: "comment", action: "delete", field_key: null, ts: "2026-09-12T02:48:00Z" }),
  ];
}

describe("buildActivityEvents", () => {
  it("counts exactly comments plus versions", () => {
    // One live comment. The deleted one is absent from `list_comments` by construction — that is
    // what makes the naive concatenation wrong rather than merely redundant.
    const comments = [comment({ id: "c-live", created_at: "2026-09-12T02:46:00Z" })];

    const events = buildActivityEvents(comments, p4History());

    expect(events).toHaveLength(3);
    expect(countActivityEvents(comments, p4History())).toBe(3);
  });

  it("renders a live comment once, not twice", () => {
    const comments = [comment({ id: "c-live", created_at: "2026-09-12T02:46:00Z" })];

    const events = buildActivityEvents(comments, p4History());

    expect(events.filter((e) => e.kind === "comment")).toHaveLength(1);
  });

  it("shows no event at all for a comment that was deleted", () => {
    // Three `comment` audit groups survive the delete; none of them is an event.
    const events = buildActivityEvents([], p4History());

    expect(events.map((e) => e.kind)).toEqual(["version", "version"]);
  });

  it("drops a write group that produced no version", () => {
    const withLink = [
      ...p4History(),
      event({ request_id: "w-link", entity_type: "link", action: "link", field_key: null }),
    ];

    expect(buildActivityEvents([], withLink)).toHaveLength(2);
  });

  it("orders newest first across both kinds", () => {
    const comments = [
      comment({ id: "c-early", created_at: "2026-09-12T02:41:00Z" }),
      comment({ id: "c-late", created_at: "2026-09-12T02:46:00Z" }),
    ];

    const events = buildActivityEvents(comments, p4History());

    expect(events.map((e) => e.id)).toEqual([
      "comment-c-late",
      "version-w-agent",
      "comment-c-early",
      "version-w-create",
    ]);
  });

  it("puts a comment above a version stamped in the same second", () => {
    // Both at 02:45:00, which is the ordinary case and not an edge: the backend stores seconds,
    // so a write and the comment about it routinely share a timestamp. A comment in the same
    // second as a write is a response to it, so newest-first puts it on top.
    const comments = [comment({ id: "c-tie", created_at: "2026-09-12T02:45:00Z" })];

    const events = buildActivityEvents(comments, p4History());

    expect(events.slice(0, 2).map((e) => e.id)).toEqual(["comment-c-tie", "version-w-agent"]);
  });

  it("puts the newer version first when two versions share a timestamp", () => {
    // The defect the first visual baseline caught: a descending comparator returning 0 on a tie
    // left both versions in the order they were pushed, which is ascending, so a feed labelled
    // "newest first" opened on the record's creation.
    const sameSecond = [
      event({ request_id: "v1", action: "create", field_key: null, ts: "2026-09-12T02:40:00Z" }),
      event({ request_id: "v1", action: "create", field_key: "company", ts: "2026-09-12T02:40:00Z" }),
      event({ request_id: "v2", action: "update", field_key: "company", ts: "2026-09-12T02:40:00Z" }),
    ];

    const events = buildActivityEvents([], sameSecond);

    expect(events.map((e) => (e.kind === "version" ? e.version : null))).toEqual([2, 1]);
  });

  it("carries the version number on a version event", () => {
    const events = buildActivityEvents([], p4History());

    expect(events.map((e) => (e.kind === "version" ? e.version : null))).toEqual([2, 1]);
  });
});
