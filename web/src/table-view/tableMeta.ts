/**
 * Module augmentation for TanStack Table's two open extension points: a column's `meta` (here,
 * the `FieldDoc` a column renders/edits) and the table's own `meta` (here, the inline-edit
 * commit callback every editable cell calls). Both interfaces ship empty in `@tanstack/table-core`
 * specifically for this kind of declaration merging.
 */
import type { CellData, RowData, TableFeatures } from "@tanstack/table-core";
import type { FieldDoc } from "../api/objectTypes";
import type { AgentLabelSidecar } from "../api/agentLabels";
import type { PrincipalSidecar } from "../api/principals";
import type { Density } from "../ui/density";
import type { RecordDoc } from "../api/records";

/* eslint-disable @typescript-eslint/no-unused-vars -- these type parameters exist only to match
   the declaration-merged interfaces' own signatures in @tanstack/table-core; neither body uses
   them. */
declare module "@tanstack/table-core" {
  interface ColumnMeta<
    TFeatures extends TableFeatures,
    TData extends RowData,
    TValue extends CellData = CellData,
  > {
    field?: FieldDoc;
  }

  interface TableMeta<TFeatures extends TableFeatures, TData extends RowData> {
    /** Commits one field's edited value for one row (`EditableCell.tsx`); the actual `PATCH`
     * call and 409 handling live in `useInlineCellEdit.ts`, not here. */
    onCellCommit?: (record: RecordDoc, field: FieldDoc, rawValue: unknown) => void;
    /** Whether this caller holds `write` on the object type. `false` makes every cell render
     * its value as text rather than as a button that opens an editor — the `update_record`
     * route is `write`, so an editor here would only ever earn a 403. Undefined is treated as
     * `true`, so a caller outside `TableView` need not pass it. */
    canWrite?: boolean;
    /** The current page's `principals` sidecar, the same shape `query_records`
     * returns as a sibling of `records`. `EditableCell` reads it here — the same idiom
     * `onCellCommit` already uses — to resolve a `user_ref` cell's display name without a second
     * prop threaded through `columns.tsx`. */
    principals?: PrincipalSidecar;
    /** The page's `agent_labels` sidecar, the sibling of `principals` that resolves a
     * record's `updated_by_agent_label_id` to the label a person can read. Read by the `By`
     * column through the same idiom `principals` already uses. */
    agentLabels?: AgentLabelSidecar;
    /** docs/DESIGN.md 2.4: the row density, which the `By` column's avatar matches (20px
     * comfortable, 18px compact). Threaded here rather than as a prop for the same reason
     * `principals` is — `columns.tsx` builds the cell and takes no props of its own. */
    density?: Density;
  }
}
/* eslint-enable @typescript-eslint/no-unused-vars */

export {};
