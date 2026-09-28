# 3: The image's notices cover every package it redistributes

| | |
| --- | --- |
| Issue | #3, not yet filed (see "Open before approval"); the number is provisional |
| Branch | `3-generated-notices`, cut from `1-release-images` at `bdb8172`, not from `main` (see "How this relates to change 1") |
| Spec | `THIRD_PARTY_NOTICES.md` "What this file covers"; `Dockerfile` runtime stage; `README.md` licence paragraph; `AGENTS.md` "Commands" and "Where things are"; `CONTRIBUTING.md` "What CI runs" |
| Decisions | none changed |
| Requirements | FR-P1 (one image) |
| Depends on | change 1 (PR #2) merged first: this change extends its `scripts/notices_coverage.py` |

## Why

Publishing the image is redistribution. The image carries about 160 third-party packages,
and most of their licences (MIT, BSD, ISC, Apache-2.0) require the licence text and
copyright notice to travel with every copy. `THIRD_PARTY_NOTICES.md` covers the three fonts
and the embedding model and says it covers nothing else. The maintainer decided on
2026-09-28 that the first public image waits for this gap to close, and that change 1's
notices check stays a hard gate rather than a warning.

## How this relates to change 1

Change 1 (PR #2, branch `1-release-images`, open, not merged) adds `scripts/notices_coverage.py`,
the check this change must make pass, and the release workflow that runs it before any push.
Its own adversarial pass (its F1) left open which remedy the check accepts, headings in the
notices file or a generated licences file in the image, "decided with that change". This is
that change.

**Chosen: this branch is cut from `1-release-images` and extends that checker in place.**
Nothing is pushed to `1-release-images`; this branch carries change 1's four commits
underneath its own until PR #2 merges.

- Landing a second copy of the checker here was rejected: two definitions of "which npm
  packages count" would drift, and the release gate would be judging by one while this
  change's test judged by the other.
- Cutting from `main` and treating the checker as external was rejected: the generator and
  its test need the checker's npm rule (`npm_production_names`, including
  `BUNDLED_BUILD_TOOLS`), and importing a file that is not on the branch is not possible.

**What it costs.** The order is fixed: PR #2 merges first (with its closeout), then this
branch merges `origin/main` locally as hello@glosswork.dev, is verified on that merge, and
opens its pull request against `main`. This change's PR cannot open before #2 merges, because
it would carry #2's commits. The first tag (change 1's A6) waits for both.

## Premises

- **P1. The gap is 109 of 163, as change 1 measured it.** Re-measured 2026-09-28: an image
  built from `bdb8172` (`docker build -t gw18:base --build-arg GW_REVISION=bdb8172 .`, exit
  0), then `uv run python scripts/notices_coverage.py --image gw18:base`, exit 1: Python 57
  distributions, 54 covered, 3 not (`flatbuffers`, `sqlite-vec`, `tokenizers`); npm 106, 0
  covered.
- **P2. The runtime Python set in `uv.lock` is 60 packages, and the image holds 57 of them.**
  `UV_OFFLINE=1 uv export --frozen --no-dev --no-hashes --no-emit-project` exits 0 offline and
  lists 60. Compared with the distributions `importlib.metadata` lists inside `gw18:base`: the
  three in the lock and not the image are `colorama` (`sys_platform == 'win32'`), `pywin32`
  (win32) and `httpx2-jsfetch` (emscripten); the one in the image and not the lock set is
  `glosswork` itself. `uv.lock` has 71 packages in all; the other 11 are development-only.
- **P3. 57 of the 60 runtime Python packages carry licence text in the wheel `uv.lock`
  records.** Measured by downloading one lock-recorded wheel per package (a `py3-none-any`
  wheel where one exists, else a manylinux x86_64 one), checking each against the lock's
  sha256 (all matched), and listing its licence files. The three with none are the same three
  as P1. `tokenizers`' lock-recorded sdist does carry `tokenizers/LICENSE`. `flatbuffers`
  and `sqlite-vec` have no sdist in the lock.
- **P4. Upstream licence texts exist for the two with no text anywhere in the lock.**
  Fetched 2026-09-28, HTTP 200 each: `https://raw.githubusercontent.com/google/flatbuffers/v25.12.19/LICENSE`
  (Apache-2.0, sha256 prefix `cfc7749b96f63bd3`);
  `https://raw.githubusercontent.com/asg017/sqlite-vec/v0.1.9/LICENSE-MIT` (`6ce72bbe12d975bd`,
  "Copyright (c) 2024 Alex Garcia") and `.../LICENSE-APACHE` (`a38070a94d4afd9c`). The
  package's metadata declares "MIT License, Apache License, Version 2.0", so both texts go in.
  The full digests are recorded by the generator, not here.
- **P5. Licence files do not differ between the two architectures the image is built for.**
  For all 17 runtime packages with platform wheels for both linux x86_64 and aarch64, the
  licence files under `.dist-info/` hash identically between the two (measured 2026-09-28,
  downloading both wheels and comparing sha256 per file). So one wheel per package is enough.
- **P6. Every npm package the checker counts carries a licence file, and every lock entry
  for it has a registry URL and an integrity hash.** After `npm ci` in `web/` (exit 0), each
  of the 106 names from `npm_production_names` has a `LICENSE`-style file at its package
  root. Declared licences: MIT 102, OFL-1.1 3 (the `@fontsource` fonts), ISC 1. The lockfile
  holds 107 matching entries (`@types/unist` at both 2.0.11 and 3.0.3), each with a
  `https://registry.npmjs.org/` `resolved` URL and a `sha512-` integrity.
- **P7. About 450 KB of licence text is involved.** The Python licence files total 361 KB
  across 100 files (including duplicates outside `.dist-info/`); the largest is numpy's at
  47 KB, which carries the notices of the libraries numpy bundles. The npm files are about
  1 KB each.
- **P8. No licence text involved today would trip the documentation tests or a code fence.**
  `tests/test_documentation_structure.py` scans every tracked file for backticked `*.md`
  paths and `.md` links. Over the 474 licence files in the downloaded wheels and all of
  `web/node_modules`, none contains a backticked `.md` path, a `](...md)` link, or a line
  opening with three backticks or tildes (measured 2026-09-28).
- **P9. A root-level markdown file is code to CI's change classifier** (`CONTRIBUTING.md`
  "What CI runs": everything not on the documentation list is code), so a change to the
  generated file runs the full pipeline, as a change to `THIRD_PARTY_NOTICES.md` does
  (read; `tests/test_ci_changes.py` pins the latter).
