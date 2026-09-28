/**
 * Saved-views UI (FR-U3): list/select per object type, save the current state back to the
 * selected view, save as a new named view, and mark the selected view as the type's default.
 * `TableView.tsx` owns turning "save" into a `TableViewConfig` (`tableViewConfig.ts`) and
 * turning "select" into applied local state; this component only collects the user's intent.
 *
 * **It is the View menu's contents**, not a toolbar row of its own: `ViewMenu.tsx` renders it
 * inside a popover, with `Export CSV` beneath it. Its controls stack in a column rather than a
 * wrapping row, for the same reason the sort list does in its popover: width is the scarce
 * dimension in a panel and height is not.
 */
import { useState } from "react";
import type { SavedView } from "../api/savedViews";
import { Button } from "../ui/Button";
import { btnSmClass, compactInputClass, compactSelectClass } from "../ui/classes";

/**
 * A stacked label, rather than `inlineLabelClass` with column utilities appended: `items-center`
 * and `items-stretch` are both single-class selectors, so appending one to the other is a
 * specificity tie settled by the order of the built stylesheet — the exact collision that once
 * cost a real contrast failure. The two colours are never in the class list together
 * here either.
 */
const stackedLabelClass = "flex flex-col items-stretch gap-1 text-xs font-medium text-ink-2";

export interface SavedViewsBarProps {
  views: SavedView[];
  selectedViewId: string | null;
  onSelectView: (viewId: string | null) => void;
  onSave: () => void;
  onSaveAsNew: (name: string) => void;
  onSetDefault: () => void;
  /** `create_saved_view`, `update_saved_view` and `set_default` are all `write`. Selecting an
   * existing view is a read and is never gated. */
  canWrite: boolean;
}

export function SavedViewsBar({
  views,
  selectedViewId,
  onSelectView,
  onSave,
  onSaveAsNew,
  onSetDefault,
  canWrite,
}: SavedViewsBarProps) {
  const [newName, setNewName] = useState("");
  const selected = views.find((view) => view.id === selectedViewId) ?? null;

  return (
    <div className="flex flex-col items-stretch gap-2" data-testid="saved-views-bar">
      <label className={stackedLabelClass}>
        View
        <select
          className={compactSelectClass}
          aria-label="Saved view"
          value={selectedViewId ?? ""}
          onChange={(event) => onSelectView(event.target.value || null)}
        >
          <option value="">(unsaved)</option>
          {views.map((view) => (
            <option key={view.id} value={view.id}>
              {view.name}
              {view.is_default ? " (default)" : ""}
            </option>
          ))}
        </select>
      </label>
      {canWrite && (
        <>
          <Button
            type="button"
            className={btnSmClass}
            onClick={onSave}
            disabled={selected === null}
          >
            Save
          </Button>
          <label className={stackedLabelClass}>
            New view name
            <input
              className={compactInputClass}
              aria-label="New view name"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
            />
          </label>
          <Button
            type="button"
            className={btnSmClass}
            onClick={() => {
              if (newName.trim()) {
                onSaveAsNew(newName.trim());
                setNewName("");
              }
            }}
          >
            Save as new
          </Button>
          <Button
            type="button"
            className={btnSmClass}
            onClick={onSetDefault}
            disabled={selected === null || selected.is_default}
          >
            Set as default
          </Button>
        </>
      )}
    </div>
  );
}
