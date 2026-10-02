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
for one answer to each, and each area says exactly what changes under the other answer.
Approving the plan as written approves those four answers: version 0.1.1, the changelog
entry as it now reads, the README line moved in this change, and one dry run first. The
adversarial pass changed the evidence under area 1 and the text under area 2, and its
findings F1, F2 and F5 are the ones to read before approving.

### 1. The version number: 0.1.1 or 0.2.0

The plan's text is written for 0.1.1, which the planning run recommended. The adversarial
pass re-measured the question and makes no recommendation: the evidence for each number is
set out below, and the number is the maintainer's. Approving the plan as written approves
0.1.1.

The rule (`CONTRIBUTING.md`, "Releases"): while the major version is 0, a change an
operator has to act on when upgrading raises `Y`, and everything else raises `Z`. It
names three cases. Each was measured against the published 0.1.0 image and an image built
from `main`, first on a nearly empty workspace (P9) and then, in the adversarial pass, on
one holding real data (F3):

| The rule's case | What was measured | Result |
| --- | --- | --- |
| A setting renamed or removed | The settings each image defines, and each one's default: 38 in 0.1.0, 44 now | None renamed or removed, and no default changed. Six added, all optional and off by default |
| A migration that cannot be rolled back by restoring a backup | A 0.1.0 workspace with records, an attachment, a comment, two people, two tokens and a live browser session: upgraded; taken back to 0.1.0 on the migrated volume; taken back again after invites and sign-in codes had been used; and restored from a backup 0.1.0 took before the upgrade | Migration 13 adds two tables and two indexes and changes no existing table. Every step came up ready in 3 or 4 seconds with the same records, search results, attachment bytes, tokens and session, and accepted a write |
| A changed API or MCP contract | `/openapi.json`, the MCP tool list and `describe_capabilities` from each | REST: all 73 operations and 32 schemas unchanged, 6 operations and 3 schemas added. MCP: 32 tools, descriptions and input schemas identical, `describe_capabilities` identical |

**None of the three named cases applies. What does differ for an unchanged environment is
this, and it is what makes the number a choice rather than a lookup.** This version reads
six environment variables that 0.1.0 ignored, and refuses every other name under one
prefix. Each row was run on both images in the adversarial pass (F1, F2):

| The environment already carries | 0.1.0 | This version |
| --- | --- | --- |
| Any other name beginning `GW_TLS_` (`GW_TLS_ENABLED=false`, lower-case `gw_tls_mode=off`, `GW_TLS_PROXY_SERVICE_HOST=10.0.0.9`) | Runs, `/readyz` 200 | Exits 1 at startup, naming the variable |
| One of the three TLS names without the other two | Runs | Exits 1, naming the missing two |
| `GW_RELAY_URL` or `GW_RELAY_TOKEN` alone | Runs | Exits 1, naming the missing one |
| Both relay names, well formed | Runs, password sign-in 200 | **Runs, and password sign-in answers 409.** The one difference that is not loud |
| `GW_OPERATOR_BACKUP` blank, or `true` with no operator token | Runs | Exits 1, naming the variable |
| `WEB_CONCURRENCY=2` or `4`, on a volume that already holds a workspace | Runs 2 or 4 processes | Runs one process. Nothing to do |

- **For 0.1.1.** No setting, route or tool that 0.1.0 defined has changed, a deployment
  set up as 0.1.0 documents upgrades by starting the new image, and going back works,
  with and without a backup. Every refusal above concerns a name no version ever defined
  and 0.1.0's documents never mention; all but one fail loudly at startup naming the
  variable, and the changelog says so first. Every release that adds a validated setting
  gives meaning to a name the last version ignored; reading that as "an operator has to
  act" would make every feature release a `Y`, and `Y` would stop meaning "read this
  before you upgrade".
- **For 0.2.0.** The strict reading: environments exist that ran 0.1.0 and do not start
  on this version, so an operator may have to act. Three things the planning run did not
  have make that less remote than "a case no deployment is in". It is six names and a
  whole prefix, not one name. A `GW_TLS_` name can arrive without anybody typing it:
  Kubernetes gives every container a `<NAME>_SERVICE_HOST` variable for each Service in
  its namespace, so a Service called `gw-tls-proxy` is enough (the injection is
  Kubernetes' documented behaviour and was not reproduced here; that such a name stops
  this version was). And who runs 0.1.0 is not known: Docker Hub reports 293 pulls of the
  image since 2026-09-29, an unknown share of them this project's own. `0.2.0` costs
  nothing to build. A third-digit change is also what people and update tools take
  unattended, and this one carries three features.

Under 0.2.0 every `0.1.1` in this plan reads `0.2.0`: the version, the lockfile line, the
changelog heading, the README tag, the Accept block and the tag command. That includes the
escaped form in AC3's two `awk` programs, where `0\.1\.1` becomes `0\.2\.0` and
`0\.1\.0` stays. Nothing else changes.

**Decided: 0.2.0.** The maintainer chose 0.2.0 on 2026-10-02, and the substitution above
is applied in this file: in area 3's recommendation, the changelog entry's heading, "What
changes", checklist steps 2, 3, 6 and 7, the Accept block including AC3's two `awk`
programs, and "After the merge". This area's own text, the sentence in the introduction
above that names the four answers, the premises and the adversarial pass still name the
planning run's number where they record what was argued or measured with it, and are left
as written: the scratch clone and its image were at that number.

### 2. The changelog entry's text

Customers read it. It is under "The changelog entry" below, in full, and it is the text
the build writes, byte for byte. Three choices are inside it:

