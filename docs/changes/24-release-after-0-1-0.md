# 24: The version after 0.1.0 is released, carrying changes 7, 9, 11, 20 and 22

| | |
| --- | --- |
| Issue | #24, <https://github.com/glosswork/glosswork/issues/24> |
| Branch | `24-release-after-0-1-0` |
| Spec | `CONTRIBUTING.md` "Releases"; `CHANGELOG.md`; `README.md` "Run it"; read, not changed: `.github/workflows/release.yml`, `docs/DEPLOYMENT.md` sections 2, 4a, 5a and 6 |
| Decisions | none |
| Requirements | none |
| Depends on | nothing unmerged. Change 22 is on `main` at `ac1b349`, and CI passed there (P3) |

## Why

The only published version is 0.1.0, cut before five changes that are now on `main`. A
deployment names the version it runs and there is no `latest` tag, so sign-in by emailed
code (9), the operator backup (20) and the workspace's own TLS with the client-certificate
lock (22) cannot be run from a published image until a new version exists. The hosted
control plane pins a published version and needs the lock.

`CONTRIBUTING.md` "Releases" makes a release a change like any other. This plan is that
change, up to the tag. The tag is the maintainer's.

## Judgment areas

Four things in this plan are the maintainer's to decide. The plan's text below is written
for the recommended answer to each, and each area says exactly what changes under the
other answer. Approving the plan as written approves the four recommendations.

### 1. The version number: 0.1.1 (recommended) or 0.2.0

The rule (`CONTRIBUTING.md`, "Releases"): while the major version is 0, a change an
operator has to act on when upgrading raises `Y`, and everything else raises `Z`. It
names three cases. Each was measured against the published 0.1.0 image and an image built
from `main` (P9):

| The rule's case | What was measured | Result |
| --- | --- | --- |
| A setting renamed or removed | The settings each image defines: 38 in 0.1.0, 44 now | None renamed or removed. Six added, all optional and off by default |
| A migration that cannot be rolled back by restoring a backup | The new image started on a 0.1.0 volume, then 0.1.0 started again on the volume it had migrated | Migration 13 adds two tables and two indexes and changes no existing table. 0.1.0 starts and signs in on the migrated volume |
| A changed API or MCP contract | `/openapi.json`, the MCP tool list and `describe_capabilities` from each | REST: all 73 operations and 32 schemas unchanged, 6 operations and 3 schemas added. MCP: 32 tools, descriptions and input schemas identical, `describe_capabilities` byte-identical |

**One thing does behave differently for an unchanged environment**, and it is what makes
this a choice rather than a lookup. Change 22 refuses to start when any environment
variable begins `GW_TLS_` and is not one of its three. 0.1.0 ignored such a name.
Measured: with `GW_TLS_ENABLED=false` set, 0.1.0 starts and answers `/readyz` with 200;
the new image exits 1 with `Configuration error: GW_TLS_ENABLED: not a setting. ...`.
An operator carrying such a variable has to remove it.

- **0.1.1, recommended.** No setting, route or tool that 0.1.0 defined has changed, and a
  deployment set up as 0.1.0 documents upgrades by starting the new image. The stray-name
  refusal concerns a name no version ever defined, it fails loudly at startup naming the
  variable, and the changelog says so first. Every release that adds a validated setting
  gives meaning to a name the last version ignored; reading that as "an operator has to
  act" would make every feature release a `Y`, and `Y` would stop meaning "read this
  before you upgrade".
- **0.2.0.** The strict reading: an environment exists that ran 0.1.0 and does not start
  on this version, so an operator may have to act. It costs nothing to build. It spends
  the `Y` signal on a case that, as far as is known, no deployment is in.

Under 0.2.0 every `0.1.1` in this plan reads `0.2.0`: the version, the lockfile line, the
changelog heading, the README tag, the Accept block and the tag command. Nothing else
changes.

### 2. The changelog entry's text

Customers read it. It is under "The changelog entry" below, in full, and it is the text
the build writes, byte for byte. Three choices are inside it:

