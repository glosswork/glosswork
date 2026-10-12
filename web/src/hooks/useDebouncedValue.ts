import { useEffect, useState } from "react";

/**
 * How long a filter waits after the last key before it is sent.
 *
 * Two users: this hook's default, and the tree builder (`filters/FilterBuilder.tsx`), which
 * sends a typed value after this long without another key. The builder imports the number and
 * not the hook: it has to send at once on Enter, on blur and on close, and a debounced value
 * cannot be told to hurry. The relation picker's title search
 * (`record-detail/RelationPicker.tsx`) declares its own `RELATION_SEARCH_DEBOUNCE_MS`.
 */
export const FILTER_DEBOUNCE_MS = 300;

/**
 * `value`, but only after it has held still for `delayMs`.
 *
 * The one live consumer is the relation picker's title search
 * (`record-detail/RelationPicker.tsx`), which passes its own `delayMs` instead of the default
 * above. A filter chip's popover does not debounce: `table-view/ConditionPopover.tsx` commits
 * on Apply, Enter or close. The tree builder waits for a pause, with its own timer.
 *
 * Debouncing at the value rather than at the call site means every consumer of the derived query
 * key inherits it, and the input itself stays fully controlled and immediate.
 */
export function useDebouncedValue<T>(value: T, delayMs: number = FILTER_DEBOUNCE_MS): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}
