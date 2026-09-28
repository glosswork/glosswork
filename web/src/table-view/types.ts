/**
 * `TableViewConfig` is the entire shape persisted as one saved view's opaque `config` JSON
 * (`docs/DATA_MODEL.md` section 11: "JSON: filter, sort, group_by, columns, widths"). There is
 * no generated type for it — the backend stores and returns it as `Record<string, unknown>` —
 * so this file is the single source of truth for what the frontend puts in that blob.
 */
import type { FilterNode } from "../filters/types";
import type { SortKey } from "./sortSpec";
import type { Density } from "../ui/density";

export type TableViewMode = "table" | "card";

/** Column show/hide/reorder/resize state, keyed by field key (TanStack Table's own
 * `columnVisibility`/`columnOrder`/`columnSizing` state shapes, minus the library-specific
 * wrapper types). */
export interface ColumnStateConfig {
  order: string[];
  visibility: Record<string, boolean>;
  sizing: Record<string, number>;
}

export interface TableViewConfig {
  filter: FilterNode | null;
  sort: SortKey[];
  groupBy: string | null;
  columns: ColumnStateConfig;
  mode: TableViewMode;
  /** docs/DESIGN.md 2.4. Optional because a view saved without a density omits it, and a view that
   * says nothing falls through to the user's own preference (`ui/density.ts::resolveDensity`)
   * rather than being forced to the default. The backend validates `config` as "a JSON object" and
   * nothing more, so no migration is involved. */
  density?: Density;
}
