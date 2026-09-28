/**
 * Serializing the table view's live component state into a `TableViewConfig` (for `Save`/`Save
 * as new`), and parsing a saved view's stored `config` blob back into that same live state (for
 * "load a view"). Kept as pure functions per AGENTS.md's "saved-view serialization lives in
 * hooks/utilities, never inline in a component" rule; `TableView.tsx` only calls these.
 */
import type { FieldDoc } from "../api/objectTypes";
import type { FilterNode } from "../filters/types";
import type { SortKey } from "./sortSpec";
import type { ColumnStateConfig, TableViewConfig, TableViewMode } from "./types";
import { isDensity, type Density } from "../ui/density";

export function defaultColumnState(fields: FieldDoc[]): ColumnStateConfig {
  return {
    order: fields.map((field) => field.key),
    visibility: {},
    sizing: {},
  };
}

export function defaultTableViewConfig(fields: FieldDoc[]): TableViewConfig {
  return {
    filter: null,
    sort: [],
    groupBy: null,
    columns: defaultColumnState(fields),
    mode: "table",
  };
}

/** The live pieces of `TableView`'s component state that make up one saved view. */
export interface TableViewLiveState {
  filter: FilterNode | null;
  sort: SortKey[];
  groupBy: string | null;
  columnOrder: string[];
  columnVisibility: Record<string, boolean>;
  columnSizing: Record<string, number>;
  mode: TableViewMode;
  density: Density;
}

export function serializeTableViewConfig(state: TableViewLiveState): TableViewConfig {
  return {
    filter: state.filter,
    sort: state.sort,
    groupBy: state.groupBy,
    columns: {
      order: state.columnOrder,
      visibility: state.columnVisibility,
      sizing: state.columnSizing,
    },
    mode: state.mode,
    density: state.density,
  };
}

function isSortKeyArray(value: unknown): value is SortKey[] {
  return (
    Array.isArray(value) &&
    value.every(
      (entry) =>
        typeof entry === "object" &&
        entry !== null &&
        typeof (entry as { field?: unknown }).field === "string" &&
        ((entry as { dir?: unknown }).dir === "asc" || (entry as { dir?: unknown }).dir === "desc"),
    )
  );
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((entry) => typeof entry === "string");
}

function isStringBooleanRecord(value: unknown): value is Record<string, boolean> {
  return (
    typeof value === "object" &&
    value !== null &&
    Object.values(value as Record<string, unknown>).every((entry) => typeof entry === "boolean")
  );
}

function isStringNumberRecord(value: unknown): value is Record<string, number> {
  return (
    typeof value === "object" &&
    value !== null &&
    Object.values(value as Record<string, unknown>).every((entry) => typeof entry === "number")
  );
}

/** Defensively reconstructs a `TableViewConfig` from a saved view's opaque `config` JSON,
 * falling back to per-field defaults (rather than rejecting the whole view) so an older or
 * hand-edited config never crashes the table view. `fields` supplies the default column order
 * when the stored config predates a field or omits column state entirely. */
export function parseTableViewConfig(
  raw: Record<string, unknown>,
  fields: FieldDoc[],
): TableViewConfig {
  const fallback = defaultTableViewConfig(fields);
  const filter = isFilterNodeLike(raw.filter) ? (raw.filter as FilterNode) : fallback.filter;
  const sort = isSortKeyArray(raw.sort) ? raw.sort : fallback.sort;
  const groupBy = typeof raw.groupBy === "string" ? raw.groupBy : fallback.groupBy;
  const mode = raw.mode === "card" ? "card" : "table";

  const rawColumns =
    typeof raw.columns === "object" && raw.columns !== null
      ? (raw.columns as Record<string, unknown>)
      : {};
  const columns: ColumnStateConfig = {
    order: isStringArray(rawColumns.order) ? rawColumns.order : fallback.columns.order,
    visibility: isStringBooleanRecord(rawColumns.visibility)
      ? rawColumns.visibility
      : fallback.columns.visibility,
    sizing: isStringNumberRecord(rawColumns.sizing) ? rawColumns.sizing : fallback.columns.sizing,
  };

  // Absent on every view saved without a density, and left absent rather than defaulted
  // so `resolveDensity` can tell "this view has no opinion" from "this view says comfortable".
  const density = isDensity(raw.density) ? raw.density : undefined;

  return { filter, sort, groupBy, columns, mode, density };
}

function isFilterNodeLike(value: unknown): boolean {
  return typeof value === "object" && value !== null;
}
