/**
 * Bulk edit's two-step write path (docs/MCP_TOOLS.md 5.2's own guidance): a `dry_run: true`
 * preview of `affected_count` first, a real call only on explicit confirmation. Scoped to
 * exactly the multi-selected rows via the `key in [...]` filter trick (`bulkEditFilter.ts`)
 * since `bulk-update` has no "these record ids" parameter of its own.
 */
import { useCallback, useState } from "react";
import { bulkUpdateRecords, type BulkUpdateResult } from "../api/records";
import { selectedKeysFilter } from "./bulkEditFilter";

export interface BulkEditPreview {
  recordKeys: string[];
  fieldKey: string;
  value: unknown;
  affectedCount: number;
  sampleKeys: string[];
}

export interface UseBulkEditResult {
  preview: BulkEditPreview | null;
  error: string | null;
  runPreview: (recordKeys: string[], fieldKey: string, value: unknown) => Promise<void>;
  confirm: () => Promise<BulkUpdateResult | null>;
  cancel: () => void;
}

export function useBulkEdit(objectTypeKey: string): UseBulkEditResult {
  const [preview, setPreview] = useState<BulkEditPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runPreview = useCallback(
    async (recordKeys: string[], fieldKey: string, value: unknown) => {
      setError(null);
      try {
        const result = await bulkUpdateRecords(objectTypeKey, {
          filter: selectedKeysFilter(recordKeys) as unknown as Record<string, unknown>,
          values: { [fieldKey]: value },
          dry_run: true,
        });
        setPreview({
          recordKeys,
          fieldKey,
          value,
          affectedCount: result.affected_count,
          sampleKeys: result.sample_keys,
        });
      } catch (caught) {
        setPreview(null);
        setError(caught instanceof Error ? caught.message : "Failed to preview the bulk edit.");
      }
    },
    [objectTypeKey],
  );

  const confirm = useCallback(async () => {
    if (preview === null) return null;
    setError(null);
    try {
      const result = await bulkUpdateRecords(objectTypeKey, {
        filter: selectedKeysFilter(preview.recordKeys) as unknown as Record<string, unknown>,
        values: { [preview.fieldKey]: preview.value },
        dry_run: false,
      });
      setPreview(null);
      return result;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to apply the bulk edit.");
      return null;
    }
  }, [objectTypeKey, preview]);

  const cancel = useCallback(() => setPreview(null), []);

  return { preview, error, runPreview, confirm, cancel };
}
