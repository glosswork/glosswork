# 1: Versioned images publish to the GitHub registry and Docker Hub

| | |
| --- | --- |
| Issue | #1, https://github.com/glosswork/glosswork/issues/1 |
| Branch | `1-release-images` |
| Spec | CONTRIBUTING.md "What CI runs", "The repository's GitHub settings", new "Releases"; docs/DEPLOYMENT.md section 2; THIRD_PARTY_NOTICES.md (read only) |
| Decisions | none changed |
| Requirements | FR-P1 (one image) |
| Depends on | nothing unmerged; `main` at `e5a047b`, single root |

## Why

Nothing can be installed by version. There are no tags, `ci.yml`'s `image` job builds and
never publishes, and CONTRIBUTING says nothing about versions or a changelog. Issue #1 has
the evidence.

## Premises

- **P1. No tag and no release workflow exist.** `git tag -l` prints nothing on `e5a047b`;
  `.github/workflows/` holds only `ci.yml` (listed 2026-09-28).
- **P2. `pyproject.toml` says `version = "0.1.0"`** (read). So the first tag is `v0.1.0`
  with no version change.
- **P3. Only GitHub's own actions and `astral-sh/setup-uv` may run**
  (`scripts/github/configure.sh`, `SELECTED_ACTIONS`, read). So the workflow uses no
  `docker/*` action: it drives `docker buildx` from the command line, which the runner
  image carries.
