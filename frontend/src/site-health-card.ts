import { mdiEarth, mdiShieldCheckOutline } from "@mdi/js";
import { html, nothing, type TemplateResult } from "lit";

import { WS_SOURCES } from "./contract";
import {
  BaseDashboardCard,
  GenericEditor,
  SITE_HEALTH_CARD_TAG,
  SITE_HEALTH_EDITOR_TAG,
  formatUptime,
} from "./dashboard-cards-base";
import { iconTemplate, mdiDevices, mdiRouterNetwork, mdiWifi } from "./icons";
import { registerDashboardCard } from "./register-dashboard-card";

export class UnifiInsightsSiteHealthCard extends BaseDashboardCard {
  static override editorTag = SITE_HEALTH_EDITOR_TAG;

  protected get cardType(): string {
    return `custom:${SITE_HEALTH_CARD_TAG}`;
  }

  protected get sourceCommand(): string {
    return WS_SOURCES;
  }

  protected get subscribeCommand(): string {
    return "unifi_insights/site_health/subscribe";
  }

  protected get defaultTitle(): string {
    return "Site Health";
  }

  protected get headerIcon(): string {
    return mdiShieldCheckOutline;
  }

  protected override get headerAccent(): string {
    const level = String(
      (this.snapshot?.health as Record<string, unknown> | undefined)?.level ??
        "unknown",
    );
    if (level === "critical") return "var(--uit-offline)";
    if (level === "degraded") return "var(--uit-warning)";
    return level === "healthy" ? "var(--uit-online)" : "var(--uit-unknown)";
  }

  protected override renderHeaderBadge(): TemplateResult | typeof nothing {
    if (!this.snapshot) return nothing;
    const health = (this.snapshot.health ?? {}) as Record<string, unknown>;
    const level = String(health.level ?? "unknown");
    return html`<span class="chip ${level}">
      <span class="status-dot"></span>
      <span>${level}</span>
    </span>`;
  }

  protected renderContent() {
    const snapshot = this.snapshot;
    if (!snapshot) {
      return html`<div class="state">Choose site.</div>`;
    }
    const health = (snapshot.health ?? {}) as Record<string, unknown>;
    const gateway = (snapshot.gateway ?? {}) as Record<string, unknown>;
    const clients = (snapshot.clients ?? {}) as Record<string, unknown>;
    const devicesByKind = (snapshot.devices ?? {}) as Record<
      string,
      Record<string, number>
    >;
    const level = String(health.level ?? "unknown");
    const internet = String(gateway.internet ?? "unknown");
    const totalClients = Number(clients.total ?? 0);
    const wiredClients = Number(clients.wired ?? 0);
    const wirelessClients = Number(clients.wireless ?? 0);
    const wirelessPct =
      totalClients > 0
        ? Math.round((wirelessClients / totalClients) * 100)
        : 50;

    let onlineDevices = 0;
    let totalDevices = 0;
    for (const counts of Object.values(devicesByKind)) {
      if (counts && typeof counts === "object") {
        const on = Number(counts.online ?? 0);
        const off = Number(counts.offline ?? 0);
        const unk = Number(counts.unknown ?? 0);
        onlineDevices += on;
        totalDevices += on + off + unk;
      }
    }
    const gwName =
      typeof gateway.name === "string" && gateway.name
        ? gateway.name
        : "UniFi Gateway";
    const uptime = formatUptime(gateway.uptime_s);

    return html`
      <div class="hero-banner">
        <div class="hero-left">
          ${iconTemplate(mdiRouterNetwork)}
          <div>
            <div class="hero-title">${gwName}</div>
            <div class="hero-meta">
              Status: ${level}${uptime ? ` · Uptime ${uptime}` : ""}
            </div>
          </div>
        </div>
        <span class="chip ${internet === "online" ? "ok" : "warning"}">
          <span class="status-dot"></span>
          ${internet}
        </span>
      </div>

      <div class="kpi-grid">
        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Internet</span>
            ${iconTemplate(mdiEarth)}
          </div>
          <div class="kpi-value">${internet}</div>
          <div class="kpi-sub">WAN Health · ${level}</div>
        </div>

        <div class="kpi-tile">
          <div class="kpi-top">
            <span>Clients</span>
            ${iconTemplate(mdiWifi)}
          </div>
          <div class="kpi-value">${totalClients}</div>
          <div class="bar-track" aria-hidden="true">
            <div class="bar-fill" style=${`width: ${wirelessPct}%`}></div>
          </div>
          <div class="kpi-sub">
            ${wirelessClients || wiredClients
              ? `${wirelessClients} Wi-Fi · ${wiredClients} Wired`
              : "Connected clients"}
          </div>
        </div>

        ${totalDevices > 0
          ? html`<div class="kpi-tile">
              <div class="kpi-top">
                <span>Devices</span>
                ${iconTemplate(mdiDevices)}
              </div>
              <div class="kpi-value">${onlineDevices}/${totalDevices}</div>
              <div class="kpi-sub">Infrastructure online</div>
            </div>`
          : nothing}
      </div>
    `;
  }
}

export class UnifiInsightsSiteHealthCardEditor extends GenericEditor {
  constructor() {
    super(SITE_HEALTH_CARD_TAG, WS_SOURCES);
  }
}

registerDashboardCard({
  tag: SITE_HEALTH_CARD_TAG,
  editorTag: SITE_HEALTH_EDITOR_TAG,
  card: UnifiInsightsSiteHealthCard,
  editor: UnifiInsightsSiteHealthCardEditor,
  name: "UniFi Site Health",
  description:
    "Compact site health summary with WAN, gateway, device, and client status.",
});

export { SITE_HEALTH_CARD_TAG, SITE_HEALTH_EDITOR_TAG };
