import { useEffect, useState } from "react";

/** The default delay the old audit filters used, from before filters moved to an explicit
 * commit boundary. The hook's one consumer, the relation picker's title search
 * (`record-detail/RelationPicker.tsx`), declares its own `RELATION_SEARCH_DEBOUNCE_MS` rather
 * than reusing this one. */
export const FILTER_DEBOUNCE_MS = 300;

/**
 * `value`, but only after it has held still for `delayMs`.
 *
 * The one live consumer is the relation picker's title search
 * (`record-detail/RelationPicker.tsx`), which passes its own `delayMs` instead of the default
 * above. Filter composers deliberately do not debounce: `docs/DESIGN.md` 8.7 and
 * `table-view/ConditionPopover.tsx` commit a condition to the network only once it is
 * complete, so a half-typed value never reaches it at all. This hook is not a filter tool.
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
