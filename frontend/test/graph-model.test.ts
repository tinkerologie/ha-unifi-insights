import { describe, expect, it } from "vitest";
import {
  NODE_KINDS,
  type NodeKind,
  type SiteTopology,
  type TopologyNode,
} from "../src/contract";
import {
  ROOT_CLIENT_GROUP,
  UNCONNECTED_GROUP,
  buildModel,
  createModelBuilder,
  groupContaining,
  isGroupExpanded,
  siblingsOf,
  type UiState,
} from "../src/model/graph-model";
import { fixtureSnapshot } from "./helpers";

const ui = (overrides: Partial<UiState> = {}): UiState => ({
  kinds: new Set(NODE_KINDS),
  clients: "collapsed",
  toggledGroups: new Set(),
  ...overrides,
});
const node = (
  id: string,
  kind: NodeKind,
  name: string,
  extra: Partial<TopologyNode> = {},
): TopologyNode => ({ id, kind, name, state: "online", ...extra });
const snap = (nodes: TopologyNode[], edges: [string, string][]): SiteTopology =>
  fixtureSnapshot({
    nodes,
    edges: edges.map(([source, target]) => ({
      source,
      target,
      medium: "wired",
    })),
  });

describe("buildModel on the Python fixture", () => {
  const model = buildModel(fixtureSnapshot(), ui());

  it("builds the gateway → core → switches/AP tree, devices first by kind then name", () => {
    expect(model.roots).toEqual(["dev:uuid-gw"]);
    expect(model.children.get("dev:uuid-gw")).toEqual(["dev:uuid-core"]);
    expect(model.children.get("dev:uuid-core")).toEqual([
      "dev:uuid-flex",
      "dev:uuid-lite",
      "dev:uuid-pdu",
      "dev:uuid-u1",
      "dev:uuid-u2",
      "dev:uuid-ap",
    ]);
  });

  it("carries the uplink edge on each link", () => {
    expect(model.links.get("dev:uuid-core")).toMatchObject({
      parentId: "dev:uuid-gw",
      viaHidden: [],
      edge: { parent_port: 11, speed_mbps: 10000 },
    });
  });

  it("groups clients under their device, collapsed by default", () => {
    expect(model.children.get("dev:uuid-u1")).toEqual(["group:dev:uuid-u1"]);
    expect(model.children.get("dev:uuid-ap")).toEqual(["group:dev:uuid-ap"]);
    const group = model.visuals.get("group:dev:uuid-ap");
    expect(group).toMatchObject({
      type: "group",
      expanded: false,
      counts: { total: 1, wireless: 1 },
    });
    expect(model.visuals.has("cli:cli-phone")).toBe(false);
    expect(model.clientCounts.get("dev:uuid-u1")).toMatchObject({
      total: 1,
      wired: 1,
    });
    expect(model.stats).toMatchObject({ devices: 8, clients: 2 });
  });
});

describe("client modes", () => {
  it("expands every group in expanded mode, and a toggle collapses one again", () => {
    const expanded = buildModel(fixtureSnapshot(), ui({ clients: "expanded" }));
    expect(expanded.children.get("group:dev:uuid-ap")).toEqual([
      "cli:cli-phone",
    ]);
    expect(expanded.links.get("cli:cli-phone")).toMatchObject({
      parentId: "group:dev:uuid-ap",
    });
    const toggled = buildModel(
      fixtureSnapshot(),
      ui({
        clients: "expanded",
        toggledGroups: new Set(["group:dev:uuid-ap"]),
      }),
    );
    expect(toggled.visuals.has("cli:cli-phone")).toBe(false);
  });

  it("expands one toggled group in collapsed mode", () => {
    const m = buildModel(
      fixtureSnapshot(),
      ui({ toggledGroups: new Set(["group:dev:uuid-u1"]) }),
    );
    expect(m.children.get("group:dev:uuid-u1")).toEqual(["cli:cli-tv"]);
    expect(isGroupExpanded(ui(), "group:dev:uuid-u1")).toBe(false);
  });

  it("hides clients entirely but keeps their counts", () => {
    const m = buildModel(fixtureSnapshot(), ui({ clients: "hidden" }));
    expect([...m.visuals.values()].some((v) => v.type !== "device")).toBe(
      false,
    );
    expect(m.clientCounts.get("dev:uuid-ap")?.total).toBe(1);
  });
});

