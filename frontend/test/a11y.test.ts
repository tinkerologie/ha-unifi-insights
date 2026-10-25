import axe from "axe-core";
import { afterEach, describe, expect, it } from "vitest";
import "../src/index";
import { WS_NODE_DETAILS } from "../src/contract";
import type { MessageBase } from "../src/ha-types";
import type { UnifiInsightsTopologyCard } from "../src/topology-card";
import type { UitDetailPanel } from "../src/views/detail-panel";
import type { UitNodeMenu } from "../src/views/node-menu";
import { cleanup, fakeHass, fixtureSnapshot, settle } from "./helpers";

afterEach(cleanup);

async function audit(root: Element): Promise<string[]> {
  const result = await axe.run(root, {
    runOnly: {
      type: "tag",
      values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
    },
    // jsdom cannot compute colours; contrast comes from HA theme variables (see styles.ts).
    rules: { "color-contrast": { enabled: false } },
  });
  return result.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`,
  );
}

async function loadedCard(
  config: Record<string, unknown> = {},
  snapshot = fixtureSnapshot(),
  fake = fakeHass(),
) {
  const el = document.createElement(
    "unifi-insights-topology-card",
  ) as UnifiInsightsTopologyCard;
  el.setConfig({ type: "custom:unifi-insights-topology-card", ...config });
  el.hass = fake.hass;
  document.body.append(el);
  await settle(el);
  fake.push(snapshot);
  await settle(el);
  return el;
}

describe("WCAG 2.2 AA (axe-core)", () => {
  it("graph view", async () => {
    expect(await audit(await loadedCard())).toEqual([]);
  });

  it("list view", async () => {
    expect(await audit(await loadedCard({ view: "list" }))).toEqual([]);
  });

  it("partial snapshot banner with the detail panel open", async () => {
    const el = await loadedCard(
      {},
      fixtureSnapshot({
        status: "partial",
        issues: [{ code: "devices_unavailable", severity: "warning" }],
      }),
    );
    el.selectedId = "dev:uuid-core";
    await settle(el);
    expect(await audit(el)).toEqual([]);
  });

  it("error state", async () => {
    const fake = fakeHass({
      subscribeError: { code: "entry_not_found", message: "" },
    });
    const el = document.createElement(
      "unifi-insights-topology-card",
    ) as UnifiInsightsTopologyCard;
    el.setConfig({
      type: "custom:unifi-insights-topology-card",
      entry_id: "x",
      site_id: "y",
    });
    el.hass = fake.hass;
    document.body.append(el);
    await settle(el);
    expect(await audit(el)).toEqual([]);
  });

  it("narrow layout with the collapsed toolbar", async () => {
    const el = await loadedCard();
    el.narrow = true;
    await settle(el);
    expect(await audit(el)).toEqual([]);
  });

  describe("node action menu", () => {
    const DETAILS = {
      kind: "switch" as const,
      ip_address: "192.0.2.10",
      mac_address: "aa:bb:cc:dd:ee:ff",
      device_id: "reg-core",
      entity_id: "sensor.core_status",
    };

    async function menuOpened(
      fake = fakeHass({
        admin: true,
        details: { "dev:uuid-core": DETAILS },
      }),
    ) {
      const el = await loadedCard({}, fixtureSnapshot(), fake);
      const graph = el.shadowRoot!.querySelector("uit-graph-view")!;
      graph
        .shadowRoot!.querySelector('[data-id="dev:uuid-core"]')!
        .dispatchEvent(
          new MouseEvent("contextmenu", {
            bubbles: true,
            cancelable: true,
            composed: true,
          }),
        );
      await settle(el);
      const panel =
        el.shadowRoot!.querySelector<UitDetailPanel>("uit-detail-panel")!;
      await settle(panel);
      const menu =
        panel.shadowRoot!.querySelector<UitNodeMenu>("uit-node-menu")!;
      if (menu) await settle(menu);
      return { el, panel, menu };
    }

    it("open, with every action", async () => {
      const { el, menu } = await menuOpened();
      expect(
        menu.shadowRoot!.querySelectorAll('[role="menuitem"]'),
      ).toHaveLength(4);
      const button = menu.shadowRoot!.querySelector("button.trigger")!;
      const popup = menu.shadowRoot!.querySelector('[role="menu"]')!;
      expect(button.getAttribute("aria-haspopup")).toBe("menu");
      expect(button.getAttribute("aria-expanded")).toBe("true");
      expect(button.getAttribute("aria-controls")).toBe(popup.id);
      expect(button.getAttribute("aria-label")).toBe("Actions");
      expect(popup.getAttribute("aria-label")).toBe("Actions");
      expect(
        [...menu.shadowRoot!.querySelectorAll('[role="menuitem"]')].map(
          (item) => item.textContent?.trim(),
        ),
      ).toEqual([
        "Copy IP address",
        "Copy MAC address",
        "Open device",
        "Open entity",
      ]);
      expect(await audit(el)).toEqual([]);
      menu.hide();
      await settle(menu);
      expect(button.getAttribute("aria-expanded")).toBe("false");
      expect(menu.shadowRoot!.querySelector('[role="menu"]')).toBeNull();
      expect(await audit(el)).toEqual([]);
    });

    it("hides Actions while details load", async () => {
      const fake = fakeHass({ admin: true });
      const answerSources = fake.hass.callWS;
      fake.hass.callWS = ((message: MessageBase) =>
        message.type === WS_NODE_DETAILS
          ? new Promise(() => undefined)
          : answerSources(message)) as never;
      const { el, menu } = await menuOpened(fake);
      expect(menu).toBeNull();
      expect(await audit(el)).toEqual([]);
    });

    it("after a failed copy, with the value shown for selecting", async () => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async () => {
            throw new Error("denied");
          },
        },
      });
      try {
        const { el, panel, menu } = await menuOpened();
        menu
          .shadowRoot!.querySelector<HTMLElement>('[data-action="copy_ip"]')!
          .click();
        await settle(panel);
        expect(panel.shadowRoot!.querySelector(".manual")!.textContent).toBe(
          "192.0.2.10",
        );
        expect(await audit(el)).toEqual([]);
      } finally {
        delete (navigator as { clipboard?: unknown }).clipboard;
      }
    });
  });
});

describe("companion card accessibility", () => {
  for (const name of [
    "site-health",
    "internet-activity",
    "performance",
    "protect-status",
    "timeline",
  ]) {
    for (const status of [
      "ok",
      "partial",
      "unavailable",
      "no_protect",
    ] as const) {
      it(`${name}: ${status}`, async () => {
        const fake = fakeHass();
        const tag = `unifi-insights-${name}-card`;
        const el = document.createElement(
          tag,
        ) as import("../src/dashboard-cards-base").BaseDashboardCard;
        el.setConfig({
          type: `custom:${tag}`,
          entry_id: "entry-1",
          site_id: "site-1",
        });
        el.hass = fake.hass;
        document.body.append(el);
        await settle(el);
        fake.subs[0]!.callback({ status, devices: [], items: [] });
        await settle(el);
        expect(await audit(el)).toEqual([]);
      });
    }
    it(`${name} editor`, async () => {
      const tag = `unifi-insights-${name}-card`;
      const el = document.createElement(
        `${tag}-editor`,
      ) as import("../src/dashboard-cards-editor").GenericEditor;
      el.setConfig({ type: `custom:${tag}` });
      el.hass = fakeHass().hass;
      document.body.append(el);
      await settle(el);
      expect(await audit(el)).toEqual([]);
    });
  }
});
