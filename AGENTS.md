# AGENTS.md

Instructions for any coding agent or human working in this repository. This is the canonical
entry point; tool-specific files, if any, point here rather than restating rules.

For what Glosswork *is* and how to run it, read [README.md](README.md). This file is about
how to change it.

## What you are working on

Glosswork is a schema-flexible, agent-native record store with an MCP endpoint, a REST API,
and a React UI. Humans and AI agents share one surface for tracking initiatives, tasks,
decisions, and institutional memory.

## Commands

| Purpose | Command |
| --- | --- |
| Install | `uv sync` |
| Test | `uv run pytest -q` |
| Structural guards only (what CI runs on every pipeline) | `uv run pytest -q -m structural` |
| Lint | `uv run ruff check . && uv run ruff format --check .` |
| Types | `uv run mypy src` |
| Run locally | `uv run uvicorn glosswork.app:app --reload` |
| Fetch the embedding model (required by dev and tests) | `uv run python scripts/fetch_model.py` |
| Frontend build | `npm --prefix web run build` |
| Frontend lint | `npm --prefix web run lint` |
| Frontend types | `npm --prefix web run typecheck` |
| Frontend test | `npm --prefix web run test` |
| Frontend e2e | `npm --prefix web run e2e` (see "Traps" below) |
| Generate API types | `npm --prefix web run generate:api-types` |
| Build image | `docker build -t glosswork .` |
| Run container | `docker run -p 8000:8000 -v gw-data:/data glosswork` |
| Container accept tests | `uv run pytest -q container_tests` (needs Docker) |
| Golden retrieval report | `uv run pytest -q tests/test_search_golden.py -s` |
| Regenerate third-party licences (after any dependency change; needs the network) | `uv run python scripts/third_party_licenses.py` (`--check` to test it is current) |

Operator CLI:

| Purpose | Command |
| --- | --- |
| Create first admin | `uv run python -m glosswork.admin create-admin --email <you> --password <pw>` |
| Mint a token | `uv run python -m glosswork.admin mint-token --name <where> --scope admin` |
| Set a principal's role | `uv run python -m glosswork.admin set-role --principal <email> --role creator` |
| Grant access to one type | `uv run python -m glosswork.admin grant --type <key> --principal <email> --level <none\|read\|write\|admin>` |
| Revoke / list grants | `uv run python -m glosswork.admin revoke --type <key> --principal <email>`, `list-grants --type <key>` |

Performance measurement (see `docs/PERFORMANCE.md` for the order; `measure_fanout.py` is
destructive, and a perf corpus seeded before `object_types.display_field_key` existed cannot be
opened, because that column was added to migration 1 in place before migrations were frozen
(DD-6)):

`scripts/seed_perf.py`, `measure_perf.py`, `measure_indexing.py`, `measure_backup.py`,
`measure_fanout.py`, all taking `--data-dir`.

`scripts/measure_cold_start.py` is the exception: it measures a stopped container coming
back to `/readyz` and needs Docker and an image tag rather than the seeded corpus
(`--image`, `--runs`, `--out`). It creates and removes its own volume and container.

## Where things are

