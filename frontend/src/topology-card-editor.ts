import { LitElement, html, nothing, type TemplateResult } from "lit";
import {
  CLIENT_MODES,
  DENSITIES,
  LAYOUTS,
  ORIENTATIONS,
  VIEWS,
  allSites,
  sameBinding,
  type ClientsMode,
  type Density,
  type LayoutMode,
  type Orientation,
  type SiteOption,
  type TopologyCardConfig,
} from "./config";
import {
  MAX_CLIENTS_PER_SITE,
  NODE_KINDS,
  WS_SOURCES,
  type NodeKind,
  type TopologySource,
} from "./contract";
import { fireEvent, type CardHelpers, type HomeAssistant } from "./ha-types";
import { makeLocalize, type LocalizeFunc, type LocalizeKey } from "./localize";
import { KIND_KEYS, VIEW_KEYS } from "./views/describe";

/** Site value for a configured site that no longer exists: kept, labelled "(unavailable)". */
export const STALE_SITE = "current";
const NO_SITE = "";

export interface EditorFormData {
  site: string;
  title: string;
  view: string;
  clients: string;
  density: string;
  orientation: string;
  layout: string;
  kinds: string[];
  show_site_selector: boolean;
  show_labels: boolean;
  show_wan: boolean;
  max_clients: number;
}

export interface FormField {
  name: string;
  type?: "grid";
  selector?: Record<string, unknown>;
  schema?: FormField[];
}

const CLIENT_KEYS: Record<ClientsMode, LocalizeKey> = {
  collapsed: "clients.collapsed",
  expanded: "clients.expanded",
  hidden: "clients.hidden",
};
const DENSITY_KEYS: Record<"auto" | Density, LocalizeKey> = {
  auto: "density.auto",
  comfortable: "density.comfortable",
  compact: "density.compact",
};
const ORIENTATION_KEYS: Record<Orientation, LocalizeKey> = {
  vertical: "orientation.vertical",
  horizontal: "orientation.horizontal",
};
const LAYOUT_KEYS: Record<LayoutMode, LocalizeKey> = {
  tree: "layout.tree",
  tiered: "layout.tiered",
};
const LABEL_KEYS: Record<string, LocalizeKey> = {
  site: "editor.site",
  title: "editor.title",
  view: "editor.view",
  clients: "editor.clients",
  kinds: "editor.kinds",
  density: "editor.density",
  orientation: "editor.orientation",
  layout: "editor.layout",
  show_site_selector: "editor.show_site_selector",
  show_labels: "editor.show_labels",
  show_wan: "editor.show_wan",
  max_clients: "editor.max_clients",
};

function options<T extends string>(
  values: readonly T[],
  keys: Record<T, LocalizeKey>,
  localize: LocalizeFunc,
): { value: T; label: string }[] {
  return values.map((value) => ({ value, label: localize(keys[value]) }));
}

const isOneOf = <T extends string>(
  value: unknown,
  allowed: readonly T[],
): value is T =>
  typeof value === "string" && (allowed as readonly string[]).includes(value);

export function siteValue(
  config: TopologyCardConfig,
  sites: SiteOption[],
): string {
  const { entry_id, site_id } = config;
  if (entry_id === undefined || site_id === undefined) return NO_SITE;
  const index = sites.findIndex((s) =>
    sameBinding(s.binding, { entry_id, site_id }),
  );
  return index >= 0 ? String(index) : STALE_SITE;
}

