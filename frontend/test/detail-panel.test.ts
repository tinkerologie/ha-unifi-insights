import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type NodeDetails } from "../src/contract";
import type { HomeAssistant } from "../src/ha-types";
import { makeLocalize } from "../src/localize";
import { UitDetailPanel } from "../src/views/detail-panel";
import type { UitNodeMenu } from "../src/views/node-menu";
import { cleanup, fixtureModel, fixtureSnapshot, mount } from "./helpers";

const localize = makeLocalize("en");
afterEach(cleanup);

const panel = (selectedId: string | undefined) =>
  mount<UitDetailPanel>("uit-detail-panel", {
    model: fixtureModel(),
    localize,
    selectedId,
  });
const rows = (el: UitDetailPanel) =>
  Object.fromEntries(
    [...el.shadowRoot!.querySelectorAll(".row")].map((r) => [
      r.querySelector("dt")!.textContent,
      r.querySelector("dd")!.textContent,
    ]),
  );

it("shows a device with its uplink and an Open device link", async () => {
  const el = await panel("dev:uuid-core");
  expect(el.shadowRoot!.querySelector("h3")!.textContent).toBe("Core 24");
  expect(rows(el)).toMatchObject({
    Type: "Switch",
    Model: "USW Pro Max 24",
    State: "Online",
    "Connected to": "Gateway",
    Port: "11 → 26",
    Speed: "10G",
    Link: "Wired",
  });
  el.shadowRoot!.querySelector<HTMLButtonElement>("button.action")!.click();
  expect(location.pathname).toBe("/config/devices/device/reg-core");
});

it("shows PoE draw on a powered link", async () => {
  expect(rows(await panel("dev:uuid-ap"))).toMatchObject({ PoE: "12.3 W" });
});

it("shows a client hidden inside a collapsed group", async () => {
  const el = await panel("cli:cli-tv");
  expect(rows(el)).toMatchObject({
    Connection: "Wired",
    VLAN: "3",
    Network: "Media",
    "Connected to": "Ultra A",
    Port: "4",
  });
  expect(el.shadowRoot!.querySelector("button.action")).toBeNull();
});

it("lists and searches group members, selecting one on click", async () => {
  const el = await panel("group:dev:uuid-u1");
  const selected = vi.fn();
  el.addEventListener("uit-select", (e) => selected((e as CustomEvent).detail));
  el.shadowRoot!.querySelector<HTMLButtonElement>("button.member")!.click();
  expect(selected).toHaveBeenCalledWith({ id: "cli:cli-tv" });
  const search = el.shadowRoot!.querySelector<HTMLInputElement>("input")!;
  search.value = "zzz";
  search.dispatchEvent(new Event("input"));
  await el.updateComplete;
  expect(el.shadowRoot!.querySelectorAll("button.member")).toHaveLength(0);
});

it("closes on request and renders nothing without a selection", async () => {
  const el = await panel("dev:uuid-gw");
  const closed = vi.fn();
  el.addEventListener("uit-close", closed);
  el.shadowRoot!.querySelector<HTMLButtonElement>("button.close")!.click();
  expect(closed).toHaveBeenCalledOnce();
  const empty = await panel("dev:gone");
  expect(empty.shadowRoot!.querySelector("section")).toBeNull();
});

it("shows details in a modal dialog that closes the panel", async () => {
  // jsdom has no dialog methods or layout, so stub them and force the mode.
  // Browsers fire "close" in a later task, so the stub defers it too.
  const proto = HTMLDialogElement.prototype;
  proto.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.open = true;
  });
  proto.close = vi.fn(function (this: HTMLDialogElement) {
    this.open = false;
    queueMicrotask(() => this.dispatchEvent(new Event("close")));
  });
  const settle = () => new Promise((resolve) => setTimeout(resolve));
  try {
    const el = await panel("dev:uuid-core");
    el.modal = true;
    await el.updateComplete;
    const dialog = el.shadowRoot!.querySelector("dialog")!;
    expect(dialog.open).toBe(true);
    expect(dialog.getAttribute("aria-labelledby")).toBe("title");
    expect(dialog.querySelector("section")!.hasAttribute("role")).toBe(false);
    expect(dialog.querySelector("#title")!.textContent).toBe("Core 24");
    const closed = vi.fn();
    el.addEventListener("uit-close", closed);

    // A click inside the panel, or a drag from it onto the backdrop,
    // keeps the dialog open.
    const dl = dialog.querySelector<HTMLElement>("dl")!;
    dl.click();
    dl.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    dialog.click();
    await settle();
    expect(closed).not.toHaveBeenCalled();

    // A press and release on the backdrop closes it.
    dialog.dispatchEvent(new Event("pointerdown"));
    dialog.click();
    await settle();
    expect(closed).toHaveBeenCalledOnce();

    // Escape and Close go through the dialog, not straight to uit-close.
    for (const close of [
      () =>
        dialog.dispatchEvent(
          new KeyboardEvent("keydown", {
            key: "Escape",
            bubbles: true,
          }),
        ),
      () => dialog.querySelector<HTMLButtonElement>("button.close")!.click(),
    ]) {
      dialog.open = true;
      closed.mockClear();
      close();
      expect(dialog.open).toBe(false);
      await settle();
      expect(closed).toHaveBeenCalledOnce();
    }

    dialog.open = true;
    dialog.querySelector<HTMLButtonElement>("button.action")!.click();
    expect(dialog.open).toBe(false);
    expect(location.pathname).toBe("/config/devices/device/reg-core");

    // Detaching the panel (HA suspending the view) closes the dialog.
    dialog.open = true;
    el.remove();
    expect(dialog.open).toBe(false);
    document.body.append(el);

    el.selectedId = undefined;
    await el.updateComplete;
    expect(el.modal).toBe(false);
  } finally {
    delete (proto as Partial<HTMLDialogElement>).showModal;
    delete (proto as Partial<HTMLDialogElement>).close;
  }
});