| Document | Holds | Read it |
| --- | --- | --- |
| `PRD.md` | Problem, thesis, goals, users, concepts, numbered FRs, success criteria, risks | Before implementing anything |
| `docs/ARCHITECTURE.md` | The parts of the system, how a request moves through them, and where each is specified | First, before any change |
| `docs/DESIGN_DECISIONS.md` | The key technical decisions, DD-1 onward, each as today's rule and its reason | When a change touches an area a decision governs |
| `docs/DATA_MODEL.md` | Table schemas, field type catalog, indexing strategy, audit and embedding design | Any storage or query work |
| `docs/MCP_TOOLS.md` | Tool catalog, filter grammar, error conventions | Any MCP or filter work |
| `docs/DEPLOYMENT.md` | Operating it: config, proxy, auth modes, backup and restore, re-indexing | Operational questions |
| `docs/PERFORMANCE.md` | Every measured number, the platform, and how to reproduce | Before changing the read path |
| `docs/DESIGN.md` | The UI design system (DD-41): tokens, type, the mark, attribution primitives, components, screen intents | Any frontend or brand work |
| `docs/AGENT_ONBOARDING.md` | How an agent onboards onto a *live deployment* over MCP. A product document, not a contributor one | Agent-facing work |
| `docs/changes/README.md` | How a change is planned, adversarially passed, executed and verified, and the plan template | Before starting any change |
| `THIRD_PARTY_NOTICES.md` | Licence and copyright notices for the three bundled fonts and the embedding model, where every other package's notice is, and what it deliberately does not cover | Before changing what the image bundles |
| `THIRD_PARTY_LICENSES.md` | Generated: the licence text of every package the image installs from `uv.lock` or bundles from `web/package-lock.json`. Never edited by hand | Regenerate it whenever a lockfile changes |

These are **specifications, not background**, and they are more detailed than they first appear.
Read the FRs and design decisions a change references, not just its summary.

Source layout: `src/glosswork/` (backend), `web/src/` (frontend), `tests/` (backend tests),
`web/e2e/` (Playwright), `container_tests/` (image acceptance), `scripts/` (measurement and
model fetch).

## Non-negotiables

These decay silently if not enforced on every change.

1. **Never pin a dependency version from memory.** Verify current stable on PyPI
   (`uv run --with pip pip index versions <pkg>`; `uv` itself has no `pip index` subcommand) or
   npm (`npm view <pkg> version`) before pinning, then confirm what actually resolved.

   **Any `uv lock`, `uv add` or `uv lock --upgrade-package` run on a machine whose user-level
   `uv` configuration names a private index rewrites *every* `source = { registry = ... }` line
   in `uv.lock`, not only the package being changed**, and no project-level setting overrides
   that: `[[tool.uv.index]]`, `[tool.uv] index-url`, a project `uv.toml`, `UV_INDEX_URL`,
   `UV_DEFAULT_INDEX` and `uv lock --default-index` were each measured and all six lost to the
   machine-level setting. `tests/test_supply_chain.py` in the guards lane is what catches it, on
   every pipeline. The remedy is one substitution, from the repository root:

   ```
   sed -i '' -E 's|registry = "[^"]*"|registry = "https://pypi.org/simple"|g' uv.lock
   ```

   **A `uv lock` against a deleted lockfile is not the remedy**: it re-resolves, and on this
   project that silently upgraded transitive dependencies by 677 lines. Substitute in place, then
   confirm `git diff --text uv.lock` changes nothing but registry lines.
2. **`ActorContext` is required on every write path** (DD-4): principal, agent label, auth
   method, surface, request id. A write that cannot attribute itself is a bug.
3. **Business logic lives in the service layer** (DD-3). If logic appears in a route handler or
   an MCP tool function, it is in the wrong place. REST and MCP are adapters over one service
   layer, which is what makes their parity structural rather than aspirational. Build the
   service layer first and the adapters second, in every change. The frontend equivalent:
   business logic lives in hooks and utilities, never in a component render body.
4. **Raw SQL stays inside the repository layer** (DD-2). Filters compile through the filter-AST
   compiler. Only that compiler knows about `json_extract`.
5. **Every behavior change ships with a test.** Stated acceptance criteria are the floor, not
   the target.
6. **Descriptions on object types and fields are required and agent-facing.** Reject empty ones.
   They are the product's differentiator, not metadata.
7. **Type-hint new and modified functions, and prefer `pathlib` over `os.path`.** Use `uv` with
   the local `.venv`; do not depend on a global `python3`.
