/**
 * Typed wrapper functions over the saved-view routes (FR-U3, `src/glosswork/routes/saved_views.py`).
 *
 * `CreateSavedViewBody` / `UpdateSavedViewBody` are precisely generated request-body types
 * reused from `schema.ts`. The response bodies are `dict[str, Any]` from FastAPI, so the
 * interface below describes the real shape produced by `envelopes.py::saved_view_doc`.
 */
import type { components } from "./schema";
import { apiRequest } from "./client";

export type CreateSavedViewBody = components["schemas"]["CreateSavedViewBody"];
export type UpdateSavedViewBody = components["schemas"]["UpdateSavedViewBody"];

export interface SavedView {
  id: string;
  object_type_id: string;
  name: string;
  description: string | null;
  mode: string;
  config: Record<string, unknown>;
  is_default: boolean;
  created_at: string;
  created_by: string;
  updated_at: string;
  updated_by: string;
}

/** `GET /api/v1/object-types/{objectTypeKey}/saved-views`. */
export async function listSavedViews(objectTypeKey: string): Promise<SavedView[]> {
  return apiRequest<SavedView[]>(`/object-types/${encodeURIComponent(objectTypeKey)}/saved-views`);
}

/** `POST /api/v1/object-types/{objectTypeKey}/saved-views`. */
export async function createSavedView(
  objectTypeKey: string,
  body: CreateSavedViewBody,
): Promise<SavedView> {
  return apiRequest<SavedView>(`/object-types/${encodeURIComponent(objectTypeKey)}/saved-views`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** `PATCH /api/v1/saved-views/{viewId}`. */
export async function updateSavedView(
  viewId: string,
  body: UpdateSavedViewBody,
): Promise<SavedView> {
  return apiRequest<SavedView>(`/saved-views/${encodeURIComponent(viewId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

/** `DELETE /api/v1/saved-views/{viewId}`. */
export async function deleteSavedView(viewId: string): Promise<SavedView> {
  return apiRequest<SavedView>(`/saved-views/${encodeURIComponent(viewId)}`, {
    method: "DELETE",
  });
}
