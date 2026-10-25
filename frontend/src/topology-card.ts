import {
  LitElement,
  css,
  html,
  nothing,
  type PropertyValues,
  type TemplateResult,
} from "lit";
import {
  CARD_TYPE,
  EDITOR_TAG,
  VIEWS,
  allSites,
  resolveBinding,
  resolveConfig,
  sameBinding,
  validateConfig,
  type Density,
  type ResolvedConfig,
  type SiteBinding,
  type TopologyCardConfig,
  type ViewMode,
} from "./config";
import {
  NODE_KINDS,
  WS_SOURCES,
  type NodeKind,
  type SiteTopology,
  type TopologySource,
} from "./contract";
import { NodeDetailsFetcher, type NodeDetailsKey } from "./data/node-details";
import { Announcer } from "./data/announcer";
import {
  deriveState,
  issueNotice,
  type CardPhase,
  type CardState,
  type Notice,
  type NoticeAction,
} from "./data/state";
import { TopologySubscription, backoffDelay } from "./data/subscription";
import {
  isAdmin,
  navigate,
  toWsError,
  type Connection,
  type HomeAssistant,
  type WsError,
} from "./ha-types";
import {
  iconTemplate,
  mdiFitToScreenOutline,
  mdiMagnifyMinusOutline,
  mdiMagnifyPlusOutline,
  mdiRouterNetwork,
} from "./icons";
import {
  makeLocalize,
  type LocalizeFunc,
  type LocalizeKey,
  type LocalizeVars,
} from "./localize";
import {
  createModelBuilder,
  groupContaining,
  type GraphModel,
  type UiState,
} from "./model/graph-model";
import { controlStyles, themeTokens } from "./styles";
import { KIND_KEYS, VIEW_KEYS } from "./views/describe";
import "./views/detail-panel";
import type { UitDetailPanel } from "./views/detail-panel";
import "./views/graph-view";
import type { UitGraphView } from "./views/graph-view";
import "./views/list-view";

const NARROW_PX = 600;
const INTEGRATION_PATH = "/config/integrations/integration/unifi_insights";
const ACTION_KEYS: Record<NoticeAction, LocalizeKey> = {
  integration: "action.integration",
  edit: "action.edit",
};
const PHASE_MESSAGES: Partial<
  Record<CardPhase, { key: LocalizeKey; action?: NoticeAction }>
> = {
  loading: { key: "state.loading" },
  no_sources: { key: "state.no_sources", action: "integration" },
  unconfigured: { key: "state.unconfigured", action: "edit" },
  empty: { key: "state.empty" },
  incompatible: { key: "state.incompatible" },
  reloading: { key: "state.reconnecting" },
};

/** Offline infrastructure devices; clients and WAN connections are not counted. */
const offlineDevices = (snapshot: SiteTopology | undefined): number =>
  snapshot?.nodes.filter(
    (n) => n.kind !== "client" && n.kind !== "wan" && n.state === "offline",
  ).length ?? 0;

/** Lovelace card: one UniFi site's topology as an interactive graph or an accessible list. */
export class UnifiInsightsTopologyCard extends LitElement {
  static override properties = {
    hass: { attribute: false },
    layout: { attribute: false },
    config: { state: true },
    sources: { state: true },
    snapshot: { state: true },
    lastGood: { state: true },
    error: { state: true },
    incompatible: { state: true },
    disconnected: { state: true },
    ui: { state: true },
    view: { state: true },
    selectedId: { state: true },
    menuRequest: { state: true },
    siteOverride: { state: true },
    narrow: { state: true },
    reducedMotion: { state: true },
    announcement: { state: true },
  };

  declare hass?: HomeAssistant;
  /** Set by hui-card: "panel" | "grid" | "masonry". */
  declare layout?: string;
  declare config?: ResolvedConfig;
  declare sources?: TopologySource[];
  declare snapshot?: SiteTopology;
  declare lastGood?: SiteTopology;
  declare error?: WsError;
  declare incompatible: boolean;
  /** The socket is down: the drawn graph is the last one received. */
  declare disconnected: boolean;
  declare ui: UiState;
  declare view: ViewMode;
  declare selectedId?: string;
  /** A fresh object asks the detail panel to open the menu of that node. */
  declare menuRequest?: NodeDetailsKey | undefined;
  declare siteOverride?: SiteBinding;
  declare narrow: boolean;
  declare reducedMotion: boolean;
  declare announcement: string;

