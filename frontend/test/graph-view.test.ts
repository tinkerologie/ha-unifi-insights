import { afterEach, describe, expect, it, vi } from "vitest";
import { makeLocalize } from "../src/localize";
import { describeVisual } from "../src/views/describe";
import {
  SPACING,
  fitTransform,
  layoutModel,
  layoutTiered,
} from "../src/model/layout";
import { zoomFilter, type UitGraphView } from "../src/views/graph-view";
import { cleanup, fixtureModel, fixtureSnapshot, mount } from "./helpers";

const localize = makeLocalize("en");
afterEach(cleanup);

async function graph(
  props: Record<string, unknown> = {},
): Promise<UitGraphView> {
  const el = await mount<UitGraphView>("uit-graph-view", {
    model: fixtureModel(),
    localize,
    siteName: "Home",
    reducedMotion: true,
    ...props,
  });
  el.setViewportSize(800, 600);
  await el.updateComplete;
  return el;
}
const nodes = (el: UitGraphView) => [
  ...el.shadowRoot!.querySelectorAll<SVGGElement>("g.nodes g.node"),
];
const byId = (el: UitGraphView, id: string) =>
  nodes(el).find((n) => n.getAttribute("data-id") === id)!;
const svgOf = (el: UitGraphView) => el.shadowRoot!.querySelector("svg.canvas")!;

describe("<uit-graph-view> rendering", () => {
  it("draws every visual and link, labelled for assistive tech", async () => {
    const el = await graph();
    const model = fixtureModel();
    expect(nodes(el)).toHaveLength(model.visuals.size);
    expect(el.shadowRoot!.querySelectorAll("g.links path.link")).toHaveLength(
      model.links.size + model.uplinkLinks.length,
    );
    const svg = svgOf(el);
    expect(svg.getAttribute("role")).toBe("application");
    expect(svg.getAttribute("aria-roledescription")).toBe("network topology");
    expect(svg.getAttribute("aria-label")).toBe(
      "Home topology, 8 devices, 2 clients",
    );
    expect(byId(el, "dev:uuid-core").getAttribute("aria-label")).toBe(
      describeVisual(model, model.visuals.get("dev:uuid-core")!, localize),
    );
    expect(byId(el, "group:dev:uuid-ap").getAttribute("aria-expanded")).toBe(
      "false",
    );
    expect(
      nodes(el)
        .filter((n) => n.getAttribute("tabindex") === "0")
        .map((n) => n.getAttribute("data-id")),
    ).toEqual(["dev:uuid-gw"]);
  });

  it("labels links with port, speed and PoE, and can hide labels", async () => {
    const el = await graph();
    const labels = [...el.shadowRoot!.querySelectorAll("text.link-label")].map(
      (t) => t.textContent,
    );
    expect(labels).toContain("p20 · 2.5G · PoE");
    const bare = await graph({ showLabels: false });
    expect(
      bare.shadowRoot!.querySelector("text.label, text.link-label"),
    ).toBeNull();
  });

  it("renders hostile names as text", async () => {
    const snapshot = fixtureSnapshot();
    snapshot.nodes.find((n) => n.id === "dev:uuid-gw")!.name =
      "<img src=x onerror=alert(1)> and a very long gateway name";
    const el = await graph({ model: fixtureModel({}, snapshot) });
    const gw = byId(el, "dev:uuid-gw");
    expect(el.shadowRoot!.querySelector("img")).toBeNull();
    expect(gw.querySelector("title")!.textContent).toBe(
      "<img src=x onerror=alert(1)> and a very long gateway name",
    );
    expect(gw.querySelector("text.label")!.textContent).toMatch(/…$/);
  });

  it("highlights the path from the selection to the root, via a collapsed client group", async () => {
    const el = await graph({ selectedId: "cli:cli-tv" });
    const onPath = nodes(el)
      .filter((n) => n.classList.contains("on-path"))
      .map((n) => n.getAttribute("data-id"));
    expect(onPath).toEqual([
      "dev:uuid-gw",
      "dev:uuid-core",
      "dev:uuid-u1",
      "group:dev:uuid-u1",
    ]);
  });

  it("marks offline nodes with text, not colour alone", async () => {
    const snapshot = fixtureSnapshot();
    snapshot.nodes.find((n) => n.id === "dev:uuid-pdu")!.state = "offline";
    const el = await graph({ model: fixtureModel({}, snapshot) });
    expect(
      byId(el, "dev:uuid-pdu").querySelector("text.state-text")!.textContent,
    ).toBe("Offline");
  });
});

