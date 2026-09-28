/**
 * A file size a person reads, in the units a file manager uses.
 *
 * Its own module rather than an export from `AttachmentField.tsx`: a component file that also
 * exports a function breaks Fast Refresh, which `react-refresh/only-export-components` enforces
 * across this codebase.
 *
 * Binary steps, because `GW_MAX_ATTACHMENT_BYTES` and an attachment field's own `config.max_bytes`
 * are both stated in MiB. Deterministic for a given byte count, which is what lets the visual
 * baseline carry a size unmasked.
 */
export function formatByteSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}
