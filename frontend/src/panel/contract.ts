/**
 * Wire contract of the `unifi_insights/device_groups/*` WebSocket commands, schema 1.
 *
 * Kept separate from `frontend/src/contract.ts` (topology card contract).
 */

export const DEVICE_GROUPS_SCHEMA_VERSION = 1;

export const WS_DEVICE_GROUPS_SOURCES = "unifi_insights/device_groups/sources";
export const WS_DEVICE_GROUPS_SUBSCRIBE =
  "unifi_insights/device_groups/subscribe";

export const ERR_ENTRY_NOT_FOUND = "entry_not_found";
export const ERR_ENTRY_NOT_LOADED = "entry_not_loaded";
export const ERR_GROUPING_DISABLED = "grouping_disabled";

export const DEVICE_GROUPS_STATUSES = [
  "ok",
  "entry_unloaded",
  "grouping_disabled",
] as const;
export type DeviceGroupsStatus = (typeof DEVICE_GROUPS_STATUSES)[number];

export const DEVICE_GROUP_SECTION_KINDS = [
  "site",
  "unassigned",
  "other",
] as const;
export type DeviceGroupSectionKind =
  (typeof DEVICE_GROUP_SECTION_KINDS)[number];

export const DEVICE_GROUP_KINDS = [
  "gateway",
  "switch",
  "access_point",
  "network_device",
  "camera",
  "other",
] as const;
export type DeviceGroupKind = (typeof DEVICE_GROUP_KINDS)[number];

export const DEVICE_GROUP_REASONS = [
  "no_uplink",
  "historical",
  "ambiguous",
  "parent_not_found",
  "cycle",
  "stale",
] as const;
export type DeviceGroupReason = (typeof DEVICE_GROUP_REASONS)[number];

export interface DeviceGroupRow {
  key: string;
  name: string;
  kind: DeviceGroupKind | (string & {});
  ha_device_id: string | null;
  parent_key: string | null;
  port: number | null;
  reason: DeviceGroupReason | (string & {}) | null;
}

export interface DeviceGroupSection {
  key: string;
  kind: DeviceGroupSectionKind | (string & {});
  title: string | null;
  rows: DeviceGroupRow[];
}

export interface DeviceGroupsSnapshot {
  schema_version: number;
  entry_id: string;
  status: DeviceGroupsStatus | (string & {});
  revision: string;
  stale: boolean;
  sections: DeviceGroupSection[];
}

export interface DeviceGroupSourceEntry {
  entry_id: string;
  title: string;
}

export interface DeviceGroupsSourcesResponse {
  schema_version: number;
  entries: DeviceGroupSourceEntry[];
}

/** The backend speaks a schema this panel was not built for. */
export class IncompatibleDeviceGroupsSchemaError extends Error {
  readonly received: unknown;

  constructor(received: unknown) {
    super(`Unsupported device_groups schema_version ${String(received)}`);
    this.name = "IncompatibleDeviceGroupsSchemaError";
    this.received = received;
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isStringOrNull = (value: unknown): value is string | null =>
  value === null || typeof value === "string";

function assertRow(raw: unknown, index: number, sectionKey: string): void {
  if (!isRecord(raw))
    throw new TypeError(
      `Device groups row ${sectionKey}[${index}] is not an object`,
    );
  for (const field of ["key", "name", "kind"] as const) {
    if (typeof raw[field] !== "string")
      throw new TypeError(
        `Device groups row ${sectionKey}[${index}].${field} is not a string`,
      );
  }
  for (const field of ["ha_device_id", "parent_key", "reason"] as const) {
    if (!isStringOrNull(raw[field]))
      throw new TypeError(
        `Device groups row ${sectionKey}[${index}].${field} is not string | null`,
      );
  }
  if (
    raw.port !== null &&
    (typeof raw.port !== "number" || !Number.isFinite(raw.port))
  )
    throw new TypeError(
      `Device groups row ${sectionKey}[${index}].port is not number | null`,
    );
}

function assertSection(raw: unknown, index: number): void {
  if (!isRecord(raw))
    throw new TypeError(`Device groups section[${index}] is not an object`);
  for (const field of ["key", "kind"] as const) {
    if (typeof raw[field] !== "string")
      throw new TypeError(
        `Device groups section[${index}].${field} is not a string`,
      );
  }
  if (!isStringOrNull(raw.title))
    throw new TypeError(
      `Device groups section[${index}].title is not string | null`,
    );
  if (!Array.isArray(raw.rows))
    throw new TypeError(`Device groups section[${index}].rows is not a list`);
  const sectionKey = raw.key as string;
  raw.rows.forEach((row, rowIndex) => assertRow(row, rowIndex, sectionKey));
}

function assertSchemaVersion(raw: Record<string, unknown>, name: string): void {
  if (typeof raw.schema_version !== "number")
    throw new TypeError(`${name} field schema_version is not a number`);
  if (raw.schema_version !== DEVICE_GROUPS_SCHEMA_VERSION)
    throw new IncompatibleDeviceGroupsSchemaError(raw.schema_version);
}

/**
 * Validate a `device_groups/sources` response. Throws
 * IncompatibleDeviceGroupsSchemaError for another schema version and TypeError
 * for a malformed payload.
 */
export function parseDeviceGroupsSources(
  raw: unknown,
): DeviceGroupsSourcesResponse {
  if (!isRecord(raw))
    throw new TypeError("Device groups sources response is not an object");
  assertSchemaVersion(raw, "Device groups sources");
  if (!Array.isArray(raw.entries))
    throw new TypeError("Device groups sources field entries is not a list");
  raw.entries.forEach((entry, index) => {
    if (!isRecord(entry))
      throw new TypeError(
        `Device groups source entry[${index}] is not an object`,
      );
    if (typeof entry.entry_id !== "string")
      throw new TypeError(
        `Device groups source entry[${index}].entry_id is not a string`,
      );
    if (typeof entry.title !== "string")
      throw new TypeError(
        `Device groups source entry[${index}].title is not a string`,
      );
  });
  return raw as unknown as DeviceGroupsSourcesResponse;
}

/**
 * Validate a pushed `device_groups/subscribe` snapshot. Throws
 * IncompatibleDeviceGroupsSchemaError for another schema version and TypeError
 * for a malformed payload.
 */
export function parseDeviceGroupsSnapshot(raw: unknown): DeviceGroupsSnapshot {
  if (!isRecord(raw))
    throw new TypeError("Device groups snapshot is not an object");
  assertSchemaVersion(raw, "Device groups snapshot");
  for (const field of ["entry_id", "status", "revision"] as const) {
    if (typeof raw[field] !== "string")
      throw new TypeError(
        `Device groups snapshot field ${field} is not a string`,
      );
  }
  if (typeof raw.stale !== "boolean")
    throw new TypeError("Device groups snapshot field stale is not a boolean");
  if (!Array.isArray(raw.sections))
    throw new TypeError("Device groups snapshot field sections is not a list");
  raw.sections.forEach((section, index) => assertSection(section, index));
  return raw as unknown as DeviceGroupsSnapshot;
}
