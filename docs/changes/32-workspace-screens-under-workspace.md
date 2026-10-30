# 32: An object type's table is reachable whatever its key, because the workspace screens live under /workspace

| | |
| --- | --- |
| Issue | [#32](https://github.com/glosswork/glosswork/issues/32) |
| Branch | `32-workspace-screens-under-workspace` |
| Spec | `docs/DESIGN.md` 8.1, 8.6, 8.7 and every other section naming these addresses; `PRD.md` FR-U sections; `docs/DEPLOYMENT.md` |
| Decisions | A new DD entry at closeout takes the next unused number |
| Requirements | FR-U1 |
| Depends on | Nothing unmerged. Branch from `main` at `ab23ef3`, after change 13 |

**Status: shape approved by the maintainer in the working session on 2026-10-30, as a draft of
this text. The adversarial pass ran the same day, on Opus, in a session that did not write the
plan. Its findings F1 to F14 are folded in below. The address family and the old-link promise
were not reopened.**

## Why

An object type keyed `activity` has no working table. `web/src/App.tsx` serves the Activity feed
at `/activity`, and a static route outranks `/:objectTypeKey`, so the type's sidebar entry opens
the feed. Keys are immutable, so the type cannot be renamed out of the clash. `people`, `setup`,
`schema`, `audit` and `settings` shadow a type the same way. Nothing warns the person creating it.
A type keyed `schema` has it worse: `/schema/SCH-1` and `/schema/import` open the schema editor, so
its record pages and import button are broken too.

## Premises

- **P1. The clash is real on a live deployment.** Read from a running deployment's database: an
  object type with key `activity` and prefix `ACT`. Reported in the browser by the maintainer.
- **P2. Route ranking.** Read: `App.tsx` lists `/activity` above `/:objectTypeKey`, and the comment
  in that file records that React Router ranks static segments higher.
- **P3. No screen name can equal a record key.** Read: `validate_key_prefix` in `fieldtypes.py`
  requires 2-10 uppercase letters or digits, and a record key is that prefix, a dash and a number.
  The screen names are lowercase with no dash.
- **P4. No server text hands out these addresses.** Rechecked by the pass with a pattern grep over
  the product's `src`, `docs/AGENT_ONBOARDING.md`, the kit, the site, the control plane and the pilot
  package. Docstrings mention them, and the one handed-out address is `/inbox/{id}`, which does not
  move. A path assembled in pieces would not show in a grep.
- **P5. A type key's shape is exact.** Read: `KEY_PATTERN` in `fieldtypes.py` is
  `^[a-z][a-z0-9_]{0,62}$`. It allows no hyphen, so a UUID can never match it, and `useRecord` takes
  a human key or a UUID.
- **P6. An unreadable type answers 403, not 404.** Run by the pass: `get_object_type` raises
  `ForbiddenError` for a member with no grant, and no grant is the normal case. `retry` is off in the
  query client, so a pending state ends at the first answer.

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
addresses. There is no route at bare `/workspace`, so a type keyed `workspace` keeps its table. With
no such type, bare `/workspace` shows "Object type not found." like any unknown address.

A type keyed `workspace` must not light up on every workspace screen, because `NavLink` matches
subpaths and that puts two `aria-current` elements on one page. Its sidebar link takes
`end={objectType.key === "workspace"}`, at `Sidebar.tsx` and at `ObjectTypeNav.tsx`. The cost is that
this one type loses its highlight on its own record pages.

**2. Old addresses are decided below the type routes, never above them.** A static redirect at
`/activity` would recreate the bug. The static routes for `/activity`, `/audit`, `/people`,
`/setup`, `/settings` and `/schema*` are removed. When `/:objectTypeKey` resolves:

- If a type with that key is readable, its table renders, as for any other type.
- If the lookup answers 404 or 403 and the key is one of the six old addresses, it redirects with
  `replace`, keeping query string and hash: `activity` and `audit` to `/workspace/activity`, `people`
  and `settings` to `/workspace/people`, `setup` to `/workspace/setup`, `schema` to
  `/workspace/schema`. 404 and 403 behave alike, so a member with no grant reaches the screen they
  reach today and the page does not reveal that a hidden type exists.
- If the lookup fails any other way, it does not redirect and it stops spinning: the existing
  "Object type not found." shows, exactly as it does today for every error. No new copy.
- For any other key, the existing "Object type not found." stands.

The spinner shows only while the lookup is pending. The mapping takes the route parameter, not
`pathname`, so a trailing slash is harmless. Uppercase old addresses such as `/ACTIVITY` are not
mapped. The query string and hash are kept for a signed-in person; `LoginPage` returns to the
pathname only, and no moved screen reads a query string today, so this is cheap future-proofing.

**3. Old two-segment schema addresses.** `/schema/new` and `/schema/<key>` fall to
`/:objectTypeKey/:recordKey` once the static routes go. A second segment matching `KEY_PATTERN`
exactly cannot be a record key or a UUID (P3, P5), so under `schema` it always redirects to
`/workspace/schema/<encoded segment>`, whether or not a type keyed `schema` exists. A small wrapper
route makes this check and renders `RecordPage` only when it does not redirect, so no
`GET /records/new` is sent.

`/schema/import` stays the import route, and with no type keyed `schema` it shows the wizard's
existing not-found. The one casualty is an old bookmark to the editor of a type keyed `import`.
Keeping it would add a redirect branch to the wizard for almost no one.

**4. The comment at `App.tsx`** that records the reserved words is rewritten to the set that remains:
`inbox`, `search`, `login`, plus the server's own paths (F11).

## What does not change

- Data, schema keys, the REST API and MCP. A running job or agent is unaffected.
- `/inbox`, `/inbox/:proposalId`, `/search` and `/login` stay at the top level.
- What each screen contains, and every test id and accessible name in the navigation.

**Known gaps, left for a follow-up and recorded on #32:**

- A type keyed `inbox`, `search` or `login` is still shadowed, and creation does not refuse those
  keys.
- A type keyed `docs`, `redoc`, `healthz`, `readyz`, `mcp` or `assets` works from a sidebar click but
  reaches the server's own path on reload. Read by the pass, not run.
- A type keyed `new` has no reachable schema editor, because `/workspace/schema/new` wins. This
  bug exists today at `/schema/new`.

## Constraints

- No route under `/workspace` may match anything a type keyed `workspace` serves: its table, its
  import page, its record pages.
- A redirect never pushes history, so Back does not bounce.
- No new reserved word.
- The visual suite stays free of clash fixtures: a seeded type repaints baselines.
- Existing "a member's own 403 does not refetch itself" behaviour stays.
- The mapping of old to new addresses lives in one pure function, with its tests, not in a render
  body (AGENTS.md non-negotiable 3, frontend clause).
- Commits are `hello@glosswork.dev` with no session line.

## Checklist

1. Write the new assertions below and run them against the unfixed tree. Record how each failed.
2. Add the pure old-address mapping and its unit tests.
3. Move the routes and update `SHELL_NAV` and every in-app link.
4. Add the redirect decision to the object type and record routes.
5. Update existing unit tests and e2e specs that visit old addresses, including `SHELL_ROUTES`,
   `shellNav.test.ts` and `FirstRun.test.tsx`. A spec that passes through a redirect has stopped
   testing the screen directly. Tighten the regexes that match both old and new addresses
   (`/\/activity$/`, `/\/people$/`, `/\/setup$/`, `` `/schema/${KEY}$` ``) to the `/workspace/` form.
6. Update `docs/DESIGN.md`, `PRD.md` and `docs/DEPLOYMENT.md` wherever they name these addresses, and
   comments that name them as routes. `docs/DECISIONS.md` and `docs/BACKLOG.md` are history and are
   left alone.
7. Run the Accept block. Record the baseline count.
8. Close out, with a DD entry stating the rule: no route at a bare top-level segment, except
   `inbox`, `search`, `login` and the server's own paths. Add the known gaps to #32.

## Accept

- **AC1.** `npm --prefix web run test`, including new unit tests that:
  - map each old address, keeping query string and hash;
  - render a type keyed `activity` at `/activity` as its table, with the feed at
    `/workspace/activity`;
  - redirect `/activity` with `replace` when no such type exists, with Back leaving rather than
    bouncing;
  - redirect on a `forbidden` envelope for a member, as on a 404;
  - on a 500, wait for the failed response, then assert no redirect, no spinner and the existing
    not-found text;
  - do not redirect while the lookup is pending (a fence: nothing redirects on the unfixed tree);
  - at `/schema/new`, send no request to `/api/v1/records/new`, and at `/schema/<uuid>`, stay on the
    record route;
  - put one `aria-current` element on the page at `/workspace/people` with a type keyed `workspace`;
  - after a cached 404 and a redirect, return to a clashing type correctly when it exists.
- **AC2.** `npm --prefix web run e2e -- --project=e2e workspace-routes.spec.ts`, a new spec with its
  own data directory and `webServer` entries, on the precedent of `trial-banner.spec.ts`. It cannot
  share the `e2e` server: type keys are unique and cannot be retired there, a type keyed `activity`
  sorts first and changes where `/` lands for other specs, and a sidebar link named "Activity" breaks
  their strict locators. One server has no clashing types, and one has them. Covered:
  - every old address with no clashing type, plus `/schema/<key>` and `/schema/new`, asserting
    `new URL(page.url()).pathname` exactly and `page.goBack()` for at least one;
  - a type keyed `activity`: table at `/activity`, record page, feed at `/workspace/activity`;
  - a member with no grant on that type: `/activity` lands on the feed;
  - a type keyed `schema`: table, `/schema/SCH-1`, import wizard, and `/schema/<lowercase>`
    redirecting;
  - a type keyed `workspace`: table, record page and import page, beside `/workspace/people` (table,
    record and import are fences, because they pass on the unfixed tree; the single highlight is not).
- **AC3.** `npm --prefix web run e2e -- --project=e2e`.
- **AC4.** `npm --prefix web run e2e -- --project=visual`.
- **AC5.** `npm --prefix web run lint`, `npm --prefix web run typecheck`, `npm --prefix web run build`,
  each with the `--prefix web` form in `AGENTS.md`.
- **AC6.** `uv run pytest -q -m structural`.

The 22 untracked leftover files in the maintainer's tree break the literal AC3 and AC6 runs, so run
them on a clean checkout of the branch.

## Baseline repaint

Expected: 0. Screenshots do not show the address, and no product screen renders these addresses as
visible text. Any repaint is a finding. The suite's `SHELL_ROUTES`-style navigation must go to the
new addresses.

## Adversarial pass

Run 2026-10-30 on Opus by a session that did not write the plan. No plan code existed to run, so
each finding was read or probed against `matchRoutes` and `NavLink` from react-router 7.18.2.

- **F1, high.** A member with no grant would spin forever under the 403 rule as first written.
  Fixed: 404 and 403 redirect alike.
- **F2, high.** AC2 could not be written on the shared e2e server. Fixed: its own servers.
- **F3, medium-high.** A type keyed `workspace` lit up on every workspace screen. Fixed: `end`.
- **F4, medium.** AC1 required an endless spinner on a server error. Fixed: today's message shows.
- **F5, medium.** Existing URL assertions matched old and new addresses alike. Fixed: exact pathname,
  tightened regexes.
- **F6, medium.** A type keyed `schema` was unnamed and untested. Fixed: in Why and AC2.
- **F7, medium.** Parts of AC2 cannot fail unfixed. Fixed: labelled as fences.
- **F8, medium.** "Lowercase" was the wrong test and the check fired a request. Fixed: `KEY_PATTERN`
  and a wrapper route.
- **F9, medium.** `/schema/import` cost more than it saved. Fixed: dropped.
- **F10, medium.** Documents were missed. Fixed: `PRD.md`, `docs/DEPLOYMENT.md`, pinned test strings.
- **F11, low-medium.** The DD rule was false twice. Fixed: reworded, and gaps listed.
- **F12, low.** The query string has no reader. Accepted and stated.
- **F13, low.** Case and trailing slash. Accepted and stated.
- **F14, low.** Bare `/workspace` shows not-found. Accepted, as in the plan.

Held up: route ranking, P2 to P4, the SPA fallback, `NavLink` on subpaths, and change 13's code apart
from `SHELL_ROUTES`. Not checked by the pass: P1. The pass did not run the e2e or visual suites.

## Deviations from the approved plan

None yet.

## Durable content moved out of this plan

Nothing yet.
