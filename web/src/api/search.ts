/**
 * Typed wrapper for `POST /api/v1/search` (`search`, docs/MCP_TOOLS.md 5.1). The request body
 * reuses the generated `SearchBody` (`schema.ts`); the response
 * is hand-typed here because FastAPI's route returns `dict[str, Any]` and the generator can only
 * give it `{[key: string]: unknown}` — the same reason `api/objectTypes.ts` hand-types
 * `describe_object_type`'s response rather than trusting the generated one.
 */
import type { components } from "./schema";
import { apiRequest } from "./client";

export type SearchBody = components["schemas"]["SearchBody"];

/** A hit whose best location is a field's value. */
export interface FieldHitSource {
  type: "field";
  field_key: string;
}

/** A hit whose best location is a comment on the record. */
export interface CommentHitSource {
  type: "comment";
  comment_id: string;
  author: string;
  created_at: string;
}

export type HitSource = FieldHitSource | CommentHitSource;

export interface SearchHit {
  record_key: string;
  record_id: string;
  object_type: string;
  title: string;
  score: number;
  hit_source: HitSource;
  /** Pre-highlighted with literal `<em>`/`</em>` markers around matched terms — never render
   * this directly; escape it first and only then promote those markers (`renderSnippet.ts`). */
  snippet: string;
  /** The record's additional keyword-matched locations beyond `hit_source`; 0 for a purely
   * semantic hit. */
  other_matches: number;
}

export interface IndexLag {
  pending_jobs: number;
  failed_jobs: number;
}

export type SearchModeApplied = "hybrid" | "semantic" | "keyword";

export interface SearchResponse {
  results: SearchHit[];
  index_lag: IndexLag;
  /** Differs from the request's `mode` only when semantic search is disabled on this deployment
   * and `hybrid` degraded to `keyword`. */
  mode_applied: SearchModeApplied;
}

/** `POST /api/v1/search` (scope `read`). */
export function search(body: SearchBody): Promise<SearchResponse> {
  return apiRequest<SearchResponse>("/search", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
