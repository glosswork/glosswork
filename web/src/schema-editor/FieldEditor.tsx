import { useState } from "react";
import type { FieldDoc } from "../api/objectTypes";
import {
  addField,
  updateField,
  type FieldUpdatePending,
  type FieldSpec,
} from "../api/schemaAdmin";
import { parseApiError } from "../table-view/apiErrors";
import { useObjectTypes } from "../hooks/useObjectTypes";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { Select } from "../ui/Select";
import { fieldErrorClass, fieldLabelClass, inputClass } from "../ui/classes";
import { AgentFacingLabel, guidancePanelClass } from "./descriptionPanel";
import { DESCRIPTION_EXAMPLES, DESCRIPTION_GUIDANCE, validateDescription } from "./descriptionGuidance";
import { EnumOptionsEditor, type EnumOption } from "./EnumOptionsEditor";
import { FIELD_TYPES } from "./fieldTypes";
import { buildFieldChanges, editableFieldFrom, type EditableFieldSpec } from "./fieldSpecDiff";

interface FieldEditorProps {
  objectTypeKey: string;
  field?: FieldDoc; // present in edit mode, absent when adding a new field
  onApplied: (field: FieldDoc) => void;
  onPending: (result: FieldUpdatePending) => void;
  onCancel: () => void;
}

const EMPTY_FIELD: EditableFieldSpec = {
  name: "",
  description: "",
  type: "short_text",
  config: {},
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
};

/** Add (FR-S4) or edit (FR-S5/FR-S6) one field of any of the thirteen supported
 * types, with its type-specific config. Additive changes apply immediately;
 * destructive ones hand back a pending proposal for the caller to render via
 * `BlastRadiusPanel` — this component never decides which happened, it only
 * reports the server's own response (`FieldUpdateResponse`). */
