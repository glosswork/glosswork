import { describe, expect, it } from "vitest";
import type { RecordDoc } from "../api/records";
import { lastTouchedBy } from "./lastTouchedBy";

function record(overrides: Partial<RecordDoc> = {}): RecordDoc {
  return {
    id: "rec-1",
    key: "PROS-001",
    version: 3,
    created_at: "2026-09-01T10:00:00Z",
    created_by: "p-1",
    updated_at: "2026-09-12T02:45:00Z",
    updated_by: "p-1",
    updated_by_agent_label_id: null,
    deleted_at: null,
    comment_count: 0,
    last_comment_at: null,
    data: {},
    ...overrides,
  };
}

describe("lastTouchedBy", () => {
  it("resolves a bare person write through the principals sidecar", () => {
    const inputs = lastTouchedBy(
      record({
        updated_by: "p-1",
        principals: { "p-1": { display_name: "Dana Reyes", email: null, is_active: true, type: "user" } },
      }),
    );

    expect(inputs.principal).toEqual({ display_name: "Dana Reyes", type: "user" });
    expect(inputs.agentLabel).toBeNull();
    expect(inputs.fallbackId).toBe("p-1");
  });

  it("carries both the principal and the agent label for an agent acting on a credential", () => {
    const inputs = lastTouchedBy(
      record({
        updated_by: "p-1",
        updated_by_agent_label_id: "a-1",
        principals: { "p-1": { display_name: "Sam Okafor", email: null, is_active: true, type: "user" } },
        agent_labels: { "a-1": { label: "sales-agent", display_name: null } },
      }),
    );

    expect(inputs.principal).toEqual({ display_name: "Sam Okafor", type: "user" });
    expect(inputs.agentLabel).toEqual({ label: "sales-agent", display_name: null });
  });

  it("falls back to the raw id when the principal sidecar has no entry", () => {
    const inputs = lastTouchedBy(record({ updated_by: "gone-1", principals: {} }));

    expect(inputs.principal).toBeUndefined();
    expect(inputs.fallbackId).toBe("gone-1");
  });

  it("falls back to the raw id as label text when the agent-label sidecar has no entry", () => {
    const inputs = lastTouchedBy(
      record({ updated_by_agent_label_id: "missing-label-id", agent_labels: {} }),
    );

    expect(inputs.agentLabel).toEqual({ label: "missing-label-id", display_name: null });
  });

  it("never returns a Pair-shaped result: it is always one principal and one nullable label", () => {
    const inputs = lastTouchedBy(record());
    expect(Object.keys(inputs).sort()).toEqual(["agentLabel", "fallbackId", "principal"]);
  });
});
