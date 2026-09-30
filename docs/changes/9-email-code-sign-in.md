# 9: People sign in to a hosted workspace with a one-time email code

| | |
| --- | --- |
| Issue | #9, https://github.com/glosswork/glosswork/issues/9 |
| Branch | `9-email-code-sign-in` |
| Spec | PRD.md FR-I1, FR-I3; `docs/DESIGN_DECISIONS.md` DD-9, DD-10, DD-14, DD-15, DD-38; `docs/DATA_MODEL.md` sections 2 and 9; `docs/DEPLOYMENT.md` sections 3, 5 and 6a |
| Decisions | DD-9 (sessions), DD-14 (two rate-limit windows), DD-15 (the edge fails closed), DD-38 (read-only mode); new DD-45 |
| Requirements | FR-I1 amended; new FR-I18 (email-code sign-in), FR-I19 (invites) |
| Depends on | Nothing unmerged. The hosting control plane's relay is built to the definition in this plan, not the other way round |

**This change reaches into areas that need the maintainer's judgment** (CONTRIBUTING.md,
"Working with AI coding agents"): it adds an authentication path (a security boundary), two
tables and their repositories (the storage layer), and two audit entity kinds (audit design).
It does not touch the filter compiler, the schema engine, or the access model: an invited
person gets a role exactly as an administrator-created one does today, and a code sign-in
issues the same session a password sign-in does. The design questions are DQ1 to DQ3 below,
each with a lean, for approval with the plan.

## Why

A hosted workspace's first administrator is created by `POST /api/v1/bootstrap` with a
password nobody is told, and the hosting control plane revokes the token that claim returns in
the same run. So after provisioning, the person who signed up has no way in. Nor can an
administrator invite a colleague by email: "Invite a user" creates an account whose password
the administrator must set and pass on by hand.

The fix is sign-in by a six-digit code the workspace emails to the address. The workspace keeps
the codes, the attempt counts and the sessions in its own database. It does not send email
itself: it asks the hosting control plane's relay to, with a request naming one of two
templates and typed fields, so a workspace cannot send free text. Everything here is off
unless the relay is configured, and a deployment without it behaves exactly as today. There is
no SMTP option, deliberately and for good: a self-hosted workspace keeps passwords or its own
OIDC provider.

## Premises

- **P1. No human sign-in method reaches a hosted workspace today.** `src/glosswork/routes/auth.py`
  serves `/login` (password), `/oidc/start`, `/oidc/callback`, `/modes` and `DELETE /session`,
  read in full at `aeaaf02`. `git grep -n -i relay -- src web/src` finds only two comment lines
  in `src/glosswork/envelopes.py:59-60` about an agent relaying a sentence, and
  `git grep -c -i 'email_code\|sign_in_code' -- src web/src` exits 1 (none).
- **P2. The bootstrap administrator is a `local` account with a password in the claim.** Read at
  `src/glosswork/services/bootstrap.py:142-149`: `auth_provider="local"`, `role="admin"`,
  `display_name=display_name or email.split("@", 1)[0]`. That account is the person's account
  once they sign in by code (DEC-06 in the operating plan; the hosting control plane's design
  says the same).
- **P3. Removing a person already ends their sessions and revokes their tokens in one
  transaction.** `PrincipalService.deactivate_principal` (`src/glosswork/services/principals.py:559-601`)
  sets `is_active = 0`, revokes every live `read`/`write`/`admin` token, and deletes every live
  session, then appends the audit rows, inside one `db.write()`. `DELETE /api/v1/principals/{id}`
  calls it (`src/glosswork/routes/identity.py:245-253`). Run on `aeaaf02`:
  `uv run pytest -q "tests/test_auth_routes.py::test_deactivation_kills_both_a_live_session_and_a_live_pat_on_the_next_request" "tests/test_sessions.py::test_deactivating_a_principal_revokes_its_sessions_in_the_same_transaction"`
  passed (exit 0). This clause of the issue is therefore already true in both modes; the change
  keeps those tests and adds that a removed person's outstanding code no longer signs them in.
- **P4. The credential-exempt auth routes are pinned by equality in two sets.**
  `scopes.SCOPE_EXEMPT_PATHS` (`src/glosswork/scopes.py:88-100`) and
  `middleware.AUTH_PUBLIC_PATHS` (`src/glosswork/middleware.py:190-199`). A path in the second
  runs as `anonymous_actor` at `read` scope with no session resolution and so no CSRF check
  (`middleware.py:296-310`). Two new public routes must be added to both, and the pinning tests
  updated in the same commit.
