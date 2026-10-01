/**
 * The msw fixtures `/people` and `/setup` share.
 *
 * `settings/SettingsPage.test.tsx` was one 903-line file over one page, so its fixtures and its
 * handlers could be local. That page is now two, and the two halves read overlapping routes: both
 * pages sign in as the same principals, `/people` reads principals and agent labels, `/setup` reads
 * access tokens and the search index, and the error-surface suite runs against mutations on both.
 * One copy rather than two, because two copies of a fixture drift and then two tests disagree about
 * what "Other User" is.
 *
 * The stores are module-level and mutable, exactly as they were in the file this replaces: a
 * handler needs to see a write the test just made, and `resetIdentityStores()` in `beforeEach`
 * is what keeps tests independent.
 */
import { http, HttpResponse } from "msw";
import { DEFAULT_TEST_PRINCIPAL } from "./renderWithProviders";
import type { AgentLabelDoc } from "../api/agentLabels";
import type { PrincipalDoc, PrincipalRole } from "../api/principals";
import type { AccessTokenDoc, MintedAccessTokenDoc } from "../api/accessTokens";
import type { AuthModes, CurrentPrincipal } from "../api/auth";
import type { CreatedInvite, InviteDoc, InviteEmailResult } from "../api/invites";
import type { SearchIndexStatus } from "../api/searchIndex";

export const TEST_PRINCIPAL_ID = DEFAULT_TEST_PRINCIPAL.id;
export const otherPrincipalId = "00000000-0000-4000-8000-000000000002";

export const MEMBER_PRINCIPAL: CurrentPrincipal = {
  id: "00000000-0000-4000-8000-000000000003",
  display_name: "Test Member",
  email: "test-member@example.com",
  type: "user",
  role: "member",
  scope: "write",
  auth_method: "session",
  auth_provider: "local",
};

/**
 * A `creator` signed in over a session cookie: `role_scope("creator")` is `admin`, so this
 * credential's ceiling is `admin`.
 */
export const CREATOR_PRINCIPAL: CurrentPrincipal = {
  id: "00000000-0000-4000-8000-000000000004",
  display_name: "Test Creator",
  email: "test-creator@example.com",
  type: "user",
  role: "creator",
  scope: "admin",
  auth_method: "session",
  auth_provider: "local",
};

/** The same creator reaching the SPA with a `write`-scoped PAT: the credential caps the offer
 * below the role ceiling, which is the half of `_check_ceilings` a role alone cannot express. */
export const CREATOR_WRITE_PAT_PRINCIPAL: CurrentPrincipal = {
  ...CREATOR_PRINCIPAL,
  scope: "write",
  auth_method: "pat",
};

export const ownLabels: AgentLabelDoc[] = [
  {
    id: "label-1",
    principal_id: TEST_PRINCIPAL_ID,
    label: "claude-code",
    display_name: "Claude Code",
    description: "The CLI agent.",
    verified: true,
    first_seen_at: "2026-08-01T10:00:00",
    last_seen_at: "2026-08-24T10:00:00",
    call_count: 42,
  },
  {
    id: "label-2",
    principal_id: TEST_PRINCIPAL_ID,
    label: "unnamed-agent",
    display_name: null,
    description: null,
    verified: false,
    first_seen_at: "2026-08-20T10:00:00",
    last_seen_at: "2026-08-23T10:00:00",
    call_count: 3,
  },
];

export const allLabels: AgentLabelDoc[] = [
  ...ownLabels,
  {
    id: "label-3",
    principal_id: otherPrincipalId,
    label: "other-agent",
    display_name: "Other Agent",
    description: "Someone else's agent.",
    verified: true,
    first_seen_at: "2026-08-05T10:00:00",
    last_seen_at: "2026-08-22T10:00:00",
    call_count: 10,
  },
];

export const principals: PrincipalDoc[] = [
  {
    id: TEST_PRINCIPAL_ID,
    type: "user",
    display_name: "Test Admin",
    email: "test-admin@example.com",
    role: "admin",
    auth_provider: "local",
    external_id: null,
    is_active: true,
    description: null,
    created_at: "2026-08-01T10:00:00",
    created_by: null,
  },
  {
    id: otherPrincipalId,
    type: "user",
    display_name: "Other User",
    email: "other-user@example.com",
    role: "member",
    auth_provider: "local",
    external_id: null,
    is_active: true,
    description: null,
    created_at: "2026-08-02T10:00:00",
    created_by: TEST_PRINCIPAL_ID,
  },
];

