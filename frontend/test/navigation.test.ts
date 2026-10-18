import { expect, it } from "vitest";
import { NODE_KINDS } from "../src/contract";
import { buildModel } from "../src/model/graph-model";
import { nextGraphFocus } from "../src/model/navigation";
import { fixtureSnapshot } from "./helpers";

const model = buildModel(fixtureSnapshot(), {
  kinds: new Set(NODE_KINDS),
  clients: "collapsed",
  toggledGroups: new Set(),
});

it("follows the tree with arrows (vertical)", () => {
  expect(nextGraphFocus(model, "dev:uuid-core", "ArrowUp", "vertical")).toBe(
    "dev:uuid-gw",
  );
  expect(nextGraphFocus(model, "dev:uuid-core", "ArrowDown", "vertical")).toBe(
    "dev:uuid-flex",
  );
  expect(nextGraphFocus(model, "dev:uuid-flex", "ArrowRight", "vertical")).toBe(
    "dev:uuid-lite",
  );
  expect(nextGraphFocus(model, "dev:uuid-flex", "ArrowLeft", "vertical")).toBe(
    "dev:uuid-flex",
  );
  expect(nextGraphFocus(model, "dev:uuid-gw", "ArrowUp", "vertical")).toBe(
    "wan:uuid-gw:wan",
  );
  expect(
    nextGraphFocus(model, "group:dev:uuid-ap", "ArrowDown", "vertical"),
  ).toBe("group:dev:uuid-ap");
});

it("moves between a gateway and its WANs", () => {
  expect(
    nextGraphFocus(model, "wan:uuid-gw:wan", "ArrowDown", "vertical"),
  ).toBe("dev:uuid-gw");
  expect(
    nextGraphFocus(model, "wan:uuid-gw:wan", "ArrowRight", "vertical"),
  ).toBe("wan:uuid-gw:wan2");
  expect(
    nextGraphFocus(model, "wan:uuid-gw:wan2", "ArrowRight", "vertical"),
  ).toBe("wan:uuid-gw:wan2");
  expect(nextGraphFocus(model, "wan:uuid-gw:wan", "ArrowUp", "vertical")).toBe(
    "wan:uuid-gw:wan",
  );
});

it("stays on a root without WANs when moving up", () => {
  const noWans = buildModel(fixtureSnapshot(), {
    kinds: new Set(NODE_KINDS.filter((k) => k !== "wan")),
    clients: "collapsed",
    toggledGroups: new Set(),
  });
  expect(nextGraphFocus(noWans, "dev:uuid-gw", "ArrowUp", "vertical")).toBe(
    "dev:uuid-gw",
  );
  expect(nextGraphFocus(noWans, "dev:uuid-gw", "ArrowDown", "vertical")).toBe(
    "dev:uuid-core",
  );
});

it("rotates the arrow keys when horizontal", () => {
  expect(
    nextGraphFocus(model, "dev:uuid-core", "ArrowLeft", "horizontal"),
  ).toBe("dev:uuid-gw");
  expect(
    nextGraphFocus(model, "dev:uuid-core", "ArrowRight", "horizontal"),
  ).toBe("dev:uuid-flex");
  expect(
    nextGraphFocus(model, "dev:uuid-flex", "ArrowDown", "horizontal"),
  ).toBe("dev:uuid-lite");
});

it("starts at the first root and ignores other keys", () => {
  expect(nextGraphFocus(model, undefined, "ArrowDown", "vertical")).toBe(
    "dev:uuid-gw",
  );
  expect(nextGraphFocus(model, "dev:gone", "ArrowDown", "vertical")).toBe(
    "dev:uuid-gw",
  );
  expect(
    nextGraphFocus(model, "dev:uuid-core", "Enter", "vertical"),
  ).toBeUndefined();
});
