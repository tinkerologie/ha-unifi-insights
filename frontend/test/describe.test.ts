import { expect, it } from "vitest";
import { makeLocalize } from "../src/localize";
import {
  describeVisual,
  formatSpeed,
  formatSpeedLong,
  linkLabel,
  truncate,
  visualStateKey,
  visualTitle,
} from "../src/views/describe";
import { fixtureModel } from "./helpers";

const localize = makeLocalize("en");

it("formats link speeds", () => {
  expect([formatSpeed(10000), formatSpeed(2500), formatSpeed(100)]).toEqual([
    "10G",
    "2.5G",
    "100M",
  ]);
  expect([formatSpeedLong(10000), formatSpeedLong(100)]).toEqual([
    "10 gigabit",
    "100 megabit",
  ]);
});

it("labels links with port, speed and PoE", () => {
  expect(
    linkLabel({
      source: "a",
      target: "b",
      medium: "wired",
      parent_port: 20,
      speed_mbps: 2500,
      poe_power_w: 12.3,
    }),
  ).toBe("p20 · 2.5G · PoE");
  expect(linkLabel(undefined)).toBe("");
});

it("truncates by characters with an ellipsis", () => {
  expect(truncate("short", 22)).toBe("short");
  expect(truncate("a".repeat(30), 22)).toBe(`${"a".repeat(21)}…`);
});

it("describes nodes and groups for screen readers", () => {
  const model = fixtureModel();
  expect(
    describeVisual(model, model.visuals.get("dev:uuid-core")!, localize),
  ).toBe("Switch Core 24, online, uplink port 11 at 10 gigabit");
  expect(
    describeVisual(model, model.visuals.get("dev:uuid-u1")!, localize),
  ).toBe("Switch Ultra A, online, 1 client, uplink port 6 at 1 gigabit");
  expect(
    describeVisual(model, model.visuals.get("group:dev:uuid-ap")!, localize),
  ).toBe("1 client: 1 wireless, 0 offline");
  expect(visualTitle(model.visuals.get("group:dev:uuid-ap")!, localize)).toBe(
    "1 client",
  );
});

it("describes a WAN as a connection of its gateway", () => {
  const model = fixtureModel();
  expect(
    describeVisual(model, model.visuals.get("wan:uuid-gw:wan2")!, localize),
  ).toBe("WAN 5G Backup, disconnected, internet connection of Gateway");
  expect(visualStateKey(model.visuals.get("wan:uuid-gw:wan")!)).toBe(
    "state.connected",
  );
  expect(visualStateKey(model.visuals.get("group:dev:uuid-ap")!)).toBe(
    "state.online",
  );
});
