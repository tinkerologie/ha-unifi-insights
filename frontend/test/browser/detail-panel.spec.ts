import { expect, test, type Locator, type Page } from "@playwright/test";

interface Geometry {
  bottom: number;
  clientHeight: number;
  clientLeft: number;
  clientTop: number;
  clientWidth: number;
  left: number;
  right: number;
  scrollHeight: number;
  scrollTop: number;
  top: number;
}

const card = "unifi-insights-topology-card";
const panelHost = `${card} uit-detail-panel`;
const panel = `${panelHost} .panel`;

// Lovelace Sections grid cells are 56px rows separated by 8px gaps.
function sectionsHeight(rows: number): number {
  return rows * 56 + (rows - 1) * 8;
}

interface FixtureOptions {
  width: number;
  height: number;
  layout?: string;
  unresolved?: number;
  admin?: boolean;
  clients?: "expanded";
  deferDetails?: boolean;
}

async function loadFixture(
  page: Page,
  dimensions: FixtureOptions,
): Promise<void> {
  page.on("pageerror", (error) => console.error(`Fixture error: ${error}`));
  const query = new URLSearchParams({
    width: String(dimensions.width),
    height: String(dimensions.height),
  });
  if (dimensions.layout) query.set("layout", dimensions.layout);
  if (dimensions.unresolved !== undefined)
    query.set("unresolved", String(dimensions.unresolved));
  if (dimensions.admin) query.set("admin", "1");
  if (dimensions.clients) query.set("clients", dimensions.clients);
  if (dimensions.deferDetails) query.set("deferDetails", "1");
  await page.goto(`/test/browser/fixture.html?${query}`);
  await page.waitForFunction(() => window.fixtureReady === true);
}

async function openFixture(
  page: Page,
  dimensions: FixtureOptions,
): Promise<void> {
  await loadFixture(page, dimensions);
  await page.locator(`${card} [data-id="dev:uuid-core"]`).click();
  await expect(page.locator(panel)).toBeVisible();
}

async function geometry(locator: Locator): Promise<Geometry> {
  return locator.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      left: rect.left,
      clientHeight: element.clientHeight,
      clientLeft: element.clientLeft,
      clientTop: element.clientTop,
      clientWidth: element.clientWidth,
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
    };
  });
}

function expectWithin(
  inner: Geometry,
  outer: Pick<Geometry, "left" | "top" | "right" | "bottom">,
): void {
  expect(inner.left).toBeGreaterThanOrEqual(outer.left - 2);
  expect(inner.top).toBeGreaterThanOrEqual(outer.top - 2);
  expect(inner.right).toBeLessThanOrEqual(outer.right + 2);
  expect(inner.bottom).toBeLessThanOrEqual(outer.bottom + 2);
}

async function expectContained(
  page: Page,
  { overflows = true } = {},
): Promise<void> {
  const frame = await geometry(page.locator("#frame"));
  const outer = await geometry(page.locator(`${card} .card`));
  const body = await geometry(page.locator(`${card} .body`));
  const inner = await geometry(page.locator(panelHost));
  const visibleBody = {
    left: Math.max(outer.left, frame.left, body.left),
    top: Math.max(outer.top, frame.top, body.top),
    right: Math.min(outer.right, frame.right, body.right),
    bottom: Math.min(outer.bottom, frame.bottom, body.bottom),
  };
  expectWithin(inner, visibleBody);
  const scrolling = await geometry(page.locator(panel));
  expectWithin(scrolling, inner);
  expectWithin(scrolling, visibleBody);
  if (overflows)
    expect(scrolling.scrollHeight).toBeGreaterThan(scrolling.clientHeight);
  else
    expect(scrolling.scrollHeight).toBeLessThanOrEqual(scrolling.clientHeight);
}

async function scrollToAction(page: Page): Promise<void> {
  const scrollingPanel = page.locator(panel);
  await scrollingPanel.hover();
  await page.mouse.wheel(0, 2000);
  await expect
    .poll(async () => {
      const box = await geometry(scrollingPanel);
      return Math.abs(box.scrollHeight - box.clientHeight - box.scrollTop);
    })
    .toBeLessThanOrEqual(2);
  await expectActionInView(page);
}

