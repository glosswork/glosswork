# Deploying Glosswork

Everything an operator needs to run, secure, back up, and monitor a Glosswork
deployment. It describes the system as it actually ships, and the numbers in it
were measured rather than estimated; `docs/PERFORMANCE.md` carries the measurements and
the platform they were taken on.

If you are connecting an AI agent rather than running the server, read
[docs/AGENT_ONBOARDING.md](AGENT_ONBOARDING.md) instead.

## 1. What a deployment is

One container image, one process, one mounted volume (FR-P1, FR-P2). There is no
sidecar database, no cache, no queue broker, and no network egress required at runtime:
the embedding model is baked into the image at build time and is never downloaded
(DD-32).

Everything that survives a restart lives under the volume:

```
/data
├── glosswork.sqlite3        the database: schema, records, comments, audit,
│                               the FTS index and the vector index
├── glosswork.sqlite3-wal    write-ahead log (present while running)
├── glosswork.sqlite3-shm    shared memory index (present while running)
└── attachments/<xx>/<sha256>   uploaded file bytes, content addressed
```

That is the whole of the persistent state, which is what makes the backup story in
section 6 as short as it is.

**If you run this deployment for somebody else**, set `GW_OPERATOR_TOKEN` as well
(DD-39, FR-P10). It opens `GET /api/v1/usage`, which answers with aggregate counts --
records, attachment bytes, people, agent labels, and tool calls by name and error code --
and with no tenant content whatever. It is off unless the variable is set, which is why a
self-hosted deployment is unaffected and should leave it unset.

It is **not** a personal access token and cannot be minted, listed or revoked from inside
the workspace, which is the point: a workspace administrator holding an `admin` token is
refused this endpoint exactly as an anonymous caller is. It must be at least 32
characters or the container refuses to start naming the variable; blank counts as unset.
Rotation is a restart. Section 8 has what it returns and how to read it.

## 2. Running it

```bash
docker build -t glosswork .
docker volume create gw-data
docker run -d --name glosswork \
  -p 8000:8000 \
  -v gw-data:/data \
  -e GW_DATA_DIR=/data \
  -e GW_COOKIE_SECURE=true \
  -e GW_BASE_URL=https://tracker.example.com \
  -e GW_TRUSTED_PROXY_IPS=10.0.0.7 \
  glosswork
```

Released versions are published as `ghcr.io/glosswork/glosswork:X.Y.Z` and
`docker.io/glosswork/glosswork:X.Y.Z`, the same image in both registries, for
`linux/amd64` and `linux/arm64`; use one in place of `glosswork` above and skip the
build. There is no `latest` tag, so a deployment always names the version it runs, and a
published version is never replaced. `CHANGELOG.md` says what each version changed.

The image is about 496 MB, of which 133.8 MB is the embedding model. A started
container settles at roughly 264 MiB resident with the model loaded. Set
`GW_EMBEDDING_ENABLED=false` to run without semantic search; the model is still in the
image, but it is never loaded and no indexing worker starts. Keyword search continues
to work either way.

Configuration is entirely by environment variable and every variable is documented in
[`.env.example`](../.env.example), which a test keeps in step with the code: if
`config.py` reads a variable that file does not document, the suite fails.

Two endpoints exist for orchestration (FR-P4): `/healthz` says the process is up, and
**One database cannot be migrated forward and must be deleted instead.** A local `data/`
directory, or any `GW_DATA_DIR` kept across runs, that applied the *pre-release* form of
migration 6 has `schema_migrations` row 6 recorded against a `vec_embeddings` table without its
`object_type_id` and `model_id` partition keys. Migration 6 was changed in place before it was
released, so the runner considers such a database already migrated and will not fix it. Delete it
and let it rebuild. This affects development trees only: no released deployment ever applied the
earlier form, and the test, e2e and container suites create a fresh database per run.

`/readyz` returns 503 while migrations are pending. Point a readiness probe at
`/readyz` and a liveness probe at `/healthz`. Migrations run automatically at startup
and are idempotent (FR-P6), so a normal upgrade is: pull the new image, stop the old
container, start the new one on the same volume.

### Upgrading a database with old index names: one rebuild, once

**The first start on a database whose per-type indexes on `records` carry the old, key-based names
rebuilds every one of them.** Those indexes are *named* from the object type's id rather than its
key, so that two types' names cannot collide (docs/DATA_MODEL.md section 5, DD-12), and SQLite
cannot rename an index, so the upgrade drops and recreates. It happens after the migrations, in one
write transaction, and it is idempotent: the second start drops nothing, creates nothing, and logs
nothing.

Watch for `indexes_reconciled` in the startup log, which carries `dropped` and `created` counts and
the names. `index_reconcile_failed` means the pass rolled back; the deployment still serves, more
slowly, and the next start retries.

**Measured on the 200,000-record corpus** (`scripts/seed_perf.py`, arm64, 15 object types, 60
per-field indexes and 45 sort composites, every one of them carrying the old name): the first
start took **6.82 s** wall clock and logged `dropped=105 created=105`; the second took **0.07 s**
and logged nothing. So the pass itself costs about **6.8 seconds** at this size, paid once. A
deployment large enough for that to matter should expect its first container to be unready for
that long, which `/readyz` covers, because the pass runs before the process reports ready.

No query text changed and no index's *expression* changed, only the names, so the read path should
be unaffected; `docs/PERFORMANCE.md` records the re-measurement rather than asserting it.

### Reserved field keys: reported, not rewritten

The eight system pseudo-field names are reserved as **field** keys: `key`, `created_at`,
`updated_at`, `created_by`, `updated_by`, `deleted_at`, `comment_count`, `last_comment_at`. A new
field with one of those keys is refused. A database created before the reservation may
already hold one, made in good faith, and **nothing rewrites it** (DD-20): renaming a field key
would rewrite `records.data` over user data with no undo, and flipping precedence so the system
column wins would silently change every filter and saved view already reading it.

So every start scans the live schema and logs one `reserved_key_collision` warning per hit, carrying
`object_type`, `field` and a `hint`. It is non-fatal and `/readyz` still returns 200: a deployment
with a collision is degraded, not down. `reserved_key_collision_scan_failed` means the scan itself
raised; the deployment serves normally and the next start retries.

To resolve one, propose a `delete_field` for the field and re-add it under another key; the
deprecation window that proposal already has is what preserves the data while readers move over.
A suffixed key such as `created_by_name` is accepted. Until it is resolved, the collision is
visible in the product: `describe_object_type` returns the key **twice**, once under `fields` and
once under `system_fields`, with two different types, and a filter or sort on that type reads the
user field while a projection and a multi-type `search` read the system column. That disagreement
is deliberately left as it is.

## 2a. Stopping and starting

**Both `SIGINT` and `SIGTERM` are graceful.** The process handles either: it stops
accepting new connections, waits for the requests already in flight, runs the application
shutdown, and exits.

**A stop finishes the source being embedded and returns the rest to the queue.** The
indexing worker claims up to 32 sources at a time. On a stop it finishes the one it is
holding, which is a single write transaction, and puts every source it had claimed but
not started back to `pending` without charging a retry attempt. Nothing is lost and
nothing is retried that did not fail; the next start picks the queue up where it was.

**An ungraceful kill also loses no work.** A `SIGKILL` leaves the sources that were in
flight marked `running` with no process behind them. The next start returns every one of
them to the queue before the worker's first claim, whatever their age, which is why a
container that is killed and restarted seconds later resumes immediately rather than
waiting out a timeout. Each of those does cost the source one of its five attempts, so a
source that reliably kills the process becomes `failed` rather than being retried
forever; a graceful stop costs nothing.

