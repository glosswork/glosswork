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
   set the time it asserts on without a test-only server setting. The adversarial pass
   should attack this choice.

3. **"Trial ended" follows the end time, not the freeze.** The banner switches when the
   end time passes. The workspace freezes when the operator restarts it with
   `GW_READ_ONLY=true`, which is the same moment by intent and some seconds later in
   practice, plus the restart (P5, P6). In that gap the banner says the trial has ended
   and an edit still saves. The banner therefore claims only that the trial ended. It does
   not say the workspace is read-only, because for those seconds that would be false.
   Considered and declined: also sending whether the workspace is read-only and switching
   on either. It would make a self-hosted read-only workspace's document change, and it
   solves a gap of seconds.

4. **A new key on the workspace document, visible at `read` scope.** `trial` is `null`
   unless the end time is set, and otherwise an object with the end time and the subscribe
   address. Every signed-in person and every `read` token can therefore read the subscribe
   address during a trial. That address is already given to every caller whose write is
   refused (P5), and the hosting plan accepts that a subscribe link only lets someone pay
   (PLAN Q64). On a self-hosted workspace the only difference is one key whose value is
   `null`, as `mcp_url` is when `GW_BASE_URL` is unset.

5. **Everyone signed in sees the banner, not only administrators.** A trial that ends
   freezes everyone's work, and the link only lets someone pay. This is a product choice
   and is put to the maintainer under "Questions for the maintainer".

6. **The words on the banner.** Drafts only, under "Questions for the maintainer". The
   build uses whatever the maintainer approves there. The component and its tests take
   the copy from one constant so a wording change is one line.

7. **A new design decision and a new requirement.** Proposed text is under "Durable
   content".

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
  **Not established:** that the real application signs in and renders under a pinned
  clock. Checklist step 2 establishes it before anything is built on it.

- **P12. The existing hosted test server cannot carry this spec.** Read at
  `web/e2e/email-code.spec.ts:7-10`: its first administrator is sent at most four codes a
  run, retries included, against a cap of five an hour, and password sign-in is off there.
  A banner spec signing in on that server would spend the fifth and sixth. So the spec
  gets its own server, with password sign-in. Ports 8931 to 8935 are taken
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

## What changes

**The setting.** `Settings.trial_ends_at`, from `GW_TRIAL_ENDS_AT`. Optional. Blank is
unset. Anything else must be an ISO 8601 date and time with a UTC offset or `Z`, such as
`2026-10-09T15:00:00Z`; a value with no offset, a bare date or a bare number refuses
startup, naming the variable and giving that example. A time already in the past is
legal: a frozen workspace is restarted with it. It may be set without `GW_SUBSCRIBE_URL`,
and then the banner has no link. `.env.example` ships it blank with a comment. At startup,
when it is set, one `info` line `trial_end_set` carries the end time and whether a
subscribe address is set, as `read_only_mode` does for the freeze.

**The service.** `WorkspaceService.get_workspace` returns a `trial` value on the
document: `None` when the setting is unset, and otherwise the end time and
`settings.subscribe_url`. No clock is read on the server, and no database read is added.

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

- `web/src/app/trialBanner.ts`: a pure function from the end time and a millisecond clock
  reading to either `{ kind: "running", timeLeft }` or `{ kind: "ended" }`, and the copy
  constants. `timeLeft` is hours and minutes left, each at least two digits, **rounded up
  to the minute**, so a trial with 30 seconds left reads `00:01` and never `00:00`, and a
  full day reads `24:00`. At or after the end time the state is `ended`. An end time the
  browser cannot parse yields no banner.
- A hook that returns the current time and re-renders once a second while a trial is
  set, and sets no timer otherwise.
- `web/src/app/TrialBanner.tsx`: one full-width strip above the sidebar or top bar,
  `data-testid="trial-banner"`, with the time in `data-testid="trial-time-left"` and the
  link in `data-testid="trial-subscribe"`. The link opens the subscribe address in the
  same tab. The strip is a labelled region and is **not** a live region, so a screen
  reader is not interrupted every minute; the time carries an accessible label in words
  ("23 hours 59 minutes left"). It uses the `warn` tokens while running and the neutral
  ones once ended; it does not use the human family (P15).

