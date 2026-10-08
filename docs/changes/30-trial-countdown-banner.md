# 30: A workspace on trial shows the time left and a subscribe link, and says when the trial has ended

| | |
| --- | --- |
| Issue | [#30](https://github.com/glosswork/glosswork/issues/30) |
| Branch | `30-trial-countdown-banner` |
| Spec | PRD.md FR-P3, FR-P9; docs/DESIGN_DECISIONS.md DD-28, DD-38; docs/DESIGN.md sections 5, 7, 8.1 and 10; docs/DEPLOYMENT.md section 6a; `.env.example`; PLAN.md sections 6.1 and 12 item 5, Q12 and Q73 |
| Decisions | One new decision, taking the next unused number (47 when this was written; text proposed under "Durable content"). DD-28 is amended: the workspace document gains a fifth key. DD-38 is read and not changed |
| Requirements | FR-P3; one new requirement, taking the next unused number (FR-P12 when this was written) |
| Depends on | Nothing. `main` at `85bea5a`, which carries `GW_SUBSCRIBE_URL` (DD-38). Unblocks control-plane CP-10 |

## Why

A hosted trial lasts one day (PLAN Q12). The person on it should see, on every page, how
long is left and where to subscribe, and should be told plainly once it is over. Today
the workspace cannot be told a trial exists (P1), nothing in the UI mentions one (P2), and
the browser has no read that could carry the fact (P3).

The hosting operator already configures a workspace entirely through `GW_` environment
variables, and already hands it the subscribe address that way (P4, P5). This change adds
one more optional variable, the trial end time, puts it and the subscribe address into the
one read the shell already makes, and draws a banner from them.

A self-hosted workspace sets no trial end time and gets no banner, no banner markup, and
no new behaviour (PLAN Q73). The same image serves both (PLAN Q6).

**This change does not reach the filter compiler, the schema engine or the access model**
(P14, and AC8 is the fence). It adds no table, no migration, no route, no MCP tool and no
credential.

## Judgment areas this change reaches (CONTRIBUTING)

The maintainer approves each of these in this plan before any code.

1. **How the trial end time reaches the workspace: one environment variable, read at
   startup.** `GW_TRIAL_ENDS_AT`. This is how every other hosted-only setting arrives
   (P4), and it is how the freeze is switched on (P5), so the two facts that meet at hour
   24 travel the same road. Considered and declined: a route the operator's token calls to
   set the time while the workspace runs. That would be a stored value and a second thing
   the operator credential may do, which is a change to what DD-39 grants, for a value that
   is known when the workspace is created or shortly after.

   **What that costs the hosting operator, stated because it is not free.** Changing a
   running workspace's environment restarts it, about twenty seconds on the first hosting
   driver (P6). So the operator sets this variable when it creates the workspace, and pays
   one restart to remove it when the person subscribes. P6 records that the control plane
   as written today fixes the trial end only after the workspace first answers, which
   would mean a second restart. That is the control plane's to settle and is reported to
   its task. Nothing in this change depends on which it chooses.

2. **The banner is drawn from the browser's clock.** The workspace sends the end time; the
   browser subtracts its own clock from it, once a second. A person whose laptop clock is
   wrong by ten minutes sees a countdown wrong by ten minutes. The freeze is not affected:
   that is the operator's restart, on the operator's clock. Considered and declined: the
   server sending seconds remaining, or its own time for the browser to correct against.
   Either is more accurate on a wrong clock and both make the end-to-end test unable to
   set the time it asserts on without a test-only server setting.

   **Amended by the adversarial pass (F14).** The two directions are not equal. A clock
   that runs ahead says "Trial ended" early, on a workspace that still works. A clock that
   runs behind is the one that matters: the banner promises time that does not exist, and
   a save is refused while it still reads, say, "00:10 left". And the reason above for
   declining server time is stronger than the facts: an end-to-end test could still assert
   it, with an end time computed when the test server starts and a looser assertion, at
   the price of losing the exact `HH:MM` scenarios and the crossing scenario. The design
   stays as written. The choice is the maintainer's, under Q-D, with both costs stated.

3. **"Trial ended" follows the end time, not the freeze.** The banner switches when the
   end time passes. The workspace freezes when the operator restarts it with
   `GW_READ_ONLY=true`, which is the same moment by intent and later in practice: however
   long the operator's scheduled freeze takes to run, plus the restart, during which every
   request fails (P5, P6). How long that is was not measured here, because it is the
   control plane's (F15). In that gap the banner says the trial has ended and an edit
   still saves. The banner therefore claims only that the trial ended. It does
   not say the workspace is read-only, because for those seconds that would be false.
   Considered and declined: also sending whether the workspace is read-only and switching
   on either. It would make a self-hosted read-only workspace's document change, and it
   solves a gap of seconds.

4. **A new key on the workspace document, visible at `read` scope.** `trial` is `null`
   unless the end time is set, and otherwise an object with the end time and the subscribe
   address. Every signed-in person and every `read` token can therefore read the subscribe
   address during a trial. **This is a new disclosure, not an existing one (F6).** The
   first draft of this plan said the address is already given to every caller whose write
   is refused. Measured, that is true only of a credential that could have written, and
   only once the workspace is frozen: a `read` token's write is refused for its scope
   first, with a 403 that carries no address, frozen or not (P16). So today no `read`
   credential ever receives the address, and nobody receives it while a trial is running.
   After this change both do. The hosting plan accepts that a subscribe link only lets
   someone pay (PLAN Q64), which holds only while the address carries nothing secret, so
   that becomes a written rule for the operator (Constraints, and docs/DEPLOYMENT.md at
   closeout) and a question for the maintainer (Q-F). On a self-hosted workspace the only difference is one key whose value is
   `null`, as `mcp_url` is when `GW_BASE_URL` is unset.

5. **Everyone signed in sees the banner, not only administrators.** A trial that ends
   freezes everyone's work, and the link only lets someone pay. This is a product choice
   and is put to the maintainer under "Questions for the maintainer".

6. **The words on the banner.** Drafts only, under "Questions for the maintainer". The
   build uses whatever the maintainer approves there. The component and its tests take
   the copy from one constant so a wording change is one line.

7. **A new design decision and a new requirement.** Proposed text is under "Durable
   content".

8. **Where the strip sits (F2).** The first draft put it above the sidebar and the top
   bar, full width. Built that way it pushes the page 35 pixels past the bottom of the
   window on every screen and takes the sign-out control below the fold (P17). The plan
   now puts it at the head of the main column: to the right of the sidebar on a wide
   window, directly under the top bar on a narrow one. That placement was built and
   measured with no overflow at three window sizes. It is a visible choice and is put to
   the maintainer under Q-E.

## Premises

Everything below was established on 2026-10-08 against `main` at `85bea5a`, on arm64
macOS, Node 22.17.1, pydantic 2.13.4, `@playwright/test` 1.62.1.

- **P1. The workspace cannot be given a trial end time.** Run:
  `GW_TRIAL_ENDS_AT=not-a-time uv run python -c "from glosswork.config import load_settings; s = load_settings(); print(hasattr(s, 'trial_ends_at'))"`
  prints `False` and exits 0. `Settings` is declared with `extra="ignore"`
  (`src/glosswork/config.py:51`), so the name is discarded without a word.

- **P2. Nothing in the UI mentions a trial period.** Run: a case-insensitive
  `grep -rli trial web/src web/e2e` matches `web/src/routes/mcpUrl.ts` and
  `web/src/routes/IndexRoute.test.tsx` only. Read: both use the word for a person trying
  the product on a laptop.

- **P3. The shell's one read about the deployment has exactly four keys, pinned.** Read at
  `src/glosswork/envelopes.py:298-314` and `tests/test_api_workspace.py:70-79` and `:320`:
  `set(body) == {"name", "people", "agents", "mcp_url"}`. Run:
  `uv run pytest -q tests/test_api_workspace.py tests/test_config.py` exits 0, 47 passed.
  So the pin is live, and a fifth key turns it red until the pin is rewritten, which is a
  retired assertion and a checklist step.

- **P4. Every hosted-only setting is an optional `GW_` variable that is off when unset or
  blank.** Read in `src/glosswork/config.py`: `bootstrap_secret` (`:81`), `operator_token`
  (`:101`), `subscribe_url` (`:235`), `relay_url` and `relay_token` (`:243-244`), and the
  three TLS paths (`:259-261`). Each comment says blank counts as unset because
  `.env.example` ships every variable blank, and
  `test_every_setting_config_reads_appears_in_env_example` (`tests/test_config.py:119`)
  fails a setting that is missing from that file.

- **P5. The freeze is `GW_READ_ONLY`, read once at startup, and the subscribe address is
  `GW_SUBSCRIBE_URL`, which may be set without it.** Read at `src/glosswork/config.py:222-235`
  and `src/glosswork/services/workspace.py:97-115`: one predicate,
  `refuse_write_if_read_only`, reads `settings.read_only` and hands
  `settings.subscribe_url` to the refusal. The comment at `config.py:233-234` already says
  the subscribe address is independent "because a trial banner needs the link while the
  trial is still running". `docs/DEPLOYMENT.md` section 6a says turning the mode on or off
  is a restart, and that nothing in the UI says a deployment is read-only.
  `test_a_subscribe_url_is_allowed_without_read_only` (`tests/test_config.py:219`) passed
  in P3's run.

- **P6. What the control plane does today, read in its repository at `36cf004`, not run.**
  Its docs/SPEC.md section 1 item 7 says a freeze is a restart of about twenty seconds and
  that updating a running machine's configuration reboots it. Its environment table lists
  "Trial end time: set for `trialing`, absent otherwise. Not yet in the product. PROD-04
  names the variable." Its state table sets `trial_ends_at` "on the first `/readyz`", which
  is after the workspace is already running, and drops the trial end on subscribing "(a
  restart)". Its provisioning code (`src/glosswork_control/services/tenants.py:234-245`)
  builds the workspace's environment with ten variables, and `GW_SUBSCRIBE_URL` is not one
  of them: the state table sets the subscribe link at the freeze. It stores times as ISO
  8601 with microseconds and a `Z`.
  **Two consequences for the control plane, neither of which this change can fix:** as
  written, a workspace on trial would have no subscribe address until it is frozen, so the
  banner would count down with no link; and setting the trial end after first readiness
  costs a restart in the first minute of a one-day trial. Both are reported to CP-10.

