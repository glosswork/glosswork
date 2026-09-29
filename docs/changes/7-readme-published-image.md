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
  signs in with the bootstrap email and password. Measured 2026-09-29: a scratch container
  from the pinned digest with `GW_BASE_URL`, `GW_BOOTSTRAP_ADMIN_EMAIL` and
  `GW_BOOTSTRAP_ADMIN_PASSWORD` answered `/readyz` with `{"status":"ok"}` after 3 seconds.
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
  `CHANGELOG.md`, and one giving the build-from-source route as the alternative.

## What does not change

- `docs/DEPLOYMENT.md`, `CONTRIBUTING.md`, the image, the release workflow, any code.
- The rest of the README, including "Running from source".

## Constraints

- No em dashes. Every claim the new text makes is checkable against `CONTRIBUTING.md`
  "Releases", `CHANGELOG.md` or a command.
- Any link or backticked `*.md` path names a file on this branch
  (`tests/test_documentation_structure.py`).

## Checklist

- [ ] 1. Run AC1 and AC2 against the unfixed tree and record how each failed.
- [ ] 2. Edit the "Run it" block and its paragraph.
- [ ] 3. Run the Accept block.

## Accept

- **AC1.** The block's first command line is a `docker run` naming the published image:
  `awk '/^## Run it/{f=1} f&&/^```bash/{getline; print; exit}' README.md | grep -q '^docker run '`
  and `sed -n '/^## Run it/,/^## Point/p' README.md | grep -q 'docker.io/glosswork/glosswork:0.1.0$'`
- **AC2.** The stale sentence is gone: `! grep -q 'No versioned release image is published yet' README.md`
- **AC3.** The named reference pulls with no credential:
  `d=$(mktemp -d); DOCKER_CONFIG=$d docker pull docker.io/glosswork/glosswork:0.1.0` exit 0.
- **AC4.** The structural lane passes: `uv run pytest -q -m structural` exit 0.
- **AC5.** Only `README.md` differs from `main` once the plan is deleted:
  `git diff --name-only origin/main...HEAD` prints `README.md` alone.

## Adversarial pass

## Deviations from the approved plan

## Durable content moved out of this plan
