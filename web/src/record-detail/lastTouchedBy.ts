/**
 * The record header's "last touched" line: who last wrote the
 * record, rendered as a `Hand`, never a `Pair`.
 *
 * `RecordDoc` carries exactly two attribution fields on the row itself — `updated_by` and
 * `updated_by_agent_label_id` — resolved through the response's `principals` and `agent_labels`
 * sidecars. That is 6.2's `Hand`: a person, or an agent acting on a person's credential,
 * rendered `[SA] sales-agent for Sam Okafor`. It is **not** 6.3's `Pair`: a `Pair` needs the
 * *previous* version's hand to say two different writes by two different kinds both touched the
 * record recently, and the record row does not carry a previous version's anything —
 * `ui/Avatar.tsx`'s own docstring (`:88-94`, `:163-167`) says the same thing from the `By`
 * column's side. Rendering a `Pair` from these two fields would claim two hands from data that
 * describes one, so this returns `Hand`'s inputs and nothing else, and `RecordHeader.tsx` never
 * imports `Pair`.
 *
 * A pure function, in its own module with its own test: the header's attribution inputs are
 * pure functions in their own modules.
 */
import type { RecordDoc } from "../api/records";
import type { HandInputs } from "../ui/attributionDerivation";

export function lastTouchedBy(record: RecordDoc): HandInputs {
  const principalRef = record.principals?.[record.updated_by];
  const principal = principalRef
    ? { display_name: principalRef.display_name, type: principalRef.type }
    : undefined;

  // `!` rather than `=== null`: the wire's own type is `string | null`, never `undefined`, but a
  // defensive read guards the same way `PrincipalName` and `Hand` do elsewhere for a value that
  // came back looser than its type says — a page that renders no agent bar is a far smaller
  // failure than one that crashes the whole header on a malformed document.
  if (!record.updated_by_agent_label_id) {
    return { principal, agentLabel: null, fallbackId: record.updated_by };
  }

  const agentLabelRef = record.agent_labels?.[record.updated_by_agent_label_id];
  // The write carried a label, but the sidecar has no entry for it — the same shape of gap
  // DD-25 documents for an unresolved principal. Rather than silently rendering the write as
  // though no agent were involved (6.5: "never fabricates an agent" cuts the other way too: it
  // must not fabricate an *absence*), the raw id stands in as the label text, in the one shape
  // `Hand` has for text it did not resolve to a display name.
  const agentLabel = agentLabelRef ?? {
    label: record.updated_by_agent_label_id,
    display_name: null,
  };

  return { principal, agentLabel, fallbackId: record.updated_by };
}
