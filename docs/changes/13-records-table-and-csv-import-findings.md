# 13: Seven records-table and CSV import findings from a pilot are fixed

| | |
| --- | --- |
| Issue | [#13](https://github.com/glosswork/glosswork/issues/13), and this one change also closes #14, #15, #16, #17, #18 and #19 |
| Branch | `13-records-table-and-csv-import-findings` |
| Spec | `PRD.md` 6.2 (FR-R5, FR-R8), 6.8 (FR-U1, FR-U3), 6.11 (FR-E1, FR-E2, FR-E4); `docs/DATA_MODEL.md` 4 and 11; `docs/MCP_TOOLS.md` 4, 6 and 8; `docs/DESIGN.md` 7.4, 8.1, 8.2 and 9 |
| Decisions | DD-3, DD-19, DD-20, DD-22, DD-41, DD-43. New entries at closeout take the next unused numbers |
| Requirements | FR-U1, FR-U3, FR-R5, FR-R8, FR-E1, FR-E2 |
| Depends on | Nothing unmerged. The planned change on client-sent timestamps (field editors and the Activity filter) touches `web/src/table-view` and `web/src/filters` too and must not run at the same time. Whichever runs second reads the other's result |

**Status: draft, not approved.** Five design questions are open (Q1 to Q5 below). The checklist
and the Accept block are written for the recommended answer to each. The maintainer's answers
are folded into this text in place before the adversarial pass, and no code is written until
the plan is approved as amended.

## Why

A pilot user reported seven things about the records table and CSV import on 2026-09-16,
against a pre-release image. One GitHub issue each, #13 to #19. All seven are still true on
`main` at `9e4b8a2`: each was run in a browser or against the API on 2026-10-10 (P1 to P7).

They are uneven. #15 is the serious one: a date filter that reads as applied and returns the
wrong rows. Its cause is not where the issue guessed. The browser sends the typed value
faithfully and the server accepts it, compares it as text, and answers 200 (P3). So the same
wrong answer is available to any agent calling `query_records`, `search`,
`bulk_update_records` or CSV export with a date the server does not understand, and for a bulk
update that means changing the wrong records. #19 changes what an import may write. #18 is a
new feature and the largest piece. #13, #14, #16 and #17 are friction.

One change rather than seven because they share four files (`TableView.tsx`, `ValueInput.tsx`,
`Popover.tsx`, `tableViewConfig.ts`), one new browser spec and one set of repainted baselines.

## Premises

Every measurement below was taken on 2026-10-10 on `main` at `9e4b8a2`, with the repository's
own functional Playwright project (the built frontend, a real `uvicorn`, a fresh SQLite
database), viewport 1280 by 800, against a scratch object type of 18 fields and 60 records made
for the purpose. The measuring spec was temporary and is not part of this change.

- **P1 (#13, reproduced in part).** Opening the View menu puts its panel past the right edge:
  trigger at x 1189, panel 256 wide, panel right edge at 1445 in a 1280 viewport, 165 past it.
  **The page itself gains no scrollbar** (`documentElement.scrollWidth` stays 1280). The
  content area does: `main` computes `overflow-x: auto` and its `scrollWidth` goes from 1056 to
  1221 with the menu open. So the sideways scroll the report describes is on the content area.
  Read: `web/src/ui/Popover.tsx:188` positions every panel `absolute left-0 top-full`, and
  `web/src/table-view/ViewMenu.tsx:66` is one of its callers. Measured with the panel's inline
  style set to `left: auto; right: 0`: panel spans 1000 to 1256 and `main` no longer overflows.
- **P2 (#14, reproduced).** A `Stage is Active` chip filters 60 rows to 30. Clicking the
  sidebar's Activity link and then the sidebar's link back shows 60 rows and no chip.
  `sessionStorage` and `localStorage` hold no key for it and the URL is the bare type path both
  times. Read: `web/src/table-view/TableView.tsx:107-114` holds the state in `useState`.
  **The sidebar link back is a bare path**, so state kept only in the URL would not survive the
  reported journey (see Q3).
- **P3 (#15, reproduced, and the cause is established and is not the one the issue names).**
  With a select chip plus `Last Used is before 7 Jan 2026`, the browser sends
  `{"and":[{"field":"stage","op":"eq","value":"active"},{"field":"last_used","op":"lt","value":"7 Jan 2026"}]}`,
  the server answers **200** with `total_count` 30, and the table shows 30 rows where 10 is
  right. Nothing is dropped in the browser. The same request with `2026-01-07T00:00` returns
  10. Sent straight to the API on the datetime field alone, `lt` answered: `7 Jan 2026` 60 of
  60, `7 Jan` 60, `Yesterday 10:00` 60, `banana` 60, `1 Jan 2026` 0, `01/07/2026` 0,
  `2026-01-07T00:00:00Z` 20, `2026-01-07T00:00` 20, `2026-01-07` 20 (20 is correct). No request
  was refused. Read: `src/glosswork/filters.py:245-254` returns any string for a `date` or
  `datetime` literal unchanged, and the comparison is then a text comparison against stored
  ISO strings, so `7 Jan 2026` sorts after every stored value and `1 Jan 2026` before every
  one. The Advanced builder has the same fault and one more: it sent ten requests for the ten
  characters typed, all 200, and showed 60 rows.
  **Five comments in the frontend already say the server refuses this**
  (`filters/completeness.ts:14`, `table-view/TableView.tsx:258`, `table-view/apiErrors.ts:61`,
  `table-view/FilterChipRow.tsx:21`, `table-view/TableView.test.tsx:967`), and the chip row's
  whole error path (`FilterChipRow.tsx`, `chipFilter.ts::ownsFilterError`) was built on that
  belief. It has never fired for a date.
- **P4 (#16, reproduced).** The document is 2470 tall in an 800 viewport and `main` is 2470
  tall too, so `main` never scrolls and the window does. After a 1500 wheel scroll the sidebar
  spans -1500 to -700, its first link is at -1439 and the table toolbar at -1436: all out of
  view. Read: `web/src/App.tsx:81` (`flex min-h-screen`), `:85` (`overflow-y-auto` with no
  bounded height), `web/src/app/Sidebar.tsx:226` (`h-screen`, neither sticky nor fixed).
  Measured with inline styles only: sidebar `position: sticky; top: 0; align-self: flex-start`,
  `main` `overflow: visible`, toolbar `position: sticky; top: 0`. After the same scroll the
  sidebar spans 0 to 800, its first link is at 61 and the toolbar at 0 to 28, and the document
  is still 1280 wide.
- **P5 (#17, reproduced exactly as the issue reads the code).** Typing `v1, v2` key by key
  into the chip's "is any of" input leaves the input reading `v`, `v1`, `v1`, `v1`, `v1v`,
  `v1v2`. The request carries `{"field":"variant","op":"in","value":["v1v2"]}` and the table
  shows 0 rows where 40 is right. The Advanced builder's `in` does the same. Read:
  `web/src/filters/ValueInput.tsx:134-153`.
- **P6 (#18, confirmed absent).** With the table's wrapper scrolled 800 sideways (its content
  is 3452 wide in a 1006 box) the Name cell is at x -327, off the left of the table. No header
  or body cell computes `position: sticky` and the Columns popover has no pin control.
  Measured with inline styles only: `position: sticky; left: <offset>` on the columns up to
  Name holds the Name cell at x 473 after the same scroll, inside the existing `overflow-x`
  wrapper and under `table-layout: fixed`.
- **P7 (#19, reproduced).** `POST /api/v1/object-types/{key}/import`, `mode=upsert`, matching
  on a unique field. A row with an empty cell answers `updated: 1` and the stored value and
  the record's version are unchanged. The dry run of the same file also says `updated: 1`. A
  cell holding `null` stores the text `null`; a cell holding `\N` stores the text `\N`. The
  OpenAPI operation has the summary `Import Csv` and no description. Read:
  `src/glosswork/services/csv.py:419-420`.
- **P8. The service already knows how to clear a field.** Read at
  `src/glosswork/services/records.py:745-756`: `update_record_in_txn` treats a `None` value as
  "remove the key" and refuses it on a required field with `Field 'x' is required and cannot
  be cleared.` The import calls that method (`csv.py`, the write pass), so #19 is a matter of
  handing it `None`, not a new write path.
- **P9. No visual baseline is a full-page screenshot.** Measured:
  `grep -c fullPage web/e2e/ui-visual.spec.ts` prints 0 and exits 1. All 45
  `toHaveScreenshot` calls target one element. Four of them are the records table's own
  section (`table-default`, `table-grouped`, `table-selected`, `table-wide`).
- **P10. The saved-view config needs no migration.** Read: `docs/DATA_MODEL.md` 11 stores
  `config` as one JSON text column, and `web/src/table-view/tableViewConfig.ts` already
  rebuilds a config defensively, field by field.
- **P11. `tests/test_generated_api_types_fresh.py` exists**, so a changed OpenAPI document
  fails the suite until `web/src/api/schema.ts` is regenerated.

Not established, and named so nobody reads it as checked:

- How many existing backend tests, fixtures or golden cases send a date literal the Q4 rule
  would refuse. Checklist step 2 measures it before any rule is written.
- Whether any code relies on `main` being a scroll container. One grep for
  `scrollTo|scrollTop|scrollIntoView` over `web/src` found no file; the functional suite is the
  real check.
- The reporter's window was about 2000 wide. Every measurement here is at 1280. Nothing in the
  mechanisms depends on the width, but 2000 was not run.

## Questions for the maintainer

Each has a recommendation, and the rest of this plan is written for it.

### Q1 (#19). How does an import say "clear this field"?

- **A. An opt-in flag, `blank_clears=true` (recommended, and the reporter's preference).** With
  it, an empty cell in a column the file carries clears that field on a record the row
  updates. Costs: it is all-or-nothing per file, so a sparse file imported with the flag on
  wipes every blank it carries. The dry run reporting the clears by field is the guard, and
  the default stays off. Why recommended: a blank cell is what an export already writes for an
  empty field, so export, edit, re-import round-trips with no special text, and nothing a
  person might legitimately store becomes magic.
- **B. A null token**, a cell text such as `\N` that means "clear". Costs: it is precise per
  cell, but it takes a string out of the set of values a text field can hold, which is the
  thing DD-20 ("content is data") exists to prevent, and export would have to learn to escape
  it. The pilot's scratch record already stores `null` and `\N` as text, so any token chosen
  can already be sitting in someone's data.

### Q2 (#15). What does a person type into a date filter?

The server-side half is Q4. This is only the input.

- **A. Keep the text box, add a calendar button beside it and a one-line hint (recommended).**
  The button is the browser's own date picker and writes the picked day into the box. Relative
  tokens stay typeable because the box is still a text box. Anything that is not a date the
  product understands, including `7 Jan 2026`, is held back before it is sent, with the hint
  naming what is accepted. Cost: about a third of a build run. The displayed format is refused,
  politely, not understood.
- **B. Also understand the displayed format.** `7 Jan 2026`, `7 Jan`, `Yesterday 10:00` typed
  into the box are parsed into a real date. Costs: a second parser that must stay the exact
  mirror of the display formatter, a guess at the year when none is typed, and most of a build
  run. It can be added later on top of A without undoing anything.
- **C. Hint only.** No calendar button. Cheapest, and the person still has to type
  `2026-01-07` by hand.

One point rides with any answer: **a picked or typed day means the viewer's own calendar
day**, because the table the person is filtering shows dates in their timezone. The browser
sends the instant that day starts for them. The alternative, UTC midnight, makes "before 7
Jan" disagree with the table by up to a day's worth of rows for anyone not in UTC.

### Q3 (#14). Where does the unsaved table state live?

- **A. The browser tab's session storage, per object type (recommended).** Survives leaving
  and coming back by any route, and a reload. Gone when the tab closes. Costs: it is not a
  link, so it cannot be sent to a colleague (a saved view can), and a second tab starts clean.
- **B. The URL.** A filter becomes a shareable, bookmarkable link. Costs: **it does not fix
  the reported journey on its own**, because the sidebar's link back to a table is a bare path
  (P2), so it would need a per-type memory as well; and filters make long URLs.
- **C. Both.** B's link plus A's memory. About twice A's work, and two sources of truth to
  reconcile on every load.

### Q4 (#15). Does the server refuse a date it cannot read? New, found by this run

P3 shows the wrong answer comes from the server. This question was not in the issue.

- **A. Yes, in this change (recommended).** A filter on a `date` or `datetime` field accepts a
  relative token, `YYYY-MM-DD`, and for `datetime` also `YYYY-MM-DDTHH:MM:SSZ`. Anything else
  is `validation_failed` naming the field and the accepted forms. A date-only value on a
  `datetime` field means that day at 00:00:00 UTC. Costs: **it is an API and MCP contract
  change.** A caller sending `2026-01-07T00:00` or a value with an offset is refused where
  today it gets an answer (a right one for the first, by luck of text ordering). A saved view
  holding such a value shows the refusal on its chip after upgrade. And it edits
  `src/glosswork/filters.py`, the filter grammar's validation, which is on the list of work
  that needs the maintainer's design approval. It does not touch SQL emission
  (`glosswork.compiler`). Why recommended: without it the product still gives agents a wrong
  answer with a 200, and the frontend's existing error path for exactly this case stays dead.
- **B. Browser only for now.** The input from Q2 refuses bad values before sending, and the
  server rule waits for the planned timestamp change. Costs: #15's acceptance is met for people
  and not for agents, and the bulk-update case stays open until then.

### Q5 (#18). What else stays put beside a pinned column?

- **A. Only pinned fields (recommended).** The checkbox, By and Key columns scroll away; the
  display field stops at the left edge and stays. Costs: the row checkbox and the key link are
  out of sight while scrolled right.
- **B. Checkbox, By and Key are always pinned too.** Costs: 212 pixels of every table are
  frozen before the first field, 392 with the display field, out of about 1006 at a 1280
  window.

### Issue numbering, not a question unless the maintainer wants it to be

One change, seven issues. This plan takes #13 as the change's number and the pull request
closes #14 to #19 as well.

## What changes

Service layer first, adapters second (DD-3). Written for Q1 A, Q2 A, Q3 A, Q4 A, Q5 A.

**1. A date literal in a filter is validated (#15, Q4).** `filters.py::_resolve_scalar`, the
`date` and `datetime` branch:

- a token is resolved as today;
- `date`: `YYYY-MM-DD`, checked with `timeutil.parse_date`;
- `datetime`: `YYYY-MM-DDTHH:MM:SSZ`, checked with `timeutil.parse_datetime`, or `YYYY-MM-DD`,
  which is rewritten to that day at `T00:00:00Z` so the comparison is between instants;
- anything else raises `ValidationFailedError` with the field key. Wording, for approval
  because error copy is the maintainer's:
  `Field 'last_used' (datetime) compares against YYYY-MM-DDTHH:MM:SSZ (UTC), YYYY-MM-DD, or a date token like @today-7d; got '7 Jan 2026'.`
  and for a date field
  `Field 'due' (date) compares against YYYY-MM-DD or a date token like @today-7d; got '7 Jan 2026'.`

Every operator reaches this function, `between` and the list operators member by member, and
so do the datetime pseudo-fields. `query_records`, `search`, `bulk_update_records` and CSV
export inherit it because they share the parser. `docs/MCP_TOOLS.md` 4 and the two type
descriptions in `services/capabilities.py` say which literal forms a filter accepts.

**2. A CSV upsert can clear a field (#19, Q1).** `CsvService.import_csv` gains
`blank_clears: bool = False`; the route gains the form field of the same name.

- With it on, for a row that resolves to an existing record, each field whose column is in the
  file's header and whose cell is empty is set to `None` in that row's values. `_build_row`
  learns which columns the header carries; a column the file does not carry is never cleared.
- A row that creates a record is unchanged: there is nothing to clear.
- Relation columns are unchanged: add-only (FR-E4). Attachment columns are still refused.
- "Empty" stays what it is today, the cell being the empty string after the DD-20 restore. A
  cell of spaces is not blank.
- A required field with a blank cell on an updating row is a row error at validation, so the
  dry run reports it: `Field 'x' is required and cannot be cleared.` (the service's existing
  sentence, P8).
- The result gains `cleared`, the number of field values the commit removes, and
  `cleared_by_field`, a map of field key to count. A field counts only when the record holds a
  value for it now. Both are a prediction on a dry run and a report on a commit (DD-22), zero
  and empty without the flag, and zero and empty when any row has an error.
- The route gets a `summary` and a `description` that state: a blank cell changes nothing by
  default; `blank_clears`; that it applies only to columns in the file and only to rows that
  update; required fields; relations add-only. `web/src/api/schema.ts` is regenerated (P11).
- The import wizard, in upsert mode, shows one checkbox, off by default. Copy, for approval:
  label `Blank cells clear the field`, help text `Off: a blank cell leaves the record's value
  as it is. On: a blank cell in a column you mapped removes the value. Linked records are
  never removed by an import.` After a dry run with the box on, the summary adds
  `3 values will be cleared: Superseded by (3).`

**3. A multi-value text filter keeps what is typed (#17).** `CommaListInput` holds the typed
text in its own state and commits the split list on each change; it re-reads the committed
value only when that value changes to something the typed text does not already split to
(operator change, saved view). The relation branch shares the component and gets the same
behaviour. Values that contain a comma remain out of scope.

**4. A date filter's input says what it accepts and offers a calendar (#15, Q2).** A
`DateValueInput` in `ValueInput.tsx` for `date` and `datetime` fields, used by the chip
popover and the Advanced builder alike:

- a text box, a native date input styled as a calendar button, and a hint line:
  `A date like 2026-01-07, or a relative date like @today-7d.`
- text starting with `@` is passed through untouched; the server judges tokens, and the token
  grammar is not copied into the browser;
- `YYYY-MM-DD`, typed or picked, becomes the value: as typed for a `date` field; for a
  `datetime` field the canonical UTC instant at which that day starts in the viewer's timezone;
- a canonical `YYYY-MM-DDTHH:MM:SSZ` is passed through;
- anything else commits `undefined`, which the existing completeness gate already treats as
  "do not send", and the hint turns into the refusal: `Not a date this filter understands.
  Use 2026-01-07 or @today-7d.` `Apply` is disabled with that line beside it, as the popover
  already does for a value that is missing.
- the chip's sentence renders a datetime instant with the table's own formatter, so the chip
  reads the way the column does.

The server's refusal (1) remains the backstop for a saved view or an edited request, and the
Advanced popover shows it whenever there is one, not only for a tree no chip row can express.

**5. A popover stays inside the viewport (#13).** `Popover.tsx` measures its panel when it
opens and, when the panel's right edge would pass the viewport's, shifts it left by the
overflow, never past the left edge. One implementation, so the View menu and every other
popover built on it get the same behaviour. No caller changes.

**6. The sidebar and the table toolbar stay in view (#16).** At the `shell` breakpoint and
wider: the sidebar is `sticky top-0` and `self-start`; `main` drops `overflow-y-auto`, which
never scrolled (P4) and which stops anything inside it sticking to the window; the table
toolbar is `sticky top-0` with the page's ground behind it and a stacking order above the
table. The window remains the scroller. Below the breakpoint nothing changes.

**7. Unsaved table state survives leaving the page (#14, Q3).** A pure module,
`table-view/tableViewSession.ts`, and a hook that uses it:

- stores `{version, principalId, selectedViewId, config}` under `gw.table-view.<type key>` in
  `sessionStorage` whenever the filter, sort, grouping, columns, pins or selected view change;
  density is left out because it has its own persistence;
- on mount, a stored entry for the signed-in principal is applied through the existing
  `parseTableViewConfig` and `applyConfig`, and the default saved view is then not applied on
  top of it;
- storage access is guarded as `ui/density.ts` guards its own; an entry for another principal
  is ignored and removed; sign-out removes every `gw.table-view.` key;
- choosing a saved view replaces the stored state, as it replaces the live state today.

**8. Columns can be pinned (#18, Q5).**

- `ColumnStateConfig` gains `pinned?: string[]`, field keys. Absent, as in every view saved
  before this change, means "the display field" (`effective_display_field_key`). An explicit
  empty list means nothing is pinned.
- A pinned column is `position: sticky` with `left` equal to the summed widths of the pinned
  columns before it in the current order, computed by a pure function over the header sizes.
  Columns are not reordered by pinning. The last pinned column carries a right-hand rule.
  Pinned cells are opaque and still show the row's hover and selection tint.
- The Columns popover gains one pin toggle per field, named `Pin <field>` and `Unpin <field>`.
- Pinning applies at the `shell` breakpoint and wider. Below it the table scrolls as today.
- Pin state is part of `serializeTableViewConfig`, so `Save` and `Save as new` carry it, and
  of the session state in 7.

**9. Specifications, at closeout.** `PRD.md` FR-U1 (pinning, persistence of unsaved state),
FR-R8 and FR-R5 (the literal forms a date filter accepts), FR-E1 and FR-E2 (`blank_clears`,
the cleared counts); `docs/DATA_MODEL.md` 4 (the timestamp rule covers filter literals; the
blank-cell rule) and 11 (`pinned`); `docs/MCP_TOOLS.md` 4, 6 and 8; `docs/DESIGN.md` 7.4, 8.1
and 8.2; new entries in `docs/DESIGN_DECISIONS.md`, with the next unused numbers, for the date-literal rule, the
opt-in clear, and where unsaved table state lives. The five frontend comments in P3 become
true and are left as they are.

No file that defines CI is touched. No migration. No new dependency.

## What does not change

- SQL emission, `glosswork.compiler`, and every index. The access model. The schema engine.
- Relative date tokens, their grammar and their resolution.
- What the write path accepts for a `date` or `datetime` field value.
- An import without `blank_clears`: byte-for-byte today's behaviour, including the `updated`
  count for a row that changes nothing.
- Relations by import: add-only.
- The filter JSON a chip or the builder sends for anything that is not a date value.
- That the Advanced builder sends on every complete change. It is not this change's subject.
- The narrow-viewport shell and top bar.
- What the View menu contains, and every accessible name and test id in it.
- The table header row: it does not become sticky.
- Paging, the cursor walk and row selection. None of them is stored by 7.
- Saved views on the server: `config` is still validated as a JSON object and nothing more.

## Constraints

- **DD-3.** `blank_clears` and the date rule are decided in the service layer; the route and
  the MCP adapter add nothing but a parameter.
- **DD-19.** Every new refusal is a 4xx naming what to fix. None echoes the request body
  beyond the one offending literal the message quotes.
- **DD-20.** No cell text acquires a meaning. The clear is a flag, not a token.
- **DD-22.** The import stays one transaction, and a dry run's `cleared` equals the commit's.
- **`completeness.ts` is a shape gate, and stays one.** The date input may hold back a value
  the server would refuse; it must never hold back one the server accepts. The token grammar
  is not mirrored in the browser.
- **Logic in hooks and utilities, not in a render body** (AGENTS.md, non-negotiable 3): the
  session module, the pin offsets and the date parsing are pure functions with unit tests.
- **Layout is proven in Playwright or not at all.** Every geometric claim in the Accept block
  is a browser assertion.
- **Stacking.** Sticky toolbar above pinned cells; a popover opened from the toolbar above
  both. Asserted in the browser, because three sticky layers are easy to get wrong silently.
- **One root, one address.** Every commit is `Glosswork <hello@glosswork.dev>`; no reporter's
  name, workspace, records or screenshots appear in the plan, the tests, the fixtures, the
  commits or the pull request.

## Checklist

1. Write the new assertions (the backend tests and the browser spec named in Accept) and run
   each against the unfixed tree. Record here how each failed, in the order written. An
   assertion that passes unfixed is a fence: label it and do not count it.
2. Measure how many existing tests send a date filter literal the rule in "What changes" 1
   would refuse: add the rule behind nothing, run `uv run pytest -q`, record the count and the
   files, and decide each (fix the test's literal, or report it if it shows the rule is wrong).
3. Server: the date-literal rule in `filters.py`, its tests, the capability descriptions.
4. Server: `blank_clears` in `CsvService`, the route's parameter, summary and description, the
   result's two new members, regenerated `schema.ts`.
5. `CommaListInput` (#17), with a key-by-key component test for the general and the relation
   branch.
6. `DateValueInput` and the chip sentence (#15), with unit tests for the parsing in at least
   one timezone west of UTC and one east.
7. `Popover` viewport containment (#13).
8. Shell and toolbar stickiness (#16).
9. Session state (#14).
10. Column pinning (#18): config, offsets, rendering, Columns popover.
11. Import wizard checkbox and dry-run summary (#19).
12. Run the whole local suite once, both Playwright projects included; repaint baselines and
    record the count against "Baseline repaint".
13. Hand over for verification against the Accept block.
14. Closeout in two commits: the plan's final edits, then the specifications and the deletion.

## Accept

Each is a command. Read each exit code directly, never through a pipe.

- **AC1 (#15, server).** `uv run pytest -q tests/test_filter_date_literals.py` exits 0. It
  seeds rows either side of a boundary and asserts: `7 Jan 2026`, `01/07/2026`, `banana`,
  `2026-01-07T00:00` and a value with an offset are each 422 `validation_failed` whose
  `field_key` is the field and whose message names the accepted forms, on a `datetime` field,
  a `date` field and `created_at`; `2026-01-07` and `2026-01-07T00:00:00Z` return the same
  count, compared with a count computed in the same test from the seeded rows; `@today-7d`
  still resolves; a `between` with one bad end and an `in` with one bad member are refused;
  `bulk_update_records` with a refused literal changes no record.
- **AC2 (#19).** `uv run pytest -q tests/test_csv_blank_clears.py` exits 0. It asserts:
  without the flag a blank cell leaves value and version unchanged; with it the value is
  gone and `cleared` and `cleared_by_field` say so; a dry run with it returns the same two
  members and writes nothing; a column absent from the header is not cleared; a row that
  creates is unaffected; a required field's blank is a row error naming the field and the
  batch writes nothing; a relation column's blank removes no link; `null` and `\N` are still
  stored as text; the OpenAPI operation's description contains `blank_clears`.
- **AC3 (generated types).** `uv run pytest -q tests/test_generated_api_types_fresh.py` exits 0.
- **AC4 (frontend units).** `npm --prefix web run test` exits 0, and includes: `CommaListInput`
  typed key by key commits `["v1","v2"]` for a text field and for a relation field; the date
  parsing table; `tableViewSession` round trip, other-principal entry ignored, storage that
  throws tolerated; pin offsets; `parseTableViewConfig` with `pinned` absent, empty and stale.
- **AC5 (browser, the seven items).**
  `npm --prefix web run e2e -- --project=e2e table-pilot-findings.spec.ts` exits 0. One test
  per item, on a seeded type wider and taller than the viewport:
  - #13: with the View menu open, the panel's right edge is at or inside `window.innerWidth`,
    and neither `document.documentElement` nor `main` has `scrollWidth` greater than
    `clientWidth`. Repeated at 800 wide for the Columns popover.
  - #14: a chip is applied, the sidebar's Activity link is clicked, the sidebar's link back is
    clicked; the chip and the filtered row count are there. A second type's table shows no
    chip. Choosing a saved view replaces the state. After sign-out and sign-in the table is
    unfiltered.
  - #15: a select chip plus a datetime "is before" chip. Typing `7 Jan 2026` sends no request
    (an absence assertion that waits its whole window), leaves `Apply` disabled and shows the
    refusal naming the formats. Picking the day, and separately typing `2026-01-07`, shows the
    row count the API returns for the same filter in the same run. Typing `@today-7d` is sent
    as typed. The same three in the Advanced builder. A saved view seeded with `7 Jan 2026`
    shows the server's message on its chip and in the Advanced popover, with the table's
    previous rows still on screen.
  - #16: after scrolling the window 1500, the sidebar's first link and the chip row are inside
    the viewport, and the Columns popover opened from the stuck toolbar is the topmost element
    at its own centre. At 800 wide, after the same scroll, the top bar has scrolled out of
    view exactly as it does on `main` today. That last one is a fence: it passes on the
    unfixed tree by construction and is not counted.
  - #17: `v1, v2` typed with `pressSequentially` in the chip and in the Advanced builder; the
    request's filter value is `["v1","v2"]` and the row count equals the API's for that filter.
  - #18: by default the display field's cell stays at the same x after the wrapper is scrolled
    800 sideways and a far column moves; pinning a second field from the Columns popover holds
    it beside the first; unpinning releases it; an inline edit, a resize and a reorder each
    work on a pinned and an unpinned column; `Save as new`, reload, select the view: the pins
    are back; a view saved with no `pinned` member pins the display field.
  - #19: the wizard's checkbox is absent in create mode and present and off in upsert mode;
    a dry run with it on states the clears.
- **AC6 (nothing else broke).** Each on its own line, each exit 0:
  `uv run pytest -q`
  `uv run ruff check .`
  `uv run ruff format --check .`
  `uv run mypy src`
  `npm --prefix web run lint`
  `npm --prefix web run typecheck`
  `npm --prefix web run e2e -- --project=e2e`
  `npm --prefix web run e2e -- --project=visual`
- **AC7 (no reporter data).** `git log -p main..HEAD` is read by the verifier for any workspace
  name, person's name or record content from the feedback export. This one is a reading, not
  a command, and is reported as such.

## Baseline repaint

Expected: 4, the records table's own section, because the display field gains a right-hand
rule as the last pinned column: `table-default`, `table-grouped`, `table-selected`,
`table-wide`. Expected 0 for the sidebar and for every `main` screenshot, since stickiness
moves nothing at rest. Expected 0 or 2 for the CSV wizard (`csv-import-mapping-errors`,
`csv-import-commit`), depending on whether those fixtures are in upsert mode; the build run
records which. Any other repaint is a finding.

Actual: not yet run.

## Adversarial pass

Not yet run. It is run once, by a session that did not write this plan, after the maintainer
has answered Q1 to Q5. Places it should press, from the author's own doubts:

- Rewriting a date-only literal to midnight on a `datetime` field changes `lte`, `gt` and `eq`
  at exactly midnight compared with today's text ordering. Is that the right reading, and
  does any stored saved view or golden case depend on the old one?
- "The viewer's own day" in the browser against "UTC midnight" for the same text sent by an
  agent: two meanings for `2026-01-07` depending on who sends it.
- `cleared` needs each updating row's current values during validation. What does that cost on
  a file at the row cap, and what do two rows naming one record count?
- A stored session entry that names a field since removed, or a filter the new date rule now
  refuses.
- Three sticky layers and the popovers opened over them: which one ends up under which.
- Whether removing `overflow-y-auto` from `main` lets some other wide element scroll the page.

## Deviations from the approved plan

None yet.

## Durable content moved out of this plan

Nothing yet. See "What changes" 9 for where it goes.
