/**
 * Pinned columns: which fields stay put while the table scrolls sideways, and where.
 *
 * A pinned column is `position: sticky` with a `left` equal to the summed widths of the pinned
 * columns before it **in the current order**. Pinning reorders nothing: a pinned column scrolls
 * with the table until it reaches the wrapper's left edge and stops there. Only fields are
 * pinned; the checkbox, By and Key columns scroll away with the rest.
 *
 * Pure functions, because the arithmetic is the part that can be unit tested: the geometry
 * itself is a browser measurement (`e2e/table-pilot-findings.spec.ts`).
 */

/**
 * The live pin list for a view.
 *
 * `stored` absent is every view saved before pinning existed, and it means "the display field":
 * the column that names a record is the one worth keeping in sight. An explicit list is taken as
 * it is, so an empty one means nothing is pinned.
 */
export function resolvePinned(
  stored: readonly string[] | undefined,
  displayFieldKey: string | null,
): string[] {
  if (stored !== undefined) return [...stored];
  return displayFieldKey === null ? [] : [displayFieldKey];
}

/** `pinned` with `key` added, or removed if it is already there. */
export function togglePinned(pinned: readonly string[], key: string): string[] {
  return pinned.includes(key) ? pinned.filter((one) => one !== key) : [...pinned, key];
}

export interface PinPlacement {
  /** `left` for each pinned, visible column, in pixels. */
  offsets: Record<string, number>;
  /** The right-most pinned, visible column: the one that carries the dividing rule. */
  lastPinned: string | null;
}

/**
 * Where each pinned column sticks.
 *
 * @param visibleOrder every visible column id, left to right, display columns included.
 * @param pinned the pinned field keys. One that is not visible takes no room and no offset.
 * @param widthOf the rendered width of a column, in pixels.
 */
export function pinPlacement(
  visibleOrder: readonly string[],
  pinned: readonly string[],
  widthOf: (columnId: string) => number,
): PinPlacement {
  const pinnedSet = new Set(pinned);
  const offsets: Record<string, number> = {};
  let left = 0;
  let lastPinned: string | null = null;
  for (const columnId of visibleOrder) {
    if (!pinnedSet.has(columnId)) continue;
    offsets[columnId] = left;
    left += widthOf(columnId);
    lastPinned = columnId;
  }
  return { offsets, lastPinned };
}
