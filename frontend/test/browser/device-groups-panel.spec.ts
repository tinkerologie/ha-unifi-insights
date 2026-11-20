import { expect, test, type Page } from "@playwright/test";
import type axe from "axe-core";

declare global {
  interface Window {
    axe: typeof axe;
    panelFixtureReady?: boolean;
    panelFixtureNavigations?: string[];
    panelFixtureUpdate?: (overrides?: Record<string, unknown>) => void;
  }
}

async function openPanel(
  page: Page,
  opts: {
    width: number;
    height: number;
    theme: "light" | "dark";
    entries?: number;
    narrow?: boolean;
  },
) {
  await page.setViewportSize({ width: opts.width, height: opts.height });
  const query = new URLSearchParams({
    theme: opts.theme,
    entries: String(opts.entries ?? 1),
    narrow: opts.narrow ? "1" : "0",
  });
  await page.goto(`/test/browser/device-groups-fixture.html?${query}`);
  await page.waitForFunction(() => window.panelFixtureReady === true);
  const panel = page.locator("unifi-insights-devices-panel");
  await expect(panel.locator('[role="treeitem"]').first()).toBeVisible();
  return panel;
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

for (const theme of ["light", "dark"] as const) {
  for (const viewport of [
    { name: "mobile", width: 360, height: 800, narrow: true },
    { name: "desktop", width: 1024, height: 800, narrow: false },
  ]) {
    test(`device-groups-panel: ${theme}, ${viewport.name} (${viewport.width}px) layout & a11y`, async ({
      page,
    }) => {
      const panel = await openPanel(page, {
        width: viewport.width,
        height: viewport.height,
        theme,
        entries: 2,
        narrow: viewport.narrow,
      });

      await audit(page);

      // No horizontal overflow at 360px or desktop width.
      const overflow = await panel.evaluate((el) => ({
        clientWidth: el.clientWidth,
        scrollWidth: el.scrollWidth,
      }));
      expect(overflow.scrollWidth).toBeLessThanOrEqual(
        overflow.clientWidth + 1,
      );

      // Empty "Other devices" section is hidden; Default & Unassigned are visible.
      await expect(panel.locator(".section-title")).toHaveText([
        "Default",
        "Unassigned",
      ]);

      // Entry picker is visible when >1 entries exist.
      await expect(panel.locator("select")).toBeVisible();
    });
  }
}

test("device-groups-panel: navigation, plain-text rows, expand/collapse, and live updates", async ({
  page,
}) => {
  const panel = await openPanel(page, {
    width: 1024,
    height: 800,
    theme: "light",
    entries: 1,
    narrow: false,
  });

  // Single entry hides the picker.
  await expect(panel.locator("select")).toHaveCount(0);

  // Encoded device link navigates via history.pushState + location-changed.
  const firstLink = panel.locator("a.device-link").first();
  await expect(firstLink).toHaveAttribute(
    "href",
    "/config/devices/device/dev%2Fcore%201",
  );
  await firstLink.click();
  await expect
    .poll(() => page.evaluate(() => window.panelFixtureNavigations))
    .toEqual(["/config/devices/device/dev%2Fcore%201"]);

  // Plain-text row has no link and does not navigate on click.
  const apRow = panel.locator('[data-key="net:default:ap"]');
  await expect(apRow.locator("a")).toHaveCount(0);
  await expect(apRow).toContainText("Garage AP");
  await expect(apRow).toContainText("Last known uplink only");
  await apRow.click();
  expect(
    await page.evaluate(() => window.panelFixtureNavigations),
  ).toHaveLength(1);

  // Expand/collapse via chevron hides and restores child camera row.
  const switchRow = panel.locator('[data-key="net:default:sw"]');
  await expect(panel.locator('[data-key="cam:c1"]')).toBeVisible();
  await switchRow.locator(".chevron").click();
  await expect(panel.locator('[data-key="cam:c1"]')).toHaveCount(0);
  await switchRow.locator(".chevron").click();
  await expect(panel.locator('[data-key="cam:c1"]')).toBeVisible();

  // Simulated live update marks data stale and adds a new device.
  await page.evaluate(() => {
    window.panelFixtureUpdate?.({
      stale: true,
      sections: [
        {
          key: "site:default",
          kind: "site",
          title: "Default",
          rows: [
            {
              key: "net:default:gw",
              name: "UDM Pro Max",
              kind: "gateway",
              ha_device_id: "dev/core 1",
              parent_key: null,
              port: null,
              reason: null,
            },
            {
              key: "net:default:sw2",
              name: "Lab Switch",
              kind: "switch",
              ha_device_id: "sw-2",
              parent_key: "net:default:gw",
              port: 8,
              reason: null,
            },
          ],
        },
      ],
    });
  });

  await expect(panel.locator(".notice")).toContainText(
    "Data may be out of date.",
  );
  await expect(panel.locator('[data-key="net:default:gw"]')).toContainText(
    "UDM Pro Max",
  );
  await expect(panel.locator('[data-key="net:default:sw2"]')).toContainText(
    "Port 8",
  );
  await audit(page);
});
