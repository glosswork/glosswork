/**
 * The table page's filter, as docs/DESIGN.md 7.4 describes it: a row of sentence chips.
 *
 * `Stage is not Lost ×`, `+ Add filter`, and `Advanced`. The chips are a **flat AND**, and the
 * boolean grammar is not dropped but moved one click away: the `Advanced` chip opens the tree
 * builder that already exists, and a filter that is not a flat AND renders as a single
 * `Advanced filter` chip that opens the same builder (such a filter can already be sitting in a
 * saved view, so it arrives by loading one, not only by building one).
 *
 * **This is the table page's layer, and `/search` does not get it.** Both screens share
 * `FilterBuilder`, which is where the completeness gate lives so both inherit it; chips are what
 * wraps it here. `SearchPage.tsx` keeps the inline tree.
 *
 * **The `Advanced` chip never unmounts, and that is load-bearing rather than tidy.** Building a
 * nested tree inside it passes through states that *are* chip-expressible (`{"and": [one
 * condition]}`) and states that are not, so a row that rendered the advanced chip only in one of
 * those two shapes would unmount the builder mid-edit and take its tree with it. It is always the
 * last slot; only its word and its `×` change.
 *
 * **What the error is doing here.** Completeness is not acceptance: `filters.py`
 * rejects *complete* values too — a `date` that is neither ISO nor a token, an unresolvable
 * `user_ref`, a relation key naming no record — so a chip can be finished, sent, and refused.
 * That refusal is shown **in the popover of the chip that carries the offending field**, named by
 * `error.details.field_key`, instead of the page-level "Could not load records" alert that empties
 * the table. `TableView` keeps the previous rows on screen for the same reason: the person's last
 * good answer is still the truest thing the page knows.
 *
 * **Geometry is unproven here.** 7.4's 28px chip and the row's wrapping at 640px
 * (docs/DESIGN.md 9) live in class lists; `getBoundingClientRect` returns zeroes under jsdom
 * (AGENTS.md, Traps), so no test in this directory claims to measure either. Playwright gets
 * them when it walks the toolbar.
 */
import { useMemo, useState } from "react";

import type { FieldDoc, SystemFieldDoc } from "../api/objectTypes";
import {
  chipSentence,
  fromChipConditions,
  toChipConditions,
  type ChipCondition,
} from "../filters/chipFilter";
import { FilterBuilder } from "../filters/FilterBuilder";
import { toFilterableFields } from "../filters/filterableFields";
import type { FilterNode } from "../filters/types";
import { Chip } from "../ui/Chip";
import { ConditionPopover } from "./ConditionPopover";

/** A refused value's payload: the server's message, and the field it named. */
export interface FilterChipError {
  message: string;
  fieldKey: string | null;
}

export interface FilterChipRowProps {
  fields: FieldDoc[];
  systemFields?: SystemFieldDoc[];
  /** The committed filter. This component holds no filter state of its own: a chip row is a
   * rendering of the tree, and every edit goes straight back out through `onChange`. */
  filter: FilterNode | null;
  onChange: (next: FilterNode | null) => void;
  /** Bumped to remount the tree builder when a saved view is applied, as `TableView` already did. */
  builderKey?: number;
  error?: FilterChipError | null;
}

