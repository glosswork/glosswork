/**
 * One `attachment` field on the record card: the files it holds, a download link for each, and
 * and, above `read`, an upload control and a per-row Remove (FR-U2).
 *
 * **It never asks the level question itself.** `canWrite` arrives as a prop, already derived
 * once by `RecordDetailView` for the whole screen. Importing `levelAllows` here would make this
 * a fifth screen pinned by `access/hidingIsNeverTheOnlySignal.test.ts` and would oblige it to
 * render its own `ReadOnlyBanner`, giving the record card two banners, which is exactly what
 * the "exactly one banner naming the level held" rule forbids. Gating once and passing a
 * boolean is what `FieldList` already does for its Edit buttons.
 *
 * **Every mutation composes the new id array from `record.data[field.key]`, never from the rows
 * on screen.** The `attachments` sidecar silently omits an id that names no row *and* an id
 * the attachment read rule withholds from this caller, and the two are indistinguishable from
 * here. Rebuilding the array from what resolved would therefore write `[]` on a record
 * holding an id the user cannot see, destroying it. So a remove is a filter over the stored
 * ids and an upload is an append to them.
 *
 * **An unresolved id still gets a row**, so the number of rows always equals the number of ids
 * stored. Its wording covers both causes without claiming either, because the client cannot
 * tell them apart. Showing only what resolved would make the screen under-report what the
 * record holds, the silent absence "hiding is never the only signal" exists to prevent.
 *
 * There is no write path of its own: the value is saved through the `onCommit` callback, which
 * `FieldList` wires to the same `useInlineCellEdit.commitCell` its Edit buttons and the table's
 * inline cell edit use, so the `expected_version` check and `MergeConflictDialog` are inherited
 * rather than reimplemented. The card refetches on success and the new filename arrives from
 * the server; there is no optimistic row and no second source of truth.
 */
import { useState } from "react";
import type { FieldDoc } from "../api/objectTypes";
import { attachmentDownloadUrl, uploadAttachment } from "../api/attachments";
import type { AttachmentRef, RecordWithIncludes } from "../api/records";
import { Alert } from "../ui/Alert";
import { Button } from "../ui/Button";
import { btnSmClass, fieldHelpClass } from "../ui/classes";
import { formatByteSize } from "./byteSize";

interface AttachmentFieldProps {
  field: FieldDoc;
  record: RecordWithIncludes;
  /** `record.attachments?.[field.key]`: the resolved subset, passed in rather than dug out
   * here so the "sidecar is not the value" asymmetry is visible at the call site too. */
  resolved: AttachmentRef[];
  canWrite: boolean;
  /** `FieldList`'s `commitCell` wrapper. The only way this field is ever written. */
  onCommit: (field: FieldDoc, value: unknown) => void;
}

/** The record's own stored ids for this field, which are the only thing a mutation composes
 * from. Defensive about shape rather than trusting it: `data[key]` is `unknown` on the wire. */
function storedIds(record: RecordWithIncludes, fieldKey: string): string[] {
  const value = record.data[fieldKey];
  if (!Array.isArray(value)) return [];
  return value.filter((entry): entry is string => typeof entry === "string");
}

export function AttachmentField({
  field,
  record,
  resolved,
  canWrite,
  onCommit,
}: AttachmentFieldProps) {
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const ids = storedIds(record, field.key);
  const byId = new Map(resolved.map((entry) => [entry.id, entry]));

  const maxFilesRaw = field.config.max_files;
  const maxFiles = typeof maxFilesRaw === "number" ? maxFilesRaw : undefined;
  const atCapacity = maxFiles !== undefined && ids.length >= maxFiles;

  const handlePick = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // Reset before the await: re-picking the same file must fire `change` again, and the input
    // is not re-rendered between picks.
    event.target.value = "";
    if (!file) return;
    setUploadError(null);
    setUploading(true);
    try {
      const uploaded = await uploadAttachment(file);
      // Composed from the stored ids, not from `resolved` (see this file's header).
      onCommit(field, [...ids, uploaded.id]);
    } catch (caught) {
      setUploadError(caught instanceof Error ? caught.message : "Could not upload the file.");
    } finally {
      setUploading(false);
    }
  };

  const handleRemove = (id: string) => {
    onCommit(
      field,
      ids.filter((stored) => stored !== id),
    );
  };

  return (
    <div className="space-y-2" data-testid={`attachment-field-${field.key}`}>
      {uploadError && (
        <Alert tone="error" title="Could not upload the file.">
          {uploadError}
        </Alert>
      )}

      {ids.length === 0 ? (
        <p className="text-ink-2">No files.</p>
      ) : (
        <ul className="space-y-1">
          {ids.map((id) => {
            const entry = byId.get(id);
            return (
              <li key={id} className="flex items-center justify-between gap-2">
                {entry ? (
                  <span className="min-w-0 truncate">
                    <a
                      className="text-human-ink hover:underline"
                      href={attachmentDownloadUrl(entry.id)}
                    >
                      {entry.filename}
                    </a>
                    <span className="ml-2 text-xs text-ink-2">
                      {formatByteSize(entry.byte_size)}
                    </span>
                  </span>
                ) : (
                  /* Both causes, neither claimed: the sidecar cannot tell "no such row" from
                     "you may not read it", so neither can this row. No request is made
                     for it. The id is shown as withheld, not made readable. */
                  <span className="min-w-0 truncate text-ink-2">
                    Unavailable file (you may not have access, or it was removed)
                  </span>
                )}
                {canWrite && (
                  <Button
                    type="button"
                    variant="quiet"
                    className={btnSmClass}
                    aria-label={`Remove ${entry ? entry.filename : "unavailable file"}`}
                    onClick={() => handleRemove(id)}
                  >
                    Remove
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {canWrite && (
        <div>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-ink-2">Add a file</span>
            <input
              type="file"
              className="block max-w-md text-sm text-ink file:mr-2 file:rounded-ctl file:border file:border-line-2 file:bg-surface file:px-2 file:py-1 file:text-sm file:text-ink"
              disabled={atCapacity || uploading}
              onChange={(event) => void handlePick(event)}
            />
          </label>
          {atCapacity && (
            <p className={fieldHelpClass}>
              {`This field holds at most ${maxFiles} ${maxFiles === 1 ? "file" : "files"}. Remove one to add another.`}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