describe("kind filter", () => {
  it("re-attaches children of hidden devices to the nearest visible ancestor", () => {
    const m = buildModel(
      fixtureSnapshot(),
      ui({
        kinds: new Set<NodeKind>(["gateway", "access_point", "client"]),
      }),
    );
    expect(m.roots).toEqual(["dev:uuid-gw"]);
    expect(m.children.get("dev:uuid-gw")).toEqual([
      "dev:uuid-ap",
      "group:dev:uuid-gw",
    ]);
    expect(m.links.get("dev:uuid-ap")).toMatchObject({
      parentId: "dev:uuid-gw",
      viaHidden: ["switch"],
    });
    expect(m.links.get("group:dev:uuid-gw")).toMatchObject({
      viaHidden: ["switch"],
    });
  });

  it("removes clients when the client kind is filtered out", () => {
    const m = buildModel(
      fixtureSnapshot(),
      ui({
        kinds: new Set<NodeKind>(["gateway", "switch", "access_point"]),
      }),
    );
    expect([...m.visuals.keys()].some((id) => id.startsWith("group:"))).toBe(
      false,
    );
  });

  it("puts clients whose whole ancestry is hidden into one root group", () => {
    const m = buildModel(
      fixtureSnapshot(),
      ui({ kinds: new Set<NodeKind>(["client"]) }),
    );
    expect(m.roots).toEqual([ROOT_CLIENT_GROUP]);
    expect(m.visuals.get(ROOT_CLIENT_GROUP)).toMatchObject({
      counts: { total: 2 },
    });
  });
});

describe("malformed data", () => {
  it("ignores edges it cannot place", () => {
    const s = snap(
      [
        node("dev:gw", "gateway", "GW"),
        node("dev:sw", "switch", "SW"),
        node("dev:orphan", "switch", "Orphan"),
        node("cli:c", "client", "Laptop", { connection: "wired" }),
      ],
      [
        ["dev:sw", "dev:gw"],
        ["dev:sw", "dev:orphan"],
        ["dev:ghost", "dev:gw"],
        ["dev:orphan", "cli:c"],
        ["dev:gw", "dev:gw"],
      ],
    );
    const m = buildModel(s, ui());
    expect(m.roots).toEqual(["dev:gw", "dev:orphan", UNCONNECTED_GROUP]);
    expect(m.children.get("dev:gw")).toEqual(["dev:sw"]);
    expect(m.realParent.has("dev:orphan")).toBe(false);
  });

  it("cuts parent cycles", () => {
    const s = snap(
      [node("dev:a", "switch", "A"), node("dev:b", "switch", "B")],
      [
        ["dev:a", "dev:b"],
        ["dev:b", "dev:a"],
      ],
    );
    const m = buildModel(s, ui());
    expect(m.roots).toEqual(["dev:b"]);
    expect(m.children.get("dev:b")).toEqual(["dev:a"]);
  });

  it("puts clients without devices in the unconnected group", () => {
    const s = snap(
      [
        node("cli:x", "client", "X"),
        node("cli:y", "client", "Y", { state: "offline" }),
      ],
      [["cli:x", "dev:gone"]],
    );
    const m = buildModel(s, ui());
    expect(m.roots).toEqual([UNCONNECTED_GROUP]);
    expect(m.visuals.get(UNCONNECTED_GROUP)).toMatchObject({
      counts: { total: 2, offline: 1 },
    });
  });
});

