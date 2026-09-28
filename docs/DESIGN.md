# Design system: Counterpart

**Status:** the design system DD-41 names. **Sections 2, 3 and 4 ship: the tokens, the type
scale, the radii, the focus rule, dark mode, the favicon and the login lockup.** Section 6 ships,
and **section 8 is complete**: the shell (8.1), the table page (8.2), the record page
(8.3), the Inbox (8.4), first run (8.5), People & agents / Setup (8.6) and Activity (8.7) are
all live. Where this document and the running product disagree, one of them is wrong and it is
not safe to assume which: every subsection of 8 records what shipped rather than what was
intended.

**Read this the way you read `docs/DATA_MODEL.md`:** as a specification with numbers, not as a
mood board. Every value here is a decision. An implementing agent that wants a different value
opens the question in the pull request, and the answer lands back here.

**Reference stills** are in `docs/design/`. They are the approved comps for each screen until
Playwright visual baselines replace them. They are pictures of intent, so a pixel difference
between a still and the implementation is not a defect; a difference in structure, hierarchy,
color role or copy is.

---

## 1. The five rules

Everything below follows from these. If a component decision is not covered, derive it from
here and record it.

1. **Two hands, one ledger.** People and agents are peers on one surface, and the design shows
   both in every list, record and feed. A row, a comment, a change and a proposal all carry
   whose hand made them, using one attribution primitive (section 6).
2. **Blue is what people do. Amber is what agents did.** `human` colors the primary action,
   selection, links, the signed-in person and every person's avatar. `agent` colors every
   agent's avatar, its entries in a feed, the change bar beside a value it wrote and the badge
   on anything waiting for a person because an agent asked. **Amber is never a button**, because
   agents do not click. Blue never marks an agent.
3. **Semantic status is a third family and never borrows from the first two.** Won, lost,
   overdue, failed validation use `ok`, `warn` and `bad`. `warn` is red-orange, not amber, so a
   warning cannot be read as an agent.
4. **Typography-led, no illustration, no icon set.** Hierarchy comes from the display face, the
   text ramp and spacing. No sparkle, robot, brain, chat bubble or gradient anywhere, in product
   or marketing. Where an icon becomes necessary (mobile navigation), it is a separate decision.
5. **Comfortable by default, compact by choice.** The persona reads more of each row than it
   scans rows. Rows are 38px unless the user chooses 32px, and the choice persists per view.

---

## 2. Tokens

Tokens live in `web/src/index.css` inside the single `@theme` block, as today. The default
Tailwind palette stays cleared (`--color-*: initial`), so a component cannot reach an unthemed
color. No raw hex in component classes.

### 2.1 Color

| Token | Light | Dark | Role |
| --- | --- | --- | --- |
| `--color-ground` | `#f6f7f9` | `#12141b` | Page and sidebar background |
| `--color-surface` | `#ffffff` | `#1a1d26` | Tables, cards, inputs, header |
| `--color-raised` | `#fafbfc` | `#20242f` | Table header, hovered row |
| `--color-sunk` | `#eef0f4` | `#0e1016` | Inset panels, code and prompt blocks, neutral pills |
| `--color-ink` | `#1c2030` | `#edeff5` | Text |
| `--color-ink-2` | `#5d6479` | `#a2a8b8` | Secondary text, labels |
| `--color-ink-3` | `#8f95a6` | `#6f7587` | Placeholder, metadata, disabled |
| `--color-line` | `#e3e6ed` | `#272b37` | Hairline dividers, table rules |
| `--color-line-2` | `#cbd0db` | `#363b49` | Control borders |
| `--color-human` | `#2e5bff` | `#7c95ff` | Primary action fill, selection, focus ring, person avatar ring |
| `--color-human-ink` | `#2247d4` | `#a3b4ff` | Text on light/dark ground in the human family (links, person initials) |
| `--color-human-soft` | `#e9eeff` | `#1e2748` | Person avatar fill, selected row, access banner |
| `--color-human-line` | `#b9c8ff` | `#34448a` | Person avatar border, banner border |
| `--color-on-human` | `#ffffff` | `#0d1330` | Text on a `human` fill |
| `--color-agent` | `#e09a0f` | `#f5b841` | Change bar beside an agent-written value, live indicator |
| `--color-agent-ink` | `#8c5e00` | `#f7c968` | Agent label text, agent initials |
| `--color-agent-soft` | `#fff3d6` | `#3b2e12` | Agent avatar fill, agent feed-entry wash (at 40% over surface), proposal badge fill |
| `--color-agent-line` | `#f3d48a` | `#6b5320` | Agent avatar border, badge border |
| `--color-ok` / `-soft` | `#1f9d55` / `#e3f5ea` | `#5fcf8c` / `#163325` | Positive status |
| `--color-warn` / `-soft` | `#d9480f` / `#fdebe3` | `#f0895c` / `#3a2015` | Caution status (red-orange; see rule 3) |
| `--color-bad` / `-soft` | `#d93838` / `#fde8e8` | `#f07a7a` / `#3d1c1c` | Danger, destructive text, removed values |

**Status borders are derived, not authored.**
`--color-ok-line`, `--color-warn-line` and `--color-bad-line` are
`color-mix(in srgb, var(--color-<family>) 30%, transparent)`. `Alert` and `Badge` need a border
in the status families, and three families times two themes is six more hand-picked values to
keep in step; a border that is a wash of its own family cannot drift out of step with it.

**`info` is not a fourth semantic role.** The
informational variants of `Alert` and `Badge` render neutral: `sunk` fill, `line` border,
`ink-2` text, which is the treatment 3 already gives an unmapped select option. Rule 3 is why
a fourth hue was not added: every hue is another thing that can be misread as the human or the
agent family.

Dark values are defined twice, exactly as the artifact does it: once under
`@media (prefers-color-scheme: dark)` guarded as `:root:not([data-theme="light"])`, and once
under `:root[data-theme="dark"]`. The light set is the bare `:root`. A user theme choice writes
`data-theme` on `<html>` and persists in `localStorage` under `gw-theme`; the default is
`system`. `body` sets `background-color: var(--color-ground)` explicitly.

Contrast, measured 2026-09-08 for the values above (re-measure if any value moves): `ink` on
`surface` 16.2:1 light / 14.6:1 dark; `ink-2` on `surface` 5.9 / 7.1; `human-ink` on
`human-soft` 6.2 / 7.3; `agent-ink` on `agent-soft` 5.1 / 8.5; `on-human` on `human` 5.2 / 6.6.
Floors: 4.5:1 for any text pair, 3:1 for control boundaries.

**`ink-3` is in this list, and it does not clear the floor on every ground.** Measured 2026-09-09:
`ink-3` on `surface` 2.99:1 light / — ; `ink-3` on **`ground` 2.79:1 light and 4.00:1 dark**. For
comparison `ink-2` on `ground` is 5.50 / 7.73. `ink-3`'s role is placeholder, metadata and disabled
text, and placeholder and disabled text are outside the WCAG floor; **any other use of it is not**,
and informational text set in `ink-3` on `ground` fails this section's own stated floor in both
themes.