async function expectActionInView(page: Page): Promise<void> {
  const action = page.getByRole("button", { name: "Open device" });
  await expect(action).toBeVisible();
  const box = await geometry(page.locator(panel));
  expectWithin(await geometry(action), {
    left: box.left + box.clientLeft,
    top: box.top + box.clientTop,
    right: box.left + box.clientLeft + box.clientWidth,
    bottom: box.top + box.clientTop + box.clientHeight,
  });
  await action.focus();
  await expect(action).toBeFocused();
}

const dialog = `${panelHost} dialog`;

// A card too short for the details shows them in a modal dialog instead.
async function expectDialogShowsEverything(page: Page): Promise<void> {
  const modal = page.locator(dialog);
  await expect(modal).toBeVisible();
  expect(await modal.evaluate((element) => element.matches(":modal"))).toBe(
    true,
  );
  const viewport = page.viewportSize()!;
  const box = await geometry(modal);
  expectWithin(box, {
    left: 0,
    top: 0,
    right: viewport.width,
    bottom: viewport.height,
  });
  const scrolling = await geometry(page.locator(panel));
  // The panel fills the dialog exactly: no browser-default frame around it.
  for (const side of ["left", "top", "right", "bottom"] as const)
    expect(Math.abs(scrolling[side] - box[side])).toBeLessThanOrEqual(1);
  expect(box.right - box.left).toBeLessThanOrEqual(400);
  expect(scrolling.scrollHeight).toBeLessThanOrEqual(scrolling.clientHeight);
  await expectActionInView(page);
}

const node = `${card} [data-id="dev:uuid-core"]`;

async function expectInCard(page: Page): Promise<void> {
  await expect(page.locator(dialog)).toHaveCount(0);
  await expectContained(page, { overflows: false });
  expect((await geometry(page.locator(panel))).scrollTop).toBe(0);
  await expectActionInView(page);
}