- **It opens with an Upgrading paragraph. When this was written the number was 0.1.1, a
  `Z`, which is what made the paragraph a choice; the number is now 0.2.0, a `Y`
  (area 1).** The paragraph says a deployment set up as documented has nothing to do, and then
  names what to check (area 1). The alternative is no Upgrading paragraph, with the
  stray-name refusal folded into line 22. Recommended as written: an operator looks under
  Upgrading, and "nothing else to do" is itself worth saying.
- **The check it names is six variables, not one prefix.** The planning run's paragraph
  told an operator to look for a stray `GW_TLS_` name only. The adversarial pass measured
  the same kind of difference for `GW_RELAY_URL`, `GW_RELAY_TOKEN` and
  `GW_OPERATOR_BACKUP` (area 1's second table), one of them silent, so the paragraph now
  names all six. The sentence it replaced is kept in F1 for comparison. This is a change
  to the customer-facing text since the planning run, made by the adversarial pass.
- **It does not say that 0.1.0 starts on a volume this version has migrated.** That is
  now measured on a workspace with real data, before and after invites and sign-in codes
  were used (F3), on one machine. A changelog line would still be read as a promise about
  downgrading, and one thing does not come back with it: a person who joined by invite
  has no password, so on 0.1.0 an administrator has to set one. Left out.
- **It does not mention `WEB_CONCURRENCY` under Upgrading. The planning run's reason for
  that was wrong, and whether to mention it is now a real choice.** 0.1.0 exits with that
  variable set to 2 only on an empty volume, where two processes race to create the
  database. On a volume that already holds a workspace it runs two processes, or four,
  and answers `/readyz` (F2). So a 0.1.0 deployment can be running with it set, and after
  upgrading it runs one process. That needs nothing from the operator, 0.1.0's documents
  never mention the variable, and line 11 says what is now true, so the entry is left as
  it was. The alternative is one more sentence under Upgrading: "If you set
  `WEB_CONCURRENCY`, it is now ignored and the workspace runs one process."
- **Line 11's last sentence describes, it does not announce a fix.** "Indexing is
  unaffected by a pause" is true of this version and was true of 0.1.0 run as one process:
  change 11 changed the indexing worker's comments and documentation, and its one change
  to behaviour is the single process (F4). Left as written; the alternative is to end the
  line at "does to a workspace".

### 3. The README's run line moves to the new version in this change (recommended)

`README.md` line 23 names `docker.io/glosswork/glosswork:0.1.0`, and nothing ties it to
the version (P11). "Releases" lists three things a release sets, and the README is not
one of them, so as the rule stands the README would go on naming 0.1.0.

- **Recommended: this change sets it to 0.2.0, and "Releases" gains the clause that says
  a release does so**, so the next release does not depend on someone remembering. The
  cost: from the merge until the maintainer's tag has published, the README on `main`
  names an image that does not exist yet. **That is 20 minutes at the least, not a few**
  (F9): the tag cannot be pushed until CI is green on `main` for the merge commit, which
  took 10 to 11 minutes for each of the last four code merges, and the release run then
  took 8 minutes 25 seconds for `v0.1.0`. In that window the README's command fails
  loudly, with the registry's "not found". The repository is private today, so its only
  readers are the maintainer and the agents. If the release run fails, the window lasts
  until the run is fixed.
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
by the minute per job that is 8 x64 minutes and 7 arm64 minutes. The planning run
expected about 10 minutes of wall time and about 20 billed minutes because the suite has
grown. The adversarial pass measured the growth and that estimate is on the high side
(F10): the tests added since `v0.1.0` take 13 of the suite's 267 seconds on this machine,
because one unchanged file, `container_tests/test_clean_shutdown.py`, is 204 of them. So
expect about 8 minutes of wall time and about 17 billed minutes. At GitHub's listed prices
on 2026-10-02, $0.006 a minute for the x64 runner and $0.005 for the arm64 one, that is
about 10 cents if the organisation's included minutes are used up, and nothing if they
are not: its Team plan includes 3,000 a month. How many of those are left this month
could not be read with the agent's token. It uses billable runner minutes, so the
maintainer starts it or approves an agent to. **The agent's token can start it**: it is a
classic token whose scopes include `repo` and `workflow`, with push access to the
repository, which is what the dispatch call requires. That is read from the token's
scopes; no run was started to prove it.

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
## 0.2.0

**Upgrading.** Pull the new image and start it on the same volume. A deployment set up as
0.1.0 documents has nothing else to do: the first start adds two tables and changes no
existing one, and no setting, REST route or MCP tool that 0.1.0 had is renamed or removed.
One thing to check first: this version reads six environment variables that 0.1.0 ignored,
`GW_RELAY_URL`, `GW_RELAY_TOKEN`, `GW_OPERATOR_BACKUP` and the three `GW_TLS_` names in
change 22 below, and it refuses to start, naming the variable, on any other name that
begins `GW_TLS_`. If your environment already carries one of them, remove it unless you
mean what it now does.

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
protects stored codes"). Line 22, section 4a. The Upgrading paragraph, P9 and F1. The
adversarial pass checked each line against the image built from `main`, not only against
the section it cites (F4).

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
  **The adversarial pass did not re-run this step** (F7): its session's permission check
  refused the command, so the measurement above is the planning run's alone. Read
  instead: all 70 registry lines on `main` name `https://pypi.org/simple` and the only
  other source is the project itself, so the substitution cannot overwrite an index that
  should stay.

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
  - *The tag is annotated, and the checks read a commit.* `v0.1.0` is an annotated tag
    (`git cat-file -t v0.1.0` prints `tag`, tagger `hello@glosswork.dev`) and its run,
    36619380689, passed every job, so the workflow's "is a commit", "is on main" and CI
    queries are shown to work for the kind of tag the command below makes (F8).
  - *`publish` waits on nobody.* The `release` environment has one rule, a tag policy of
    `v*`, and no required reviewer, read from the environments API. What happens when
    `publish` fails part way is under "After the merge" (F8).

- **P8. Neither registry holds 0.1.1 or 0.2.0.** With an empty Docker configuration,
  `docker buildx imagetools inspect docker.io/glosswork/glosswork:0.1.1` exits 1 with
  `not found`, the same for `0.2.0`, and exits 0 for `0.1.0`. The GitHub registry
  refuses an anonymous read, so it was read through the API:
  `orgs/glosswork/packages/container/glosswork/versions` lists one tagged version,
  `0.1.0`.

- **P9. What each of the five changes requires of an operator upgrading from 0.1.0:
  nothing, unless the environment already carries one of the names this version starts
  to read.** The adversarial pass corrected two things in this premise, both marked
  below: the names are six and a prefix, not one prefix (F1), and the `WEB_CONCURRENCY`
  conclusion was wrong (F2). It also repeated the upgrade on a workspace with real data
  (F3). Read from each merge's diff, then measured by running
  the published image `docker.io/glosswork/glosswork:0.1.0` (index
  `sha256:12c783a9...89b4`, revision label `0d6fd1f`) and the scratch image.

  | Change | What it brings | What an upgrading operator must do |
  | --- | --- | --- |
  | 7 | `README.md` only | Nothing |
  | 9 | Migration 13 (two new tables, two indexes); `GW_RELAY_URL` and `GW_RELAY_TOKEN`, off unless both set; five new routes; `GET /api/v1/auth/modes` gains `email_code`; `httpx2` 2.12.0 to 2.13.1, the only locked dependency that moved | Nothing, unless the environment already carries either relay name (F1). With neither set, password sign-in is as before |
  | 11 | `workers=1`; `WEB_CONCURRENCY` ignored; documentation | Nothing. A deployment that set `WEB_CONCURRENCY` above 1 goes from that many processes to one (F2) |
  | 20 | `GW_OPERATOR_BACKUP`, off by default; one new route; staged backups cleaned up | Nothing, unless the environment already carries `GW_OPERATOR_BACKUP` (F1). Unset, the operator token opens what it opened before: measured, `/api/v1/usage` 200 and the backup route 401 with the same token on an upgraded 0.1.0 deployment |
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
  - *`WEB_CONCURRENCY=2`.* **Corrected by F2.** On a fresh volume 0.1.0 exits within 12
    seconds with code 0, after a clean shutdown in its log, and serves nothing; with
    `WEB_CONCURRENCY=1` it runs. The planning run concluded from that that no running
    0.1.0 deployment has the variable above 1, and that does not follow: the exit is two
    processes racing to create the database (`Child process [8] failed to start`, under
    a SQLAlchemy traceback). On a volume that already holds a workspace, 0.1.0 with
    `WEB_CONCURRENCY=2` and with `4` stayed up for the 45 seconds watched, answered
    `/readyz` with 200 throughout, and logged a completed startup 2 and 4 times. The
    image built from `main` on that volume with `WEB_CONCURRENCY=4` logs one and runs
    one `python` process.
  - *The upgrade again, on real data (F3).* See the adversarial pass.

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

- `pyproject.toml`: `version = "0.2.0"`.
- `uv.lock`: the `glosswork` entry's `version = "0.2.0"`, and no other line.
- `CHANGELOG.md`: the entry under "The changelog entry", above `## 0.1.0`.
- `README.md`, "Run it": the run line names `docker.io/glosswork/glosswork:0.2.0`
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

- [x] 1. The dry run of area 4, on `main` at `ac1b349`, started by the maintainer or with
  the maintainer's approval: `gh workflow run release.yml --ref main`. Read its jobs from
  `actions/runs/<id>/jobs`. Go on only when `preflight`, `build-amd64` and `build-arm64`
  all conclude `success` and `publish` is `skipped`. If a build fails, stop: the defect
  is in a merged change and gets its own issue.
- [x] 2. Run AC1 to AC5 against the unfixed tree and record how each failed. Measured
  while planning, on `main`: AC1 prints `0.1.0`; AC2's lock line reads `0.1.0`; AC3's
  first heading is `## 0.1.0` and its count is 0; AC4's counts are 0 and 1; AC5 compares
  `0.2.0` with `0.1.0`.
- [x] 3. Set `version = "0.2.0"` in `pyproject.toml`.
- [x] 4. `uv lock`, then the substitution from `AGENTS.md` non-negotiable 1, then confirm
  the diff is the one version line (AC2).
- [x] 5. Add the changelog entry, as written above.
- [x] 6. Set the README's run line to `docker.io/glosswork/glosswork:0.2.0`.
- [x] 7. Commit as `24: the version is 0.2.0, and the changelog says what it carries`.
- [x] 8. Run the Accept block and record its output.

**What the build recorded, 2026-10-02, on this machine (arm64) unless a runner is named.**

- *Step 1.* Actions run 37044863460, `workflow_dispatch` on `main` at `ac1b349`, started
  by the build agent's account at 18:03:22Z on the maintainer's approval of one run. It
  finished `success` at 18:10:50Z, 7 minutes 28 seconds of wall time. `preflight`
  `success` in 7 s, `build-arm64` `success` in 428 s, `build-amd64` `success` in 431 s,
  `publish` `skipped`. In each build job the steps "The image passes container_tests" and
  "Its notices cover every third-party package it carries" concluded `success`, and the
  sign-in and push steps were `skipped`. Read from `actions/runs/37044863460` and its
  `/jobs`. So the suite, change 22's TLS tests included, passes on `linux/amd64` and on
  both release runners at `ac1b349`.
- *Step 2, on the tree at `87786cd`, before any edit.* AC1 printed `0.1.0`. AC2:
  `uv lock --check` exited 0, both counts printed 0 and the third command printed
  nothing, since the lockfile was `main`'s; the two registry counts printed 70 and 70.
  AC3: the first heading was `## 0.1.0`, the count was 0, the `diff` exited 1, and
  `git diff --numstat` printed nothing where the entry has 28 lines. AC4 printed 0 and 1.
  AC5: the pattern matched, and `0.2.0` did not equal `0.1.0`. Each of AC1 to AC5 failed.
- *Step 4.* With `pyproject.toml` at 0.2.0, `uv lock --check` exited 1. `uv lock` exited
  0 and printed `Updated glosswork v0.1.0 -> v0.2.0`; `git diff --text uv.lock` then held
  71 added lines. After the substitution it is one hunk, `-version = "0.1.0"` and
  `+version = "0.2.0"`, and `uv lock --check` exits 0. This is the second measurement of
  P4, by a session that did not make the first (F7). `uv` here is 0.9.18.
- *Step 5.* The entry was copied out of this file's `markdown` fence by AC3's own `awk`
  program and inserted above `## 0.1.0`, not retyped.
- *Step 7.* Commit `8be5db2`: `CHANGELOG.md`, `README.md`, `pyproject.toml`, `uv.lock`.
- *Step 8, at `8be5db2` on a clean tree.* This is the build's own run of the Accept
  block, not the verification, which a separate session does.
  - AC1 printed `0.2.0`.
  - AC2: `uv lock --check` exit 0; counts 1 and 1; `+version = "0.2.0"`; 70 and 70.
  - AC3: `## 0.2.0`; 5; `diff` exit 0; `git diff --numstat` printed `28`, `0`,
    `CHANGELOG.md`, and the entry's line count is 28.
  - AC4 printed 1 and 0.
  - AC5: the pattern matched and `0.2.0` equals AC1's output.
  - AC6: `CHANGELOG.md`, `README.md`, `docs/changes/24-release-after-0-1-0.md`,
    `pyproject.toml`, `uv.lock`.
  - AC7: structural lane exit 0, 101 passed; supply chain and licences exit 0, 11 passed.
  - AC8: `uv run pytest -q` exit 0, 2215 passed, 3 xfailed; `ruff check` exit 0;
    `ruff format --check` exit 0, 263 files.
  - AC9: `container_tests` with `GW_IMAGE` unset exit 0, 41 passed, none skipped, in
    274 s; the image's installed package printed `0.2.0`; the notices check exit 0,
    python 57 of 57 and npm 107 of 107.
  - AC10: `imagetools inspect` exit 1 with `not found`; the API query printed `false`.

Verification, the two closeout commits, the one push and the one pull request follow as
`docs/changes/README.md` sets out.

## Accept

Each exit code is read on its own line, never through a pipe.

- **AC1.** The version is set, read the way the release workflow reads it:
  `uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'`
  prints `0.2.0`.
- **AC2.** The lockfile changed by its one line. `uv lock --check` exits 0.
  `git diff --text origin/main -- uv.lock | grep -c '^[+][^+]'` prints 1, the same with
  `'^[-][^-]'` prints 1, and `git diff --text origin/main -- uv.lock | grep '^[+][^+]'`
  prints `+version = "0.2.0"`. `grep -c 'registry = "https://pypi.org/simple"' uv.lock`
  and `grep -c 'registry = ' uv.lock` print the same number.
- **AC3.** The changelog carries the approved entry and has lost nothing.
  `grep -m1 '^## ' CHANGELOG.md` prints `## 0.2.0`.
  `awk '/^## 0\.2\.0$/{f=1} /^## 0\.1\.0$/{f=0} f' CHANGELOG.md | grep -c -E '^- (7|9|11|20|22): '`
  prints 5. That same `awk` output is identical to this plan's entry:
  `diff <(awk '/^## 0\.2\.0$/{f=1} /^## 0\.1\.0$/{f=0} f' CHANGELOG.md) <(awk '/^```markdown$/{f=1;next} /^```$/{f=0} f' docs/changes/24-release-after-0-1-0.md)`
  exits 0. Every added line is the entry's and no line is removed:
  `git diff --numstat origin/main -- CHANGELOG.md` prints the number that
  `awk '/^```markdown$/{f=1;next} /^```$/{f=0} f' docs/changes/24-release-after-0-1-0.md | wc -l`
  prints, then `0`, then the path. (The planning run's form of this check,
  `grep -c '^-[^-]'` over the diff, could not see a removed list line, because a removed
  `- 1: ...` line reads `-- 1: ...` in a diff, and it counted no added line at all: F5.)
- **AC4.** The README names the version and no longer names the last one:
  `sed -n '/^## Run it/,/^## Point/p' README.md | grep -c 'docker.io/glosswork/glosswork:0.2.0$'`
  prints 1, and `grep -c 'glosswork/glosswork:0.1.0' README.md` prints 0.
- **AC5.** The tag the maintainer will push matches: with `TAG=v0.2.0`, the workflow's
  pattern `^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$` matches it in `bash`,
  and `${TAG#v}` equals AC1's output. This restates AC1 against the tag command below:
  it cannot fail unless AC1 does, and is not counted as separate coverage (F6).
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
  prints `0.2.0`, and
  `uv run python scripts/notices_coverage.py --image glosswork:container-test` exits 0.
- **AC10 (fence).** The version is free in both registries: with an empty
  `DOCKER_CONFIG`, `docker buildx imagetools inspect docker.io/glosswork/glosswork:0.2.0`
  exits 1 with `not found`, and
  `gh api orgs/glosswork/packages/container/glosswork/versions --jq '[.[].metadata.container.tags[]] | any(. == "0.2.0")'`
  prints `false`. The GitHub package is private, so it is read through the API with a
  token and not anonymously.

## After the merge, for the maintainer

Not part of the branch. Once the pull request is merged and CI is green on `main` for
the merge commit (the same query as P7, returning 1), from a clone whose `user.email` is
`hello@glosswork.dev`:

```
git fetch origin
git tag -a v0.2.0 -m "0.2.0" <merge commit>
git push origin v0.2.0
```

The release run's `preflight`, `build-amd64`, `build-arm64` and `publish` should all
conclude `success`. Then `docker.io/glosswork/glosswork:0.2.0` pulls with no login for
both architectures, and its revision label is the merge commit. That is the version the
hosted control plane pins. Docker Hub is the only name that pulls without a login: the
GitHub package is private, and `ghcr.io/glosswork/glosswork:0.1.0` refuses an anonymous
read today.

**If the release run fails** (F8, read from `.github/workflows/release.yml`; none of
these was produced):

- *`preflight` fails because CI was not yet green on `main`.* Nothing was built. Wait
  for the `main` run and re-run the release run; the tag stays.
- *A build job fails.* Nothing has a name in either registry. The defect is in merged
  code and needs its own change; delete the tag, and tag the fix's merge commit with the
  same version, which is still free.
- *`publish` fails at sign-in.* A Docker Hub credential that no longer works stops the
  job before either registry is tagged. A dry run cannot show this in advance, because
  it never signs in. Fix the credential and use "Re-run failed jobs".
- *`publish` fails between the two registries.* It tags the GitHub registry first and
  Docker Hub second, so the GitHub registry then holds `0.2.0` and Docker Hub does not,
  and nobody outside can pull it. **Use "Re-run failed jobs", never "Re-run all jobs".**
  The first keeps the two build jobs' digests, finds the GitHub registry already holding
  exactly this build, and goes on to Docker Hub. The second builds again on fresh
  runners, and nothing makes a second build byte-identical to the first (the base images
  are named by tag, and Python in the image moved from 3.13.15 to 3.13.16 between the
  0.1.0 build and one made today from the same Dockerfile), so `publish` would find a
  `0.2.0` in the GitHub registry that is not its own and refuse, every time. The way out
  of that is to delete the private GitHub package version by hand, or to release the
  next number.

## Baseline repaint

None. No UI change.

## Adversarial pass

Run on 2026-10-02 by a session that did not write this plan, on this machine (arm64).
"Old" is the published `glosswork/glosswork:0.1.0`; "new" is an image built from a
separate clone at `ac1b349`. Nothing in the working clone but this file was edited.
Three findings change what the maintainer is asked to approve: **F1** (the evidence for
the number, and the Upgrading paragraph's text), **F2** (one reason given in area 2 was
wrong, which opens a choice) and **F5** (an Accept criterion that could pass on a wrong
changelog). F9 and F10 correct the cost and the window stated in areas 3 and 4 without
changing what is proposed. The rest confirm, or record what is not established.

- **F1. This version gives meaning to six names 0.1.0 ignored, not to one prefix, and
  one of the differences is silent.** Each of these was started on both images with
  otherwise the same environment: `GW_TLS_ENABLED=false`, lower-case `gw_tls_mode=off`,
  `GW_TLS_PROXY_SERVICE_HOST=10.0.0.9`, `GW_TLS_CERT_FILE` alone, `GW_RELAY_TOKEN` alone,
  `GW_RELAY_URL` alone, `GW_OPERATOR_BACKUP` blank, and `GW_OPERATOR_BACKUP=true` with no
  token. Old ran and answered `/readyz` with 200 every time; new exited 1 every time with
  a `Configuration error:` line naming a variable. With both relay names set and well
  formed, both images ran, and a password sign-in that old answered with 200 new answered
  with 409. Blank `GW_TLS_CERT_FILE` and `GW_OPERATOR_BACKUP=false` changed nothing. The
  planning run tested the first of these only, and its Upgrading paragraph read: "One
  thing to check first: an environment variable whose name begins `GW_TLS_`, other than
  the three in change 22 below, now stops the workspace at startup, naming the variable.
  0.1.0 ignored such a name. Remove it." Also read: Docker Hub's public record of the
  image shows `pull_count` 293. *Disposition: folded into area 1, as its second table
  and both arguments, with no recommendation; into P9; and into the entry's Upgrading
  paragraph, which now names all six. The number and the new sentence are the
  maintainer's to approve.*

- **F2. P9's `WEB_CONCURRENCY` conclusion was wrong.** Measured as P9 now records: 0.1.0
  runs 2 or 4 processes on a volume that already holds a workspace, and exits only on an
  empty one. So a 0.1.0 deployment can be running with the variable set, and upgrading
  takes it to one process. *Disposition: P9 corrected in place; area 2's bullet rewritten
  as a choice, with the entry left as it was and the alternative sentence given.*

- **F3. The upgrade holds on a workspace with real data, in both directions.** One
  volume, the same environment throughout, with `GW_OPERATOR_TOKEN` set. On old: one
  object type, 24 records with an embedded field, a comment, a 3 MB attachment, a second
  person with a password, a token minted from the command line and one minted over REST,
  and a browser session. Then, in order:
  - *New on that volume.* Ready in 3 s, migration 13 applied. Both tokens, the session
    cookie minted by old, both password sign-ins, the 24 records, a semantic search (the
    same three records), the attachment's bytes by SHA-256 and the comment were as on
    old. The operator token read `/api/v1/usage` with 200 and was refused the backup
    route with 401, as area 1 says an upgraded deployment should. A write succeeded.
    Every structured log line was at `info`.
  - *Old again on the migrated volume.* The same checks, the same results.
  - *New with the relay on*, the relay being a recorder inside the container. The old
    session still answered; password sign-in answered 409; the administrator and the
    second person signed in by code; one person was invited and joined by code; one
    invite and one code were left open.
  - *Old on that volume.* Ready in 3 s. The three sessions minted by code answered 200.
    Records, search, export, backup and a write worked. The invited person has no
    password: a password sign-in is refused with 401, an administrator can set one, and
    the sign-in then succeeds. Every log line at `info`.
  - *A restore*, by the procedure in `docs/DEPLOYMENT.md` section 6, of the backup old
    took before any upgrade, onto an empty volume: old came up on it with 24 records,
    the session, search and attachment intact, and so did new, applying migration 13.
  *Disposition: no change to the plan's claims; area 1's table now cites it. Measured on
  one arm64 machine and one small workspace, and not on `linux/amd64`.*

- **F4. Each changelog line was checked against the built image, and each is true.**
  Line 7: read from change 7's diff. Line 9: F3's relay step (`202` on a code request,
  sign-in by code, an invite accepted, password sign-in 409 only with both variables
  set); the code is six digits and the invite is by email, read in
  `src/glosswork/services/sign_in_codes.py`. Line 11: one process under
  `WEB_CONCURRENCY=4` (F2); the pause sentence is true and is not a change from 0.1.0,
  whose indexing worker differs from this one in comments only
  (`git diff v0.1.0..origin/main -- src/glosswork/services/embedding_worker.py`).
  Line 20: off by default (F3); an abandoned download of a 165 MB backup left one file
  in the staging directory on old for the 60 seconds watched and none on new one second
  after the client hung up; the stored code is an HMAC keyed from the relay token, read
  at `sign_in_codes.py` lines 123 and 130, not attacked. Line 22: `container_tests` on
  the image built from `main`, exit 0, 40 passed and 1 skipped, in 267 s, including
  `container_tests/test_tls_lock.py`. Upgrading: the REST and MCP comparisons were
  repeated and match P9 (73 operations and 32 schemas unchanged, 6 and 3 added; 32 tools,
  instructions and `describe_capabilities` identical), and no existing setting's default
  changed. Two things the image carries that no line mentions, because a line is a
  change and these are not one: Python 3.13.16 where 0.1.0 has 3.13.15 (the Dockerfile
  names its base images by tag), and `httpx2` 2.13.1 where 0.1.0 has 2.12.0 (change 9).
  *Disposition: area 2 gains the note on line 11. No line was changed for truth.*

- **F5. AC3 could pass on a changelog that had lost or gained lines.** Its last check
  counted removed lines with `grep -c '^-[^-]'` over a diff. A removed list line begins
  `- ` and so reads `-- ` in a diff, which that pattern skips, and nothing counted added
  lines. Measured on copies of the changelog text: with one word of the 0.1.0 entry
  changed, with an unapproved line appended under 0.1.0, and with an unapproved sentence
  added above the entry, all of AC3 passed. It did fail, as it should, on a missing
  line, a changed word, an extra space, the entry twice, the entry below 0.1.0, and a
  heading reading `0.2.0`, which was the wrong heading when this was measured: the plan
  was then written for 0.1.1. *Disposition: the last check is replaced with
  `git diff --numstat`, whose added count must equal the entry's own line count and
  whose removed count must be 0. On the same copies it fails all three cases that
  passed before (one added line more than the entry has, or 1 removed) and passes the
  correct file.*

- **F6. Three smaller faults in the Accept block.** AC10's second half was a sentence,
  not a command. AC5 cannot fail unless AC1 does. And under 0.2.0, "every `0.1.1` reads
  `0.2.0`" missed AC3's escaped patterns. AC4 was run on copies of the README text and
  fails on the unfixed text, on `0.1.2`, on `0.1.10` and on the other registry's name.
  *Disposition: AC10 given its command, which prints `false` today for `0.1.1` and `true`
  for `0.1.0`; AC5 labelled; area 1 names the escaped patterns.* **Not run by this pass:** AC1 and AC2 against a tree with
  the wrong version in `pyproject.toml` or in `uv.lock` (see F7). By reading, AC1 prints
  what `pyproject.toml` holds and AC2's third command prints the lockfile's own line, so
  each fails on a wrong number in its own file; and `tests/test_supply_chain.py` records
  that `uv lock --check` exits 0 on a lockfile whose registry lines are wrong, so of
  AC2's commands only the two `grep -c` counts and AC7 guard those lines.

- **F7. The lockfile step was not reproduced by this pass.** The session's permission
  check refused the one command that would have re-run `uv lock` and the substitution in
  a scratch clone, and it was not attempted another way. P4 therefore stands on the
  planning run's measurement. Read: `main` holds 70 registry lines, all PyPI, and one
  other source, the project itself. `uv` on this machine is 0.9.18 and the workflows pin
  0.12.19; the planning run's one-line diff was made with the local one. *Disposition:
  recorded in P4. The build's own step 4 and AC2 are the next measurement.*

- **F8. The preflight would accept the tag, as far as can be shown, and a failed
  `publish` has one right way to be retried.** The tag trigger, the "is a commit" check,
  the "on main" check and both CI queries all passed for `v0.1.0`, an annotated tag made
  by the same command. A tag on the branch's own head instead of the merge commit is
  refused, by reading: that commit has a `pull_request` run and no `push` run on `main`. The
  `release` environment has no required reviewer. What cannot be shown before the merge
  is the CI run on `main` for the merge commit, as P7 says. The publish job tags the
  GitHub registry before Docker Hub, and what follows from that is now under "After the
  merge". *Disposition: P7 extended; the failure cases added to "After the merge".
  Whether a second full run would reproduce the first run's digests was read, not
  measured.*

- **F9. The README window is at least 20 minutes, and the README is not the only place
  the tag is written.** Timings as area 3 now gives them. The site repository names
  `docker.io/glosswork/glosswork:0.1.0` in three files (the content of its home page and
  of its install page, and the home page's template), which the "Releases" clause
  proposed here does not reach. That is
  another repository's change and is not made here. *Disposition: area 3 corrected. The
  proposal is unchanged.*

- **F10. The dry run's cost estimate holds, on the high side, and the agent's token can
  start one.** As area 4 now gives it: per-file durations from this pass's run of the
  suite, step timings of runs 36618298361 and 36619380689 read from the jobs API, and
  prices read at <https://docs.github.com/en/billing/reference/actions-runner-pricing>
  on 2026-10-02. No run was started. An alternative was considered and not taken: a dry
  run on the pull request's head, after its CI passes and before the merge, would test
  the release's own tree on both runners, but it finds a defect in merged code later
  than a dry run on `main` now, and the tag run repeats the test either way.
  *Disposition: area 4 corrected. The proposal is unchanged.*

**Not established by either run:** the suite on `linux/amd64` or on the release runners
(the dry run is for this); CI on `main` for the release's merge commit; that the Docker
Hub credential still works, which only a tag run exercises; how many included Actions
minutes are left this month; and who, outside this project, runs 0.1.0.

## Verification

A separate session that wrote none of the plan and made none of the build's edits verified
the branch at `b9fc533` on 2026-10-02, on this machine (arm64), with `GW_IMAGE` unset and
a clean tree before, between and after. It ran the Accept block as the verification of
record and changed nothing in the repository. **All ten criteria passed.** Its output is
under "Final Accept output".

What it checked beyond the block:

- **The changelog entry is the approved text.** Compared with this plan as approved, at
  `23ab7e3`, and not only with the plan on the branch: the fence there and the entry in
  `CHANGELOG.md` are both 28 lines and 1885 bytes, `diff` shows one line, the heading
  (`## 0.1.1` there, `## 0.2.0` here), and everything after the heading has the same
  SHA-256 (`42ee5c66...`). The 0.1.0 entry and the lines above the first heading hash the
  same as on `main`.
- **The version is 0.2.0 everywhere a release sets it**, in `pyproject.toml`, the
  `glosswork` entry of `uv.lock`, the changelog heading and the README's run line, and no
  file outside this plan names 0.1.1. The Accept block at `b9fc533` is the approved one
  with only area 1's substitution applied, so the build changed no check.
- **`uv.lock` differs from `main` by its one line**, and 70 of 70 registry lines are PyPI.
- **The dry run.** Actions run 37044863460, read from the API: `preflight`, `build-arm64`
  and `build-amd64` `success`, `publish` `skipped`, the `container_tests` and notices
  steps `success` in both builds, the sign-in and push steps `skipped`. Both registries
  still hold only `0.1.0`.
- **The preflight would accept `v0.2.0`, as far as can be shown before the merge.** The
  workflow's two CI queries return 1 at `ac1b349` and 0 at `b9fc533`, so they can fail,
  and a tag on the branch head would be refused.
- **The Accept block fails on a wrong release.** In a scratch clone the block passed
  unmodified and then failed on each of thirteen wrong releases: a wrong version in
  `pyproject.toml`, in the lock line or in both; a wrong README tag; a wrong changelog
  heading; an entry line removed; an entry sentence reworded; a word of the 0.1.0 entry
  changed; a line appended under 0.1.0; a sentence above the entry; the 0.1.0 entry
  deleted; a registry line pointed at another index; an extra committed file.

**Two sentences of this plan read wrong under 0.2.0, and the verification raised both.**
Area 2's first bullet called the number the entry is written for a `Z`, and 0.2.0 is a
`Y`. F5 listed "a heading reading `0.2.0`" among the cases AC3 rightly failed on, which
under 0.2.0 reads as if the correct heading fails. Neither changed a check or the release.
Both are corrected in place at closeout, to say what was true when each was written.

**Two limits of the Accept block, neither a fault in this release.** AC3 compares the
changelog with the plan on the same branch, so it passes if both are reworded the same
way; the comparison with `23ab7e3` above closes that here. Once this file is deleted, AC3's
`diff` and its line count have nothing to read, and the comparison to repeat is the one
with the fence at `23ab7e3`. And AC1's `uv run` re-locks a stale lockfile before AC2 reads
it, so on a wrong version AC2 fails by reporting the rewrite, not the wrong number.

**Not established by the verification.** CI on the pull request and on `main` for the
merge commit, neither of which existed. The 0.2.0 tree on `linux/amd64` and on the release
runners: the dry run tested `ac1b349`, which differs from this tree in the version string
and documents only, and the tag run tests it again before it publishes. That the Docker
Hub credential still works. AC6's after-closeout form, which the closeout runs.

## Final Accept output

The verifying session's run at `b9fc533`, 2026-10-02, from the repository root. AC7 to AC9
are its saved output. AC1 to AC6 and AC10 are as its report gives them. Every exit code is
the command's own, read with no pipe.

```
HEAD b9fc53353283e25756c8aa378868e34dc97fc72e      git status --short: (prints nothing)
--- AC1   uv run python -c '...tomllib...["project"]["version"]'
0.2.0
exit=0
--- AC2   uv lock --check
Resolved 71 packages
exit=0
          added lines 1, removed lines 1, the added line: +version = "0.2.0"
          registry lines naming PyPI 70, registry lines 70
--- AC3   first heading: ## 0.2.0      lines for 7, 9, 11, 20 and 22: 5
          diff of the changelog's entry against this plan's fence: exit=0
          git diff --numstat origin/main -- CHANGELOG.md: 28 0 CHANGELOG.md
          lines in the fence: 28
--- AC4   1 and 0
--- AC5   v0.2.0 matches the workflow's pattern in bash; ${TAG#v} is 0.2.0
--- AC6   git diff --name-only origin/main...HEAD      (the before-closeout form)
CHANGELOG.md
README.md
docs/changes/24-release-after-0-1-0.md
pyproject.toml
uv.lock
exit=0
--- AC7   uv run pytest -q -m structural
101 passed, 2117 deselected in 16.61s
exit=0
          uv run pytest -q tests/test_supply_chain.py tests/test_third_party_licenses.py
11 passed in 0.12s
exit=0
--- AC8   uv run pytest -q
2215 passed, 3 xfailed, 2 warnings in 288.99s (0:04:48)
exit=0
          uv run ruff check .            All checks passed!            exit=0
          uv run ruff format --check .   263 files already formatted   exit=0
--- AC9   uv run pytest -q -rs container_tests      (GW_IMAGE unset, clean tree)
41 passed in 276.55s (0:04:36)      (no skip line under -rs)
exit=0
          docker run --rm --entrypoint python glosswork:container-test -c '...m.version("glosswork")'
0.2.0
exit=0
          uv run python scripts/notices_coverage.py --image glosswork:container-test
| Ecosystem | Packages | Covered | Not covered |
| --- | --- | --- | --- |
| python | 57 | 57 | 0 |
| npm | 107 | 107 | 0 |
exit=0
          the image's revision label: b9fc53353283e25756c8aa378868e34dc97fc72e
--- AC10  docker buildx imagetools inspect docker.io/glosswork/glosswork:0.2.0   (empty DOCKER_CONFIG)
not found
exit=1
          gh api orgs/glosswork/packages/container/glosswork/versions --jq '... any(. == "0.2.0")'
false
exit=0
```

Fences, which cannot fail on a tree without this change and are not counted as coverage:
AC7, AC8 and AC10. AC5 restates AC1 (F6).

The closeout runs the block again on the branch's final head, with AC6 in its
after-closeout form and AC3's comparison made against `23ab7e3`. That output is in the
pull request, since this file is gone by then.

## Deviations from the approved plan

- **The version is 0.2.0, not the 0.1.1 this plan was written for.** The maintainer's
  choice under area 1, on 2026-10-02. The build's first commit, `87786cd`, applied that
  area's substitution to this file and changed nothing else in it.
- **The substitution was applied where the plan says what to do, not to every occurrence
  of the number.** Area 1 says "every `0.1.1` in this plan" and then lists the places.
  The listed places were changed, with checklist step 2's prediction for AC5 and the
  three sentences of "After the merge" that name the published version. Area 1's own
  argument, the introduction's sentence naming the four answers, the premises and the
  adversarial pass were left: they record what was argued and measured at 0.1.1, and
  rewriting them would have them claim measurements at a number nobody measured. What
  those premises established does not depend on the number, and steps 4 and 8 above repeat
  P4, P5 and P6 at 0.2.0.
- **Step 2's wording for AC2.** The checklist expected "AC2's lock line reads `0.1.0`".
  AC2 has no command that prints the lock line on an unchanged lockfile: what it showed
  on the unfixed tree is counts of 0 and 0 and an empty third command. Recorded above as
  observed.
- **Two sentences of this plan were corrected at closeout.** The verification raised them:
  area 2's first bullet and F5's list, as "Verification" sets out. Each now says what was
  true when it was written. No command, criterion or line of the changelog entry changed.
- **The `CONTRIBUTING.md` clause is added to the sentence that is already there.** "Durable
  content" gives the whole sentence, with the changelog's name in backticks. The sentence
  in "Releases" already reads that way word for word without the README clause, and its
  mention of the changelog is a link. The closeout adds the clause, "names the new version
  in the run line of `README.md`,", and leaves the link a link, so the words are the
  approved ones and no link is removed that nobody asked to remove.

No step was done out of order, and no step was changed.

## Durable content moved out of this plan

At closeout, under area 3 as recommended, `CONTRIBUTING.md` "Releases", second
paragraph: "It sets the new version in `pyproject.toml`, updates the `glosswork` entry
in `uv.lock` to match, names the new version in the run line of `README.md`, and adds
the version's entry to `CHANGELOG.md`." The changelog entry is itself durable and stays
where the build puts it. Nothing else here outlives the change.

The closeout's second commit makes that one edit to `CONTRIBUTING.md` and deletes this
file. Its final text is the pull request's description, and the file stays readable in
the commit before the one that deletes it.