- **P10. The release workflow calls the checker with its defaults**
  (`uv run python scripts/notices_coverage.py --image glosswork:release --summary ...`,
  `.github/workflows/release.yml` lines 149 and 203, read). So a checker that reads a second
  notices file by default needs no workflow edit, and `tests/test_release_workflow.py`, which
  pins those commands, is untouched.
- **P11. The checker reads the notices from the repository, not from the image.** Read at
  `scripts/notices_coverage.py`, `main()`. What proves the file is in the image is
  `container_tests/test_image_notices.py`, which copies `/app/THIRD_PARTY_NOTICES.md` out and
  compares it byte for byte, and which the release workflow runs before the checker.

## What changes

- **A generated file, THIRD_PARTY_LICENSES.md, at the repository root**, committed, and
  copied into the image at `/app/` beside `LICENSE` and `THIRD_PARTY_NOTICES.md`. It holds
  one entry per package: every runtime Python package in `uv.lock` (P2: 60, including the
  three that never install on Linux, because the list is the lockfile's and not one
  platform's) and every npm package the checker counts (P6: 107 entries). Each entry is:
  - a heading naming the package in backticks with its version, as in
    `` ## `react` 19.2.8 ``, which is what the checker counts;
  - one line: ecosystem, the licence the package declares, and the URL of the artifact the
    text was taken from;
  - each licence file's path in that artifact and its full text, **indented four spaces**
    rather than fenced, so no licence text can close a fence and no line of it can begin
    with `#` and be read as a heading.

  The copyright notice is whatever the package's own files state. Where a text names no
  holder (the Apache-2.0 body does not), the entry does not invent one, which is the rule
  change 035 applied to the embedding model (its D4). Identical texts are not deduplicated:
  that would save about 130 KB and cost every entry a cross-reference.
- **A generator, `scripts/third_party_licenses.py`**, standard library only. It reads the two
  lockfiles and nothing installed, downloads each package's lock-recorded artifact, refuses
  any whose hash differs from the lock's, and writes the file deterministically, sorted by
  ecosystem then name then version. Where a package's text comes from, in order:
  1. Python: the licence files under the wheel's `.dist-info/` (a `py3-none-any` wheel, else
     a manylinux x86_64 wheel, else the first listed; P5); if there are none, the licence
     files in the lock-recorded sdist (this is `tokenizers`, P3).
  2. npm: the licence files at the tarball's package root.
  3. Otherwise, an **override** from `scripts/third_party_licenses.toml`, which names the
     package, the exact version it applies to, and each upstream URL with its sha256
     (`flatbuffers` and `sqlite-vec`, P4).
  4. Otherwise it exits 1 naming the package. It also exits 1 for an override whose package
     or version is no longer in the lock, so an override cannot outlive the release it was
     checked against.

  `--check` regenerates in memory and exits 1 if the result differs from the committed file.
  Downloads are cached under a directory the caller names (default a temporary one), never
  in the repository.
