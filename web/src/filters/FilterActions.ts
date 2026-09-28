/** The mutation entry points every recursive filter-tree component calls, bound to `FilterBuilder`'s state. */
import type { FilterPath } from "./filterTree";
import type { FilterCondition, GroupKind } from "./types";

export interface FilterActions {
  updateCondition: (path: FilterPath, condition: FilterCondition) => void;
  removeNode: (path: FilterPath) => void;
  addCondition: (groupPath: FilterPath) => void;
  addGroup: (groupPath: FilterPath, kind: GroupKind) => void;
  setGroupKind: (path: FilterPath, kind: GroupKind) => void;
  toggleNot: (path: FilterPath) => void;
}