/** The ha-form schema for every card option, with localized option labels. */
export function buildSchema(
  config: TopologyCardConfig,
  sites: SiteOption[],
  localize: LocalizeFunc,
): FormField[] {
  const siteOptions = sites.map((s, i) => ({
    value: String(i),
    label: s.label,
  }));
  if (siteValue(config, sites) === STALE_SITE) {
    siteOptions.push({
      value: STALE_SITE,
      label: localize("editor.site_unavailable", {
        site: config.site_id ?? "",
      }),
    });
  }
  return [
    {
      name: "site",
      selector: { select: { mode: "dropdown", options: siteOptions } },
    },
    { name: "title", selector: { text: {} } },
    {
      name: "",
      type: "grid",
      schema: [
        {
          name: "view",
          selector: {
            select: {
              mode: "dropdown",
              options: options(VIEWS, VIEW_KEYS, localize),
            },
          },
        },
        {
          name: "clients",
          selector: {
            select: {
              mode: "dropdown",
              options: options(CLIENT_MODES, CLIENT_KEYS, localize),
            },
          },
        },
        {
          name: "density",
          selector: {
            select: {
              mode: "dropdown",
              options: options(
                ["auto", ...DENSITIES] as const,
                DENSITY_KEYS,
                localize,
              ),
            },
          },
        },
        {
          name: "orientation",
          selector: {
            select: {
              mode: "dropdown",
              options: options(ORIENTATIONS, ORIENTATION_KEYS, localize),
            },
          },
        },
        {
          name: "layout",
          selector: {
            select: {
              mode: "dropdown",
              options: options(LAYOUTS, LAYOUT_KEYS, localize),
            },
          },
        },
      ],
    },
    {
      name: "kinds",
      selector: {
        select: {
          multiple: true,
          mode: "list",
          options: options(NODE_KINDS, KIND_KEYS, localize),
        },
      },
    },
    { name: "show_site_selector", selector: { boolean: {} } },
    { name: "show_labels", selector: { boolean: {} } },
    { name: "show_wan", selector: { boolean: {} } },
    {
      name: "max_clients",
      selector: {
        number: { min: 1, max: MAX_CLIENTS_PER_SITE, mode: "box" },
      },
    },
  ];
}

/** Config to form values, filling each option's default. */
export function toFormData(
  config: TopologyCardConfig,
  sites: SiteOption[],
): EditorFormData {
  return {
    site: siteValue(config, sites),
    title: config.title ?? "",
    view: config.view ?? "graph",
    clients: config.clients ?? "collapsed",
    density: config.density ?? "auto",
    orientation: config.orientation ?? "vertical",
    layout: config.layout ?? "tree",
    kinds: config.kinds ? [...config.kinds] : [...NODE_KINDS],
    show_site_selector: config.show_site_selector ?? false,
    show_labels: config.show_labels ?? true,
    show_wan: config.show_wan ?? true,
    max_clients: config.max_clients ?? MAX_CLIENTS_PER_SITE,
  };
}

/** Merge edited form values into the previous config, keeping keys the form does not own. */
export function fromFormData(
  data: Partial<EditorFormData>,
  previous: TopologyCardConfig,
  sites: SiteOption[],
): TopologyCardConfig {
  const next: TopologyCardConfig = { ...previous };
  const site = data.site ?? NO_SITE;
  if (site === NO_SITE) {
    delete next.entry_id;
    delete next.site_id;
  } else if (site !== STALE_SITE) {
    const chosen = sites[Number(site)];
    if (chosen) {
      next.entry_id = chosen.binding.entry_id;
      next.site_id = chosen.binding.site_id;
    }
  }
  if (data.title) next.title = data.title;
  else delete next.title;
  if (isOneOf(data.view, VIEWS)) next.view = data.view;
  if (isOneOf(data.clients, CLIENT_MODES)) next.clients = data.clients;
  if (isOneOf(data.orientation, ORIENTATIONS))
    next.orientation = data.orientation;
  if (data.layout === "tree") delete next.layout;
  else if (isOneOf(data.layout, LAYOUTS)) next.layout = data.layout;
  if (isOneOf(data.density, DENSITIES)) next.density = data.density;
  else if (data.density === "auto") delete next.density;
  const kinds = (data.kinds ?? []).filter((k): k is NodeKind =>
    isOneOf(k, NODE_KINDS),
  );
  if (kinds.length === NODE_KINDS.length) delete next.kinds;
  else if (kinds.length > 0)
    next.kinds = NODE_KINDS.filter((k) => kinds.includes(k));
  if (typeof data.show_site_selector === "boolean")
    next.show_site_selector = data.show_site_selector;
  if (typeof data.show_labels === "boolean")
    next.show_labels = data.show_labels;
  // Shown by default: only an opt-out is written to the YAML.
  if (data.show_wan === true) delete next.show_wan;
  else if (data.show_wan === false) next.show_wan = false;
  const max = data.max_clients;
  // Absent means "the server cap"; pinning today's cap would outlive a change to it.
  if (max === MAX_CLIENTS_PER_SITE) delete next.max_clients;
  else if (
    typeof max === "number" &&
    Number.isInteger(max) &&
    max >= 1 &&
    max <= MAX_CLIENTS_PER_SITE
  )
    next.max_clients = max;
  return next;
}

