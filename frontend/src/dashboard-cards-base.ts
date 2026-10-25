import { mdiAlertCircleOutline } from "@mdi/js";
import {
  LitElement,
  html,
  nothing,
  type PropertyValues,
  type TemplateResult,
} from "lit";

import { resolveBinding, type SiteBinding } from "./config";
import { type TopologySource } from "./contract";
import {
  type DashboardCardConfig,
  GenericEditor,
  INTERNET_ACTIVITY_CARD_TAG,
  INTERNET_ACTIVITY_EDITOR_TAG,
  PERFORMANCE_CARD_TAG,
  PERFORMANCE_EDITOR_TAG,
  PROTECT_CARD_TAG,
  PROTECT_EDITOR_TAG,
  type ProtectSource,
  SITE_HEALTH_CARD_TAG,
  SITE_HEALTH_EDITOR_TAG,
  TIMELINE_CARD_TAG,
  TIMELINE_EDITOR_TAG,
  formatBps,
  formatBytesCompact,
  formatRelativeTimestamp,
  formatUptime,
  kindIcon,
  metricTone,
} from "./dashboard-cards-editor";
import { dashboardCardStyles } from "./dashboard-cards-styles";
import {
  fireEvent,
  toWsError,
  type HomeAssistant,
  type MessageBase,
  type UnsubscribeFunc,
  type WsError,
} from "./ha-types";
import { iconTemplate } from "./icons";
import { makeLocalize } from "./localize";

export {
  type DashboardCardConfig,
  GenericEditor,
  INTERNET_ACTIVITY_CARD_TAG,
  INTERNET_ACTIVITY_EDITOR_TAG,
  PERFORMANCE_CARD_TAG,
  PERFORMANCE_EDITOR_TAG,
  PROTECT_CARD_TAG,
  PROTECT_EDITOR_TAG,
  type ProtectSource,
  SITE_HEALTH_CARD_TAG,
  SITE_HEALTH_EDITOR_TAG,
  TIMELINE_CARD_TAG,
  TIMELINE_EDITOR_TAG,
  formatBps,
  formatBytesCompact,
  formatRelativeTimestamp,
  formatUptime,
  kindIcon,
  metricTone,
};

interface SnapshotIssue {
  code?: string;
  severity?: "info" | "warning" | "error";
}

interface CompanionSnapshot extends Record<string, unknown> {
  status?: "ok" | "partial" | "degraded" | "unavailable" | "no_protect";
  issues?: SnapshotIssue[];
  freshness?: "fresh" | "stale" | "unavailable";
}

export abstract class BaseDashboardCard extends LitElement {
  static editorTag = "";

  static async getConfigElement(this: {
    editorTag: string;
  }): Promise<HTMLElement> {
    return document.createElement(this.editorTag);
  }

  static override properties = {
    hass: { attribute: false },
    config: { state: true },
    snapshot: { state: true },
    sources: { state: true },
    loading: { state: true },
    error: { state: true },
  };

  declare hass?: HomeAssistant;
  declare config: DashboardCardConfig;
  declare snapshot: CompanionSnapshot | undefined;
  declare sources: TopologySource[] | ProtectSource[];
  declare loading: boolean;
  declare error: WsError | undefined;

  private unsubscribe: UnsubscribeFunc | undefined;
  private bindingKey: string | undefined;
  private syncGeneration = 0;
  private failedBindingKey: string | undefined;
  private retryAfterMs = 0;
  private retryTimer: ReturnType<typeof setTimeout> | undefined;

  constructor() {
    super();
    this.config = { type: "" };
    this.snapshot = undefined;
    this.sources = [];
    this.loading = false;
    this.error = undefined;
  }

  protected abstract get cardType(): string;
  protected abstract get sourceCommand(): string;
  protected abstract get subscribeCommand(): string;
  protected abstract get defaultTitle(): string;
  protected abstract get headerIcon(): string;
  protected abstract renderContent(): unknown;

  protected get headerAccent(): string {
    return "var(--primary-color, #03a9f4)";
  }

  protected renderHeaderBadge(): TemplateResult | typeof nothing {
    return nothing;
  }

  protected get loadingLabel(): string {
    return "Loading";
  }

  protected get includeSiteInSubscribeMessage(): boolean {
    return true;
  }

