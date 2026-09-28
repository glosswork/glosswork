/**
 * Recursive dispatcher: renders whatever `node` actually is (a `not` wrapper, an `and`/`or`
 * group, or a leaf condition) at `path`.
 */
import type { FilterActions } from "./FilterActions";
import type { FilterPath } from "./filterTree";
import type { FilterableField } from "./filterableFields";
import { groupChildren, isAndNode, isNotNode, isOrNode, type FilterNode } from "./types";
import { ConditionRow } from "./ConditionRow";
import { GroupView } from "./GroupView";
import { Button } from "../ui/Button";
import { btnSmClass } from "../ui/classes";

export interface FilterNodeViewProps {
  node: FilterNode;
  path: FilterPath;
  fields: FilterableField[];
  actions: FilterActions;
  testId: string;
}

export function FilterNodeView({ node, path, fields, actions, testId }: FilterNodeViewProps) {
  if (isNotNode(node)) {
    return (
      <div
        className="flex flex-col items-start gap-1.5 border-l-2 border-warn pl-2"
        data-testid={`${testId}-not`}
      >
        <span className="text-2xs font-bold tracking-wider text-warn uppercase">NOT</span>
        <Button
          type="button"
          variant="quiet"
          className={btnSmClass}
          aria-label="Remove NOT"
          onClick={() => actions.toggleNot(path)}
        >
          Remove NOT
        </Button>
        <FilterNodeView node={node.not} path={path} fields={fields} actions={actions} testId={testId} />
      </div>
    );
  }

  if (isAndNode(node) || isOrNode(node)) {
    return (
      <GroupView
        kind={isAndNode(node) ? "and" : "or"}
        children={groupChildren(node)}
        path={path}
        fields={fields}
        actions={actions}
        testId={testId}
      />
    );
  }

  return (
    <ConditionRow
      fields={fields}
      condition={node}
      onChange={(condition) => actions.updateCondition(path, condition)}
      onRemove={() => actions.removeNode(path)}
      onToggleNot={() => actions.toggleNot(path)}
      testId={testId}
    />
  );
}