test.describe("topology detail panel overflow", () => {
  test("desktop card with room keeps the details beside the graph", async ({
    page,
  }) => {
    // Reduced motion makes zoom instant, so each transform can be compared.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 1280, height: 800 });
    await openFixture(page, { width: 800, height: 620 });
    await expect(page.locator(panelHost)).not.toHaveAttribute("narrow");
    await expectInCard(page);

    const viewport = page.locator(`${card} uit-graph-view g.viewport`);
    const fitted = await viewport.getAttribute("transform");
    await page.getByRole("button", { name: "Zoom in" }).click();
    await expect(viewport).not.toHaveAttribute("transform", fitted!);
    const zoomed = await viewport.getAttribute("transform");
    const canvas = page.locator(`${card} uit-graph-view svg.canvas`);
    const canvasBox = await canvas.boundingBox();
    expect(canvasBox).not.toBeNull();
    await page.mouse.move(canvasBox!.x + 120, canvasBox!.y + 100);
    await page.mouse.down();
    await page.mouse.move(canvasBox!.x + 180, canvasBox!.y + 130);
    await page.mouse.up();
    await expect(viewport).not.toHaveAttribute("transform", zoomed!);
    await expectContained(page, { overflows: false });

    // Shrink across the 600px breakpoint into the narrow bottom sheet.
    await page.locator("#frame").evaluate((frame) => {
      frame.style.width = "560px";
    });
    await expect(page.locator(panelHost)).toHaveAttribute("narrow", "");
    await expectContained(page, { overflows: false });

    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.locator(panel)).toBeHidden();
  });

  test("narrow card with room shows the full switch detail unscrolled", async ({
    page,
  }) => {
    // The #186 reporter's card after the first fix: the bottom sheet was
    // capped at 60% of the canvas, so details still needed scrolling.
    await page.setViewportSize({ width: 1280, height: 800 });
    await openFixture(page, { width: 560, height: 620, unresolved: 93 });
    await expect(page.locator(panelHost)).toHaveAttribute("narrow", "");
    await expect(page.locator(`${card} .notices`)).toBeVisible();
    await expectInCard(page);
  });

  test("eight-row narrow Sections card shows full details unscrolled", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await openFixture(page, {
      width: 500,
      height: sectionsHeight(8),
      layout: "grid",
    });
    await expect(page.locator(panelHost)).toHaveAttribute("narrow", "");
    await expectInCard(page);
  });

  test("short desktop card opens the full details in a dialog", async ({
    page,
  }) => {
    // #186 on 2026.10.0: a short card cut the details off, and reaching
    // Open device needed a scroll inside the card.
    await page.setViewportSize({ width: 1280, height: 800 });
    await openFixture(page, { width: 800, height: 360 });
    await expectDialogShowsEverything(page);

    await page.keyboard.press("Escape");
    await expect(page.locator(panel)).toBeHidden();
    await expect(page.locator(dialog)).toHaveCount(0);
    await expect(page.locator(node)).toBeFocused();
  });

  test("short narrow card opens the full details in a dialog", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openFixture(page, { width: 360, height: 320 });
    await expectDialogShowsEverything(page);

    await page.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.locator(dialog)).toHaveCount(0);
    await expect(page.locator(node)).toBeFocused();
  });

  test("short narrow client sheet becomes a dialog when details arrive", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loadFixture(page, {
      width: 360,
      height: 320,
      admin: true,
      clients: "expanded",
      deferDetails: true,
    });
    const client = page.locator(`${card} [data-id="cli:browser-0"]`);
    await client.click();
    await expect(page.locator(panel)).toBeVisible();
    await expect(page.locator(panelHost)).toHaveAttribute("narrow", "");
    await expect(page.locator(dialog)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Actions" })).toHaveCount(0);
    await expect
      .poll(() => page.evaluate(() => window.fixtureDetailsCalls.length))
      .toBe(1);
    const sheet = await geometry(page.locator(panel));
    expect(sheet.scrollHeight).toBeLessThanOrEqual(sheet.clientHeight);

    await page.evaluate(() => window.fixtureResolveDetails());
    const modal = page.locator(dialog);
    await expect(modal).toBeVisible();
    expect(await modal.evaluate((element) => element.matches(":modal"))).toBe(
      true,
    );
    await expect(modal.getByRole("button", { name: "Actions" })).toBeVisible();
    const full = await geometry(page.locator(panel));
    expect(full.scrollHeight).toBeLessThanOrEqual(full.clientHeight);
    await expect(page.getByRole("menu")).toHaveCount(0);

    await page.keyboard.press("Escape");
    await expect(page.locator(dialog)).toHaveCount(0);
    await expect(page.locator(panel)).toBeHidden();
    await expect(client).toBeFocused();
  });

  test("five-row Sections card dialog closes from the backdrop", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await openFixture(page, {
      width: 800,
      height: sectionsHeight(5),
      layout: "grid",
    });
    await expectDialogShowsEverything(page);

    await page.mouse.click(4, 4);
    await expect(page.locator(dialog)).toHaveCount(0);
    await expect(page.locator(panel)).toBeHidden();
  });

  test("Open device leaves no dialog behind", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await openFixture(page, { width: 800, height: 360 });
    await expectDialogShowsEverything(page);

    await page.getByRole("button", { name: "Open device" }).click();
    await expect(page).toHaveURL(/\/config\/devices\/device\//);
    await expect(page.locator(dialog)).toHaveCount(0);
  });

  test("dragging a text selection onto the backdrop keeps the dialog", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await openFixture(page, { width: 800, height: 360 });
    await expectDialogShowsEverything(page);

    const value = await page.locator(`${panel} dd`).first().boundingBox();
    expect(value).not.toBeNull();
    await page.mouse.move(value!.x + 2, value!.y + value!.height / 2);
    await page.mouse.down();
    await page.mouse.move(4, 4, { steps: 5 });
    await page.mouse.up();
    await expect(page.locator(dialog)).toBeVisible();
  });

  test("detaching the card closes the dialog instead of leaving it in the card", async ({
    page,
  }) => {
    // HA detaches hidden and cached panels and later re-attaches them.
    await page.setViewportSize({ width: 1280, height: 800 });
    await openFixture(page, { width: 800, height: 360 });
    await expectDialogShowsEverything(page);

    await page.evaluate(async () => {
      const element = window.fixtureCard;
      const parent = element.parentElement!;
      element.remove();
      await new Promise((resolve) => setTimeout(resolve, 50));
      parent.append(element);
    });
    await expect(page.locator(dialog)).toHaveCount(0);
    await expect(page.locator(panel)).toBeHidden();

    await page.locator(node).click();
    await expectDialogShowsEverything(page);
  });

  test("card shrunk after opening keeps the panel in the card and scrollable", async ({
    page,
  }) => {
    // Only opening a panel (or crossing the narrow breakpoint) can turn
    // it into a dialog; a resize alone leaves the scrolling panel.
    await page.setViewportSize({ width: 1280, height: 800 });
    await openFixture(page, { width: 800, height: 620 });
    await expectInCard(page);

    await page.locator("#frame").evaluate((frame) => {
      frame.style.height = "360px";
    });
    await expect(page.locator(panelHost)).not.toHaveAttribute("narrow");
    await expectContained(page);
    await expect(page.locator(dialog)).toHaveCount(0);
    // Nor does the next live update.
    await page.evaluate(() => window.fixtureUpdate());
    await page.evaluate(
      () => new Promise((resolve) => requestAnimationFrame(resolve)),
    );
    await expect(page.locator(dialog)).toHaveCount(0);
    await scrollToAction(page);
  });

  test("dialog scrolls when the screen itself is too short", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 320 });
    await openFixture(page, { width: 800, height: 300 });
    const modal = page.locator(dialog);
    await expect(modal).toBeVisible();
    expectWithin(await geometry(modal), {
      left: 0,
      top: 0,
      right: 1280,
      bottom: 320,
    });
    const scrolling = await geometry(page.locator(panel));
    expect(scrolling.scrollHeight).toBeGreaterThan(scrolling.clientHeight);
    await scrollToAction(page);
  });
});

