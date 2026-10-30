# 32: An object type's table is reachable whatever its key, because the workspace screens live under /workspace

| | |
| --- | --- |
| Issue | [#32](https://github.com/glosswork/glosswork/issues/32) |
| Branch | `32-workspace-screens-under-workspace` |
| Spec | `docs/DESIGN.md` 8.1, 8.6 and 8.7 |
| Decisions | A new DD entry at closeout takes the next unused number |
| Requirements | FR-U1 |
| Depends on | Nothing unmerged. Branch from `main` at `ab23ef3`, after change 13 |

**Status: shape approved by the maintainer in the working session on 2026-10-30, as a draft of
this text. The adversarial pass has not run.**

## Why

An object type keyed `activity` has no working table. `web/src/App.tsx` serves the Activity feed
at `/activity`, and a static route outranks `/:objectTypeKey`, so the type's sidebar entry opens
the feed. Keys are immutable, so the type cannot be renamed out of the clash. `people`, `setup`,
`schema`, `audit` and `settings` shadow a type the same way. Nothing warns the person creating it.

## Premises

- **P1. The clash is real on a live deployment.** Read from a running deployment's database: an
  object type with key `activity` and prefix `ACT`. Reported in the browser by the maintainer.
- **P2. Route ranking.** Read: `App.tsx` lists `/activity` above `/:objectTypeKey`, and the comment
  in that file records that React Router ranks static segments higher.
- **P3. No screen name can equal a record key.** Read: `validate_key_prefix` in `fieldtypes.py`
  requires 2-10 uppercase letters or digits, and a record key is that prefix, a dash and a number.
  The screen names are lowercase with no dash.
- **P4. No server text hands out these four addresses.** Established by grep over `src` and
  `docs/*.md` for quoted paths and for `}/people` style interpolation. Docstrings mention them. The
  pass must recheck this, including `docs/AGENT_ONBOARDING.md` and MCP tool descriptions.
- **P5. Object type keys are lowercase.** Read: `KEY_PATTERN` is `^[a-z][a-z0-9_]*$`.

## What changes

**1. The four screens move.**

| Screen | Today | New |
| --- | --- | --- |
| People & agents | `/people` | `/workspace/people` |
| Activity | `/activity` | `/workspace/activity` |
| Schema | `/schema` | `/workspace/schema` |
| Schema, new | `/schema/new` | `/workspace/schema/new` |
| Schema, one type | `/schema/<key>` | `/workspace/schema/<key>` |
| Setup | `/setup` | `/workspace/setup` |

`SHELL_NAV`, the first-run link, the schema editor's links and every other in-app link use the new
addresses. There is no route at bare `/workspace`, so a type keyed `workspace` keeps its table.

**2. Old addresses are decided below the type routes, never above them.** A static redirect at
`/activity` would recreate the bug. The static routes for `/activity`, `/audit`, `/people`,
`/setup`, `/settings` and `/schema*` are removed. When `/:objectTypeKey` resolves:

- If a type with that key is readable, its table renders, as for any other type.
- If not, and the key is one of the six old addresses, it redirects with `replace`, keeping query
  string and hash: `activity` and `audit` to `/workspace/activity`, `people` and `settings` to
  `/workspace/people`, `setup` to `/workspace/setup`, `schema` to `/workspace/schema`.
- Otherwise the existing "Object type not found." stands.

The decision waits for the type lookup to settle, showing the existing spinner, and must not act on
a transient failure of that lookup as though the type did not exist.

**3. Old two-segment schema addresses.** `/schema/new` and `/schema/<key>` fall to
`/:objectTypeKey/:recordKey` once the static routes go. A lowercase second segment cannot be a
record key (P3), so under `schema` it always redirects to `/workspace/schema/<segment>`, whether or
not a type keyed `schema` exists. `/schema/import` is the one ambiguous case: if a type keyed
`schema` is readable it is that type's import wizard, and otherwise it redirects to the editor of a
type keyed `import`.

**4. The comment at `App.tsx`** that records the reserved words is rewritten to the set that remains:
`inbox`, `search`, `login`.

## What does not change

- Data, schema keys, the REST API and MCP. A running job or agent is unaffected.
- `/inbox`, `/inbox/:proposalId`, `/search` and `/login` stay at the top level.
- What each screen contains, and every test id and accessible name in the navigation.

**Known gap, left for a follow-up:** a type keyed `inbox`, `search` or `login` is still shadowed,
and creation does not refuse those keys. Recorded on #32.

## Constraints

- No route under `/workspace` may match anything a type keyed `workspace` serves: its table, its
  import page, its record pages.
- A redirect never pushes history, so Back does not bounce.
- No new reserved word.
- The mapping of old to new addresses lives in one pure function, with its tests, not in a render
  body (AGENTS.md non-negotiable 3, frontend clause).
- Commits are `hello@glosswork.dev` with no session line.

## Checklist

1. Write the new assertions below and run them against the unfixed tree. Record how each failed.
2. Add the pure old-address mapping and its unit tests.
3. Move the routes and update `SHELL_NAV` and every in-app link.
4. Add the redirect decision to the object type and record routes.
5. Update existing unit tests and e2e specs that visit old addresses, keeping one spec per old
   address that proves the redirect.
6. Update `docs/DESIGN.md` 8.1, 8.6 and 8.7, and comments that name these addresses as routes.
7. Run the Accept block. Record the baseline count.
8. Close out, with a DD entry stating the rule: a built-in screen may not take a top-level segment
   that a valid object type key could equal, except the three listed.

## Accept

- **AC1.** `npm --prefix web run test`, including new unit tests that:
  - map each old address, keeping query string and hash;
  - render a type keyed `activity` at `/activity` as its table, with the feed at
    `/workspace/activity`;
  - redirect `/activity` with `replace` when no such type exists, with Back leaving rather than
    bouncing;
  - keep the spinner and do not redirect while the lookup is pending, or when it fails with anything
    other than not found.
- **AC2.** `npm --prefix web run e2e -- --project=e2e workspace-routes.spec.ts`, a new spec against
  the real server covering a type keyed `activity` and a type keyed `workspace` (table, record page,
  import page), every old address with no clashing type, and `/schema/<key>` and `/schema/new`.
- **AC3.** `npm --prefix web run e2e -- --project=e2e`.
- **AC4.** `npm --prefix web run e2e -- --project=visual`.
- **AC5.** `npm --prefix web run lint`, `npm --prefix web run typecheck`, `npm --prefix web run build`,
  each with the `--prefix web` form in `AGENTS.md`.
- **AC6.** `uv run pytest -q -m structural`.

The 22 untracked leftover files in the maintainer's tree break the literal AC3 and AC6 runs, so run
them on a clean checkout of the branch.

## Baseline repaint

Expected: 0. Screenshots do not show the address. Any repaint is a finding.

## Adversarial pass

Not yet run.

## Deviations from the approved plan

None yet.

## Durable content moved out of this plan

Nothing yet.
