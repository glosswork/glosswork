# Change plans

One file per in-flight change, at `docs/changes/<N>-<slug>.md`, where `N` is the change's
number and `slug` matches the branch name. A change's number is its GitHub issue number, written
as it is, with no padding. **This README is the only permanent resident of this directory.** A
plan file lives on its own branch, for the life of that branch, and is deleted in the change's
final commit.

The issue carries the *why*. This file carries the *how*, and it is the thing that gets
approved before any code is written.

## Why a file and not a pull request description

- **It is a diff.** The adversarial pass edits premise 4 in place and `git` holds what changed,
  which a text field in a web form does not.
- **Agents can read it.** `impl` and `verify` run against the working tree. A plan they cannot
  open is a plan they will guess at.
- **It survives the session.** A session that ends mid-change hands over a file, not a memory.
- **It dies on purpose.** A work order has a limited life. Deleting it at merge is what stops
  `docs/changes/` from becoming a second, stale specification competing with `PRD.md`,
  `docs/DESIGN_DECISIONS.md` and the rest of the specification set.

## Lifecycle

**Steps 2 through 8 happen on your machine.** The branch reaches the remote once, when the
change is finished and verified, and the pull request is opened once, already ready. Designing
a change, attacking it and revising it are a local loop; GitHub is where a finished change goes.

Pushing is not the expensive part: a push to a branch with **no open pull request** runs no CI
at all, because `.github/workflows/ci.yml` runs on pull requests and on `main` only, so pushing
for backup is fine. Opening the pull request early is what costs, in CI runs and in round trips.

1. **Issue filed** on GitHub with the `Change` or `Bug` template. It carries the problem, the
   evidence that it is real, what good looks like, and the specifications it touches by section.
   Issues are cheap and run nothing.
2. **Branch from `main`**, named `N-slug` for the change's number.
3. **Write `docs/changes/<N>-<slug>.md`** from the template below and commit it **alone**, as
   `N: plan`.
4. **Run the adversarial pass** against the plan, before any implementation. Findings go into
   the plan's own "Adversarial pass" section as `F1..Fn`, each with a disposition. Fixes are
   folded into the plan text in place; git holds what changed.
5. **Get the plan approved as written**, in the working session. The maintainer is usually
   present while the plan is written, and that is where it is approved. When they are not, send
   them the file. A pushed branch is not a substitute for someone reading the plan.
6. **Execute in checklist order.** Every deviation is recorded in the plan's "Deviations"
   section as it happens, not reconstructed afterward.
7. **Verify** with the `verify` agent against the Accept block. It runs locally, which is what
   anchoring the block to commands is for. Keep the output: it goes in the pull request.
8. **Close it out in two commits**, in this order: first the plan's own final edits, then the
   commit that moves durable content to the specifications and deletes the plan file. **Keep a
   copy of its final text.** A file added and deleted on one branch never appears in the pull
   request's diff, so the description is the only place the plan reaches GitHub.

   Two commits rather than one because git keeps only what a commit records. A closeout that
   deletes its plan in a commit that also changes other files leaves the closeout's own edits to
   the plan -- the ticked checklist, the deviations, the final Accept output -- in no commit at
   all: git preserves the version as it stood *before* the
   closeout, and the approved final text survives only in a pull request description. The
   first commit is what makes the plan's last state readable from `main` afterward.
9. **Push, once. Open the pull request, once, and not as a draft**, using the pull request
   template: the plan's final text, the Accept output, the deviations and the closeout.
   One push, one pull request, one CI run.
10. **Then** the maintainer merges it, in GitHub's web page, once `ci-ok` is green. The
    closeout is part of the change, so it belongs in the change, not after it: a merge before
    the closeout carries the plan file onto `main`.

    **A change whose closeout deletes a plan is merged with a merge commit, never squashed.** A
    squash replaces the branch's commits with one, so the plan's final text ends up in no commit
    reachable from `main`, and deleting the branch on merge then makes the commits that held it
    unreachable. Step 8's two commits are defeated entirely by a squash, with every check green
    at each step. The ruleset on `main` allows merge commits only
    (`scripts/github/ruleset-main.json`), and the repository allows no other merge method, so a
    squash is not offered.

