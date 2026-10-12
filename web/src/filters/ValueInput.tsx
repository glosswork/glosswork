/**
 * Renders the value editor(s) for one condition row.
 *
 * `field.type` selects an input *widget* here (text box vs. number box vs. checkbox group vs.
 * two-sided range) — that is the one place branching on `type` is allowed.
 * `op` separately selects the value's *cardinality* (no value, one value, two values, or
 * a list of values) — that is a property of the operator's semantics (`between` always takes a
 * pair, `in`/`has_any`/etc. always take a list), not a field-type-to-operator legality table, and
 * it is exactly the shape docs/MCP_TOOLS.md section 4 calls for. Neither branch ever decides
 * which operators are *legal* for a field; that list is never computed here, only consumed.
 */
import { useEffect, useId, useRef, useState } from "react";

import type { FilterableField } from "./filterableFields";
import { usePrincipalDirectory } from "../hooks/usePrincipalDirectory";
import { compactInputClass, compactSelectClass } from "../ui/classes";
import { cx } from "../ui/cx";
import { isCalendarDay } from "../ui/datetime";
import {
  commitDateFilterText,
  dateFilterHint,
  dateFilterText,
  type DateFilterProblem,
  type RangeBound,
} from "./dateFilterValue";
import { commaListText, sameCommaList, splitCommaList } from "./commaList";
import { LIST_OPS, NO_VALUE_OPS } from "./completeness";
import { useTypedText } from "./useTypedText";

/**
 * Operators that take no value at all, and operators whose value is a list — both read from
 * `completeness.ts`, which mirrors `src/glosswork/filters.py`'s `NO_VALUE_OPS` and `LIST_OPS`.
 *
 * **They are imported, not declared here, because a copy drifts**: a local list once held five
 * of the six list operators and omitted `linked_to_any`, so a `relation` field filtered with
 * `linked_to_any` got a single text box and sent a string where the API requires a non-empty
 * array. One import is what stops that happening again.
 */
const UNARY_OPERATORS = NO_VALUE_OPS;
const LIST_VALUE_OPERATORS = LIST_OPS;

export interface ValueInputProps {
  field: FilterableField;
  op: string;
  value: unknown;
  onChange: (value: unknown) => void;
}

