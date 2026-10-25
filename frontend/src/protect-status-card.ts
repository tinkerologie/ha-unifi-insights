import { mdiAlertCircleOutline, mdiCctv } from "@mdi/js";
import { html, nothing, type TemplateResult } from "lit";

import { type SiteBinding } from "./config";
import {
  BaseDashboardCard,
  GenericEditor,
  PROTECT_CARD_TAG,
  PROTECT_EDITOR_TAG,
  kindIcon,
  type ProtectSource,
} from "./dashboard-cards-base";
import { iconTemplate } from "./icons";
import { registerDashboardCard } from "./register-dashboard-card";

export class UnifiInsightsProtectStatusCard extends BaseDashboardCard {
  static override editorTag = PROTECT_EDITOR_TAG;

  protected get cardType(): string {
    return `custom:${PROTECT_CARD_TAG}`;
  }

  protected get sourceCommand(): string {
    return "unifi_insights/protect/sources";
  }

  protected get subscribeCommand(): string {
    return "unifi_insights/protect/subscribe";
  }

  protected get defaultTitle(): string {
    return "Protect Status";
  }

  protected get headerIcon(): string {
    return mdiCctv;
  }

  protected override get includeSiteInSubscribeMessage(): boolean {
    return false;
  }

  protected override getBinding(): SiteBinding | undefined {
    const entryId = this.config.entry_id;
    if (entryId) {
      return { entry_id: entryId, site_id: this.config.site_id ?? "*" };
    }
    if (this.sources.length !== 1) {
      return undefined;
    }
    const first = this.sources[0] as ProtectSource;
    return { entry_id: first.entry_id, site_id: this.config.site_id ?? "*" };
  }

  protected override renderHeaderBadge(): TemplateResult | typeof nothing {
    if (!this.snapshot) return nothing;
    const devices = Array.isArray(this.snapshot.devices)
      ? (this.snapshot.devices as Record<string, unknown>[])
      : [];
    if (this.snapshot.status === "degraded" && devices.length === 0)
      return nothing;
    const offline = devices.filter((d) => d.connected === false).length;
    const online = devices.length - offline;
    return html`
      <span
        class="chip ${offline > 0 || this.snapshot.status === "degraded"
          ? "warning"
          : "ok"}"
      >
        <span class="status-dot"></span>
        ${online}/${devices.length} Online
      </span>
    `;
  }

  protected renderContent() {
    const snapshot = this.snapshot;
    if (!snapshot) {
      return html`<div class="state">No Protect data</div>`;
    }
    const devices = Array.isArray(snapshot.devices)
      ? (snapshot.devices as Record<string, unknown>[])
      : [];
    if (snapshot.status === "degraded" && devices.length === 0) return nothing;
    const offline = devices.filter(
      (device) => device.connected === false,
    ).length;
    const online = devices.length - offline;

    return html`
      <div class="kpi-grid">
        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Devices</span>
            ${iconTemplate(mdiCctv)}
          </div>
          <div class="kpi-value">${devices.length}</div>
          <div class="kpi-sub">${online} active</div>
        </div>
        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Offline</span>
            ${iconTemplate(mdiAlertCircleOutline)}
          </div>
          <div class="kpi-value">${offline}</div>
          <div class="kpi-sub">
            ${offline === 0 && snapshot.status !== "degraded"
              ? "All connected"
              : "Needs attention"}
          </div>
        </div>
      </div>

      <div class="list">
        ${devices.slice(0, 6).map((device) => {
          const entityId =
            typeof device.camera_entity_id === "string"
              ? device.camera_entity_id
              : undefined;
          const name = String(device.name ?? "Device");
          const kind = String(device.kind ?? "device");
          const isOnline = device.connected !== false;
          const stateText = isOnline ? "online" : "offline";
          const label = `${name} · ${kind}`;
          return html`
            <div class="item-card">
              <div class="item-icon ${stateText}">
                ${iconTemplate(kindIcon(kind))}
              </div>
              <div class="item-body">
                <div class="item-top">
                  ${entityId
                    ? html`<button
                        type="button"
                        class="link item-name"
                        @click=${() => this.openMoreInfo(entityId)}
                      >
                        ${label}
                      </button>`
                    : html`<span class="item-name"> ${label} </span>`}
                  <span class="chip ${stateText}">
                    <span class="status-dot"></span>
                    ${stateText}
                  </span>
                </div>
                ${device.is_recording || device.motion_active
                  ? html`<div class="item-meta">
                      ${device.is_recording && isOnline
                        ? html`<span>● Recording</span>`
                        : nothing}
                      ${device.motion_active
                        ? html`<span>· Motion detected</span>`
                        : nothing}
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

export class UnifiInsightsProtectStatusCardEditor extends GenericEditor {
  constructor() {
    super(PROTECT_CARD_TAG, "unifi_insights/protect/sources");
  }
}

registerDashboardCard({
  tag: PROTECT_CARD_TAG,
  editorTag: PROTECT_EDITOR_TAG,
  card: UnifiInsightsProtectStatusCard,
  editor: UnifiInsightsProtectStatusCardEditor,
  name: "UniFi Protect Status",
  description:
    "Live UniFi Protect camera, doorbell, chime, and NVR status summary.",
});

export { PROTECT_CARD_TAG, PROTECT_EDITOR_TAG };
