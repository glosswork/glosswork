import { describe, expect, it } from "vitest";
import type { AuditEventDoc } from "../api/records";
import { commentBodyVersions } from "./commentBodyVersions";

function makeEvent(overrides: Partial<AuditEventDoc>): AuditEventDoc {
  return {
    id: 1,
    ts: "2026-08-24T00:00:00Z",
    request_id: "req-1",
    principal_id: "principal-1",
    principal_type: "service_account",
    agent_label_id: null,
    agent_label: null,
    auth_method: "pat",
    surface: "rest",
    entity_type: "comment",
    entity_id: "comment-1",
    record_id: "record-1",
    object_type_id: "object-type-1",
    action: "create",
    field_key: null,
    old_value: null,
    new_value: "Original body.",
    note: null,
    principal_display_name: null,
    record_key: "INIT-1",
    ...overrides,
  };
}

describe("commentBodyVersions", () => {
  it("returns the chronological body history of one comment", () => {
    const events: AuditEventDoc[] = [
      makeEvent({ id: 1, action: "create", new_value: "Initial text." }),
      makeEvent({
        id: 2,
        action: "update",
        old_value: "Initial text.",
        new_value: "Revised text.",
      }),
    ];

    expect(commentBodyVersions(events, "comment-1")).toEqual([
      { ts: "2026-08-24T00:00:00Z", body: "Initial text." },
      { ts: "2026-08-24T00:00:00Z", body: "Revised text." },
    ]);
  });

  it("ignores events for other comments and other entity types", () => {
    const events: AuditEventDoc[] = [
      makeEvent({ id: 1, entity_id: "comment-2", new_value: "Someone else's comment." }),
      makeEvent({ id: 2, entity_type: "record", new_value: "A field update." }),
    ];

    expect(commentBodyVersions(events, "comment-1")).toEqual([]);
  });

  it("ignores delete events, which carry no new body", () => {
    const events: AuditEventDoc[] = [
      makeEvent({ id: 1, action: "create", new_value: "Body." }),
      makeEvent({ id: 2, action: "delete", old_value: "Body.", new_value: null }),
    ];

    expect(commentBodyVersions(events, "comment-1")).toEqual([
      { ts: "2026-08-24T00:00:00Z", body: "Body." },
    ]);
  });
});