It went unnoticed until text was first put on `ground`, because this paragraph measured five
pairs and `ink-3` was not one of them. 4.3's
workspace line was specified in `ink-3` and ships in `ink-2` for exactly this reason. Any new
use of `ink-3` states which ground it sits on and whether it is exempt.

### 2.2 Type

| Token | Value | Role |
| --- | --- | --- |
| `--font-display` | `"Bricolage Grotesque Variable", "Bricolage Grotesque", "Helvetica Neue", Arial, sans-serif` | Page titles, record titles, Inbox headlines, first-run question, wordmark, impact numbers |
| `--font-sans` | `"Figtree Variable", "Figtree", "Helvetica Neue", Arial, sans-serif` | Everything a person operates or reads in the UI |
| `--font-mono` | `"DM Mono", ui-monospace, SFMono-Regular, Menlo, monospace` | Record keys, versions, timestamps in ISO form, agent labels, field keys, API names, code |

Packages (verified 2026-09-08 on npm, installed at 5.3.0): `@fontsource-variable/bricolage-grotesque`,
`@fontsource-variable/figtree`, `@fontsource/dm-mono` (400, 500).

**The `Variable` suffix in the two stacks is load-bearing, and was found by running the product
rather than by reading the stylesheet.** Those packages declare their `font-family` as
`Bricolage Grotesque Variable` and `Figtree Variable`. The first edition of this table named the
plain families, which match nothing: every heading and every word of UI text would have rendered
in Helvetica Neue, in the product and in all 35 re-recorded visual baselines, with nothing red
anywhere. The static names are kept behind the variable ones for a browser that has the face
installed locally. Imported from `main.tsx`
and bundled into `dist/`, never fetched at runtime (FR-P1, DD-41). `@fontsource/ibm-plex-sans`
and `@fontsource/ibm-plex-mono` are removed.

Scale: `11.5 / 12.5 / 13.5 / 14 / 15 / 18 / 22 / 26 / 30 / 36` px, mapped to
`--text-2xs … --text-5xl` so no component reaches an off-scale size. Line heights 1.35 at
and below 14px, 1.45 at 15 to 18, 1.15 at 22 and above. Display face at 22px and above is
tracked `-0.025em`; at 26 and above `-0.03em`. Weights: sans 400/500/600; display 600 only in
product (700 allowed in marketing); mono 400/500.

Uppercase labels (table headers, sidebar section labels) are 11.5px, weight 600, tracked
`+0.06em`. Digits that line up in a column use `font-variant-numeric: tabular-nums`.

*`--text-5xl` (36px) exists because 8.5 calls for that size and the scale stopped at 30. It is
read by the first-run question and by nothing else. Two things were
measured while adding it and are worth keeping here. The rule above -- that nothing reaches an
off-scale size -- **is not currently a property of this codebase**: `grep -rn 'text-\[' web/src`
finds fourteen arbitrary values, including the Inbox `<h1>` at `text-[28px]` and an impact number
at `text-[30px]`, so the two named steps above 26 have had their values in use as literals for
longer than they have had readers. And a search for `text-4xl|text-3xl|text-2xl` cannot see any of
that, which is how one search concluded that nothing in the product exceeded 22px. Measure
this rule with `text-\[` in the pattern or it will keep reporting what it wants to hear.*

### 2.3 Space, radius, elevation

- Spacing stays on Tailwind's 4px grid.
- Radii: `--radius-ctl 6px` (buttons, inputs, chips that are not pills), `--radius-card 8px`
  (cards, tables as a single card, gloss panel), `--radius-frame 12px` (sidebar avatar tile,
  dialogs). Pills and filter chips are `999px`. Person avatars are circles; agent avatars are
  `6px` squares (section 6).
- Elevation is color, not shadow: `ground → surface → raised`. One shadow exists,
  `0 1px 2px rgba(28,32,48,.05), 0 10px 30px rgba(28,32,48,.07)` (dark: `rgba(0,0,0,.5)` /
  `.45`), used only on dialogs and popovers.

### 2.4 Density

Two presets, a user toggle in the table toolbar, persisted per saved view and per user:

| | Comfortable (default) | Compact |
| --- | --- | --- |
| Table row | 38px | 32px |
| Table header | 34px | 30px |
| Cell text | 13.5px | 13px |
| Avatar in a row | 20px | 18px |

Nothing else changes with density.

---

## 3. Color rules in practice

- The **primary button** is `human` fill with `on-human` text. There is exactly one per screen
  region.
- **Selection** (selected row, active sidebar item, focused chip) is `human-soft`.
- **Focus ring**: `2px solid var(--color-human)`, `outline-offset: 2px`, on every interactive
  element, styled once in the base layer.
- **Links** in prose are `human-ink`, underlined on hover only. So is any other text in the
  human family, including the active navigation link. `human` is fill, selection, focus ring
  and avatar ring; it is never text on a light ground. Measured: `human` on `human-soft` is
  4.47:1, below the 4.5 AA floor the sidebar contrast assertion guards, and `human-ink` on
  `human-soft` clears it.
- **Agent presence** on a value: a 3px `agent` bar in the left gutter of the value, for the
  field(s) changed in the record's most recent agent-authored version.
- **Agent presence** in a feed: the event row gets a wash of `agent-soft` mixed at 40% over
  `surface` (`color-mix(in srgb, var(--color-agent-soft) 40%, transparent)`).
- **Things waiting on a person because an agent asked** (pending proposals): badge with
  `agent-soft` fill, `agent-line` border, `agent-ink` text.
- **Status pills** (select values that carry state): `ok-soft/ok`, `warn-soft/warn`,
  `bad-soft/bad`, and `human-soft/human-ink` for "in progress" states. Which option maps to
  which tone is chosen by a stable hash of **the option key alone** — not per field — so one
  value carries one colour on every screen it appears on; a tone that shifted between the table
  and the record page would read as though the shift meant something. A blank or absent key uses
  `sunk/ink-2`. (A `tone` on select options is a schema change and its own issue; until then, the
  hash. **The hash is not semantic**: `at_risk` landing on `bad` is luck, and a status named
  `Won` may come out red.)

---

## 4. The mark and the wordmark

### 4.1 The mark: interlinear gloss

Two bars: a line of text and a shorter line written above it, offset to the right. It is the
interlinear gloss, which is what the product is named after.

Construction, in a `0 0 100 100` viewBox:

- Tile: `rect 0 0 100 100`, `rx 24`, fill `human`.
- Lower bar: line from `(24, 62)` to `(76, 62)`.
- Upper bar: line from `(46, 38)` to `(76, 38)`.
- Both bars: stroke `agent`, `stroke-width 12`, `stroke-linecap round`.

Variants:

| Variant | Tile | Bars | Use |
| --- | --- | --- | --- |
| Tile | `human` | `agent` | App sidebar (28px), favicon, social image, workspace avatar |
| Bare | none | `ink` (or `agent` on the marketing site) | Inline beside text below 20px; monochrome print |
| Mono | `ink` | `surface` | Single-color contexts |

Minimum tile size 16px. Below 20px inline, use the bare glyph. The amber bars measure 2.2:1
against the blue tile; that is a logo, exempt from text contrast rules, but it is the reason the
bars are 12 units thick and round-capped, and the reason the tile never shrinks below 16px. The tile's blue does not change
with theme (it is `human` light in both; the favicon is one file).

