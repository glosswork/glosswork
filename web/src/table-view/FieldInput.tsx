/**
 * The edit widget for one field, shared by the table cell (`EditableCell`) and the record detail
 * view (`FieldList`).
 *
 * Widget choice and value coercion stay in `fieldWidgets.ts`'s pure functions; this component
 * only renders the control and reports draft changes, commits, and cancels. Extracted so the two
 * surfaces share one set of widgets rather than drifting apart — the table passes its compact
 * cell classes, the detail card passes its own.
 */
import type { KeyboardEvent } from "react";
import type { FieldDoc } from "../api/objectTypes";
import { usePrincipalDirectory } from "../hooks/usePrincipalDirectory";
import { widgetKindFor, type EditDraft } from "./fieldWidgets";

export interface FieldInputProps {
  field: FieldDoc;
  draft: EditDraft;
  onDraftChange: (draft: EditDraft) => void;
  onCommit: () => void;
  onCancel: () => void;
  /** The control's accessible name; each surface phrases it for its own context. */
  label: string;
  inputClassName: string;
  selectClassName: string;
  textareaClassName: string;
  autoFocus?: boolean;
  /** The control's `id`, so a surface with a visible `<label htmlFor>` can associate the
   * two (the new-record form). The table cell and the record card name their controls
   * with `label` alone and pass nothing here. */
  id?: string;
  /** Commit when the control loses focus. True for the table cell, where blur is the only way
   * out of an editor with no buttons; false for the record detail card, which has explicit Save
   * and Cancel buttons that blur would otherwise pre-empt. */
  commitOnBlur?: boolean;
}

export function FieldInput({
  field,
  draft,
  onDraftChange,
  onCommit,
  onCancel,
  label,
  inputClassName,
  selectClassName,
  textareaClassName,
  autoFocus = true,
  commitOnBlur = true,
  id,
}: FieldInputProps) {
  const onBlur = commitOnBlur ? onCommit : undefined;
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    // Enter commits everywhere but a textarea, where it is a newline the user meant to type.
    if (event.key === "Enter" && field.type !== "long_text") onCommit();
    if (event.key === "Escape") onCancel();
  };

  const widget = widgetKindFor(field);

  if (widget === "boolean") {
    return (
      <input
        className="accent-accent"
        type="checkbox"
        id={id}
        aria-label={label}
        autoFocus={autoFocus}
        checked={draft === true}
        onChange={(event) => onDraftChange(event.target.checked)}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
      />
    );
  }

  if (widget === "select") {
    return (
      <select
        className={selectClassName}
        id={id}
        aria-label={label}
        autoFocus={autoFocus}
        value={typeof draft === "string" ? draft : ""}
        onChange={(event) => onDraftChange(event.target.value)}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
      >
        <option value="">{"—"}</option>
        {field.options?.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  if (widget === "user_ref") {
    return (
      <UserRefSelect
        className={selectClassName}
        label={label}
        id={id}
        autoFocus={autoFocus}
        draft={draft}
        onDraftChange={onDraftChange}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
      />
    );
  }

  if (widget === "multi_select") {
    const selected = Array.isArray(draft) ? draft : [];
    return (
      <select
        className={selectClassName}
        id={id}
        aria-label={label}
        autoFocus={autoFocus}
        multiple
        value={selected}
        onChange={(event) =>
          onDraftChange(Array.from(event.target.selectedOptions, (option) => option.value))
        }
        onBlur={onBlur}
        onKeyDown={onKeyDown}
      >
        {field.options?.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  if (widget === "textarea") {
    return (
      <textarea
        className={textareaClassName}
        id={id}
        aria-label={label}
        autoFocus={autoFocus}
        value={typeof draft === "string" ? draft : ""}
        onChange={(event) => onDraftChange(event.target.value)}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
      />
    );
  }

  return (
    <input
      className={inputClassName}
      type={
        widget === "number"
          ? "number"
          : widget === "date"
            ? "date"
            : widget === "datetime"
              ? "datetime-local"
              : "text"
      }
      id={id}
      aria-label={label}
      autoFocus={autoFocus}
      value={typeof draft === "string" ? draft : ""}
      onChange={(event) => onDraftChange(event.target.value)}
      // `onBlur`, not `onCommit`. Hardcoding `onCommit` here would ignore `commitOnBlur` while
      // the other five branches honour it — and it is the branch serving `short_text`, `url`,
      // `integer`, `decimal`, `date` and `datetime`, so the prop would be inoperative for most of
      // the field types a person types into. That happened once: `DetailsCard` asked for
      // `commitOnBlur={false}` and got blur-commits; the create dialog also needs it to mean
      // what it says.
      onBlur={onBlur}
      onKeyDown={onKeyDown}
    />
  );
}

interface UserRefSelectProps {
  className: string;
  label: string;
  id: string | undefined;
  autoFocus: boolean;
  draft: EditDraft;
  onDraftChange: (draft: EditDraft) => void;
  onBlur: (() => void) | undefined;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
}

/**
 * The `user_ref` widget: a combobox over `GET /api/v1/principals/directory`,
 * submitting the selected principal's **id** — never a name — so `parseEditedValue` needs no
 * `user_ref` case of its own (fieldWidgets.ts). `usePrincipalDirectory` is the shared hook that
 * makes many of these on one screen cost one request rather than one each.
 *
 * A separate component, not an inline branch in `FieldInput`, because it is the only widget that
 * needs a hook — keeping the hook call in its own component means a screen with a hundred
 * non-`user_ref` cells never mounts it at all.
 */
function UserRefSelect({
  className,
  label,
  id,
  autoFocus,
  draft,
  onDraftChange,
  onBlur,
  onKeyDown,
}: UserRefSelectProps) {
  const directoryQuery = usePrincipalDirectory();
  const entries = directoryQuery.data ?? [];
  return (
    <select
      className={className}
      id={id}
      aria-label={label}
      autoFocus={autoFocus}
      value={typeof draft === "string" ? draft : ""}
      onChange={(event) => onDraftChange(event.target.value)}
      onBlur={onBlur}
      onKeyDown={onKeyDown}
    >
      <option value="">{"—"}</option>
      {entries.map((entry) => (
        <option key={entry.id} value={entry.id}>
          {entry.email ? `${entry.display_name} (${entry.email})` : entry.display_name}
        </option>
      ))}
    </select>
  );
}