8. **Existing migrations are never edited** (DD-6). A schema change is a new migration appended
   to `MIGRATIONS`, never an edit to one already on `main`, and that includes whitespace and SQL
   comments inside a statement. The runner recognizes an applied migration by its number alone,
   so a database that already ran a migration never receives an edit to it.
   `tests/test_migrations_are_forward_only.py` checks every migration against
   `tests/migration_hashes.txt`; a new migration appends its own line in the same change, with
   the command in `CONTRIBUTING.md` "Migrations", and no existing digest line is ever changed. A red
   guard means the edit is wrong, never the manifest.

## Architecture invariants

The load-bearing property of this codebase is that most invariants have **exactly one
implementation**, and a meta-test fails if a second one appears. Before adding a code path that
answers a question already answered somewhere, look for the funnel. Breaking one of these is
almost never the right fix.

**Authorization has three axes** (DD-11). Credential scope (`access_tokens.scope`, or
`role_scope(principal.role)` for a session) says how much a credential may do anywhere; the
system role (`principals.role`: `admin | creator | member`) says whether a principal
administers the deployment and may define object types; and `object_type_grants.level`,
defaulting to `object_types.default_level`, says how much a principal may do to one object
type. What a call may do is `min(credential scope, granted level)`. The credential is a
**ceiling** and can only ever narrow. Every object type is closed by default. `AccessService`
is the only place that answers the question and `SqliteGrantRepository` the only place that
reads the table; meta-tests assert both, and that every object-type-touching service entry
point consults it. Authority is never taken from an input: acting on another principal's
credentials goes through one `_require_admin_delegation` (DD-12).

**A capability credential narrows the ceiling and never widens it** (DD-16). It is read for
authorization at one predicate, `scopes.refuse_capability_credential`, called first by
`enforce_scope` and also by `McpAdapter.identity` because `/mcp` is scope-exempt. Exactly one
route opts back in with `require_capability`, pinned by equality.

**A principal reference resolves at exactly one function** (DD-24),
`services/principals.py::resolve_principal_ref`, in the order `@me`, id, email, exact display
name, with three callers: the write path, the filter compiler (through a callable injected on
`FilterContext`, since that module has no database access), and CSV import.
`tests/test_one_principal_resolver.py` fails if a fourth appears. An ambiguous display name is
refused with every candidate named, never picked.

**An agent label resolves at exactly one function** (DD-17),
`actor.py::resolve_agent_label_id`, holding FR-M5's precedence (per-call `agent` beats the
`X-Agent-Label` header, which beats the label stored on the presented token) and FR-I6's
auto-registration, with two callers: the MCP adapter and the REST edge.
`tests/test_one_agent_label_resolver.py` walks the AST and fails if a second
`register_use` call appears — it walks rather than greps because a comment in `adapter.py` names
the method and a text search counts it. REST honors the header for a bearer credential and never
for a session cookie. Every tool that appends an audit event accepts `agent`;
`create_attachment_upload` is the one exemption and appends none, which a test asserts.

**The display field is chosen, not guessed** (DD-23). `object_types.display_field_key` holds a
`fields.key`, and `services/base.py::display_field` is the only rule: it honors the key when it
names a live, display-eligible field and otherwise falls back to the first non-relation field by
position. `fieldtypes.py::is_display_eligible` is the only place `relation`, `attachment` and
`user_ref` are excluded. Both answers ride the wire so the frontend reads them rather than
re-deriving either.

**Hiding is never the only signal** (DD-42). A screen about an object type renders only the
controls the caller's `your_access` reaches and carries exactly one banner naming the level held
and who can raise it. `web/src/access/hidingIsNeverTheOnlySignal.test.ts` fails if a module
gates on level without rendering that banner, and pins the list of gating screens.

**A client rendering references composes mutations from stored ids, never from resolved rows**
(DD-27). A resolver may silently omit an id that names no row and one the read rule withholds,
and the two are indistinguishable from the client, so rebuilding from rendered rows destroys
ids the user cannot see. An unresolved id gets a placeholder row; the number of rows always
equals the number of ids stored.

