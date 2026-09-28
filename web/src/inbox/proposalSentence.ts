/**
 * The headline sentence on a proposal (docs/DESIGN.md 5 and 8.4).
 *
 * A proposal headed by its change type in monospace — `delete_field` — over a `<dl>` of
 * `JSON.stringify`ed impact values names neither the object type, nor the field, nor who asked.
 * The promise of this screen is "your agent can build anything, it cannot delete anything
 * without you", and a key/value dump is not that sentence.
 *
 * **A pure function, not a render body** (AGENTS.md non-negotiable 3, DD-3's frontend clause).
 * It is also why the server projects `target` onto the proposal document rather than
 * composing the sentence itself: the sentence is voice, voice belongs to the frontend
 * (docs/DESIGN.md 5), and a unit test over all five change kinds is a test of this function,
 * where it would be a test of an unreachable string had the backend composed it.
 *
 * **Nothing here resolves an id.** Every noun arrives already resolved, on the same response:
 * `target` from the server's projection, the author from the two sidecars. That is DD-25,
 * and it is why this file takes plain data and imports nothing but types.
 */
import type { AgentLabelLike, PrincipalLike } from "../ui/attributionDerivation";
import { fieldTypeWord } from "../ui/vocabulary";

/** The five members of `DESTRUCTIVE_CHANGE_TYPES` (`services/schema.py`). A proposal can be
 * nothing else: the service refuses an unknown `change_type` with a 422 before one is stored. */
export type ChangeKind =
  | "delete_field"
  | "delete_object_type"
  | "change_field_type"
  | "remove_enum_option"
  | "tighten_constraint";

/** The `target` projection, as the wire carries it. `field_*` is null for
 * `delete_object_type`, which has no field — null rather than absent, so a reader never has to
 * branch on the change type before it can read the document. */
export interface ProposalTarget {
  object_type_key: string | null;
  object_type_name: string | null;
  /** The type as a collection, which is how every sentence here refers to it. */
  object_type_name_plural: string | null;
  field_key: string | null;
  field_name: string | null;
  field_type: string | null;
}

/** What the sentence needs about a proposal, which is less than the document carries. */
export interface SentenceInput {
  change_type: string;
  target: ProposalTarget | null;
  payload: Record<string, unknown>;
  /** The agent label that raised it, when the sidecar resolved one. */
  agentLabel?: AgentLabelLike | null;
  /** The person behind the credential, when the sidecar resolved one. */
  principal?: PrincipalLike;
}

/**
 * Who is being described, as a noun phrase.
 *
 * Precedence follows docs/DESIGN.md 6.5 exactly: the agent when there is one, the person
 * otherwise, and a deliberately non-committal noun when neither resolved. "Someone" is the
 * honest answer to an unresolved id — DD-27's rule applied to a sentence rather than to a row.
 * It never renders the raw id inline: a UUID in the middle of an English sentence is the defect
 * this screen exists to end, and the id stays available in the detail's metadata line.
 */
export function authorName(input: SentenceInput): string {
  if (input.agentLabel) {
    return input.agentLabel.display_name?.trim() || input.agentLabel.label;
  }
  if (input.principal?.display_name?.trim()) {
    return input.principal.display_name.trim();
  }
  return "Someone";
}

/**
 * The type as a person reads it, **plural**, falling back to the singular, then to the key, then
 * to a neutral noun.
 *
 * Plural because every sentence here refers to the type as a collection — "remove the Notes field
 * from Prospects", "delete Prospects entirely". With `name` alone an e2e run reads
 * "from Inbox Probe", which is wrong English no screenshot would ever catch.
 *
 * A target that resolved to nothing is an id naming no row, which DD-27 says is shown as
 * withheld rather than composed away.
 */
function typeName(target: ProposalTarget | null): string {
  return (
    target?.object_type_name_plural ||
    target?.object_type_name ||
    target?.object_type_key ||
    "an object type"
  );
}

/**
 * The field as a person reads it, or `null` when the projection resolved nothing.
 *
 * `null` rather than a fallback noun, because the fallback has to change the *grammar* of the
 * sentence rather than slot into it: "remove the Notes field" and "remove a field" are the two
 * readings, and a placeholder noun produces "remove the a field field", which this module's own
 * test guards against.
 */
