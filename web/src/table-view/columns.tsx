/**
 * Builds one TanStack Table column per object-type field, in declared order (FR-U1). This is
 * pure column *configuration* — value accessors (also the grouping key, per
 * `createGroupedRowModel`'s `row.getValue(columnId)`) and which component renders a cell. The
 * interactive editing behavior itself lives in `EditableCell.tsx`; the PATCH call and 409
 * handling live in `useInlineCellEdit.ts`.
 */
import { Link } from "react-router-dom";
import { legacyCreateColumnHelper } from "@tanstack/react-table/legacy";
import type { LegacyColumnDef } from "@tanstack/react-table/legacy";
import type { FieldDoc } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import "./tableMeta";
import { EditableCell } from "./EditableCell";
import { Hand } from "../ui/Avatar";
import { relationGroupKey } from "./relationExpand";

export const SELECT_COLUMN_ID = "__select__";
export const KEY_COLUMN_ID = "__key__";
export const BY_COLUMN_ID = "__by__";

/** The columns the table adds at its own boundary rather than holding in `columnOrder` state.
 * Neither is a field, so neither belongs in the persisted saved-view blob, the CSV export's
 * column list, or `ColumnPicker`'s move-up/move-down indexing — and neither offers a sort,
 * because the query has no field to sort on. */
export const DISPLAY_COLUMN_IDS: ReadonlySet<string> = new Set([
  SELECT_COLUMN_ID,
  KEY_COLUMN_ID,
  BY_COLUMN_ID,
]);

const columnHelper = legacyCreateColumnHelper<RecordDoc>();

/** The checkbox column driving row multi-select for bulk edit/delete. Always first, never part
 * of the persisted column order/visibility (it isn't a field). */
export function buildSelectionColumn(): LegacyColumnDef<RecordDoc> {
  return columnHelper.display({
    id: SELECT_COLUMN_ID,
    size: 36,
    enableResizing: false,
    header: ({ table }) => (
      <input
        className="accent-accent"
        type="checkbox"
        aria-label="Select all rows"
        checked={table.getIsAllRowsSelected()}
        ref={(node) => {
          if (node) node.indeterminate = table.getIsSomeRowsSelected() && !table.getIsAllRowsSelected();
        }}
        onChange={table.getToggleAllRowsSelectedHandler()}
      />
    ),
    cell: ({ row }) => (
      <input
        className="accent-accent"
        type="checkbox"
        aria-label={`Select row ${row.original.key}`}
        checked={row.getIsSelected()}
        onChange={row.getToggleSelectedHandler()}
      />
    ),
  });
}

/** The record's key, as a link to its detail page.
 *
 * Without it a row could not be named, quoted to an agent, or typed into the audit browser's Record
 * filter, and there would be nothing to click through to the record with. Same route and mono
 * treatment `SearchPage` already uses.
 *
 * A display column at the table boundary, exactly as the selection column is, for the three
 * reasons that were measured: `visibleFieldKeys` derives the CSV export's column list from
 * `columnOrder` (FR-U7), `serializeTableViewConfig` persists it into every saved view, and
 * `ColumnPicker` indexes its move-up/move-down `disabled` against it. */
export function buildKeyColumn(objectTypeKey: string): LegacyColumnDef<RecordDoc> {
  return columnHelper.display({
    id: KEY_COLUMN_ID,
    size: 120,
    header: () => "Key",
    cell: ({ row }) => (
      <Link
        to={`/${objectTypeKey}/${row.original.key}`}
        className="font-mono text-sm text-human-ink hover:underline"
      >
        {row.original.key}
      </Link>
    ),
  });
}

/**
 * docs/DESIGN.md 6.4: the `By` column, the `Avatar` of the hand that last changed the row.
 *
 * **Second, not first.** 6.4 says "every table's first column", but the selection checkbox is
 * documented "Always first" above and exists to feed `BulkToolbar`. 6.4 gives the order as
 * `__select__`, `By`, `__key__`, then fields.
 *
 * **Records table only.** 6.4's "every table" is also narrowed: the other five tables sharing
 * these recipes have no "hand that last changed the row" to render — audit rows *are* events and
 * carry a principal already, CSV wizard rows are CSV columns, schema editor rows are field
 * definitions, permissions rows are principals, and blast-radius rows are affected records.
 *
 * **No sort, and no `Pair`.** A display column at the table boundary offers no sort because the
 * query has no field to sort on, and sorting by kind would need a ninth pseudo-field — DD-20
 * reserves exactly eight, published in `describe_capabilities`, so adding one is its own
 * decision. A `Pair` would need the *previous* version's hand, which the record row does not
 * carry. 6.4 records both, and also where the person-and-agent signal does live: the record's
 * audit timeline.
 *
 * The avatar renders alone at 56px — there is no room for a name — with the name in its
 * accessible label and its tooltip, so the row still says who without being read aloud.
 */
export function buildByColumn(): LegacyColumnDef<RecordDoc> {
  return columnHelper.display({
    id: BY_COLUMN_ID,
    size: 56,
    enableResizing: false,
    header: () => "By",
    cell: ({ row, table }) => {
      const record = row.original;
      const meta = table.options.meta;
      const labelId = record.updated_by_agent_label_id;
      const agentLabel = labelId ? meta?.agentLabels?.[labelId] : undefined;
      return (
        <Hand
          principal={meta?.principals?.[record.updated_by]}
          agentLabel={agentLabel ?? null}
          fallbackId={record.updated_by}
          size={meta?.density === "compact" ? "rowCompact" : "row"}
          avatarOnly
        />
      );
    },
  });
}

/** One column per field, honoring the object type's declared field order. `record.data[key]`
 * backs every non-relation field's value; a relation field's accessor resolves to its group key
 * (`record.expand[key][0]?.key`, or "(unlinked)") so grouping-by-relation works the same way
 * grouping-by-select does, via the column's own value. */
export function buildDataColumns(fields: FieldDoc[]): LegacyColumnDef<RecordDoc>[] {
  return fields.map((field) =>
    columnHelper.accessor(
      (record) =>
        field.type === "relation" ? relationGroupKey(record, field.key) : record.data[field.key],
      {
        id: field.key,
        header: field.name,
        size: 180,
        minSize: 80,
        enableResizing: true,
        meta: { field },
        cell: (context) => <EditableCell field={field} record={context.row.original} table={context.table} />,
      },
    ),
  );
}
