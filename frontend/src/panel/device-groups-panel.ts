import {
  LitElement,
  css,
  html,
  nothing,
  type PropertyValues,
  type TemplateResult,
} from "lit";
import { repeat } from "lit/directives/repeat.js";
import { backoffDelay } from "../data/subscription";
import { defineOnce } from "../define";
import {
  navigate,
  toWsError,
  type Connection,
  type HomeAssistant,
  type WsError,
} from "../ha-types";
import {
  iconTemplate,
  mdiAccessPoint,
  mdiCctv,
  mdiChevronDown,
  mdiChevronRight,
  mdiDevices,
  mdiLan,
  mdiRouterNetwork,
  mdiSwitch,
} from "../icons";
import { makeLocalize, type LocalizeFunc, type LocalizeKey } from "../localize";
import { controlStyles, themeTokens } from "../styles";
import {
  ERR_ENTRY_NOT_FOUND,
  ERR_ENTRY_NOT_LOADED,
  ERR_GROUPING_DISABLED,
  IncompatibleDeviceGroupsSchemaError,
  WS_DEVICE_GROUPS_SOURCES,
  parseDeviceGroupsSources,
  type DeviceGroupRow,
  type DeviceGroupSection,
  type DeviceGroupSourceEntry,
  type DeviceGroupsSnapshot,
} from "./contract";
import { DeviceGroupsSubscription } from "./subscription";
import {
  buildSectionTree,
  collapseKeyFor,
  flattenSectionTree,
  type FlattenedDeviceRow,
} from "./tree";

export const PANEL_TAG = "unifi-insights-devices-panel";

const RETRYABLE_ERRORS = new Set([ERR_ENTRY_NOT_LOADED, "unknown_command"]);

const REASON_KEYS: Record<string, LocalizeKey> = {
  no_uplink: "panel.reason.no_uplink",
  historical: "panel.reason.historical",
  ambiguous: "panel.reason.ambiguous",
  parent_not_found: "panel.reason.parent_not_found",
  cycle: "panel.reason.cycle",
  stale: "panel.reason.stale",
};

function kindIcon(kind: string): string {
  switch (kind) {
    case "gateway":
      return mdiRouterNetwork;
    case "switch":
      return mdiSwitch;
    case "access_point":
      return mdiAccessPoint;
    case "network_device":
      return mdiLan;
    case "camera":
      return mdiCctv;
    default:
      return mdiDevices;
  }
}

function reasonKey(reason: string): LocalizeKey {
  return REASON_KEYS[reason] ?? "panel.reason.unknown";
}

function deviceHref(haDeviceId: string): string {
  return `/config/devices/device/${encodeURIComponent(haDeviceId)}`;
}

function sectionTitle(
  section: DeviceGroupSection,
  localize: LocalizeFunc,
): string {
  if (section.kind === "unassigned")
    return localize("panel.section.unassigned");
  if (section.kind === "other") return localize("panel.section.other");
  return section.title || section.key;
}

/** Custom Home Assistant panel showing UniFi devices grouped by uplink. */
export class UnifiInsightsDevicesPanel extends LitElement {
  static override properties = {
    hass: { attribute: false },
    narrow: { type: Boolean, reflect: true },
    route: { attribute: false },
    panel: { attribute: false },
    entries: { state: true },
    selectedEntryId: { state: true },
    snapshot: { state: true },
    lastGood: { state: true },
    error: { state: true },
    sourcesError: { state: true },
    incompatible: { state: true },
    disconnected: { state: true },
    groupingDisabled: { state: true },
    collapsed: { state: true },
    focusBySection: { state: true },
  };

