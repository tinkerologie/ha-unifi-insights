import type { ClientsMode } from "../config";
import type {
  NodeKind,
  SiteTopology,
  TopologyEdge,
  TopologyNode,
} from "../contract";

export const UNCONNECTED_GROUP = "group:unconnected";
export const ROOT_CLIENT_GROUP = "group:root";
export const groupIdFor = (parentId: string): string => `group:${parentId}`;

export interface ClientCounts {
  total: number;
  wired: number;
  wireless: number;
  offline: number;
}

export interface DeviceVisual {
  type: "device";
  id: string;
  node: TopologyNode;
}

export interface ClientVisual {
  type: "client";
  id: string;
  node: TopologyNode;
}

export interface GroupVisual {
  type: "group";
  id: string;
  members: TopologyNode[];
  counts: ClientCounts;
  expanded: boolean;
}

export type Visual = DeviceVisual | ClientVisual | GroupVisual;

/** A drawn link, keyed by its child. `viaHidden` lists filtered-out kinds it skips over. */
export interface VisualLink {
  childId: string;
  parentId: string;
  edge?: TopologyEdge | undefined;
  viaHidden: NodeKind[];
}

export interface UiState {
  kinds: ReadonlySet<NodeKind>;
  clients: ClientsMode;
  /** Groups flipped from the mode's default (expanded in collapsed mode, collapsed in expanded mode). */
  toggledGroups: ReadonlySet<string>;
}

export interface GraphModel {
  snapshot: SiteTopology;
  roots: string[];
  visuals: Map<string, Visual>;
  children: Map<string, string[]>;
  /** Visual child id → visual parent id. */
  parentOf: Map<string, string>;
  links: Map<string, VisualLink>;
  /** Raw node lookup (includes clients hidden inside collapsed groups). */
  nodes: Map<string, TopologyNode>;
  /** Raw child id → raw parent id, after invalid edges and cycles are dropped. */
  realParent: Map<string, string>;
  /** Raw child id → its uplink edge. */
  edges: Map<string, TopologyEdge>;
  /** Raw device id → its direct clients, regardless of filters. */
  clientCounts: Map<string, ClientCounts>;
  /** Visible device id → its visible WAN ids, drawn above it but outside the tree. */
  uplinks: Map<string, string[]>;
  /** Visible WAN id → the device it is an uplink of. */
  uplinkOwner: Map<string, string>;
  /** One link per visible WAN, from its owner (child) to the WAN (parent). */
  uplinkLinks: VisualLink[];
  /** WAN nodes are connections, not devices, so they are not counted here. */
  stats: { devices: number; clients: number; offlineDevices: number };
}

const KIND_ORDER: Record<NodeKind, number> = {
  wan: 0,
  gateway: 1,
  switch: 2,
  access_point: 3,
  other: 4,
  client: 5,
};
const byName = (a: TopologyNode, b: TopologyNode): number =>
  a.name.localeCompare(b.name) || a.id.localeCompare(b.id);
const byKindThenName = (a: TopologyNode, b: TopologyNode): number =>
  KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || byName(a, b);

export function isGroupExpanded(ui: UiState, id: string): boolean {
  const toggled = ui.toggledGroups.has(id);
  return ui.clients === "expanded" ? !toggled : toggled;
}

const emptyCounts = (): ClientCounts => ({
  total: 0,
  wired: 0,
  wireless: 0,
  offline: 0,
});

function count(counts: ClientCounts, client: TopologyNode): void {
  counts.total++;
  if (client.connection === "wired") counts.wired++;
  if (client.connection === "wireless") counts.wireless++;
  if (client.state === "offline") counts.offline++;
}

/** Drop the edge that closes each parent cycle, so every walk up terminates. */
function cutCycles(
  order: readonly TopologyNode[],
  parent: Map<string, string>,
  edges: Map<string, TopologyEdge>,
): void {
  const settled = new Set<string>();
  for (const start of order) {
    const path = new Set<string>();
    let current: string | undefined = start.id;
    while (current !== undefined && !settled.has(current)) {
      path.add(current);
      const next = parent.get(current);
      if (next !== undefined && path.has(next)) {
        parent.delete(current);
        edges.delete(current);
        break;
      }
      current = next;
    }
    for (const id of path) settled.add(id);
  }
}

