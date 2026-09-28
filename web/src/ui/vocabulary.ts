/**
 * The display vocabulary (docs/DESIGN.md 5): one module, one word per thing.
 *
 * docs/DESIGN.md 5 calls this "a display vocabulary layer", singular. Before this module there were
 * two halves of one idea in two places: `inbox/proposalSentence.ts` held the field-type words, and
 * nothing at all held the operator words — the filter builder's `<select>` rendered `{op}` raw, so
 * a person read `gte`. Both halves live here now, and `proposalSentence.ts` imports `fieldTypeWord`
 * rather than declaring it, so a second copy cannot appear without deleting this sentence.
 *
 * **An operator's word depends on the field type.** `lt` on a `date` is "is before"; the
 * same key on an `integer` is "is less than", because "Amount is before 1000" is not English.
 * That is the only type dependency: every other operator has one word whatever it is applied to.
 *
 * **Which words are specified and which were chosen.** docs/DESIGN.md 5 names six of them
 * verbatim — "is", "is not", "is before", "is after", "contains", "is empty" — and leaves the
 * rest to this module. The eighteen chosen here are written in 5's voice (sentences, no jargon
 * the person did not type):
 *
 * | Chosen | Reading |
 * | --- | --- |
 * | `not_contains` "does not contain", `starts_with` "starts with", `ends_with` "ends with" | the plain negation and the plain verbs |
 * | `in` "is any of", `not_in` "is none of" | the value is a list, and the chip says so |
 * | `gt` "is greater than", `lt` "is less than" | "is greater than" is the plain reading of `gt` on a number |
 * | `gte` "is at least", `lte` "is at most" | "is greater than or equal to" is the jargon 5 exists to remove |
 * | `gte`/`lte` on a date: "is on or after" / "is on or before" | the chronological reading of the same pair |
 * | `between` "is between" | |
 * | `has_any` "has any of", `has_all` "has all of", `has_none` "has none of" | multi-select, read as a sentence about the list |
 * | `is_not_empty` "is not empty" | the negation of 5's own "is empty" |
 * | `linked_to` "is linked to", `linked_to_any` "is linked to any of" | |
 * | `has_links` "has links", `has_no_links` "has no links" | |
 * | `is_null` "is blank", `is_not_null` "is not blank" | **not** "is empty": `is_null` and `is_empty` are two different operators and `multi_select` accepts both, so one word for both would make the chip row ambiguous |
 * | the `not` **node**: "Except where" (`NEGATION_WORD`, below) | not an operator at all; **not** "is not", which `neq` already says — see that constant's own header |
 *
 * **What proves it complete.** Not a test in this directory — a frontend test over this module's
 * own keys asserts only that every key it has, it has. `tests/test_display_vocabulary.py`
 * computes the real `(field type, operator)` cross-product from `glosswork.fieldtypes` and reads
 * `OPERATOR_WORDS` out of this file, so adding an operator to `_TYPE_OPS` without a word here
 * turns the backend suite red instead of rendering `gte` to a person. `OPERATOR_WORDS` is the
 * named literal that test parses; renaming it is a two-file change.
 */

/**
 * The word for every operator the API accepts, keyed by operator name, with the **quantity**
 * reading of the four comparisons. The chronological reading is the override below.
 *
 * Read by `tests/test_display_vocabulary.py` by name. Keep it a flat object literal of
 * `key: "word"` lines: that test parses the source rather than executing it, because the list it
 * checks against only exists in Python (there is no REST capabilities route).
 */
const OPERATOR_WORDS: Record<string, string> = {
  eq: "is",
  neq: "is not",
  contains: "contains",
  not_contains: "does not contain",
  starts_with: "starts with",
  ends_with: "ends with",
  in: "is any of",
  not_in: "is none of",
  gt: "is greater than",
  gte: "is at least",
  lt: "is less than",
  lte: "is at most",
  between: "is between",
  has_any: "has any of",
  has_all: "has all of",
  has_none: "has none of",
  is_empty: "is empty",
  is_not_empty: "is not empty",
  linked_to: "is linked to",
  linked_to_any: "is linked to any of",
  has_links: "has links",
  has_no_links: "has no links",
  is_null: "is blank",
  is_not_null: "is not blank",
};

/**
 * The same four operators, read as time rather than as quantity. docs/DESIGN.md 5 asks for
 * "is before" and "is after" by name, and those words are only correct on a date.
 */
const CHRONOLOGICAL_WORDS: Record<string, string> = {
  gt: "is after",
  gte: "is on or after",
  lt: "is before",
  lte: "is on or before",
};

/**
 * The field types that read their comparisons chronologically. An object literal per type rather
 * than a list of type names, so `noHardcodedOperators.test.ts` has nothing to bite on: its
 * fingerprint is a field-type key mapped directly to an *array* of operator tokens.
 */
const CHRONOLOGICAL_TYPES: Record<string, Record<string, string>> = {
  date: CHRONOLOGICAL_WORDS,
  datetime: CHRONOLOGICAL_WORDS,
};

/**
 * The operator as a person reads it, in the sentence a filter chip is (docs/DESIGN.md 7.4).
 *
 * An unknown operator returns its own key rather than a placeholder: a chip reading `gte` is bad
 * and a chip reading "unknown" is worse, and the structural test above is what stops an unknown
 * operator ever reaching a screen.
 */
