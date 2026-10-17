import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import "../src/index";
import { allSites } from "../src/config";
import { NODE_KINDS } from "../src/contract";
import { makeLocalize } from "../src/localize";
import {
  STALE_SITE,
  buildSchema,
  ensureHaForm,
  fromFormData,
  toFormData,
  type EditorFormData,
  type FormField,
} from "../src/topology-card-editor";
import { SOURCES_MULTI, cleanup, fakeHass, settle } from "./helpers";

const TYPE = "custom:unifi-insights-topology-card";
const sites = allSites(SOURCES_MULTI);
const localize = makeLocalize("en");

class FakeHaForm extends HTMLElement {
  hass?: unknown;
  data?: EditorFormData;
  schema?: FormField[];
  computeLabel?: (field: FormField) => string;
}

beforeAll(() => {
  if (!customElements.get("ha-form"))
    customElements.define("ha-form", FakeHaForm);
});
afterEach(cleanup);

describe("form mapping", () => {
  it("maps a config to form data with defaults", () => {
    expect(
      toFormData({ type: TYPE, entry_id: "entry-1", site_id: "site-2" }, sites),
    ).toEqual({
      site: "1",
      title: "",
      view: "graph",
      clients: "collapsed",
      density: "auto",
      orientation: "vertical",
      layout: "tree",
      kinds: [...NODE_KINDS],
      show_site_selector: false,
      show_labels: true,
      show_wan: true,
      max_clients: 500,
    });
    expect(toFormData({ type: TYPE }, sites).site).toBe("");
  });

  it("writes show_wan only when WAN nodes are turned off", () => {
    const previous = { type: TYPE, grid_options: { columns: 12 } };
    const hidden = fromFormData(
      { ...toFormData(previous, sites), show_wan: false },
      previous,
      sites,
    );
    expect(hidden.show_wan).toBe(false);
    expect(toFormData(hidden, sites).show_wan).toBe(false);
    const shown = fromFormData(
      { ...toFormData(hidden, sites), show_wan: true },
      hidden,
      sites,
    );
    expect(shown).not.toHaveProperty("show_wan");
    expect(fromFormData({}, hidden, sites).show_wan).toBe(false);
  });

  it("writes a chosen site and keeps keys the form does not own", () => {
    const previous = { type: TYPE, grid_options: { columns: 12 } };
    const next = fromFormData(
      {
        ...toFormData(previous, sites),
        site: "2",
        title: "Office",
        view: "list",
      },
      previous,
      sites,
    );
    expect(next).toMatchObject({
      type: TYPE,
      entry_id: "entry-2",
      site_id: "default",
      title: "Office",
      view: "list",
      grid_options: { columns: 12 },
    });
  });

  it("drops defaults that have their own meaning when absent", () => {
    const previous = {
      type: TYPE,
      title: "Old",
      density: "compact" as const,
      kinds: ["gateway" as const],
    };
    const next = fromFormData(
      {
        ...toFormData(previous, sites),
        title: "",
        density: "auto",
        kinds: [...NODE_KINDS],
      },
      previous,
      sites,
    );
    expect(next).not.toHaveProperty("title");
    expect(next).not.toHaveProperty("density");
    expect(next).not.toHaveProperty("kinds");
  });

  it("writes the tiered layout and drops the key again for the default", () => {
    const previous = { type: TYPE, grid_options: { columns: 12 } };
    const tiered = fromFormData(
      { ...toFormData(previous, sites), layout: "tiered" },
      previous,
      sites,
    );
    expect(tiered).toMatchObject({
      layout: "tiered",
      grid_options: { columns: 12 },
    });
    expect(toFormData(tiered, sites).layout).toBe("tiered");
    const back = fromFormData(
      { ...toFormData(tiered, sites), layout: "tree" },
      tiered,
      sites,
    );
    expect(back).not.toHaveProperty("layout");
    expect(back).toMatchObject({ grid_options: { columns: 12 } });
  });

  it("ignores a layout value the card does not know", () => {
    const previous = { type: TYPE, layout: "tiered" as const };
    const next = fromFormData(
      { ...toFormData(previous, sites), layout: "isometric" },
      previous,
      sites,
    );
    expect(next.layout).toBe("tiered");
  });

  it("never saves an empty kind list or an out-of-range client cap", () => {
    const previous = {
      type: TYPE,
      kinds: ["gateway" as const],
      max_clients: 100,
    };
    const next = fromFormData(
      { ...toFormData(previous, sites), kinds: [], max_clients: 9000 },
      previous,
      sites,
    );
    expect(next.kinds).toEqual(["gateway"]);
    expect(next.max_clients).toBe(100);
  });

  it("keeps a stale site binding selectable", () => {
    const previous = {
      type: TYPE,
      entry_id: "entry-9",
      site_id: "gone",
      show_labels: true,
    };
    expect(toFormData(previous, sites).site).toBe(STALE_SITE);
    const siteField = buildSchema(previous, sites, localize)[0]!;
    expect(siteField.selector).toMatchObject({
      select: {
        options: expect.arrayContaining([
          { value: STALE_SITE, label: "gone (unavailable)" },
        ]),
      },
    });
    const next = fromFormData(
      { ...toFormData(previous, sites), show_labels: false },
      previous,
      sites,
    );
    expect(next).toMatchObject({
      entry_id: "entry-9",
      site_id: "gone",
      show_labels: false,
    });
  });

  it("labels every select option", () => {
    const schema = buildSchema({ type: TYPE }, sites, localize);
    const kinds = schema.find((f) => f.name === "kinds")!;
    expect(kinds.selector).toMatchObject({
      select: {
        multiple: true,
        options: expect.arrayContaining([
          { value: "access_point", label: "Access point" },
          { value: "wan", label: "WAN" },
        ]),
      },
    });
    expect(schema.find((f) => f.name === "show_wan")!.selector).toEqual({
      boolean: {},
    });
    expect(
      schema.find((f) => f.type === "grid")!.schema!.map((f) => f.name),
    ).toEqual(["view", "clients", "density", "orientation", "layout"]);
    const layout = schema
      .find((f) => f.type === "grid")!
      .schema!.find((f) => f.name === "layout")!;
    expect(layout.selector).toEqual({
      select: {
        mode: "dropdown",
        options: [
          { value: "tree", label: "Tree" },
          { value: "tiered", label: "Tiered by role" },
        ],
      },
    });
  });
});