const trigger = (page: Page) => page.getByRole("button", { name: "Actions" });
const menuItem = (page: Page, name: string) =>
  page.getByRole("menuitem", { name });
const liveRegion = (page: Page) => page.locator(`${panelHost} [role="status"]`);

test.describe("topology node action menu", () => {
  test("works inside the narrow native dialog: keys, focus, Escape, copy", async ({
    page,
    context,
  }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.setViewportSize({ width: 390, height: 844 });
    await openFixture(page, { width: 360, height: 320, admin: true });
    await expectDialogShowsEverything(page);
    const modal = page.locator(dialog);

    // Open with the keyboard: the first item takes focus.
    await trigger(page).focus();
    await page.keyboard.press("ArrowDown");
    const menu = page.getByRole("menu", { name: "Actions" });
    await expect(menu).toBeVisible();
    await expect(menuItem(page, "Copy IP address")).toBeFocused();
    await expect(trigger(page)).toHaveAttribute("aria-expanded", "true");
    expect(
      await menu.evaluate((element) => {
        // closest() stops at the shadow boundary; walk the flat tree.
        let at: Node | null = element;
        while (at) {
          if (at instanceof HTMLDialogElement) return true;
          at = at.parentNode ?? (at as ShadowRoot).host ?? null;
        }
        return false;
      }),
    ).toBe(true);
    // Inside the dialog's box, so a modal dialog's inertness cannot hide it.
    const box = await geometry(modal);
    expectWithin(await geometry(menu), box);

    // Arrows wrap; Home and End jump.
    await page.keyboard.press("End");
    await expect(menuItem(page, "Open entity")).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(menuItem(page, "Copy IP address")).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(menuItem(page, "Open entity")).toBeFocused();
    await page.keyboard.press("Home");
    await expect(menuItem(page, "Copy IP address")).toBeFocused();

    // Escape closes the menu only; the dialog stays and focus returns.
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);
    await expect(modal).toBeVisible();
    await expect(trigger(page)).toBeFocused();

    // Enter opens it again and Enter on an item copies.
    await page.keyboard.press("Enter");
    await expect(menuItem(page, "Copy IP address")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(menu).toHaveCount(0);
    await expect(trigger(page)).toBeFocused();
    await expect(liveRegion(page)).toHaveText(
      "IP address copied to the clipboard.",
    );
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
      "192.0.2.10",
    );
    await expect(liveRegion(page)).toHaveAttribute("aria-live", "polite");

    // Escape with the menu closed still closes the dialog.
    await page.keyboard.press("Escape");
    await expect(modal).toHaveCount(0);
  });

  for (const key of ["Enter", "Space"]) {
    test(`native ${key} activates a menu item`, async ({ page, context }) => {
      await context.grantPermissions(["clipboard-read", "clipboard-write"]);
      await page.setViewportSize({ width: 390, height: 844 });
      await openFixture(page, { width: 360, height: 320, admin: true });
      await trigger(page).click();
      await expect(menuItem(page, "Copy IP address")).toBeFocused();
      await page.keyboard.down(key);
      if (key === "Space") {
        // Native Space activates on release, not on the press.
        await expect(page.getByRole("menu")).toBeVisible();
        await expect(liveRegion(page)).toHaveText("");
      }
      await page.keyboard.up(key);
      await expect(page.getByRole("menu")).toHaveCount(0);
      await expect(liveRegion(page)).toHaveText(
        "IP address copied to the clipboard.",
      );
      expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
        "192.0.2.10",
      );
      await expect(trigger(page)).toBeFocused();
    });
  }

  test("copies through the textarea fallback while the modal dialog is open", async ({
    page,
    context,
  }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.setViewportSize({ width: 390, height: 844 });
    await openFixture(page, { width: 360, height: 320, admin: true });
    await expectDialogShowsEverything(page);
    // No async clipboard: everything outside the dialog is inert, so the
    // temporary textarea has to live inside it.
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: undefined,
      });
    });

    await trigger(page).focus();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowDown");
    await expect(menuItem(page, "Copy MAC address")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(liveRegion(page)).toHaveText(
      "MAC address copied to the clipboard.",
    );
    await expect(trigger(page)).toBeFocused();

    await page.evaluate(() => {
      delete (navigator as { clipboard?: unknown }).clipboard;
    });
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
      "aa:bb:cc:dd:ee:ff",
    );
    // The temporary field is gone again.
    expect(
      await page.evaluate(
        () =>
          window.fixtureCard.shadowRoot
            ?.querySelector("uit-detail-panel")
            ?.shadowRoot?.querySelectorAll("textarea").length,
      ),
    ).toBe(0);
  });

  test("shows the value for manual copying when nothing can copy", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openFixture(page, { width: 360, height: 320, admin: true });
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async () => {
            throw new DOMException("denied", "NotAllowedError");
          },
        },
      });
      document.execCommand = () => false;
    });
    await trigger(page).focus();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await expect(liveRegion(page)).toContainText("Couldn't copy.");
    const manual = liveRegion(page).locator(".manual");
    await expect(manual).toHaveText("192.0.2.10");
    expect(
      await manual.evaluate((element) => getComputedStyle(element).userSelect),
    ).toBe("text");
    expect(await page.evaluate(() => window.getSelection()?.toString())).toBe(
      "192.0.2.10",
    );
  });

  test("Open entity closes the dialog and asks Home Assistant for the entity", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openFixture(page, { width: 360, height: 320, admin: true });
    await trigger(page).focus();
    await page.keyboard.press("ArrowUp");
    await expect(menuItem(page, "Open entity")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator(dialog)).toHaveCount(0);
    expect(await page.evaluate(() => window.fixtureMoreInfo)).toEqual([
      { entityId: "sensor.core_status" },
    ]);
  });

  test("Open device in the menu leaves no dialog behind", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openFixture(page, { width: 360, height: 320, admin: true });
    await trigger(page).focus();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("ArrowDown");
    await expect(menuItem(page, "Open device")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/config\/devices\/device\/reg-core/);
    await expect(page.locator(dialog)).toHaveCount(0);
  });

  test("Shift+F10 on a focused node opens the details and the menu", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await loadFixture(page, { width: 360, height: 320, admin: true });
    await page.locator(node).focus();
    await page.keyboard.press("Shift+F10");
    await expect(page.locator(dialog)).toBeVisible();
    await expect(page.getByRole("menu", { name: "Actions" })).toBeVisible();
    await expect(menuItem(page, "Copy IP address")).toBeFocused();

    // Escape twice: the menu, then the dialog; focus lands on the node.
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(page.locator(dialog)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.locator(dialog)).toHaveCount(0);
    await expect(page.locator(node)).toBeFocused();
  });

  test("a context click opens the menu in the side panel too", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await loadFixture(page, { width: 800, height: 620, admin: true });
    await page.locator(node).click({ button: "right" });
    await expect(page.locator(dialog)).toHaveCount(0);
    await expect(page.locator(panel)).toBeVisible();
    await expect(menuItem(page, "Copy IP address")).toBeFocused();
    // A press elsewhere dismisses the menu without restoring trigger focus.
    await page.locator(`${card} .title`).click();
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(page.locator(panel)).toBeVisible();
    await expect(trigger(page)).not.toBeFocused();
  });

  test("dismissing the menu on dialog content leaves browser focus unchanged", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openFixture(page, { width: 360, height: 320, admin: true });
    await trigger(page).focus();
    await page.keyboard.press("ArrowDown");
    await expect(menuItem(page, "Copy IP address")).toBeFocused();

    // The heading and a row label take no focus: the browser would leave
    // nothing (or the dialog itself) focused after the press.
    await page.locator(`${panelHost} h3`).click();
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(page.locator(dialog)).toBeVisible();
    await expect(trigger(page)).not.toBeFocused();

    await trigger(page).click();
    await expect(menuItem(page, "Copy IP address")).toBeFocused();
    // A row label below the dropdown takes no focus; dismissal must not
    // focus the trigger.
    await page.locator(`${panel} dt`).last().click();
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(trigger(page)).not.toBeFocused();
  });

  test.describe("pressed controls with the menu open", () => {
    test.use({ hasTouch: true });
    for (const input of ["mouse", "touch"] as const) {
      for (const scenario of [
        "modal Close",
        "modal Open device",
        "sheet Close",
      ] as const) {
        test(`${input} press acts on ${scenario}`, async ({ page }) => {
          await page.setViewportSize({ width: 390, height: 844 });
          const sheet = scenario === "sheet Close";
          await openFixture(page, {
            width: 360,
            height: sheet ? 760 : 320,
            admin: true,
          });
          await expect(page.locator(dialog)).toHaveCount(sheet ? 0 : 1);
          const control = page.getByRole("button", {
            name: scenario === "modal Open device" ? "Open device" : "Close",
            exact: true,
          });
          const closed = await geometry(control);
          await trigger(page).click();
          await expect(page.getByRole("menu")).toBeVisible();
          const before = await geometry(control);
          expect(before.top).toBe(closed.top);
          expect(before.left).toBe(closed.left);
          const x = (before.left + before.right) / 2;
          const y = (before.top + before.bottom) / 2;
          if (input === "mouse") {
            await page.mouse.move(x, y);
            await page.mouse.down();
            await expect(page.getByRole("menu")).toHaveCount(0);
            const after = await geometry(control);
            await expect(control).toBeFocused();
            await page.mouse.up();
            // The release must hit the control that received the press.
            expect(after.top).toBe(before.top);
            expect(after.left).toBe(before.left);
          } else {
            await page.touchscreen.tap(x, y);
          }
          if (scenario === "modal Open device") {
            await expect(page).toHaveURL(/\/config\/devices\/device\/reg-core/);
          }
          await expect(page.locator(panel)).toHaveCount(0);
          await expect(page.locator(dialog)).toHaveCount(0);
        });
      }
    }
  });

  test("Tab closes the menu and moves to the next control", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openFixture(page, { width: 360, height: 320, admin: true });
    await trigger(page).focus();
    await page.keyboard.press("ArrowDown");
    await expect(menuItem(page, "Copy IP address")).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(page.locator(dialog)).toBeVisible();
    // The next control after the body menu is the existing device link.
    await expect(
      page.getByRole("button", { name: "Open device", exact: true }),
    ).toBeFocused();
  });

  test("Tab from the last item and Shift+Tab preserve native focus order", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openFixture(page, { width: 360, height: 320, admin: true });
    await trigger(page).focus();
    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("End");
    await page.keyboard.press("Tab");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Open device", exact: true }),
    ).toBeFocused();
    await trigger(page).click();
    await page.keyboard.press("Shift+Tab");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(trigger(page)).toBeFocused();
  });

  test("a non-administrator never requests details or sees Actions", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openFixture(page, { width: 360, height: 320 });
    await expect(trigger(page)).toHaveCount(0);
    await expect(page.getByRole("menuitem")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Open device", exact: true }),
    ).toBeVisible();
    expect(await page.evaluate(() => window.fixtureDetailsCalls)).toEqual([]);
  });
});

declare global {
  interface Window {
    fixtureCard: HTMLElement;
    fixtureResolveDetails: () => void;
    fixtureDetailsCalls: unknown[];
    fixtureMoreInfo: unknown[];
    fixtureReady: boolean;
    fixtureUpdate: () => void;
  }
}

test("opens the switch ports with the keyboard on a narrow card", async ({
  page,
}) => {
  await openFixture(page, { width: 320, height: sectionsHeight(6) });
  const toggle = page.getByRole("button", { name: "Ports · 2 ports, 1 up" });
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.focus();
  await toggle.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(toggle).toBeFocused();
  const portItems = page.locator(`${panelHost} .port-grid li`);
  await expect(portItems).toHaveCount(2);
  await expect(portItems.first()).toContainText("0 Mbps");
  await expect(portItems.first()).toContainText("PoE disabled");
  await expect(portItems.last()).toContainText("10 Gbps");
  await expect
    .poll(async () => {
      return portItems
        .last()
        .evaluate((item) => item.getBoundingClientRect().right);
    })
    .toBeLessThanOrEqual((await geometry(page.locator(panel))).right);
  await toggle.press("Space");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
});