`Shell` in `web/src/App.tsx` renders the strip only when `workspace.trial` is not null.
When it is null the rendered markup is what it is today, element for element.

**The tests.** Backend: settings parsing and refusals in `tests/test_config.py`; the
document's two shapes and the rewritten key pin in `tests/test_api_workspace.py`. Frontend
unit: the pure function's boundaries, and the component in its three states. End to end:
a new server configured as a workspace on trial and `web/e2e/trial-banner.spec.ts`, plus
one assertion in `web/e2e/shell.spec.ts` that the ordinary server has no banner.

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
- No business logic in the route or in the component's render body. The service composes
  the `trial` value; the pure function decides the state.
- The server reads no clock for this feature.
- The subscribe address appears on the wire only inside a non-null `trial`.
- No call to `levelAllows` in the new modules (P15).
- Banner text is asserted with locators, never by screenshot (AGENTS.md, Traps).
- No dependency is added.

## Checklist

1. Write the backend assertions and run them against the unfixed tree, recording how each
   fails: the setting is read; blank is unset; no offset, a bare date and a bare number
   each refuse startup naming `GW_TRIAL_ENDS_AT`; a past time is accepted; `.env.example`
   ships the name blank; the document has `trial: null` unset and the object set, with
   `subscribe_url` null when that is unset; the key set is exactly five.
2. Add the trial server to `web/playwright.config.ts` and `web/e2e/constants.ts`
   (port 8936, its own data directory, `GW_AUTH_MODE=standalone`, embedding off,
   `GW_TRIAL_ENDS_AT=2030-01-02T00:00:00Z`,
   `GW_SUBSCRIBE_URL=https://subscribe.example.com/e2e`), write
   `web/e2e/trial-banner.spec.ts` and the `shell.spec.ts` assertion, and run them against
   the unfixed tree, recording how each fails. **First** confirm what P11 left open: with
   the clock pinned before navigation, the existing password sign-in reaches
   `current-principal` on this server. If it does not, stop and report; the fallback
   (pinning the clock after sign-in) is a deviation to record, not to choose silently.
   The spec's scenarios: with the clock at `2030-01-01T00:01:00Z` the time reads `23:59`
   and the link's `href` is the configured address; with it at `2030-01-01T23:59:30Z` it
   reads `00:01`; after moving it to `2030-01-02T00:00:00Z` the banner reads the ended
   copy with no time element; the banner is present on `/setup` as well as `/`.
3. Add the setting, its validator and the startup log line to `src/glosswork/config.py`
   and `src/glosswork/app.py`, and the entry to `.env.example`. Step 1's settings
   assertions pass.
4. Add the `trial` value to `WorkspaceService.get_workspace` and to `workspace_doc`, and
   rewrite the four-key pin as a five-key pin in both places it appears. Step 1's document
   assertions pass.
5. Write the unit tests for the pure function and run them failing, then add
   `web/src/app/trialBanner.ts`: `24:00` at exactly a day, `23:59` one minute in, `00:01`
   at 30 seconds left, `ended` at zero and after, more than 99 hours unpadded, an
   unparseable end time yields nothing.
6. Add the `trial` field to `WorkspaceDoc`, the clock hook, `TrialBanner.tsx` and its
   component tests, and render it from `Shell`. Step 2's specs pass.
7. Mutations, each built before its failure is believed (AGENTS.md, Traps), each
   reverted: render the banner unconditionally (the `shell.spec.ts` absence assertion and
   the unset component test must fail); round down instead of up (the `00:01` assertions
   must fail); make `ended` never fire (the ended scenario must fail); send
   `subscribe_url` at the top level of the document (the five-key pin must fail).
8. Run the whole suite once: the Accept block, in order.

## Accept

Each is run from the repository root and its exit code read directly, not through a pipe.