Files: `web/public/favicon.svg` (tile), `web/src/brand/mark-tile.svg`,
`web/src/brand/mark-bare.svg`, `web/src/brand/mark-mono.svg`. There is no plain-text twin of
the mark: in `llms.txt`, the install page and any markdown context the brand is the word
**Glosswork** alone. Two bars do not survive as characters, and plain text does not need them.

### 4.2 Wordmark

`Glosswork` set in Bricolage Grotesque, weight 600, optical size at or above 24, tracked
`-0.03em`, sentence case. Always preceded by the tile at the wordmark's cap height, gap equal
to 0.4 em. Never set in the sans, never all caps, never without the tile above 20px.

### 4.3 Where the brand appears in product

- Sidebar top: tile (28px) plus the **workspace name** in the display face at 15px, with
  "6 people · 3 agents" beneath in **`ink-2`**. The product name does not appear in the shell; the
  workspace's does.

  Three points, each with its reason. **The tone is `ink-2`, not `ink-3`**: `ink-3` on `ground`
  measures 2.79:1 light and 4.00:1 dark against 2.1's stated 4.5:1 floor, and this line is
  informational text rather than placeholder or disabled text, so the exemption does not reach it.
  **The name comes from `GW_WORKSPACE_NAME`** (DD-28), which an operator sets. **An unset name
  renders the tile alone**, with the counts still beneath it — it deliberately does not fall back to
  "Glosswork", because this bullet's own last sentence rules that out. What "6 people · 3 agents"
  counts is DD-28.
- Login: tile (40px) plus wordmark, centered above the form; nothing else.
- The pair avatar (section 6) does **not** use the tile between the two avatars; they overlap.

---

## 5. Voice

- Sentences, names first, dates in words, no jargon the person did not type.
- Say "sales-agent moved this to Negotiating", "Claude Code wants to remove the Notes field.
  13 prospects have one", "Nothing here yet. Tell your agent what you want to keep track of."
- Do not say "AI", "assistant", "automation", "smart", "magic". No exclamation marks.
- A button says what happens: **Approve**, **Decline**, **Save view**, **New prospect**,
  **Post**. Never "Submit", "OK", "Yes".
- Errors say what went wrong and what to do, in that order, without apology.
- A **display vocabulary layer** sits between API names and the UI: operators render as
  "is", "is not", "is before", "is after", "contains", "is empty"; field types render as
  "Text", "Long text", "Select", "Number", "Date", "Link", "Attachment", "Person", "Checkbox".
  The API name is available on hover and in the schema editor's key column.
  **One module, `web/src/ui/vocabulary.ts`** — the layer is singular, so a second table of words
  anywhere is the defect this rule exists to prevent.
  **An operator's word depends on the field type**: `gt` reads "is after" on a `date`
  and "is more than"'s sense — "is greater than" — on a number, because "Amount is after 1000" is
  not English. The six words this section names are specified here; the other eighteen were
  chosen in the module, and `web/src/ui/vocabulary.ts`'s header records which is which.
  `is_null` reads "is blank" rather than "is empty", because `multi_select` accepts both operators
  and one word for two would make a filter chip ambiguous.
  **Negation is a clause, not an operator word:** a `not` node reads `Except where Notes contains
  renewal`. It cannot read "is not" — `neq` already owns that, and `{"not": {"op": "eq"}}` and
  `{"op": "neq"}` are different queries that would then read identically.
  `tests/test_display_vocabulary.py` asserts that every `(field type, operator)` pair the API
  accepts has a word, so a new operator cannot ship rendering its API name to a person.

Formatting: numbers with grouping separators and the field's own `scale` (`68,000`, `1.50`);
dates as `Sat 13 Sep` within the current year, `13 Sep 2025` otherwise, with a relative hint
("in 6 days") on record pages; timestamps as `Today 09:14`, `Yesterday 17:02`, then `5 Sep`; ISO
form only in mono metadata and on hover. **Any value that truncates carries the full stored value
on hover**, not long text alone — a clipped `short_text` hides exactly as much — and the
hover shows the **stored** value rather than the displayed one, which is what makes "ISO form only
on hover" and 7.3's "its key is on hover" true without either needing its own implementation.
Cells rendering a derived summary rather than a stored value (a relation, an attachment
count) carry none.

*Money is not formatted by the field's currency and locale (`$68,000`): **no field config
carries a currency** — `CONFIG_KEYS` in `src/glosswork/fieldtypes.py`
gives `decimal` exactly `{precision, scale}` and `integer` exactly `{min, max}` — so that rule
could not be implemented, and a guessed `$` on a euro column is worse than no symbol.
Separators ship; a currency field config is its own issue.*

A date-only value is parsed from its parts, never through `Date`'s string parser:
`new Date("2026-09-13").getDate()` is **12** in `America/New_York`, so every reader west of
Greenwich would see the previous day.

---

## 6. Attribution primitives

One component family renders every principal or agent label anywhere in the product:
tables, record headers, comments, activity, proposals, settings, pickers.

### 6.1 `Avatar`

| | Person | Agent |
| --- | --- | --- |
| Shape | circle | square, radius 6px |
| Fill / border / text | `human-soft` / `human-line` / `human-ink` | `agent-soft` / `agent-line` / `agent-ink` |
| Label | initials, sans 700 | two-letter code, mono 500 |
| Sizes | 24px default, 20px in rows, 18px compact rows, 32px in headers | same |

Initials: first letters of the first two words of the display name, uppercased. Agent code:
first letters of the first two hyphen- or space-separated parts of the label (`sales-agent` →
`SA`, `claude-code` → `CC`); a single-word label takes its first two letters. A service
account is an agent. A label with no display name shows its key.

Shape and color both encode the kind, so the distinction survives greyscale and
color-blindness. Never a circle for an agent, never a square for a person.

### 6.2 `Hand`

Avatar plus name: `[avatar] Dana Reyes` or `[avatar] sales-agent`. When an agent acts on a
person's token, append `for Sam Okafor` in `ink-3`. Names are sans 600; agent labels are sans
600 in `agent-ink` (not mono, in prose contexts).

### 6.3 `Pair`

When a record was last touched by both a person and an agent, or when summarising a thread
with both, render both avatars overlapping by 6px, person first, with the caption
`Sam & sales-agent`. This is the only place an ampersand appears in the product and it is a
character in a sentence, not a mark.

### 6.4 The "By" column

The **records table** leads with `By`: the `Avatar` of the hand that last changed the row.
Width 56px, header the word "By", the avatar alone — at 56px there is no room for a name, so
the name rides in the avatar's accessible label and its tooltip.

It sits **second, after the selection checkbox**, which is always first because it exists to
feed the bulk toolbar. The order is `select`, `By`, `Key`, then fields.

**Only the records table.** The other five tables in the product have no "hand that last
changed the row" to render: audit rows *are* events and already carry a principal, CSV wizard
rows are CSV columns, schema-editor rows are field definitions, permissions rows are principals,
and blast-radius rows are affected records.

**It offers no sort, and it does not render a `Pair`.** Both were specified here and neither is
built; the reasons are recorded rather than the omissions left to be rediscovered:

