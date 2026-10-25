import {
  mdiBellRingOutline,
  mdiCheckCircleOutline,
  mdiMotionSensor,
  mdiTimelineClockOutline,
} from "@mdi/js";
import { html, nothing, type TemplateResult } from "lit";

import { WS_SOURCES } from "./contract";
import {
  BaseDashboardCard,
  GenericEditor,
  TIMELINE_CARD_TAG,
  TIMELINE_EDITOR_TAG,
  formatRelativeTimestamp,
} from "./dashboard-cards-base";
import { iconTemplate } from "./icons";
import { registerDashboardCard } from "./register-dashboard-card";
import { makeLocalize } from "./localize";

export class UnifiInsightsTimelineCard extends BaseDashboardCard {
  static override editorTag = TIMELINE_EDITOR_TAG;

  protected get cardType(): string {
    return `custom:${TIMELINE_CARD_TAG}`;
  }

  protected get sourceCommand(): string {
    return WS_SOURCES;
  }

  protected get subscribeCommand(): string {
    return "unifi_insights/timeline/subscribe";
  }

  protected get defaultTitle(): string {
    return "Event Timeline";
  }

  protected get headerIcon(): string {
    return mdiTimelineClockOutline;
  }

  protected override renderHeaderBadge(): TemplateResult | typeof nothing {
    if (!this.snapshot) return nothing;
    const items = Array.isArray(this.snapshot.items)
      ? (this.snapshot.items as Record<string, unknown>[])
      : [];
    return html`<span class="chip ${items.length > 0 ? "warning" : "ok"}">
      ${items.length} Events
    </span>`;
  }

  protected renderContent() {
    const snapshot = this.snapshot;
    if (!snapshot) {
      return html`<div class="state">No events</div>`;
    }
    const items = Array.isArray(snapshot.items)
      ? (snapshot.items as Record<string, unknown>[])
      : [];
    if (items.length === 0) {
      if (snapshot.status !== "ok") {
        return html`<div class="state">
          ${makeLocalize(this.hass?.language)("dashboard.no_events")}
        </div>`;
      }
      return html`
        <div class="empty-hero">
          <div class="icon-badge">${iconTemplate(mdiCheckCircleOutline)}</div>
          <div class="empty-title">All Quiet · 0 Events</div>
          <div class="empty-sub">
            No security or motion detections in the recent window.
          </div>
        </div>
      `;
    }
    return html`
      <div class="list" tabindex="0" role="region" aria-label="Recent events">
        ${items.slice(0, 6).map((item) => {
          const source = (item.source ?? {}) as Record<string, unknown>;
          const kind = String(item.kind ?? "event");
          const severity = String(item.severity ?? "info");
          const timeLabel = formatRelativeTimestamp(item.timestamp);
          return html`
            <div class="item-card">
              <div
                class="item-icon ${severity === "warning"
                  ? "offline"
                  : "online"}"
              >
                ${iconTemplate(
                  kind === "ring" ? mdiBellRingOutline : mdiMotionSensor,
                )}
              </div>
              <div class="item-body">
                <div class="item-top">
                  <span class="item-name">
                    ${String(source.name ?? "Src")}
                  </span>
                  <span
                    class="chip ${severity === "warning" ? "warning" : "ok"}"
                  >
                    ${kind}
                  </span>
                </div>
                ${timeLabel
                  ? html`<div class="item-meta">
                      <span>${timeLabel}</span>
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

export class UnifiInsightsTimelineCardEditor extends GenericEditor {
  constructor() {
    super(TIMELINE_CARD_TAG, WS_SOURCES);
  }
}

registerDashboardCard({
  tag: TIMELINE_CARD_TAG,
  editorTag: TIMELINE_EDITOR_TAG,
  card: UnifiInsightsTimelineCard,
  editor: UnifiInsightsTimelineCardEditor,
  name: "UniFi Event Timeline",
  description: "Recent UniFi Protect security and device activity timeline.",
});

export { TIMELINE_CARD_TAG, TIMELINE_EDITOR_TAG };
