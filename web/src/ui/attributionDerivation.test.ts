/**
 * docs/DESIGN.md 6.1's derivation rules, tested where they live rather than through a
 * component: the edge cases are about strings, not about rendering.
 */
import { describe, expect, it } from "vitest";

import {
  agentCode,
  agentName,
  initials,
  principalKind,
} from "./attributionDerivation";

describe("initials", () => {
  it("takes the first letters of the first two words, uppercased", () => {
    expect(initials("Dana Reyes")).toBe("DR");
    expect(initials("sam okafor")).toBe("SO");
  });

  it("takes only the first two words of a longer name", () => {
    expect(initials("Ada Beatrice Carver")).toBe("AB");
  });

  it("yields one letter for a one-word name rather than inventing a second", () => {
    expect(initials("Prince")).toBe("P");
  });

  it("yields nothing for an empty or whitespace-only name", () => {
    expect(initials("")).toBe("");
    expect(initials("   ")).toBe("");
  });

  it("collapses irregular whitespace", () => {
    expect(initials("  Dana   Reyes  ")).toBe("DR");
  });
});

describe("agentCode", () => {
  it("takes the first letters of the first two hyphen-separated parts", () => {
    expect(agentCode("sales-agent")).toBe("SA");
    expect(agentCode("claude-code")).toBe("CC");
  });

  it("treats spaces as separators too", () => {
    expect(agentCode("sales agent")).toBe("SA");
  });

  it("takes the first two letters of a single-word label", () => {
    expect(agentCode("scribe")).toBe("SC");
  });

  it("uses only the first two parts of a longer label", () => {
    expect(agentCode("acme-sales-agent")).toBe("AS");
  });

  it("yields nothing for a label with no word characters", () => {
    expect(agentCode("--")).toBe("");
  });
});

describe("agentName", () => {
  it("prefers the owner's display name", () => {
    expect(agentName({ label: "sales-agent", display_name: "Sales Agent" })).toBe("Sales Agent");
  });

  it("falls back to the label when there is no display name", () => {
    expect(agentName({ label: "sales-agent", display_name: null })).toBe("sales-agent");
    expect(agentName({ label: "sales-agent", display_name: "  " })).toBe("sales-agent");
  });

  it("derives the code from the label even when a display name is set", () => {
    // 6.1: the code comes from the stable identifier, never from free text an owner may edit.
    expect(agentCode("sales-agent")).toBe("SA");
    expect(agentName({ label: "sales-agent", display_name: "Quarterly Bot" })).toBe(
      "Quarterly Bot",
    );
  });
});

describe("principalKind", () => {
  it("calls a service account an agent (6.1)", () => {
    expect(principalKind({ display_name: "Importer", type: "service_account" })).toBe("agent");
  });

  it("calls a user a person", () => {
    expect(principalKind({ display_name: "Dana Reyes", type: "user" })).toBe("person");
  });

  it("falls back to person when the kind is unknown, rather than inventing agency", () => {
    // 6.5's instinct: never fabricate an agent. A sidecar entry with no `type` key,
    // or an id the map does not cover at all, under-claims rather than over-claims.
    expect(principalKind({ display_name: "Dana Reyes" })).toBe("person");
    expect(principalKind(undefined)).toBe("person");
  });
});