  declare hass?: HomeAssistant;
  declare narrow: boolean;
  declare route?: unknown;
  declare panel?: unknown;
  declare entries?: DeviceGroupSourceEntry[];
  declare selectedEntryId?: string;
  declare snapshot?: DeviceGroupsSnapshot;
  declare lastGood?: DeviceGroupsSnapshot;
  declare error?: WsError;
  declare sourcesError?: WsError;
  declare incompatible: boolean;
  declare disconnected: boolean;
  declare groupingDisabled: boolean;
  declare collapsed: ReadonlySet<string>;
  declare focusBySection: ReadonlyMap<string, string>;

  private readonly subscription = new DeviceGroupsSubscription({
    onSnapshot: (snapshot) => this.applySnapshot(snapshot),
    onError: (error) => {
      this.error = error;
      if (error.code === ERR_ENTRY_NOT_FOUND) {
        this.entryNotFound = true;
        this.retriedLiveSources = false;
        if (this.hass) void this.loadSources(this.hass);
      } else if (RETRYABLE_ERRORS.has(error.code)) {
        this.needsSourcesRefresh = true;
        this.retriedLiveSources = false;
      }
    },
    onGroupingDisabled: (entryId) => {
      this.groupingDisabled = true;
      this.entryNotFound = false;
      if (this.hass) void this.loadSources(this.hass, entryId);
    },
    onIncompatible: () => {
      this.incompatible = true;
    },
    onDisconnected: () => {
      this.disconnected = true;
    },
    onReconnected: () => {
      this.disconnected = false;
      this.entryNotFound = false;
      this.retriedLiveSources = false;
      if (this.hass) void this.loadSources(this.hass);
    },
  });

  private sourcesFor: Connection | undefined;
  private sourcesPending = false;
  private sourcesRefreshQueued = false;
  private queuedDisabledEntryId: string | undefined;
  private sourcesRetry: ReturnType<typeof setTimeout> | undefined;
  private sourcesAttempt = 0;
  private boundEntryId: string | undefined;
  private entryNotFound = false;
  private needsSourcesRefresh = false;
  private retriedLiveSources = false;
  private localizeLang: string | undefined;
  private localizeFn: LocalizeFunc | undefined;
  private typeahead = "";
  private typeaheadTimer: ReturnType<typeof setTimeout> | undefined;
  private readonly onVisibilityChange = (): void => {
    if (
      typeof document !== "undefined" &&
      document.visibilityState === "visible" &&
      this.isConnected &&
      this.hass
    ) {
      void this.loadSources(this.hass);
    }
  };

  constructor() {
    super();
    this.narrow = false;
    this.incompatible = false;
    this.disconnected = false;
    this.groupingDisabled = false;
    this.collapsed = new Set();
    this.focusBySection = new Map();
  }

