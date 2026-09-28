/**
 * One table cell: click-to-edit for every field type except `relation`/`attachment`, which are
 * view-only in the table (both are edited from the record detail view; an attachment cell shows
 * how many files the record holds). Widget choice and value
 * coercion are pure functions from `fieldWidgets.ts`; committing calls the table's
 * `onCellCommit` meta hook, whose actual `PATCH` call and 409 handling live in
 * `useInlineCellEdit.ts`. This component only owns its own open/closed and draft-value state;
 * the widgets themselves are `FieldInput`, shared with the record detail view.
 *
 * `long_text` is the one exception: an in-cell editor would be a column width wide no matter
 * what, so the click opens `LongTextCellDialog` in view mode instead of a cramped textarea. The
 * display stays one clipped line, so rows keep a uniform height. The exception is exactly that
 * one type: every other field type edits in place below.
 */
import { useState, type ReactNode } from "react";
import type { Table } from "@tanstack/react-table";
import type { LegacyFeatures } from "@tanstack/react-table/legacy";
import type { FieldDoc } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import { formatFieldValue, selectValueLabel } from "../record-detail/fieldDisplay";
import { PrincipalName } from "../principals/PrincipalName";
import { Pill } from "../ui/Pill";
import { formatTimestamp } from "../ui/datetime";
import { formatDate, formatNumber, storedValueTitle } from "../ui/valueFormat";
import { isEditableFieldType } from "./fieldEditability";
import { relationCellDisplay } from "./relationExpand";
import { draftFromStoredValue, parseEditedValue, type EditDraft } from "./fieldWidgets";
import { FieldInput } from "./FieldInput";
import { LongTextCellDialog } from "./LongTextCellDialog";
import { compactInputClass, compactSelectClass } from "../ui/classes";

/**
 * The stored value as one line of hover text for a cell, or `undefined` when there is nothing to
 * hover.
 *
 * The **stored** value rather than the displayed one, which is the rest of docs/DESIGN.md 5's
 * sentence: "ISO form only in mono metadata and on hover". So a date cell reads `Sun 13 Sep` and
 * hovers `2026-09-13`, an amount reads `68,000` and hovers `68000`, and a note truncated at one
 * line hovers the whole note, where the cell had measured `title: null` before. A select's key is
 * on hover by 7.3 and `Pill` already puts it there itself; the cell's own title happens to be the
 * same string.
 *
 * `undefined` rather than `""` for an absent or empty value: an empty `title` is still a `title`
 * attribute, and hovering an em dash to be shown an empty tooltip is worse than no tooltip. The
 * object form matches `formatFieldValue`'s own fallthrough so the two never disagree about what a
 * stored list looks like as text.
 *
 * *It lives in `ui/valueFormat.ts`.* The record page's Details card needs the same rule, and two
 * implementations of one sentence in docs/DESIGN.md 5 is the defect this repository's
 * one-implementation rule exists to prevent, so the function is shared and this file imports it.
 */

export interface EditableCellProps {
  field: FieldDoc;
  record: RecordDoc;
  table: Table<LegacyFeatures, RecordDoc>;
}

