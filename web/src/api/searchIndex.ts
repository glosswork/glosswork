/**
 * Typed wrappers over the admin search-index observation and re-index routes (FR-Q9,
 * `src/glosswork/routes/search_index.py`). Hand-typed for the same reason `api/search.ts` is:
 * both routes return `dict[str, Any]` from FastAPI, so `schema.ts` only gives their bodies
 * `{[key: string]: unknown}`.
 */
import { apiRequest } from "./client";

/** One `embedding_jobs` row that exhausted its retries, as reported by the status route. */
export interface FailedJobDoc {
  record_key: string | null;
  record_id: string;
  source_type: "field" | "comment";
  field_key: string | null;
  comment_id: string | null;
  attempts: number;
  last_error: string | null;
  updated_at: string;
}

export interface SearchIndexStatus {
  pending_jobs: number;
  running_jobs: number;
  failed_jobs: FailedJobDoc[];
  indexed_chunks: number;
  stale_chunks: number;
  embedding_model: string;
  semantic_enabled: boolean;
}

/** `GET /api/v1/admin/search-index` (scope `admin`). */
export function getSearchIndexStatus(): Promise<SearchIndexStatus> {
  return apiRequest<SearchIndexStatus>("/admin/search-index");
}

/** `POST /api/v1/admin/search-index/reindex` (scope `admin`), `202 {enqueued}`. `feature_disabled`
 * (409) when embedding is disabled on this deployment (`details.feature === "reindex"`). */
export function reindexSearchIndex(): Promise<{ enqueued: number }> {
  return apiRequest<{ enqueued: number }>("/admin/search-index/reindex", { method: "POST" });
}
