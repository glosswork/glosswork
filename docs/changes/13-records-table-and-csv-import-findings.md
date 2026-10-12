# 13: Seven records-table and CSV import findings from a pilot are fixed

| | |
| --- | --- |
| Issue | [#13](https://github.com/glosswork/glosswork/issues/13), and this one change also closes #14, #15, #16, #17, #18 and #19 |
| Branch | `13-records-table-and-csv-import-findings` |
| Spec | `PRD.md` 6.2 (FR-R5, FR-R8), 6.8 (FR-U1, FR-U3), 6.11 (FR-E1, FR-E2, FR-E4); `docs/DATA_MODEL.md` 4 and 11; `docs/MCP_TOOLS.md` 4, 6 and 8; `docs/DESIGN.md` 7.4, 8.1, 8.2 and 9 |
| Decisions | DD-3, DD-19, DD-20, DD-22, DD-41, DD-43. New entries at closeout take the next unused numbers |
| Requirements | FR-U1, FR-U3, FR-R5, FR-R8, FR-E1, FR-E2 |
| Depends on | Nothing unmerged. The planned change on client-sent timestamps (field editors and the Activity filter) touches `web/src/table-view` and `web/src/filters` too and must not run at the same time. Whichever runs second reads the other's result |

**Status: approved and built, not verified.** The maintainer answered Q1 to Q5 on 2026-10-10
and approved the plan as it read at commit `68272e3` on 2026-10-12. The one adversarial pass
ran on 2026-10-10, by a session that did not write the plan; its findings F1 to F19 are at the
end and their fixes are folded into the text in place. Checklist steps 1 to 12 were built on
2026-10-12; what each assertion did against the unfixed tree is recorded under the checklist,
and where the build departed from this text is under "Deviations from the approved plan". The
one full verification ran on 2026-10-12 at `0bc0b27` and failed on one point of #15: a filter
the server refused could still look applied over rows it did not produce. The maintainer
decided the same day what a refused filter shows, and a fix run changed it (D11 to D14).
**"What changes" 4 and 10 and AC5 still read as approved; where they say a refused filter keeps
the table's previous rows, D11 is what the code does.** The fix has not been rechecked, and
nothing is pushed.

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
  **That measurement froze the checkbox, By and Key columns too, which is Q5's answer B.** The
  adversarial pass measured the answer chosen, A (F17): only the display field's cells sticky
  at `left: 0`. At rest the Name header is at x 461; after the wrapper is scrolled 800 its
  header and body cells are at x 249, one pixel inside the wrapper's left edge at 248, and the
  cell is the topmost element at its own position. **A border set on a sticky cell does not
  travel with it**: the table computes `border-collapse: collapse`, and a 2 pixel red
  `border-right` set on every sticky cell was not painted anywhere after the scroll (read off
  a screenshot). Body cells compute a transparent background, so a pinned cell needs its own.
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

Premises P12 to P17 were established by the adversarial pass on 2026-10-10, on the same tree,
with a second temporary spec (18 fields, 60 records, 1280 by 800) that is also not part of
this change. It re-measured what the design leans on before adding anything: P3's counts
(`7 Jan 2026` 60, `banana` 60, `1 Jan 2026` 0, `2026-01-07` 20, typed into a chip and sent as
typed with 60 rows shown and no alert), P4 (`main` is 2470 tall with a `scrollHeight` of 2470,
so it never scrolls; with the three inline styles the sidebar spans 0 to 800 and the toolbar 0
to 28 after a 1500 scroll) and P1 (panel right edge 1445, `main` `scrollWidth` 1221).

- **P12. The repository's own date parsers are not strict, so "it parses" is not the check.**
  Measured by calling them: `timeutil.parse_date("2026-1-7")` succeeds, as does a date written
  in full-width digits, and `timeutil.parse_datetime` accepts `2026-1-7T0:0:0Z` and a
  lowercase `z`. Measured against the API: `lt 2026-1-7` answers 60 of 60 on a `date` field
  and on a `datetime` field, where 20 is right. A rule that only asks whether a literal parses
  would accept that literal and keep the wrong answer.
- **P13. No existing backend test sends a literal the rule refuses.** Measured: the refusal
  was patched over `filters._resolve_scalar` from a plugin outside the tree, logging every
  literal it refused, and `uv run pytest -q -k "not structural"` exited 0 with the log empty.
  No test, end-to-end spec or golden case filters a `datetime` field with a date-only literal
  (grep over `tests` and `web/e2e`; the golden set has no date filter at all). The kit's one
  date filter example is a token.
- **P14. What the refused forms answer today.** Measured, `lt` on the `datetime` field:
  `2026-01-07T00:00`, `2026-01-07T00:00:00.000Z`, `2026-01-07T00:00:00+00:00` and
  `2026-01-07T02:00:00+02:00` each answer 20, the right count, by text ordering. On the `date`
  field each of those answers 21 where 20 is right. `eq 2026-01-07` on the `datetime` field
  answers 0. A `bulk-update` dry run filtered on `lt 7 Jan 2026` reports `affected_count` 60.
- **P15. An unknown date token is refused without naming its field.** Measured:
  `resolve_date_token("@tod", "datetime")` raises `validation_failed` with empty details. Read:
  `chipFilter.ts::ownsFilterError` returns false for a flat filter when the field key is null,
  so `TableView.tsx:688` shows the page-level alert and line 285 drops the rows.
- **P16. The date box is a controlled input over the committed value.** Read at
  `web/src/filters/ValueInput.tsx:214-221`: `value={typeof value === "string" ? value : ""}`.
  It is the same construction P5 measured failing for the comma list.
- **P17. With `main`'s overflow removed, whatever overflows `main` scrolls the page.**
  Measured with the View menu open and unfixed: `documentElement.scrollWidth` is 1280 today
  and 1445 once `main` computes `overflow: visible`. The filter chips are inside the element
  with test id `table-toolbar`, so one sticky element carries both. With the toolbar sticky at
  a stacking order of 20, a View menu opened from it after a 1500 scroll is the topmost
  element at its own centre, and the toolbar is topmost where it crosses the pinned column.

Not established, and named so nobody reads it as checked:

- How many saved views in any real deployment hold a date literal the Q4 rule refuses. It
  cannot be counted without reading deployments' data, which this change does not do. What
  such a view does after the upgrade is in "What changes" 10 and F6.
- Whether any page other than the records table puts something wider than `main` inside it.
  P17 shows what happens if one does; AC5's per-route assertion is the check.
- Whether any code relies on `main` being a scroll container. One grep for
  `scrollTo|scrollTop|scrollIntoView` over `web/src` found no file; the functional suite is the
  real check.
- The reporter's window was about 2000 wide. Every measurement here is at 1280. Nothing in the
  mechanisms depends on the width, but 2000 was not run.

## Questions for the maintainer

**All five are decided.** The maintainer answered each on 2026-10-10, in the words quoted
under it, and each answer is the one this plan was written for. The options are kept so the
reasoning stays readable; none is open.

### Q1 (#19). How does an import say "clear this field"?

**Decided 2026-10-10: "A flag on the import (Recommended)".** Answer A.

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

**Decided 2026-10-10: "Text box, calendar button, hint (Recommended)".** Answer A, and a
picked day means the viewer's own calendar day.

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

**Decided 2026-10-10: "In the tab (Recommended)".** Answer A.

- **A. The browser tab's session storage, per object type (recommended).** Survives leaving
  and coming back by any route, and a reload. Gone when the tab closes. Costs: it is not a
  link, so it cannot be sent to a colleague (a saved view can), and a second tab starts clean.
- **B. The URL.** A filter becomes a shareable, bookmarkable link. Costs: **it does not fix
  the reported journey on its own**, because the sidebar's link back to a table is a bare path
  (P2), so it would need a per-type memory as well; and filters make long URLs.
- **C. Both.** B's link plus A's memory. About twice A's work, and two sources of truth to
  reconcile on every load.

### Q4 (#15). Does the server refuse a date it cannot read? New, found by this run

**Decided 2026-10-10: "Refuse on the server now (Recommended)".** Answer A.

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

**Decided 2026-10-10: "Only pinned fields (Recommended)".** Answer A.

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

- a token is resolved as today. One thing changes beside it: when the token is unknown, the
  refusal carries the field key, as the `user_ref` branch already does for its own resolver's
  error, so the table can show it on the chip (P15, F4). The token grammar, its resolution and
  its message are untouched;
- `date`: exactly `YYYY-MM-DD`;
- `datetime`: exactly `YYYY-MM-DDTHH:MM:SSZ`, or exactly `YYYY-MM-DD`, which is rewritten to
  that day at `T00:00:00Z` so the comparison is between instants;
- **"exactly" is a full match on ASCII digits and then a calendar check, not a parse alone**
  (P12, F1). The check is two small functions beside the parsers in `timeutil`, used by the
  filter rule only in this change: ten or twenty characters, ASCII digits, the separators in
  place, an upper-case `T` and `Z`, and then `parse_date` or `parse_datetime` to refuse
  30 February and hour 25. `2026-1-7`, a lower-case `z`, a fractional second and an offset are
  all refused;
- anything else raises `ValidationFailedError` with the field key, a value that is not a
  string included, so each type has one message. Wording, for approval because error copy is
  the maintainer's. It was amended by the adversarial pass to say what the commonest refused
  machine forms get wrong (F5):
  `Field 'last_used' (datetime) compares against YYYY-MM-DDTHH:MM:SSZ (UTC, whole seconds, ending in Z), YYYY-MM-DD, or a date token like @today-7d; got '2026-01-07T00:00:00.000Z'.`
  and for a date field
  `Field 'due' (date) compares against YYYY-MM-DD or a date token like @today-7d; got '7 Jan 2026'.`
  The quoted literal is cut at 40 characters.

Every operator reaches this function, `between` and the list operators member by member, and
so do the datetime pseudo-fields. `query_records`, `search`, `bulk_update_records` and CSV
export inherit it because they share the parser. `docs/MCP_TOOLS.md` 4 and the two type
descriptions in `services/capabilities.py` say which literal forms a filter accepts, and say
in one sentence what a date-only value means on a `datetime` field: that day at 00:00:00 UTC,
an instant, so `lte 2026-01-07` stops at the first second of the 7th and `eq 2026-01-07`
matches only that second. That sentence is for agents; the browser does the day arithmetic
for people (4).

**Who sends a date literal today, and what each sees afterwards** (F5):

| Caller | Sends today | After this change |
| --- | --- | --- |
| Table chips and the Advanced builder | whatever was typed, unchecked (P3) | the date input holds back what is not a date (4); what it sends is canonical |
| The Search page's inline builder | the same, on the datetime system fields | it shares `ValueInput`, so it gets the same input; a refusal shows in its existing alert |
| A saved view holding a refused literal | a wrong answer, or a right one by luck (P14) | it opens; its chip carries the server's message; see 10 |
| The table's bulk edit and bulk delete | a filter on `key`, never a date | unchanged |
| CSV export from the table | the view's filter | refused with the view's filter; the failure is shown (10) |
| `query_records`, `search`, `bulk_update_records` over MCP and REST | anything | `validation_failed` naming the field and the forms; nothing is written |
| CSV import matching on a unique field | an `eq` filter built from the row's own value | unchanged unless the unique field is a date written unpadded, which is then refused for the whole file with the same message |
| Existing backend tests, end-to-end specs, golden cases | canonical literals and tokens | none refused (P13) |
| The kit's skills and examples | one token | unchanged |

**Whether a call that works today can fail with no way to tell what to send.** No, as far as
this pass could establish. Four forms that answer correctly today on a `datetime` field are
refused afterwards (P14): no seconds, a fractional second, `+00:00`, and any other offset.
Each gets the message above, which names the exact form and quotes what was sent, so one
retry fixes it; an agent that sent an offset has to convert to UTC itself, which the message
says. Nothing the API stamps is in a refused form: a grep for `strftime` and `isoformat` over
`src` finds the formatters in `timeutil`, one SQL stamp in `migrations.py` in the same shape,
and one `isoformat` call in `config.py` that is not a response. The one value that can come
back out of the API and be refused as a filter is a field value that was written unpadded,
because the write path does not use the strict check (P12). That is the timestamp change's subject, and it is
written down for it under "What does not change".

**2. A CSV upsert can clear a field (#19, Q1).** `CsvService.import_csv` gains
`blank_clears: bool = False`; the route gains the form field of the same name.

- With it on, for a row that resolves to an existing record, each field whose column is in the
  file's header and whose cell is empty is cleared on that record. A column the file does not
  carry is never cleared.
- **The clears are added to a row's values after the row is resolved and after the in-file
  uniqueness pass, never inside `_build_row`** (F10). Read at `services/csv.py:229-250` and
  `:549`: both the upsert lookup and the `claimed` loop test `field.key in values`, so a
  `None` placed there earlier would make two rows that each clear the same unique field fail
  with "row 2 of this file already claims None", and would send a row whose matching field is
  blank down the lookup with no value.
- **`blank_clears=true` with `mode=create` is refused**, `validation_failed`: `blank_clears
  applies to mode='upsert'; a create import has no existing value to clear.` A flag that does
  nothing is how a mistyped mode passes unnoticed (F13).
- A row that creates a record is unchanged: there is nothing to clear, a blank cell still
  takes the field's default, and it never appears in the counts.
- Relation columns are unchanged: add-only (FR-E4). Attachment columns are still refused at
  the header, flag or no flag.
- "Empty" stays what it is today, the cell being the empty string after the DD-20 restore. A
  cell of spaces is not blank. **A row shorter than the header is blank in the cells it
  lacks**, as it is today (`row.get(key) or ""`), and the wizard already pads short rows
  before it uploads (`CsvImportWizardPage.tsx:70`), so with the flag on a ragged row clears
  its missing trailing fields. The description says so and AC2 pins it (F12).
- A required field with a blank cell on an updating row is a row error at validation, so the
  dry run reports it: `Field 'x' is required and cannot be cleared.` (the service's existing
  sentence, P8). It is an error whether or not the record holds a value, as in the service.
- **A clear removes whatever the record holds now, not what it held when the file was
  exported.** The import writes without a version check (`force=True` at `csv.py:314`), as it
  always has for cells that carry a value. So a value a colleague or an agent added after the
  export is removed by the re-import's blank cell. The dry run's count is the guard and the
  record's history can restore it; the help text and the description say it (F12).
- The result gains `cleared`, the number of field values the import removes, and
  `cleared_by_field`, a map of field key to count. **One clear is one record and field that
  holds a value before the import and none after it** (F11). Two rows naming one record are
  folded in file order, as the write does, so a value set by one row and cleared by a later
  one is not a clear, and a field cleared twice counts once. On a dry run the counts come
  from the validation pass, using the record each row's lookup already fetched (the
  unique-field lookup asks for every field instead of the compact default, so there is no
  extra read per row at the 20,000 row cap). On a commit they are counted inside the write
  transaction from what each record held just before its update, so the report is true even
  if a record changed after the dry run. Zero and empty without the flag, and zero and empty
  when any row has an error (DD-22).
- The route gets a `summary` and a `description` that state: a blank cell changes nothing by
  default; `blank_clears`; that it applies only to columns in the file, only to rows that
  update, and to a short row's missing cells; that it removes the record's current value;
  required fields; relations add-only; refused with `mode=create`. `web/src/api/schema.ts` is
  regenerated (P11).
- The import wizard, in upsert mode, shows one checkbox, off by default, and sends the flag
  only in upsert mode. **Ticking or clearing it discards the last dry run**, through the same
  `invalidatePriorDryRun` every other option calls, so a commit can never clear anything a dry
  run did not report (F13). Copy, for approval; the second sentence of the help text was
  amended by the adversarial pass: label `Blank cells clear the field`, help text `Off: a
  blank cell leaves the record's value as it is. On: a blank cell in a column you mapped
  removes whatever the record holds now, including a value added since the file was exported.
  Linked records are never removed by an import.` After a dry run with the box on, the summary
  adds `3 values will be cleared: Superseded by (3).` After the commit it reads
  `3 values cleared: Superseded by (3).`

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
- **the box keeps what was typed in its own state** and commits a value beside it, as the
  comma list does in 3 (P16, F3). Today's box is drawn from the committed value, so a box that
  committed `undefined` for `2` would empty itself on the first key and no date could be typed
  at all. It re-reads the committed value only when that value changes to something the typed
  text does not already produce (a saved view, an operator change);
- text starting with `@` is passed through untouched; the server judges tokens, and the token
  grammar is not copied into the browser;
- `YYYY-MM-DD`, typed or picked, becomes the value: as typed for a `date` field; for a
  `datetime` field a canonical UTC instant on that day in the viewer's timezone, **chosen by
  the operator so that the sentence on the chip is true** (F2). A stored instant has whole
  seconds, so the last second of a day is exact:

  | Operator, as the chip words it | A day D becomes |
  | --- | --- |
  | is before (`lt`), is on or after (`gte`), the lower end of is between | the first second of D |
  | is on or before (`lte`), is after (`gt`), the upper end of is between | the last second of D, 23:59:59 |
  | is, is not (`eq`, `neq`) | held back, see below |

  Without this, "is on or before 7 Jan" would stop at the first second of the 7th and leave
  out the whole day, "is after 7 Jan" would include it, and "is 7 Jan" would match only
  records stamped at midnight to the second (P14: 0 rows). That is #15's own defect, a filter
  that reads as applied and returns the wrong rows, on three operators the reported case does
  not use. For `is` and `is not` with a day, `Apply` stays disabled and the hint reads, for
  approval: `"is" and "is not" need an exact time here. For a whole day, use "is between"
  with the same day twice.` Changing the operator re-derives the instant from the typed day;
- the day arithmetic is one pure function, built from the day's own numbers and checked by
  turning the result back into a day: `2026-02-31`, which a `Date` silently rolls into March,
  a year below 1000, and the six-digit year a native picker allows are refused in the box and
  never sent. Where a zone's clocks skip midnight the first second of the day is the first
  one that exists;
- a canonical `YYYY-MM-DDTHH:MM:SSZ` is passed through, for any operator;
- anything else commits `undefined`, which the existing completeness gate already treats as
  "do not send", and the hint turns into the refusal: `Not a date this filter understands.
  Use 2026-01-07 or @today-7d.` `Apply` is disabled with that line beside it, as the popover
  already does for a value that is missing;
- the chip's sentence renders a datetime instant with the table's own formatter, so the chip
  reads the way the column does. An instant that is the first or last second of a day for
  the viewer reads as the day alone; any other reads as day and time. Saved views are shared
  by everyone (`services/saved_views.py:4`), so a view saved by a colleague in another
  timezone holds an instant that is not a day boundary here, and "is before 6 Jan 21:00" is
  the true sentence for it (F8). On reopening, the box shows the day for a boundary instant
  and the canonical instant otherwise;
- `web/src/ui/datetime.ts` gains the function and its header is amended: it says today that
  what the frontend sends for a picked date is UTC, which stays true of the expiry picker it
  was written for and is not true of a filter (F8).

The Search page's inline builder uses the same `ValueInput` and so the same box.

The server's refusal (1) remains the backstop for a saved view or an edited request, and the
Advanced popover shows it whenever there is one, not only for a tree no chip row can express.

**5. A popover stays inside the viewport (#13).** `Popover.tsx` measures its panel when it
opens and, when the panel's right edge would pass the viewport's, shifts it left by the
overflow, never past the left edge. One implementation, so the View menu and every other
popover built on it get the same behaviour. No caller changes. It lands before 6, because 6
turns an overflowing panel from a scrollbar on the content area into one on the page (P17).

**6. The sidebar and the table toolbar stay in view (#16).** At the `shell` breakpoint and
wider: the sidebar is `sticky top-0` and `self-start`; `main` drops `overflow-y-auto`, which
never scrolled (P4) and which stops anything inside it sticking to the window; the table
toolbar is `sticky top-0` with the page's ground behind it and a stacking order above the
table. The window remains the scroller. Below the breakpoint nothing changes.

- The sticky element is the one with test id `table-toolbar`. The filter chips are inside it
  (P17), so the chips and the controls stay together and a chip row that wraps makes the
  stuck block taller, not hidden.
- `main` gets no `overflow` of its own on either axis. Any value but `visible` on one axis
  makes it a scroll container again and the toolbar stops sticking to the window.
- **`main` stops absorbing sideways overflow** (P17, F16). Today anything wider than `main`
  scrolls inside it; afterwards it scrolls the page, and the sticky sidebar goes with it. The
  records table keeps its own wrapper, and popovers are contained by 5. Whether any other
  page overflows is not established, so AC5 asserts it per route.
- The bulk toolbar that appears under the table toolbar when rows are selected does not
  stick. It is where it is today, at the top of the page.

**7. Unsaved table state survives leaving the page (#14, Q3).** A pure module,
`table-view/tableViewSession.ts`, and a hook that uses it:

- stores `{version, principalId, selectedViewId, config}` under `gw.table-view.<type key>` in
  `sessionStorage` whenever the filter, sort, grouping, columns, pins or selected view change;
  density is left out because it has its own persistence;
- on mount, a stored entry for the signed-in principal is read through the existing
  `parseTableViewConfig` **as the table's initial state**, not applied by an effect after the
  first render, and the default saved view is then not applied on top of it. An effect would
  send one unfiltered query first, and those unfiltered rows are what the table keeps on
  screen if the restored filter is then refused (F15);
- a stored `selectedViewId` that is not in the saved views list when it arrives becomes "no
  view", so `Save` is never offered against a view somebody deleted (F15);
- a stored entry that names a field since removed behaves exactly as a saved view naming one
  does today: the server refuses it and the page says so. It cannot trap anyone, because the
  View menu is in the toolbar on every table and choosing any view there, "no view" included,
  replaces the stored state (F15);
- storage access is guarded as `ui/density.ts` guards its own;
- **what the tab holds after the person changes** (F14). An entry is filter values, which can
  be a name, an email address or a record key. Sign-out removes every `gw.table-view.` key
  **before** it calls the sign-out route, so a failed request cannot leave them. A session
  that ends any other way (it expires, or is signed out from another tab) leaves them, so
  **every sign-in sweeps the keys and removes each entry whose principal is not the one who
  just signed in**, whether or not that table is ever opened. A read of an entry for another
  principal still ignores and removes it, as the last line of defence;
- choosing a saved view replaces the stored state, as it replaces the live state today.

**8. Columns can be pinned (#18, Q5).**

- `ColumnStateConfig` gains `pinned?: string[]`, field keys. Absent, as in every view saved
  before this change, means "the display field" (`effective_display_field_key`). An explicit
  empty list means nothing is pinned.
- A pinned column is `position: sticky` with `left` equal to the summed widths of the pinned
  columns before it in the current order, computed by a pure function over the header sizes.
  With only the display field pinned that is `left: 0`: the cell scrolls with the table until
  it reaches the wrapper's left edge and stops there (P6, measured for this answer).
  Columns are not reordered by pinning. The last pinned column carries a right-hand rule,
  **drawn as an inset shadow or a pseudo-element, not as a border**: the table collapses its
  borders and a border on a sticky cell stays behind when the cell sticks (P6, F17).
  Pinned cells are opaque and still show the row's hover and selection tint; body cells are
  transparent today and the tint is on the row, so a pinned cell takes its background from
  the row's state.
- Stacking, in one place: pinned cells sit above the cells that scroll under them and below
  the sticky toolbar; a popover's panel is inside the toolbar's own stacking context, so it is
  above both (P17). No popover opens from inside a table cell today (`Popover` is used by
  `Chip`, `ViewMenu` and the record page's relation picker), so a pinned cell traps nothing.
- Pinning is not capped. Pinned columns wider than the table leave nothing else in view; the
  Columns popover is in the toolbar, which is always reachable, so unpinning is too.
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
true and are left as they are. `CHANGELOG.md` says, in the terms of "What changes" 1's table,
which filter values are now refused and what a saved view holding one looks like.

**10. A refused filter is never shown as an empty result (#15, found by the adversarial
pass, F6 and F7).** Two places in `TableView.tsx` where Q4's refusal would otherwise read as
something else:

- **First load.** A saved view is shared, and one per type is the default for everybody. If
  it holds a literal the rule refuses, or a restored session entry does, the first query of
  the page is refused and there are no previous rows to keep. Read at `TableView.tsx:285` and
  `:809-822`: the table then shows `No records match this filter`, which is false. When the
  filter was refused and no earlier result exists, the table area shows the server's message
  under the title `This filter could not be applied.` with the existing `Clear filter`
  button, and not the empty state. The view still opens, its chip is marked and carries the
  message, and nothing is dropped from it silently: the person removes or re-picks the value
  and saves.
- **Export.** `handleExport` awaits the request with nothing catching a failure
  (`TableView.tsx:482-489`, `:666`), so `Export CSV` under a refused filter does nothing at
  all. Today the same click exports the wrong rows. The failure is shown in the page's
  existing error alert, title `Could not export.`, with the server's message.

No file that defines CI is touched. No migration. No new dependency.

## What does not change

- SQL emission, `glosswork.compiler`, and every index. The access model. The schema engine.
- Relative date tokens, their grammar, their resolution and their message. Only the refusal of
  an unknown one gains the field key.
- That a token resolves in UTC. `@today` is UTC's today on the server, and a day picked in
  the browser is the viewer's, so for a viewer west of UTC in the evening the two name
  different days. That is true of tokens today and is not this change's to settle.
- What the write path accepts for a `date` or `datetime` field value. **For the planned
  timestamp change to pick up:** the write path checks with the lenient parsers and stores
  what it was given. Measured (F1): a record written with `due: 2026-1-7` and
  `last_used: 2026-1-7T0:0:0Z` is accepted and stored as sent, `due eq 2026-01-07` then does
  not find it, and it sorts as text. After this change `eq 2026-1-7` is refused, so such a
  value cannot be matched by equality at all until it is rewritten. CSV import shares the
  check. The strict functions this change adds to `timeutil` are there to be reused.
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
  the server would refuse; it must never hold back one the server accepts, with one stated
  exception: a bare day with `is` or `is not` on a `datetime` field, which the server accepts
  and which means one second (What changes 4). The token grammar is not mirrored in the
  browser.
- **A typed input keeps its own text.** The comma list and the date box each hold what was
  typed and commit beside it. Neither is drawn from the committed value while the person
  types, and each is tested key by key.
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
2. Confirm that no existing test sends a date filter literal the rule in "What changes" 1
   refuses. The adversarial pass measured none (P13) with the refusal alone patched in; this
   step repeats it with the real rule, the midnight rewrite included: run the backend tests
   that send a filter, record the count, and stop and report if it is not zero.
3. Server: the strict check in `timeutil`, the date-literal rule in `filters.py`, the field
   key on an unknown token's refusal, their tests, the capability descriptions.
4. Server: `blank_clears` in `CsvService`, the route's parameter, summary and description, the
   result's two new members, regenerated `schema.ts`.
5. `CommaListInput` (#17), with a key-by-key component test for the general and the relation
   branch.
6. `DateValueInput`, the day arithmetic and the chip sentence (#15), with unit tests for the
   arithmetic in at least one timezone west of UTC and one east, and a key-by-key component
   test.
7. `Popover` viewport containment (#13). Before 8, for the reason in "What changes" 5.
8. Shell and toolbar stickiness (#16).
9. Session state (#14).
10. Column pinning (#18): config, offsets, rendering, Columns popover.
11. Import wizard checkbox and dry-run summary (#19); the refused filter on first load and the
    export failure ("What changes" 10).
12. Run the whole local suite once, both Playwright projects included; repaint baselines and
    record the count against "Baseline repaint".
13. Hand over for verification against the Accept block.
14. Closeout in two commits: the plan's final edits, then the specifications and the deletion.

### What the build recorded, 2026-10-12

**Step 1, how each new assertion failed against the unfixed tree.** "Main" below is a clean
checkout of `main` at `9e4b8a2` with only the new spec file added. The order in which this was
done is deviation D1.

| Assertions | Measured against | Result unfixed |
| --- | --- | --- |
| AC1, the date-literal tests | the branch before step 3 | 72 of 80 failed: every refused literal answered 200, or 422 without the new sentence; an unknown token carried no field key; a bulk update and an export went through. 8 passed: six fences (a canonical date on a date field, three tokens, two for the list operator), and `lt` and `gte` with a date-only value on a datetime field, which were already right by text ordering (F9) |
| AC2, the clear tests | the branch before step 4 | 23 of 23 failed, each at its first read of `cleared`, which the result did not have. The fences among them (`null` and `\N` stored as text, a cell of spaces, a row whose matching field is blank) would have passed without that read |
| #17, two tests | main | both failed: the box read `v1v2` and the request carried `["v1v2"]` |
| #15, nine tests | main | all failed: `Apply` stayed enabled for `7 Jan 2026` and a chip for it appeared; a typed day was sent as typed and not as an instant; there was no calendar input; `is` with a day was sent; the Advanced builder sent `"7"` on the first key; `@tod` put nothing on its chip; no request was ever refused with 422, so the two saved-view tests timed out waiting for one |
| #13, two tests | main | both failed: the View menu was 165.125 pixels past the right edge at 1280 and at 800, and the content area gained a sideways scroll. The Columns popover half of the 800 test is a fence, see D8 |
| #16, the scroll test | main | failed: the sidebar's first link, the chip row, the toolbar and the identity block were all out of view after a 1500 scroll |
| #16, the top bar at 800 | main | passed, a fence by construction, not counted |
| #16, no sideways page scroll on nine routes at 1280 and at 960 | main | passed at both widths, so no shell route overflows today. Fences, not counted |
| #14, five tests | the branch at `2da75c7`, before any step 9 code | all failed: 60 rows and no chip after returning; nothing in `sessionStorage` |
| #18, five tests | the same | all failed: the Name cell was at x -339 after an 800 scroll where 249 is right; no pin control; no sticky cell |
| #19, the wizard test | the same | failed: no checkbox in upsert mode |

**Step 2.** The real rule, midnight rewrite included, refused no date literal sent by an
existing backend test: `uv run pytest -q -m "not structural"` with a plugin logging every
refusal from `filters._resolve_scalar` ran 2,147 tests green and logged 0 refusals. The same
plugin logged 69 refusals over the new date-literal file, so it fires. Four test files that
are in the working tree and not in the repository were left out of that run.

## Accept

Each is a command. Read each exit code directly, never through a pipe.

- **AC1 (#15, server).** `uv run pytest -q tests/test_filter_date_literals.py` exits 0. It
  seeds rows either side of a boundary and asserts: `7 Jan 2026`, `01/07/2026`, `banana`,
  `2026-01-07T00:00`, `2026-01-07T00:00:00.000Z`, a value with an offset, `2026-1-7`,
  `2026-1-7T0:0:0Z`, a lower-case `z`, `2026-02-30` and a number are each 422
  `validation_failed` whose `field_key` is the field and whose message names the accepted
  forms, on a `datetime` field, a `date` field and `created_at`; a full datetime on the `date`
  field is refused; `2026-01-07` and `2026-01-07T00:00:00Z` return the same count on the
  `datetime` field, compared with a count computed in the same test from the seeded rows;
  `@today-7d` still resolves; an unknown token is refused with the field's key in
  `field_key`; a `between` with one bad end and an `in` with one bad member are refused;
  `bulk_update_records` with a refused literal, dry run and live, changes no record; a search
  and a CSV export with a refused literal are 422 with the same message.
- **AC2 (#19).** `uv run pytest -q tests/test_csv_blank_clears.py` exits 0. It asserts:
  without the flag a blank cell leaves value and version unchanged; with it the value is
  gone and `cleared` and `cleared_by_field` say so; a dry run with it returns the same two
  members and writes nothing; a column absent from the header is not cleared; a row that
  creates is unaffected; a required field's blank is a row error naming the field and the
  batch writes nothing; a relation column's blank removes no link; `null` and `\N` are still
  stored as text; the OpenAPI operation's description contains `blank_clears`. And, from the
  adversarial pass: two rows that each clear the same unique field on different records
  import cleanly; a row matched on a unique field is still matched with the flag on; a row
  shorter than the header clears its missing fields with the flag and changes nothing
  without; two rows naming one record, the first setting a field and the second blanking it,
  report a count equal to what the commit removes; a commit after another write has filled a
  field reports that clear although the earlier dry run did not; `blank_clears=true` with
  `mode=create` is 422; a blank `multi_select`, `boolean` and `user_ref` cell each clear; a
  created row with a blank cell takes the field's default.
- **AC3 (generated types).** `uv run pytest -q tests/test_generated_api_types_fresh.py` exits 0.
- **AC4 (frontend units).** `npm --prefix web run test` exits 0, and includes: `CommaListInput`
  typed key by key commits `["v1","v2"]` for a text field and for a relation field; the date
  box typed key by key ends holding `2026-01-07` and commits one value; the day arithmetic
  table, per operator, in a zone west of UTC and one east, with `2026-02-31`, a two-digit
  year and a six-digit year refused; the chip sentence for a boundary instant and for one
  that is not; `tableViewSession` round trip, other-principal entry ignored, the sign-in
  sweep, an unknown view id, storage that throws tolerated; pin offsets;
  `parseTableViewConfig` with `pinned` absent, empty and stale; the wizard's checkbox
  discarding the last dry run.
- **AC5 (browser, the seven items).**
  `npm --prefix web run e2e -- --project=e2e table-pilot-findings.spec.ts` exits 0. One test
  per item, on a seeded type wider and taller than the viewport:
  - #13: with the View menu open, the panel's right edge is at or inside `window.innerWidth`,
    and neither `document.documentElement` nor `main` has `scrollWidth` greater than
    `clientWidth`. Repeated at 800 wide for the Columns popover.
  - #14: a chip is applied, the sidebar's Activity link is clicked, the sidebar's link back is
    clicked; the chip and the filtered row count are there. A second type's table shows no
    chip. Choosing a saved view replaces the state. After sign-out and sign-in the table is
    unfiltered and `sessionStorage` holds no `gw.table-view.` key. With the session cookie
    removed instead of signing out, then a sign-in as a second account, no key written by the
    first account is left, for a type the second account never opens included.
  - #15: a select chip plus a datetime "is before" chip. Every value is typed with
    `pressSequentially`, never set whole. Typing `7 Jan 2026` sends no request (an absence
    assertion that waits its whole window), leaves `Apply` disabled and shows the refusal
    naming the formats. Picking the day, and separately typing `2026-01-07`, shows the row
    count the API returns for the same filter in the same run. With a record stamped in the
    middle of that day, "is on or before" the day includes it and "is after" the day does
    not, each compared with a count the API returns for the instant the browser sent; "is"
    with the day leaves `Apply` disabled with the hint. Typing `@today-7d` is sent as typed,
    and typing `@tod` and applying shows the server's message on the chip with the rows still
    on screen and no page-level alert. The same in the Advanced builder. A saved view seeded
    with `7 Jan 2026` and selected after the table has loaded shows the server's message on
    its chip and in the Advanced popover, with the table's previous rows still on screen. The
    same view made the default and opened on a fresh load shows `This filter could not be
    applied.` with the server's message, and the text `No records match this filter` is not
    on the page. `Export CSV` under that view shows `Could not export.`.
  - #16: after scrolling the window 1500, the sidebar's first link and the chip row are inside
    the viewport, and the Columns popover opened from the stuck toolbar is the topmost element
    at its own centre. At 800 wide, after the same scroll, the top bar has scrolled out of
    view exactly as it does on `main` today. That last one is a fence: it passes on the
    unfixed tree by construction and is not counted. And at 1280 and at 960 wide, on each of
    the shell's routes (a table, a record, the import wizard, Inbox, Schema, Activity, People,
    Setup, Search), `document.documentElement.scrollWidth` is not greater than its
    `clientWidth`. Only the table route was measured on the unfixed tree (1280 against 1280);
    step 1 records the others, and where this passes unfixed it is a fence. It is here
    because of what 6 removes.
  - #17: `v1, v2` typed with `pressSequentially` in the chip and in the Advanced builder; the
    request's filter value is `["v1","v2"]` and the row count equals the API's for that filter.
  - #18: by default the display field's cell stays at the same x after the wrapper is scrolled
    800 sideways and a far column moves; pinning a second field from the Columns popover holds
    it beside the first; unpinning releases it; an inline edit, a resize and a reorder each
    work on a pinned and an unpinned column; `Save as new`, reload, select the view: the pins
    are back; a view saved with no `pinned` member pins the display field. With the window
    also scrolled 1500, the stuck toolbar is the topmost element where it crosses the pinned
    column. The right-hand rule is paint, which no locator reads: the visual project gains one
    baseline of the table scrolled sideways (see "Baseline repaint").
  - #19: the wizard's checkbox is absent in create mode and present and off in upsert mode;
    a dry run with it on states the clears; ticking the box after a dry run disables `Commit`
    until a new dry run.
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
records which. One new baseline, `table-wide-pinned-scrolled`: the wide table scrolled
sideways with the display field stuck, which is the only place the rule on a stuck cell is
ever checked (F17). Any other repaint is a finding.

Actual, 2026-10-12: 4 repainted and 1 added, the count expected, and the four moved by more
than the plan expected. `table-default`, `table-grouped`, `table-selected` and `table-wide`
each gained one thin vertical line to the right of the Name column, in the header and in every
row. Beyond that line, because the records table now separates its borders (D5), **every row
line below the header sits one whole pixel higher** than in `main`'s images (119 to 118, 157 to
156 and on down), the header row is one pixel shorter, and `table-selected` is 540 pixels tall
where it was 541. Between 4.3 and 5.4 percent of pixels differ in each of the four. Those
numbers are the verifier's, measured against `main`'s images on 2026-10-12; the build run had
read the diff images and called it a fraction of a pixel, which was wrong. What a person
would see is the new line and nothing else they could point to. Whether `table-wide` would
have passed the tolerance with the line alone is not established. `table-wide-pinned-scrolled`
is new. The two CSV wizard baselines passed unchanged: both fixtures are in create mode, where
the checkbox is not shown. Every other baseline passed unchanged.

The fix run (D11 to D13) repainted nothing: the visual project passed, 50 scenarios, with no
baseline rewritten.

## Adversarial pass

Run once, on 2026-10-10, by a session that did not write this plan, after the maintainer
answered Q1 to Q5. It started from the author's six doubts, which are F9, F8, F11, F15, F18
and F16 below, re-measured the premises the design leans on (P12 to P17, and the correction
in P6), and then looked for what nobody had named. "Folded" means the plan's text above was
changed in place. No finding replaces a mechanism or adds a component: the design is the one
the maintainer's five answers describe. Three findings change what a person or an agent sees
beyond what those answers said, and are marked **new behaviour** so the approval covers them
knowingly.

**The date rule and the date input (#15, Q4, Q2)**

- **F1. The rule as written would have kept the wrong answer for `2026-1-7`.** It said
  "checked with `timeutil.parse_date`", and that parser accepts unpadded dates, full-width
  digits and a lower-case `z` (P12). `lt 2026-1-7` answers 60 of 60 today on both field
  types. *Folded:* the check is a full match and then a calendar check (What changes 1, AC1).
  The write path has the same leniency and stores what it is given; that is recorded for the
  timestamp change under "What does not change" and is not fixed here.
- **F2. A picked day was right for "is before" and wrong for three other operators. New
  behaviour.** A day sent as its first second makes "is on or before 7 Jan" leave out the
  7th, "is after 7 Jan" include it, and "is 7 Jan" match nothing (P14 measured 0 for `eq`;
  the other two are arithmetic on the rule and were not run). That is the reported defect on
  operators the report did not use. *Folded:* the browser picks the first or the last second
  of the day by operator, and holds back a bare day for "is" and "is not" with a hint (What
  changes 4, AC4, AC5). The server is not made operator-aware: for an agent a date-only
  value on a `datetime` field is one stated instant, and the capability text says so.
- **F3. The date box as described could not be typed into.** "Anything else commits
  `undefined`" over a box drawn from the committed value empties the box on the first key
  (P16). It is #17's fault rebuilt in the fix for #15. *Folded:* the box keeps its own text,
  a constraint says so for both inputs, and the tests type key by key.
- **F4. A mistyped token emptied the table, and the plan sends every `@` text to the
  server.** The unknown-token refusal names no field (P15), so the page-level alert shows and
  the rows go, on each key in the Advanced builder. *Folded:* the refusal carries the field
  key (What changes 1, AC1, AC5). The grammar is still not mirrored in the browser.
- **F5. Who sends a date literal, and whether the refusal can strand an agent.** The table in
  "What changes" 1 is the inventory. No existing test is refused (P13). Four forms that
  answer correctly today are refused afterwards (P14), and each gets a message naming the
  form and quoting what was sent. *Folded:* the inventory, and the message now says "UTC,
  whole seconds, ending in Z", because a fractional second and `+00:00` are what a program
  sends by default and "UTC" alone does not say what to change.
- **F6. A saved view stays openable and nothing is dropped from it, but on a fresh load it
  would have read "No records match this filter". New behaviour.** Saved views are shared and
  one is everybody's default. *Folded:* What changes 10, AC5, and a line in the changelog.
  How many such views exist anywhere is not established and cannot be without reading
  deployments' data.
- **F7. Export under a refused filter did nothing at all.** Read, not run. *Folded:* What
  changes 10, AC5.
- **F8. Two meanings for `2026-01-07`, by who sends it.** The maintainer chose the viewer's
  day for the browser on 2026-10-10; an agent's date-only value is UTC. *Accepted as
  decided, and written down:* the capability text states the agent's meaning; the chip shows
  a time whenever an instant is not a day boundary for the viewer, which is what a shared
  view saved in another timezone holds; the header of `ui/datetime.ts`, which says the
  frontend sends UTC, is amended; `@today` staying UTC is listed under "What does not
  change". Also *folded:* the day arithmetic refuses `2026-02-31`, which a `Date` rolls into
  March without complaint.
- **F9. The midnight rewrite (the author's first doubt).** Compared with today's text
  ordering it changes `lte`, `gt` and `eq` only for a value stamped at 00:00:00 exactly, and
  makes all three consistent with comparing instants. No test, spec or golden case filters a
  `datetime` field with a date-only literal (P13). *Accepted, no change.*

**CSV clear (#19, Q1)**

- **F10. Putting the clears in `_build_row` breaks two things.** The in-file uniqueness pass
  would report two rows clearing one unique field as a collision on `None`, and the upsert
  lookup reads the same values (read at `csv.py:231-233` and `:549`). *Folded:* the clears
  are added after both (What changes 2, AC2).
- **F11. `cleared` was not defined for two rows naming one record, and a commit's count
  could be stale (the author's third doubt).** *Folded:* one clear is one record and field
  with a value before and none after; rows fold in file order; the commit counts inside its
  transaction. The cost at the 20,000 row cap is nothing extra, because each row's lookup
  already fetches the record and discards it (`csv.py:545`, `:551-557`).
- **F12. What the flag does to each kind of cell and row.** Required field: a row error at
  validation, reported by the dry run, whether or not a value is held. Unique field: cleared,
  and the value is free again. Relation: untouched, add-only. Attachment column: the file is
  refused at the header as today. A row that creates: untouched, defaults still apply. A row
  shorter than the header: its missing cells are blank and are cleared. A cell of spaces: not
  blank, and what it does today (an error for a number or a date, the spaces stored for
  text, an empty list for a multi-select) is unchanged. And the one that matters for the
  export, edit, re-import journey the flag exists for: the import has never checked versions,
  so a blank cell clears a value added since the export. *Folded:* each is stated in What
  changes 2 and pinned in AC2; the help text and the description say the last one.
- **F13. Two ways to clear without having been told. New behaviour** for the second.
  Ticking the box after a dry run left `Commit` enabled, since only the other options discard
  the last dry run (read at `CsvImportWizardPage.tsx:140-145`, `:225`). And
  `blank_clears=true` with `mode=create` did nothing and said nothing. *Folded:* the box
  discards the dry run; the server refuses the combination.

**Unsaved state (#14, Q3)**

- **F14. What the tab holds after sign-out and a sign-in as someone else.** Sign-out as
  planned removed the keys after the request, so a failed request left them; and a session
  that expires, or is ended from another tab, never runs sign-out, so the first person's
  filter values stayed in the tab for every table the second person did not open. *Folded:*
  remove before the request, and sweep at every sign-in (What changes 7, AC4, AC5). What
  stays true: until the tab is closed or someone signs in, an expired session's entries are
  readable by whoever has the tab.
- **F15. Stale entries (the author's fourth doubt).** A removed field, a deleted view, a
  filter the server now refuses. *Folded:* the entry is the table's initial state, so no
  unfiltered query runs first; an unknown view id becomes "no view"; a refused filter shows
  as F6 describes. *Accepted:* an entry naming a removed field shows the server's refusal as
  a saved view does today, and the View menu clears it.

**Layout (#13, #16, #18)**

- **F16. Removing `main`'s overflow moves sideways overflow to the page (the author's sixth
  doubt).** Measured: 1280 to 1445 with the menu open (P17). *Folded:* 5 lands before 6, and
  AC5 asserts no sideways page scroll on each route at two widths. Whether a page other than
  the table overflows today is not established.
- **F17. P6 measured the answer the maintainer did not choose, and the rule cannot be a
  border.** The stickiness was measured with the checkbox, By and Key columns frozen as
  well. Re-measured for "only pinned fields": it holds, at the wrapper's left edge (P6). A
  border on a stuck cell is left behind under collapsed borders. *Folded:* P6, What changes
  8, and one new visual baseline, since nothing else can see paint.
- **F18. Three sticky layers (the author's fifth doubt).** Measured with the toolbar above
  the pinned cells: a popover opened from the stuck toolbar is topmost at its centre and the
  toolbar is topmost over the pinned column (P17). No popover opens from inside a table cell,
  so a pinned cell's stacking context traps nothing. *Folded* as one stacking rule in What
  changes 8 and an assertion in AC5. *Accepted:* the bulk toolbar does not stick, and pinning
  is not capped.
- **F19. Popover containment (#13).** Nothing found beyond its ordering with 6 (F16). The
  panel is measured when it opens and not again on a window resize while open. *Accepted.*

## Deviations from the approved plan

Recorded as the build went. None replaces a mechanism the plan chose except D5, which is why
it is written at length.

- **D1. Step 1 was done item by item, not all up front.** The backend tests were written and
  run unfixed first (steps 3 and 4 followed). Each browser test was then written before its
  own fix and run against a tree that did not have that fix. The #13, #15, #16 and #17 tests
  were run against a clean checkout of `main`, because the server half of #15 was already on
  the branch by then; the #14, #18 and #19 tests were run on the branch before any of their
  code existed. Every assertion was measured failing, or is labelled a fence, in the table
  under the checklist.
- **D2. AC1 asks for "an `in` with one bad member", and no list operator reaches a date
  field.** The operator matrix gives `date` and `datetime` only `between` as a multi-value
  operator, so `in` on either is `invalid_operator` (400) before any literal is looked at. The
  test pins that as a fence, and `between` is tested with a bad lower and a bad upper end.
- **D3. F6's premise was wrong about a default view, and the fix is one line more than the
  plan describes.** "What changes" 10 says the first query of the page is refused and there
  are no previous rows to keep. Run in a browser: the saved views arrive after the first
  render, so the table has already answered one unfiltered query when the default view is
  applied, and those unfiltered rows are what stayed on screen under the refused view's name.
  The default view now discards that earlier result when it is applied, and a placeholder
  result (the previous query's rows, shown while the next is pending) is no longer taken for
  the last real one. The outcome is the one the plan asks for: `This filter could not be
  applied.` and never the unfiltered rows.
- **D4. The chip reads an instant as a day only when it is that operator's edge of the day.**
  "What changes" 4 says the first or last second of a day reads as the day alone. Taken
  literally, `is before` the last second of the 7th would read "is before 7 Jan", which is
  false by a day. `is before` and `is on or after` read the first second as the day; `is on or
  before` and `is after` read the last; `is` and `is not` always show the time. The box on
  reopening follows the same rule.
- **D5. The pinned column's row rule is the cell's own border, and the records table now
  separates its borders.** "What changes" 8 says the rule is an inset shadow or a
  pseudo-element because a border stays behind when a cell sticks. That is true of the
  vertical rule, which is an inset shadow as planned. It is also true of the **row** rule
  under every pinned cell, which the plan did not consider: stuck, a pinned cell has no bottom
  rule at all. The first attempt dropped the cell's bottom border and drew it as an inset
  shadow; in the browser the pinned cells' rules sat a pixel off their rows' and the text in
  some cells moved a pixel at rest. The records table (and only it) now uses
  `border-separate` with no spacing, so each border belongs to its cell and travels with it.
  These cells only carry a bottom border, so nothing doubles. Cost, as the verifier measured
  it against `main`'s images: every row line below the header sits one whole pixel higher, the
  header row is one pixel shorter, the table is one pixel shorter overall, and 4.3 to 5.4
  percent of pixels differ in each of the four table baselines (see "Baseline repaint"). The
  plan expected stickiness to move nothing at rest. A person sees one new thin line to the
  right of the Name column and nothing else they could point to. The functional and visual
  geometry assertions on the table all still pass.
- **D6. `RecordService` gains one public method, `get_record_in_txn`.** The commit counts
  clears from what each record held just before its update, inside the write transaction, as
  the plan says. That needs a read on the transaction's own connection, and there was none. It
  checks `read` on the record's type like `get_record`, and is added to the list of service
  methods no route calls (`tests/test_api_infra.py`), beside the three writes it sits with.
- **D7. Eleven existing assertions on the import response were updated.** They compared the
  whole response with a literal, and the response now always carries `cleared` and
  `cleared_by_field`, zero and empty without the flag, as "What changes" 2 says.
- **D8. Two additions to AC5's browser assertions.** At 800 wide the Columns popover fits
  without help on the unfixed tree, so that half is a fence; the same test also opens the View
  menu at 800, which does overflow unfixed. And the #14 tests assert that the keys exist
  before asserting they are gone, since "no key is left" is true of a product that never
  stored one.
- **D9. Wording this build wrote, for the maintainer's eye.** The plan fixes the two refusal
  sentences, the checkbox label and help text, the three hints and the two titles, and those
  are used as written. It does not fix: the two type descriptions in
  `services/capabilities.py`; the import route's summary and description; the pin toggle's
  visible text (`Pin`, `Unpin`); the calendar input's accessible name (`<field> value: pick a
  day`); and the clears sentence for one value (`1 value will be cleared: Notes (1).`) and for
  none (`0 values will be cleared.`), which follow the approved plural form.
- **D10. Smaller choices the plan left open.** The date hint is printed by the date input
  itself, under the box, in the chip popover and the Advanced builder alike, and the popover's
  generic "Add a value" line is not shown beside it. A `between` on a date field shows one
  hint for the pair. The calendar is the browser's date input reduced to its picker button,
  styled for and checked in Chromium only. The sign-in sweep also runs when a page loads
  already signed in, which is where an OIDC redirect lands. The Columns popover is 32 pixels
  wider, to fit the pin toggle. A stuck toolbar extends the page's ground 6 pixels around
  itself with a shadow, so rows do not show in the gaps. A committed list that was not typed
  is shown joined with a comma and a space.

The fix run of 2026-10-12, after the verification failed at `0bc0b27`:

- **D11. A filter the server refuses shows no rows, by every path. This departs from the
  approved plan, on the maintainer's decision.** "What changes" 4 and 10, F4 and F6, and AC5
  kept the table's previous rows on screen when a typed chip or a saved view picked after
  load was refused, and marked the chip. Rebuilt by the verifier in a browser, that is a saved
  view's name over every record with no alert: the reported defect, still reachable. Asked on
  2026-10-12 what a refused filter should show, having been told it goes against the approved
  plan, the maintainer answered: "No rows, and a message (Recommended)". So for a typed chip,
  the Advanced builder, a saved view picked from the View menu, a default view and a filter
  restored from the tab alike, the table area reads `This filter could not be applied.` with
  the server's sentence and `Clear filter`, and shows no row and no count. Nothing holds an
  earlier result any more, so D3's special case for a default view is gone with it. It
  applies to every `validation_failed` answered while a filter is set, whether or not it names
  a field a chip carries; before, one that named none showed `Could not load records.` over
  `No records match this filter`. `Export CSV` from that state is refused by the server with
  the same filter and downloads nothing.
  **What it does not cover:** while the refused request is in flight, the previous rows are
  still on screen, as they are for a filter about to succeed. Nothing knows the answer until
  it arrives, and blanking the table for every pending filter would flash on every good one.
  The verifier timed that window at 6 ms locally; on a real network it is one round trip.
- **D12. A chip's colours are chosen once per property, and an open chip now paints.** The
  refused chip carried `border-bad text-bad` and painted exactly like its neighbour, because
  those classes sat in one list with the chip's own `border-line-2 text-ink` and the
  stylesheet's order decided, not the list's. The cause is in `web/src/ui/Chip.tsx`, which
  this change had not touched, and it was losing a second state the same way: measured at
  `0bc0b27`, an open chip computed `bg-surface` and not `bg-human-soft`, on `+ Add filter`
  and on `Columns` alike, so no chip in the product painted as open (docs/DESIGN.md 3 says a
  focused chip is `human-soft`). `Chip` now takes a `tone` and picks one fill, one text
  colour and one border colour from its whole state. Visible consequence beyond #15: every
  chip with its popover open is now tinted. No baseline shows an open chip, so none moved.
- **D13. The calendar button is this product's own icon over an invisible date input.** D10
  described the browser's date input reduced to its picker button. The verifier found that
  drew an empty square in Chromium 151. The button is now an inline SVG with the date input
  lying over it at the same size and zero opacity, and a click calls `showPicker`. Seen
  drawing its icon in headless Chromium 151 (a screenshot, and an assertion that counts
  painted pixels inside the button). Not checked: a headed window, Firefox, Safari, and
  whether the native picker itself opens on a click, which no headless run shows.
- **D14. The browser assertions for a refusal read what a person reads.** They read the
  visible sentence, the absence of every record row and of the count, and the chip's computed
  border and text colour against the theme's `bad` colour and against an accepted chip beside
  it. Before, they read the chip's accessible name, which passed while the page showed every
  record. Run at `0bc0b27` before the fix, four failed: the typed `@tod` chip, `@tod` in the
  Advanced builder and the saved view picked after load each found no `This filter could not
  be applied.`, and the default-view scenario, which already showed the message, failed on
  the chip's painted colour alone. One unit assertion that pinned the old behaviour ("keeps
  the rows on screen") now asserts the opposite.

## Durable content moved out of this plan

Nothing yet. See "What changes" 9 for where it goes.
