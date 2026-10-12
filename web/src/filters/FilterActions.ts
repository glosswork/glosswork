/** The mutation entry points every recursive filter-tree component calls, bound to `FilterBuilder`'s state. */
import type { FilterPath } from "./filterTree";
import type { FilterCondition, GroupKind } from "./types";

export interface FilterActions {
  /**
   * `typing` is true when the change is a key in a value box that can be half-typed. The
   * builder shows it at once and sends it when typing pauses; every other change is sent at
   * once (`FilterBuilder.tsx`).
   */
  updateCondition: (path: FilterPath, condition: FilterCondition, typing?: boolean) => void;
  removeNode: (path: FilterPath) => void;
  addCondition: (groupPath: FilterPath) => void;
  addGroup: (groupPath: FilterPath, kind: GroupKind) => void;
  setGroupKind: (path: FilterPath, kind: GroupKind) => void;
  toggleNot: (path: FilterPath) => void;
}
