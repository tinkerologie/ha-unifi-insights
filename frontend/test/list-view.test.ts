import { afterEach, describe, expect, it, vi } from "vitest";
import { NODE_KINDS } from "../src/contract";
import { makeLocalize } from "../src/localize";
import { listRows, type UitListView } from "../src/views/list-view";
import { cleanup, fixtureModel, fixtureSnapshot, mount } from "./helpers";

const localize = makeLocalize("en");
afterEach(cleanup);

const rowsOf = (el: UitListView) => [
  ...el.shadowRoot!.querySelectorAll<HTMLElement>('[role="treeitem"]'),
];
const key = (el: UitListView, k: string) =>
  el
    .shadowRoot!.querySelector('[role="tree"]')!
    .dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));

/** The fixture without its WAN rows, for tests about the tree itself. */
const withoutWans = () =>
  fixtureModel({ kinds: new Set(NODE_KINDS.filter((k) => k !== "wan")) });

describe("listRows", () => {
  const model = withoutWans();

  it("lists each WAN right before its gateway, at the gateway's level", () => {
    const rows = listRows(fixtureModel(), new Set(), "");
    expect(rows.slice(0, 3).map((r) => `${r.level}:${r.id}`)).toEqual([
      "1:wan:uuid-gw:wan",
      "1:wan:uuid-gw:wan2",
      "1:dev:uuid-gw",
    ]);
    expect(rows[1]).toMatchObject({
      posinset: 2,
      setsize: 3,
      hasChildren: false,
      parentId: undefined,
    });
    expect(rows[2]).toMatchObject({ posinset: 3, setsize: 3 });
  });

  it("finds a WAN by name, with its gateway", () => {
    expect(
      listRows(fixtureModel(), new Set(), "backup").map((r) => r.id),
    ).toEqual(["wan:uuid-gw:wan2", "dev:uuid-gw"]);
  });

  it("flattens the tree depth-first with ARIA positions", () => {
    const rows = listRows(model, new Set(), "");
    expect(rows.map((r) => `${r.level}:${r.id}`)).toEqual([
      "1:dev:uuid-gw",
      "2:dev:uuid-core",
      "3:dev:uuid-flex",
      "3:dev:uuid-lite",
      "3:dev:uuid-pdu",
      "3:dev:uuid-u1",
      "4:group:dev:uuid-u1",
      "3:dev:uuid-u2",
      "3:dev:uuid-ap",
      "4:group:dev:uuid-ap",
    ]);
    expect(rows[2]).toMatchObject({
      posinset: 1,
      setsize: 6,
      hasChildren: false,
      parentId: "dev:uuid-core",
    });
    expect(rows[6]).toMatchObject({ hasChildren: true, expanded: false });
  });

  it("hides the descendants of collapsed devices", () => {
    expect(
      listRows(model, new Set(["dev:uuid-core"]), "").map((r) => r.id),
    ).toEqual(["dev:uuid-gw", "dev:uuid-core"]);
  });

  it("filters to matches and their ancestors, searching inside collapsed groups", () => {
    expect(
      listRows(model, new Set(["dev:uuid-core"]), "tv").map((r) => r.id),
    ).toEqual([
      "dev:uuid-gw",
      "dev:uuid-core",
      "dev:uuid-u1",
      "group:dev:uuid-u1",
    ]);
  });
});

