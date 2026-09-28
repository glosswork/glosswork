/**
 * Typed wrapper functions over `GET /api/v1/object-types` and `GET /api/v1/object-types/{key}`
 * (`list_object_types` / `describe_object_type`, docs/MCP_TOOLS.md 5.1).
 *
 * Both routes return `dict[str, Any]` from FastAPI, so `schema.ts` types their response bodies
 * as bare `{ [key: string]: unknown }` (see `operations["list_object_types_..."]` /
 * `operations["get_object_type_..."]` in the generated file). The interfaces below describe the
 * real, documented shape (docs/MCP_TOOLS.md 5.1; `envelopes.py::object_type_summary` and
 * `describe_object_type_doc`) rather than duplicating anything the generator already types
 * precisely.
 */
import type { paths } from "./schema";
import { apiRequest } from "./client";

/**
 * What a principal may do to one object type: `object_type_grants.level`, falling back to
 * `object_types.default_level` (DD-11). `none` is an explicit closed state, not an absent
 * one — every object type is closed by default.
 */
export type Level = "none" | "read" | "write" | "admin";

/** Pinned so a fifth level is a test failure here rather than a silent default elsewhere. */
export const LEVELS: readonly Level[] = ["none", "read", "write", "admin"] as const;

const LEVEL_ORDER: Record<Level, number> = { none: -1, read: 0, write: 1, admin: 2 };

/**
 * The frontend's deliberate second copy of `auth.py::level_allows`. `required`
 * excludes `none` for the same reason the backend types it as `Scope`: no call site can ask
 * whether a level clears "no access". Pinned exhaustively in `api/accessModel.test.ts`.
 *
 * This gates *affordances*, never authorization — the server is the boundary and stays it.
 * Every call here has a `require_level` twin on the server.
 */
export function levelAllows(actual: Level, required: Exclude<Level, "none">): boolean {
  return LEVEL_ORDER[actual] >= LEVEL_ORDER[required];
}

export interface ObjectTypeSummary {
  key: string;
  name: string;
  description: string;
  key_prefix: string;
  record_count: number;
  field_count: number;
  /**
   * What *this* caller may do to this type: the already-composed
   * `min(credential scope, granted level)` (`envelopes.py::object_type_summary`).
   * The UI never sees a raw grant and never composes anything. `ObjectTypeDetail` carries the
   * same field, so a screen that already fetched the detail document reads it from there.
   */
  your_access: Level;
}

export interface FieldOption {
  value: string;
  label: string;
  description: string;
}

/**
 * One field of a `describe_object_type` document. `options` is present for `single_select` /
 * `multi_select` fields; `target_type_key`, `cardinality`, and `inverse_field_key` are present
 * for `relation` fields; `samples` is present only when `include_samples=true` was requested
 * (docs/MCP_TOOLS.md 5.1).
 */
export interface FieldDoc {
  key: string;
  name: string;
  type: string;
  description: string;
  required: boolean;
  unique: boolean;
  indexed: boolean;
  embed: boolean;
  default: unknown;
  config: Record<string, unknown>;
  position: number;
  operators: string[];
  /** Whether this field may be the type's display field: `fieldtypes.py::is_display_eligible`,
   * the only implementation. False for `relation`, `attachment` and
   * `user_ref`. Read it; never re-derive it from `type` here. */
  display_eligible: boolean;
  options?: FieldOption[];
  target_type_key?: string | null;
  cardinality?: string | null;
  inverse_field_key?: string | null;
  samples?: unknown[];
}

export interface SystemFieldDoc {
  key: string;
  type: string;
  description: string;
  operators: string[];
}

export interface ObjectTypeDetail extends ObjectTypeSummary {
  name_plural: string;
  /**
   * DD-23. Two fields because one value cannot serve both readers.
   * `display_field_key` is the **stored** column, null when nobody has chosen: the schema
   * editor's select shows this one, so saving an unrelated setting cannot silently convert
   * an implicit null into a pin the user never made. `effective_display_field_key` is what
   * the backend actually resolves to, null only when the type has no eligible field at all.
   * Neither is re-derived here; both arrive on the wire.
   */
  display_field_key: string | null;
  effective_display_field_key: string | null;
  fields: FieldDoc[];
  system_fields: SystemFieldDoc[];
}

type GetObjectTypeQuery = NonNullable<
  paths["/api/v1/object-types/{key}"]["get"]["parameters"]["query"]
>;

/** `GET /api/v1/object-types` (`list_object_types`). */
export async function listObjectTypes(): Promise<ObjectTypeSummary[]> {
  return apiRequest<ObjectTypeSummary[]>("/object-types");
}

/** `GET /api/v1/object-types/{key}` (`describe_object_type`). */
export async function getObjectType(
  key: string,
  query: GetObjectTypeQuery = {},
): Promise<ObjectTypeDetail> {
  const search = query.include_samples ? "?include_samples=true" : "";
  return apiRequest<ObjectTypeDetail>(`/object-types/${encodeURIComponent(key)}${search}`);
}
