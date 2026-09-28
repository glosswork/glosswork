/**
 * There is no "target exactly these record ids" parameter on `bulk-update`; the filter grammar's
 * `key` system pseudo-field (text-typed, `in` operator, docs/MCP_TOOLS.md section 4) is how a
 * multi-selected set of table rows becomes a bulk-update/bulk-delete-loop's request scope.
 */
import type { FilterCondition } from "../filters/types";

export function selectedKeysFilter(recordKeys: string[]): FilterCondition {
  return { field: "key", op: "in", value: recordKeys };
}