- **It opens with an Upgrading paragraph although the recommended number is a `Z`.** The
  paragraph says a deployment set up as documented has nothing to do, and then names the
  one thing to check (area 1). The alternative is no Upgrading paragraph, with the
  stray-name refusal folded into line 22. Recommended as written: an operator looks under
  Upgrading, and "nothing else to do" is itself worth saying.
- **It does not say that 0.1.0 starts on a volume this version has migrated.** That was
  measured once, on a workspace that had used none of the new features (P9), and a
  changelog line would be read as a promise about downgrading. Left out.
- **It does not mention `WEB_CONCURRENCY` under Upgrading.** 0.1.0 did not stay up with
  that variable set to 2 (P9), so no running 0.1.0 deployment is affected. Line 11 says
  what is now true.

### 3. The README's run line moves to the new version in this change (recommended)

`README.md` line 23 names `docker.io/glosswork/glosswork:0.1.0`, and nothing ties it to
the version (P11). "Releases" lists three things a release sets, and the README is not
one of them, so as the rule stands the README would go on naming 0.1.0.

- **Recommended: this change sets it to 0.1.1, and "Releases" gains the clause that says
  a release does so**, so the next release does not depend on someone remembering. The
  cost: from the merge until the maintainer's tag has published, the README on `main`
  names an image that does not exist yet. That is minutes when the tag follows the
  merge, and the repository is private today. If the release run fails, it lasts until
  the run is fixed.
- **Alternative: leave the README at 0.1.0** and move it in a separate
  documentation-only change after the image is published. The README is never ahead of
  the registry, at the cost of a second change for every release. Under this answer
  checklist step 6, AC4 and the "Releases" clause in "Durable content" are struck, and
  `README.md` and `CONTRIBUTING.md` leave AC6's list.

### 4. One dry run of the release workflow on `main`, before the build (recommended)

`gh workflow run release.yml --ref main` builds and tests both architectures on their
real runners, runs the notices check, and stops. Nothing signs in, pushes or publishes
(`CONTRIBUTING.md`, "Releases"; read in `.github/workflows/release.yml`: every sign-in and
push step, and the whole `publish` job, is `if: github.event_name == 'push'`).

**Why it belongs.** The release workflow is the only place `container_tests` runs on
`linux/amd64` or on the release runners, and it last ran at `0d6fd1f`, before all five
changes (P10). The suite has grown from 30 test functions to 38 since, including change
22's TLS tests, whose refused-client symptom on those runners was recorded as not
established when change 22 merged. This run measured the suite on one arm64 laptop only
(P6). The first dry run this project ever did failed on both runners on proofs sized on
a laptop. If the suite fails on a runner, the defect is in a merged change and needs its
own change before any tag: finding that before this change is built is cheaper than
finding it after the changelog is merged.

**Why once, and on `main` now.** This change touches no code, test, lockfile dependency
or image input other than the version string, so what the runners would test at
`ac1b349` is what they will test at the release commit. The tag run itself repeats the
same build and test before it publishes anything, and publishes nothing if they fail.

**What it costs.** The last dry run (run 36618298361, at `0d6fd1f`) took 7 minutes 6
seconds of wall time: `preflight` 5 s, `build-amd64` 404 s, `build-arm64` 404 s. Billed
by the minute per job that is 8 x64 minutes and 7 arm64 minutes. The suite is larger now
(280 s on this machine for the container tests alone), so expect about 10 minutes of
wall time and about 20 billed minutes, a few cents at the per-minute prices if the
plan's included minutes are used up, and nothing if they are not. It uses billable
runner minutes, so the maintainer starts it or approves an agent to. Whether the agent's
token may start a workflow is not established: it has never tried.

- **Recommended: checklist step 1 is that dry run, and the build does not start until
  all three of its jobs conclude `success`.**
- **Alternative: no dry run.** The tag run is then the first time the suite meets the
  runners. A failure there publishes nothing, but it happens after the release change is
  merged and the maintainer has tagged, and the tag has to be deleted and pushed again
  once a fix merges. Under this answer checklist step 1 is struck.