export function FieldEditor({ objectTypeKey, field, onApplied, onPending, onCancel }: FieldEditorProps) {
  const isEdit = field !== undefined;
  const [key, setKey] = useState(field?.key ?? "");
  const [spec, setSpec] = useState<EditableFieldSpec>(field ? editableFieldFrom(field) : EMPTY_FIELD);
  const [descriptionError, setDescriptionError] = useState<string | null>(null);
  const [optionErrors, setOptionErrors] = useState<(string | null)[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // `services/schema.py` derives `embed` from
  // `field_type == "long_text"` but honors an explicit value over that default, so a form that
  // always sends one makes the surface, not the type, decide whether a field is searchable.
  // The control therefore tracks the type until the user touches it, and only then holds still.
  const [embedTouched, setEmbedTouched] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { data: objectTypes } = useObjectTypes();

  const patch = (partial: Partial<EditableFieldSpec>) => setSpec((prev) => ({ ...prev, ...partial }));

  const options = (spec.config.options as EnumOption[] | undefined) ?? [];
  const setOptions = (next: EnumOption[]) => patch({ config: { ...spec.config, options: next } });

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const descError = validateDescription(spec.description, isEdit ? "Field" : "New field");
    setDescriptionError(descError);
    // Every enum option's own description is required too (FR-S4), validated client-side the
    // same way the object type's and the field's own descriptions are.
    const isEnumType = spec.type === "single_select" || spec.type === "multi_select";
    const nextOptionErrors = isEnumType
      ? options.map((option) => validateDescription(option.description, "Enum option"))
      : [];
    setOptionErrors(nextOptionErrors);
    if (descError || nextOptionErrors.some((error) => error !== null)) return;
    if (!isEdit && !key.trim()) {
      setSubmitError("Field key is required.");
      return;
    }

    setIsSubmitting(true);
    setSubmitError(null);
    try {
      if (!isEdit) {
        const body: FieldSpec = {
          key: key.trim(),
          name: spec.name,
          type: spec.type,
          description: spec.description,
          config: spec.config,
          required: spec.required,
          unique: spec.unique,
          indexed: spec.indexed,
          embed: spec.embed,
          default: spec.default,
        };
        const result = await addField(objectTypeKey, body);
        onApplied(result.field);
        return;
      }
      const changes = buildFieldChanges(field, spec);
      if (Object.keys(changes).length === 0) {
        onCancel();
        return;
      }
      const result = await updateField(objectTypeKey, field.key, changes);
      if (result.status === "applied") {
        onApplied(result.field);
      } else {
        onPending(result);
      }
    } catch (caught) {
      const envelope = parseApiError(caught);
      setSubmitError(envelope?.message ?? "The request failed.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <form
      onSubmit={(event) => void handleSubmit(event)}
      aria-label={isEdit ? "Edit field" : "Add field"}
      className="max-w-2xl space-y-3 rounded-card border border-line bg-surface p-4"
    >
      <h3 className="text-lg font-semibold text-ink">
        {isEdit ? `Edit field: ${field.key}` : "Add field"}
      </h3>

      <label className="flex flex-col gap-1">
        <span className={fieldLabelClass}>Key</span>
        <input
          className={inputClass}
          value={isEdit ? field.key : key}
          onChange={(event) => setKey(event.target.value)}
          disabled={isEdit}
        />
      </label>
      {isEdit && (
        <p className="max-w-md text-xs text-ink-2">
          Field keys are immutable after creation. Add a new field instead.
        </p>
      )}

      <label className="flex flex-col gap-1">
        <span className={fieldLabelClass}>Name</span>
        <input
          className={inputClass}
          value={spec.name}
          onChange={(event) => patch({ name: event.target.value })}
        />
      </label>

      <label className="flex flex-col gap-1">
        <AgentFacingLabel>Description</AgentFacingLabel>
        <textarea
          aria-label="Description"
          className={inputClass}
          value={spec.description}
          onChange={(event) => patch({ description: event.target.value })}
        />
      </label>
      <p className={guidancePanelClass}>
        {DESCRIPTION_GUIDANCE} {DESCRIPTION_EXAMPLES.field}
      </p>
      {descriptionError && (
        <p role="alert" className={fieldErrorClass}>
          {descriptionError}
        </p>
      )}

      <label className="flex flex-col gap-1">
        <span className={fieldLabelClass}>Type</span>
        <Select
          className="max-w-xs"
          value={spec.type}
          onChange={(event) => {
            const nextType = event.target.value;
            patch({
              type: nextType,
              config: {},
              ...(embedTouched ? {} : { embed: nextType === "long_text" }),
            });
          }}
          disabled={isEdit && field.type === "relation"}
        >
          {FIELD_TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </Select>
      </label>

      {(spec.type === "single_select" || spec.type === "multi_select") && (
        <EnumOptionsEditor options={options} onChange={setOptions} errors={optionErrors} />
      )}

      {spec.type === "decimal" && (
        <label className="flex flex-col gap-1">
          <span className={fieldLabelClass}>Precision (decimal places)</span>
          <input
            type="number"
            className={inputClass}
            value={(spec.config.precision as number | undefined) ?? ""}
            onChange={(event) =>
              patch({ config: { ...spec.config, precision: Number(event.target.value) } })
            }
          />
        </label>
      )}

      {spec.type === "relation" && (
        <div className="space-y-3">
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Target object type</span>
            <Select
              className="max-w-xs"
              value={(spec.config.target_type_key as string | undefined) ?? ""}
              onChange={(event) =>
                patch({ config: { ...spec.config, target_type_key: event.target.value } })
              }
            >
              <option value="">Select an object type</option>
              {(objectTypes ?? []).map((objectType) => (
                <option key={objectType.key} value={objectType.key}>
                  {objectType.name}
                </option>
              ))}
              <option value={objectTypeKey}>(self-referential)</option>
            </Select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Cardinality</span>
            <Select
              className="max-w-xs"
              value={(spec.config.cardinality as string | undefined) ?? "many"}
              onChange={(event) => patch({ config: { ...spec.config, cardinality: event.target.value } })}
            >
              <option value="one">one</option>
              <option value="many">many</option>
            </Select>
          </label>
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Inverse field key (optional)</span>
            <input
              className={inputClass}
              value={(spec.config.inverse_field_key as string | undefined) ?? ""}
              onChange={(event) =>
                patch({ config: { ...spec.config, inverse_field_key: event.target.value || undefined } })
              }
            />
          </label>
        </div>
      )}

      {spec.type === "attachment" && (
        <div className="space-y-3">
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Max files</span>
            <input
              type="number"
              className={inputClass}
              value={(spec.config.max_files as number | undefined) ?? ""}
              onChange={(event) =>
                patch({ config: { ...spec.config, max_files: Number(event.target.value) } })
              }
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Max bytes per file</span>
            <input
              type="number"
              className={inputClass}
              value={(spec.config.max_bytes as number | undefined) ?? ""}
              onChange={(event) =>
                patch({ config: { ...spec.config, max_bytes: Number(event.target.value) } })
              }
            />
          </label>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <Checkbox checked={spec.required} onChange={(event) => patch({ required: event.target.checked })}>
          Required
        </Checkbox>
        <Checkbox checked={spec.unique} onChange={(event) => patch({ unique: event.target.checked })}>
          Unique
        </Checkbox>
        <Checkbox checked={spec.indexed} onChange={(event) => patch({ indexed: event.target.checked })}>
          Indexed
        </Checkbox>
        {(spec.type === "long_text" || spec.type === "short_text") && (
          <Checkbox
            checked={spec.embed}
            onChange={(event) => {
              setEmbedTouched(true);
              patch({ embed: event.target.checked });
            }}
            help="Controls both the keyword and semantic search indexes for this field. Turning it off makes the field's content unsearchable by either mode."
          >
            Include in search indexes (keyword and semantic)
          </Checkbox>
        )}
      </div>

      {submitError && (
        <p role="alert" className={fieldErrorClass}>
          {submitError}
        </p>
      )}

      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={isSubmitting}>
          {isEdit ? "Save field" : "Add field"}
        </Button>
        <Button type="button" variant="quiet" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
