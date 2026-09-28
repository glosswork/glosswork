/**
 * FR-U10's merge prompt: a real side-by-side comparison per conflicting field, not a toast or a
 * bare reload prompt. Resolution logic (what value each choice produces, the resubmit payload)
 * is `mergeConflict.ts`'s job; this component only renders the choices and reports the result.
 *
 * Built on the `ui/Dialog` primitive (native `<dialog>`/`showModal`), not a
 * `<div role="dialog">`, which is what gives it the focus trap, Escape-to-cancel, and backdrop.
 * The implicit role and the accessible name are what the queries rely on —
 * `getByTestId("merge-conflict-dialog")`, `getByRole("dialog")`.
 */
import { useState } from "react";
import type { FieldDoc } from "../api/objectTypes";
import { formatFieldValue } from "../record-detail/fieldDisplay";
import { Button } from "../ui/Button";
import { Dialog } from "../ui/Dialog";
import { btnSmClass, compactInputClass } from "../ui/classes";
import {
  buildResubmitPayload,
  defaultResolutions,
  type ConflictDetails,
  type FieldChoice,
  type FieldResolution,
  type ResubmitPayload,
} from "./mergeConflict";

export interface MergeConflictDialogProps {
  conflict: ConflictDetails;
  pendingValues: Record<string, unknown>;
  fieldsByKey: Record<string, FieldDoc>;
  onResubmit: (payload: ResubmitPayload) => void;
  onCancel: () => void;
}

const conflictThClass =
  "border-b border-line-2 px-2.5 py-1 text-left text-2xs font-semibold tracking-wider " +
  "text-ink-2 uppercase";

const conflictTdClass = "border-b border-line px-2.5 py-1.5 align-top";

const choiceLabelClass = "mr-2.5 inline-flex items-center gap-1 text-sm";

export function MergeConflictDialog({
  conflict,
  pendingValues,
  fieldsByKey,
  onResubmit,
  onCancel,
}: MergeConflictDialogProps) {
  const [resolutions, setResolutions] = useState<Record<string, FieldResolution>>(() =>
    defaultResolutions(conflict),
  );
  const conflictingFields = Object.keys(conflict.conflicting_fields);

  const setChoice = (field: string, choice: FieldChoice) => {
    setResolutions((prev) => ({ ...prev, [field]: { ...prev[field], choice } }));
  };
  const setEditedValue = (field: string, editedValue: string) => {
    setResolutions((prev) => ({ ...prev, [field]: { choice: "edited", editedValue } }));
  };

  return (
    <Dialog
      label="Resolve version conflict"
      data-testid="merge-conflict-dialog"
      onCancel={onCancel}
    >
      <h2 className="mb-1 text-lg font-semibold">Resolve version conflict</h2>
      <p className="mb-3 max-w-lg text-sm text-ink-2">
        <span className="font-mono text-ink">{conflict.record_key}</span> was changed by someone
        else (now at version {conflict.current_version}). Choose how to resolve each conflicting
        field.
      </p>
      <table className="mb-4 w-full border-collapse text-sm">
        <thead>
          <tr>
            <th className={conflictThClass}>Field</th>
            <th className={conflictThClass}>Your value</th>
            <th className={conflictThClass}>Current value</th>
            <th className={conflictThClass}>Resolution</th>
          </tr>
        </thead>
        <tbody>
          {conflictingFields.map((field) => {
            const values = conflict.conflicting_fields[field];
            const fieldDoc = fieldsByKey[field];
            const resolution = resolutions[field];
            return (
              <tr key={field} data-testid={`conflict-field-${field}`}>
                <td className={conflictTdClass}>{fieldDoc?.name ?? field}</td>
                <td className={conflictTdClass}>
                  {fieldDoc ? formatFieldValue(fieldDoc, values.your_value) : String(values.your_value)}
                </td>
                <td className={conflictTdClass}>
                  {fieldDoc
                    ? formatFieldValue(fieldDoc, values.current_value)
                    : String(values.current_value)}
                </td>
                <td className={conflictTdClass}>
                  <label className={choiceLabelClass}>
                    <input
                      className="accent-accent"
                      type="radio"
                      name={`resolution-${field}`}
                      checked={resolution.choice === "mine"}
                      onChange={() => setChoice(field, "mine")}
                    />
                    Mine
                  </label>
                  <label className={choiceLabelClass}>
                    <input
                      className="accent-accent"
                      type="radio"
                      name={`resolution-${field}`}
                      checked={resolution.choice === "theirs"}
                      onChange={() => setChoice(field, "theirs")}
                    />
                    Theirs
                  </label>
                  <label className={choiceLabelClass}>
                    <input
                      className="accent-accent"
                      type="radio"
                      name={`resolution-${field}`}
                      checked={resolution.choice === "edited"}
                      onChange={() => setChoice(field, "edited")}
                    />
                    Edit
                  </label>
                  {resolution.choice === "edited" && (
                    <input
                      className={compactInputClass + " mt-1 w-full"}
                      aria-label={`Merged value for ${fieldDoc?.name ?? field}`}
                      value={resolution.editedValue ?? ""}
                      onChange={(event) => setEditedValue(field, event.target.value)}
                    />
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="quiet" className={btnSmClass} onClick={onCancel}>
          Cancel
        </Button>
        <Button
          type="button"
          variant="primary"
          className={btnSmClass}
          onClick={() => onResubmit(buildResubmitPayload(conflict, pendingValues, resolutions))}
        >
          Resubmit
        </Button>
      </div>
    </Dialog>
  );
}