export const accessTokens: AccessTokenDoc[] = [
  {
    id: "token-1",
    principal_id: TEST_PRINCIPAL_ID,
    name: "laptop",
    token_prefix: "gw_pat_a",
    scope: "write",
    expires_at: null,
    last_used_at: "2026-08-20T10:00:00",
    revoked_at: null,
    created_at: "2026-08-01T10:00:00",
    created_by: TEST_PRINCIPAL_ID,
    agent_label: "claude-code",
  },
];

export const defaultSearchIndexStatus: SearchIndexStatus = {
  pending_jobs: 3,
  running_jobs: 1,
  failed_jobs: [
    {
      record_key: "INIT-014",
      record_id: "record-1",
      source_type: "field",
      field_key: "scope_summary",
      comment_id: null,
      attempts: 2,
      last_error: "embedding provider timed out",
      updated_at: "2026-08-24T10:00:00",
    },
  ],
  indexed_chunks: 128,
  stale_chunks: 0,
  embedding_model: "bge-small-en-v1.5",
  semantic_enabled: true,
};

/** What the handlers read and write. A test may assign to any of these before rendering. */
export const stores = {
  ownLabels: [] as AgentLabelDoc[],
  allLabels: [] as AgentLabelDoc[],
  principals: [] as PrincipalDoc[],
  accessTokens: [] as AccessTokenDoc[],
  searchIndexStatus: defaultSearchIndexStatus,
  reindexEnqueued: 42,
  /** `GET /api/v1/auth/modes`. A password workspace unless a test turns `email_code` on. */
  modes: { standalone: true, oidc: false, email_code: false } as AuthModes,
  /** Pending invites, and what the next invite's email comes to (change 9). */
  invites: [] as InviteDoc[],
  inviteEmail: { outcome: "accepted", message: "Invite sent." } as InviteEmailResult,
  /** Every path a handler answered this test, in order. `AgentLabelsTable` is the reason: one
   * assertion is about which requests a role does NOT make, and the only way to assert an absence
   * is to record the presences. */
  requests: [] as string[],
};

export function resetIdentityStores(): void {
  stores.ownLabels = ownLabels.map((label) => ({ ...label }));
  stores.allLabels = allLabels.map((label) => ({ ...label }));
  stores.principals = principals.map((principal) => ({ ...principal }));
  stores.accessTokens = accessTokens.map((token) => ({ ...token }));
  stores.searchIndexStatus = {
    ...defaultSearchIndexStatus,
    failed_jobs: defaultSearchIndexStatus.failed_jobs.map((job) => ({ ...job })),
  };
  stores.reindexEnqueued = 42;
  stores.modes = { standalone: true, oidc: false, email_code: false };
  stores.invites = [];
  stores.inviteEmail = { outcome: "accepted", message: "Invite sent." };
  stores.requests = [];
}

