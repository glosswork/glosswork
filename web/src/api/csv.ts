/**
 * Typed wrapper functions over `POST /api/v1/object-types/{key}/import` and
 * `GET /api/v1/object-types/{key}/export` (FR-U6, FR-U7; `src/glosswork/services/csv.py`).
 *
 * Import is a multipart upload (`apiFetch`, not `apiRequest`: the body is `FormData`, and the
 * response is plain JSON we parse ourselves). Export streams `text/csv`, so it returns a `Blob`
 * rather than parsed JSON. Neither route takes a column-mapping parameter — the CSV header must
 * already equal field keys (or the literal `key` column) by the time it reaches these functions;
 * rewriting the header is the caller's job (`web/src/csv-import/`).
 */
import { apiFetch } from "./client";

export interface CsvImportError {
  row: number;
  field: string | null;
  reason: string;
}

export interface CsvImportResult {
  dry_run: boolean;
  created: number;
  updated: number;
  errors: CsvImportError[];
}

export interface ImportCsvOptions {
  mode: "create" | "upsert";
  upsertKey?: string;
  dryRun: boolean;
  createMissingOptions: boolean;
}

/** `POST /api/v1/object-types/{key}/import`. `csvText` must already have a field-key (or
 * `key`) header row. */
export async function importCsv(
  objectTypeKey: string,
  csvText: string,
  options: ImportCsvOptions,
): Promise<CsvImportResult> {
  const form = new FormData();
  form.set("file", new Blob([csvText], { type: "text/csv" }), "import.csv");
  form.set("mode", options.mode);
  if (options.upsertKey) form.set("upsert_key", options.upsertKey);
  form.set("dry_run", String(options.dryRun));
  form.set("create_missing_options", String(options.createMissingOptions));
  const response = await apiFetch(`/object-types/${encodeURIComponent(objectTypeKey)}/import`, {
    method: "POST",
    body: form,
  });
  return (await response.json()) as CsvImportResult;
}

export interface ExportCsvOptions {
  filter?: unknown;
  sort?: unknown;
  columns?: string[];
}

/** `GET /api/v1/object-types/{key}/export`. Returns the streamed CSV as a `Blob` for the
 * caller to turn into a file download. */
export async function exportCsv(
  objectTypeKey: string,
  options: ExportCsvOptions = {},
): Promise<Blob> {
  const params = new URLSearchParams();
  if (options.filter) params.set("filter", JSON.stringify(options.filter));
  if (options.sort) params.set("sort", JSON.stringify(options.sort));
  if (options.columns?.length) params.set("columns", options.columns.join(","));
  const search = params.toString();
  const response = await apiFetch(
    `/object-types/${encodeURIComponent(objectTypeKey)}/export${search ? `?${search}` : ""}`,
  );
  return await response.blob();
}