  override connectedCallback(): void {
    super.connectedCallback();
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", this.onVisibilityChange);
    }
    this.sync();
  }

  override disconnectedCallback(): void {
    super.disconnectedCallback();
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", this.onVisibilityChange);
    }
    this.subscription.stop();
    this.sourcesFor = undefined;
    this.sourcesRefreshQueued = false;
    this.queuedDisabledEntryId = undefined;
    this.clearSourcesRetry();
    if (this.typeaheadTimer !== undefined) clearTimeout(this.typeaheadTimer);
    this.typeaheadTimer = undefined;
  }

  /** HA replaces `hass` on every entity state change; only connection and language matter here. */
  protected override shouldUpdate(changed: PropertyValues<this>): boolean {
    if (changed.size !== 1 || !changed.has("hass")) return true;
    const previous = changed.get("hass") as HomeAssistant | undefined;
    const hass = this.hass;
    return (
      !previous ||
      !hass ||
      previous.connection !== hass.connection ||
      (previous.locale?.language ?? previous.language) !==
        (hass.locale?.language ?? hass.language)
    );
  }

  protected override willUpdate(changed: PropertyValues<this>): void {
    // groupingDisabled clears when the sources list the selected entry again,
    // which must restart its stream even though the selection did not change.
    if (
      changed.has("hass") ||
      changed.has("selectedEntryId") ||
      changed.has("groupingDisabled")
    )
      this.sync();
  }

  private get localize(): LocalizeFunc {
    const lang = this.hass?.locale?.language ?? this.hass?.language ?? "en";
    if (lang !== this.localizeLang || !this.localizeFn) {
      this.localizeLang = lang;
      this.localizeFn = makeLocalize(lang);
    }
    return this.localizeFn;
  }

  private sync(): void {
    const { hass } = this;
    if (!this.isConnected || !hass) return;
    if (this.sourcesFor !== hass.connection) {
      this.sourcesFor = hass.connection;
      this.disconnected = false;
      this.incompatible = false;
      this.groupingDisabled = false;
      this.entryNotFound = false;
      this.needsSourcesRefresh = false;
      this.retriedLiveSources = false;
      this.sourcesError = undefined;
      this.clearSourcesRetry();
      this.sourcesAttempt = 0;
      void this.loadSources(hass);
    }
    if (this.selectedEntryId !== this.boundEntryId) {
      this.boundEntryId = this.selectedEntryId;
      this.snapshot = undefined;
      this.lastGood = undefined;
      this.error = undefined;
      this.incompatible = false;
      this.entryNotFound = false;
      this.needsSourcesRefresh = false;
      this.retriedLiveSources = false;
      if (this.selectedEntryId !== undefined) this.groupingDisabled = false;
    }
    this.subscription.update(
      hass.connection,
      this.groupingDisabled ? undefined : this.selectedEntryId,
    );
  }

  private async loadSources(
    hass: HomeAssistant,
    disabledEntryId?: string,
  ): Promise<void> {
    if (this.sourcesPending) {
      this.sourcesRefreshQueued = true;
      if (disabledEntryId !== undefined) {
        this.queuedDisabledEntryId = disabledEntryId;
      }
      return;
    }
    this.sourcesPending = true;
    this.sourcesRefreshQueued = false;
    this.queuedDisabledEntryId = undefined;
    this.clearSourcesRetry();
    const connection = hass.connection;
    try {
      const raw = await hass.callWS<unknown>({
        type: WS_DEVICE_GROUPS_SOURCES,
      });
      if (this.sourcesFor !== connection) return;
      const parsed = parseDeviceGroupsSources(raw);
      this.entries = parsed.entries;
      this.sourcesError = undefined;
      this.sourcesAttempt = 0;
      this.reconcileSelectedEntry(parsed.entries, disabledEntryId);
    } catch (err) {
      if (this.sourcesFor !== connection) return;
      if (err instanceof IncompatibleDeviceGroupsSchemaError) {
        this.incompatible = true;
        return;
      }
      if (err instanceof TypeError) {
        this.sourcesError = { code: "invalid_payload", message: String(err) };
        return;
      }
      const wsError = toWsError(err);
      this.sourcesError = wsError;
      if (RETRYABLE_ERRORS.has(wsError.code)) {
        this.needsSourcesRefresh = true;
        this.scheduleSourcesRetry();
      }
    } finally {
      this.sourcesPending = false;
      if (
        this.isConnected &&
        this.sourcesFor !== undefined &&
        this.hass &&
        (this.sourcesFor !== connection || this.sourcesRefreshQueued)
      ) {
        const queuedDisabled =
          this.sourcesFor === connection
            ? this.queuedDisabledEntryId
            : undefined;
        this.sourcesRefreshQueued = false;
        this.queuedDisabledEntryId = undefined;
        void this.loadSources(this.hass, queuedDisabled);
      }
    }
  }

  private reconcileSelectedEntry(
    entries: readonly DeviceGroupSourceEntry[],
    disabledEntryId?: string,
  ): void {
    if (disabledEntryId !== undefined) {
      if (
        this.selectedEntryId !== undefined &&
        this.selectedEntryId !== disabledEntryId &&
        entries.some((e) => e.entry_id === this.selectedEntryId)
      ) {
        this.groupingDisabled = false;
        return;
      }
      const replacement = entries.find((e) => e.entry_id !== disabledEntryId);
      if (replacement) {
        this.groupingDisabled = false;
        this.entryNotFound = false;
        this.selectedEntryId = replacement.entry_id;
        return;
      }
      if (entries.length === 0) {
        this.selectedEntryId = undefined;
      }
      return;
    }

    if (this.selectedEntryId === undefined) {
      this.selectedEntryId = entries[0]?.entry_id;
      return;
    }

    if (entries.some((e) => e.entry_id === this.selectedEntryId)) {
      if (this.groupingDisabled) this.groupingDisabled = false;
      this.needsSourcesRefresh = false;
      return;
    }

    if (this.groupingDisabled) {
      this.selectedEntryId = entries[0]?.entry_id;
      if (this.selectedEntryId !== undefined) this.groupingDisabled = false;
      return;
    }

    if (this.entryNotFound && entries.length > 0) {
      this.entryNotFound = false;
      this.needsSourcesRefresh = false;
      this.selectedEntryId = entries[0]!.entry_id;
      return;
    }

    // Keep selectedEntryId across transient empty/missing source lists while
    // its subscription is connecting or retrying; refresh once the stream recovers.
    this.needsSourcesRefresh = true;
    if (this.subscription.live && !this.retriedLiveSources) {
      this.retriedLiveSources = true;
      this.sourcesRefreshQueued = true;
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

  private applySnapshot(snapshot: DeviceGroupsSnapshot): void {
    this.snapshot = snapshot;
    this.incompatible = false;
    this.disconnected = false;
    if (snapshot.status === "entry_unloaded") {
      this.needsSourcesRefresh = true;
      this.retriedLiveSources = false;
      return;
    }
    if (snapshot.status === "ok") {
      this.lastGood = snapshot;
      this.error = undefined;
      this.groupingDisabled = false;
      this.entryNotFound = false;
      if (this.needsSourcesRefresh) {
        this.needsSourcesRefresh = false;
        if (this.hass) void this.loadSources(this.hass);
      }
    }
  }

  private onEntryChange(e: Event): void {
    const next = (e.target as HTMLSelectElement).value;
    if (!next || next === this.selectedEntryId) return;
    this.groupingDisabled = false;
    this.selectedEntryId = next;
  }

  private toggleCollapse(collapseId: string): void {
    const next = new Set(this.collapsed);
    if (next.has(collapseId)) next.delete(collapseId);
    else next.add(collapseId);
    this.collapsed = next;
  }

  private onLinkClick(e: MouseEvent, href: string): void {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) {
      e.stopPropagation();
      return;
    }
    e.preventDefault();
    e.stopPropagation();
    navigate(href);
  }

  private activateRow(
    row: DeviceGroupRow,
    hasChildren: boolean,
    collapseId: string,
  ): void {
    if (row.ha_device_id !== null) {
      navigate(deviceHref(row.ha_device_id));
      return;
    }
    if (hasChildren) this.toggleCollapse(collapseId);
  }

  private onTreeKeydown(
    e: KeyboardEvent,
    sectionKey: string,
    rows: readonly FlattenedDeviceRow[],
  ): void {
    if (rows.length === 0) return;
    const currentKey = this.focusKeyFor(sectionKey, rows);
    const index = rows.findIndex((r) => r.key === currentKey);
    const row = rows[index];
    if (!row) return;
    const collapseId = collapseKeyFor(sectionKey, row.key);

    let target: string | undefined;
    switch (e.key) {
      case "ArrowDown":
        target = rows[index + 1]?.key;
        break;
      case "ArrowUp":
        target = rows[index - 1]?.key;
        break;
      case "Home":
        target = rows[0]?.key;
        break;
      case "End":
        target = rows.at(-1)?.key;
        break;
      case "ArrowRight":
        if (row.hasChildren && !row.expanded) this.toggleCollapse(collapseId);
        else if (row.expanded) target = rows[index + 1]?.key;
        break;
      case "ArrowLeft":
        if (row.expanded) this.toggleCollapse(collapseId);
        else target = row.parentKey;
        break;
      case "Enter":
      case " ":
        this.activateRow(row.row, row.hasChildren, collapseId);
        break;
      default:
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
          e.preventDefault();
          this.typeAhead(e.key, sectionKey, rows, index);
        }
        return;
    }
    e.preventDefault();
    if (target !== undefined) void this.focusRow(sectionKey, target);
  }

  private typeAhead(
    char: string,
    sectionKey: string,
    rows: readonly FlattenedDeviceRow[],
    from: number,
  ): void {
    this.typeahead += char.toLowerCase();
    if (this.typeaheadTimer !== undefined) clearTimeout(this.typeaheadTimer);
    this.typeaheadTimer = setTimeout(() => {
      this.typeahead = "";
    }, 500);
    for (let step = 1; step <= rows.length; step++) {
      const candidate = rows[(from + step) % rows.length]!;
      if (candidate.row.name.toLowerCase().startsWith(this.typeahead)) {
        void this.focusRow(sectionKey, candidate.key);
        return;
      }
    }
  }

  private focusKeyFor(
    sectionKey: string,
    rows: readonly FlattenedDeviceRow[],
  ): string | undefined {
    const stored = this.focusBySection.get(sectionKey);
    if (stored !== undefined && rows.some((r) => r.key === stored))
      return stored;
    return rows[0]?.key;
  }

  private setFocusKey(sectionKey: string, rowKey: string): void {
    if (this.focusBySection.get(sectionKey) === rowKey) return;
    const next = new Map(this.focusBySection);
    next.set(sectionKey, rowKey);
    this.focusBySection = next;
  }

  private async focusRow(sectionKey: string, rowKey: string): Promise<void> {
    this.setFocusKey(sectionKey, rowKey);
    await this.updateComplete;
    const trees =
      this.renderRoot.querySelectorAll<HTMLElement>('[role="tree"]');
    for (const tree of trees) {
      if (tree.getAttribute("data-section") !== sectionKey) continue;
      for (const item of tree.querySelectorAll<HTMLElement>(
        '[role="treeitem"]',
      ))
        if (item.getAttribute("data-key") === rowKey) item.focus();
    }
  }

  protected override render(): TemplateResult {
    const localize = this.localize;
    const entries = this.entries;
    const showPicker = entries !== undefined && entries.length > 1;
    return html`
      <div class="panel">
        <header class="header">
          <div class="title-wrap">
            <span class="header-icon" aria-hidden="true">
              ${iconTemplate(mdiLan)}
            </span>
            <h1 class="title">${localize("panel.title")}</h1>
          </div>
          ${showPicker ? this.renderEntryPicker(entries, localize) : nothing}
        </header>
        ${this.renderNotices(localize)} ${this.renderBody(localize)}
      </div>
    `;
  }

  private renderEntryPicker(
    entries: readonly DeviceGroupSourceEntry[],
    localize: LocalizeFunc,
  ): TemplateResult {
    return html`
      <label class="entry-picker">
        <span class="picker-label">${localize("panel.entry_label")}</span>
        <select
          .value=${this.selectedEntryId ?? ""}
          @change=${this.onEntryChange}
        >
          ${entries.map(
            (entry) =>
              html`<option
                value=${entry.entry_id}
                ?selected=${entry.entry_id === this.selectedEntryId}
              >
                ${entry.title}
              </option>`,
          )}
        </select>
      </label>
    `;
  }

  private renderNotices(
    localize: LocalizeFunc,
  ): TemplateResult | typeof nothing {
    const notices: { kind: "warning" | "error" | "info"; text: string }[] = [];
    const activeSnapshot =
      this.snapshot?.status === "ok" ? this.snapshot : this.lastGood;

    if (this.incompatible) {
      notices.push({
        kind: "error",
        text: localize("panel.incompatible"),
      });
    } else if (
      this.groupingDisabled ||
      this.error?.code === ERR_GROUPING_DISABLED
    ) {
      notices.push({
        kind: "warning",
        text: localize("panel.grouping_disabled"),
      });
    } else {
      if (this.disconnected) {
        notices.push({
          kind: "warning",
          text: localize("panel.disconnected"),
        });
      }
      if (
        this.snapshot?.status === "entry_unloaded" ||
        (this.error !== undefined && RETRYABLE_ERRORS.has(this.error.code))
      ) {
        notices.push({
          kind: "info",
          text: localize("panel.reconnecting"),
        });
      } else if (this.error && activeSnapshot) {
        notices.push({
          kind: "error",
          text: this.formatError(this.error, localize),
        });
      }
      if (activeSnapshot?.stale) {
        notices.push({
          kind: "warning",
          text: localize("panel.stale"),
        });
      }
    }

    if (notices.length === 0) return nothing;
    return html`
      <div class="notices" role="status" aria-live="polite">
        ${notices.map(
          (n) => html`<div class="notice ${n.kind}">${n.text}</div>`,
        )}
      </div>
    `;
  }

  private formatError(error: WsError, localize: LocalizeFunc): string {
    if (error.code === ERR_ENTRY_NOT_FOUND)
      return localize("panel.error_not_found");
    return localize("panel.error", { code: error.code });
  }

  private renderBody(localize: LocalizeFunc): TemplateResult {
    if (this.incompatible) return html``;

    if (this.groupingDisabled || this.error?.code === ERR_GROUPING_DISABLED)
      return html``;

    if (this.sourcesError && this.entries === undefined) {
      if (RETRYABLE_ERRORS.has(this.sourcesError.code)) {
        return html`<p class="state-card">
          ${localize("panel.reconnecting")}
        </p>`;
      }
      return html`<p class="state-card error">
        ${this.formatError(this.sourcesError, localize)}
      </p>`;
    }

    if (this.entries === undefined) {
      return html`<p class="state-card">${localize("panel.loading")}</p>`;
    }

    if (this.entries.length === 0 && this.selectedEntryId === undefined) {
      return html`<p class="state-card">${localize("panel.empty_entries")}</p>`;
    }

    const activeSnapshot =
      this.snapshot?.status === "ok" ? this.snapshot : this.lastGood;

    if (!activeSnapshot) {
      if (
        this.error &&
        !RETRYABLE_ERRORS.has(this.error.code) &&
        this.snapshot?.status !== "entry_unloaded"
      ) {
        return html`<p class="state-card error">
          ${this.formatError(this.error, localize)}
        </p>`;
      }
      if (
        this.snapshot?.status === "entry_unloaded" ||
        (this.error && RETRYABLE_ERRORS.has(this.error.code))
      ) {
        return html``;
      }
      return html`<p class="state-card">${localize("panel.loading")}</p>`;
    }

    const visibleSections = activeSnapshot.sections.filter(
      (section) => section.rows.length > 0,
    );
    if (visibleSections.length === 0) {
      return html`<p class="state-card">${localize("panel.empty_devices")}</p>`;
    }

    return html`
      <div class="sections">
        ${visibleSections.map((section, index) =>
          this.renderSection(section, index, localize),
        )}
      </div>
    `;
  }

  private renderSection(
    section: DeviceGroupSection,
    index: number,
    localize: LocalizeFunc,
  ): TemplateResult {
    const title = sectionTitle(section, localize);
    const headingId = `section-heading-${index}`;
    const tree = buildSectionTree(section.rows);
    const flatRows = flattenSectionTree(tree, this.collapsed, section.key);
    const focusKey = this.focusKeyFor(section.key, flatRows);

    return html`
      <section class="section" aria-labelledby=${headingId}>
        <h2 id=${headingId} class="section-title">${title}</h2>
        <div
          class="tree"
          role="tree"
          aria-labelledby=${headingId}
          data-section=${section.key}
          @keydown=${(e: KeyboardEvent) =>
            this.onTreeKeydown(e, section.key, flatRows)}
        >
          ${repeat(
            flatRows,
            (item) => item.key,
            (item) => this.renderRow(section.key, item, focusKey, localize),
          )}
        </div>
      </section>
    `;
  }

  private renderRow(
    sectionKey: string,
    item: FlattenedDeviceRow,
    focusKey: string | undefined,
    localize: LocalizeFunc,
  ): TemplateResult {
    const { row } = item;
    const collapseId = collapseKeyFor(sectionKey, item.key);
    const href =
      row.ha_device_id !== null ? deviceHref(row.ha_device_id) : undefined;
    return html`
      <div
        class="row ${href ? "linked" : "plain"}"
        role="treeitem"
        data-key=${item.key}
        aria-level=${item.level}
        aria-setsize=${item.setsize}
        aria-posinset=${item.posinset}
        aria-expanded=${item.hasChildren ? String(item.expanded) : nothing}
        tabindex=${item.key === focusKey ? 0 : -1}
        style=${`--level: ${item.level}`}
        @click=${() => this.activateRow(row, item.hasChildren, collapseId)}
        @focus=${() => this.setFocusKey(sectionKey, item.key)}
      >
        <span
          class="chevron"
          aria-hidden="true"
          @click=${(e: Event) => {
            e.stopPropagation();
            if (item.hasChildren) this.toggleCollapse(collapseId);
          }}
        >
          ${item.hasChildren
            ? iconTemplate(item.expanded ? mdiChevronDown : mdiChevronRight)
            : nothing}
        </span>
        <span class="kind-icon" aria-hidden="true">
          ${iconTemplate(kindIcon(row.kind))}
        </span>
        <div class="row-main">
          ${href
            ? html`<a
                class="name device-link"
                href=${href}
                title=${row.name}
                tabindex="-1"
                @click=${(e: MouseEvent) => this.onLinkClick(e, href)}
                >${row.name}</a
              >`
            : html`<span class="name" title=${row.name}>${row.name}</span>`}
          ${row.port !== null || row.reason !== null
            ? html`<div class="meta">
                ${row.port !== null
                  ? html`<span class="badge port">
                      ${localize("panel.port", { port: row.port })}
                    </span>`
                  : nothing}
                ${row.reason !== null
                  ? html`<span class="badge reason">
                      ${localize(reasonKey(row.reason))}
                    </span>`
                  : nothing}
              </div>`
            : nothing}
        </div>
      </div>
    `;
  }

  static override styles = [
    themeTokens,
    controlStyles,
    css`
      :host {
        display: block;
        min-height: 100%;
        box-sizing: border-box;
        padding: 16px;
        background: var(
          --primary-background-color,
          var(--card-background-color, transparent)
        );
        --indent: 24px;
      }
      :host([narrow]) {
        padding: 12px;
        --indent: 16px;
      }
      .panel {
        max-width: 960px;
        margin: 0 auto;
        display: flex;
        flex-direction: column;
        gap: 16px;
        min-width: 0;
      }
      .header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
        gap: 12px;
      }
      .title-wrap {
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 0;
      }
      .header-icon {
        width: 40px;
        height: 40px;
        border-radius: 12px;
        display: grid;
        place-items: center;
        flex: none;
        background: color-mix(in srgb, var(--primary-color) 15%, transparent);
        color: var(--primary-color);
      }
      .title {
        margin: 0;
        font-size: 1.35rem;
        font-weight: 600;
        line-height: 1.25;
      }
      .entry-picker {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
        max-width: 100%;
      }
      .picker-label {
        font-size: 0.9rem;
        color: var(--ha-color-text-secondary, var(--secondary-text-color));
      }
      .entry-picker select {
        max-width: 100%;
      }
      .notices {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .notice {
        padding: 10px 14px;
        border-radius: 10px;
        border: 1px solid var(--uit-line);
        border-inline-start: 4px solid var(--uit-warning);
        background: var(
          --ha-color-fill-warning-quiet-resting,
          color-mix(in srgb, var(--uit-warning) 14%, transparent)
        );
        color: var(--ha-color-text-primary, var(--primary-text-color));
      }
      .notice.info {
        border-inline-start-color: var(--uit-focus);
        background: var(
          --ha-color-fill-primary-quiet-resting,
          color-mix(in srgb, var(--uit-focus) 12%, transparent)
        );
      }
      .notice.error {
        border-inline-start-color: var(--uit-offline);
        background: var(
          --ha-color-fill-danger-quiet-resting,
          color-mix(in srgb, var(--uit-offline) 14%, transparent)
        );
      }
      .state-card {
        margin: 0;
        padding: 16px;
        border-radius: 12px;
        background: var(--card-background-color);
        border: 1px solid var(--uit-line);
        color: var(--ha-color-text-secondary, var(--secondary-text-color));
      }
      .state-card.error {
        color: var(--ha-color-text-primary, var(--primary-text-color));
        border-inline-start: 4px solid var(--uit-offline);
      }
      .sections {
        display: flex;
        flex-direction: column;
        gap: 16px;
      }
      .section {
        background: var(--card-background-color);
        border: 1px solid var(--uit-line);
        border-radius: 12px;
        padding: 12px;
        min-width: 0;
      }
      .section-title {
        margin: 0 0 8px;
        font-size: 1rem;
        font-weight: 600;
        color: var(--ha-color-text-primary, var(--primary-text-color));
      }
      .tree {
        display: flex;
        flex-direction: column;
        gap: 2px;
      }
      .row {
        display: flex;
        align-items: center;
        gap: 8px;
        min-height: 44px;
        padding-block: 6px;
        padding-inline-start: calc(
          min(var(--level) - 1, 6) * var(--indent) + 6px
        );
        padding-inline-end: 10px;
        border-radius: 8px;
        min-width: 0;
        box-sizing: border-box;
      }
      .row.linked {
        cursor: pointer;
      }
      .row.linked:hover,
      .row:focus-visible {
        background: color-mix(in srgb, var(--uit-focus) 10%, transparent);
      }
      .chevron {
        width: 24px;
        height: 24px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        flex: none;
        cursor: pointer;
      }
      .kind-icon {
        display: inline-flex;
        align-items: center;
        flex: none;
        color: var(--ha-color-text-secondary, var(--secondary-text-color));
      }
      .row-main {
        flex: 1;
        min-width: 0;
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px 10px;
      }
      .name {
        min-width: 0;
        overflow-wrap: anywhere;
        font-weight: 500;
      }
      .device-link {
        color: var(--ha-color-text-primary, var(--primary-text-color));
        text-decoration: underline;
        text-underline-offset: 2px;
      }
      .meta {
        display: inline-flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px;
        margin-inline-start: auto;
      }
      .badge {
        display: inline-flex;
        align-items: center;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 0.78rem;
        line-height: 1.4;
        background: color-mix(
          in srgb,
          var(--primary-text-color) 8%,
          transparent
        );
        color: var(--ha-color-text-primary, var(--primary-text-color));
      }
      .badge.reason {
        background: var(
          --ha-color-fill-warning-quiet-resting,
          color-mix(in srgb, var(--uit-warning) 18%, transparent)
        );
      }
      @media (max-width: 480px) {
        :host {
          padding: 12px;
          --indent: 16px;
        }
        .meta {
          margin-inline-start: 0;
        }
      }
    `,
  ];
}

defineOnce(PANEL_TAG, UnifiInsightsDevicesPanel);

declare global {
  interface HTMLElementTagNameMap {
    [PANEL_TAG]: UnifiInsightsDevicesPanel;
  }
}
