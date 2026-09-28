import { Button } from "../ui/Button";
import { btnSmClass, fieldErrorClass, fieldLabelClass, inputClass } from "../ui/classes";
import { AgentFacingLabel, guidancePanelClass } from "./descriptionPanel";
import { DESCRIPTION_EXAMPLES, DESCRIPTION_GUIDANCE } from "./descriptionGuidance";

export interface EnumOption {
  value: string;
  label: string;
  description: string;
}

interface EnumOptionsEditorProps {
  options: EnumOption[];
  onChange: (options: EnumOption[]) => void;
  /** One entry per option, aligned by index; `null`/absent means that option's
   * description is valid. Computed and validated by the caller (`FieldEditor`) so
   * every enum option's description is rejected client-side exactly like the
   * object type's and the field's own (FR-S4), not silently skipped. */
  errors?: (string | null)[];
}

/** `single_select` / `multi_select` config editor: each option carries its own
 * required, agent-facing description (FR-S4: "enum options carry their own
 * descriptions"). */
export function EnumOptionsEditor({ options, onChange, errors }: EnumOptionsEditorProps) {
  const update = (index: number, patch: Partial<EnumOption>) => {
    onChange(options.map((option, i) => (i === index ? { ...option, ...patch } : option)));
  };
  const remove = (index: number) => {
    onChange(options.filter((_, i) => i !== index));
  };
  const add = () => {
    onChange([...options, { value: "", label: "", description: "" }]);
  };

  return (
    <fieldset className="space-y-3 rounded-card border border-line bg-ground px-3.5 py-3">
      <legend className="text-sm font-semibold text-ink">Options</legend>
      {options.map((option, index) => (
        <div
          key={index}
          data-testid={`enum-option-${index}`}
          className="space-y-2 rounded-card border border-line bg-surface p-3"
        >
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Value</span>
            <input
              className={inputClass}
              value={option.value}
              onChange={(event) => update(index, { value: event.target.value })}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className={fieldLabelClass}>Label</span>
            <input
              className={inputClass}
              value={option.label}
              onChange={(event) => update(index, { label: event.target.value })}
            />
          </label>
          <label className="flex flex-col gap-1">
            <AgentFacingLabel>Description</AgentFacingLabel>
            <input
              aria-label="Description"
              className={inputClass}
              value={option.description}
              onChange={(event) => update(index, { description: event.target.value })}
            />
          </label>
          <p className={guidancePanelClass}>
            {DESCRIPTION_GUIDANCE} {DESCRIPTION_EXAMPLES.enum_option}
          </p>
          {errors?.[index] && (
            <p role="alert" className={fieldErrorClass}>
              {errors[index]}
            </p>
          )}
          <Button type="button" variant="quiet" className={btnSmClass} onClick={() => remove(index)}>
            Remove option
          </Button>
        </div>
      ))}
      <Button type="button" variant="secondary" onClick={add}>
        Add option
      </Button>
    </fieldset>
  );
}