- **`scripts/notices_coverage.py`** (change 1's checker), two edits:
  - `named_in_notices` counts headings in THIRD_PARTY_LICENSES.md as well as in
    `THIRD_PARTY_NOTICES.md`, through a new `--licenses` option whose default is the
    repository's file (P10). Its docstring's "gives it a heading" rule names both files.
  - `npm_production_names` is rebuilt on a new `npm_production_entries(lock)` that also
    returns each entry's version, `resolved` URL and integrity. One rule, used by the
    checker, the generator and the new test. Its behaviour is unchanged, which the existing
    tests in `tests/test_notices_coverage.py` fence.
- **`tests/test_third_party_licenses.py`**, new, in the structural lane (and added to
  `MARKED` in `tests/test_structural_lane.py`), offline:
  - T1: every runtime Python package in `uv.lock` (from `uv export`, P2) and every npm entry
    from `npm_production_entries` has exactly one entry at that version.
  - T2: no entry names a package or version the lockfiles no longer hold.
  - T3: every entry carries at least one non-empty licence text.
  - T4: on synthetic inputs, a lockfile with one added package fails the coverage function,
    naming that package. This is the done-when's "a new dependency without an entry fails",
    proven independently of the real lockfiles.
  - T5: every override names a package at the version the lock holds.
- **`Dockerfile`**: `COPY LICENSE THIRD_PARTY_NOTICES.md THIRD_PARTY_LICENSES.md /app/`.
- **`container_tests/test_image_notices.py`**: the fixture copies the new file out too, and a
  new test compares it byte for byte with the repository's.
- **`tests/test_ci_changes.py`**: the new file joins `THIRD_PARTY_NOTICES.md` in the "is
  code" cases. A fence (P9: it is already code by rule), labelled as one.
- **`THIRD_PARTY_NOTICES.md`**: the "It is not a complete inventory" paragraph is replaced by
  one saying where every other package's notice is and how that file is made. The fonts and
  model entries do not change.
- **Docs**: `README.md` licence paragraph names the new file; `AGENTS.md` gains a "Commands"
  row for regenerating it and a "Where things are" row; `CONTRIBUTING.md` says a dependency
  change regenerates the file in the same commit, and that T1 is what fails if it does not.

## What does not change

- The base image's own contents (CPython, Debian, the base image's `pip`), which the checker
  already excludes and which are the base image's to notice.
- The fonts' and the embedding model's entries in `THIRD_PARTY_NOTICES.md`, and the
  container tests that pin them.
- The checker's rule for Python: a distribution that installs a licence file stays covered by
  that alone. After this change every one is also covered by a heading.
- Development-only packages (pytest, ruff, mypy, and the npm dev tree apart from `vite` and
  `tailwindcss`) get no entry. The image does not redistribute them. This reads the
  done-when's "every Python package in `uv.lock`" under its governing clause, "every work the
  runtime image redistributes"; see "Open before approval".
- The release workflow and `tests/test_release_workflow.py` (P10).
- Anything in `src/` or `web/`.

## Constraints

- No new dependency. The generator uses `urllib`, `hashlib`, `zipfile`, `tarfile`, `tomllib`.
- Nothing is downloaded in CI or in any test: T1 to T5 read committed files only.
  `--check` needs the network and is an Accept criterion, not a guard.
