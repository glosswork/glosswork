/**
 * What one filter chip opens: field, operator, value, and the "Exclude matches" switch
 * (docs/DESIGN.md 7.4).
 *
 * **The commit boundary is explicit, and there is no debounce anywhere.** Once
 * "complete" is what gates the query, a text value typed character by character is complete at
 * the first character, and `Alpha` would be five queries. So a draft lives here, in the panel,
 * and reaches the chip row on exactly three events:
 *
 * - **Apply**, the button below;
 * - **Enter**, anywhere in the panel;
 * - **close** — the popover dismissing for any reason, which is this component unmounting.
 *
 * with one exception the answer names: a control that **cannot be half-typed** commits as soon
 * as it changes. That is `isTypedValueWidget` below, and it is a property of the widget
 * `ValueInput` renders rather than a list kept here: a `<select>`, a checkbox group and an
 * operator that takes no value at all are all finished the moment they change, and a text or
 * number box is not.
 *
 * **Escape commits too, and that is a choice rather than an oversight.** The rule is "on
 * close", and Escape is a close (`Popover.tsx`). One rule with no hidden cancel path is easier to
 * predict than two, and nothing is destroyed by it: the chip is still one click from being
 * edited or removed. Recorded because the opposite convention is common enough that a reader
 * will wonder.
 *
 * **The gate is here as well as in `FilterBuilder`, and it is the same rule.** An incomplete
 * draft never reaches `onCommit`, so it never reaches the chip row, the filter state, or the
 * network. `isConditionComplete` is imported from `filters/completeness.ts` — the module
 * `FilterBuilder`'s own gate calls and the composer tripwire test judges by — so this screen's
 * two filter surfaces cannot come to different conclusions about what is sendable. The two gates
 * are separately mutable and are separately measured; `TableView.incompleteFilter.test.tsx`'s
 * header says which of its cases measures which.
 *
 * **The incomplete state is shown here, not in the chip row** (7.4): `Apply` is disabled, never
 * hidden (DD-42), and the line beneath it says what is missing.
 */
import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

import type { ChipCondition } from "../filters/chipFilter";
import { isConditionComplete, valueCardinality } from "../filters/completeness";
import type { FilterableField } from "../filters/filterableFields";
import { ValueInput } from "../filters/ValueInput";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { btnSmClass, compactSelectClass, fieldLabelClass } from "../ui/classes";
import { operatorWord } from "../ui/vocabulary";

/**
 * Can this condition's value be half-typed?
 *
 * The answer decides whether a change commits immediately ("a select or a boolean, which cannot
 * be half-typed, commits on change") and it is read off the widget `ValueInput` will
 * render, branch for branch: no widget at all for a `NO_VALUE_OPS` operator; a `<select>` or a
 * checkbox group for a select field, a boolean and a `user_ref`; a text or number box for
 * everything else, including the comma-separated list a `relation` gets for `linked_to_any` and
 * the two boxes of a `between`.
 *
 * Not exported: it is measured through the behaviour it produces — typing commits nothing until
 * Apply, choosing commits at once — rather than by a unit test of its own, which would assert the
 * table and not the rule.
 */
function isTypedValueWidget(field: FilterableField, op: string): boolean {
  const cardinality = valueCardinality(op);
  if (cardinality === "none") return false;
  if (field.type === "single_select" || field.type === "multi_select") return false;
  if (cardinality !== "one") return true;
  return field.type !== "boolean" && field.type !== "user_ref";
}

function sameChip(a: ChipCondition, b: ChipCondition): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * The one commit path: complete, changed, and then out. Written as a module function over its
 * refs rather than a closure, so the unmount cleanup and the three event handlers below run
 * **the same code** — "commits on close" and "commits on Apply" differing by a line is exactly
 * the kind of drift that ships one of them broken.
 */
function commitDraft(
  draft: ChipCondition,
  committed: RefObject<ChipCondition>,
  onCommit: RefObject<(chip: ChipCondition) => void>,
): void {
  if (!isConditionComplete(draft.condition)) return;
  if (sameChip(draft, committed.current)) return;
  committed.current = draft;
  onCommit.current(draft);
}

export interface ConditionPopoverProps {
  /** Every field the filter grammar can address here, user fields and pseudo-fields alike. */
  fields: FilterableField[];
  /** The chip as it stands — or, from `+ Add filter`, an empty condition to fill in. */
  initial: ChipCondition;
  /** Called with a **complete** condition, and never with anything else. */
  onCommit: (chip: ChipCondition) => void;
  /** `Popover`'s own dismiss, handed down by `Chip`. */
  close: () => void;
  /** The server's refusal of this chip's value, shown against the chip that carries it. */
  error?: string | null;
}