- **P7. pydantic's aware datetime type accepts the control plane's format, refuses a time
  with no offset, and also accepts a bare number.** Run, through
  `TypeAdapter(AwareDatetime).validate_python`, then `timeutil.format_datetime`:

  | Input | Result |
  | --- | --- |
  | `2026-09-30T12:00:00.000000Z` | `2026-09-30T12:00:00Z` |
  | `2026-09-30T12:00:00Z` | `2026-09-30T12:00:00Z` |
  | `2026-09-30T08:00:00-04:00` | `2026-09-30T12:00:00Z` |
  | `2026-09-30T12:00:00` | refused, "Input should have timezone info" |
  | `2026-09-30` | refused, "Input should have timezone info" |
  | `1790000000` | **accepted**, as `2026-09-21T14:13:20Z` |
  | blank | refused, "input is too short" |

  So the type alone is not enough: a blank must be turned into unset before parsing, as
  the TLS paths do (`config.py:263-270`), and a bare number must be refused by this
  change's own check, because a mistyped number silently becoming a trial end is the kind
  of value nobody can diagnose from a banner.

- **P8. The workspace sends times in a form every browser parses.** `format_datetime`
  (`src/glosswork/timeutil.py:20`) renders `2026-08-23T14:22:00Z`, whole seconds and a
  `Z`. Run: `node -e 'console.log(new Date("2026-09-30T12:00:00Z").getTime())'` prints
  `1790769600000`. The key carries that form, never the operator's input echoed.

- **P9. The shell is one component, and the sign-in page is outside it.** Read at
  `web/src/App.tsx:45-128`: `/login` renders `LoginPage` alone; every other route renders
  `Shell` inside `RequireAuth`. `Shell` already calls `useWorkspace()` and holds the
  document, so the banner needs no second request. The route behind it requires `read`
  scope (`src/glosswork/routes/identity.py:137`), so there is nothing to draw a banner from
  before sign-in.

- **P10. No MCP tool returns the workspace document.** Run:
  `grep -rn "get_workspace\|workspace_doc" src/glosswork/mcp_server` prints nothing. The
  change is REST and browser only, and the tool catalog does not move.

- **P11. A Playwright test can pin the browser's clock while timers keep running, and move
  it.** Run, with this repository's `playwright-core` 1.62.1 against Chromium, a page
  whose `setInterval` writes a tick count and `new Date().toISOString()` every 200 ms:
  after `page.clock.setFixedTime("2026-10-08T12:00:00Z")` the page read
  `3 2026-10-08T12:00:00.000Z`, and after a second `setFixedTime` to
  `2026-10-09T12:00:01Z` it read `4 2026-10-09T12:00:01.000Z`. So one spec can assert an
  exact `HH:MM` against a fixed end time, then cross the end and assert "ended", with no
  waiting and no test-only server setting.
  **Established by the adversarial pass, on a throwaway build of this plan (F10):** the
  real application signs in with the clock pinned before navigation, and the banner
  follows the clock when it is moved on an open page with no reload. Run: a
  `playwright-core` 1.62.1 script against a real `uvicorn` on port 8941 serving a built
  `web/dist`, with `GW_TRIAL_ENDS_AT=2030-01-02T00:00:00Z`. With the clock at
  `2030-01-01T00:01:00Z` the banner read `23:59`; after `setFixedTime` to
  `2030-01-01T23:59:30Z` it read `00:01`; after `2030-01-02T00:00:00Z` it read the ended
  text with no time element. The same three readings came back with the browser's time
  zone set to UTC, to Pacific/Kiritimati (14 hours ahead) and to America/St_Johns (a
  half-hour zone). Checklist step 2 still checks it first, because the throwaway build is
  not the build.