- *Sort.* A display column at the table boundary has no field for the query to sort on, and a
  sort key for "kind" would be a ninth pseudo-field. DD-20 reserves exactly eight names, refused
  at `fieldtypes.validate_field_key` and published in `describe_capabilities`, so adding one is
  its own decision and its own change.
- *`Pair`.* Rendering one here means knowing the *previous* version's hand, which the record row
  does not carry. Two routes were priced when this was written: a second denormalised column
  pair, and a batched sidecar read taking the last two writes per record from `audit_events`
  (partitioned by `request_id`, since one write is one request id). The second is cheaper, needs
  no migration and no backfill, and is correct for history; if this is revived, it is that one.

**What that costs the reader, stated plainly:** the table cannot show that a person *and* an
agent have both been in a record recently. A row an agent edited five minutes ago and a person
edited an hour ago shows an agent square alone. That signal lives one click away, in the
record's audit timeline, which carries every version and every hand.

This is a different fact from an agent acting on a person's credential, which the row *can*
always answer: it carries both the principal and the agent label, and 6.2's `Hand` renders
`sales-agent for Sam Okafor` wherever there is room for a name.

### 6.5 When the label is missing

The primitive shows the principal alone and **never fabricates an agent**.

A missing label used to be the norm: REST writes dropped `X-Agent-Label` outright and proposals
over REST were unattributed. DD-17 fixed both, so a label is now present on writes from every
surface and its absence is the exception. It is still a real case, and each one is legitimate:

- a person at a keyboard, which is most writes;
- a browser session, which DD-17 refuses a label for by design — `surface="ui"` carrying an
  agent label would be a contradiction, and the UI sends no such header;
- any row written before the deployment upgraded past the migration that added the column, where
  the backfill found no field-writing audit event to attribute.

A principal with no label is drawn by kind: a person is a circle, and a **service account is an
agent** (6.1) and gets the square. That distinction is only renderable because the `principals`
sidecar carries `type`; without it a service account renders as a person, which is a wrong
answer rendered confidently rather than an absent one.

---

## 7. Components

### 7.1 Button

Height 34px (28px `sm`), padding `0 14px`, radius `ctl`, sans 600 13.5px. Variants:
`primary` (`human` fill), `default` (`surface` fill, `line-2` border), `quiet` (no border),
`danger` (`default` with `bad` text). Disabled at 45% opacity, never hidden (DD-42).

### 7.2 Input, Select, Textarea

Height 34px, `line-2` border, radius `ctl`, `surface` fill, sans 14px; placeholder `ink-3`.
Native `<select>` stays native and is restyled.

### 7.3 Pill

Select values in tables and records. Height 24px, radius 999px, sans 500 12.5px, tone per
section 3. The pill shows the option's display label; its key is on hover.

"And records" matters: a select rendered as bare text on the record page reads as a sentence
fragment rather than as the state it names, and section 3's "one option key, one colour on every
screen" could not hold across the two screens it is about.

### 7.4 Filter chip and popover

Filters render as sentence chips: `Stage is not Lost ×`. Height 28px, radius 999px, `line-2`
border. The `+ Add filter` chip is dashed. Clicking a chip or the add chip opens a popover
with field, operator (display vocabulary) and value. **The query does not run until the
condition is complete**; an incomplete condition is shown in the popover, not in the chip row,
and never produces a server error. Group and sort are the same chip grammar
(`Group by Stage`, `Sort: Next action`). The NOT toggle is a switch inside the popover labelled
"Exclude matches".

### 7.5 Access banner (DD-42)

One per object-type screen, `human-soft` fill, `human-line` border, `human-ink` text, radius
`ctl`, 13px: "You can **edit** prospects. Dana Reyes can give you more access." The level word
is bold. It is the only banner that uses the human family; alerts use semantic tones.

### 7.6 Card

`surface`, `line` border, radius `card`. Cards have a header row (sans 600 14px, optional
`ink-3` hint right-aligned) separated by a `line` rule. A table is one card; rows are not
cards.

**One implementation, `web/src/ui/Card.tsx`.** The heading is passed in as an element rather
than a string, because its *level* depends on the page and its size and weight do not: on the
record page the two cards are the `h2`s beneath the record's `h1`, and `heading-outline.spec.ts`
walks that outline. A heading rendered *above* a bordered box instead of inside its header row
reads as a section label with a table under it, which the approved comp rules out.

### 7.7 Table

Header 34px, `raised` fill, 11.5px uppercase labels, `line-2` bottom rule. Rows per section
2.4, `line` rules, hover `raised`, selected `human-soft`. Group headers 32px, `ground` fill,
sans 600 13px with the count in `ink-3`. Keys in mono 12px `ink-3`. Numbers right-aligned,
tabular. The record link is the display value, not the key. Row actions appear on hover at the
row's right edge. Long text truncates with the full value on hover.

### 7.8 Activity event

Grid `24px 1fr`, gap 12px, padding `12px 16px`, `line` rule beneath. Header: `Hand`, then
context ("for Sam"), then time right-aligned in `ink-3` 12px. Body 13.5px. Changes made in the
same version render as pills beneath the body: `Stage: Proposal sent → **Negotiating**`.
Agent events carry the wash from section 3. Comments and changes are the same component with
different bodies.

### 7.9 Inbox item and impact tiles

Inbox item: grid `24px 1fr`, title sans 600 13.5px, second line `ink-2` 12.5px, third line
`ink-3` 11.5px (kind and time). Selected item `human-soft`. Impact tiles: `card` with a display
face number at 30px (`bad` for what is removed, `ok` for what is preserved, `ink` for neutral)
and a 13px `ink-2` caption; three across, `auto-fit, minmax(150px, 1fr)`.

### 7.10 Prompt block

The copyable text on the first-run screen. `surface` card, sans 14px, with API names in mono
inside it, and a `Copy` button top-right that leaves 76px of right padding.

### 7.11 Gloss panel

*8.2 and 8.3 both refer to "the gloss panel"; section 7 defines it here.*

A **disclosure**, not a popover: a `sunk` panel with radius `card`, opening directly beneath the
thing it explains, toggled by a control that carries `aria-expanded` and `aria-controls`. It holds
a description — the object type's on the table page (behind an "About" control beside the title),
a field's on the record page (behind its `?`). A disclosure rather than a transient anchored
surface because descriptions run to several lines, and because DD-42 applied to viewports means the
text must stay reachable at every width.

It emits no heading of its own. A panel that is unmounted while closed cannot carry a page's
outline, so a heading there would be absent from the default page — see 10.

**One panel, two placements.** `ui/GlossDisclosure.tsx` exports the composed disclosure —
which the table page's "About" control uses — and its two halves, `GlossToggle` and `GlossPanel`.
The Details card needs the halves: its rows are a `130px 1fr` grid, and a panel rendered inside
the label cell is trapped in a 130px column where the prose it holds has to run the full width of
the row it explains. The panel's markup exists once either way; what varies is who places it.
A control whose label is not a name — the record page's `?` — carries an `aria-label` naming the
field it explains, or four buttons called "?" tell a screen-reader user nothing (10).

### 7.12 View menu

