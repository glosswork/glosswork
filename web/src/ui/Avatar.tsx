/**
 * The attribution primitive (docs/DESIGN.md 6.1 to 6.3, DD-41): one component family renders
 * every principal and every agent label anywhere in the product.
 *
 * **Shape carries the kind, not colour alone.** A person is a circle and an agent a square,
 * always, in both themes, so the distinction survives greyscale and colour-blindness
 * (docs/DESIGN.md 10). `web/src/ui/oneAttributionPrimitive.test.ts` fails if a second renderer
 * of a principal or a label appears; the derivations live in `attributionDerivation.ts` because
 * business logic does not live in a render body (DD-3's frontend equivalent).
 *
 * **It never fabricates an agent** (docs/DESIGN.md 6.5). A label can be legitimately absent: a
 * person at a keyboard, a session cookie (which DD-17 refuses a label for by design), and every
 * row written before the deployment upgraded past migration 9. `Hand` given no label renders
 * the person alone rather than guessing.
 */
import {
  agentCode,
  agentName,
  initials,
  principalKind,
  type AgentLabelLike,
  type HandKind,
  type PrincipalLike,
} from "./attributionDerivation";
import { cx } from "./cx";

/** docs/DESIGN.md 6.1's four sizes. `row` and `rowCompact` are the density presets (2.4). */
export type AvatarSize = "row" | "rowCompact" | "default" | "header";

const SIZE_CLASS: Record<AvatarSize, string> = {
  rowCompact: "h-[18px] w-[18px] text-[9px]",
  row: "h-5 w-5 text-[10px]",
  default: "h-6 w-6 text-[11px]",
  header: "h-8 w-8 text-sm",
};

/** Shape and palette together, per 6.1's table. Kept as one entry per kind so a change that
 * swapped a shape without swapping its palette would read as obviously wrong here. */
const KIND_CLASS: Record<HandKind, string> = {
  person: "rounded-full bg-human-soft border-human-line text-human-ink font-bold",
  agent: "rounded-card bg-agent-soft border-agent-line text-agent-ink font-mono font-medium",
};

const BASE_CLASS =
  "inline-flex shrink-0 select-none items-center justify-center border leading-none";

export interface AvatarProps {
  kind: HandKind;
  /** The glyphs inside: initials for a person, the two-letter code for an agent. May be empty,
   * in which case the shape renders alone rather than showing a fabricated letter. */
  text: string;
  /** The accessible name. Required: an avatar with no name is an unlabelled graphic, and
   * docs/DESIGN.md 10 asks every element to carry one. */
  label: string;
  size?: AvatarSize;
}

export function Avatar({ kind, text, label, size = "default" }: AvatarProps) {
  return (
    <span
      data-testid="avatar"
      data-kind={kind}
      className={cx(BASE_CLASS, SIZE_CLASS[size], KIND_CLASS[kind])}
      role="img"
      aria-label={label}
      title={label}
    >
      {text}
    </span>
  );
}

export interface HandProps {
  /** The principal behind the write. Absent when the sidecar does not cover the id, which is
   * indistinguishable from an id naming no row and is rendered as a placeholder, never dropped
   * (DD-27's instinct). */
  principal?: PrincipalLike;
  /** The agent label on the write, when there was one. */
  agentLabel?: AgentLabelLike | null;
  size?: AvatarSize;
  /** Render the avatar alone, for a 56px column that has no room for a name (6.4). */
  avatarOnly?: boolean;
  /** The raw id, shown only when the sidecar resolved nothing — copyable, greppable, and
   * visibly not a name (the raw-id fallback, DD-25). */
  fallbackId?: string;
}

/**
 * Avatar plus name (docs/DESIGN.md 6.2): `[avatar] Dana Reyes` or `[avatar] sales-agent`.
 *
 * **When an agent acts on a person's token, the person is named too** — `for Sam Okafor` in
 * `ink-3`. This is the case the record row can always answer, because it carries both the
 * principal and the label; it is *not* the same as 6.3's `Pair`, which is about two different
 * writes by two different kinds.
 */
export function Hand({
  principal,
  agentLabel,
  size = "default",
  avatarOnly = false,
  fallbackId,
}: HandProps) {
  const isAgent = Boolean(agentLabel);
  const kind: HandKind = isAgent ? "agent" : principalKind(principal);
  const personName = principal?.display_name ?? fallbackId ?? "Unknown";
  const name = agentLabel ? agentName(agentLabel) : personName;
  const text = agentLabel
    ? agentCode(agentLabel.label)
    : initials(principal?.display_name ?? "");
  // The accessible name says the kind out loud, so a screen reader gets what the shape carries.
  const label = agentLabel
    ? principal
      ? `${name} (agent) for ${personName}`
      : `${name} (agent)`
    : `${name}${kind === "agent" ? " (agent)" : ""}`;

  const avatar = <Avatar kind={kind} text={text} label={label} size={size} />;
  if (avatarOnly) return avatar;

  return (
    <span data-testid="hand" className="inline-flex items-center gap-1.5">
      {avatar}
      <span
        className={cx(
          "font-semibold",
          isAgent ? "text-agent-ink" : "text-ink",
          !principal && !agentLabel && fallbackId ? "font-mono font-normal" : undefined,
        )}
      >
        {name}
      </span>
      {/* 6.2's "for Sam Okafor". The id fallback survives here too: an agent write whose
          principal the sidecar did not resolve still says whose credential it used, in mono,
          rather than silently dropping the person (DD-25). Without this the raw id
          disappears exactly when an agent was involved — the case attribution matters most. */}
      {agentLabel && (principal || fallbackId) && (
        <span className="text-ink-3">
          for{" "}
          {principal ? (
            principal.display_name
          ) : (
            <span className="font-mono">{fallbackId}</span>
          )}
        </span>
      )}
    </span>
  );
}

export interface PairProps {
  principal: PrincipalLike;
  agentLabel: AgentLabelLike;
  size?: AvatarSize;
  avatarOnly?: boolean;
}

/**
 * Both avatars, overlapping by 6px, person first (docs/DESIGN.md 6.3), captioned
 * `Sam & sales-agent`.
 *
 * The ampersand is deliberate and singular: 6.3 records that this is the only place one appears
 * in the product, and that it is a character in a sentence rather than a mark.
 *
 * **Not used by the `By` column.** 6.4 would want a `Pair` when the last two versions were
 * different kinds, which needs the *previous* version's hand — data the record row does not carry.
 * 6.4 records that, and where the signal does live (the record's audit timeline). This component is
 * built because 6.3 specifies it for thread and record summaries, which is where it is used.
 */
export function Pair({ principal, agentLabel, size = "default", avatarOnly = false }: PairProps) {
  const personLabel = principal.display_name;
  const label = `${personLabel} and ${agentName(agentLabel)} (agent)`;
  const avatars = (
    <span className="inline-flex items-center" data-testid="pair">
      <Avatar
        kind={principalKind(principal)}
        text={initials(principal.display_name)}
        label={personLabel}
        size={size}
      />
      <span className="-ml-1.5 inline-flex">
        <Avatar
          kind="agent"
          text={agentCode(agentLabel.label)}
          label={`${agentName(agentLabel)} (agent)`}
          size={size}
        />
      </span>
    </span>
  );
  if (avatarOnly) return avatars;
  return (
    <span className="inline-flex items-center gap-1.5" aria-label={label}>
      {avatars}
      <span className="font-semibold text-ink">
        {principal.display_name} &amp; {agentName(agentLabel)}
      </span>
    </span>
  );
}
