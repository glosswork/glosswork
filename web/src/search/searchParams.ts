/**
 * Pure parse/serialize helpers for `/search`'s URL params. The header's `search-slot` form
 * (`App.tsx`) writes only `q`; this page owns `mode`, `types`, and `filter` from here on, so a
 * reload or a shared link reproduces the exact same query.
 *
 * `filter` is a JSON-serialized `FilterNode` and is only ever read or written when `types` names
 * exactly one object-type key, matching the backend's one-type filter rule (docs/MCP_TOOLS.md
 * 5.1: with several or no `object_types`, `filter` may reference only system pseudo-fields, so a
 * stored filter for a specific type is meaningless — and potentially invalid — once the type
 * selection no longer names exactly that one type).
 */
import type { FilterNode } from "../filters/types";

export type SearchMode = "hybrid" | "semantic" | "keyword";

export const DEFAULT_SEARCH_MODE: SearchMode = "hybrid";

export interface ParsedSearchParams {
  q: string;
  mode: SearchMode;
  types: string[];
  filter: FilterNode | null;
}

function isSearchMode(value: string | null): value is SearchMode {
  return value === "hybrid" || value === "semantic" || value === "keyword";
}

/** Reads `q`, `mode`, `types`, and `filter` out of the current URL search params. An unknown
 * `mode` value falls back to the default rather than being sent to the backend; a `filter` that
 * fails to parse as JSON, or that is present while `types` does not name exactly one key, is
 * dropped rather than thrown. */
export function parseSearchParams(params: URLSearchParams): ParsedSearchParams {
  const q = params.get("q") ?? "";

  const modeRaw = params.get("mode");
  const mode = isSearchMode(modeRaw) ? modeRaw : DEFAULT_SEARCH_MODE;

  const typesRaw = params.get("types");
  const types = typesRaw
    ? typesRaw
        .split(",")
        .map((key) => key.trim())
        .filter((key) => key.length > 0)
    : [];

  let filter: FilterNode | null = null;
  const filterRaw = params.get("filter");
  if (filterRaw && types.length === 1) {
    try {
      filter = JSON.parse(filterRaw) as FilterNode;
    } catch {
      filter = null;
    }
  }

  return { q, mode, types, filter };
}

/** The inverse of `parseSearchParams`: only ever writes what a reload needs to reproduce the
 * same request. The default mode is omitted (a clean `?q=...` for the common case); `filter` is
 * omitted whenever `types` does not name exactly one key, even if one was passed in, so a
 * multi-type or all-types selection never carries a stale single-type filter forward. */
export function serializeSearchParams(parsed: ParsedSearchParams): URLSearchParams {
  const params = new URLSearchParams();
  if (parsed.q) {
    params.set("q", parsed.q);
  }
  if (parsed.mode !== DEFAULT_SEARCH_MODE) {
    params.set("mode", parsed.mode);
  }
  if (parsed.types.length > 0) {
    params.set("types", parsed.types.join(","));
  }
  if (parsed.filter !== null && parsed.types.length === 1) {
    params.set("filter", JSON.stringify(parsed.filter));
  }
  return params;
}
