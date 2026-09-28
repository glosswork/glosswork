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
import type { FilterableField } from "./filterableFields";
import { usePrincipalDirectory } from "../hooks/usePrincipalDirectory";
import { compactInputClass, compactSelectClass } from "../ui/classes";
import { LIST_OPS, NO_VALUE_OPS } from "./completeness";

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

  return (
    <SingleValueInput
      field={field}
      value={value}
      onChange={onChange}
      ariaLabel={`${field.name} value`}
    />
  );
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
 */
function CommaListInput({ value, onChange, ariaLabel, placeholder }: CommaListInputProps) {
  const text = Array.isArray(value) ? value.join(",") : typeof value === "string" ? value : "";
  return (
    <input
      className={compactInputClass + " w-44"}
      type="text"
      aria-label={ariaLabel}
      value={text}
      placeholder={placeholder}
      onChange={(event) => onChange(splitCommaList(event.target.value))}
    />
  );
}

function splitCommaList(text: string): string[] {
  return text
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

interface SingleValueInputProps {
  field: FilterableField;
  value: unknown;
  onChange: (value: unknown) => void;
  ariaLabel: string;
}

/**
 * The widget for exactly one scalar value. Free-text fields (`short_text`, `long_text`, `url`,
 * `date`, `datetime`, and anything else not special-cased below) all get a plain text input on
 * purpose: `date`/`datetime` need to accept relative tokens (`@today`, `@today-7d`, ...), and the
 * server — not this component — resolves them. A date picker would make those untypeable.
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
