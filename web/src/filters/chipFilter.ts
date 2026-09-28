/**
 * The chip row's grammar: the flat AND of docs/DESIGN.md 7.4, and the one place that converts
 * between it and the filter tree on the wire.
 *
 * **The tree is the truth; a chip is a rendering of one branch of it.** `docs/DESIGN.md` 7.4
 * says filters render as sentence chips and `PRD.md` FR-U1 says the table view has "a filter
 * builder mirroring the API grammar", which nests `and`/`or`/`not` arbitrarily
 * (`docs/MCP_TOOLS.md` section 4). This satisfies both rather than picking one: the common
 * case — conditions ANDed together — becomes a row of sentences, and everything else is
 * reachable through the `Advanced` chip, which opens the tree builder that already exists.
 * **Nothing here changes the JSON that goes out**; that is the invariant, and this
 * module exists precisely so the conversion is written once and can be tested against the AST
 * literal the tree builder produces.
 *
 * **What is chip-expressible, exactly.** `toChipConditions` returns `null` — read as "this
 * filter is Advanced" — for anything it cannot render as a flat AND of sentences:
 *
 * | Filter | Chips |
 * | --- | --- |
 * | `null` | none: the row is just `+ Add filter` |
 * | `{field, op, value}` | one chip |
 * | `{"not": {field, op, value}}` | one chip with "Exclude matches" on |
 * | `{"and": [ …conditions and `not`-of-conditions… ]}` | one chip each |
 * | `{"or": …}`, a group nested inside a group, `{"not": {group}}` | **`null`** — the `Advanced filter` chip |
 *
 * The last row exists because `SavedViewService._validate_config` asserts only
 * that a saved view's config is a dict, and the tree builder composes
 * arbitrary trees, so a filter no chip row can express **can already be sitting in a saved view**
 * and be loaded into this screen. It gets a defined rendering rather than an undefined one.
 *
 * **`fromChipConditions` is not a left inverse of `toChipConditions`, deliberately.** One chip
 * serialises to a bare condition, never `{"and": [condition]}` — that is the AST the tree
 * builder sends for one condition. So a loaded `{"and": [c]}` renders as one chip and
 * would serialise back as `c` if the person edited it. Nothing re-serialises a filter the person
 * has not touched: this module is read-only for display, and `FilterChipRow` calls
 * `fromChipConditions` only from an edit. A loaded filter therefore rides the wire byte-for-byte
 * as it was stored until somebody changes it.
 */
import { NEGATION_WORD, operatorWord } from "../ui/vocabulary";
import { valueCardinality } from "./completeness";
import type { FilterableField } from "./filterableFields";
import {
  groupChildren,
  isAndNode,
  isConditionNode,
  isNotNode,
  type FilterCondition,
  type FilterNode,
} from "./types";

/**
 * One chip: a leaf condition, and whether the tree wraps it in `not`.
 *
 * `negated` is a separate flag rather than part of the condition because that is what the wire
 * shape says — `not` is a node, not an operator (`docs/MCP_TOOLS.md` section 4) — and because
 * docs/DESIGN.md 7.4 renders it as its own control, the switch labelled "Exclude matches".
 */
export interface ChipCondition {
  condition: FilterCondition;
  negated: boolean;
}

/** One node as a chip, or `null` if it is not a leaf condition (or a `not` around one). */
function asChip(node: FilterNode): ChipCondition | null {
  if (isConditionNode(node)) return { condition: node, negated: false };
  if (isNotNode(node) && isConditionNode(node.not)) return { condition: node.not, negated: true };
  return null;
}

/**
 * The chips for one filter tree, or `null` when the tree is not a flat AND of conditions and
 * must be rendered as the single `Advanced filter` chip instead.
 *
 * `{"and": []}` maps to an empty chip row rather than to `null`. It is an unsendable node
 * (`completeness.ts`: `filters.py:158-160` refuses it) so the gate means it cannot arrive from
 * this screen's own controls, and "no chips" is the honest rendering of a group holding nothing.
 */
