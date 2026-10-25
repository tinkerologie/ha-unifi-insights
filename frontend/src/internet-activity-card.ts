import {
  mdiArrowDownBold,
  mdiArrowUpBold,
  mdiEarth,
  mdiSpeedometer,
} from "@mdi/js";
import { html, nothing, type TemplateResult } from "lit";

import { WS_SOURCES } from "./contract";
import {
  BaseDashboardCard,
  GenericEditor,
  INTERNET_ACTIVITY_CARD_TAG,
  INTERNET_ACTIVITY_EDITOR_TAG,
  formatBps,
  formatBytesCompact,
} from "./dashboard-cards-base";
import { iconTemplate } from "./icons";
import { registerDashboardCard } from "./register-dashboard-card";

export class UnifiInsightsInternetActivityCard extends BaseDashboardCard {
  static override editorTag = INTERNET_ACTIVITY_EDITOR_TAG;

  static override properties = {
    ...BaseDashboardCard.properties,
    selectedWindow: { state: true },
  };

  declare selectedWindow: string;

  constructor() {
    super();
    this.selectedWindow = "1d";
  }

  protected get cardType(): string {
    return `custom:${INTERNET_ACTIVITY_CARD_TAG}`;
  }

  protected get sourceCommand(): string {
    return WS_SOURCES;
  }

  protected get subscribeCommand(): string {
    return "unifi_insights/internet_activity/subscribe";
  }

  protected get defaultTitle(): string {
    return "Internet Activity";
  }

  protected get headerIcon(): string {
    return mdiSpeedometer;
  }

  private resolveActiveWindow(windows: Record<string, unknown>): {
    keys: string[];
    activeWindow: string;
  } {
    const keys = ["1h", "1d", "1w", "1m"].filter((k) => k in windows);
    const activeWindow =
      this.selectedWindow in windows ? this.selectedWindow : (keys[0] ?? "1d");
    return { keys, activeWindow };
  }

  protected override renderHeaderBadge(): TemplateResult | typeof nothing {
    const windows = (this.snapshot?.windows ?? {}) as Record<string, unknown>;
    const { keys, activeWindow } = this.resolveActiveWindow(windows);
    if (keys.length <= 1) {
      return nothing;
    }
    return html`
      <div class="pill-tabs" role="group" aria-label="Time window">
        ${keys.map(
          (w) => html`
            <button
              type="button"
              class="pill-tab"
              aria-pressed=${String(activeWindow === w)}
              @click=${() => {
                this.selectedWindow = w;
              }}
            >
              ${w}
            </button>
          `,
        )}
      </div>
    `;
  }

  protected renderContent() {
    const snapshot = this.snapshot;
    if (!snapshot) {
      return html`<div class="state">No data</div>`;
    }
    const windows = (snapshot.windows ?? {}) as Record<string, unknown>;
    const { activeWindow } = this.resolveActiveWindow(windows);
    const selected = (windows[activeWindow] ??
      windows["1d"] ??
      windows["1h"] ??
      {}) as Record<string, unknown>;
    const validBytes = (value: unknown): number | undefined =>
      typeof value === "number" && Number.isFinite(value) && value >= 0
        ? value
        : undefined;
    const down = validBytes(selected.download_bytes);
    const up = validBytes(selected.upload_bytes);
    const total =
      down !== undefined && up !== undefined ? down + up : undefined;
    const asMb = (value: number | undefined) =>
      value === undefined ? "--" : `${Math.round(value / 1_000_000)} MB`;
    const compact = (value: number | undefined) =>
      value === undefined ? "--" : formatBytesCompact(value);
    const downMb = asMb(down);
    const upMb = asMb(up);
    const totalMb = asMb(total);
    const downPct =
      total !== undefined && down !== undefined
        ? total > 0
          ? Math.round((down / total) * 100)
          : 0
        : undefined;
    const upPct =
      total !== undefined && up !== undefined
        ? total > 0
          ? 100 - (downPct ?? 0)
          : 0
        : undefined;

    const throughput = (snapshot.throughput ?? {}) as Record<string, unknown>;
    const rxRate = formatBps(throughput.rx_bps);
    const txRate = formatBps(throughput.tx_bps);
    const entityIds = (snapshot.entity_ids ?? {}) as Record<string, string>;
    const downEntity = entityIds[`download_${activeWindow}`];
    const upEntity = entityIds[`upload_${activeWindow}`];

    return html`
      ${rxRate || txRate
        ? html`<div class="hero-banner">
            <div class="hero-left">
              ${iconTemplate(mdiEarth)}
              <div>
                <div class="hero-title">
                  ${rxRate && txRate ? "Live Throughput" : "Throughput"}
                </div>
                <div class="hero-meta">
                  ↓ ${rxRate ?? "--"} · ↑ ${txRate ?? "--"}
                </div>
              </div>
            </div>
            ${rxRate && txRate
              ? html`<span class="chip ok">
                  <span class="status-dot"></span>Live
                </span>`
              : nothing}
          </div>`
        : nothing}

      <div class="kpi-grid">
        <button
          type="button"
          class="kpi-tile ${downEntity ? "clickable" : ""}"
          ?disabled=${!downEntity}
          @click=${downEntity ? () => this.openMoreInfo(downEntity) : nothing}
        >
          <div class="kpi-top">
            <span>Download</span>
            ${iconTemplate(mdiArrowDownBold)}
          </div>
          <div class="kpi-value">${compact(down)}</div>
          <div class="kpi-sub">
            ${downMb} · ${downPct === undefined ? "--" : `${downPct}%`}
          </div>
        </button>

        <button
          type="button"
          class="kpi-tile ${upEntity ? "clickable" : ""}"
          ?disabled=${!upEntity}
          @click=${upEntity ? () => this.openMoreInfo(upEntity) : nothing}
        >
          <div class="kpi-top">
            <span>Upload</span>
            ${iconTemplate(mdiArrowUpBold)}
          </div>
          <div class="kpi-value">${compact(up)}</div>
          <div class="kpi-sub">
            ${upMb} · ${upPct === undefined ? "--" : `${upPct}%`}
          </div>
        </button>
      </div>

      <div class="kpi-tile">
        <div class="item-top">
          <span class="kpi-top">Total Traffic (${activeWindow})</span>
          <span class="item-value"> ${compact(total)} (${totalMb}) </span>
        </div>
        <div class="bar-track" aria-hidden="true">
          <div class="bar-fill" style=${`width: ${downPct ?? 0}%`}></div>
          <div
            class="bar-fill secondary"
            style=${`width: ${upPct ?? 0}%`}
          ></div>
        </div>
      </div>
    `;
  }
}

export class UnifiInsightsInternetActivityCardEditor extends GenericEditor {
  constructor() {
    super(INTERNET_ACTIVITY_CARD_TAG, WS_SOURCES);
  }
}

registerDashboardCard({
  tag: INTERNET_ACTIVITY_CARD_TAG,
  editorTag: INTERNET_ACTIVITY_EDITOR_TAG,
  card: UnifiInsightsInternetActivityCard,
  editor: UnifiInsightsInternetActivityCardEditor,
  name: "UniFi Internet Activity",
  description:
    "Historical WAN download/upload activity and live gateway throughput.",
});

export { INTERNET_ACTIVITY_CARD_TAG, INTERNET_ACTIVITY_EDITOR_TAG };
