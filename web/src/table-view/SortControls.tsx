/**
 * The explicit "add a sort key" affordance for multi-column sort (FR-U1). Clicking a column
 * header (wired in `TableView.tsx`) replaces the sort with that one field, cycling
 * unsorted/asc/desc; this control is how a *second* (or third...) key gets added without
 * disturbing the first, and how existing keys are reordered/removed. All state transitions come
 * from `sortSpec.ts`'s pure functions.
 *
 * **A chip.** docs/DESIGN.md 7.4 names it: "Group and sort are the same chip grammar
 * (`Group by Stage`, `Sort: Next action`)". The chip says the sentence and the multi-key control
 * below lives in the popover behind it, with every accessible name it had before it moved there,
 * so the specs that drive `Add sort key` take an opening click rather than a rename.
 *
 * **The chip names the keys and not their directions**, which is 7.4's own form: `Sort: Next
 * action`, not `Sort: Next action (asc)`. The direction is visible in two better places — inside
 * the popover, and on the column header's own ▲/▼ indicator — and a chip row is a row of
 * sentences rather than a rendering of the sort spec.
 */
import type { FieldDoc } from "../api/objectTypes";
import { cycleMultiSort, type SortKey } from "./sortSpec";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";
import { compactSelectClass, inlineLabelClass } from "../ui/classes";

export interface SortControlsProps {
  fields: FieldDoc[];
  sort: SortKey[];
  onChange: (sort: SortKey[]) => void;
}

export function SortControls({ fields, sort, onChange }: SortControlsProps) {
  const fieldName = (key: string) => fields.find((field) => field.key === key)?.name ?? key;

  return (
    <Chip
      data-testid="sort-chip"
      onRemove={sort.length === 0 ? undefined : () => onChange([])}
      removeLabel="Remove sorting"
      popover={<SortPanel {...{ fields, sort, onChange }} />}
    >
      {sort.length === 0 ? "Sort" : `Sort: ${sort.map((key) => fieldName(key.field)).join(", ")}`}
    </Chip>
  );
}

function SortPanel({ fields, sort, onChange }: SortControlsProps) {
  const fieldName = (key: string) => fields.find((field) => field.key === key)?.name ?? key;
  const availableFields = fields.filter(
    (field) => !sort.some((sortKey) => sortKey.field === field.key),
  );

  return (
    <div className="flex flex-col items-start gap-2" data-testid="sort-controls">
      {/* An `h2`, not an `h3`. The table view's only other heading is the
          object type's `h1` in `TableView.tsx`, so an `h3` here left the outline
          `["H1 <type>", "H3 Sort"]` — a skipped level on the most-used screen in the product.
          The class list is unchanged and Tailwind's preflight resets heading font size and
          weight to `inherit`, so this is a semantic change with no rendered difference. */}
      <h2 className="text-2xs font-semibold tracking-wider text-ink-2 uppercase">Sort</h2>
      {/* A column rather than a wrapping row: this list is inside a popover, where width is the
          scarce dimension and height is not. */}
      <ol className="flex flex-col items-stretch gap-1.5">
        {sort.map((sortKey, index) => (
          <li
            key={sortKey.field}
            className="flex items-center gap-1 rounded-ctl border border-line bg-ground py-0.5 pl-2 text-sm"
          >
            {index + 1}. {fieldName(sortKey.field)} ({sortKey.dir})
            <Button
              type="button"
              variant="quiet"
              className="px-1.5 py-0.5 text-sm"
              aria-label={`Toggle sort direction for ${fieldName(sortKey.field)}`}
              onClick={() => onChange(cycleMultiSort(sort, sortKey.field))}
            >
              Toggle direction
            </Button>
            <Button
              type="button"
              variant="quiet"
              className="px-1.5 py-0.5 text-sm"
              aria-label={`Remove ${fieldName(sortKey.field)} from sort`}
              onClick={() => onChange(sort.filter((key) => key.field !== sortKey.field))}
            >
              Remove
            </Button>
          </li>
        ))}
      </ol>
      <label className={inlineLabelClass}>
        Add sort key
        <select
          className={compactSelectClass}
          value=""
          onChange={(event) => {
            if (event.target.value) onChange(cycleMultiSort(sort, event.target.value));
          }}
        >
          <option value="">Choose field…</option>
          {availableFields.map((field) => (
            <option key={field.key} value={field.key}>
              {field.name}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