- **P4. Native arm64 runners exist for private repositories** as `ubuntu-24.04-arm`, billed
  against the plan's minutes at $0.005 a minute against $0.006 for x64
  (github.blog changelog 2026-01-29 "arm64 standard runners are now available in private
  repositories"; docs.github.com billing "actions runner pricing"; read 2026-09-28). So
  each architecture builds natively and no QEMU is needed.
- **P5. The image a user pulls can be proven to be the image that was tested.** Measured
  locally 2026-09-28 with a `docker-container` builder: `--load` then a second build
  exporting `push-by-digest` to a scratch registry, both with `SOURCE_DATE_EPOCH` set to
  the commit time, gave `containerimage.config.digest` equal to the loaded image's `Id`
  (`sha256:b80309f2...`). The engine here uses the classic `overlay2` store, where `Id` is
  the config digest. GitHub's runners run Docker Server 28.0.4 today
  (the Ubuntu 24.04 readme in actions/runner-images, image 20260920.314.1), and
  runner-images issue #13474 announces Docker 29, whose containerd store reports the
  manifest digest as `Id`. So the check accepts either digest (F6).
- **P6. `docker buildx imagetools create` joins the two per-architecture digests into one
  tag, and `imagetools inspect` reads back both platforms and each one's labels.** Measured
  against the same scratch registry: an index with `linux/amd64` and `linux/arm64` at the
  pushed digests, and `org.opencontainers.image.revision` equal to the built commit on
  both. Inspecting a tag that does not exist exits 1.
- **P7. A package pushed by a workflow's own token is private at first, and is linked to
  the repository** (docs.github.com "Working with the Container registry", read
  2026-09-28). The repository is private (`gh api repos/glosswork/glosswork`). So an
  anonymous pull from `ghcr.io` needs the package made public by hand, which is
  documented as UI-only; no API for it was found.
- **P8. Docker Hub creates a repository on first push with the namespace's default
  privacy** (docs.docker.com "Repository settings", read 2026-09-28). What that default is
  for the `glosswork` organization is not readable from here, so the repository is created
  by hand, public, before the first tag.
- **P9. Environment secrets and tag-restricted environments work on private repositories
  on the Team plan** (docs.github.com "Deployments and environments", read 2026-09-28). So
  the Docker Hub token can be readable only by runs for tags matching `v*`.
- **P10. The image's notices do not cover everything it carries.** Measured on an image
  built from `e5a047b`, with `scripts/notices_coverage.py` as written by this change: 57
  third-party Python distributions, of which 54 install a licence file and 3
  (`flatbuffers`, `sqlite-vec`, `tokenizers`) install none; 106 npm packages (104
  production entries of the lockfile, plus `vite` and `tailwindcss`, whose code the bundle
  carries although they are development packages, F3) compiled into a bundle that carries
  no licence text (`grep -c -i license` over the one JavaScript file is 0), and
  `THIRD_PARTY_NOTICES.md` gives none of them a heading. The check exits 1: 109 of 163. The
  three font packages' licences are reproduced in that file, under the fonts' names rather
  than the packages', so the rule counts them as uncovered too.
- **P12. `imagetools create` copies an index across registries.** Measured with two local
  registries on different ports: an index created on the second from the first's two
  digests serves both platforms at the same digests, with the same revision label, and
  `docker pull --platform linux/amd64` of it exits 0. Inspecting a missing tag prints
  `ERROR: ...: not found` and exits 1. So Docker Hub is filled from the GitHub registry in
  the one job that holds its credential.
- **P11. `container_tests` pass against an image built from `e5a047b`**: 28 passed, 1
  skipped, 133 s, with `GW_IMAGE` set (measured locally, arm64).

## What changes

- **`.github/workflows/release.yml`**, new. Runs on a pushed tag `vX.Y.Z`, and by hand as
  a dry run. `preflight` checks the tag against `pyproject.toml`, that the commit is on
  `main`, and that `ci.yml` succeeded on `main` for it (a dry run checks only that CI
  passed). `build-amd64` and `build-arm64`, identical but for runner and platform, build
  into the engine, run `container_tests` and the notices check, and on a tag only, sign
  in to the GitHub registry and push the same build by digest with no tag, failing unless
  the pushed image is the tested one. `publish`, on a tag only and the only job in the
  `release` environment, checks nothing out: it treats a version as absent only on a
  registry's "not found", refuses one that exists as a different build, skips one that is
  exactly this build, writes `X.Y.Z` on the GitHub registry and copies the index to
  Docker Hub, and reads both back. No `latest`. This file defines CI.
- **`scripts/mutate_release_workflow.py`**, new: applies fifteen mutations to a copy of the
  workflow and fails unless each turns `test_release_workflow.py` red (AC8).
- **`scripts/notices_coverage.py`**, new: the check in P10, exit 1 while anything is
  uncovered. Defines CI.
- **`tests/test_release_workflow.py`**, new, structural: pins the trigger, per-job
  permissions, the secret only in `publish`'s first step, no environment on any other
  job, the two builds identical, the exact test and notices commands with nothing
  neutralising them, only the push step pushing and only on a tag, the order test,
  notices, sign-in, push, the tested-equals-pushed check, never overwriting a version,
  and SHA-pinned actions. Added to `MARKED` in `tests/test_structural_lane.py`.
- **`tests/test_notices_coverage.py`**, new: the coverage rules on synthetic inputs.
- **`scripts/github/configure.sh`**: a `release` environment that admits tags matching
  `v*` only, applied and checked like every other setting. The maintainer runs `apply`.
- **`CONTRIBUTING.md`**: a "Releases" section (version scheme, the release change, the
  changelog convention, the dry run, how to tag, what the workflow refuses, where the
  credentials live and who can reach the Docker Hub one, the first release's ordered
  one-time steps); the settings list and the CI-defining file list gain the new files.
- **`CHANGELOG.md`**, new, with the `0.1.0` entry. This change is the `0.1.0` release
  change: `pyproject.toml` already says `0.1.0`.
- **`docs/DEPLOYMENT.md`** section 2: the published image names, and that there is no
  `latest`.

## What does not change

- `ci.yml` and everything it runs; `ruleset-main.json`; the `Dockerfile`.
- `THIRD_PARTY_NOTICES.md`. Closing the gap in P10 is a separate decision.
- README's quick start. It keeps `docker build` until an image exists to point at.
- No tag is pushed, no package is made public, no secret is stored by this change.

## Constraints

- Nothing is pullable by name until every check has passed for both architectures.
- The Docker Hub token is readable only by a `v*` tag run, only in `publish`'s sign-in
  step, and never before both images have passed their tests. Pushing a `v*` tag is
  therefore the maintainer's act alone (F2).
- Every action pinned by SHA; no action outside the allowed set.
- Every commit `Glosswork <hello@glosswork.dev>`, one root.

## Checklist

1. [x] Run the new structural and unit tests against the unfixed tree (no workflow, no
   script) and record how each failed. On `5ccbfa0`: `test_notices_coverage.py` fails
   collection (`FileNotFoundError`, no script); `test_release_workflow.py` 11 failed, 10
   `FileNotFoundError` (no workflow) and 1 `AssertionError` (no `release` environment in
   `configure.sh`).
2. [x] Add `scripts/notices_coverage.py` and its tests.
3. [x] Add `release.yml` and `tests/test_release_workflow.py`; add it to `MARKED`.
4. [x] Add the `release` environment to `configure.sh`.
5. [x] CONTRIBUTING "Releases", CHANGELOG, DEPLOYMENT.
6. [x] Run the Accept block (AC1 to AC8 and AC10 below; AC9 on the pull request).
7. [ ] Close out in two commits; push once; open the pull request once. The pull request
   is open; the closeout waits for approval (D2).

## Accept

- **AC1.** `uv run pytest -q tests/test_release_workflow.py tests/test_notices_coverage.py tests/test_structural_lane.py` exits 0.
- **AC2.** `uv run pytest -q -m structural` exits 0.
- **AC3.** `uv run pytest -q` exits 0.
- **AC4.** `uv run ruff check .` exits 0, and separately `uv run ruff format --check .` exits 0.
- **AC5.** `actionlint -color` (1.7.12, with shellcheck on `PATH`) exits 0.
- **AC6.** `shellcheck scripts/github/configure.sh` exits 0, and `configure.sh check` run
  with the agent token reports `environment release` as differing (it does not exist yet).
- **AC7.** `docker build -t glosswork:local . && uv run python scripts/notices_coverage.py --image glosswork:local`
  exits 1 and lists `flatbuffers`, `sqlite-vec`, `tokenizers` and every production npm package.
- **AC8.** `uv run python scripts/mutate_release_workflow.py` exits 0: the unmutated copy
  passes and each of the fifteen mutations exits 1.
- **AC9.** The pull request's CI run ends with `ci-ok` `success` (read through
  `actions/runs?head_sha=`).
