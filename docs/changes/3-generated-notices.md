# 3: The image's notices cover every package it redistributes

| | |
| --- | --- |
| Issue | #3, https://github.com/glosswork/glosswork/issues/3 |
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
  records, but only 56 carry it under `.dist-info/`.** Measured by downloading one
  lock-recorded wheel per package (a `py3-none-any` wheel where one exists, else a manylinux
  x86_64 one), checking each against the lock's sha256 (all matched), and listing its
  licence files. The three with none anywhere are the same three as P1. `tokenizers`'
  lock-recorded sdist does carry `tokenizers/LICENSE`. `flatbuffers` and `sqlite-vec` have
  no sdist in the lock. *Corrected by the adversarial pass (F1):* the 57th, `onnxruntime`,
  has nothing under `.dist-info/` and no sdist in the lock; its texts are
  `onnxruntime/LICENSE` and `onnxruntime/ThirdPartyNotices.txt` (337 KB, the notices for
  the libraries onnxruntime compiles in), inside the package directory. Re-measured
  2026-09-28 on the 1.29.0 cp313 manylinux x86_64 wheel and in the aarch64 image: both files
  hash identically in both.
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
  `https://registry.npmjs.org/` `resolved` URL and a `sha512-` integrity. *Extended by the
  adversarial pass (F3):* with `rolldown` added to the counted set, 107 names and 108
  entries; `rolldown` 1.2.5 has one lock entry, marked `dev`.
- **P7. About 900 KB of licence text is involved.** *Corrected by the adversarial pass
  (F10); it read "about 450 KB".* Under the selection rule in "What changes", measured
  2026-09-28: Python 93 files, 648 KB, of which `onnxruntime`'s ThirdPartyNotices.txt is
  337 KB and numpy's LICENSE.txt 47 KB (it carries the notices of the libraries numpy
  bundles); npm 108 files, 242 KB, most of it Vite's LICENSE.md, which carries the notices
  of what Vite bundles. Before the overrides and the `tokenizers` sdist.
- **P8. No licence text involved today would trip a test that reads every tracked file, or
  a code fence.** `tests/test_documentation_structure.py` has four scans over every tracked
  file: backticked `*.md` paths, `.md` links, internal decision numbers
  (`INTERNAL_DECISION`), and `DD-` citations. Over the 474 licence files in the downloaded
  wheels and all of `web/node_modules`, none contains a backticked `.md` path, a
  markdown link to a relative `.md` target, or a line opening with three backticks or tildes (measured 2026-09-28).
  *Extended by the adversarial pass (F7):* the other two scans were not measured. Over the
  208 files the selection rule takes (with `pywin32`'s and the `tokenizers` sdist's), all
  four patterns, copied from the test, match nothing (measured 2026-09-28). gitleaks 8.30.1
  with `.gitleaks.toml` over the Python texts and five npm packages' texts (702 KB): no
  leaks, exit 0.
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

Premises P12 to P15 were added by the adversarial pass.

- **P12. The base image carries its own notices.** In an image built from `bdb8172`:
  `/usr/local/lib/python3.13/LICENSE.txt` exists; 87 Debian packages are installed and
  `/usr/share/doc/*/copyright` has 87 files; the base image's `pip` has a `licenses/`
  directory in its `.dist-info`. So "the base image's to notice" (What does not change) is
  measured, not assumed (F11).