export function ValueInput({ field, op, value, onChange }: ValueInputProps) {
  if (UNARY_OPERATORS.has(op)) {
    return null;
  }

  if (op === "between") {
    const [lo, hi] = Array.isArray(value) ? value : [undefined, undefined];
    if (isDateField(field)) {
      // Each end knows which end it is, because a day means its first second at the lower end
      // and its last at the upper. One hint line for the pair, under both boxes.
      return (
        <span className="inline-flex flex-col gap-1">
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <DateValueInput
              field={field}
              op={op}
              bound="lower"
              value={lo}
              onChange={(next) => onChange([next, hi])}
              ariaLabel={`${field.name} lower bound`}
              quietHint
            />
            <DateValueInput
              field={field}
              op={op}
              bound="upper"
              value={hi}
              onChange={(next) => onChange([lo, next])}
              ariaLabel={`${field.name} upper bound`}
              quietHint
            />
          </span>
          <DateHint problem={null} />
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1.5">
        <SingleValueInput
          field={field}
          value={lo}
          onChange={(next) => onChange([next, hi])}
          ariaLabel={`${field.name} lower bound`}
        />
        <SingleValueInput
          field={field}
          value={hi}
          onChange={(next) => onChange([lo, next])}
          ariaLabel={`${field.name} upper bound`}
        />
      </span>
    );
  }

  if (field.type === "single_select" || field.type === "multi_select") {
    return (
      <SelectValueInput
        field={field}
        multiple={field.type === "multi_select" || LIST_VALUE_OPERATORS.has(op)}
        value={value}
        onChange={onChange}
      />
    );
  }

  if (field.type === "relation") {
    // **The relation branch asks the operator for its cardinality first.** `linked_to_any` is a
    // `LIST_OPS` member and it occurs on no
    // other field type, so a relation field is the only place a list operator can reach this
    // branch — and this branch returns before the general list check below is ever evaluated.
    // That is why simply adding `linked_to_any` to the list set would be *inert*: the check
    // has to be here, in the branch that answers first.
    if (LIST_VALUE_OPERATORS.has(op)) {
      return (
        <CommaListInput
          value={value}
          onChange={onChange}
          ariaLabel={`${field.name} value`}
          placeholder="comma-separated record keys"
        />
      );
    }
    return (
      <input
        className={compactInputClass + " w-36"}
        type="text"
        aria-label={`${field.name} value`}
        value={typeof value === "string" ? value : ""}
        placeholder="record key"
        onChange={(event) => onChange(event.target.value)}
      />
    );
  }

  if (LIST_VALUE_OPERATORS.has(op)) {
    return (
      <CommaListInput
        value={value}
        onChange={onChange}
        ariaLabel={`${field.name} value`}
        placeholder="comma-separated values"
      />
    );
  }

  if (isDateField(field)) {
    return (
      <DateValueInput
        field={field}
        op={op}
        bound={null}
        value={value}
        onChange={onChange}
        ariaLabel={`${field.name} value`}
      />
    );
  }

  return (
    <SingleValueInput
      field={field}
      value={value}
      onChange={onChange}
      ariaLabel={`${field.name} value`}
    />
  );
}

/** The two field types whose filter value is a date, and so get `DateValueInput`. */
function isDateField(field: FilterableField): boolean {
  return field.type === "date" || field.type === "datetime";
}

interface CommaListInputProps {
  value: unknown;
  onChange: (value: unknown) => void;
  ariaLabel: string;
  placeholder: string;
}

/**
 * The widget for a `LIST_OPS` value: a comma-separated free-text field whose committed value is
 * always an array, never the raw string. One component rather than two so the relation branch and
 * the general branch cannot answer the same operator differently.
 *
 * **The box holds what was typed; the array is committed beside it** (`useTypedText`). Drawn
 * from the array, the box read `["v1"]` back as `v1` the moment a comma was typed, so the comma
 * never survived to the next key and `v1, v2` arrived as the single value `v1v2`.
 *
 * A value that itself contains a comma cannot be entered here.
 */
function CommaListInput({ value, onChange, ariaLabel, placeholder }: CommaListInputProps) {
  const [text, setText] = useTypedText<unknown>({
    committed: value,
    textFor: commaListText,
    commitFor: splitCommaList,
    same: sameCommaList,
  });
  return (
    <input
      className={compactInputClass + " w-44"}
      type="text"
      aria-label={ariaLabel}
      value={text}
      placeholder={placeholder}
      onChange={(event) => {
        setText(event.target.value);
        onChange(splitCommaList(event.target.value));
      }}
    />
  );
}

interface SingleValueInputProps {
  field: FilterableField;
  value: unknown;
  onChange: (value: unknown) => void;
  ariaLabel: string;
}

interface DateValueInputProps {
  field: FilterableField;
  op: string;
  /** Which end of a `between` this box is; `null` for an operator that takes one value. */
  bound: RangeBound;
  value: unknown;
  onChange: (value: unknown) => void;
  ariaLabel: string;
  /** Show the line only when it is a refusal. For a pair, whose caller prints the hint once. */
  quietHint?: boolean;
}

function sameDateValue(a: unknown, b: unknown): boolean {
  return (a ?? undefined) === (b ?? undefined);
}

/**
 * The widget for a `date` or `datetime` filter value: a text box, a calendar button, and a line
 * saying what the box accepts.
 *
 * **It is a text box because relative dates have to stay typeable.** `@today-7d` is resolved by
 * the server, and a date picker alone would make it unreachable. The calendar button beside the
 * box is the browser's own date input, shrunk to its picker, and it writes the picked day into
 * the box as text.
 *
 * **The box holds what was typed; the value is committed beside it** (`useTypedText`). What the
 * box commits is decided by `dateFilterValue.ts`: a token as typed, a real day, a canonical
 * instant, and for everything else nothing at all, with the line below turning into the reason.
 * A box drawn from the committed value would empty itself on the first key, since `2` commits
 * nothing.
 *
 * **A day on a `datetime` field is an instant that depends on the operator**, so when the
 * operator changes the instant is derived again from the day still in the box. Both callers
 * reset the value on an operator change (`ConditionPopover`, `ConditionRow`); this component
 * answers by committing again, and holds its text meanwhile.
 */
function DateValueInput({
  field,
  op,
  bound,
  value,
  onChange,
  ariaLabel,
  quietHint = false,
}: DateValueInputProps) {
  const [seenOp, setSeenOp] = useState(op);
  const opChanged = op !== seenOp;
  if (opChanged) setSeenOp(op);

  const [text, setText] = useTypedText<unknown>({
    committed: value,
    textFor: (committed) => dateFilterText(field.type, op, bound, committed),
    commitFor: (typed) => commitDateFilterText(field.type, op, bound, typed).value,
    same: sameDateValue,
    hold: opChanged,
  });
  const { value: commits, problem } = commitDateFilterText(field.type, op, bound, text);

  /** The live `onChange` and text, for the effect below, which must run on an operator change
   * alone and not on every key. */
  const latest = useRef({ onChange, commits, text });
  useEffect(() => {
    latest.current = { onChange, commits, text };
  });
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    // The operator changed and the caller reset the value: derive it again from the typed day.
    if (latest.current.text.trim() !== "") latest.current.onChange(latest.current.commits);
  }, [op]);

  const type = (next: string) => {
    setText(next);
    onChange(commitDateFilterText(field.type, op, bound, next).value);
  };

  const typedDay = text.trim();
  const hintId = useId();
  const showsHint = !quietHint || problem !== null;
  return (
    <span className="inline-flex flex-col gap-1">
      <span className="inline-flex items-center gap-1">
        <input
          className={compactInputClass + " w-36"}
          type="text"
          aria-label={ariaLabel}
          aria-invalid={problem !== null}
          aria-describedby={showsHint ? hintId : undefined}
          value={text}
          onChange={(event) => type(event.target.value)}
        />
        {/* The browser's own date input, showing only its picker button: the day's text fields
            are hidden, so it reads as a calendar button and cannot be half-typed. Picking a day
            writes it into the box as `YYYY-MM-DD`. */}
        <input
          className={
            "h-7 w-7 cursor-pointer rounded-ctl border border-line-2 bg-surface p-0 text-ink "
            + "[&::-webkit-calendar-picker-indicator]:m-0 [&::-webkit-calendar-picker-indicator]:h-full "
            + "[&::-webkit-calendar-picker-indicator]:w-full [&::-webkit-calendar-picker-indicator]:cursor-pointer "
            + "[&::-webkit-calendar-picker-indicator]:p-1.5 [&::-webkit-datetime-edit]:hidden"
          }
          type="date"
          aria-label={`${ariaLabel}: pick a day`}
          data-testid="date-value-picker"
          value={isCalendarDay(typedDay) ? typedDay : ""}
          onChange={(event) => type(event.target.value)}
        />
      </span>
      {showsHint && <DateHint id={hintId} problem={problem} />}
    </span>
  );
}

