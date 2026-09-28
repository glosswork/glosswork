/**
 * One proposal, as a page about a decision (docs/DESIGN.md 7.9 and 8.4).
 *
 * What it is not: the change type in monospace, the reason, and
 * `Object.entries(proposal.impact)` rendered as a `<dl>` of `JSON.stringify` values, where a
 * person reads `sample_values` → `["Retire after the audit."]` and is offered an unlabelled
 * **Approve**.
 *
 * The parts, and which specification each answers:
 *
 * - **Kind badge and raised-at line** — 8.4. The time goes through `ui/datetime.ts`, the first
 *   place in this product that renders a timestamp as anything but raw ISO.
 * - **The headline sentence** — what the proposal would do, in words, from `proposalSentence.ts`.
 * - **"It says:"** — the rationale, quoted, because it is the agent's own words and not ours.
 * - **Three impact tiles** — 7.9. The middle one says "Snapshot / taken before anything changes"
 *   rather than 8.4's "Saved": `snapshot_ref` is null while a proposal is pending, so a
 *   tile claiming a saved snapshot would assert something that has not happened.
 * - **The plain-language paragraph** — what Approve and Decline actually do, and whose name the
 *   decision lands under.
 * - **Sample values, struck through, with both counts.** No "See all N" link: the samples
 *   are capped at five distinct values server-side and no route returns the rest, so the honest
 *   thing is to say how many are shown out of how many there are.
 * - **Approve / Decline** — gated on the `admin` role, a known simplification of the real rule
 *   (`admin` on the proposal's own object type, which the browser cannot compute).
 *
 * There is deliberately **no "Ask <Agent> why"**. A comment cannot exist off a record, and
 * more decisively `list_schema_proposals` returns no comments — so the question would be one no
 * agent could read.
 */
import { Hand } from "../ui/Avatar";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { formatRaisedAt } from "../ui/datetime";
import { decisionParagraph, fieldTypeWord, proposalSentence } from "./proposalSentence";
import type { ProposalDoc } from "../api/schemaProposals";
import type { AgentLabelLike, PrincipalLike } from "../ui/attributionDerivation";

/** The change kinds, in the display vocabulary (docs/DESIGN.md 5). The API name stays available
 * in the metadata line; the badge says the words a person would use. */
const KIND_WORDS: Record<string, string> = {
  delete_field: "Remove a field",
  delete_object_type: "Delete a type",
  change_field_type: "Change a field type",
  remove_enum_option: "Remove an option",
  tighten_constraint: "Tighten a rule",
};

export interface ProposalDetailProps {
  proposal: ProposalDoc;
  principals: Record<string, PrincipalLike>;
  agentLabels: Record<string, AgentLabelLike>;
  /** The signed-in person's name, for the paragraph that says whose decision this is. */
  approverName: string;
  /** The `admin` role. Absent buttons plus the page's own access statement, never a
   * disabled button with a tooltip (DD-42's hide-do-not-disable rule). */
  canDecide: boolean;
  onApprove: () => void;
  onDecline: () => void;
  pending?: boolean;
}

/** One impact tile (docs/DESIGN.md 7.9): a display-face number at 30px over a 13px caption. */
function Tile({
  value,
  caption,
  tone,
  testId,
}: {
  value: string;
  caption: string;
  tone: "bad" | "ok" | "ink";
  testId: string;
}) {
  const toneClass =
    tone === "bad" ? "text-bad" : tone === "ok" ? "text-ok" : "text-ink";
  return (
    <div className="rounded-card border border-line bg-surface px-4 py-3" data-testid={testId}>
      <p className={`font-display text-[30px] leading-none ${toneClass}`}>{value}</p>
      <p className="mt-1.5 text-[13px] text-ink-2">{caption}</p>
    </div>
  );
}

/** The counts a proposal's impact carries, read defensively: `impact` is stored JSON, and the
 * shape differs per change kind (only field changes carry `non_empty_values`). */
function count(impact: Record<string, unknown>, key: string): number | null {
  const value = impact[key];
  return typeof value === "number" ? value : null;
}

function samples(impact: Record<string, unknown>): string[] {
  const value = impact["sample_values"];
  if (!Array.isArray(value)) return [];
  return value.map((entry) => (typeof entry === "string" ? entry : JSON.stringify(entry)));
}

