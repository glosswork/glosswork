# 20: The operator token can take a backup, and nothing more

| | |
| --- | --- |
| Issue | [#20](https://github.com/glosswork/glosswork/issues/20) |
| Branch | `20-operator-token-backup` |
| Spec | PRD.md FR-P8, FR-P10; docs/DESIGN_DECISIONS.md DD-36, DD-39, DD-11, DD-4, DD-15; docs/DEPLOYMENT.md sections 6 and 8; docs/MCP_TOOLS.md section 8; PLAN.md section 12 (the exception), Q57, Q63 |
| Decisions | DD-39 (extended), DD-36, DD-11, DD-4, DD-15 |
| Requirements | FR-P8, FR-P10 |
| Depends on | Nothing. `main` at `753af3c`. Unblocks control-plane CP-11. |

## Why

The hosted offering needs a nightly backup per tenant (PLAN 5.5, CP-11). The only
backup route today, `POST /api/v1/admin/backup`, needs an `admin` personal access token
and the `admin` role (`src/glosswork/routes/admin_ops.py:35`). Under Q57 the control
plane holds no admin token for any tenant, by design: it revokes the one the bootstrap
claim returned and never stores it. So the backup the hosted offering depends on has no
credential it is allowed to present.

Chris decided this on 2026-09-23 (Q63): the operator token, which today opens only the
usage counts, may also take a backup, and nothing else. This is an explicit, named
exception to PLAN 12's last line ("none of these touches the access model"), granted
knowingly, because the only alternatives are an admin token stored per tenant (which
reverses Q57) or volume snapshots alone (which cannot meet PLAN 5.5's 30-day retention or
the EU-bucket rule).

**This change reaches a security boundary and an audit decision, and both are in the
list CONTRIBUTING keeps out of delegation.** They are set out under "Judgment areas"
below and approved in this plan before any code.

## Judgment areas this change reaches (CONTRIBUTING)

1. **The access model (PLAN 12's exception; DD-11, DD-39).** The operator token's reach
   grows from one route (the usage counts) to two (plus the backup). The design keeps
   this out of `access_tokens` and out of the three authorization axes: the operator
   token stays a setting the workspace cannot mint, list, revoke or restore, and no
   tenant `scope` value changes. The whole access model stays as it is; what changes is
   one more credential-exempt operator route, built exactly as `GET /api/v1/usage` is.

2. **Audit design (DD-4).** An operator-triggered backup has no tenant principal, yet
   `BackupService.stream` appends a `backup_taken` audit row and DD-4 forbids a write
   with no actor. The decision: an operator backup is attributed to the bootstrap
   service-account principal (`actor.anonymous_actor`), the same principal the
   credential-exempt edge already uses, at `read` scope, `auth_method="pat"`,
   `surface="api"`. That satisfies the `audit_events` CHECK constraints and the
   `principal_id` foreign key, and it reads honestly in the trail: the deployment itself
   took this backup, not a person. Verified by running (see P6).

3. **Error copy (DD-39).** The refusal for a wrong, absent or unconfigured operator
   token on the new route is the existing `OperatorTokenRefusedError` (401
   `operator_token_refused`), reused unchanged, so a prober cannot distinguish the new
   route's states any more than it can the usage route's. No new error code.

## The corrected cost of Q63, for approval

Q63's recorded cost is: "each workspace's stored operator token can now read all of that
workspace's content, though it cannot write." **Measured against `753af3c`, that
understates the reach for a hosted workspace that signs people in by emailed code
(DD-45), which is how every hosted workspace signs people in.**

A backup artifact necessarily contains the `sign_in_codes` table, so that a restored
deployment is a working one. A sign-in code is a six-digit value protected by its
ten-minute lifetime, a five-attempt cap and per-address rate limits, **not** by hash
strength: `src/glosswork/services/sign_in_codes.py` says so in terms ("a six-digit code
has a million values, so no hash protects it from someone holding the database"). The
code-request route is credential-exempt and answers for any address. So possession of
one backup artifact of a hosted workspace is, in effect, a path to a sign-in, and
therefore to whatever that account may write. I reproduced this end to end in a scratch
harness against `753af3c` (the run is not included here; it recovers a live code from an
artifact offline in under a second and signs in). **The operator token's artifact is
therefore not a read-only credential in effect; for a hosted workspace it is a path to
write.**

This is a property of the backup artifact itself, which already holds these tables for
any `admin`-token backup. What this change adds is the first *stored* credential (the
per-tenant operator token at the control plane) that can produce such an artifact,
because Q57 keeps the admin token out of the control plane. So the boundary this change
crosses is wider than Q63 recorded, and the record should say so.

**The mitigation is not in this product and is already planned.** The control-plane
design (OQ11) encrypts each artifact to a public key the control plane cannot decrypt,
and keeps the private key offline. The product cannot close this itself: an artifact that
omits sign-in material would not restore, and omitting it would not close the boundary
anyway, since the artifact still carries session hashes, token hashes and Argon2 password
hashes. So the right product action is to build PROD-51 as specified and correct the
record; the containment lives at the control plane.

This correction is a change to PLAN 16.2 (Q63) and to PLAN 12's cost note, which only
Chris decides. See "Waiting on Chris".

## Premises

- **P1. The operator token is refused on the backup route today, on `753af3c`.**
  Established by running a scratch harness over the real app built from the working tree:
  `POST /api/v1/admin/backup` with the operator token in `X-Operator-Token` answers
  `401 invalid_token`; the same token in `Authorization: Bearer` answers `401
  invalid_token`; an `admin` PAT answers `200 application/x-tar` carrying
  `glosswork.sqlite3`. (The task's original `36ec314` predates REPO-25's re-root; this
  restates the baseline against `753af3c`.)

- **P2. The operator token opens exactly one route today.** Established by sweeping all
  84 registered REST route entries (method, path) from `scopes.flatten_routes`, issuing
  each once with no credential and once with only `X-Operator-Token`, and diffing status
  and body. Exactly one entry changes answer with the header: `GET /api/v1/usage`
  (`401` to `200`). No credentialed `/api/` entry returns anything but `401` with the
  operator header, and `/mcp tools/list` refuses it. This is the enumeration the
  `done_when` asks for, and the test below reproduces it as a guard.

- **P3. The backup route is `admin` scope and `admin` role, and streams one tar.** Read
  at `src/glosswork/routes/admin_ops.py:35-52`: `require_scope("admin")`,
  `require_role("admin")`, `StreamingResponse(services.backup.stream(actor), media_type
  "application/x-tar")`. The service is `src/glosswork/services/backup.py`:
  `BackupService.stream(actor)` is a generator that snapshots the database, then walks the
  blob tree, then appends one `backup_taken` audit row through `make_event(actor, ...)`.

- **P4. `GET /api/v1/usage` is the pattern to copy.** Read at `src/glosswork/routes/
  usage.py`, `src/glosswork/services/usage.py`, `src/glosswork/scopes.py:91-109` and
  `src/glosswork/middleware.py:193-204`. It is credential-exempt on both allowlists
  (`SCOPE_EXEMPT_PATHS`, `AUTH_PUBLIC_PATHS`), reads `X-Operator-Token`, compares it in
  the one function `UsageService.token_matches` (`hmac.compare_digest` over sha256
  digests), and raises `OperatorTokenRefusedError` (401) for every non-operator, byte for
  byte, whether the variable is unset, blank, absent or wrong. Both allowlists are pinned
  by equality in `tests/test_rest_scope_enforcement.py`, so a new exempt entry is a
  visible, test-breaking edit.

- **P5. A refusal raised inside a `StreamingResponse` generator is NOT a refusal.**
  Established by running: a route that returns `StreamingResponse(gen)` where `gen`
  raises `OperatorTokenRefusedError` on its first pull answers `200` with an empty body,
  because the response has already started. This is the same class of defect PROD-02's
  adversarial pass caught (it turned a success into `internal_error`). **Consequence for
  the design: the operator-token check must run in the route body or a dependency, before
  `BackupService.stream` is handed to `StreamingResponse`, never inside the generator.**

- **P6. The bootstrap service-account actor produces a working, correctly attributed
  backup.** Established by running `BackupService.stream(anonymous_actor(...))`: it yields
  a readable tar and writes one `backup_taken` audit row attributed to the bootstrap
  principal (`00000000-0000-4000-8000-000000000001`), `service_account`, `auth_method
  "pat"`, `surface "api"`, `entity_type "backup"`. The principal exists, is active, role
  `admin`. So attributing an operator backup to this actor needs no new principal and no
  migration.

- **P7. The corrected cost (Q63) is real.** Established by running, against `753af3c`, in
  a hosted (relay-configured) app: an artifact taken by the backup service carries the
  live `sign_in_codes` row for an address, the six-digit code is recovered from the
  artifact offline in well under a second, and verifying it issues a session that mints
  an `admin` token. Described in "The corrected cost of Q63" above; the proof script is
  scratch and is not committed.

## What changes

- **A new operator backup route**, in its own module `src/glosswork/routes/
  operator_backup.py` (beside `routes/usage.py`, for its reason: it resolves no tenant
  caller, so it belongs with neither the authenticated routes nor the sign-in flow).
  `POST /api/v1/operator/backup`, reading `X-Operator-Token`, is added to
  `scopes.SCOPE_EXEMPT_PATHS` and `middleware.AUTH_PUBLIC_PATHS` as the next entry in
  each. It calls `UsageService.token_matches` (the one credential comparison) and raises
  `OperatorTokenRefusedError` before streaming; on success it streams
  `BackupService.stream(anonymous_actor(request_id))` with the same `application/x-tar`
  envelope and `Content-Disposition` the admin route uses. **The check is eager (P5): it
  runs in the route body before `StreamingResponse` is constructed.**
  - The path `/api/v1/operator/backup` rather than reusing `/api/v1/admin/backup`:
    `admin` means "a tenant `admin` credential" on every other route, and one path
    answering to two unrelated credentials is the confusion `middleware.py` and
    `routes/usage.py` both warn about. A distinct path under a distinct verb keeps the
    tenant route and the operator route separable and independently testable.
- **`UsageService.token_matches` is reused, not copied.** It is already the one reader of
  `GW_OPERATOR_TOKEN` (DD-39). The route reaches it through the service bundle. No second
  comparison of the operator token appears anywhere.
- **Tests** (`tests/test_operator_backup.py`): the operator token streams a readable tar
  whose first member is `glosswork.sqlite3`; a wrong, absent, blank-configured and
  unconfigured operator token each answer `401 operator_token_refused`, byte for byte; a
  tenant `admin` PAT (the highest a workspace can mint) is refused on this route; the
  `backup_taken` audit row is attributed to the bootstrap service account; and a route
  sweep asserts the operator token opens exactly `GET /api/v1/usage` and `POST
  /api/v1/operator/backup` and nothing else (the `done_when`'s enumeration).
- **The allowlist equality tests** in `tests/test_rest_scope_enforcement.py` gain the new
  path in both pinned sets (`SCOPE_EXEMPT_PATHS`, `AUTH_PUBLIC_PATHS`), since those sets
  grow by exactly one entry.
- **Specifications**, at closeout: DD-39 extended to say the operator credential also
  opens one backup route and that the artifact's reach for a hosted workspace is a sign-in
  path (the corrected cost); DD-36 noting the second caller of `BackupService.stream`;
  `docs/DEPLOYMENT.md` sections 6 and 8 documenting the operator backup route and its
  corrected cost; `docs/MCP_TOOLS.md` section 8 noting the new REST-only operator route.
- **`web/src/api/schema.ts`** regenerated, because a new route changes `/openapi.json`
  (`tests/test_generated_api_types_fresh.py`).

## What does not change

- `access_tokens.scope`, the three authorization axes (DD-11), and how a tenant
  credential resolves. The operator token is still not a row anyone can mint.
- `POST /api/v1/admin/backup`: unchanged, still `admin` scope and role.
- `BackupService` and the artifact's contents and ordering (DD-36). No new data in the
  artifact; the corrected cost is about who can now produce one, not about what it holds.
- The filter compiler, the schema engine, indexing, search ranking, the storage
  abstraction. None is touched.
- Restore. Still a documented operator procedure (DD-36), not an endpoint.

## Constraints

- **The operator-token check is eager, never inside the streaming generator (P5).** The
  Accept block proves a bad token answers 401 with no body streamed.
- **One credential comparison (DD-39).** `UsageService.token_matches` stays the only
  reader of `GW_OPERATOR_TOKEN`; no second comparison is written.
- **Both allowlists stay pinned by equality.** The new entry appears in the tests that
  assert them, so widening is a visible edit.
- **Same image (Q6).** The route is in every image; with `GW_OPERATOR_TOKEN` unset it
  refuses identically to a configured-but-wrong token, so self-host is unaffected and a
  test says so.
- **The refusal discloses nothing (DD-39).** One identical 401 for unset, blank, absent
  and wrong, so the route cannot tell a prober whether the workspace is metered.

## Checklist

1. **Write the new tests first and run them against the unfixed tree**, recording how
   each fails: the route does not exist yet, so the success test 404s/405s and the sweep
   test sees the operator token open only `GET /api/v1/usage`. (Order: this step before
   any route code.)
2. Add `src/glosswork/routes/operator_backup.py` with the eager operator-token check and
   the streamed artifact; register it in `routes/__init__.py`.
3. Add `/api/v1/operator/backup` to `scopes.SCOPE_EXEMPT_PATHS` and
   `middleware.AUTH_PUBLIC_PATHS`, and to the equality tests that pin both.
4. Run `uv run pytest -q tests/test_operator_backup.py tests/test_rest_scope_enforcement.py
   tests/test_operator_usage.py tests/test_backup.py tests/test_api_infra.py` green.
5. Regenerate `web/src/api/schema.ts` (`npm --prefix web run generate:api-types`) and
   confirm `tests/test_generated_api_types_fresh.py`.
6. Run the full backend suite, `ruff check . && ruff format --check .` (both halves,
   reading each exit code), and `mypy src`.
7. Closeout: move the durable content into DD-39, DD-36, `docs/DEPLOYMENT.md` and
   `docs/MCP_TOOLS.md`; delete this plan file.

## Accept

- **AC1. The operator token streams a backup.** `uv run pytest -q
  tests/test_operator_backup.py -k streams_a_readable_tar` passes: the response is `200
  application/x-tar` and its first tar member is `glosswork.sqlite3`.
- **AC2. Every non-operator gets one identical 401.** `uv run pytest -q
  tests/test_operator_backup.py -k refusal` passes: wrong, absent, blank-configured and
  unconfigured operator token each answer `401` with body
  `{"error":{"code":"operator_token_refused", ...}}`, compared equal to each other, and no
  bytes of a tar are streamed.
- **AC3. A tenant `admin` PAT is refused on this route.** Same file, `-k tenant_pat`
  passes: an `admin`-scoped PAT answers `401 operator_token_refused`, identical to the
  anonymous refusal.
- **AC4. The operator token opens exactly two routes, enumerated.** `uv run pytest -q
  tests/test_operator_backup.py -k sweep` passes: a sweep of the whole route table finds
  the operator token changes the answer on exactly `GET /api/v1/usage` and `POST
  /api/v1/operator/backup`.
- **AC5. The backup is attributed to the bootstrap service account.** Same file,
  `-k audited` passes: a `backup_taken` audit row exists with `principal_id` the bootstrap
  id and `principal_type` `service_account`.
- **AC6. The access model is unchanged.** `uv run pytest -q tests/test_rest_scope_
  enforcement.py` passes, including the two allowlist-equality tests with their new entry;
  and `git diff --text main -- src/glosswork/migrations.py` is empty (no migration, no
  scope-constraint change).
- **AC7. Nothing else regressed.** `uv run pytest -q` green;
  `uv run ruff check . && uv run ruff format --check .` each exit 0; `uv run mypy src`
  exit 0; `uv run pytest -q tests/test_generated_api_types_fresh.py` green.

## Adversarial pass

F1..Fn, filled by a separate session.

## Deviations from the approved plan

None yet.

## Durable content moved out of this plan

None yet.
