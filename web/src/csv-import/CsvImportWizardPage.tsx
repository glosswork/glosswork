/**
 * The CSV import wizard (FR-U6). Route: `/:objectTypeKey/import`.
 *
 * `POST /api/v1/object-types/{key}/import` takes no column-mapping parameter — it requires the
 * CSV header row to already equal object-type field keys (or the literal `key` column used for
 * upsert matching), rejecting anything else. Column mapping is therefore entirely a frontend
 * concern: parse the uploaded file client-side, suggest a field per header by name similarity
 * (`suggestFieldForHeader`), let the user confirm/override/exclude columns, then rewrite the
 * header row (and drop excluded columns from every row) before ever calling the import route.
 * No file content reaches the server until the user has confirmed mapping and mode and triggers
 * a dry run.
 *
 * The commit step is deliberately blocked while the most recent dry run reported any row error,
 * forcing a corrected re-run rather than a partial commit (FR-U6).
 */
import { type ChangeEvent, useState } from "react";
import { useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { levelAllows, type FieldDoc } from "../api/objectTypes";
import { ReadOnlyBanner } from "../access/ReadOnlyBanner";
import { type CsvImportResult, importCsv } from "../api/csv";
import { useObjectType } from "../hooks/useObjectType";
import { suggestFieldForHeader } from "./columnMatching";
import { parseCsv, serializeCsv } from "./csvTable";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { Checkbox } from "../ui/Checkbox";
import { EmptyState } from "../ui/EmptyState";
import { Select } from "../ui/Select";
import { Spinner } from "../ui/Spinner";
import { fieldLabelClass } from "../ui/classes";
import { rowClass, tableClass, tableWrapClass, tdClass, thClass } from "../ui/tableClasses";
import { cx } from "../ui/cx";

/** The record's own human key: a real backend-recognized column for upsert matching, not a
 * field on the object type. */
const KEY_COLUMN = "key";
/** Sentinel `<select>` value meaning "exclude this column from the upload entirely". */
const SKIP_COLUMN = "";

type ImportMode = "create" | "upsert";

/** `submitError`'s shape: `title` is always the screen's existing fixed string, verbatim;
 * `cause` is the caught error, when there is one, so `Alert` can parse a server-sent FR-A4
 * envelope beside it rather than rendering the raw `ApiError.body`. */
interface SubmitError {
  title: string;
  cause?: unknown;
}

function normalizedEquals(value: string, target: string): boolean {
  return value.trim().toLowerCase() === target;
}

/** Column-mapping default for one header: the literal `key` column if the header names it,
 * else a name-similarity suggestion, else "exclude this column". */
function suggestMapping(header: string, fields: FieldDoc[]): string {
  if (normalizedEquals(header, KEY_COLUMN)) return KEY_COLUMN;
  return suggestFieldForHeader(header, fields) ?? SKIP_COLUMN;
}

/** Rewrites the parsed data rows into the CSV text the import route accepts, using `mapping`
 * (one chosen destination column per original header index) for the new header row: excluded
 * columns are dropped from every row, not just the header. */
function buildRewrittenCsv(dataRows: string[][], mapping: string[]): string {
  const includedIndexes = mapping
    .map((value, index) => (value === SKIP_COLUMN ? -1 : index))
    .filter((index) => index !== -1);
  const newHeader = includedIndexes.map((index) => mapping[index]);
  const newRows = dataRows.map((row) => includedIndexes.map((index) => row[index] ?? ""));
  return serializeCsv([newHeader, ...newRows]);
}

/** Fields eligible as an upsert key, mirroring the backend's `_require_upsert_key` validation
 * (`src/glosswork/services/csv.py`): the literal `key` column, or any field marked unique. */
function upsertKeyOptions(fields: FieldDoc[]): string[] {
  return [KEY_COLUMN, ...fields.filter((field) => field.unique).map((field) => field.key)];
}

/** Fields a CSV column may be mapped onto, mirroring what the import route will accept
 * CSV carries no attachment data in either direction: export omits an
 * attachment field and import refuses a header naming one. Filtering here rather than at
 * the `<select>` covers both places the list is used, because `suggestMapping` reads the same
 * array, and suggestion is the half that bites: unfiltered, it auto-selects "Files (files)" for
 * a `files` column with no action by the user, straight into a value the backend then drops.
 * `upsertKeyOptions` deliberately keeps the unfiltered list; an attachment field is not unique
 * and that check is unrelated. */
function mappableFields(fields: FieldDoc[]): FieldDoc[] {
  return fields.filter((field) => field.type !== "attachment");
}

/** The `h2` step heading treatment: a top divider plus weight and
 * size, so "Column mapping -> Mode -> Options -> Dry run -> Commit" reads as a sequence of
 * distinct blocks rather than a flat list of same-weight headings. */
const stepHeadingClass = "border-t border-line pt-4 text-lg font-semibold text-ink";

export function CsvImportWizardPage() {
  const { objectTypeKey } = useParams<{ objectTypeKey: string }>();
  const objectTypeQuery = useObjectType(objectTypeKey);
  const queryClient = useQueryClient();

  const [headerRow, setHeaderRow] = useState<string[] | null>(null);
  const [dataRows, setDataRows] = useState<string[][]>([]);
  const [mapping, setMapping] = useState<string[]>([]);
  const [mode, setMode] = useState<ImportMode>("create");
  const [upsertKey, setUpsertKey] = useState<string>("");
  const [createMissingOptions, setCreateMissingOptions] = useState(false);
  const [lastDryRun, setLastDryRun] = useState<CsvImportResult | null>(null);
  const [commitResult, setCommitResult] = useState<CsvImportResult | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<SubmitError | null>(null);

  if (objectTypeQuery.isLoading) return <Spinner />;
  if (objectTypeQuery.isError || !objectTypeQuery.data) {
    return <EmptyState title="Object type not found." />;
  }
  const objectType = objectTypeQuery.data;

  /**
   * Hiding the Import CSV link on the table view gates the *link*, not the route —
   * a bookmark or a typed URL still lands here, and `csv.import_csv` is `write`.
   * So the whole wizard is withheld, and `ReadOnlyBanner` is what stands in its place.
   */
  if (!levelAllows(objectType.your_access, "write")) {
    return (
      <section aria-label="CSV import" className="space-y-4">
        <h1 className="text-2xl font-semibold text-ink">Import CSV: {objectType.name}</h1>
        <ReadOnlyBanner
          typeName={objectType.name}
          level={objectType.your_access}
          required="write"
        />
      </section>
    );
  }

  const fields = objectType.fields;
  const mappable = mappableFields(fields);

  // Any edit to mapping/mode/upsert-key/create-missing-options invalidates the last dry run,
  // forcing a fresh one before commit can be enabled again.
  const invalidatePriorDryRun = () => {
    setLastDryRun(null);
    setCommitResult(null);
  };

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    const text = await file.text();
    const rows = parseCsv(text);
    if (rows.length === 0) {
      setSubmitError({ title: "The uploaded file has no rows." });
      return;
    }
    const [header, ...body] = rows;
    setHeaderRow(header);
    setDataRows(body);
    setMapping(header.map((column) => suggestMapping(column, mappable)));
    setMode("create");
    setUpsertKey("");
    setCreateMissingOptions(false);
    setSubmitError(null);
    invalidatePriorDryRun();
  };

  const handleMappingChange = (index: number, value: string) => {
    setMapping((prev) => prev.map((current, i) => (i === index ? value : current)));
    invalidatePriorDryRun();
  };

  const handleModeChange = (value: ImportMode) => {
    setMode(value);
    invalidatePriorDryRun();
  };

  const handleUpsertKeyChange = (value: string) => {
    setUpsertKey(value);
    invalidatePriorDryRun();
  };

  const handleCreateMissingOptionsChange = (checked: boolean) => {
    setCreateMissingOptions(checked);
    invalidatePriorDryRun();
  };

  const runImport = async (dryRun: boolean): Promise<CsvImportResult | null> => {
    if (!headerRow || !objectTypeKey) return null;
    setIsSubmitting(true);
    setSubmitError(null);
    try {
      const rewritten = buildRewrittenCsv(dataRows, mapping);
      return await importCsv(objectTypeKey, rewritten, {
        mode,
        upsertKey: mode === "upsert" ? upsertKey : undefined,
        dryRun,
        createMissingOptions,
      });
    } catch (caught) {
      // The raw `caught.body` — the server's JSON envelope text itself — never renders
      // directly. `Alert` parses it into `code` +
      // `message` beside this same fixed title; a genuinely unparseable body (a network failure,
      // a non-`ApiError`) falls back to the title alone.
      setSubmitError({ title: "The import request failed.", cause: caught });
      return null;
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDryRun = async () => {
    const result = await runImport(true);
    if (result) setLastDryRun(result);
    setCommitResult(null);
  };

  const handleCommit = async () => {
    const result = await runImport(false);
    if (!result) return;
    setCommitResult(result);
    void queryClient.invalidateQueries({ queryKey: ["table-view-records", objectTypeKey] });
  };

  const canCommit = lastDryRun !== null && lastDryRun.errors.length === 0 && !isSubmitting;

  return (
    <section aria-label="CSV import" className="space-y-4">
      <h1 className="text-2xl font-semibold text-ink">Import CSV: {objectType.name}</h1>

      <div>
        <label htmlFor="csv-import-file" className={fieldLabelClass}>
          Upload CSV file
        </label>
        <input
          id="csv-import-file"
          type="file"
          accept=".csv"
          aria-label="Upload CSV file"
          onChange={(event) => void handleFileChange(event)}
          className="block text-sm text-ink file:mr-2.5 file:rounded-ctl file:border file:border-line-2 file:bg-surface file:px-2.5 file:py-1.5 file:text-sm file:font-medium file:text-ink hover:file:bg-ground"
        />
      </div>

      {submitError && (
        <Alert tone="error" title={submitError.title} error={submitError.cause} />
      )}

      {headerRow && (
        <>
          <h2 className={stepHeadingClass}>Column mapping</h2>
          <div className={tableWrapClass}>
            <table className={tableClass}>
              <thead>
                <tr>
                  <th className={thClass}>CSV column</th>
                  <th className={thClass}>Maps to</th>
                </tr>
              </thead>
              <tbody>
                {headerRow.map((column, index) => (
                  <tr key={`${column}-${index}`} className={rowClass}>
                    <td className={tdClass}>{column}</td>
                    <td className={tdClass}>
                      <Select
                        aria-label={`Column mapping for ${column}`}
                        value={mapping[index] ?? SKIP_COLUMN}
                        onChange={(event) => handleMappingChange(index, event.target.value)}
                      >
                        <option value={SKIP_COLUMN}>(skip this column)</option>
                        <option value={KEY_COLUMN}>key</option>
                        {mappable.map((field) => (
                          <option key={field.key} value={field.key}>
                            {field.name} ({field.key})
                          </option>
                        ))}
                      </Select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2 className={stepHeadingClass}>Mode</h2>
          <div>
            <label htmlFor="csv-import-mode" className={fieldLabelClass}>
              Import mode
            </label>
            <Select
              id="csv-import-mode"
              aria-label="Import mode"
              value={mode}
              onChange={(event) => handleModeChange(event.target.value as ImportMode)}
            >
              <option value="create">Create new records</option>
              <option value="upsert">Create or update (upsert)</option>
            </Select>
          </div>

          {mode === "upsert" && (
            <div>
              <label htmlFor="csv-import-upsert-key" className={fieldLabelClass}>
                Upsert key
              </label>
              <Select
                id="csv-import-upsert-key"
                aria-label="Upsert key"
                value={upsertKey}
                onChange={(event) => handleUpsertKeyChange(event.target.value)}
              >
                <option value="">Select an upsert key</option>
                {upsertKeyOptions(fields).map((key) => (
                  <option key={key} value={key}>
                    {key}
                  </option>
                ))}
              </Select>
            </div>
          )}

          <h2 className={stepHeadingClass}>Options</h2>
          <Checkbox
            aria-label="Create missing options"
            checked={createMissingOptions}
            onChange={(event) => handleCreateMissingOptionsChange(event.target.checked)}
          >
            Create missing options: if checked, unrecognized single-select/multi-select values
            are added as new options; if unchecked, a row with an unrecognized option value fails
            (FR-E3).
          </Checkbox>

          <h2 className={stepHeadingClass}>Dry run</h2>
          <Button
            type="button"
            variant="secondary"
            disabled={isSubmitting || (mode === "upsert" && !upsertKey)}
            onClick={() => void handleDryRun()}
          >
            Run dry-run
          </Button>

          {lastDryRun && (
            <div role="status">
              {lastDryRun.errors.length === 0 ? (
                <Alert
                  tone="success"
                  title={`Dry run passed: ${lastDryRun.created} would be created, ${lastDryRun.updated} would be updated.`}
                />
              ) : (
                <>
                  <p className="text-sm font-semibold text-bad">
                    {lastDryRun.errors.length} row(s) failed validation.
                  </p>
                  <div className={tableWrapClass}>
                    <table className={tableClass} data-testid="dry-run-errors">
                      <thead>
                        <tr>
                          <th className={thClass}>Row</th>
                          <th className={thClass}>Field</th>
                          <th className={thClass}>Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {lastDryRun.errors.map((error, index) => (
                          <tr
                            key={index}
                            data-testid={`dry-run-error-${index}`}
                            className={rowClass}
                          >
                            <td className={cx(tdClass, "text-bad")}>{error.row}</td>
                            <td className={cx(tdClass, "text-bad")}>{error.field ?? ""}</td>
                            <td className={cx(tdClass, "text-bad")}>{error.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}

          <h2 className={stepHeadingClass}>Commit</h2>
          <Button type="button" variant="primary" disabled={!canCommit} onClick={() => void handleCommit()}>
            Commit
          </Button>

          {commitResult && (
            <Alert
              tone="success"
              title={`Import complete: ${commitResult.created} created, ${commitResult.updated} updated.`}
            />
          )}
        </>
      )}
    </section>
  );
}
