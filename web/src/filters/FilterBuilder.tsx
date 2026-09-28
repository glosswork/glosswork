/**
 * Top-level filter builder. Takes an object type's `fields` (plus, optionally, `system_fields`
 * — the queryable pseudo-fields of docs/MCP_TOOLS.md section 4) and an initial `FilterNode | null`
 * (`null` means "no filter", matching the query grammar), and reports every **complete** change
 * via `onChange`. It never calls the query API itself; the table view and `/search` wire
 * `onChange` into their own refetch.
 *
 * **The completeness gate: "an incomplete condition never reaches the network".** Without it,
 * every intermediate state of the builder is a query: `defaultCondition()` below composes a
 * condition with no `value`, and both consumers put the resulting tree in a React Query key — so
 * the very first click on `+ Condition` would send `{"field": "name", "op": "eq"}` and get back
 * `422 validation_failed — "Operator 'eq' requires a value."`, emptying the table behind a red
 * alert. **`+ Group (AND)` would do the same thing from the button beside it**,
 * sending `{"and": []}` for
 * `422 validation_failed — "'and' requires a non-empty list of filters."`; it is gated by the same
 * rule, because it is the same defect and produces the same sentence.
 *
 * The gate lives **here** rather than in either consumer. `SearchPage.tsx` renders this
 * same component and refires on the same mechanism against the same validator, so a gate in
 * the consumer would fix one of two screens and leave the next consumer to rediscover the
 * defect. `tree` still advances on every edit — the builder shows what you are typing, and an
 * empty group is held, not abandoned — and only `onChange` waits for the tree to be sendable.
 *
 * It is deliberately **behaviour, not markup**: the gate changes nothing about what this
 * component renders, so `ui-visual.spec.ts`'s `search-scoped.png`, which deliberately contains
 * this builder, is unaffected by it. This builder has no explicit commit boundary (Apply, Enter,
 * close); the chip popovers have one. Here a complete tree commits as soon as it becomes
 * complete, which is the smallest rule that satisfies the gate.
 */
import { useMemo, useState } from "react";
import type { FieldDoc, SystemFieldDoc } from "../api/objectTypes";
import type { FilterActions } from "./FilterActions";
import { isFilterComplete } from "./completeness";
import {
  addChild,
  createCondition,
  createGroup,
  removeNodeAtPath,
  replaceCondition,
  setGroupKind,
  toggleNot,
} from "./filterTree";
import { toFilterableFields } from "./filterableFields";
import type { FilterNode, GroupKind } from "./types";
import { FilterNodeView } from "./FilterNodeView";
import { Button } from "../ui/Button";
import { btnSmClass } from "../ui/classes";

export interface FilterBuilderProps {
  fields: FieldDoc[];
  systemFields?: SystemFieldDoc[];
  initialFilter: FilterNode | null;
  onChange: (tree: FilterNode | null) => void;
}

export function FilterBuilder({
  fields,
  systemFields = [],
  initialFilter,
  onChange,
}: FilterBuilderProps) {
  const [tree, setTree] = useState<FilterNode | null>(initialFilter);
  const filterableFields = useMemo(
    () => toFilterableFields(fields, systemFields),
    [fields, systemFields],
  );

  const commit = (next: FilterNode | null) => {
    setTree(next);
    // The gate. `isFilterComplete` is the one rule (`completeness.ts`), mirrored from
    // `src/glosswork/filters.py` and imported by the tests too, so the test and the product
    // cannot judge "complete" differently. An incomplete tree is held in local state and the
    // consumer keeps the last complete filter it was given.
    if (isFilterComplete(next)) {
      onChange(next);
    }
  };

  const defaultCondition = () => {
    const first = filterableFields[0];
    return createCondition(first?.key ?? "", first?.operators[0] ?? "");
  };

  const actions: FilterActions = useMemo(() => {
    return {
      updateCondition: (path, condition) => {
        if (tree === null) return;
        commit(replaceCondition(tree, path, condition));
      },
      removeNode: (path) => {
        if (tree === null) return;
        commit(removeNodeAtPath(tree, path));
      },
      addCondition: (groupPath) => {
        commit(addChild(tree, groupPath, defaultCondition()));
      },
      addGroup: (groupPath, kind) => {
        commit(addChild(tree, groupPath, createGroup(kind)));
      },
      setGroupKind: (path, kind: GroupKind) => {
        if (tree === null) return;
        commit(setGroupKind(tree, path, kind));
      },
      toggleNot: (path) => {
        if (tree === null) return;
        commit(toggleNot(tree, path));
      },
    };
    // `defaultCondition`/`commit` close over `tree` and `filterableFields` freshly on every
    // render; re-deriving `actions` whenever either changes keeps them in sync without needing
    // functional state updates.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tree, filterableFields]);

  return (
    <div
      className="flex flex-col items-start gap-2 rounded-card border border-line bg-surface p-3"
      data-testid="filter-builder"
    >
      {tree === null ? (
        <EmptyState
          onAddCondition={() => commit(defaultCondition())}
          onAddGroup={(kind) => commit(createGroup(kind))}
        />
      ) : (
        <>
          <FilterNodeView
            node={tree}
            path={[]}
            fields={filterableFields}
            actions={actions}
            testId="filter-node-root"
          />
          <Button type="button" variant="quiet" className={btnSmClass} onClick={() => commit(null)}>
            Clear filter
          </Button>
        </>
      )}
    </div>
  );
}

interface EmptyStateProps {
  onAddCondition: () => void;
  onAddGroup: (kind: GroupKind) => void;
}

function EmptyState({ onAddCondition, onAddGroup }: EmptyStateProps) {
  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="filter-builder-empty">
      <p className="text-sm text-ink-2">No filter (matches all records).</p>
      <Button type="button" className={btnSmClass} onClick={onAddCondition}>
        + Condition
      </Button>
      <Button type="button" className={btnSmClass} onClick={() => onAddGroup("and")}>
        + Group (AND)
      </Button>
      <Button type="button" className={btnSmClass} onClick={() => onAddGroup("or")}>
        + Group (OR)
      </Button>
    </div>
  );
}
