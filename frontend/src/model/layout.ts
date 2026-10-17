import { hierarchy, tree } from "d3-hierarchy";
import type { Density, LayoutMode, Orientation } from "../config";
import type { NodeKind } from "../contract";
import type { GraphModel, Visual } from "./graph-model";

export interface Point {
  x: number;
  y: number;
}

export interface Bounds {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface Layout {
  positions: Map<string, Point>;
  bounds: Bounds;
}

export interface ViewTransform {
  x: number;
  y: number;
  k: number;
}

/** Distance between siblings (`breadth`) and between levels (`depth`), in SVG units. */
export const SPACING: Record<Density, { breadth: number; depth: number }> = {
  comfortable: { breadth: 132, depth: 150 },
  compact: { breadth: 92, depth: 112 },
};

/** Above this many visible nodes only those near the viewport are put in the DOM. */
export const CULL_THRESHOLD = 300;

const SUPER_ROOT = "\u0000root";

const emptyLayout = (): Layout => ({
  positions: new Map(),
  bounds: { minX: 0, minY: 0, maxX: 0, maxY: 0 },
});

/** The layout axes are `along` a level and `down` through the levels; horizontal swaps them. */
const toPoint = (
  along: number,
  down: number,
  orientation: Orientation,
): Point =>
  orientation === "vertical" ? { x: along, y: down } : { x: down, y: along };

/** Layout from positions, with the bounds that enclose them. */
function layoutOf(positions: Map<string, Point>): Layout {
  const bounds: Bounds = {
    minX: Infinity,
    minY: Infinity,
    maxX: -Infinity,
    maxY: -Infinity,
  };
  for (const p of positions.values()) {
    bounds.minX = Math.min(bounds.minX, p.x);
    bounds.minY = Math.min(bounds.minY, p.y);
    bounds.maxX = Math.max(bounds.maxX, p.x);
    bounds.maxY = Math.max(bounds.maxY, p.y);
  }
  return { positions, bounds };
}

/** Both modes use the same positions-and-bounds contract. */
export function layoutModel(
  model: GraphModel,
  density: Density,
  orientation: Orientation,
  mode: LayoutMode = "tree",
): Layout {
  return mode === "tiered"
    ? layoutTiered(model, density, orientation)
    : layoutTree(model, density, orientation);
}

/** Tidy-tree layout of the model; a synthetic super-root turns the forest into one tree. */
function layoutTree(
  model: GraphModel,
  density: Density,
  orientation: Orientation,
): Layout {
  if (model.roots.length === 0) return emptyLayout();
  const { breadth, depth } = SPACING[density];
  const root = hierarchy<string>(SUPER_ROOT, (id) =>
    id === SUPER_ROOT ? model.roots : model.children.get(id),
  );
  const laidOut = tree<string>()
    .nodeSize([breadth, depth])
    .separation((a, b) => (a.parent === b.parent ? 1 : 1.25))(root);
  const positions = new Map<string, Point>();
  // WAN uplinks sit one level above their owner; rows are keyed by that level.
  const uplinkRows = new Map<number, { id: string; along: number }[]>();
  for (const n of laidOut.descendants()) {
    if (n.data === SUPER_ROOT) continue;
    const down = (n.depth - 1) * depth;
    positions.set(n.data, toPoint(n.x, down, orientation));
    const wans = model.uplinks.get(n.data) ?? [];
    if (wans.length === 0) continue;
    const row = uplinkRows.get(down - depth) ?? [];
    uplinkRows.set(down - depth, row);
    wans.forEach((id, i) =>
      row.push({ id, along: n.x + (i - (wans.length - 1) / 2) * breadth }),
    );
  }
  // Two gateways side by side must not stack their WANs on one spot.
  for (const [down, row] of uplinkRows) {
    row.sort((a, b) => a.along - b.along);
    row.forEach((wan, i) => {
      if (i > 0) wan.along = Math.max(wan.along, row[i - 1]!.along + breadth);
      positions.set(wan.id, toPoint(wan.along, down, orientation));
    });
  }
  return layoutOf(positions);
}

/** Tier of each node kind, top row first; client groups share the clients' tier. */
const TIER: Record<NodeKind, number> = {
  wan: 0,
  gateway: 1,
  switch: 2,
  access_point: 3,
  other: 4,
  client: 5,
};
const TIER_COUNT = TIER.client + 1;
const tierOf = (visual: Visual): number =>
  visual.type === "group"
    ? TIER.client
    : (TIER[visual.node.kind] ?? TIER.other);

/**
 * Role tiers with parent-relative sub-rows, stacked without empty tiers.
 * Forest pre-order keeps siblings adjacent within each occupied row.
 */
export function layoutTiered(
  model: GraphModel,
  density: Density,
  orientation: Orientation,
): Layout {
  if (model.roots.length === 0) return emptyLayout();
  const { breadth, depth } = SPACING[density];

  // Pre-order: each parent gets its slot before its children.
  const slots = new Map<string, { tier: number; sub: number }>();
  const pending = [...model.roots].reverse();
  const preorder: string[] = [];
  const subRows = new Array<number>(TIER_COUNT).fill(0);
  while (pending.length > 0) {
    const id = pending.pop()!;
    preorder.push(id);
    const tier = tierOf(model.visuals.get(id)!);
    const parent = model.parentOf.get(id);
    const above = parent === undefined ? undefined : slots.get(parent)!;
    const effectiveTier = Math.max(tier, above?.tier ?? tier);
    const slot = {
      tier: effectiveTier,
      sub: above?.tier === effectiveTier ? above.sub + 1 : 0,
    };
    slots.set(id, slot);
    subRows[slot.tier] = Math.max(subRows[slot.tier]!, slot.sub + 1);
    // WANs fill the top tier in the pre-order of the devices they belong to.
    for (const wan of model.uplinks.get(id) ?? []) {
      preorder.push(wan);
      slots.set(wan, { tier: TIER.wan, sub: 0 });
      subRows[TIER.wan] = 1;
    }
    pending.push(...[...(model.children.get(id) ?? [])].reverse());
  }

  const firstRow: number[] = [];
  let rowCount = 0;
  for (const count of subRows) {
    firstRow.push(rowCount);
    rowCount += count;
  }
  const rows: string[][] = Array.from({ length: rowCount }, () => []);
  for (const id of preorder) {
    const { tier, sub } = slots.get(id)!;
    rows[firstRow[tier]! + sub]!.push(id);
  }

  const positions = new Map<string, Point>();
  rows.forEach((ids, row) => {
    ids.forEach((id, i) => {
      // All rows share centre zero, including the widest row.
      const offset = (i - (ids.length - 1) / 2) * breadth;
      positions.set(id, toPoint(offset, row * depth, orientation));
    });
  });
  return layoutOf(positions);
}

/** Transform that centres `bounds` in the viewport, scaled into [0.2, 1.5]. */
export function fitTransform(
  bounds: Bounds,
  width: number,
  height: number,
  padding = 48,
): ViewTransform {
  const w = Math.max(bounds.maxX - bounds.minX, 1);
  const h = Math.max(bounds.maxY - bounds.minY, 1);
  const k = Math.min(
    1.5,
    Math.max(
      0.2,
      Math.min((width - 2 * padding) / w, (height - 2 * padding) / h),
    ),
  );
  const cx = (bounds.minX + bounds.maxX) / 2;
  const cy = (bounds.minY + bounds.maxY) / 2;
  return { k, x: width / 2 - cx * k, y: height / 2 - cy * k };
}

/** Ids whose screen position falls inside the viewport grown by `margin` px. */
export function visibleIds(
  layout: Layout,
  t: ViewTransform,
  width: number,
  height: number,
  margin = 120,
): Set<string> {
  const out = new Set<string>();
  for (const [id, p] of layout.positions) {
    const sx = p.x * t.k + t.x;
    const sy = p.y * t.k + t.y;
    if (
      sx >= -margin &&
      sx <= width + margin &&
      sy >= -margin &&
      sy <= height + margin
    )
      out.add(id);
  }
  return out;
}