export function ProposalDetail({
  proposal,
  principals,
  agentLabels,
  approverName,
  canDecide,
  onApprove,
  onDecline,
  pending = false,
}: ProposalDetailProps) {
  const principal = principals[proposal.proposed_by];
  const agentLabel = proposal.proposed_agent ? agentLabels[proposal.proposed_agent] : null;
  const sentenceInput = {
    change_type: proposal.change_type,
    target: proposal.target,
    payload: proposal.payload,
    agentLabel,
    principal,
  };

  const affected = count(proposal.impact, "affected_records");
  const nonEmpty = count(proposal.impact, "non_empty_values");
  const shown = samples(proposal.impact);
  const total = nonEmpty ?? affected;

  return (
    <article className="max-w-2xl" data-testid={`proposal-detail-${proposal.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="danger">{KIND_WORDS[proposal.change_type] ?? proposal.change_type}</Badge>
        <span className="text-xs text-ink-3" data-testid="proposal-raised-at">
          {formatRaisedAt(proposal.proposed_at)} · waiting on an administrator
        </span>
      </div>

      <h2 className="mt-3 font-display text-[26px] leading-tight text-ink" data-testid="proposal-headline">
        {proposalSentence(sentenceInput)}
      </h2>

      <div className="mt-3 flex items-center gap-2 text-sm text-ink-2">
        <Hand principal={principal} agentLabel={agentLabel} size="row" fallbackId={proposal.proposed_by} />
      </div>

      {proposal.reason && (
        <div className="mt-5">
          <p className="text-xs font-semibold text-ink">It says:</p>
          <blockquote
            className="mt-1.5 border-l-2 border-line-2 pl-3 text-base italic text-ink-2"
            data-testid="proposal-reason"
          >
            {proposal.reason}
          </blockquote>
        </div>
      )}

      <div
        className="mt-5 grid gap-3"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}
        data-testid="proposal-impact-tiles"
      >
        <Tile
          testId="impact-affected"
          tone="bad"
          value={affected === null ? "—" : String(affected)}
          caption={affected === 1 ? "record is affected" : "records are affected"}
        />
        {/* Future tense, because the snapshot has not been taken yet: `snapshot_ref` is
            null until `approve_proposal` writes it (FR-S7). */}
        <Tile testId="impact-snapshot" tone="ok" value="Snapshot" caption="taken before anything changes" />
        <Tile testId="impact-nothing-yet" tone="ink" value="0" caption="changes happen until you approve" />
      </div>

      <p className="mt-5 text-base text-ink-2" data-testid="proposal-decision-paragraph">
        {decisionParagraph(sentenceInput, approverName)}
      </p>

      {shown.length > 0 && (
        <div className="mt-5" data-testid="proposal-samples">
          <p className="text-xs font-semibold text-ink">
            What would be removed from {proposal.target?.field_name ?? "this field"}
          </p>
          <ul className="mt-1.5 space-y-1">
            {shown.map((value, index) => (
              <li key={`${value}-${index}`} className="text-base text-ink-2 line-through">
                {value}
              </li>
            ))}
          </ul>
          {/* Both counts, no link. The samples are five distinct values at most
              (`_IMPACT_SAMPLE_SIZE`) and nothing returns the rest. */}
          <p className="mt-1.5 text-xs text-ink-3" data-testid="proposal-sample-count">
            {total === null
              ? `${shown.length} shown.`
              : `${shown.length} of ${total} values shown.`}
          </p>
        </div>
      )}

      {proposal.change_type === "change_field_type" && (
        <p className="mt-4 text-xs text-ink-3">
          Values that cannot be converted to{" "}
          {fieldTypeWord(
            typeof proposal.payload["to_type"] === "string" ? proposal.payload["to_type"] : null,
          )}{" "}
          will stop the change rather than being discarded.
        </p>
      )}

      {canDecide && (
        <div className="mt-6 flex gap-2">
          <Button type="button" variant="primary" onClick={onApprove} disabled={pending}>
            Approve
          </Button>
          <Button type="button" variant="danger" onClick={onDecline} disabled={pending}>
            Decline
          </Button>
        </div>
      )}

      <p className="mt-6 font-mono text-[11px] text-ink-3">{proposal.id}</p>
    </article>
  );
}

/** One row in the list pane (docs/DESIGN.md 7.9): a `24px 1fr` grid, three lines. */
export function ProposalListItem({
  proposal,
  principals,
  agentLabels,
  selected,
}: {
  proposal: ProposalDoc;
  principals: Record<string, PrincipalLike>;
  agentLabels: Record<string, AgentLabelLike>;
  selected: boolean;
}) {
  const principal = principals[proposal.proposed_by];
  const agentLabel = proposal.proposed_agent ? agentLabels[proposal.proposed_agent] : null;
  const sentence = proposalSentence({
    change_type: proposal.change_type,
    target: proposal.target,
    payload: proposal.payload,
    agentLabel,
    principal,
  });

  return (
    <div
      className={`grid grid-cols-[24px_1fr] gap-3 px-3 py-2.5 ${selected ? "bg-human-soft" : ""}`}
    >
      {/* `avatarOnly`: the 24px column has no room for a name, and the name is in the sentence
          beside it anyway (docs/DESIGN.md 6.4's reasoning for the By column). */}
      <Hand
        principal={principal}
        agentLabel={agentLabel}
        size="row"
        avatarOnly
        fallbackId={proposal.proposed_by}
      />
      <div className="min-w-0">
        <p className="truncate text-[13.5px] font-semibold text-ink">{sentence}</p>
        <p className="truncate text-[12.5px] text-ink-2">{proposal.reason ?? "No reason given."}</p>
        <p className="text-[11.5px] text-ink-3">
          {KIND_WORDS[proposal.change_type] ?? proposal.change_type} ·{" "}
          {formatRaisedAt(proposal.proposed_at).replace(/^Raised /, "")}
        </p>
      </div>
    </div>
  );
}

/** Exported for the empty detail pane, so the two panes cannot disagree about the placeholder. */
export function NoProposalSelected() {
  return (
    <p className="px-3 py-2 text-base text-ink-2" data-testid="no-proposal-selected">
      Select a proposal to review it.
    </p>
  );
}