export function toChipConditions(tree: FilterNode | null): ChipCondition[] | null {
  if (tree === null) return [];
  const single = asChip(tree);
  if (single !== null) return [single];
  if (!isAndNode(tree)) return null;
  const chips: ChipCondition[] = [];
  for (const child of groupChildren(tree)) {
    const chip = asChip(child);
    if (chip === null) return null;
    chips.push(chip);
  }
  return chips;
}

/** The filter tree for a row of chips: nothing, one bare condition, or an `and` of them. */
export function fromChipConditions(chips: readonly ChipCondition[]): FilterNode | null {
  const nodes: FilterNode[] = chips.map((chip) =>
    chip.negated ? { not: chip.condition } : chip.condition,
  );
  if (nodes.length === 0) return null;
  if (nodes.length === 1) return nodes[0];
  return { and: nodes };
}

/**
 * Whether the chip row is the right place to show a `validation_failed` the server answered a
 * query with, read by `TableView` to decide between the chip and the page-level
 * "Could not load records" alert that empties the table.
 *
 * A filter that is not chip-expressible is owned whole by the `Advanced filter` chip, because
 * that chip is the whole filter. A flat AND is owned only when the error names one of its chips:
 * a `validation_failed` that names nothing in the filter did not come from the filter, and the
 * page-level alert is still the right place for it.
 */
export function ownsFilterError(filter: FilterNode | null, fieldKey: string | null): boolean {
  if (filter === null) return false;
  const chips = toChipConditions(filter);
  if (chips === null) return true;
  if (fieldKey === null) return false;
  return chips.some((chip) => chip.condition.field === fieldKey);
}

/**
 * One value as a person reads it, inside a chip's sentence.
 *
 * **Not `formatFieldValue`**, and the difference is the point: that function renders a *stored
 * record value* for a cell, and a filter value is a different animal — it can be a date token
 * (`@today-7d`), `@me`, a list of option keys, or a pair of bounds, none of which a cell ever
 * holds. What the two do share is the rule that a select renders its option's **label** and
 * never its stored key (FR-U2, docs/DESIGN.md 7.3), and that is the line below that reads
 * `field.options`.
 */
function valueWord(field: FilterableField | undefined, value: unknown): string {
  if (value === null || value === undefined) return "";
  const option = field?.options?.find((candidate) => candidate.value === value);
  if (option) return option.label;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function valuePhrase(field: FilterableField | undefined, condition: FilterCondition): string {
  const cardinality = valueCardinality(condition.op);
  if (cardinality === "none") return "";
  const value = condition.value;
  if (cardinality === "pair" && Array.isArray(value)) {
    return `${valueWord(field, value[0])} and ${valueWord(field, value[1])}`;
  }
  if (cardinality === "list" && Array.isArray(value)) {
    return value.map((one) => valueWord(field, one)).join(", ");
  }
  return valueWord(field, value);
}

/**
 * The chip's sentence: `Stage is not Lost` (docs/DESIGN.md 7.4).
 *
 * The field's **name**, the operator's **word** — through `operatorWord`, which is the one
 * display vocabulary and which needs the field type because `lt` on a date is "is before"
 * and on a number is "is less than" — and the value's **label**. No call site writes any
 * of those three words itself.
 *
 * **A negated chip opens with `NEGATION_WORD`** — `Except where Notes contains renewal` — and
 * that word lives in `ui/vocabulary.ts` with the others rather than here, because docs/DESIGN.md
 * 5 calls the display vocabulary singular and a word written at a call site is the second one.
 * Its header carries why it is not "is not" (that is `neq`, and the two trees would read
 * identically while sending different JSON) and why it is not "Not".
 */
export function chipSentence(
  field: FilterableField | undefined,
  chip: ChipCondition,
  fallbackFieldName?: string,
): string {
  const name = field?.name ?? fallbackFieldName ?? chip.condition.field;
  const word = operatorWord(chip.condition.op, field?.type);
  const phrase = valuePhrase(field, chip.condition);
  return [chip.negated ? NEGATION_WORD : "", name, word, phrase]
    .filter((part) => part !== "")
    .join(" ");
}
