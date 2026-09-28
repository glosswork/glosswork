/**
 * Class recipes for the records table (DD-41).
 *
 * Recipes rather than a component: TanStack Table owns the `<table>` markup in
 * `table-view/TableView.tsx`, and these recipes style it without restructuring it.
 * **Density follows DD-41** (docs/DESIGN.md 2.4). Comfortable is the
 * default — 38px rows, 34px headers, 13.5px cell text — and compact (32/30/13px) is a click
 * away in the records table's toolbar. Compact suits an operations team that
 * scans rows; the beachhead persona reads more of each row than it scans rows, so the
 * default follows the persona.
 *
 * **All six tables that share these recipes take the comfortable default; only the records
 * table gets the toggle.** A settings table at 32px beside a records table at 38px is
 * two products, and only the records table has a toolbar and a saved view to persist a choice
 * into. `MergeConflictDialog` renders its own local recipes and is untouched.
 */
import type { Density } from "./density";

export const tableWrapClass = "overflow-x-auto rounded-card border border-line bg-surface";

/**
 * The same wrapper for a table that lives **inside** a `ui/Card.tsx`.
 *
 * `tableWrapClass` above carries `rounded-card border border-line bg-surface`, and `Card` already
 * supplies all three of those on its own `<section>`. Reusing it inside a card therefore draws two
 * concentric rounded borders, which is docs/DESIGN.md 7.6's "a table is one card; rows are not
 * cards" broken in the one place it is easiest to break. The horizontal scroll is the part that is
 * still needed: it is what makes section 9's "nothing reachable at 1280 is unreachable at 800" true
 * of a wide table.
 *
 * The five tables that are NOT inside cards keep `tableWrapClass`.
 */
export const cardTableWrapClass = "overflow-x-auto";

/**
 * 2.4's cell-text row lives HERE, not on `tdClass`: the face is set once on the table and
 * inherited by every cell, which is why changing the row height and changing the text size are
 * two different edits rather than one.
 */
export const tableClass = "w-full border-collapse text-sm";

/** The comfortable table, for the five tables with no toggle and for the records table's
 * default. `text-[13.5px]` is 2.4's comfortable cell text; `text-sm` (13px) is compact's. */
export const tableClassFor = (density: Density): string =>
  density === "compact" ? tableClass : "w-full border-collapse text-[13.5px]";

/**
 * The records table only. `table-layout: fixed` makes the first
 * row's cells authoritative, so the `width: header.getSize()` every `<th>` in `TableView.tsx`
 * already carries becomes a real constraint instead of a suggestion the browser's auto algorithm
 * is free to overrule from cell content — which is what `tdClass`'s `whitespace-nowrap` invites
 * it to do. `truncate` on `EditableCell`'s display button then has a definite box to clip
 * against, and `w-full` on the in-edit textarea has a percentage base to resolve.
 *
 * Deliberately NOT folded into `tableClass`: five other tables share that recipe (the CSV
 * wizard's mapping and dry-run-error tables, the schema editor's fields table, the audit
 * browser's events table, and `BlastRadiusPanel`'s), none of which gives any column an explicit
 * width. Fixed layout there would hand each of them equal-width columns and break the schema
 * editor's deliberate `whitespace-normal` Description column.
 */
export const recordsTableClass = tableClass + " table-fixed";

/** The records table at a chosen density — the one table with a toggle. */
export const recordsTableClassFor = (density: Density): string =>
  tableClassFor(density) + " table-fixed";

const TH_BASE =
  "whitespace-nowrap border-b border-line-2 bg-ground px-3 text-left text-2xs " +
  "font-semibold uppercase tracking-wider text-ink-2";

/** 2.4: 34px comfortable, 30px compact. */
export const thClassFor = (density: Density): string =>
  `${density === "compact" ? "h-[30px]" : "h-[34px]"} ${TH_BASE}`;

/** The comfortable header, for the five tables that take the default and never toggle. */
export const thClass = thClassFor("comfortable");

export const rowClass = "hover:bg-ground";

const TD_BASE = "whitespace-nowrap border-b border-line px-3";

/** 2.4: 38px comfortable, 32px compact. */
export const tdClassFor = (density: Density): string =>
  `${density === "compact" ? "h-8" : "h-[38px]"} ${TD_BASE}`;

/** The comfortable row, for the five tables that take the default and never toggle. */
export const tdClass = tdClassFor("comfortable");

/** Row-selection tint; the `td`s are transparent, so the `tr` background shows through. */
export const selectedRowClass = "bg-human-soft";

/** docs/DESIGN.md 7.7: group headers are 32px, `ground` fill, sans 600 13px. Not a density
 * preset — 2.4 lists four rows and this is not one of them — so it takes one height. */
export const groupTdClass =
  "h-8 border-b border-line bg-ground px-3 text-[13px] font-semibold text-ink-2";

/** Record keys, versions, counts: identifiers set in the mono face (DD-41). */
export const keyCellClass = "font-mono text-xs text-ink-2";

export const numCellClass = "text-right tabular-nums";

/**
 * A timestamp cell. Left-aligned like text, but with tabular figures.
 *
 * docs/DESIGN.md 7.7 says numbers are tabular, and a column of times is a column of numbers a
 * reader scans down: proportional digits leave `Today 09:14` and `Today 11:11` visibly ragged.
 *
 * **It is also what makes a screenshot of such a column stable**, which is how the need for it
 * was found. `setup-access-tokens.png` masks the Last used cell, whose value updates on every
 * request the visual suite makes -- but a mask hides a cell's *content*, not its effect on
 * layout. With proportional digits the cell's intrinsic width moved with the clock, the table's
 * auto layout moved every column to its right, and the baseline failed by ~555 pixels with
 * nothing visibly wrong. Tabular figures make the width a function of the character count,
 * which for this cell is fixed.
 */
export const timeCellClass = "tabular-nums";

/* The in-edit cell needs no dedicated recipe: the widgets autofocus, and the base layer's
 * global focus-visible ring (index.css) is the edit-focus treatment. */
