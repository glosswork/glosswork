# 20: The operator token can take a backup, and nothing more

| | |
| --- | --- |
| Issue | [#20](https://github.com/glosswork/glosswork/issues/20) |
| Branch | `20-operator-token-backup` |
| Spec | PRD.md FR-P8, FR-P10, FR-I18; docs/DESIGN_DECISIONS.md DD-39, DD-36, DD-45, DD-11, DD-4, DD-15, DD-38; docs/DATA_MODEL.md (`sign_in_codes`); docs/DEPLOYMENT.md sections 5a, 6, 6a and 8; docs/MCP_TOOLS.md section 8; PLAN.md section 12 (the exception), Q57, Q63 |
| Decisions | DD-39 (extended), DD-45 (the stored form of a code), DD-36, DD-11, DD-4, DD-15, DD-38 |
| Requirements | FR-P8, FR-P10, FR-I18 |
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

The first version of this plan found that Q63's recorded cost ("can read all of that
workspace's content, though it cannot write") was not true on a workspace that signs
people in by emailed code, and put four questions to Chris. He answered them on
2026-10-01 (see "Decided by Chris"). This version designs those answers in: the stored
form of a sign-in code is keyed with a secret no copy of the database carries, the
operator backup is off unless a deployment turns it on, and an operator-triggered backup
says so in the audit trail. **No design question is left open.**

**This change reaches a security boundary and an audit decision, and both are in the
list CONTRIBUTING keeps out of delegation.** They are set out under "Judgment areas"
below, and Chris approves that design in this plan before any code.

## Decided by Chris

Answered 2026-10-01, each the option this plan had recommended. These replace the "Open
questions for Chris" section of the previous version.

- **OQ1, the security boundary: "Key the code hash."** Mix a secret that is not in the
  database into how sign-in codes are stored, so no copy of the database carries a usable
  code and the operator token stays read-only. Considered and declined: scrubbing the
  codes from the backup, and accepting the wider cost.
- **OQ2, audit: "Add a marker."** The backup's audit entry says it was triggered by the
  operator token.
- **OQ3, upgrade: "Opt-in setting."** The operator backup stays off unless a deployment
  turns it on with a new setting; the hosted control plane turns it on for its tenants.
  Considered and declined: accept-and-document, and a second token.
- **OQ4, PLAN:** follows from OQ1. PLAN 16.2's Q63 row was amended by the dispatcher on
  2026-10-01. This change does not edit PLAN.

## Judgment areas this change reaches (CONTRIBUTING)

1. **The access model (PLAN 12's exception; DD-11, DD-39).** The operator token's reach
   grows from one route (the usage counts) to at most two (plus the backup, and only on a
   deployment that opted in). The design keeps this out of `access_tokens` and out of the
   three authorization axes: the operator token stays a setting the workspace cannot
   mint, list, revoke or restore, and no tenant `scope` value changes. The whole access
   model stays as it is; what changes is one more credential-exempt operator route, built
   exactly as `GET /api/v1/usage` is.

2. **Audit design (DD-4).** An operator-triggered backup has no tenant principal, yet
   `BackupService.stream` appends a `backup_taken` audit row and DD-4 forbids a write
   with no actor. It is attributed to the bootstrap service-account principal
   (`actor.anonymous_actor`), which satisfies the `audit_events` CHECK constraints and
   the foreign key with no migration (P6). That row's `auth_method` reads `pat`, which is
   not what happened, and the column cannot say anything else without a migration that
   rebuilds the audit table (P14). So the row carries a marker in `new_value` instead:
   `"trigger": "operator_token"`. **The marker is written only for an operator-triggered
   backup. An admin backup's row is byte for byte what it is today**, so no existing or
   future admin entry is made to claim anything new, and the reading rule is one
   sentence: a `backup_taken` row with `trigger` was taken with the operator credential
   and its principal and `auth_method` describe the deployment, not a person; a row
   without it was taken by the tenant credential the row names.

3. **Error copy (DD-39).** The refusal for a wrong, absent or unconfigured operator
   token, and for a right token on a deployment that has not opted in, is the existing
   `OperatorTokenRefusedError` (401 `operator_token_refused`), reused unchanged. A prober
   cannot tell the new route's states apart, and cannot tell whether the backup is turned
   on. No new error code.

4. **The stored form of a sign-in code (DD-45; a security boundary).** The code's stored
   form changes from a plain digest of `"<id>:<code>"` to a keyed one, the key being the
   deployment's relay token. This is the part of the change most likely to be wrong, so
   "The boundary this change closes" states the mechanism, the key, the upgrade and
   rotation behaviour, and what remains.

## The boundary this change closes

Q63's recorded cost is: "each workspace's stored operator token can now read all of that
workspace's content, though it cannot write." **Measured against `753af3c`, that is
wrong for a hosted workspace that signs people in by emailed code (DD-45), which is how
every hosted workspace signs people in.**

The mechanism, stated without a recipe: a backup artifact must contain the
`sign_in_codes` table so a restored deployment works. A sign-in code is a six-digit value
protected by its ten-minute lifetime, a five-attempt cap and per-address rate limits, not
by hash strength; `src/glosswork/services/sign_in_codes.py` says so in terms. The
code-request route is credential-exempt and answers for any address. So on `753af3c` the
holder of a tenant's operator token, with nothing else, could obtain an administrator
session on that workspace once the token can take a backup (P7).

**Scope of the increment (F1).** This is a property of the backup artifact, which already
holds these tables for any `admin`-token backup; the relay already receives every code in
clear (`src/glosswork/services/relay.py:84-91`); and the control-plane design already
concedes that a control-plane runtime breach reaches every workspace (the control-plane
SPEC, section 11). What the backup route would add is narrower and still real: a stolen
per-tenant operator token alone, with no relay position and no hosting-platform position,
would yield an admin session. The control plane's backup encryption does not contain it,
because the token holder sees the stream before any encryption and can ask for a fresh
code.

**How this change closes it (OQ1).** The stored form of a code becomes a keyed digest:
HMAC-SHA256 over a fixed label, the row id and the code, keyed with the deployment's
relay token (`GW_RELAY_TOKEN`). The column, its name and the table are unchanged, so
there is no migration and no change to the artifact's format (DD-36). An artifact then
holds, for each code, a value that cannot be tested against candidate codes without the
relay token, and the relay token is in no artifact the operator-token holder can obtain
(P10). After this change the operator token's reach is what Q63 recorded: read, not
write.

**Which secret keys the hash, on every kind of deployment.** There is exactly one key and
no fallback. Sign-in by code is on exactly when `GW_RELAY_URL` and `GW_RELAY_TOKEN` are
both set; a half-configured relay refuses startup, and a deployment with neither answers
`feature_disabled` on both code routes and never writes a code row (P8). So a deployment
that signs in by code always has a relay token, and a self-hosted deployment without one
has no codes to protect. The same image serves both (Q6) with no branch on "hosted". The
service refuses to be constructed with a relay and no key, and no function in `src/`
computes an unkeyed digest of a code after this change, so there is no path that stores a
code the old way.

**Why the relay token and not a new secret.** A dedicated setting was considered. It
would sit in the same place as the relay token (the tenant machine's environment), be
generated by the same control-plane run, and add a second secret to provision and rotate,
for no additional separation. The relay token is present exactly when codes are on, is
256 random bits on a hosted workspace (P9), and the control plane keeps only its SHA-256.
The label in the keyed digest keeps this use distinct from the token's use as a bearer
credential.

**Codes issued before the upgrade.** They stop verifying, and that is deliberate. A code
row written by the old image holds the unkeyed form; the new verifier computes only the
keyed form, so an old row can never match (P11). The cost is bounded by the code
lifetime: a person who asked for a code in the ten minutes before their workspace was
upgraded sees the ordinary "that code is not right, or it has expired" message once and
asks for another. No dual acceptance window exists, because accepting the old form would
keep alive exactly the rows an artifact can be used against. Old rows are deleted by the
existing 24-hour retention.

**Rotating the relay token.** Same behaviour, same bound: codes issued under the old
token stop verifying, and a new code works (P11). On a hosted workspace the token changes
only when the control plane provisions again. docs/DEPLOYMENT.md section 5a gains one
sentence saying so.

**Restoring an artifact.** A restored deployment carries the code rows of the moment the
backup was taken. They are expired by the time anyone restores, and under a different
relay token they would not verify anyway. Restore needs no new step.

**What this does not close, stated plainly.**

- **Whoever holds both the relay token and the operator token** can still do what the
  boundary section describes. Both live in the tenant machine's environment, so this is
  the hosting-platform position and the control-plane runtime position, which already
  reach every workspace (F1). The change removes the case where the operator token alone
  was enough.
- **The keying is as strong as the relay token.** `GW_RELAY_TOKEN`'s startup check is a
  length floor of 32 characters, not an entropy check. The hosted control plane generates
  43 random URL-safe characters (P9). A hand-set weak relay token would weaken the
  keying; nothing self-hosted sets one today, because self-host has no relay.
- **A self-hosted deployment that opts in and signs people in by password** hands the
  operator-token holder its Argon2id password hashes, as any backup file does. Those do
  not fall to a search the way a six-digit code does, but a weak password is a weak
  password. docs/DEPLOYMENT.md says so beside the new setting. On a hosted workspace
  password sign-in is refused while codes are on and no person holds a password (P12).

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
  `BackupService.stream(actor)` is a generator that snapshots the database, appends one
  `backup_taken` audit row through `make_event(actor, ...)` before the first tar byte,
  then walks the blob tree.

- **P4. `GET /api/v1/usage` is the pattern to copy.** Read at `src/glosswork/routes/
  usage.py`, `src/glosswork/services/usage.py`, `src/glosswork/scopes.py:91-109` and
  `src/glosswork/middleware.py:193-204`. It is credential-exempt on both allowlists
  (`SCOPE_EXEMPT_PATHS`, `AUTH_PUBLIC_PATHS`), reads `X-Operator-Token`, compares it in
  the one function `UsageService.token_matches` (`hmac.compare_digest` over sha256
  digests), and raises `OperatorTokenRefusedError` (401) for every non-operator, byte for
  byte, whether the variable is unset, blank, absent or wrong. Both allowlists are pinned
  by equality in `tests/test_rest_scope_enforcement.py`. Re-run for this version: a wrong
  token on the usage route answers `401` with body
  `{"error":{"code":"operator_token_refused","message":"This request did not carry the
  operator credential this endpoint requires.","details":{}}}`.

- **P5. A refusal must be raised before the response starts streaming, never inside the
  generator.** Established by running: a route returning `StreamingResponse(gen)` where
  `gen` raises on its first pull does not produce a clean 401. The exact symptom depends
  on the server (F11): under `TestClient(raise_server_exceptions=False)` it is a `200`
  with an empty body; the default `TestClient` raises `RuntimeError: Caught handled
  exception, but response already started`; real uvicorn sends `200` then aborts the
  chunked body. This is the class of defect PROD-02's adversarial pass caught.
  **Consequence for the design: the operator-token check and the opt-in check run in a
  dependency, before `BackupService.stream` is handed to `StreamingResponse`.**

- **P6. The bootstrap service-account actor produces a working, correctly attributed
  backup.** Established by running `BackupService.stream(anonymous_actor(...))`: a readable
  tar, and one `backup_taken` audit row attributed to the bootstrap principal
  (`00000000-0000-4000-8000-000000000001`), `service_account`, `auth_method "pat"`,
  `surface "api"`, `entity_type "backup"`. The principal exists, is active, role `admin`.
  No new principal, no migration. Re-run and confirmed by the adversarial pass. The row
  is honest about which deployment took the backup but not about what triggered it (F5),
  which is what the marker repairs.

- **P7. On `753af3c`, a database copy alone is enough to recover a live code.**
  Established by running, in a hosted (relay-configured) app, in run 1 and reproduced
  independently by the adversarial pass (0.23 s); re-run for this version (the stored form
  of a freshly issued code is reproduced from the row alone). Described under "The
  boundary this change closes". The proof script is scratch and is not committed.

- **P8. Codes are on exactly when both relay settings are set, on every path that builds
  the app.** Established by running `load_settings` with each combination: `GW_RELAY_URL`
  alone refuses startup (`GW_RELAY_TOKEN: required when GW_RELAY_URL is set`), the token
  alone refuses (`GW_RELAY_URL: required when GW_RELAY_TOKEN is set`), a blank token
  beside a URL refuses the same way, neither gives `email_codes_enabled=False`, both give
  `True`. And for the path tests use, which skips `load_settings`: `create_app(Settings(
  relay_url=...))` with no token gives `email_codes_enabled False`,
  `services.sign_in_codes.enabled False`, and `POST /api/v1/auth/code/request` answers
  `409 feature_disabled`. Read at `src/glosswork/config.py:236-238` and `:458-469`, and
  `src/glosswork/services/__init__.py` ("Built only when both relay settings are set").

- **P9. A hosted workspace's relay token is 256 random bits and the control plane stores
  only its SHA-256.** Read in the control-plane repository: its relay service mints the
  token with `secrets.token_urlsafe(32)`, and its SPEC section 9 says "only its SHA-256
  is stored, as `tenants.relay_credential_hash`". The operator token is stored there
  encrypted (SPEC section 3). So a copy of the control plane's database holds the
  operator token's ciphertext and no usable relay token. Cited as prose because that
  document is not on this branch.

- **P10. The relay token is in no artifact an operator-token holder can obtain.**
  Established by running a hosted app (fake relay, a distinctive relay token, an operator
  token) through a code request, a sign-in, two backups, a usage read, a usage refusal,
  the admin export, `/openapi.json` and `/api/v1/auth/modes`, with the process's whole
  stdout and stderr captured, then searching all eleven resulting files (both tars, the
  extracted snapshot, the live database file, each response body and the process log)
  for the token raw, as sha256 hex and raw digest, as base64 and URL-safe base64, as hex,
  as UTF-16, and for its first half: 0 hits, with a control string found in the log. The
  tar's only member on an attachment-free workspace is `glosswork.sqlite3`; the blob tree
  holds only uploaded bytes. Settings are never written to the database: its 35 tables
  hold no settings table.

- **P11. The keyed form works and behaves as stated across an upgrade and a rotation.**
  Established by patching the keyed digest over the real service in a scratch harness
  (nothing in `src/` changed): a code issued under the key signs its person in (`200`);
  no six-digit value reproduces the stored form without the key (the P7 search returns
  nothing); a live code issued before the patch is refused after it (`401`, the ordinary
  failure message, one attempt counted); a live code issued under one key is refused
  after the key changes (`401`) and a code issued under the new key signs in (`200`).

- **P12. `sign_in_codes.code_hash` is the only short secret in a hosted artifact, and a
  hosted workspace takes no password.** Established by listing the columns of the five
  credential-bearing tables in an extracted snapshot: `access_tokens.token_hash` and
  `sessions.session_hash` / `csrf_hash` are sha256 of long random secrets
  (`src/glosswork/services/tokens.py:184`, `src/glosswork/services/sessions.py:107`);
  `invites` holds no secret column; `principals.password_hash` is Argon2id and is null
  for every person a hosted workspace creates (2 principals, 0 hashes in the harness;
  invited people are created "`local` with no password"). And by running: password
  sign-in on a codes-on app answers `409 feature_disabled`
  (`src/glosswork/services/authn.py:41-53`).

- **P13. A boolean setting given a blank value refuses startup, naming the variable.**
  Established by running `load_settings` with `GW_READ_ONLY=` (blank): `ConfigError:
  Invalid configuration: GW_READ_ONLY: Input should be a valid boolean`; `true` loads;
  `maybe` refuses the same way. `.env.example` ships its booleans with explicit values
  (`GW_READ_ONLY=false`), unlike its string settings, which ship blank. The new setting
  follows the boolean precedent.

- **P14. The audit table cannot name a third `auth_method` without a migration, and
  `new_value` is free-form.** Established by running: inserting an `audit_events` row
  with `auth_method 'operator_token'` fails with `CHECK constraint failed: auth_method IN
  ('session', 'pat')`, where the same row with `pat` inserts. A `backup_taken` row whose
  `new_value` carries a third key, `"trigger": "operator_token"`, is returned intact by
  `GET /api/v1/audit-events?action=backup_taken` (`200`, the key present), so a tenant
  administrator reading the trail sees the marker with no schema or UI change. Today's
  admin row is `{"snapshot_bytes": ..., "snapshot_ms": ...}`, two keys.

## What changes

- **A new operator backup route**, in its own module `src/glosswork/routes/
  operator_backup.py` (beside `routes/usage.py`, for its reason: it resolves no tenant
  caller). `POST /api/v1/operator/backup`, reading `X-Operator-Token`, added to
  `scopes.SCOPE_EXEMPT_PATHS` and `middleware.AUTH_PUBLIC_PATHS` as the next entry in
  each. The path is distinct from `/api/v1/admin/backup` because `admin` means "a tenant
  `admin` credential" on every other route, and one path answering two unrelated
  credentials is the confusion `middleware.py` and `routes/usage.py` both warn about.
  **The route is always registered**, whether or not the deployment opted in: an
  unregistered path under `/api/` answers 404, which would tell a prober the setting's
  state.

- **One raiser per route, one comparison (F6).** `UsageService` grows
  `require_operator(presented)`, which `snapshot()` calls in place of its inline
  `token_matches`/raise, and `require_operator_backup(presented)`, which the new route
  calls through a tagged dependency in `dependencies=[...]`. Both raise the one
  `OperatorTokenRefusedError`. That makes the check eager by construction (P5), keeps
  `token_matches` the only reader of `GW_OPERATOR_TOKEN` (DD-39), and lets a
  `route_operator_credentials(app)` set be pinned by equality the way `route_capabilities`
  is (F7). The handler takes the edge's `ActorDep` (the same `anonymous_actor` the
  middleware already put on `request.state` for an `AUTH_PUBLIC_PATHS` path,
  `middleware.py:315`) and streams `BackupService.stream(actor, operator_triggered=True)`
  with the admin route's `application/x-tar` envelope and `Content-Disposition`.

- **The opt-in setting (OQ3): `GW_OPERATOR_BACKUP`**, a boolean in `config.Settings`
  (`operator_backup: bool = False`), shipped as `GW_OPERATOR_BACKUP=false` in
  `.env.example` with its cost written beside it.
  - **Unset or `false`: the route refuses every caller, the operator included**, with the
    same `401 operator_token_refused` body a wrong token gets. `require_operator_backup`
    always runs the token comparison first and then requires the setting, so the work
    done for a wrong token does not depend on the setting. No snapshot is staged and no
    audit row is written for a refused call.
  - **`true`: the operator token takes a backup.** Everyone else is refused as above.
  - **`true` with no `GW_OPERATOR_TOKEN`** refuses startup, naming `GW_OPERATOR_BACKUP`
    and never echoing a value: the caller of this route is a scheduled job, and a nightly
    401 for a reason set at boot is a diagnosis nobody can act on from there (the
    reasoning `config.py` already gives for the operator token's length check).
  - **A blank or non-boolean value** refuses startup naming the variable, exactly as
    `GW_READ_ONLY` does (P13). It never reads as on.
  - `require_operator_backup` is the only reader of the setting outside `config.py`,
    pinned structurally (below). An existing deployment that set `GW_OPERATOR_TOKEN`
    under DD-39's "no tenant content" promise keeps that promise through the upgrade
    with no action (F10).

- **The keyed code digest (OQ1).** In `src/glosswork/services/sign_in_codes.py`,
  `code_hash` takes the key as its first argument and returns
  `hmac.new(key, b"glosswork.sign-in-code.v1:" + f"{code_id}:{code}".encode(),
  hashlib.sha256).hexdigest()`. `SignInCodeService` takes `code_key: bytes | None`;
  `build_services` passes `settings.relay_token.encode("utf-8")` when
  `settings.email_codes_enabled` and `None` otherwise, on the line that builds the relay
  sender, so the two cannot disagree. The constructor raises `ValueError` when it is
  given a relay and no key, or an empty key. `_record_code` and `verify_code` use the
  key; with no key the routes have already answered `feature_disabled`, and
  `_record_code` raises rather than write a row. No unkeyed digest of a code remains in
  `src/`. The module docstring's last paragraph ("no hash protects it from someone
  holding the database") is rewritten to say what is now true. The column keeps its
  name and type: no migration.

- **The audit marker (OQ2).** `BackupService.stream` gains a keyword-only
  `operator_triggered: bool = False`. When true, `_record_audit` writes
  `"trigger": "operator_token"` as a third key in the `backup_taken` row's `new_value`,
  and the `backup_streamed` log line carries the same field. When false, both are exactly
  what they are today. The audit row is still written before the first tar byte, so a
  backup that cannot be audited streams nothing. `routes/admin_ops.py` is not edited.

- **Tests** (`tests/test_operator_backup.py`, `tests/test_sign_in_codes.py`,
  `tests/test_backup.py`, `tests/test_operator_usage.py` for the config arm): listed
  criterion by criterion under Accept.

- **Retired assertions.** `tests/relay_support.py::code_hash` (an unkeyed helper) is
  deleted, and its one use, in `test_no_log_line_carries_the_code_its_hash_or_the_relay_
  token`, is replaced by reading the stored `code_hash` column from the row. Left as it
  is, that assertion would check for a value the product no longer produces and could
  never fail.

- **Structural pins (F7):** the call sites of `require_operator`,
  `require_operator_backup` and `token_matches` equal a named set; `operator_backup` is
  read in exactly `config.py` and `services/usage.py`; `operator_triggered=True` is
  passed at exactly one call site; and `hashlib.sha256` does not appear in
  `services/sign_in_codes.py` outside the `hmac.new` call. In the
  `tests/test_one_usage_counter.py` style (syntax-tree walks where a comment could
  satisfy a text search), so "and nothing more" is guarded structurally, not only by the
  HTTP sweep.

- **The allowlist-equality tests** in `tests/test_rest_scope_enforcement.py` gain the new
  path in both pinned sets.

- **`web/src/api/schema.ts`** regenerated, because a new route changes `/openapi.json`
  (`tests/test_generated_api_types_fresh.py`), then `npm --prefix web run typecheck` (F13).

- **A container proof arm (F13),** an operator-backup case in
  `container_tests/test_operator_usage.py` or `test_backup_restore.py`, run with
  `GW_OPERATOR_BACKUP=true` and once without, because the consumer is a program outside
  the container and `TestClient` and uvicorn differ on streaming (F11, F12).

- **Specifications**, at closeout (see "Durable content" / F9).

## What does not change

- `access_tokens.scope`, the three authorization axes (DD-11), how a tenant credential
  resolves. The operator token is still not a row anyone can mint.
- `POST /api/v1/admin/backup`: unchanged, still `admin` scope and role, and
  `src/glosswork/routes/admin_ops.py` is not edited. Its audit row is unchanged.
- The artifact's contents and ordering (DD-36). `BackupService` gains one keyword and one
  optional key in its audit row; what it snapshots, stages and streams is untouched.
- `src/glosswork/migrations.py` and `tests/migration_hashes.txt`. No migration. The
  comment above migration 13 that describes `code_hash` as "the sha256 of `<id>:<code>`"
  becomes stale and stays, because existing migrations are never edited (DD-6);
  docs/DATA_MODEL.md is the specification and is corrected at closeout.
- The sign-in code's lifetime, attempt cap, per-address and per-source limits, the
  request's one answer and the verification's one failure message.
- The relay protocol and its golden requests (`tests/test_relay_definition.py`). The
  relay token is still sent only as the bearer credential to `GW_RELAY_URL`.
- A deployment with no relay settings: no code is issued or stored, exactly as before.
- A deployment that does not set `GW_OPERATOR_BACKUP`: the operator token opens exactly
  what it opens today, the usage counts.
- The filter compiler, the schema engine, indexing, search ranking, the storage
  abstraction.
- Restore. Still a documented operator procedure (DD-36), not an endpoint.

## Constraints

- **The operator-token check and the opt-in check are eager, in a dependency, never
  inside the streaming generator (P5, F6).** The Accept block proves a refused call
  answers 401 with no tar bytes streamed, no staged snapshot and no audit row, and a
  checklist mutation proves the check moved inside the generator turns a refusal test
  red (F8).
- **One credential comparison (DD-39).** `UsageService.token_matches` stays the only
  reader of `GW_OPERATOR_TOKEN`; the new route reaches it through
  `require_operator_backup`.
- **The opt-in fails closed.** Off is the default; a blank or malformed value refuses
  startup; the setting is read in one function; the comparison runs before the setting
  is consulted.
- **The refusal discloses nothing (DD-39).** One identical 401 for unset token, blank
  token, absent header, wrong token, tenant PAT, operator-as-Bearer, and for every one of
  those and the right token when the backup is off. Compared on status, body and the set
  of response header names, since `x-request-id`'s value differs (F8).
- **There is one stored form of a code and it is keyed.** No fallback to an unkeyed
  digest, no acceptance of the old form, no key other than the relay token, and no code
  row without a key.
- **The key goes nowhere new.** `services/__init__.py` hands the relay token's bytes to
  `SignInCodeService` and to nothing else. The key is never logged, never returned,
  never written to the database or the data directory, and never placed in an exception
  message (`request_code` already logs only the exception class).
- **An admin backup's audit row and log line are byte for byte today's.** The marker is
  additive and appears only on an operator-triggered backup.
- **The new route is open while frozen by construction, and must NOT be added to
  `READ_ONLY_OPEN_ROUTES` (F4).** It never reaches a scope dependency, so it is already
  open; adding it breaks `test_read_only_mode.py::test_every_open_route_is_a_route_the_
  rule_selects`. A test asserts the operator backup runs while `read_only=True`.
- **Both allowlists stay pinned by equality.** The new entry appears in the tests that
  assert them.
- **Same image (Q6).** No branch on "hosted". Every behaviour above is configuration:
  `GW_OPERATOR_TOKEN`, `GW_OPERATOR_BACKUP`, `GW_RELAY_URL`, `GW_RELAY_TOKEN`.
- **Naming (F14):** `tests/test_one_usage_counter.py` greps `\boperator_token\b` outside
  `usage.py`/`config.py`. The new module uses `x_operator_token` and no bare
  `operator_token` identifier or docstring token.

## Checklist

1. **Write the new tests first and run them against the unfixed tree**, in the order of
   the Accept block, recording how each fails: the route does not exist (the success test
   gets a 404 JSON body, not a tar); the sweep sees the operator token open only `GET
   /api/v1/usage`; the stored form of a code equals its unkeyed digest; a row in the old
   form signs its person in; `Settings` has no `operator_backup`. Record which criteria
   are fences and pass on the unfixed tree (AC6, AC16, and the admin half of AC5).
2. Add `operator_backup` to `config.Settings` and its startup check to `load_settings`;
   add `GW_OPERATOR_BACKUP=false` and its comment to `.env.example`.
3. Add `require_operator` and `require_operator_backup` to
   `src/glosswork/services/usage.py`; route `snapshot()` through `require_operator`.
4. Add `operator_triggered` to `BackupService.stream` and the marker to `_record_audit`
   and the `backup_streamed` line.
5. Add `src/glosswork/routes/operator_backup.py` with the tagged operator dependency and
   the streamed artifact; register it in `routes/__init__.py`. Use `x_operator_token`
   spelling only (F14).
6. Add `/api/v1/operator/backup` to `scopes.SCOPE_EXEMPT_PATHS` and
   `middleware.AUTH_PUBLIC_PATHS`, and to the equality tests that pin both.
7. Key the code digest: `code_hash(key, ...)`, `SignInCodeService(code_key=...)`, the
   constructor refusal, the `build_services` line, the docstring. Delete
   `tests/relay_support.py::code_hash` and repair its one caller.
8. Add the structural pins (F7).
9. **Mutation checks, each watched to fail and then reverted (F8).** Each mutation must
   import and start the app before its failure is believed.
   - The operator check moved inside the generator: AC2 red (a streamed-after-start
     error, not a clean 401).
   - The operator check removed: AC2 red (an anonymous caller gets a tar).
   - The check accepting a tenant PAT: AC4 red (a third open route).
   - The setting check removed from `require_operator_backup`: AC9 red (the right token
     gets a tar with the backup off).
   - `code_hash` returned to the unkeyed digest: AC12 and AC13 red.
   - The verifier accepting either form: AC14 red.
   - `operator_triggered=True` dropped at the route: AC5 red.
   - The marker written for every backup: AC5's admin half red.
   - The relay token written into a row by the harness: AC16 red (this is what shows the
     fence can see a leak).
10. Run `uv run pytest -q tests/test_operator_backup.py tests/test_sign_in_codes.py
    tests/test_email_code_off.py tests/test_invites.py tests/test_rest_scope_enforcement.py
    tests/test_operator_usage.py tests/test_one_usage_counter.py tests/test_backup.py
    tests/test_read_only_mode.py tests/test_api_infra.py` green.
11. Regenerate `web/src/api/schema.ts` (`npm --prefix web run generate:api-types`); confirm
    `tests/test_generated_api_types_fresh.py`; run `npm --prefix web run typecheck` (F13).
12. Add the container-proof arm (F13) and run it (needs Docker).
13. Full backend suite; `uv run ruff check .` then `uv run ruff format --check .` as two
    commands reading each exit code (F8); `uv run mypy src`.
14. Closeout: move durable content into the specs (F9); delete this plan file.

## Accept

Each `-k` selection states its expected passed count in the test module's docstring, and
the verifier compares the count the run prints with it.

- **AC1. With the backup turned on, the operator token streams a backup.** `uv run pytest
  -q tests/test_operator_backup.py -k streams_a_readable_tar`: `200 application/x-tar`,
  first tar member `glosswork.sqlite3`. (Not vacuous: POST is not absorbed by the SPA
  catch-all, and it asserts tar content.)
- **AC2. Every non-operator gets one identical 401.** `-k refusal`, with the backup on:
  the eight variants (absent, wrong, blank-configured, unconfigured, tenant `admin` PAT,
  PAT in the operator header, garbage `Authorization`, operator-as-Bearer) each `401`,
  status, body and header-name set compared equal to each other, body
  `{"error":{"code":"operator_token_refused", ...}}`, no tar bytes, no file left under
  `backup-tmp`, and no new `backup_taken` row.
- **AC3. A tenant `admin` PAT is refused.** Covered by AC2's variant; `-k tenant_pat`:
  `401 operator_token_refused` identical to the anonymous refusal.
- **AC4. The operator token opens exactly the routes it should, enumerated, in both
  modes.** `-k sweep`: with the backup on, the operator token changes the answer on
  exactly `GET /api/v1/usage` and `POST /api/v1/operator/backup`; with it off or unset,
  on exactly `GET /api/v1/usage`. The route list comes from `scopes.flatten_routes` in
  the same run, not from a constant. Plus the structural call-site pins (F7) green.
- **AC5. An operator backup is marked in the trail, and an admin backup is not.** `-k
  audited`: after one operator backup and one admin backup in one app, exactly one
  `backup_taken` row has `request_id` equal to the operator response's `x-request-id`,
  its `principal_id` is the bootstrap id and its `new_value` is exactly the keys
  `snapshot_bytes`, `snapshot_ms`, `trigger` with `trigger == "operator_token"`; the
  admin's row has `principal_id` the administrator's and `new_value` exactly the keys
  `snapshot_bytes`, `snapshot_ms`; and `GET /api/v1/audit-events?action=backup_taken`
  returns both, the marker on the first only. (The admin half is a **fence**.)
- **AC6. The access model is unchanged.** `uv run pytest -q tests/test_rest_scope_
  enforcement.py` green, including both allowlist-equality tests with their new entry
  (a **fence**: these are edited in this change); and `git diff --exit-code --text main --
  src/glosswork/migrations.py tests/migration_hashes.txt src/glosswork/routes/admin_ops.py`
  exits 0 (no migration, no scope-constraint change, the admin route untouched).
- **AC7. The backup runs while frozen (F4).** `-k read_only`: the operator backup answers
  `200` with `read_only=True` and the backup on.
- **AC8. Nothing else regressed.** `uv run pytest -q` green;
  `uv run ruff check .` exit 0 **and** `uv run ruff format --check .` exit 0, read
  separately (F8); `uv run mypy src` exit 0; `tests/test_generated_api_types_fresh.py`
  green; `npm --prefix web run typecheck` exit 0.
- **AC9. With the backup off, the right operator token is refused like a wrong one.**
  `-k opt_in`: with `operator_backup` unset, and again with it `False`, the right token
  answers `401` equal in status, body and header-name set to the wrong-token answer on
  the same app and to the wrong-token answer on an app with the backup on; no tar bytes,
  no file under `backup-tmp`, no `backup_taken` row; and the same token still reads
  `GET /api/v1/usage` (`200`).
- **AC10. The setting's startup behaviour.** `uv run pytest -q
  tests/test_operator_usage.py -k operator_backup_setting`: `GW_OPERATOR_BACKUP=true`
  with no `GW_OPERATOR_TOKEN` raises `ConfigError` naming `GW_OPERATOR_BACKUP` and
  echoing no value; a blank value raises `ConfigError` naming it; `false` and unset load
  with `operator_backup is False`; `true` beside a valid token loads with `True`.
- **AC11. An upgraded deployment that did nothing keeps its promise (F10).** `-k
  upgrade_keeps_usage_only`: an app built from settings with only `operator_token` set
  refuses the operator backup and serves the usage counts. (The same assertion as AC9's
  unset arm, named for the reader who looks for it.)
- **AC12. The stored form of a code is keyed with the relay token.** `uv run pytest -q
  tests/test_sign_in_codes.py -k stored_form_is_keyed`: for a code the fake relay
  received, the row's `code_hash` equals the keyed digest computed in the test from the
  app's relay token, and does not equal the unkeyed digest of `"<id>:<code>"`.
- **AC13. A database copy alone does not yield a code.** `-k artifact_holds_no_usable_
  code`: on an app with the backup on, request a code for an administrator, take an
  operator backup, open the snapshot from the tar, and assert that no six-digit value
  reproduces that row's stored form without the key; then assert the code the relay
  received still signs the administrator in on the live app. (On the unfixed tree the
  first half fails: a value is found.)
- **AC14. A code stored the old way never verifies.** `-k pre_upgrade_code_is_refused`:
  a live row written in the unkeyed form for a known code answers `401` with the
  ordinary failure message, its `attempts` goes up by one, and no session is issued. (On
  the unfixed tree it signs in.)
- **AC15. A code does not survive a change of relay token.** `-k
  relay_token_rotation`: a live code issued by an app with one relay token is refused
  (`401`) by an app built on the same data directory with another; a code issued by the
  second app signs in (`200`).
- **AC16. The key is in nothing a token holder can obtain** (a **fence**: true today,
  P10, and kept true). `-k relay_token_is_in_no_artifact`: after a sign-in, an operator
  backup, a usage read and a refusal on one app at `log_level="debug"`, the relay token
  (raw, sha256 hex, base64, hex) is absent from the tar bytes, every file under the data
  directory, each response body and headers, `/openapi.json`, and captured stdout and
  stderr. Checklist step 9's last mutation is what shows it can fail.
- **AC17. There is no way to build the code service without a key.** `-k
  code_service_needs_a_key`: `SignInCodeService(..., relay=<a sender>, code_key=None)`
  and `code_key=b""` each raise `ValueError`; an app with no relay settings answers
  `409 feature_disabled` on both code routes and holds zero `sign_in_codes` rows
  afterwards.
- **AC18. Container proof (F13).** `uv run pytest -q container_tests -k operator_backup`
  (needs Docker): against the built image, with `GW_OPERATOR_BACKUP=true` the operator
  token downloads a tar whose first member is `glosswork.sqlite3`; without the variable
  the same request answers `401 operator_token_refused`.

## Consequences outside this repository

Stated so they are not lost; none is done by this change.

- **CP-11** sets `GW_OPERATOR_BACKUP=true` in each tenant's environment (the control
  plane's composition of a tenant's settings), and existing tenants get it on their next
  configuration update, which is a restart of seconds.
- **CP-11's reader of the response** treats `200` as "a backup started", not "a complete
  artifact" (F12): it checks the tar's end-of-archive and the snapshot's integrity.
- **Hosted people** who asked for a code in the ten minutes before their workspace is
  upgraded to the image carrying this change ask once more.

## Baseline repaint

Not a UI change. `web/src/api/schema.ts` is regenerated (generated types, not a baseline).

## Adversarial pass

### First pass, on `750ad40` (run 1)

A separate Opus session that did not write this plan ran the pass on `750ad40`: built the
plan as written in a scratch copy and ran the full backend suite (baseline 2164 passed /
3 xfailed; built 2163 passed, the one failure the `schema.ts` freshness test the plan
already names). Premises P1, P2, P3, P4, P6, P7 re-run and held; P5 held with a wording
fix (F11). No unmentioned test breaks.

- **F1 (blocker, folded).** The first draft's mitigation (control-plane encryption) does
  not mitigate this path, and its "possession of an artifact" framing was too broad.
  Rewrote the boundary section: the risk is the operator-token holder signing in, the
  increment is a stolen token alone, and the control-plane claim is struck.
- **F2 (should-fix, folded as OQ1; decided 2026-10-01).** "The product cannot close this"
  was false. Options were (a) key the code hash off-database, (b) scrub-and-VACUUM, (c)
  encrypt the stream. Chris chose (a), now designed above.
- **F3 (should-fix, folded).** A plan with an open design question is not approvable. The
  questions were put to Chris and are answered ("Decided by Chris").
- **F4 (should-fix, folded).** Read-only: open by construction; do NOT add to
  `READ_ONLY_OPEN_ROUTES`; added a constraint, AC7, and DD-38 / DEPLOYMENT 6a closeout
  edits.
- **F5 (should-fix, folded as OQ2; decided 2026-10-01).** AC5 keys on `request_id`, and
  the marker Chris chose is now designed above.
- **F6 (should-fix, folded).** One raiser per route, a tagged dependency, the edge's
  `ActorDep`.
- **F7 (should-fix, folded).** Structural call-site pins for the operator-token check.
- **F8 (should-fix, folded).** Accept block fixed: split ruff, `git diff --exit-code`,
  mutation checklist step, body equality, expected `-k` counts.
- **F9 (should-fix, folded).** Closeout list widened (below).
- **F10 (should-fix, folded as OQ3; decided 2026-10-01).** Upgrade would silently widen an
  existing metering token. The opt-in setting Chris chose is now designed above (AC9 to
  AC11).
- **F11 (nice-to-know, folded).** P5 wording corrected.
- **F12 (nice-to-know, folded).** A snapshot failure after the eager check is still a 200;
  noted for DEPLOYMENT and CP-11 (status 200 is not evidence of a complete artifact).
- **F13 (nice-to-know, folded).** Container proof arm and `typecheck` added.
- **F14 (nice-to-know, folded).** Naming constraint for the `\boperator_token\b` grep.

### Second pass, on the amended design (run 2)

Pending: recorded here when the pass has run.

## Deviations from the approved plan

None yet.

## Durable content moved out of this plan (planned; F9)

At closeout: DD-39 (the operator credential also opens one backup route, only when
`GW_OPERATOR_BACKUP` is on; its cost as now true; the audit marker and its reading rule);
DD-45, docs/DATA_MODEL.md `sign_in_codes` and `src/glosswork/repositories/models.py`'s
`SignInCodeRow` docstring (the stored form is keyed with the relay token; old-form rows
never verify); DD-36 (second caller of `BackupService.stream`; F12's "200 is not a
complete artifact"); DD-38 and `docs/DEPLOYMENT.md` section 6a table (the operator backup
is open while frozen); `docs/DEPLOYMENT.md` section 5a (rotating the relay token ends
live codes), sections 6 and 8 (the route, the setting, what turning it on hands the
operator-token holder including password hashes on a password deployment, F12);
`docs/DEPLOYMENT.md:32-36` and `src/glosswork/errors.py:526` docstring and
`src/glosswork/services/usage.py:88-91` comment ("no tenant content whatever" holds
unless the deployment opts in); `docs/ARCHITECTURE.md:110`; `docs/MCP_TOOLS.md:1100-1106`
and section 8 (the new REST-only operator route); PRD.md FR-P8, FR-P10 and FR-I18.
(`.env.example` and `config.py` edits are build steps, not closeout.)