/**
 * ha-form is lazy-loaded by HA and may not exist when a custom editor opens.
 * Creating a built-in card's config element forces it to register.
 */
export async function ensureHaForm(
  registry: CustomElementRegistry = customElements,
  loadHelpers:
    | (() => Promise<CardHelpers>)
    | undefined = window.loadCardHelpers,
): Promise<void> {
  if (registry.get("ha-form")) return;
  const helpers = await loadHelpers?.();
  const card = helpers?.createCardElement({ type: "entities", entities: [] });
  const ctor = card?.constructor as
    | { getConfigElement?: () => Promise<unknown> }
    | undefined;
  await ctor?.getConfigElement?.();
}

/** Visual editor: site picker from `topology/sources` plus every card option. */
export class UnifiInsightsTopologyCardEditor extends LitElement {
  static override properties = {
    hass: { attribute: false },
    config: { state: true },
    sources: { state: true },
    formReady: { state: true },
  };

  declare hass?: HomeAssistant;
  declare config?: TopologyCardConfig;
  declare sources?: TopologySource[];
  declare formReady: boolean;

  private sourcesRequested = false;

  constructor() {
    super();
    this.formReady = false;
  }

  setConfig(config: TopologyCardConfig): void {
    this.config = { ...config };
  }

  override connectedCallback(): void {
    super.connectedCallback();
    void ensureHaForm()
      .catch(() => undefined)
      .then(() => {
        this.formReady = true;
      });
  }

  protected override willUpdate(): void {
    if (this.hass && !this.sourcesRequested) {
      this.sourcesRequested = true;
      this.hass.callWS<TopologySource[]>({ type: WS_SOURCES }).then(
        (sources) => {
          this.sources = sources;
        },
        () => {
          this.sources = [];
        },
      );
    }
  }

  protected override render(): TemplateResult | typeof nothing {
    const { hass, config } = this;
    if (!hass || !config) return nothing;
    const localize = makeLocalize(hass.locale?.language ?? hass.language);
    if (!this.formReady) return html`<p>${localize("state.loading")}</p>`;
    const sites = allSites(this.sources ?? []);
    return html`<ha-form
      .hass=${hass}
      .data=${toFormData(config, sites)}
      .schema=${buildSchema(config, sites, localize)}
      .computeLabel=${(field: FormField) => {
        const key = LABEL_KEYS[field.name];
        return key ? localize(key) : "";
      }}
      @value-changed=${this.onValueChanged}
    ></ha-form>`;
  }

  private readonly onValueChanged = (e: Event): void => {
    e.stopPropagation();
    const previous = this.config;
    if (!previous) return;
    const value = (e as CustomEvent<{ value: Partial<EditorFormData> }>).detail
      .value;
    const next = fromFormData(value, previous, allSites(this.sources ?? []));
    this.config = next;
    fireEvent(this, "config-changed", { config: next });
  };
}
