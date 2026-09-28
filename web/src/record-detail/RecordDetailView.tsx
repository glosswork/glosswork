/**
 * The record page (`docs/DESIGN.md` 8.3): a header that names the thing
 * (`RecordHeader`), then two cards — Details, then Activity — side by side at or above
 * `--breakpoint-shell` (960px).
 *
 * **Below the shell breakpoint, Activity stacks first, for the whole range.** `docs/DESIGN.md` 9
 * mentions "Activity first" only under its 640px bullet, and a literal reading would leave the
 * 640-to-960px range unspecified. The rule is one rule: Activity is first everywhere below the
 * shell breakpoint, not just below the narrower one. `useIsWideViewport` is the same hook the shell
 * and `/inbox` already use to answer "which layout" from the same named breakpoint (one definition,
 * `hooks/useIsWideViewport.ts`'s own reason for existing) — reused here rather than a CSS `order`
 * utility, so which component renders *first* is also which one a keyboard or screen-reader user
 * reaches first: at each width, DOM order and visual order agree, rather than one following the
 * grid's visual placement while the other follows source order. A browser test proves the geometry
 * in a real browser, since `getBoundingClientRect` is zeroes in jsdom (AGENTS.md, Traps).
 *
 * **`canWrite` is derived once, here, and passed down**: every write this page
 * offers — the per-field click-to-edit, comment add/edit/delete, both reverts — ends at the same
 * `write` gate on this object type, so one derivation serves the whole page.
 * `access/hidingIsNeverTheOnlySignal.test.ts` pins this file as the only record-detail module
 * naming `levelAllows(`, beside the one `ReadOnlyBanner` the screen renders.
 */
import { levelAllows, type ObjectTypeDetail } from "../api/objectTypes";
import type { RecordWithIncludes } from "../api/records";
import { ReadOnlyBanner } from "../access/ReadOnlyBanner";
import { useIsWideViewport } from "../hooks/useIsWideViewport";
import { ActivityCard } from "./ActivityCard";
import { DetailsCard } from "./DetailsCard";
import { RecordHeader } from "./RecordHeader";

interface RecordDetailViewProps {
  record: RecordWithIncludes;
  objectType: ObjectTypeDetail;
}

export function RecordDetailView({ record, objectType }: RecordDetailViewProps) {
  const canWrite = levelAllows(objectType.your_access, "write");
  const isWide = useIsWideViewport();
  const fieldsByKey = Object.fromEntries(objectType.fields.map((field) => [field.key, field]));

  const details = (
    <DetailsCard key="details" record={record} fields={objectType.fields} canWrite={canWrite} />
  );
  const activity = (
    <ActivityCard
      key="activity"
      recordRef={record.key}
      recordVersion={record.version}
      fieldsByKey={fieldsByKey}
      principals={record.principals}
      canWrite={canWrite}
    />
  );

  return (
    <div className="space-y-6">
      <RecordHeader record={record} objectType={objectType} />
      <ReadOnlyBanner typeName={objectType.name} level={objectType.your_access} required="write" />
      <div className="grid grid-cols-1 items-start gap-6 shell:grid-cols-2">
        {isWide ? (
          <>
            {details}
            {activity}
          </>
        ) : (
          <>
            {activity}
            {details}
          </>
        )}
      </div>
    </div>
  );
}
