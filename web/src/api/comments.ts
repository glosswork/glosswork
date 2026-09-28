/**
 * Typed wrapper functions over the comment routes (FR-C1 through FR-C8,
 * `src/glosswork/routes/comments.py`): list/add/update/delete comments on a record.
 *
 * `CommentBody` is the precisely generated request-body type (`schema.ts`
 * `components["schemas"]`), reused here rather than duplicated. The response bodies are all
 * `dict[str, Any]` from FastAPI, so the interfaces below describe the real shape produced by
 * `envelopes.py` (`comment_doc`, `comments_page_doc`).
 */
import type { components } from "./schema";
import { apiRequest } from "./client";

export type CommentBody = components["schemas"]["CommentBody"];

export interface CommentDoc {
  id: string;
  record_id: string;
  body: string;
  author_id: string;
  agent_label_id: string | null;
  /** The label's text, resolved at the repository (DD-25), so the comment header never
   * renders a raw id as `(agent: <uuid>)`. */
  agent_label: string | null;
  created_at: string;
  updated_at: string;
  edited: boolean;
  deleted_at: string | null;
  /** DD-25: the author's human label, resolved at the repository layer. Null when the author
   * principal is gone; render `author_id` instead. */
  principal_display_name: string | null;
}

export interface CommentPage {
  comments: CommentDoc[];
  next_cursor: string | null;
}

export interface ListCommentsOptions {
  limit?: number;
  cursor?: string;
}

function buildCommentsQuery(options: ListCommentsOptions): string {
  const params = new URLSearchParams();
  if (options.limit !== undefined) {
    params.set("limit", String(options.limit));
  }
  if (options.cursor !== undefined) {
    params.set("cursor", options.cursor);
  }
  const search = params.toString();
  return search ? `?${search}` : "";
}

/** `GET /api/v1/records/{ref}/comments` (`list_comments`): chronological, keyset-paginated. */
export async function listComments(
  ref: string,
  options: ListCommentsOptions = {},
): Promise<CommentPage> {
  return apiRequest<CommentPage>(
    `/records/${encodeURIComponent(ref)}/comments${buildCommentsQuery(options)}`,
  );
}

/** `POST /api/v1/records/{ref}/comments` (`add_comment`). */
export async function addComment(ref: string, body: string): Promise<CommentDoc> {
  return apiRequest<CommentDoc>(`/records/${encodeURIComponent(ref)}/comments`, {
    method: "POST",
    body: JSON.stringify({ body } satisfies CommentBody),
  });
}

/** `PATCH /api/v1/comments/{comment_id}` (`update_comment`). The prior body is retained in the
 * audit store and the response comment is marked `edited` (FR-C6). */
export async function updateComment(commentId: string, body: string): Promise<CommentDoc> {
  return apiRequest<CommentDoc>(`/comments/${encodeURIComponent(commentId)}`, {
    method: "PATCH",
    body: JSON.stringify({ body } satisfies CommentBody),
  });
}

/** `DELETE /api/v1/comments/{comment_id}` (`delete_comment`). Soft delete: the response carries
 * `deleted_at`, and the record no longer lists it among its live comments (FR-C6). */
export async function deleteComment(commentId: string): Promise<CommentDoc> {
  return apiRequest<CommentDoc>(`/comments/${encodeURIComponent(commentId)}`, {
    method: "DELETE",
  });
}
