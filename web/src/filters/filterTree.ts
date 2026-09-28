/**
 * Pure, framework-free operations on a `FilterNode` tree (AGENTS.md: "business logic lives in
 * frontend hooks/utilities ... never inline in a component render body"). No function here
 * touches React or the DOM, and none of them know or care what a field's legal operators are —
 * that list always comes from the caller (ultimately, from a field's `operators` array).
 *
 * A `FilterPath` addresses a node relative to a tree root: each step is the index to descend into
 * when the current node is an `and`/`or` group. When the current node is a `not` wrapper there is
 * only ever one child, so the step's numeric value is ignored and traversal simply descends into
 * `.not`. This lets one path type address nodes through arbitrary and/or/not nesting without a
 * second "step kind" tag.
 */
import {
  isAndNode,
  isConditionNode,
  isNotNode,
  isOrNode,
  type FilterCondition,
  type FilterNode,
  type GroupKind,
} from "./types";

export type FilterPath = readonly number[];

/** Builds a fresh leaf condition. `value` is omitted (not `undefined`-valued) when not given. */
export function createCondition(field: string, op: string, value?: unknown): FilterCondition {
  return value === undefined ? { field, op } : { field, op, value };
}

/** Builds a fresh, empty `and`/`or` group. */
export function createGroup(kind: GroupKind, children: FilterNode[] = []): FilterNode {
  return kind === "and" ? { and: children } : { or: children };
}

/**
 * Reads the node at `path`, or `undefined` if `path` does not resolve within `root`. A `not`
 * wrapper along the way is skipped without consuming a path step (it is a transparent hop, not an
 * addressable level of its own) *unless* it is the destination itself — `path` addresses "the
 * node stored at this slot," and a `not` node is a legitimate answer to that when `path` ends
 * there, since callers like `toggleNot` need to see it to decide whether to unwrap it.
 */
export function getNodeAtPath(root: FilterNode, path: FilterPath): FilterNode | undefined {
  let current: FilterNode | undefined = root;
  let index = 0;
  while (index < path.length) {
    if (current === undefined) return undefined;
    if (isNotNode(current)) {
      current = current.not;
      continue;
    }
    const step = path[index];
    if (isAndNode(current)) {
      current = current.and[step];
    } else if (isOrNode(current)) {
      current = current.or[step];
    } else {
      return undefined;
    }
    index += 1;
  }
  return current;
}

/** Returns a new tree with the node at `path` replaced by `replacement`. `root` is untouched. */
export function setNodeAtPath(
  root: FilterNode,
  path: FilterPath,
  replacement: FilterNode,
): FilterNode {
  if (path.length === 0) return replacement;
  if (isNotNode(root)) {
    // Transparent hop: passing through a `not` on the way to a deeper and/or index does not
    // consume a path step (mirrors `getNodeAtPath`).
    return { not: setNodeAtPath(root.not, path, replacement) };
  }
  const [step, ...rest] = path;
  if (isAndNode(root)) {
    const children = root.and.slice();
    children[step] = setNodeAtPath(children[step], rest, replacement);
    return { and: children };
  }
  if (isOrNode(root)) {
    const children = root.or.slice();
    children[step] = setNodeAtPath(children[step], rest, replacement);
    return { or: children };
  }
  throw new Error("filterTree: cannot descend into a leaf condition");
}

/**
 * Removes the node at `path`. Removing the root itself (`path` is empty) collapses the whole
 * filter to `null` ("no filter", per docs/MCP_TOOLS.md section 4: "an empty or omitted filter
 * matches all live records").
 */
export function removeNodeAtPath(root: FilterNode, path: FilterPath): FilterNode | null {
  if (path.length === 0) return null;
  const parentPath = path.slice(0, -1);
  const lastStep = path[path.length - 1];
  const parent = getNodeAtPath(root, parentPath);
  if (parent === undefined) {
    throw new Error("filterTree: invalid path, parent not found");
  }
  if (isAndNode(parent)) {
    return setNodeAtPath(root, parentPath, {
      and: parent.and.filter((_, index) => index !== lastStep),
    });
  }
  if (isOrNode(parent)) {
    return setNodeAtPath(root, parentPath, {
      or: parent.or.filter((_, index) => index !== lastStep),
    });
  }
  throw new Error("filterTree: cannot remove a child from a non-group node");
}

/**
 * Appends `child` to the group at `groupPath`. `root` of `null` (an empty filter) is only valid
 * with an empty `groupPath`, and simply becomes `child` (the tree's first node).
 */
export function addChild(
  root: FilterNode | null,
  groupPath: FilterPath,
  child: FilterNode,
): FilterNode {
  if (root === null) {
    if (groupPath.length !== 0) {
      throw new Error("filterTree: cannot address a path within an empty (null) tree");
    }
    return child;
  }
  const group = getNodeAtPath(root, groupPath);
  if (group === undefined) {
    throw new Error("filterTree: invalid group path");
  }
  if (isAndNode(group)) {
    return setNodeAtPath(root, groupPath, { and: [...group.and, child] });
  }
  if (isOrNode(group)) {
    return setNodeAtPath(root, groupPath, { or: [...group.or, child] });
  }
  throw new Error("filterTree: target node is not an and/or group");
}

/** Switches an `and`/`or` group at `path` to the other kind, preserving its children in order. */
export function setGroupKind(root: FilterNode, path: FilterPath, kind: GroupKind): FilterNode {
  const node = getNodeAtPath(root, path);
  if (node === undefined) {
    throw new Error("filterTree: invalid path");
  }
  if (isAndNode(node)) {
    return setNodeAtPath(root, path, createGroup(kind, node.and));
  }
  if (isOrNode(node)) {
    return setNodeAtPath(root, path, createGroup(kind, node.or));
  }
  throw new Error("filterTree: target node is not an and/or group");
}

/** Replaces the leaf condition at `path` with `condition`. */
export function replaceCondition(
  root: FilterNode,
  path: FilterPath,
  condition: FilterCondition,
): FilterNode {
  const node = getNodeAtPath(root, path);
  if (node === undefined || !isConditionNode(node)) {
    throw new Error("filterTree: target node is not a leaf condition");
  }
  return setNodeAtPath(root, path, condition);
}

/**
 * Toggles `not` on the node at `path`: wraps it if it is not already a `not` node, unwraps it
 * (replacing it with its inner node) if it is. This is the single operation both "add NOT" and
 * "remove NOT" controls call.
 */
export function toggleNot(root: FilterNode, path: FilterPath): FilterNode {
  const node = getNodeAtPath(root, path);
  if (node === undefined) {
    throw new Error("filterTree: invalid path");
  }
  return setNodeAtPath(root, path, isNotNode(node) ? node.not : { not: node });
}
