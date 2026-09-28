# Contributing

This project is maintained through GitHub issues and pull requests. The workflow below is
short, but it is not ceremony: every step in it exists because skipping it cost real time at
least once.

Read [AGENTS.md](AGENTS.md) first. It carries the commands, the non-negotiables, and the
architecture invariants. This file is only about process.

**Outside pull requests are not accepted yet.** There is no contributor agreement in place, and
until there is, the copyright has to stay in one place so the licence stays changeable. Issues
and bug reports are very welcome now. If you want to contribute code, open an issue first and
say so, and we will tell you when the agreement exists.

## Roles

**Maintainer** means whoever is listed in [CODEOWNERS](CODEOWNERS) for the paths a change
touches. Where older documents say "product owner", they mean the maintainer: the person who
approves a plan before it is executed and merges the pull request afterward.

## One change at a time

1. **Open an issue.** It carries the *why*: the problem, and the evidence that it is real. Use
   the `Change` or `Bug` template. Label it. An observation you are not ready to act on uses the
   `Backlog` template, which applies the `backlog` label, and stays there; a labeled backlog
   issue is a record, not a commitment.

2. **Branch from `main` and write the plan as a file**, at `docs/changes/<N>-<slug>.md`, where
   `<N>` is the change's number: its GitHub issue number, written as it is, with no padding.
   Commit the plan **alone**, as `N: plan`.
   `docs/changes/README.md` holds the template and the rules for writing one. The plan is the
   thing that gets approved, so it must be concrete enough to argue with. It carries, in this
   order: why, the premises and **how each was established**, what changes, what deliberately
   does not change, the invariants the execution must not violate, an ordered checklist, and an
   **Accept** block anchored to commands rather than to judgment.

3. **Everything from here to step 7 happens locally.** Do not open a pull request yet. A branch
   is pushed once, when the change is finished and verified. Pushing for backup is free (a
   branch with no pull request runs no CI), but the pull request itself waits.

4. **Run an adversarial pass over the plan before executing any of it.** Try to falsify your own
   premises. Every adversarial pass in this project has found real defects, and the premises that
   look most solid are the ones written from structural measurements rather than from actually
   reading the thing they describe. Findings go into the plan's own "Adversarial pass" section
   as `F1..Fn`, each with a disposition, and the fixes are folded into the plan text in place.

5. **Get the plan approved as written, in the working session.** The maintainer is usually
   present while the plan is written, and that is where it is approved. When they are not, send
   them the file. Amendments are folded into the plan by editing the affected text in place, not
   appended as a log: the file should always read as the current plan, and git holds what
   changed.

6. **Execute in checklist order.** A deviation is recorded in the plan's "Deviations" section as
   it happens, not reconstructed afterward and not silently absorbed.

7. **Verify independently, locally.** Run the Accept block as written, not the checklist item
   you remember executing. Keep the output. Do not assert completion from inspection. The
   `verify` agent exists for this and has no write tools on purpose.

8. **Close it out in two commits.** First commit the plan's own final edits: the ticked
   checklist, the deviations, the Accept output. Then, in a second commit, move what stays true
   into `PRD.md` (requirements), `docs/DESIGN_DECISIONS.md` (design decisions),
   `docs/DATA_MODEL.md`, `docs/MCP_TOOLS.md`, `docs/DEPLOYMENT.md`, `docs/PERFORMANCE.md` or
   `docs/AGENT_ONBOARDING.md`, and delete the plan file, keeping a copy of its final text. A
   work order has a limited life, and doing that migration is part of the change, not a
   follow-up.

   The order is the point. A commit that edits a file and deletes it records only the deletion,
   so a one-commit closeout leaves the plan's final state in no commit: git keeps the version as
   it stood before the closeout ran. Merge with a merge commit and not a squash, for the reason in
   `docs/changes/README.md` step 10.

9. **Push once, and open the pull request once**, not as a draft, using the pull request
   template. Its description carries the plan's final text (a file added and deleted on one
   branch never appears in the pull request's diff), the Accept output, every deviation, and
   the closeout. One push, one pull request, one CI run. Then, and only then, the maintainer
   merges it.

## Branches and commits

- Branch from `main`, named for the change: `123-short-kebab-description`, where `123` is the
  change's number (step 2).