describe("<uit-graph-view> interaction", () => {
  it("moves focus along the tree with arrows and activates with Enter or click", async () => {
    const el = await graph();
    const activated = vi.fn();
    el.addEventListener("uit-activate", (e) =>
      activated((e as CustomEvent).detail),
    );
    byId(el, "dev:uuid-gw").focus();
    svgOf(el).dispatchEvent(
      new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
    );
    await el.updateComplete;
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement?.getAttribute("data-id")).toBe(
      "dev:uuid-core",
    );
    svgOf(el).dispatchEvent(
      new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
    byId(el, "dev:uuid-ap").dispatchEvent(
      new MouseEvent("click", { bubbles: true }),
    );
    expect(activated.mock.calls).toEqual([
      [{ id: "dev:uuid-core" }],
      [{ id: "dev:uuid-ap" }],
    ]);
  });

  it("fits on first size and zooms with the keyboard", async () => {
    const el = await graph();
    const viewport = () =>
      el.shadowRoot!.querySelector("g.viewport")!.getAttribute("transform");
    const fitted = viewport();
    expect(fitted).not.toBe("translate(0,0) scale(1)");
    svgOf(el).dispatchEvent(
      new KeyboardEvent("keydown", { key: "+", bubbles: true }),
    );
    expect(viewport()).not.toBe(fitted);
    svgOf(el).dispatchEvent(
      new KeyboardEvent("keydown", { key: "0", bubbles: true }),
    );
    expect(viewport()).toBe(fitted);
  });

  it("shows a hint instead of hijacking plain wheel scrolling in dashboards", async () => {
    const el = await graph();
    svgOf(el).dispatchEvent(
      new WheelEvent("wheel", {
        deltaY: 100,
        bubbles: true,
        cancelable: true,
      }),
    );
    await el.updateComplete;
    expect(el.shadowRoot!.querySelector(".hint")!.hasAttribute("hidden")).toBe(
      false,
    );
  });

  it("decides which gestures zoom", () => {
    expect(zoomFilter(new WheelEvent("wheel"), true)).toBe("hint");
    expect(zoomFilter(new WheelEvent("wheel", { ctrlKey: true }), true)).toBe(
      "zoom",
    );
    expect(zoomFilter(new WheelEvent("wheel"), false)).toBe("zoom");
    expect(zoomFilter(new MouseEvent("mousedown", { button: 2 }), true)).toBe(
      "ignore",
    );
    expect(zoomFilter(new MouseEvent("mousedown"), true)).toBe("zoom");
  });
});

