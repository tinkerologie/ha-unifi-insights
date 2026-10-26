import { afterEach, describe, expect, it } from "vitest";

import "../src/index";
import {
  PROTECT_CARD_TAG,
  SITE_HEALTH_CARD_TAG,
  UnifiInsightsInternetActivityCard,
  UnifiInsightsPerformanceCard,
  UnifiInsightsProtectStatusCard,
  UnifiInsightsProtectStatusCardEditor,
  UnifiInsightsSiteHealthCard,
  UnifiInsightsSiteHealthCardEditor,
  UnifiInsightsTimelineCard,
} from "../src/dashboard-cards";
import { cleanup, fakeHass, settle } from "./helpers";

afterEach(cleanup);

describe("timeline detection labels", () => {
  it.each([
    ["smartDetectZone", ["person"], ["Person"]],
    ["smartDetect", ["animal"], ["Animal"]],
    ["smartDetectZone", ["vehicle", "car"], ["Car"]],
    [
      "smartDetectZone",
      ["person", "animal", "vehicle", "person", "unknown"],
      ["Person", "Animal", "Car", "Unknown object (unknown)"],
    ],
    ["smartDetectZone", [], ["Motion"]],
    ["smartDetectZone", undefined, ["Motion"]],
    ["smartDetectZone", "person", ["Motion"]],
    ["smartDetectZone", [null, 1, {}, " "], ["Motion"]],
    ["smartDetectZone", [" animal "], ["Animal"]],
    [
      "smartDetectZone",
      ["<img src=x onerror=alert(1)>"],
      ["Unknown object (<img src=x onerror=alert(1)>)"],
    ],
    ["motion", undefined, ["Motion"]],
    ["ring", undefined, ["Doorbell"]],
    ["vehicle", undefined, ["Car"]],
    ["person", undefined, ["Person"]],
    ["animal", undefined, ["Animal"]],
    ["sensor_opened", undefined, ["sensor_opened"]],
  ])(
    "renders %s with %j as readable badges",
    async (kind, metadata, expected) => {
      const fake = fakeHass();
      const el = document.createElement(
        "unifi-insights-timeline-card",
      ) as UnifiInsightsTimelineCard;
      el.setConfig({
        type: "custom:unifi-insights-timeline-card",
        entry_id: "entry-1",
        site_id: "site-1",
      });
      el.hass = fake.hass;
      document.body.append(el);
      await settle(el);
      fake.subs[0]!.callback({
        status: "ok",
        items: [
          { kind, source: { name: "Driveway" }, smart_detect_types: metadata },
        ],
      });
      await settle(el);
      const badges = [
        ...el.shadowRoot!.querySelectorAll(".event-labels .chip"),
      ];
      expect(badges.map((b) => b.textContent!.trim())).toEqual(expected);
      expect(el.shadowRoot!.querySelectorAll("img")).toHaveLength(0);
      expect(
        badges.every((b) => b.querySelector('svg[aria-hidden="true"] path')),
      ).toBe(true);
      expect(el.shadowRoot!.textContent).toContain("Driveway");
    },
  );
});

