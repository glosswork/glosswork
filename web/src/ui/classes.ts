/**
 * Class recipes for form markup (DD-41).
 *
 * These exist alongside the `Field` component because most screens already carry their own
 * label/control markup: the binding constraint is "add classes to existing elements; do not
 * restructure the DOM", so a screen whose shape doesn't match `Field`'s
 * applies these recipes to the elements it has rather than rewrapping them.
 */

export const fieldLabelClass = "mb-1 block text-xs font-semibold text-ink";

export const inputClass =
  "block w-full max-w-md rounded-ctl border border-line-2 bg-surface px-2.5 py-1.5 " +
  "text-base text-ink placeholder:text-ink-2";

export const inputErrorClass = "border-bad";

export const selectClass = inputClass;

export const fieldHelpClass = "mt-1 max-w-md text-xs text-ink-2";

export const fieldErrorClass = "mt-1 max-w-md text-xs text-bad";

/**
 * Shell navigation links (App.tsx and ObjectTypeNav share one treatment).
 *
 * The resting colour is a THIRD class, not part of `navLinkClass`,
 * because the two colours must never be in the class list at the same time. `text-human-ink` and
 * `text-ink-2` are both single-class selectors, so specificity ties and source order in the
 * built stylesheet decides — `.text-ink-2` is emitted later, so it won the collision and the
 * selected link painted `#71717a` on `#eff6ff` (4.44:1, below the 4.5:1 AA floor) while the
 * code asked for `#2563eb` (4.75:1). Making them mutually exclusive removes the collision; an
 * `!important` would only have hidden it.
 */
export const navLinkClass = "rounded-ctl px-2.5 py-1 text-base hover:bg-ground hover:text-ink";

export const navLinkRestClass = "text-ink-2";

export const navLinkActiveClass = "bg-human-soft font-medium text-human-ink";

/**
 * The sidebar's own nav treatment (docs/DESIGN.md 8.1).
 *
 * Separate recipes from `navLinkClass` above rather than a variant of it, because the shapes
 * genuinely differ: the header's links were inline pills in a row, and these are full-width rows
 * with a count pushed to the far end.
 *
 * **The three-class structure is carried over deliberately and is the load-bearing part.**
 * The collision it prevents is a cascade collision, not a wrong colour:
 * `text-human-ink` and `text-ink-2` are both single-class selectors, so specificity ties and
 * source order in the built stylesheet decides. `.text-ink-2` is emitted later, so it won, and
 * the selected link painted 4.44:1 while the code asked for 4.75:1. Keeping the resting colour
 * in its own class means the two are never in the class list together.
 *
 * Measured for the new background (the sidebar is on `ground`, the header was on `surface`):
 * the active row carries its own opaque `bg-human-soft`, so its ratio is unchanged; the resting
 * row inherits `ground`, where `ink-2` is 5.50:1 light and 7.73:1 dark, both clearing 4.5:1.
 */
export const sidebarLinkClass =
  "flex items-center justify-between gap-2 rounded-ctl px-2.5 py-1.5 text-sm "
  + "hover:bg-surface hover:text-ink";

export const sidebarLinkRestClass = "text-ink-2";

export const sidebarLinkActiveClass = "bg-human-soft font-medium text-human-ink";

/** The count at the end of a sidebar row: the Inbox badge and each object type's record count.
 * `tabular-nums` so a column of counts does not jitter as the digits change. */
export const sidebarCountClass = "shrink-0 tabular-nums text-xs text-ink-2";

/** "Tracking" and "Workspace" (docs/DESIGN.md 8.1).
 *
 * A `<p>`, never a heading element: the sidebar contributes nothing to the document outline,
 * which `heading-outline.spec.ts` walks and `theme.spec.ts` reads the first `h1` of.
 *
 * `ink-2` rather than `ink-3` for the same reason the workspace line is, and it is worth stating
 * twice because `ink-3` here would repeat that fault one line further down. `ink-3` on `ground` is
 * 2.79:1 light and 4.00:1 dark; these labels are 11.5px, so they are small text and the floor is
 * the full 4.5:1 with no large-text allowance. */
export const sidebarSectionLabelClass =
  "px-3 pb-1 pt-4 text-[11.5px] font-semibold uppercase tracking-wide text-ink-2";

/** Compact controls for the table view's toolbar rows. */
export const compactSelectClass =
  "rounded-ctl border border-line-2 bg-surface px-2 py-1 text-sm text-ink";

export const compactInputClass = compactSelectClass + " placeholder:text-ink-2";

export const inlineLabelClass = "inline-flex items-center gap-1.5 text-xs font-medium text-ink-2";

/** Compact sizing appended to a Button in a toolbar row. */
export const btnSmClass = "px-2.5 py-1 text-sm";

/** A Link styled as a secondary button (the element stays an <a>). */
export const linkButtonClass =
  "inline-flex w-fit items-center rounded-ctl border border-line-2 bg-surface px-2.5 py-1 " +
  "text-sm text-ink hover:bg-ground";

/** A popover trigger in the table toolbar (the View menu).
 *
 * The same recipe as `linkButtonClass`, on the element that already is a button: `Popover`
 * renders its own `<button>` and takes a class rather than a component, so a trigger that should
 * read as a secondary button needs the recipe rather than `Button`. */
export const toolbarTriggerClass =
  "inline-flex cursor-pointer items-center gap-1.5 rounded-ctl border border-line-2 bg-surface " +
  "px-2.5 py-1 text-sm text-ink hover:bg-ground";

/**
 * A label/value readout inside a card: the search-index counts on `/setup`, and whatever else
 * needs a `<dl>` rather than a table.
 *
 * **It takes the place of five older `panel*` recipes**, which went with `web/src/settings/`. Two
 * of them (`panelClass`, `panelHeadingClass`) were the settings panel itself and are now
 * `ui/Card.tsx`. The third, `panelDlClass`, carried `rounded-card border border-line bg-ground` and
 * so drew a card inside the card it sat in, and docs/DESIGN.md 7.6 is explicit that a table is one
 * card and rows are not cards. The grid and the type treatment were never the problem, so they
 * survive without the box.
 */
export const statListClass = "grid grid-cols-[140px_1fr] gap-x-4 gap-y-1 text-sm";

export const statTermClass = "text-xs font-medium text-ink-2";

export const statDetailClass = "text-ink";
