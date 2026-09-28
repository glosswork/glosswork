import type { FieldDoc } from "../api/objectTypes";

/** The field editor's local editable state: the same shape as `FieldDoc`'s
 * mutable subset, so diffing against the loaded field is a straight comparison. */
export interface EditableFieldSpec {
  name: string;
  description: string;
  type: string;
  config: Record<string, unknown>;
  required: boolean;
  unique: boolean;
  indexed: boolean;
  embed: boolean;
  default: unknown;
}

export function editableFieldFrom(field: FieldDoc): EditableFieldSpec {
  return {
    name: field.name,
    description: field.description,
    type: field.type,
    config: field.config,
    required: field.required,
    unique: field.unique,
    indexed: field.indexed,
    embed: field.embed,
    default: field.default,
  };
}

/** Deep-enough equality for JSON-serializable schema config values. */
function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Only the keys that actually changed, in `PATCH .../fields/{key}`'s `changes`
 * shape — never the whole edited object. Sending only what changed is what lets
 * the service layer's own destructive/additive mix check (`update_field`'s
 * "This change mixes a destructive component with additive changes" error) do its
 * job: a field editor that only touched the description never accidentally also
 * resubmits an untouched `type`. */
export function buildFieldChanges(
  original: FieldDoc,
  edited: EditableFieldSpec,
): Record<string, unknown> {
  const changes: Record<string, unknown> = {};
  if (edited.name !== original.name) changes.name = edited.name;
  if (edited.description !== original.description) changes.description = edited.description;
  if (edited.type !== original.type) changes.type = edited.type;
  if (!sameValue(edited.config, original.config)) changes.config = edited.config;
  if (edited.required !== original.required) changes.required = edited.required;
  if (edited.unique !== original.unique) changes.unique = edited.unique;
  if (edited.indexed !== original.indexed) changes.indexed = edited.indexed;
  if (edited.embed !== original.embed) changes.embed = edited.embed;
  if (!sameValue(edited.default, original.default)) changes.default = edited.default;
  return changes;
}
