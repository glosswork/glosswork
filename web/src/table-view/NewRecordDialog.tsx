/**
 * The create-record form (DD-44): one modal over the table page, one field per value
 * the create route will accept, and a write through the one API module.
 *
 * Modelled on `people/PeopleTable.tsx`'s `InviteDialog` and `setup/ServiceAccountsTable.tsx` —
 * `ui/Dialog` + a `<form>` + a primary and a quiet Cancel — rather than inventing a third shape.
 * The synchronous pre-flight below is the second one of those two; a same-tick `setState` in a
 * submit handler is already proven safe here, which is what `search/SearchPage.tsx`'s twelve-line
 * paragraph about React 19 and `FormData` is NOT about (that one is a same-tick *router* update).
 *
 * **Relation and attachment fields are absent, and that is the write path's rule rather than this
 * form's scope.** The server refuses a relation value at create outright — a relation lives in the
 * link table — and an attachment value must name ids that only an upload produces. `creatableFields`
 * is what encodes it, shared with the table's inline cell edit.
 */
import { useState } from "react";
import type { FormEvent } from "react";
import type { FieldDoc, ObjectTypeDetail } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { fieldErrorClass, fieldLabelClass, inputClass, selectClass } from "../ui/classes";
import { FieldInput } from "./FieldInput";
import {
  creatableFields,
  draftToValues,
  initialDraft,
  missingRequired,
  type RecordDraft,
} from "./newRecordDraft";
import { useCreateRecord } from "./useCreateRecord";

export interface NewRecordDialogProps {
  objectType: ObjectTypeDetail;
  onCreated: (record: RecordDoc) => void;
  onCancel: () => void;
}

/** `FieldInput` requires both callbacks, and in this surface neither has anything to do.
 *
 * The form owns Enter and the dialog owns Escape, and `FieldInput` calls `preventDefault` on
 * neither key — so wiring `onCommit` to the submit would run it *and* the form's own implicit
 * submission, and wiring `onCancel` to the close would run it *and* the native `cancel` event that
 * `Dialog` already routes to `onCancel`. Each key is handled exactly once, one layer up. */
const NOT_THIS_SURFACE = () => {};

export function NewRecordDialog({ objectType, onCreated, onCancel }: NewRecordDialogProps) {
  const fields = creatableFields(objectType.fields);
  const [draft, setDraft] = useState<RecordDraft>(() => initialDraft(objectType.fields));
  const [missing, setMissing] = useState<string[]>([]);
  const { create, failure, clearFailure, isPending } = useCreateRecord(objectType.key);

  const hiddenFieldCount = objectType.fields.length - fields.length;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    clearFailure();

    // The pre-flight. Its whole purpose is that a required field the person has not filled in is
    // reported beside that field before a request goes out, rather than as a 422 afterwards.
    const missingNow = missingRequired(objectType.fields, draft);
    setMissing(missingNow);
    if (missingNow.length > 0) return;

    void create(draftToValues(objectType.fields, draft)).then((created) => {
      if (created) onCreated(created);
    });
  }

  function errorFor(field: FieldDoc): string | null {
    if (missing.includes(field.key)) return `${field.name} is required.`;
    if (failure?.fieldKey === field.key) return failure.message;
    return null;
  }

  return (
    <Dialog
      label={`New ${objectType.name}`}
      data-testid="new-record-dialog"
      onCancel={onCancel}
      className="max-h-[85vh] overflow-y-auto"
    >
      {/* An `h2`, not the `h3` the invite dialog uses: `heading-outline.spec.ts` walks every
          heading inside `main`, and this renders inside `main` beneath the table page's `h1`. */}
      <h2 className="mb-3 text-lg font-semibold text-ink">New {objectType.name}</h2>

      <form aria-label={`New ${objectType.name}`} onSubmit={handleSubmit} className="space-y-3">
        {fields.map((field, index) => {
          const error = errorFor(field);
          const controlId = `new-record-${field.key}`;
          return (
            <div key={field.key} className="flex flex-col gap-1">
              <label className={fieldLabelClass} htmlFor={controlId}>
                {field.name}
                {field.required && <span className="ml-1 text-bad">*</span>}
              </label>
              <FieldInput
                field={field}
                draft={draft[field.key]}
                onDraftChange={(next) => setDraft((prev) => ({ ...prev, [field.key]: next }))}
                onCommit={NOT_THIS_SURFACE}
                onCancel={NOT_THIS_SURFACE}
                label={field.name}
                id={controlId}
                inputClassName={inputClass}
                selectClassName={selectClass}
                textareaClassName={inputClass}
                // Explicit, because the prop DEFAULTS TO TRUE: omitting it on the other fields
                // would autofocus every one of them and let the last win.
                autoFocus={index === 0}
                // Honoured on every branch of the field input. If it were not, tabbing between
                // fields here would fire a commit per field.
                commitOnBlur={false}
              />
              {error && (
                <p role="alert" className={fieldErrorClass}>
                  {error}
                </p>
              )}
            </div>
          );
        })}

        {hiddenFieldCount > 0 && (
          /* Phrased by what the fields do, naming no field type. `docs/DESIGN.md` 5 makes
             `ui/vocabulary.ts` the one table of words, and it renders BOTH `url` and `relation` as
             "Link" — so "Link fields" would be ambiguous with the `url` fields this form does
             offer. Saying it plainly sidesteps that and reads better besides. */
          <p className="text-xs text-ink-2" data-testid="new-record-deferred-fields">
            Linked records and files are added on the record page once it exists.
          </p>
        )}

        {failure && failure.fieldKey === null && (
          <Alert tone="error" title={failure.message} />
        )}

        <div className="flex gap-2">
          <Button type="submit" variant="primary" disabled={isPending}>
            {isPending ? "Creating…" : `Create ${objectType.name}`}
          </Button>
          <Button type="button" variant="quiet" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
