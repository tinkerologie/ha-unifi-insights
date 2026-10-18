import {
  MAX_CLIENTS_PER_SITE,
  NODE_KINDS,
  type NodeKind,
  type TopologySource,
} from "./contract";

export const CARD_TAG = "unifi-insights-topology-card";
export const EDITOR_TAG = "unifi-insights-topology-card-editor";
export const CARD_TYPE = `custom:${CARD_TAG}`;

export const VIEWS = ["graph", "list"] as const;
export type ViewMode = (typeof VIEWS)[number];
export const CLIENT_MODES = ["collapsed", "expanded", "hidden"] as const;
export type ClientsMode = (typeof CLIENT_MODES)[number];
export const DENSITIES = ["comfortable", "compact"] as const;
export type Density = (typeof DENSITIES)[number];
export const ORIENTATIONS = ["vertical", "horizontal"] as const;
export type Orientation = (typeof ORIENTATIONS)[number];
export const LAYOUTS = ["tree", "tiered"] as const;
export type LayoutMode = (typeof LAYOUTS)[number];

/** Card YAML. Every key but `type` is optional; unknown keys (grid_options, visibility, ...) are kept. */
export interface TopologyCardConfig {
  type: string;
  entry_id?: string;
  site_id?: string;
  title?: string;
  view?: ViewMode;
  show_site_selector?: boolean;
  clients?: ClientsMode;
  kinds?: NodeKind[];
  density?: Density;
  orientation?: Orientation;
  layout?: LayoutMode;
  show_labels?: boolean;
  show_wan?: boolean;
  max_clients?: number;
  [key: string]: unknown;
}

export interface ResolvedConfig {
  entry_id: string | undefined;
  site_id: string | undefined;
  title: string | undefined;
  view: ViewMode;
  show_site_selector: boolean;
  clients: ClientsMode;
  kinds: NodeKind[];
  /** undefined = automatic (compact below 600 px card width). */
  density: Density | undefined;
  orientation: Orientation;
  layout: LayoutMode;
  show_labels: boolean;
  /** false drops the `wan` kind from the graph, the list and the filter chips. */
  show_wan: boolean;
  max_clients: number;
}

export interface SiteBinding {
  entry_id: string;
  site_id: string;
}

export interface SiteOption {
  binding: SiteBinding;
  label: string;
}

function checkOneOf(
  value: unknown,
  allowed: readonly string[],
  key: string,
): void {
  if (
    value !== undefined &&
    !(typeof value === "string" && allowed.includes(value))
  ) {
    throw new Error(`${key} must be one of: ${allowed.join(", ")}`);
  }
}

/** Throw a readable Error for an invalid config; HA shows it as the standard error card. */
export function validateConfig(raw: unknown): TopologyCardConfig {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new Error("Card configuration must be an object");
  }
  const c = raw as Record<string, unknown>;
  if (typeof c.type !== "string") throw new Error("type is required");
  for (const key of ["entry_id", "site_id", "title"] as const) {
    const value = c[key];
    if (value !== undefined && (typeof value !== "string" || value === "")) {
      throw new Error(`${key} must be a non-empty string`);
    }
  }
  if ((c.entry_id === undefined) !== (c.site_id === undefined)) {
    throw new Error("entry_id and site_id must be set together");
  }
  checkOneOf(c.view, VIEWS, "view");
  checkOneOf(c.clients, CLIENT_MODES, "clients");
  checkOneOf(c.density, DENSITIES, "density");
  checkOneOf(c.orientation, ORIENTATIONS, "orientation");
  checkOneOf(c.layout, LAYOUTS, "layout");
  for (const key of [
    "show_site_selector",
    "show_labels",
    "show_wan",
  ] as const) {
    if (c[key] !== undefined && typeof c[key] !== "boolean")
      throw new Error(`${key} must be true or false`);
  }
  if (
    c.kinds !== undefined &&
    (!Array.isArray(c.kinds) ||
      c.kinds.length === 0 ||
      !c.kinds.every((k) => (NODE_KINDS as readonly unknown[]).includes(k)))
  ) {
    throw new Error(
      `kinds must be a non-empty list of: ${NODE_KINDS.join(", ")}`,
    );
  }
  const max = c.max_clients;
  if (
    max !== undefined &&
    (typeof max !== "number" ||
      !Number.isInteger(max) ||
      max < 1 ||
      max > MAX_CLIENTS_PER_SITE)
  ) {
    throw new Error(
      `max_clients must be a whole number from 1 to ${MAX_CLIENTS_PER_SITE}`,
    );
  }
  return c as TopologyCardConfig;
}

/** Fill every option the YAML leaves out; `show_wan: false` also drops the `wan` kind. */
export function resolveConfig(c: TopologyCardConfig): ResolvedConfig {
  const showWan = c.show_wan ?? true;
  return {
    entry_id: c.entry_id,
    site_id: c.site_id,
    title: c.title,
    view: c.view ?? "graph",
    show_site_selector: c.show_site_selector ?? false,
    clients: c.clients ?? "collapsed",
    kinds: (c.kinds ?? NODE_KINDS).filter((k) => showWan || k !== "wan"),
    density: c.density,
    orientation: c.orientation ?? "vertical",
    layout: c.layout ?? "tree",
    show_labels: c.show_labels ?? true,
    show_wan: showWan,
    max_clients: c.max_clients ?? MAX_CLIENTS_PER_SITE,
  };
}

export function allSites(sources: readonly TopologySource[]): SiteOption[] {
  return sources.flatMap((source) =>
    source.sites.map((site) => ({
      binding: { entry_id: source.entry_id, site_id: site.id },
      label: `${source.title} — ${site.name}`,
    })),
  );
}

/** The configured site, or the only site when exactly one exists. */
export function resolveBinding(
  config: ResolvedConfig,
  sources: readonly TopologySource[] | undefined,
): SiteBinding | undefined {
  if (config.entry_id !== undefined && config.site_id !== undefined) {
    return { entry_id: config.entry_id, site_id: config.site_id };
  }
  const sites = allSites(sources ?? []);
  return sites.length === 1 ? sites[0]!.binding : undefined;
}

export function sameBinding(
  a: SiteBinding | undefined,
  b: SiteBinding | undefined,
): boolean {
  return a?.entry_id === b?.entry_id && a?.site_id === b?.site_id;
}