**The request edge fails closed** (DD-15). A credential-exempt request runs as
`anonymous_actor` at `scope="read"` on both paths that build one. `bootstrap_actor` keeps
`admin` for the lifespan seed and the CLI only, and `grep bootstrap_actor src/glosswork/routes/`
must stay empty. `AUTH_PUBLIC_PATHS` and `SCOPE_EXEMPT_PATHS` are pinned by equality, as is
`GATED_METHODS`. `InternalError` is raised by nobody: it is constructed only at the seams where
an unclassified exception escapes, and carries only a request id.

**Every input is bounded, every bound is a setting or a named constant, and a bad input is a
4xx** (DD-18, DD-19). Bounds are published in `describe_capabilities`. Each cap is enforced in the
service so both adapters inherit it and neither re-declares it as a schema bound. Client-
correctable input is a 4xx naming what to fix, never a 500, and a 422 never echoes the body.

**Content is data** (DD-20). The eight pseudo-field names are reserved as field keys, refused at
`fieldtypes.validate_field_key`. CSV cells are neutralized on export and restored on import as a
**run-length** escape, which is what makes it injective. Existing collisions are reported at
startup, never rewritten.

**A multi-row write is one transaction** (DD-22). `RecordService.write_batch()` plus the
`*_in_txn` methods. The extraction is mandatory: a nested `db.write()` takes a second connection
and deadlocks on its own writer lock.

**Handles ride in tool calls; bytes ride HTTP or `resources/read`** (DD-29). A tool call's
arguments are the model's output tokens and a tool result is its input tokens, and base64
tokenizes at roughly one token per raw byte. Text an agent authored is the one payload allowed
inside a call.

**The endpoint teaches itself** (DD-30). `instructions` is an index under
`INSTRUCTIONS_BUDGET = 2000` characters, with a test, because hosts truncate. The manual is
`describe_capabilities`, at `read` scope, returning the same bytes to every caller.
`tests/test_endpoint_teaches_itself.py` drives a real `mcp.Client` and opens no path in this
repository, which is what stops `docs/AGENT_ONBOARDING.md` becoming load-bearing.

**Index names derive from the object type id** (DD-12) and are injective by construction.
`IF NOT EXISTS` is deliberately absent from both creators, because it turned a collision into
silence rather than an error. `retire_legacy_index_names` is the only code in `src/` that reads
an index name's structure, and its prefixes are matched literally, because in SQL `LIKE` an
unescaped `ix_rec_%` also matches `ix_records_type_live`.

## Measured numbers

Both reproducible; re-measure rather than trusting these.

- **Retrieval quality.** Hybrid recall@5 = 23/27 = 0.852, MRR 0.789, on arm64 with
  `bge-small-en-v1.5@5c38ec7`. That is the shipping gate. The 0.90 target is deferred by
  decision (DD-33), and golden cases D2, D3 and D5 are `xfail(strict=True)`, so an accidental
  improvement surfaces as an unexpected pass rather than silence.
- **Query latency.** Indexed shapes 3.00 / 3.05 / 3.94 ms p50 against a 50 ms gate. Read
  `docs/PERFORMANCE.md` before changing anything in the read path.

Suite sizes, as a floor rather than a claim, re-measured 2026-09-12: 1,748 backend tests of which
3 are strict xfails, 998 frontend tests across 95 files, 112 Playwright scenarios (65 functional
across 19 files, 47 visual) against 39 committed baselines, 32 MCP tools, 8 container-proof
tests. Read the Playwright numbers as two projects, not one suite: a bare
`npm --prefix web run e2e` runs both, and only `--project=e2e` is the functional half.

**The functional suite signs in once per scenario and shares one source IP**, so it spends a
login rate-limit budget that is a deployment setting rather than a test one
(`login_ip_max_attempts`, 60 per 300 seconds). At 61 scenarios it was one sign-in under that
ceiling, and four more tipped it: four *unrelated* specs went red reporting `current-principal`
missing after login, which reads as an application bug rather than a 429. Both test servers now
raise the per-source budget in `web/playwright.config.ts`. If a batch of new specs ever produces
a cluster of login failures in files it did not touch, look there first.