- **P12. The existing hosted test server cannot carry this spec.** Read at
  `web/e2e/email-code.spec.ts:7-10`: its first administrator is sent at most four codes a
  run, retries included, against a cap of five an hour, and password sign-in is off there.
  A banner spec signing in on that server would spend the fifth and sixth. So the spec
  gets its own server, with password sign-in. **That server needs its own first
  administrator (F12):** a server started with only a data directory, standalone sign-in,
  embedding off and the two trial variables answers the e2e administrator's sign-in with
  401, because nothing created the account. The emailed-code server gets its
  administrator from `GW_BOOTSTRAP_ADMIN_EMAIL` and `GW_BOOTSTRAP_ADMIN_PASSWORD`, and
  this one does the same. Ports 8931 to 8935 are taken
  (`web/e2e/constants.ts`); `grep -rn 8936 web/e2e web/playwright.config.ts` prints
  nothing.

- **P13. There are 41 committed visual baselines, and none can see the banner.** Run:
  `find web -name '*-darwin.png' -not -path '*/node_modules/*' | wc -l` prints 41.
  `npx playwright test --list` from `web/` reports 77 tests in 22 files for the functional
  project and 49 in 1 file for the visual one. The visual project has its own server
  (`web/playwright.config.ts`), and this change sets no trial end on it.

- **P14. The three areas the hosted work must not touch are these files.** Read: the
  filter compiler is `src/glosswork/compiler.py`, `filters.py` and `sqlexpr.py`; the schema
  engine is `src/glosswork/services/schema.py` and `fieldtypes.py`; the access model is
  `src/glosswork/services/access.py` and `scopes.py`. The checklist names none of them.

- **P15. The existing banner in the product is a different thing with a guard of its
  own.** `web/src/access/ReadOnlyBanner.tsx` is DD-42's per-object-type access banner, and
  `web/src/access/hidingIsNeverTheOnlySignal.test.ts:61-66` fails any module that contains
  `levelAllows(` without `ReadOnlyBanner`. The trial banner gates on no access level, so
  it must not call `levelAllows` and is not named `ReadOnlyBanner`. docs/DESIGN.md 7.5
  says the access banner is the only one that uses the human colour family.

The premises below were established by the adversarial pass on 2026-10-08, on a throwaway
build of this plan in a separate worktree at `a8604c8`, never committed. Each is a
measurement, with the finding it supports.

- **P16. A `read` credential is never handed the subscribe address today.** Run, against
  a real server started with `GW_READ_ONLY=true` and `GW_SUBSCRIBE_URL` set:
  `POST /api/v1/object-types` with a `read` token answers 403 `insufficient_scope` with
  no address in the body; the same call with an `admin` token answers 409
  `workspace_read_only` with the address. On a server that is not frozen the `read` token
  gets the same 403. `GET /api/v1/workspace` with no credential answers 401. (F6)

- **P17. The sidebar is exactly as tall as the window, so anything placed above the shell
  is added to the page's height.** Read at `web/src/app/Sidebar.tsx:226` (`h-screen`) and
  `web/src/App.tsx:80` (`min-h-screen`). Measured in Chromium at 1280 by 800 with a
  35-pixel strip rendered above the shell: the sidebar ran from 35 to 835, the signed-in
  block's bottom edge was at 835, and the document's scroll height was 835 against a
  window of 800. Without the strip all three are 800. With the strip as the first thing
  inside `<main>` instead, at 1280 by 800 the sidebar ran from 0 to 800, the strip from
  0 to 35 at x 224, and the scroll height was 800; at 800 by 800 the strip sat from 62
  to 97, directly under the top bar, with a scroll height of 800. (F2)

- **P18. The operator's usage read calls the same service method.** Read at
  `src/glosswork/services/usage.py:480`: `GET /api/v1/usage` calls
  `WorkspaceService.get_workspace()`. So anything that can fail while that method builds
  the `trial` value fails the hosting operator's read of the workspace as well as the
  browser's. (F4)

- **P19. An end time at the edge of the calendar is accepted at startup and fails at
  request time, if the conversion to UTC is left to the request.** Run:
  `GW_TRIAL_ENDS_AT=9999-12-31T23:59:59-14:00` loads; `timeutil.format_datetime` on it
  raises `OverflowError: date value out of range`, and so does
  `0001-01-01T00:00:00+14:00`. A server started that way answered `/readyz` with 200,
  `GET /api/v1/workspace` with 500 `internal_error` and `GET /api/v1/usage` with 500, and
  the shell rendered with no workspace block and no banner. (F4)

- **P20. pydantic's type accepts more than a bare whole number.** Extending P7's table,
  the same way: `1790000000.5` is accepted as `2026-09-21T14:13:20Z`; `-1` is accepted as
  `1969-12-31T23:59:59Z`; `2026-10-09 15:00:00Z` with a space is accepted; a trailing
  space, a trailing newline and a value wrapped in quotes are each refused; a value with
  no offset is refused with pydantic's own sentence, "Input should have timezone info",
  which names the variable through `load_settings` and gives no example. A check for
  digits only refused `1790000000` and let `1790000000.5` and `-1` through. (F5)

- **P21. The assertion "no banner once the signed-in block is visible" can pass on a
  workspace that is about to show one.** The signed-in block comes from `GET /api/v1/me`
  and the banner from `GET /api/v1/workspace`, two separate reads. Run, against the trial
  server: with the workspace read held for 800 ms, the banner's count was 0 at the moment
  `current-principal` became visible in 8 sign-ins of 8, and so was the count of
  `workspace-people-agents`, the sidebar line drawn from the same document. With no
  delay it was 0 of 8 on the home page, and it happened once unprompted on `/setup`. (F3)

- **P22. An open tab re-reads the workspace document only when it is reloaded or returned
  to.** Run: a tab open on the trial server while that server was restarted with no trial
  end. Left alone for three seconds, and after a click on a sidebar link, the banner was
  still there and no new read had been made. After a `visibilitychange` event the
  document was read once and the banner was gone. That re-read is the query library's
  default (`web/src/app/queryClient.ts` sets only `retry`), not something this
  repository has chosen. (F11)

- **P23. Two files in one directory whose names differ only in the case of one letter do
  not build on macOS.** The first draft named `web/src/app/trialBanner.ts` beside
  `web/src/app/TrialBanner.tsx`. Built that way, `npm run build` exited 2 with
  `TS2305: Module '"./app/TrialBanner"' has no exported member` and `TS1261: Already
  included file name ... differs from file name ... only in casing`. No two source files
  in `web/src` differ only in case today. (F7)

- **P24. The frontend's type check does not notice a workspace fixture without the new
  key.** Run, with `trial` added to `WorkspaceDoc` as a required field:
  `npm --prefix web run typecheck` exits 0. With the shell written as "render when
  `workspace.trial !== null`", `npm --prefix web run test` exits 1: seven tests in
  `web/src/App.test.tsx` fail with `TypeError: Cannot read properties of undefined
  (reading 'ends_at')`, because their document has no `trial` key and `undefined` is not
  `null`. (F8)

- **P25. A clock hook called from `Shell` re-renders the whole shell every second.**
  Measured over five seconds on the trial server: 5 renders of `Shell` with the hook
  called there, 0 with the hook called inside the banner component. (F9)

- **P26. This plan file, as first committed, fails a guard that runs on every pipeline.**
  Run on a clean checkout of `a8604c8`:
  `uv run pytest -q tests/test_documentation_structure.py` exits 1,
  `test_every_cited_design_decision_exists` naming this file, because the proposed
  requirement text cited the new decision by a number that has no heading yet. The guard
  is in the structural lane. With the citation written in words the same command exits
  0. (F1)

## What changes

