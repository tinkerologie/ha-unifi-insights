import { describe, expect, it } from "vitest";
import type { DeviceGroupRow } from "../src/panel/contract";
import {
  buildSectionTree,
  collapseKeyFor,
  flattenSectionTree,
} from "../src/panel/tree";

function row(
  key: string,
  parent_key: string | null = null,
  overrides: Partial<DeviceGroupRow> = {},
): DeviceGroupRow {
  return {
    key,
    name: key,
    kind: "switch",
    ha_device_id: `ha-${key}`,
    parent_key,
    port: null,
    reason: null,
    ...overrides,
  };
}

describe("buildSectionTree", () => {
  it("builds a hierarchy and preserves backend sibling order", () => {
    const rows = [
      row("gw", null, { kind: "gateway" }),
      row("sw-b", "gw"),
      row("sw-a", "gw"),
      row("ap-2", "sw-b", { kind: "access_point" }),
      row("ap-1", "sw-b", { kind: "access_point" }),
      row("cam-1", "sw-a", { kind: "camera" }),
    ];

    const tree = buildSectionTree(rows);
    expect(tree.map((n) => n.row.key)).toEqual(["gw"]);
    expect(tree[0]!.children.map((n) => n.row.key)).toEqual(["sw-b", "sw-a"]);
    expect(tree[0]!.children[0]!.children.map((n) => n.row.key)).toEqual([
      "ap-2",
      "ap-1",
    ]);
    expect(tree[0]!.children[1]!.children.map((n) => n.row.key)).toEqual([
      "cam-1",
    ]);
  });

  it("handles children listed before their parent while preserving order", () => {
    const rows = [
      row("child-1", "parent"),
      row("child-2", "parent"),
      row("parent"),
    ];
    const tree = buildSectionTree(rows);
    expect(tree.map((n) => n.row.key)).toEqual(["parent"]);
    expect(tree[0]!.children.map((n) => n.row.key)).toEqual([
      "child-1",
      "child-2",
    ]);
  });

  it("promotes rows with a dangling parent_key to top level", () => {
    const rows = [row("gw"), row("orphan", "missing-parent"), row("sw", "gw")];
    const tree = buildSectionTree(rows);
    expect(tree.map((n) => n.row.key)).toEqual(["gw", "orphan"]);
    expect(tree[0]!.children.map((n) => n.row.key)).toEqual(["sw"]);
  });

  it("promotes self-cyclic and mutual-cyclic rows to top level without dropping descendants", () => {
    const rows = [
      row("self", "self"),
      row("a", "b"),
      row("b", "a"),
      row("c", "a"),
      row("x", "y"),
      row("y", "z"),
      row("z", "x"),
    ];
    const tree = buildSectionTree(rows);
    expect(tree.map((n) => n.row.key)).toEqual([
      "self",
      "a",
      "b",
      "x",
      "y",
      "z",
    ]);
    expect(tree[1]!.children.map((n) => n.row.key)).toEqual(["c"]);
  });

  it("ignores duplicate row keys defensively", () => {
    const rows = [
      row("gw", null, { name: "First" }),
      row("gw", null, { name: "Duplicate" }),
    ];
    const tree = buildSectionTree(rows);
    expect(tree).toHaveLength(1);
    expect(tree[0]!.row.name).toBe("First");
  });
});

describe("flattenSectionTree", () => {
  it("flattens depth-first with ARIA levels/positions and respects collapsed rows", () => {
    const tree = buildSectionTree([
      row("gw"),
      row("sw1", "gw"),
      row("ap1", "sw1"),
      row("sw2", "gw"),
    ]);

    const all = flattenSectionTree(tree, new Set(), "site:1");
    expect(
      all.map((r) => ({
        key: r.key,
        level: r.level,
        posinset: r.posinset,
        setsize: r.setsize,
        hasChildren: r.hasChildren,
        expanded: r.expanded,
        parentKey: r.parentKey,
      })),
    ).toEqual([
      {
        key: "gw",
        level: 1,
        posinset: 1,
        setsize: 1,
        hasChildren: true,
        expanded: true,
        parentKey: undefined,
      },
      {
        key: "sw1",
        level: 2,
        posinset: 1,
        setsize: 2,
        hasChildren: true,
        expanded: true,
        parentKey: "gw",
      },
      {
        key: "ap1",
        level: 3,
        posinset: 1,
        setsize: 1,
        hasChildren: false,
        expanded: false,
        parentKey: "sw1",
      },
      {
        key: "sw2",
        level: 2,
        posinset: 2,
        setsize: 2,
        hasChildren: false,
        expanded: false,
        parentKey: "gw",
      },
    ]);

    const collapsed = new Set([collapseKeyFor("site:1", "sw1")]);
    const partial = flattenSectionTree(tree, collapsed, "site:1");
    expect(partial.map((r) => `${r.key}:${r.expanded}`)).toEqual([
      "gw:true",
      "sw1:false",
      "sw2:false",
    ]);
  });
});