/** Turn one snapshot plus the card's UI state into the tree every view draws. */
export function buildModel(snapshot: SiteTopology, ui: UiState): GraphModel {
  const nodes = new Map(snapshot.nodes.map((n) => [n.id, n]));
  const realParent = new Map<string, string>();
  const edges = new Map<string, TopologyEdge>();
  // A WAN is its gateway's uplink, kept out of the tree so the gateway stays a root.
  const wanOwner = new Map<string, { owner: string; edge: TopologyEdge }>();
  for (const edge of snapshot.edges) {
    const child = nodes.get(edge.source);
    const parent = nodes.get(edge.target);
    if (parent?.kind === "wan") {
      if (child && child.kind !== "wan" && !wanOwner.has(parent.id))
        wanOwner.set(parent.id, { owner: child.id, edge });
      continue;
    }
    if (child?.kind === "wan") continue;
    // Unknown ids, self-edges, client parents and second edges for one child cannot be placed.
    if (
      !child ||
      !parent ||
      child.id === parent.id ||
      parent.kind === "client" ||
      realParent.has(child.id)
    )
      continue;
    realParent.set(child.id, parent.id);
    edges.set(child.id, edge);
  }
  cutCycles(snapshot.nodes, realParent, edges);

  const devices = snapshot.nodes
    .filter((n) => n.kind !== "client" && n.kind !== "wan")
    .sort(byKindThenName);
  const clients = snapshot.nodes
    .filter((n) => n.kind === "client")
    .sort(byName);
  const clientCounts = new Map<string, ClientCounts>();
  for (const client of clients) {
    const parent = realParent.get(client.id);
    if (parent === undefined) continue;
    let counts = clientCounts.get(parent);
    if (!counts) clientCounts.set(parent, (counts = emptyCounts()));
    count(counts, client);
  }

  const visuals = new Map<string, Visual>();
  const children = new Map<string, string[]>();
  const parentOf = new Map<string, string>();
  const links = new Map<string, VisualLink>();
  const roots: string[] = [];
  const visible = (n: TopologyNode): boolean => ui.kinds.has(n.kind);
  const nearestVisibleAncestor = (
    rawId: string,
  ): { id: string | undefined; skipped: NodeKind[] } => {
    const skipped: NodeKind[] = [];
    let current = realParent.get(rawId);
    while (current !== undefined) {
      const n = nodes.get(current)!;
      if (visible(n)) return { id: current, skipped };
      skipped.push(n.kind);
      current = realParent.get(current);
    }
    return { id: undefined, skipped };
  };
  const attach = (
    childId: string,
    parentId: string | undefined,
    edge: TopologyEdge | undefined,
    skipped: Iterable<NodeKind>,
  ): void => {
    if (parentId === undefined) {
      roots.push(childId);
      return;
    }
    parentOf.set(childId, parentId);
    links.set(childId, {
      childId,
      parentId,
      edge,
      viaHidden: [...new Set(skipped)],
    });
    const list = children.get(parentId);
    if (list) list.push(childId);
    else children.set(parentId, [childId]);
  };

  for (const device of devices) {
    if (!visible(device)) continue;
    visuals.set(device.id, { type: "device", id: device.id, node: device });
    const ancestor = nearestVisibleAncestor(device.id);
    attach(device.id, ancestor.id, edges.get(device.id), ancestor.skipped);
  }

  const uplinks = new Map<string, string[]>();
  const uplinkOwner = new Map<string, string>();
  const uplinkLinks: VisualLink[] = [];
  for (const wan of snapshot.nodes) {
    const link = wanOwner.get(wan.id);
    if (!link || !visible(wan) || !visuals.has(link.owner)) continue;
    visuals.set(wan.id, { type: "device", id: wan.id, node: wan });
    const list = uplinks.get(link.owner);
    if (list) list.push(wan.id);
    else uplinks.set(link.owner, [wan.id]);
    uplinkOwner.set(wan.id, link.owner);
    uplinkLinks.push({
      childId: link.owner,
      parentId: wan.id,
      edge: link.edge,
      viaHidden: [],
    });
  }

  if (ui.clients !== "hidden" && ui.kinds.has("client")) {
    const groups = new Map<
      string,
      {
        parent: string | undefined;
        members: TopologyNode[];
        skipped: Set<NodeKind>;
      }
    >();
    for (const client of clients) {
      const real = realParent.get(client.id);
      let parent: string | undefined;
      let skipped: NodeKind[] = [];
      let id: string;
      if (real === undefined) {
        id = UNCONNECTED_GROUP;
      } else {
        const realNode = nodes.get(real)!;
        if (visible(realNode)) {
          parent = real;
        } else {
          const ancestor = nearestVisibleAncestor(real);
          parent = ancestor.id;
          skipped = [realNode.kind, ...ancestor.skipped];
        }
        id = parent === undefined ? ROOT_CLIENT_GROUP : groupIdFor(parent);
      }
      let group = groups.get(id);
      if (!group)
        groups.set(id, (group = { parent, members: [], skipped: new Set() }));
      group.members.push(client);
      for (const kind of skipped) group.skipped.add(kind);
    }
    const ordered = [...groups].sort(
      ([a], [b]) =>
        Number(a === UNCONNECTED_GROUP) - Number(b === UNCONNECTED_GROUP),
    );
    for (const [id, group] of ordered) {
      const counts = emptyCounts();
      for (const member of group.members) count(counts, member);
      const expanded = isGroupExpanded(ui, id);
      visuals.set(id, {
        type: "group",
        id,
        members: group.members,
        counts,
        expanded,
      });
      attach(id, group.parent, undefined, group.skipped);
      if (!expanded) continue;
      for (const member of group.members) {
        visuals.set(member.id, {
          type: "client",
          id: member.id,
          node: member,
        });
        attach(member.id, id, edges.get(member.id), []);
      }
    }
  }

  return {
    snapshot,
    roots,
    visuals,
    children,
    parentOf,
    links,
    nodes,
    realParent,
    edges,
    clientCounts,
    uplinks,
    uplinkOwner,
    uplinkLinks,
    stats: {
      devices: devices.length,
      clients: clients.length,
      offlineDevices: devices.filter((d) => d.state === "offline").length,
    },
  };
}

