import { describe, expect, it } from "vitest";
import { NODE_KINDS } from "../src/contract";
import {
  allSites,
  resolveBinding,
  resolveConfig,
  sameBinding,
  validateConfig,
} from "../src/config";
import { SOURCES_MULTI, SOURCES_ONE } from "./helpers";

const TYPE = "custom:unifi-insights-topology-card";

describe("validateConfig", () => {
  it("accepts a full config and ignores keys HA adds", () => {
    const config = {
      type: TYPE,
      entry_id: "e",
      site_id: "s",
      title: "Net",
      view: "list",
      show_site_selector: true,
      clients: "hidden",
      kinds: ["gateway", "switch"],
      density: "compact",
      orientation: "horizontal",
      layout: "tiered",
      show_labels: false,
      max_clients: 120,
      grid_options: { columns: 12 },
      visibility: [],
    };
    expect(validateConfig(config)).toBe(config);
  });

  it.each([
    [null, /must be an object/],
    [{}, /type is required/],
    [{ type: TYPE, entry_id: "e" }, /set together/],
    [{ type: TYPE, title: "" }, /title must be a non-empty string/],
    [{ type: TYPE, view: "map" }, /view must be one of/],
    [{ type: TYPE, clients: "all" }, /clients must be one of/],
    [{ type: TYPE, density: "tiny" }, /density must be one of/],
    [{ type: TYPE, orientation: "diagonal" }, /orientation must be one of/],
    [
      { type: TYPE, layout: "isometric" },
      /layout must be one of: tree, tiered/,
    ],
    [{ type: TYPE, layout: 3 }, /layout must be one of/],
    [{ type: TYPE, kinds: [] }, /kinds must be a non-empty list/],
    [{ type: TYPE, kinds: ["router"] }, /kinds must be a non-empty list/],
    [{ type: TYPE, show_labels: "yes" }, /show_labels must be true or false/],
    [{ type: TYPE, show_wan: "no" }, /show_wan must be true or false/],
    [
      { type: TYPE, max_clients: 0 },
      /max_clients must be a whole number from 1 to 500/,
    ],
    [{ type: TYPE, max_clients: 501 }, /max_clients/],
    [{ type: TYPE, max_clients: 1.5 }, /max_clients/],
  ])("rejects %j", (raw, message) => {
    expect(() => validateConfig(raw)).toThrow(message);
  });
});

describe("resolveConfig", () => {
  it("fills every default", () => {
    expect(resolveConfig({ type: TYPE })).toEqual({
      entry_id: undefined,
      site_id: undefined,
      title: undefined,
      view: "graph",
      show_site_selector: false,
      clients: "collapsed",
      kinds: [...NODE_KINDS],
      density: undefined,
      orientation: "vertical",
      layout: "tree",
      show_labels: true,
      show_wan: true,
      max_clients: 500,
    });
  });

  it("resolves a configured layout and keeps tree as the default", () => {
    expect(resolveConfig({ type: TYPE, layout: "tiered" }).layout).toBe(
      "tiered",
    );
    expect(resolveConfig({ type: TYPE, layout: "tree" }).layout).toBe("tree");
  });
});

describe("site binding", () => {
  it("uses explicit ids even before sources load", () => {
    const config = resolveConfig({
      type: TYPE,
      entry_id: "e",
      site_id: "s",
    });
    expect(resolveBinding(config, undefined)).toEqual({
      entry_id: "e",
      site_id: "s",
    });
  });

  it("auto-picks the only site", () => {
    expect(resolveBinding(resolveConfig({ type: TYPE }), SOURCES_ONE)).toEqual({
      entry_id: "entry-1",
      site_id: "site-1",
    });
  });

  it("does not guess between several sites", () => {
    expect(
      resolveBinding(resolveConfig({ type: TYPE }), SOURCES_MULTI),
    ).toBeUndefined();
    expect(
      resolveBinding(resolveConfig({ type: TYPE }), undefined),
    ).toBeUndefined();
  });

  it("labels every site with its entry title", () => {
    expect(allSites(SOURCES_MULTI).map((s) => s.label)).toEqual([
      "Crestwood — Home",
      "Crestwood — Cabin",
      "Office — Default",
    ]);
  });

  it("compares bindings by value", () => {
    expect(
      sameBinding(
        { entry_id: "a", site_id: "b" },
        { entry_id: "a", site_id: "b" },
      ),
    ).toBe(true);
    expect(
      sameBinding(
        { entry_id: "a", site_id: "b" },
        { entry_id: "a", site_id: "c" },
      ),
    ).toBe(false);
    expect(sameBinding(undefined, undefined)).toBe(true);
    expect(sameBinding({ entry_id: "a", site_id: "b" }, undefined)).toBe(false);
  });
});

describe("show_wan", () => {
  it("drops the wan kind when off and keeps it by default", () => {
    expect(resolveConfig({ type: TYPE }).kinds).toContain("wan");
    const off = resolveConfig({ type: TYPE, show_wan: false });
    expect(off.show_wan).toBe(false);
    expect(off.kinds).toEqual(NODE_KINDS.filter((k) => k !== "wan"));
    expect(
      resolveConfig({ type: TYPE, show_wan: false, kinds: ["wan", "gateway"] })
        .kinds,
    ).toEqual(["gateway"]);
  });
});
