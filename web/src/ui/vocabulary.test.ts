/**
 * **What this file cannot prove, and does not try to.** Completeness of the operator table is
 * `tests/test_display_vocabulary.py`'s job, because the list of operators the API accepts exists
 * only in Python (there is no REST capabilities route, so a frontend test would have to
 * transcribe the list, and a transcribed list that iterates itself proves nothing).
 * What is asserted here is the part that is genuinely about *this* module: that the word for a
 * comparison operator depends on the field type, that the field-type words survived the
 * move from `inbox/`, and that a tone is stable.
 */
import { describe, expect, it } from "vitest";

import { fieldTypeWord, NEGATION_WORD, operatorWord, selectTone } from "./vocabulary";

describe("operatorWord", () => {
  it("says the six words docs/DESIGN.md 5 names", () => {
    expect(operatorWord("eq")).toBe("is");
    expect(operatorWord("neq")).toBe("is not");
    expect(operatorWord("contains")).toBe("contains");
    expect(operatorWord("is_empty")).toBe("is empty");
    expect(operatorWord("lt", "date")).toBe("is before");
    expect(operatorWord("gt", "date")).toBe("is after");
  });

  it("reads the four comparisons as time on a date and on a datetime", () => {
    for (const type of ["date", "datetime"]) {
      expect(operatorWord("lt", type)).toBe("is before");
      expect(operatorWord("lte", type)).toBe("is on or before");
      expect(operatorWord("gt", type)).toBe("is after");
      expect(operatorWord("gte", type)).toBe("is on or after");
    }
  });

  it("reads the same four as quantity on an integer and on a decimal", () => {
    // "Amount is after 1000" is not English, which is the whole reason `operatorWord` takes a
    // field type at all.
    for (const type of ["integer", "decimal"]) {
      expect(operatorWord("lt", type)).toBe("is less than");
      expect(operatorWord("lte", type)).toBe("is at most");
      expect(operatorWord("gt", type)).toBe("is greater than");
      expect(operatorWord("gte", type)).toBe("is at least");
    }
  });

  it("gives every other operator one word whatever the field type", () => {
    for (const type of ["short_text", "date", "integer", "multi_select", "relation", undefined]) {
      expect(operatorWord("eq", type)).toBe("is");
      expect(operatorWord("in", type)).toBe("is any of");
      expect(operatorWord("is_null", type)).toBe("is blank");
    }
  });

  it("keeps `is blank` and `is empty` apart, because multi_select accepts both", () => {
    expect(operatorWord("is_null", "multi_select")).not.toBe(operatorWord("is_empty", "multi_select"));
    expect(operatorWord("is_not_null", "multi_select")).not.toBe(
      operatorWord("is_not_empty", "multi_select"),
    );
  });

  it("falls back to the operator's own key, and to nothing at all for no operator", () => {
    expect(operatorWord("sounds_like")).toBe("sounds_like");
    expect(operatorWord(null)).toBe("");
  });
});

/**
 * The word for the `not` **node**. A constant rather than a lookup because there is
 * nothing to look up: one combinator, one word.
 *
 * The assertion that matters is the collision one. A negated chip and a `neq` chip are different
 * trees and must not read alike — `{"not": {"op": "eq"}}` against `{"op": "neq"}` — and the same
 * holds for every operator the API pairs with a negative. Asserting the word is none of theirs is
 * what stops a later edit quietly choosing "is not" because it reads better in one example.
 */
describe("NEGATION_WORD", () => {
  it("is a word, and is not any operator's word", () => {
    expect(NEGATION_WORD.trim()).not.toBe("");

    const paired = ["eq", "neq", "contains", "not_contains", "in", "not_in", "is_null", "is_not_null"];
    for (const op of paired) {
      expect(NEGATION_WORD).not.toBe(operatorWord(op));
    }
  });

  it("opens a sentence, so it reads in front of an operator word rather than beside one", () => {
    // `Except where Notes contains renewal`. No operator word begins a sentence, which is what
    // keeps the two halves of a chip's grammar telling themselves apart.
    expect(NEGATION_WORD[0]).toBe(NEGATION_WORD[0].toUpperCase());
    expect(operatorWord("contains")[0]).toBe(operatorWord("contains")[0].toLowerCase());
  });
});

describe("fieldTypeWord", () => {
  /** The move from `inbox/proposalSentence.ts` changed no word. `proposalSentence.test.ts`
   * covers the same function through its old import path and was not touched. */
  it("says the words it said in inbox/", () => {
    expect(fieldTypeWord("short_text")).toBe("Text");
    expect(fieldTypeWord("user_ref")).toBe("Person");
    expect(fieldTypeWord("boolean")).toBe("Checkbox");
    expect(fieldTypeWord("multi_select")).toBe("Multi-select");
    expect(fieldTypeWord("integer")).toBe("Number");
    expect(fieldTypeWord("decimal")).toBe("Number");
  });

  it("falls back to the API name, and to a noun when there is no type", () => {
    expect(fieldTypeWord("geospatial")).toBe("geospatial");
    expect(fieldTypeWord(null)).toBe("another type");
  });
});

describe("selectTone", () => {
  it("gives one key the same tone every time it is asked", () => {
    expect(selectTone("negotiating")).toBe(selectTone("negotiating"));
    expect(selectTone("at_risk")).toBe(selectTone("at_risk"));
  });

  /**
   * The pins that make "stable" mean stable across *processes*, not just across two calls in
   * one. A tone that changed between a release and the next would repaint every screenshot and
   * quietly re-colour a status a person had learned.
   */
  it("is pinned: FNV-1a over the key, indexed into ok/warn/bad/human", () => {
    expect(selectTone("on_track")).toBe("ok");
    expect(selectTone("stage")).toBe("warn");
    expect(selectTone("at_risk")).toBe("bad");
    expect(selectTone("lost")).toBe("human");
  });

  it("does not depend on any field the key was found on", () => {
    // One value is one colour everywhere: the function takes nothing but the option key, so
    // there is no second argument a caller could disagree about.
    expect(selectTone.length).toBe(1);
  });

  it("reaches all four tones", () => {
    expect(new Set(["a", "b", "c", "d"].map(selectTone))).toEqual(
      new Set(["ok", "warn", "bad", "human"]),
    );
  });

  it("is the quiet default when there is no option key at all", () => {
    expect(selectTone(undefined)).toBe("neutral");
    expect(selectTone(null)).toBe("neutral");
    expect(selectTone("")).toBe("neutral");
    expect(selectTone("   ")).toBe("neutral");
  });
});
