import { describe, expect, it } from "vitest";
import type { AuditEventDoc } from "../api/records";
import { fieldChangeCount, groupAuditEvents, summarizeWrite } from "./auditGroups";

let nextId = 0;
function event(overrides: Partial<AuditEventDoc>): AuditEventDoc {
  nextId += 1;
  return {
    id: nextId,
    ts: "2026-08-20T10:00:00",
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
    field_key: "status",
    old_value: null,
    new_value: null,
    note: null,
    principal_display_name: null,
    record_key: "INIT-1",
    ...overrides,
  };
}

describe("groupAuditEvents", () => {
  it("collapses one write's field rows into one entry", () => {
    const groups = groupAuditEvents([
      event({ request_id: "w1", action: "create", field_key: "title" }),
      event({ request_id: "w1", action: "create", field_key: "status" }),
      event({ request_id: "w1", action: "create", field_key: null }),
      event({ request_id: "w2", action: "update", field_key: "status" }),
    ]);

    expect(groups.map((g) => g.requestId)).toEqual(["w1", "w2"]);
    expect(groups.map((g) => g.events.length)).toEqual([3, 1]);
    expect(groups.map(fieldChangeCount)).toEqual([2, 1]);
  });

  it("derives the record version each write produced", () => {
    // The exact sequence probed against the running service layer: create, update, comment,
    // two-field update, comment delete -> actual versions 1, 2, 2, 3, 3.
    const groups = groupAuditEvents([
      event({ request_id: "w1", action: "create", field_key: "title" }),
      event({ request_id: "w1", action: "create", field_key: null }),
      event({ request_id: "w2", action: "update", field_key: "title" }),
      event({ request_id: "w3", entity_type: "comment", action: "create", field_key: null }),
      event({ request_id: "w4", action: "update", field_key: "title" }),
      event({ request_id: "w4", action: "update", field_key: "note" }),
      event({ request_id: "w5", entity_type: "comment", action: "delete", field_key: null }),
    ]);

    expect(groups.map((g) => g.version)).toEqual([1, 2, null, 3, null]);
  });

  it("gives a link write no version, because linking does not bump one", () => {
    const groups = groupAuditEvents([
      event({ request_id: "w1", action: "create", field_key: null }),
      event({ request_id: "w2", entity_type: "link", action: "create", field_key: null }),
    ]);
    expect(groups.map((g) => g.version)).toEqual([1, null]);
  });

  it("does not merge two writes that happen to share a timestamp", () => {
    const groups = groupAuditEvents([
      event({ request_id: "w1", ts: "2026-08-20T10:00:00" }),
      event({ request_id: "w2", ts: "2026-08-20T10:00:00" }),
    ]);
    expect(groups).toHaveLength(2);
  });

  it("returns nothing for no events", () => {
    expect(groupAuditEvents([])).toEqual([]);
  });
  it("refuses to invent a version when the creation event is not loaded", () => {
    const groups = groupAuditEvents([
      event({ request_id: "w1", action: "update", field_key: "a" }),
      event({ request_id: "w2", action: "update", field_key: "b" }),
    ]);
    expect(groups.map((g) => g.version)).toEqual([null, null]);
  });
});

describe("summarizeWrite", () => {
  it("names the field count on a multi-field write", () => {
    const [group] = groupAuditEvents([
      event({ request_id: "w1", action: "update", field_key: "a" }),
      event({ request_id: "w1", action: "update", field_key: "b" }),
    ]);
    expect(summarizeWrite(group)).toBe("Updated 2 fields");
  });

  it("uses the singular for one field", () => {
    const [group] = groupAuditEvents([event({ request_id: "w1", field_key: "a" })]);
    expect(summarizeWrite(group)).toBe("Updated 1 field");
  });

  it("names a create as a create", () => {
    const [group] = groupAuditEvents([
      event({ request_id: "w1", action: "create", field_key: "a" }),
      event({ request_id: "w1", action: "create", field_key: null }),
    ]);
    expect(summarizeWrite(group)).toBe("Created 1 field");
  });

  it("falls back to the entity for a write that touched no field", () => {
    const [group] = groupAuditEvents([
      event({ request_id: "w1", entity_type: "comment", action: "create", field_key: null }),
    ]);
    expect(summarizeWrite(group)).toBe("Created comment");
  });
  it("reads a link write as 'Linked record', not as an entity called 'link'", () => {
    const [linked] = groupAuditEvents([
      event({ request_id: "w1", entity_type: "link", action: "link", field_key: null }),
    ]);
    expect(summarizeWrite(linked)).toBe("Linked record");

    const [unlinked] = groupAuditEvents([
      event({ request_id: "w2", entity_type: "link", action: "unlink", field_key: null }),
    ]);
    expect(summarizeWrite(unlinked)).toBe("Unlinked record");
  });

  it("capitalizes an action it has no past tense for rather than mangling it", () => {
    const [group] = groupAuditEvents([
      event({ request_id: "w1", entity_type: "attachment", action: "purge", field_key: null }),
    ]);
    expect(summarizeWrite(group)).toBe("Purge attachment");
  });
});