  private readonly subscription = new TopologySubscription({
    onSnapshot: (snapshot) => this.applySnapshot(snapshot),
    onError: (error) => {
      this.error = error;
    },
    onIncompatible: () => {
      this.incompatible = true;
    },
    onDisconnected: () => {
      this.disconnected = true;
    },
    onReconnected: () => {
      // Entries may have been added, removed or still be loading.
      if (this.hass) void this.loadSources(this.hass);
    },
  });
  private readonly details = new NodeDetailsFetcher(() => this.requestUpdate());
  private selectionScope: string | undefined;
  private readonly buildModel = createModelBuilder();
  private readonly announcer = new Announcer((message) => {
    this.announcement = message;
  });
  private sourcesFor: Connection | undefined;
  private sourcesPending = false;
  private sourcesRetry: ReturnType<typeof setTimeout> | undefined;
  private sourcesAttempt = 0;
  private sourcesError: WsError | undefined;
  private boundKey: string | undefined;
  private cardState: CardState = {
    phase: "loading",
    stale: false,
    notices: [],
  };
  private model: GraphModel | undefined;
  private resizeObserver: ResizeObserver | undefined;
  private motionQuery: MediaQueryList | undefined;
  private localizeLang: string | undefined;
  private localizeFn: LocalizeFunc | undefined;

  constructor() {
    super();
    this.incompatible = false;
    this.disconnected = false;
    this.ui = {
      kinds: new Set(NODE_KINDS),
      clients: "collapsed",
      toggledGroups: new Set(),
    };
    this.view = "graph";
    this.narrow = false;
    this.reducedMotion = false;
    this.announcement = "";
  }

  setConfig(config: TopologyCardConfig): void {
    const resolved = resolveConfig(validateConfig(config));
    this.config = resolved;
    this.view = resolved.view;
    this.ui = {
      kinds: new Set(resolved.kinds),
      clients: resolved.clients,
      toggledGroups: new Set(),
    };
    this.siteOverride = undefined;
  }

  getCardSize(): number {
    return 8;
  }

  getGridOptions(): {
    columns: number;
    rows: number;
    min_columns: number;
    min_rows: number;
  } {
    return { columns: 12, rows: 8, min_columns: 6, min_rows: 4 };
  }

  static getConfigElement(): HTMLElement {
    return document.createElement(EDITOR_TAG);
  }

  static async getStubConfig(hass: HomeAssistant): Promise<TopologyCardConfig> {
    try {
      const first = allSites(
        await hass.callWS<TopologySource[]>({ type: WS_SOURCES }),
      )[0];
      if (first) return { type: CARD_TYPE, ...first.binding };
    } catch {
      // The card renders its own error state once added.
    }
    return { type: CARD_TYPE };
  }

