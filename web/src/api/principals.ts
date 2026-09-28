/**
 * Typed wrapper functions over the principal-management routes (FR-I3, FR-I4, FR-I5,
 * `src/glosswork/routes/identity.py`): listing, creating, updating, deactivating, and
 * setting a local password — for both human users and service accounts, which share one table
 * (`principals`) and one set of routes, distinguished by `type`.
 *
 * It is also where the identity vocabulary the whole SPA shares is declared once: the three
 * system roles (`PrincipalRole`), the three credential scopes (`Scope`), and `roleScope`, the
 * frontend's deliberate second copy of `services/principals.py::role_scope`.
 * There is no shared type generation for the mapping, because `role` reaches the SPA through
 * `GET /api/v1/me`, which FastAPI types as `dict[str, Any]` and `schema.ts` therefore types as
 * `{ [key: string]: unknown }`. The mitigation is `api/accessModel.test.ts`, which pins the
 * mapping against all three roles, not a claim that the duplication is avoided.
 */
import { apiRequest } from "./client";

/** The system role (`principals.role`), ordered `member < creator < admin` (DD-11).
 * `creator` can reach a deployment from an identity provider through `GW_OIDC_CREATOR_GROUPS`,
 * with no operator involved. */
export type PrincipalRole = "admin" | "creator" | "member";

/** Pinned so a fourth role is a test failure here rather than a silent default elsewhere. */
export const PRINCIPAL_ROLES: readonly PrincipalRole[] = ["admin", "creator", "member"] as const;

/** A credential's scope (`access_tokens.scope`): what it may do anywhere, before any
 * per-object-type grant narrows it. The first of DD-11's three axes. */
export type Scope = "read" | "write" | "admin";

/**
 * The scope a role-derived credential carries, mirroring `services/principals.py::role_scope`
 * exactly: `admin -> admin`, `creator -> admin`, anything else -> `write`. A session cookie
 * carries no scope of its own and derives one through this, so it is what caps what a browser
 * session may mint (`AccessTokensPanel`) and the credential half of `your_access`.
 *
 * A `Record` rather than a conditional, so adding a fourth role to `PrincipalRole` fails to
 * compile here instead of falling through to `write`.
 */
const ROLE_SCOPE: Record<PrincipalRole, Scope> = {
  admin: "admin",
  creator: "admin",
  member: "write",
};

export function roleScope(role: PrincipalRole): Scope {
  return ROLE_SCOPE[role];
}

/**
 * One entry of `GET /api/v1/principals/directory` (DD-25, FR-I16) — the one
 * route in this namespace any authenticated principal may read, and the reason it may is that
 * it carries **exactly these five keys**. `role`, `auth_provider`, `external_id` and
 * `description` are on `PrincipalDoc` and deliberately not here.
 *
 * The permissions panel needs none of the withheld keys, so it needs no `admin` gate (DD-11):
 * it reads only `id` and `display_name`, and `is_active` is one of the five above in any case.
 * The panel offers people from this directory and names existing grant rows from the `principals`
 * sidecar on the grants document, which is what a picker and a row label actually want: a picker
 * offers active people, a label names whoever the row already names, live or not.
 */
export interface PrincipalDirectoryEntry {
  id: string;
  display_name: string;
  email: string | null;
  type: "user" | "service_account";
  is_active: boolean;
}

/**
 * One entry of the `principals` sidecar map that rides on every response document carrying a
 * record. Four keys, not the directory's five: the map is **keyed by** the
 * principal id, so `id` is the one that stays out.
 *
 * `type` is one of the four, although a name rendered next to a value might seem to have no
 * use for it. docs/DESIGN.md 6.1 makes the *kind* load-bearing (shape carries it, so it
 * survives greyscale) and says a service account is an agent, so without `type` every service
 * account would render as a person circle. It discloses nothing new: `type` is one of the five keys
 * `GET /principals/directory` already serves to any authenticated caller.
 *
 * A separate interface rather than `Partial<PrincipalDirectoryEntry>` because these are two
 * different documents from two different routes, and a shape that happens to overlap today is
 * not a reason to make a change to one silently retype the other.
 */