**The setting.** `Settings.trial_ends_at`, from `GW_TRIAL_ENDS_AT`. Optional. Blank is
unset. Anything else must be an ISO 8601 date and time with a UTC offset or `Z`, such as
`2026-10-09T15:00:00Z`. The rule, restated after F4 and F5 so that each part is testable:

- Leading and trailing whitespace is removed first, so a stray space or newline is not a
  refusal.
- **Any value Python's `float()` accepts is refused**, before the datetime type sees it.
  That covers `1790000000`, `1790000000.5` and `-1` (P20), which the type would otherwise
  read as seconds since 1970.
- A value with no offset and a bare date are refused.
- **The value is converted to UTC once, at startup, and a value that cannot be converted
  is refused there** (P19). What the setting holds afterwards is a UTC time that
  `format_datetime` cannot fail on.
- **Every refusal of this variable is the same sentence**, naming `GW_TRIAL_ENDS_AT` and
  giving the example above, whichever rule refused it. pydantic's own wording does not
  reach the operator for this variable (P20).

A time already in the past is legal: a frozen workspace is restarted with it. A time far
in the future is legal too, and reads oddly rather than wrongly: the banner shows
`100:00` for a hundred hours and `8760:00` for a year, so an operator who mistypes the
year gets a banner that says so (F16). It may be set without `GW_SUBSCRIBE_URL`,
and then the banner has no link. `.env.example` ships it blank with a comment. At startup,
when it is set, one `info` line `trial_end_set` carries the end time and whether a
subscribe address is set, as `read_only_mode` does for the freeze.

**The service.** `WorkspaceService.get_workspace` returns a `trial` value on the
document: `None` when the setting is unset, and otherwise the end time and
`settings.subscribe_url`. No clock is read on the server, and no database read is added.
**Nothing in building that value can raise**: the conversion that could was done at
startup (P19), because this method is also behind the operator's usage read (P18).

**The document.** `GET /api/v1/workspace` gains a fifth key:

```json
"trial": null
```

or, with the setting set:

```json
"trial": { "ends_at": "2026-10-09T15:00:00Z", "subscribe_url": "https://example.com/subscribe" }
```

`subscribe_url` is `null` inside the object when `GW_SUBSCRIBE_URL` is unset. The
subscribe address is never sent outside the `trial` object, so a workspace with a
subscribe address and no trial sends nothing new.

**The browser.** Three small pieces, with the logic outside the component (AGENTS.md
non-negotiable 3):

- `web/src/app/trialCountdown.ts`: a pure function from the end time and a millisecond
  clock reading to either `{ kind: "running", timeLeft }` or `{ kind: "ended" }`, and the
  copy constants. The name is deliberately not the component's name in another case
  (P23, F7). `timeLeft` is hours and minutes left, each at least two digits, **rounded up
  to the minute**, so a trial with 30 seconds left reads `00:01` and never `00:00`, and a
  full day reads `24:00`. At or after the end time the state is `ended`. An end time the
  browser cannot parse yields no banner.
- `web/src/app/useTrialClock.ts`: a hook that returns the current time and re-renders
  its caller once a second. **It is called from the banner component and from nowhere
  else**, so the shell and the page under it do not re-render with it (P25, F9). With no
  trial the banner is not mounted, so no timer exists.
- `web/src/app/TrialBanner.tsx`: one strip across the head of the main column (judgment
  area 8, Q-E), `data-testid="trial-banner"`, with the time in `data-testid="trial-time-left"` and the
  link in `data-testid="trial-subscribe"`. The link opens the subscribe address in the
  same tab. The strip is a labelled region and is **not** a live region, so a screen
  reader is not interrupted every minute; the time carries an accessible label in words
  ("23 hours 59 minutes left"). It uses the `warn` tokens while running and the neutral
  ones once ended; it does not use the human family (P15).

`Shell` in `web/src/App.tsx` renders the strip as the first child of `<main>`, before
the routes, and only when the document has arrived and its `trial` is an object. **A
document whose `trial` is `null` and a document with no `trial` key at all are the same
case: no banner** (P24, F8). In that case the rendered markup is what it is today,
element for element. The strip is in the page's normal flow and is not pinned: on a long
page it scrolls away with the top of the page, as the page heading does. The strip
cancels `<main>`'s own padding so that it runs edge to edge of the column; the sidebar,
the top bar and their heights are not edited (P17).

**What an open tab does (F11, P22).** The countdown itself is always right for the end
time the tab holds, because it is recomputed from the clock on every tick and not
counted down. What goes stale is the document. A tab that is open when the operator
restarts the workspace with a different trial end, or with none after the person
subscribes, keeps the banner it had until the person reloads or comes back to the tab,
at which point the query library re-reads the document. This change adds no polling.
It does pin that re-read with a test, since the banner now depends on a library default.

**The tests.** Backend: settings parsing and refusals in `tests/test_config.py`; the
document's two shapes and the rewritten key pin in `tests/test_api_workspace.py`; one
test in `tests/test_operator_usage.py` that the usage read answers with a trial end set.
Frontend unit: the pure function's boundaries; the component in its three states; the
shell with a document that has no `trial` key; and the workspace query re-reading when
the window regains focus. End to end: a new server configured as a workspace on trial
and `web/e2e/trial-banner.spec.ts`, which also asserts where the strip sits, plus one
assertion in `web/e2e/shell.spec.ts` that the ordinary server has no banner.

**The documents**, at closeout: see "Durable content".

## What does not change

- The freeze. `GW_READ_ONLY`, its predicate, its open list and its refusal copy are not
  edited, and `tests/test_read_only_mode.py`, `tests/test_mcp_read_only.py` and
  `tests/test_one_read_only_predicate.py` are not edited.
- The filter compiler, the schema engine and the access model (P14).
- The database. No migration, no table, no stored value.
- The MCP surface: no tool, no instruction text, no capability entry (P10).
- The sign-in page (P9).
- What the browser shows for a refused edit. docs/DEPLOYMENT.md section 6a still records
  that as a bare 409.
- Any self-hosted workspace's behaviour, except that its workspace document carries
  `"trial": null`.
- Every committed visual baseline (P13).

## Constraints

- With `GW_TRIAL_ENDS_AT` unset or blank, `document.querySelector('[data-testid="trial-banner"]')`
  is null on every page, and the shell's markup is unchanged.
- **An assertion that the banner is absent is made only after the workspace document is
  known to have arrived** (P21, F3): the test first waits for
  `workspace-people-agents`, which is drawn from the same document. A presence assertion
  is a retrying locator assertion, never a count read once.
- The clock hook is imported by `TrialBanner.tsx` and by nothing else (F9).
- With a trial set, a page shorter than the window does not scroll, and the signed-in
  block stays inside the window (F2). This is a Playwright assertion, because layout is
  never proven in jsdom.
- Nothing on the request path can fail because of the trial end's value (F4).
- The banner renders no clock time and no date, only a length of time, so no time zone
  or daylight-saving rule can change what it says (F16).
- The banner modules read no role, scope or access level, unless Q-B is answered
  "administrators only" (F17).
- `GW_SUBSCRIBE_URL` is treated as public to every credential on the workspace. The
  operator never puts a token or any other secret in it (F6).
- No business logic in the route or in the component's render body. The service composes
  the `trial` value; the pure function decides the state.