function fieldName(target: ProposalTarget | null): string | null {
  return target?.field_name || target?.field_key || null;
}

/**
 * The display vocabulary for field types lives in `ui/vocabulary.ts` beside the operator
 * words: docs/DESIGN.md 5 calls it "a display vocabulary layer", singular, and
 * a table page importing its words from `inbox/` is the first step to a second copy.
 *
 * Re-exported rather than moved out of sight, because `ProposalDetail.tsx` and
 * `proposalSentence.test.ts` both read it from this module and neither is about where the words
 * are kept. The words themselves are unchanged.
 */
export { fieldTypeWord };

/** The values a `remove_enum_option` proposal would remove, as the payload carries them. */
function removedValues(payload: Record<string, unknown>): string[] {
  const raw = payload["remove_values"];
  return Array.isArray(raw) ? raw.map((value) => String(value)) : [];
}

function joinWords(values: string[]): string {
  if (values.length === 0) return "an option";
  if (values.length === 1) return values[0];
  if (values.length === 2) return `${values[0]} and ${values[1]}`;
  return `${values.slice(0, -1).join(", ")} and ${values[values.length - 1]}`;
}

/**
 * The sentence, for every change kind.
 *
 * Written as "<Author> wants to <do something> <to something>", present tense, names first,
 * no jargon the person did not type (docs/DESIGN.md 5). Each kind gets its own clause rather
 * than a templated verb, because "remove the Notes field from Prospects" and "change Notes from
 * Long text to Text" are not the same sentence with different nouns.
 *
 * An unknown `change_type` cannot reach this from the API — the service refuses one — but the
 * function still answers rather than throwing, because a screen that renders nothing is worse
 * than one that renders a vaguer truth.
 */
export function proposalSentence(input: SentenceInput): string {
  const who = authorName(input);
  const type = typeName(input.target);
  const field = fieldName(input.target);

  switch (input.change_type as ChangeKind) {
    case "delete_field":
      return field === null
        ? `${who} wants to remove a field from ${type}`
        : `${who} wants to remove the ${field} field from ${type}`;
    case "delete_object_type":
      return `${who} wants to delete ${type} entirely`;
    case "change_field_type": {
      const from = fieldTypeWord(input.target?.field_type);
      const to = fieldTypeWord(
        typeof input.payload["to_type"] === "string" ? input.payload["to_type"] : null,
      );
      return `${who} wants to change ${field ?? "a field"} on ${type} from ${from} to ${to}`;
    }
    case "remove_enum_option": {
      const values = removedValues(input.payload);
      const noun = values.length > 1 ? "options" : "option";
      const where = field === null ? type : `${field} on ${type}`;
      return `${who} wants to remove the ${joinWords(values)} ${noun} from ${where}`;
    }
    case "tighten_constraint": {
      const constraint =
        typeof input.payload["constraint"] === "string" ? input.payload["constraint"] : null;
      const what = field ?? "a field";
      if (constraint === "required") {
        return `${who} wants to make ${what} required on every ${type} record`;
      }
      if (constraint === "unique") {
        return `${who} wants to make ${what} unique across every ${type} record`;
      }
      return `${who} wants to tighten a rule on ${what} on ${type}`;
    }
    default:
      return `${who} wants to change ${type}`;
  }
}

/**
 * What the buttons do, stated in full (docs/DESIGN.md 8.4).
 *
 * The paragraph is deliberately specific about three things: that approving takes a snapshot
 * first (FR-S7), that the decision is recorded under the approver's own name, and that declining
 * leaves the data untouched. It does
 * **not** offer to send a reason back to the agent, because `Decline` posts no note —
 * a paragraph promising a channel the product does not have is the same defect as an "Ask why"
 * button nothing can read.
 */
export function decisionParagraph(input: SentenceInput, approverName: string): string {
  const who = authorName(input);
  return (
    `Approve takes a snapshot of the affected data first, then applies the change. ` +
    `Decline leaves everything exactly as it is. Either way the decision is recorded ` +
    `under your name, ${approverName}, and ${who} can see the outcome.`
  );
}