export function EditableCell({ field, record, table }: EditableCellProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<EditDraft>(() => draftFromStoredValue(field, record.data[field.key]));

  if (field.type === "attachment") {
    /* The count, rather than a literal "(attachment)" that reads the same whether the record
       holds zero files or five. The count costs no backend work: `useTableRecordsQuery`
       already sends `fields: "*"`, so the stored ids are on the row. Filenames here are
       deliberately out of scope: `query_records` has `expand_relations` and no attachment
       equivalent, so a filename would need a new batched sidecar on the read path. The cell
       stays read-only; the field is managed on the record card. */
    const stored = record.data[field.key];
    const count = Array.isArray(stored) ? stored.length : 0;
    return (
      <span className="block truncate text-ink-2">
        {count === 1 ? "1 file" : `${count} files`}
      </span>
    );
  }
  if (field.type === "relation") {
    return <span className="block truncate">{relationCellDisplay(record, field.key)}</span>;
  }

  /** `user_ref` renders through `PrincipalName` everywhere this component would
   * otherwise show `formatFieldValue`'s string — the read-only span below and the click-to-edit
   * button's label further down — but NOT as an early return the way `relation` and `attachment`
   * are above. Those two have no inline editor at all, so returning early costs nothing; doing the
   * same for `user_ref` would make the field permanently uneditable, and it must stay editable.
   * `formatFieldValue` itself returns the raw id (fieldDisplay.test.ts pins it), so
   * `MergeConflictDialog` and `groupLabel.ts`, which call it directly rather than through this
   * cell, keep seeing the raw id.
   *
   * The rest of docs/DESIGN.md 5 and 7.7 uses the same shape: a select is a `Pill` (7.3), a
   * number goes through `formatNumber`, a date through `formatDate` and a timestamp through
   * `datetime.ts`'s `formatTimestamp`. Without them the cell shows `68000`, `2026-09-13`,
   * `2026-09-11T13:14:00Z` and a bare button.
   *
   * **Composed OVER `formatFieldValue`, never inside it**. That function returns a `string`
   * and has three string-consuming callers; a pill is a node. So every branch here decides
   * *whether* to render something richer and everything it does not claim — text, booleans,
   * absent values and their em dash — still falls through to the one string rule below.
   *
   * **Still not markdown.** The table cell stays plain (see `FieldValue.tsx`'s header) so a
   * stray asterisk in a `short_text` cannot silently change how a row displays. Pills and number
   * formatting are not markdown, and do not change that. */
  const cellText = (value: unknown): ReactNode => {
    if (field.type === "user_ref") {
      return <PrincipalName id={value} principals={table.options.meta?.principals} />;
    }
    if (value !== null && value !== undefined) {
      if (field.type === "single_select") {
        return <Pill label={selectValueLabel(field, value)} optionKey={String(value)} />;
      }
      if (field.type === "multi_select" && Array.isArray(value) && value.length > 0) {
        // One pill per value (docs/DESIGN.md 7.3), in a flex row so the gap between them is not
        // a space character inside a truncating line.
        return (
          <span className="inline-flex items-center gap-1 align-middle">
            {value.map((one, index) => (
              <Pill
                key={`${String(one)}-${index}`}
                label={selectValueLabel(field, one)}
                optionKey={String(one)}
              />
            ))}
          </span>
        );
      }
      if (field.type === "integer" || field.type === "decimal") {
        return formatNumber(field, value);
      }
      if (field.type === "date" && typeof value === "string") {
        return formatDate(value);
      }
      if (field.type === "datetime" && typeof value === "string") {
        return formatTimestamp(value);
      }
    }
    return formatFieldValue(field, value);
  };

  /** The full stored value on the cell itself, the half of docs/DESIGN.md 5's long-text rule a
   * truncated cell needs: without it, measured, the cell truncated at `clientWidth 156` against a
   * `scrollWidth` of 426 with `title` null, so the rest of a note was unreachable. */
  const cellTitle = storedValueTitle(record.data[field.key]);

  // Below `write` the cell is a read-only display. Hidden rather than disabled, and the
  // screen's one banner is what explains the absence.
  if (table.options.meta?.canWrite === false) {
    return (
      <span
        title={cellTitle}
        className={
          "block truncate px-1 py-0.5 " +
          (field.type === "integer" || field.type === "decimal" ? "text-right" : "text-left")
        }
      >
        {cellText(record.data[field.key])}
      </span>
    );
  }

  const commit = (value: unknown) => {
    setEditing(false);
    table.options.meta?.onCellCommit?.(record, field, value);
  };

  /** The cell keeps rendering its clipped display while the pop-out is open, so
   * opening it moves nothing in the table. */
  const popOut = field.type === "long_text";

  if (popOut || !isEditableFieldType(field.type) || !editing) {
    return (
      <>
        <button
          type="button"
          title={cellTitle}
          className={
            "block w-full cursor-pointer truncate rounded-ctl px-1 py-0.5 hover:bg-ground " +
            (field.type === "integer" || field.type === "decimal" ? "text-right" : "text-left")
          }
          aria-label={`Edit ${field.name} for ${record.key}`}
          onClick={() => {
            setDraft(draftFromStoredValue(field, record.data[field.key]));
            setEditing(true);
          }}
        >
          {cellText(record.data[field.key])}
        </button>
        {popOut && editing && (
          <LongTextCellDialog
            field={field}
            record={record}
            onCommit={commit}
            onClose={() => setEditing(false)}
          />
        )}
      </>
    );
  }

  return (
    <FieldInput
      field={field}
      draft={draft}
      onDraftChange={setDraft}
      onCommit={() => commit(parseEditedValue(field, draft))}
      onCancel={() => setEditing(false)}
      label={`${field.name} value for ${record.key}`}
      inputClassName={compactInputClass + " w-full"}
      selectClassName={compactSelectClass + " w-full"}
      textareaClassName={compactInputClass + " min-h-16 w-full"}
    />
  );
}
