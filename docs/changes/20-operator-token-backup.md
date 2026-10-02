# 20: The operator token can take a backup, and nothing more

| | |
| --- | --- |
| Issue | [#20](https://github.com/glosswork/glosswork/issues/20) |
| Branch | `20-operator-token-backup` |
| Spec | PRD.md FR-P8, FR-P10; docs/DESIGN_DECISIONS.md DD-39, DD-36, DD-11, DD-4, DD-15, DD-38; docs/DEPLOYMENT.md sections 6, 6a and 8; docs/MCP_TOOLS.md section 8; PLAN.md section 12 (the exception), Q57, Q63 |
| Decisions | DD-39 (extended), DD-36, DD-11, DD-4, DD-15, DD-38 |
| Requirements | FR-P8, FR-P10 |
| Depends on | Nothing. `main` at `753af3c`, CI green. Unblocks control-plane CP-11. |

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
below. The plan surfaces four questions only Chris can settle (see "Open questions for
Chris"); this plan is not approvable until they are answered (`docs/changes/README.md`).

## Judgment areas this change reaches (CONTRIBUTING)

1. **The access model (PLAN 12's exception; DD-11, DD-39).** The operator token's reach
   grows from one route (the usage counts) to two (plus the backup). The design keeps
   this out of `access_tokens` and out of the three authorization axes: the operator
   token stays a setting the workspace cannot mint, list, revoke or restore, and no
   tenant `scope` value changes. The whole access model stays as it is; what changes is
   one more credential-exempt operator route, built exactly as `GET /api/v1/usage` is.

2. **Audit design (DD-4).** An operator-triggered backup has no tenant principal, yet
   `BackupService.stream` appends a `backup_taken` audit row and DD-4 forbids a write
   with no actor. The mechanical answer: attribute it to the bootstrap service-account
   principal (`actor.anonymous_actor`), which satisfies the `audit_events` CHECK
   constraints and the foreign key with no migration (P6). **But that row is
   indistinguishable from an admin-PAT backup's except by `request_id`, and its
   `auth_method="pat"` is untrue for a call that resolved no PAT (F5).** Whether to mark
   an operator backup in the trail is OQ2, for Chris.

3. **Error copy (DD-39).** The refusal for a wrong, absent or unconfigured operator
   token on the new route is the existing `OperatorTokenRefusedError` (401
   `operator_token_refused`), reused unchanged, so a prober cannot distinguish the new
   route's states any more than it can the usage route's. No new error code.

## The boundary this change actually crosses

Q63's recorded cost is: "each workspace's stored operator token can now read all of that
workspace's content, though it cannot write." **Measured against `753af3c`, that is
wrong for a hosted workspace that signs people in by emailed code (DD-45), which is how
every hosted workspace signs people in: the operator token's reach is not read-only.**

The mechanism, stated without a recipe: a backup artifact must contain the
`sign_in_codes` table so a restored deployment works. A sign-in code is a six-digit value
protected by its ten-minute lifetime, a five-attempt cap and per-address rate limits, not
by hash strength; `src/glosswork/services/sign_in_codes.py` says so in terms. The
code-request route is credential-exempt and answers for any address. So the holder of a
tenant's operator token, with nothing else, can obtain an administrator session on that
workspace. I reproduced this end to end against `753af3c` in a scratch harness and the
adversarial pass reproduced it independently (0.23 s). **The operator token is therefore,
for a hosted workspace, a path to sign in as any person and write, not a read-only
credential.**

**Scope of the increment, stated precisely (F1).** This is a property of the backup
artifact, which already holds these tables for any `admin`-token backup; and the relay
already receives every code in clear (`src/glosswork/services/relay.py:84-91`), and the
control-plane design already concedes that a control-plane runtime breach reaches every
workspace (the control-plane SPEC, section 11). What this change genuinely adds is
narrower and still real: **a stolen per-tenant operator token alone, with no relay
position and no Fly position, now yields an admin session**, because Q57 keeps the admin
token out of the control plane and this is the first stored credential that can produce a
fresh artifact.

**The control plane's encryption does not mitigate this (F1).** OQ11/Q62 encrypts each
artifact to a public key the control plane cannot decrypt, protecting artifacts at rest
in the bucket. But the artifacts at rest hold only dead codes, and the party who gains
from this path is the operator-token holder, who requests a fresh code and takes a fresh
artifact, seeing the plaintext stream before any encryption. So the containment does not
live at the control plane, and the earlier draft's claim that it did is struck.

**The product can close this (F2), contrary to the first draft.** `sessions` and
`access_tokens` store the sha256 of high-entropy secrets, which do not invert
(`src/glosswork/services/sessions.py:107`, `src/glosswork/services/tokens.py:184`), and
hosted people hold no password, so `sign_in_codes` is the only offline-recoverable
credential in a hosted artifact. Three product-side options close it, smallest first:
  - **(a) Key the code hash with a secret that is not in the database**, such as
    `GW_RELAY_TOKEN` (the control plane keeps only a hash of it, control-plane
    the control-plane SPEC, change 9). An artifact then carries no usable code. No artifact-format
    change, no DD-36 change.
  - **(b) Scrub `sign_in_codes` from the staged snapshot.** Measured by the adversarial
    pass: a plain `DELETE` leaves the hash in free pages; it is gone only after `VACUUM`,
    and `integrity_check` stays `ok`. Touches `BackupService`.
  - **(c) The product encrypts the stream to a recipient key**, closing the read cost
    too. Largest, and overlaps the control plane's own encryption.

Either (a) or (b) makes Q63's recorded cost true as written, instead of amending the
record to accept a wider one. This is OQ1 for Chris.

## Premises

- **P1. The operator token is refused on the backup route today, on `753af3c`.**
  Established by running a scratch harness over the real app built from the working tree:
  `POST /api/v1/admin/backup` with the operator token in `X-Operator-Token` answers
  `401 invalid_token`; the same token in `Authorization: Bearer` answers `401
  invalid_token`; an `admin` PAT answers `200 application/x-tar` carrying
  `glosswork.sqlite3` first. Re-run and confirmed by the adversarial pass. (The task's
  original `36ec314` predates REPO-25's re-root; this restates the baseline against
  `753af3c`.)

- **P2. The operator token opens exactly one route today.** Established by sweeping all
  84 registered REST route entries from `scopes.flatten_routes`, issuing each once with
  no credential and once with only `X-Operator-Token`, and diffing status and body:
  exactly `GET /api/v1/usage` changes (`401` to `200`). No credentialed `/api/` entry
  returns anything but `401` with the operator header, and `/mcp tools/list` refuses it.
  Re-run and confirmed by the adversarial pass.

- **P3. The backup route is `admin` scope and `admin` role, and streams one tar.** Read
  at `src/glosswork/routes/admin_ops.py:35-52`: `require_scope("admin")`,
  `require_role("admin")`, `StreamingResponse(services.backup.stream(actor), media_type
  "application/x-tar")`. The service is `src/glosswork/services/backup.py`:
  `BackupService.stream(actor)` is a generator that snapshots the database, walks the blob
  tree, then appends one `backup_taken` audit row through `make_event(actor, ...)`.

- **P4. `GET /api/v1/usage` is the pattern to copy.** Read at `src/glosswork/routes/
  usage.py`, `src/glosswork/services/usage.py`, `src/glosswork/scopes.py:91-109` and
  `src/glosswork/middleware.py:193-204`. It is credential-exempt on both allowlists
  (`SCOPE_EXEMPT_PATHS`, `AUTH_PUBLIC_PATHS`), reads `X-Operator-Token`, compares it in
  the one function `UsageService.token_matches` (`hmac.compare_digest` over sha256
  digests), and raises `OperatorTokenRefusedError` (401) for every non-operator, byte for
  byte, whether the variable is unset, blank, absent or wrong. Both allowlists are pinned
  by equality in `tests/test_rest_scope_enforcement.py`.

- **P5. A refusal must be raised before the response starts streaming, never inside the
  generator.** Established by running: a route returning `StreamingResponse(gen)` where
  `gen` raises on its first pull does not produce a clean 401. The exact symptom depends
  on the server (F11): under `TestClient(raise_server_exceptions=False)` it is a `200`
  with an empty body; the default `TestClient` raises `RuntimeError: Caught handled
  exception, but response already started`; real uvicorn sends `200` then aborts the
  chunked body. This is the class of defect PROD-02's adversarial pass caught.
  **Consequence for the design: the operator-token check runs in a dependency or the route
  body, before `BackupService.stream` is handed to `StreamingResponse`.**

- **P6. The bootstrap service-account actor produces a working, correctly attributed
  backup.** Established by running `BackupService.stream(anonymous_actor(...))`: a readable
  tar, and one `backup_taken` audit row attributed to the bootstrap principal
  (`00000000-0000-4000-8000-000000000001`), `service_account`, `auth_method "pat"`,
  `surface "api"`, `entity_type "backup"`. The principal exists, is active, role `admin`.
  No new principal, no migration. Re-run and confirmed by the adversarial pass. **The row
  is honest about which deployment took the backup but not about what triggered it (F5).**

- **P7. The operator token alone yields an admin session on a code-sign-in workspace.**
  Established by running, against `753af3c`, in a hosted (relay-configured) app, and
  reproduced independently by the adversarial pass (0.23 s). Described under "The boundary
  this change actually crosses". The proof script is scratch and is not committed.

## What changes

- **A new operator backup route**, in its own module `src/glosswork/routes/
  operator_backup.py` (beside `routes/usage.py`, for its reason: it resolves no tenant
  caller). `POST /api/v1/operator/backup`, reading `X-Operator-Token`, added to
  `scopes.SCOPE_EXEMPT_PATHS` and `middleware.AUTH_PUBLIC_PATHS` as the next entry in
  each. The path is distinct from `/api/v1/admin/backup` because `admin` means "a tenant
  `admin` credential" on every other route, and one path answering two unrelated
  credentials is the confusion `middleware.py` and `routes/usage.py` both warn about.
- **One raiser, reused, not a second check (F6).** `UsageService` grows
  `require_operator(presented)`, which `snapshot()` calls in place of its inline
  `token_matches`/raise and which the new route calls through a tagged dependency in
  `dependencies=[...]`. That makes the check eager by construction (P5), keeps
  `token_matches` the only reader of `GW_OPERATOR_TOKEN` (DD-39), and lets a
  `route_operator_credentials(app)` set be pinned by equality the way `route_capabilities`
  is (F7). The handler takes the edge's `ActorDep` (the same `anonymous_actor` the
  middleware already put on `request.state` for an `AUTH_PUBLIC_PATHS` path,
  `middleware.py:315`) and streams `BackupService.stream(actor)` with the admin route's
  `application/x-tar` envelope and `Content-Disposition`.
- **If Chris chooses OQ1 (a) or (b),** the matching code: keying `sign_in_codes.code_hash`
  with `GW_RELAY_TOKEN` (a) or scrubbing the table from the staged snapshot with
  `DELETE` + `VACUUM` (b). Deferred until OQ1 is answered; it changes `What does not
  change` below.
- **If Chris chooses a backup-trigger marker (OQ2),** a `trigger` keyword on
  `BackupService.stream` written into the `backup_taken` `new_value`, which moves
  `BackupService` out of "unchanged" below.
- **Tests** (`tests/test_operator_backup.py`): the operator token streams a readable tar
  whose first member is `glosswork.sqlite3`; wrong, absent, blank-configured and
  unconfigured operator tokens, a tenant `admin` PAT, a PAT in the operator header, a
  garbage `Authorization`, and the operator token as `Bearer` each answer `401
  operator_token_refused` with bodies compared equal (F8); the `backup_taken` row is the
  one whose `request_id` equals the response `x-request-id` (F5); the backup still runs
  while read-only (F4); and a route sweep asserts the operator token opens exactly `GET
  /api/v1/usage` and `POST /api/v1/operator/backup`.
- **A structural pin (F7):** the call sites of `require_operator`/`token_matches` equal a
  named set, in the `tests/test_one_usage_counter.py` style, so "and nothing more" is
  guarded structurally, not only by the HTTP sweep.
- **The allowlist-equality tests** in `tests/test_rest_scope_enforcement.py` gain the new
  path in both pinned sets.
- **`web/src/api/schema.ts`** regenerated, because a new route changes `/openapi.json`
  (`tests/test_generated_api_types_fresh.py`), then `npm --prefix web run typecheck` (F13).
- **A container proof arm (F13),** an operator-backup case in
  `container_tests/test_operator_usage.py` or `test_backup_restore.py`, because the
  consumer is a program outside the container and `TestClient` and uvicorn differ on
  streaming (F11, F12).
- **Specifications**, at closeout (see "Durable content" / F9).

## What does not change

- `access_tokens.scope`, the three authorization axes (DD-11), how a tenant credential
  resolves. The operator token is still not a row anyone can mint.
- `POST /api/v1/admin/backup`: unchanged, still `admin` scope and role.
- `BackupService` and the artifact's contents and ordering (DD-36) — **unless Chris
  chooses OQ1 (b) or OQ2**, each of which edits `BackupService`.
- The filter compiler, the schema engine, indexing, search ranking, the storage
  abstraction.
- Restore. Still a documented operator procedure (DD-36), not an endpoint.

## Constraints

- **The operator-token check is eager, in a dependency or the route body, never inside the
  streaming generator (P5, F6).** The Accept block proves a bad token answers 401 with no
  tar bytes streamed, and a checklist mutation proves the check moved inside the generator
  turns a refusal test red (F8).
- **One credential comparison (DD-39).** `UsageService.token_matches` stays the only
  reader of `GW_OPERATOR_TOKEN`; the new route reaches it through `require_operator`.
- **The new route is open while frozen by construction, and must NOT be added to
  `READ_ONLY_OPEN_ROUTES` (F4).** It never reaches a scope dependency, so it is already
  open; adding it breaks `test_read_only_mode.py::test_every_open_route_is_a_route_the_
  rule_selects`. A test asserts the operator backup runs while `read_only=True`.
- **Both allowlists stay pinned by equality.** The new entry appears in the tests that
  assert them.
- **Same image (Q6).** With `GW_OPERATOR_TOKEN` unset the route refuses identically to a
  configured-but-wrong token; a test says so.
- **The refusal discloses nothing (DD-39).** One identical 401 body for unset, blank,
  absent, wrong, tenant PAT, and operator-as-Bearer; compared body-only, since
  `x-request-id` differs (F8).
- **Naming (F14):** `tests/test_one_usage_counter.py` greps `\boperator_token\b` outside
  `usage.py`/`config.py`. The new module uses `x_operator_token` and no bare
  `operator_token` identifier or docstring token.

## Checklist

1. **Write the new tests first and run them against the unfixed tree**, recording how each
   fails: the route does not exist (the success test 405/404s), and the sweep sees the
   operator token open only `GET /api/v1/usage`.
2. **Mutation checks for the refusal (F8),** each watched to fail: the check moved inside
   the generator (expect a streamed-after-start error, not a clean 401); the check removed
   (expect a tenant PAT or anonymous caller to get a tar); the check present but accepting
   a tenant PAT (expect the sweep to find a third open route). Record each.
3. Add `src/glosswork/services/usage.py::require_operator`; route `snapshot()` through it.
4. Add `src/glosswork/routes/operator_backup.py` with the tagged operator dependency and
   the streamed artifact; register it in `routes/__init__.py`. Use `x_operator_token`
   spelling only (F14).
5. Add `/api/v1/operator/backup` to `scopes.SCOPE_EXEMPT_PATHS` and
   `middleware.AUTH_PUBLIC_PATHS`, and to the equality tests that pin both.
6. Add the structural call-site pin (F7).
7. **If Chris chose OQ1 (a)/(b) or OQ2,** implement it here with its own test watched to
   fail first.
8. Run `uv run pytest -q tests/test_operator_backup.py tests/test_rest_scope_enforcement.py
   tests/test_operator_usage.py tests/test_one_usage_counter.py tests/test_backup.py
   tests/test_read_only_mode.py tests/test_api_infra.py` green.
9. Regenerate `web/src/api/schema.ts` (`npm --prefix web run generate:api-types`); confirm
   `tests/test_generated_api_types_fresh.py`; run `npm --prefix web run typecheck` (F13).
10. Add the container-proof arm (F13) and run it (needs Docker).
11. Full backend suite; `uv run ruff check .` then `uv run ruff format --check .` as two
    commands reading each exit code (F8); `uv run mypy src`.
12. Closeout: move durable content into the specs (F9); delete this plan file.

## Accept

- **AC1. The operator token streams a backup.** `uv run pytest -q
  tests/test_operator_backup.py -k streams_a_readable_tar` → `1 passed`: `200
  application/x-tar`, first tar member `glosswork.sqlite3`. (Not vacuous: POST is not
  absorbed by the SPA catch-all, and it asserts tar content.)
- **AC2. Every non-operator gets one identical 401 body.** `-k refusal` → the eight
  variants (absent, wrong, blank-configured, unconfigured, tenant `admin` PAT, PAT in the
  operator header, garbage `Authorization`, operator-as-Bearer) each `401`, bodies
  compared equal to each other, body is `{"error":{"code":"operator_token_refused", ...}}`,
  and no tar bytes streamed. State the expected passed count in the test.
- **AC3. A tenant `admin` PAT is refused.** Covered by AC2's variant; `-k tenant_pat` →
  `1 passed`, `401 operator_token_refused` identical to the anonymous refusal.
- **AC4. The operator token opens exactly two routes, enumerated.** `-k sweep` → the
  operator token changes the answer on exactly `GET /api/v1/usage` and `POST
  /api/v1/operator/backup`. Plus the structural call-site pin (F7) green.
- **AC5. The operator backup's audit row is identified by its request id (F5).** `-k
  audited` → exactly one `backup_taken` row whose `request_id` equals the response's
  `x-request-id` header, with `principal_id` the bootstrap id. (If OQ2 adds a marker, also
  assert `new_value.trigger == "operator_token"`.)
- **AC6. The access model is unchanged.** `uv run pytest -q tests/test_rest_scope_
  enforcement.py` green, including both allowlist-equality tests with their new entry
  (a **fence**: these are edited in this change); and `git diff --exit-code --text main --
  src/glosswork/migrations.py tests/migration_hashes.txt` exits 0 (no migration, no
  scope-constraint change).
- **AC7. The backup runs while frozen (F4).** `uv run pytest -q
  tests/test_operator_backup.py -k read_only` → operator backup answers `200` with
  `read_only=True`.
- **AC8. Nothing else regressed.** `uv run pytest -q` green;
  `uv run ruff check .` exit 0 **and** `uv run ruff format --check .` exit 0, read
  separately (F8); `uv run mypy src` exit 0; `tests/test_generated_api_types_fresh.py`
  green; `npm --prefix web run typecheck` exit 0.

## Open questions for Chris

- **OQ1 (security boundary; blocks approval).** The operator token is a path to an admin
  session on a code-sign-in hosted workspace (above). Accept the wider cost and correct
  Q63's record, or close it in the product? Options, recommendation under "Waiting on
  Chris" of the run report.
- **OQ2 (audit).** Mark an operator-triggered backup in the trail (a `trigger` field), or
  accept that it is attributed to the bootstrap service account and inferable only by
  `request_id`, and say so in DD-39?
- **OQ3 (upgrade; F10).** A self-hosted deployment that set `GW_OPERATOR_TOKEN` under
  DD-39's "no tenant content" promise finds, after upgrade, that the same secret downloads
  the whole database. Accept and document, or gate the backup behind an opt-in or a second
  token?
- **OQ4 (PLAN).** OQ1's answer changes PLAN 16.2 (Q63) and PLAN 12's cost note, which only
  Chris edits.

## Baseline repaint

Not a UI change. `web/src/api/schema.ts` is regenerated (generated types, not a baseline).

## Adversarial pass

A separate Opus session that did not write this plan ran the pass on `750ad40`: built the
plan as written in a scratch copy and ran the full backend suite (baseline 2164 passed /
3 xfailed; built 2163 passed, the one failure the `schema.ts` freshness test the plan
already names). Premises P1, P2, P3, P4, P6, P7 re-run and held; P5 held with a wording
fix (F11). No unmentioned test breaks.

- **F1 (blocker, folded).** The first draft's mitigation (control-plane encryption) does
  not mitigate this path, and its "possession of an artifact" framing was too broad.
  Rewrote "The boundary this change actually crosses": the risk is the operator-token
  holder signing in, the increment is a stolen token alone, and the control-plane claim is
  struck.
- **F2 (should-fix, folded as OQ1).** "The product cannot close this" was false. Added
  options (a) key the code hash off-database, (b) scrub-and-VACUUM, (c) encrypt the stream;
  put to Chris.
- **F3 (should-fix, folded).** Added "Waiting on Chris" to the run report and "Open
  questions for Chris" here; a plan with an open design question is not approvable.
- **F4 (should-fix, folded).** Read-only: open by construction; do NOT add to
  `READ_ONLY_OPEN_ROUTES`; added a constraint, AC7, and DD-38 / DEPLOYMENT 6a closeout
  edits.
- **F5 (should-fix, folded as OQ2).** AC5 now keys on `request_id`; the marker decision is
  OQ2.
- **F6 (should-fix, folded).** One raiser `require_operator`, a tagged dependency, the
  edge's `ActorDep`.
- **F7 (should-fix, folded).** A structural call-site pin for the operator-token check.
- **F8 (should-fix, folded).** Accept block fixed: split ruff, `git diff --exit-code`,
  mutation checklist step, body-only equality, expected `-k` counts.
- **F9 (should-fix, folded).** Closeout list widened (below).
- **F10 (should-fix, folded as OQ3).** Upgrade silently widens an existing metering token.
- **F11 (nice-to-know, folded).** P5 wording corrected.
- **F12 (nice-to-know, folded).** A snapshot failure after the eager check is still a 200;
  noted for DEPLOYMENT and CP-11 (status 200 is not evidence of a complete artifact).
- **F13 (nice-to-know, folded).** Container proof arm and `typecheck` added.
- **F14 (nice-to-know, folded).** Naming constraint for the `\boperator_token\b` grep.

## Deviations from the approved plan

None yet.

## Durable content moved out of this plan (planned; F9)

At closeout: DD-39 (operator credential also opens one backup route; the corrected cost;
whatever OQ1/OQ2 decide); DD-36 (second caller of `BackupService.stream`; F12's "200 is
not a complete artifact"); DD-38 and `docs/DEPLOYMENT.md` section 6a table (the operator
backup is open while frozen); `docs/DEPLOYMENT.md` sections 6 and 8 (the route, its
corrected cost, F10, F12); `docs/DEPLOYMENT.md:32-36` and `src/glosswork/errors.py:526`
docstring and `src/glosswork/services/usage.py:88-91` comment and `.env.example:48-58`
("no tenant content whatever" is no longer the whole story); `docs/ARCHITECTURE.md:110`;
`docs/MCP_TOOLS.md:1100-1106` and section 8 (the new REST-only operator route); PRD.md
FR-P8 and FR-P10. (`.env.example` and `config.py` edits are build steps, not closeout.)
