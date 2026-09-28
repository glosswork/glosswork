/**
 * Typed wrappers over the three per-object-type grant routes
 * (`src/glosswork/routes/schema.py:180, 194, 205`), and the human surface over them, open to
 * every principal the routes accept (DD-11): a type's own administrator, holding `admin` on it
 * and no system role at all.
 *
 * All three declare `require_scope("admin")` and check `admin` on the object type itself.
 * A grant's `level` is the **stored** grant, not the composed `effective` value — a caller
 * asking what *it* may do reads `your_access` on the orientation document instead
 * (`serializers.py::grant_doc`). `level: "none"` is an explicit deny that overrides a
 * permissive `default_level`, which is why the panel renders such a row as "Denied" rather
 * than omitting it.
 */
import { apiRequest } from "./client";
import type { Level } from "./objectTypes";
import type { PrincipalSidecar } from "./principals";

export interface GrantDoc {
  object_type_id: string;
  principal_id: string;
  level: Level;
  created_at: string;
  created_by: string;
  updated_at: string | null;
  updated_by: string | null;
}

export interface ObjectTypeGrants {
  object_type: string;
  /** The type's fallback for any principal with no explicit row. Every object type is closed
   * by default (`none`), which is DD-11's core decision. */
  default_level: Level;
  grants: GrantDoc[];
  /**
   * Every principal id the rows mention — `principal_id`, `created_by` and `updated_by` —
   * resolved to a name (DD-25). The same sidecar shape as on every document
   * carrying a record, and it is required rather than decorative: the picker beside this table
   * reads `GET /principals/directory`, which is active-only and capped at 200, so a row naming
   * a departed colleague would render as a raw UUID without a map built from the rows' own ids.
   */
  principals: PrincipalSidecar;
}

/** `GET /api/v1/object-types/{key}/grants`. */
export function listObjectTypeGrants(key: string): Promise<ObjectTypeGrants> {
  return apiRequest<ObjectTypeGrants>(`/object-types/${encodeURIComponent(key)}/grants`);
}

/** `PUT /api/v1/object-types/{key}/grants/{principal_id}` — creates or updates in one call. */
export function putObjectTypeGrant(
  key: string,
  principalId: string,
  level: Level,
): Promise<GrantDoc> {
  return apiRequest<GrantDoc>(
    `/object-types/${encodeURIComponent(key)}/grants/${encodeURIComponent(principalId)}`,
    { method: "PUT", body: JSON.stringify({ level }) },
  );
}

/** `DELETE /api/v1/object-types/{key}/grants/{principal_id}`. Removes the row entirely, so the
 * principal falls back to `default_level` — which is not the same as granting them `none`. */
export function deleteObjectTypeGrant(
  key: string,
  principalId: string,
): Promise<{ status: string }> {
  return apiRequest<{ status: string }>(
    `/object-types/${encodeURIComponent(key)}/grants/${encodeURIComponent(principalId)}`,
    { method: "DELETE" },
  );
}
