import { describe, expect, it } from "vitest";
import { DESCRIPTION_EXAMPLES, DESCRIPTION_GUIDANCE, validateDescription } from "./descriptionGuidance";

describe("validateDescription", () => {
  it("rejects an empty description with the agent-facing framing", () => {
    const error = validateDescription("", "Field");
    expect(error).toContain("requires a non-empty description");
    expect(error).toContain("how agents interpret the schema");
  });

  it("rejects a whitespace-only description", () => {
    expect(validateDescription("   \n\t", "Object type")).not.toBeNull();
  });

  it("accepts a real description", () => {
    expect(validateDescription("Effort estimate in story points.", "Field")).toBeNull();
  });
});

describe("guidance copy", () => {
  it("mentions agents, not just humans", () => {
    expect(DESCRIPTION_GUIDANCE.toLowerCase()).toContain("agent");
  });

  it("provides an example for every description context", () => {
    expect(DESCRIPTION_EXAMPLES.object_type).toContain("Example:");
    expect(DESCRIPTION_EXAMPLES.field).toContain("Example:");
    expect(DESCRIPTION_EXAMPLES.enum_option).toContain("Example:");
  });
});
