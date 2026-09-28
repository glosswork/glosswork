/**
 * The View menu: one popover on the right of the toolbar, opened by
 * a control naming the current view, holding the saved-view list, `Save`, `Save as new`,
 * `Set as default`, the name input and `Export CSV`.
 *
 * **Why it exists.** `docs/DESIGN.md` 8.2 describes a one-row toolbar — "filter chips, then group,
 * sort, columns and density controls right-aligned, then Import" — and sends CSV export "to **the
 * view menu**", a term the repository used once and defined nowhere (the same gap "gloss panel"
 * had). The six controls it does not place — `Export CSV`, `Saved view`, `Save`, `New view name`,
 * `Save as new`, `Set as default` — were measured as most of the eleven-control toolbar that
 * wrapped to two rows and pushed the first data row to 50.5% down the window. Collapsing them into
 * one control is what makes 8.2's one row possible, and it keeps FR-U3 and FR-U7 reachable at every
 * viewport (DD-42 applied to viewports), which a second toolbar row that wraps away does not.
 *
 * **Nothing inside is renamed.** Every accessible name and test id that still names a
 * control that exists is kept — `Saved view`, `Save`, `New view name`, `Save as new`,
 * `Set as default`, `Export CSV`, `saved-views-bar` — so the specs that drive them gain an
 * opening step and nothing else. `SavedViewsBar` is rendered unchanged apart from its own
 * stacking, which is why this file is a composition and not a rewrite.
 *
 * **The read-vs-write gating stays where it was**, on `SavedViewsBar`'s `canWrite` prop, and
 * `Export CSV` stays ungated because `export_csv` is a `read` route. The matching half of this:
 * behind a closed popover, an absence assertion about
 * `Save as new` passes because the control is **unmounted**, not because access was denied, so
 * `access-levels.spec.ts` opens this menu before asserting either way.
 */
import type { SavedView } from "../api/savedViews";
import { Popover } from "../ui/Popover";
import { Button } from "../ui/Button";
import { btnSmClass, toolbarTriggerClass } from "../ui/classes";
import { SavedViewsBar } from "./SavedViewsBar";

export interface ViewMenuProps {
  views: SavedView[];
  selectedViewId: string | null;
  onSelectView: (viewId: string | null) => void;
  onSave: () => void;
  onSaveAsNew: (name: string) => void;
  onSetDefault: () => void;
  /** FR-U7. The entry point moves in here (8.2); the export itself is unchanged. */
  onExport: () => void;
  canWrite: boolean;
}

export function ViewMenu({
  views,
  selectedViewId,
  onSelectView,
  onSave,
  onSaveAsNew,
  onSetDefault,
  onExport,
  canWrite,
}: ViewMenuProps) {
  const selected = views.find((view) => view.id === selectedViewId) ?? null;
  /**
   * The trigger names the current view when there is one to name, and reads "View"
   * when there is not. "Unsaved view" is a description of an absence rather than a name, the
   * title line already carries it in `ink-3` beside the count, and a toolbar control that
   * repeats it costs 54px of the one row 8.2 asks for. The accessible name below keeps both
   * halves in every state.
   */
  const triggerText = selected?.name ?? "View";

  return (
    <Popover
      panelTestId="view-menu"
      className="w-64"
      // The visible text is the current view's name, which is what the trigger is meant to say.
      // The accessible name adds what the control *is*, because "Active widgets" alone
      // does not read as a button that opens anything — and it keeps the visible string inside
      // the accessible one, which is what WCAG 2.5.3 asks for.
      //
      // **"Current view", and the wording is a measurement rather than a preference.** Playwright
      // matches an accessible name by SUBSTRING, so a trigger named for what it opens collides
      // with locators that already exist and every collision is a strict-mode failure in a spec
      // about something else. Both were hit, not reasoned about:
      //   - `View menu: Unsaved view` collided with `getByLabel("Saved view")`, the `<select>`
      //     inside this very popover, which four specs drive — "Un*saved view*".
      //   - `View menu` collided with `getByRole("button", { name: "Menu" })`, the app shell's
      //     narrow-viewport toggle (`shell.spec.ts`), which is a different component entirely.
      // Naming the absence of a view is the title line's job (it carries "Unsaved view" in
      // `ink-3`); this control says whose view it is showing, and only when there is one.
      triggerLabel={selected === null ? "Current view" : `Current view: ${selected.name}`}
      triggerClassName={toolbarTriggerClass}
      trigger={
        <>
          {/* Bounded, and the bound is what makes the toolbar's one-row layout a property of the
              toolbar rather than of the fixture's view name: a name longer than this truncates here
              instead of wrapping the row. Nothing is lost by the truncation — the full name is on
              the title line in `ink-2`, in the `<select>` inside this popover, and in the `title`
              below. */}
          <span className="max-w-28 truncate" title={triggerText}>
            {triggerText}
          </span>
          <span aria-hidden="true" className="text-ink-2">
            ▾
          </span>
        </>
      }
    >
      <div className="flex flex-col items-stretch gap-3">
        <SavedViewsBar
          views={views}
          selectedViewId={selectedViewId}
          onSelectView={onSelectView}
          onSave={onSave}
          onSaveAsNew={onSaveAsNew}
          onSetDefault={onSetDefault}
          canWrite={canWrite}
        />
        <div className="border-t border-line pt-3">
          <Button type="button" className={btnSmClass} onClick={onExport}>
            Export CSV
          </Button>
        </div>
      </div>
    </Popover>
  );
}