- The server reads no clock for this feature.
- The subscribe address appears on the wire only inside a non-null `trial`.
- No call to `levelAllows` in the new modules (P15).
- Banner text is asserted with locators, never by screenshot (AGENTS.md, Traps).
- No dependency is added.

## Checklist

1. **Done.** Write the backend assertions and run them against the unfixed tree, recording how each
   fails: the setting is read; blank and whitespace alone are unset; a value with a
   trailing space or newline is accepted; each of no offset, a bare date, `1790000000`,
   `1790000000.5`, `-1`, a value wrapped in quotes, `9999-12-31T23:59:59-14:00` and
   `0001-01-01T00:00:00+14:00` refuses startup, and each refusal names `GW_TRIAL_ENDS_AT`
   and contains the example `2026-10-09T15:00:00Z`; a past time is accepted; an offset
   time is held as its UTC equal; `.env.example` ships the name blank; the document has
   `trial: null` unset and the object set, with `subscribe_url` null when that is unset;
   the key set is exactly five; `GET /api/v1/usage` answers 200 with the operator
   credential when a trial end is set. Every test name contains `trial`, which AC1 reads.
   **Measured on the unfixed tree:** 30 failed, 2 passed. Twelve failed with
   `AttributeError: 'Settings' object has no attribute 'trial_ends_at'`, nine with
   `DID NOT RAISE ConfigError`, five on a document with no `trial` key, the two key pins
   on the missing fifth key, one on `.env.example`, and one on a log line that was never
   written. **The two that passed are fences and are not counted:** the usage read with a
   trial end set answers 200 on the unfixed tree because the setting is ignored there,
   and it can fail only if building the `trial` value raises (the mutation that converts
   per request fails at startup instead, on the two out-of-range assertions); and "no
   log line when unset" cannot fail on a tree that never writes the line.
2. **Done.** Add the trial server to `web/playwright.config.ts` and `web/e2e/constants.ts`
   (port 8936, its own data directory, `GW_AUTH_MODE=standalone`, embedding off,
   `GW_TRIAL_ENDS_AT=2030-01-02T00:00:00Z`,
   `GW_SUBSCRIBE_URL=https://subscribe.example.com/e2e`, and, as the emailed-code server
   has them and for the reason in P12, `GW_BOOTSTRAP_ADMIN_EMAIL`,
   `GW_BOOTSTRAP_ADMIN_PASSWORD`, `GW_COOKIE_SECURE=false`, `GW_BASE_URL`,
   `GW_WORKSPACE_NAME` and `GW_LOGIN_IP_MAX_ATTEMPTS=1000`), write
   `web/e2e/trial-banner.spec.ts` and the `shell.spec.ts` assertion, and run them against
   the unfixed tree, recording how each fails. **First** confirm what P11 left open: with
   the clock pinned before navigation, the existing password sign-in reaches
   `current-principal` on this server. If it does not, stop and report; the fallback
   (pinning the clock after sign-in) is a deviation to record, not to choose silently.
   The spec's scenarios: with the clock at `2030-01-01T00:01:00Z` the time reads `23:59`
   and the link's `href` is the configured address; with it at `2030-01-01T23:59:30Z` it
   reads `00:01`; after moving it to `2030-01-02T00:00:00Z` the banner reads the ended
   copy with no time element; the banner is present on `/setup` as well as `/`.
   **The three clock readings are one scenario on one page, with no navigation and no
   reload between them** (F10): that is the only thing that proves the countdown moves
   by itself. One more scenario asserts where the strip sits (F2), at 1280 by 800 and at
   800 by 800, on `/setup`, a page shorter than the window: the strip's top edge is at
   the top of `<main>`; `document.documentElement.scrollHeight` equals
   `window.innerHeight`; and at 1280 the bottom edge of `current-principal` is no lower
   than `window.innerHeight`. The `shell.spec.ts` assertion follows the constraint from
   F3: wait for `workspace-people-agents`, read `GET /api/v1/workspace` in the same run
   and assert its `trial` is `null`, then assert the banner's count is 0.
   **Measured on the unfixed tree:** all five scenarios failed. The four on the trial
   server each passed the password sign-in with the clock already pinned and then failed
   at `trial-time-left`, element not found, which settles what P11 left open. The
   `shell.spec.ts` assertion failed at `toHaveProperty("trial", null)`, the document
   having no such key. See D2 and D3 for the two things this step could not do as
   written.
3. **Done.** Add the setting, its validator and the startup log line to `src/glosswork/config.py`
   and `src/glosswork/app.py`, and the entry to `.env.example`. Step 1's settings
   assertions pass.
4. **Done.** Add the `trial` value to `WorkspaceService.get_workspace` and to `workspace_doc`, and
   rewrite the four-key pin as a five-key pin in both places it appears. Step 1's document
   assertions pass.
5. **Done.** Write the unit tests for the pure function and run them failing, then add
   `web/src/app/trialCountdown.ts`: `24:00` at exactly a day, `23:59` one minute in, `00:01`
   at 30 seconds left, `ended` at zero and after, more than 99 hours unpadded, an
   unparseable end time yields nothing.
   **Measured** against a module that exported the names and returned nothing, so that
   the failures were assertions and not a missing import: 8 of 9 failed. The ninth, "an
   unparseable end time yields nothing", passes against a function that always yields
   nothing, so it was first measured by the implementation, which without its check
   renders `NaN:NaN`.
6. **Done.** Add the `trial` field to `WorkspaceDoc`, the clock hook, `TrialBanner.tsx` and its
   component tests, and render it from `Shell`. Add two tests beside them: `App` given a
   workspace document with no `trial` key renders the shell and no banner (P24), and the
   workspace query is fetched a second time when the window regains focus (P22). The
   existing fixtures in `web/src/App.test.tsx` are left without the key on purpose; they
   are the first of those two tests' evidence. Step 2's specs pass.
7. **Done.** Mutations, each built before its failure is believed (AGENTS.md, Traps), each
   reverted: render the banner unconditionally (the `shell.spec.ts` absence assertion and
   the unset component test must fail); round down instead of up (the `00:01` assertions
   must fail); make `ended` never fire (the ended scenario must fail); send
   `subscribe_url` at the top level of the document (the five-key pin must fail). Added
   by the adversarial pass: render the banner whenever the document has arrived, whether
   or not it has a trial (the `shell.spec.ts` absence assertion must fail, F3); remove
   the timer from the clock hook (the one-page clock scenario must fail at its second
   reading, F10); move the strip out of `<main>` to above the shell (the placement
   scenario must fail on scroll height, F2); replace the `float()` refusal with a
   digits-only check (the `1790000000.5` and `-1` assertions must fail, F5); format the
   end time per request instead of at startup (the two out-of-range startup assertions
   must fail, F4).
   **Measured, all nine, each on a tree that built (`npm run build` exit 0, or the module
   imported), each reverted with `git checkout`:**

   | Mutation | What failed |
   | --- | --- |
   | Banner rendered unconditionally | `shell.spec.ts` absence: count 1, expected 0. Both unset tests in `App.test.tsx` |
   | Rounded down | `00:01` in the spec read `00:00`; three unit tests |
   | `ended` never fires | The spec read "Your trial has 00:00 left." for "Trial ended."; five unit tests |
   | `subscribe_url` at the top level | The five-key pin, in both places |
   | Banner for any arrived document | `shell.spec.ts` absence: count 1, expected 0. Both unset tests in `App.test.tsx` |
   | No timer in the clock hook | The one-page scenario at its second reading: `23:59` for `00:01`; two unit tests |
   | Strip above the shell | Both placement scenarios: scroll height 836 for 800, the strip outside `<main>`, and at 1280 the signed-in block's bottom edge at 835.9 |
   | Digits-only check | `1790000000.5` and `-1` did not refuse startup |
   | UTC conversion per request | Both out-of-range values did not refuse startup |
