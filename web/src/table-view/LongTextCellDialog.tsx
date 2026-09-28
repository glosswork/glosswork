/**
 * The pop-out a `long_text` table cell opens.
 *
 * ONE surface, not two. "Click to expand" and "click to edit" are the same gesture on the same
 * cell, so they are the same pop-out: it opens in view mode showing the whole stored value, and
 * the Edit control inside it switches this same dialog to the text box. There is no read-only
 * variant — `fieldEditability.ts` excludes only `relation` and `attachment`, and `EditableCell`
 * returns a plain span for both before it ever reaches here, so every `long_text` cell is
 * editable and a read-only branch would be dead code.
 *
 * Keyboard reachability and dismissal are why the rejected alternative (hover-to-expand) was
 * rejected, so they are not decoration here: the cell's affordance is already a `<button>`, and
 * `ui/Dialog`'s native `<dialog>`/`showModal()` supplies the focus trap, `aria-modal` and the
 * Escape-to-dismiss path from the browser engine rather than from a hand-rolled key handler.
 *
 * View mode renders the value as MARKDOWN, through the same `FieldValue`
 * component the record detail card uses — the pop-out and the record page are the two surfaces
 * with room to read a formatted value, and the one-line cell this opens from is deliberately
 * not one of them.
 */
import { useState } from "react";
import type { FieldDoc } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import { FieldValue } from "../record-detail/FieldValue";
import { FieldInput } from "./FieldInput";
import { draftFromStoredValue, parseEditedValue, type EditDraft } from "./fieldWidgets";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { btnSmClass, inputClass } from "../ui/classes";

export interface LongTextCellDialogProps {
  field: FieldDoc;
  record: RecordDoc;
  /** Hands the parsed value to `EditableCell`, which routes it to the table's `onCellCommit`
   * meta hook — the same PATCH, `expected_version` discipline and 409 handling the in-cell
   * editor used. This component owns no write path of its own. */
  onCommit: (value: unknown) => void;
  onClose: () => void;
}

export function LongTextCellDialog({ field, record, onCommit, onClose }: LongTextCellDialogProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<EditDraft>(() =>
    draftFromStoredValue(field, record.data[field.key]),
  );

  return (
    <Dialog
      label={`${field.name} for ${record.key}`}
      data-testid="long-text-cell-dialog"
      onCancel={onClose}
    >
      <h2 className="text-lg font-semibold text-ink">{field.name}</h2>
      <p className="mb-3 text-xs text-ink-2">{record.key}</p>

      {editing ? (
        <div className="space-y-3">
          <FieldInput
            field={field}
            draft={draft}
            onDraftChange={setDraft}
            onCommit={() => onCommit(parseEditedValue(field, draft))}
            // Back to view mode, the inverse of the Edit control that opened the text box —
            // not a dismissal. Escape is the dismissal, and the engine's own `cancel` event
            // carries it to `onClose` above.
            onCancel={() => setEditing(false)}
            // The pop-out has explicit Save and Cancel, so a blur must not pre-empt them —
            // the same reason the record detail card passes false (`FieldInput`'s own note).
            commitOnBlur={false}
            label={`${field.name} value for ${record.key}`}
            inputClassName={inputClass}
            selectClassName={inputClass}
            // Sized for real writing: the full dialog width, no `max-w-md`, and tall enough
            // that the value the cell clips to one line reads as a paragraph. The dialog keeps
            // `ui/Dialog`'s own `max-w-xl` rather than overriding it — `cx` is a plain join,
            // so passing `max-w-2xl` here would have put two single-class max-width selectors
            // in one class list and let source order decide which one wins. It
            // was written that way first and measured at 534px, not the 632px the class asked
            // for; the override is gone rather than made to win.
            textareaClassName={`${inputClass} min-h-64 w-full max-w-none`}
          />
          <div className="flex gap-2">
            <Button
              type="button"
              variant="primary"
              className={btnSmClass}
              onClick={() => onCommit(parseEditedValue(field, draft))}
            >
              Save
            </Button>
            <Button
              type="button"
              variant="quiet"
              className={btnSmClass}
              onClick={() => setEditing(false)}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <div
            data-testid="long-text-cell-value"
            // `whitespace-pre-wrap` lives in `FieldValue`'s non-markdown branch, not here: on the
            // markdown path it would put a visible blank line before every rendered block,
            // because `react-markdown` emits literal `\n` text nodes between them. Line breaks
            // are kept by `remark-breaks` instead. `break-words` stays: a `long_text` value can
            // carry an unbroken run with no wrapping opportunity.
            className="max-h-96 overflow-y-auto break-words rounded-card border border-line bg-ground px-3 py-2 text-sm text-ink"
          >
            <FieldValue field={field} value={record.data[field.key]} />
          </div>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="primary"
              className={btnSmClass}
              onClick={() => {
                setDraft(draftFromStoredValue(field, record.data[field.key]));
                setEditing(true);
              }}
            >
              Edit
            </Button>
            <Button type="button" variant="quiet" className={btnSmClass} onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
