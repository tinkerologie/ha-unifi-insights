import type { MessageBase } from "../src/ha-types";
import type { TopologyCardConfig } from "../src/config";
import { afterEach, describe, expect, it, vi } from "vitest";
import "../src/index";
import { WS_SUBSCRIBE } from "../src/contract";
import { BACKOFF_BASE_MS } from "../src/data/subscription";
import { UnifiInsightsTopologyCard } from "../src/topology-card";
import type { UitDetailPanel } from "../src/views/detail-panel";
import type { UitGraphView } from "../src/views/graph-view";
import type { UitListView } from "../src/views/list-view";
import type { UitNodeMenu } from "../src/views/node-menu";
import {
  SOURCES_MULTI,
  SOURCES_ONE,
  cleanup,
  fakeHass,
  fixtureSnapshot,
  settle,
  unavailableSnapshot,
} from "./helpers";

const TYPE = "custom:unifi-insights-topology-card";
afterEach(cleanup);

async function card(config: Record<string, unknown> = {}, fake = fakeHass()) {
  const el = document.createElement(
    "unifi-insights-topology-card",
  ) as UnifiInsightsTopologyCard;
  el.setConfig({ type: TYPE, ...config });
  el.hass = fake.hass;
  document.body.append(el);
  await settle(el);
  return { el, fake };
}
const text = (el: Element) => el.shadowRoot!.textContent ?? "";
const q = <T extends Element>(el: Element, selector: string) =>
  el.shadowRoot!.querySelector<T>(selector);
const button = (el: Element, label: string) =>
  [...el.shadowRoot!.querySelectorAll<HTMLButtonElement>("button")].find(
    (b) => b.textContent?.trim() === label,
  )!;
const graphOf = (el: Element) => q<UitGraphView>(el, "uit-graph-view")!;
const dispatch = (el: Element, type: string, id: string) =>
  q(el, ".card")!.dispatchEvent(
    new CustomEvent(type, {
      detail: { id },
      bubbles: true,
      composed: true,
    }),
  );

describe("registration", () => {
  it("appears once in the card picker with a preview", () => {
    const entries = (window.customCards ?? []).filter(
      (c) => c.type === "unifi-insights-topology-card",
    );
    expect(entries).toEqual([
      expect.objectContaining({
        name: "UniFi Topology",
        preview: true,
      }),
    ]);
  });

  it("rejects an invalid config with a readable error", () => {
    const el = document.createElement(
      "unifi-insights-topology-card",
    ) as UnifiInsightsTopologyCard;
    expect(() =>
      el.setConfig({
        type: TYPE,
        view: "map",
      } as unknown as TopologyCardConfig),
    ).toThrow(/view must be one of/);
  });

  it("sizes itself for masonry and sections", () => {
    const el = document.createElement(
      "unifi-insights-topology-card",
    ) as UnifiInsightsTopologyCard;
    expect(el.getCardSize()).toBe(8);
    expect(el.getGridOptions()).toEqual({
      columns: 12,
      rows: 8,
      min_columns: 6,
      min_rows: 4,
    });
  });

  it("uses a shrinkable flex basis on .body and min-height: 0 on .content so fixed-height cards do not clip overlays", () => {
    // jsdom does not compute layout, so verify the CSS contract directly.
    const cssText = UnifiInsightsTopologyCard.styles
      .map((s) => s.cssText)
      .join("\n");
    expect(cssText).toMatch(
      /\.body\s*\{[^}]*flex:\s*1 1 280px;[^}]*min-height:\s*0;/s,
    );
    expect(cssText).not.toMatch(/\.body\s*\{[^}]*min-height:\s*(280|240)px;/s);
    expect(cssText).toMatch(
      /\.content\s*\{[^}]*min-width:\s*0;[^}]*min-height:\s*0;/s,
    );
    expect(cssText).toMatch(
      /@container\s*\(width\s*<\s*600px\)\s*\{[\s\S]*?\.body\s*\{[^}]*flex-basis:\s*240px;/,
    );
  });

  it("builds a stub config from the first source", async () => {
    const Card = customElements.get(
      "unifi-insights-topology-card",
    ) as unknown as typeof UnifiInsightsTopologyCard;
    expect(
      await Card.getStubConfig(fakeHass({ sources: SOURCES_MULTI }).hass),
    ).toEqual({ type: TYPE, entry_id: "entry-1", site_id: "site-1" });
    expect(
      await Card.getStubConfig(
        fakeHass({
          sourcesError: { code: "unknown_command", message: "" },
        }).hass,
      ),
    ).toEqual({ type: TYPE });
  });
});

