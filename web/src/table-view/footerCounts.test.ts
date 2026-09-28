import { describe, expect, it } from "vitest";

import type { RecordDoc } from "../api/records";
import {
  agentTouchedCount,
  agentTouchedLabel,
  currentViewLabel,
  hiddenByFilterCount,
  hiddenByFilterLabel,
  recordCountLabel,
} from "./footerCounts";

function row(agentLabelId: string | null): RecordDoc {
  return {
    id: "id",
    key: "K-1",
    version: 1,
    created_at: "2026-09-11T00:00:00Z",
    created_by: "p1",
    updated_at: "2026-09-11T00:00:00Z",
    updated_by: "p1",
    updated_by_agent_label_id: agentLabelId,
    deleted_at: null,
    comment_count: 0,
    last_comment_at: null,
    data: {},
  };
}

describe("hiddenByFilterCount", () => {
  it("is the difference between the type's live count and the filtered one", () => {
    expect(hiddenByFilterCount(13, 10)).toBe(3);
  });

  it("clamps at zero when the two fetches disagree", () => {
    // The unfiltered count rides the type document and the filtered one rides the query
    // response, so a record created between the two makes this negative. "-1 hidden" is worse
    // than silence.
    expect(hiddenByFilterCount(10, 13)).toBe(0);
    expect(hiddenByFilterCount(10, 10)).toBe(0);
  });
});

describe("hiddenByFilterLabel", () => {
  it("says how many rows the filter is holding back", () => {
    expect(hiddenByFilterLabel(3)).toBe("3 hidden by your filter");
  });

  it("says nothing when nothing is hidden", () => {
    expect(hiddenByFilterLabel(0)).toBeNull();
    expect(hiddenByFilterLabel(-2)).toBeNull();
  });
});

describe("agentTouchedCount", () => {
  it("counts the rows on this page whose last write carried an agent label", () => {
    expect(agentTouchedCount([row("a"), row(null), row("b"), row(null)])).toBe(2);
    expect(agentTouchedCount([])).toBe(0);
  });
});

describe("agentTouchedLabel", () => {
  it("words the count as a claim about this page", () => {
    expect(agentTouchedLabel(4)).toBe("4 of these rows were last touched by an agent");
  });

  it("agrees with itself in the singular", () => {
    expect(agentTouchedLabel(1)).toBe("1 of these rows was last touched by an agent");
  });

  it("says nothing when no row on this page was", () => {
    expect(agentTouchedLabel(0)).toBeNull();
  });
});

describe("recordCountLabel", () => {
  it("counts the type's records for the title line", () => {
    expect(recordCountLabel(13)).toBe("13 records");
    expect(recordCountLabel(1)).toBe("1 record");
    expect(recordCountLabel(0)).toBe("0 records");
  });
});

describe("currentViewLabel", () => {
  it("names the loaded view", () => {
    expect(currentViewLabel("Active widgets")).toBe("Active widgets");
  });

  it("names the absence of one rather than leaving the line half-written", () => {
    expect(currentViewLabel(null)).toBe("Unsaved view");
    expect(currentViewLabel(undefined)).toBe("Unsaved view");
    expect(currentViewLabel("   ")).toBe("Unsaved view");
  });
});