- **P13. The shipped JavaScript opens with rolldown's runtime.** Vite 8.2.2 bundles with
  `rolldown` 1.2.5 (dev-only in the lockfile). The first bytes of the bundle in
  `/app/web/dist/assets/` are its CommonJS interop helpers (`Object.create`,
  `Object.defineProperty`, a `__esModule` check, a `Symbol.toStringTag` of `Module`), which
  rolldown takes from esbuild. Rolldown's package ships `LICENSE` (VoidZero) and
  `THIRD-PARTY-LICENSE`, which carries the rollup and esbuild ("Copyright (c) 2020 Evan
  Wallace") notices. Read 2026-09-28 (F3).
- **P14. Six compiled packages link third-party code that no file in their wheel notices.**
  Measured 2026-09-28 by reading the extension modules in the image for Cargo registry
  source paths, which Rust leaves in panic messages and so undercounts: `hf-xet` at least
  73 crates, `tokenizers` 42, `pydantic-core` 26, `cryptography` 10 (and the string
  "OpenSSL 3", statically linked), `watchfiles` 9, `rpds-py` 6. Their sdists' `Cargo.lock`
  files list 416, 165, 103, 32, 48 and 18 registry crates, some for other platforms. Each
  wheel carries only the package's own licence. `numpy` (OpenBLAS, libgfortran) and
  `onnxruntime` (abseil, Eigen, protobuf, SQLite) do notice what they link. This is
  outside the lockfiles, so outside this plan as written; see F8 and "Open before
  approval".
- **P15. Some licence texts are not clean LF text.** Over the 208 files: 14 (all
  `pywin32`) contain carriage returns, 8 (`numpy`) have no final newline, 6 contain tabs,
  7 carry trailing whitespace. None is non-UTF-8, none has a byte-order mark, and none
  contains a character Python's `str.splitlines` treats as a line break other than
  `\n` and `\r` (measured 2026-09-28). `.gitattributes` sets `* text=auto eol=lf` (F5).

## What changes

- **A generated file, THIRD_PARTY_LICENSES.md, at the repository root**, committed, and
  copied into the image at `/app/` beside `LICENSE` and `THIRD_PARTY_NOTICES.md`. It holds
  one entry per package: every runtime Python package in `uv.lock` (P2: 60, including the
  three that never install on Linux, because the list is the lockfile's and not one
  platform's) and every npm package the checker counts (P6: 108 entries, with `rolldown`).
  The file opens with a title, one paragraph saying it is generated by
  `scripts/third_party_licenses.py` from `uv.lock` and `web/package-lock.json` and is not
  edited by hand, and nothing else. Each entry is:
  - a heading naming the package in backticks with its version, as in
    `` ## `react` 19.2.8 ``, which is what the checker counts. **No other heading in the
    file carries backticks**, because the checker counts every backticked word in a heading
    as a package (F5);
  - one line: ecosystem, the licence the package declares, and the URL of the artifact the
    text was taken from;
  - each licence file's path in that artifact, in plain text, and its full text,
    **indented four spaces** rather than fenced, so no licence text can close a fence and no
    line of it can begin with `#` and be read as a heading. Before indenting, the text is
    normalised: CRLF and lone CR become LF, and it ends with exactly one LF (P15). A text
    holding any other character `str.splitlines` breaks on (U+000B, U+000C, U+001C to U+001E,
    U+0085, U+2028, U+2029) makes the generator exit 1 naming the file, because the
    checker splits with `splitlines` and such a character would start an unindented line
    (F5). None does today.

  The copyright notice is whatever the package's own files state. Where a text names no
  holder (the Apache-2.0 body does not), the entry does not invent one, which is the rule
  change 035 applied to the embedding model (its D4). Identical texts are not deduplicated:
  that would save about 130 KB and cost every entry a cross-reference.
- **A generator, `scripts/third_party_licenses.py`**, standard library only. It reads the two
  lockfiles and nothing installed, downloads each package's lock-recorded artifact, refuses
  any whose hash differs from the lock's, and writes the file deterministically, sorted by
  ecosystem then name then version. The runtime Python set is one function in the
  generator, `runtime_python_packages()`, which runs
  `uv export --frozen --no-dev --no-hashes --no-emit-project` with `UV_OFFLINE=1` and reads
  its `name==version` lines (P2); the new test imports it rather than running its own
  export, so there is one definition (F6). A **licence file** is a file whose name matches
  the checker's `LICENCE_FILE` pattern or begins `THIRD-PARTY`, `THIRD_PARTY` or
  `ThirdParty` (any case), and does not end `.py`. Where a package's text comes from, in
  order:
  1. Python (a `py3-none-any` wheel, else a manylinux x86_64 wheel, else the first listed;
     P5): every file under `.dist-info/licenses/` (the directory PEP 639 installs declared
     licence files into, which is how `PyJWT`'s and `sse-starlette`'s `AUTHORS` files come
     along, F2) and every licence file directly in `.dist-info/`; if there are none, every
     licence file anywhere in the wheel (this is `onnxruntime`, P3, F1); if there are none,
     every licence file in the lock-recorded sdist (this is `tokenizers`, P3).
  2. npm: the licence files at the tarball's package root, `THIRD-PARTY-LICENSE` included
     (this is `rolldown`, P13, F3).
  3. Otherwise, an **override** from `scripts/third_party_licenses.toml`, which names the
     package, the exact version it applies to, and each upstream URL with its sha256
     (`flatbuffers` and `sqlite-vec`, P4).
  4. Otherwise it exits 1 naming the package. It also exits 1 for an override whose package
     or version is no longer in the lock, so an override cannot outlive the release it was
     checked against.

  `--check` regenerates in memory and exits 1 if the result differs from the committed file.
  Downloads are cached under a directory the caller names (default a temporary one), never
  in the repository.
- **`scripts/notices_coverage.py`** (change 1's checker), three edits:
  - `named_in_notices` counts headings in THIRD_PARTY_LICENSES.md as well as in
    `THIRD_PARTY_NOTICES.md`, through a new `--licenses` option whose default is the
    repository's file (P10). Its docstring's "gives it a heading" rule names both files.
  - `npm_production_names` is rebuilt on a new `npm_production_entries(lock)` that also
    returns each entry's version, `resolved` URL and integrity. For a name in
    `BUNDLED_BUILD_TOOLS` it returns every lock entry of that name, although each is marked
    `dev`. One rule, used by the checker, the generator and the new test.
  - `BUNDLED_BUILD_TOOLS` gains `rolldown`, and its comment records the measurement (P13,
    F3). This changes what the checker counts, from 106 npm packages to 107.
  - `tests/test_notices_coverage.py` changes with it, so its tests are not a fence of
    unchanged behaviour. Every test that calls `main()` passes `--licenses` pointing at a
    file under `tmp_path`: with the new default, `test_main_exits_1_and_names_what_is_uncovered`
    would read the real file, in which `ms` has an entry, and fail from checklist step 4 on
    (F4). The all-covered fixture gains a `rolldown` heading, and the test that names the
    build tools asserts `rolldown` too.
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
  - T6: read with `str.splitlines`, as the checker reads it, every line of the file that
    starts with `#` is either the title or an entry heading of the form in "What changes",
    and the set of backticked names across all headings is exactly the set of entries
    (F5).
- **`Dockerfile`**: `COPY LICENSE THIRD_PARTY_NOTICES.md THIRD_PARTY_LICENSES.md /app/`.
- **`container_tests/test_image_notices.py`**: the fixture copies the new file out too, and a
  new test compares it byte for byte with the repository's.
- **`tests/test_ci_changes.py`**: the new file joins `THIRD_PARTY_NOTICES.md` in the "is
  code" cases. A fence (P9: it is already code by rule), labelled as one.
- **`THIRD_PARTY_NOTICES.md`**: the "It is not a complete inventory" paragraph is replaced by
  one saying where every other package's notice is and how that file is made, and the
  opening paragraph's "both licences involved" and its last sentence change to match. The
  fonts and model entries do not change. The text is public and reads as a statement about
  what a copy carries, so it is written out below and approved as written (F9).

### The public text, as it will read

The opening paragraph's first sentence becomes "The Glosswork runtime image redistributes
third-party work, and the licences involved require their notices to travel with any copy."
Its last sentence becomes "Publishing an image is redistribution, so `LICENSE`, this file and
THIRD_PARTY_LICENSES.md are copied into it at `/app/`." The FSL-1.1-ALv2 and OFL-1.1
sentences between them do not change. (In the file, the licences file's name is
backticked; it is not here, because it does not exist on this branch yet.)

The section "What this file covers, and what it does not" keeps its first bold line and
replaces the "not a complete inventory" paragraph with these three:

> **Every package the image installs from `uv.lock` or bundles from `web/package-lock.json`
> has an entry in THIRD_PARTY_LICENSES.md**, beside this file: one entry per package and
> version, holding the licence and copyright text from the exact artifact the lockfile
> records, reproduced as published. Where a package's own text names no copyright holder,
> the entry names none. The file is generated from the two lockfiles by
> `scripts/third_party_licenses.py`, and a test fails when either lockfile holds a package
> it has no entry for, so it is not edited by hand. It lists every runtime package in the
> lockfiles, including three that install only on Windows or in a browser, and none of the
> development-only ones, which the image does not carry, apart from the three build tools
> whose code ends up in the web bundle.
>
> **The base image's own contents carry their own notices.** CPython, the Debian packages
> and the base image's `pip` keep theirs where the base image puts them.
>
> **Code compiled into a package from elsewhere is covered only as far as that package's
> own files cover it.** Six Python packages (`cryptography`, `hf-xet`, `pydantic-core`,
> `rpds-py`, `tokenizers` and `watchfiles`) are built from Rust and link crates their
> wheels carry no notice for, and `cryptography` also links OpenSSL. Their entries hold what
> the packages themselves publish. `numpy` and `onnxruntime` publish notices for what they
> link, and those are included.

The third paragraph stands: the maintainer kept F8 out of this change (see "Decided at
approval"). The three build tools are `vite`, `tailwindcss` and `rolldown` (F3).
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
- Development-only packages (pytest, ruff, mypy, and the npm dev tree apart from `vite`,
  `tailwindcss` and `rolldown`) get no entry. The image does not redistribute them. This reads the
  done-when's "every Python package in `uv.lock`" under its governing clause, "every work the
  runtime image redistributes"; see "Decided at approval".
- The release workflow and `tests/test_release_workflow.py` (P10).
- Anything in `src/` or `web/`.

## Constraints

- No new dependency. The generator uses `urllib`, `hashlib`, `zipfile`, `tarfile`, `tomllib`.
- Nothing is downloaded in CI or in any test: T1 to T6 read committed files only (T1
  through an offline `uv export`). `--check` needs the network and is an Accept criterion,
  not a guard. So CI proves that every package has an entry at the locked version; it does
  not prove an entry's text is what the artifact holds. That is proven by AC6 whenever the
  file is regenerated, and by the verifier (F12).
- The generator reads archive members in memory (`ZipFile.read`, `TarFile.extractfile`)
  and never extracts an archive to disk, so no member path is ever joined to a directory
  (F13).
- `uv.lock` and `web/package-lock.json` are not modified (AGENTS.md non-negotiable 1, and the
  `-diff` trap).
- Every commit is `Glosswork <hello@glosswork.dev>`, author and committer, with no trailer.
  Every ref pushed has `e5a047b` as its only root.
- Nothing is pushed to `1-release-images`.

## Checklist

Executed 2026-09-28, in order, in commits `285a8a9` (steps 1 and 2, the checker half) and
`030f87b` (steps 1 and 3 to 6).

1. [x] Write T1 to T6, the checker test changes (`npm_production_entries`, `--licenses` in
   every `main()` test, `rolldown` in the build tools and the all-covered fixture), the
   container test for the new file, and the `test_ci_changes.py` fence. Run them against
   this plan commit and record how each failed (T1 to T3, T5 and T6: the file and the
   overrides do not exist; T4: the function does not exist; the checker tests: no
   `--licenses` option, no `rolldown`).
   *Recorded.* Against `8340459`: `tests/test_third_party_licenses.py` failed at collection,
   `FileNotFoundError` for `scripts/third_party_licenses.py`, so T1 to T6 all failed. With
   the generator present but the file and the overrides absent: T1, T2, T3 and T6 errored
   (`FileNotFoundError` for THIRD_PARTY_LICENSES.md) and T5 failed (no overrides file);
   T4 and T5's synthetic refusal test passed, as synthetic tests should. The checker
   tests: 5 failed, `AttributeError: ... no attribute 'npm_production_entries'`, `rolldown`
   not in the real lockfile's names, and the three `main()` tests exiting 2 on the unknown
   `--licenses`. The `test_ci_changes.py` case passed, as a fence does. The
   container test was not run before the fix: the image had no such file to copy.
2. [x] Refactor `npm_production_names` onto `npm_production_entries`; add `--licenses`; add
   `rolldown` to `BUNDLED_BUILD_TOOLS`. The checker tests pass.
   *Recorded.* 12 passed. One existing test changed beyond the plan's list (Deviation D1).
3. [x] Write `scripts/third_party_licenses.py` and `scripts/third_party_licenses.toml` with
   the two overrides.
   *Recorded.* The override digests re-fetched 2026-09-28 match P4's prefixes in full.
4. [x] Generate THIRD_PARTY_LICENSES.md. Record here: entries per ecosystem, the declared
   licences by count, which entries took files from outside `.dist-info/`, an sdist or an
   override, how many texts were line-ending normalised, and the file's size.
   *Recorded.* `uv run python scripts/third_party_licenses.py`, exit 0, 25 seconds with an
   empty cache. 168 entries: Python 60, npm 108. 206 licence texts: Python 97, npm 109.
   - Declared licences, npm: MIT 104, OFL-1.1 3, ISC 1. Python: MIT 25, BSD-3-Clause 14,
     Apache-2.0 4, "MIT License" 2, and one each of MPL-2.0, MIT-0, "BSD License",
     "Apache-2.0 OR BSD-3-Clause", "Apache 2.0", "MIT AND PSF-2.0", "BSD-3-Clause AND 0BSD
     AND MIT AND Zlib AND CC0-1.0", "Apache-2.0 OR BSD-2-Clause", "3-Clause BSD License",
     PSF, "MIT License, Apache License, Version 2.0", "MIT OR Apache-2.0", "Apache Software
     License", "MPL-2.0 AND MIT" and PSF-2.0, as each package's metadata states them.
   - Outside `.dist-info/`: `onnxruntime` (`onnxruntime/LICENSE`,
     `onnxruntime/ThirdPartyNotices.txt`). From an sdist: `tokenizers`
     (`tokenizers-0.23.1/tokenizers/LICENSE`, the sdist's only licence file). From an
     override: `flatbuffers` (1 file), `sqlite-vec` (2 files).
   - Normalised: 19 of 206 texts, 8 for carriage returns and 11 for their final newline
     only (Deviation D3 on P15's count).
   - Size: 1,021,369 bytes (Deviation D4 on P7).
5. [x] Dockerfile `COPY`; build; run the checker and the container notices tests.
   *Recorded.* See AC7 and AC8.
6. [x] Docs: `THIRD_PARTY_NOTICES.md` as written in "The public text, as it will read",
   `README.md`, `AGENTS.md`, `CONTRIBUTING.md`. Any backticked or linked mention of the new
   file lands in the commit that creates it, or later (`docs/changes/README.md`, "Two
   tests guard a citation").
   *Recorded.* The public text went in as approved, with the licences file's name
   backticked in all three places it appears, as the plan's note says. Every backticked or
   linked mention is in `030f87b`, the commit that creates the file; `285a8a9`'s checker
   docstring names it in plain text, and the structural lane passed at `285a8a9` (88
   passed).
7. [x] Run the Accept block and record its output here.
   *Recorded* under "Accept output".

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
- **AC12.** The texts F1 to F3 found are in the file. Each of these prints a count of at
  least 1: `grep -c 'THIRD PARTY SOFTWARE NOTICES AND INFORMATION' THIRD_PARTY_LICENSES.md`
  (onnxruntime's notices), `grep -c 'Copyright (c) 2020 Evan Wallace' THIRD_PARTY_LICENSES.md`
  (esbuild's notice through rolldown), and `grep -c '^## .rolldown. ' THIRD_PARTY_LICENSES.md`.

### Accept output

Run 2026-09-28 by the building session at `030f87b`, on a clean tree, arm64 macOS with
Docker 28.4.0. This is the builder's record; the verifier runs the block again.

| | Result |
| --- | --- |
| AC1 | exit 0, 64 passed |
| AC2 | exit 0, 96 passed, 1955 deselected |
| AC3 | exit 0, 2048 passed, 3 xfailed |
| AC4 | exit 0, "All checks passed!" |
| AC5 | exit 0, "250 files already formatted" |
| AC6 | exit 0, with an empty download cache |
| AC7 | build exit 0; the check exit 0: python 57 of 57 covered, npm 107 of 107, 0 not covered for both. The image's revision label is `030f87b...` |
| AC8 | exit 0, 7 passed, 1 skipped (the revision test skips by design when `GW_IMAGE` is set) |
| AC9 | `react` entry deleted: exit 1, T1 names "npm `react` 19.2.8". `scheduler` bumped from 0.27.0 to 0.99.0 in the lockfile copy: exit 1, T1 names "npm `scheduler` 0.99.0" and T2 names "npm `scheduler` 0.27.0" |
| AC10 | prints 0, exit 1, the expected answer |
| AC11 | root `e5a047b` only; `Glosswork <hello@glosswork.dev> Glosswork <hello@glosswork.dev>` only |
| AC12 | 1, 1, 1 |

Also run: gitleaks 8.30.1 as CI's `secrets` job runs it (`git . --config .gitleaks.toml
--log-opts=HEAD`), 9 commits, "no leaks found", exit 0.

## Adversarial pass

Run 2026-09-28 by a session that did not write this plan. Every finding below was
established by running something against the lock-recorded artifacts, an image built from
`bdb8172`, or `web/node_modules` after `npm ci`; the premises it touched say so in place.

- **F1. The generator as written cannot finish: `onnxruntime` has no licence file under
  `.dist-info/` and no sdist.** Rule 1 read only `.dist-info/`, then the sdist, then an
  override. `onnxruntime` 1.29.0 ships `onnxruntime/LICENSE` and
  `onnxruntime/ThirdPartyNotices.txt` inside the package and nothing in `.dist-info/`, and
  the lock records no sdist for it, so the generator would exit 1 on it, or an implementer
  would improvise a rule. P3 counted it as covered because its measurement looked anywhere
  in the wheel. ThirdPartyNotices.txt is also the only notice for the libraries onnxruntime
  compiles in, and its name does not match the checker's `LICENCE_FILE` pattern.
  *Fixed:* a licence-file definition that includes third-party notice names, and a
  fallback to licence files anywhere in the wheel when `.dist-info/` has none. P3
  corrected; both files measured identical on x86_64 and aarch64. AC12 checks it landed.
- **F2. Declared licence files that are not named LICENSE were dropped.** `PyJWT` and
  `sse-starlette` declare `AUTHORS` files as licence files, and PEP 639 installs them under
  `.dist-info/licenses/`; a rule matching only licence-like names skips them, and for
  "Copyright (c) the authors" style notices the list of authors is the holder. *Fixed:*
  everything under `.dist-info/licenses/` is taken.
- **F3. The web bundle carries a third build tool's code, and its notice is in a file the
  plan's npm rule skipped.** P13: the shipped JavaScript opens with rolldown's CommonJS
  interop helpers, which rolldown takes from esbuild. `rolldown` is dev-only in the lock
  and is not in change 1's `BUNDLED_BUILD_TOOLS`, so neither the checker nor this plan
  counted it. Its esbuild and rollup notices are in `THIRD-PARTY-LICENSE`, not `LICENSE`.
  Change 1's F3 measured by grepping for Vite and Tailwind strings; minified helper names
  are mangled, so a grep for helper names finds nothing and reading the bundle's first
  bytes is what shows it. *Fixed:* `rolldown` joins `BUNDLED_BUILD_TOOLS` in this change's
  checker edit, the npm rule takes `THIRD-PARTY-*` files, counts become 107 names and 108
  entries, AC12 checks the esbuild notice. *Not proven:* that no other development
  package puts code in the bundle. This pass found rolldown by reading the bundle's first
  bytes and did not survey the rest.
- **F4. The new `--licenses` default breaks an existing checker test.**
  `test_main_exits_1_and_names_what_is_uncovered` passes `--notices` only and expects `ms`
  to be uncovered. `ms` is in the real production set (measured), so once the real file
  exists the default makes it covered and the test fails from checklist step 4; the
  all-covered test would pass partly on the real file. The plan called these tests a fence
  of unchanged behaviour. *Fixed:* every `main()` test passes `--licenses` to a temporary
  file, and the fixture and build-tool test gain `rolldown`.
- **F5. Licence text can defeat the file's own structure.** P15: 14 texts carry carriage
  returns. With `* text=auto eol=lf`, git stores them as LF, so `--check` passes on the
  machine that generated the file and fails on any fresh checkout, including the
  verifier's. Separately, the checker finds headings with `str.splitlines`, which also
  breaks on form feeds and other separators, so such a character in a licence would start
  an unindented line that could read as a heading; and a backticked word in any heading,
  such as a licence file's path, is counted as a package. *Fixed:* texts are normalised to
  LF with one final newline, the other separators make the generator exit 1 (none occurs
  today), licence paths are not in headings, and T6 checks the file's headings the way the
  checker reads them.
- **F6. Two definitions of the runtime Python set.** The generator was "standard library
  only, reads the lockfiles", while T1 used `uv export`; resolving 60 runtime packages from
  `uv.lock` by hand means walking extras and markers. *Fixed:* one function in the
  generator runs the offline `uv export`, and T1 imports it.
- **F7. P8 measured two of the four scans that read every tracked file.**
  `INTERNAL_DECISION` (which matches the old decision prefix even as a bare two-letter
  word) and `DD_CITATION` also run over
  the new file. *Fixed in the premise:* measured, no match, and gitleaks with the
  repository's configuration finds nothing. *Accepted:* a future licence text that trips a
  scan fails the guards job naming THIRD_PARTY_LICENSES.md, which is the right failure; the
  file is not exempted.
- **F8. Code compiled into six packages from elsewhere has no notice, in the image or in
  this plan.** P14: `cryptography`, `hf-xet`, `pydantic-core`, `rpds-py`, `tokenizers` and
  `watchfiles` statically link Rust crates (at least 166 found in the binaries; 782
  registry entries in their sdists' `Cargo.lock` files, some for other platforms), and
  `cryptography` links OpenSSL 3. Their wheels carry only the package's own licence. The
  task's done-when enumerates lockfile packages, and the checker counts packages, so this
  plan meets both; its governing clause, "every work the runtime image redistributes",
  reaches further, which is the same argument that created this task from change 035.
  *Disposition: kept out by the maintainer at approval, 2026-09-28, with no follow-up*
  (see "Decided at approval"). The public text says so.
- **F9. The public text was not written.** The plan said the scope paragraph would be
  "replaced by one saying where every other package's notice is". That paragraph is
  published in the image and on GitHub and reads as a statement about what a copy
  carries, and the opening paragraph's "both licences involved" also becomes untrue.
  *Fixed:* the replacement text is written out in "What changes", for approval as written.
- **F10. P7's size was half the real one.** About 900 KB, not 450 KB, most of it two
  bundled-notices files (onnxruntime's, Vite's). *Fixed in the premise.* It changes
  nothing else.
- **F11. The base-image exclusion was asserted, not measured.** *Measured (P12)*: CPython,
  all 87 Debian packages and `pip` carry their notices in the image. It holds.
- **F12. CI cannot tell a hand-edited entry from a generated one.** Only `--check` compares
  text with the artifacts, and it needs the network. *Accepted, and stated in
  "Constraints":* coverage and versions are guarded offline; fidelity is proven at each
  regeneration and by the verifier. The file's opening paragraph says it is generated.
  Running `--check` in the release workflow would close the gap, at the cost of a release
  depending on PyPI, npm and GitHub being reachable and about 100 MB of downloads; not done
  here.
- **F13. Archive extraction.** A generator that extracts wheels, sdists or tarballs to disk
  joins member paths to a directory. *Fixed in "Constraints":* members are read in memory.
- **F14. The plan commit itself fails the structural lane.** P8 wrote an example of a
  markdown link to a `.md` target inside backticks, and `test_every_referenced_document_exists`
  matches links inside backticks too: `uv run pytest -q tests/test_documentation_structure.py`
  at `ab9bb18` exits 1 with "link to ...md". Run 1 ran the structural lane on the plan's
  parent, not on the plan. *Fixed:* the example is prose. This pass's own first draft
  tripped `test_no_internal_decision_number_is_cited` the same way, by quoting the pattern
  it describes, which is F7's point made twice.

Checked and found sound: P2 (60 runtime packages from an offline `uv export`, exit 0);
P5 extended to onnxruntime's and numpy's out-of-`.dist-info` files; the override URLs for
`flatbuffers` and `sqlite-vec` (HTTP 200) and the absence of an upstream NOTICE file for
either (HTTP 404), so Apache-2.0's NOTICE clause adds nothing for them; every one of the
107 npm names has a licence file at its package root.

## Decided at approval

The maintainer approved this plan as written at `8340459` on 2026-09-28. The approval
covers the public text in "The public text, as it will read", the entry format, the rule
that no copyright holder is invented, and "every Python package in `uv.lock`" read as the
60 runtime packages. **F8 was kept out**: the Rust crates and OpenSSL compiled into six
Python packages are not listed, the public text says so, and there is no follow-up.
Merging is not approved yet. Issue #3 was filed on 2026-09-28 and took the number the plan
assumed, so nothing was renamed.

## Deviations from the approved plan

Recorded during execution, 2026-09-28. None changes the approved public text, the entry
format's substance or the scope.

- **D1. One more existing checker test changed.**
  `test_npm_production_names_leave_out_dev_packages_and_the_root` expected the names as
  three production names followed by the sorted build tools. With `rolldown` added,
  `rolldown` sorts before `scheduler`, so the expectation became the sorted union, and it
  now also asserts the `devOptional` package is left out. Same behaviour, correct order.
- **D2. The checker's covered-by-heading reason reads "a heading in the notices"** for
  either file, where it named THIRD_PARTY_NOTICES.md. No test pins the wording.
- **D3. P15 counted 14 texts with carriage returns; the generated set has 8.** All are
  `pywin32`'s, from the wheel the selection rule picks for it (no pure or manylinux wheel,
  so the first listed). Why the counts differ is not established: P15 does not record
  which `pywin32` wheel it read. The rule normalises whatever it finds, and `--check`
  passes on a fresh regeneration (AC6).
- **D4. The file is 1.02 MB, not about 900 KB.** P7 measured the licence texts alone; the
  file adds the four-space indent on every line, the headings and the source lines.
- **D5. Each override carries an `ecosystem` key** beside `package` and `version`, so an
  override names its package unambiguously. T5 has a second, synthetic test that an
  override for a version the lockfiles no longer hold is refused, naming it.
- **D6. The entry's source line reads `Ecosystem: <python|npm>. Declared licence: <as
  declared>. Source: <artifact URL>`.** For an override the source is "upstream, pinned in
  scripts/third_party_licenses.toml", and each file line carries the upstream URL. The
  declared licence is the metadata's `License-Expression`, else a one-line `License`,
  else its licence classifiers, else "not declared"; for npm, the lockfile's `license`.
- **D7. The container test module's docstrings say three files where they said two.**

## Durable content moved out of this plan

Not yet.
