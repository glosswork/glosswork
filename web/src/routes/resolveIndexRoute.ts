import type { ObjectTypeSummary } from "../api/objectTypes";

/**
 * Decides where `/` redirects: the first live object type's table route, in the order
 * `list_object_types` returns them, or `null` when no object type exists yet (empty state).
 * Extracted as its own pure function (rather than inlined in a route component's render body)
 * per the rule that business logic lives in hooks and utilities.
 */
export function resolveIndexRoute(objectTypes: ObjectTypeSummary[]): string | null {
  const first = objectTypes[0];
  return first ? `/${first.key}` : null;
}
