/**
 * Groups a search response's flat `results` array by `object_type` for `SearchPage.tsx`'s per-type
 * `<section>`s: results grouped by object type, in order of first appearance. The search service
 * itself already returns results ranked by fused relevance across every type in scope, so this only
 * regroups — it never re-sorts within or across groups, and a group's position is fixed by the
 * first hit that introduced it, not by type key or record count.
 */
import type { SearchHit } from "../api/search";

export interface SearchResultGroup {
  objectType: string;
  hits: SearchHit[];
}

export function groupResultsByType(results: SearchHit[]): SearchResultGroup[] {
  const groups: SearchResultGroup[] = [];
  const indexByType = new Map<string, number>();

  for (const hit of results) {
    let index = indexByType.get(hit.object_type);
    if (index === undefined) {
      index = groups.length;
      indexByType.set(hit.object_type, index);
      groups.push({ objectType: hit.object_type, hits: [] });
    }
    groups[index].hits.push(hit);
  }

  return groups;
}