**Give the platform at least 10 seconds of grace.** That is arithmetic, not a round
number. What has to fit inside it, in order:

| | |
| --- | --- |
| In-flight HTTP requests | **unbounded**: a long request extends the stop, and nothing here caps it |
| The usage counter's flush | 1.5 s at worst, `STOP_GRACE_SECONDS` in `src/glosswork/services/usage.py` |
| The worker's grace | 5 s, `STOP_GRACE_SECONDS` in `src/glosswork/services/embedding_worker.py` |

In-flight requests drain first because uvicorn waits for them before the application
shutdown runs at all. Of the two steps inside that shutdown, the usage counter's flush
goes **first**, deliberately: it is one transaction of a few dozen single-row upserts and
is sub-millisecond unless another writer holds the lock, and going before the worker is
what keeps it from queueing behind the worker's last write (DD-39). Measured on the
built image with a real `docker stop -t 5`, the whole stop took **0.57 s** and nothing was
lost. Measured against a flush that could not run at all, with the single writer lock held
in another transaction, the flush released the shutdown in **1.65 s** and logged
`usage_counter_stop_timed_out`. A timeout there is not a failure: what is lost is the
counts since the last 30 s interval, which is the trade DD-39 records.

The worker's 5 s covers a worst-case single source on the machine this was measured on:
64 chunks, the per-source cap, at the 43.62 ms per full chunk in
[`docs/PERFORMANCE.md`](PERFORMANCE.md). **On a machine two or three times slower that
does not fit.** The join times out, the worker logs
`embedding_worker_stop_timed_out`, the thread is abandoned mid-source, and the row is
recovered by the next start's reclaim at the cost of one attempt. That degradation is
safe and bounded, and it is the reason to ask for more than the bare 10 s if the
deployment runs on shared, slow CPU.

Defaults worth knowing: `docker stop` allows 10 seconds before `SIGKILL`, and some platforms
default to `SIGINT` and allow as little as 5 seconds. Five seconds is less than the worker's
own grace, so on such a platform a stop that lands mid-source is killed before the clean path
finishes and the kill path runs instead.

**Set the platform's stop signal to `SIGTERM` where it lets you.** Uvicorn handles both
signals the same way once, but a **second** `SIGINT` while it is shutting down sets
`force_exit` and abandons the application shutdown, so the worker's stop never runs. A
second `SIGTERM` does not. Platforms that send the signal twice, or an operator pressing
Ctrl-C twice, hit that asymmetry on `SIGINT` only.

**A clean stop's exit code is `0` or `143`, and both mean the same thing.** Uvicorn
re-raises the signal it caught after restoring the default handler. Linux discards a
default-action signal sent to PID 1 and delivers it to anything else, so the identical
clean shutdown exits `0` when the application is PID 1 and `143` when anything supervises
it: `docker run --init`, `tini`, or a platform that runs its own init. **`143` is not a
crash.** An operator who reads it as one will configure around a problem that is not
there, and a health check that treats it as one will fight scale-to-zero. What actually
says the stop was clean is the log: `Waiting for application shutdown`, then
`usage_counter_stopped`, then `embedding_worker_stopped`, then `shutdown`, then
`Finished server process`. The warnings `usage_counter_stop_timed_out` and
`embedding_worker_stop_timed_out`, each in place of the `_stopped` line above it, are the
ones to watch for. `embedding_worker_stopped` appears only when embedding is enabled,
because with it off no worker starts; `usage_counter_stopped` appears on every stop.

### Pausing is not stopping

A laptop that sleeps while Docker Desktop runs the container, or a hosting platform that
suspends a machine rather than stopping it, **pauses** the process instead of stopping it.
Nothing above runs: no signal arrives, and the process resumes exactly where it was. What
moves is the clocks. The container's monotonic clock (and its boot clock, and
`/proc/uptime`) does not advance while paused; its wall clock is set again on resume.
Measured on an Apple-silicon Mac (Docker 28.4.0, kernel 6.10.14-linuxkit): the container's
monotonic clock read 566,833 s while the Docker VM had been running for 695,246 s by the
host's clock, the difference being the host's sleeps, and the container's wall clock agreed
with the host's to within a second. A suspended Fly machine showed the same in one measurement
(2026-09-19, not repeated), and Fly says the first request after a resume may be served before
the guest's wall clock is updated.

What that does to a running workspace:

- **The indexing queue is unaffected.** The worker's idle reclaim compares wall-clock times,
  and it runs only on the worker's own thread, between batches, while that thread holds
  nothing, so a pause of any length cannot make it take back a batch it is still holding.
  A batch that straddles a pause finishes normally after it.
- **Expiry is by wall clock, so it ages across a pause**, as a person expects: sessions,
  tokens, upload tickets, sign-in codes, invites and retry backoff. In the window where the
  first request after a resume is served before the wall clock is set, an expiry that fell
  during the pause can read as not yet passed for that request, and a sign-in through an
  identity provider can be refused as "not yet valid". Both last only until the clock is
  set.
- **A login lockout does not age while paused.** The login and password-change limiters
  count on the monotonic clock, so a lockout in force when the machine paused lasts its
  full window of running time after it. That errs toward refusing, and `Retry-After` stays
  true.
- **Logged durations exclude the pause.** A batch, request, backup or export that straddles
  one logs its running time, not the time a person waited.

**The image runs one process, and `WEB_CONCURRENCY` is ignored.** The entry point passes
`workers=1` to uvicorn, which otherwise reads `WEB_CONCURRENCY` and would start a second
process on the same database. That second process would run a second indexing worker,
whose reclaim takes back rows the first still holds, and a second, separate login limiter.
One process per database is a requirement (FR-P1), and the image enforces it.

## 3. Creating the first credential

**A fresh deployment has no accounts and no tokens, and refuses every request with 401.**
That is deliberate: there is no default credential and nothing resolves to
`admin` by accident. The first administrator is created out of band, and this is the
intended bootstrap path rather than a workaround.

```bash
docker exec glosswork python -m glosswork.admin \
  create-admin --email you@example.com --password '<a real password>'
```

Then, if you want an agent or a script to reach the API, mint a personal access token.
It is printed once and cannot be recovered afterwards, only replaced:

```bash
docker exec glosswork python -m glosswork.admin \
  mint-token --name laptop-cli --scope admin
```

The operator CLI also has `set-password` and `list-principals`. `set-password`
**revokes every session and every personal access token the account holds**: a reset is the
containment action after a phished password or a stolen cookie, and one that revoked nothing
would contain nothing. Alternatively, set
`GW_BOOTSTRAP_ADMIN_EMAIL` and `GW_BOOTSTRAP_ADMIN_PASSWORD` before the first start and
the administrator is created for you; a second start with them still set is a no-op, so
they are safe to leave in a compose file.

The browser reaches both password paths: an administrator resets a person's
password from their row on `People & agents`, and anyone with a local password changes their own
from `Setup`, with the current password. Both revoke exactly what `set-password` revokes (DD-13).

Browsers authenticate with a session cookie, not a token. Agents and scripts use
`Authorization: Bearer gw_pat_...` on both the REST API and the MCP endpoint.

### Bootstrapping over HTTP

Both paths above need a command run inside the container, which a program that just
provisioned one cannot always do. Set `GW_BOOTSTRAP_SECRET` and the deployment will
exchange that secret, exactly once, for the first administrator's account, an `admin`
personal access token, and the two addresses an agent and a person need:

```bash
openssl rand -base64 32          # generate the secret; at least 32 characters
```

