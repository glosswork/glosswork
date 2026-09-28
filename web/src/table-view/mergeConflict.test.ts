import { describe, expect, it } from "vitest";
import { buildResubmitPayload, defaultResolutions, type ConflictDetails } from "./mergeConflict";

const conflict: ConflictDetails = {
  record_key: "INIT-1",
  current_version: 9,
  supplied_version: 7,
  conflicting_fields: {
    status: { your_value: "on_track", current_value: "at_risk" },
  },
  changed_since_your_version: ["status", "target_date"],
};

describe("defaultResolutions", () => {
  it("defaults every conflicting field to the user's own pending value", () => {
    expect(defaultResolutions(conflict)).toEqual({ status: { choice: "mine" } });
  });
});

describe("buildResubmitPayload", () => {
  it("uses the user's value for 'mine' and the server's expected_version", () => {
    const payload = buildResubmitPayload(conflict, { status: "on_track" }, {
      status: { choice: "mine" },
    });
    expect(payload).toEqual({ values: { status: "on_track" }, expected_version: 9 });
  });

  it("uses the current server value for 'theirs'", () => {
    const payload = buildResubmitPayload(conflict, { status: "on_track" }, {
      status: { choice: "theirs" },
    });
    expect(payload.values.status).toBe("at_risk");
  });

  it("uses the edited value for 'edited'", () => {
    const payload = buildResubmitPayload(conflict, { status: "on_track" }, {
      status: { choice: "edited", editedValue: "blocked" },
    });
    expect(payload.values.status).toBe("blocked");
  });

  it("carries non-conflicting pending fields through unchanged", () => {
    const payload = buildResubmitPayload(
      conflict,
      { status: "on_track", owner_note: "keep this" },
      { status: { choice: "theirs" } },
    );
    expect(payload.values.owner_note).toBe("keep this");
  });
});
