import { mdiChip } from "@mdi/js";
import { html, nothing, type TemplateResult } from "lit";

import { WS_SOURCES } from "./contract";
import {
  BaseDashboardCard,
  GenericEditor,
  PERFORMANCE_CARD_TAG,
  PERFORMANCE_EDITOR_TAG,
  kindIcon,
  metricTone,
} from "./dashboard-cards-base";
import { iconTemplate } from "./icons";
import { registerDashboardCard } from "./register-dashboard-card";

export class UnifiInsightsPerformanceCard extends BaseDashboardCard {
  static override editorTag = PERFORMANCE_EDITOR_TAG;

  protected get cardType(): string {
    return `custom:${PERFORMANCE_CARD_TAG}`;
  }

  protected get sourceCommand(): string {
    return WS_SOURCES;
  }

  protected get subscribeCommand(): string {
    return "unifi_insights/performance/subscribe";
  }

  protected get defaultTitle(): string {
    return "Device Performance";
  }

  protected get headerIcon(): string {
    return mdiChip;
  }

  protected override renderHeaderBadge(): TemplateResult | typeof nothing {
    const devices = Array.isArray(this.snapshot?.devices)
      ? (this.snapshot.devices as Record<string, unknown>[])
      : [];
    if (!this.snapshot) return nothing;
    return html`<span
      class="chip ${this.snapshot.status === "partial" ? "partial" : ""}"
      >${devices.length} Devices</span
    >`;
  }

  protected renderContent() {
    const snapshot = this.snapshot;
    if (!snapshot) {
      return html`<div class="state">No devices</div>`;
    }
    const devices = Array.isArray(snapshot.devices)
      ? (snapshot.devices as Record<string, unknown>[])
      : [];
    const cpuValues = devices
      .map((d) => d.cpu_pct)
      .filter(
        (n): n is number =>
          typeof n === "number" && Number.isFinite(n) && n >= 0,
      );
    const memValues = devices
      .map((d) => d.memory_pct)
      .filter(
        (n): n is number =>
          typeof n === "number" && Number.isFinite(n) && n >= 0,
      );
    const avgCpu =
      cpuValues.length > 0
        ? Math.round(cpuValues.reduce((a, b) => a + b, 0) / cpuValues.length)
        : undefined;
    const avgMem =
      memValues.length > 0
        ? Math.round(memValues.reduce((a, b) => a + b, 0) / memValues.length)
        : undefined;

    return html`
      <div class="ring-strip">
        <div class="ring-card">
          <div
            class="ring-gauge"
            style=${`--pct: ${avgCpu ?? 0}; --ring-color: var(--uit-${avgCpu === undefined ? "unknown" : metricTone(avgCpu) === "ok" ? "online" : metricTone(avgCpu) === "warning" ? "warning" : "offline"})`}
          >
            <span>${avgCpu === undefined ? "--" : `${avgCpu}%`}</span>
          </div>
          <div>
            <div class="kpi-top">Avg CPU</div>
            <div class="kpi-sub">${devices.length} Devices</div>
          </div>
        </div>
        <div class="ring-card">
          <div
            class="ring-gauge"
            style=${`--pct: ${avgMem ?? 0}; --ring-color: var(--primary-color)`}
          >
            <span>${avgMem === undefined ? "--" : `${avgMem}%`}</span>
          </div>
          <div>
            <div class="kpi-top">Avg Memory</div>
            <div class="kpi-sub">System Load</div>
          </div>
        </div>
      </div>

      <div
        class="list"
        tabindex="0"
        role="region"
        aria-label="Device performance"
      >
        ${devices.slice(0, 6).map((device) => {
          const name = String(device.name ?? "Device");
          const kind = String(device.kind ?? "other");
          const raw = Number(device.cpu_pct);
          const cpu =
            device.cpu_pct == null || !Number.isFinite(raw)
              ? null
              : Math.min(100, Math.max(0, Math.round(raw)));
          const mem =
            device.memory_pct == null
              ? null
              : Math.round(Number(device.memory_pct));
          const tone = cpu == null ? "ok" : metricTone(cpu);
          const clients =
            typeof device.clients === "number"
              ? `${device.clients} clients`
              : undefined;
          return html`
            <div class="item-card">
              <div class="item-icon">${iconTemplate(kindIcon(kind))}</div>
              <div class="item-body">
                <div class="item-top">
                  <span class="item-name">${name}</span>
                  <span class="item-value">
                    ${cpu == null ? "--" : `${cpu}%`}
                  </span>
                </div>
                <div class="bar-track" aria-hidden="true">
                  <div
                    class="bar-fill ${tone}"
                    style=${`width: ${cpu ?? 0}%`}
                  ></div>
                </div>
                ${mem != null || clients
                  ? html`<div class="item-meta">
                      ${mem != null ? html`<span>RAM ${mem}%</span>` : nothing}
                      ${clients ? html`<span>· ${clients}</span>` : nothing}
                    </div>`
                  : nothing}
              </div>
            </div>
          `;
        })}
      </div>
    `;
  }
}

export class UnifiInsightsPerformanceCardEditor extends GenericEditor {
  constructor() {
    super(PERFORMANCE_CARD_TAG, WS_SOURCES);
  }
}

registerDashboardCard({
  tag: PERFORMANCE_CARD_TAG,
  editorTag: PERFORMANCE_EDITOR_TAG,
  card: UnifiInsightsPerformanceCard,
  editor: UnifiInsightsPerformanceCardEditor,
  name: "UniFi Device Performance",
  description:
    "Infrastructure CPU, memory, PoE, client load, and throughput summary.",
});

export { PERFORMANCE_CARD_TAG, PERFORMANCE_EDITOR_TAG };