  override connectedCallback(): void {
    super.connectedCallback();
    void this.sync();
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.syncGeneration += 1;
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = undefined;
    }
    void this.unsubscribe?.();
    this.unsubscribe = undefined;
  }

  protected override willUpdate(changed: PropertyValues<this>): void {
    const hassChanged = changed.has("hass");
    const configChanged = changed.has("config");
    const previousHass = changed.get("hass") as HomeAssistant | undefined;
    const connectionChanged =
      hassChanged && previousHass?.connection !== this.hass?.connection;
    if (configChanged || connectionChanged) {
      void this.sync();
    }
  }

  setConfig(config: DashboardCardConfig): void {
    if (!config || typeof config.type !== "string") {
      throw new Error("Invalid card config");
    }
    this.config = { ...config };
  }

  getCardSize(): number {
    return 4;
  }

  getGridOptions() {
    return { columns: 6, rows: 4, min_columns: 3, min_rows: 3 };
  }

  /** The configured site, or the only site when exactly one exists. */
  protected getBinding(): SiteBinding | undefined {
    if (this.config.entry_id && this.config.site_id) {
      return {
        entry_id: this.config.entry_id,
        site_id: this.config.site_id,
      };
    }
    return resolveBinding(
      {
        entry_id: this.config.entry_id,
        site_id: this.config.site_id,
        title: this.config.title,
        view: "graph",
        show_site_selector: false,
        clients: "collapsed",
        kinds: ["gateway", "switch", "access_point", "client", "other"],
        density: undefined,
        orientation: "vertical",
        layout: "tree",
        show_labels: true,
        show_wan: true,
        max_clients: 500,
      },
      this.sources as TopologySource[],
    );
  }

  private async sync(): Promise<void> {
    const generation = ++this.syncGeneration;
    if (!this.hass) return;
    this.loading = true;
    try {
      this.sources = (await this.hass.callWS({
        type: this.sourceCommand,
      })) as TopologySource[] | ProtectSource[];
      if (generation !== this.syncGeneration || !this.isConnected) {
        this.loading = false;
        return;
      }
      this.error = undefined;
    } catch (err) {
      if (generation !== this.syncGeneration || !this.isConnected) {
        this.loading = false;
        return;
      }
      this.error = toWsError(err);
      this.loading = false;
      return;
    }

    const binding = this.getBinding();
    const key = binding ? `${binding.entry_id}:${binding.site_id}` : undefined;
    if (key === this.bindingKey && this.unsubscribe) {
      this.loading = false;
      return;
    }
    this.bindingKey = key;
    if (key !== this.failedBindingKey) {
      this.failedBindingKey = undefined;
      this.retryAfterMs = 0;
    }
    this.snapshot = undefined;
    await this.unsubscribe?.();
    this.unsubscribe = undefined;
    if (!binding) {
      this.loading = false;
      return;
    }

    if (
      key !== undefined &&
      key === this.failedBindingKey &&
      Date.now() < this.retryAfterMs
    ) {
      this.loading = false;
      if (this.retryTimer) clearTimeout(this.retryTimer);
      this.retryTimer = setTimeout(
        () => {
          if (generation === this.syncGeneration && this.isConnected) {
            void this.sync();
          }
        },
        Math.max(0, this.retryAfterMs - Date.now()),
      );
      return;
    }

    const message: MessageBase = {
      type: this.subscribeCommand,
      entry_id: binding.entry_id,
    };
    if (this.includeSiteInSubscribeMessage) {
      message.site_id = binding.site_id;
    }

    try {
      const unsubscribe = await this.hass.connection.subscribeMessage(
        (msg: unknown) => {
          if (generation !== this.syncGeneration || !this.isConnected) return;
          const issues = Array.isArray((msg as Record<string, unknown>).issues)
            ? ((msg as Record<string, unknown>).issues as SnapshotIssue[])
            : [];
          if (issues.some((issue) => issue?.code === "entry_unloaded")) {
            this.unsubscribe?.().catch(() => undefined);
            this.unsubscribe = undefined;
            this.bindingKey = undefined;
            if (this.retryTimer) clearTimeout(this.retryTimer);
            this.retryTimer = setTimeout(() => {
              if (generation === this.syncGeneration && this.isConnected) {
                void this.sync();
              }
            }, 1000);
            return;
          }
          this.failedBindingKey = undefined;
          this.retryAfterMs = 0;
          this.error = undefined;
          this.snapshot = msg as CompanionSnapshot;
          this.loading = false;
        },
        message,
        { resubscribe: false },
      );
      if (generation !== this.syncGeneration || !this.isConnected) {
        await unsubscribe();
        this.loading = false;
        return;
      }
      this.unsubscribe = unsubscribe;
    } catch (err) {
      if (generation !== this.syncGeneration || !this.isConnected) {
        this.loading = false;
        return;
      }
      this.failedBindingKey = key;
      this.retryAfterMs = Date.now() + 5000;
      this.error = toWsError(err);
      this.loading = false;
      this.retryTimer = setTimeout(() => {
        if (generation === this.syncGeneration && this.isConnected) {
          void this.sync();
        }
      }, 5000);
    }
  }

  protected openMoreInfo(entityId: string): void {
    fireEvent(this, "hass-more-info", { entityId });
  }

  private renderStatus() {
    const localize = makeLocalize(
      this.hass?.language ?? this.hass?.locale?.language,
    );
    if (this.loading) {
      return html`<div
        class="state loading-box"
        role="status"
        aria-live="polite"
      >
        <span class="pulse-dot" aria-hidden="true"></span>
        ${localize("dashboard.loading", { title: this.defaultTitle })}
      </div>`;
    }
    if (this.error) {
      return html`<div class="state error" role="alert">
        ${iconTemplate(mdiAlertCircleOutline)}
        <div>
          ${localize("dashboard.error")}
          <span class="state-detail">${this.error.code}</span>
        </div>
      </div>`;
    }
    const status = this.snapshot?.status;
    if (status === "unavailable") {
      return html`<div class="state error" role="alert">
        ${iconTemplate(mdiAlertCircleOutline)}
        ${localize("dashboard.unavailable")}
      </div>`;
    }
    if (status === "no_protect") {
      return html`<div class="state" role="status" aria-live="polite">
        ${localize("dashboard.no_protect")}
      </div>`;
    }
    if (
      status === "partial" ||
      status === "degraded" ||
      this.snapshot?.freshness === "stale"
    ) {
      return html`<div class="state" role="status" aria-live="polite">
        ${iconTemplate(mdiAlertCircleOutline)}
        <div>
          ${localize(
            this.snapshot?.freshness === "stale"
              ? "dashboard.stale"
              : "dashboard.partial",
          )}
          ${Array.isArray(this.snapshot?.warnings) &&
          this.snapshot.warnings.length
            ? html`<span class="state-detail"
                >${this.snapshot.warnings
                  .map((warning) => String(warning).replaceAll("_", " "))
                  .join(" · ")}</span
              >`
            : nothing}
        </div>
      </div>`;
    }
    return nothing;
  }

  override render() {
    const contentAvailable =
      !this.loading &&
      !this.error &&
      this.snapshot?.status !== "unavailable" &&
      this.snapshot?.status !== "no_protect";
    const subtitle =
      typeof this.snapshot?.site_name === "string" &&
      this.snapshot.site_name.trim()
        ? this.snapshot.site_name
        : typeof this.snapshot?.entry_title === "string" &&
            this.snapshot.entry_title.trim()
          ? this.snapshot.entry_title
          : undefined;
    return html`
      <ha-card style=${`--card-accent: ${this.headerAccent}`}>
        <div class="header">
          <div class="header-main">
            <div class="header-icon">${iconTemplate(this.headerIcon)}</div>
            <div class="header-titles">
              <h2 class="header-title" id="card-title">
                ${this.config.title ?? this.defaultTitle}
              </h2>
              ${subtitle
                ? html`<div class="header-subtitle">${subtitle}</div>`
                : nothing}
            </div>
          </div>
          <div class="header-actions">
            ${contentAvailable ? this.renderHeaderBadge() : nothing}
          </div>
        </div>
        <div
          class="content"
          tabindex="0"
          role="region"
          aria-labelledby="card-title"
        >
          ${this.renderStatus()}
          ${contentAvailable ? this.renderContent() : nothing}
        </div>
      </ha-card>
    `;
  }

  static override styles = dashboardCardStyles;
}
