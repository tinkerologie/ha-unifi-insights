import { describe, expect, it } from "vitest";
import {
  NODE_KINDS,
  type NodeKind,
  type TopologyEdge,
  type TopologyNode,
} from "../src/contract";
import { buildModel } from "../src/model/graph-model";
import {
  SPACING,
  fitTransform,
  layoutModel,
  layoutTiered,
  visibleIds,
} from "../src/model/layout";
import { fixtureModel, fixtureSnapshot } from "./helpers";

const model = buildModel(fixtureSnapshot(), {
  kinds: new Set(NODE_KINDS),
  clients: "collapsed",
  toggledGroups: new Set(),
});

describe("layoutModel", () => {
  it("places every visual, one depth step per level", () => {
    const layout = layoutModel(model, "comfortable", "vertical", "tree");
    expect([...layout.positions.keys()].sort()).toEqual(
      [...model.visuals.keys()].sort(),
    );
    const depth = SPACING.comfortable.depth;
    expect(layout.positions.get("dev:uuid-gw")!.y).toBe(0);
    expect(layout.positions.get("dev:uuid-core")!.y).toBe(depth);
    expect(layout.positions.get("dev:uuid-ap")!.y).toBe(2 * depth);
    expect(layout.positions.get("group:dev:uuid-ap")!.y).toBe(3 * depth);
    const xs = model.children
      .get("dev:uuid-core")!
      .map((id) => layout.positions.get(id)!.x);
    expect(new Set(xs).size).toBe(xs.length);
  });

  it("swaps axes when horizontal and tightens when compact", () => {
    const horizontal = layoutModel(model, "compact", "horizontal", "tree");
    expect(horizontal.positions.get("dev:uuid-core")).toMatchObject({
      x: SPACING.compact.depth,
    });
    expect(horizontal.bounds.maxX).toBe(3 * SPACING.compact.depth);
  });

  it("handles an empty model", () => {
    const empty = buildModel(fixtureSnapshot({ nodes: [], edges: [] }), {
      kinds: new Set(NODE_KINDS),
      clients: "collapsed",
      toggledGroups: new Set(),
    });
    expect(
      layoutModel(empty, "comfortable", "vertical", "tree").positions.size,
    ).toBe(0);
  });
});

const device = (id: string, kind: NodeKind, name: string): TopologyNode => ({
  id: `dev:${id}`,
  kind,
  name,
  state: "online",
});
const client = (id: string, name: string): TopologyNode => ({
  id: `cli:${id}`,
  kind: "client",
  name,
  state: "online",
  connection: "wired",
});
/** `child` is plugged into `parent`; both are node ids. */
const uplink = (child: string, parent: string): TopologyEdge => ({
  source: child,
  target: parent,
  medium: "wired",
});
const modelOf = (
  nodes: TopologyNode[],
  edges: TopologyEdge[],
  ui: Parameters<typeof fixtureModel>[0] = {},
) => fixtureModel(ui, fixtureSnapshot({ nodes, edges }));

