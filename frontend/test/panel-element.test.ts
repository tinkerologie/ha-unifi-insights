import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { HomeAssistant, MessageBase } from "../src/ha-types";
import {
  WS_DEVICE_GROUPS_SOURCES,
  type DeviceGroupSourceEntry,
  type DeviceGroupsSnapshot,
} from "../src/panel/contract";
import "../src/panel/device-groups-panel";
import type { UnifiInsightsDevicesPanel } from "../src/panel/device-groups-panel";
import { cleanup, settle } from "./helpers";

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

interface FakeSub {
  message: MessageBase;
  callback: (message: unknown) => void;
  unsubscribe: ReturnType<typeof vi.fn>;
}

function fakePanelHass(
  opts: {
    entries?: DeviceGroupSourceEntry[];
    sourcesResponse?: unknown;
    sourcesError?: unknown;
    subscribeError?: unknown;
    language?: string;
  } = {},
) {
  const subs: FakeSub[] = [];
  const listeners = new Map<string, Set<() => void>>();
  let currentEntries = opts.entries ?? [
    { entry_id: "entry-1", title: "Home UDM" },
  ];
  let currentSourcesError = opts.sourcesError;
  let currentSubscribeError = opts.subscribeError;

  const connection = {
    addEventListener: vi.fn((event: string, listener: () => void) => {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event)!.add(listener);
    }),
    removeEventListener: vi.fn((event: string, listener: () => void) => {
      listeners.get(event)?.delete(listener);
    }),
    subscribeMessage: vi.fn(
      (callback: (m: unknown) => void, message: MessageBase) => {
        if (currentSubscribeError !== undefined)
          return Promise.reject(currentSubscribeError);
        const unsubscribe = vi.fn(async () => undefined);
        subs.push({ message, callback, unsubscribe });
        return Promise.resolve(unsubscribe);
      },
    ),
  };

  const hass = {
    connection,
    callWS: vi.fn(async (message: MessageBase) => {
      if (message.type === WS_DEVICE_GROUPS_SOURCES) {
        if (currentSourcesError !== undefined) throw currentSourcesError;
        if (opts.sourcesResponse !== undefined) return opts.sourcesResponse;
        return { schema_version: 1, entries: currentEntries };
      }
      throw new Error(`Unexpected WS call: ${message.type}`);
    }),
    locale: { language: opts.language ?? "en" },
  } as unknown as HomeAssistant;

  return {
    hass,
    subs,
    connection,
    push: (payload: unknown) => subs.at(-1)!.callback(payload),
    emit: (event: "ready" | "disconnected") =>
      listeners.get(event)?.forEach((listener) => listener()),
    setEntries: (next: DeviceGroupSourceEntry[]) => {
      currentEntries = next;
    },
    setSourcesError: (err: unknown) => {
      currentSourcesError = err;
    },
    setSubscribeError: (err: unknown) => {
      currentSubscribeError = err;
    },
  };
}

function sampleSnapshot(
  overrides: Partial<DeviceGroupsSnapshot> = {},
): DeviceGroupsSnapshot {
  return {
    schema_version: 1,
    entry_id: "entry-1",
    status: "ok",
    revision: "rev-1",
    stale: false,
    sections: [
      {
        key: "site:default",
        kind: "site",
        title: "Default",
        rows: [
          {
            key: "net:default:gw",
            name: "UDM Pro",
            kind: "gateway",
            ha_device_id: "dev/id with spaces",
            parent_key: null,
            port: null,
            reason: null,
          },
          {
            key: "net:default:sw",
            name: "Office switch",
            kind: "switch",
            ha_device_id: "sw-1",
            parent_key: "net:default:gw",
            port: 5,
            reason: null,
          },
          {
            key: "cam:c1",
            name: "Front door",
            kind: "camera",
            ha_device_id: "cam-1",
            parent_key: "net:default:sw",
            port: 3,
            reason: null,
          },
          {
            key: "net:default:ap",
            name: "Garage AP",
            kind: "access_point",
            ha_device_id: null,
            parent_key: null,
            port: null,
            reason: "historical",
          },
        ],
      },
      {
        key: "unassigned",
        kind: "unassigned",
        title: null,
        rows: [
          {
            key: "net:unassigned:1",
            name: "Spare Switch",
            kind: "network_device",
            ha_device_id: null,
            parent_key: null,
            port: null,
            reason: "no_uplink",
          },
        ],
      },
      {
        key: "other",
        kind: "other",
        title: null,
        rows: [
          {
            key: "other:1",
            name: "Unknown Box",
            kind: "future_gadget",
            ha_device_id: null,
            parent_key: null,
            port: null,
            reason: "future_reason",
          },
        ],
      },
    ],
    ...overrides,
  };
}