8. **Done.** Run the whole suite once: the Accept block, in order. Results are under each clause below.

## Accept

Each is run from the repository root and its exit code read directly, not through a pipe.

**The build's own run, 2026-10-08, at `95f527e`, once, in order.** This is the builder's
record and not the verification, which a different session runs.

| Clause | Result |
| --- | --- |
| AC1 | Exit 0, 112 passed. The collection exits 0 and lists 31 tests |
| AC2 | Exit 0: 2,254 passed, 3 xfailed |
| AC3 | `ruff check` exit 0; `ruff format --check` exit 0 |
| AC4 | Exit 0 |
| AC5 | lint exit 0; typecheck exit 0; test exit 0, 1,107 passed in 104 files |
| AC6 | Exit 0, 82 passed, no `flaky` line; the listing exits 0 and names the countdown scenario, the `/setup` scenario and the placement scenario at both sizes |
| AC7 | Exit 0, 49 passed; `git status --porcelain` over the baselines prints nothing |
| AC8 | `git ls-files --error-unmatch` exit 0; `git diff --quiet` against merge base `85bea5a` exit 0 |
| AC9 | Passed inside AC6: "a workspace with no trial end set shows no trial banner" |
| AC10 | Prints nothing |
| AC11 | Prints 0, exit 1, the expected answer |
| AC12 | Exit 0, 107 passed |