describe("<uit-graph-view> context requests", () => {
  const contexts = (el: UitGraphView) => {
    const seen: CustomEvent[] = [];
    el.addEventListener("uit-context", (e) => seen.push(e as CustomEvent));
    return seen;
  };
  const contextmenu = (target: Element) => {
    const event = new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
      composed: true,
    });
    target.dispatchEvent(event);
    return event;
  };
  const pointer = (
    target: Element,
    type: string,
    init: PointerEventInit = {},
  ) =>
    target.dispatchEvent(
      new PointerEvent(type, {
        bubbles: true,
        composed: true,
        pointerId: 1,
        // The first finger or the mouse is the primary pointer.
        isPrimary: (init.pointerId ?? 1) === 1,
        ...init,
      }),
    );
  const key = (el: UitGraphView, k: string, init: KeyboardEventInit = {}) => {
    const event = new KeyboardEvent("keydown", {
      key: k,
      bubbles: true,
      cancelable: true,
      ...init,
    });
    svgOf(el).dispatchEvent(event);
    return event;
  };

  it("asks for the node's actions on a context click and hides the browser menu", async () => {
    const el = await graph();
    const seen = contexts(el);
    const event = contextmenu(byId(el, "dev:uuid-ap"));
    expect(event.defaultPrevented).toBe(true);
    expect(seen).toHaveLength(1);
    expect(seen[0]!.detail).toEqual({ id: "dev:uuid-ap" });
    expect(seen[0]!.bubbles && seen[0]!.composed).toBe(true);
  });

  it("also reports client groups, which the card then keeps menu-less", async () => {
    const el = await graph();
    const seen = contexts(el);
    contextmenu(byId(el, "group:dev:uuid-ap"));
    expect(seen.map((e) => e.detail)).toEqual([{ id: "group:dev:uuid-ap" }]);
  });

  it("leaves the browser menu alone away from the nodes", async () => {
    const el = await graph();
    const seen = contexts(el);
    const event = contextmenu(svgOf(el));
    expect(event.defaultPrevented).toBe(false);
    expect(seen).toHaveLength(0);
  });

  it("asks on the ContextMenu key and on Shift+F10 for the focused node", async () => {
    const el = await graph();
    const seen = contexts(el);
    byId(el, "dev:uuid-gw").focus();
    await el.updateComplete;
    const viaKey = key(el, "ContextMenu");
    const viaShift = key(el, "F10", { shiftKey: true });
    expect(viaKey.defaultPrevented && viaShift.defaultPrevented).toBe(true);
    expect(seen.map((e) => e.detail)).toEqual([
      { id: "dev:uuid-gw" },
      { id: "dev:uuid-gw" },
    ]);
    // Follows the focus.
    byId(el, "dev:uuid-core").focus();
    await el.updateComplete;
    key(el, "ContextMenu");
    expect(seen.at(-1)!.detail).toEqual({ id: "dev:uuid-core" });
  });

  it("ignores F10 without Shift and keeps the existing keys", async () => {
    const el = await graph();
    const seen = contexts(el);
    const activated = vi.fn();
    el.addEventListener("uit-activate", (e) =>
      activated((e as CustomEvent).detail),
    );
    byId(el, "dev:uuid-gw").focus();
    await el.updateComplete;
    const plain = key(el, "F10");
    key(el, "Enter");
    key(el, " ");
    expect(plain.defaultPrevented).toBe(false);
    expect(seen).toHaveLength(0);
    expect(activated).toHaveBeenCalledTimes(2);
  });

  it("does not open the menu after a pan", async () => {
    const el = await graph();
    const seen = contexts(el);
    const node = byId(el, "dev:uuid-ap");
    pointer(node, "pointerdown", { clientX: 100, clientY: 100 });
    pointer(node, "pointermove", { clientX: 140, clientY: 100 });
    const event = contextmenu(node);
    // The browser menu is still hidden; only the node menu is skipped.
    expect(event.defaultPrevented).toBe(true);
    expect(seen).toHaveLength(0);
  });

  it("tolerates a little jitter and a stationary long press", async () => {
    const el = await graph();
    const seen = contexts(el);
    const node = byId(el, "dev:uuid-ap");
    pointer(node, "pointerdown", { clientX: 100, clientY: 100 });
    pointer(node, "pointermove", { clientX: 103, clientY: 102 });
    contextmenu(node);
    expect(seen).toHaveLength(1);
  });

  it("does not open the menu after a pinch, and recovers on the next press", async () => {
    const el = await graph();
    const seen = contexts(el);
    const node = byId(el, "dev:uuid-ap");
    pointer(node, "pointerdown", {
      pointerId: 1,
      clientX: 10,
      clientY: 10,
    });
    pointer(node, "pointerdown", {
      pointerId: 2,
      clientX: 60,
      clientY: 60,
    });
    pointer(node, "pointerup", { pointerId: 2 });
    // One finger is still down, but the gesture was a pinch.
    contextmenu(node);
    expect(seen).toHaveLength(0);

    pointer(node, "pointerup", { pointerId: 1 });
    pointer(node, "pointerdown", {
      pointerId: 1,
      clientX: 10,
      clientY: 10,
    });
    contextmenu(node);
    expect(seen).toHaveLength(1);
  });

  it("does not count a pointer that moves after its press ended", async () => {
    const el = await graph();
    const seen = contexts(el);
    const node = byId(el, "dev:uuid-ap");
    pointer(node, "pointerdown", { clientX: 0, clientY: 0 });
    pointer(node, "pointerup");
    // A hovering mouse keeps sending moves; that is not a pan.
    pointer(node, "pointermove", { clientX: 200, clientY: 0 });
    contextmenu(node);
    expect(seen).toHaveLength(1);
  });

  it("forgets a cancelled press", async () => {
    const el = await graph();
    const seen = contexts(el);
    const node = byId(el, "dev:uuid-ap");
    pointer(node, "pointerdown", { clientX: 0, clientY: 0 });
    pointer(node, "pointermove", { clientX: 90, clientY: 0 });
    pointer(node, "pointercancel");
    pointer(node, "pointerdown", { clientX: 0, clientY: 0 });
    contextmenu(node);
    expect(seen).toHaveLength(1);
  });
});

