/**
 * FR-U10's merge prompt, as pure data transforms so `MergeConflictDialog.tsx` stays a thin
 * renderer (AGENTS.md: "merge-conflict resolution lives in frontend hooks/utilities... never
 * inline in a component render body"). Shape matches `VersionConflictError.details`
 * (`src/glosswork/errors.py`), nested under the REST envelope's `error.details`, not
 * flattened — see `apiErrors.ts` for the envelope parse.
 */

export interface ConflictingFieldValues {
  your_value: unknown;
  current_value: unknown;
}

export interface ConflictDetails {
  record_key: string;
  current_version: number;
  supplied_version: number;
  conflicting_fields: Record<string, ConflictingFieldValues>;
  changed_since_your_version: string[];
}

export type FieldChoice = "mine" | "theirs" | "edited";

export interface FieldResolution {
  choice: FieldChoice;
  /** Only meaningful when `choice === "edited"`. */
  editedValue?: string;
}

/** Every conflicting field defaults to "mine" (the user's own pending edit), the least
 * surprising starting point for a resolution the user must actively confirm either way. */
export function defaultResolutions(conflict: ConflictDetails): Record<string, FieldResolution> {
  const resolutions: Record<string, FieldResolution> = {};
  for (const field of Object.keys(conflict.conflicting_fields)) {
    resolutions[field] = { choice: "mine" };
  }
  return resolutions;
}

function resolvedValue(
  conflict: ConflictDetails,
  field: string,
  resolution: FieldResolution,
): unknown {
  const values = conflict.conflicting_fields[field];
  if (resolution.choice === "theirs") return values?.current_value;
  if (resolution.choice === "edited") return resolution.editedValue;
  return values?.your_value;
}

export interface ResubmitPayload {
  values: Record<string, unknown>;
  expected_version: number;
}

/** Builds the `PATCH` body to resubmit after the user has chosen, per conflicting field, their
 * value / the current value / an edited merge. Non-conflicting fields from the original pending
 * edit are carried through unchanged (only fields the server actually flagged need a choice);
 * `expected_version` is always the server's `current_version`, never the stale supplied one. */
export function buildResubmitPayload(
  conflict: ConflictDetails,
  pendingValues: Record<string, unknown>,
  resolutions: Record<string, FieldResolution>,
): ResubmitPayload {
  const values: Record<string, unknown> = { ...pendingValues };
  for (const field of Object.keys(conflict.conflicting_fields)) {
    const resolution = resolutions[field];
    if (resolution !== undefined) {
      values[field] = resolvedValue(conflict, field, resolution);
    }
  }
  return { values, expected_version: conflict.current_version };
}
