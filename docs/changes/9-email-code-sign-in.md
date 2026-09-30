# 9: People sign in to a hosted workspace with a one-time email code

| | |
| --- | --- |
| Issue | #9, https://github.com/glosswork/glosswork/issues/9 |
| Branch | `9-email-code-sign-in` |
| Spec | PRD.md FR-I1, FR-I3; `docs/DESIGN_DECISIONS.md` DD-9, DD-10, DD-14, DD-15, DD-38; `docs/DATA_MODEL.md` sections 2 and 9; `docs/DEPLOYMENT.md` sections 3, 5 and 6a |
| Decisions | DD-9 (sessions), DD-14 (two rate-limit windows), DD-15 (the edge fails closed), DD-38 (read-only mode); one new decision, numbered at closeout |
| Requirements | FR-I1 amended; new FR-I18 (email-code sign-in), FR-I19 (invites) |
| Depends on | Nothing unmerged. The hosting control plane's relay is built to the definition in this plan, not the other way round |

**This change reaches into areas that need the maintainer's judgment** (CONTRIBUTING.md,
"Working with AI coding agents"): it adds an authentication path (a security boundary), two
tables and their repositories (the storage layer), and two audit entity kinds (audit design).
It does not touch the filter compiler, the schema engine, or the access model: an invited
person gets a role exactly as an administrator-created one does today, and a code sign-in
issues the same session a password sign-in does. The design questions are DQ1 to DQ5 below,
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
  passed on `aeaaf02`. The invite routes declare `admin` and so are refused while frozen,
  except revoking one, which this change opens (F9).
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
- **P8. An outbound HTTP client and its test seam exist, but only transitively at run time (F11).**
  `httpx2==2.12.0` is declared in the `dev` dependency group (`pyproject.toml:47-52`), not in
  `[project] dependencies`; the runtime image gets it through `mcp`. `OidcFlowService` already
  relies on that, taking an injected `httpx2.Client` and tested against `httpx2.MockTransport`
  (`src/glosswork/services/oidc_flow.py:66-68`, `tests/test_oidc.py:429`). This change declares
  `httpx2` as a direct runtime dependency, as `pyjwt` is for the same reason, at the version
  current on PyPI when step 4 runs (non-negotiable 1), with the `uv.lock` registry substitution
  and `THIRD_PARTY_LICENSES.md` regenerated in the same commit.
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
  refuse as `inviter_name`, and the relay also refuses a name over 80 characters after NFKC
  normalization. The product sets no display-name length bound of its own
  (`services/principals.py:1109`, `_require_text`). See DQ5 and F6.
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
  every new route test asserts the body, not only a status (AGENTS.md, Traps). The same
  measurement shows why an "identical answers" test is vacuous on its own: every case answers
  the same 401 today. So each such test also pins the expected status and body and carries a
  positive control (F3).
- **P17. Nothing can reactivate a removed person today (F5).** `grep -rn -i reactivat
  src/glosswork` finds only refusal messages; `update_principal` takes no `is_active`
  (`services/principals.py:475-484`) and `glosswork.admin` has no such command.
- **P18. A database error can carry bound parameters into a log line (F4).** The engine is made
  without `hide_parameters` (`src/glosswork/db.py:40`), and SQLAlchemy's `str()` of an
  `IntegrityError` includes `[parameters: (...)]`, measured by the adversarial pass. An
  exception raised in a background task after the response has started is not seen by
  `app.py`'s handlers and is logged by uvicorn with its traceback.