Set it alongside `GW_BASE_URL`, which the handoff's URLs are composed from. **Do not set
`GW_BOOTSTRAP_ADMIN_EMAIL` or `GW_BOOTSTRAP_ADMIN_PASSWORD` beside it**: that path creates
the administrator at startup, which would leave every claim refused. The deployment
refuses to start on that combination, on `GW_AUTH_MODE=oidc` (local sign-in is refused
there, so the account it creates could never sign in), on a secret under 32 characters,
and on a missing `GW_BASE_URL`. Each refusal names the variable and never prints the
secret.

Wait for `/readyz` to answer 200, then claim:

```bash
curl -sS -X POST https://tracker.example.com/api/v1/bootstrap \
  -H 'Content-Type: application/json' \
  -d '{"secret":"<GW_BOOTSTRAP_SECRET>","email":"you@example.com","password":"<a real password>"}'
```

```json
{
  "token": "gw_pat_...",
  "token_prefix": "gw_pat_x",
  "scope": "admin",
  "principal_id": "…",
  "mcp_url": "https://tracker.example.com/mcp",
  "sign_in_url": "https://tracker.example.com/login",
  "agent_label": null
}
```

The token is returned once and is stored only as a hash, exactly like one from
`mint-token`. The response carries `Cache-Control: no-store`; do not log it.

**The claim body takes an optional `agent_label`**, and the response echoes it as the
seventh name above (DD-37). A hosting operator provisioning a tenant for one named
tool mints the first token already labeled, so every call that token makes is attributed
to that label without any header, which matters because some harnesses cannot send one.
The echo is read back from the stored row rather than from the request, so a caller
learns what was kept. A label that is blank or longer than 200 characters is refused
`422 validation_failed`, and that claim rolls back: the deployment stays claimable.

**Where each address works.** The claim itself is an ordinary REST call with no `Host`
allowlist, so it may be sent to any address that reaches the container, including a
private one inside your runtime's network. `mcp_url` is different: the MCP transport
checks `Host` against `GW_BASE_URL` before reading any credential, so an agent must
connect at the address the handoff returned. Anything else is refused `421 Invalid Host
header`.

**Four refusals**, each with the code a client should branch on:

| Situation | Status | Code |
| --- | --- | --- |
| `GW_BOOTSTRAP_SECRET` is not set on this deployment | 409 | `feature_disabled` |
| The presented secret is missing or wrong | 401 | `invalid_credentials` |
| The deployment already has a user account | 409 | `bootstrap_claimed` |
| The password is shorter than `GW_PASSWORD_MIN_LENGTH` | 422 | `validation_failed` |

The secret is compared before anything else is validated, so a caller without it learns
nothing about the password policy or about whether the deployment is already
bootstrapped.

**The secret is safe to leave set.** A second claim is refused once the deployment has a
user, and that is permanent: principals are never deleted, so `create-admin`,
`GW_BOOTSTRAP_ADMIN_*`, a claim, or a first OIDC sign-in each close this endpoint for the
life of the volume.

**If a claim's response is lost** — a timeout, a proxy reset, a crash between the commit
and the reply — the deployment is bootstrapped and nobody holds the token. It cannot be
recovered over HTTP: every retry is refused `bootstrap_claimed`, which is the point, since
a path that re-issues an `admin` token after the fact is the exposure this design exists to
close. Recover by destroying and reprovisioning the container, which costs nothing on a
fresh volume, or, if it already holds data, by minting a replacement token directly
against the volume:

```bash
docker run --rm -v gw-data:/data --entrypoint python glosswork \
  -m glosswork.admin mint-token --name recovery --scope admin
```

**In `both` mode**, an OIDC identity whose email matches the account a claim created is
refused: provisioning looks a principal up by subject, and the local account already holds
that email. This is the same collision `create-admin` has always had. Use a different
email for the bootstrap administrator, or sign that person in locally.

## 3a. Sharing one object type, without sharing the deployment

**Every object type is closed to everyone but a system administrator when it is created**
(DD-11). Nobody else can read it until somebody grants them access, which is
what makes the MCP endpoint shareable: a colleague with a `read` grant on `initiative`
sees `initiative` and nothing else -- not the other types, not their records, and not
their history in `/api/v1/audit-events`.

```bash
docker exec glosswork python -m glosswork.admin \
  grant --type initiative --principal them@example.com --level read
```

`--level` is one of `none`, `read`, `write`, `admin`. `none` is an **explicit deny**: it
overrides a permissive default, which is how one person is excluded from a type that is
otherwise open. `revoke` removes the row entirely and returns them to the type's default,
which is a different thing:

```bash
docker exec glosswork python -m glosswork.admin revoke --type initiative --principal them@example.com
docker exec glosswork python -m glosswork.admin list-grants --type initiative
```

`list-grants` prints the type's `default_level` on its first line, then one line per grant
row, so an empty list under a real `default_level` is not an error -- it means every access is
still coming from the default:

```
initiative: default_level = none
  read    them@example.com
```

To open a type to everyone by default rather than person by person, set its
`default_level` (`admin` scope, `admin` level on the type):

```
PATCH /api/v1/object-types/initiative   {"default_level": "read"}
```

The same three grant operations exist over REST, for a script rather than a shell:
`GET /api/v1/object-types/{key}/grants` lists them alongside the type's `default_level`,
`PUT /api/v1/object-types/{key}/grants/{principal_id}` with body `{"level": "..."}` sets one,
and `DELETE` on the same path revokes it. All three require `admin` scope, the same as the CLI
form, and the service layer requires `admin` *level* on that type underneath -- which is what
lets a type's own administrator manage its grants without being a system administrator.

### In the browser: a permissions panel, for a type's administrator

The same three operations, plus the type's default access, have a screen: a
**Permissions** panel at the bottom of `/schema/{key}`. It shows the type's default access, one
row per explicit grant with its level and a Remove control, and a picker to grant someone new. A
grant at `none` shows as **Denied** rather than vanishing, because `none` is an explicit denial
that overrides a permissive default, which is a different fact from having no row at all.

**The panel appears for any principal holding `admin` on that object type** -- the level, not a
system role (DD-11). A `creator` that defined a type receives an `admin` grant on it in the same
transaction, so it administers that type's access in the browser like anyone else, while every
other type is refused.

The panel is not gated on the `admin` **role**, so a `creator` does not need the CLI. The
`grant` / `revoke` / `list-grants` CLI commands above still work and are the right tool for
scripted and out-of-band administration; they are not the only path.

The panel needs only a principal's id and display name, not the fields only the `admin`-gated
`GET /api/v1/principals` carries. It offers people from the directory of the next section and
names existing rows from the `principals` map on the grants response itself, which is what lets a
row for a **deactivated** colleague still show a name. `GET /api/v1/principals` still requires
the `admin` role.

### Every authenticated principal can read a name-and-email directory

`GET /api/v1/principals/directory` and the `find_principals` MCP tool are readable by **any**
authenticated principal at `read` scope, with no system role. This is worth knowing before you
deploy into an organisation with a view about internal directories, so it is stated here rather than
left to be discovered from a route table.

What it returns is fixed at five keys -- `id`, `display_name`, `email`, `type`, `is_active` -- and
nothing else. `role`, `auth_provider`, `external_id` and `description` are not in it, which is
exactly why this one route can drop the `admin` role requirement that every other `/principals*`
route keeps (DD-25). A member learns who exists and how to address them; they learn nothing about
how the deployment is administered, and the permissions panel above still needs the `admin` role
because it needs three of the withheld fields.

