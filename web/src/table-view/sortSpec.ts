/**
 * Multi-column sort state as a pure, order-preserving array (`query_records`'s `sort` shape:
 * `[{field, dir}]`, in priority order — the first entry is the primary key). Kept out of any
 * component's render body per AGENTS.md's "business logic lives in hooks/utilities" rule, and
 * out of TanStack Table's own sorting feature entirely: sort order here drives the server-side
 * `query_records` request, not a client-side row model.
 */

export interface SortKey {
  field: string;
  dir: "asc" | "desc";
}

function directionOf(sortKeys: SortKey[], field: string): "asc" | "desc" | null {
  return sortKeys.find((key) => key.field === field)?.dir ?? null;
}

/** A plain click on a column header: this field becomes the *only* sort key, cycling
 * unsorted -> asc -> desc -> unsorted. */
export function cycleSingleSort(sortKeys: SortKey[], field: string): SortKey[] {
  const current = sortKeys.length === 1 && sortKeys[0].field === field ? sortKeys[0].dir : null;
  const next = nextDirection(current);
  return next === null ? [] : [{ field, dir: next }];
}

/** A shift-click (or equivalent "add sort key" affordance): this field is added as the next
 * sort key, or cycled/removed in place if it's already part of the multi-key sort, without
 * disturbing the relative order of the other keys. */
export function cycleMultiSort(sortKeys: SortKey[], field: string): SortKey[] {
  const current = directionOf(sortKeys, field);
  const next = nextDirection(current);
  if (next === null) {
    return sortKeys.filter((key) => key.field !== field);
  }
  if (current === null) {
    return [...sortKeys, { field, dir: next }];
  }
  return sortKeys.map((key) => (key.field === field ? { field, dir: next } : key));
}

function nextDirection(current: "asc" | "desc" | null): "asc" | "desc" | null {
  if (current === null) return "asc";
  if (current === "asc") return "desc";
  return null;
}