- `uv.lock` and `web/package-lock.json` are not modified (AGENTS.md non-negotiable 1, and the
  `-diff` trap).
- Every commit is `Glosswork <hello@glosswork.dev>`, author and committer, with no trailer.
  Every ref pushed has `e5a047b` as its only root.
- Nothing is pushed to `1-release-images`.

## Checklist

1. [ ] Write T1 to T5, the checker test additions for `npm_production_entries` and the
   `--licenses` file, the container test for the new file, and the `test_ci_changes.py`
   fence. Run them against this plan commit and record how each failed (T1 to T3 and T5:
   the file and the overrides do not exist; T4: the function does not exist).
2. [ ] Refactor `npm_production_names` onto `npm_production_entries`; add `--licenses`. The
   existing checker tests stay green.
3. [ ] Write `scripts/third_party_licenses.py` and `scripts/third_party_licenses.toml` with
   the two overrides.
4. [ ] Generate THIRD_PARTY_LICENSES.md. Record here: entries per ecosystem, the declared
   licences by count, which entries took an sdist or an override, and the file's size.
5. [ ] Dockerfile `COPY`; build; run the checker and the container notices tests.
6. [ ] Docs: `THIRD_PARTY_NOTICES.md` scope paragraph, `README.md`, `AGENTS.md`,
   `CONTRIBUTING.md`.
7. [ ] Run the Accept block and record its output here.

## Accept

- **AC1.** `uv run pytest -q tests/test_third_party_licenses.py tests/test_notices_coverage.py tests/test_structural_lane.py tests/test_ci_changes.py tests/test_documentation_structure.py` exits 0.
- **AC2.** `uv run pytest -q -m structural` exits 0.
- **AC3.** `uv run pytest -q` exits 0.
- **AC4.** `uv run ruff check .` exits 0.
- **AC5.** `uv run ruff format --check .` exits 0.
- **AC6.** `uv run python scripts/third_party_licenses.py --check` exits 0: regenerating from
  the lockfiles reproduces the committed file byte for byte.
- **AC7.** The task's acceptance test.
  `docker build -t glosswork:local --build-arg GW_REVISION=$(git rev-parse HEAD) .` exits 0,
  then `uv run python scripts/notices_coverage.py --image glosswork:local` exits 0 and prints
  0 not covered for both ecosystems.
- **AC8.** `GW_IMAGE=glosswork:local uv run pytest -q container_tests/test_image_notices.py`
  exits 0.
- **AC9.** The real file, mutated, turns T1 red. On a scratch copy of the tree, delete the
  `react` entry from THIRD_PARTY_LICENSES.md; `uv run pytest -q tests/test_third_party_licenses.py`
  exits 1 naming `react`. Separately, bump one npm version in a scratch copy of
  `web/package-lock.json`; it exits 1 naming that package at both versions.
- **AC10.** `grep -c 'not a complete inventory' THIRD_PARTY_NOTICES.md` prints 0 (and exits 1,
  which is the expected answer).
- **AC11.** `git rev-list --max-parents=0 HEAD` prints only `e5a047b`, and
  `git log --format='%an <%ae> %cn <%ce>' origin/main..HEAD | sort -u` prints only
  `Glosswork <hello@glosswork.dev> Glosswork <hello@glosswork.dev>`.

## Adversarial pass

Not yet run. It is run by a session that did not write this plan.

## Open before approval

- **The issue is not filed.** Filing #3 was refused by this run's permission check. The
  issue text is ready. If the number GitHub assigns is not 3, the branch and this file are
  renamed before anything is pushed; nothing here has left this machine.
- **The reading of "every Python package in `uv.lock`".** This plan covers the 60 runtime
  packages and not the 11 development-only ones, under the done-when's governing clause
  "every work the runtime image redistributes". The maintainer confirms that reading when
  approving.
- **The notices text ships publicly.** The generated file is third-party text reproduced
  verbatim, plus one generated line per entry. The maintainer approves its shape (the entry
  format above, and the no-invented-holder rule) with the plan.

## Deviations from the approved plan

None yet.

## Durable content moved out of this plan

Not yet.