- Commit messages start with the change's number: `123: the display field is chosen, not
  guessed`. Write the subject as what is now true, not as what you did. A pull request's title
  starts the same way.
- **Every commit is `Glosswork <hello@glosswork.dev>`**, author and committer. Set it in your
  clone's local git config. The `secrets` job fails a pull request carrying any other address.
- `main` is protected by a ruleset (see "The repository's GitHub settings"). Everything lands
  by pull request, with a merge commit. The maintainer merges in GitHub's web page with "Create
  a merge commit", choosing `hello@glosswork.dev` as the commit email. GitHub records itself,
  `noreply@github.com`, as that commit's committer, which the check allows.
- **Never press "Update branch".** `main` requires a branch to be up to date before it merges,
  and GitHub offers that button whenever `main` has moved. It writes a merge commit onto the
  branch under an address GitHub picks, with no email dialog, and once that commit is in the
  branch the only way out is to rewrite the branch without it. Bring a branch up to date
  locally instead: `git fetch origin`, `git merge origin/main` as `hello@glosswork.dev`, check
  that `git rev-list --max-parents=0 HEAD` prints exactly one commit, and push.
- **Every push is of a ref with exactly one root commit**: `git rev-list --max-parents=0 <ref>`
  prints one line. A branch that merged unrelated history still descends from `main`, so
  descent is not the test. CI fails a history with more than one root.

## What a pull request must carry

- The plan's final text, with its checklist ticked. The file itself is gone by then, and a
  file added and deleted on one branch never appears in the pull request's diff.
- The Accept block, with the output of the commands that prove it.
- A note of every deviation from the approved plan, and why each was right.
- Green CI: the `ci-ok` check, and the three checks that always run. "What CI runs" below says
  what that covers.
- For UI work: confirmation that the Playwright **visual** project passed locally, and the count
  of baselines repainted. Visual baselines are macOS-only and do not run in CI, so this is the
  only place that check happens.

## What CI runs

One workflow, `.github/workflows/ci.yml`, runs on every pull request, on every push to `main`,
and by hand. A push to any other branch runs nothing. It runs only what a change can affect.
The only other workflow, `.github/workflows/release.yml`, runs on a version tag and nothing
else ("Releases" below).

**A change is documentation-only when every path it adds, changes, deletes or renames** (both
sides of a rename) is one of these, and it has at least one path:

- `docs/**`
- `README.md`
- `PRD.md`
- `AGENTS.md`
- `CONTRIBUTING.md`
- `SECURITY.md`
- `CODEOWNERS`
- `.claude/agents/*.md`
- `.github/ISSUE_TEMPLATE/**`
- `.github/pull_request_template.md`

`docs/**` is anything under `docs/`; `*` is one path segment; every other entry is one exact
path from the repository root. Everything else is code, deliberately including `LICENSE` and
`THIRD_PARTY_NOTICES.md` (the image ships them), markdown anywhere else, `perf-data/`, and the
workflow itself. An empty change counts as code. The same list is a tuple in
`scripts/ci_changes.py`, and `tests/test_ci_changes.py` fails if the two differ.

| Job | Runs | What it does |
| --- | --- | --- |
| `changes` | always | Classifies the change: `code=true` or `code=false` |
| `guards` | always | `uv run pytest -q -m structural`, then `actionlint` over the workflow |
| `secrets` | always | gitleaks over the history under test with `.gitleaks.toml`; every commit is `hello@glosswork.dev`; the history has one root commit |
| `sast` | always | Semgrep with `p/python`, `p/javascript` and `p/typescript` |
| `backend-lint` | code only | `ruff check .`, `ruff format --check .`, `mypy src` |
| `frontend-lint` | code only | `npm --prefix web run lint`, `typecheck` |
| `backend-test` | code only | the whole backend suite, with the real embedding model |
| `frontend-test` | code only | `npm --prefix web run test` |
| `e2e` | code only | the Playwright functional project |
| `image` | code only | builds the image; never publishes it |
| `ci-ok` | always | Fails unless every job ended the way the classification allows |

**A documentation-only change still runs `changes`, `guards`, `secrets`, `sast` and `ci-ok`.**
`guards` is the whole structural lane, which is every test that reads documentation:
`tests/test_structural_lane.py` fails if a test that builds a path to a document, or searches
the whole tree, is not in it. A push to `main` that is one merge commit on top of the previous
`main` is classified the same way, because its tree is the tree the pull request's run tested.

**Why the skip is per job.** GitHub leaves a required check from a workflow skipped by a path
filter "Pending" forever, which blocks the merge, and reports a job skipped by `if` as
"Success". So there is no path filter on the workflow, no heavy job is required on its own,
and `ci-ok` fails if a heavy job ran on a documentation change, was skipped on a code change,
or was skipped because `changes` failed. `tests/test_ci_workflow.py` pins that shape.

**One Semgrep rule is excluded, over one file.** `avoid-sqlalchemy-text` flags every `text()`
in `src/glosswork/repositories/sqlite.py`, the storage module, where raw SQL is meant to live
(AGENTS.md, non-negotiable 4) and every query binds its parameters. It is excluded there only:
everywhere else it is the one rule that catches SQL built by string formatting. A new
formatted query inside that module is therefore not scanned, and review is what catches it.
The rule packs are fetched from semgrep.dev at run time and are not versioned, so a new rule
can turn `sast` red on an unrelated pull request. That pull request triages it: fix the code,
or exclude the rule here with a reason, never by weakening `ci-ok`.

**The files that define CI can weaken the checks that judge them.** A pull request runs the
workflow, the classifier, the secret-scan rules and the tests as that pull request has them.
These files define CI: `.github/`, `scripts/ci_changes.py`, `.gitleaks.toml`,
`scripts/github/`, `scripts/notices_coverage.py`, `tests/test_ci_changes.py`,
`tests/test_ci_workflow.py`, `tests/test_release_workflow.py` and
`tests/test_structural_lane.py`. A change that touches one says so in its plan's "What
changes", so the diff is read for it.

## The repository's GitHub settings

Every GitHub setting this repository relies on is in `scripts/github/configure.sh`, applied
from literal values in the script and from `scripts/github/ruleset-main.json`: merge commits
only, branches deleted on merge, Actions limited to GitHub's own actions and
`astral-sh/setup-uv` pinned by SHA, a read-only workflow token, the `backlog` label, the
`release` environment that only `v*` tags may enter (see "Releases"), and the ruleset on
`main`. The ruleset requires a pull request, `ci-ok` and the three always-run
checks from GitHub Actions, and the branch up to date before merging; it allows merge commits
only, blocks force pushes and deletion, and has no bypass list, so it binds administrators.
It requires no approval, because every pull request is opened from the maintainer's own
account and one cannot approve one's own.

No setting is changed by hand. Change the script or the ruleset file in a pull request, then
the maintainer runs it with their own `gh` login, since it needs the Administration
permission:

```
env -u GH_TOKEN -u GITHUB_TOKEN scripts/github/configure.sh apply
env -u GH_TOKEN -u GITHUB_TOKEN scripts/github/configure.sh check
```

`check` exits 1 on any difference. A fresh repository gets identical settings by running
`apply` against it.

## Releases

**A version is `X.Y.Z`, following [Semantic Versioning](https://semver.org/).** While the
major version is 0, a change an operator has to act on when upgrading (a setting renamed
or removed, a migration that cannot be rolled back by restoring a backup, a changed API or
MCP contract) raises `Y`; everything else raises `Z`. The version lives in one place,
`pyproject.toml`, and the release tag is that version with a `v` in front.

**A release is a change like any other**, numbered, planned and merged by pull request. It
sets the new version in `pyproject.toml`, updates the `glosswork` entry in `uv.lock` to
match, and adds the version's entry to [CHANGELOG.md](CHANGELOG.md). Re-locking on a machine
with a private `uv` index rewrites every registry line (AGENTS.md, non-negotiable 1), so
after `uv lock` run the substitution given there and confirm `git diff --text uv.lock`
changes only the `glosswork` version line.

**The changelog is written once per release, not once per change.** `CHANGELOG.md` holds
one section per released version, newest first, headed `## X.Y.Z`; the tag records the
date. Each line is one change merged since the previous version, by its number, written as
what is now true, as its commit subjects are. Anything an operator must do when upgrading
comes first, under **Upgrading**. Ordinary changes do not touch the file, so it never
carries an "unreleased" section.

**Before tagging, the release can be rehearsed.** Running `release.yml` by hand
(`gh workflow run release.yml --ref main`) is a dry run: it builds and tests both
architectures on their real runners and runs the notices check, then stops. Nothing signs
in, pushes or publishes, and the Docker Hub credential is never in reach.

**After the release change merges, the maintainer tags its merge commit** and pushes the
tag, from a clone whose `user.email` is `hello@glosswork.dev`, because an annotated tag
records its tagger:

```
git fetch origin
git tag -a vX.Y.Z -m "X.Y.Z" <merge commit>
git push origin vX.Y.Z
```

The tag starts `.github/workflows/release.yml`, which publishes nothing unless:

- the tag is `vX.Y.Z` and `X.Y.Z` is `pyproject.toml`'s version;
- the tagged commit is on `main`, and CI passed on `main` for that commit;
- each architecture's image, built on its own native runner, passes `container_tests`;
- `scripts/notices_coverage.py` finds, inside the image, the licence text of every
  third-party package the project put there (from `uv.lock` and `web/package-lock.json`;
  the base image's own contents are not counted);
- the version does not exist yet in either registry, other than as exactly this build.

Then it publishes one tag, `X.Y.Z`, covering `linux/amd64` and `linux/arm64`, to
`ghcr.io/glosswork/glosswork` and `docker.io/glosswork/glosswork`, and reads both back.
There is no `latest` tag and no moving `X.Y` tag: a deployment names the version it runs.
**A published version is never replaced.** A bad release is fixed by the next patch
version; the workflow refuses to overwrite a version that exists. A run that failed part
way can be re-run: it skips a registry that already serves exactly this build.

**Where the credentials live.** The GitHub registry needs no stored credential: the
workflow's own token pushes there. The Docker Hub credential is the `DOCKERHUB_TOKEN`
secret of the repository's `release` environment, with the account it belongs to in that
environment's `DOCKERHUB_USERNAME` variable. `scripts/github/configure.sh` lets only runs
for tags matching `v*` enter that environment, and only the `publish` job, which runs no
code from the repository, names it. So no pull request, branch or dry run can read the
token. **Anyone who can push a `v*` tag can**, because a tag runs the workflow as the
tagged commit has it: pushing a release tag is the maintainer's act alone.

**The first release needs, in this order:** `configure.sh apply` (a job that names an
environment that does not exist makes GitHub create it with no tag restriction); the
`DOCKERHUB_USERNAME` variable and `DOCKERHUB_TOKEN` secret on the `release` environment;
the Docker Hub repository `glosswork/glosswork` created as public, since a first push
would otherwise create it with the organization's default privacy; the organization's
package settings allowing public packages; a dry run; the tag; and, after the run, the
GitHub package `glosswork` made public in its settings, because a package pushed by a
workflow starts private.

## Design decisions

`docs/DESIGN_DECISIONS.md` holds the key technical decisions, each as the rule that holds today
and its reason. A change that establishes a new rule adds an entry with the next unused number; a
change that alters a rule edits its entry in place, as the current rule with its reason, in the
same closeout commit that moves the change's other durable content. An entry carries no dated
amendment: git holds its history. A decision that stops being true is removed, and its number is
never given to another decision, so the numbers are unique and ascending but may have gaps.

Do not amend a decision silently by changing the code it governs. If the code and a decision
disagree, one of them is a bug, and which one is a question for the maintainer.

## Tests

Every behavior change ships with a test. Beyond that:

- **Every assertion must be able to fail.** Run a new assertion against the unfixed tree and
  watch it fail before you fix anything. An assertion guarding behavior the change deliberately
  does not alter cannot fail there by construction: label it a fence and do not count it toward
  the change's coverage.
- **Retire superseded assertions as part of the change.** Keeping a test that asserts the shape
  the change decided against asserts two contradictory outcomes at once.
- **Meta-tests are load-bearing.** Several invariants in this codebase are enforced by tests
  that fail if a second implementation of something appears (see AGENTS.md, "Architecture
  invariants"). If one of those fails, the default assumption is that the change is wrong, not
  the test.

## Migrations

**Existing migrations are never edited** (DD-6). The runner recognizes an applied migration by
its number alone, so a deployment that already ran a migration never receives an edit to it, and
its schema silently parts from a fresh install's.

- **To add a migration,** append a `Migration` to `MIGRATIONS` in `src/glosswork/migrations.py`,
  run `uv run python -m tests.test_migrations_are_forward_only >> tests/migration_hashes.txt`,
  and commit both together. The command prints a line only for a migration the manifest does not
  list yet, so it can only append.
- **A mistake in a merged migration is corrected by a new migration**, never by editing the old
  one, and that includes whitespace and SQL comments inside a statement.
- **When the guard fails on an existing migration, revert the edit.** Never regenerate or change
  the manifest to turn it green: no existing digest line of `tests/migration_hashes.txt` ever
  changes.

## Working with AI coding agents

Agents are a routine part of this workflow, and the discipline above is what makes that safe.
`docs/changes/README.md` carries the full division of labor; the rules that matter most:

- **Name the exact document and section an agent must read.** Never ask an agent to figure out
  the design. If it needs a design decision, it should stop and report rather than choose.
- **Delegate work that is already specified; keep work that requires judgment.** Route handlers,
  CRUD endpoints, tests written from stated acceptance criteria, CSV logic, React components and
  config plumbing delegate well. The filter-AST compiler, the schema-proposal and coercion
  engine, indexing strategy, audit design, MCP tool and error copy, hybrid search ranking, and
  any change to the storage abstraction or to a decision in `docs/DESIGN_DECISIONS.md` do not.
- **The plan is written by one session and executed by another.** An adversarial pass is worth
  most when it is read by something that did not write the premises.
- **Hand `impl` one goal at a time**, naming the plan file, the sections to read, what must not
  change, the command that proves it done, and the instruction to stop and report rather than
  guess when the specification is ambiguous.
- **`verify` runs the Accept block and reports `PASS` / `FAIL` / `NOT PROVEN`.** It has no write
  tools, deliberately. `NOT PROVEN` means no test exists at all, and it is a distinct and
  important outcome from `FAIL`.

Agent-authored changes go through the same issue, plan, approval, and pull request path as any
other. There is no fast lane.

## Reporting a security issue

See [SECURITY.md](SECURITY.md). Do not open a public issue for a vulnerability.
