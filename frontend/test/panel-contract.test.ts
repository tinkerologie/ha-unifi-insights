import { describe, expect, it } from "vitest";
import {
  DEVICE_GROUPS_SCHEMA_VERSION,
  IncompatibleDeviceGroupsSchemaError,
  WS_DEVICE_GROUPS_SOURCES,
  WS_DEVICE_GROUPS_SUBSCRIBE,
  parseDeviceGroupsSnapshot,
  parseDeviceGroupsSources,
} from "../src/panel/contract";

const VALID_SOURCES = {
  schema_version: 1,
  entries: [{ entry_id: "abc", title: "Home UDM" }],
};

const VALID_SNAPSHOT = {
  schema_version: 1,
  entry_id: "abc",
  status: "ok",
  revision: "3f2a9c01d4e5b6a7",
  stale: false,
  sections: [
    {
      key: "site:default",
      kind: "site",
      title: "Default",
      rows: [
        {
          key: "net:default:gw",
          name: "UDM Pro",
          kind: "gateway",
          ha_device_id: "f00",
          parent_key: null,
          port: null,
          reason: null,
        },
        {
          key: "cam:c1",
          name: "Front door",
          kind: "camera",
          ha_device_id: "f01",
          parent_key: "net:default:gw",
          port: 5,
          reason: null,
        },
      ],
    },
    { key: "unassigned", kind: "unassigned", title: null, rows: [] },
    { key: "other", kind: "other", title: null, rows: [] },
  ],
};

describe("panel contract", () => {
  it("exports schema version 1 and WS command constants", () => {
    expect(DEVICE_GROUPS_SCHEMA_VERSION).toBe(1);
    expect(WS_DEVICE_GROUPS_SOURCES).toBe(
      "unifi_insights/device_groups/sources",
    );
    expect(WS_DEVICE_GROUPS_SUBSCRIBE).toBe(
      "unifi_insights/device_groups/subscribe",
    );
  });

  describe("parseDeviceGroupsSources", () => {
    it("parses a valid sources response", () => {
      expect(parseDeviceGroupsSources(VALID_SOURCES)).toEqual(VALID_SOURCES);
    });

    it("throws IncompatibleDeviceGroupsSchemaError on schema mismatch", () => {
      expect(() =>
        parseDeviceGroupsSources({ ...VALID_SOURCES, schema_version: 2 }),
      ).toThrow(IncompatibleDeviceGroupsSchemaError);
    });

    it.each([
      null,
      [],
      { ...VALID_SOURCES, schema_version: "1" },
      { ...VALID_SOURCES, entries: "bad" },
      { ...VALID_SOURCES, entries: [null] },
      { ...VALID_SOURCES, entries: [{ entry_id: 1, title: "x" }] },
      { ...VALID_SOURCES, entries: [{ entry_id: "a", title: 2 }] },
    ])("rejects malformed sources payload %#", (raw) => {
      expect(() => parseDeviceGroupsSources(raw)).toThrow(TypeError);
    });
  });

  describe("parseDeviceGroupsSnapshot", () => {
    it("parses a valid snapshot", () => {
      expect(parseDeviceGroupsSnapshot(VALID_SNAPSHOT)).toEqual(VALID_SNAPSHOT);
    });

    it("throws IncompatibleDeviceGroupsSchemaError on schema mismatch", () => {
      expect(() =>
        parseDeviceGroupsSnapshot({ ...VALID_SNAPSHOT, schema_version: 99 }),
      ).toThrow(IncompatibleDeviceGroupsSchemaError);
    });

    it.each([
      null,
      "bad",
      { ...VALID_SNAPSHOT, schema_version: "1" },
      { ...VALID_SNAPSHOT, entry_id: 123 },
      { ...VALID_SNAPSHOT, status: null },
      { ...VALID_SNAPSHOT, revision: 0 },
      { ...VALID_SNAPSHOT, stale: "false" },
      { ...VALID_SNAPSHOT, sections: {} },
      { ...VALID_SNAPSHOT, sections: [null] },
      {
        ...VALID_SNAPSHOT,
        sections: [{ key: 1, kind: "site", title: null, rows: [] }],
      },
      {
        ...VALID_SNAPSHOT,
        sections: [{ key: "s", kind: 1, title: null, rows: [] }],
      },
      {
        ...VALID_SNAPSHOT,
        sections: [{ key: "s", kind: "site", title: 1, rows: [] }],
      },
      {
        ...VALID_SNAPSHOT,
        sections: [{ key: "s", kind: "site", title: null, rows: "no" }],
      },
      {
        ...VALID_SNAPSHOT,
        sections: [{ key: "s", kind: "site", title: null, rows: [null] }],
      },
      {
        ...VALID_SNAPSHOT,
        sections: [
          {
            key: "s",
            kind: "site",
            title: null,
            rows: [
              {
                key: 1,
                name: "x",
                kind: "other",
                ha_device_id: null,
                parent_key: null,
                port: null,
                reason: null,
              },
            ],
          },
        ],
      },
      {
        ...VALID_SNAPSHOT,
        sections: [
          {
            key: "s",
            kind: "site",
            title: null,
            rows: [
              {
                key: "k",
                name: "x",
                kind: "other",
                ha_device_id: 42,
                parent_key: null,
                port: null,
                reason: null,
              },
            ],
          },
        ],
      },
      {
        ...VALID_SNAPSHOT,
        sections: [
          {
            key: "s",
            kind: "site",
            title: null,
            rows: [
              {
                key: "k",
                name: "x",
                kind: "other",
                ha_device_id: null,
                parent_key: null,
                port: "5",
                reason: null,
              },
            ],
          },
        ],
      },
      {
        ...VALID_SNAPSHOT,
        sections: [
          {
            key: "s",
            kind: "site",
            title: null,
            rows: [
              {
                key: "k",
                name: "x",
                kind: "other",
                ha_device_id: null,
                parent_key: null,
                port: Number.NaN,
                reason: null,
              },
            ],
          },
        ],
      },
    ])("rejects malformed snapshot payload %#", (raw) => {
      expect(() => parseDeviceGroupsSnapshot(raw)).toThrow(TypeError);
    });
  });
});
