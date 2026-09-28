/**
 * The pure half of the attribution primitive (docs/DESIGN.md 6.1): what an `Avatar` shows and
 * which kind it is, derived from a principal or an agent label.
 *
 * A separate module from `Avatar.tsx` because business logic does not live in a render body
 * (DD-3's frontend equivalent, AGENTS.md non-negotiable 3), and because these are the rules
 * with edge cases worth testing directly rather than through a component: a one-word label, a
 * label that is punctuation, a display name that is a single character.
 */

/** Which family a hand belongs to. Shape and color both encode it, so the distinction
 * survives greyscale and color-blindness (docs/DESIGN.md 6.1, 10). */
export type HandKind = "person" | "agent";

/** A principal as the `principals` sidecar carries it, `type` included. */
export interface PrincipalLike {
  display_name: string;
  type?: "user" | "service_account";
}

/** An agent label as the `agent_labels` sidecar carries it. */
export interface AgentLabelLike {
  label: string;
  display_name: string | null;
}

/**
 * The kind of a bare principal.
 *
 * **A service account is an agent** (docs/DESIGN.md 6.1). This is why the sidecar carries
 * `type`: without it every service account renders as a person circle, which is a wrong
 * answer rendered confidently. A sidecar entry that predates that key — or a caller that
 * passes none — falls back to `person`, which is the safe direction: it under-claims agency
 * rather than inventing it (6.5).
 */
export function principalKind(principal: PrincipalLike | undefined): HandKind {
  return principal?.type === "service_account" ? "agent" : "person";
}

/**
 * Initials: the first letters of the first two words of the display name, uppercased.
 *
 * A single-word name yields one letter rather than two; padding it out of the rest of the word
 * would invent a second initial the person does not have. An empty or whitespace-only name
 * yields the empty string, and `Avatar` renders a shape with no glyph rather than a fabricated
 * one — the same instinct as 6.5.
 */
export function initials(displayName: string): string {
  const words = displayName.trim().split(/\s+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();
}

/**
 * The agent's two-letter code: the first letters of the first two hyphen- or space-separated
 * parts of the label (`sales-agent` -> `SA`, `claude-code` -> `CC`); a single-word label takes
 * its first two letters (`scribe` -> `SC`).
 *
 * "A label with no display name shows its key" (6.1): the *code* is always derived from the
 * label itself, which is the stable identifier, never from the display name, which is free text
 * an owner may change. The display name is what `Hand` renders beside the avatar.
 *
 * A label that separates into no word characters at all (`"--"`) yields the empty string rather
 * than throwing. Labels are bounded and non-blank at the edge (DD-17 refuses a blank or
 * over-long one with a 422), so this is defence rather than an expected path.
 */
export function agentCode(label: string): string {
  const parts = label.trim().split(/[-\s]+/).filter(Boolean);
  if (parts.length === 0) return "";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

/** What `Hand` writes beside an agent's avatar: the owner's chosen display name when there is
 * one, otherwise the label itself (docs/DESIGN.md 6.1). */
export function agentName(label: AgentLabelLike): string {
  return label.display_name?.trim() || label.label;
}

/**
 * An audit event or a comment, as far as attribution is concerned: the three resolved fields
 * DD-25 puts on both envelopes, plus the raw id each keeps as its documented fallback.
 */
export interface AttributedRow {
  principal_display_name: string | null;
  principal_id: string;
  /** `audit_events.principal_type` carries the kind directly. A comment row does not, so it
   * passes nothing and the bare principal falls back to `person` — under-claiming agency rather
   * than inventing it (docs/DESIGN.md 6.5). */
  principal_type?: string | null;
  /** The label's text, resolved at the repository. Null for a write by a person. */
  agent_label: string | null;
}

export interface HandInputs {
  principal?: PrincipalLike;
  agentLabel?: AgentLabelLike | null;
  fallbackId?: string;
}

/**
 * The mapping from an envelope row to `Hand`'s inputs, in one place because the record
 * timeline and the audit browser both need it and a second copy would drift.
 *
 * A row whose principal did not resolve keeps its raw id as the fallback (DD-25): the
 * id is still useful on its own — copyable, greppable, and visibly not a name.
 */
export function handInputsFor(row: AttributedRow): HandInputs {
  const principal =
    row.principal_display_name === null
      ? undefined
      : {
          display_name: row.principal_display_name,
          type: row.principal_type === "service_account" ? ("service_account" as const) : ("user" as const),
        };
  return {
    principal,
    agentLabel: row.agent_label ? { label: row.agent_label, display_name: null } : null,
    fallbackId: row.principal_id,
  };
}