- **AC1.** `uv run pytest -q tests/test_config.py tests/test_api_workspace.py tests/test_operator_usage.py`
  exits 0. Then
  `uv run pytest -q --collect-only -k trial tests/test_config.py tests/test_api_workspace.py tests/test_operator_usage.py`
  exits 0 and its output lists a test for each clause of step 1. (Measured on the unfixed
  tree: that second command exits 5, pytest's code for "nothing collected".)
- **AC2.** `uv run pytest -q` exits 0.
- **AC3.** `uv run ruff check .` exits 0, and `uv run ruff format --check .` exits 0, read
  separately.
- **AC4.** `uv run mypy src` exits 0.
- **AC5.** `npm --prefix web run lint` exits 0; `npm --prefix web run typecheck` exits 0;
  `npm --prefix web run test` exits 0.
- **AC6.** `npm --prefix web run e2e -- --project=e2e` exits 0, with its output kept
  in a file; that file's closing tally has no `flaky` line; and
  `npm --prefix web run e2e -- --project=e2e --list trial-banner.spec.ts` exits 0 and
  lists the countdown scenario, the placement scenario and the `/setup` scenario.
- **AC7.** `npm --prefix web run e2e -- --project=visual` exits 0, and afterwards
  `git status --porcelain web/e2e/ui-visual.spec.ts-snapshots` prints nothing.
- **AC8.** A fence, not coverage. First
  `git ls-files --error-unmatch` over the same twelve paths exits 0, because `git diff`
  over a path that does not exist exits 0 and would pass for the wrong reason (measured).
  Then, against the merge base and not the tip of `main`, so that another change merging
  meanwhile cannot turn it red (measured, F13):
  `git diff --quiet "$(git merge-base origin/main HEAD)" -- src/glosswork/compiler.py src/glosswork/filters.py src/glosswork/sqlexpr.py src/glosswork/fieldtypes.py src/glosswork/scopes.py src/glosswork/services/schema.py src/glosswork/services/access.py src/glosswork/migrations.py src/glosswork/mcp_server tests/test_read_only_mode.py tests/test_mcp_read_only.py tests/test_one_read_only_predicate.py`
  exits 0.
- **AC9.** The self-host case, against a real server rather than a test client: the
  `shell.spec.ts` assertion in AC6 that `page.getByTestId("trial-banner")` has count 0
  on the server that sets no trial end, made after `workspace-people-agents` is visible
  and after the same run has read `trial: null` from `GET /api/v1/workspace` (F3). Step
  7's first mutation and its fifth are the record that it can fail.
- **AC10.** `git diff --text "$(git merge-base origin/main HEAD)" -- uv.lock web/package-lock.json`
  prints nothing.
- **AC11.** A fence, not coverage: `grep -c useTrialClock web/src/App.tsx` prints 0
  (and so exits 1, which is the expected answer here).
- **AC12.** `uv run pytest -q -m structural` exits 0, named on its own because this plan
  file broke it once (P26).

## Baseline repaint

Expected: 0 of 41 (P13). The visual server sets no trial end, and the shell's markup is
unchanged when `trial` is null. No baseline shows the banner, so its look is proven by
nothing here: the placement scenario proves where it sits, and locator assertions prove
what it says. Any repaint is a finding and stops the build.

Actual: 0 of 41. The visual project passed, 49 of 49, and no baseline file changed.

## Questions for the maintainer

None of these is decided by this plan. Each has a recommendation, and the build uses the
answer given at approval.

**Answered by the maintainer on 2026-10-08, with the approval of this plan as written at
`85ba862` ("PROD-04 approve"). The build uses these exactly.**

- **Q-A:** "Your trial has 23:59 left." then a "Subscribe" link; after the end, "Trial
  ended." with the same link; a screen reader says "23 hours 59 minutes left", with
  "1 hour" and "1 minute" in the singular.
- **Q-B:** everyone signed in.
- **Q-C:** a trial end set with no subscribe address shows the countdown with no link.
- **Q-D:** the customer's own device clock runs the countdown.
- **Q-E:** the strip sits at the head of the main column.
- **Q-F:** every credential on a trial workspace may read the subscribe address, under
  the written rule that the address never holds a secret.

**Q-A. The words.** Drafts, following docs/DESIGN.md section 5 (sentences, no exclamation
marks, a control says what happens):

| State | Recommended draft | Alternative |
| --- | --- | --- |
| Counting down | "Your trial has **23:59** left." then the link | "Trial ends in **23:59**." then the link |
| Ended | "Trial ended." then the link | "Trial ended. Subscribe to keep making changes." |
| The link | "Subscribe" | "Subscribe now" |
| What a screen reader says for the time | "23 hours 59 minutes left", with "1 hour" and "1 minute" in the singular | "23:59 left", read as digits |

Why the first row's recommendation: "ends in 23:59" can be read as a time of day, one
minute before midnight, and that is a wrong promise about when the trial ends. "has 23:59
left" cannot. PLAN 6.1 gives the ended text as "Trial ended", which the recommendation
keeps exactly; the alternative says more but implies the workspace is already read-only,
which judgment area 3 explains is not yet true for some seconds.

**Q-B. Who sees it.** Recommended: everyone signed in. Alternative: administrators only.
A member cannot subscribe on the company's behalf in every company, but hiding the
countdown from them means the freeze arrives with no warning for most of the people it
stops.

**Q-C. A trial end set with no subscribe address.** Recommended: the banner shows the
time with no link. Alternative: refuse startup. Refusing would take a customer's
workspace down over a missing link, which is worse than a banner without one. P6 is why
this is not hypothetical today.

**Q-D. The browser's clock decides the countdown** (judgment area 2, sharpened by F14).
Recommended: accept it, knowing what it costs. A person whose device clock is behind by
ten minutes sees "00:10 left" at the moment the workspace freezes, and their next save is
refused. A person whose clock is ahead sees "Trial ended" early on a workspace that still
works. The freeze itself is never affected. How many devices have a clock wrong by
minutes was not measured; the common case is a clock set by hand after a wrong time zone,
which is wrong by whole hours.
Alternative: the workspace also sends its own time and the browser corrects for the
difference. The countdown is then right on any device. It costs the exact end-to-end
scenarios (`23:59`, `00:01`, and crossing the end on an open page), which fall back to
unit tests, and it is a different mechanism from the one this plan was attacked on, so
choosing it sends the plan round for a second adversarial pass before it is built.

**Q-E. Where the strip sits** (judgment area 8, F2). Recommended: at the head of the main
column, to the right of the sidebar on a wide window and under the top bar on a narrow
one. It was built and measured: nothing else on the page moves. Alternative: across the
whole window, above the sidebar too, as the first draft had it. That reads more like a
notice about the whole workspace, but as measured it pushes every page 35 pixels past
the window and the sign-out control off the bottom. Making it fit means changing how
the shell and the sidebar get their height, which was not designed or built in this
pass, so choosing it sends the plan back for that design before it is built.

**Q-F. Every credential on a trial workspace can read the subscribe address** (judgment
area 4, F6). Recommended: accept it, with the written rule that the address never carries
a secret. This is new: today a `read` token is never given the address, and nobody is
given it before the freeze. It is in line with Q64, under which a subscribe link only
lets someone pay. Alternative: leave the address out of the document for a `read` token
and for a member's session. That makes the document differ by caller, which it does not
today, and hides the link from the people Q-B recommends showing the banner to.

## Adversarial pass

Run once, on 2026-10-08, by a session that did not write this plan, against the file at
`a8604c8`. Every finding below was built before it was recorded: on a throwaway copy of
the repository with the plan's design implemented the way the plan read, four real
servers on ports 8941 to 8944, and Chromium driven by this repository's
`playwright-core` 1.62.1. The throwaway build was never committed and is not the build.
The measurements are premises P16 to P26 and the amended P11 and P12.

| | Finding | Disposition |
| --- | --- | --- |
| F1 | **This plan file turned a pipeline guard red.** It cited the new decision by a number with no heading yet, and `test_every_cited_design_decision_exists` failed on a clean checkout (P26). AC2 could not have passed before closeout. | Fixed in place: the citation is written in words. AC12 names the structural run. |
| F2 | **The strip, placed above the shell as drafted, breaks the page's height.** Every page became 35 pixels taller than the window and the sign-out control went below the fold (P17). No Accept criterion could see it: jsdom has no layout and no baseline shows the banner. | Amended: the strip moves to the head of the main column, measured clean at three window sizes; a placement scenario and a mutation are added; the choice goes to the maintainer as Q-E. |
| F3 | **The self-host proof could pass on a workspace about to show a banner.** With the workspace read 800 ms slow, the banner's count was 0 when the signed-in block appeared in 8 sign-ins of 8, on a server with a trial (P21). | Amended: absence is asserted only after the document is known to have arrived and has been read as `trial: null` in the same run. A mutation that draws the banner for any arrived document is added. |
| F4 | **An end time at the edge of the calendar starts a workspace that then fails.** `/readyz` answered 200 while the workspace document and the operator's usage read both answered 500 (P18, P19). | Amended: the value is converted to UTC and checked at startup, and nothing on the request path can raise. Two startup refusals, a usage test and a mutation are added. |
| F5 | **"A bare number is refused" was satisfiable by a check that lets numbers through.** A digits-only check passed `1790000000.5` and `-1`; and the refusal for a missing offset gave no example, which the plan promised (P20). | Amended: anything `float()` accepts is refused, whitespace is trimmed, and every refusal is one sentence with the example. The named values are in step 1 and a mutation is added. |
| F6 | **The subscribe address at `read` scope is a new disclosure, and the plan said it was an existing one.** A `read` token's write is refused for scope before the freeze is consulted, so it never sees the address today (P16). | Judgment area 4 corrected. A constraint and an operator rule are added. Put to the maintainer as Q-F, recommended accept under Q64. |
| F7 | **The two module names the plan chose do not build on macOS.** They differed only in the case of one letter (P23). On Linux, where CI runs, the same tree would have resolved differently. | Amended: the pure module is `trialCountdown.ts`. |
| F8 | **"Not null" is not "an object".** A workspace document with no `trial` key crashed the whole shell, in seven existing tests, and the type check did not notice the fixture (P24). | Amended: `null` and absent are one case. A test is added, and the existing fixtures are left as its evidence. |
| F9 | **The plan did not say where the clock hook is called.** Called from the shell, it re-rendered the shell and every page under it once a second (P25). | Amended: the hook is called only from the banner. A constraint and the AC11 fence are added. |
| F10 | **A countdown that never moves could pass, if the spec reloaded between clock readings.** Measured the other way: on one open page the banner followed the clock through all three readings, in three time zones, after a real sign-in. That also establishes what P11 left open. | Amended: the three readings are one scenario with no navigation. A mutation that removes the timer is added. P11 updated. |
| F11 | **A tab left open holds the document it loaded.** After the workspace restarted with no trial end, the banner stayed through three idle seconds and an in-app navigation, and went on return to the tab (P22). The same applies to a trial end that arrives after a person has signed in. | Accepted and written down, with no polling added. The re-read on focus is pinned by a test, since the banner now depends on it. Goes to docs/DEPLOYMENT.md at closeout. |
| F12 | **The trial test server as listed has nobody to sign in as.** Sign-in answered 401 (P12). | Amended: step 2 names the full environment. |
| F13 | **Four Accept clauses were weaker than they read.** AC1 and AC6 each ended in a clause that is not a command. AC8 and AC10 compared against the tip of `main`: on real history, a branch that touched nothing failed AC8's form with exit 1 once `main` moved, and the merge-base form exited 0. A path that does not exist passed AC8 with exit 0. | Rewritten as commands, each run once here. |
| F14 | **The browser-clock choice was argued more strongly than the facts allow**, and its harmful direction was not named: a clock that is behind promises time while saves are refused. | Design unchanged. Judgment area 2 and Q-D now state both directions and the true cost of the alternative. Not measured: how common a wrong device clock is. |
| F15 | **The gap after "Trial ended" is not "some seconds", and the frozen UI is not silent.** The gap is the operator's schedule plus a restart in which requests fail; its length is the control plane's and was not measured. On a frozen server, creating an object type in the browser showed the API's full sentence, subscribe address included, so "the browser shows a bare 409" is not true of every screen. | Judgment area 3 corrected. The closeout edit to docs/DEPLOYMENT.md section 6a re-measures what a refused edit shows before it repeats that paragraph. The recommended ended text stays "Trial ended.". |
| F16 | **Attacked and held.** Time zones and daylight saving: the banner is a length of time computed from two instants, and read the same in three zones. A past end time: "Trial ended" from the first paint. More than 99 hours: `100:00`, and `8760:00` for a mistyped year, which is odd and not wrong. The filter compiler, the schema engine and the access model: the throwaway build touched `config.py`, `services/workspace.py`, `envelopes.py` and the browser only; the one line in `mcp_server` that names the workspace service is the freeze predicate; AC8's twelve paths all exist. The whole backend suite, run once on the throwaway build, failed in four places and passed 2,220: the three pins this plan already names (the `.env.example` test and the two four-key pins) and F1. The control plane and the kit do not read the workspace document. | No change, beyond the constraint that the banner never renders a clock time. |
| F17 | **Q-B had no test either way**: the spec signs in as an administrator only. | Amended: answered "everyone", the banner modules read no role, by constraint. Answered "administrators only", the build adds component tests for all three roles before the component, and says so in Deviations. |

**Did the pass change the design?** No different mechanism, no new component, and no new
entry on the full-lane list. One visible thing moved: the strip's place on the page (F2),
which is the maintainer's to confirm under Q-E. Two answers would change the design and
send the plan back before it is built: the alternative under Q-D, and the alternative
under Q-E.

## Deviations from the approved plan

Recorded as each happened, during the build of 2026-10-08.

- **D1. The time's words reach a screen reader as a second, visually hidden sentence, not
  as a label on the digits.** "What changes" says the time carries an accessible label in
  words. Measured in Chromium's accessibility tree before the component was written: for
  a paragraph "Your trial has 23:59 left." whose digits sit in a `span` with
  `aria-label="23 hours 59 minutes left"`, the tree reads `paragraph: Your trial has 23:59
  left.` The label is not there, because a plain `span` takes no name. Had it been
  honoured, the sentence would also have said "left" twice. So the strip renders the
  visible sentence hidden from assistive technology and, beside it, the same sentence
  with the time in words, visually hidden: the tree then reads `paragraph: Your trial has
  23 hours 59 minutes left.` The approved words are unchanged. The Playwright spec
  asserts the accessibility tree itself, at `23:59`, at `00:01` and after the end. One
  more test id exists than the plan lists: `trial-message` is the visible sentence, and
  `trial-message-spoken` the spoken one.
- **D2. The placement scenario runs on `/people`, not on `/setup`.** Step 2 calls `/setup`
  "a page shorter than the window". It is not, for an administrator: measured on the
  trial server at 1280 by 800, `/setup` is 1570 pixels tall without the strip and 1606
  with it, and at 800 by 800 it is 1632 and 1668. So "scroll height equals the window's
  height" fails there with the strip in the right place. `/`, `/people`, `/inbox`,
  `/activity`, `/search` and `/schema` each measured 800 with and without the strip at
  both sizes, with the strip's top edge at the top of `<main>` (0 wide, 62 narrow), which
  are P17's numbers. The scenario uses `/people`, a fixed route that does not depend on
  which object types exist, and first asserts that the page is no taller than the window
  with the strip hidden, so a page that grows later fails with that sentence and not with
  a wrong one about the strip. The separate scenario that the banner is present on
  `/setup` is as planned.
- **D3. The narrow placement scenario signs in at 1280 and then resizes to 800.** Below
  the breakpoint the signed-in block that the shared sign-in helper waits for is inside a
  closed menu, so a sign-in at 800 fails at the helper. Every existing narrow scenario
  does the same.
- **D4. The copy constant is pinned to the approved words by one unit test, and the
  Playwright spec writes the sentences out.** Judgment area 6 says the component and its
  tests take the copy from one constant so a wording change is one line. The component
  tests do. But a test that imports the constant it checks cannot fail when the constant
  changes, and these are words the maintainer approved, so `trialCountdown.test.ts` has
  one test that writes them out and `e2e/trial-banner.spec.ts` asserts the sentences as a
  customer reads them. A wording change is therefore the constant, that one test, and the
  spec.

Three things the build wrote that the approved answers do not settle. None changes an
approved word, and each is the maintainer's to change:

- **A zero is spoken as written.** With 30 seconds left a screen reader is given "Your
  trial has 0 hours 1 minute left.", and at exactly a day "24 hours 0 minutes left". The
  answer to Q-A gives the form and the two singulars and says nothing about dropping a
  zero, so nothing is dropped.
- **The strip's accessible name is "Trial".** The plan calls it a labelled region and
  gives no label.
- **The startup log line `trial_end_set` and the `.env.example` comment** are operator
  text, written here; the refusal sentence is "must be an ISO 8601 date and time with a
  UTC offset or Z, such as 2026-10-09T15:00:00Z", after the variable's name.

## Durable content moved out of this plan

Nothing has moved yet. At closeout:

- **PRD.md**, a new requirement after FR-P11, proposed text: "With `GW_TRIAL_ENDS_AT` set,
  the workspace document reports the trial's end time and the subscribe address, and the
  web UI shows the time left and a subscribe link on every signed-in page, then that the
  trial has ended. Unset means no banner and no other change", closed at closeout with
  the new decision's number in parentheses. The number is not written in this file,
  because a guard reads it as a citation of a decision that has no heading yet (P26).
- **docs/DESIGN_DECISIONS.md**, a new decision, proposed text: "A trial is a time the
  operator configures, and the browser counts it down. The workspace is told when its
  trial ends by one optional setting and reports it, with the subscribe address, in the
  workspace document. It stores nothing and enforces nothing: freezing is DD-38's, and
  the two are set independently. **Why.** The same image serves a hosted trial and a
  self-hosted deployment that has no trial, and a workspace that enforced its own trial
  would be a second freeze predicate. **Held by.** `tests/test_api_workspace.py`,
  `tests/test_config.py`, and the trial banner's Playwright spec." DD-28's first sentence
  gains "and, where a trial end time is configured, the trial".
- **docs/DESIGN.md**: a component entry in section 7 for the trial banner (tokens, the
  approved copy, the rounding rule, not a live region), one sentence in 8.1 that it sits
  at the head of the main column (or wherever Q-E is answered) and is absent without a
  trial, and the time-left form in section 5's formatting paragraph.
- **docs/DEPLOYMENT.md** section 6a: the variable's row, the accepted forms, that it is
  read at startup, that the banner follows the end time and the freeze follows
  `GW_READ_ONLY`, and that the countdown uses the reader's own clock, with both
  directions of a wrong clock stated (Q-D). Also, from the adversarial pass: every
  credential on the workspace can read the subscribe address once a trial end is set, so
  it never carries a secret (F6); a tab that is already open shows a changed or removed
  trial end only after a reload or a return to the tab (F11); and an end time far in the
  future is shown as it is, in hours (F16). The paragraph "The browser does not explain
  the refusal yet" is re-measured before it is repeated, because at least one screen
  already shows the refusal's full sentence (F15), and is amended to say a workspace on
  trial now shows that the trial ended.
- **`.env.example`**: the entry, in the commit that adds the setting.
- **CHANGELOG.md** is not edited by this change: CONTRIBUTING, "Releases", says it is
  written once per release. The release that carries this change names
  `GW_TRIAL_ENDS_AT` among the variables a deployment should check its environment for.
