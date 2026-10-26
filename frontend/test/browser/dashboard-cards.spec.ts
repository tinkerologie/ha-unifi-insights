import { expect, test, type Page } from "@playwright/test";
import type axe from "axe-core";

declare global {
  interface Window {
    axe: typeof axe;
  }
}

const cards = [
  "topology",
  "site-health",
  "internet-activity",
  "performance",
  "protect-status",
  "timeline",
];

async function ready(
  page: Page,
  name: string,
  width: number,
  height: number | "auto",
  theme: string,
  editor = false,
) {
  await page.goto(
    `/test/browser/fixture.html?${new URLSearchParams({ card: name, width: String(width), height: String(height), theme, ...(editor ? { editor: "1" } : {}) })}`,
  );
  await page.waitForFunction(() => window.fixtureReady);
  const root = page.locator(
    `unifi-insights-${name}-card${editor ? "-editor" : ""}`,
  );
  await expect(
    root.locator(
      editor ? "select" : name === "topology" ? "uit-graph-view" : ".content",
    ),
  ).toBeVisible();
  return root;
}

async function audit(page: Page) {
  await page.addScriptTag({ path: "node_modules/axe-core/axe.min.js" });
  const violations = await page.evaluate(async () => {
    const result = await window.axe.run(document.querySelector("main")!, {
      runOnly: {
        type: "tag",
        values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"],
      },
    });
    return result.violations.map((v) => ({
      id: v.id,
      nodes: v.nodes.map((n) => ({
        target: n.target,
        summary: n.failureSummary,
      })),
    }));
  });
  expect(violations).toEqual([]);
}

test("timeline built bundles show readable object classifications", async ({
  page,
}) => {
  const root = await ready(page, "timeline", 320, "auto", "dark");
  await expect(root.locator(".event-labels").nth(0)).toHaveText(/Person/);
  await expect(root.locator(".event-labels").nth(1)).toHaveText(/Animal/);
  await expect(root.locator(".event-labels").nth(2)).toHaveText(/Car/);
  const mixed = root.locator(".event-labels").nth(3);
  await expect(mixed.locator(".chip")).toHaveCount(3);
  await expect(root.locator(".event-labels").nth(4)).toHaveText(/Motion/);
  await expect(root.locator(".event-labels").nth(5)).toHaveText(
    /Unknown object \(unknown\)[\s\S]*Person/,
  );
  await audit(page);
});

for (const theme of ["light", "dark"]) {
  for (const width of [320, 800]) {
    for (const name of cards) {
      test(`${name}: ${theme}, ${width}px, Sections sizing and accessibility`, async ({
        page,
      }) => {
        await page.setViewportSize({
          width: Math.max(width + 24, 390),
          height: 900,
        });
        await page.emulateMedia({ reducedMotion: "reduce" });
        // Sections uses 56px rows with an 8px gap; exercise default and minimum heights.
        const rowCounts = name === "topology" ? [8, 4] : [4, 3];
        for (const rows of rowCounts) {
          const root = await ready(
            page,
            name,
            width,
            rows * 56 + (rows - 1) * 8,
            theme,
          );
          await audit(page);
          const geometry = await root.locator("ha-card").evaluate((el) => ({
            width: el.clientWidth,
            scroll: el.scrollWidth,
          }));
          expect(geometry.scroll).toBeLessThanOrEqual(geometry.width + 1);
          for (const control of await root
            .locator(
              "button:visible, input:visible, select:visible, summary:visible",
            )
            .all()) {
            const box = await control.boundingBox();
            expect(box!.height).toBeGreaterThanOrEqual(44);
            expect(box!.width).toBeGreaterThanOrEqual(44);
          }
          if (name !== "topology") {
            const content = root.locator(".content");
            await content.focus();
            await expect(content).toBeFocused();
            const outline = await content.evaluate(
              (el) => getComputedStyle(el).outlineWidth,
            );
            expect(parseFloat(outline)).toBeGreaterThanOrEqual(2);
            const list = root.locator(".list");
            if (await list.count()) {
              // Every final row stays reachable through a bounded scroll region.
              await list.locator(".item-card").last().scrollIntoViewIfNeeded();
              await expect(list.locator(".item-card").last()).toBeInViewport();
            }
            const transitions = await root
              .locator(".bar-fill")
              .evaluateAll((els) =>
                els.map((el) => getComputedStyle(el).transitionDuration),
              );
            expect(
              transitions.every((duration) => parseFloat(duration) <= 0.001),
            ).toBe(true);
          }
        }
      });
    }
    for (const name of cards.filter((name) => name !== "topology")) {
      test(`${name} native editor: ${theme}, ${width}px`, async ({ page }) => {
        await page.setViewportSize({
          width: Math.max(width + 24, 390),
          height: 900,
        });
        const root = await ready(page, name, width, "auto", theme, true);
        await audit(page);
        await expect(
          root.getByLabel(name === "protect-status" ? "Integration" : "Site", {
            exact: true,
          }),
        ).toBeVisible();
        await root.getByLabel("Title", { exact: true }).fill("Renamed card");
        for (const control of await root.locator("input, select").all()) {
          const box = await control.boundingBox();
          expect(box!.height).toBeGreaterThanOrEqual(44);
          await control.focus();
          await expect(control).toBeFocused();
          expect(
            await control.evaluate((el) => getComputedStyle(el).outlineWidth),
          ).toBe("2px");
        }
      });
    }
  }
}

for (const name of cards.filter((name) => name !== "topology")) {
  test(`${name} automatic height and enlarged text`, async ({ page }) => {
    const root = await ready(page, name, 320, "auto", "light");
    await page.addStyleTag({
      content:
        ":root { --ha-font-size-s: 24px; --ha-font-size-m: 28px; --ha-font-size-l: 32px; --ha-font-size-xl: 40px; }",
    });
    const geometry = await root.locator("ha-card").evaluate((el) => ({
      height: el.clientHeight,
      scroll: el.scrollHeight,
      width: el.clientWidth,
      scrollWidth: el.scrollWidth,
    }));
    expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.width + 1);
    expect(geometry.scroll).toBeLessThanOrEqual(geometry.height + 1);
    await audit(page);
  });
}

test("fixture rejects prototype keys before mutating a snapshot", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/test/browser/fixture.html?card=__proto__&status=polluted");
  await expect.poll(() => errors).toContain("Unknown fixture card");
  expect(
    await page.evaluate(() =>
      Object.prototype.hasOwnProperty.call(Object.prototype, "status"),
    ),
  ).toBe(false);
  expect(await page.locator("main > *").count()).toBe(0);
});