export const identityHandlers = [
  http.get("/api/v1/auth/modes", () => HttpResponse.json(stores.modes)),
  http.get("/api/v1/invites", () => {
    stores.requests.push("/invites");
    return HttpResponse.json({ invites: stores.invites });
  }),
  http.post("/api/v1/invites", async ({ request }) => {
    const body = (await request.json()) as { email: string; display_name: string; role: string };
    const invite: InviteDoc = {
      id: `invite-${stores.invites.length + 1}`,
      email: body.email,
      display_name: body.display_name,
      role: body.role as PrincipalRole,
      invited_by: TEST_PRINCIPAL_ID,
      created_at: "2026-09-29T15:00:00Z",
      expires_at: "2026-10-13T15:00:00Z",
      accepted_at: null,
      revoked_at: null,
    };
    stores.invites = [invite, ...stores.invites];
    const created: CreatedInvite = { invite, email: stores.inviteEmail };
    return HttpResponse.json(created, { status: 201 });
  }),
  http.delete("/api/v1/invites/:id", ({ params }) => {
    const target = stores.invites.find((invite) => invite.id === params.id);
    stores.invites = stores.invites.filter((invite) => invite.id !== params.id);
    return HttpResponse.json({ ...target, revoked_at: "2026-09-29T16:00:00Z" });
  }),
  http.get("/api/v1/principals", ({ request }) => {
    stores.requests.push("/principals");
    const url = new URL(request.url);
    const type = url.searchParams.get("type");
    const filtered = type
      ? stores.principals.filter((p) => p.type === type)
      : stores.principals;
    return HttpResponse.json({ principals: filtered });
  }),
  http.post("/api/v1/principals", async ({ request }) => {
    const body = (await request.json()) as {
      type: "user" | "service_account";
      display_name: string;
      email?: string;
      role: PrincipalRole;
      description?: string;
      password?: string;
    };
    if (body.type === "service_account" && (!body.description || body.description.trim() === "")) {
      return HttpResponse.json(
        { code: "validation_failed", message: "A description is required.", details: {} },
        { status: 422 },
      );
    }
    const created: PrincipalDoc = {
      id: `principal-${stores.principals.length + 1}`,
      type: body.type,
      display_name: body.display_name,
      email: body.email ?? null,
      role: body.role,
      auth_provider: body.type === "user" ? "local" : null,
      external_id: null,
      is_active: true,
      description: body.description ?? null,
      created_at: "2026-08-24T10:00:00",
      created_by: TEST_PRINCIPAL_ID,
    };
    stores.principals = [...stores.principals, created];
    return HttpResponse.json(created);
  }),
  http.patch("/api/v1/principals/:id", async ({ request, params }) => {
    const body = (await request.json()) as { role?: PrincipalRole; display_name?: string };
    stores.principals = stores.principals.map((p) =>
      p.id === params.id ? { ...p, ...body } : p,
    );
    return HttpResponse.json(stores.principals.find((p) => p.id === params.id));
  }),
  http.delete("/api/v1/principals/:id", ({ params }) => {
    stores.principals = stores.principals.map((p) =>
      p.id === params.id ? { ...p, is_active: false } : p,
    );
    return HttpResponse.json(stores.principals.find((p) => p.id === params.id));
  }),
  http.get("/api/v1/access-tokens", () =>
    HttpResponse.json({ access_tokens: stores.accessTokens }),
  ),
  http.post("/api/v1/access-tokens", async ({ request }) => {
    const body = (await request.json()) as {
      name: string;
      scope: "read" | "write" | "admin";
      expires_at?: string;
      agent_label?: string;
    };
    // Mirrors the real backend (`AccessTokenService.mint`): the plaintext is returned exactly
    // once, on this response, and is never part of the row the list handler serves above.
    const doc: AccessTokenDoc = {
      id: `token-${stores.accessTokens.length + 1}`,
      principal_id: TEST_PRINCIPAL_ID,
      name: body.name,
      token_prefix: "gw_pat_z",
      scope: body.scope,
      expires_at: body.expires_at ?? null,
      last_used_at: null,
      revoked_at: null,
      created_at: "2026-08-24T10:00:00",
      created_by: TEST_PRINCIPAL_ID,
      // Mirrors the real mint, which stores the label it was given and publishes
      // it back, so a token minted without one reads as null rather than as absent.
      agent_label: body.agent_label ?? null,
    };
    stores.accessTokens = [...stores.accessTokens, doc];
    const minted: MintedAccessTokenDoc = {
      ...doc,
      token: "gw_pat_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
    };
    return HttpResponse.json(minted);
  }),
  http.delete("/api/v1/access-tokens/:id", ({ params }) => {
    stores.accessTokens = stores.accessTokens.map((token) =>
      token.id === params.id ? { ...token, revoked_at: "2026-08-24T11:00:00" } : token,
    );
    return HttpResponse.json(stores.accessTokens.find((token) => token.id === params.id));
  }),
  // The admin cross-user view (FR-I7) is its own admin-scoped route rather than `?all=true`,
  // so that a route's required scope is declared once where it is registered rather than
  // raised inside a handler for one parameter value.
  http.get("/api/v1/agent-labels", () => {
    stores.requests.push("/agent-labels");
    return HttpResponse.json({ labels: stores.ownLabels });
  }),
  http.get("/api/v1/admin/agent-labels", () => {
    stores.requests.push("/admin/agent-labels");
    return HttpResponse.json({ labels: stores.allLabels });
  }),
  http.patch("/api/v1/agent-labels/:id", async ({ request, params }) => {
    const body = (await request.json()) as { display_name?: string; description?: string };
    const apply = (label: AgentLabelDoc): AgentLabelDoc =>
      label.id === params.id
        ? {
            ...label,
            display_name: body.display_name ?? label.display_name,
            description: body.description ?? label.description,
            verified: true,
          }
        : label;
    stores.ownLabels = stores.ownLabels.map(apply);
    stores.allLabels = stores.allLabels.map(apply);
    return HttpResponse.json(stores.allLabels.find((label) => label.id === params.id));
  }),
  http.get("/api/v1/admin/search-index", () => HttpResponse.json(stores.searchIndexStatus)),
  http.post("/api/v1/admin/search-index/reindex", () =>
    HttpResponse.json({ enqueued: stores.reindexEnqueued }),
  ),
];