- **AC1.** `uv run pytest -q tests/test_config.py tests/test_api_workspace.py` exits 0,
  and its collected tests include the ones step 1 names.
- **AC2.** `uv run pytest -q` exits 0.
- **AC3.** `uv run ruff check .` exits 0, and `uv run ruff format --check .` exits 0, read
  separately.
- **AC4.** `uv run mypy src` exits 0.
- **AC5.** `npm --prefix web run lint` exits 0; `npm --prefix web run typecheck` exits 0;
  `npm --prefix web run test` exits 0.
- **AC6.** `npm --prefix web run e2e -- --project=e2e` exits 0, with no test reported
  flaky, and its list output names every scenario of `trial-banner.spec.ts`.
- **AC7.** `npm --prefix web run e2e -- --project=visual` exits 0, and afterwards
  `git status --porcelain web/e2e/ui-visual.spec.ts-snapshots` prints nothing.
- **AC8.** A fence, not coverage:
  `git diff --quiet origin/main -- src/glosswork/compiler.py src/glosswork/filters.py src/glosswork/sqlexpr.py src/glosswork/fieldtypes.py src/glosswork/scopes.py src/glosswork/services/schema.py src/glosswork/services/access.py src/glosswork/migrations.py src/glosswork/mcp_server tests/test_read_only_mode.py tests/test_mcp_read_only.py tests/test_one_read_only_predicate.py`
  exits 0.
- **AC9.** The self-host case, against a real server rather than a test client: the
  `shell.spec.ts` assertion in AC6 that `page.getByTestId("trial-banner")` has count 0
  after `current-principal` is visible, on the server that sets no trial end. Step 7's
  first mutation is the record that it can fail.
- **AC10.** `git diff --text origin/main -- uv.lock web/package-lock.json` prints nothing.

## Baseline repaint

Expected: 0 of 41 (P13). The visual server sets no trial end, and the shell's markup is
unchanged when `trial` is null. Any repaint is a finding and stops the build.

Actual: to be recorded at build.

## Questions for the maintainer

None of these is decided by this plan. Each has a recommendation, and the build uses the
answer given at approval.

**Q-A. The words.** Drafts, following docs/DESIGN.md section 5 (sentences, no exclamation
marks, a control says what happens):

| State | Recommended draft | Alternative |
| --- | --- | --- |
| Counting down | "Your trial has **23:59** left." then the link | "Trial ends in **23:59**." then the link |
| Ended | "Trial ended." then the link | "Trial ended. Subscribe to keep making changes." |
| The link | "Subscribe" | "Subscribe now" |

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

**Q-D. The browser's clock decides the countdown** (judgment area 2). Recommended: accept
it. A wrong laptop clock gives a wrong countdown by the same amount; the freeze itself is
unaffected.

## Adversarial pass

Not yet run. A different session runs it against this file and folds findings in here as
F1..Fn.

## Deviations from the approved plan

None yet.

## Durable content moved out of this plan

Nothing has moved yet. At closeout:

- **PRD.md**, a new requirement after FR-P11, proposed text: "With `GW_TRIAL_ENDS_AT` set,
  the workspace document reports the trial's end time and the subscribe address, and the
  web UI shows the time left and a subscribe link on every signed-in page, then that the
  trial has ended. Unset means no banner and no other change (DD-47)."
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
  above the sidebar and the top bar and is absent without a trial, and the time-left form
  in section 5's formatting paragraph.
- **docs/DEPLOYMENT.md** section 6a: the variable's row, the accepted forms, that it is
  read at startup, that the banner follows the end time and the freeze follows
  `GW_READ_ONLY`, and that the countdown uses the reader's own clock. The paragraph "The
  browser does not explain the refusal yet" stays, amended to say a workspace on trial
  now shows that the trial ended.
- **`.env.example`**: the entry, in the commit that adds the setting.
- **CHANGELOG.md** is not edited by this change: CONTRIBUTING, "Releases", says it is
  written once per release. The release that carries this change names
  `GW_TRIAL_ENDS_AT` among the variables a deployment should check its environment for.