export interface PrincipalRef {
  display_name: string;
  email: string | null;
  is_active: boolean;
  type: "user" | "service_account";
}

/**
 * The sidecar map itself: every principal id a response document references, resolved to a name.
 * Declared here rather than in `api/records.ts` because two unrelated documents
 * carry one — every document holding a record, and `GET /object-types/{key}/grants` — and the
 * type belongs beside the `PrincipalRef` it is a map of, not beside the first thing to carry it.
 *
 * `PrincipalName` is what renders an entry — through the attribution primitive — with
 * the raw id in `font-mono` as the fallback when a referent is not in the map.
 */
export type PrincipalSidecar = Record<string, PrincipalRef>;

export interface PrincipalDirectoryOptions {
  q?: string;
  type?: "user" | "service_account";
  includeInactive?: boolean;
  limit?: number;
}

/**
 * The directory read. Unlike `listPrincipals` it needs no role: it is what makes a `user_ref`
 * field fillable by someone who knows a colleague's name but not their UUID.
 */
export async function fetchPrincipalDirectory(
  options: PrincipalDirectoryOptions = {},
): Promise<PrincipalDirectoryEntry[]> {
  const params = new URLSearchParams();
  if (options.q) params.set("q", options.q);
  if (options.type) params.set("type", options.type);
  if (options.includeInactive) params.set("include_inactive", "true");
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  const query = params.toString();
  const response = await apiRequest<{ principals: PrincipalDirectoryEntry[] }>(
    `/principals/directory${query ? `?${query}` : ""}`,
  );
  return response.principals;
}

export interface PrincipalDoc {
  id: string;
  type: "user" | "service_account";
  display_name: string;
  email: string | null;
  role: PrincipalRole;
  auth_provider: "local" | "oidc" | null;
  external_id: string | null;
  is_active: boolean;
  description: string | null;
  created_at: string;
  created_by: string | null;
}

export async function listPrincipals(
  type?: "user" | "service_account",
): Promise<PrincipalDoc[]> {
  const params = new URLSearchParams({ include_inactive: "true" });
  if (type) {
    params.set("type", type);
  }
  const response = await apiRequest<{ principals: PrincipalDoc[] }>(
    `/principals?${params.toString()}`,
  );
  return response.principals;
}

export interface CreateUserBody {
  type: "user";
  display_name: string;
  email: string;
  role: PrincipalRole;
  password?: string;
}

export interface CreateServiceAccountBody {
  type: "service_account";
  display_name: string;
  /** Required (AGENTS.md non-negotiable 6): the server rejects an empty description
   * regardless of what the form does client-side. */
  description: string;
  role: PrincipalRole;
}

export function createPrincipal(
  body: CreateUserBody | CreateServiceAccountBody,
): Promise<PrincipalDoc> {
  return apiRequest<PrincipalDoc>("/principals", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface UpdatePrincipalBody {
  display_name?: string;
  role?: PrincipalRole;
  description?: string;
}

export function updatePrincipal(id: string, body: UpdatePrincipalBody): Promise<PrincipalDoc> {
  return apiRequest<PrincipalDoc>(`/principals/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** Deactivates the principal (`is_active = false`); never a hard delete (FR-I3) — every audit
 * row, record `created_by`, and comment author is a foreign key onto this table. */
export function deactivatePrincipal(id: string): Promise<PrincipalDoc> {
  return apiRequest<PrincipalDoc>(`/principals/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function setPrincipalPassword(id: string, password: string): Promise<PrincipalDoc> {
  return apiRequest<PrincipalDoc>(`/principals/${encodeURIComponent(id)}/password`, {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}