describe("dashboard card registration", () => {
  it("registers all five additional dashboard cards once", () => {
    const cards = window.customCards ?? [];
    const types = cards.map((card) => card.type);

    expect(
      types.filter((type) => type === "unifi-insights-site-health-card"),
    ).toHaveLength(1);
    expect(
      types.filter((type) => type === "unifi-insights-internet-activity-card"),
    ).toHaveLength(1);
    expect(
      types.filter((type) => type === "unifi-insights-performance-card"),
    ).toHaveLength(1);
    expect(
      types.filter((type) => type === "unifi-insights-protect-status-card"),
    ).toHaveLength(1);
    expect(
      types.filter((type) => type === "unifi-insights-timeline-card"),
    ).toHaveLength(1);
  });

  it("registers concise card names and non-empty card picker descriptions", () => {
    const cards = window.customCards ?? [];
    const expectedNames: Record<string, string> = {
      "unifi-insights-site-health-card": "UniFi Site Health",
      "unifi-insights-internet-activity-card": "UniFi Internet Activity",
      "unifi-insights-performance-card": "UniFi Device Performance",
      "unifi-insights-protect-status-card": "UniFi Protect Status",
      "unifi-insights-timeline-card": "UniFi Event Timeline",
    };
    for (const [type, expectedName] of Object.entries(expectedNames)) {
      const entry = cards.find((card) => card.type === type);
      expect(entry?.name).toBe(expectedName);
      expect(entry?.description?.trim().length).toBeGreaterThan(0);
    }
  });

  it("exposes config elements for visual editor discovery", async () => {
    const siteEditor = await UnifiInsightsSiteHealthCard.getConfigElement();
    const protectEditor =
      await UnifiInsightsProtectStatusCard.getConfigElement();
    const timelineEditor = await UnifiInsightsTimelineCard.getConfigElement();

    expect(siteEditor.tagName.toLowerCase()).toBe(
      "unifi-insights-site-health-card-editor",
    );
    expect(protectEditor.tagName.toLowerCase()).toBe(
      "unifi-insights-protect-status-card-editor",
    );
    expect(timelineEditor.tagName.toLowerCase()).toBe(
      "unifi-insights-timeline-card-editor",
    );
  });

  it("uses card-specific defaults instead of topology labels", async () => {
    const fake = fakeHass();
    const el = document.createElement(
      SITE_HEALTH_CARD_TAG,
    ) as UnifiInsightsSiteHealthCard;
    el.setConfig({ type: `custom:${SITE_HEALTH_CARD_TAG}` });
    el.hass = fake.hass;
    document.body.append(el);
    await settle(el);

    const text = el.shadowRoot?.textContent ?? "";
    expect(text).toContain("Site Health");
    expect(text).not.toContain("UniFi Insights Topology");
    expect(text).not.toContain("Loading network topology");
  });

  it("subscribes on connect and unsubscribes on disconnect", async () => {
    const fake = fakeHass();
    const el = document.createElement(
      SITE_HEALTH_CARD_TAG,
    ) as UnifiInsightsSiteHealthCard;
    el.setConfig({ type: `custom:${SITE_HEALTH_CARD_TAG}` });
    el.hass = fake.hass;
    document.body.append(el);
    await settle(el);

    expect(fake.subs).toHaveLength(1);
    expect(fake.subs[0]?.message).toEqual({
      type: "unifi_insights/site_health/subscribe",
      entry_id: "entry-1",
      site_id: "site-1",
    });

    el.remove();
    await settle(el);
    expect(fake.subs[0]?.unsubscribe).toHaveBeenCalledTimes(1);
  });

  it("does not send site_id for protect subscribe", async () => {
    const fake = fakeHass({
      sources: [
        {
          entry_id: "entry-1",
          title: "Console",
          sites: [{ id: "site-1", name: "Home" }],
        },
      ] as never,
    });
    const el = document.createElement(
      PROTECT_CARD_TAG,
    ) as UnifiInsightsProtectStatusCard;
    el.setConfig({ type: `custom:${PROTECT_CARD_TAG}` });
    el.hass = fake.hass;
    document.body.append(el);
    await settle(el);

    expect(fake.subs).toHaveLength(1);
    expect(fake.subs[0]?.message).toEqual({
      type: "unifi_insights/protect/subscribe",
      entry_id: "entry-1",
    });
  });

  it("renders snapshots across all five cards and handles more-info clicks", async () => {
    const fake = fakeHass({
      sources: [
        {
          entry_id: "entry-1",
          title: "Console",
          sites: [{ id: "site-1", name: "Home" }],
        },
      ] as never,
    });

    // Site Health card with snapshot
    const siteEl = document.createElement(
      SITE_HEALTH_CARD_TAG,
    ) as UnifiInsightsSiteHealthCard;
    expect(() => siteEl.setConfig(null as never)).toThrow(
      "Invalid card config",
    );
    siteEl.setConfig({
      type: `custom:${SITE_HEALTH_CARD_TAG}`,
      entry_id: "entry-1",
      site_id: "site-1",
    });
    expect(siteEl.getCardSize()).toBe(4);
    expect(siteEl.getGridOptions()).toEqual({
      columns: 6,
      rows: 4,
      min_columns: 3,
      min_rows: 3,
    });
    siteEl.hass = fake.hass;
    document.body.append(siteEl);
    await settle(siteEl);
    fake.subs[0]?.callback({
      health: { level: "healthy" },
      gateway: { internet: "online" },
      clients: { total: 42 },
    } as never);
    await settle(siteEl);
    expect(siteEl.shadowRoot?.textContent).toContain("healthy");
    expect(siteEl.shadowRoot?.textContent).toContain("42");

    // Internet Activity card
    const internetEl = document.createElement(
      "unifi-insights-internet-activity-card",
    ) as UnifiInsightsInternetActivityCard;
    internetEl.setConfig({
      type: "custom:unifi-insights-internet-activity-card",
    });
    internetEl.hass = fake.hass;
    document.body.append(internetEl);
    await settle(internetEl);
    expect(internetEl.shadowRoot?.textContent).toContain("No data");
    fake.subs[1]?.callback({
      windows: {
        "1d": { download_bytes: 25_000_000, upload_bytes: 5_000_000 },
      },
    } as never);
    await settle(internetEl);
    expect(internetEl.shadowRoot?.textContent).toContain("25 MB");
    expect(internetEl.shadowRoot?.textContent).toContain("30 MB");

    // Performance card
    const perfEl = document.createElement(
      "unifi-insights-performance-card",
    ) as UnifiInsightsPerformanceCard;
    perfEl.setConfig({ type: "custom:unifi-insights-performance-card" });
    perfEl.hass = fake.hass;
    document.body.append(perfEl);
    await settle(perfEl);
    expect(perfEl.shadowRoot?.textContent).toContain("No devices");
    fake.subs[2]?.callback({
      devices: [
        { name: "Core Switch", cpu_pct: 18.4 },
        { name: "Living AP", cpu_pct: null },
      ],
    } as never);
    await settle(perfEl);
    expect(perfEl.shadowRoot?.textContent).toContain("Core Switch");
    expect(perfEl.shadowRoot?.textContent).toContain("18%");
    expect(perfEl.shadowRoot?.textContent).toContain("--");

    // Protect Status card with camera entity link and multi-source fallback
    const protectEl = document.createElement(
      PROTECT_CARD_TAG,
    ) as UnifiInsightsProtectStatusCard;
    protectEl.setConfig({
      type: `custom:${PROTECT_CARD_TAG}`,
      entry_id: "entry-1",
    });
    protectEl.hass = fake.hass;
    document.body.append(protectEl);
    await settle(protectEl);
    expect(protectEl.shadowRoot?.textContent).toContain("No Protect data");
    fake.subs[3]?.callback({
      devices: [
        {
          name: "Front Door",
          kind: "camera",
          connected: true,
          camera_entity_id: "camera.front_door",
        },
        {
          name: "Hall Chime",
          kind: "chime",
          connected: false,
        },
      ],
    } as never);
    await settle(protectEl);
    expect(protectEl.shadowRoot?.textContent).toContain("Front Door");
    expect(protectEl.shadowRoot?.textContent).toContain("offline");

    let moreInfoEntityId = "";
    protectEl.addEventListener("hass-more-info", ((ev: CustomEvent) => {
      moreInfoEntityId = ev.detail.entityId;
    }) as EventListener);
    const linkBtn = protectEl.shadowRoot?.querySelector(
      "button.link",
    ) as HTMLButtonElement | null;
    linkBtn?.click();
    expect(moreInfoEntityId).toBe("camera.front_door");

    // Timeline card
    const timelineEl = document.createElement(
      "unifi-insights-timeline-card",
    ) as UnifiInsightsTimelineCard;
    timelineEl.setConfig({ type: "custom:unifi-insights-timeline-card" });
    timelineEl.hass = fake.hass;
    document.body.append(timelineEl);
    await settle(timelineEl);
    expect(timelineEl.shadowRoot?.textContent).toContain("No events");
    fake.subs[4]?.callback({
      status: "ok",
      items: [],
    } as never);
    await settle(timelineEl);
    expect(timelineEl.shadowRoot?.textContent).toContain("All Quiet");
    fake.subs[4]?.callback({
      items: [
        {
          source: { name: "Driveway" },
          kind: "motion",
          severity: "warning",
          timestamp: new Date().toISOString(),
        },
        {
          source: { name: "Front Door" },
          kind: "ring",
          severity: "info",
          timestamp: new Date(Date.now() - 3600_000 * 2).toISOString(),
        },
      ],
    } as never);
    await settle(timelineEl);
    expect(timelineEl.shadowRoot?.textContent).toContain("Driveway");
    expect(timelineEl.shadowRoot?.textContent).toContain("Motion");

    // Test rich visual fields (window tabs, GB/Mbps formatting, uptime, CPU tones)
    fake.subs[0]?.callback({
      site_name: "Main Site",
      health: { level: "degraded" },
      gateway: { name: "UDM-Pro", internet: "online", uptime_s: 172800 },
      devices: {
        gateway: { online: 1, offline: 0, unknown: 0 },
        switch: { online: 2, offline: 0, unknown: 0 },
      },
      clients: { total: 42, wired: 12, wireless: 30 },
    } as never);
    await settle(siteEl);
    expect(siteEl.shadowRoot?.textContent).toContain("UDM-Pro");
    expect(siteEl.shadowRoot?.textContent).toContain("2d 0h");

    fake.subs[1]?.callback({
      throughput: { rx_bps: 45_000_000, tx_bps: 850_000 },
      windows: {
        "1h": { download_bytes: 500_000_000, upload_bytes: 100_000_000 },
        "1d": { download_bytes: 28_812_000_000, upload_bytes: 3_949_000_000 },
      },
      entity_ids: {
        download_1d: "sensor.internet_down_1d",
        upload_1d: "sensor.internet_up_1d",
      },
    } as never);
    await settle(internetEl);
    expect(internetEl.shadowRoot?.textContent).toContain("28.8 GB");
    expect(internetEl.shadowRoot?.textContent).toContain("45.0 Mbps");
    const tabBtn = Array.from(
      internetEl.shadowRoot?.querySelectorAll(".pill-tab") ?? [],
    ).find((btn) => btn.textContent?.trim() === "1h") as
      | HTMLButtonElement
      | undefined;
    tabBtn?.click();
    await settle(internetEl);
    expect(internetEl.shadowRoot?.textContent).toContain("500 MB");
    expect(internetEl.shadowRoot?.textContent).toContain("600 MB");

    fake.subs[2]?.callback({
      devices: [
        {
          name: "UDM-Pro",
          kind: "gateway",
          cpu_pct: 88,
          memory_pct: 72,
          clients: 14,
        },
        {
          name: "Office Switch",
          kind: "switch",
          cpu_pct: 65,
          memory_pct: 50,
        },
      ],
    } as never);
    await settle(perfEl);
    expect(perfEl.shadowRoot?.textContent).toContain("88%");
    expect(perfEl.shadowRoot?.textContent).toContain("RAM 72%");
  });

  it("handles subscribe errors, backoff, and entry_unloaded recovery", async () => {
    const fake = fakeHass();
    const el = document.createElement(
      SITE_HEALTH_CARD_TAG,
    ) as UnifiInsightsSiteHealthCard;
    el.setConfig({ type: `custom:${SITE_HEALTH_CARD_TAG}` });
    el.hass = fake.hass;
    document.body.append(el);
    await settle(el);

    // Trigger entry_unloaded issue from snapshot stream
    fake.subs[0]?.callback({
      issues: [{ code: "entry_unloaded" }],
    } as never);
    await settle(el);
    expect(fake.subs[0]?.unsubscribe).toHaveBeenCalledTimes(1);

    // Test subscribe failure and backoff early return
    const failingHass = fakeHass({
      subscribeError: { code: "sub_failed", message: "Subscription error" },
    });
    el.hass = failingHass.hass;
    await settle(el);
    expect(el.shadowRoot?.textContent).toContain("sub_failed");

    // Re-updating config within backoff window hits early-return retry scheduling
    el.setConfig({
      type: `custom:${SITE_HEALTH_CARD_TAG}`,
      title: "Updated Title",
    });
    await settle(el);
    expect(el.shadowRoot?.textContent).toContain("Updated Title");

    // Test callWS failure
    const wsFailHass = fakeHass({
      sourcesError: { code: "ws_error", message: "WS error" },
    });
    el.hass = wsFailHass.hass;
    await settle(el);
    expect(el.shadowRoot?.textContent).toContain("ws_error");
  });

  it("renders editors and emits config-changed for site and title updates", async () => {
    const fake = fakeHass();
    const editor = document.createElement(
      "unifi-insights-site-health-card-editor",
    ) as UnifiInsightsSiteHealthCardEditor;
    editor.setConfig({
      type: "custom:unifi-insights-site-health-card",
      title: "My Health",
    });
    editor.hass = fake.hass;
    document.body.append(editor);
    await settle(editor);

    const configs: Record<string, unknown>[] = [];
    editor.addEventListener("config-changed", ((ev: CustomEvent) => {
      configs.push(ev.detail.config);
    }) as EventListener);

    const select = editor.shadowRoot?.querySelector(
      "select",
    ) as HTMLSelectElement;
    select.value = "0";
    select.dispatchEvent(new Event("change"));
    expect(configs.at(-1)).toMatchObject({
      entry_id: "entry-1",
      site_id: "site-1",
    });

    select.value = "";
    select.dispatchEvent(new Event("change"));
    expect(configs.at(-1)?.entry_id).toBeUndefined();

    const input = editor.shadowRoot?.querySelector("input") as HTMLInputElement;
    input.value = "New Title";
    input.dispatchEvent(new Event("input"));
    expect(configs.at(-1)?.title).toBe("New Title");

    input.value = "   ";
    input.dispatchEvent(new Event("input"));
    expect(configs.at(-1)?.title).toBeUndefined();

    // Protect editor + error path
    const protectEditor = document.createElement(
      "unifi-insights-protect-status-card-editor",
    ) as UnifiInsightsProtectStatusCardEditor;
    protectEditor.setConfig({
      type: "custom:unifi-insights-protect-status-card",
    });
    protectEditor.hass = fakeHass({
      sourcesError: { code: "err", message: "Failed" },
    }).hass;
    document.body.append(protectEditor);
    await settle(protectEditor);
    expect(protectEditor.shadowRoot?.querySelectorAll("option")).toHaveLength(
      1,
    );
    expect(protectEditor.shadowRoot?.querySelector("label")?.textContent).toBe(
      "Integration",
    );
  });
});