- **P5. Sign-in keeps working while the workspace is read-only, and new public routes inherit
  that.** The read-only gate is `scopes.refuse_read_only_write`, called only from the
  `require_scope` dependency path and returning early for a route that declares `read` or is
  `GET` (`src/glosswork/scopes.py:204-224`). Credential-exempt routes declare no scope, so they
  are never gated. Measured: `uv run pytest -q "tests/test_read_only_mode.py::test_sign_in_still_issues_a_session"`
  passed on `aeaaf02`. The invite routes declare `admin` and so are refused while frozen, which
  is intended.
- **P6. One login rate limiter already exists, with a per-(email, source) window and a
  per-source window, counted before any lookup.** `services/rate_limit.py`, read in full;
  `check_and_record(email, source_ip, attempt=...)` names the attempt kind in its 429, and the
  password-change route already shares it (`attempt="password change"`). It is in memory and is
  cleared by a restart.
- **P7. A restart clears in-memory limits, and hosted machines restart.** A hosted workspace
  scales to zero when idle (operating plan section 12 item 6) and every configuration change is
  a restart. So a per-address limit held only in `LoginRateLimiter` resets whenever the machine
  sleeps. The per-address code limit is therefore counted from the `sign_in_codes` table
  itself, which survives a restart; the per-source limit stays in memory, as DD-14 has it.
- **P8. An outbound HTTP client and its test seam exist.** `httpx2` is a runtime dependency
  (`pyproject.toml:50`, `httpx2==2.12.0`); `OidcFlowService` takes an injected `httpx2.Client`
  and is tested against `httpx2.MockTransport` (`src/glosswork/services/oidc_flow.py:66-68`,
  `tests/test_oidc.py:429`). No new dependency is needed.
- **P9. A background task in FastAPI runs after the response is sent, but not under
  `TestClient`.** Measured with a scratch app: under `fastapi.testclient.TestClient` a
  `BackgroundTasks` job sleeping 0.3 s ran before `post()` returned (0.39 s, job recorded).
  Under uvicorn, Starlette sends the response and then runs the tasks. So the test that the
  request answers before the relay is called runs against a real uvicorn server, not
  `TestClient`.