Two things make this a smaller disclosure than it first sounds. Display names and email addresses
are published to every reader anyway: `principals.display_name` is joined onto comment and audit
rows in the repository layer (DD-25) and rendered on every record's comment thread and audit
timeline. And withholding a directory would not protect them -- it would only make `user_ref` a
field you could fill in solely if you already knew somebody's UUID, which is what left the field
essentially unused while there was no directory.

If a deployment genuinely must not expose a directory, the lever is the deployment boundary
(who gets an account and a credential at all), not this route: there is no configuration flag that
disables it, and adding one would silently break `user_ref` name resolution on both surfaces.

Everywhere else in the browser, per-object-type access is invisible until it bites: a screen
shows only the controls your level reaches and says once, at the top, what level you hold on that
type and who can raise it (DD-42). If a grant changes while someone has a page open, their next
write is refused with the same explanation the API gives and the page corrects itself without a
reload.

### Delegating a type is two steps, not one

Handing someone ownership of an object type takes **both** of these:

```bash
docker exec glosswork python -m glosswork.admin \
  set-role --principal them@example.com --role creator
docker exec glosswork python -m glosswork.admin \
  grant --type initiative --principal them@example.com --level admin
```

The grant alone is not enough, and this is a consequence of the model rather than an
oversight (DD-11). Exercising `admin` on a type requires a credential carrying `admin`
scope, and only `creator` and `admin` principals may hold one; a `member` granted `admin`
resolves to `write` through every credential it is capable of holding. `grant` prints a
warning when you hit this.

A `creator` may define its own object types and reaches the schema routes, but **not**
`/api/v1/admin/*` or `/api/v1/principals*` -- those twelve routes require the `admin`
*role* in addition to `admin` scope, which is what keeps a whole-database export and the
principal table out of a creator's reach.

The last active administrator cannot be demoted to any role, or deactivated, so a
deployment cannot be left with nobody who can administer it.

### Troubleshooting

**"My colleague gets `forbidden` and I already granted them."** Check their role, not just
the grant. `forbidden` means the credential was strong enough and the grant was not -- if the
token itself were too weak the error would be `insufficient_scope` instead (DD-11). A
`member` granted `admin` on a type resolves to `write` through every credential a member can
hold, so `admin`-level operations on that type -- editing its schema, approving a
proposal, managing its grants -- still fail. Run `list-principals` to check the role and
`list-grants --type <key>` to check the grant; if the grant already reads `admin` and the role
reads `member`, the fix is the role, not the grant:

```bash
docker exec glosswork python -m glosswork.admin set-role --principal them@example.com --role creator
```

**"A new object type is invisible to everyone."** This is by design, not a bug: every object
type is created with `default_level = 'none'` (DD-11), closed to everyone but a system
administrator and whoever created it. Either grant the people who need it:

```bash
docker exec glosswork python -m glosswork.admin grant --type initiative --principal them@example.com --level read
```

or open the type to everyone by widening its default, as above.

**"I made someone a creator and it stopped working."** If they sign in through your identity
provider, their role is re-derived from group membership on every login, so a `set-role`
change lasts until their next sign-in (section 5). `set-role` warns you about this at the
time. The durable fix is to put them in a group named in `GW_OIDC_CREATOR_GROUPS`; check
`list-principals` to confirm what their role is now.

## 4. Behind a reverse proxy

Glosswork expects to sit behind a TLS-terminating reverse proxy on an internal
network (FR-P7). Two settings have to agree with your proxy, and the failure mode when
they do not is silent, which is why they are configuration rather than something the
application infers.

`GW_COOKIE_SECURE` (default `true`) controls whether the session cookie carries
`Secure`. It is **never** inferred from the inbound request's scheme. Behind a proxy
that terminates TLS, the application sees plain HTTP, so an inferred value would ship
session cookies without `Secure` in production with nothing failing anywhere. Leave it
`true` in any real deployment; set it to `false` only when serving plain HTTP on
localhost for development.

`GW_TRUSTED_PROXY_IPS` (default `127.0.0.1`) is the list of addresses uvicorn will
believe `X-Forwarded-Proto` and `X-Forwarded-For` from. **The default trusts only
loopback, and a proxy in a different container or on a different host is not loopback.**
If you leave it at the default, forwarded headers are discarded: the access log
attributes every request to the proxy rather than to the real client, the OIDC flow and
the login rate limiter both see one source address for everyone, and the scheme reads
`http`. Set it to your proxy's actual address.

That last consequence matters more because of the **per-source** login window
(`GW_LOGIN_IP_MAX_ATTEMPTS`, section 5). With the proxy untrusted, every login attempt in the
deployment shares one budget of 60 per five minutes, so a busy morning can throttle people who did
nothing wrong. The window is on regardless, and its default is deliberately generous for exactly
this reason: sharing a budget is fail-closed, where taking `X-Forwarded-For` from an untrusted
client would let anyone pick their own bucket by forging a header, which is a limiter that limits
nothing. Name your proxy here and the budget applies per real client.

**Glosswork occupies the root of a hostname or port. Sub-path deployment is not
supported** and this is a recorded decision, not an oversight (DD-40): there is no
`root_path` handling, no `X-Forwarded-Prefix` support, no Vite `base`, and the built
frontend references `/assets/...` absolutely. Serve it at `https://tracker.example.com/`,
not at `https://intranet.example.com/tracker/`.

A minimal nginx server block:

```nginx
server {
    listen 443 ssl;
    server_name tracker.example.com;

    # The largest of the three in-app body caps: GW_MAX_REQUEST_BYTES is
    # 4 MiB, and the attachment upload and the CSV import are exempt from it at 25 MiB
    # each. Set this to the largest cap you actually allow. nginx's own default is 1 MiB,
    # which would silently refuse every attachment over that with its own 413.
    client_max_body_size 25m;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        # Backups and exports stream artifacts on the order of the database size.
        proxy_buffering    off;
        proxy_read_timeout 600s;
    }
}
```

**`client_max_body_size` is not optional, and the reason is a risk the application
accepts rather than closes.** The two routes exempt from `GW_MAX_REQUEST_BYTES` enforce their own
ceilings — `GW_MAX_ATTACHMENT_BYTES` and `GW_MAX_CSV_IMPORT_BYTES` — and each route reads
only `cap + 1` bytes, so neither can exhaust memory. But Starlette spools a multipart
upload to the container's temporary directory *before* the route's bounded read runs. An
authenticated `write` caller can therefore still write unbounded bytes to that disk, and no
in-app cap can stop it, because the bytes have already landed by the time any application
code sees them. The proxy directive is the control. This is a risk accepted for a trusted
authenticated principal, not an
oversight — set the directive and it does not arise.

If you enable the MCP endpoint's `Host` allowlist (`GW_MCP_ALLOWED_HOSTS`, plus the host
implied by `GW_BASE_URL`), the proxy must pass `Host` through unchanged, as above.
Leaving both unset keeps the check off, which is the shipped default.

**The `Origin` allowlist is derived from the same two settings** (DD-15), so there is
one thing to configure and enabling the host check cannot leave the origin check misconfigured.
The scheme is derived rather than assumed: `GW_BASE_URL` contributes its own scheme, so a
deployment served over plain HTTP works, and each `GW_MCP_ALLOWED_HOSTS` entry contributes both
`https://` and `http://` forms, since it carries no scheme of its own. A `:*` port wildcard is
preserved. An origin list left empty would refuse a request carrying the deployment's *own*
`Origin` with 403 once the hardening is on; a request with no `Origin` header — every non-browser
client — is accepted either way.

**`GET /mcp` is refused with 405.** That verb is the SSE stream, which this stateless
deployment never serves; an unauthenticated GET would otherwise hold a connection open
indefinitely.
A global uvicorn `limit_concurrency` is *not* set here: removing the free stream removed the
unbounded axis, and a concurrency cap is a tuning question for your own deployment rather than a
value this project should pick for you. It is worth setting if you front the deployment with
nothing else that caps connections.

