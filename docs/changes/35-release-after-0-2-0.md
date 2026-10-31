# 35: The version after 0.2.0 is released, carrying changes 13 to 19, 28, 30 and 32

| | |
| --- | --- |
| Issue | [#35](https://github.com/glosswork/glosswork/issues/35) |
| Branch | `35-release-after-0-2-0` |
| Spec | `CONTRIBUTING.md` "Releases"; `CHANGELOG.md`; `README.md` "Run it" |
| Decisions | none |
| Requirements | none |
| Depends on | Nothing unmerged. `main` at `badcc57`, where CI passed in run 41202504116 |

## Why

Nobody can run a published image with these changes. The newest published version is 0.2.0.

## Premises

- **P1. The version is 0.3.0.** `CONTRIBUTING.md` raises `Y` for a changed API or MCP contract. Change
  15 changes one: a date literal that used to answer 200 can now answer 422 (DD-48).
- **P2. The changelog text is approved.** The maintainer approved it on 2026-10-31. A line for change
  28 was added under the file's one-line-per-change rule, as he was told before approving.

## What changes

`pyproject.toml`, the `glosswork` entry in `uv.lock`, the `README.md` run line, and a `CHANGELOG.md`
section for 0.3.0.

## What does not change

Product code and tests. The workflow. Nothing is published by this change: only the maintainer's tag
does that.

## Constraints

`git diff --text uv.lock` changes only the `glosswork` version line.

## Checklist

1. Make the four edits.
2. Check each changelog claim against `main`.
3. Run the Accept block on a clean checkout.
4. Close out.

## Accept

- **AC1.** `git diff --text origin/main -- uv.lock` changes one line.
- **AC2.** `uv run pytest -q -m structural`.
- **AC3.** `uv run pytest -q tests/test_release_workflow.py`.

Results at this branch's release commit, on a clean checkout: AC1 one line changed. AC2 107 passed.
AC3 13 passed. The container tests and the image build run in the release workflow.

## Adversarial pass

Skipped by the maintainer's decision on 2026-10-31: releases are low risk. Instead, the session
that wrote this plan checked the changelog's claims against the code.

## Deviations from the approved plan

None.

## Durable content moved out of this plan

The release text is in `CHANGELOG.md`. Nothing else.