/**
 * The line under a date box: what it accepts, or why what was typed is not being sent.
 *
 * Tied to its box by `aria-describedby` rather than announced as a `status` region: the table
 * page already has one of those, its footer count, and a second would make that one ambiguous.
 * A refusal is never colour alone (docs/DESIGN.md 10): the sentence itself changes.
 */
function DateHint({ id, problem }: { id?: string; problem: DateFilterProblem | null }) {
  return (
    <span
      id={id}
      className={cx("max-w-64 text-xs", problem === null ? "text-ink-2" : "text-bad")}
      data-testid="date-value-hint"
      data-refusal={problem ?? undefined}
    >
      {dateFilterHint(problem)}
    </span>
  );
}

/**
 * The widget for exactly one scalar value. Free-text fields (`short_text`, `long_text`, `url`,
 * and anything else not special-cased below) get a plain text input. `date` and `datetime` do
 * not reach this component: `DateValueInput` above is theirs.
 *
 * `user_ref` is not on that list, although `@me` must stay reachable (FR-R8/FR-R9). It gets
 * `FieldInput`'s directory picker,
 * with `@me` pinned above the directory rather than left to be typed: the resolver
 * (`resolve_principal_ref`) accepts an id, an email, an exact display name, or `@me` on a filter
 * exactly as on a write, so every reference form the server understands is reachable by choosing
 * from the list, and `@me` — the one form that names no specific person — is pinned rather than
 * searched for.
 */