## 5. Authentication modes

`GW_AUTH_MODE` is `standalone` (local password accounts), `oidc`, or `both`. In `oidc`
or `both` mode, `GW_OIDC_ISSUER`, `GW_OIDC_CLIENT_ID` and `GW_BASE_URL` are required and
startup fails fast naming whichever is missing. The OIDC `redirect_uri` is built from
`GW_BASE_URL` and never from the inbound request, so it must match what you registered
with the provider exactly.

**`GW_BASE_URL` has a second consumer (DD-29)**, and it is worth setting even in `standalone`
mode. It shapes the `download_url` every attachment document carries -- on
`GET /api/v1/attachments/{id}`, on the upload response, on `get_record include=attachments`, and
on the `get_attachment` MCP tool. With it set, that key is the absolute
`{GW_BASE_URL}/api/v1/attachments/{id}/download`, which an agent holding a bearer token can fetch
directly. With it unset, the key is the bare path, because the setting is optional outside OIDC
mode and a path a caller can join to the host it already connected to is more useful than a
refusal. Nothing breaks either way; agents just have one more step. The value is also published
to agents as `attachments.download_url_pattern` in `describe_capabilities`.

**Upload tickets (DD-16) make it worth setting in every deployment, and startup says so.** An
agent connected over MCP cannot see the bearer token its client authenticates with, so it cannot
upload a file on its own; `create_attachment_upload` mints it a single-use credential and an
absolute `upload_url`, derived from `GW_BASE_URL` **only** and never from a request's `Host` --
a poisoned header telling an agent where to POST a file is worse than a refusal. With the setting
unset, that tool refuses outright naming the variable, because a relative URL is useless to a model
that cannot see this deployment's origin either, and the lifespan logs one `base_url_unset`
warning. It blocks nothing and fails no probe: the browser UI is entirely unaffected, since the
SPA joins relative paths to the origin it loaded from. `GW_UPLOAD_TICKET_TTL_SECONDS` (default
300) is how long such a credential lives; it is the largest part of that credential's blast
radius, so lengthen it only deliberately.

`principals.role` is `admin | creator | member` (DD-11), and it governs more
than login -- it is one of the three axes access is computed from (section 3a). In operator
terms:

