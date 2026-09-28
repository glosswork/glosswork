/**
 * One leaf condition row: field, operator, value. The operator `<select>`'s options are always
 * exactly the selected field's `operators` array (docs/MCP_TOOLS.md section 4) — nothing here
 * ever computes or hardcodes which operators a field type supports.
 */
import type { FilterableField } from "./filterableFields";
import type { FilterCondition } from "./types";
import { ValueInput } from "./ValueInput";
import { Button } from "../ui/Button";
import { btnSmClass, compactSelectClass } from "../ui/classes";

export interface ConditionRowProps {
  fields: FilterableField[];
  condition: FilterCondition;
  onChange: (condition: FilterCondition) => void;
  onRemove: () => void;
  onToggleNot: () => void;
  testId: string;
}

export function ConditionRow({
  fields,
  condition,
  onChange,
  onRemove,
  onToggleNot,
  testId,
}: ConditionRowProps) {
  const field = fields.find((candidate) => candidate.key === condition.field);
  const operators = field?.operators ?? [];

  const handleFieldChange = (key: string) => {
    const nextField = fields.find((candidate) => candidate.key === key);
    onChange({ field: key, op: nextField?.operators[0] ?? "", value: undefined });
  };

  const handleOpChange = (op: string) => {
    onChange({ ...condition, op, value: undefined });
  };

  const handleValueChange = (value: unknown) => {
    onChange({ ...condition, value });
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5" data-testid={testId}>
      <Button type="button" variant="quiet" className={btnSmClass} onClick={onToggleNot}>
        NOT
      </Button>
      <select
        className={compactSelectClass}
        aria-label="Field"
        value={condition.field}
        onChange={(event) => handleFieldChange(event.target.value)}
      >
        <option value="" disabled>
          Select field
        </option>
        {fields.map((candidate) => (
          <option key={candidate.key} value={candidate.key}>
            {candidate.name}
          </option>
        ))}
      </select>
      <select
        className={compactSelectClass}
        aria-label="Operator"
        value={condition.op}
        disabled={field === undefined}
        onChange={(event) => handleOpChange(event.target.value)}
      >
        {operators.map((op) => (
          <option key={op} value={op}>
            {op}
          </option>
        ))}
      </select>
      {field !== undefined && (
        <ValueInput field={field} op={condition.op} value={condition.value} onChange={handleValueChange} />
      )}
      <Button
        type="button"
        variant="quiet"
        className={btnSmClass}
        aria-label="Remove condition"
        onClick={onRemove}
      >
        Remove
      </Button>
    </div>
  );
}
