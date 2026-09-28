/**
 * Renders an `and`/`or` group: its own boolean toggle, its children (recursively, via
 * `FilterNodeView`), and controls to add a condition or a nested group.
 */
import type { FilterActions } from "./FilterActions";
import type { FilterPath } from "./filterTree";
import type { FilterableField } from "./filterableFields";
import type { FilterNode, GroupKind } from "./types";
import { FilterNodeView } from "./FilterNodeView";
import { Button } from "../ui/Button";
import { btnSmClass, compactSelectClass } from "../ui/classes";

export interface GroupViewProps {
  kind: GroupKind;
  children: FilterNode[];
  path: FilterPath;
  fields: FilterableField[];
  actions: FilterActions;
  testId: string;
}

export function GroupView({ kind, children, path, fields, actions, testId }: GroupViewProps) {
  return (
    <fieldset
      className="flex flex-col items-start gap-2 rounded-ctl border border-line p-2 pl-3"
      data-testid={testId}
    >
      <legend className="flex items-center gap-1.5 px-1" data-testid={`${testId}-legend`}>
        <select
          className={compactSelectClass}
          aria-label="Group type"
          value={kind}
          onChange={(event) => actions.setGroupKind(path, event.target.value as GroupKind)}
        >
          <option value="and">AND</option>
          <option value="or">OR</option>
        </select>
        <Button type="button" variant="quiet" className={btnSmClass} onClick={() => actions.toggleNot(path)}>
          NOT
        </Button>
        <Button
          type="button"
          variant="quiet"
          className={btnSmClass}
          aria-label="Remove group"
          onClick={() => actions.removeNode(path)}
        >
          Remove
        </Button>
      </legend>
      {children.map((child, index) => (
        <FilterNodeView
          key={index}
          node={child}
          path={[...path, index]}
          fields={fields}
          actions={actions}
          testId={`${testId}-${index}`}
        />
      ))}
      {/* A dedicated testid so tests can target *this* group's own add-controls, never a nested
          descendant group's identical-looking buttons. */}
      <div className="flex gap-1.5" data-testid={`${testId}-actions`}>
        <Button type="button" className={btnSmClass} onClick={() => actions.addCondition(path)}>
          + Condition
        </Button>
        <Button type="button" className={btnSmClass} onClick={() => actions.addGroup(path, "and")}>
          + Group
        </Button>
      </div>
    </fieldset>
  );
}