describe("dashboard availability and accessible editors", () => {
  const names = [
    "site-health",
    "internet-activity",
    "performance",
    "protect-status",
    "timeline",
  ];
  async function companion(name: string, snapshot: Record<string, unknown>) {
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
    fake.subs[0]!.callback(snapshot);
    await settle(el);
    return el;
  }
  for (const name of names) {
    it(`${name} announces unavailable without healthy zero values`, async () => {
      const el = await companion(name, {
        status: "unavailable",
        issues: [{ code: "devices_unavailable", severity: "error" }],
      });
      expect(
        el.shadowRoot?.querySelector('[role="alert"]')?.textContent,
      ).toContain("temporarily unavailable");
      expect(
        el.shadowRoot?.querySelector(".kpi-grid, .ring-strip, .empty-hero"),
      ).toBeNull();
      expect(
        el.shadowRoot?.querySelector(".header-actions")?.textContent?.trim(),
      ).toBe("");
    });
    it(`${name} editor associates labels and emits full configuration`, async () => {
      const tag = `unifi-insights-${name}-card`;
      const editor = document.createElement(
        `${tag}-editor`,
      ) as import("../src/dashboard-cards-editor").GenericEditor;
      editor.setConfig({
        type: `custom:${tag}`,
        title: "Original",
        custom_option: true,
      });
      editor.hass = fakeHass().hass;
      document.body.append(editor);
      await settle(editor);
      for (const control of editor.shadowRoot!.querySelectorAll<
        HTMLInputElement | HTMLSelectElement
      >("input, select"))
        expect(control.labels?.length).toBe(1);
      let config: Record<string, unknown> | undefined;
      editor.addEventListener("config-changed", ((event: CustomEvent) => {
        config = event.detail.config;
      }) as EventListener);
      const input = editor.shadowRoot!.querySelector("input")!;
      input.value = "Updated";
      input.dispatchEvent(new Event("input"));
      expect(config).toMatchObject({
        type: `custom:${tag}`,
        title: "Updated",
        custom_option: true,
      });
    });
  }
  it("announces loading and actionable errors", async () => {
    const el = await companion("site-health", { status: "ok" });
    el.loading = true;
    await settle(el);
    expect(
      el.shadowRoot?.querySelector('[role="status"]')?.textContent,
    ).toContain("Loading Site Health");
    el.loading = false;
    el.error = { code: "entry_not_loaded", message: "" };
    await settle(el);
    expect(
      el.shadowRoot?.querySelector('[role="alert"]')?.textContent,
    ).toContain("Check the integration and connection");
    expect(el.shadowRoot?.querySelector(".state-detail")?.textContent).toBe(
      "entry_not_loaded",
    );
  });
  it("shows stale site health with neutral unknown accent", async () => {
    const el = await companion("site-health", {
      status: "partial",
      freshness: "stale",
      health: { level: "unknown" },
    });
    expect(
      el.shadowRoot?.querySelector('[role="status"]')?.textContent,
    ).toContain("last available readings");
    expect(
      el.shadowRoot?.querySelector("ha-card")?.getAttribute("style"),
    ).toContain("--uit-unknown");
  });
  it("distinguishes Protect not configured and degraded", async () => {
    const el = await companion("protect-status", {
      status: "no_protect",
      devices: [],
    });
    expect(
      el.shadowRoot?.querySelector('[role="status"]')?.textContent,
    ).toContain("not configured");
    el.snapshot = {
      status: "degraded",
      devices: [],
      warnings: ["storage_unhealthy"],
    };
    await settle(el);
    expect(
      el.shadowRoot?.querySelector('[role="status"]')?.textContent,
    ).toContain("storage unhealthy");
    expect(el.shadowRoot?.querySelector(".kpi-grid")).toBeNull();
    expect(
      el.shadowRoot?.querySelector(".header-actions")?.textContent?.trim(),
    ).toBe("");
    el.snapshot = {
      status: "degraded",
      devices: [{ name: "Door", connected: true }],
    };
    await settle(el);
    expect(el.shadowRoot?.querySelector(".chip.warning")).not.toBeNull();
    expect(el.shadowRoot?.querySelector(".kpi-grid")?.textContent).toContain(
      "Needs attention",
    );
    expect(
      el.shadowRoot?.querySelector(".kpi-grid")?.textContent,
    ).not.toContain("All connected");
  });
  it("excludes null performance values and preserves zero", async () => {
    const el = await companion("performance", {
      status: "partial",
      devices: [
        { cpu_pct: null, memory_pct: null },
        { cpu_pct: 60, memory_pct: 40 },
      ],
    });
    const gauges = () =>
      [...el.shadowRoot!.querySelectorAll(".ring-gauge span")].map(
        (e) => e.textContent,
      );
    expect(gauges()).toEqual(["60%", "40%"]);
    expect(el.shadowRoot?.querySelector(".header-actions .ok")).toBeNull();
    el.snapshot = {
      status: "ok",
      devices: [{ cpu_pct: null, memory_pct: null }],
    };
    await settle(el);
    expect(gauges()).toEqual(["--", "--"]);
    el.snapshot = { status: "ok", devices: [{ cpu_pct: 0, memory_pct: 0 }] };
    await settle(el);
    expect(gauges()).toEqual(["0%", "0%"]);
  });
  it("renders null traffic and missing rates as unknown", async () => {
    const el = await companion("internet-activity", {
      status: "ok",
      windows: { "1d": { download_bytes: null, upload_bytes: null } },
      throughput: { rx_bps: null, tx_bps: 100 },
    });
    expect(
      [...el.shadowRoot!.querySelectorAll(".kpi-value")].map(
        (e) => e.textContent,
      ),
    ).toEqual(["--", "--"]);
    expect(el.shadowRoot?.querySelector(".hero-meta")?.textContent).toContain(
      "↓ --",
    );
    expect(el.shadowRoot?.querySelector(".chip.ok")).toBeNull();
    el.snapshot = {
      status: "ok",
      windows: { "1d": { download_bytes: 0, upload_bytes: 0 } },
      throughput: { rx_bps: 0, tx_bps: 0 },
    };
    await settle(el);
    expect(el.shadowRoot?.querySelector(".chip.ok")?.textContent).toContain(
      "Live",
    );
    expect(el.shadowRoot?.querySelector(".kpi-sub")?.textContent).toContain(
      "0 MB",
    );
  });
  it("shows all quiet only for healthy empty timeline", async () => {
    const el = await companion("timeline", { status: "partial", items: [] });
    expect(el.shadowRoot?.querySelector(".empty-hero")).toBeNull();
    expect(el.shadowRoot?.querySelector(".content")?.textContent).toContain(
      "No recent events",
    );
    el.snapshot = { status: "ok", items: [] };
    await settle(el);
    expect(el.shadowRoot?.querySelector(".empty-hero")?.textContent).toContain(
      "All Quiet",
    );
  });
});