async function mountPanel(
  fake = fakePanelHass(),
  snapshot: DeviceGroupsSnapshot | undefined = sampleSnapshot(),
) {
  const el = document.createElement(
    "unifi-insights-devices-panel",
  ) as UnifiInsightsDevicesPanel;
  el.hass = fake.hass;
  el.narrow = false;
  el.route = { prefix: "/unifi-insights-devices", path: "" };
  el.panel = { config: {} };
  document.body.append(el);
  await settle(el);
  if (snapshot !== undefined && fake.subs.length > 0) {
    fake.push(snapshot);
    await settle(el);
  }
  return { el, fake };
}

async function audit(root: Element): Promise<string[]> {
  const result = await axe.run(root, {
    runOnly: {
      type: "tag",
      values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
    },
    rules: { "color-contrast": { enabled: false } },
  });
  return result.violations.map(
    (v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`,
  );
}

describe("<unifi-insights-devices-panel>", () => {
  it("hides entry picker for <=1 entry and shows empty state for 0 entries", async () => {
    const { el } = await mountPanel(fakePanelHass({ entries: [] }), undefined);
    expect(el.shadowRoot!.querySelector("select")).toBeNull();
    expect(el.shadowRoot!.textContent).toContain(
      "No UniFi Insights entries have device grouping enabled.",
    );
    expect(await audit(el)).toEqual([]);
  });

  it("shows entry picker only when >1 entries exist and switches subscriptions", async () => {
    const fake = fakePanelHass({
      entries: [
        { entry_id: "entry-1", title: "Home UDM" },
        { entry_id: "entry-2", title: "Office Gateway" },
      ],
    });
    const { el } = await mountPanel(fake);
    const select = el.shadowRoot!.querySelector("select")!;
    expect(select).not.toBeNull();
    expect([...select.options].map((o) => o.textContent?.trim())).toEqual([
      "Home UDM",
      "Office Gateway",
    ]);

    select.value = "entry-2";
    select.dispatchEvent(new Event("change"));
    await settle(el);
    expect(fake.subs).toHaveLength(2);
    expect(fake.subs[1]!.message).toEqual({
      type: "unifi_insights/device_groups/subscribe",
      entry_id: "entry-2",
    });
    // Old snapshot cleared until entry-2 pushes its own.
    expect(el.shadowRoot!.querySelectorAll('[role="treeitem"]')).toHaveLength(
      0,
    );
  });

  it("renders site, Unassigned, and Other devices sections and hides empty sections", async () => {
    const snap = sampleSnapshot();
    snap.sections[1]!.rows = []; // empty Unassigned should be hidden
    const { el } = await mountPanel(fakePanelHass(), snap);
    const headings = [...el.shadowRoot!.querySelectorAll(".section-title")].map(
      (h) => h.textContent?.trim(),
    );
    expect(headings).toEqual(["Default", "Other devices"]);
    expect(el.shadowRoot!.querySelectorAll('[role="tree"]')).toHaveLength(2);
  });

  it("renders linked rows with encoded device URLs and fires location-changed navigation", async () => {
    const { el } = await mountPanel();
    const pushSpy = vi.spyOn(history, "pushState");
    const events: CustomEvent[] = [];
    const listener = (e: Event) => events.push(e as CustomEvent);
    window.addEventListener("location-changed", listener);

    try {
      const link =
        el.shadowRoot!.querySelector<HTMLAnchorElement>("a.device-link")!;
      expect(link.getAttribute("href")).toBe(
        "/config/devices/device/dev%2Fid%20with%20spaces",
      );

      // Modified click should not call navigate.
      link.dispatchEvent(
        new MouseEvent("click", { bubbles: true, ctrlKey: true }),
      );
      expect(pushSpy).not.toHaveBeenCalled();

      // Plain click navigates via history.pushState + location-changed.
      link.click();
      expect(pushSpy).toHaveBeenCalledWith(
        null,
        "",
        "/config/devices/device/dev%2Fid%20with%20spaces",
      );
      expect(events).toHaveLength(1);
      expect(events[0]!.detail).toEqual({ replace: false });

      // Keyboard Enter on a linked row also navigates.
      const tree = el.shadowRoot!.querySelector('[role="tree"]')!;
      const rows = tree.querySelectorAll<HTMLElement>('[role="treeitem"]');
      rows[1]!.focus();
      tree.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
      );
      expect(pushSpy).toHaveBeenCalledWith(
        null,
        "",
        "/config/devices/device/sw-1",
      );
      expect(events).toHaveLength(2);
    } finally {
      window.removeEventListener("location-changed", listener);
      pushSpy.mockRestore();
    }
  });

  it("renders plain-text rows when ha_device_id is null and does not navigate on click", async () => {
    const { el } = await mountPanel();
    const pushSpy = vi.spyOn(history, "pushState");
    const apRow = el.shadowRoot!.querySelector<HTMLElement>(
      '[data-key="net:default:ap"]',
    )!;
    expect(apRow.querySelector("a")).toBeNull();
    expect(apRow.querySelector(".name")!.textContent).toBe("Garage AP");
    apRow.click();
    expect(pushSpy).not.toHaveBeenCalled();
    pushSpy.mockRestore();
  });

  it("renders Port N, all fallback reasons, and unknown fallback reason", async () => {
    const snap = sampleSnapshot();
    snap.sections = [
      {
        key: "site:r",
        kind: "site",
        title: "Reasons",
        rows: [
          {
            key: "r0",
            name: "P0",
            kind: "switch",
            ha_device_id: null,
            parent_key: null,
            port: 0,
            reason: "no_uplink",
          },
          {
            key: "r1",
            name: "P1",
            kind: "switch",
            ha_device_id: null,
            parent_key: null,
            port: null,
            reason: "historical",
          },
          {
            key: "r2",
            name: "P2",
            kind: "switch",
            ha_device_id: null,
            parent_key: null,
            port: null,
            reason: "ambiguous",
          },
          {
            key: "r3",
            name: "P3",
            kind: "switch",
            ha_device_id: null,
            parent_key: null,
            port: null,
            reason: "parent_not_found",
          },
          {
            key: "r4",
            name: "P4",
            kind: "switch",
            ha_device_id: null,
            parent_key: null,
            port: null,
            reason: "cycle",
          },
          {
            key: "r5",
            name: "P5",
            kind: "switch",
            ha_device_id: null,
            parent_key: null,
            port: null,
            reason: "stale",
          },
          {
            key: "r6",
            name: "P6",
            kind: "switch",
            ha_device_id: null,
            parent_key: null,
            port: null,
            reason: "mystery_code",
          },
        ],
      },
    ];
    const { el } = await mountPanel(fakePanelHass(), snap);
    const text = el.shadowRoot!.textContent!;
    expect(text).toContain("Port 0");
    expect(text).toContain("No uplink reported");
    expect(text).toContain("Last known uplink only");
    expect(text).toContain("Multiple possible uplinks");
    expect(text).toContain("Uplink device not found");
    expect(text).toContain("Uplink loop detected");
    expect(text).toContain("Uplink data stale");
    expect(text).toContain("Uplink unknown");
  });

  it("supports ARIA tree expand/collapse and keyboard navigation", async () => {
    const { el } = await mountPanel();
    const tree = el.shadowRoot!.querySelector<HTMLElement>(
      '[data-section="site:default"]',
    )!;
    const rows = () => [
      ...tree.querySelectorAll<HTMLElement>('[role="treeitem"]'),
    ];
    const key = (k: string) =>
      tree.dispatchEvent(
        new KeyboardEvent("keydown", { key: k, bubbles: true }),
      );

    expect(rows().map((r) => r.dataset.key)).toEqual([
      "net:default:gw",
      "net:default:sw",
      "cam:c1",
      "net:default:ap",
    ]);
    expect(rows()[0]!.getAttribute("aria-level")).toBe("1");
    expect(rows()[1]!.getAttribute("aria-level")).toBe("2");
    expect(rows()[1]!.getAttribute("aria-expanded")).toBe("true");
    expect(rows()[2]!.hasAttribute("aria-expanded")).toBe(false);

    // Click chevron to collapse sw
    rows()[1]!.querySelector<HTMLElement>(".chevron")!.click();
    await el.updateComplete;
    expect(rows().map((r) => r.dataset.key)).toEqual([
      "net:default:gw",
      "net:default:sw",
      "net:default:ap",
    ]);

    // Focus gw, ArrowDown -> sw, ArrowRight -> expand sw, ArrowRight -> cam:c1
    rows()[0]!.focus();
    key("ArrowDown");
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement?.getAttribute("data-key")).toBe(
      "net:default:sw",
    );
    key("ArrowRight");
    await el.updateComplete;
    expect(rows()).toHaveLength(4);
    key("ArrowRight");
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement?.getAttribute("data-key")).toBe(
      "cam:c1",
    );

    // ArrowLeft from cam:c1 -> parent sw; ArrowLeft on expanded sw -> collapses sw
    key("ArrowLeft");
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement?.getAttribute("data-key")).toBe(
      "net:default:sw",
    );
    key("ArrowLeft");
    await el.updateComplete;
    expect(rows()).toHaveLength(3);

    // End, Home, ArrowUp, type-ahead
    key("End");
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement?.getAttribute("data-key")).toBe(
      "net:default:ap",
    );
    key("Home");
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement?.getAttribute("data-key")).toBe(
      "net:default:gw",
    );
    key("o");
    await el.updateComplete;
    expect(el.shadowRoot!.activeElement?.getAttribute("data-key")).toBe(
      "net:default:sw",
    );
  });

  it("shows stale notice when snapshot.stale is true", async () => {
    const { el } = await mountPanel(
      fakePanelHass(),
      sampleSnapshot({ stale: true }),
    );
    expect(el.shadowRoot!.textContent).toContain("Data may be out of date.");
  });

  it("handles grouping_disabled status by showing notice and re-fetching sources without retrying", async () => {
    const fake = fakePanelHass();
    const { el } = await mountPanel(fake);
    fake.setEntries([]);
    fake.push({
      schema_version: 1,
      entry_id: "entry-1",
      status: "grouping_disabled",
      revision: "",
      stale: false,
      sections: [],
    });
    await settle(el);
    expect(el.shadowRoot!.textContent).toContain(
      "Device grouping is turned off for this integration.",
    );
    expect(fake.hass.callWS).toHaveBeenCalledTimes(2);
    expect(await audit(el)).toEqual([]);
  });

  it("handles grouping_disabled error code and switches to another enabled entry if one remains", async () => {
    const fake = fakePanelHass({
      entries: [
        { entry_id: "entry-1", title: "Home" },
        { entry_id: "entry-2", title: "Office" },
      ],
      subscribeError: { code: "grouping_disabled", message: "off" },
    });
    fake.setEntries([{ entry_id: "entry-2", title: "Office" }]);
    // First call returns both entries, second (after grouping_disabled) returns only entry-2.
    let callCount = 0;
    (fake.hass.callWS as ReturnType<typeof vi.fn>).mockImplementation(
      async () => {
        callCount += 1;
        if (callCount === 1) {
          return {
            schema_version: 1,
            entries: [
              { entry_id: "entry-1", title: "Home" },
              { entry_id: "entry-2", title: "Office" },
            ],
          };
        }
        fake.setSubscribeError(undefined);
        return {
          schema_version: 1,
          entries: [{ entry_id: "entry-2", title: "Office" }],
        };
      },
    );

    const { el } = await mountPanel(fake, undefined);
    expect(fake.subs.at(-1)!.message.entry_id).toBe("entry-2");
    expect(el.shadowRoot!.querySelector(".notice")).toBeNull();
  });

  it("handles entry_unloaded status with reconnecting notice and retry", async () => {
    vi.useFakeTimers();
    const fake = fakePanelHass();
    const el = document.createElement(
      "unifi-insights-devices-panel",
    ) as UnifiInsightsDevicesPanel;
    el.hass = fake.hass;
    document.body.append(el);
    await vi.advanceTimersByTimeAsync(10);
    await el.updateComplete;

    fake.push({
      schema_version: 1,
      entry_id: "entry-1",
      status: "entry_unloaded",
      revision: "",
      stale: false,
      sections: [],
    });
    await el.updateComplete;
    expect(el.shadowRoot!.textContent).toContain(
      "Integration is reloading. Reconnecting…",
    );

    await vi.advanceTimersByTimeAsync(3000);
    expect(fake.connection.subscribeMessage).toHaveBeenCalledTimes(2);
  });

  it("shows incompatible version message on snapshot or sources schema mismatch", async () => {
    const { el } = await mountPanel(fakePanelHass(), {
      ...sampleSnapshot(),
      schema_version: 99,
    });
    expect(el.shadowRoot!.textContent).toContain(
      "Incompatible panel version. Reload the page to continue.",
    );
    expect(await audit(el)).toEqual([]);

    cleanup();
    const badSources = fakePanelHass({
      sourcesResponse: { schema_version: 2, entries: [] },
    });
    const { el: el2 } = await mountPanel(badSources, undefined);
    expect(el2.shadowRoot!.textContent).toContain(
      "Incompatible panel version. Reload the page to continue.",
    );
  });

  it("passes WCAG 2.2 AA axe-core checks on a loaded multi-entry stale panel", async () => {
    const fake = fakePanelHass({
      entries: [
        { entry_id: "entry-1", title: "Home UDM" },
        { entry_id: "entry-2", title: "Office UDM" },
      ],
    });
    const { el } = await mountPanel(fake, sampleSnapshot({ stale: true }));
    expect(await audit(el)).toEqual([]);
  });

  it("handles disconnect/reconnect, empty devices, and entry_not_found errors", async () => {
    const fake = fakePanelHass();
    const { el } = await mountPanel(fake);

    fake.emit("disconnected");
    await settle(el);
    expect(el.shadowRoot!.textContent).toContain(
      "Connection lost. Reconnecting…",
    );

    fake.emit("ready");
    await settle(el);
    fake.push(
      sampleSnapshot({
        revision: "rev-empty",
        sections: [
          { key: "unassigned", kind: "unassigned", title: null, rows: [] },
        ],
      }),
    );
    await settle(el);
    expect(el.shadowRoot!.textContent).toContain(
      "No devices found for this integration.",
    );

    cleanup();
    const errFake = fakePanelHass({
      subscribeError: { code: "entry_not_found", message: "gone" },
    });
    const { el: errEl } = await mountPanel(errFake, undefined);
    expect(errEl.shadowRoot!.textContent).toContain(
      "This integration entry is no longer available.",
    );
  });

  it("skips unrelated hass updates and re-localizes when language changes", async () => {
    const fake = fakePanelHass();
    const { el } = await mountPanel(fake);
    const spy = vi.spyOn(
      el as unknown as { willUpdate: () => void },
      "willUpdate",
    );

    // Same connection and language: shouldUpdate returns false.
    el.hass = { ...fake.hass };
    await el.updateComplete;
    expect(spy).not.toHaveBeenCalled();

    // Language change triggers update.
    el.hass = { ...fake.hass, locale: { language: "de-DE" } };
    await el.updateComplete;
    expect(spy).toHaveBeenCalledOnce();
    spy.mockRestore();
  });
});