describe("states", () => {
  it("auto-binds the only site, subscribes and shows loading", async () => {
    const { el, fake } = await card();
    expect(fake.subs).toHaveLength(1);
    expect(fake.subs[0]!.message).toEqual({
      type: WS_SUBSCRIBE,
      entry_id: "entry-1",
      site_id: "site-1",
      max_clients: 500,
    });
    expect(text(el)).toContain("Loading network topology…");
  });

  it("renders the graph and the site name once a snapshot arrives", async () => {
    const { el, fake } = await card();
    fake.push(fixtureSnapshot());
    await settle(el);
    expect(graphOf(el).model?.visuals.has("dev:uuid-core")).toBe(true);
    expect(q(el, "h2")!.textContent).toBe("Home");
    expect(q(el, '[role="status"]')!.textContent?.trim()).toBe(
      "Topology updated.",
    );
  });

  it("points to the integration when none is loaded", async () => {
    const { el } = await card({}, fakeHass({ sources: [] }));
    expect(text(el)).toContain("No UniFi Insights integration is loaded.");
    button(el, "Integration").click();
    expect(location.pathname).toBe(
      "/config/integrations/integration/unifi_insights",
    );
  });

  it("asks for a site when several exist and none is configured", async () => {
    const { el, fake } = await card({}, fakeHass({ sources: SOURCES_MULTI }));
    expect(fake.subs).toHaveLength(0);
    expect(text(el)).toContain("Choose a site to show.");
    button(el, "Edit card").click();
    expect(location.search).toBe("?edit=1");
  });

  it("shows site_not_selected with an edit action", async () => {
    const fake = fakeHass({
      subscribeError: { code: "site_not_selected", message: "Site gone" },
    });
    const { el } = await card({ entry_id: "entry-1", site_id: "gone" }, fake);
    expect(text(el)).toContain(
      "The configured site no longer exists or is disabled.",
    );
    expect(button(el, "Edit card")).toBeDefined();
  });

  it("lists each issue of a partial snapshot in a banner", async () => {
    const { el, fake } = await card();
    fake.push(
      fixtureSnapshot({
        revision: "b".repeat(16),
        status: "partial",
        issues: [{ code: "parents_unresolved", severity: "warning" }],
        unresolved: [{ node_id: "dev:x", reason: "parent_not_found" }],
      }),
    );
    await settle(el);
    expect(q(el, ".notices li.warning")!.textContent).toContain(
      "1 nodes couldn't be placed under a parent.",
    );
    expect(graphOf(el)).not.toBeNull();
  });

  it("keeps the last graph, greyed and marked stale, while the site is unavailable", async () => {
    const { el, fake } = await card();
    fake.push(fixtureSnapshot());
    fake.push(unavailableSnapshot("site_unavailable"));
    await settle(el);
    expect(q(el, ".content.stale")).not.toBeNull();
    expect(q(el, ".badge.stale")!.textContent).toBe("Stale");
    expect(graphOf(el).model?.visuals.size).toBeGreaterThan(0);
  });

  it("shows reconnecting after the entry unloads", async () => {
    const { el, fake } = await card();
    fake.push(fixtureSnapshot());
    fake.push(unavailableSnapshot("entry_unloaded"));
    await settle(el);
    expect(q(el, ".badge.stale")!.textContent).toBe("Stale · Reconnecting…");
  });

  it("reports a schema mismatch", async () => {
    const { el, fake } = await card();
    fake.push({ ...fixtureSnapshot(), schema_version: 1 });
    await settle(el);
    expect(text(el)).toContain("Card and integration versions don't match.");
  });
});