  override connectedCallback(): void {
    super.connectedCallback();
    this.resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      this.narrow = width > 0 && width < NARROW_PX;
    });
    this.resizeObserver.observe(this);
    this.motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    this.motionQuery.addEventListener("change", this.onMotionChange);
    this.onMotionChange();
    this.sync();
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    this.subscription.stop();
    this.details.stop();
    this.menuRequest = undefined;
    this.announcer.dispose();
    this.resizeObserver?.disconnect();
    this.resizeObserver = undefined;
    this.motionQuery?.removeEventListener("change", this.onMotionChange);
    this.sourcesFor = undefined;
    this.clearSourcesRetry();
  }

  /** HA replaces `hass` on every entity state change; only its connection, language and the user's admin right matter here. */
  protected override shouldUpdate(changed: PropertyValues<this>): boolean {
    if (changed.size !== 1 || !changed.has("hass")) return true;
    const previous = changed.get("hass") as HomeAssistant | undefined;
    const hass = this.hass;
    return (
      !previous ||
      !hass ||
      previous.connection !== hass.connection ||
      (previous.locale?.language ?? previous.language) !==
        (hass.locale?.language ?? hass.language) ||
      // The node menu offers addresses to administrators only.
      isAdmin(previous) !== isAdmin(hass)
    );
  }

  protected override willUpdate(changed: PropertyValues<this>): void {
    if (
      changed.has("hass") ||
      changed.has("config") ||
      changed.has("sources") ||
      changed.has("siteOverride")
    )
      this.sync();
    const config = this.config;
    if (!config) return;
    this.cardState = deriveState({
      sources: this.sources,
      binding: this.binding,
      snapshot: this.snapshot,
      lastGood: this.lastGood,
      error: this.error,
      incompatible: this.incompatible,
      disconnected: this.disconnected,
      maxClients: config.max_clients,
    });
    this.model = this.cardState.render
      ? this.buildModel(this.cardState.render, this.ui)
      : undefined;
    const selected = this.selectedId;
    if (
      selected !== undefined &&
      !(this.model?.visuals.has(selected) || this.model?.nodes.has(selected))
    ) {
      this.deselect();
    }
    const binding = this.binding;
    const key =
      binding && this.selectedId !== undefined
        ? { ...binding, node_id: this.selectedId }
        : undefined;
    const scope = JSON.stringify(key);
    if (scope !== this.selectionScope) {
      this.selectionScope = scope;
      if (JSON.stringify(this.menuRequest) !== scope)
        this.menuRequest = undefined;
    }
    this.details.select(this.isConnected ? this.hass : undefined, key);
  }

  private get binding(): SiteBinding | undefined {
    return this.config
      ? (this.siteOverride ?? resolveBinding(this.config, this.sources))
      : undefined;
  }

  private get localize(): LocalizeFunc {
    const lang = this.hass?.locale?.language ?? this.hass?.language ?? "en";
    if (lang !== this.localizeLang || !this.localizeFn) {
      this.localizeLang = lang;
      this.localizeFn = makeLocalize(lang);
    }
    return this.localizeFn;
  }

  private get graphView(): UitGraphView | null {
    return this.renderRoot.querySelector("uit-graph-view");
  }

  /** Fetch sources once per connection and point the subscription at the bound site. */
  private sync(): void {
    const { hass, config } = this;
    if (!this.isConnected || !hass || !config) return;
    if (this.sourcesFor !== hass.connection) {
      this.sourcesFor = hass.connection;
      this.disconnected = false;
      this.clearSourcesRetry();
      this.sourcesAttempt = 0;
      void this.loadSources(hass);
    }
    const binding = this.binding;
    const key = binding
      ? `${binding.entry_id}\u0000${binding.site_id}`
      : undefined;
    if (key !== this.boundKey) {
      // A different site must never show the previous site's graph.
      this.boundKey = key;
      this.snapshot = undefined;
      this.lastGood = undefined;
      this.error = undefined;
      this.sourcesError = undefined;
      this.incompatible = false;
      this.deselect();
    }
    this.subscription.update(
      hass.connection,
      binding ? { ...binding, max_clients: config.max_clients } : undefined,
    );
  }

  /**
   * Sources list loaded entries only, so during HA startup or a setup retry
   * they come back empty; keep asking (with backoff) until one loads.
   */
  private async loadSources(hass: HomeAssistant): Promise<void> {
    if (this.sourcesPending) return;
    this.sourcesPending = true;
    this.clearSourcesRetry();
    const connection = hass.connection;
    try {
      const sources = await hass.callWS<TopologySource[]>({
        type: WS_SOURCES,
      });
      if (this.sourcesFor !== connection) return;
      this.sources = sources;
      if (this.sourcesError && this.error === this.sourcesError)
        this.error = undefined;
      this.sourcesError = undefined;
      if (sources.length > 0) this.sourcesAttempt = 0;
      else this.scheduleSourcesRetry();
    } catch (err) {
      if (this.sourcesFor !== connection) return;
      const previousSourcesError = this.sourcesError;
      this.sourcesError = toWsError(err);
      if (!this.error || this.error === previousSourcesError)
        this.error = this.sourcesError;
      this.scheduleSourcesRetry();
    } finally {
      this.sourcesPending = false;
      // A connection change can happen while the old request is pending.
      if (
        this.isConnected &&
        this.sourcesFor !== undefined &&
        this.sourcesFor !== connection &&
        this.hass
      )
        void this.loadSources(this.hass);
    }
  }

  private scheduleSourcesRetry(): void {
    if (!this.isConnected) return;
    this.sourcesRetry = setTimeout(() => {
      this.sourcesRetry = undefined;
      if (this.hass) void this.loadSources(this.hass);
    }, backoffDelay(this.sourcesAttempt++));
  }

  private clearSourcesRetry(): void {
    if (this.sourcesRetry !== undefined) clearTimeout(this.sourcesRetry);
    this.sourcesRetry = undefined;
  }

  private applySnapshot(snapshot: SiteTopology): void {
    const previous = this.snapshot;
    this.snapshot = snapshot;
    this.error = undefined;
    this.incompatible = false;
    this.disconnected = false;
    if (snapshot.status !== "unavailable" && snapshot.nodes.length > 0)
      this.lastGood = snapshot;
    // Sources were fetched before this entry finished loading (HA startup,
    // a setup retry): refresh them so selectors and titles catch up.
    if (
      this.hass &&
      this.sources !== undefined &&
      !this.sources.some((s) => s.entry_id === snapshot.entry_id)
    )
      void this.loadSources(this.hass);
    const localize = this.localize;
    let message = localize("announce.updated");
    const firstIssue = snapshot.issues[0];
    const offline = offlineDevices(snapshot);
    if (snapshot.status === "unavailable" && firstIssue) {
      const notice = issueNotice(
        firstIssue,
        snapshot,
        this.config?.max_clients ?? 0,
      );
      message = localize(notice.key, notice.vars);
    } else if (offline > 0 && offline !== offlineDevices(previous)) {
      message = localize("announce.offline", { count: offline });
    }
    this.announcer.announce(message);
  }

  private readonly onMotionChange = (): void => {
    this.reducedMotion = this.motionQuery?.matches ?? false;
  };

  private readonly onActivate = (e: Event): void => {
    const id = (e as CustomEvent<{ id: string }>).detail.id;
    if (this.model?.visuals.get(id)?.type === "group") this.toggleGroup(id);
    this.selectedId = id;
  };

  private readonly onSelect = (e: Event): void => {
    const id = (e as CustomEvent<{ id: string }>).detail.id;
    const model = this.model;
    if (model && !model.visuals.has(id)) {
      const group = groupContaining(model, id);
      if (group && !group.expanded) this.toggleGroup(group.id);
    }
    this.selectedId = id;
  };

  private readonly onToggleGroup = (e: Event): void => {
    this.toggleGroup((e as CustomEvent<{ id: string }>).detail.id);
  };

  /**
   * A context click, the ContextMenu key or Shift+F10 on a node: select it
   * and open its action menu. Client groups have no menu, so they are only
   * selected.
   */
  private readonly onContext = (e: Event): void => {
    const id = (e as CustomEvent<{ id: string }>).detail.id;
    if (!this.model?.visuals.has(id) && !this.model?.nodes.has(id)) return;
    this.onSelect(e);
    const binding = this.binding;
    this.menuRequest =
      binding && !id.startsWith("group:")
        ? { ...binding, node_id: id }
        : undefined;
  };

  private readonly onClose = (): void => {
    this.deselect();
  };

  private readonly onKeydown = (e: KeyboardEvent): void => {
    if (
      e.key === "Escape" &&
      this.selectedId !== undefined &&
      !this.renderRoot.querySelector<UitDetailPanel>("uit-detail-panel")
        ?.menuOpen
    ) {
      e.stopPropagation();
      this.deselect();
    }
  };

  /** Close the details, and forget any request to open the menu. */
  private deselect(): void {
    this.selectedId = undefined;
    this.menuRequest = undefined;
  }

  private toggleGroup(id: string): void {
    const toggled = new Set(this.ui.toggledGroups);
    if (toggled.has(id)) toggled.delete(id);
    else toggled.add(id);
    this.ui = { ...this.ui, toggledGroups: toggled };
  }

  private toggleKind(kind: NodeKind): void {
    const kinds = new Set(this.ui.kinds);
    if (kinds.has(kind)) {
      if (kinds.size === 1) return;
      kinds.delete(kind);
    } else {
      kinds.add(kind);
    }
    this.ui = { ...this.ui, kinds };
  }

  private runAction(action: NoticeAction): void {
    navigate(
      action === "integration"
        ? INTEGRATION_PATH
        : `${location.pathname}?edit=1`,
    );
  }

  /** Header, toolbar, notices and the graph or list view of the current site. */
  protected override render(): TemplateResult | typeof nothing {
    const config = this.config;
    if (!config) return nothing;
    const localize = this.localize;
    const state = this.cardState;
    const model = this.model;
    const title =
      config.title ??
      state.render?.site_name ??
      this.snapshot?.site_name ??
      localize("card.name");
    const nodes = state.render?.nodes ?? [];
    const devCount = nodes.filter(
      (n) => n.kind !== "client" && n.kind !== "wan",
    ).length;
    const cliCount = nodes.filter((n) => n.kind === "client").length;
    return html`<ha-card>
      <div
        class="card ${this.narrow ? "narrow" : ""}"
        @keydown=${this.onKeydown}
        @uit-activate=${this.onActivate}
        @uit-select=${this.onSelect}
        @uit-context=${this.onContext}
        @uit-toggle-group=${this.onToggleGroup}
        @uit-close=${this.onClose}
      >
        <header>
          <div class="header-icon" aria-hidden="true">
            ${iconTemplate(mdiRouterNetwork)}
          </div>
          <div class="header-text">
            <h2 class="title" title=${title}>${title}</h2>
            ${nodes.length > 0
              ? html`<div class="subtitle">
                  ${localize("header.summary", {
                    devices: devCount,
                    clients: cliCount,
                  })}
                </div>`
              : nothing}
          </div>
          ${this.renderSiteSelector(config, localize)}
        </header>
        ${model ? this.renderToolbar(localize) : nothing}
        ${model ? this.renderNotices(state.notices, localize) : nothing}
        <div class="body">
          ${model
            ? this.renderContent(model, state, config, localize)
            : this.renderMessage(state, localize)}
        </div>
        <div class="sr-only" role="status" aria-live="polite">
          ${this.announcement}
        </div>
      </div>
    </ha-card>`;
  }

  private renderSiteSelector(
    config: ResolvedConfig,
    localize: LocalizeFunc,
  ): TemplateResult | typeof nothing {
    const sites = allSites(this.sources ?? []);
    if (!config.show_site_selector || sites.length < 2) return nothing;
    const current = this.binding;
    return html`<label class="site">
      <span class="sr-only">${localize("toolbar.site")}</span>
      <select
        @change=${(e: Event) => {
          const site = sites[Number((e.target as HTMLSelectElement).value)];
          if (site) this.siteOverride = site.binding;
        }}
      >
        ${sites.map(
          (s, i) =>
            html`<option
              value=${i}
              ?selected=${sameBinding(s.binding, current)}
            >
              ${s.label}
            </option>`,
        )}
      </select>
    </label>`;
  }

  /** View switch, kind filter chips (no WAN chip with `show_wan: false`) and zoom. */
  private renderToolbar(localize: LocalizeFunc): TemplateResult {
    const controls = html`
      <div class="group" role="group" aria-label=${localize("toolbar.view")}>
        ${VIEWS.map(
          (v) =>
            html`<button
              aria-pressed=${String(this.view === v)}
              @click=${() => {
                this.view = v;
              }}
            >
              ${localize(VIEW_KEYS[v])}
            </button>`,
        )}
      </div>
      <div class="group" role="group" aria-label=${localize("toolbar.filters")}>
        ${NODE_KINDS.filter(
          (k) => this.config?.show_wan !== false || k !== "wan",
        ).map(
          (k) =>
            html`<button
              aria-pressed=${String(this.ui.kinds.has(k))}
              @click=${() => this.toggleKind(k)}
            >
              ${localize(KIND_KEYS[k])}
            </button>`,
        )}
      </div>
      ${this.view === "graph"
        ? html`<div
            class="group"
            role="group"
            aria-label=${localize("toolbar.zoom")}
          >
            ${this.iconButton(mdiMagnifyPlusOutline, localize("zoom.in"), () =>
              this.graphView?.zoomBy(1.25),
            )}
            ${this.iconButton(
              mdiMagnifyMinusOutline,
              localize("zoom.out"),
              () => this.graphView?.zoomBy(0.8),
            )}
            ${this.iconButton(mdiFitToScreenOutline, localize("zoom.fit"), () =>
              this.graphView?.fit(),
            )}
          </div>`
        : nothing}
    `;
    return this.narrow
      ? html`<details class="toolbar">
          <summary>${localize("toolbar.options")}</summary>
          <div class="controls">${controls}</div>
        </details>`
      : html`<div class="toolbar">
          <div class="controls">${controls}</div>
        </div>`;
  }

  private iconButton(
    icon: string,
    label: string,
    run: () => void,
  ): TemplateResult {
    return html`<button
      class="icon-button"
      aria-label=${label}
      title=${label}
      @click=${run}
    >
      ${iconTemplate(icon)}
    </button>`;
  }

  private actionButton(
    action: NoticeAction,
    localize: LocalizeFunc,
  ): TemplateResult {
    return html`<button class="action" @click=${() => this.runAction(action)}>
      ${localize(ACTION_KEYS[action])}
    </button>`;
  }

  private renderNotices(
    notices: Notice[],
    localize: LocalizeFunc,
  ): TemplateResult | typeof nothing {
    if (notices.length === 0) return nothing;
    return html`<ul class="notices">
      ${notices.map(
        (n) =>
          html`<li class=${n.severity}>
            <span>${localize(n.key, n.vars)}</span>${n.action
              ? this.actionButton(n.action, localize)
              : nothing}
          </li>`,
      )}
    </ul>`;
  }

  private renderMessage(
    state: CardState,
    localize: LocalizeFunc,
  ): TemplateResult {
    const phase = PHASE_MESSAGES[state.phase];
    const vars: LocalizeVars = { site: this.snapshot?.site_name ?? "" };
    const items: {
      key: LocalizeKey;
      vars?: LocalizeVars;
      action?: NoticeAction;
    }[] = [];
    if (phase) items.push({ ...phase, vars });
    items.push(...state.notices);
    return html`<div class="message ${state.phase}">
      ${state.phase === "loading"
        ? html`<svg class="skeleton" viewBox="0 0 120 60" aria-hidden="true">
            <path d="M60 18 L30 37 M60 18 L90 37"></path>
            <circle cx="60" cy="10" r="8"></circle>
            <circle cx="30" cy="45" r="8"></circle>
            <circle cx="90" cy="45" r="8"></circle>
          </svg>`
        : nothing}
      ${items.map(
        (i) =>
          html`<p>${localize(i.key, i.vars)}</p>
            ${i.action ? this.actionButton(i.action, localize) : nothing}`,
      )}
    </div>`;
  }

  private renderContent(
    model: GraphModel,
    state: CardState,
    config: ResolvedConfig,
    localize: LocalizeFunc,
  ): TemplateResult {
    const density: Density =
      config.density ?? (this.narrow ? "compact" : "comfortable");
    const site = state.render?.site_name ?? "";
    const badge =
      state.phase === "reloading"
        ? `${localize("state.stale")} · ${localize("state.reconnecting")}`
        : localize("state.stale");
    return html`<div class="content ${state.stale ? "stale" : ""}">
      ${state.stale ? html`<span class="badge stale">${badge}</span>` : nothing}
      ${this.view === "graph"
        ? html`<uit-graph-view
            .model=${model}
            .density=${density}
            .orientation=${config.orientation}
            .layoutMode=${config.layout}
            .showLabels=${config.show_labels}
            .selectedId=${this.selectedId}
            .localize=${localize}
            .siteName=${site}
            .ctrlZoom=${this.layout !== "panel"}
            .reducedMotion=${this.reducedMotion}
          ></uit-graph-view>`
        : html`<uit-list-view
            .model=${model}
            .selectedId=${this.selectedId}
            .localize=${localize}
            .siteName=${site}
          ></uit-list-view>`}
      <uit-detail-panel
        .model=${model}
        .selectedId=${this.selectedId}
        .hass=${this.hass}
        .menuRequest=${this.menuRequest}
        .details=${this.details.state.status === "ready"
          ? this.details.state.details
          : undefined}
        .localize=${localize}
        ?narrow=${this.narrow}
      ></uit-detail-panel>
    </div>`;
  }

  static override styles = [
    themeTokens,
    controlStyles,
    css`
      :host {
        display: block;
        height: 100%;
      }
      ha-card {
        height: 100%;
        display: flex;
        flex-direction: column;
        overflow: hidden;
        container-type: inline-size;
      }
      .card {
        position: relative;
        display: flex;
        flex-direction: column;
        flex: 1;
        min-height: 0;
      }
      header {
        display: flex;
        align-items: center;
        gap: 12px;
        padding: 14px 16px 6px;
      }
      .header-icon {
        width: 38px;
        height: 38px;
        border-radius: 12px;
        display: grid;
        place-items: center;
        background: color-mix(in srgb, var(--primary-color) 15%, transparent);
        color: var(--primary-color);
        flex: none;
      }
      .header-text {
        flex: 1;
        min-width: 0;
      }
      .title {
        margin: 0;
        font-size: 1.05rem;
        font-weight: 600;
        line-height: 1.25;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .subtitle {
        font-size: 0.76rem;
        color: var(--secondary-text-color);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .toolbar {
        padding: 4px 16px 8px;
      }
      details.toolbar > summary {
        min-height: 44px;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        cursor: pointer;
        padding: 0 14px;
        border-radius: 999px;
        border: 1px solid var(--uit-line);
        background: color-mix(
          in srgb,
          var(--primary-text-color) 5%,
          transparent
        );
        font-size: 0.82rem;
        font-weight: 600;
        list-style: none;
      }
      details.toolbar > summary::-webkit-details-marker {
        display: none;
      }
      details.toolbar[open] > .controls {
        margin-top: 8px;
      }
      .controls {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 8px 16px;
      }
      .group {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
      }
      .icon-button {
        padding: 0;
        justify-content: center;
      }
      .notices {
        list-style: none;
        margin: 4px 16px;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .notices li {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 6px 10px;
        border-radius: 8px;
        border-inline-start: 4px solid var(--uit-warning);
        background: color-mix(in srgb, var(--uit-warning) 12%, transparent);
      }
      .notices li.info {
        border-color: var(--uit-focus);
        background: color-mix(in srgb, var(--uit-focus) 10%, transparent);
      }
      .notices li.error {
        border-color: var(--uit-offline);
        background: color-mix(in srgb, var(--uit-offline) 12%, transparent);
      }
      .notices li span {
        flex: 1;
      }
      .body {
        position: relative;
        display: flex;
        flex: 1 1 280px;
        min-height: 0;
        margin: 4px 12px 12px;
        border-radius: 14px;
        border: 1px solid var(--uit-line);
        background-color: color-mix(
          in srgb,
          var(--primary-text-color) 2.5%,
          transparent
        );
        background-image: radial-gradient(
          color-mix(in srgb, var(--primary-text-color) 12%, transparent) 1px,
          transparent 1px
        );
        background-size: 18px 18px;
        overflow: hidden;
      }
      .content {
        position: relative;
        display: flex;
        flex: 1;
        min-width: 0;
        min-height: 0;
      }
      .content.stale uit-graph-view,
      .content.stale uit-list-view {
        opacity: 0.55;
        filter: grayscale(1);
      }
      .badge.stale {
        position: absolute;
        top: 8px;
        left: 12px;
        z-index: 1;
        padding: 2px 10px;
        border-radius: 12px;
        background: var(--card-background-color);
        border: 1px solid var(--uit-line);
        font-size: 0.85em;
      }
      .message {
        margin: auto;
        padding: 24px;
        text-align: center;
        color: var(--secondary-text-color);
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 8px;
      }
      .message p {
        margin: 0;
      }
      .skeleton {
        width: 120px;
        fill: var(--uit-line);
        stroke: var(--uit-line);
        stroke-width: 2;
        animation: uit-pulse 1.5s ease-in-out infinite;
      }
      @keyframes uit-pulse {
        50% {
          opacity: 0.4;
        }
      }
      @container (width < 600px) {
        header {
          padding: 8px 12px 0;
        }
        .body {
          flex-basis: 240px;
        }
      }
    `,
  ];
}
