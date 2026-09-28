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
  (`sha256:b80309f2...`). The engine here uses the classic `overlay2` store, as GitHub's
  runners do, where `Id` is the config digest.
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
  (`flatbuffers`, `sqlite-vec`, `tokenizers`) install none; 104 production npm packages
  compiled into a bundle that carries no licence text (`grep -c -i license` over the one
  JavaScript file is 0), and `THIRD_PARTY_NOTICES.md` gives none of them a heading. The
  three font packages' licences are reproduced in that file, under the fonts' names rather
  than the packages', so the rule counts them as uncovered too.
- **P11. `container_tests` pass against an image built from `e5a047b`**: 28 passed, 1
  skipped, 133 s, with `GW_IMAGE` set (measured locally, arm64).

## What changes

- **`.github/workflows/release.yml`**, new. Runs on a pushed tag `vX.Y.Z` and on nothing
  else. `preflight` checks the tag against `pyproject.toml`, that the commit is on `main`,
  and that `ci.yml` succeeded on `main` for it. `build-amd64` and `build-arm64`, identical
  but for runner and platform, build into the engine, run `container_tests` and the
  notices check, then sign in and push the same build by digest with no tag, failing
  unless the pushed config digest is the tested image's id. `publish` refuses a version
  that exists in either registry, writes `X.Y.Z` in both with `imagetools create`, and
  reads both back. No `latest`. This file defines CI.
- **`scripts/notices_coverage.py`**, new: the check in P10, exit 1 while anything is
  uncovered. Defines CI.
- **`tests/test_release_workflow.py`**, new, structural: pins the trigger, per-job
  permissions, the secret only inside the `release` environment and only in a step, the
  two builds identical, the order test, notices, sign-in, push, the tested-equals-pushed
  check, and SHA-pinned actions. Added to `MARKED` in `tests/test_structural_lane.py`.
- **`tests/test_notices_coverage.py`**, new: the coverage rules on synthetic inputs.
- **`scripts/github/configure.sh`**: a `release` environment that admits tags matching
  `v*` only, applied and checked like every other setting. The maintainer runs `apply`.
- **`CONTRIBUTING.md`**: a "Releases" section (version scheme, the release change, the
  changelog convention, how to tag, what the workflow refuses, where the credential
  lives); the settings list and the CI-defining file list gain the new files.
- **`CHANGELOG.md`**, new, with the `0.1.0` entry.
- **`docs/DEPLOYMENT.md`** section 2: the published image names, and that there is no
  `latest`.

## What does not change

- `ci.yml` and everything it runs; `ruleset-main.json`; the `Dockerfile`.
- `THIRD_PARTY_NOTICES.md`. Closing the gap in P10 is a separate decision.
- README's quick start. It keeps `docker build` until an image exists to point at.
- No tag is pushed, no package is made public, no secret is stored by this change.

## Constraints

- Nothing is pullable by name until every check has passed for both architectures.
- The Docker Hub token is readable only by a `v*` tag run, only in the sign-in steps, and
  never before the image has passed its tests.
- Every action pinned by SHA; no action outside the allowed set.
- Every commit `Glosswork <hello@glosswork.dev>`, one root.

## Checklist

1. [ ] Run the new structural and unit tests against the unfixed tree (no workflow, no
   script) and record how each failed.
2. [ ] Add `scripts/notices_coverage.py` and its tests.
3. [ ] Add `release.yml` and `tests/test_release_workflow.py`; add it to `MARKED`.
4. [ ] Add the `release` environment to `configure.sh`.
5. [ ] CONTRIBUTING "Releases", CHANGELOG, DEPLOYMENT.
6. [ ] Run the Accept block.
7. [ ] Close out in two commits; push once; open the pull request once.

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
- **AC8.** Each `test_release_workflow.py` rule fails on a mutation of `release.yml` that
  breaks it: the push moved before the tests; the Docker Hub secret in a job's `env`; a
  `pull_request` trigger; the arm64 build's steps differing from the amd64 build's.
- **AC9.** The pull request's CI run ends with `ci-ok` `success` (read through
  `actions/runs?head_sha=`).

What cannot be accepted before the maintainer's approvals, and is therefore not in this
block: a tag run, the published images, and anonymous pulls. Those are the done-when's
last clauses and are proven after the first tag.

## Adversarial pass

## Deviations from the approved plan

## Durable content moved out of this plan
