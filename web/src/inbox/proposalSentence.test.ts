/**
 * The headline sentence, for all five change kinds.
 *
 * All five are here by name rather than by a loop over a list, because the point of the test is
 * that each kind reads as a sentence a person would say, and a loop asserting "contains the
 * field name" would pass against five bad sentences.
 *
 * The two fallbacks matter as much as the five sentences: a proposal raised over REST with no
 * `X-Agent-Label` has no agent (docs/DESIGN.md 6.5), and an id the sidecar did not resolve must
 * not appear inline (DD-27).
 */
import { describe, expect, it } from "vitest";

import {
  authorName,
  decisionParagraph,
  fieldTypeWord,
  proposalSentence,
  type SentenceInput,
} from "./proposalSentence";

const PROSPECT = {
  object_type_key: "prospect",
  object_type_name: "Prospect",
  object_type_name_plural: "Prospects",
  field_key: "notes",
  field_name: "Notes",
  field_type: "long_text",
};

const CLAUDE = { label: "claude-code", display_name: null };

function input(overrides: Partial<SentenceInput> = {}): SentenceInput {
  return {
    change_type: "delete_field",
    target: PROSPECT,
    payload: {},
    agentLabel: CLAUDE,
    ...overrides,
  };
}

describe("the headline sentence, for all five change kinds", () => {
  it("delete_field names the field and the type", () => {
    expect(proposalSentence(input())).toBe(
      "claude-code wants to remove the Notes field from Prospects",
    );
  });

  it("delete_object_type names the type and says entirely", () => {
    expect(proposalSentence(input({ change_type: "delete_object_type" }))).toBe(
      "claude-code wants to delete Prospects entirely",
    );
  });

  it("change_field_type names both types in the display vocabulary, not the API's", () => {
    const sentence = proposalSentence(
      input({ change_type: "change_field_type", payload: { to_type: "short_text" } }),
    );

    expect(sentence).toBe("claude-code wants to change Notes on Prospects from Long text to Text");
    // docs/DESIGN.md 5: the person reads "Long text", never `long_text`.
    expect(sentence).not.toContain("long_text");
    expect(sentence).not.toContain("short_text");
  });

  it("remove_enum_option names the values, and agrees in number", () => {
    const one = proposalSentence(
      input({
        change_type: "remove_enum_option",
        target: { ...PROSPECT, field_key: "stage", field_name: "Stage" },
        payload: { remove_values: ["Closed won"] },
      }),
    );
    const many = proposalSentence(
      input({
        change_type: "remove_enum_option",
        target: { ...PROSPECT, field_key: "stage", field_name: "Stage" },
        payload: { remove_values: ["Closed won", "Closed lost", "Parked"] },
      }),
    );

    expect(one).toBe("claude-code wants to remove the Closed won option from Stage on Prospects");
    expect(many).toBe(
      "claude-code wants to remove the Closed won, Closed lost and Parked options from Stage on Prospects",
    );
  });

  it("tighten_constraint says which rule, in words", () => {
    expect(
      proposalSentence(
        input({ change_type: "tighten_constraint", payload: { constraint: "required" } }),
      ),
    ).toBe("claude-code wants to make Notes required on every Prospects record");

    expect(
      proposalSentence(
        input({ change_type: "tighten_constraint", payload: { constraint: "unique" } }),
      ),
    ).toBe("claude-code wants to make Notes unique across every Prospects record");
  });
});

describe("who is named", () => {
  it("prefers the agent's display name over its label", () => {
    expect(
      proposalSentence(input({ agentLabel: { label: "claude-code", display_name: "Claude Code" } })),
    ).toBe("Claude Code wants to remove the Notes field from Prospects");
  });

  it("falls back to the person when there is no agent label", () => {
    // A proposal raised over REST without `X-Agent-Label`, which is every proposal the visual
    // fixture seeds. docs/DESIGN.md 6.5: under-claim agency rather than invent it.
    expect(
      proposalSentence(input({ agentLabel: null, principal: { display_name: "Dana Reyes" } })),
    ).toBe("Dana Reyes wants to remove the Notes field from Prospects");
  });

  it("never puts an unresolved id inside the sentence", () => {
    // DD-27: a reference the reader cannot resolve is shown as withheld, never composed away —
    // and never rendered as a UUID in the middle of an English sentence, which is the defect
    // this screen exists to end.
    const sentence = proposalSentence(input({ agentLabel: null, principal: undefined }));

    expect(sentence).toBe("Someone wants to remove the Notes field from Prospects");
  });

  it("treats a blank display name as no name at all", () => {
    expect(authorName(input({ agentLabel: null, principal: { display_name: "   " } }))).toBe(
      "Someone",
    );
  });
});

describe("a target that resolved to nothing", () => {
  it("degrades to neutral nouns rather than rendering null", () => {
    const sentence = proposalSentence(input({ target: null }));

    // The grammar changes rather than a placeholder noun being slotted in: a placeholder noun
    // produces "remove the a field field from an object type", and this is the assertion that
    // catches it.
    expect(sentence).toBe("claude-code wants to remove a field from an object type");
    expect(sentence).not.toContain("null");
  });

  it("uses the key when only the name is missing", () => {
    expect(
      proposalSentence(
        input({
          target: {
            ...PROSPECT,
            object_type_name: null,
            object_type_name_plural: null,
            field_name: null,
          },
        }),
      ),
    ).toBe("claude-code wants to remove the notes field from prospect");
  });
});

describe("the field type vocabulary", () => {
  it("maps the API's names onto the words DESIGN.md 5 lists", () => {
    expect(fieldTypeWord("short_text")).toBe("Text");
    expect(fieldTypeWord("user_ref")).toBe("Person");
    expect(fieldTypeWord("boolean")).toBe("Checkbox");
  });

  it("passes an unknown type through rather than inventing a word for it", () => {
    expect(fieldTypeWord("geospatial")).toBe("geospatial");
    expect(fieldTypeWord(null)).toBe("another type");
  });
});

describe("the paragraph that says what the buttons do", () => {
  it("states the snapshot, that Decline changes nothing, and whose name it lands under", () => {
    const paragraph = decisionParagraph(input(), "Dana Reyes");

    expect(paragraph).toContain("snapshot");
    expect(paragraph).toContain("Decline leaves everything exactly as it is");
    expect(paragraph).toContain("under your name, Dana Reyes");
  });

  it("does not promise to send a reason back to the agent", () => {
    // Decline posts no note, so the paragraph must not imply a channel that does not
    // exist — the same defect as an "Ask why" button nothing can read.
    const paragraph = decisionParagraph(input(), "Dana Reyes");

    expect(paragraph).not.toMatch(/reason|why|explain/i);
  });
});
