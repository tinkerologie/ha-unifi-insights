import { mdiBellRingOutline, mdiCctv, mdiDoorbellVideo } from "@mdi/js";
import { LitElement, css, html } from "lit";

import { allSites } from "./config";
import { type TopologySource } from "./contract";
import { fireEvent, type HomeAssistant } from "./ha-types";
import { controlStyles, themeTokens } from "./styles";
import {
  mdiAccessPoint,
  mdiDevices,
  mdiRouterNetwork,
  mdiSwitch,
} from "./icons";

export const SITE_HEALTH_CARD_TAG = "unifi-insights-site-health-card";
export const SITE_HEALTH_EDITOR_TAG = "unifi-insights-site-health-card-editor";
export const INTERNET_ACTIVITY_CARD_TAG =
  "unifi-insights-internet-activity-card";
export const INTERNET_ACTIVITY_EDITOR_TAG =
  "unifi-insights-internet-activity-card-editor";
export const PERFORMANCE_CARD_TAG = "unifi-insights-performance-card";
export const PERFORMANCE_EDITOR_TAG = "unifi-insights-performance-card-editor";
export const PROTECT_CARD_TAG = "unifi-insights-protect-status-card";
export const PROTECT_EDITOR_TAG = "unifi-insights-protect-status-card-editor";
export const TIMELINE_CARD_TAG = "unifi-insights-timeline-card";
export const TIMELINE_EDITOR_TAG = "unifi-insights-timeline-card-editor";

export interface DashboardCardConfig {
  type: string;
  title?: string;
  entry_id?: string;
  site_id?: string;
  show_site_selector?: boolean;
  [key: string]: unknown;
}

export interface ProtectSource {
  entry_id: string;
  title: string;
  camera_count: number;
  sites: { id: string; name: string }[];
}

export function formatBytesCompact(bytes: number): string {
  const mb = Math.round(bytes / 1_000_000);
  if (bytes >= 1_000_000_000) {
    return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
  }
  return `${mb} MB`;
}

export function formatBps(bps: unknown): string | undefined {
  if (typeof bps !== "number" || !Number.isFinite(bps) || bps < 0)
    return undefined;
  if (bps >= 1_000_000_000) return `${(bps / 1_000_000_000).toFixed(1)} Gbps`;
  if (bps >= 1_000_000) return `${(bps / 1_000_000).toFixed(1)} Mbps`;
  if (bps >= 1_000) return `${Math.round(bps / 1_000)} Kbps`;
  return `${Math.round(bps)} bps`;
}

export function formatUptime(seconds: unknown): string | undefined {
  if (
    typeof seconds !== "number" ||
    !Number.isFinite(seconds) ||
    seconds <= 0
  ) {
    return undefined;
  }
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return `${days}d ${hours}h`;
  const mins = Math.max(1, Math.floor((seconds % 3600) / 60));
  return hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
}

export function formatRelativeTimestamp(iso: unknown): string {
  if (typeof iso !== "string" || !iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const diffMinutes = Math.max(
    0,
    Math.round((Date.now() - date.getTime()) / 60000),
  );
  if (diffMinutes < 1) return "Just now";
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  const hours = Math.floor(diffMinutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function kindIcon(kind: unknown): string {
  switch (String(kind ?? "")) {
    case "gateway":
      return mdiRouterNetwork;
    case "switch":
      return mdiSwitch;
    case "access_point":
      return mdiAccessPoint;
    case "camera":
      return mdiCctv;
    case "doorbell":
      return mdiDoorbellVideo;
    case "chime":
    case "ring":
      return mdiBellRingOutline;
    default:
      return mdiDevices;
  }
}

export function metricTone(pct: number): string {
  if (pct >= 85) return "critical";
  if (pct >= 60) return "warning";
  return "ok";
}

export class GenericEditor extends LitElement {
  static override properties = {
    hass: { attribute: false },
    config: { state: true },
  };

  declare hass?: HomeAssistant;
  declare config: DashboardCardConfig;

  protected cardType: string;
  protected sourceCommand: string;
  protected options: Array<{ entryId: string; siteId: string; label: string }>;
  private optionsRequested = false;

  constructor(cardType: string, sourceCommand: string) {
    super();
    this.cardType = cardType;
    this.sourceCommand = sourceCommand;
    this.config = { type: `custom:${cardType}` };
    this.options = [];
  }

  setConfig(config: DashboardCardConfig): void {
    this.config = { ...config };
  }

  protected override willUpdate(): void {
    if (this.hass && !this.optionsRequested) {
      this.optionsRequested = true;
      void this.loadOptions();
    }
  }

  private async loadOptions(): Promise<void> {
    if (!this.hass) return;
    try {
      const payload = await this.hass.callWS({ type: this.sourceCommand });
      this.options =
        this.sourceCommand === "unifi_insights/protect/sources"
          ? (payload as ProtectSource[]).map((s) => ({
              entryId: s.entry_id,
              siteId: "*",
              label: s.title,
            }))
          : allSites(payload as TopologySource[]).map((site) => ({
              entryId: site.binding.entry_id,
              siteId: site.binding.site_id,
              label: site.label,
            }));
      this.requestUpdate();
    } catch {
      this.options = [];
      this.requestUpdate();
    }
  }

  private applySite(value: string): void {
    const next = {
      ...this.config,
      type: this.config.type || `custom:${this.cardType}`,
    };
    if (!value) {
      delete next.entry_id;
      delete next.site_id;
    } else {
      const option = this.options[Number.parseInt(value, 10)];
      if (option) {
        next.entry_id = option.entryId;
        next.site_id = option.siteId;
      }
    }
    this.config = next;
    fireEvent(this, "config-changed", { config: next });
  }

  private applyTitle(value: string): void {
    const next = { ...this.config };
    if (value.trim()) next.title = value.trim();
    else delete next.title;
    this.config = next;
    fireEvent(this, "config-changed", { config: next });
  }

  override render() {
    const selected =
      this.config.entry_id && this.config.site_id
        ? String(
            this.options.findIndex(
              (o) =>
                o.entryId === this.config.entry_id &&
                o.siteId === this.config.site_id,
            ),
          )
        : "";
    return html`
      <div class="editor">
        <label for="source"
          >${this.sourceCommand === "unifi_insights/protect/sources"
            ? "Integration"
            : "Site"}</label
        >
        <select
          id="source"
          @change=${(ev: Event) =>
            this.applySite((ev.target as HTMLSelectElement).value)}
        >
          <option value="">Auto</option>
          ${this.options.map((option, index) => {
            const value = String(index);
            return html`<option value=${value} ?selected=${value === selected}>
              ${option.label}
            </option>`;
          })}
        </select>
        <label for="title">Title</label>
        <input
          id="title"
          .value=${this.config.title ?? ""}
          @input=${(ev: Event) =>
            this.applyTitle((ev.target as HTMLInputElement).value)}
        />
      </div>
    `;
  }

  static override styles = [
    themeTokens,
    controlStyles,
    css`
      :host {
        display: block;
        min-width: 0;
      }
      .editor {
        display: grid;
        gap: 8px;
      }
      label {
        font-size: 0.85rem;
        color: var(--secondary-text-color);
      }
      select,
      input {
        min-height: 44px;
        width: 100%;
        min-width: 0;
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        padding: 4px 8px;
        background: var(--card-background-color);
        color: var(--primary-text-color);
      }
    `,
  ];
}