describe("<unifi-insights-topology-card-editor>", () => {
  async function editor(config: Record<string, unknown>) {
    const el = document.createElement(
      "unifi-insights-topology-card-editor",
    ) as HTMLElement & {
      hass?: unknown;
      setConfig(c: unknown): void;
      updateComplete: Promise<boolean>;
    };
    el.hass = fakeHass({ sources: SOURCES_MULTI }).hass;
    el.setConfig({ type: TYPE, ...config });
    document.body.append(el);
    await settle(el);
    return el;
  }

  it("is what the card returns as its config element", () => {
    const Card = customElements.get(
      "unifi-insights-topology-card",
    ) as unknown as { getConfigElement(): HTMLElement };
    expect(Card.getConfigElement().tagName.toLowerCase()).toBe(
      "unifi-insights-topology-card-editor",
    );
  });

  it("renders ha-form with site options from the integration", async () => {
    const el = await editor({ entry_id: "entry-1", site_id: "site-1" });
    const form = el.shadowRoot!.querySelector("ha-form") as FakeHaForm;
    expect(form.data?.site).toBe("0");
    expect(form.schema![0]!.selector).toMatchObject({
      select: {
        options: [
          { value: "0", label: "Crestwood — Home" },
          { value: "1", label: "Crestwood — Cabin" },
          { value: "2", label: "Office — Default" },
        ],
      },
    });
    expect(form.computeLabel!({ name: "max_clients" })).toBe("Maximum clients");
    expect(form.computeLabel!({ name: "layout" })).toBe("Layout");
    expect(form.computeLabel!({ name: "show_wan" })).toBe(
      "Show internet connections (WAN)",
    );
  });

  it("fires config-changed with the merged config", async () => {
    const el = await editor({ entry_id: "entry-1", site_id: "site-1" });
    const changed = vi.fn();
    el.addEventListener("config-changed", (e) =>
      changed((e as CustomEvent).detail.config),
    );
    const form = el.shadowRoot!.querySelector("ha-form") as FakeHaForm;
    form.dispatchEvent(
      new CustomEvent("value-changed", {
        detail: { value: { ...form.data, site: "2" } },
      }),
    );
    expect(changed).toHaveBeenCalledWith(
      expect.objectContaining({
        type: TYPE,
        entry_id: "entry-2",
        site_id: "default",
      }),
    );
  });

  it("saves a chosen layout through config-changed", async () => {
    const el = await editor({ entry_id: "entry-1", site_id: "site-1" });
    const changed = vi.fn();
    el.addEventListener("config-changed", (e) =>
      changed((e as CustomEvent).detail.config),
    );
    const form = el.shadowRoot!.querySelector("ha-form") as FakeHaForm;
    expect(form.data?.layout).toBe("tree");
    form.dispatchEvent(
      new CustomEvent("value-changed", {
        detail: { value: { ...form.data, layout: "tiered" } },
      }),
    );
    expect(changed).toHaveBeenLastCalledWith(
      expect.objectContaining({ layout: "tiered" }),
    );
    await settle(el);
    expect(form.data?.layout).toBe("tiered");
    form.dispatchEvent(
      new CustomEvent("value-changed", {
        detail: { value: { ...form.data, layout: "tree" } },
      }),
    );
    expect(changed.mock.lastCall![0]).not.toHaveProperty("layout");
  });
});

describe("ensureHaForm", () => {
  it("forces HA to load ha-form through a built-in card editor", async () => {
    const getConfigElement = vi.fn(async () => undefined);
    class EntitiesCard extends HTMLElement {
      static getConfigElement = getConfigElement;
    }
    const createCardElement = vi.fn(() => new EntitiesCard());
    if (!customElements.get("fake-entities-card"))
      customElements.define("fake-entities-card", EntitiesCard);
    await ensureHaForm(
      { get: () => undefined } as unknown as CustomElementRegistry,
      async () => ({ createCardElement }),
    );
    expect(createCardElement).toHaveBeenCalledWith({
      type: "entities",
      entities: [],
    });
    expect(getConfigElement).toHaveBeenCalledOnce();
  });

  it("does nothing when ha-form already exists", async () => {
    const load = vi.fn();
    await ensureHaForm(customElements, load);
    expect(load).not.toHaveBeenCalled();
  });
});
