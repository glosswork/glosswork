/**
 * The record page's header (`docs/DESIGN.md` 8.3): a title that names the
 * thing, not the row that holds it.
 *
 * **The title is the display value, never the key** (DD-23). `record.data[effective_
 * display_field_key]` is the chosen display field's current value; `services/base.py::
 * display_field` already decided which field that is, so this renders the answer rather than
 * re-deriving it. **The fallback:** the record key, in the display face, when that value is
 * empty or the type has no eligible field at all (`effective_display_field_key === null`) —
 * never an empty `<h1>`. This is the page's only `<h1>`; `heading-outline.spec.ts` depends on it
 * being exactly one.
 *
 * **The key does not vanish; it demotes.** It survives as a mono chip beside "Version N", both
 * read straight off the record.
 *
 * **"Last touched" is a `Hand`, never a `Pair`**. `lastTouchedBy` is the pure derivation;
 * this component only renders what it returns, through the one attribution primitive.
 */
import type { FieldDoc, ObjectTypeDetail } from "../api/objectTypes";
import type { RecordWithIncludes } from "../api/records";
import { formatTimestamp } from "../ui/datetime";
import { Hand } from "../ui/Avatar";
import { formatFieldValue } from "./fieldDisplay";
import { lastTouchedBy } from "./lastTouchedBy";

interface RecordHeaderProps {
  record: RecordWithIncludes;
  objectType: ObjectTypeDetail;
}

/** The record key when the display value is empty, absent, or the type names no eligible
 * field at all. A display field may be any non-relation, non-`user_ref`, non-`attachment` type
 * (DD-23), so this formats through the same `formatFieldValue` a select or boolean display field
 * renders with everywhere else on this page, rather than printing its bare stored value. */
function displayTitle(record: RecordWithIncludes, objectType: ObjectTypeDetail): string {
  const displayKey = objectType.effective_display_field_key;
  if (displayKey === null) return record.key;

  const raw = record.data[displayKey];
  if (raw === null || raw === undefined) return record.key;
  if (typeof raw === "string" && raw.trim() === "") return record.key;

  const field = objectType.fields.find((candidate) => candidate.key === displayKey);
  return field ? formatFieldValue(field as FieldDoc, raw) : String(raw);
}

export function RecordHeader({ record, objectType }: RecordHeaderProps) {
  const hand = lastTouchedBy(record);

  return (
    <header data-testid="record-header" className="space-y-2">
      <h1 className="font-display text-4xl font-semibold tracking-[-0.03em] text-ink">
        {displayTitle(record, objectType)}
      </h1>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-ink-2">
        <span
          data-testid="record-key-chip"
          className="inline-flex items-center rounded-full border border-line-2 bg-sunk px-2 py-0.5 font-mono text-xs text-ink-2"
        >
          {record.key}
        </span>
        <span aria-hidden="true">&middot;</span>
        <span>Version {record.version}</span>
      </div>
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-sm text-ink-2">
        <Hand
          principal={hand.principal}
          agentLabel={hand.agentLabel}
          fallbackId={hand.fallbackId}
          size="default"
        />
        <span aria-hidden="true">&middot;</span>
        <time dateTime={record.updated_at} title={record.updated_at}>
          {formatTimestamp(record.updated_at)}
        </time>
      </div>
    </header>
  );
}