describe("layoutTiered", () => {
  const depth = SPACING.comfortable.depth;

  it("puts the fixture on rows by role, every edge crossing to a lower row", () => {
    const layout = layoutTiered(model, "comfortable", "vertical");
    expect([...layout.positions.keys()].sort()).toEqual(
      [...model.visuals.keys()].sort(),
    );
    const y = (id: string) => layout.positions.get(id)!.y;
    // The gateway's WANs are the top row.
    expect(y("wan:uuid-gw:wan")).toBe(0);
    expect(y("wan:uuid-gw:wan2")).toBe(0);
    expect(y("dev:uuid-gw")).toBe(depth);
    expect(y("dev:uuid-core")).toBe(2 * depth);
    // Switches uplinked to the core switch share its tier: sub-row below it.
    for (const id of [
      "dev:uuid-flex",
      "dev:uuid-lite",
      "dev:uuid-pdu",
      "dev:uuid-u1",
      "dev:uuid-u2",
    ])
      expect(y(id)).toBe(3 * depth);
    expect(y("dev:uuid-ap")).toBe(4 * depth);
    expect(y("group:dev:uuid-ap")).toBe(5 * depth);
    expect(y("group:dev:uuid-u1")).toBe(5 * depth);
    expect(model.links.size).toBeGreaterThan(0);
    for (const link of [...model.links.values(), ...model.uplinkLinks])
      expect(y(link.childId)).toBeGreaterThan(y(link.parentId));
  });

  it("orders the rows gateway, switch, access point, other, clients", () => {
    const layout = layoutTiered(
      modelOf(
        [
          device("pdu", "other", "PDU"),
          device("ap", "access_point", "AP"),
          device("sw", "switch", "Switch"),
          device("gw", "gateway", "Gateway"),
          client("c", "Laptop"),
          client("t", "Tablet"),
        ],
        [
          uplink("dev:sw", "dev:gw"),
          uplink("dev:ap", "dev:gw"),
          uplink("dev:pdu", "dev:gw"),
          uplink("cli:c", "dev:pdu"),
          uplink("cli:t", "dev:sw"),
        ],
      ),
      "comfortable",
      "vertical",
    );
    const y = (id: string) => layout.positions.get(id)!.y;
    expect(
      ["dev:gw", "dev:sw", "dev:ap", "dev:pdu", "group:dev:pdu"].map(y),
    ).toEqual([0, depth, 2 * depth, 3 * depth, 4 * depth]);
    // A client group is on the client row wherever its parent is.
    expect(y("group:dev:sw")).toBe(4 * depth);
  });

  it("puts a child of the same or an earlier tier on a sub-row below its parent", () => {
    // gateway > AP > switch > AP > client group: the switch and the second
    // AP are not below the first AP's tier, so each steps one sub-row down
    // inside the access point tier. The client group starts the next row.
    const layout = layoutTiered(
      modelOf(
        [
          device("gw", "gateway", "Gateway"),
          device("core", "switch", "Core"),
          device("ap1", "access_point", "AP 1"),
          device("sw", "switch", "Mesh switch"),
          device("ap2", "access_point", "AP 2"),
          client("c", "Phone"),
        ],
        [
          uplink("dev:core", "dev:gw"),
          uplink("dev:ap1", "dev:gw"),
          uplink("dev:sw", "dev:ap1"),
          uplink("dev:ap2", "dev:sw"),
          uplink("cli:c", "dev:ap2"),
        ],
      ),
      "comfortable",
      "vertical",
    );
    const y = (id: string) => layout.positions.get(id)!.y;
    expect(
      [
        "dev:gw",
        "dev:core",
        "dev:ap1",
        "dev:sw",
        "dev:ap2",
        "group:dev:ap2",
      ].map(y),
    ).toEqual([0, depth, 2 * depth, 3 * depth, 4 * depth, 5 * depth]);
  });

  it("puts expanded clients on a sub-row below their group", () => {
    const expanded = fixtureModel({ clients: "expanded" });
    const layout = layoutTiered(expanded, "comfortable", "vertical");
    const y = (id: string) => layout.positions.get(id)!.y;
    expect(y("group:dev:uuid-ap")).toBe(5 * depth);
    expect(y("cli:cli-phone")).toBe(6 * depth);
    expect(y("cli:cli-tv")).toBe(6 * depth);
    for (const link of expanded.links.values())
      expect(y(link.childId)).toBeGreaterThan(y(link.parentId));
  });

  it("skips tiers that have no nodes", () => {
    const noGateway = fixtureModel({
      kinds: new Set<NodeKind>(["switch", "access_point", "client"]),
    });
    const layout = layoutTiered(noGateway, "comfortable", "vertical");
    const y = (id: string) => layout.positions.get(id)!.y;
    expect(y("dev:uuid-core")).toBe(0);
    expect(y("dev:uuid-flex")).toBe(depth);
    expect(y("dev:uuid-ap")).toBe(2 * depth);
    expect(y("group:dev:uuid-ap")).toBe(3 * depth);
    expect(layout.bounds.maxY).toBe(3 * depth);
  });

  it("keeps the forest pre-order across sibling branches", () => {
    // Alpha's complete subtree precedes Beta's subtree, regardless of
    // the client names or the iteration order of the visuals map.
    const layout = layoutTiered(
      modelOf(
        [
          device("gw", "gateway", "Gateway"),
          device("alpha", "switch", "Alpha"),
          device("beta", "switch", "Beta"),
          device("ap", "access_point", "AP"),
          client("a", "Aaa"),
          client("b", "Bbb"),
        ],
        [
          uplink("dev:alpha", "dev:gw"),
          uplink("dev:beta", "dev:gw"),
          uplink("dev:ap", "dev:alpha"),
          uplink("cli:a", "dev:beta"),
          uplink("cli:b", "dev:ap"),
        ],
      ),
      "comfortable",
      "vertical",
    );
    const x = (id: string) => layout.positions.get(id)!.x;
    expect(x("dev:alpha")).toBeLessThan(x("dev:beta"));
    expect(x("group:dev:ap")).toBeLessThan(x("group:dev:beta"));
  });

  it("puts a node without a parent after the others in its row", () => {
    const snapshot = fixtureSnapshot();
    snapshot.nodes.push(client("orphan", "Orphan"));
    const layout = layoutTiered(
      fixtureModel({}, snapshot),
      "comfortable",
      "vertical",
    );
    const at = (id: string) => layout.positions.get(id)!;
    expect(at("group:unconnected").y).toBe(at("group:dev:uuid-ap").y);
    expect(at("group:unconnected").x).toBeGreaterThan(
      at("group:dev:uuid-u1").x,
    );
    expect(at("group:dev:uuid-ap").x).toBeGreaterThan(
      at("group:dev:uuid-u1").x,
    );
  });

  it("spreads siblings one breadth apart", () => {
    const layout = layoutTiered(model, "comfortable", "vertical");
    const row = [
      "dev:uuid-flex",
      "dev:uuid-lite",
      "dev:uuid-pdu",
      "dev:uuid-u1",
      "dev:uuid-u2",
    ].map((id) => layout.positions.get(id)!.x);
    const breadth = SPACING.comfortable.breadth;
    expect(row).toEqual([-2, -1, 0, 1, 2].map((i) => i * breadth));
  });

  it("swaps axes when horizontal", () => {
    const vertical = layoutTiered(model, "comfortable", "vertical");
    const horizontal = layoutTiered(model, "comfortable", "horizontal");
    for (const [id, p] of vertical.positions)
      expect(horizontal.positions.get(id)).toEqual({ x: p.y, y: p.x });
    expect(horizontal.bounds).toEqual({
      minX: vertical.bounds.minY,
      minY: vertical.bounds.minX,
      maxX: vertical.bounds.maxY,
      maxY: vertical.bounds.maxX,
    });
  });

  it("tightens rows and siblings when compact", () => {
    const compact = layoutTiered(model, "compact", "vertical");
    const at = (id: string) => compact.positions.get(id)!;
    expect(at("dev:uuid-core").y).toBe(2 * SPACING.compact.depth);
    expect(at("group:dev:uuid-ap").y).toBe(5 * SPACING.compact.depth);
    expect(at("dev:uuid-lite").x - at("dev:uuid-flex").x).toBe(
      SPACING.compact.breadth,
    );
    const xs = [...compact.positions.values()].map((p) => p.x);
    const ys = [...compact.positions.values()].map((p) => p.y);
    expect(compact.bounds).toEqual({
      minX: Math.min(...xs),
      minY: Math.min(...ys),
      maxX: Math.max(...xs),
      maxY: Math.max(...ys),
    });
  });

  it("handles an empty model", () => {
    const empty = buildModel(fixtureSnapshot({ nodes: [], edges: [] }), {
      kinds: new Set(NODE_KINDS),
      clients: "collapsed",
      toggledGroups: new Set(),
    });
    const layout = layoutTiered(empty, "comfortable", "vertical");
    expect(layout.positions.size).toBe(0);
    expect(layout.bounds).toEqual({ minX: 0, minY: 0, maxX: 0, maxY: 0 });
  });
});

