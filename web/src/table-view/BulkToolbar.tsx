/**
 * Row multi-select's toolbar (FR-U1): bulk edit (dry-run preview, then confirm) and bulk delete
 * (per-record outcomes, `relation_blocked` failures surfaced with their blocking keys). The
 * dry-run/confirm and delete-loop logic live in `useBulkEdit.ts`/`useBulkDelete.ts`; this
 * component only collects the field/value to edit and renders results.
 */
import { useState } from "react";
import type { FieldDoc } from "../api/objectTypes";
import { isEditableFieldType } from "./fieldEditability";
import { parseEditedValue, widgetKindFor, type EditDraft } from "./fieldWidgets";
import type { UseBulkEditResult } from "./useBulkEdit";
import type { UseBulkDeleteResult } from "./useBulkDelete";
import { Button } from "../ui/Button";
import { btnSmClass, compactInputClass, compactSelectClass, inlineLabelClass } from "../ui/classes";

export interface BulkToolbarProps {
  selectedKeys: string[];
  fields: FieldDoc[];
  bulkEdit: UseBulkEditResult;
  bulkDelete: UseBulkDeleteResult;
  onBulkEditApplied: () => void;
}

export function BulkToolbar({
  selectedKeys,
  fields,
  bulkEdit,
  bulkDelete,
  onBulkEditApplied,
}: BulkToolbarProps) {
  const editableFields = fields.filter((field) => isEditableFieldType(field.type));
  const [fieldKey, setFieldKey] = useState<string>(editableFields[0]?.key ?? "");
  const [draft, setDraft] = useState<EditDraft>("");
  const activeField = editableFields.find((field) => field.key === fieldKey);

  if (selectedKeys.length === 0) return null;

  const startPreview = () => {
    if (!activeField) return;
    void bulkEdit.runPreview(selectedKeys, activeField.key, parseEditedValue(activeField, draft));
  };

  const confirmEdit = async () => {
    const result = await bulkEdit.confirm();
    if (result) onBulkEditApplied();
  };

  return (
    <div
      className="flex flex-col items-start gap-2 rounded-card border border-human-line bg-human-soft p-3"
      data-testid="bulk-toolbar"
    >
      <p className="text-sm font-medium text-ink">{selectedKeys.length} selected</p>

      <fieldset
        className="flex flex-wrap items-center gap-2"
        disabled={bulkEdit.preview !== null}
      >
        <legend className="float-left mr-2 text-2xs font-semibold tracking-wider text-ink-2 uppercase">
          Bulk edit
        </legend>
        <label className={inlineLabelClass}>
          Field
          <select
            className={compactSelectClass}
            aria-label="Bulk edit field"
            value={fieldKey}
            onChange={(event) => {
              setFieldKey(event.target.value);
              setDraft("");
            }}
          >
            {editableFields.map((field) => (
              <option key={field.key} value={field.key}>
                {field.name}
              </option>
            ))}
          </select>
        </label>
        {activeField && widgetKindFor(activeField) === "boolean" ? (
          <label className={inlineLabelClass}>
            New value
            <input
              className="accent-accent"
              type="checkbox"
              aria-label="Bulk edit value"
              checked={draft === true}
              onChange={(event) => setDraft(event.target.checked)}
            />
          </label>
        ) : activeField && widgetKindFor(activeField) === "select" ? (
          <label className={inlineLabelClass}>
            New value
            <select
              className={compactSelectClass}
              aria-label="Bulk edit value"
              value={typeof draft === "string" ? draft : ""}
              onChange={(event) => setDraft(event.target.value)}
            >
              <option value="">{"—"}</option>
              {activeField.options?.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <label className={inlineLabelClass}>
            New value
            <input
              className={compactInputClass}
              aria-label="Bulk edit value"
              value={typeof draft === "string" ? draft : ""}
              onChange={(event) => setDraft(event.target.value)}
            />
          </label>
        )}
        <Button type="button" className={btnSmClass} onClick={startPreview} disabled={!activeField}>
          Preview
        </Button>
      </fieldset>

      {bulkEdit.preview && (
        <div className="flex flex-wrap items-center gap-2" data-testid="bulk-edit-preview">
          <p className="text-sm">{bulkEdit.preview.affectedCount} record(s) will be updated.</p>
          <Button type="button" variant="primary" className={btnSmClass} onClick={() => void confirmEdit()}>
            Confirm
          </Button>
          <Button type="button" variant="quiet" className={btnSmClass} onClick={bulkEdit.cancel}>
            Cancel
          </Button>
        </div>
      )}
      {bulkEdit.error && (
        <p className="text-sm text-bad" role="alert">
          {bulkEdit.error}
        </p>
      )}

      <div className="flex flex-col items-start gap-1.5">
        <Button
          type="button"
          variant="danger"
          className={btnSmClass}
          disabled={bulkDelete.running}
          onClick={() => {
            bulkDelete.clearResults();
            void bulkDelete.run(selectedKeys);
          }}
        >
          Bulk delete
        </Button>
        {bulkDelete.results && (
          <ul className="flex flex-col gap-0.5 text-sm" data-testid="bulk-delete-results">
            {bulkDelete.results.map((outcome) => (
              <li key={outcome.recordKey} data-testid={`bulk-delete-result-${outcome.recordKey}`}>
                {outcome.recordKey}:{" "}
                {outcome.status === "deleted"
                  ? "deleted"
                  : outcome.status === "blocked"
                    ? `blocked by ${outcome.blockingRecordKeys?.join(", ")}`
                    : (outcome.message ?? "failed")}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