**What local-first costs, stated plainly.** A draft pull request had one real benefit that is
not the CI run: opening early forced the plan to be legible to somebody who had not written
it. Locally, one session can write a plan, attack its own premises and implement it with nobody
else reading anything, and a pass on premises you wrote yourself is the weak version of the
exercise. Step 5 is the mitigation, and so is the rule below that the session which plans is not
the session which implements. Neither is as strong as a second pair of eyes, so ask for one when
the change is load-bearing.

## Template

```markdown
# N: <title, written as what is now true>

| | |
| --- | --- |
| Issue | #<issue number>, a link |
| Branch | `N-slug` |
| Spec | the documents and sections that govern this, by number |
| Decisions | DD-nn |
| Requirements | FR-xn |
| Depends on | what must be merged first, and why |

## Why
## Premises            P1..Pn, each recording how it was established
## What changes
## What does not change
## Constraints         the invariants execution must not violate
## Checklist           ordered; step 1 or 2 is always "run the new assertions against the
                       unfixed tree and record how each failed"
## Accept              AC1..ACn, each a runnable command
## Baseline repaint    UI changes only: expected count, then actual, with a reason each
## Adversarial pass    F1..Fn with a disposition each
## Deviations from the approved plan
## Durable content moved out of this plan
```

### Cite only what your branch brings

`tests/test_documentation_structure.py` asserts that every backticked `*.md` path in a tracked
file names a file that exists **on this branch**. A plan that cites a document living on a
sibling in-flight branch is therefore red until that branch merges. Three kinds of citation have
made it red: a placeholder path, a design specification on another branch, and this README cited
from a branch cut before it existed. Write the citation as prose when the document is not yours
to bring, or bring it.

### One trap in the template

Write a placeholder path with angle brackets, as docs/changes/&lt;N&gt;-&lt;slug&gt;.md, and never
as a backticked path with letters in it. `tests/test_documentation_structure.py` asserts that
every backticked `*.md` path in a tracked file names a file that exists, and a placeholder that
looks like a citation fails it. That test exists because removing two documents once left
roughly forty dangling citations and nothing in the suite noticed, so the answer is to keep
placeholders distinguishable rather than to add exemptions.

### Two tests guard a citation, and the link one fires a commit earlier

`tests/test_documentation_structure.py` carries both `BACKTICKED_DOC`, which matches a
backticked `*.md` path, and `MARKDOWN_LINK`, which matches any relative markdown link whose
target ends in `.md`. Both require the target to exist **on this branch**, and the second is
wider, so a change that stages a document and a link to it across two commits fails one
commit earlier than the backtick rule alone would predict. Measured: putting
the README licence paragraph's link to the notices file in the commit before the one that
creates the file fails `test_every_referenced_document_exists` with
`README.md: link to THIRD_PARTY_NOTICES.md`. A file with no `.md` suffix, `LICENSE` among
them, is matched by neither. So when a change's own text links a file that change creates,
the link lands in the commit that creates the file, not the one that writes the text.

## Writing premises

A premise records **how it was established**, in the premise itself: "read at
`src/glosswork/services/base.py:212`", "measured by running `find web -name '*-darwin.png' |
wc -l`", "reproduced against a seeded dev server". This is not bookkeeping. `AGENTS.md` puts it
plainly: the premises that look most solid are the ones written from structural measurements
rather than from reading the thing they describe, and a premise read off a file can be wrong
about what the file inherits.

A premise inherited from another issue's prose is the weakest kind. Re-establish it.

## Writing Accept criteria

Every criterion is a command someone else can run. Not a description of a command, and not a
comment. The rules that have cost this project time at least once:

- **Run every new assertion against the unfixed tree first, in the order written.** An
  assertion that has never failed has never been measured. An assertion that only ever executes
  after a failing one has never been measured either.
- **A fence is not coverage.** An assertion guarding behavior the change deliberately does not
  alter cannot fail there by construction. Label it a fence and do not count it.
- **Retire superseded assertions and baselines as part of the change.** Keeping one asserts two
  contradictory outcomes at once.
