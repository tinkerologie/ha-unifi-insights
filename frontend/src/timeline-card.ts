import {
  mdiAccount,
  mdiPaw,
  mdiCar,
  mdiHelpCircleOutline,
  mdiBellRingOutline,
  mdiCheckCircleOutline,
  mdiMotionSensor,
  mdiTimelineClockOutline,
} from "@mdi/js";
import { css, html, nothing, type TemplateResult } from "lit";

import { WS_SOURCES, type TimelineItem } from "./contract";
import {
  BaseDashboardCard,
  GenericEditor,
  TIMELINE_CARD_TAG,
  TIMELINE_EDITOR_TAG,
  formatRelativeTimestamp,
} from "./dashboard-cards-base";
import { iconTemplate } from "./icons";
import { registerDashboardCard } from "./register-dashboard-card";
import { makeLocalize, type LocalizeFunc } from "./localize";

function eventLabels(item: TimelineItem, localize: LocalizeFunc) {
  const kind = String(item.kind ?? "event");
  const smart = kind === "smartDetect" || kind === "smartDetectZone";
  const raw =
    smart && Array.isArray(item.smart_detect_types)
      ? item.smart_detect_types
          .filter((v) => typeof v === "string" && v.trim())
          .map((v) => v.trim())
      : [];
  const types = raw.length > 0 ? raw : [smart ? "motion" : kind];
  return [...new Set(types.map((v) => (v === "vehicle" ? "car" : v)))].map(
    (type) => {
      switch (type) {
        case "person":
          return { label: localize("timeline.person"), icon: mdiAccount };
        case "animal":
          return { label: localize("timeline.animal"), icon: mdiPaw };
        case "car":
          return { label: localize("timeline.car"), icon: mdiCar };
        case "motion":
          return { label: localize("timeline.motion"), icon: mdiMotionSensor };
        case "ring":
          return {
            label: localize("timeline.doorbell"),
            icon: mdiBellRingOutline,
          };
        default:
          return {
            label:
              smart && raw.length > 0
                ? localize("timeline.unknown_object", { value: type })
                : type,
            icon:
              smart && raw.length > 0 ? mdiHelpCircleOutline : mdiMotionSensor,
          };
      }
    },
  );
}

export class UnifiInsightsTimelineCard extends BaseDashboardCard {
  static override editorTag = TIMELINE_EDITOR_TAG;
  static override styles = [
    ...BaseDashboardCard.styles,
    css`
      .event-labels {
        display: flex;
        flex-wrap: wrap;
        gap: var(--ha-space-2, 6px);
        min-width: 0;
      }
      .event-labels .chip {
        white-space: normal;
        overflow-wrap: anywhere;
        min-width: 0;
      }
    `,
  ];

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
          const labels = eventLabels(
            item as TimelineItem,
            makeLocalize(this.hass?.language),
          );
          const severity = String(item.severity ?? "info");
          const timeLabel = formatRelativeTimestamp(item.timestamp);
          return html`
            <div class="item-card">
              <div
                class="item-icon ${severity === "warning"
                  ? "offline"
                  : "online"}"
              >
                ${iconTemplate(labels[0]!.icon)}
              </div>
              <div class="item-body">
                <div class="item-top">
                  <span class="item-name">
                    ${String(source.name ?? "Src")}
                  </span>
                  <span class="event-labels"
                    >${labels.map(
                      ({ label, icon }) => html`
                        <span
                          class="chip ${severity === "warning"
                            ? "warning"
                            : "ok"}"
                        >
                          ${iconTemplate(icon)} ${label}
                        </span>
                      `,
                    )}</span
                  >
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