function SingleValueInput({ field, value, onChange, ariaLabel }: SingleValueInputProps) {
  if (field.type === "user_ref") {
    return <UserRefValueInput value={value} onChange={onChange} ariaLabel={ariaLabel} />;
  }
  if (field.type === "boolean") {
    const current = value === true ? "true" : value === false ? "false" : "";
    return (
      <select
        className={compactSelectClass}
        aria-label={ariaLabel}
        value={current}
        onChange={(event) =>
          onChange(event.target.value === "" ? undefined : event.target.value === "true")
        }
      >
        <option value="">--</option>
        <option value="true">True</option>
        <option value="false">False</option>
      </select>
    );
  }

  if (field.type === "integer" || field.type === "decimal") {
    return (
      <input
        className={compactInputClass + " w-28"}
        type="number"
        aria-label={ariaLabel}
        value={typeof value === "number" ? value : ""}
        onChange={(event) =>
          onChange(event.target.value === "" ? undefined : Number(event.target.value))
        }
      />
    );
  }

  return (
    <input
      className={compactInputClass + " w-36"}
      type="text"
      aria-label={ariaLabel}
      value={typeof value === "string" ? value : ""}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

interface UserRefValueInputProps {
  value: unknown;
  onChange: (value: unknown) => void;
  ariaLabel: string;
}

/**
 * The `user_ref` picker: `@me` pinned above `GET /api/v1/principals/directory`'s
 * entries, submitting whichever token the caller picked — `@me` unresolved, a directory entry's
 * id — for `resolve_principal_ref` to settle server-side, same as `FieldInput`'s widget.
 * `usePrincipalDirectory` shares its query key with every other picker on the page, so a
 * filter row costs no request beyond whatever a table cell already made.
 */
function UserRefValueInput({ value, onChange, ariaLabel }: UserRefValueInputProps) {
  const directoryQuery = usePrincipalDirectory();
  const entries = directoryQuery.data ?? [];
  return (
    <select
      className={compactSelectClass}
      aria-label={ariaLabel}
      value={typeof value === "string" ? value : ""}
      onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.value)}
    >
      <option value="">--</option>
      <option value="@me">@me</option>
      {entries.map((entry) => (
        <option key={entry.id} value={entry.id}>
          {entry.email ? `${entry.display_name} (${entry.email})` : entry.display_name}
        </option>
      ))}
    </select>
  );
}

interface SelectValueInputProps {
  field: FilterableField;
  multiple: boolean;
  value: unknown;
  onChange: (value: unknown) => void;
}

/** `single_select`/`multi_select` fields are always chosen from `field.options`, never typed. */
function SelectValueInput({ field, multiple, value, onChange }: SelectValueInputProps) {
  const options = field.options ?? [];

  if (!multiple) {
    return (
      <select
        className={compactSelectClass}
        aria-label={`${field.name} value`}
        value={typeof value === "string" ? value : ""}
        onChange={(event) => onChange(event.target.value === "" ? undefined : event.target.value)}
      >
        <option value="">--</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  const selected = new Set(Array.isArray(value) ? (value as string[]) : []);
  const toggle = (optionValue: string, checked: boolean) => {
    const next = new Set(selected);
    if (checked) {
      next.add(optionValue);
    } else {
      next.delete(optionValue);
    }
    onChange(Array.from(next));
  };

  return (
    <fieldset
      className="flex flex-wrap items-center gap-2 rounded-ctl border border-line px-2 py-1"
      aria-label={`${field.name} value`}
    >
      {options.map((option) => (
        <label key={option.value} className="inline-flex items-center gap-1 text-sm">
          <input
            className="accent-accent"
            type="checkbox"
            checked={selected.has(option.value)}
            onChange={(event) => toggle(option.value, event.target.checked)}
          />
          {option.label}
        </label>
      ))}
    </fieldset>
  );
}