## The changelog entry

This block goes into `CHANGELOG.md` directly above `## 0.1.0`, followed by one blank
line. It is the only `markdown` fence in this file, which AC3 relies on.

```markdown
## 0.1.1

**Upgrading.** Pull the new image and start it on the same volume. A deployment set up as
0.1.0 documents has nothing else to do: the first start adds two tables and changes no
existing one, and no setting, REST route or MCP tool that 0.1.0 had is renamed or removed.
One thing to check first: an environment variable whose name begins `GW_TLS_`, other than
the three in change 22 below, now stops the workspace at startup, naming the variable.
0.1.0 ignored such a name. Remove it.

- 7: the README's run line pulls the published image, where it used to build one from
  source.
- 9: a workspace run by a hosting control plane signs people in with a six-digit code sent
  to their email address, and its administrators invite people by email. This is on only
  when `GW_RELAY_URL` and `GW_RELAY_TOKEN` are both set, and password sign-in is then off.
  A self-hosted workspace sets neither and keeps passwords or its own identity provider.
- 11: the image runs one process whatever `WEB_CONCURRENCY` says, and `docs/DEPLOYMENT.md`
  says what pausing a container does to a workspace. Indexing is unaffected by a pause.
- 20: where a deployment sets `GW_OPERATOR_BACKUP=true`, the operator token takes a backup
  at `POST /api/v1/operator/backup`. It is off by default, and the token opens nothing else
  new. No backup holds a usable sign-in code, and an abandoned backup download leaves no
  copy of the database on the volume.
- 22: a workspace can terminate TLS itself and answer only a caller whose client
  certificate chains to a named authority, with `GW_TLS_CERT_FILE`, `GW_TLS_KEY_FILE` and
  `GW_TLS_CLIENT_CA_FILE`, all three or none. With none it serves plain HTTP behind your
  own proxy, as before.

```

Where each line's claims come from: line 7, change 7's diff (`README.md` only). Line 9,
`docs/DEPLOYMENT.md` section 5a. Line 11, section 2's "Pausing is not stopping" and the
`workers=1` in `src/glosswork/entrypoint.py`. Line 20, section 6 ("The operator backup",
"An abandoned download leaves nothing behind") and section 5a ("The relay token also
protects stored codes"). Line 22, section 4a. The Upgrading paragraph, P9.

## Premises

Every one was established on 2026-10-02. "The scratch clone" is a separate local clone of
this repository at `ac1b349` with this plan's edits applied (version 0.1.1, the lockfile
line, the changelog entry above, the README line). Nothing in the working clone was
edited to establish a premise.

