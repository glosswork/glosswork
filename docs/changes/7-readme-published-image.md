# 7: The README's run line names a published image

| | |
| --- | --- |
| Issue | #7, <https://github.com/glosswork/glosswork/issues/7> |
| Branch | `7-readme-published-image` |
| Spec | `README.md` "Run it"; read, not changed: `docs/DEPLOYMENT.md` section 2, `CONTRIBUTING.md` "Releases" |
| Decisions | none |
| Requirements | none |
| Depends on | nothing: 0.1.0 is already published (change 1, #1) |

## Why

The README's first instruction builds a 496 MB image from source and says no release image
exists. Since 0.1.0 one does, and it can be pulled with no account. Pulling is a shorter and
more reliable first step than building.

## Premises

- **P1.** The README's "Run it" block begins with `docker build -t glosswork .` and says "No
  versioned release image is published yet". Read at `README.md` lines 16 to 30 on `main`
  (`0d6fd1f`), measured by `grep -n "docker build\|No versioned" README.md` (lines 19 and 29).
- **P2.** `docker.io/glosswork/glosswork:0.1.0` pulls with no credential. Measured
  2026-09-29 with `DOCKER_CONFIG` pointing at an empty directory: `docker pull
  docker.io/glosswork/glosswork:0.1.0` exit 0, digest
  `sha256:12c783a909e7727065d878d40e9b8c15e7c93f0cc7917236a1b621281da389b4`.
- **P3.** That image runs the README's own command unchanged apart from the image name, and
  signs in with the bootstrap email and password over plain `http://localhost`. Measured
  2026-09-29 on a scratch container from the pinned digest (arm64) with `GW_BASE_URL`,
  `GW_BOOTSTRAP_ADMIN_EMAIL` and `GW_BOOTSTRAP_ADMIN_PASSWORD`: `/readyz` answered
  `{"status":"ok"}` after 3 seconds; `POST /api/v1/auth/login` answered 200 with `Secure`
  session and CSRF cookies; `GET /api/v1/object-types` with that cookie jar answered 200.
  Not established: a real browser. curl sends `Secure` cookies to `localhost`; whether every
  browser does is not measured here, and the old README had the same flags (F3).
- **P3a.** A bootstrap password under 12 characters stops the container. Measured 2026-09-29:
  `GW_BOOTSTRAP_ADMIN_PASSWORD=short` exits with code 3 on first start, log
  `ValidationFailedError: Password must be at least 12 characters` from
  `src/glosswork/services/passwords.py` line 44 via `src/glosswork/app.py` line 117. So the
  README says so (F5).
- **P4.** A deployment names a version by its tag, and there is no `latest`. Read at
  `CONTRIBUTING.md` "Releases" ("There is no `latest` tag and no moving `X.Y` tag: a
  deployment names the version it runs. A published version is never replaced.") and
  `docs/DEPLOYMENT.md` section 2, which says to use the registry name "in place of
  `glosswork` above and skip the build". Neither document pins by digest. So the README
  names the tag, not a digest.
- **P5.** The GitHub registry copy is not yet anonymously pullable, so the README names the
  Docker Hub image. Source: the release task's record (the ghcr anonymous pull moved to a
  later task on 2026-09-29). Not re-measured here, because the README does not name ghcr.
- **P6.** The change is documentation-only: `README.md` and `docs/**` only
  (`CONTRIBUTING.md` "What CI runs"), so CI runs `changes`, `guards`, `secrets`, `sast` and
  `ci-ok`.

## What changes

- `README.md` "Run it": the `docker build` line goes; `docker run` names
  `docker.io/glosswork/glosswork:0.1.0`; the "No versioned release image" sentence is
  replaced by one saying each version keeps its tag and there is no `latest`, pointing at
  `CHANGELOG.md`, and one giving the build-from-source route as the alternative. The
  password placeholder says "at least 12 characters" (P3a).

## What does not change

- `docs/DEPLOYMENT.md`, `CONTRIBUTING.md`, the image, the release workflow, any code.
- The rest of the README, including "Running from source".

## Constraints

- No em dashes. Every claim the new text makes is checkable against `CONTRIBUTING.md`
  "Releases", `CHANGELOG.md` or a command.
- Any link or backticked `*.md` path names a file on this branch
  (`tests/test_documentation_structure.py`).

## Checklist

- [x] 1. Run AC1, AC2 and AC6 against the unfixed tree and record how each failed.
  At `f3b95d4`: AC1a exit 1 (the first line was `docker build -t glosswork .`), AC1b exit 1,
  AC2 exit 1, AC6 exit 1.
- [x] 2. Edit the "Run it" block and its paragraph.
- [x] 3. Run the Accept block. At `bec8817`, 2026-09-29: AC1a 0, AC1b 0, AC2 0, AC3 0
  (index `sha256:12c783a9...89b4`, `linux/amd64` and `linux/arm64`), AC4 0 (96 passed),
  AC6 0. AC5 is run after the plan is deleted, by the separate verifying session.

## Accept

- **AC1.** The block's first command line is a `docker run` naming the published image:
  `awk '/^## Run it/{f=1} f&&/^```bash/{getline; print; exit}' README.md | grep -q '^docker run '`
  and `sed -n '/^## Run it/,/^## Point/p' README.md | grep -q 'docker.io/glosswork/glosswork:0.1.0$'`
- **AC2.** The stale sentence is gone: `! grep -q 'No versioned release image is published yet' README.md`
- **AC3 (fence).** The named reference resolves with no credential, for both architectures:
  `d=$(mktemp -d); DOCKER_CONFIG=$d docker buildx imagetools inspect docker.io/glosswork/glosswork:0.1.0`
  exit 0, listing `linux/amd64` and `linux/arm64`. A fence: it passes on the unfixed tree,
  because the image exists independently of this change.
- **AC4 (fence).** The structural lane passes: `uv run pytest -q -m structural` exit 0.
- **AC5.** Only `README.md` differs from `main` once the plan is deleted:
  `git diff --name-only origin/main...HEAD` prints `README.md` alone.
- **AC6.** The password floor is stated:
  `sed -n '/^## Run it/,/^## Point/p' README.md | grep -q 'at least 12 characters'`

## Adversarial pass

Run by a separate Opus session on 2026-09-29 against `6811e8a`, read-only.

- **F1.** The README's tag goes stale at the next release: "Releases" in `CONTRIBUTING.md`
  updates `pyproject.toml`, `uv.lock` and `CHANGELOG.md`, not the README, and no test ties
  them. **Disposition:** out of scope, because either fix (a line in "Releases" or a
  structural test) changes a file this plan keeps. Recorded for the maintainer.
- **F2.** `docs/DEPLOYMENT.md` section 2 and the 0.1.0 entry in `CHANGELOG.md` name the
  GitHub registry copy, which answered 401 to an anonymous token request. **Disposition:**
  the README names Docker Hub only; the other two are left to the task that makes the GitHub
  copy public.
- **F3.** P3 claimed sign-in on the strength of `/readyz` alone. **Disposition:** fixed. P3
  now records a measured password login and a cookie round trip, and says a real browser is
  not established.
- **F4.** AC3 and AC4 cannot fail on the unfixed tree, and AC3 downloaded the whole image.
  **Disposition:** fixed. Both are labelled fences, and AC3 reads the manifest instead,
  which also checks both architectures.
- **F5.** A short bootstrap password stops the container with no hint in the README.
  **Disposition:** fixed. Measured (P3a) and stated in the README (AC6).
- **F6.** `docs/DEPLOYMENT.md` section 2 has a sentence about `/healthz` spliced into an
  unrelated paragraph. **Disposition:** out of scope, recorded.
- **F7.** Anonymous Docker Hub pulls are rate-limited per address. **Disposition:** none.

## Deviations from the approved plan

- **D1. The maintainer approved the change, not this file's text, before execution.** The
  approval (2026-09-29) covered filing the issue, the branch, the commits and a pull request
  with CI green. The plan's final text is in the pull request for review before the merge,
  which is the maintainer's.
- **D2. Independent verification ran after the closeout, not before it**, because AC5 is only
  true once the plan file is deleted.

## Durable content moved out of this plan

None. The README is the durable content, and P3a's password floor is already
documented as `GW_PASSWORD_MIN_LENGTH=12` in `.env.example`. F1, F2 and F6 are recorded in
the pull request description for the maintainer.