describe("interaction", () => {
  async function loaded(config: Record<string, unknown> = {}) {
    const result = await card(config);
    result.fake.push(fixtureSnapshot());
    await settle(result.el);
    return result;
  }

  it("toggles between graph and list views", async () => {
    const { el } = await loaded();
    button(el, "List").click();
    await settle(el);
    expect(q(el, "uit-list-view")).not.toBeNull();
    expect(button(el, "List").getAttribute("aria-pressed")).toBe("true");
    expect(button(el, "Graph").getAttribute("aria-pressed")).toBe("false");
  });

  it("filters kinds with chips but never hides every kind", async () => {
    const { el } = await loaded();
    button(el, "Switch").click();
    await settle(el);
    expect(graphOf(el).model?.visuals.has("dev:uuid-core")).toBe(false);
    const single = await loaded({ kinds: ["gateway"] });
    button(single.el, "Gateway").click();
    await settle(single.el);
    expect(button(single.el, "Gateway").getAttribute("aria-pressed")).toBe(
      "true",
    );
  });

  it("shows WAN nodes and their chip by default, and neither with show_wan off", async () => {
    const { el } = await loaded();
    expect(graphOf(el).model?.visuals.has("wan:uuid-gw:wan")).toBe(true);
    expect(button(el, "WAN").getAttribute("aria-pressed")).toBe("true");
    expect(text(el)).toContain("8 devices");
    const off = await loaded({ show_wan: false });
    expect(graphOf(off.el).model?.visuals.has("wan:uuid-gw:wan")).toBe(false);
    expect(button(off.el, "WAN")).toBeUndefined();
    expect(text(off.el)).toContain("8 devices");
  });

  it("announces offline devices in the first snapshot, without down WANs", async () => {
    const { el, fake } = await card();
    const snapshot = fixtureSnapshot();
    for (const id of ["dev:uuid-pdu", "wan:uuid-gw:wan"])
      snapshot.nodes.find((n) => n.id === id)!.state = "offline";
    fake.push(snapshot);
    await settle(el);
    expect(q(el, '[role="status"]')!.textContent?.trim()).toBe(
      "1 devices offline.",
    );
  });

  it("never counts a disconnected WAN as an offline device", async () => {
    const { el, fake } = await loaded();
    const snapshot = fixtureSnapshot();
    snapshot.revision = "wan-down";
    snapshot.nodes.find((n) => n.id === "wan:uuid-gw:wan")!.state = "offline";
    fake.push(snapshot);
    await settle(el);
    expect(q(el, '[role="status"]')!.textContent).not.toContain("offline");
  });

  it("opens details on activate and closes them with Escape", async () => {
    const { el } = await loaded();
    dispatch(el, "uit-activate", "dev:uuid-core");
    await settle(el);
    expect(el.selectedId).toBe("dev:uuid-core");
    expect(
      q<HTMLElement & { selectedId?: string }>(el, "uit-detail-panel")!
        .selectedId,
    ).toBe("dev:uuid-core");
    q(el, ".card")!.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    await settle(el);
    expect(el.selectedId).toBeUndefined();
  });

  it("expands a group when it is activated or when one of its clients is selected", async () => {
    const { el } = await loaded();
    dispatch(el, "uit-activate", "group:dev:uuid-ap");
    await settle(el);
    expect(graphOf(el).model?.visuals.has("cli:cli-phone")).toBe(true);
    dispatch(el, "uit-select", "cli:cli-tv");
    await settle(el);
    expect(graphOf(el).model?.visuals.has("cli:cli-tv")).toBe(true);
    expect(el.selectedId).toBe("cli:cli-tv");
  });

  it("switches sites with the selector without flashing the old graph", async () => {
    const fake = fakeHass({ sources: SOURCES_MULTI });
    const { el } = await card(
      {
        entry_id: "entry-1",
        site_id: "site-1",
        show_site_selector: true,
      },
      fake,
    );
    fake.push(fixtureSnapshot());
    await settle(el);
    const select = q<HTMLSelectElement>(el, "select")!;
    expect(select.options).toHaveLength(3);
    select.value = "1";
    select.dispatchEvent(new Event("change"));
    await settle(el);
    expect(fake.subs[0]!.unsubscribe).toHaveBeenCalledOnce();
    expect(fake.subs[1]!.message).toMatchObject({
      entry_id: "entry-1",
      site_id: "site-2",
    });
    expect(q(el, "uit-graph-view")).toBeNull();
    expect(text(el)).toContain("Loading network topology…");
  });

  it("collapses the toolbar and compacts the graph when narrow", async () => {
    const { el } = await loaded();
    el.narrow = true;
    await settle(el);
    expect(q(el, "details.toolbar")).not.toBeNull();
    expect(graphOf(el).density).toBe("compact");
    expect(
      q<HTMLElement & { narrow: boolean }>(el, "uit-detail-panel")!.narrow,
    ).toBe(true);
  });

  it("passes the configured layout to the graph", async () => {
    const { el } = await loaded();
    expect(graphOf(el).layoutMode).toBe("tree");
    const tiered = await loaded({ layout: "tiered" });
    expect(graphOf(tiered.el).layoutMode).toBe("tiered");
  });

  it("lets the wheel zoom directly only in panel view", async () => {
    const { el } = await loaded();
    expect(graphOf(el).ctrlZoom).toBe(true);
    el.layout = "panel";
    await settle(el);
    expect(graphOf(el).ctrlZoom).toBe(false);
  });
});