- **`!` applies to a whole pipeline.** `! grep A path | grep -v B` tests `grep -v`'s exit
  status, which is rarely what was meant. Use `--exclude` or a single pattern.
- **`grep -r` on a missing directory exits 2**, which `!` turns into a pass. Guard a path that
  a build produces with `test -d` first.
- **`--include="*.tsx"` never matches `index.css`**, so filtering it out afterward is dead
  weight that reads like a safeguard. This repository keeps class recipes in `.ts` files, so a
  `.tsx`-only search misses them.
- **A count in an assertion comes from the run, not from a constant.** Capture the API's answer
  in the same run and compare, so the criterion still holds when the fixture changes.

### For UI changes

- **A passing visual suite is evidence about layout only.** `toHaveScreenshot` runs at
  `maxDiffPixelRatio: 0.001`, about 1,024 pixels on a 1280x800 shot, which is more ink than a
  heading contains. Renaming the product changed the login `<h1>` and all 38 baselines passed.
  Assert text with a locator.
- **Layout is never proven in jsdom.** `getBoundingClientRect` returns zeroes. Row heights and
  breakpoints are Playwright assertions or they are not proven.
- **State the baseline repaint count and a reason for each.** A count larger than expected is a
  finding. Seeding a ninth fixture object type once repainted six unrelated baselines.
- **Run the whole functional project, not only the new spec.** A shell restructure breaks steps
  in specs that are about something else.
- **The visual project is macOS-only and does not run in CI.** The pull request is the only
  place that check exists.

## Working this with agents

The division of labor that works here, and why:

| Who | Does | Does not |
| --- | --- | --- |
| **Planning session** (a full agent with repository access) | Reads the issue and the named specifications, writes `docs/changes/<N>-<slug>.md`, runs the adversarial pass, revises the plan | Write product code |
| **Maintainer** | Approves the plan as written. Answers every design question the plan surfaces | Approve a plan that leaves a design question open |
| **`impl`** | Executes one approved checklist item at a time, with the plan file and the named specification sections in front of it | Choose, when the plan is ambiguous. It stops and reports |
| **`verify`** | Runs the Accept block and reports `PASS` / `FAIL` / `NOT PROVEN` with real output | Fix anything. It has no write tools, deliberately |

Rules that make the split safe:

- **Name the exact document and section an agent must read.** Never ask an agent to figure out
  the design. If it needs a design decision, it stops and reports rather than choosing.
- **Delegate work that is already specified; keep work that requires judgment.** Route
  handlers, CRUD endpoints, tests written from stated acceptance criteria, CSV logic, React
  components and config plumbing delegate well. The filter-AST compiler, the schema-proposal
  and coercion engine, indexing strategy, audit design, MCP tool and error copy, hybrid search
  ranking, and any change to the storage abstraction or to a decision in
  `docs/DESIGN_DECISIONS.md` do not.
- **Hand `impl` a goal, not a task list.** One checklist item, the reading list, the command
  that proves it done, and the instruction to stop and report rather than guess:

  ```
  Goal: <the one sentence that is true when this is done>
  Read first: AGENTS.md; docs/changes/<N>-<slug>.md; <exact sections>
  Do not change: <the plan's "What does not change">
  Done when: <the exact command from the Accept block>
  If the specification is ambiguous: stop and report the candidate readings. Do not choose.
  ```

- **One goal at a time, verified before the next.** Parallel agents on one change produce a
  diff nobody planned.
- **A planning agent and an implementing agent should not be the same session.** The adversarial
  pass is worth most when it is read by something that did not write the premises.
- **Agent-authored changes go through the same issue, plan, approval and pull request path as
  any other.** There is no fast lane.

## Sequencing a series

When one issue's work depends on another's:

- Write each plan when its change starts, not all of them up front. A plan written before the
  change ahead of it lands is built on premises that are about to move.
- Write the **series** plan first, as its own file, and give it the same adversarial pass: the
  dependency order, which changes share a component or a baseline, the measured repaint budget,
  and every design question the specification does not yet answer. A sequencing error found
  there is free; found in change six it is not.
- A change that repaints every baseline lands alone and first.
