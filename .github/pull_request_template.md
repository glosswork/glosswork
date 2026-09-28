<!--
This pull request is opened ONCE, when the change is finished and verified, and not as a
draft beforehand. Designing a change, attacking its premises and revising it happen locally;
GitHub is where a finished change goes. See CONTRIBUTING.md and docs/changes/README.md.

By the time you open this, the plan file is already deleted (its durable content moved into
the specifications), the adversarial pass is folded into the branch history, and the Accept
block has passed on your machine. Paste the plan's final text below: a file added and deleted
on one branch never appears in this pull request's diff, so this is the only place it lands.
-->

Closes #

## Plan

<!--
The plan's final text, as it stood when the change was executed. Not a summary, and not a
link to a file that no longer exists. Collapse it if it is long:

<details><summary>docs/changes/N-slug.md, as executed</summary>

...the plan...

</details>
-->

- Adversarial pass: <!-- findings F1..Fn, or "none found", and who ran it -->
- Plan approved by: <!-- maintainer, and where: the working session, or a review of the file -->

## Accept

<!--
The OUTPUT of the Accept block, run as written, on your machine, before this pull request
existed. Every criterion is a command; a criterion that is a sentence is a defect in the plan.
Say which criteria are fences: an assertion guarding behaviour this change deliberately does
not alter cannot fail here, and does not count as coverage.
-->

```
```

## Local checks CI does not run

- [ ] Playwright functional suite: `npm --prefix web run e2e -- --project=e2e` (also runs in CI)
- [ ] Playwright **visual** suite: `npm --prefix web run e2e -- --project=visual`
      <!-- macOS-only baselines, excluded from CI. Required for any UI change.
           State the number of baselines repainted, and why each was expected. A count larger
           than expected is a finding, not a formality. -->
- [ ] Text this change touches is asserted by locator, not by screenshot
      <!-- toHaveScreenshot runs at maxDiffPixelRatio 0.001, which hides a heading-sized
           difference. A passing visual suite is evidence about layout only. -->
- [ ] Container acceptance, if the image or its entrypoint changed:
      `uv run pytest -q container_tests`

## Deviations from the approved plan

<!-- Every one, with why it was right. They are recorded in the plan as they happen; this is
     the summary. "None" is a valid answer, but it is an answer. -->

## Closeout

- [ ] A new or edited entry in `docs/DESIGN_DECISIONS.md`, if this change established or changed a rule
- [ ] Durable content moved to `PRD.md` / `docs/` (this is part of the change, not a follow-up)
- [ ] Superseded assertions and baselines retired, not left contradicting the new ones
- [ ] Plan file deleted in the final commit, its final text pasted above
- [ ] Merged only after everything above was true, with "Create a merge commit" as
      hello@glosswork.dev