*8.2 sends CSV export "to the view menu"; this section defines it.*

One popover on the right of a table's toolbar, opened by a control naming the current view
("View", or the loaded view's name, truncated with the full name on hover). It holds everything
about *which* view is loaded and what happens to it: the saved-view list, `Save`, `Save as new`,
`Set as default`, the new-view name input, and `Export CSV`. It exists because 8.2's toolbar is
one row and those six controls have no other home; collapsing them is what makes the row fit at
800px with a named view loaded.

---

## 8. Screens

Each screen lists what changes, what does not, and the test hooks that must survive. The DOM
is load-bearing (DD-41): 322 test call sites query by role, label, test id and text. Restyle
elements in place; restructure only where a screen is redesigned outright and its tests are
rewritten with it.

### 8.1 Shell

**Changes.** Top bar becomes a 224px left sidebar on `ground`: workspace tile and name with
"N people · M agents"; `Inbox` with a count badge (pending proposals plus unread mentions);
`Search`; a "Tracking" section listing object types with record counts; a "Workspace" section
with `People & agents`, `Activity`, `Setup`; the **theme control**; the signed-in person's `Hand`
and role at the bottom with sign-out. Below 960px the sidebar collapses to a top bar with a menu
button. Search opens from the sidebar item and from `/`; the search box no longer lives in the
header.

**Does not change.** Routes. `/login` remains the one signed-out route. The `current-principal`
test id remains on the signed-in identity. Object-type navigation still comes from
`list_object_types`.

*Five things the list above does not say, recorded here rather than left to be rediscovered:*

- **The Workspace section carries a `Schema` entry**, which the list above omits. The shell is the
  schema editor's only entry point — `/schema/new` is linked from `/schema` and from nowhere else —
  so building this list literally would leave both routes alive and neither navigable. Wherever the
  schema editor eventually belongs, it is reachable from the shell until something else reaches it.
- **`People & agents` and `Setup` are two entries**, at `/people` and `/setup`; `/settings` only
  redirects. One route gets one link, because two links to one route put two `aria-current="page"`
  elements on it. The Workspace section reads `People & agents`, `Activity`, `Schema`, `Setup`: this
  list's order with `Schema` kept in the slot the bullet above gives it, and `Setup` last.
- **`Inbox` points at `/inbox`**, where the proposals it counts live, so the badge and its
  destination agree.
- **The menu control below 960px is the word `Menu`**, set in the UI face. Rule 4 of section 1
  leaves the icon-set decision open, and the menu control is the first place that needed one;
  answering it here would have closed that decision by accident. **Rule 4 therefore still
  stands.**
- **The Inbox badge renders only for a caller whose credential can read what it counts.**
  `GET /api/v1/schema-proposals` requires the `admin` scope and `role_scope` gives a `member`
  only `write`, so a member's request is refused. The item is always shown and the number is
  omitted; a zero would be the client asserting "nothing is waiting" when it was refused
  permission to look. DD-42's rule is satisfied by the item's own presence, as it is for the
  cross-type proposals panel that decision already exempts.

*One structural note for whoever builds the next screen.* Exactly **one** of the sidebar and the
top bar is in the DOM at a time, chosen in JavaScript from `matchMedia`, not both with one
hidden by CSS. Two CSS-hidden shells put two `current-principal` test ids in the document, and
`e2e/constants.ts::signInAs` — which every spec in the suite calls — resolves that test id
strictly. The breakpoint lives in `web/src/index.css` as `--breakpoint-shell` and in the hook as
a media-query string, pinned against each other by a test because a custom property is not
readable by `matchMedia`.

### 8.2 Table page

**Changes.** Page title in the display face with the count and view name in `ink-2` on the
same line, and the primary `New <type>` right-aligned at its end (DD-44). **The count
is the type's unfiltered live count** (`record_count`), so the title says how big the type is
while the footer says what this view shows — no two numbers on the page mean the same thing. The
type's description no longer prints above the table; it is available from an "About" control
beside the title, which opens the gloss panel (7.11). Toolbar is one row: filter
chips, then group, sort, columns and density **as chips** (7.4) right-aligned, then Import, then
the view menu (7.12). The filter builder and column picker are popovers. `By` column first.
Pills for select values, formatted numbers and dates, truncated values with the full stored value
on hover (5). Footer: "Showing 10 of 13 · 3 hidden by your filter" left — the hidden clause only
when a filter is active, and never negative — "4 of these rows were last touched by an agent"
right, **worded as the page** because only the fetched page is knowable without a new aggregate on
the read path; paging controls right when more than one page.

*Three points, each recorded rather than silently applied.* **`ink-2`, not `ink-3`:**
`ink-3` on `ground` measures **2.79:1** in light and **4.00:1** in dark, against the 4.5 floor 2.1
sets and 10 says holds in both themes, resolved the same way as the sidebar's workspace
line. **Density is a chip**, which 7.4's two-form list did not anticipate: with a named saved
view loaded, a visible density pair wrapped the toolbar at 800px by 34px.

*`New <type>` (DD-44) is **not in the toolbar**. It does not fit in the toolbar's last slot,
measured, not argued. The toolbar's children are a constant 716.8px and its narrowest width across
the viewport range is 688px, at exactly the 960px shell breakpoint where the sidebar appears, so the
row overflows by 28.8px before any control is added; a type-named primary is 139px at its worst.
More permanently, the label carries the object type's **name**, which is user data of unbounded
length, so no one-row guarantee can be made about a row containing it. It sits on the title line
above, which is `flex-wrap` and whose geometry nothing asserts. The empty state carries the same
control.*

*And a known gap: **"Toolbar is one row" is not true today.** It wraps at 960, 980 and 760 px on
the `vis_wide` fixture. `ui-visual.spec.ts` asserts one row at 1280 and at 800, which sit either
side of the 960–1000px failure band, so the suite has never seen it. Section 9's "wrap at 640" is
wrong in the same way.*

**Does not change.** The keyset paging model. Saved views semantics (FR-U3). Bulk toolbar
behaviour. Inline cell editing. CSV import and export entry points (they move to the toolbar's
Import and to the view menu).

**The filter grammar this page shows.** The chip row is a flat AND of conditions; anything the
row cannot express — nested `and`/`or`, a `not` around a group — lives behind an `Advanced` chip
holding the tree builder, and a loaded filter that is not a flat AND renders as a single
`Advanced filter` chip. See **DD-43**. A condition never reaches the network until it is
complete, and a value the server refuses for some other reason reports in the offending chip's
own popover rather than in a page-level alert that empties the table.

### 8.3 Record page

Title is the display value
(`object_types.effective_display_field_key`, DD-23) in the display face at 30px, falling back to
the record key when that value is empty or no display field resolves; the key is a mono chip
beside "Version N" and a `Hand` "last touched" line. Two columns at or above
`--breakpoint-shell`; below it they stack **Activity first**, for the whole range. Details card
(label 130px, value; the value is a `<button>` named `Edit <Field>` and edits in place through
the same write path as the table) and Activity card (events newest first, composer at the
bottom: placeholder "Reply. Agents read this too.", primary `Post`). Both card headers are `h2`s
inside the card's own header row (7.6), so the page's outline is `h1` then two `h2`s. Every
field has a `?` control that opens the field's gloss beneath it, spanning the full width of its
row. Values changed in the latest agent-authored version carry the agent bar. Attachments stay
on this page (FR-U2).

**An Activity event is a live comment or a write that produced a version, and nothing else.**
`audit_events` already carries a row per comment, so appending the audit trail to the comment
thread renders every live comment twice and keeps a bodyless copy of every deleted one. The
count is exactly `comments + versions`, which `record-detail/activityEvents.ts` owns and its
test pins. A link or an unlink therefore does not appear on this page; it is still in `/activity`
and still in the audit trail.

**Newest-first is a reversal, not a descending sort.** Timestamps are stored at second precision
(`timeutil.DATETIME_FORMAT`), so a write and the comment about it routinely share one, and a
comparator returning 0 on a tie leaves ties in the order they were pushed — ascending. Both
streams are also **paged forward from the oldest row**, so the card drains both cursors, bounded
by a named constant, before rendering anything: `auditGroups.ts` derives version numbers by
counting forward from the record's creation, and the agent bar asks which fields the *most
recent* agent version changed. Neither is answerable from page one. When the bound is reached
the card says older activity is not shown rather than presenting a partial history as a whole one.

**Four points where the reference still shows something the product cannot do:**

- **The "last touched" line is a `Hand`, never a `Pair`.** This section said `Pair`/`Hand` and
  the reference still shows a `Pair`. `records.updated_by` plus `updated_by_agent_label_id` is
  6.2's case — an agent acting on a person's credential — and 6.3's `Pair` needs the *previous*
  version's hand, which the record row does not carry. 6.4 already priced that gap for the `By`
  column. A `Pair` here would claim two hands from data describing one.
- **Linked records are title-only pills, with no status.** This section asked for "pills for
  their status" and nothing on the wire carries one: `list_link_summaries` projects
  `{key, id, display}` and `display` is the target's *title*. Beyond the projection cost, no
  field is marked as a status, so choosing one would be guessing at a display role — the thing
  DD-23 exists to stop being guessed. The pill reads the key **and** the title
  (`ENG-004 Two-phase fulfillment review`), as the reference still does: this is the one screen
  where the pill is the only place either appears.
- **There is no single "Linked" row.** Every field gets a row, in schema order, relations
  included. A type with two relation fields has two descriptions and one row to hang them on,
  and every field's gloss has to be reachable. A type with exactly one relation field looks
  precisely like the "Linked" row this section was written from.
- **The agent bar marks the most recent agent version's fields minus any a later version
  changed.** Section 3's wording is the bare set; a bar beside a value a person rewrote
  afterwards attributes their sentence to an agent, which is what the colour rule exists to get
  right.

**Linking a record is a picker, not a typed key.** A relation field's "Link a record"
(`one`) or "Add another" (`many`) control opens an anchored, non-modal popover (7.4,
`ui/Popover.tsx`), because linking is a quick action beside the field it fills. The popover
searches the target type's title, its `effective_display_field_key` (DD-23), with a
case-insensitive `contains` through the existing query route. The record key is not searched,
since nobody types one, but every option reads key then title exactly as the pill does, so two
records sharing a title stay distinguishable. Records already linked are not offered, and one
just picked is not offered again before the page's links refresh. A target whose title field
does not support `contains` lists its records without a search box. A target the caller cannot
read says "You can't read the records this field links to." in the panel rather than hiding the
control, and a refused read shows the server's own forbidden message (`ui/Alert.tsx`). Nothing is
requested until the popover opens.

**The picker's search is debounced, and 8.7's no-debounce rule does not reach it.** 8.7 replaces a
debounce with a commit boundary because a filter condition being typed is "complete" at every
keystroke, so debouncing it only delays sending half a thought. The picker's search has no
incomplete state: an empty box sends no filter, and every non-empty value is one complete
`contains`. Its 300 ms debounce (`hooks/useDebouncedValue.ts`) only spares the server a request per
keystroke. Filter composers still commit on completion and do not use the hook, and DD-43's gate
(`filters/everyFilterComposerGates.test.ts`) still watches every module that composes a filter.

**Does not change.** The write path, version checks and 409 merge dialog. The audit timeline's
data; it is re-rendered as Activity events. The `RecordDetailView` test ids where the element
still exists — `field-list`, `field-value-<key>`, `relation-field-<key>` and the
`Edit <Field name>` accessible name all survive.

### 8.4 Inbox and proposal

`/inbox` lists pending schema proposals, newest first. Two panes at
or above 960px: list (300px) and detail; below it, `/inbox` is the list and `/inbox/<id>` is the
detail, so the browser's Back button returns to the list. The selection is a **route**, not
component state, which is also what lets the API's approval message send a person to one named
proposal.

The proposal detail: kind badge and "Raised today at 09:28 · waiting on an administrator";
headline `<Agent> wants to remove the <Field> field from <Type>`, with its own sentence per
change kind; "It says:" with the rationale quoted; three impact tiles; a plain-language
paragraph stating exactly what Approve and Decline do and that the decision is recorded under
the approver's name; the sample values struck through; `Approve` (primary) and `Decline`
(danger). Approve and Decline are offered to administrators only; a caller whose credential
cannot read proposals at all sees the page, no list, and one sentence saying so.

Four details differ from the reference still, each because the still shows something the
product cannot do:

- **The middle impact tile reads "Snapshot / taken before anything changes", not "Saved".** The
  FR-S7 snapshot is written *during* approval, so `snapshot_ref` is null on every pending
  proposal. A tile saying "Saved" would claim a safety guarantee had already been honoured when
  it had not.
- **There is no "See all N" link.** `sample_values` is capped at five distinct values
  (`_IMPACT_SAMPLE_SIZE`) and no route returns the rest. The screen states both counts instead —
  "5 of 9 values shown" over "13 records are affected" — which is the first place in the product
  that distinguishes `non_empty_values` from `affected_records`.
- **There is no "Ask &lt;Agent&gt; why".** A comment can only exist on a record
  (`comments.record_id` is `NOT NULL`), and the only proposal-reading tool an agent has,
  `list_schema_proposals`, returns no comments — so the question would be one its recipient
  could never read.
- **Mentions and agent replies are absent.** No API surfaces either yet.

**Removes.** The "Pending schema proposals" card from Settings. The proposals API's *behaviour*
is unchanged — approval authority, the FR-S7 snapshot, the impact recomputation and the audit of
the decision are all untouched — but the document grew a `target` projection and two sidecars so
the sentence above has nouns to use (DD-25), and the list became a bounded page.

### 8.5 First run

**New.** When `list_object_types` is empty, `/` renders the first-run screen: the question
"What do you want to keep track of?" in the display face at `--text-5xl`; one paragraph; the
prompt block with the deployment's real MCP URL and a Copy button; a link to the schema editor
for building by hand. The copy of the prompt is the text in the reference still
(`docs/design/counterpart-firstrun-light.png`).

*Seven things the paragraph above does not say, recorded here rather than left to be
rediscovered:*

- **The screen does not replace "Object type not found".** That string is
  `ObjectTypePage`'s, for a key nobody created, and it is what the committed
  `object-type-not-found` baseline shows. The screen `/` actually opened on was
  `IndexRoute`'s `EmptyState`, "No object types have been created yet.".
- **"When `list_object_types` is empty" is not the whole condition.** That list is filtered
  to the types the caller holds `read` on (FR-I11) and every object type is closed by default,
  so an empty list means either "none exist" or "none you can see" and the client cannot tell
  them apart. **First run replaces the non-member branch only**; a `member` still reads the
  access sentence. A member handed a prompt that builds a schema has been told to do something
  their token would be refused for.
- **The question is 36px, and 36px was not on the scale.** 2.2 stopped at `--text-4xl` (30px)
  while saying nothing may be off it, and the Inbox `<h1>` already shipped at `text-[28px]`, so
  30px would have been a 7% step over a routine page title. 2.2 carries `--text-5xl: 36px`; this
  section names the token, not the number.
- **The second clause of the question is `agent-ink`.** Sampling the reference stills returns
  `(140, 94, 0)` light and `(247, 201, 104)` dark, which are exactly that token in each theme.
  The sentence is addressed to an agent, so it is set in the agent colour.
- **The URL does not come from `GW_BASE_URL` alone, and it never comes from the browser.**
  `GET /api/v1/workspace` carries `mcp_url`, composed server-side from `GW_BASE_URL` and `null`
  when it is unset; the client falls back to its own origin only on `null`. Composing it in the
  browser is wrong where it matters: the `/mcp` transport's Host and Origin allowlists are built
  from `GW_BASE_URL` (DD-15), so a UI reached on an origin outside that allowlist would print a
  URL this deployment refuses, and the URL's consumer is the agent rather than the person, which
  a laptop's `http://localhost:8000` does not serve. `null` is safe to fall back from because it
  is exactly the case where the allowlist is empty and the check disables itself.
- **The still's footer offers "Start with a template" and there is no template gallery.** The
  screen ships the schema-editor link alone. Its link is underlined on hover only, per section 3,
  where the still underlines it at rest; the specification wins.
- **The still is framed without a sidebar and the screen keeps the shell.** Every other still has
  one; the theme control and sign-out live there and nowhere else, and a second shell variant
  would need its own collapsed form below 960px (section 9). The Tracking section is empty on a
  fresh workspace, which is the honest rendering of a fresh workspace.

*One note for whoever builds the next screen.* **This screen has no visual baseline, on purpose.**
It is reachable in the functional suite by signing in as a grantless `creator`, but a principal
seeded into the *visual* database moves the sidebar's "N people · M agents" line, which
`shell-sidebar.png` and `shell-top-bar.png` both frame whole: one new baseline would cost two
repaints of baselines about something else. Mocking the response instead would make it the only
mocked baseline in a project whose whole point is that every pixel is a function of its own
fixtures. It is proven by component tests and by `e2e/first-run.spec.ts` instead.

### 8.6 People & agents, Setup

Settings is two routes, and `/settings` redirects to `/people` with
`replace` so Back leaves the app rather than bouncing between the old URL and the new one.

`/people` is three cards, each a `Card` (7.6) holding a table (7.7) at the comfortable density:

- **People** (admin only): `Person` as a `Hand` (6.2), `Email`, `Role` as the native select in
  the row, `Status`, and an unheaded actions cell. Invite is a native `<dialog>` opened from the
  card's header hint. The only-active-administrator guard disables the whole select and its
  sentence sits under the table. Each active local account's row also offers **`Reset password`**,
  before `Deactivate`, opening a native `<dialog>` named `Reset password for {name}` that
  says, before anything is submitted, that the person will be signed out everywhere and every token
  they hold stops working. The caller's own row never offers it: your own password is changed from
  `/setup`, with the current one (DD-13).
- **Agent labels**, grouped by owner, with the owner's `Hand` in a 7.7 group header: `Label`
  (mono), `Display name`, `Description`, `First seen`, `Last seen`, `Calls`, `Verified`. A row
  the caller owns carries an inline rename; every other row is read-only, which is exactly what
  `PATCH /agent-labels/{id}` enforces.
- **Service accounts** (admin only): `Account` as a `Hand`, which draws the agent square per 6.5,
  `Description`, `Status`, actions. Create is a native `<dialog>`.

`/setup` is four cards: personal access tokens (every signed-in principal; the inline mint form
and the once-only minted-token dialog are unchanged), **password** (every signed-in person),
the search index panel, and export.

*Seven points, recorded here rather than left to be rediscovered:*

- **"Last seen" for a person is not a fact this product stores.** `principals` has no such
  column, and the nearest thing, `access_tokens.last_used_at`, sees PAT traffic and never a
  browser session, so a column built on it would read "never" for everyone who signs in through
  the browser. The column is **omitted**. Adding it honestly means a migration, a write on
  session login and on credential use, and a decision about what "seen" means for a service
  account. The agent half of this section was always buildable because
  `agent_labels` does carry `first_seen_at` and `last_seen_at`.
- **Backup is not on this page, and the Export card says why.** `POST /api/v1/admin/backup` is
  live, but it is a write, and a session-authenticated write must echo `X-GW-CSRF`, a header no
  HTML form can set. Reaching it from the browser therefore means `fetch()` into a Blob, with an
  unbounded tar artifact resident in browser memory. DD-36 already reached the same conclusion
  for its neighbour: restore is an operator procedure. Export ships because `GET` is a safe
  method, so an `<a download>` streams it to disk under the route's own `Content-Disposition`.
- **`(unverified)` is not a warning badge.** `Badge tone="warning"` is the `warn` family,
  `#d9480f` — the red-orange section 1 rule 3 exists to keep away from agents "so a warning
  cannot be read as an agent". A label nobody has named is not a warning; FR-I6 accepts
  unknown labels and never rejects them. It is a `Verified` column reading `Yes` or `No`.
  `warn` survives on this screen in exactly one place, the search index's stale-chunk alert,
  which is a genuine caution about data.
- **`(inactive)` is a `Status` column that also says `Active`.** A Badge rendered only in the
  negative case makes "is this person still here" a question answered by noticing an absence. A
  column answers it for every row at once, which is most of the argument for the table.
- **A table inside a `Card` cannot use `tableWrapClass`.** That recipe carries
  `rounded-card border border-line bg-surface` and `Card` supplies all three, so reusing it draws
  concentric borders. `ui/tableClasses.ts` carries `cardTableWrapClass`, the horizontal scroll
  alone. The five tables that are not inside cards keep the recipe they have.
- **A timestamp cell is `tabular-nums`** (`timeCellClass`). 7.7 says numbers are tabular and a
  column of times is a column a reader scans down. It is also what makes a screenshot of such a
  column stable: a Playwright mask hides a cell's content but not its effect on layout, so
  proportional figures let a masked live timestamp move every column to its right.
- **A non-admin's `/people` carries one sentence saying why it is nearly empty.** Two of the
  three cards need the `admin` role (FR-I10), and a page called "People & agents" showing neither
  would promise what it withholds. It is not 7.5's access banner: that sentence names one object
  type and the level held on it, and this is a deployment-wide role, not a grant. The precedent
  is 8.1's Inbox badge, where an absence gets a reason rather than a silence.

*Three more, about passwords, recorded here for the same reason:*

- **The `Password` card is a sentence for a person who signs in through an identity provider**, not
  an absence. An OIDC principal has no password here, and a Setup page with no password card would
  read as a bug rather than a decision, which is the argument the non-admin `/people` sentence above
  makes. For a local account the card is three `type="password"` fields with their `autocomplete`
  values, the revocation sentence **before** the button (changing a password costs every token, so
  the cost is stated before it is paid), and the server's own message under `Current password` when
  that is what was wrong. The password floor is not shown before submit, because nothing publishes
  it; the 422 names it.
- **The reset dialog has no visual baseline.** The visual database holds one user, the bootstrap
  admin, whose own row offers no reset. Seeding a second user to open the dialog would repaint
  `people-table.png`, `shell-sidebar.png` and `shell-top-bar.png`, a trade already refused for first
  run. The dialog is proven by component tests and `e2e/password.spec.ts`;
  `setup-password.png` covers the card. A card inserted above Export repaints `setup-export.png`: a
  card-scoped screenshot still changes when its card moves by a fractional pixel, because glyph
  anti-aliasing follows the offset.
- **The reset dialog names its subject in prose**, so `ui/oneAttributionPrimitive.test.ts` carries
  it as a third exemption beside the two deactivate confirmations, for the same reason.

*A role gate, and why it is asserted by rendering.* Without one, every caller's page would request
`GET /admin/agent-labels` and `GET /principals`, both `admin`-role routes, and a member would read
"Could not load agent labels." under a heading promising everyone's. The merged table asks one
question chosen by role. The guard against its return is a rendered assertion rather than a source
search: a grep for the `Badge` import is evaded by a re-export, by a wrapper component defined
elsewhere, or by a file one directory deeper, and the component tests assert what the page actually
renders.

### 8.7 Activity

`/audit` is `/activity` and redirects with `replace`. The screen is
a chip row over a feed: six chips in 7.4's grammar — `Record`, `Person`, `Agent`, `Type`,
`Field`, `Date` — above `ui/ActivityEvent.tsx` at full width, one entry per write, each with the
write's `Hand`, the record it is about as a link, and a change pill per field.

`Person` and `Agent` are pickers. `Person` reads `GET /principals/directory`; `Agent` reads
`GET /api/v1/agent-labels/directory`, which exists because no other `read`-scoped route lists
agent labels and so the filter could not offer its own options.

*Eight points, recorded here rather than left to be rediscovered:*

- **The agent label column prints the label's text, not a UUID**, through DD-25's third
  repository join. A raw id can still appear as the **principal** fallback for a referent whose
  display name is gone, which is deliberate. What genuinely demanded a UUID was the two filter
  *inputs*, and that is what the pickers replace.
- **"Events for one record version group into one entry" cannot be implemented as written.** A
  version is derived by counting forward from a record's `create` event, which is exact only when
  the history has been walked from its start; a cross-record keyset walk newest-first never is.
  The feed groups by **write** and renders no version number. That is the same sentence for every
  write that produces a version, and a defined answer for the ones that do not.
- **A request id is not a write.** It identifies an HTTP request, and `write_batch` (DD-22) or a
  CSV import is one request across many records, so the group key is `(request_id, record_id)`.
  On the record page every group shares one record, which is why the record page's grouping can
  say "one write is one request id" and be right.
- **"Revert stays per entry" ships as per change.** An entry is a write, and there is no
  whole-write revert route: `revert_field_change` is per field event and `revert_to_version`
  needs a version this screen does not have. The affordance sits on each change pill, which is
  the same one the table offered per row.
- **Two field types show that a field changed, not what it changed to.** `user_ref` and
  `relation` resolve an id through a `principals` sidecar, and `GET /api/v1/audit-events` carries
  none — the record page gets its from the record document. Formatting one here would print a
  36-character id inside a pill, which is the defect this screen exists to remove, so both take
  `ActivityChange`'s "`<Field>` edited" shape. Every other type formats: a select reads `At risk`
  where the table printed `"at_risk"`. Resolving them properly means a sidecar on the audit
  response, which is a change to that document and its own decision.
- **The feed has no sortable columns, and that is a loss.** It cannot be ordered by surface or
  action. Sortable headers once brought a record's events together; the `Record` chip and the
  per-write grouping do that, and the sort only ever ordered the pages already fetched, because the
  route takes no sort spec.
- **A chip commits on completion, which is what replaces the debounce.** 7.4 already said the
  query does not run until the condition is complete; debouncing free-text inputs into the query
  key, and leaving `since` and `until` undebounced, would let a half-typed date issue a request
  per keystroke.
- **The directory's bound is published nowhere, and that is a known gap.** It reuses
  `DIRECTORY_DEFAULT_LIMIT` / `DIRECTORY_MAX_LIMIT` rather than declaring a second pair for one
  concept. DD-18 says every bound is published in the capabilities document; `/principals/directory`
  escapes that because its number rides `find_principals`' parameter description, and a REST-only
  route has no such carrier. Publishing it means either an MCP tool nothing else needs or a
  capabilities entry for a route no agent calls.

**What makes the agent-label directory safe, since it widens an API.** It returns
the labels appearing on audit events the caller may read, using the same predicate
`AuditService.search` applies — `audit_access_clause`, now a module-level function precisely
because it has two readers. Its rows are therefore exactly what an unfiltered `/audit-events`
walk by the same caller would already reveal, so it discloses nothing new. An **unscoped**
directory over `list_labels(None)` would have published every label string in the deployment,
including labels whose activity is confined to object types the caller is closed out of; that is
a disclosure decision, and `tests/test_agent_label_directory.py` asserts the equivalence rather
than asserting the route merely answers. `AgentLabelService` is one of
`tests/test_access_completeness.py`'s enforcing services, because this route makes it read
through object-type access.

---

## 9. Responsive

Breakpoints: `960px` (sidebar collapses; two-column screens stack; Inbox becomes list then
detail) and `640px` (toolbar chips wrap; tables scroll inside their card; record Details and
Activity stack with Activity first). Nothing is hidden at a smaller width that was reachable
at a larger one (DD-42 applies to viewports as well as to access).

Both are **named tokens** — `--breakpoint-shell` and `--breakpoint-narrow` in the single
`@theme` block — for the reason 2.1 gives about colour: every value in this document is a
decision, and a decision spelled `min-[960px]:` at four call sites is four places to change it
and no place to read it. They deliberately do not reuse Tailwind's `md` (768px), which is neither
of these numbers.

---

## 10. Accessibility

- Every interactive element has a visible focus state (section 3) and a name.
- Kind is never color alone: shape (circle/square) carries person/agent; text carries status
  in pills.
- Contrast floors in 2.1 hold in both themes.
- `prefers-reduced-motion` disables transitions; there are no animations to disable.
- Dialogs stay native `<dialog>` (DD-41).

---

## 11. Relation to DD-41

This document is the design language DD-41 names. Its structural rules hold across every
screen: the DOM is load-bearing; dialogs are native; semantic color is separate from the
accent; focus is visible everywhere; the 4px grid; fonts ship in the bundle; a single
`@theme` indirection; and the accent is two role-bound families, people and agents.