- **AC10.** `grep -c -e "Semantic Versioning" -e "The changelog is written once per release" CONTRIBUTING.md`
  prints 2.

After merge, and each needing the maintainer's approval, in this order:

- **AC11.** A dry run, `gh workflow run release.yml --ref main`, ends with `preflight`,
  `build-amd64` and `build-arm64` `success`, their push steps `skipped`, and `publish`
  `skipped`. This is the first time `container_tests` run on GitHub's runners (F4). While
  the notices gap is open it ends with both builds failing at the notices step, which is
  the check working, and everything before that step green is the evidence.
- **AC12.** A `v0.1.0` tag run ends `success` in all four jobs; then the anonymous pulls in the
  operator's release runbook, section 4 (it lives outside this repository), print both platforms for both references.

### Accept output, 2026-09-28, on `bc5b86b`

```
AC1  exit 0   25 passed
AC2  exit 0   88 passed, 1953 deselected
AC3  exit 0   2038 passed, 3 xfailed
AC4  exit 0   ruff check: All checks passed!
     exit 0   ruff format --check: 246 files already formatted
AC5  exit 0   actionlint 1.7.12 with shellcheck 0.11.0 on PATH
AC6  exit 0   shellcheck scripts/github/configure.sh
     exit 1   configure.sh check (agent token): "DIFFERS environment release: unreadable
              with this token (HTTP 404)" and the same for the tag policy; the
              environment does not exist until the maintainer runs apply
AC7  exit 1   python 57/54/3 (flatbuffers, sqlite-vec, tokenizers); npm 106/0/106;
              "109 of 163 third-party packages ... have no licence text in the image"
AC8  exit 0   unmutated exit 0; each of 15 mutations exit 1
AC10 exit 0   2
```

## Adversarial pass

Run by a separate Opus session on `5ccbfa0` and the working tree, 2026-09-28. It found
twelve; four needed the maintainer and the rest are folded in above.

- **F1. The notices check fails every release as the image stands**, so the first tag
  builds, tests and stops, and this change alone cannot meet its done-when. The rule also
  fixes the remedy: an npm package counts only through a heading in the notices file.
  *Disposition: for the maintainer.* Keep the check hard, and close the gap in a change
  of its own before the first tag, or make the check report-only until then. Which remedy
  the rule accepts (headings, or a generated licences file in the image) is decided with
  that change.
