/**
 * Column show/hide/reorder control (FR-U1). Reorder is move-up/move-down buttons, an accepted
 * substitute for drag-and-drop; resize itself happens on the
 * column header's own resize handle (`TableView.tsx`), not here.
 *
 * **A popover behind a chip**, which is docs/DESIGN.md 8.2: "The filter builder and column
 * picker are popovers." The case against a collapsible picker is kept and answered on the list
 * below, where it matters.
 *
 * The chip reads `Columns` when every column is showing and `Columns: 5 of 8` when some are not.
 * 7.4 gives no sentence for this one (it names group and sort), so the count is a choice: a
 * picker behind a popover can hide a column and say nothing about it, and a person who cannot
 * find a field they know exists then has no way to tell whether it is hidden or gone.
 */
import type { FieldDoc } from "../api/objectTypes";
import { moveColumn } from "./columnOrder";
import { Button } from "../ui/Button";
import { Chip } from "../ui/Chip";

export interface ColumnPickerProps {
  fields: FieldDoc[];
  columnOrder: string[];
  columnVisibility: Record<string, boolean>;
  onChangeOrder: (order: string[]) => void;
  onChangeVisibility: (visibility: Record<string, boolean>) => void;
}

export function ColumnPicker({
  fields,
  columnOrder,
  columnVisibility,
  onChangeOrder,
  onChangeVisibility,
}: ColumnPickerProps) {
  const orderedKeys = columnOrder.length ? columnOrder : fields.map((field) => field.key);
  const shown = orderedKeys.filter((key) => columnVisibility[key] !== false).length;

  return (
    <Chip
      data-testid="columns-chip"
      popover={
        <ColumnPickerPanel
          {...{ fields, columnOrder, columnVisibility, onChangeOrder, onChangeVisibility }}
        />
      }
    >
      {shown === orderedKeys.length ? "Columns" : `Columns: ${shown} of ${orderedKeys.length}`}
    </Chip>
  );
}

function ColumnPickerPanel({
  fields,
  columnOrder,
  columnVisibility,
  onChangeOrder,
  onChangeVisibility,
}: ColumnPickerProps) {
  const fieldsByKey = Object.fromEntries(fields.map((field) => [field.key, field]));
  const orderedKeys = columnOrder.length ? columnOrder : fields.map((field) => field.key);

  return (
    <fieldset className="w-64" data-testid="column-picker">
      <legend className="px-1 text-2xs font-semibold tracking-wider text-ink-2 uppercase">
        Columns
      </legend>
      {/* A bounded height with its own scroller, so the picker's footprint is not a linear
          function of field count: unbounded it was 503px on a twelve-field type, which alone
          pushed the table below the fold.

          **Why a popover does not break the real-browser spec.**
          `e2e/table-end-to-end.spec.ts` unchecks a column through this control in a real
          browser, where Playwright requires visibility, so a collapsed picker would break the
          spec that matters while still passing the jsdom tests that reach these checkboxes with
          `getByLabelText`. docs/DESIGN.md 8.2 makes the
          column picker a popover, and the answer to that objection is that the spec gains an
          opening click rather than losing the uncheck: it opens the popover and unchecks
          `Status` in a real browser. The bound and the scroller stay inside the popover —
          Playwright scrolls within a scroll container before acting, so bounding costs no query
          churn — and are what keep a forty-field type from growing a popover taller than the
          window.

          **No real-browser assertion holds this height.** A `boundingBox().height <= 160` on
          `column-picker` over the twelve-field `vis_wide` fixture cannot work behind a popover:
          that element does not exist until the chip is clicked, so the assertion would measure a
          locator timeout rather than a footprint, and a popover has no footprint on the page to
          bound at all. What stands in for it is not another height assertion: it is the `<= 250`
          over the first data row, which is the page-level consequence the 160px was a proxy for.
          So this `max-h-64` and its scroller are the ONLY thing bounding a forty-field type's
          panel, and nothing measures that height in a real browser — the next person to widen
          this panel has a class to read and no test to fail. */}
      <ul className="mt-1 flex max-h-64 flex-col gap-y-1 overflow-y-auto pr-1">
        {orderedKeys.map((key, index) => {
          const field = fieldsByKey[key];
          if (!field) return null;
          const visible = columnVisibility[key] !== false;
          return (
            <li key={key} className="flex items-center gap-1.5 text-sm">
              <label className="inline-flex items-center gap-1.5">
                <input
                  className="accent-accent"
                  type="checkbox"
                  checked={visible}
                  onChange={() =>
                    onChangeVisibility({ ...columnVisibility, [key]: !visible })
                  }
                />
                {field.name}
              </label>
              <Button
                type="button"
                variant="quiet"
                className="px-1.5 py-0.5 text-sm"
                aria-label={`Move ${field.name} up`}
                disabled={index === 0}
                onClick={() => onChangeOrder(moveColumn(orderedKeys, key, "up"))}
              >
                ↑
              </Button>
              <Button
                type="button"
                variant="quiet"
                className="px-1.5 py-0.5 text-sm"
                aria-label={`Move ${field.name} down`}
                disabled={index === orderedKeys.length - 1}
                onClick={() => onChangeOrder(moveColumn(orderedKeys, key, "down"))}
              >
                ↓
              </Button>
            </li>
          );
        })}
      </ul>
    </fieldset>
  );
}