describe("viewport maths", () => {
  it("fits and centres the bounds, clamped to 1.5×", () => {
    expect(
      fitTransform({ minX: 0, minY: 0, maxX: 100, maxY: 100 }, 400, 300),
    ).toEqual({ k: 1.5, x: 125, y: 75 });
    const wide = fitTransform(
      { minX: 0, minY: 0, maxX: 10000, maxY: 100 },
      400,
      300,
    );
    expect(wide.k).toBe(0.2);
  });

  it("culls positions outside the viewport plus margin", () => {
    const layout = {
      positions: new Map([
        ["in", { x: 10, y: 10 }],
        ["out", { x: 5000, y: 10 }],
      ]),
      bounds: { minX: 10, minY: 10, maxX: 5000, maxY: 10 },
    };
    expect([...visibleIds(layout, { x: 0, y: 0, k: 1 }, 800, 600)]).toEqual([
      "in",
    ]);
  });
});

describe("plan v3 tiered layout", () => {
  it("selects the tiered algorithm through layoutModel", () => {
    expect(layoutModel(model, "comfortable", "vertical", "tiered")).toEqual(
      layoutTiered(model, "comfortable", "vertical"),
    );
  });

  it("orders rows by forest pre-order across branches of different depths", () => {
    const forest = modelOf(
      [
        device("gw", "gateway", "Gateway"),
        device("alpha", "switch", "Alpha"),
        device("beta", "switch", "Beta"),
        device("ap1", "access_point", "AP 1"),
        device("ap2", "access_point", "AP 2"),
        device("other", "other", "Other"),
        client("a", "Client A"),
        client("b", "Client B"),
      ],
      [
        uplink("dev:alpha", "dev:gw"),
        uplink("dev:beta", "dev:gw"),
        uplink("dev:ap1", "dev:alpha"),
        uplink("dev:ap2", "dev:ap1"),
        uplink("dev:other", "dev:beta"),
        uplink("cli:a", "dev:ap2"),
        uplink("cli:b", "dev:other"),
      ],
    );
    const layout = layoutTiered(forest, "comfortable", "vertical");
    const left = layout.positions.get("group:dev:ap2")!;
    const right = layout.positions.get("group:dev:other")!;
    expect(left.y).toBe(right.y);
    expect(left.x).toBeLessThan(right.x);
    expect(right.x - left.x).toBe(SPACING.comfortable.breadth);
  });

  it("maps unknown roles to other infrastructure", () => {
    const unknown = modelOf(
      [
        device("gw", "gateway", "Gateway"),
        device("ap", "access_point", "AP"),
        device("unknown", "other", "Future device"),
        client("c", "Client"),
      ],
      [
        uplink("dev:ap", "dev:gw"),
        uplink("dev:unknown", "dev:gw"),
        uplink("cli:c", "dev:unknown"),
      ],
    );
    const visual = unknown.visuals.get("dev:unknown")!;
    if (visual.type === "group") throw new Error("expected infrastructure");
    visual.node.kind = "future_kind" as NodeKind;
    const layout = layoutTiered(unknown, "comfortable", "vertical");
    expect(layout.positions.get("dev:unknown")!.y).toBe(
      2 * SPACING.comfortable.depth,
    );
    expect(layout.positions.get("group:dev:unknown")!.y).toBe(
      3 * SPACING.comfortable.depth,
    );
  });

  it("positions every root at sub-row zero and centres all rows on the widest", () => {
    const forest = modelOf(
      [
        device("a", "switch", "Alpha"),
        device("b", "switch", "Beta"),
        device("ap", "access_point", "AP"),
      ],
      [uplink("dev:ap", "dev:a")],
    );
    const layout = layoutTiered(forest, "comfortable", "vertical");
    expect(layout.positions.size).toBe(forest.visuals.size);
    expect(layout.positions.get("dev:a")).toEqual({
      x: -SPACING.comfortable.breadth / 2,
      y: 0,
    });
    expect(layout.positions.get("dev:b")).toEqual({
      x: SPACING.comfortable.breadth / 2,
      y: 0,
    });
    expect(layout.positions.get("dev:ap")).toEqual({
      x: 0,
      y: SPACING.comfortable.depth,
    });
    expect(layout.bounds).toEqual({
      minX: -66,
      maxX: 66,
      minY: 0,
      maxY: 150,
    });
  });

  it.each(["tree", "tiered"] as const)(
    "returns only positions and bounds for %s",
    (mode) => {
      expect(
        Object.keys(layoutModel(model, "comfortable", "vertical", mode)).sort(),
      ).toEqual(["bounds", "positions"]);
    },
  );
});