- **P19. The per-source limit is only as good as `source_ip` (F18).** Behind Fly's proxy,
  `source_ip` is the client only when the control plane sets `GW_TRUSTED_PROXY_IPS` to Fly's
  range (the control plane's own design, section 6, assigns that to CP-07). Without it every
  hosted request shares one source.
- **P20. The control plane cannot install this package as a test dependency (F10).** The
  adversarial pass ran `uv pip compile` over this package and the control plane's pins
  (`sqlalchemy==2.0.54`, `pydantic==2.13.5`, `httpx2==2.13.1`): exit 1, "glosswork==0.1.0
  depends on sqlalchemy==2.0.52 ... unsatisfiable", and it would also pull in onnxruntime and
  sqlite-vec. So the driver's requests reach CP-18 as committed files, not as an import.

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
  says the request names a workspace name; under (a), CP-18 fills it itself. The control plane
  keeps no workspace name today, only the generated slug (its design, section 3), so CP-18
  fills `workspace_name` from the slug (F13).
- **DQ4. What is accepted about someone who knows a person's address and wants them locked
  out, or wants in (F2, F19)?** After F2's fix no request supersedes the code a person is
  typing, but two levers remain while limits exist: anyone can spend an address's
  `CODES_PER_ADDRESS_PER_HOUR` and `CODES_PER_ADDRESS_PER_DAY` from many sources, after which
  that person gets no code until the window rolls off (up to a day); and five wrong guesses
  kill every live code for the address. With DQ1 (a) a hosted person has no other way in.
  Guessing odds at these numbers: 20 codes a day times 5 attempts over a million values is
  about 1 in 10,000 per targeted address per day, about 3.6% a year against one address
  attacked every day without pause, and every such attempt puts a code in the target's inbox.
  (a) Accept both, write them into the new design decision, and give a locked-out person a named recovery: an
  operator command, `python -m glosswork.admin clear-sign-in-codes --email <address>`, which
  deletes that address's code rows and so resets its per-address count, run by an operator under
  the operator-access policy (a customer asks in a ticket). (b) Drop the day cap, so a lockout
  lasts at most an hour, at about 1 in 1,700 per targeted address per day. (c) Keep password
  sign-in beside codes, reversing DQ1. **Lean (a).** A day-long lockout of one person is a
  support ticket; the guess odds are the ones worth keeping low. Cost: recovery needs a person
  at Glosswork, and the control plane has no admin endpoint for it, so it is a command run by
  hand.
- **DQ5. What happens to an invite email when the relay refuses the inviter's name (F6)?** The
  relay refuses a dotted name such as `christopher.scheidel`, and the first administrator's
  display name defaults to the email's local part (P2, P10), so most invites from a hosted
  workspace's first administrator would fail. (a) CP-18 sends the invite without the inviter's
  name when `inviter_name` is refused, from a second approved copy variant. No product change.
  (b) The product checks the name before sending and asks the administrator to change their
  display name first, which copies the relay's rules into the product. (c) The bootstrap claim
  takes a display name from the control plane. **Lean (a).** The invite always reaches the
  person and the name rules stay in one place. Cost: one more email copy variant for Chris to
  approve in the control plane. Whatever is chosen, the product handles `refused_fields` as
  written below.

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
| `message_id` | string | A UUID (version 4, lowercase, hyphenated) the workspace makes per message. A trace id, not an idempotency key: the workspace logs it when it sends (it is not secret), and the relay stores it on its outbox row so one message can be followed across both logs (F14) |
| `template` | string | `sign_in_code` or `invite` |
| `to` | string | One email address, as the workspace stores it: trimmed and lowercased, at most 254 characters, exactly one `@` with text on both sides, no whitespace, and none of `,;<>"` (F12) |
| `fields` | object | Exactly the template's fields below, and no others |

| Template | Field | Type and rule |
| --- | --- | --- |
| `sign_in_code` | `code` | String of exactly six ASCII digits, leading zeros kept |
| `sign_in_code` | `code_expires_at` | RFC 3339 UTC with a `Z` and whole seconds, for example `2026-09-29T15:25:00Z`. The moment the code stops working, ten minutes after it was made |
| `invite` | `inviter_name` | The inviting administrator's display name exactly as stored. The product sets no bound beyond non-empty; the relay refuses one over 80 characters after NFKC normalization or one that reads as a link (P10), answering `refused_fields` (DQ5) |

Not in the request (DQ3): the workspace's name and its sign-in address. The relay fills both
from its own record of the tenant.

What the relay must also hold for this to work, stated here so CP-18 builds it:

- The bearer token alone identifies the workspace. The control plane generates
  `GW_RELAY_TOKEN` with at least 32 characters, or the workspace refuses to start.
- A workspace that is frozen read-only is still accepted, because sign-in keeps working while
  frozen. Only a deleted workspace's credential is refused.
- A refusal of fields is `422` with the body
  `{"error": {"code": "refused_fields", "fields": ["<field name>", ...]}}`, field names only and
  never values. The workspace reads `fields` from exactly that shape and treats any other `4xx`
  body as unparsed (F7).
- Success is any `2xx`; the control plane may use `202`.

The per-source limit in the workspace depends on the control plane too: it is per client only
once `GW_TRUSTED_PROXY_IPS` names Fly's proxy range (P19), so that setting must be in place
before a workspace is given `GW_RELAY_URL`.

What the workspace does with each answer. It never retries: a person can ask for another code,
and an administrator sees the invite outcome on screen.

| Answer | Outcome | Code request | Invite |
| --- | --- | --- | --- |
| Any `2xx` | `accepted` | Done | "Invite sent" |
| `401` or `403` | `refused_credential` | Logged at error: "the relay refused this workspace's credential" | Invite kept; "saved, but the email could not be sent" |
| `422` | `refused_fields` | Logged at error with the relay's field names, never values | Invite kept; the same, plus "your display name may be the reason" when `inviter_name` is named |
| `429` | `rate_limited` | Logged at warning | Invite kept; "the email service is busy, try again later" |
| Anything else, a connection error, or no complete answer within 5 s in total | `unavailable` | Logged at error | Invite kept; "the email could not be sent" |

The 5 s is one deadline for the whole exchange, not httpx2's per-phase timeout (F7).

No log line, audit row, error envelope or exception message carries a code, a code hash, the
relay token, or a request body. The code-request background task catches every exception and
logs only its class and the request id, so a database error cannot put a bound code hash into
a traceback (P18, F4).

The driver is `glosswork.services.relay`: a pure `build_relay_request(message, settings)` that
returns the method, URL, headers and body bytes, and a `RelaySender` that posts it through an
injected `httpx2.Client` and maps the answer to one outcome. **Golden requests** (F10, P20): the
product commits one file per template under `docs/relay/`, each the exact request
`build_relay_request` produces for a fixed clock, id, code and token (the token a placeholder
that is plainly not a credential), and a structural test regenerates them and compares bytes.
CP-18's test sends those files, copied at a pinned commit of this repository, so what it tests
is what this driver produces rather than a hand-written copy.

### Data (migration 13, or the next free number)

```sql
CREATE TABLE sign_in_codes (
  id           TEXT PRIMARY KEY,
  email        TEXT NOT NULL,          -- trimmed, lowercased
  code_hash    TEXT NOT NULL,          -- sha256 of "<id>:<code>"
  created_at   TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  attempts     INTEGER NOT NULL DEFAULT 0,
  consumed_at  TEXT,                   -- set on success, and at the attempt cap
  sent         INTEGER NOT NULL        -- 1 when the address could sign in and a send was made
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
  principal_id  TEXT REFERENCES principals(id),  -- the person the acceptance created or reactivated
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
below. A code is compared with `hmac.compare_digest` (F19). Rows older than 24 hours are
deleted when a new code is written, so the table stays small without a timer. Rows for
addresses that cannot sign in (`sent = 0`) are written only while fewer than
`UNSENT_CODE_ROWS_PER_DAY` exist in the last day; past that ceiling an unknown address's
request writes nothing, which the requester cannot see because it happens after the answer.
An address that can sign in is never refused by this ceiling (F15).

### Sign-in by code (service `SignInCodeService`, routes in `routes/auth.py`)

Numbers are named constants in `services/sign_in_codes.py`, published by nothing because no
agent uses these routes: `CODE_LIFETIME = 10 min`, `CODE_MAX_ATTEMPTS = 5`,
`CODES_PER_ADDRESS_PER_HOUR = 5`, `CODES_PER_ADDRESS_PER_DAY = 20`, `CODE_RETENTION = 24 h`,
`UNSENT_CODE_ROWS_PER_DAY = 10000`, `INVITE_LIFETIME = 14 days`, `RELAY_TIMEOUT_SECONDS = 5`.

**`POST /api/v1/auth/code/request {email}`**, credential-exempt.

1. Counts one attempt in the login limiter, `attempt="sign-in code"`: the per-(address, source)
   and per-source windows, before anything is looked up (P6). Over either, `429`.
2. Schedules the rest as a background task and answers **`202`**, always the same body:
   `{"message": "If this address can sign in here, a code is on its way. It works for 10 minutes."}`.
3. The background task, for every address, known or not: if the address has had
   `CODES_PER_ADDRESS_PER_HOUR` codes in the last hour or `CODES_PER_ADDRESS_PER_DAY` in the last
   day, stop. Otherwise write a new row with a fresh code from `secrets.randbelow(10**6)`,
   zero-padded to six digits. **A new code does not supersede older live ones** (F2): someone
   else asking for a code for this address must not kill the code its owner is typing, and a
   code they trigger lands in the owner's inbox, where it still works. Then, only if the
   address belongs to an active `user` principal whose `auth_provider` is `local`, or has a
   live invite, send `sign_in_code` through the relay.

A row is written for every address, known or not (up to the unsent-row ceiling above), so what
`verify` does next cannot tell the two apart either; an unknown address's code is simply never
sent. The per-address limit is
counted from the table, so it survives a restart (P7), and it applies across every source
address.

**`POST /api/v1/auth/code/verify {email, code}`**, credential-exempt.

1. Counts one attempt in the login limiter, as above. Over either window, `429`.
2. The code must be a six-digit string before anything is looked up; otherwise the same
   failure as below. Then, in one transaction: compare it with every unconsumed, unexpired code
   for the address (at most `CODES_PER_ADDRESS_PER_HOUR` live at once). No match is a failure,
   and adds one to `attempts` on every live code for the address; a code at
   `CODE_MAX_ATTEMPTS` gets `consumed_at`, so five wrong guesses kill the address's live codes
   and a right code after them fails (F2). A match sets that code's `consumed_at` (single use)
   and then resolves the person **by address first, whatever their state** (F5): an active
   `local` user principal signs in; an inactive principal with that address signs in only
   through a live invite, which reactivates that same principal with the invite's role
   (audited `principal update is_active` and `role`); no principal and a live invite creates
   the principal (`type='user'`, `auth_provider='local'`, no password, the invite's display
   name and role, `created_by` the inviting administrator (F20)). Either way the invite is
   marked accepted with the principal's id. Anything else, including a revoked or expired
   invite, is a failure.

   An invite is **live** only while it is unaccepted, unrevoked, younger than
   `INVITE_LIFETIME`, and its `invited_by` is still an active principal whose role is `admin`
   (F8). All of it is checked inside this transaction, so an administrator removed or demoted
   leaves no invite that still makes people, admins included.
3. On success, clears the pair window (as `/login` does), issues a session through
   `SessionService.issue(note="email code sign-in")`, and answers exactly as `/login` does:
   the `me` document and the two cookies.
4. Every failure is the same `401 invalid_credentials` with one message: "That code is not
   right, or it has expired. Ask for a new code."

The acceptance writes are attributed as OIDC provisioning is: to `anonymous_actor` at `read`
scope (DD-15), with audit rows `principal create` or `principal update` (note `invite
accepted`) and `invite update` (`accepted_at`). The session audit row is the principal's.

**The operator's recovery command** (DQ4 (a)): `python -m glosswork.admin clear-sign-in-codes
--email <address>` deletes that address's code rows, which resets its per-address count, and
prints how many it deleted. It touches nothing else.

**Codes and removal.** A code is resolved to a person when it is used, not when it is sent, so
a person removed after a code was sent is refused by step 2. Removal itself is unchanged (P3).

**Read-only.** Both routes are credential-exempt and so stay open while the workspace is
frozen (P5), including an invite's acceptance, which is the sign-in that creates the person.
So that an administrator can still stop an invite while frozen, `DELETE
/api/v1/invites/{invite_id}` joins `scopes.READ_ONLY_OPEN_ROUTES`, beside `DELETE
/principals/{principal_id}` and for the same containment reason, and its equality pin in
`tests/test_read_only_mode.py` is updated (F9).

### Invites (service `InviteService`, routes in `routes/identity.py`)

All three declare `admin` scope and `require_role("admin")`, and answer `feature_disabled`
(P14) when codes are off.

- `GET /api/v1/invites`: the live invites, newest first.
- `POST /api/v1/invites {email, display_name, role}`: the address must pass the `to` rule in
  the relay definition, or `422 validation_failed` (F12). Refuses `409 conflict` when an
  active principal already has the address ("already has an account here") and when a live
  invite for it exists. An inactive principal's address may be invited: that is how a removed
  person comes back (P17, F5), and acceptance reactivates the same principal, so their history
  stays theirs. On
  success writes the invite and its `invite create` audit row, then sends `invite` through the
  relay synchronously and answers `201` with the invite and `"email": {"outcome": ...,
  "message": ...}` from the table above. The invite stands whatever the outcome: the person can
  always go to the sign-in page and ask for a code.
- `DELETE /api/v1/invites/{invite_id}`: sets `revoked_at` and `revoked_by`, audited. An accepted or
  already revoked invite is `409`.

### Modes, and password sign-in when codes are on (DQ1 (a))

`GET /api/v1/auth/modes` gains `email_code: bool`. With codes on, it reports `standalone: false`,
and `POST /api/v1/auth/login` answers `feature_disabled` without counting an attempt or
touching a hash. Nothing else about passwords changes on the server.

### Web UI

- **Sign-in page** (`web/src/auth/LoginPage.tsx`): with `email_code`, an email field and "Send
  code"; then a six-digit code field, "Sign in", and "Send another code", with the 202 message
  shown verbatim. No password form (DQ1). Without `email_code`, the page is unchanged. While
  `modes` is loading the page renders no form, rather than today's password form
  (`LoginPage.tsx:62` shows it while `modes === undefined`), so a code workspace never flashes
  one (F17).
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
  scripts; a scripted `422` carries exactly the `refused_fields` body defined above. It records
  each accepted message. Runnable as a process
  (`uv run python -m tests.fake_relay --port N --token T`) for Playwright, with a test-only
  `GET /sent` that returns what it received. It is the one enforcer of the definition; there is
  no second copy in JavaScript.
- A live fixture that serves the fake relay on a loopback port in a thread, so the route tests
  configure `GW_RELAY_URL` exactly as a deployment does and need no seam in `create_app`. That is
  also what lets them run against the unfixed tree and fail on behaviour rather than on an
  import.
- **Moving time and source (F16).** Expiry, the day cap and invite lifetime are tested by
  backdating `created_at` and `expires_at` rows in the test's own database, and "across source
  addresses" by `TestClient(app, client=(ip, port))` with varied addresses. No clock seam is
  added to `create_app`.
- **Playwright**: two more `webServer` entries, the fake relay process and a third app server in
  relay mode on its own port and data directory, with a first administrator from
  `GW_BOOTSTRAP_ADMIN_*` and `GW_EMBEDDING_ENABLED=false`. One new functional spec,
  `web/e2e/email-code.spec.ts`, runs against it, using a fresh address per scenario so a retry
  (`retries: 1`) never meets the per-address cap (F17). The existing functional server and
  every existing spec are untouched.

## What does not change

- Password sign-in, OIDC, bootstrap and every existing route, with no relay configured. Tested
  (AC3). The one exception is `/auth/modes`, whose answer gains `email_code: false`, so
  `tests/test_auth_routes.py::test_auth_modes_reports_each_configured_mode` (exact equality,
  `tests/test_auth_routes.py:81-98`) and the web fixtures that mock `modes`
  (`web/src/App.test.tsx`, `web/src/auth/LoginPage.test.tsx`) are amended in this change, as
  superseded assertions (F1).
- The bootstrap claim, which still takes and hashes a password.
- Session lifetimes (12 hours absolute, 8 idle). A hosted workspace that wants longer sets
  `GW_SESSION_*`, which is the control plane's configuration, not this change.
- Removal: `deactivate_principal` and its tests are not edited (P3). What removal leaves behind
  is handled at acceptance instead: an invite whose inviter is no longer an active admin is not
  live (F8).
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
- DD-18: every input bounded (an email at most 254 characters and, for an invite, the `to`
  rule; a code exactly the six-digit string shape before any lookup; a display name non-empty,
  as `create_user` requires today), and a bad input is a 4xx that never echoes the body.
- No code, code hash, relay token or request body in any log line, error, audit row or
  exception message.
- The relay is the only sender. No `smtplib` or any mail library.
- Every commit `Glosswork <hello@glosswork.dev>`, and the branch keeps one root commit.

## Checklist

1. **Done** (`aa03c78`). Write the new backend assertions below that go through HTTP and the live
   fake relay, and run them against the unfixed tree. Record how each fails. Expected: the code
   and invite routes answer `401` (the edge refuses an unknown `/api/v1/` path before routing),
   `/auth/modes` has no `email_code`, the fake relay receives nothing, and the startup-refusal
   tests find `load_settings()` accepting what they expect refused. A test that fails on an
   import is rewritten until it fails on behaviour. Recorded under "Build record" below.
2. **Done** (`41d96b3`). Migration and the two repositories, with the manifest line.
3. **Done** (`0fae5d2`). Settings, startup refusals, `.env.example`.
4. **Done** (`6022992`, `b7ebd2e`). `httpx2` declared as a runtime dependency (P8), then the relay
   driver (`build_relay_request`, `RelaySender`) and its outcome mapping. Its unit
   tests use `httpx2.MockTransport`; each is measured by a mutation that builds and runs (drop
   one status branch, add one body key) and recorded.
5. **Done** (`5e6478e`, with step 6; D2). `SignInCodeService`, then the two routes, the pinned
   path sets, and `modes`.
6. **Done** (`5e6478e`). `InviteService`, then its three routes.
7. **Done** (`f66a50d`). Password sign-in off with codes on (DQ1).
8. **Done** (`ee82c39`, `5f1c6ee`). The durable relay definition in `docs/DEPLOYMENT.md` (new
   section 5a), the golden requests under `docs/relay/`, and the structural tests: section 5a
   names exactly the templates and fields the fake relay's models define, and the golden files
   are byte-identical to what `build_relay_request` produces. Then the operator command
   `clear-sign-in-codes` (DQ4).
9. **Done** (`135ac3a`). Frontend: API types, sign-in page, People & agents, hidden password
   controls, with Vitest cases.
10. **Done** (`aa5e222`). Playwright: fake relay and relay-mode servers, `email-code.spec.ts`.
11. **Done.** Run the whole Accept block and record the output below.

The new backend tests, by file, each tied to the issue's clauses:

- `tests/test_sign_in_codes.py`: a known address receives a six-digit code and signs in with
  it; a code is single use; a code expires after ten minutes; five wrong attempts kill the
  address's live codes and the right code after them fails; a second request does not kill the
  first code, and either code signs in; the per-address hour and day caps hold across source
  addresses and survive a restart of the app; requests and verifications are limited per
  source address (`429`); sign-in works while read-only; a person removed after a code was
  sent cannot use it; `clear-sign-in-codes` resets an address's count.

  **Every "same answer" or "nothing happened" test pins the expected answer and carries a
  positive control in the same test (F3, P16)**, because on the unfixed tree every case answers
  the same `401`: the request answer is `202` with the exact message and is byte-identical,
  apart from the request id, for a known, an unknown, a deactivated and an OIDC address, and the
  fake relay received exactly one `sign_in_code` for the known local address and none for the
  other three; verify failures are `401 invalid_credentials` with the exact message for an
  unknown address, a wrong code and an expired code, beside a success for the right code; the
  request answers `202` before a fake relay that takes two seconds to reply, under uvicorn (P9),
  and the relay then receives the message; the log test first asserts a code was sent, then that
  no record at `DEBUG` carries the code, its hash or the relay token; and a forced failure of the
  background insert leaves no code hash, code or token in any record (F4).
- `tests/test_invites.py`: an administrator invites by email and role and the relay receives an
  `invite`; the invited address's first code sign-in creates the person with the invited role
  and display name and marks the invite accepted; a revoked invite lets no one in; inviting an
  address that has an active account is refused, as is a second live invite; inviting a
  removed person's address and accepting it reactivates the same principal; an invite from an
  administrator since removed or demoted, or older than 14 days, lets no one in; an invite can
  be revoked while read-only; a malformed address is `422`; a non-administrator is refused; an
  invite the relay refuses is kept and reports the outcome, naming the display name when the
  relay's `refused_fields` names `inviter_name`; all three routes answer `feature_disabled` with
  codes off.
- `tests/test_relay_driver.py`: the driver sends exactly the documented request for each
  template, accepted by the fake relay; the fake relay refuses a request outside the definition
  (an extra key, a missing or wrong bearer, a five-digit code, an expiry more than 30 minutes or
  less than three minutes ahead, a list of recipients, an unknown template); each answer maps to
  its outcome; the settings refusals.
- `tests/test_relay_definition.py` (structural): `docs/DEPLOYMENT.md` section 5a names exactly
  the templates and fields the fake relay's models define.
- `tests/test_email_code_off.py`: with no relay, the code and invite routes answer
  `feature_disabled`, `modes` reports `email_code: false` and `standalone` as today, and no
  relay client is constructed (a **fence**: no relay client exists before the change either).
  Password sign-in with no relay is a **fence** too (the existing
  `tests/test_auth_routes.py` and `tests/test_local_accounts.py` are its coverage).
- With codes on: `/auth/login` answers `feature_disabled` (DQ1).
- Removal (P3): the two existing tests are fences; the new "removed after a code was sent"
  test is the coverage this change adds.

## Build record

**Step 1, the new assertions against the unfixed tree** (`f190c1a` plus the test commit only,
`uv run pytest -q tests/test_sign_in_codes.py tests/test_invites.py tests/test_email_code_off.py
tests/test_relay_driver.py tests/test_auth_routes.py::test_auth_modes_reports_each_configured_mode`,
exit 1, 88 failed, 19 passed). Every failure is behaviour, apart from the driver's own unit tests,
which step 4 measures by mutation:

| Failure | Count | Where |
| --- | --- | --- |
| `401 invalid_token` JSON where a `202`, a `401 invalid_credentials` or a `409 feature_disabled` was expected | 18 | every code route test and `test_email_code_off.py`'s two route cases |
| `405 Method Not Allowed` on `POST /api/v1/invites` with a valid admin token (D1) | 27 | `test_invites.py`, every case that invites |
| `404 Not Found` on `DELETE /api/v1/invites/{id}` | 1 | the codes-off invite case |
| `modes` without `email_code` | 4 | the two modes tests, the failed-insert test's opening check, and the amended `test_auth_modes_reports_each_configured_mode` (F1) |
| A password sign-in answered `200` where `feature_disabled` was expected | 1 | DQ1's test |
| `load_settings()` accepted what it should refuse (`DID NOT RAISE ConfigError`) | 10 | the settings refusals |
| `Settings` has no `email_codes_enabled` | 5 | the settings acceptances, one of them the blank-is-unset fence |
| `glosswork.services.relay` cannot be imported | 22 | the driver unit tests, measured by mutation instead (below) |

The 19 that passed are the fake relay's own enforcement cases (a guard on the enforcer, which
exists before the driver does) and the labelled fence "no relay client is constructed".

**Step 4, the driver's tests measured by mutation**, each on a tree that builds and runs:

- Drop the `429` branch of `outcome_for`: `test_each_answer_maps_to_one_outcome[429-...]` fails
  (1 failed, 14 passed).
- Add a `workspace_name` key to the body: `test_the_driver_sends_exactly_the_documented_request_for_each_template`
  fails on the key list, and `test_a_scripted_refusal_from_the_live_fake_round_trips` fails with
  `unavailable`, because the live fake relay refused the request as outside the definition.

**Step 8, the structural tests measured by mutation:** deleting the `invite` row from section 5a's
template table fails `test_section_5a_names_exactly_the_templates_and_fields_the_fake_relay_enforces`,
and doubling a space in `docs/relay/invite.http` fails `test_the_golden_request_is_what_the_driver_produces[invite]`.

**Step 9, the new Vitest cases against the unfixed components** (the seven changed component and
API files restored to `f190c1a`, the new test files kept): 9 of the 10 new cases fail, and the
tenth, "is unchanged with codes off", passes by construction and is the labelled control for the
absences the others assert.

## Accept

Each command is run bare and its exit code read on its own.

- **AC1** `uv run pytest -q tests/test_sign_in_codes.py tests/test_invites.py tests/test_relay_driver.py tests/test_relay_definition.py tests/test_email_code_off.py` exits 0.
- **AC2** `uv run pytest -q` exits 0 (the whole backend suite, including the migration guard and
  the pinned path sets).
- **AC3** `uv run pytest -q tests/test_auth_routes.py tests/test_local_accounts.py tests/test_sessions.py tests/test_read_only_mode.py tests/test_bootstrap_handoff.py tests/test_oidc.py` exits 0, and
  `git diff --stat main -- tests/test_local_accounts.py tests/test_sessions.py` prints nothing,
  and `git diff main -- tests/test_auth_routes.py` changes only
  `test_auth_modes_reports_each_configured_mode` (F1).
- **AC4** `uv run pytest -q -m structural` exits 0.
- **AC5** `uv run ruff check .` exits 0; `uv run ruff format --check .` exits 0; `uv run mypy src`
  exits 0.
- **AC6** `npm --prefix web run lint` exits 0; `npm --prefix web run typecheck` exits 0;
  `npm --prefix web run test` exits 0.
- **AC7** `npm --prefix web run e2e -- --project=e2e` exits 0, including `email-code.spec.ts`.
- **AC8** `npm --prefix web run e2e -- --project=visual` exits 0 with zero baselines repainted.
- **AC9** `git grep -n -i -E 'smtplib|aiosmtplib|import smtp|SMTP_' -- src` exits 1 (no mail
  library or SMTP setting anywhere in the product). A **fence**: it is also true today.
- **AC10** `git rev-list --max-parents=0 HEAD` prints exactly `e5a047bb647709814716b7c56a71a6c97d11e266`, one line, and
  `git log --format='%ae %ce' main..HEAD | sort -u` prints only `hello@glosswork.dev hello@glosswork.dev`.

**Accept output**, run by the build session on 2026-09-29 against `aa5e222`, macOS arm64, each
command bare and its exit code read on its own:

```
AC1  exit 0   111 passed
AC2  exit 0   2159 passed, 3 xfailed
AC3  exit 0   169 passed; git diff --stat main -- tests/test_local_accounts.py tests/test_sessions.py
              prints nothing; git diff main -- tests/test_auth_routes.py is one hunk, the two
              expected dicts of test_auth_modes_reports_each_configured_mode gaining
              "email_code": False
AC4  exit 0   101 passed, 2061 deselected
AC5  ruff check exit 0; ruff format --check exit 0 (259 files already formatted); mypy src exit 0
AC6  lint exit 0; typecheck exit 0; test exit 0 (102 files, 1084 tests)
AC7  exit 0   77 passed, email-code.spec.ts's three among them
AC8  exit 0   49 passed; git status --short web/e2e prints nothing (no baseline repainted)
AC9  exit 1   (a fence: no mail library or SMTP setting in src, before or after)
AC10 one root, e5a047bb647709814716b7c56a71a6c97d11e266; identities: hello@glosswork.dev hello@glosswork.dev
```

## Baseline repaint

Expected 0: every visual scenario runs against the server without a relay, where the sign-in page
and People & agents are unchanged. Actual 0 (AC8).

## Adversarial pass

Run on 2026-09-29 by a separate Opus session that did not write the plan, against `449372c`.
It edited nothing; every finding below is folded into the plan text above.

- **F1 (blocker). AC3 contradicted the `modes` change.** `test_auth_modes_reports_each_configured_mode`
  asserts `modes` by exact equality. *Accepted:* that assertion and the web fixtures are amended
  as superseded; AC3 rewritten.
- **F2 (blocker). Anyone who knows an address could keep its owner out**, by requesting codes
  (each superseding the one being typed) or spending the per-address cap, with no fallback under
  DQ1. *Accepted:* no supersession; verify compares against every live code, and a wrong guess
  counts against all of them. The residual lockout and the guessing odds go to Chris as DQ4.
- **F3 (should-fix). The "identical answer" and "nothing sent" tests passed on the unfixed tree**,
  because every case answers the same 401 there. *Accepted:* each pins status and body and
  carries a positive control; two fences labelled.
- **F4 (should-fix). A database error in the background task could log the bound code hash**
  (P18), and the hash with its row id gives up the code. *Accepted:* the task catches and logs
  only the exception class and request id, with a test. Hiding parameters engine-wide is a
  product-wide change and not made here.
- **F5 (should-fix). A removed person could never come back**: the plan cited a reactivation
  path that does not exist (P17). *Accepted:* an inactive person's address may be invited, and
  acceptance reactivates the same principal; the person is resolved by address first, which
  also closes a uniqueness error the plan would have hit.
- **F6 (should-fix). Invite emails would fail for most hosted first administrators** (P10), and
  the "1 to 200 characters" bound was invented. *Accepted:* text corrected; the remedy is Chris's
  (DQ5).
- **F7 (should-fix). The relay's refusal body was undefined** though the product reads it.
  *Accepted:* `refused_fields` body defined, plus what the relay must hold (token identifies the
  workspace, frozen accepted, token length) and a total 5 s deadline.
- **F8 (should-fix). An invite outlived its inviter and never expired.** *Accepted:* live only
  while the inviter is an active admin and for 14 days.
- **F9 (should-fix). While frozen, an invite could be accepted but not revoked.** *Accepted:*
  revoking joins `READ_ONLY_OPEN_ROUTES`.
- **F10 (should-fix). CP-18 cannot install this package** (P20). *Accepted:* golden request
  files, checked byte for byte by a structural test.
- **F11 (should-fix). `httpx2` is a dev dependency, not a runtime one** (P8 was wrong).
  *Accepted:* declared directly, with the lockfile and licence steps.
- **F12 (minor). Invite addresses were not validated.** *Accepted:* the `to` rule, used for both.
- **F13 (minor). The control plane has no workspace name to fill in.** *Accepted:* CP-18 uses
  the slug; noted under DQ3.
- **F14 (minor). `message_id` promised deduplication nobody gives.** *Accepted:* redefined as a
  trace id.
- **F15 (minor). Rows for unknown addresses could grow storage without limit.** *Accepted:* a
  daily ceiling on unsent rows that never affects a real address.
- **F16 (minor). The expiry and day-cap tests had no way to move time or source.** *Accepted:*
  backdated rows and `TestClient(client=...)`.
- **F17 (minor). Playwright retries and the login page's loading flash.** *Accepted:* fresh
  address per scenario, embedding off on the third server, no form while `modes` loads.
- **F18 (minor). The per-source limit depends on the control plane's proxy setting** (P19).
  *Accepted:* stated in the relay definition as a precondition for turning codes on.
- **F19 (minor). The residual guessing odds were unstated.** *Accepted:* stated in DQ4, to be
  recorded in the new design decision; `hmac.compare_digest` named.
- **F20 (minor). Housekeeping.** *Accepted:* AC10 carries the full root SHA; an accepted
  invite's principal is `created_by` its inviter.

Premises the pass tried and failed to break: P2, P3, P4, P5, P9 (re-measured under uvicorn with
this app's own middleware: answer in 0.007 s, task after, keep-alive not blocked), P10, P12, P13,
P16; the audit foreign key holds under `anonymous_actor`; `create_user_in_txn` lets acceptance
run inside the verify transaction without a nested write; `BEGIN IMMEDIATE` serializes
concurrent verifies; the access model, filter compiler and schema engine are untouched.

## Deviations from the approved plan

- **D1. Step 1's invite routes did not answer `401` on the unfixed tree.** With a valid admin
  token the edge lets the request through, and with `web/dist` present locally the SPA's `GET`
  catch-all matches the path, so `POST` answered `405` and `DELETE` `404`. Both are failures on
  behaviour, which is what step 1 requires; without `web/dist` (CI) both are `404`.
- **D2. Steps 5 and 6 landed in one commit.** Verification resolves an invited address through
  `InviteService.accept_in_txn` and eligibility through `InviteService.live_invite`, so the code
  service could not be built and tested without the invite service. The order inside the commit
  followed the checklist.
- **D3. An open invite that is no longer live is revoked when its address is invited again.** The
  unique index on open invites (neither accepted nor revoked) would otherwise block an expired
  invite's address, or one whose inviter was removed, for good. The revocation is audited as
  `invite update revoked_at`, noted `superseded`. A live invite is still refused `409`.
- **D4. The `409` refusals carry a new code, `conflict`.** The plan named the status; no existing
  code meant "this already exists" (`validation_failed` is "fix the named field"). `ConflictError`
  joins `errors.py` and `STATUS_BY_CODE`; it is REST-only, so docs/MCP_TOOLS.md section 6 does not
  list it.
- **D5. `RELAY_TIMEOUT_SECONDS` lives in `services/relay.py`**, beside the deadline it bounds;
  every other named constant is in `services/sign_in_codes.py` (`INVITE_LIFETIME` in
  `services/invites.py`).
- **D6. Acceptance reactivates only a removed `local` person.** The plan said an inactive
  principal with the address is reactivated; a removed person from an identity provider (only
  possible in `both` mode) is not, because reactivating them would make an `oidc` account sign in
  by code. Their invite stays open and verification fails.
- **D7. More superseded assertions than F1 named.** Seven existing tests pin the list of
  migrations (`[1, ..., 12]`), `tests/test_one_usage_counter.py` pins the modules that make a
  constant-time comparison, and `tests/test_rest_scope_enforcement.py` pins the role-declaring
  routes (twelve, now fifteen). Each gained change 9's entry and nothing else.
- **D8. The test servers leave logging as they found it.** A uvicorn server started in-process
  reconfigures the process's `uvicorn` loggers unless `log_config=None`, which turned
  `tests/test_logs_and_host_allowlist.py` red when it ran after the new tests; and the driver's
  tests bind structlog to their own stdout, as `create_app` does for an app test.
- **D9. `POST /api/v1/invites` answers `{"invite": {...}, "email": {"outcome", "message"}}`.** The
  plan said "the invite and `email`" without fixing the nesting.
- **D10. `httpx2` moved from 2.12.0 to 2.13.1**, the version current on PyPI at step 4 (P8), and
  `httpcore2` with it. Nothing else in `uv.lock` changed (compared package by package).

## Durable content moved out of this plan

At closeout: FR-I1 amended and FR-I18, FR-I19 added to PRD.md; a new design decision (email-code sign-in:
hosted-only, relay-only, enumeration-safe by construction, per-address limits in the database)
in `docs/DESIGN_DECISIONS.md`; `sign_in_codes` and `invites` in `docs/DATA_MODEL.md` section 2
and the two audit entity kinds in section 9; the `clear-sign-in-codes` command in AGENTS.md's
operator table and `docs/DEPLOYMENT.md`; the golden requests under `docs/relay/` stay, as the
files CP-18 copies; `docs/DEPLOYMENT.md` section 5a (settings, the
relay request, outcomes) written during the build, plus a sentence in section 6a that sign-in by
code stays open while read-only.
