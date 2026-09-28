import { describe, expect, it } from "vitest";
import type { FieldDoc } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import { groupRowLabel } from "./groupLabel";

const statusField: FieldDoc = {
  key: "status",
  name: "Status",
  type: "single_select",
  description: "d",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 0,
  operators: ["eq"],
  display_eligible: true,
  options: [{ value: "at_risk", label: "At Risk", description: "d" }],
};

const ownerField: FieldDoc = {
  key: "owner",
  name: "Owner",
  type: "relation",
  description: "d",
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
};

function record(expand: RecordDoc["expand"]): RecordDoc {
  return {
    id: "id",
    key: "INIT-1",
    version: 1,
    created_at: "",
    created_by: "",
    updated_at: "",
    updated_by: "",
    updated_by_agent_label_id: null,
    deleted_at: null,
    comment_count: 0,
    last_comment_at: null,
    data: {},
    expand,
  };
}

describe("groupRowLabel", () => {
  it("shows the option label for a single_select group, not the raw value", () => {
    expect(groupRowLabel(statusField, "at_risk", undefined)).toBe("At Risk");
  });

  it("shows the linked record's key and display value for a relation group", () => {
    const sample = record({ owner: [{ key: "PERSON-1", id: "p1", display: "Ada" }] });
    expect(groupRowLabel(ownerField, "PERSON-1", sample)).toBe("PERSON-1 — Ada");
  });

  it("falls back to the raw group value for a relation group with no sample record", () => {
    expect(groupRowLabel(ownerField, "(unlinked)", undefined)).toBe("(unlinked)");
  });
});