- **`admin`** administers the deployment. It reaches every `/api/v1/admin/*` and
  `/api/v1/principals*` route (section 3a's twelve) and is implicitly `admin` on every
  object type, with no grant row needed.
- **`creator`** may define new object types (`POST /api/v1/object-types`) and, once granted
  `admin` on a type (section 3a), edit its schema and approve or reject proposals against
  it. It does **not** reach `/admin/*` or `/principals*` -- `require_role` keeps a
  whole-database export and the principal table out of its reach even though it needs
  `admin` *scope* to reach the schema routes at all.
- **`member`** may neither define nor edit an object type's schema and reaches no
  `/admin/*` or `/principals*` route. What it may read or write on the records of a given
  type is entirely the grant on that type (section 3a) or the type's `default_level`.

Group-to-role mapping is two variables against the one claim named by
`GW_OIDC_GROUP_CLAIM`: `GW_OIDC_ADMIN_GROUPS` maps to `admin`, and `GW_OIDC_CREATOR_GROUPS`
maps to `creator`. Both are comma-separated, both drop blank entries, and an
unset or empty one maps **nobody** to that role rather than everybody. Everything not matched
by either is `member` (FR-I2).

```
GW_OIDC_ADMIN_GROUPS=glosswork-admins
GW_OIDC_CREATOR_GROUPS=glosswork-creators,project-leads
```

**An identity in both an admin group and a creator group is an `admin`.** Roles are ordered
`member < creator < admin`, so the higher wins; adding someone to a creator group can never
quietly demote an administrator.

Role is re-read from the current claims on **every** login. That is what makes the identity
provider authoritative: removing someone from the admin group demotes them the next time they
sign in, removing them from a creator group does the same, and deactivating a principal kills
their sessions and their tokens on their next request.

**This is why a group, not `set-role`, is how you give an OIDC principal a role.**
`set-role --role creator` works, but it lasts only until that person's next sign-in, when the
groups overwrite it -- so the CLI warns you when the principal you are changing signs in
through the provider. Put them in a group named in `GW_OIDC_CREATOR_GROUPS` instead and the
role is durable, revocable by IT through the normal offboarding process, and auditable on the
provider's side. `set-role` remains the right tool for local accounts and service accounts,
which never pass through this mapping.

Login is rate limited on **two** windows over the same `GW_LOGIN_WINDOW_SECONDS` (default 300).
`GW_LOGIN_MAX_ATTEMPTS` (default 10) budgets the email and source address **together**, so one
attacker cannot lock a real owner out from everywhere. `GW_LOGIN_IP_MAX_ATTEMPTS` (default 60)
budgets the **source address alone**, because varying the email would otherwise buy a fresh
bucket — and a full Argon2id verification — on every request. Rejection is `429`
with `Retry-After`; which window tripped is in the error's `details`, not its message.

**The per-source window assumes your proxy is named in `GW_TRUSTED_PROXY_IPS`** (section 4). If it
is not, every attempt appears to come from the proxy and the whole office shares one budget of 60.
That is fail-closed rather than fail-open, and it is why the default is generous rather than
tight. The limiter guards `POST /api/v1/auth/login` and nothing else: in `oidc` mode the front
door is the provider's.

**Which paths answer without a credential.** Every route under `/api/` requires one, with a
named set of exemptions in `scopes.py` (`SCOPE_EXEMPT_PATHS`): the health and readiness probes,
`/openapi.json` and `/docs`, `/mcp` (which authenticates per call rather than per request), the
static bundle, and **six pre-authentication routes that must be reachable to obtain a
credential at all** — `/api/v1/auth/login`, `/api/v1/auth/oidc/start`,
`/api/v1/auth/oidc/callback`, `/api/v1/auth/modes`, the last so the sign-in page can render
the right form before anyone has signed in, and `/api/v1/auth/code/request` and
`/api/v1/auth/code/verify`, which answer `feature_disabled` unless section 5a's relay is set. A test walks every registered route against that
allowlist, so the set cannot grow silently.

**A credential-exempt request runs as a reader, never as an administrator** (DD-15).
Those routes each build their own actor for the write they attribute, and the edge actor that
labels the access log line carries `read` scope. Were it to carry `admin` and belong to the seeded
administrator, a fifth entry added to that list would not run *without* a credential — it would
run as a full administrator, satisfying both the scope and role checks. Both allowlists are pinned
by exact equality in the test suite.

## 5a. Sign-in by emailed code, for a hosted workspace

A workspace run by a hosting control plane signs people in with a six-digit code sent to their
email address, and its administrators invite people by email. The workspace never sends email
itself: it asks the control plane's **relay** to, with a request naming one of two templates and
typed fields, so a workspace cannot send free text. There is no SMTP setting and no other sender,
deliberately and for good. A self-hosted workspace leaves this off and keeps passwords, or its own
OIDC provider, exactly as sections 3 and 5 describe.

| Variable | Meaning |
| --- | --- |
| `GW_RELAY_URL` | The absolute URL the workspace posts each message to. `https`, except a loopback host (`127.0.0.1`, `::1`, `localhost`) may be `http`, for tests and local development. No user part, query or fragment |
| `GW_RELAY_TOKEN` | The workspace's relay credential, sent as a bearer token. At least 32 characters |

**Codes are on exactly when both are set**, and blank counts as unset. Startup refuses, naming the
variable and never echoing a value, when only one is set, when the URL or the token breaks the rules
above, or when `GW_AUTH_MODE` is `oidc` (codes sign local accounts in). With codes on:

- `GET /api/v1/auth/modes` reports `email_code: true` and `standalone: false`, and the sign-in page
  shows an email field and a code field rather than a password form.
- `POST /api/v1/auth/login` answers `409 feature_disabled` (`details.feature` is
  `password_sign_in`), without counting an attempt. The bootstrap claim (section 3) still takes a
  password; nobody needs to know it.
- `POST /api/v1/auth/code/request {email}` always answers `202` with one sentence, whatever the
  address, and does the rest after answering. `POST /api/v1/auth/code/verify {email, code}` answers
  exactly as `/login` does, or `401 invalid_credentials` with one message for every failure.
- `GET`, `POST /api/v1/invites` and `DELETE /api/v1/invites/{invite_id}` let an administrator
  invite a person by email and role, list the pending invites, and revoke one. Inviting answers
  `409 conflict` for an address that already has an active account, has a live invite, or belongs
  to an account that signs in through an identity provider; revoking an invite already accepted
  or revoked is `409 conflict` too. A malformed address is `422`.

With codes off, all five of those routes answer `409 feature_disabled` and nothing else changes.

**The per-source limit depends on `GW_TRUSTED_PROXY_IPS`** (section 4). Code requests and
verifications are counted in the login limiter's two windows (section 5), and the source address is
the client only when the proxy is trusted. Set it to the platform's proxy range before giving a
workspace `GW_RELAY_URL`, or every person shares one budget.

### The relay request

This is the definition the control plane's relay builds to. One request per message:

```
POST <GW_RELAY_URL>
Authorization: Bearer <GW_RELAY_TOKEN>
Content-Type: application/json
```

The workspace appends nothing to the URL; the hosted value is
`https://api.glosswork.dev/v1/relay/send`. The body is one JSON object with exactly these keys and
no others:

| Key | Type | Meaning |
| --- | --- | --- |
| `message_id` | string | A UUID (version 4, lowercase, hyphenated) the workspace makes per message. A trace id, not an idempotency key: the workspace logs it when it sends, and the relay stores it so one message can be followed across both logs |
| `template` | string | `sign_in_code` or `invite` |
| `to` | string | One email address, trimmed and lowercased: at most 254 characters, exactly one `@` with text on both sides, no whitespace, and none of `,;<>"` |
| `fields` | object | Exactly the template's fields below, and no others |

| Template | Field | Type and rule |
| --- | --- | --- |
| `sign_in_code` | `code` | String of exactly six ASCII digits, leading zeros kept |
| `sign_in_code` | `code_expires_at` | RFC 3339 UTC with a `Z` and whole seconds, such as `2026-09-29T15:25:00Z`: the moment the code stops working, ten minutes after it was made |
| `invite` | `inviter_name` | The inviting administrator's display name exactly as stored. The workspace sets no bound beyond non-empty; the relay may refuse one, answering `refused_fields` |

The request carries neither the workspace's name nor its sign-in address. The relay fills both in
from its own record of the workspace, so a compromised workspace cannot choose them.

**The golden requests.** `docs/relay/sign_in_code.http` and `docs/relay/invite.http` are the exact
requests the workspace's driver produces for a fixed message, clock and id, with a placeholder
token. A test regenerates them and compares bytes, so they cannot drift from the code. The control
plane's tests send copies of these files, taken at a pinned commit of this repository, rather than a
hand-written copy of the definition.

**What the relay must also hold for this to work:**

- The bearer token alone identifies the workspace. The control plane generates it with at least 32
  characters, or the workspace refuses to start.
- A workspace frozen read-only (section 6a) is still accepted, because sign-in keeps working while
  frozen. Only a deleted workspace's credential is refused.
- A refusal of fields is `422` with exactly the body
  `{"error": {"code": "refused_fields", "fields": ["<field name>", ...]}}`: field names, never
  values. The workspace reads `fields` from exactly that shape and treats any other `4xx` body as
  unparsed.
- Success is any `2xx`; `202` is expected.

### What the workspace does with each answer

It never retries: a person can ask for another code, and an administrator sees an invite's outcome
on screen. The whole exchange has one 5-second deadline.

| Answer | Outcome | Code request | Invite |
| --- | --- | --- | --- |
| Any `2xx` | `accepted` | Done | "Invite sent." |
| `401` or `403` | `refused_credential` | Logged at `error` | Kept; "saved, but the email could not be sent" |
| `422` | `refused_fields` | Logged at `error` with the relay's field names | Kept; the same, plus "your display name may be the reason" when `inviter_name` is named |
| `429` | `rate_limited` | Logged at `warning` | Kept; "the email service is busy" |
| Anything else, a connection error, or no complete answer in 5 seconds | `unavailable` | Logged at `error` | Kept; "the email could not be sent" |

Every send logs one `relay_send` line with the `message_id`, the template and the outcome. No log
line, audit row or error carries a code, a code hash, the relay token or a request body.

### Codes, limits and recovery

A code works for **10 minutes**, once. Five wrong guesses for an address spend every live code it
has. An address can be sent at most **5 codes an hour and 20 a day**, counted in the database, so a
restart does not reset them; asking again does not cancel the code a person is already typing.

That leaves one thing a stranger can do with only a person's address: spend its allowance and keep
that person out until the window rolls off, up to a day. The recovery is a person at the hosting
operator running, against that workspace's volume:

```bash
python -m glosswork.admin clear-sign-in-codes --email <address>
```

It deletes the address's code rows, which resets its count, prints how many it deleted, and touches
nothing else.

## 6. Backup and restore

### Taking a backup

```bash
curl -fsS -X POST https://tracker.example.com/api/v1/admin/backup \
  -H "Authorization: Bearer gw_pat_..." \
  -o glosswork-backup.tar
```

The endpoint requires `admin` scope **and the `admin` role** -- it is one
of section 3a's twelve routes. A `creator` presenting an `admin`-scoped PAT gets `forbidden`
here even though the same PAT reaches the schema routes; only a system administrator can take
a backup. It streams one tar containing the database snapshot
followed by the `attachments/` tree. The database half is a `VACUUM INTO` snapshot, not
a file copy, because a running deployment holds uncheckpointed frames in the `-wal` file
that a naive copy would silently drop (DD-36). The vector and keyword indexes ride along
inside the snapshot, so a restore needs no re-index.

**Writes continue during a backup**, and the ordering inside the artifact is normative:
database first, blob tree second, so every attachment row in the snapshot references
bytes that were already on disk when it was taken.

What it costs, measured on a 200,000-record deployment whose database was 1.24 GB: the
call took **6.7 seconds** and produced a **1,240 MB** artifact, and 5,040 concurrent
writes all succeeded. **But one of those writes waited 4.4 seconds**, against the
5,000 ms lock timeout every writer waits on. The lock is effectively held for the
duration of the snapshot, which runs at roughly **5 seconds per gigabyte** on the
platform in `docs/PERFORMANCE.md`.

So: at this size, take backups when the deployment is quiet. **Past roughly 1.5 GB,
expect writes concurrent with a backup to start failing**, and schedule backups in a
genuine maintenance window rather than during the working day.

Store the artifact off the volume it came from. It is roughly the size of the database,
which grows faster than you may expect (section 8).

### Restoring

**Restore is an operator procedure, not an endpoint** (DD-36). A restore API would have
to overwrite the database it is being served from, so it does not exist. The procedure:

```bash
docker stop glosswork
docker rm glosswork

# A fresh, empty volume. Restoring over a populated one leaves stale files behind.
docker volume rm gw-data
docker volume create gw-data

# Unpack the artifact into the empty volume through a throwaway container.
docker run --rm -v gw-data:/data -v "$PWD:/backup" alpine \
  sh -c 'tar -xf /backup/glosswork-backup.tar -C /data && chown -R 1000:1000 /data'

docker run -d --name glosswork -p 8000:8000 -v gw-data:/data <your env flags> glosswork
```

Migrations run at startup, so the restored deployment comes up ready. Confirm it with
`/readyz`, then check `GET /api/v1/admin/search-index` reports the same `indexed_chunks`
with zero pending jobs, and make one write: a successful write is what distinguishes a
deployment that is genuinely working from one that is merely readable, because it
exercises key allocation and migration state.

`chown` matters. The image runs as a non-root user, and `tar` unpacking as root leaves
files the application cannot write.

This procedure is exercised end to end by `container_tests/test_backup_restore.py`,
including a negative case that restores the database without the blob tree and asserts
the attachment download fails.

### Full-deployment export

`GET /api/v1/admin/export` (`admin` scope and the `admin` role -- section 3a) streams the whole
deployment as JSON: schema, records, links, comments, saved views, agent labels, and audit history.
This is **not** the backup format and is not the restore path. Its consumer is a downstream system,
a warehouse, or an archive that wants portable documents rather than a SQLite file.

**Each exported record carries a key for its last agent:** `updated_by_agent_label_id`, the agent
label of the write that last changed one of its values, or `null` where a person made it.
`ExportService` shares one record serializer with the API (DD-25), so the export artifact tracks the
wire document by construction rather than by remembering to. A consumer that reads the export
positionally, or asserts an exact key set, will see it. The label's *text* is not in the record
document -- the export's `agent_labels` section already carries the registry.

A `creator` cannot take this export even with an `admin`-scoped PAT: the whole point of
`require_role` here is that defining object types no longer implies reading everyone else's
data (DD-11). If your reason for exporting is one object type rather than the whole
deployment, `GET /api/v1/object-types/{key}/export` (`read` scope, REST-only) reaches a
`creator` who holds `read` on that type and needs no `admin` role.

## 6a. Read-only mode

A deployment can refuse writes while it keeps serving reads, search and export (DD-38). A hosted
workspace is frozen this way when its trial ends; on your own deployment it suits a migration
window, or a deployment kept for reference after its data moved elsewhere.

```bash
docker run -d --name glosswork -p 8000:8000 -v gw-data:/data \
  -e GW_READ_ONLY=true \
  -e GW_SUBSCRIBE_URL=https://example.com/subscribe \
  <your other env flags> glosswork
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `GW_READ_ONLY` | `false` | `true` refuses every REST and MCP write outside the open list below. A boolean like every other: a blank value refuses startup naming the variable |
| `GW_SUBSCRIBE_URL` | unset | Where a refused write tells its caller to go. Must be an absolute `http` or `https` URL, or startup is refused naming the variable. Blank is unset. It may be set without `GW_READ_ONLY` |

**Both are read at startup, so turning the mode on or off is a restart**, on the same volume.
Nothing is migrated and nothing is stored: set `GW_READ_ONLY=false` and restart to make the
deployment writable again. Where your platform restarts the container when its configuration
changes, changing the variable there does this for you. While the mode is on, the startup log
carries one `warning`, `read_only_mode`, with `subscribe_url_set` true or false, so you can
confirm the state without attempting a write.

**What is refused.** On REST, every route whose method is not `GET` and which declares `write` or
`admin` scope, except the open list. On MCP, every tool that declares `write` or `admin`, except
`list_schema_proposals` and `list_object_type_grants`, which only read. Each refusal is
`409 workspace_read_only`, and it changes nothing:

```json
{
  "error": {
    "code": "workspace_read_only",
    "message": "This workspace is read-only, so POST /api/v1/object-types/{object_type_key}/records changed nothing. Reading, searching and exporting still work. To make changes again, subscribe at https://example.com/subscribe.",
    "details": {
      "subscribe_url": "https://example.com/subscribe",
      "setting": "GW_READ_ONLY",
      "attempted": "POST /api/v1/object-types/{object_type_key}/records"
    }
  }
}
```

`attempted` is the route template on REST and the tool name on MCP. Without `GW_SUBSCRIBE_URL`,
`subscribe_url` is `null` and the last sentence reads "An administrator of this deployment turned
writes off with GW_READ_ONLY." On MCP the same envelope comes back as a tool result with `is_error`
set, and `tools/list` does not change: write tools stay listed and are refused on call. An upload
ticket minted before the restart is refused too.

**What stays open, and why.**

| Call | Why it stays open |
| --- | --- |
| `POST /api/v1/admin/backup` | The backup keeps running through the frozen days, so the data a person comes back for is still captured |
| `POST /api/v1/access-tokens` | A person who returns to take their data connects an agent, which needs a token. A token minted while frozen can only read |
| `DELETE /api/v1/access-tokens/{token_id}` | Revoking a leaked token is security, which a freeze never blocks |
| `POST /api/v1/me/password` | Changing your own password, from a browser session, is security too |
| `DELETE /api/v1/principals/{principal_id}` | Removing someone who left |
| `DELETE /api/v1/invites/{invite_id}` | Stopping an invite: accepting one is a sign-in, which stays open (section 5a) |

Everything else that writes is refused, including an administrator resetting **another** person's
password; deactivate that person instead if the account needs containing. Re-indexing, blob sweeps,
saved views, CSV import and agent label renames wait until the deployment is writable. Sign-in,
OIDC sign-in, sign-in by emailed code (including an invite's first sign-in, which creates the
person) and `POST /api/v1/bootstrap` never reach a scope check, so they stay open. Sending a new
invite waits. Reads, both
exports (`GET /api/v1/admin/export` and the per-type CSV), attachment downloads and MCP
`resources/read` all work as before.

**Changing your own password revokes your tokens, frozen or not** (DD-13). Every personal access
token you hold, and every session except the one you changed it from, stops working. Minting stays
open, so reconnect each agent with a new token; it will be able to read, which is all a frozen
workspace allows.

**The order of refusals** is credential, scope, read-only, then role and grant. A `read` token on a
write route still gets `insufficient_scope`. A caller whose scope suffices but who lacks the `admin`
role or a grant on the type is told the workspace is read-only, and hears the real reason once it is
writable again. A body that is not JSON at all still answers 422 first.

**A frozen volume still changes.** A refused call writes nothing beyond what every authenticated
call writes (a token's `last_used_at`, a session's `last_seen_at`). But the open calls append audit
events, a backup appends `backup_taken`, deactivation and a password change revoke tokens and
sessions, sign-in creates a session, the embedding worker finishes work queued before the restart,
and in `oidc` or `both` mode a first sign-in creates a principal. Do not checksum a read-only volume
and expect it to stay the same.

**The browser does not explain the refusal yet.** The web UI shows a refused edit as "API request
failed with status 409", without the message or the subscribe URL, and nothing in the UI says the
deployment is read-only. Agents and REST clients get the full message.

## 7. Re-indexing, and what it costs

`POST /api/v1/admin/search-index/reindex` (`admin`) enqueues every indexed source for
re-embedding, and the Settings screen has a button for it. It returns immediately with a
count; the worker drains the queue in the background and
`GET /api/v1/admin/search-index` reports progress. Search keeps working throughout,
because rows are replaced per source as the worker reaches them.

**Know the cost before pressing it.** On the 200,000-record deployment the queue held
**424,820 sources** and the worker drained a measured **164 sources per second**, so a
full re-index is about **43 minutes**, single threaded, holding up to four cores. Per
chunk, a short title costs about 3.6 ms and a full 256-token passage about 44.8 ms, so
a deployment whose text runs long pays proportionally more.

You need a re-index after changing `GW_EMBEDDING_MODEL` to another model of the same
dimension, and after turning `GW_EMBEDDING_ENABLED` back on following a period with it
off. You do not need one after a restore.

Turning `embed` on for a field of a large object type is the other bulk indexing action.
It runs in the background in small batches after the schema change commits, pausing
between them so ordinary writes continue: on a 90,000-record object type it takes about
37 seconds, during which 9,437 concurrent writes completed and none failed.

**Destructive schema changes are the exception, and belong in a quiet window.** Deleting
a field from a large object type rewrites every record's stored values, and that has to
happen atomically with the change that authorised it, so it is not batched. Measured on
a 90,000-record object type: about 8.6 seconds, during which a few concurrent writes
failed with a lock timeout under heavy write load. Approving a destructive proposal
against a large type is a maintenance-window action.

## 8. What to monitor

**Volume free space, first and most importantly.** Attachments and the audit trail both
grow the volume as the deployment is used, so watch its free space. Per-file uploads are
capped by `GW_MAX_ATTACHMENT_BYTES`
(default 25 MiB), and that cap is enforced by the service on every upload: a cap declared and
validated in `config.py` but read by nothing would let an upload of any size succeed no matter
what the variable said. An upload over it is `validation_failed` naming the variable, and
nothing is written.

That ceiling is also **published to agents**, as `limits.attachment_max_bytes` in
`describe_capabilities`, so an agent about to upload knows the number before it reads a file
rather than after it is refused.

Note the one thing `GW_MAX_ATTACHMENT_BYTES` still does not bound: the bytes Starlette spools
to the container's temporary directory before the route reads them. `client_max_body_size` on
the proxy is the control for that, and section 4 says why it is not optional.

Size the volume generously, because the database is larger than the record count
suggests. Measured at 200,000 records with no attachments, the database was **1.31 GB**,
and the audit trail dominated it:

| | size | share |
| --- | --- | --- |
| `audit_events` and its five indexes | 783 MB | 58% |
| `records` | 151 MB | 11% |
| keyword index (all five `fts_content*` shadow tables) | 127 MB | 9% |
| everything else | ~280 MB | 21% |

**Usage counts, if you are an operator.** `GET /api/v1/usage` is the one endpoint built
for whoever runs the deployment rather than whoever uses it (DD-39, FR-P10). Set
`GW_OPERATOR_TOKEN` to a value of at least 32 characters and present it in an
`X-Operator-Token` header:

```bash
curl -s -H "X-Operator-Token: $GW_OPERATOR_TOKEN" \
  https://tracker.example.com/api/v1/usage
```

```json
{
  "since": "2026-09-18T09:14:02Z",
  "records_live": 1840, "records_deleted": 12,
  "attachment_count": 63, "attachment_bytes_logical": 41203118,
  "attachment_bytes_stored": 38118400,
  "humans_active": 4, "humans_total": 5,
  "agent_labels_distinct": 3, "agent_label_calls_total": 9041,
  "agent_labels_by_harness": {"claude-code": 2, "__other__": 1},
  "object_types": 6, "fields": 51,
  "tool_calls": [{"tool": "query_records", "error_code": null, "count": 4120}]
}
```

Four things to know before you rely on it.

- **Everything else gets one refusal.** 401 `operator_token_refused`, byte for byte, whether
  the caller sent the wrong value, sent nothing, or reached a deployment that never set the
  variable. That is deliberate: whether usage metering is configured says whether this
  workspace is somebody's hosted customer, and an unauthenticated prober must not be able to
  read it in one request.
- **No field carries tenant content**, ever. Not a record value, not an object type key, not
  a filename, not a person's name, and not an agent label as the tenant typed it: labels are
  matched against a shipped list of known harnesses and reported as that list's own names,
  with everything else counted under `__other__`.
- **Tool calls are counted at the MCP surface only**, and a call whose *token* was refused is
  not counted at all, because it never reaches the point where counting happens. So a
  workspace whose agents are all presenting bad tokens reports zero tool calls and looks
  idle. Check `agent_label_calls_total` and the deployment's own access log before reading a
  zero as quiet.
- **`since` tells you whether the counters were reset.** It is written when the database
  first runs migration 11, so a fresh volume gets a fresh value. If the counts fall and
  `since` moved, the volume was replaced; if the counts fall and `since` did not, usage
  genuinely dropped.

Counts are held in memory and written down every 30 seconds and again on a clean stop, so
they survive the restart section 2a describes. A `docker kill` loses at most the last
interval.

Audit retention is indefinite by decision (DD-40): the audit trail is one of the
product's success criteria, and `audit_events.id` is the change-feed cursor agents read,
so a purge would have to preserve its monotonicity. Budget for it rather than plan to
trim it. A backup artifact is about the size of the database, so plan for both.

**Logs.** Everything on stdout is one JSON object per line, including lines from the MCP
SDK and the HTTP client (FR-P5). Each request emits an `access` event carrying
`request_id`, `principal_id`, `agent_label`, `surface`, `scope`, `status` and
`duration_ms`, which is what you would alert on for latency or error rate. A line that
does not parse as JSON is a bug worth reporting.

**Grant changes.** Granting, revoking or changing a principal's access to
an object type is itself an audited write, at `entity_type = 'object_type_grant'`
(`services/access.py`, DD-11). `GET /api/v1/audit-events` has no `entity_type` filter of its
own, so scope the query by type and pick the grant rows out of the response -- each carries
`action` (`create`, `update` or `delete`), `old_value`/`new_value` (the prior and new level),
and `note` naming the affected `principal=<id>`:

```bash
curl -fsS "https://tracker.example.com/api/v1/audit-events?object_type=initiative" \
  -H "Authorization: Bearer gw_pat_..." \
  | jq '.events[] | select(.entity_type == "object_type_grant")'
```

The caller needs at least `read` on `initiative` to see anything back for it at all (section
3a); a system administrator's PAT sees every type's grant history this way, one type at a
time.

**Indexing health.** `GET /api/v1/admin/search-index` reports `pending_jobs`,
`failed_jobs` and `indexed_chunks`. A `pending_jobs` count that grows without bound
means the worker is not keeping up or has stopped; a non-empty `failed_jobs` names what
failed. Agents also see `index_lag` on every search response.

**Orphaned attachment bytes.** A bounded sweep runs at startup, and
`POST /api/v1/admin/blobs/sweep` (`admin`) runs one on demand, taking an optional
`limit`. It deletes only blobs no `attachments` row references, and the response says
whether it stopped early.

## 9. Query performance

The read path is fast at the scale this product targets, and `docs/PERFORMANCE.md`
carries the numbers and the method. What matters operationally is the difference between
an indexed and an unindexed filter:

- **Indexed fields** return a filtered, sorted, paginated page in single-digit
  milliseconds at 200,000 records, including while the embedding worker is draining.
  `single_select`, `date`, `datetime` and `user_ref` fields are indexed automatically;
  any other field can be marked indexed in the schema editor.
- **Unindexed fields** cost a full scan of that object type's records: about 160 ms for
  an equality filter and about 225 ms for a substring match on long text, on the largest
  object type in the measured corpus. That scales with how many records the largest
  object type holds, so it is a property of your data rather than of the software.

If a saved view someone uses constantly filters on an unindexed field, marking that
field indexed is the fix, and it applies immediately.