export function operatorWord(
  op: string | null | undefined,
  fieldType?: string | null | undefined,
): string {
  if (!op) return "";
  const byType = fieldType ? CHRONOLOGICAL_TYPES[fieldType] : undefined;
  return byType?.[op] ?? OPERATOR_WORDS[op] ?? op;
}

/**
 * How a filter chip says that the tree wraps its condition in `not`.
 *
 * **A word for a node, not for an operator**, which is why it is a constant beside `operatorWord`
 * rather than a row inside `OPERATOR_WORDS`. `not` is a combinator in the filter grammar
 * (docs/MCP_TOOLS.md section 4): it takes no field and no value and it can wrap anything. It is
 * also why putting it in that table would be wrong mechanically as well as conceptually —
 * `tests/test_display_vocabulary.py` asserts by set equality that the operator table declares
 * nothing the API does not accept **as an operator**, so a `not` key there turns the backend
 * suite red, correctly.
 *
 * **Why not "is not".** `neq` already says that. docs/DESIGN.md 7.4's own example,
 * `Stage is not Lost`, is the `neq` operator, and `{"not": {"field": "stage", "op": "eq"…}}` is a
 * different tree that would read identically: two chips, one sentence, different JSON on the
 * wire. That failure mode is invisible to the person building it.
 * The collision is not special to `eq`: the API pairs a negative with `contains`, `in`,
 * `is_null`, `is_empty` and `has_links` too, so negating the operator's own word is not available
 * as a general rule either.
 *
 * **Why not "Not".** It is unambiguous, but it reads like a machine: `Not Notes contains renewal`,
 * `Not Due is after 13 Sep`. docs/DESIGN.md 5 asks for sentences. "Except where" is a clause, it
 * reads as English in front of every word in the table above, and it cannot collide with any of
 * them because none of them begins a sentence. **Chosen by the maintainer**; docs/DESIGN.md 5 now
 * records it.
 */
export const NEGATION_WORD = "Except where";

/** The display vocabulary for field types (docs/DESIGN.md 5): the API name is available on
 * hover and in the schema editor, and the sentence says the word a person would use.
 *
 * Moved here from `inbox/proposalSentence.ts` unchanged, word for word — thirteen keys in
 * ten distinct words, with `integer`/`decimal` both reading "Number" and `url`/`relation` both
 * reading "Link". `proposalSentence.test.ts` still covers it and was not touched. */
const FIELD_TYPE_WORDS: Record<string, string> = {
  short_text: "Text",
  long_text: "Long text",
  single_select: "Select",
  multi_select: "Multi-select",
  integer: "Number",
  decimal: "Number",
  date: "Date",
  datetime: "Date",
  url: "Link",
  attachment: "Attachment",
  user_ref: "Person",
  boolean: "Checkbox",
  relation: "Link",
};

export function fieldTypeWord(type: string | null | undefined): string {
  if (!type) return "another type";
  return FIELD_TYPE_WORDS[type] ?? type;
}

/**
 * A status pill's tone (docs/DESIGN.md 3, 7.3). `neutral` is 3's `sunk`/`ink-2`: the quiet
 * default, carrying no state at all.
 */
export type PillTone = "ok" | "warn" | "bad" | "human" | "neutral";

/** The four tones docs/DESIGN.md 3 names for a select value that carries state. `human` is 3's
 * "in progress" family; the order is fixed because the hash indexes into it. */
const TONE_CYCLE: readonly PillTone[] = ["ok", "warn", "bad", "human"];

/**
 * **FNV-1a, 32-bit**, over the UTF-16 code units of the key.
 *
 * Named and pinned rather than "some hash", because docs/DESIGN.md 3 says the tone is "chosen by
 * a stable hash of the option key" and stability is the whole property: the same value must be
 * the same colour in this browser tab, in the next one, and in a screenshot taken a year from
 * now. FNV-1a is four lines, has no dependency, and is fully specified by those four lines, so
 * this implementation *is* the specification. `Math.imul` keeps the multiply in 32 bits, which
 * is what makes the result identical in every JavaScript engine rather than drifting through a
 * double.
 */
function fnv1a32(key: string): number {
  let hash = 0x811c9dc5;
  for (let index = 0; index < key.length; index += 1) {
    hash ^= key.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0;
}

/**
 * The tone for one select option, from its **key alone**.
 *
 * Deliberately not from the field key plus the option key, as docs/DESIGN.md 3 says: a tone that is
 * green on the table and amber on the record page reads as meaning something. One value is one
 * colour everywhere.
 *
 * An absent or blank key is `neutral`. It is the one case 3's "unmapped options use `sunk`/
 * `ink-2`" can still describe once the hash is total: there is no option, so there is no state
 * to colour. (A schema-marked tone would be the other case, which 3 leaves to a schema change.)
 */
export function selectTone(optionKey: string | null | undefined): PillTone {
  if (typeof optionKey !== "string" || optionKey.trim() === "") return "neutral";
  return TONE_CYCLE[fnv1a32(optionKey) % TONE_CYCLE.length];
}
