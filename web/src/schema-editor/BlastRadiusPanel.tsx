import { Link } from "react-router-dom";
import type { ImpactDoc } from "../api/schemaAdmin";
import { Button } from "../ui/Button";
import { tableClass, tableWrapClass, tdClass, thClass } from "../ui/tableClasses";

interface BlastRadiusPanelProps {
  proposalId: string;
  changeType: string;
  impact: ImpactDoc;
  message: string;
  onAcknowledge: () => void;
}

/**
 * FR-S6, FR-S8, PRD section 10: a destructive change never applies on submission — it
 * becomes a pending proposal, and the caller must see its computed blast radius
 * (affected record count, sample values, and for a type change a per-row coercion
 * dry-run) before the editor is considered done with this change. There is no
 * approve/reject affordance here — "a pending proposal... links to its entry in the
 * [proposals] list rather than duplicating an approve/reject affordance in the schema editor
 * itself" — this panel only makes the pending state and its impact impossible to miss, and
 * requires an explicit acknowledgement before it can be dismissed. **That argument is the
 * reason this link exists at all**, and it is kept here verbatim (AGENTS.md, Traps: deleting
 * code deletes the comment that explains why it was written that way).
 *
 * **The link names the proposal's own page, `/inbox/{proposalId}`.** The tests that pin it
 * assert the destination, not the words: when both matched `/settings/i`, a list of superseded
 * assertions that missed this file once nearly shipped a link to a screen where proposals no
 * longer are, with nothing red anywhere.
 */
export function BlastRadiusPanel({
  proposalId,
  changeType,
  impact,
  message,
  onAcknowledge,
}: BlastRadiusPanelProps) {
  return (
    <div
      role="alert"
      data-testid="blast-radius-panel"
      className="max-w-2xl space-y-3 rounded-card border border-bad-line bg-bad-soft p-4"
    >
      <h3 className="text-lg font-semibold text-bad">
        This change requires approval before it takes effect
      </h3>
      <p className="text-base text-ink">{message}</p>
      <dl className="grid grid-cols-[220px_1fr] gap-x-4 gap-y-1.5 rounded-card border border-line bg-surface px-3.5 py-2.5">
        <dt className="text-xs font-medium text-ink-2">Change type</dt>
        <dd className="font-mono text-sm text-ink">{changeType}</dd>
        <dt className="text-xs font-medium text-ink-2">Affected records</dt>
        <dd data-testid="blast-radius-affected-records" className="text-sm text-ink">
          {impact.affected_records}
        </dd>
        {impact.sample_values && (
          <>
            <dt className="text-xs font-medium text-ink-2">Sample affected values</dt>
            <dd data-testid="blast-radius-sample-values" className="font-mono text-xs text-ink">
              {impact.sample_values.map((value) => JSON.stringify(value)).join(", ") || "(none)"}
            </dd>
          </>
        )}
        {impact.coercion_failures && (
          <>
            <dt className="text-xs font-medium text-ink-2">Values that would fail to convert</dt>
            <dd>
              <div className={tableWrapClass}>
                <table className={tableClass}>
                  <thead>
                    <tr>
                      <th className={thClass}>Record</th>
                      <th className={thClass}>Value</th>
                      <th className={thClass}>Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {impact.coercion_failures.map((failure, index) => (
                      <tr key={index}>
                        <td className={`${tdClass} font-mono text-xs text-ink-2`}>
                          {failure.record_key}
                        </td>
                        <td className={`${tdClass} font-mono text-xs`}>{JSON.stringify(failure.value)}</td>
                        <td className={tdClass}>{failure.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </dd>
          </>
        )}
        {impact.in_use && (
          <>
            <dt className="text-xs font-medium text-ink-2">Option values in use</dt>
            <dd className="text-sm text-ink">
              {impact.in_use.count} record(s), e.g. {impact.in_use.sample_record_keys.join(", ")}
            </dd>
          </>
        )}
        {impact.violations && (
          <>
            <dt className="text-xs font-medium text-ink-2">
              Constraint violations ({impact.violations.constraint})
            </dt>
            <dd className="text-sm text-ink">
              {impact.violations.count} record(s)
              {impact.violations.sample_record_keys &&
                `, e.g. ${impact.violations.sample_record_keys.join(", ")}`}
            </dd>
          </>
        )}
      </dl>
      <p className="text-sm text-ink">
        Proposal <strong className="font-mono">{proposalId}</strong> is pending. Review, approve,
        or decline it in{" "}
        <Link to={`/inbox/${proposalId}`} className="text-human-ink hover:underline">
          your Inbox
        </Link>
        . Nothing has been applied yet.
      </p>
      <Button type="button" variant="danger" onClick={onAcknowledge}>
        Acknowledge
      </Button>
    </div>
  );
}
