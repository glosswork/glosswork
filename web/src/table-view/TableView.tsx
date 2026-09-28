/**
 * The real table view (FR-U1): server-driven filter/sort/query, client-side grouping over the
 * fetched page (TanStack Table's grouping feature), inline cell editing with 409 merge handling
 * (FR-U10), row multi-select driving bulk edit/delete, and saved views (FR-U3). This component
 * wires state and rendering; every non-trivial transform (sort-key cycling, saved-view
 * serialization, merge-conflict resolution, relation grouping) is a pure function imported from
 * a sibling module with its own unit tests.
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { flexRender } from "@tanstack/react-table";
import { useLegacyTable, getGroupedRowModel } from "@tanstack/react-table/legacy";
import { exportCsv } from "../api/csv";
import { levelAllows, type ObjectTypeDetail } from "../api/objectTypes";
import { ReadOnlyBanner } from "../access/ReadOnlyBanner";
import { updateRecord, type QueryResult, type RecordDoc } from "../api/records";
import type { SavedView } from "../api/savedViews";
import { ownsFilterError } from "../filters/chipFilter";
import type { FilterNode } from "../filters/types";
import { BulkToolbar } from "./BulkToolbar";
import {
  buildDataColumns,
  buildByColumn,
  buildKeyColumn,
  buildSelectionColumn,
  BY_COLUMN_ID,
  DISPLAY_COLUMN_IDS,
  KEY_COLUMN_ID,
  SELECT_COLUMN_ID,
} from "./columns";
import { ColumnPicker } from "./ColumnPicker";
import { DensityToggle } from "./DensityToggle";
import { FilterChipRow } from "./FilterChipRow";
import { readDensity, resolveDensity, writeDensity, type Density } from "../ui/density";
import { GroupBySelect } from "./GroupBySelect";
import { groupRowLabel } from "./groupLabel";
import { MergeConflictDialog } from "./MergeConflictDialog";
import { NewRecordDialog } from "./NewRecordDialog";
import { parseValidationFailed, parseVersionConflict } from "./apiErrors";
import { AboutDisclosure } from "./AboutDisclosure";
import {
  agentTouchedCount,
  agentTouchedLabel,
  currentViewLabel,
  hiddenByFilterCount,
  hiddenByFilterLabel,
  recordCountLabel,
} from "./footerCounts";
import { SortControls } from "./SortControls";
import { ViewMenu } from "./ViewMenu";
import type { SortKey } from "./sortSpec";
import { cycleSingleSort } from "./sortSpec";
import { defaultTableViewConfig, parseTableViewConfig, serializeTableViewConfig } from "./tableViewConfig";
import type { TableViewConfig } from "./types";
import { useBulkDelete } from "./useBulkDelete";
import { useBulkEdit } from "./useBulkEdit";
import { type InlineEditConflict, useInlineCellEdit } from "./useInlineCellEdit";
import { Button } from "../ui/Button";
import { btnSmClass, linkButtonClass } from "../ui/classes";
import { cx } from "../ui/cx";
import { EmptyState } from "../ui/EmptyState";
import {
  groupTdClass,
  numCellClass,
  recordsTableClassFor,
  rowClass,
  selectedRowClass,
  tableWrapClass,
  tdClassFor,
  thClassFor,
} from "../ui/tableClasses";
import { useSavedViewMutations, useSavedViewsList } from "./useSavedViewsController";
import {
  tableRecordsQueryIdentity,
  tableRecordsQueryKey,
  useTableRecordsQuery,
} from "./useTableRecordsQuery";
import { pageRangeLabel } from "./pageRange";
import { TABLE_VIEW_PAGE_SIZE } from "./constants";
import { Alert } from "../ui/Alert";
import "./tableMeta";

export interface TableViewProps {
  objectType: ObjectTypeDetail;
}

export function TableView({ objectType }: TableViewProps) {
  /**
   * `your_access` is already `min(credential scope, granted level)`, and this screen
   * fetched it on the detail document it is rendering — so gating costs no request. Every
   * affordance below that reads this has a `require_level("write")` twin on the server; the CSV
   * export does not, because `export_csv` is `read`.
   *
   * This is an affordance, not a boundary. The server refuses regardless, and when a grant is
   * revoked between this render and a click, `parseForbidden` is what resolves the disagreement.
   */
  const canWrite = levelAllows(objectType.your_access, "write");
  const navigate = useNavigate();
  const fields = objectType.fields;
  const fieldsByKey = useMemo(
    () => Object.fromEntries(fields.map((field) => [field.key, field])),
    [fields],
  );

  const initial = useMemo(() => defaultTableViewConfig(fields), [fields]);
  const [filter, setFilter] = useState<FilterNode | null>(initial.filter);
  const [sortKeys, setSortKeys] = useState<SortKey[]>(initial.sort);
  const [groupBy, setGroupBy] = useState<string | null>(initial.groupBy);
  const [columnOrder, setColumnOrder] = useState<string[]>(initial.columns.order);
  const [columnVisibility, setColumnVisibility] = useState<Record<string, boolean>>(
    initial.columns.visibility,
  );
  const [columnSizing, setColumnSizing] = useState<Record<string, number>>(initial.columns.sizing);
  const [rowSelection, setRowSelection] = useState<Record<string, true>>({});
  const [selectedViewId, setSelectedViewId] = useState<string | null>(null);
  const [viewGeneration, setViewGeneration] = useState(0);
  const [conflict, setConflict] = useState<InlineEditConflict | null>(null);
  /** Whether the create dialog is mounted. `ui/Dialog`'s open/close model IS
   * mount/unmount, so this boolean is the whole of it. */
  const [creating, setCreating] = useState(false);
  // The cursor walk. `cursors[n]` is the cursor that fetches page n — `cursors[0]` is
  // null, the first page. Next writes the current page's `next_cursor` at `pageIndex + 1` and
  // advances; Previous only decrements. Nothing is popped, so Previous is a cache hit and the
  // forward-only cursor contract is never asked to run backwards.
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [pageIndex, setPageIndex] = useState(0);

  const queryClient = useQueryClient();

  // The reset is derived from the query identity, not fired at the five call sites that can
  // change it (FilterBuilder, SortControls, the column header's own cycleSingleSort,
  // GroupBySelect, and applyConfig on saved-view load). React's "adjusting state when a prop
  // changes" pattern, the same one AuditTimeline uses. Miss one call site and the frontend
  // sends a cursor whose sort spec no longer matches, which `decode_cursor` rejects with a 400
  // — so no call site is asked to remember anything, and a sixth control added later inherits
  // the reset by construction.
  //
  // Row selection resets here too, and that is data loss rather than friction: `rowSelection` is
  // keyed by record key and `useBulkDelete` deletes exactly the selected keys, so a selection
  // made under one query and a delete pressed under another would destroy rows the user cannot
  // see.
  const identity = JSON.stringify(
    tableRecordsQueryIdentity(objectType.key, filter, sortKeys, groupBy),
  );
  const [syncedIdentity, setSyncedIdentity] = useState(identity);
  if (identity !== syncedIdentity) {
    setSyncedIdentity(identity);
    setCursors([null]);
    setPageIndex(0);
    setRowSelection({});
  }

  // docs/DESIGN.md 2.4, persisted twice: `userDensity` is this browser's preference and
  // `viewDensity` is what the loaded saved view says, if anything. `resolveDensity` holds the
  // precedence in one place — the view wins — rather than each read site deciding.
  const [userDensity, setUserDensity] = useState<Density>(readDensity);
  const [viewDensity, setViewDensity] = useState<Density | undefined>(undefined);
  const density = resolveDensity(viewDensity, userDensity);

  function chooseDensity(next: Density) {
    // A deliberate choice replaces the loaded view's, and becomes this browser's default.
    setViewDensity(undefined);
    setUserDensity(next);
    writeDensity(next);
  }

  const cursor = cursors[pageIndex] ?? null;
  const queryKey = tableRecordsQueryKey(objectType.key, filter, sortKeys, groupBy, cursor);

  const applyConfig = useCallback(
    (config: TableViewConfig) => {
      setFilter(config.filter);
      setSortKeys(config.sort);
      setGroupBy(config.groupBy);
      setColumnOrder(config.columns.order);
      setColumnVisibility(config.columns.visibility);
      setColumnSizing(config.columns.sizing);
      setViewDensity(config.density);
      setRowSelection({});
      setViewGeneration((generation) => generation + 1);
    },
    [],
  );

  const savedViewsQuery = useSavedViewsList(objectType.key);
  const savedViewMutations = useSavedViewMutations(objectType.key);
  const appliedDefaultRef = useRef(false);
  const savedViews = useMemo<SavedView[]>(() => savedViewsQuery.data ?? [], [savedViewsQuery.data]);

  useEffect(() => {
    // One-time synchronization from the saved-views query (an external system) into local
    // table state, exactly once per mount: "no view selected yet, and the default has just
    // become known." Deliberate, not a derived-render calculation.
    if (appliedDefaultRef.current || !savedViewsQuery.isSuccess) return;
    appliedDefaultRef.current = true;
    const defaultView = savedViews.find((view) => view.is_default);
    if (defaultView) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setSelectedViewId(defaultView.id);
      applyConfig(parseTableViewConfig(defaultView.config, fields));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [savedViewsQuery.isSuccess, savedViews, fields]);

  const handleClearFilter = () => {
    // Clearing from outside the FilterBuilder: it owns its tree state, so remount it (the same
    // `key={viewGeneration}` mechanism applying a saved view uses).
    setFilter(null);
    setViewGeneration((generation) => generation + 1);
  };

  const handleSelectView = (viewId: string | null) => {
    setSelectedViewId(viewId);
    if (viewId === null) {
      applyConfig(defaultTableViewConfig(fields));
      return;
    }
    const view = savedViews.find((candidate) => candidate.id === viewId);
    if (view) applyConfig(parseTableViewConfig(view.config, fields));
  };

  const currentConfig = (): TableViewConfig =>
    serializeTableViewConfig({
      filter,
      sort: sortKeys,
      groupBy,
      columnOrder,
      columnVisibility,
      columnSizing,
      density,
      mode: "table",
    });

  const handleSave = () => {
    if (selectedViewId) void savedViewMutations.saveExisting(selectedViewId, currentConfig());
  };
  const handleSaveAsNew = (name: string) => {
    void savedViewMutations.saveAsNew(name, currentConfig(), savedViews.length === 0).then((created) => {
      setSelectedViewId(created.id);
    });
  };
  const handleSetDefault = () => {
    if (selectedViewId) void savedViewMutations.setDefault(selectedViewId);
  };

  const recordsQuery = useTableRecordsQuery({
    objectTypeKey: objectType.key,
    filter,
    sort: sortKeys,
    groupBy,
    fields,
    cursor,
  });

  /**
   * Completeness is not acceptance: the compiler refuses complete values too — a `date`
   * that is neither ISO nor a token, an unresolvable `user_ref`, a relation key naming no record
   * — and answers `422 validation_failed` naming the field. When the chip row can say **which
   * chip** that field belongs to, the refusal belongs in that chip's popover and the previous
   * result set stays on screen; a `validation_failed` naming nothing in the filter did not come
   * from the filter, and keeps the page-level alert.
   */
  const validationFailure = recordsQuery.isError ? parseValidationFailed(recordsQuery.error) : null;
  const filterError =
    validationFailure !== null && ownsFilterError(filter, validationFailure.fieldKey)
      ? validationFailure
      : null;

  /**
   * The last result that actually came back. `keepPreviousData` holds the previous page while the
   * next one is *pending* and drops it the moment the query errors (`queryObserver`: placeholder
   * data applies only at `status === "pending"`), which is exactly the "table goes to zero rows"
   * defect. Holding it here is what makes "the previous result set stays on screen" true for a
   * filter's own refusal.
   *
   * Held in state and adjusted during render — React's "adjusting state when a prop changes",
   * the same pattern the identity reset above uses, rather than a ref: a ref read during render
   * is what `react-hooks/refs` forbids, and it would not re-render when it changed anyway.
   */
  const [lastResult, setLastResult] = useState<QueryResult | undefined>(undefined);
  if (recordsQuery.data !== undefined && recordsQuery.data !== lastResult) {
    setLastResult(recordsQuery.data);
  }
  const result = recordsQuery.data ?? (filterError !== null ? lastResult : undefined);

  const records = useMemo(() => result?.records ?? [], [result]);

  const nextCursor = result?.next_cursor ?? null;
  const hasPrevious = pageIndex > 0;
  const hasNext = nextCursor !== null;

  // A page turn clears the selection for the same reason the identity reset does.
  const goToNextPage = () => {
    if (nextCursor === null) return;
    setCursors((prev) => {
      const next = [...prev];
      next[pageIndex + 1] = nextCursor;
      return next;
    });
    setPageIndex((index) => index + 1);
    setRowSelection({});
  };

  const goToPreviousPage = () => {
    if (pageIndex === 0) return;
    setPageIndex((index) => index - 1);
    setRowSelection({});
  };

  const applyRecordPatch = useCallback(
    (updated: RecordDoc) => {
      queryClient.setQueryData(queryKey, (old: QueryResult | undefined) =>
        old
          ? {
              ...old,
              records: old.records.map((record) => (record.key === updated.key ? updated : record)),
            }
          : old,
      );
      void queryClient.invalidateQueries({ queryKey });
    },
    // `queryKey` belongs in the dependency list. Without it both callbacks addressed
    // whichever key the view showed when they were created — and the pager makes that worse,
    // because `queryKey` now varies per page too.
    [queryClient, queryKey],
  );

  const inlineEdit = useInlineCellEdit({
    onSuccess: applyRecordPatch,
    onConflict: setConflict,
  });

  const invalidateRecords = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey });
    // The footer's "3 hidden by your filter" is `record_count` (the type document,
    // unfiltered) minus `total_count` (this query, filtered), so a write that changes how many
    // records exist leaves the first number stale while refreshing the second — and the
    // difference of a stale count and a fresh one is a wrong number rather than an old one. The
    // type document is refetched on the same invalidation that already followed a write, which
    // is what was chosen over a second unfiltered query per render. `useObjectType` keys on
    // `["object-types", key, { includeSamples }]`, and React Query matches keys by prefix.
    void queryClient.invalidateQueries({ queryKey: ["object-types", objectType.key] });
  }, [queryClient, queryKey, objectType.key]);

  const bulkEdit = useBulkEdit(objectType.key);
  const bulkDelete = useBulkDelete(invalidateRecords);

  // The selection column exists only to feed `BulkToolbar`, so it goes with it: a checkbox that
  // selects rows and then offers nothing is exactly the mysterious screen section 3 is against.
  const columns = useMemo(
    () => [
      ...(canWrite ? [buildSelectionColumn()] : []),
      // docs/DESIGN.md 6.4: second, after the selection checkbox that
      // `columns.tsx` documents as always first.
      buildByColumn(),
      buildKeyColumn(objectType.key),
      ...buildDataColumns(fields),
    ],
    [canWrite, fields, objectType.key],
  );

  const table = useLegacyTable<RecordDoc>({
    data: records,
    columns,
    getRowId: (record) => record.key,
    columnResizeMode: "onChange",
    state: {
      columnVisibility,
      // The selection and key columns are prepended here, at the table
      // boundary, rather than held in `columnOrder` state. `columnOrder` also backs
      // `visibleFieldKeys` (the CSV export's `columns` list, FR-U7),
      // `serializeTableViewConfig`'s persisted saved-view blob, and `ColumnPicker`'s
      // move-up/move-down `disabled` indexing — none of which knows about either display
      // column, so both stay out of state and are added only for TanStack's own ordering.
      columnOrder: canWrite
        ? [SELECT_COLUMN_ID, BY_COLUMN_ID, KEY_COLUMN_ID, ...columnOrder]
        : [BY_COLUMN_ID, KEY_COLUMN_ID, ...columnOrder],
      columnSizing,
      rowSelection,
      grouping: groupBy ? [groupBy] : [],
    },
    onColumnVisibilityChange: (updater) =>
      setColumnVisibility((prev) => (typeof updater === "function" ? updater(prev) : updater)),
    onColumnOrderChange: (updater) =>
      setColumnOrder((prev) => {
        const next =
          typeof updater === "function"
            ? updater([SELECT_COLUMN_ID, BY_COLUMN_ID, KEY_COLUMN_ID, ...prev])
            : updater;
        return next.filter((id) => !DISPLAY_COLUMN_IDS.has(id));
      }),
    onColumnSizingChange: (updater) =>
      setColumnSizing((prev) => (typeof updater === "function" ? updater(prev) : updater)),
    onRowSelectionChange: (updater) =>
      setRowSelection((prev) => (typeof updater === "function" ? updater(prev) : updater)),
    getGroupedRowModel: getGroupedRowModel(),
    meta: {
      canWrite,
      // `query_records`' `principals` sidecar, read straight off the page that
      // is already loaded rather than fetched again — `EditableCell`'s `user_ref` branch is the
      // one consumer.
      principals: result?.principals,
      agentLabels: result?.agent_labels,
      density,
      onCellCommit: (record, field, value) => void inlineEdit.commitCell(record, field, value),
    },
  });

  type TableRow = ReturnType<typeof table.getRowModel>["rows"][number];

  const renderRows = (rows: TableRow[]): React.ReactNode =>
    rows.map((row) => {
      if (row.getIsGrouped()) {
        const groupField = row.groupingColumnId ? fieldsByKey[row.groupingColumnId] : undefined;
        const label = groupField
          ? groupRowLabel(groupField, row.groupingValue, row.subRows[0]?.original)
          : String(row.groupingValue);
        return (
          <Fragment key={row.id}>
            <tr data-testid="group-header">
              <td className={groupTdClass} colSpan={columns.length}>
                {label} ({row.subRows.length})
              </td>
            </tr>
            {renderRows(row.subRows)}
          </Fragment>
        );
      }
      return (
        <tr
          key={row.id}
          className={cx(rowClass, row.getIsSelected() && selectedRowClass)}
          data-testid={`row-${row.original.key}`}
        >
          {row.getVisibleCells().map((cell) => {
            const cellFieldType = fieldsByKey[cell.column.id]?.type;
            return (
              <td
                className={cx(
                  tdClassFor(density),
                  (cellFieldType === "integer" || cellFieldType === "decimal") && numCellClass,
                )}
                key={cell.id}
              >
                {flexRender(cell.column.columnDef.cell, cell.getContext())}
              </td>
            );
          })}
        </tr>
      );
    });

  const selectedKeys = Object.keys(rowSelection).filter((key) => rowSelection[key]);

  // FR-U7: the export honors exactly the view's current filter, sort, and visible/ordered
  // columns — the same expression the ColumnPicker call site below uses to determine which
  // field keys are currently visible, in the user's chosen order.
  const visibleFieldKeys = (columnOrder.length ? columnOrder : fields.map((field) => field.key)).filter(
    (key) => columnVisibility[key] !== false,
  );

  /**
   * docs/DESIGN.md 8.2's three counts and the view's name, all derived rather than fetched. The
   * arithmetic and the wording live in `footerCounts.ts`, with unit
   * tests; what stays here is which numbers go in.
   *
   * **The hidden clause is shown only when a filter is active, and only against a result that
   * actually arrived.** `record_count` is the type document's unfiltered live count and
   * `total_count` is the query response's filtered one — two numbers from two requests —
   * so with no result in hand the subtraction is against nothing and says nothing.
   */
  const viewLabel = currentViewLabel(
    savedViews.find((view) => view.id === selectedViewId)?.name,
  );
  const hiddenLabel =
    filter !== null && result !== undefined
      ? hiddenByFilterLabel(hiddenByFilterCount(objectType.record_count, result.total_count))
      : null;
  const agentLabel = agentTouchedLabel(agentTouchedCount(records));

  const handleExport = async () => {
    const blob = await exportCsv(objectType.key, {
      filter: filter ?? undefined,
      sort: sortKeys.length ? sortKeys : undefined,
      columns: visibleFieldKeys,
    });
    downloadCsvBlob(blob, `${objectType.key}.csv`);
  };

  const handleResubmit = async (payload: { values: Record<string, unknown>; expected_version: number }) => {
    if (!conflict) return;
    try {
      const updated = await updateRecord(conflict.record.key, {
        values: payload.values,
        expected_version: payload.expected_version,
        force: false,
      });
      applyRecordPatch(updated);
      setConflict(null);
    } catch (caught) {
      const nextConflict = parseVersionConflict(caught);
      setConflict(
        nextConflict
          ? { record: conflict.record, field: conflict.field, attemptedValue: conflict.attemptedValue, conflict: nextConflict }
          : null,
      );
    }
  };

  return (
    // `aria-label`: an explicit accessible name so the visual baselines can
    // target `getByRole("region", { name: "Records" })` instead of `main section`, which passes
    // vacuously once a route renders two sections. Static rather than the type's own name, so
    // the locator does not depend on which fixture seeded first.
    <section aria-label="Records" className="space-y-3">
      {/* docs/DESIGN.md 8.2's title line: the type name in the display face with the count and
          the view name in `ink-3` ON THE SAME LINE, and the About control beside it. The
          description used to print here as a paragraph of its own; it measured 36px of the 454px
          above the first data row, on a screen whose whole defect was that the data did not lead
          it. It is now one click away, not gone.

          Which count is `footerCounts.ts`'s `recordCountLabel`, recorded there because 8.2 says
          only "the count". */}
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="font-display text-2xl font-semibold text-ink">{objectType.name}</h1>
        {/* **`ink-2`, not the `ink-3` the title-line sentence names.** Otherwise the document
            would contradict itself: 10 says 2.1's contrast floors hold in both themes, and measured
            against the shipped tokens `ink-3` is 2.79:1 on `ground` in light and 4.00:1 in dark
            — both under the 4.5:1 floor this 13.5px text has no large-text allowance from.
            The workspace line hit the identical trap and is resolved the same way, and
            `ui-visual.spec.ts` asserts the painted ratio here rather than the class, so a token
            move re-opens it loudly. 8.2 records the exception. */}
        <p className="text-sm text-ink-2" data-testid="table-title-meta">
          {recordCountLabel(objectType.record_count)} · {viewLabel}
        </p>
        <AboutDisclosure description={objectType.description} />

        {/* **`New <type>` lives here, and not in the toolbar 8.2 lists** (DD-44). The toolbar
            cannot hold it, measured rather than argued: its children are a constant 716.8px, and
            its narrowest width across the whole viewport range is 688px at 960px — the moment the
            sidebar appears and takes 224px while `main` keeps its 48px of padding. That is 28.8px
            of overflow before this control is added, which is also why the toolbar's one-row check
            at 1280 and 800 never caught it: both sit in one-row bands either side of the failure. A
            type-named label is 139px at its worst here, and evicting `Import` would have freed only
            68.6px.

            The deeper reason is that the label carries the object type's **name**, which is user
            data of unbounded length: no one-row guarantee can be made about a row containing it.
            This row is `flex-wrap` and nothing asserts its geometry, so the control simply wraps
            to its own line somewhere below 640px with nothing hidden (DD-42 applied to viewports).

            `ml-auto` because this row has no right-alignment of its own, and `self-center`
            because the row is `items-baseline` — a 34px primary would otherwise hang off the
            `h1`'s baseline. The three existing children are NOT restructured: `table-title-meta`
            carries a painted contrast assertion in both themes, and a restructure here is exactly
            what disturbs it. */}
        {canWrite && (
          <Button
            type="button"
            variant="primary"
            data-testid="new-record"
            className={cx(btnSmClass, "ml-auto self-center")}
            onClick={() => setCreating(true)}
          >
            New {objectType.name}
          </Button>
        )}
      </div>

      <ReadOnlyBanner
        typeName={objectType.name}
        level={objectType.your_access}
        required="write"
      />

      {/* docs/DESIGN.md 8.2's toolbar, in 8.2's own order: filter chips, then group, sort,
          columns and density right-aligned, then Import. `New <type>` is not in it; it sits on
          the title line above, for the reason given there. Six further controls that 8.2 does
          not place — `Saved view`, `Save`, `New view name`, `Save as new`, `Set as default` and
          `Export CSV` — are behind the View menu on the right.

          Earlier layouts show why one row matters: with the controls each owning a full-width
          row, the stack was as tall as the sum of its parts, and the first data row sat 971px
          down a 800px window. Grouped into wrapping rows, they measured ELEVEN controls wrapped
          to two rows, 72px tall. One row of six is what makes the 250px bound reachable rather
          than arithmetic.

          **`items-stretch`, not `items-center`, and the one-row assertion is why.** That
          assertion reads every direct child's
          `y` from `boundingBox()` and asserts they share one row. Under `items-center` a 28px
          chip beside a 30px button sits 1px lower by construction, so the criterion would be
          measuring the incidental difference between two control heights as well as the thing it
          is about. Stretch puts every child at the line's cross-start, so a difference in `y`
          means a wrap and nothing else — which is the whole claim.

          **The right-hand cluster is deliberately NOT grouped.** Wrapping those controls in one
          `<div>` would let them wrap *inside* a child whose own `y` never moves, and the assertion
          would pass over a visibly two-row toolbar. The right-alignment is a single wrapped control
          instead (a lone chip cannot wrap against itself), and `Import CSV` keeps no wrapper at
          all, so even the older anchor — the parent of `Import CSV` — still names this element.

          **`filter-chips` is the one exception, and the assertion cannot see inside it.** Not
          *every* control is a direct child: `FilterChipRow` renders its chips inside one
          `flex-wrap` `<div>`, so chips wrapping among themselves do not move any direct child's
          `y`. That is deliberate rather than a gap to close: `docs/DESIGN.md` 8.2 puts "filter
          chips" in the row as one item, and a view carrying eight conditions cannot be one row at
          any width, so chips wrapping against each other is the design working rather than the
          toolbar breaking. What the assertion proves is that the toolbar's own items share a row;
          it does not prove, and does not claim, that a chip-heavy filter stays on one line. */}
      <div className="flex flex-wrap items-center gap-1.5" data-testid="table-toolbar">
        {/* docs/DESIGN.md 7.4 and 8.2: the filter is a row of sentence chips, and the tree
            builder that used to sit open on the page all the time is behind the `Advanced` chip.
            `/search` keeps the inline builder; this is the table page's layer. */}
        <FilterChipRow
          fields={fields}
          systemFields={objectType.system_fields}
          filter={filter}
          onChange={setFilter}
          builderKey={viewGeneration}
          error={filterError}
        />

        {/* Group, sort and columns are chips — docs/DESIGN.md 7.4 names the first two ("Group
            and sort are the same chip grammar") and 8.2 makes the picker a popover. **Density is
            a chip too**, although 7.4 enumerates only group and sort. What decided it was the
            one-row assertion rather than a second opinion: density as a visible pair of buttons
            measured 197.8px and put this row into two at 800px once the fixture carried a named
            saved view. Both readings and the measurement live in `DensityToggle.tsx`'s header. */}
        <span className="ml-auto inline-flex items-center">
          <GroupBySelect fields={fields} groupBy={groupBy} onChange={setGroupBy} />
        </span>
        <SortControls fields={fields} sort={sortKeys} onChange={setSortKeys} />
        <ColumnPicker
          fields={fields}
          columnOrder={columnOrder.length ? columnOrder : fields.map((field) => field.key)}
          columnVisibility={columnVisibility}
          onChangeOrder={setColumnOrder}
          onChangeVisibility={setColumnVisibility}
        />
        <DensityToggle density={density} onChange={chooseDensity} />

        {canWrite && (
          // docs/DESIGN.md 8.2 calls this control "Import"; every spec that drives it calls it
          // `Import CSV`, and a control that still exists keeps its name. So the
          // visible word is 8.2's and the accessible name is the one already in the tree — and
          // the visible string is the start of the accessible one, which is what WCAG 2.5.3
          // asks of a label inside a name. The 31px this saves matters: the one row at 800 is a
          // width budget, measured.
          <Link
            className={linkButtonClass}
            aria-label="Import CSV"
            to={`/${objectType.key}/import`}
          >
            Import
          </Link>
        )}

        <ViewMenu
          views={savedViews}
          selectedViewId={selectedViewId}
          onSelectView={handleSelectView}
          onSave={handleSave}
          onSaveAsNew={handleSaveAsNew}
          onSetDefault={handleSetDefault}
          onExport={() => void handleExport()}
          canWrite={canWrite}
        />
      </div>

      {canWrite && (
        <BulkToolbar
          selectedKeys={selectedKeys}
          fields={fields}
          bulkEdit={bulkEdit}
          bulkDelete={bulkDelete}
          onBulkEditApplied={() => {
            invalidateRecords();
            setRowSelection({});
          }}
        />
      )}

      {/* Without this branch a failed load of the records query would render nothing. The pager
          is what makes a rejected cursor reachable, which is why it lives here. A filter's own
          refusal is NOT shown here: it goes to the chip that
          carries the field the server named, and this alert would empty the table under it. */}
      {recordsQuery.isError && filterError === null && (
        <Alert tone="error" title="Could not load records." error={recordsQuery.error} />
      )}

      {inlineEdit.error && (
        <p className="text-sm text-bad" role="alert">
          {inlineEdit.error}
        </p>
      )}

      {/* A deliberate wrapper: wide tables must scroll in their own container, not the page
          body. */}
      <div className={tableWrapClass}>
        <table className={recordsTableClassFor(density)} data-testid="records-table">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th className={cx(thClassFor(density), "relative")} key={header.id} style={{ width: header.getSize() }}>
                    {!header.isPlaceholder &&
                      (DISPLAY_COLUMN_IDS.has(header.column.id) ? (
                        flexRender(header.column.columnDef.header, header.getContext())
                      ) : (
                        <button
                          className="inline-flex w-full min-w-0 cursor-pointer items-center gap-1 overflow-hidden text-left font-semibold uppercase hover:text-ink"
                          type="button"
                          onClick={() => setSortKeys((prev) => cycleSingleSort(prev, header.column.id))}
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {sortIndicatorFor(sortKeys, header.column.id)}
                        </button>
                      ))}
                    {header.column.getCanResize() && (
                      <div
                        className="absolute inset-y-0 right-0 w-1 cursor-col-resize touch-none select-none hover:bg-human"
                        data-testid={`resize-handle-${header.column.id}`}
                        onMouseDown={header.getResizeHandler()}
                        onTouchStart={header.getResizeHandler()}
                      />
                    )}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {recordsQuery.isLoading ? (
              <tr data-testid="table-loading">
                <td className={tdClassFor(density)} colSpan={columns.length}>
                  <div className="flex flex-col gap-2 py-2" role="status" aria-label="Loading records">
                    <div className="h-3 w-1/3 animate-pulse rounded bg-line" />
                    <div className="h-3 w-1/2 animate-pulse rounded bg-line" />
                    <div className="h-3 w-1/4 animate-pulse rounded bg-line" />
                  </div>
                </td>
              </tr>
            ) : (
              renderRows(table.getRowModel().rows)
            )}
          </tbody>
        </table>
      </div>

      {/* docs/DESIGN.md 8.2's footer:
          "Showing 10 of 13 · 3 hidden by your filter" left, "4 rows last touched by an agent"
          right, paging controls right when there is more than one page.

          The left half is the pager's: the pager replaced the `truncated` notice, and grouping is
          client-side over the fetched rows, so it groups within the visible page — that is the
          pager's price, and the count line is what makes it legible.

          The hidden-by-filter clause is inside the `role="status"` paragraph rather than beside
          it, because a page that announces "Showing all 13" while withholding "3 hidden by your
          filter" has told a screen-reader user the less true half of one sentence. The agent
          sentence is NOT a second status region: two of them on this page would make
          `getByRole("status")` ambiguous, and it is not news arriving, it is a property of the
          rows already there.

          The paging controls are rendered only when there is more than one page (8.2), where
          before they rendered disabled on a single-page table. DD-42's "disabled, never hidden"
          is about affordances a caller's ACCESS withholds; a Next button on a table with no next
          page is not one of those, and `pageRangeLabel` already says what there is. */}
      {!recordsQuery.isLoading && records.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1" data-testid="table-footer">
          <p className="text-xs text-ink-2" role="status">
            {pageRangeLabel(
              pageIndex,
              TABLE_VIEW_PAGE_SIZE,
              records.length,
              result?.total_count ?? records.length,
            )}
            {hiddenLabel !== null && ` · ${hiddenLabel}`}
          </p>
          {agentLabel !== null && (
            <p className="ml-auto text-xs text-ink-2" data-testid="table-agent-count">
              {agentLabel}
            </p>
          )}
          {(hasPrevious || hasNext) && (
            <div className={cx("flex items-center gap-2", agentLabel === null && "ml-auto")}>
              <Button
                type="button"
                className={btnSmClass}
                onClick={goToPreviousPage}
                disabled={!hasPrevious || recordsQuery.isFetching}
              >
                Previous
              </Button>
              <Button
                type="button"
                className={btnSmClass}
                onClick={goToNextPage}
                disabled={!hasNext || recordsQuery.isFetching}
              >
                Next
              </Button>
            </div>
          )}
        </div>
      )}

      {!recordsQuery.isLoading &&
        records.length === 0 &&
        (filter !== null ? (
          <EmptyState
            title="No records match this filter"
            data-testid="table-empty-filtered"
            action={
              <Button type="button" className={btnSmClass} onClick={handleClearFilter}>
                Clear filter
              </Button>
            }
          >
            Change the filter, or clear it to see every record.
          </EmptyState>
        ) : (
          /* The copy once read "Records arrive here through agents, the REST API, or a CSV import"
             — an accurate sentence that was also the product telling a person the one thing they
             could not do, back when there was no create form. The three other routes in are still
             true and still worth naming; they are no longer the whole list. Gated on `canWrite`
             like the title-line primary, so a reader is not offered a control the banner above has
             just explained they do not have. */
          <EmptyState
            title="No records yet"
            data-testid="table-empty"
            action={
              canWrite ? (
                <Button
                  type="button"
                  className={btnSmClass}
                  variant="primary"
                  data-testid="new-record-empty"
                  onClick={() => setCreating(true)}
                >
                  New {objectType.name}
                </Button>
              ) : undefined
            }
          >
            Add one here, or let an agent, the REST API or a CSV import fill this in.
          </EmptyState>
        ))}

      {creating && (
        <NewRecordDialog
          objectType={objectType}
          onCreated={(created) => {
            // Straight to the record it just made, which is the only surface that can take the
            // relation and attachment values this form cannot (FR-U2, DD-44) — and which avoids
            // the alternative's worst case, where a create under an active filter or on a later
            // page succeeds and the person is returned to a table that does not show it.
            //
            // The same path the key column builds, rather than a second URL shape. The dialog is
            // still mounted at this moment; `Dialog`'s unmount cleanup closes it as the route
            // changes, which `table-create.spec.ts` observes in a real browser because jsdom's
            // `<dialog>` shim cannot.
            setCreating(false);
            navigate(`/${objectType.key}/${created.key}`);
          }}
          onCancel={() => setCreating(false)}
        />
      )}

      {conflict && (
        <MergeConflictDialog
          conflict={conflict.conflict}
          pendingValues={{ [conflict.field.key]: conflict.attemptedValue }}
          fieldsByKey={fieldsByKey}
          onResubmit={(payload) => void handleResubmit(payload)}
          onCancel={() => setConflict(null)}
        />
      )}
    </section>
  );
}

/** Triggers a browser download of an exported CSV blob via a temporary anchor element (the real
 * app runs in a browser, not a sandboxed test artifact, so `<a download>` works normally here). */
function downloadCsvBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function sortIndicatorFor(sortKeys: SortKey[], field: string): string {
  const index = sortKeys.findIndex((key) => key.field === field);
  if (index === -1) return "";
  const dirSymbol = sortKeys[index].dir === "asc" ? "▲" : "▼";
  return sortKeys.length > 1 ? ` ${dirSymbol}${index + 1}` : ` ${dirSymbol}`;
}
