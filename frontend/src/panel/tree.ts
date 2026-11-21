import type { DeviceGroupRow } from "./contract";

export interface DeviceGroupTreeNode {
  row: DeviceGroupRow;
  children: DeviceGroupTreeNode[];
}

export interface FlattenedDeviceRow {
  key: string;
  row: DeviceGroupRow;
  level: number;
  posinset: number;
  setsize: number;
  hasChildren: boolean;
  expanded: boolean;
  parentKey: string | undefined;
}

/**
 * Build a section's tree from flat rows while preserving backend sibling order.
 *
 * Rows with missing/dangling or cyclic `parent_key` references are promoted to
 * the top level defensively so no device is ever dropped or looped.
 */
export function buildSectionTree(
  rows: readonly DeviceGroupRow[],
): DeviceGroupTreeNode[] {
  const byKey = new Map<string, DeviceGroupRow>();
  const ordered: DeviceGroupRow[] = [];
  for (const row of rows) {
    if (byKey.has(row.key)) continue;
    byKey.set(row.key, row);
    ordered.push(row);
  }

  const cyclicKeys = new Set<string>();
  const safeKeys = new Set<string>();
  for (const row of ordered) {
    if (cyclicKeys.has(row.key) || safeKeys.has(row.key)) continue;
    const path: string[] = [];
    const pathIndex = new Map<string, number>();
    let cur: string | null = row.key;
    while (
      cur !== null &&
      byKey.has(cur) &&
      !cyclicKeys.has(cur) &&
      !safeKeys.has(cur)
    ) {
      const seenAt = pathIndex.get(cur);
      if (seenAt !== undefined) {
        for (let i = seenAt; i < path.length; i++) cyclicKeys.add(path[i]!);
        for (let i = 0; i < seenAt; i++) safeKeys.add(path[i]!);
        path.length = 0;
        break;
      }
      pathIndex.set(cur, path.length);
      path.push(cur);
      cur = byKey.get(cur)!.parent_key;
    }
    for (const key of path) safeKeys.add(key);
  }

  const nodes = new Map<string, DeviceGroupTreeNode>();
  for (const row of ordered) nodes.set(row.key, { row, children: [] });

  const roots: DeviceGroupTreeNode[] = [];
  for (const row of ordered) {
    const node = nodes.get(row.key)!;
    const parentKey = row.parent_key;
    if (
      parentKey !== null &&
      !cyclicKeys.has(row.key) &&
      nodes.has(parentKey)
    ) {
      nodes.get(parentKey)!.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

/** Build the scoped collapse key for a row inside a section. */
export function collapseKeyFor(sectionKey: string, rowKey: string): string {
  return `${sectionKey}\u0000${rowKey}`;
}

/** Flatten a section tree depth-first into visible ARIA tree rows. */
export function flattenSectionTree(
  roots: readonly DeviceGroupTreeNode[],
  collapsed: ReadonlySet<string>,
  sectionKey: string,
): FlattenedDeviceRow[] {
  const flat: FlattenedDeviceRow[] = [];
  const walk = (
    nodes: readonly DeviceGroupTreeNode[],
    level: number,
    parentKey: string | undefined,
  ): void => {
    nodes.forEach((node, index) => {
      const hasChildren = node.children.length > 0;
      const expanded =
        hasChildren && !collapsed.has(collapseKeyFor(sectionKey, node.row.key));
      flat.push({
        key: node.row.key,
        row: node.row,
        level,
        posinset: index + 1,
        setsize: nodes.length,
        hasChildren,
        expanded,
        parentKey,
      });
      if (expanded) walk(node.children, level + 1, node.row.key);
    });
  };
  walk(roots, 1, undefined);
  return flat;
}
