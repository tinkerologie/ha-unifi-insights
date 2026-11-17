import type { ViewMode } from "../config";
import type {
  LinkMedium,
  NodeKind,
  NodeState,
  PortEntry,
  TopologyEdge,
  TopologyNode,
} from "../contract";
import type { LocalizeFunc, LocalizeKey } from "../localize";
import {
  ROOT_CLIENT_GROUP,
  UNCONNECTED_GROUP,
  type GraphModel,
  type Visual,
} from "../model/graph-model";

export const KIND_KEYS: Record<NodeKind, LocalizeKey> = {
  wan: "kind.wan",
  gateway: "kind.gateway",
  switch: "kind.switch",
  access_point: "kind.access_point",
  client: "kind.client",
  other: "kind.other",
};
export const STATE_KEYS: Record<NodeState, LocalizeKey> = {
  online: "state.online",
  offline: "state.offline",
  unknown: "state.unknown",
};
const WAN_STATE_KEYS: Record<NodeState, LocalizeKey> = {
  online: "state.connected",
  offline: "state.disconnected",
  unknown: "state.unknown",
};
export const VIEW_KEYS: Record<ViewMode, LocalizeKey> = {
  graph: "view.graph",
  list: "view.list",
};
export const MEDIUM_KEYS: Record<LinkMedium, LocalizeKey> = {
  wired: "medium.wired",
  wireless: "medium.wireless",
  unknown: "medium.unknown",
};

/** Shorten to `max` characters (not UTF-16 units) with an ellipsis. */
export function truncate(text: string, max: number): string {
  const chars = [...text];
  return chars.length > max ? `${chars.slice(0, max - 1).join("")}…` : text;
}

const trim = (n: number): string =>
  Number.isInteger(n) ? String(n) : n.toFixed(1);

export function formatSpeed(mbps: number): string {
  return mbps >= 1000 ? `${trim(mbps / 1000)}G` : `${mbps}M`;
}

export function formatSpeedLong(mbps: number): string {
  return mbps >= 1000 ? `${trim(mbps / 1000)} gigabit` : `${mbps} megabit`;
}

export const PORT_CONNECTORS = new Map([
  ["RJ45", "RJ45"],
  ["SFP", "SFP"],
  ["SFPPLUS", "SFP+"],
  ["SFP28", "SFP28"],
  ["QSFP28", "QSFP28"],
]);

function portState(state: string, localize: LocalizeFunc): string {
  switch (state) {
    case "UP":
      return localize("ports.up");
    case "DOWN":
      return localize("ports.down");
    case "LIMITED":
      return localize("ports.limited");
    case "UNKNOWN":
      return localize("state.unknown");
    default:
      return state;
  }
}

function portSpeed(speed: number, localize: LocalizeFunc): string {
  return speed >= 1000
    ? localize("ports.speed_gbps", { speed: trim(speed / 1000) })
    : localize("ports.speed_mbps", { speed });
}

/** Describe only reported port values and tolerate older or malformed snapshots. */
export function describePort(
  port: PortEntry,
  localize: LocalizeFunc,
): string[] {
  const values: string[] = [];
  if (typeof port.state === "string")
    values.push(portState(port.state, localize));
  if (typeof port.speed_mbps === "number" && Number.isFinite(port.speed_mbps))
    values.push(portSpeed(port.speed_mbps, localize));
  if (
    typeof port.max_speed_mbps === "number" &&
    Number.isFinite(port.max_speed_mbps)
  )
    values.push(
      localize("ports.max_speed", {
        speed: portSpeed(port.max_speed_mbps, localize),
      }),
    );
  const poe = port.poe;
  if (typeof poe?.enabled === "boolean")
    values.push(
      localize(poe.enabled ? "ports.poe_enabled" : "ports.poe_disabled"),
    );
  if (typeof poe?.state === "string")
    values.push(
      localize("ports.poe_state", { state: portState(poe.state, localize) }),
    );
  if (typeof poe?.standard === "string")
    values.push(localize("ports.poe_standard", { standard: poe.standard }));
  if (Number.isInteger(poe?.type) && typeof poe?.type === "number")
    values.push(localize("ports.poe_type", { type: poe.type }));
  return values;
}

/** Short on-canvas link label, e.g. "p11 · 10G · PoE". */
export function linkLabel(edge: TopologyEdge | undefined): string {
  if (!edge) return "";
  const parts: string[] = [];
  if (edge.parent_port !== undefined) parts.push(`p${edge.parent_port}`);
  if (edge.speed_mbps) parts.push(formatSpeed(edge.speed_mbps));
  if (edge.poe_power_w !== undefined) parts.push("PoE");
  return parts.join(" · ");
}

export function visualTitle(visual: Visual, localize: LocalizeFunc): string {
  if (visual.type !== "group") return visual.node.name;
  if (visual.id === UNCONNECTED_GROUP) return localize("group.unconnected");
  if (visual.id === ROOT_CLIENT_GROUP) return localize("group.root");
  return visual.counts.total === 1
    ? localize("group.client_one")
    : localize("group.clients", { count: visual.counts.total });
}

/** State wording: a WAN connection is connected or disconnected, not online or offline. */
export function stateKey(
  node: TopologyNode | undefined,
  state: NodeState,
): LocalizeKey {
  return node?.kind === "wan" ? WAN_STATE_KEYS[state] : STATE_KEYS[state];
}

/** State wording of any visual; groups have no node. */
export function visualStateKey(visual: Visual): LocalizeKey {
  return stateKey(
    visual.type === "group" ? undefined : visual.node,
    visualState(visual),
  );
}

/** A group reads as offline only when every member is offline. */
export function visualState(visual: Visual): NodeState {
  if (visual.type !== "group") return visual.node.state;
  return visual.counts.offline === visual.counts.total ? "offline" : "online";
}

/** Full spoken label: kind, name, state, client count, uplink. */
export function describeVisual(
  model: GraphModel,
  visual: Visual,
  localize: LocalizeFunc,
): string {
  if (visual.type === "group") {
    return `${visualTitle(visual, localize)}: ${localize("group.summary", { wireless: visual.counts.wireless, offline: visual.counts.offline })}`;
  }
  const n = visual.node;
  const parts = [
    localize("node.label", {
      kind: localize(KIND_KEYS[n.kind]),
      name: n.name,
      state: localize(stateKey(n, n.state)).toLocaleLowerCase(),
    }),
  ];
  const owner = model.uplinkOwner.get(n.id);
  // buildModel only records an owner it has drawn, so its node exists.
  if (owner !== undefined)
    parts.push(
      localize("node.wan_of", { device: model.nodes.get(owner)!.name }),
    );
  const clients = model.clientCounts.get(n.id)?.total ?? 0;
  if (clients > 0)
    parts.push(
      clients === 1
        ? localize("node.client_one")
        : localize("node.clients", { count: clients }),
    );
  const edge = model.edges.get(n.id);
  if (edge?.parent_port !== undefined) {
    parts.push(
      edge.speed_mbps
        ? localize("node.uplink_speed", {
            port: edge.parent_port,
            speed: formatSpeedLong(edge.speed_mbps),
          })
        : localize("node.uplink", { port: edge.parent_port }),
    );
  } else if (n.connection) {
    parts.push(localize(MEDIUM_KEYS[n.connection]).toLocaleLowerCase());
  }
  return parts.join(", ");
}