## How to work here

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow. In short:

- One change at a time, tracked as a GitHub issue, executed on a branch, merged by pull request.
  Only the maintainer merges, and only the maintainer pushes a `v*` release tag
  (`CONTRIBUTING.md`, "Releases"): prepare a release up to the tag and stop.
- **The plan is a file**, at `docs/changes/<N>-<slug>.md`, where `N` is the change's number, its
  GitHub issue number (`CONTRIBUTING.md`, step 2), committed alone as the first commit on the
  branch. It is deleted in the change's final commit, after its durable content has moved into
  the specifications. `docs/changes/README.md` holds the template, how to write a premise, and
  how to write an Accept criterion that can actually fail.
- **A change is designed, attacked and built locally, then pushed once.** The pull request is
  opened when the change is finished and verified, never as a draft beforehand: designing and
  revising a change is a local loop, and GitHub is where a finished change goes. Pushing a
  branch with no pull request runs no CI, so backup pushes are free; the pull request is what
  waits.
- **Every commit is `Glosswork <hello@glosswork.dev>`**, author and committer, set in the
  clone's local git config (`git config user.name Glosswork`, `git config user.email
  hello@glosswork.dev`). CI's `secrets` job fails any other address. The maintainer merges in
  GitHub's web page with "Create a merge commit", choosing `hello@glosswork.dev` as the commit
  email. Never press "Update branch": it writes a merge commit under an address GitHub picks.
  Bring a branch up to date locally instead (`CONTRIBUTING.md`, "Branches and commits").
- **Run an adversarial pass over the plan before executing any of it.** Every adversarial pass
  in this project has found real defects, and the premises that look most solid are the ones
  written from structural measurements rather than from reading the thing they describe.
  Findings land in the plan file as `F1..Fn` with a disposition each.
- Get the plan approved as written. Fold amendments into it in place; `git` holds the history.
- Do not claim completion from inspection. Run the commands.

## Traps

Each of these cost real time at least once.

- **Run the e2e suite as `npm --prefix web run e2e`, never `npx --prefix web playwright test`.**
  `--prefix` changes npm's package resolution, not the working directory, so from the repo root
  Playwright finds no config, treats the repo root as `testDir`, and tries to load the Vitest
  component tests as specs, crashing Node before any test runs. `npm run` uses the package
  directory as cwd, so it cannot go wrong that way.
- **Visual baselines are macOS-only** (`*-darwin.png`) and are deliberately not run in CI. The
  visual project is a local gate. Run it before merging UI work.
- **The visual tolerance is loose enough to hide a short text change.**
  `toHaveScreenshot` runs at `maxDiffPixelRatio: 0.001`, chosen for GPU and antialiasing
  jitter. On a full-page 1280x800 screenshot that permits about 1,024 differing pixels,
  which is more ink than a heading contains: renaming the product changed the login
  page's `<h1>` and all 38 baselines still passed, leaving a committed baseline showing
  the old name. A screenshot is not proof that text is unchanged. Assert text with a
  locator assertion, and treat a passing visual suite as evidence about layout only.
- **Layout is never proven in jsdom.** `getBoundingClientRect` returns zeroes there. A claim
  about geometry is a Playwright assertion or it is not proven.
- **A new visual fixture type is never free.** Seeding a ninth object type once wrapped the app
  shell header and repainted six unrelated baselines. Prefer a new field or value inside an
  existing fixture, and state the repaint count rather than absorbing it.
- **Every assertion must be able to fail, and must be measured in the order written.** Run new
  assertions against the unfixed tree and watch them fail. An assertion guarding behavior the
  change deliberately does not alter cannot fail there by construction: label it a fence, do not
  count it. An assertion that only ever executes after a failing one has never been measured.
- **Retire superseded assertions as part of the change.** Keeping one that asserts the shape the
  change decided against asserts two contradictory outcomes at once.
- **A premise read off a file can be wrong about what the file inherits.** Reading the source
  tells you what a class does, not what it was also silently overriding from an ancestor.
- **The visual project is not the whole check.** Run the full Playwright suite; a functional
  spec is what catches a restructure that breaks a step.
- **A green tally is not a green run: read the exit code.** `npm --prefix web run test` printed
  `68 files, 576 tests, all passed` and exited **1**, because a React 19 form-action path threw
  inside a test without failing any assertion, and vitest reports that separately as an unhandled
  error. Every summary of that run said green, including one written after reading its output
  through a `grep` for the tallies, which discarded the status. Anything that reads a command's
  output for the answer it expects will miss this class; run the command and look at what it
  returned. (This is also the argument for `verify` being a different session: it reports exit
  status rather than reading for a conclusion.)
- **Half a command is not the command.** The backend lint in the table above is
  `uv run ruff check . && uv run ruff format --check .`, and the second half went unrun across
  two whole changes because neither Accept block named it and the first half passes
  on its own. `ruff format` then reported a file that had been unformatted since the change
  before, plus one the running change had just introduced. The frontend has no equivalent gap
  because `npm --prefix web run lint` is one command covering both, which is exactly why the
  backend's two-command form is the one that decays. An Accept block that gates on lint names
  **both** halves, and reads each exit code separately: the split above is also why a
  `cmd-a && cmd-b` chain hides which half failed (see "every assertion must be measured in the
  order written").
- **`uv lock --check` and `uv sync --frozen` never look at the lockfile's registry field, so
  neither is evidence about it.** Measured: with one `source = { registry = ... }` line pointed
  at `https://nope.invalid/simple`, a host that does not exist, `uv lock --check` exits 0 with or
  without the machine's configuration, and `uv sync --frozen` exits 0 and installs all 71
  packages. `--check` compares the lockfile against `pyproject.toml`'s requirements; `--frozen`
  installs from the recorded `files.pythonhosted.org` URLs. Both also exit 0 on a lockfile with
  the wrong index, which is how a private index sat in this file for months with four CI jobs
  running `uv sync --frozen` on every pipeline. What *is* evidence: resolving every recorded
  sha256 from the public index anonymously, and `tests/test_supply_chain.py`, which reads the
  field.