describe("<uit-graph-view> layout mode", () => {
  const translate = (el: UitGraphView, id: string) =>
    byId(el, id).getAttribute("style");

  it("draws the tree by default and the tiered rows on request", async () => {
    const tree = await graph();
    expect(tree.layoutMode).toBe("tree");
    const tiered = await graph({ layoutMode: "tiered" });
    const model = fixtureModel();
    const depth = SPACING.comfortable.depth;
    const treeLayout = layoutModel(model, "comfortable", "vertical");
    const tieredLayout = layoutTiered(model, "comfortable", "vertical");
    const at = (p: { x: number; y: number }) =>
      `transform: translate(${p.x}px, ${p.y}px)`;
    for (const id of ["dev:uuid-ap", "dev:uuid-flex"]) {
      expect(translate(tree, id)).toBe(at(treeLayout.positions.get(id)!));
      expect(translate(tiered, id)).toBe(at(tieredLayout.positions.get(id)!));
    }
    // The tree puts the AP two levels down, the tiers (WANs first) on row four.
    expect(tieredLayout.positions.get("dev:uuid-ap")!.y).toBe(4 * depth);
    expect(treeLayout.positions.get("dev:uuid-ap")!.y).toBe(2 * depth);
  });

  it("recomputes the positions when the layout changes", async () => {
    const el = await graph();
    const before = translate(el, "dev:uuid-ap");
    el.layoutMode = "tiered";
    await el.updateComplete;
    const after = translate(el, "dev:uuid-ap");
    expect(after).not.toBe(before);
    expect(after).toBe(
      `transform: translate(0px, ${4 * SPACING.comfortable.depth}px)`,
    );
    el.layoutMode = "tree";
    await el.updateComplete;
    expect(translate(el, "dev:uuid-ap")).toBe(before);
  });
});

describe("<uit-graph-view> updates", () => {
  it("fades new nodes in and removed nodes out, unless motion is reduced", async () => {
    const el = await graph({ reducedMotion: false });
    el.model = fixtureModel({
      toggledGroups: new Set(["group:dev:uuid-ap"]),
    });
    await el.updateComplete;
    expect(byId(el, "cli:cli-phone").classList.contains("enter")).toBe(true);
    el.model = fixtureModel();
    await el.updateComplete;
    expect(el.shadowRoot!.querySelectorAll("g.exits g.node")).toHaveLength(1);
    await new Promise((r) => setTimeout(r, 300));
    await el.updateComplete;
    expect(el.shadowRoot!.querySelectorAll("g.exits g.node")).toHaveLength(0);
  });

  it("keeps the view still when motion is reduced", async () => {
    const el = await graph();
    expect(svgOf(el).classList.contains("still")).toBe(true);
    el.model = fixtureModel({ clients: "hidden" });
    await el.updateComplete;
    expect(el.shadowRoot!.querySelectorAll("g.exits g.node")).toHaveLength(0);
  });
});