- **P10. The relay's field rules refuse some display names the product produces.** The control
  plane's template models (its `src/glosswork_control/email/templates.py` at its `main`,
  `b85ce3b`) refuse a name that "reads as a link", with the pattern
  `[^\W_](?:[\w-]*[^\W_])?\.[^\W\d_]{2,}`. Run here: `christopher.scheidel` and `Dr.Who` match;
  `Chris Scheidel` and `J. Smith` do not. P2's default display name is the email's local part,
  so a first administrator who signed up as `first.last@...` has a display name the relay will
  refuse as `inviter_name`. The product cannot prevent that and must survive it (see "The relay
  request", outcome `refused_fields`, and F-candidates for the control plane below).
- **P11. The relay's other rules bound the code.** Same file: a code is exactly six ASCII
  digits, `code_expires_at` is timezone-aware and at most 30 minutes after enqueue; its design
  (section 9 of its design specification) never sends a code in its last three minutes and refuses one
  with less than that left. A ten-minute code satisfies both with room for the relay's retries
  at 1 and 6 minutes.
- **P12. Migrations stop at 12 on `main`.** `src/glosswork/migrations.py:691-695`. The new
  migration takes the next free number when it is written, which is 13 unless another change
  merges first.
- **P13. `principals.auth_provider` has no CHECK constraint** (`src/glosswork/migrations.py:30-45`),
  and a `local` principal with no password already exists as a state ("cannot log in until an
  administrator sets it", `services/principals.py:343-346`). An invited person becomes exactly
  that: `local`, no password, and with codes on they sign in by code.
- **P14. `feature_disabled` is the refusal for a feature this deployment has not turned on**
  (`FeatureDisabledError`, `src/glosswork/errors.py:405-419`, 409), as `POST /api/v1/bootstrap`
  answers when `GW_BOOTSTRAP_SECRET` is unset.
- **P15. The Playwright run already starts helper processes beside the app**:
  `web/playwright.config.ts:115-178` starts `e2e/fake-idp.mjs`, the functional app server in
  `GW_AUTH_MODE=both`, and the visual server. All functional specs share the one functional
  server, which signs in by password.
- **P16. On the unfixed tree the new paths answer `401 invalid_token` as JSON, not the SPA
  page.** Measured on `aeaaf02` with `create_app(load_settings())` under `TestClient`, no
  credential: `POST /api/v1/auth/code/request` and `POST /api/v1/invites` both answered `401
  application/json` with `"code":"invalid_token"`, and `GET /api/v1/auth/modes` answered
  `{'standalone': True, 'oidc': False}`. So checklist step 1's assertions fail on behaviour, and
  every new route test asserts the body, not only a status (AGENTS.md, Traps).

## Design questions (for approval with the plan)

- **DQ1. When codes are on, is password sign-in off?** (a) Off: `/auth/login` answers
  `feature_disabled`, `/auth/modes` reports `standalone: false`, and the UI hides the password
  controls (sign-in form, "Reset password", the password field when inviting, "Change
  password"). (b) Both offered. **Lean (a).** A hosted workspace's only password is the one the
  control plane made up and nobody holds, so the password form is an attack surface with no
  user, and two ways in is two things to explain in a one-day trial. The bootstrap claim still
  takes a password, unchanged. Cost: a hosted person who wants a password cannot have one.
- **DQ2. Is an invite its own list, or a person row waiting to be activated?** (a) An `invites`
  table: no person exists until the invited address proves itself with a code, and revoking an
  invite leaves nothing behind. (b) Create the person at invite time, marked pending. **Lean
  (a).** It matches the operating plan's words ("invites are a list inside the workspace"), a
  mistyped address never appears in the people directory, `user_ref` pickers and the usage
  count of humans, and nothing that resolves principals needs to learn a new state. Cost: an
  invited person cannot be assigned work before their first sign-in.
- **DQ3. Who supplies the workspace name and the sign-in address in the email?** (a) The relay,
  from its own record of the tenant, so the request carries neither. (b) The workspace, from
  `GW_WORKSPACE_NAME` and `GW_BASE_URL`. **Lean (a).** The control plane's design already takes
  `sign_in_url` from the tenant's row "never from the request", `GW_WORKSPACE_NAME` is optional
  and not in the environment the control plane gives a workspace, and every field the request
  does not carry is one a compromised workspace cannot abuse. Cost: the control plane's design
  says the request names a workspace name; under (a), CP-18 fills it itself.

## What changes

### Configuration

Two settings, both optional, **blank counts as unset** (as `GW_BOOTSTRAP_SECRET` does, because
`.env.example` ships every variable blank):

| Variable | Meaning |
| --- | --- |
| `GW_RELAY_URL` | The absolute URL the workspace posts each message to. `https`, except a loopback host (`127.0.0.1`, `::1`, `localhost`) may be `http`, for tests and local development |
| `GW_RELAY_TOKEN` | The workspace's relay credential, sent as a bearer token. At least 32 characters |

Codes are on exactly when both are set. Startup refuses, naming the variable and never echoing a
value: one set without the other; a URL that is not absolute, not `https` off loopback, or
carries a user part, query or fragment; a token under 32 characters; either set with
`GW_AUTH_MODE=oidc` (codes need local accounts). `.env.example` gains both, blank, with a comment
that they are for a hosted workspace and that there is no other way for a workspace to send
email.

### The relay request (the definition the control plane builds to)

One request per message:

```
POST <GW_RELAY_URL>
Authorization: Bearer <GW_RELAY_TOKEN>
Content-Type: application/json
```

The product appends nothing to the URL. The hosted value is the control plane's
`https://api.glosswork.dev/v1/relay/send`. The body is one JSON object with exactly these keys,
and no others:

| Key | Type | Meaning |
| --- | --- | --- |
| `message_id` | string | A UUID (version 4, lowercase, hyphenated) the workspace makes per message. A retry of the same message carries the same id, so the relay may treat a repeat as one message |
| `template` | string | `sign_in_code` or `invite` |
| `to` | string | One email address, as the workspace stores it: trimmed and lowercased |
| `fields` | object | Exactly the template's fields below, and no others |

| Template | Field | Type and rule |
| --- | --- | --- |
| `sign_in_code` | `code` | String of exactly six ASCII digits, leading zeros kept |
| `sign_in_code` | `code_expires_at` | RFC 3339 UTC with a `Z` and whole seconds, for example `2026-09-29T15:25:00Z`. The moment the code stops working, ten minutes after it was made |
| `invite` | `inviter_name` | The inviting administrator's display name as stored, 1 to 200 characters. The relay applies its own name rules and may refuse it (outcome `refused_fields`) |

Not in the request (DQ3): the workspace's name and its sign-in address. The relay fills both
from its own record of the tenant.

What the workspace does with each answer. It never retries: a person can ask for another code,
and an administrator sees the invite outcome on screen.

| Answer | Outcome | Code request | Invite |
| --- | --- | --- | --- |
| Any `2xx` | `accepted` | Done | "Invite sent" |
| `401` or `403` | `refused_credential` | Logged at error: "the relay refused this workspace's credential" | Invite kept; "saved, but the email could not be sent" |
| `422` | `refused_fields` | Logged at error with the relay's field names, never values | Invite kept; the same, plus "your display name may be the reason" when `inviter_name` is named |
| `429` | `rate_limited` | Logged at warning | Invite kept; "the email service is busy, try again later" |
| Anything else, a connection error, or no answer in 5 s | `unavailable` | Logged at error | Invite kept; "the email could not be sent" |

No log line, audit row, error envelope or exception message carries a code, a code hash, the
relay token, or a request body.

The driver is `glosswork.services.relay`: a pure `build_relay_request(message, settings)` that
returns the method, URL, headers and body bytes, and a `RelaySender` that posts it through an
injected `httpx2.Client` and maps the answer to one outcome. The pure builder exists so the
control plane's own test (CP-18) can send exactly the requests this driver produces, by
installing this package at a pinned commit, rather than a hand-written copy of them.

### Data (migration 13, or the next free number)

```sql
CREATE TABLE sign_in_codes (
  id           TEXT PRIMARY KEY,
  email        TEXT NOT NULL,          -- trimmed, lowercased
  code_hash    TEXT NOT NULL,          -- sha256 of "<id>:<code>"
  created_at   TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  attempts     INTEGER NOT NULL DEFAULT 0,
  consumed_at  TEXT                    -- set on success, on supersession, and at the attempt cap
);
CREATE INDEX ix_sign_in_codes_email ON sign_in_codes(email, created_at);

CREATE TABLE invites (
  id            TEXT PRIMARY KEY,
  email         TEXT NOT NULL,         -- trimmed, lowercased
  display_name  TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('admin', 'creator', 'member')),
  invited_by    TEXT NOT NULL REFERENCES principals(id),
  created_at    TEXT NOT NULL,
  accepted_at   TEXT,
  principal_id  TEXT REFERENCES principals(id),  -- the person the acceptance created
  revoked_at    TEXT,
  revoked_by    TEXT REFERENCES principals(id)
);
CREATE UNIQUE INDEX ix_invites_live_email ON invites(email)
  WHERE accepted_at IS NULL AND revoked_at IS NULL;
```

Raw SQL for both stays in `repositories/sqlite.py` behind two new repository interfaces
(`SignInCodeRepository`, `InviteRepository`), as DD-2 requires.

A code hash is sha256, not a slow hash. A six-digit code has a million values, so no hash
protects it from someone holding the database for ten minutes, and that person holds every
session hash already. What protects a code is its lifetime, its attempt cap and the limits
below. Rows older than 24 hours are deleted when a new code is written, so the table stays
small without a timer.

### Sign-in by code (service `SignInCodeService`, routes in `routes/auth.py`)

Numbers are named constants in `services/sign_in_codes.py`, published by nothing because no
agent uses these routes: `CODE_LIFETIME = 10 min`, `CODE_MAX_ATTEMPTS = 5`,
`CODES_PER_ADDRESS_PER_HOUR = 5`, `CODES_PER_ADDRESS_PER_DAY = 20`, `CODE_RETENTION = 24 h`,
`RELAY_TIMEOUT_SECONDS = 5`.

**`POST /api/v1/auth/code/request {email}`**, credential-exempt.

1. Counts one attempt in the login limiter, `attempt="sign-in code"`: the per-(address, source)
   and per-source windows, before anything is looked up (P6). Over either, `429`.
2. Schedules the rest as a background task and answers **`202`**, always the same body:
   `{"message": "If this address can sign in here, a code is on its way. It works for 10 minutes."}`.
3. The background task, for every address, known or not: if the address has had
   `CODES_PER_ADDRESS_PER_HOUR` codes in the last hour or `CODES_PER_ADDRESS_PER_DAY` in the last
   day, stop. Otherwise, in one transaction, mark every live code for the address consumed
   (a new code supersedes the old), and write a new row with a fresh code from
   `secrets.randbelow(10**6)`. Then, only if the address belongs to an active `user` principal
   whose `auth_provider` is `local`, or has a live invite, send `sign_in_code` through the relay.

A row is written for every address, known or not, so what `verify` does next cannot tell the
two apart either; an unknown address's code is simply never sent. The per-address limit is
counted from the table, so it survives a restart (P7), and it applies across every source
address.

**`POST /api/v1/auth/code/verify {email, code}`**, credential-exempt.

1. Counts one attempt in the login limiter, as above. Over either window, `429`.
2. In one transaction: take the address's newest code that is unconsumed and unexpired. None,
   or a wrong code, is a failure; a wrong code adds one to `attempts` and, at
   `CODE_MAX_ATTEMPTS`, sets `consumed_at`, so a fifth wrong guess kills the code and a right
   one after it fails. A right code sets `consumed_at` (single use) and then resolves the
   person: an active `local` user principal with that address; otherwise a live invite, which
   creates the principal (`type='user'`, `auth_provider='local'`, no password, the invite's
   display name and role) and marks the invite accepted with its id. Neither, including a
   deactivated person or a revoked invite, is a failure.
3. On success, clears the pair window (as `/login` does), issues a session through
   `SessionService.issue(note="email code sign-in")`, and answers exactly as `/login` does:
   the `me` document and the two cookies.
4. Every failure is the same `401 invalid_credentials` with one message: "That code is not
   right, or it has expired. Ask for a new code."

The principal creation is attributed as OIDC provisioning is: to `anonymous_actor` at `read`
scope (DD-15), with audit rows `principal create` (note `invite accepted`) and
`invite update` (`accepted_at`). The session audit row is the new principal's.

**Codes and removal.** A code is resolved to a person when it is used, not when it is sent, so
a person removed after a code was sent is refused by step 2. Removal itself is unchanged (P3).

**Read-only.** Both routes are credential-exempt and so stay open while the workspace is
frozen (P5), including an invite's acceptance, which is the sign-in that creates the person.

### Invites (service `InviteService`, routes in `routes/identity.py`)

All three declare `admin` scope and `require_role("admin")`, and answer `feature_disabled`
(P14) when codes are off.

- `GET /api/v1/invites`: the live invites, newest first.
- `POST /api/v1/invites {email, display_name, role}`: refuses `409 conflict` when a principal
  already has the address, active or not ("already has an account here"; an inactive one is
  reactivated by an administrator, not re-invited), and when a live invite for it exists. On
  success writes the invite and its `invite create` audit row, then sends `invite` through the
  relay synchronously and answers `201` with the invite and `"email": {"outcome": ...,
  "message": ...}` from the table above. The invite stands whatever the outcome: the person can
  always go to the sign-in page and ask for a code.
- `DELETE /api/v1/invites/{id}`: sets `revoked_at` and `revoked_by`, audited. An accepted or
  already revoked invite is `409`.

### Modes, and password sign-in when codes are on (DQ1 (a))

`GET /api/v1/auth/modes` gains `email_code: bool`. With codes on, it reports `standalone: false`,
and `POST /api/v1/auth/login` answers `feature_disabled` without counting an attempt or
touching a hash. Nothing else about passwords changes on the server.

### Web UI

- **Sign-in page** (`web/src/auth/LoginPage.tsx`): with `email_code`, an email field and "Send
  code"; then a six-digit code field, "Sign in", and "Send another code", with the 202 message
  shown verbatim. No password form (DQ1). Without `email_code`, the page is unchanged.
- **People & agents** (`web/src/people/`): with `email_code`, the invite dialog takes display
  name, email and role, posts to `/api/v1/invites`, and shows the email outcome; a "Pending
  invites" table lists live invites with "Revoke"; "Reset password" is not offered. Without it,
  the dialog and table are unchanged.
- **Change password** is not offered with `email_code`.
- API types regenerated (`npm --prefix web run generate:api-types`).

### Tests and helpers

- **`tests/fake_relay.py`**: the fake relay. A small ASGI app that enforces the definition above:
  the path it is mounted at, `Authorization: Bearer <token>` equal to the one it was given,
  `Content-Type: application/json`, and the body through Pydantic models with `extra="forbid"`
  (six ASCII digits; `code_expires_at` in the `Z` form, at most 30 minutes ahead and at least
  three minutes ahead; one address; a known template). It answers `202`, or the status a test
  scripts, and records each accepted message. Runnable as a process
  (`uv run python -m tests.fake_relay --port N --token T`) for Playwright, with a test-only
  `GET /sent` that returns what it received. It is the one enforcer of the definition; there is
  no second copy in JavaScript.
- A live fixture that serves the fake relay on a loopback port in a thread, so the route tests
  configure `GW_RELAY_URL` exactly as a deployment does and need no seam in `create_app`. That is
  also what lets them run against the unfixed tree and fail on behaviour rather than on an
  import.
- **Playwright**: two more `webServer` entries, the fake relay process and a third app server in
  relay mode on its own port and data directory, with a first administrator from
  `GW_BOOTSTRAP_ADMIN_*`. One new functional spec, `web/e2e/email-code.spec.ts`, runs against
  it. The existing functional server and every existing spec are untouched.

## What does not change

- Password sign-in, OIDC, bootstrap and every existing route, with no relay configured. Tested
  (AC3).
- The bootstrap claim, which still takes and hashes a password.
- Session lifetimes (12 hours absolute, 8 idle). A hosted workspace that wants longer sets
  `GW_SESSION_*`, which is the control plane's configuration, not this change.
- Removal: `deactivate_principal` and its tests are not edited (P3).
- The access model, the filter compiler and the schema engine.
- The MCP surface: no tool, prompt, instruction or `describe_capabilities` text changes.
- Visual baselines: every visual scenario runs without the relay, so none repaints.
- No SMTP, and no sender other than the relay driver.

## Constraints

- DD-3: codes, invites and the relay call are service-layer logic; routes only parse and shape.
- DD-2: raw SQL only in `repositories/sqlite.py`.
- DD-4: every write carries an `ActorContext`.
- DD-6: a new migration, appended, with its line added to `tests/migration_hashes.txt`; no
  existing migration or manifest line edited.
- DD-15: new credential-exempt paths run as `anonymous_actor` at `read`; `grep bootstrap_actor
  src/glosswork/routes/` stays empty; both pinned path sets updated by equality.
- DD-18: every input bounded (email at most 320 characters, code exactly the six-digit string
  shape before any lookup, display name as `create_user` bounds it), and a bad input is a 4xx
  that never echoes the body.
- No code, code hash, relay token or request body in any log line, error, audit row or
  exception message.
- The relay is the only sender. No `smtplib` or any mail library.
- Every commit `Glosswork <hello@glosswork.dev>`, and the branch keeps one root commit.

## Checklist

1. Write the new backend assertions below that go through HTTP and the live fake relay, and run
   them against the unfixed tree. Record how each fails. Expected: the code and invite routes
   answer `401` (the edge refuses an unknown `/api/v1/` path before routing), `/auth/modes` has
   no `email_code`, the fake relay receives nothing, and the startup-refusal tests find
   `load_settings()` accepting what they expect refused. A test that fails on an import is
   rewritten until it fails on behaviour.
2. Migration and the two repositories, with the manifest line.
3. Settings, startup refusals, `.env.example`.
4. The relay driver (`build_relay_request`, `RelaySender`) and its outcome mapping. Its unit
   tests use `httpx2.MockTransport`; each is measured by a mutation that builds and runs (drop
   one status branch, add one body key) and recorded.
5. `SignInCodeService`, then the two routes, the pinned path sets, and `modes`.
6. `InviteService`, then its three routes.
7. Password sign-in off with codes on (DQ1).
8. The durable relay definition in `docs/DEPLOYMENT.md` (new section 5a), and the structural
   test that it names exactly the templates and fields the fake relay's models define.
9. Frontend: API types, sign-in page, People & agents, hidden password controls, with Vitest
   cases.
10. Playwright: fake relay and relay-mode servers, `email-code.spec.ts`.
11. Run the whole Accept block and record the output below.

The new backend tests, by file, each tied to the issue's clauses:

- `tests/test_sign_in_codes.py`: a known address receives a six-digit code and signs in with
  it; a code is single use; a code expires after ten minutes; a fifth wrong attempt kills the
  code and the right code after it fails; a new code supersedes the previous one; the
  per-address hour and day caps hold across source addresses and survive a restart of the app;
  requests and verifications are limited per source address (`429`); the request answer is
  byte-identical, apart from the request id, for a known, an unknown and a deactivated address;
  verify failures are identical for an unknown address, a wrong code and an expired code; the
  request answers before a fake relay that takes two seconds to reply, under uvicorn (P9); no
  code or relay token reaches any log record at `DEBUG`; sign-in works while read-only; a person
  removed after a code was sent cannot use it; an OIDC principal is sent no code.
- `tests/test_invites.py`: an administrator invites by email and role and the relay receives an
  `invite`; the invited address's first code sign-in creates the person with the invited role
  and display name and marks the invite accepted; a revoked invite lets no one in; inviting an
  address that has an account is refused, as is a second live invite; a non-administrator is
  refused; an invite the relay refuses is kept and reports the outcome; all three routes answer
  `feature_disabled` with codes off.
- `tests/test_relay_driver.py`: the driver sends exactly the documented request for each
  template, accepted by the fake relay; the fake relay refuses a request outside the definition
  (an extra key, a missing or wrong bearer, a five-digit code, an expiry more than 30 minutes or
  less than three minutes ahead, a list of recipients, an unknown template); each answer maps to
  its outcome; the settings refusals.
- `tests/test_relay_definition.py` (structural): `docs/DEPLOYMENT.md` section 5a names exactly
  the templates and fields the fake relay's models define.
- `tests/test_email_code_off.py`: with no relay, the code and invite routes answer
  `feature_disabled`, `modes` reports `email_code: false` and `standalone` as today, and no
  relay client is constructed. Password sign-in with no relay is a **fence** (the existing
  `tests/test_auth_routes.py` and `tests/test_local_accounts.py` are its coverage, unedited).
- With codes on: `/auth/login` answers `feature_disabled` (DQ1).
- Removal (P3): the two existing tests are fences; the new "removed after a code was sent"
  test is the coverage this change adds.

## Accept

Each command is run bare and its exit code read on its own.

- **AC1** `uv run pytest -q tests/test_sign_in_codes.py tests/test_invites.py tests/test_relay_driver.py tests/test_relay_definition.py tests/test_email_code_off.py` exits 0.
- **AC2** `uv run pytest -q` exits 0 (the whole backend suite, including the migration guard and
  the pinned path sets).
- **AC3** `uv run pytest -q tests/test_auth_routes.py tests/test_local_accounts.py tests/test_sessions.py tests/test_read_only_mode.py tests/test_bootstrap_handoff.py tests/test_oidc.py` exits 0, and
  `git diff --stat main -- tests/test_auth_routes.py tests/test_local_accounts.py tests/test_sessions.py` prints nothing (today's sign-in tests pass unedited).
- **AC4** `uv run pytest -q -m structural` exits 0.
- **AC5** `uv run ruff check .` exits 0; `uv run ruff format --check .` exits 0; `uv run mypy src`
  exits 0.
- **AC6** `npm --prefix web run lint` exits 0; `npm --prefix web run typecheck` exits 0;
  `npm --prefix web run test` exits 0.
- **AC7** `npm --prefix web run e2e -- --project=e2e` exits 0, including `email-code.spec.ts`.
- **AC8** `npm --prefix web run e2e -- --project=visual` exits 0 with zero baselines repainted.
- **AC9** `git grep -n -i -E 'smtplib|aiosmtplib|import smtp|SMTP_' -- src` exits 1 (no mail
  library or SMTP setting anywhere in the product).
- **AC10** `git rev-list --max-parents=0 HEAD` prints exactly `e5a047b...`, one line, and
  `git log --format='%ae %ce' main..HEAD | sort -u` prints only `hello@glosswork.dev hello@glosswork.dev`.

## Baseline repaint

Expected 0: every visual scenario runs against the server without a relay, where the sign-in page
and People & agents are unchanged.

## Adversarial pass

(To be filled by a session that did not write this plan.)

## Deviations from the approved plan

None yet.

## Durable content moved out of this plan

At closeout: FR-I1 amended and FR-I18, FR-I19 added to PRD.md; DD-45 (email-code sign-in:
hosted-only, relay-only, enumeration-safe by construction, per-address limits in the database)
in `docs/DESIGN_DECISIONS.md`; `sign_in_codes` and `invites` in `docs/DATA_MODEL.md` section 2
and the two audit entity kinds in section 9; `docs/DEPLOYMENT.md` section 5a (settings, the
relay request, outcomes) written during the build, plus a sentence in section 6a that sign-in by
code stays open while read-only.