- **`uv.lock` is `-diff` in `.gitattributes`, so `git diff --stat` calls it binary and every
  line-based check over it silently passes.** `git diff --stat uv.lock` reports
  `Bin 312162 -> 309362 bytes` and `--numstat` reports `- -`, so "the diff is 70 insertions and
  70 deletions" and any `grep` over `git diff uv.lock` find nothing and read as a pass, including
  against a deliberately tampered wheel hash. `git diff --text` produces the real diff; `--stat`
  and `--numstat` do **not** honour it, so count the lines out of the diff itself
  (`grep -c '^[+][^+]'`). The same applies to `web/package-lock.json`.
- **No project-level setting overrides a machine-level `uv` index.** Measured seven ways
  against this project's real dependency set, on a machine whose user-level `uv`
  configuration named a private index. Every one of these still wrote that private index
  into `uv.lock`: `[[tool.uv.index]] ... default = true` in `pyproject.toml`; `[tool.uv]
  index-url`; a project-level `uv.toml` with `[[index]] ... default = true`;
  `UV_INDEX_URL`; `UV_DEFAULT_INDEX`; and `uv lock --default-index` on the command line.
  Only `UV_NO_CONFIG=1`, which disables configuration discovery, produced the pinned index.
  The reason nothing ever failed is that the private index was a transparent proxy:
  `uv lock --no-cache -v` showed `Received HTTP 302 Found. Redirecting to
  https://pypi.org/simple/<pkg>/`, so resolution succeeded and the lockfile recorded the
  proxy rather than the destination. The pin in `pyproject.toml` is therefore for
  contributors, not for this machine, and the guard is what holds.