describe("node action menu", () => {
  const DETAILS = {
    "dev:uuid-core": {
      kind: "switch" as const,
      ip_address: "192.0.2.10",
      mac_address: "aa:bb:cc:dd:ee:ff",
      device_id: "reg-core",
      entity_id: "sensor.core_status",
    },
    "cli:cli-tv": {
      kind: "client" as const,
      ip_address: "192.0.2.77",
    },
  };

  async function loaded(admin: boolean | undefined = true) {
    const fake = fakeHass({ admin, details: DETAILS });
    const { el } = await card({}, fake);
    fake.push(fixtureSnapshot());
    await settle(el);
    return { el, fake };
  }
  const panelOf = (el: Element) => q<UitDetailPanel>(el, "uit-detail-panel")!;
  const menuOf = (el: Element) =>
    panelOf(el).shadowRoot!.querySelector<UitNodeMenu>("uit-node-menu");
  const menuList = (el: Element) =>
    menuOf(el)?.shadowRoot!.querySelector('[role="menu"]') ?? null;
  const nodeIn = (el: Element, id: string) =>
    [...graphOf(el).shadowRoot!.querySelectorAll<SVGGElement>("g.node")].find(
      (n) => n.getAttribute("data-id") === id,
    )!;
  const contextClick = (target: Element) =>
    target.dispatchEvent(
      new MouseEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
        composed: true,
      }),
    );
  const escapeOn = (target: Element) =>
    target.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        bubbles: true,
        composed: true,
        cancelable: true,
      }),
    );

  it("hands the panel the user and the site's session", async () => {
    const { el, fake } = await loaded();
    expect(panelOf(el).hass).toBe(fake.hass);
  });

  it("hands the panel a changed administrator flag even though nothing else changed", async () => {
    const { el, fake } = await loaded();
    dispatch(el, "uit-activate", "dev:uuid-core");
    await settle(el);
    await settle(panelOf(el));
    expect(menuOf(el)!.actions).toContain("copy_ip");

    // Same connection and language: only the user's rights differ.
    const demoted = {
      ...fake.hass,
      user: { is_admin: false },
    } as typeof fake.hass;
    el.hass = demoted;
    await settle(el);
    await settle(panelOf(el));
    expect(panelOf(el).hass).toBe(demoted);
    expect(menuOf(el)).toBeNull();
  });

  it("waits for fetched details and drops a stale context token after selection changes", async () => {
    const { el, fake } = await loaded();
    let answer!: (details: unknown) => void;
    const previous = fake.hass.callWS;
    fake.hass.callWS = ((message: MessageBase) =>
      message.type === "unifi_insights/topology/node_details"
        ? new Promise((resolve) => {
            answer = resolve;
          })
        : previous(message)) as never;
    contextClick(nodeIn(el, "dev:uuid-core"));
    await settle(el);
    expect(menuOf(el)).toBeNull();
    dispatch(el, "uit-select", "dev:uuid-flex");
    await settle(el);
    expect(el.menuRequest).toBeUndefined();
    answer({ kind: "switch", device_id: "reg-flex" });
    await settle(el);
    await settle(panelOf(el));
    expect(menuList(el)).toBeNull();
    expect(menuOf(el)!.actions).toEqual(["open_device"]);
  });

  it("opens only after the details for the context node arrive", async () => {
    const { el, fake } = await loaded();
    let answer!: (details: unknown) => void;
    const previous = fake.hass.callWS;
    fake.hass.callWS = ((message: MessageBase) =>
      message.type === "unifi_insights/topology/node_details"
        ? new Promise((resolve) => {
            answer = resolve;
          })
        : previous(message)) as never;
    contextClick(nodeIn(el, "dev:uuid-core"));
    await settle(el);
    expect(panelOf(el).selectedId).toBe("dev:uuid-core");
    expect(menuOf(el)).toBeNull();
    answer(DETAILS["dev:uuid-core"]);
    await settle(el);
    await settle(panelOf(el));
    expect(menuList(el)).not.toBeNull();
  });

  it("expands the containing group for context selection without toggling it again", async () => {
    const { el } = await loaded();
    dispatch(el, "uit-context", "cli:cli-tv");
    await settle(el);
    expect(el.ui.toggledGroups.has("group:dev:uuid-u1")).toBe(true);
    expect(el.selectedId).toBe("cli:cli-tv");
    dispatch(el, "uit-context", "cli:cli-tv");
    await settle(el);
    expect(el.ui.toggledGroups.has("group:dev:uuid-u1")).toBe(true);
  });

  it("drops fetched details after the card is detached", async () => {
    const { el, fake } = await loaded();
    let answer!: (details: unknown) => void;
    const previous = fake.hass.callWS;
    fake.hass.callWS = ((message: MessageBase) =>
      message.type === "unifi_insights/topology/node_details"
        ? new Promise((resolve) => {
            answer = resolve;
          })
        : previous(message)) as never;
    contextClick(nodeIn(el, "dev:uuid-core"));
    await settle(el);
    el.remove();
    answer(DETAILS["dev:uuid-core"]);
    await settle(el);
    expect(panelOf(el).details).toBeUndefined();
    expect(el.menuRequest).toBeUndefined();
  });

  it("selects the node and opens its menu on the first item after a context click", async () => {
    const { el } = await loaded();
    contextClick(nodeIn(el, "dev:uuid-core"));
    await settle(el);
    await settle(panelOf(el));
    expect(el.selectedId).toBe("dev:uuid-core");
    expect(panelOf(el).selectedId).toBe("dev:uuid-core");
    const menu = menuOf(el)!;
    expect(menuList(el)).not.toBeNull();
    expect(menu.shadowRoot!.activeElement).toBe(
      menu.shadowRoot!.querySelector('[data-action="copy_ip"]'),
    );
  });

  it("does the same from a list row", async () => {
    const { el } = await loaded();
    button(el, "List").click();
    await settle(el);
    const list = q<UitListView>(el, "uit-list-view")!;
    const row = list.shadowRoot!.querySelector<HTMLElement>(
      '[data-id="dev:uuid-core"]',
    )!;
    contextClick(row);
    await settle(el);
    await settle(panelOf(el));
    expect(el.selectedId).toBe("dev:uuid-core");
    expect(menuList(el)).not.toBeNull();
  });

  it("selects a client group without a menu or a toggle", async () => {
    const { el } = await loaded();
    const group = "group:dev:uuid-ap";
    contextClick(nodeIn(el, group));
    await settle(el);
    await settle(panelOf(el));
    expect(el.selectedId).toBe(group);
    expect(panelOf(el).menuRequest).toBeUndefined();
    expect(menuOf(el)).toBeNull();
    expect(graphOf(el).model?.visuals.has("cli:cli-phone")).toBe(false);
  });

  it("selects a client for a non-administrator without any menu", async () => {
    const { el, fake } = await loaded(false);
    button(el, "List").click();
    await settle(el);
    dispatch(el, "uit-select", "cli:cli-tv");
    await settle(el);
    dispatch(el, "uit-context", "cli:cli-tv");
    await settle(el);
    await settle(panelOf(el));
    expect(el.selectedId).toBe("cli:cli-tv");
    expect(menuOf(el)).toBeNull();
    expect(
      vi
        .mocked(fake.hass.callWS)
        .mock.calls.some(
          ([message]) =>
            (message as { type: string }).type ===
            "unifi_insights/topology/node_details",
        ),
    ).toBe(false);
  });

  it("ignores a context request for a node that is not drawn", async () => {
    const { el } = await loaded();
    dispatch(el, "uit-context", "dev:gone");
    await settle(el);
    expect(el.selectedId).toBeUndefined();
  });

  it("opens again on every context request, not only the first", async () => {
    const { el } = await loaded();
    const node = nodeIn(el, "dev:uuid-core");
    contextClick(node);
    await settle(el);
    await settle(panelOf(el));
    expect(menuList(el)).not.toBeNull();
    menuOf(el)!.hide();
    await settle(panelOf(el));
    expect(menuList(el)).toBeNull();
    contextClick(node);
    await settle(el);
    await settle(panelOf(el));
    expect(menuList(el)).not.toBeNull();
  });

  it("does not clear selection for card-level Escape while the menu is open", async () => {
    const { el } = await loaded();
    contextClick(nodeIn(el, "dev:uuid-core"));
    await settle(el);
    await settle(panelOf(el));
    expect(menuOf(el)!.open).toBe(true);
    escapeOn(el.shadowRoot!.querySelector("ha-card")!);
    await settle(el);
    expect(el.selectedId).toBe("dev:uuid-core");
  });

  it("closes only the menu on Escape and the details on the next Escape", async () => {
    const { el } = await loaded();
    contextClick(nodeIn(el, "dev:uuid-core"));
    await settle(el);
    await settle(panelOf(el));
    const item =
      menuOf(el)!.shadowRoot!.querySelector<HTMLElement>('[role="menuitem"]')!;
    escapeOn(item);
    await settle(el);
    await settle(panelOf(el));
    expect(menuList(el)).toBeNull();
    expect(el.selectedId).toBe("dev:uuid-core");

    escapeOn(menuOf(el)!.shadowRoot!.querySelector("button.trigger")!);
    await settle(el);
    expect(el.selectedId).toBeUndefined();
  });

  it("forgets the request when the details close, so a plain selection stays shut", async () => {
    const { el } = await loaded();
    contextClick(nodeIn(el, "dev:uuid-core"));
    await settle(el);
    await settle(panelOf(el));
    panelOf(el)
      .shadowRoot!.querySelector<HTMLButtonElement>("button.close")!
      .click();
    await settle(el);
    expect(el.selectedId).toBeUndefined();
    expect(panelOf(el).menuRequest).toBeUndefined();

    dispatch(el, "uit-activate", "dev:uuid-core");
    await settle(el);
    await settle(panelOf(el));
    expect(menuList(el)).toBeNull();
  });
});