/** buildModel, memoized on snapshot and UI-state identity. */
export function createModelBuilder(): (
  snapshot: SiteTopology,
  ui: UiState,
) => GraphModel {
  let lastSnapshot: SiteTopology | undefined;
  let lastUi: UiState | undefined;
  let last: GraphModel | undefined;
  return (snapshot, ui) => {
    if (last && snapshot === lastSnapshot && ui === lastUi) return last;
    lastSnapshot = snapshot;
    lastUi = ui;
    last = buildModel(snapshot, ui);
    return last;
  };
}

/** Nodes beside `id`: its parent's children, the roots, or its owner's WANs. */
export function siblingsOf(model: GraphModel, id: string): string[] {
  const owner = model.uplinkOwner.get(id);
  // buildModel lists every WAN under its owner, so the owner has uplinks.
  if (owner !== undefined) return model.uplinks.get(owner)!;
  const parent = model.parentOf.get(id);
  return parent === undefined
    ? model.roots
    : (model.children.get(parent) ?? []);
}

/** The client group holding a raw client id, whether or not it is expanded. */
export function groupContaining(
  model: GraphModel,
  rawId: string,
): GroupVisual | undefined {
  for (const visual of model.visuals.values()) {
    if (visual.type === "group" && visual.members.some((m) => m.id === rawId))
      return visual;
  }
  return undefined;
}