it("constrains .panel as a shrinkable flex child with a sticky header so narrow panels fit or scroll cleanly", () => {
  // jsdom does not compute layout, so verify the CSS contract directly.
  const cssText = UitDetailPanel.styles.map((s) => s.cssText).join("\n");
  expect(cssText).toMatch(
    /:host\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;/s,
  );
  expect(cssText).toMatch(
    /:host\(\[narrow\]\)\s*\{[^}]*max-height:\s*calc\(100%\s*-\s*8px\);/s,
  );
  expect(cssText).toMatch(
    /\.panel\s*\{[^}]*flex:\s*1 1 auto;[^}]*min-height:\s*0;[^}]*overflow:\s*auto;/s,
  );
  expect(cssText).toMatch(/header\s*\{[^}]*position:\s*sticky;[^}]*top:\s*0;/s);
  expect(cssText).not.toMatch(/\.panel\s*\{[^}]*height:\s*100%;/s);
});

describe("node actions", () => {
  const DETAILS: NodeDetails = {
    kind: "switch",
    ip_address: "192.0.2.10",
    mac_address: "aa:bb:cc:dd:ee:ff",
    device_id: "reg-core",
    entity_id: "sensor.core_status",
  };
  const CLIENT: NodeDetails = {
    kind: "client",
    ip_address: "192.0.2.77",
    entity_id: "device_tracker.tv",
  };

  const hassOf = (
    isAdminUser: boolean | undefined,
    callWS: ReturnType<typeof vi.fn>,
  ) =>
    ({
      callWS,
      user: isAdminUser === undefined ? undefined : { is_admin: isAdminUser },
    }) as unknown as HomeAssistant;

  /** callWS answering each node from a table, after an optional gate. */
  const answering = (table: Record<string, NodeDetails>) =>
    vi.fn(async (message: { node_id: string }) => {
      const answer = table[message.node_id];
      if (!answer) throw { code: "node_not_found", message: "" };
      return answer;
    });

  const adminPanel = (
    selectedId: string | undefined,
    callWS: ReturnType<typeof vi.fn> = answering({
      "dev:uuid-core": DETAILS,
      "cli:cli-tv": CLIENT,
    }),
    props: Record<string, unknown> = {},
  ) =>
    mount<UitDetailPanel>("uit-detail-panel", {
      model: fixtureModel(),
      localize,
      selectedId,
      hass: hassOf(true, callWS),
      details: selectedId?.startsWith("dev:")
        ? DETAILS
        : selectedId?.startsWith("cli:")
          ? CLIENT
          : undefined,
      ...props,
    });

  const menuOf = (el: UitDetailPanel) =>
    el.shadowRoot!.querySelector<UitNodeMenu>("uit-node-menu");
  const labelsOf = (menu: UitNodeMenu) =>
    [...menu.shadowRoot!.querySelectorAll('[role="menuitem"]')].map((i) =>
      i.textContent?.trim(),
    );
  const itemOf = (menu: UitNodeMenu, action: string) =>
    menu.shadowRoot!.querySelector<HTMLElement>(`[data-action="${action}"]`)!;
  const status = (el: UitDetailPanel) =>
    el.shadowRoot!.querySelector<HTMLElement>('[role="status"]')!;
  /** Open the menu and wait for the details and the menu to render. */
  async function openMenu(el: UitDetailPanel): Promise<UitNodeMenu> {
    await settleAll(el);
    const menu = menuOf(el)!;
    menu
      .shadowRoot!.querySelector<HTMLButtonElement>("button.trigger")!
      .click();
    await settleAll(el);
    return menu;
  }
  async function settleAll(el: UitDetailPanel): Promise<void> {
    for (let i = 0; i < 3; i++) {
      await new Promise((resolve) => setTimeout(resolve, 0));
      await el.updateComplete;
      await menuOf(el)?.updateComplete;
    }
  }

  function stubClipboard(writeText: (text: string) => Promise<void>) {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    });
  }
  // jsdom has no dialog methods; browsers fire "close" in a later task.
  const proto = HTMLDialogElement.prototype;
  beforeEach(() => {
    proto.showModal = vi.fn(function (this: HTMLDialogElement) {
      this.open = true;
    });
    proto.close = vi.fn(function (this: HTMLDialogElement) {
      this.open = false;
      queueMicrotask(() => this.dispatchEvent(new Event("close")));
    });
  });
  afterEach(() => {
    // Detaching a panel with an open dialog calls close(): detach first.
    cleanup();
    delete (proto as Partial<HTMLDialogElement>).showModal;
    delete (proto as Partial<HTMLDialogElement>).close;
    delete (navigator as { clipboard?: unknown }).clipboard;
    delete (document as { execCommand?: unknown }).execCommand;
  });

  it("hides supplied device actions for a non-administrator, who keeps the device link", async () => {
    for (const admin of [false, undefined]) {
      const el = await adminPanel("dev:uuid-core", undefined, {
        hass: hassOf(admin, vi.fn()),
      });
      await settleAll(el);
      expect(menuOf(el)).toBeNull();
      // The button next to the rows stays exactly as it was.
      expect(
        el.shadowRoot!.querySelector("button.action")!.textContent,
      ).toContain("Open device");
    }
  });

  it("hides supplied client actions for non-administrators even without a device link", async () => {
    for (const admin of [false, undefined]) {
      const el = await adminPanel("cli:cli-tv", undefined, {
        hass: hassOf(admin, vi.fn()),
        details: CLIENT,
      });
      await settleAll(el);
      expect(menuOf(el)).toBeNull();
      expect(el.shadowRoot!.querySelector("button.action")).toBeNull();
    }
  });

  it("starts every selection with a closed menu", async () => {
    const el = await adminPanel("dev:uuid-core");
    const menu = await openMenu(el);
    expect(menu.shadowRoot!.querySelector('[role="menu"]')).not.toBeNull();
    el.selectedId = "cli:cli-tv";
    el.details = CLIENT;
    await settleAll(el);
    const next = menuOf(el)!;
    expect(next).not.toBe(menu);
    expect(next.shadowRoot!.querySelector('[role="menu"]')).toBeNull();
  });

  it("hides the actions a client has no data for", async () => {
    const el = await adminPanel("cli:cli-tv");
    await settleAll(el);
    expect(menuOf(el)!.actions).toEqual(["copy_ip", "open_entity"]);
  });

  it("renders no action menu for a client group even with supplied details", async () => {
    const el = await adminPanel("group:dev:uuid-u1", undefined, {
      details: DETAILS,
    });
    await settleAll(el);
    expect(el.shadowRoot!.querySelector("h3")!.textContent).toBe("1 client");
    expect(menuOf(el)).toBeNull();
  });

  it("always has a polite live region, empty until there is something to say", async () => {
    const el = await adminPanel("dev:uuid-core");
    await settleAll(el);
    const region = status(el);
    expect(region.getAttribute("aria-live")).toBe("polite");
    expect(region.textContent?.trim()).toBe("");
  });

  it("copies the IP address and reports it in the live region", async () => {
    const writeText = vi.fn(async () => undefined);
    stubClipboard(writeText);
    const el = await adminPanel("dev:uuid-core");
    const menu = await openMenu(el);
    itemOf(menu, "copy_ip").click();
    await settleAll(el);
    expect(writeText).toHaveBeenCalledExactlyOnceWith("192.0.2.10");
    expect(status(el).textContent).toContain(
      "IP address copied to the clipboard.",
    );
    expect(status(el).querySelector(".manual")).toBeNull();
    expect(menu.shadowRoot!.querySelector('[role="menu"]')).toBeNull();
  });

  it("copies the MAC address", async () => {
    const writeText = vi.fn(async () => undefined);
    stubClipboard(writeText);
    const el = await adminPanel("dev:uuid-core");
    const menu = await openMenu(el);
    itemOf(menu, "copy_mac").click();
    await settleAll(el);
    expect(writeText).toHaveBeenCalledExactlyOnceWith("aa:bb:cc:dd:ee:ff");
    expect(status(el).textContent).toContain(
      "MAC address copied to the clipboard.",
    );
  });

  it("shows the value as selectable text when copying fails", async () => {
    stubClipboard(async () => {
      throw new DOMException("denied", "NotAllowedError");
    });
    const selection = vi.spyOn(window.getSelection()!, "addRange");
    const el = await adminPanel("dev:uuid-core");
    const menu = await openMenu(el);
    itemOf(menu, "copy_mac").click();
    await settleAll(el);
    expect(status(el).textContent).toContain(
      "Couldn't copy. Select the value below to copy it by hand.",
    );
    const manual = status(el).querySelector(".manual")!;
    expect(manual.textContent).toBe("aa:bb:cc:dd:ee:ff");
    expect(selection).toHaveBeenCalledOnce();
    const range = selection.mock.calls[0]![0];
    expect(range.startContainer).toBe(manual);
    expect(range.endOffset).toBe(manual.childNodes.length);
    expect(range.toString()).toBe("aa:bb:cc:dd:ee:ff");
    selection.mockRestore();
    expect(
      UitDetailPanel.styles.map((style) => style.cssText).join("\n"),
    ).toMatch(/user-select:\s*text/);
  });

  it("copies through the textarea fallback inside the open dialog", async () => {
    let host: Node | null = null;
    (document as unknown as { execCommand: () => boolean }).execCommand =
      () => {
        host = el.shadowRoot!.querySelector("textarea")?.parentNode ?? null;
        return true;
      };
    const el = await adminPanel("dev:uuid-core");
    el.modal = true;
    await settleAll(el);
    const menu = await openMenu(el);
    itemOf(menu, "copy_ip").click();
    await settleAll(el);
    expect(host).toBe(el.shadowRoot!.querySelector("dialog"));
    expect(el.shadowRoot!.querySelector("textarea")).toBeNull();
    expect(status(el).textContent).toContain(
      "IP address copied to the clipboard.",
    );
  });

  it("clears the message when another node is selected", async () => {
    stubClipboard(async () => undefined);
    const el = await adminPanel("dev:uuid-core");
    const menu = await openMenu(el);
    itemOf(menu, "copy_ip").click();
    await settleAll(el);
    expect(status(el).textContent).toContain("copied");
    el.selectedId = "cli:cli-tv";
    await settleAll(el);
    expect(status(el).textContent?.trim()).toBe("");
  });

  it("does not report a copy that finished after another node was selected", async () => {
    let finish!: () => void;
    stubClipboard(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    const el = await adminPanel("dev:uuid-core");
    const menu = await openMenu(el);
    itemOf(menu, "copy_ip").click();
    el.selectedId = "cli:cli-tv";
    await settleAll(el);
    finish();
    await settleAll(el);
    expect(status(el).textContent?.trim()).toBe("");
  });

  describe("a copy that fails or is still running", () => {
    const ADDRESS = "192.0.2.10";
    const denied = () => new DOMException("denied", "NotAllowedError");
    /** A clipboard whose write stays pending until the test settles it. */
    function pendingClipboard() {
      const control = { fail: (): void => undefined };
      stubClipboard(
        () =>
          new Promise<void>((_, reject) => {
            control.fail = () => reject(denied());
          }),
      );
      return control;
    }
    const startCopy = async (el: UitDetailPanel) => {
      const menu = await openMenu(el);
      itemOf(menu, "copy_ip").click();
      await settleAll(el);
    };
    const showsAddress = (el: UitDetailPanel) =>
      el.shadowRoot!.textContent!.includes(ADDRESS);

    it("shows the address of a failed copy for selecting, to an administrator", async () => {
      stubClipboard(async () => {
        throw denied();
      });
      const el = await adminPanel("dev:uuid-core");
      await startCopy(el);
      expect(status(el).querySelector(".manual")!.textContent).toBe(ADDRESS);
    });

    it("removes the address of a failed copy when administrator access is lost", async () => {
      stubClipboard(async () => {
        throw denied();
      });
      const callWS = answering({ "dev:uuid-core": DETAILS });
      const el = await adminPanel("dev:uuid-core", callWS);
      await startCopy(el);
      expect(showsAddress(el)).toBe(true);

      el.hass = hassOf(false, callWS);
      await settleAll(el);
      expect(showsAddress(el)).toBe(false);
      expect(status(el).textContent?.trim()).toBe("");
    });

    it("does not bring the address back when administrator access returns", async () => {
      stubClipboard(async () => {
        throw denied();
      });
      const callWS = answering({ "dev:uuid-core": DETAILS });
      const el = await adminPanel("dev:uuid-core", callWS);
      await startCopy(el);
      el.hass = hassOf(false, callWS);
      await settleAll(el);
      el.hass = hassOf(true, callWS);
      await settleAll(el);
      expect(showsAddress(el)).toBe(false);
      expect(status(el).textContent?.trim()).toBe("");
    });

    it("drops a copy that finishes after administrator access was lost", async () => {
      const clipboard = pendingClipboard();
      const callWS = answering({ "dev:uuid-core": DETAILS });
      const el = await adminPanel("dev:uuid-core", callWS);
      await startCopy(el);
      el.hass = hassOf(false, callWS);
      await settleAll(el);

      clipboard.fail();
      await settleAll(el);
      expect(showsAddress(el)).toBe(false);
      expect(status(el).textContent?.trim()).toBe("");
    });

    it("never renders an address for a user who is not an administrator", async () => {
      const el = await adminPanel("dev:uuid-core");
      await settleAll(el);
      el.hass = hassOf(false, answering({}));
      await settleAll(el);
      // Set after the access change, so only the rendering can stop it.
      el.copyStatus = { key: "menu.copy_failed", value: ADDRESS };
      await settleAll(el);
      expect(showsAddress(el)).toBe(false);
    });

    it("drops a late result after the site changed and the same node was selected again", async () => {
      const clipboard = pendingClipboard();
      const el = await adminPanel("dev:uuid-core");
      await startCopy(el);

      el.model = fixtureModel({}, fixtureSnapshot({ site_id: "site-2" }));
      await settleAll(el);
      clipboard.fail();
      await settleAll(el);
      expect(showsAddress(el)).toBe(false);
      expect(status(el).textContent?.trim()).toBe("");
    });

    it("drops a late result after the entry changed", async () => {
      const clipboard = pendingClipboard();
      const el = await adminPanel("dev:uuid-core");
      await startCopy(el);
      el.model = fixtureModel({}, fixtureSnapshot({ entry_id: "entry-2" }));
      await settleAll(el);
      clipboard.fail();
      await settleAll(el);
      expect(showsAddress(el)).toBe(false);
    });

    it("drops a late result after the selection ended and the same node was selected again", async () => {
      const clipboard = pendingClipboard();
      const el = await adminPanel("dev:uuid-core");
      await startCopy(el);

      el.selectedId = undefined;
      await settleAll(el);
      el.selectedId = "dev:uuid-core";
      await settleAll(el);
      clipboard.fail();
      await settleAll(el);
      expect(showsAddress(el)).toBe(false);
      expect(status(el).textContent?.trim()).toBe("");
    });

    it("still reports a copy that finishes while a live update of the same site arrives", async () => {
      let finish!: () => void;
      stubClipboard(
        () =>
          new Promise<void>((resolve) => {
            finish = resolve;
          }),
      );
      const el = await adminPanel("dev:uuid-core");
      await startCopy(el);
      el.model = fixtureModel();
      await settleAll(el);
      finish();
      await settleAll(el);
      expect(status(el).textContent).toContain(
        "IP address copied to the clipboard.",
      );
    });
  });

  it("asks Home Assistant to open the resolved entity", async () => {
    const el = await adminPanel("dev:uuid-core");
    const seen: CustomEvent[] = [];
    document.body.addEventListener("hass-more-info", (e) =>
      seen.push(e as CustomEvent),
    );
    const menu = await openMenu(el);
    itemOf(menu, "open_entity").click();
    await settleAll(el);
    expect(seen).toHaveLength(1);
    expect(seen[0]!.detail).toEqual({ entityId: "sensor.core_status" });
    expect(seen[0]!.bubbles && seen[0]!.composed).toBe(true);
  });

  it("opens the device from the menu through the same navigation as the button", async () => {
    const el = await adminPanel("dev:uuid-core");
    const menu = await openMenu(el);
    itemOf(menu, "open_device").click();
    expect(location.pathname).toBe("/config/devices/device/reg-core");
  });

  it.each([false, undefined])(
    "blocks a queued action after administrator access is lost (%s)",
    async (admin) => {
      const writeText = vi.fn(async () => undefined);
      stubClipboard(writeText);
      const el = await adminPanel("dev:uuid-core");
      const menu = await openMenu(el);
      const moreInfo = vi.fn();
      el.addEventListener("hass-more-info", moreInfo);
      const path = location.pathname;
      // Before Lit removes the menu, a queued event must still be gated.
      el.hass = hassOf(admin, vi.fn());
      for (const action of [
        "copy_ip",
        "copy_mac",
        "open_device",
        "open_entity",
      ]) {
        menu.dispatchEvent(
          new CustomEvent("uit-action", {
            detail: { action },
            bubbles: true,
            composed: true,
          }),
        );
      }
      await settleAll(el);
      expect(writeText).not.toHaveBeenCalled();
      expect(moreInfo).not.toHaveBeenCalled();
      expect(location.pathname).toBe(path);
      expect(status(el).textContent?.trim()).toBe("");
    },
  );

  it("opens a client's device from the details when the snapshot has none", async () => {
    const el = await adminPanel("cli:cli-tv", undefined, {
      details: { kind: "client", device_id: "reg/tv 1" },
    });
    const menu = await openMenu(el);
    expect(labelsOf(menu)).toEqual(["Open device"]);
    itemOf(menu, "open_device").click();
    expect(location.pathname).toBe("/config/devices/device/reg%2Ftv%201");
  });

  describe("in the modal dialog", () => {
    it("keeps Escape inside the menu from closing the dialog", async () => {
      const el = await adminPanel("dev:uuid-core");
      el.modal = true;
      await settleAll(el);
      const dialog = el.shadowRoot!.querySelector("dialog")!;
      const closed = vi.fn();
      el.addEventListener("uit-close", closed);
      const menu = await openMenu(el);
      const escape = () =>
        new KeyboardEvent("keydown", {
          key: "Escape",
          bubbles: true,
          composed: true,
          cancelable: true,
        });

      itemOf(menu, "copy_ip").dispatchEvent(escape());
      await settleAll(el);
      expect(menu.shadowRoot!.querySelector('[role="menu"]')).toBeNull();
      expect(dialog.open).toBe(true);
      expect(closed).not.toHaveBeenCalled();
      expect(menu.shadowRoot!.activeElement).toBe(
        menu.shadowRoot!.querySelector("button.trigger"),
      );

      // With the menu closed, Escape reaches the dialog again.
      menu.shadowRoot!.querySelector("button.trigger")!.dispatchEvent(escape());
      await settleAll(el);
      expect(dialog.open).toBe(false);
      expect(closed).toHaveBeenCalledOnce();
    });

    it("closes the dialog before opening the device or the entity", async () => {
      const el = await adminPanel("dev:uuid-core");
      el.modal = true;
      await settleAll(el);
      const dialog = el.shadowRoot!.querySelector("dialog")!;
      const openedWith: boolean[] = [];
      document.body.addEventListener("hass-more-info", () =>
        openedWith.push(dialog.open),
      );

      const menu = await openMenu(el);
      itemOf(menu, "open_entity").click();
      expect(openedWith).toEqual([false]);

      dialog.open = true;
      const again = await openMenu(el);
      itemOf(again, "open_device").click();
      expect(dialog.open).toBe(false);
      expect(location.pathname).toBe("/config/devices/device/reg-core");
    });
  });

  it("navigates the inline menu with arrows, Home and End inside the panel", async () => {
    const el = await adminPanel("dev:uuid-core");
    const menu = await openMenu(el);
    const items = [
      ...menu.shadowRoot!.querySelectorAll<HTMLElement>('[role="menuitem"]'),
    ];
    for (const [key, index] of [
      ["ArrowDown", 1],
      ["ArrowUp", 0],
      ["End", 3],
      ["Home", 0],
    ] as const) {
      const event = new KeyboardEvent("keydown", {
        key,
        bubbles: true,
        composed: true,
        cancelable: true,
      });
      menu.shadowRoot!.activeElement!.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
      expect(menu.shadowRoot!.activeElement).toBe(items[index]);
    }
    expect(el.selectedId).toBe("dev:uuid-core");
  });

  it("closes on an outside press without moving focus from the pressed control", async () => {
    const el = await adminPanel("dev:uuid-core");
    const menu = await openMenu(el);
    const button =
      menu.shadowRoot!.querySelector<HTMLButtonElement>("button.trigger")!;
    const focus = vi.spyOn(button, "focus");
    const outside = document.createElement("button");
    document.body.append(outside);
    outside.dispatchEvent(
      new Event("pointerdown", { bubbles: true, composed: true }),
    );
    outside.focus();
    await settleAll(el);
    expect(menu.open).toBe(false);
    expect(focus).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(outside);
    focus.mockRestore();
  });

  it("closes the menu when the native dialog closes or the panel detaches", async () => {
    const el = await adminPanel("dev:uuid-core", undefined, {
      modal: true,
    });
    const menu = await openMenu(el);
    el.shadowRoot!.querySelector("dialog")!.close();
    await settleAll(el);
    expect(menu.open).toBe(false);
    el.modal = false;
    await settleAll(el);
    const inline = await openMenu(el);
    el.remove();
    await inline.updateComplete;
    expect(inline.open).toBe(false);
  });

  it("prevents native dialog cancellation while the menu is open", async () => {
    const el = await adminPanel("dev:uuid-core", undefined, {
      modal: true,
    });
    const menu = await openMenu(el);
    const dialog = el.shadowRoot!.querySelector("dialog")!;
    const cancel = new Event("cancel", { cancelable: true });
    dialog.dispatchEvent(cancel);
    expect(cancel.defaultPrevented).toBe(true);
    expect(dialog.open).toBe(true);
    await settleAll(el);
    expect(menu.open).toBe(false);
    expect(menu.shadowRoot!.querySelector('[role="menu"]')).toBeNull();
    expect(menu.shadowRoot!.activeElement).toBe(
      menu.shadowRoot!.querySelector("button.trigger"),
    );
    const closedCancel = new Event("cancel", { cancelable: true });
    dialog.dispatchEvent(closedCancel);
    expect(closedCancel.defaultPrevented).toBe(false);
  });

  it("hides Actions when supplied details have no action values", async () => {
    const el = await adminPanel("dev:uuid-core", undefined, {
      details: { kind: "switch" },
    });
    await settleAll(el);
    expect(menuOf(el)).toBeNull();
  });

  it("rejects requests from another entry or site and clears pending selection requests", async () => {
    const el = await adminPanel("dev:uuid-core");
    for (const key of [
      { entry_id: "wrong", site_id: "site-1" },
      { entry_id: "entry-1", site_id: "wrong" },
    ]) {
      el.menuRequest = { ...key, node_id: "dev:uuid-core" };
      await settleAll(el);
      expect(menuOf(el)!.open).toBe(false);
    }
    el.details = undefined;
    el.menuRequest = {
      entry_id: "entry-1",
      site_id: "site-1",
      node_id: "dev:uuid-core",
    };
    await settleAll(el);
    el.selectedId = "cli:cli-tv";
    el.details = CLIENT;
    await settleAll(el);
    expect(menuOf(el)!.open).toBe(false);
    el.selectedId = "dev:uuid-core";
    el.details = DETAILS;
    await settleAll(el);
    expect(menuOf(el)!.open).toBe(false);
  });

  it.each(["arrive", "go away"])(
    "rechecks overflow when selected client details %s and settles the dialog",
    async (change) => {
      const el = await adminPanel("cli:cli-tv", undefined, {
        narrow: true,
        details: change === "arrive" ? undefined : CLIENT,
      });
      await settleAll(el);
      expect(el.modal).toBe(false);
      const sheet = el.shadowRoot!.querySelector(".panel")!;
      // jsdom has no layout; the browser case measures actual overflow.
      Object.defineProperty(sheet, "scrollHeight", { value: 500 });
      el.details = change === "arrive" ? CLIENT : undefined;
      await settleAll(el);
      const dialog = el.shadowRoot!.querySelector("dialog")!;
      expect(el.modal).toBe(true);
      expect(dialog.open).toBe(true);
      expect(dialog.querySelector("uit-node-menu") !== null).toBe(
        change === "arrive",
      );
      expect(proto.showModal).toHaveBeenCalledOnce();
      expect(await el.updateComplete).toBe(true);
      el.requestUpdate();
      await settleAll(el);
      expect(el.shadowRoot!.querySelector("dialog")).toBe(dialog);
      expect(proto.showModal).toHaveBeenCalledOnce();
    },
  );

  describe("opened from a context request", () => {
    it("opens the menu on its first item for the selected node", async () => {
      const el = await adminPanel("dev:uuid-core");
      el.menuRequest = {
        entry_id: "entry-1",
        site_id: "site-1",
        node_id: "dev:uuid-core",
      };
      await settleAll(el);
      const menu = menuOf(el)!;
      expect(menu.shadowRoot!.querySelector('[role="menu"]')).not.toBeNull();
      expect(labelsOf(menu)[0]).toBe("Copy IP address");
      expect(menu.shadowRoot!.activeElement).toBe(itemOf(menu, "copy_ip"));
    });

    it("waits until supplied details arrive before opening", async () => {
      const el = await adminPanel("dev:uuid-core", undefined, {
        details: undefined,
        menuRequest: {
          entry_id: "entry-1",
          site_id: "site-1",
          node_id: "dev:uuid-core",
        },
      });
      await settleAll(el);
      expect(menuOf(el)).toBeNull();
      el.details = DETAILS;
      await settleAll(el);
      const menu = menuOf(el)!;
      expect(labelsOf(menu)).toEqual([
        "Copy IP address",
        "Copy MAC address",
        "Open device",
        "Open entity",
      ]);
      expect(menu.shadowRoot!.activeElement).toBe(itemOf(menu, "copy_ip"));
    });

    it("opens inside the dialog when the panel is modal", async () => {
      const el = await adminPanel("dev:uuid-core");
      el.modal = true;
      el.menuRequest = {
        entry_id: "entry-1",
        site_id: "site-1",
        node_id: "dev:uuid-core",
      };
      await settleAll(el);
      const dialog = el.shadowRoot!.querySelector("dialog")!;
      const menu = menuOf(el)!;
      expect(dialog.contains(menu)).toBe(true);
      expect(menu.shadowRoot!.activeElement).toBe(itemOf(menu, "copy_ip"));
    });

    it("waits for the dialog that a too-short card turns the panel into", async () => {
      // jsdom has no layout: make the panel report that it overflows.
      const original = Object.getOwnPropertyDescriptor(
        Element.prototype,
        "scrollHeight",
      )!;
      Object.defineProperty(Element.prototype, "scrollHeight", {
        configurable: true,
        get(this: Element) {
          return this.classList.contains("panel") ? 500 : 0;
        },
      });
      try {
        const el = await adminPanel("dev:uuid-core", undefined, {
          menuRequest: {
            entry_id: "entry-1",
            site_id: "site-1",
            node_id: "dev:uuid-core",
          },
        });
        await settleAll(el);
        expect(el.modal).toBe(true);
        const dialog = el.shadowRoot!.querySelector("dialog")!;
        const menu = menuOf(el)!;
        expect(dialog.contains(menu)).toBe(true);
        expect(menu.shadowRoot!.querySelector('[role="menu"]')).not.toBeNull();
        expect(menu.shadowRoot!.activeElement).toBe(itemOf(menu, "copy_ip"));
      } finally {
        Object.defineProperty(Element.prototype, "scrollHeight", original);
      }
    });

    it("ignores a request for another node, a request without a menu, and a repeat", async () => {
      const el = await adminPanel("dev:uuid-core");
      el.menuRequest = {
        entry_id: "entry-1",
        site_id: "site-1",
        node_id: "cli:cli-tv",
      };
      await settleAll(el);
      expect(menuOf(el)!.shadowRoot!.querySelector('[role="menu"]')).toBeNull();

      const plain = await mount<UitDetailPanel>("uit-detail-panel", {
        model: fixtureModel(),
        localize,
        selectedId: "cli:cli-tv",
        hass: hassOf(false, answering({})),
        menuRequest: {
          entry_id: "entry-1",
          site_id: "site-1",
          node_id: "cli:cli-tv",
        },
      });
      await settleAll(plain);
      expect(menuOf(plain)).toBeNull();

      // The same request object is consumed once: closing the menu
      // sticks across later renders.
      const request = {
        entry_id: "entry-1",
        site_id: "site-1",
        node_id: "dev:uuid-core",
      };
      el.menuRequest = request;
      await settleAll(el);
      const menu = menuOf(el)!;
      menu.hide();
      el.model = fixtureModel();
      await settleAll(el);
      expect(menu.shadowRoot!.querySelector('[role="menu"]')).toBeNull();
    });
  });
});

describe("plan v3 panel inputs", () => {
  it("never fetches details itself and hides Actions until supplied", async () => {
    const callWS = vi.fn(async () => ({
      kind: "switch",
      ip_address: "192.0.2.10",
    }));
    const el = await mount<UitDetailPanel>("uit-detail-panel", {
      model: fixtureModel(),
      localize,
      selectedId: "dev:uuid-core",
      hass: { user: { is_admin: true }, callWS },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    await el.updateComplete;
    expect(callWS).not.toHaveBeenCalled();
    expect(el.shadowRoot!.querySelector("uit-node-menu")).toBeNull();
  });
});

it("shows a WAN with its state wording, its gateway and the gateway device", async () => {
  const el = await panel("wan:uuid-gw:wan2");
  expect(el.shadowRoot!.querySelector("h3")!.textContent).toBe("5G Backup");
  expect(rows(el)).toEqual({
    Type: "WAN",
    State: "Disconnected",
    "Connected to": "Gateway",
  });
  el.shadowRoot!.querySelector<HTMLButtonElement>("button.action")!.click();
  expect(location.pathname).toBe("/config/devices/device/reg-gw");
});