describe("reconnects", () => {
  it.each([
    {
      label: "multiple sites",
      sources: SOURCES_MULTI,
      expected: "Choose a site to show.",
      subscriptions: 0,
    },
    {
      label: "one site",
      sources: SOURCES_ONE,
      expected: "Loading network topology…",
      subscriptions: 1,
    },
  ])(
    "retries a failed sources load and recovers for $label",
    async ({ sources, expected, subscriptions }) => {
      vi.useFakeTimers();
      try {
        const fake = fakeHass({ sources });
        vi.mocked(fake.hass.callWS).mockRejectedValueOnce({
          code: "unknown_command",
          message: "Not ready",
        });
        const el = document.createElement(
          "unifi-insights-topology-card",
        ) as UnifiInsightsTopologyCard;
        el.setConfig({ type: TYPE });
        el.hass = fake.hass;
        document.body.append(el);
        await vi.advanceTimersByTimeAsync(0);
        await el.updateComplete;
        expect(el.error?.code).toBe("unknown_command");

        await vi.advanceTimersByTimeAsync(BACKOFF_BASE_MS * 1.2 + 1);
        await el.updateComplete;
        expect(fake.hass.callWS).toHaveBeenCalledTimes(2);
        expect(el.error).toBeUndefined();
        expect(fake.subs).toHaveLength(subscriptions);
        expect(text(el)).toContain(expected);
      } finally {
        vi.useRealTimers();
      }
    },
  );

  it("keeps a subscription error when sources refresh succeeds", async () => {
    const fake = fakeHass({
      subscribeError: { code: "site_not_selected", message: "Site gone" },
    });
    const { el } = await card({ entry_id: "entry-1", site_id: "site-1" }, fake);
    expect(el.error?.code).toBe("site_not_selected");

    fake.emit("ready");
    await settle(el);
    expect(fake.hass.callWS).toHaveBeenCalledTimes(2);
    expect(el.error?.code).toBe("site_not_selected");
  });

  it("loads sources for a new connection after an old request settles", async () => {
    const old = fakeHass();
    let finishOld!: (sources: typeof SOURCES_ONE) => void;
    const oldRequest = new Promise<typeof SOURCES_ONE>((resolve) => {
      finishOld = resolve;
    });
    vi.mocked(old.hass.callWS).mockReturnValueOnce(oldRequest);
    const { el } = await card({}, old);

    const current = fakeHass({ sources: SOURCES_MULTI });
    el.hass = current.hass;
    await settle(el);
    expect(current.hass.callWS).not.toHaveBeenCalled();

    finishOld(SOURCES_ONE);
    await settle(el);
    expect(current.hass.callWS).toHaveBeenCalledOnce();
    expect(el.sources).toEqual(SOURCES_MULTI);
    expect(text(el)).toContain("Choose a site to show.");
  });

  it("greys the graph while the socket is down and reopens the stream on ready", async () => {
    const { el, fake } = await card();
    fake.push(fixtureSnapshot());
    await settle(el);
    fake.emit("disconnected");
    await settle(el);
    expect(q(el, ".badge.stale")!.textContent).toBe("Stale · Reconnecting…");
    expect(graphOf(el).model?.visuals.size).toBeGreaterThan(0);

    fake.emit("ready");
    await settle(el);
    expect(fake.subs).toHaveLength(2);
    expect(fake.hass.callWS).toHaveBeenCalledTimes(2);
    fake.push(fixtureSnapshot());
    await settle(el);
    expect(q(el, ".content.stale")).toBeNull();
  });

  it("keeps asking for sources while none are loaded yet", async () => {
    vi.useFakeTimers();
    try {
      const fake = fakeHass({ sources: [] });
      const el = document.createElement(
        "unifi-insights-topology-card",
      ) as UnifiInsightsTopologyCard;
      el.setConfig({ type: TYPE });
      el.hass = fake.hass;
      document.body.append(el);
      await vi.advanceTimersByTimeAsync(0);
      await el.updateComplete;
      expect(text(el)).toContain("No UniFi Insights integration is loaded.");

      vi.mocked(fake.hass.callWS).mockResolvedValue(SOURCES_ONE);
      await vi.advanceTimersByTimeAsync(BACKOFF_BASE_MS * 1.2 + 1);
      await el.updateComplete;
      expect(fake.subs).toHaveLength(1);
      expect(text(el)).toContain("Loading network topology…");
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("lifecycle", () => {
  it("does not resubscribe on ordinary hass updates", async () => {
    const { el, fake } = await card();
    el.hass = { ...fake.hass } as typeof fake.hass;
    await settle(el);
    expect(fake.subs).toHaveLength(1);
  });

  it("resubscribes after being re-attached", async () => {
    const { el, fake } = await card();
    el.remove();
    expect(fake.subs[0]!.unsubscribe).toHaveBeenCalledOnce();
    document.body.append(el);
    await settle(el);
    expect(fake.subs).toHaveLength(2);
    expect(fake.subs[1]!.unsubscribe).not.toHaveBeenCalled();
  });
});

it("keeps configured tiered layout independent of HA panel layout and refits on config changes", async () => {
  const fake = fakeHass();
  const { el } = await card({ layout: "tiered" }, fake);
  fake.push(fixtureSnapshot());
  await settle(el);
  el.layout = "panel";
  await settle(el);
  const graph = graphOf(el);
  graph.setViewportSize(800, 600);
  expect(graph.layoutMode).toBe("tiered");
  expect(graph.ctrlZoom).toBe(false);
  const fit = vi.spyOn(graph, "fit");
  el.setConfig({ type: TYPE, layout: "tree" });
  await settle(el);
  await settle(graph);
  expect(graph.layoutMode).toBe("tree");
  expect(el.layout).toBe("panel");
  expect(graph.ctrlZoom).toBe(false);
  expect(fit).toHaveBeenCalledExactlyOnceWith(false);
  fit.mockRestore();
});
