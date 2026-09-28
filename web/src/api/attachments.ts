/**
 * Typed wrappers over `POST /api/v1/attachments` and the attachment download route
 * (`src/glosswork/routes/attachments.py`; FR-S3, FR-P2).
 *
 * Upload is a multipart POST built exactly the way `csv.ts::importCsv` builds one: `apiFetch`
 * rather than `apiRequest`, and `Content-Type` deliberately left unset so `fetch` writes the
 * boundary itself. The CSRF header (DD-10) comes from `apiFetch` for free, since `POST` is not
 * a safe method.
 *
 * The download is deliberately **not** a fetch. It is a same-origin href the browser follows on
 * its own: `GET` needs no CSRF header, the `gw_session` cookie rides along, and the server
 * already answers with `Content-Disposition: attachment` plus `nosniff`, so the file is saved
 * rather than rendered inside the SPA's origin. Fetching it into a blob URL here would take
 * that disposition off the response and put the bytes back in the document's own origin, which
 * is the one thing those two headers exist to prevent.
 */
import { API_BASE_URL, apiFetch } from "./client";

/** `POST /api/v1/attachments`'s response body (`envelopes.py::attachment_doc`, shared with
 * `GET /api/v1/attachments/{id}` and the `get_attachment` MCP tool). Wider than
 * `AttachmentRef`, which is the four keys this SPA reads off the record sidecar. */
export interface UploadedAttachment {
  id: string;
  sha256: string;
  filename: string;
  content_type: string;
  byte_size: number;
  uploaded_at: string;
  uploaded_by: string;
}

/** `POST /api/v1/attachments`. Stores the blob and returns its row; attaching the returned id
 * to a record is a separate `update_record` the caller makes (through `commitCell`, the
 * one write path). */
export async function uploadAttachment(file: File): Promise<UploadedAttachment> {
  const form = new FormData();
  form.set("file", file, file.name);
  const response = await apiFetch("/attachments", { method: "POST", body: form });
  return (await response.json()) as UploadedAttachment;
}

/** The same-origin href for `GET /api/v1/attachments/{id}/download`. */
export function attachmentDownloadUrl(attachmentId: string): string {
  return `${API_BASE_URL}/attachments/${encodeURIComponent(attachmentId)}/download`;
}