- **An image label is proven by `docker image inspect`, never by reading the `Dockerfile`.**
  A `LABEL` in the wrong stage does not reach the final image, and a `COPY` can be silently
  defeated by `.dockerignore`. The `Dockerfile` is the recipe; the claim is about the
  artifact. Same for a file the image is supposed to carry: `docker create` a container,
  `docker cp` the file out, `docker rm -v`, and diff it against the repository's copy. None
  of that starts the container or contacts a registry.
- **`testpaths = ["tests"]`, so `container_tests` runs only when it is named.**
  `uv run pytest -q` never collects `container_tests/`, and the `image` job in
  `.github/workflows/ci.yml` builds the image without running it either, so everything in
  that directory is a gate a person runs rather than a guard. The structural lane has the matching blind spot the other way:
  `tests/test_structural_lane.py` globs `tests/` only, so a module under `container_tests/`
  needs no `structural` marker and no `MARKED` entry, and gets no protection from either.
  Anything that must run on every pipeline goes in `tests/`.
- **Reading an exit code through a pipe reads the pipe's.** `cmd | tail -1; echo $?` reports
  `tail`'s status, which is almost always 0. This is the same class as the green-tally trap above
  and it bit in the same session that trap is written from: `uv run ruff format --check .` piped
  to `tail` reported success while exiting 1. Redirect to a file and read `$?` on the next line,
  or run the command bare.
- **An unknown path under `/api/v1/` returns `200 text/html`, not a 404**, whenever `web/dist`
  exists, because the SPA's static-file fallback answers anything unmatched. So "the route
  exists" is not something a status code can assert in this application: a test that checks
  `status_code == 200` against a route nobody has written yet **passes**. Assert the response
  body, or the content type. Both `TestAccess` assertions in `tests/test_api_workspace.py` were
  written that way first and both passed against a tree with no such route.

  The `/api/` half of the route is closed: an unknown path whose prefix is exactly `/api/`
  answers 404 JSON whether or not `web/dist` is present. **The assertion rule
  survives the repair and is the part to keep**, because the catch-all still answers every path
  outside `/api/`, `/healthz` and `/readyz` among them.

  **The count of vacuous assertions grew every time somebody looked: five, then nine, then
  thirteen.** All thirteen are repaired. The reason the number moved is a definition, and it is
  worth stating: a status-only assertion is any `assert <client>.get(<path>).status_code == 200`
  or `.request("GET", <path>)` equivalent on a path the catch-all absorbs, and a grep finds only
  the single-line ones whose path its character class happens to admit. The sweep that settled
  the number was an AST walk over every `GET` call in `tests/` and `container_tests/`. Counting
  this class by grep undercounts it; walk the syntax tree.
- **A mutation that does not compile measures nothing.** Deleting a call to prove an assertion
  can fail left a function unused, `tsc -b` failed, `npm run build` failed, and Playwright's
  `webServer` never started — reporting `Process from config.webServer was not able to start`,
  which is red and proves nothing. Same trap as an assertion that fails on a module-resolution
  error. Check that a mutation builds before believing the failure it produces.
- **`for (const el of await locator.all())` with an `await` on each element hangs when the list
  re-renders.** `.all()` snapshots one handle per match; if the page refetches mid-loop — an
  audit filter debouncing after a `fill()`, say — the later handles stop resolving and Playwright
  waits on `.nth(38)` until the **test** times out, reporting a timeout instead of the assertion
  the test is about. Read the whole list in one call (`allTextContents()`) and assert over the
  strings, or use a web-first assertion that retries. This passed locally for two changes and
  failed on both attempts in CI the first time this branch was pushed, on a slower runner with a
  longer table.
- **Deleting code deletes the comment that explains why it was written that way.** A form's
  `navigate()` carried twelve lines about React 19 snapshotting a same-tick router update into
  `FormData`, which throws under Vitest because `test/setup.ts` swaps in undici's. Deleting that
  form deleted the only copy, and the next form built in its place reintroduced the bug within
  the same change. When a deletion takes a workaround with it, move the paragraph to wherever the
  behaviour now lives.
