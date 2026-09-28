import type { ObjectTypeDetail } from "../api/objectTypes";
import { TableView } from "../table-view/TableView";

/**
 * Page shell for `/:objectTypeKey`. Kept as a thin pass-through to `TableView` (the real table
 * view: filter builder, TanStack Table, inline editing, saved views, FR-U1/FR-U3/FR-U10) so the
 * route/param plumbing in `ObjectTypePage.tsx` doesn't need to change.
 *
 * The `key` is what resets the table. `TableView` seeds eleven `useState` values, a
 * `useRef` gating the default saved view, a `viewGeneration` counter, and every `EditableCell`'s
 * draft from the object type it mounted with — none of which reset when only the prop changes,
 * so navigating between two collections without a remount would carry the previous one's filter,
 * sort, grouping, column layout and row selection across. Keying on `objectType.key` resets all
 * of it, including state added later, which resetting each `useState` by hand would not.
 */
export function ObjectTypeTablePlaceholder({ objectType }: { objectType: ObjectTypeDetail }) {
  return <TableView key={objectType.key} objectType={objectType} />;
}
