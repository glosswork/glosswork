/**
 * Normalizes a `describe_object_type` response's `fields` (and, optionally, `system_fields`)
 * into one flat, queryable list the filter builder renders from. This is the *only* place that
 * reads `field.operators`/`field.type` to decide what to show — and even here, `type` only ever
 * selects an input *widget* (see `ValueInput.tsx`), never the legal operator list, which always
 * comes straight from `operators`.
 */
import type { FieldDoc, FieldOption, SystemFieldDoc } from "../api/objectTypes";

export interface FilterableField {
  key: string;
  name: string;
  type: string;
  operators: string[];
  options?: FieldOption[];
}

export function toFilterableFields(
  fields: FieldDoc[],
  systemFields: SystemFieldDoc[] = [],
): FilterableField[] {
  const userFields: FilterableField[] = fields.map((field) => ({
    key: field.key,
    name: field.name,
    type: field.type,
    operators: field.operators,
    options: field.options,
  }));
  const pseudoFields: FilterableField[] = systemFields.map((field) => ({
    key: field.key,
    name: field.key,
    type: field.type,
    operators: field.operators,
  }));
  return [...userFields, ...pseudoFields];
}
