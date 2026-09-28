/**
 * The agent bar's rule. The case that decides it is the third one: an agent version
 * followed by a human version touching one of the same fields.
 */
import { describe, expect, it } from "vitest";
import type { AuditEventDoc } from "../api/records";
import { groupAuditEvents } from "./auditGroups";
import { agentBarFieldKeys } from "./agentBar";

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

/** A human create of three fields, which every case below builds on: without it
 * `groupAuditEvents` has no anchor and derives no versions at all. */
function creation(): AuditEventDoc[] {
  return [
    event({ request_id: "v1", action: "create", field_key: null }),
    event({ request_id: "v1", action: "create", field_key: "company" }),
    event({ request_id: "v1", action: "create", field_key: "stage" }),
    event({ request_id: "v1", action: "create", field_key: "notes" }),
  ];
}

function agentWrite(requestId: string, fieldKeys: string[]): AuditEventDoc[] {
  return fieldKeys.map((field_key) =>
    event({ request_id: requestId, field_key, agent_label_id: "a-1", agent_label: "sales-agent" }),
  );
}

function humanWrite(requestId: string, fieldKeys: string[]): AuditEventDoc[] {
  return fieldKeys.map((field_key) => event({ request_id: requestId, field_key }));
}

function keysFor(events: AuditEventDoc[]): string[] {
  return [...agentBarFieldKeys(groupAuditEvents(events))].sort();
}

describe("agentBarFieldKeys", () => {
  it("is empty when no version was agent-authored", () => {
    expect(keysFor([...creation(), ...humanWrite("v2", ["stage"])])).toEqual([]);
  });

  it("marks the fields the agent version changed", () => {
    expect(keysFor([...creation(), ...agentWrite("v2", ["stage", "notes"])])).toEqual([
      "notes",
      "stage",
    ]);
  });

  it("drops a field a later human version rewrote", () => {
    // The decisive case. `notes` on screen is the person's sentence; a bar beside it would
    // attribute it to the agent.
    const events = [
      ...creation(),
      ...agentWrite("v2", ["stage", "notes"]),
      ...humanWrite("v3", ["notes"]),
    ];

    expect(keysFor(events)).toEqual(["stage"]);
  });

  it("uses the most recent agent version when there are two", () => {
    const events = [
      ...creation(),
      ...agentWrite("v2", ["company"]),
      ...agentWrite("v3", ["stage"]),
    ];

    expect(keysFor(events)).toEqual(["stage"]);
  });

  it("ignores a later write that produced no version", () => {
    // A comment does not overwrite a value, so it cannot clear the bar.
    const events = [
      ...creation(),
      ...agentWrite("v2", ["stage"]),
      event({ request_id: "c1", entity_type: "comment", action: "create", field_key: null }),
    ];

    expect(keysFor(events)).toEqual(["stage"]);
  });

  it("marks every value when an agent created the record", () => {
    const events = [
      event({ request_id: "v1", action: "create", field_key: null, agent_label_id: "a-1" }),
      event({ request_id: "v1", action: "create", field_key: "company", agent_label_id: "a-1" }),
      event({ request_id: "v1", action: "create", field_key: "stage", agent_label_id: "a-1" }),
    ];

    expect(keysFor(events)).toEqual(["company", "stage"]);
  });

  it("is empty for a record with no history at all", () => {
    expect(keysFor([])).toEqual([]);
  });
});