- **F2. Anyone who can push a `v*` tag can read the Docker Hub token**, because a tag runs
  the workflow as the tagged commit has it, and required reviewers on environments are not
  available to private repositories on the Team plan. The agent's GitHub token belongs to
  the maintainer's own account (`gh api user` returns `crscheid`, with admin on the
  repository), so a tag ruleset with a bypass for the maintainer would bypass for the
  agent too. *Disposition: for the maintainer.* CONTRIBUTING now says it plainly, and the
  constraint below is corrected.
- **F3. Development packages ship code in the bundle.** Vite's modulepreload polyfill is
  in the JavaScript and Tailwind's preflight in the CSS, and a test asserted `vite` was not
  counted. *Fixed:* `BUNDLED_BUILD_TOOLS`, measured, and the test inverted.
- **F4. `container_tests` have never run on a GitHub runner**, whose timing bands were set
  on the maintainer's Mac, and the tag run would be the first time, after an hour of
  builds. *Fixed:* a `workflow_dispatch` dry run that builds and tests on both runners and
  stops before signing in (AC11).
- **F5. The structural test pinned less than it claimed:** six mutations (a `--push` on the
  tested build, the secret in the workflow env or a test step, `|| true` on the notices
  check, `--co` on the tests, the existence check disabled) all passed. *Fixed:* the test
  pins exact commands, the secret's one step, and the push; AC8 runs fifteen mutations.
- **F6. The tested-equals-pushed check depends on the classic image store**, which Docker
  29 on the runners will change. *Fixed:* either digest matches (P5).
- **F7. The existence check read any failure as "absent"**, and a Docker Hub failure after
  the GitHub tag was written could never be retried. *Fixed:* only "not found" is absent,
  and a registry already serving exactly this build is skipped.
- **F8. `astral-sh/setup-uv` runs in the build jobs, which hold `packages: write`.**
  *Accepted:* it is pinned by SHA and already trusted by every CI job; the jobs that hold
  a package-writing token hold no other credential, and the Docker Hub token is out of
  its reach entirely since only `publish` names the environment.
- **F9. The first release's one-time steps were written nowhere but the premises.**
  *Fixed:* CONTRIBUTING lists them in order.
- **F10. AC8 was a description, no criterion covered CONTRIBUTING, and AC5 and AC6 need
  tools this machine lacked.** *Fixed:* AC8 is a command, AC10 added; actionlint 1.7.12
  and shellcheck 0.11.0 were fetched for the run (actionlint's checksum verified), and CI's
  `guards` job runs actionlint over both workflows anyway.
- **F11. DEPLOYMENT and the changelog describe images that do not exist yet**, and the
  changelog entry broke its own convention. *Fixed:* the entry is one line per change.
  *Accepted:* DEPLOYMENT and CHANGELOG describe `0.1.0` because this is the `0.1.0`
  release change; they become true with the tag, which follows the merge.
- **F12. Nits.** The tag's commit is now peeled in `preflight`; the check's scope is
  worded as the packages the project put in the image; CONTRIBUTING says to tag from a
  clone whose `user.email` is `hello@glosswork.dev`. *Accepted:* unpinned base images can
  move between the two builds in one job, which fails the identity check safely.

## Deviations from the approved plan

- **D1. Executed before the plan was approved.** CONTRIBUTING step 5 puts approval before
  execution. The run was dispatched to prepare everything short of the merge and the tag
  in one pass, so the plan, its adversarial pass and the build are presented for approval
  together. If the maintainer changes the plan, the build commit is revised to match.
- **D2. The pull request is open before the closeout.** The closeout deletes this file
  and moves its durable content, and two of its findings (F1, F2) wait on the
  maintainer's decisions, which may change the plan. The closeout's two commits follow
  those decisions, on this pull request, before the merge.
- **D3. AC7 ran on an image built from `e5a047b`,** not from this branch. The branch
  changes no file the image is built from (`Dockerfile`, `.dockerignore`, `src/`, `web/`,
  `pyproject.toml`, `uv.lock` are untouched: `git diff --stat e5a047b -- Dockerfile
  .dockerignore src web pyproject.toml uv.lock` is empty), so the image's contents are the
  same but for the revision label.

## Durable content moved out of this plan