export function FilterChipRow({
  fields,
  systemFields = [],
  filter,
  onChange,
  builderKey,
  error = null,
}: FilterChipRowProps) {
  const filterableFields = useMemo(
    () => toFilterableFields(fields, systemFields),
    [fields, systemFields],
  );
  const chips = toChipConditions(filter);

  const emptyChip = (): ChipCondition => {
    const first = filterableFields[0];
    return {
      condition: { field: first?.key ?? "", op: first?.operators[0] ?? "", value: undefined },
      negated: false,
    };
  };

  const replaceAt = (index: number, next: ChipCondition) => {
    if (chips === null) return;
    onChange(fromChipConditions(chips.map((chip, at) => (at === index ? next : chip))));
  };

  const removeAt = (index: number) => {
    if (chips === null) return;
    onChange(fromChipConditions(chips.filter((_, at) => at !== index)));
  };

  const append = (next: ChipCondition) => {
    onChange(fromChipConditions([...(chips ?? []), next]));
  };

  /**
   * Where `+ Add filter`'s chip landed, while its popover is still open.
   *
   * One popover can commit more than once — a select commits on change and the panel commits
   * again on close — so an `+ Add filter` that always appended would add a second chip
   * for the same condition the moment the person changed their mind about the value. The first
   * commit appends and remembers where; the rest replace it. Reset when the popover **opens**
   * rather than when it closes, because the close is itself a commit point and would otherwise
   * find the index already cleared.
   *
   * State rather than a ref because the render reads it: a value refused by the server
   * belongs in whichever popover is open, and the one open at that moment is usually this one —
   * the chip was added from here a few hundred milliseconds ago and the person has not moved.
   */
  const [addedIndex, setAddedIndex] = useState<number | null>(null);
  const commitFromAddChip = (next: ChipCondition) => {
    if (addedIndex !== null) {
      replaceAt(addedIndex, next);
      return;
    }
    setAddedIndex((chips ?? []).length);
    append(next);
  };

  const fieldFor = (key: string) => filterableFields.find((candidate) => candidate.key === key);

  /** Which chip the server's refusal belongs to. `-1` is "none of them": either there is no
   * error, or it named a field no chip carries and `TableView` kept the page-level alert. */
  const erroredIndex =
    error === null || chips === null
      ? -1
      : chips.findIndex((chip) => chip.condition.field === error.fieldKey);

  /** The same message again, in `+ Add filter`'s own popover, when the chip it just added is the
   * one the server refused — which is the common case, because that popover is still open. */
  const addedChipError =
    error !== null && addedIndex !== null && addedIndex === erroredIndex ? error.message : null;

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="filter-chips">
      {chips !== null
        && chips.map((chip, index) => {
          const sentence = chipSentence(fieldFor(chip.condition.field), chip);
          const chipError = error !== null && index === erroredIndex ? error.message : null;
          return (
            <Chip
              key={index}
              data-testid={`filter-chip-${index}`}
              className={chipError !== null ? "border-bad text-bad" : undefined}
              // The chip's own sentence is its name until it carries an error, when the name
              // says so too: kind is never colour alone (docs/DESIGN.md 10), and the message
              // itself lives one click away in the popover.
              label={chipError !== null ? `${sentence} — ${chipError}` : undefined}
              removeLabel={`Remove filter: ${sentence}`}
              onRemove={() => removeAt(index)}
              popover={(close) => (
                <ConditionPopover
                  fields={filterableFields}
                  initial={chip}
                  error={chipError}
                  onCommit={(next) => replaceAt(index, next)}
                  close={close}
                />
              )}
            >
              {sentence}
            </Chip>
          );
        })}

      {chips !== null && (
        <Chip
          variant="add"
          data-testid="filter-chip-add"
          onOpenChange={(open) => {
            if (open) setAddedIndex(null);
          }}
          popover={(close) => (
            <ConditionPopover
              fields={filterableFields}
              initial={emptyChip()}
              error={addedChipError}
              onCommit={commitFromAddChip}
              close={close}
            />
          )}
        >
          + Add filter
        </Chip>
      )}

      {/* Always the last slot, in both shapes — see the header. */}
      <Chip
        data-testid="filter-chip-advanced"
        className={chips === null && error !== null ? "border-bad text-bad" : undefined}
        onRemove={chips === null ? () => onChange(null) : undefined}
        removeLabel="Remove advanced filter"
        popover={
          <div className="w-[26rem] max-w-[80vw]">
            {chips === null && error !== null && (
              <p className="mb-2 rounded-ctl bg-bad-soft px-2 py-1 text-xs text-bad" role="alert">
                {error.message}
              </p>
            )}
            <FilterBuilder
              key={builderKey}
              fields={fields}
              systemFields={systemFields}
              initialFilter={filter}
              onChange={onChange}
            />
          </div>
        }
      >
        {chips === null ? "Advanced filter" : "Advanced"}
      </Chip>
    </div>
  );
}