export function ConditionPopover({
  fields,
  initial,
  onCommit,
  close,
  error = null,
}: ConditionPopoverProps) {
  const [draft, setDraft] = useState<ChipCondition>(initial);
  /** The draft as of the last change. A ref as well as state because the unmount cleanup — the
   * "commits on close" half of the commit rule — runs after the last render and can only read a
   * ref. */
  const draftRef = useRef<ChipCondition>(initial);
  /** What has already gone out, so Apply-then-unmount does not commit the same edit twice —
   * which, from `+ Add filter`, would add the same chip twice. */
  const committedRef = useRef<ChipCondition>(initial);
  /** The live `onCommit`. The cleanup below runs once, at unmount, and a handler captured at
   * mount would close over the chip row as it was then. */
  const onCommitRef = useRef(onCommit);
  useEffect(() => {
    onCommitRef.current = onCommit;
  });

  useEffect(() => {
    // "Commits on close": the panel is unmounted rather than hidden (`Popover.tsx`), so closing
    // it — by Apply, by Escape, by a click outside, or by the chip disappearing — lands here.
    return () => commitDraft(draftRef.current, committedRef, onCommitRef);
  }, []);

  const field = fields.find((candidate) => candidate.key === draft.condition.field);
  const operators = field?.operators ?? [];
  const complete = isConditionComplete(draft.condition);

  const change = (next: ChipCondition, commitNow: boolean) => {
    draftRef.current = next;
    setDraft(next);
    if (commitNow) commitDraft(next, committedRef, onCommitRef);
  };

  const apply = () => {
    commitDraft(draftRef.current, committedRef, onCommitRef);
    close();
  };

  const handleField = (key: string) => {
    const nextField = fields.find((candidate) => candidate.key === key);
    const op = nextField?.operators[0] ?? "";
    const next: ChipCondition = {
      condition: { field: key, op, value: undefined },
      negated: draft.negated,
    };
    // A field is chosen, not typed, so the rule above applies — and the new condition is only
    // ever complete when its first operator takes no value (`Name is blank`).
    change(next, true);
  };

  const handleOperator = (op: string) => {
    change({ ...draft, condition: { ...draft.condition, op, value: undefined } }, true);
  };

  const handleValue = (value: unknown) => {
    const commitNow = field !== undefined && !isTypedValueWidget(field, draft.condition.op);
    change({ ...draft, condition: { ...draft.condition, value } }, commitNow);
  };

  const handleNegated = (negated: boolean) => {
    change({ ...draft, negated }, true);
  };

  return (
    <div
      className="flex w-64 flex-col gap-2"
      data-testid="condition-popover"
      onKeyDown={(event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        apply();
      }}
    >
      {error !== null && (
        <p className="rounded-ctl bg-bad-soft px-2 py-1 text-xs text-bad" role="alert">
          {error}
        </p>
      )}

      <label className={fieldLabelClass}>
        Field
        <select
          className={compactSelectClass + " mt-1 w-full"}
          aria-label="Field"
          value={draft.condition.field}
          onChange={(event) => handleField(event.target.value)}
        >
          <option value="" disabled>
            Select field
          </option>
          {fields.map((candidate) => (
            <option key={candidate.key} value={candidate.key}>
              {candidate.name}
            </option>
          ))}
        </select>
      </label>

      <label className={fieldLabelClass}>
        Operator
        <select
          className={compactSelectClass + " mt-1 w-full"}
          aria-label="Operator"
          value={draft.condition.op}
          disabled={field === undefined}
          onChange={(event) => handleOperator(event.target.value)}
        >
          {/* The display vocabulary, at the one call site that has a field type to give it:
              `lt` reads "is before" on a date and "is less than" on a number. The
              option's VALUE stays the API's own name, so nothing downstream reads a word. */}
          {operators.map((op) => (
            <option key={op} value={op}>
              {operatorWord(op, field?.type)}
            </option>
          ))}
        </select>
      </label>

      {field !== undefined && (
        <ValueInput
          field={field}
          op={draft.condition.op}
          value={draft.condition.value}
          onChange={handleValue}
        />
      )}

      {/* docs/DESIGN.md 7.4's words, not ours. `role="switch"` is what 7.4's "switch" means in
          the accessibility tree; the control underneath is a checkbox, which is the element that
          already carries a checked state and keyboard behaviour without a second primitive. */}
      <Checkbox
        role="switch"
        checked={draft.negated}
        onChange={(event) => handleNegated(event.target.checked)}
      >
        Exclude matches
      </Checkbox>

      <div className="flex items-center gap-2">
        <Button type="button" variant="primary" className={btnSmClass} disabled={!complete} onClick={apply}>
          Apply
        </Button>
        {!complete && (
          <p className="text-xs text-ink-2">{incompleteHint(draft.condition.op)}</p>
        )}
      </div>
    </div>
  );
}

/** What is missing, in docs/DESIGN.md 5's voice: what went wrong and what to do, no apology. */
function incompleteHint(op: string): string {
  switch (valueCardinality(op)) {
    case "pair":
      return "Fill in both ends of the range.";
    case "list":
      return "Choose at least one value.";
    default:
      return "Add a value to run this filter.";
  }
}
