/**
 * `apiRequest` throws `ApiError` with the raw response text in `.body` (see `api/client.ts`);
 * every domain error the backend can produce shares the one `{error: {code, message, details}}`
 * envelope (`src/glosswork/errors.py::error_envelope`, `app.py`'s exception handler). This is
 * the one place that parses that envelope back out, so no component hand-rolls JSON.parse on an
 * error body.
 */
import { ApiError } from "../api/client";
import type { ConflictDetails } from "./mergeConflict";

export interface ApiErrorEnvelope {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

/** Returns the parsed `{code, message, details}` envelope for an `ApiError`, or `null` if
 * `error` isn't an `ApiError` with that shape (a network failure, a non-JSON body, etc.). */
export function parseApiError(error: unknown): ApiErrorEnvelope | null {
  if (!(error instanceof ApiError)) {
    return null;
  }
  try {
    const parsed: unknown = JSON.parse(error.body);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      "error" in parsed &&
      typeof (parsed as { error: unknown }).error === "object" &&
      (parsed as { error: unknown }).error !== null
    ) {
      const envelope = (parsed as { error: Record<string, unknown> }).error;
      if (typeof envelope.code === "string" && typeof envelope.message === "string") {
        return {
          code: envelope.code,
          message: envelope.message,
          details: (envelope.details as Record<string, unknown> | undefined) ?? {},
        };
      }
    }
  } catch {
    return null;
  }
  return null;
}

/** `error.status === 409` with `code: "version_conflict"` (`VersionConflictError`) — the one
 * case the table view treats specially (the merge dialog) rather than as a generic failure.
 * Returns the typed `details` payload, or `null` for any other error. */
export function parseVersionConflict(error: unknown): ConflictDetails | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const envelope = parseApiError(error);
  if (envelope?.code !== "version_conflict") return null;
  return envelope.details as unknown as ConflictDetails;
}

/**
 * `validation_failed`, and the field the server named.
 *
 * The filter compiler refuses **complete** values as well as incomplete ones — a `date` string
 * that is neither ISO nor a date token, a non-`int` for an `integer`, an unresolvable `user_ref`,
 * a relation key naming no record — and names the offending field in `details.field_key`
 * (`src/glosswork/filters.py`). That key is what lets
 * the table page put the refusal in the popover of the chip that carries it, rather than in the
 * page-level alert that empties the table. No new parsing: `parseApiError` above is still the one
 * reader of the envelope, and this is its second consumer.
 *
 * `fieldKey` is `null` when the refusal names no field — an empty `and` group, say, which
 * `_parse_node` refuses before it reaches a field to name.
 */
export interface ValidationFailure {
  message: string;
  fieldKey: string | null;
}

export function parseValidationFailed(error: unknown): ValidationFailure | null {
  const envelope = parseApiError(error);
  if (envelope?.code !== "validation_failed") return null;
  const fieldKey = envelope.details.field_key;
  return {
    message: envelope.message,
    fieldKey: typeof fieldKey === "string" ? fieldKey : null,
  };
}

/** `relation_blocked` (FR-L4): a delete refused because other live records still link to it.
 * Returns the blocking record keys, or `null` for any other error. */
export function parseRelationBlocked(error: unknown): string[] | null {
  const envelope = parseApiError(error);
  if (envelope?.code !== "relation_blocked") return null;
  const keys = envelope.details.blocking_record_keys;
  return Array.isArray(keys) ? keys.map(String) : [];
}

/**
 * `forbidden` (DD-11): the credential's scope was sufficient and the
 * principal's **grant** was not. A sibling of `insufficient_scope`, which is also a 403 and is
 * a different situation with a different remedy — present a stronger credential, rather than
 * ask somebody for access — which is exactly why they are two separate codes instead of one
 * 403 with prose to tell apart.
 *
 * `message` is the backend's, and the UI renders it **verbatim**. The backend writes that copy to
 * name the four things a caller needs in order to do something useful instead of retrying, and a
 * second paraphrase in the frontend would be a second place to get it wrong.
 *
 * `objectType` is `null` for the role-axis variant (`ForbiddenError.for_role`),
 * which shares the code and carries `required_role` / `actual_role` instead.
 */
export interface ForbiddenDetails {
  message: string;
  objectType: string | null;
  held: string | null;
  required: string | null;
}

export function parseForbidden(error: unknown): ForbiddenDetails | null {
  if (!(error instanceof ApiError) || error.status !== 403) return null;
  const envelope = parseApiError(error);
  if (envelope?.code !== "forbidden") return null;
  const asString = (value: unknown): string | null =>
    typeof value === "string" ? value : null;
  return {
    message: envelope.message,
    objectType: asString(envelope.details.object_type),
    held: asString(envelope.details.held),
    required: asString(envelope.details.required),
  };
}