describe("WAN uplinks", () => {
  /** An online WAN node. */
  const wan = (id: string, name: string): TopologyNode => ({
    id: `wan:${id}`,
    kind: "wan",
    name,
    state: "online",
  });
  const wanModel = modelOf(
    [
      wan("gw:wan", "Fiber"),
      wan("gw:wan2", "5G"),
      wan("gw2:wan", "Cable"),
      device("gw", "gateway", "Gateway"),
      device("gw2", "gateway", "Gateway 2"),
      device("sw", "switch", "Switch"),
    ],
    [
      uplink("dev:gw", "wan:gw:wan"),
      uplink("dev:gw", "wan:gw:wan2"),
      uplink("dev:gw2", "wan:gw2:wan"),
      uplink("dev:sw", "dev:gw"),
    ],
  );
  const { breadth, depth } = SPACING.comfortable;

  it("puts each gateway's WANs one level above it in the tree, centred on it", () => {
    const layout = layoutModel(wanModel, "comfortable", "vertical", "tree");
    const at = (id: string) => layout.positions.get(id)!;
    expect(at("dev:gw").y).toBe(0);
    expect(at("wan:gw:wan").y).toBe(-depth);
    expect(at("wan:gw:wan2").y).toBe(-depth);
    expect(at("wan:gw:wan2").x - at("wan:gw:wan").x).toBe(breadth);
    expect((at("wan:gw:wan").x + at("wan:gw:wan2").x) / 2).toBe(at("dev:gw").x);
    // Neighbouring gateways never stack their WANs on one spot.
    expect(at("wan:gw2:wan").x - at("wan:gw:wan2").x).toBeGreaterThanOrEqual(
      breadth,
    );
    expect(layout.bounds.minY).toBe(-depth);
  });

  it("puts WANs left of the gateway when horizontal", () => {
    const vertical = layoutModel(wanModel, "comfortable", "vertical", "tree");
    const horizontal = layoutModel(
      wanModel,
      "comfortable",
      "horizontal",
      "tree",
    );
    for (const [id, p] of vertical.positions)
      expect(horizontal.positions.get(id)).toEqual({ x: p.y, y: p.x });
    expect(horizontal.positions.get("wan:gw:wan")!.x).toBe(-depth);
  });

  it("puts WANs on the top tier in gateway order, in both orientations", () => {
    const tiered = layoutTiered(wanModel, "comfortable", "vertical");
    const at = (id: string) => tiered.positions.get(id)!;
    expect(
      ["wan:gw:wan", "wan:gw:wan2", "wan:gw2:wan"].map((id) => at(id).y),
    ).toEqual([0, 0, 0]);
    expect(at("wan:gw:wan").x).toBeLessThan(at("wan:gw:wan2").x);
    expect(at("wan:gw:wan2").x).toBeLessThan(at("wan:gw2:wan").x);
    expect(at("dev:gw").y).toBe(depth);
    expect(at("dev:sw").y).toBe(2 * depth);
    const horizontal = layoutTiered(wanModel, "comfortable", "horizontal");
    for (const [id, p] of tiered.positions)
      expect(horizontal.positions.get(id)).toEqual({ x: p.y, y: p.x });
  });

  it("collapses the WAN tier when no WAN is shown", () => {
    const hidden = modelOf(wanModel.snapshot.nodes, wanModel.snapshot.edges, {
      kinds: new Set(NODE_KINDS.filter((k) => k !== "wan")),
    });
    const tiered = layoutTiered(hidden, "comfortable", "vertical");
    expect(tiered.positions.get("dev:gw")!.y).toBe(0);
    expect(tiered.positions.has("wan:gw:wan")).toBe(false);
  });
});