describe("helpers", () => {
  it("memoizes on snapshot and ui identity", () => {
    const build = createModelBuilder();
    const s = fixtureSnapshot();
    const u = ui();
    expect(build(s, u)).toBe(build(s, u));
    expect(build(s, ui())).not.toBe(build(s, u));
  });

  it("finds the group holding a collapsed client", () => {
    const m = buildModel(fixtureSnapshot(), ui());
    expect(groupContaining(m, "cli:cli-tv")?.id).toBe("group:dev:uuid-u1");
    expect(groupContaining(m, "dev:uuid-u1")).toBeUndefined();
  });
});

describe("WAN uplinks", () => {
  /** A gateway with two WANs (one down) and a switch below it. */
  const wanSnap = () =>
    snap(
      [
        node("wan:gw:wan", "wan", "Fiber"),
        node("wan:gw:wan2", "wan", "5G Backup", { state: "offline" }),
        node("dev:gw", "gateway", "Gateway"),
        node("dev:sw", "switch", "Switch"),
      ],
      [
        ["dev:gw", "wan:gw:wan"],
        ["dev:gw", "wan:gw:wan2"],
        ["dev:sw", "dev:gw"],
      ],
    );

  it("keeps the gateway a root and lists its WANs as uplinks", () => {
    const model = buildModel(wanSnap(), ui());
    expect(model.roots).toEqual(["dev:gw"]);
    expect(model.realParent.has("dev:gw")).toBe(false);
    expect(model.uplinks.get("dev:gw")).toEqual(["wan:gw:wan", "wan:gw:wan2"]);
    expect(model.uplinkOwner.get("wan:gw:wan2")).toBe("dev:gw");
    expect(model.visuals.get("wan:gw:wan")).toMatchObject({ type: "device" });
    expect(model.uplinkLinks).toEqual([
      expect.objectContaining({ childId: "dev:gw", parentId: "wan:gw:wan" }),
      expect.objectContaining({ childId: "dev:gw", parentId: "wan:gw:wan2" }),
    ]);
    expect(model.parentOf.has("wan:gw:wan")).toBe(false);
    expect(siblingsOf(model, "wan:gw:wan2")).toEqual([
      "wan:gw:wan",
      "wan:gw:wan2",
    ]);
  });

  it("does not count WANs as devices, or a down WAN as an offline device", () => {
    expect(buildModel(wanSnap(), ui()).stats).toEqual({
      devices: 2,
      clients: 0,
      offlineDevices: 0,
    });
  });

  it("hides WANs with their kind or with their gateway", () => {
    const noWan = buildModel(
      wanSnap(),
      ui({ kinds: new Set(NODE_KINDS.filter((k) => k !== "wan")) }),
    );
    const noGateway = buildModel(
      wanSnap(),
      ui({ kinds: new Set(NODE_KINDS.filter((k) => k !== "gateway")) }),
    );
    for (const model of [noWan, noGateway]) {
      expect(model.visuals.has("wan:gw:wan")).toBe(false);
      expect(model.uplinks.size).toBe(0);
      expect(model.uplinkLinks).toEqual([]);
    }
    expect(noGateway.roots).toEqual(["dev:sw"]);
  });

  it("ignores WAN edges it cannot place", () => {
    const model = buildModel(
      snap(
        [
          node("wan:a", "wan", "A"),
          node("wan:b", "wan", "B"),
          node("dev:gw", "gateway", "Gateway"),
          node("dev:gw2", "gateway", "Gateway 2"),
        ],
        [
          ["dev:gw", "wan:a"],
          ["dev:gw2", "wan:a"],
          ["wan:b", "dev:gw"],
          ["wan:a", "wan:b"],
          ["dev:gone", "wan:b"],
        ],
      ),
      ui(),
    );
    expect(model.uplinks.get("dev:gw")).toEqual(["wan:a"]);
    expect(model.uplinks.has("dev:gw2")).toBe(false);
    expect(model.visuals.has("wan:b")).toBe(false);
    expect(model.roots).toEqual(["dev:gw", "dev:gw2"]);
  });
});