describe("plan v3 layout refit", () => {
  it("starts a fresh fit when the mode changes after zooming", async () => {
    const el = await graph();
    el.zoomBy(2);
    const fit = vi.spyOn(el, "fit");
    el.layoutMode = "tiered";
    await el.updateComplete;
    expect(fit).toHaveBeenCalledExactlyOnceWith(false);
    const next = layoutTiered(fixtureModel(), "comfortable", "vertical");
    const t = fitTransform(next.bounds, 800, 600);
    expect(
      el.shadowRoot!.querySelector("g.viewport")!.getAttribute("transform"),
    ).toBe(`translate(${t.x},${t.y}) scale(${t.k})`);
    fit.mockClear();
    el.model = fixtureModel({ clients: "expanded" });
    await el.updateComplete;
    el.setViewportSize(900, 700);
    expect(fit).not.toHaveBeenCalled();
    fit.mockRestore();
  });

  it("keeps logical tree keyboard movement in tiered mode", async () => {
    const el = await graph({ layoutMode: "tiered" });
    byId(el, "dev:uuid-core").focus();
    for (const [key, id] of [
      ["ArrowDown", "dev:uuid-flex"],
      ["ArrowRight", "dev:uuid-lite"],
      ["ArrowUp", "dev:uuid-core"],
      ["Home", "dev:uuid-core"],
      ["ArrowUp", "dev:uuid-gw"],
    ]) {
      svgOf(el).dispatchEvent(
        new KeyboardEvent("keydown", { key, bubbles: true }),
      );
      await el.updateComplete;
      await el.updateComplete;
      expect(el.focusId).toBe(id);
      expect(el.shadowRoot!.activeElement?.getAttribute("data-id")).toBe(id);
    }
  });
});

describe("<uit-graph-view> WAN uplinks", () => {
  it("draws WANs above the gateway, marking a disconnected one", async () => {
    const el = await graph();
    const fiber = byId(el, "wan:uuid-gw:wan");
    const backup = byId(el, "wan:uuid-gw:wan2");
    expect(fiber.getAttribute("aria-label")).toBe(
      "WAN Fiber, connected, internet connection of Gateway",
    );
    expect(fiber.querySelector(".state-text")).toBeNull();
    expect(backup.classList.contains("offline")).toBe(true);
    expect(backup.querySelector(".state-text")!.textContent).toBe(
      "Disconnected",
    );
    /** A node's drawn y position, read from its transform. */
    const y = (id: string) =>
      Number(
        /translate\([^,]+, ([-\d.]+)px\)/.exec(
          byId(el, id).getAttribute("style")!,
        )![1],
      );
    expect(y("wan:uuid-gw:wan")).toBeLessThan(y("dev:uuid-gw"));
    const links = el.shadowRoot!.querySelectorAll("g.links path.link");
    expect([...links].filter((l) => l.classList.contains("down"))).toHaveLength(
      1,
    );
  });

  it("highlights the WAN link and the gateway path when a WAN is selected", async () => {
    const el = await graph({ selectedId: "wan:uuid-gw:wan" });
    expect(byId(el, "dev:uuid-gw").classList.contains("on-path")).toBe(true);
    const onPath = el.shadowRoot!.querySelectorAll("g.links path.on-path");
    expect(onPath).toHaveLength(1);
  });

  it("culls WAN links with their nodes on big graphs", async () => {
    const snapshot = fixtureSnapshot();
    for (let i = 0; i < 320; i++)
      snapshot.nodes.push({
        id: `dev:extra-${i}`,
        kind: "switch",
        name: `Extra ${i}`,
        state: "online",
      });
    const el = await graph({ model: fixtureModel({}, snapshot) });
    const links = [...el.shadowRoot!.querySelectorAll("g.links path.link")];
    expect(links.length).toBeLessThan(
      el.model!.links.size + el.model!.uplinkLinks.length,
    );
  });
});
