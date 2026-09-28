/**
 * Grouping control (FR-U1: grouping by any `single_select` or `relation` field). The available
 * fields come from `fieldEditability.ts`'s `isGroupableFieldType`, never a hardcoded list.
 *
 * **A chip**, because docs/DESIGN.md 7.4 names this one explicitly: "Group and sort are the same
 * chip grammar (`Group by Stage`, `Sort: Next action`)". The chip is the sentence —
 * `Group by Stage ×` — and the `<select>` lives in the popover behind it, keeping its
 * `aria-label`, so the specs that drive it need an opening step rather than a different name.
 *
 * The `×` clears the grouping, which is the same state the select's own "No grouping" option
 * reaches. Both are kept: the `×` is how 7.4 says a chip goes away, and the option is how the
 * popover says it to somebody who is already in there changing the field.
 */
import type { FieldDoc } from "../api/objectTypes";
import { groupableFields } from "./fieldEditability";
import { Chip } from "../ui/Chip";
import { compactSelectClass, inlineLabelClass } from "../ui/classes";

export interface GroupBySelectProps {
  fields: FieldDoc[];
  groupBy: string | null;
  onChange: (groupBy: string | null) => void;
}

export function GroupBySelect({ fields, groupBy, onChange }: GroupBySelectProps) {
  const groupedField = fields.find((field) => field.key === groupBy);

  return (
    <Chip
      data-testid="group-chip"
      onRemove={groupBy === null ? undefined : () => onChange(null)}
      removeLabel="Remove grouping"
      popover={
        <label className={inlineLabelClass}>
          Group by
          <select
            className={compactSelectClass}
            aria-label="Group by"
            value={groupBy ?? ""}
            onChange={(event) => onChange(event.target.value || null)}
          >
            <option value="">No grouping</option>
            {groupableFields(fields).map((field) => (
              <option key={field.key} value={field.key}>
                {field.name}
              </option>
            ))}
          </select>
        </label>
      }
    >
      {groupedField ? `Group by ${groupedField.name}` : "Group"}
    </Chip>
  );
}