- **P1. The current release is `v0.1.0` on `0d6fd1f`, and five changes have merged
  since.** `git ls-remote --tags origin` lists one tag, `v0.1.0`, peeled to `0d6fd1f`.
  `git log --oneline --first-parent v0.1.0..origin/main` lists five merges: `aeaaf02`
  (#7), `5988eef` (#9), `753af3c` (#11), `42e9d45` (#20), `ac1b349` (#22).

- **P2. The version is written in two places a release sets, and named in one more.**
  `git grep -n -F '0.1.0' origin/main`, leaving out the two lockfiles and the generated
  licences file, finds `pyproject.toml:3`, `CHANGELOG.md` lines 6 and 12 (the 0.1.0
  entry, which stays), and `README.md:23`. `uv.lock` line 365 is the `glosswork` entry's
  version. No file under `tests/`, `container_tests/`, `scripts/`, `src/` or `.github/`
  names it. `web/package.json` reads `0.0.0` and is not the product's version.
  **`/openapi.json` reports `0.1.0` on both images and will go on doing so**: the
  application passes no version to FastAPI, whose default happens to be that string
  (read at `src/glosswork/app.py:456`; measured on the scratch image, whose installed
  package reports 0.1.1). It is not the product's version and nothing should read it as
  one. This change leaves it alone.

- **P3. CI passed on `main` for `ac1b349`.** Actions run 37035676535 (`ci.yml`, event
  `push`, branch `main`): `completed`, `success`, finished 16:53:01Z, all eleven jobs
  `success`, `ci-ok` among them. Read from `actions/runs/37035676535` and its `/jobs`.

- **P4. Re-locking rewrites every registry line on this machine, and the substitution
  puts back all but the one that should change.** In the scratch clone, with
  `pyproject.toml` at 0.1.1: `uv lock --check` exits 1 before the lock (so it can fail);
  `uv lock` exits 0, prints `Updated glosswork v0.1.0 -> v0.1.1`, and
  `git diff --text uv.lock | grep -c '^[+][^+]'` prints 71: the version line and all 70
  `registry =` lines, rewritten to a private index. After
  `sed -i '' -E 's|registry = "[^"]*"|registry = "https://pypi.org/simple"|g' uv.lock`,
  `git diff --text uv.lock` is one hunk: `-version = "0.1.0"`, `+version = "0.1.1"`,
  one line added and one removed. `uv lock --check` then exits 0. No dependency version
  moved. `git diff --stat` reports the file as binary with equal sizes, as
  `AGENTS.md` warns, so the counts above come from `--text`.

- **P5. The repository's own checks pass with the edits in place.** In the scratch
  clone: `uv run pytest -q tests/test_supply_chain.py tests/test_third_party_licenses.py
  tests/test_release_workflow.py` exits 0, 22 passed (the licences file needs no
  regeneration: no dependency changed). `uv run pytest -q -m structural` exits 0, 101
  passed, with the changelog entry and the README line in place, so the entry's text
  passes the documentation guards. The whole backend suite, `uv run pytest -q`, exits 0:
  2215 passed, 3 xfailed. CI runs all of it again on the pull request:
  `scripts/ci_changes.py` given this change's five paths prints `code=true`.

- **P6. An image built from the edited tree carries the version, passes the notices
  check and passes `container_tests`, on arm64.** `docker build` of the scratch clone
  exits 0. Inside it `importlib.metadata.version("glosswork")` prints `0.1.1` (the
  published image prints `0.1.0`). `uv run python scripts/notices_coverage.py --image
  <that image>` exits 0: python 57 of 57 covered, npm 107 of 107. `GW_IMAGE=<that image>
  uv run pytest -q -rs container_tests` exits 0: 40 passed, 1 skipped, in 280 s. The skip
  is `test_image_notices.py:172`, which cannot know the revision of an image the run did
  not build; with `GW_IMAGE` unset it runs. **Not established:** `linux/amd64`, and
  either architecture on the release workflow's runners. That is area 4.

- **P7. The release workflow's preflight would accept the tag, as far as can be shown
  before the merge commit exists.** Read in `.github/workflows/release.yml`, then each
  check run by hand:
  - *The tag is `vX.Y.Z`.* The workflow's own pattern accepts `v0.1.1` and `v0.2.0` and
    refuses `v0.1.01` and `0.1.1`.
  - *`X.Y.Z` is the project's version.* The workflow's own line, `python3 -c 'import
    tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'`,
    prints `0.1.1` in the scratch clone and `0.1.0` on `main`.
  - *CI passed on `main` for the commit.* The workflow's two queries, run against
    `ac1b349`, each return 1 (and returned 0 while that run was still in progress, so
    the check can fail). Against `0d6fd1f` each returns 1. For this change's merge
    commit it cannot be shown until the commit exists; the two merges before this one
    took 10 and 11 minutes to go green on `main`. The maintainer tags after that run is
    green, or the release run stops at preflight and is re-run.
  - *The notices check.* P6.
  - *The version does not exist in either registry.* P8.

- **P8. Neither registry holds 0.1.1 or 0.2.0.** With an empty Docker configuration,
  `docker buildx imagetools inspect docker.io/glosswork/glosswork:0.1.1` exits 1 with
  `not found`, the same for `0.2.0`, and exits 0 for `0.1.0`. The GitHub registry
  refuses an anonymous read, so it was read through the API:
  `orgs/glosswork/packages/container/glosswork/versions` lists one tagged version,
  `0.1.0`.

- **P9. What each of the five changes requires of an operator upgrading from 0.1.0:
  nothing, with one exception.** Read from each merge's diff, then measured by running
  the published image `docker.io/glosswork/glosswork:0.1.0` (index
  `sha256:12c783a9...89b4`, revision label `0d6fd1f`) and the scratch image.

  | Change | What it brings | What an upgrading operator must do |
  | --- | --- | --- |
  | 7 | `README.md` only | Nothing |
  | 9 | Migration 13 (two new tables, two indexes); `GW_RELAY_URL` and `GW_RELAY_TOKEN`, off unless both set; five new routes; `GET /api/v1/auth/modes` gains `email_code` | Nothing. With neither variable set, password sign-in is as before |
  | 11 | `workers=1`; `WEB_CONCURRENCY` ignored; documentation | Nothing |
  | 20 | `GW_OPERATOR_BACKUP`, off by default; one new route; staged backups cleaned up | Nothing. Unset, the operator token opens what it opened before |
  | 22 | `GW_TLS_CERT_FILE`, `GW_TLS_KEY_FILE`, `GW_TLS_CLIENT_CA_FILE`, all or none | Nothing, unless the environment carries some other name beginning `GW_TLS_`: remove it |

  Measured:
  - *Settings.* `Settings.model_fields` in each image: 38 and 44. In the old and not in
    the new: none. Added: the six above.
  - *The upgrade.* 0.1.0 on a fresh volume: ready in 3 s, migrations 1 to 12, password
    sign-in 200. The scratch image on the same volume with the same environment: ready
    in 3 s, logs `migrations_applied` `[13]`, tables `invites` and `sign_in_codes` exist,
    the same password signs in with 200, and every structured log line of that start is
    at `info`.
  - *Going back.* 0.1.0 started again on that migrated volume: ready in 3 s, the same
    sign-in 200. Its runner applies what is pending and ignores a row it does not know
    (`src/glosswork/migrations.py`, `pending_migrations`). Measured on a workspace that
    had used none of the new features; not a claim about one that has.
  - *REST.* `/openapi.json` from each: 73 operations and 32 schemas in 0.1.0, every one
    of them byte-identical in the new image, which adds 6 operations
    (`/api/v1/auth/code/request`, `/api/v1/auth/code/verify`, three on
    `/api/v1/invites`, `/api/v1/operator/backup`) and 3 schemas. `GET
    /api/v1/auth/modes` answered `{"standalone":true,"oidc":false}` and now answers the
    same with `"email_code":false` added.
  - *MCP.* A real client session against each: 32 tools in both, every description and
    input schema identical, `instructions` identical, `describe_capabilities`
    byte-identical (19,640 bytes).
  - *The exception.* With `GW_TLS_ENABLED=false`: 0.1.0 runs and `/readyz` answers 200;
    the scratch image exits 1 with the `Configuration error:` line naming the variable.
  - *`WEB_CONCURRENCY=2`.* 0.1.0 exits within 12 seconds with code 0, after a clean
    shutdown in its log, and serves nothing; with `WEB_CONCURRENCY=1` it runs. The
    scratch image with `WEB_CONCURRENCY=2` runs one process. So no running 0.1.0
    deployment has that variable above 1.

- **P10. The release workflow has run three times, never since `0d6fd1f`.** From
  `actions/workflows/release.yml/runs`: a failed dry run at `8ec0cc9`, a successful dry
  run at `0d6fd1f` (36618298361: `preflight` 5 s, each build 404 s), and the `v0.1.0`
  tag run (36619380689: builds 420 s and 408 s, `publish` 66 s). `container_tests` held
  30 test functions at `v0.1.0` and holds 38 on `main`
  (`git grep -c 'def test_'` at each). CI's `image` job builds the image and does not run
  the suite (`AGENTS.md`, "Traps").

- **P11. Nothing ties the README's tag to the version.** `README.md:23` reads
  `docker.io/glosswork/glosswork:0.1.0`; no test names that line (P2); "Releases" does
  not list the README. Change 7's adversarial pass recorded exactly this and left it to
  the maintainer.

## What changes

- `pyproject.toml`: `version = "0.1.1"`.
- `uv.lock`: the `glosswork` entry's `version = "0.1.1"`, and no other line.
- `CHANGELOG.md`: the entry under "The changelog entry", above `## 0.1.0`.
- `README.md`, "Run it": the run line names `docker.io/glosswork/glosswork:0.1.1`
  (area 3).
- At closeout, `CONTRIBUTING.md` "Releases": the clause under "Durable content" (area 3).

None of these is a file that defines CI (`CONTRIBUTING.md`, "What CI runs").

## What does not change

- Any file under `src/`, `tests/`, `container_tests/`, `scripts/`, `web/` or `.github/`.
- `Dockerfile`, `THIRD_PARTY_LICENSES.md`, `THIRD_PARTY_NOTICES.md`: no dependency moves.
- The 0.1.0 entry in `CHANGELOG.md`, and every line of `README.md` but the one tag.
- The version `/openapi.json` reports (P2).
- No tag is created or pushed by this change, and nothing is published by it.

## Constraints

- **Only the maintainer pushes a `v*` tag.** The build, the verification and the closeout
  stop at the pull request.
- **`uv lock` is followed by the substitution, in place.** Never `uv lock` against a
  deleted lockfile (`AGENTS.md`, non-negotiable 1). If `git diff --text uv.lock` shows
  anything but the one version line, stop.
- **The changelog entry is the approved text, byte for byte.** A wording change is the
  maintainer's, made in this plan first.
- Every commit is `Glosswork <hello@glosswork.dev>`, and the branch has one root commit.
- No em dashes. Any backticked `*.md` path names a file on this branch.

## Checklist

- [ ] 1. The dry run of area 4, on `main` at `ac1b349`, started by the maintainer or with
  the maintainer's approval: `gh workflow run release.yml --ref main`. Read its jobs from
  `actions/runs/<id>/jobs`. Go on only when `preflight`, `build-amd64` and `build-arm64`
  all conclude `success` and `publish` is `skipped`. If a build fails, stop: the defect
  is in a merged change and gets its own issue.
- [ ] 2. Run AC1 to AC5 against the unfixed tree and record how each failed. Measured
  while planning, on `main`: AC1 prints `0.1.0`; AC2's lock line reads `0.1.0`; AC3's
  first heading is `## 0.1.0` and its count is 0; AC4's counts are 0 and 1; AC5 compares
  `0.1.1` with `0.1.0`.
- [ ] 3. Set `version = "0.1.1"` in `pyproject.toml`.
- [ ] 4. `uv lock`, then the substitution from `AGENTS.md` non-negotiable 1, then confirm
  the diff is the one version line (AC2).
- [ ] 5. Add the changelog entry, as written above.
- [ ] 6. Set the README's run line to `docker.io/glosswork/glosswork:0.1.1`.
- [ ] 7. Commit as `24: the version is 0.1.1, and the changelog says what it carries`.
- [ ] 8. Run the Accept block and record its output.

Verification, the two closeout commits, the one push and the one pull request follow as
`docs/changes/README.md` sets out.

## Accept

Each exit code is read on its own line, never through a pipe.

- **AC1.** The version is set, read the way the release workflow reads it:
  `uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'`
  prints `0.1.1`.
- **AC2.** The lockfile changed by its one line. `uv lock --check` exits 0.
  `git diff --text origin/main -- uv.lock | grep -c '^[+][^+]'` prints 1, the same with
  `'^[-][^-]'` prints 1, and `git diff --text origin/main -- uv.lock | grep '^[+][^+]'`
  prints `+version = "0.1.1"`. `grep -c 'registry = "https://pypi.org/simple"' uv.lock`
  and `grep -c 'registry = ' uv.lock` print the same number.
- **AC3.** The changelog carries the approved entry and has lost nothing.
  `grep -m1 '^## ' CHANGELOG.md` prints `## 0.1.1`.
  `awk '/^## 0\.1\.1$/{f=1} /^## 0\.1\.0$/{f=0} f' CHANGELOG.md | grep -c -E '^- (7|9|11|20|22): '`
  prints 5. That same `awk` output is identical to this plan's entry:
  `diff <(awk '/^## 0\.1\.1$/{f=1} /^## 0\.1\.0$/{f=0} f' CHANGELOG.md) <(awk '/^```markdown$/{f=1;next} /^```$/{f=0} f' docs/changes/24-release-after-0-1-0.md)`
  exits 0. `git diff origin/main -- CHANGELOG.md | grep -c '^-[^-]'` prints 0.
- **AC4.** The README names the version and no longer names the last one:
  `sed -n '/^## Run it/,/^## Point/p' README.md | grep -c 'docker.io/glosswork/glosswork:0.1.1$'`
  prints 1, and `grep -c 'glosswork/glosswork:0.1.0' README.md` prints 0.
- **AC5.** The tag the maintainer will push matches: with `TAG=v0.1.1`, the workflow's
  pattern `^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$` matches it in `bash`,
  and `${TAG#v}` equals AC1's output.
- **AC6.** Nothing else changed. Before the closeout,
  `git diff --name-only origin/main...HEAD` prints `CHANGELOG.md`, `README.md`,
  `docs/changes/24-release-after-0-1-0.md`, `pyproject.toml` and `uv.lock`. After it,
  the plan file is gone from that list and `CONTRIBUTING.md` is in it.
- **AC7 (fence).** The guards: `uv run pytest -q -m structural` exits 0, and
  `uv run pytest -q tests/test_supply_chain.py tests/test_third_party_licenses.py`
  exits 0. A fence: both pass on the unfixed tree.
- **AC8 (fence).** The whole backend suite, `uv run pytest -q`, exits 0, and the lint in
  both halves, `uv run ruff check .` then `uv run ruff format --check .`, each exits 0.
- **AC9.** The image carries the version and its notices. With `GW_IMAGE` unset,
  `uv run pytest -q -rs container_tests` exits 0 with nothing skipped, on a clean tree,
  so the image it builds is this commit's. Then
  `docker run --rm --entrypoint python glosswork:container-test -c 'import importlib.metadata as m; print(m.version("glosswork"))'`
  prints `0.1.1`, and
  `uv run python scripts/notices_coverage.py --image glosswork:container-test` exits 0.
- **AC10 (fence).** The version is free in both registries: with an empty
  `DOCKER_CONFIG`, `docker buildx imagetools inspect docker.io/glosswork/glosswork:0.1.1`
  exits 1 with `not found`, and the GitHub package's version list holds no `0.1.1`.

## After the merge, for the maintainer

Not part of the branch. Once the pull request is merged and CI is green on `main` for
the merge commit (the same query as P7, returning 1), from a clone whose `user.email` is
`hello@glosswork.dev`:

```
git fetch origin
git tag -a v0.1.1 -m "0.1.1" <merge commit>
git push origin v0.1.1
```

The release run's `preflight`, `build-amd64`, `build-arm64` and `publish` should all
conclude `success`. Then `docker.io/glosswork/glosswork:0.1.1` pulls with no login for
both architectures, and its revision label is the merge commit. That is the version the
hosted control plane pins.

## Baseline repaint

None. No UI change.

## Adversarial pass

Not yet run. It is run by a session that did not write this plan. Places worth attacking
first: whether anything an operator must do was missed in P9, since it was established on
a nearly empty workspace; each sentence of the changelog entry against the section it
cites; whether AC3's `awk` comparison can pass on a wrong entry; and area 3's window in
which the README is ahead of the registry.

## Deviations from the approved plan

None yet.

## Durable content moved out of this plan

At closeout, under area 3 as recommended, `CONTRIBUTING.md` "Releases", second
paragraph: "It sets the new version in `pyproject.toml`, updates the `glosswork` entry
in `uv.lock` to match, names the new version in the run line of `README.md`, and adds
the version's entry to `CHANGELOG.md`." The changelog entry is itself durable and stays
where the build puts it. Nothing else here outlives the change.