describe("<uit-list-view>", () => {
  const props = () => ({ model: withoutWans(), localize, siteName: "Home" });

  it("labels WAN rows and marks a disconnected one", async () => {
    const el = await mount<UitListView>("uit-list-view", {
      ...props(),
      model: fixtureModel(),
    });
    const [fiber, backup] = rowsOf(el);
    expect(fiber!.getAttribute("aria-label")).toBe(
      "WAN Fiber, connected, internet connection of Gateway",
    );
    expect(fiber!.querySelector(".state-text")).toBeNull();
    expect(backup!.classList.contains("offline")).toBe(true);
    expect(backup!.querySelector(".state-text")!.textContent).toBe(
      "Disconnected",
    );
  });

  it("renders an ARIA tree with one tab stop", async () => {
    const el = await mount<UitListView>("uit-list-view", props());
    const rows = rowsOf(el);
    expect(
      el.shadowRoot!.querySelector('[role="tree"]')!.getAttribute("aria-label"),
    ).toBe("Home devices and clients");
    expect(rows).toHaveLength(10);
    expect(rows.filter((r) => r.tabIndex === 0)).toHaveLength(1);
    expect(rows[1]!.getAttribute("aria-level")).toBe("2");
    expect(rows[1]!.getAttribute("aria-expanded")).toBe("true");
    expect(rows[2]!.hasAttribute("aria-expanded")).toBe(false);
  });

  it("moves focus with arrows, Home/End and type-ahead", async () => {
    const el = await mount<UitListView>("uit-list-view", props());
    rowsOf(el)[0]!.focus();
    key(el, "ArrowDown");
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement?.getAttribute("data-id")).toBe(
      "dev:uuid-core",
    );
    key(el, "End");
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement?.getAttribute("data-id")).toBe(
      "group:dev:uuid-ap",
    );
    key(el, "Home");
    key(el, "l");
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement?.getAttribute("data-id")).toBe(
      "dev:uuid-lite",
    );
  });

  it("collapses and expands devices, and asks the card to toggle groups", async () => {
    const el = await mount<UitListView>("uit-list-view", props());
    const toggles = vi.fn();
    el.addEventListener("uit-toggle-group", (e) =>
      toggles((e as CustomEvent).detail),
    );
    rowsOf(el)[1]!.focus();
    key(el, "ArrowLeft");
    await el.updateComplete;
    expect(rowsOf(el)).toHaveLength(2);
    key(el, "ArrowRight");
    await el.updateComplete;
    expect(rowsOf(el)).toHaveLength(10);
    rowsOf(el)[6]!.focus();
    key(el, "ArrowRight");
    expect(toggles).toHaveBeenCalledWith({ id: "group:dev:uuid-u1" });
  });

  it("activates rows with Enter and click", async () => {
    const el = await mount<UitListView>("uit-list-view", props());
    const activate = vi.fn();
    el.addEventListener("uit-activate", (e) =>
      activate((e as CustomEvent).detail),
    );
    rowsOf(el)[0]!.focus();
    key(el, "Enter");
    rowsOf(el)[3]!.click();
    expect(activate.mock.calls).toEqual([
      [{ id: "dev:uuid-gw" }],
      [{ id: "dev:uuid-lite" }],
    ]);
  });

  it("keeps the search box focused while filtering", async () => {
    const el = await mount<UitListView>("uit-list-view", props());
    const input = el.shadowRoot!.querySelector("input")!;
    input.focus();
    input.value = "ultra";
    input.dispatchEvent(new Event("input"));
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement).toBe(input);
    expect(rowsOf(el).map((r) => r.dataset.id)).toEqual([
      "dev:uuid-gw",
      "dev:uuid-core",
      "dev:uuid-u1",
      "dev:uuid-u2",
    ]);
    input.value = "zzz";
    input.dispatchEvent(new Event("input"));
    await el.updateComplete;
    expect(el.shadowRoot!.textContent).toContain("No matches");
  });

  it("renders hostile and long names as text", async () => {
    const hostile = "<img src=x onerror=alert(1)>";
    const long = "x".repeat(80);
    const snapshot = fixtureSnapshot();
    snapshot.nodes.find((n) => n.id === "dev:uuid-gw")!.name = hostile;
    snapshot.nodes.find((n) => n.id === "dev:uuid-core")!.name = long;
    const el = await mount<UitListView>("uit-list-view", {
      ...props(),
      model: fixtureModel(
        { kinds: new Set(NODE_KINDS.filter((k) => k !== "wan")) },
        snapshot,
      ),
    });
    const names = [...el.shadowRoot!.querySelectorAll<HTMLElement>(".name")];
    expect(el.shadowRoot!.querySelector("img")).toBeNull();
    expect(names[0]!.textContent).toBe(hostile);
    expect(names[1]!.title).toBe(long);
  });

  describe("context requests", () => {
    const contexts = (el: UitListView) => {
      const seen: CustomEvent[] = [];
      el.addEventListener("uit-context", (e) => seen.push(e as CustomEvent));
      return seen;
    };
    const keydown = (
      el: UitListView,
      k: string,
      init: KeyboardEventInit = {},
    ) => {
      const event = new KeyboardEvent("keydown", {
        key: k,
        bubbles: true,
        cancelable: true,
        ...init,
      });
      el.shadowRoot!.querySelector('[role="tree"]')!.dispatchEvent(event);
      return event;
    };

    it("asks for the row's actions on a context click and hides the browser menu", async () => {
      const el = await mount<UitListView>("uit-list-view", props());
      const seen = contexts(el);
      const event = new MouseEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
        composed: true,
      });
      rowsOf(el)[3]!.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
      expect(seen.map((e) => e.detail)).toEqual([{ id: "dev:uuid-lite" }]);
      expect(seen[0]!.bubbles && seen[0]!.composed).toBe(true);
    });

    it("asks on the ContextMenu key and on Shift+F10 for the focused row", async () => {
      const el = await mount<UitListView>("uit-list-view", props());
      const seen = contexts(el);
      rowsOf(el)[1]!.focus();
      const viaKey = keydown(el, "ContextMenu");
      const viaShift = keydown(el, "F10", { shiftKey: true });
      expect(viaKey.defaultPrevented && viaShift.defaultPrevented).toBe(true);
      expect(seen.map((e) => e.detail)).toEqual([
        { id: "dev:uuid-core" },
        { id: "dev:uuid-core" },
      ]);
    });

    it("reports a client group row too", async () => {
      const el = await mount<UitListView>("uit-list-view", props());
      const seen = contexts(el);
      rowsOf(el)[6]!.focus();
      keydown(el, "ContextMenu");
      expect(seen.map((e) => e.detail)).toEqual([{ id: "group:dev:uuid-u1" }]);
    });

    it("keeps F10 without Shift and the type-ahead keys as they were", async () => {
      const el = await mount<UitListView>("uit-list-view", props());
      const seen = contexts(el);
      rowsOf(el)[0]!.focus();
      const plain = keydown(el, "F10");
      expect(plain.defaultPrevented).toBe(false);
      expect(seen).toHaveLength(0);
      keydown(el, "l");
      await el.updateComplete;
      expect(el.shadowRoot!.activeElement?.getAttribute("data-id")).toBe(
        "dev:uuid-lite",
      );
    });
  });
});

describe("plan v3 list gestures", () => {
  it.each(["pan", "pinch"])(
    "suppresses contextmenu during %s",
    async (gesture) => {
      const el = await mount<UitListView>("uit-list-view", {
        model: fixtureModel(),
        localize,
      });
      const row = rowsOf(el)[0]!;
      const emitted = vi.fn();
      el.addEventListener("uit-context", emitted);
      const pointer = (type: string, id: number, x: number) => {
        const event = new Event(type, { bubbles: true });
        Object.assign(event, {
          pointerId: id,
          isPrimary: id === 1,
          clientX: x,
          clientY: 0,
        });
        row.dispatchEvent(event);
      };
      pointer("pointerdown", 1, 0);
      if (gesture === "pan") pointer("pointermove", 1, 20);
      else pointer("pointerdown", 2, 0);
      const event = new MouseEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
      });
      row.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
      expect(emitted).not.toHaveBeenCalled();
    },
  );
});
