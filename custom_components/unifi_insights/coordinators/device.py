# Copyright 2026 UniFi Insights contributors
"""Device coordinator for UniFi Insights - handles fast-changing device data."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final, Literal, NamedTuple

from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceNotFound,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.network.models import (
    device_id_is_mac,
    parse_outlet_metrics,
)

from custom_components.unifi_insights.client_wake import (
    WAKE_ON_LAN_DOMAIN,
    WAKE_ON_LAN_SERVICE,
    derive_directed_broadcast,
    find_client_by_mac,
    first_non_blank_text,
    history_network_hint,
)
from custom_components.unifi_insights.const import (
    DOMAIN,
    MAX_CONSECUTIVE_MISSING_POLLS,
    SCAN_INTERVAL_DEVICE,
)
from custom_components.unifi_insights.data_transforms import (
    CPU_STAT_KEYS,
    MEMORY_STAT_KEYS,
    NETWORK_FACTS_KEY,
    UPTIME_STAT_KEYS,
    build_site_network_facts,
    device_facts,
    normalize_legacy_storage,
    normalize_legacy_wans,
)
from custom_components.unifi_insights.topology import (
    first_present,
    normalize_mac,
)

from .base import CommandFailure, UnifiInsightsBaseCoordinator, async_execute_command

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from unifi_official_api.network import UniFiNetworkClient
    from unifi_official_api.protect import UniFiProtectClient

    from custom_components.unifi_insights.data_transforms import NetworkPortFacts

    from .config import UnifiInsightsConfigCoordinator

_LOGGER = logging.getLogger(__name__)

WAKE_BROADCAST_TIMEOUT_SECONDS: Final = 5

# A site's legacy device MACs from the same poll, for the stale-device
# cross-check: the normalized MACs, LEGACY_UNVERIFIED when the legacy call
# failed or returned no devices, or None when the site has no classic name
# and so no legacy view at all.
LEGACY_UNVERIFIED: Final = "unverified"
type LegacyMacs = frozenset[str] | Literal["unverified"] | None


class SiteProcessResult(NamedTuple):
    """Result of processing a single site's devices and clients."""

    devices: dict[str, dict[str, Any]]
    stats: dict[str, dict[str, Any]]
    clients: dict[str, dict[str, Any]]
    source: Literal["v1", "legacy"]
    device_macs: dict[str, str]
    # Raw controller outlet arrays by device ID, captured before the display
    # normalizer runs. Private poll data: never added to a device record.
    raw_outlet_arrays: dict[str, dict[str, Any]]
    legacy_macs: LegacyMacs = None


def get_device_port(
    coordinator_data: Any,
    site_id: str | None,
    device_id: str | None,
    port_idx: int,
) -> Mapping[str, Any] | None:
    """
    Return a device's port record, or None if it is not found.

    Reads the shared port tuple of the published read model and matches the
    raw index exactly: ``3`` finds ``3``, never ``"03"`` (PoE button and
    action discovery only accept integer indexes).
    """
    if not site_id or not device_id or not isinstance(coordinator_data, dict):
        return None
    facts = device_facts(coordinator_data, site_id, device_id)
    port = facts.port_by_raw_index(port_idx) if facts is not None else None
    return port.record if port is not None else None


def port_can_be_power_cycled(port: Mapping[str, Any]) -> bool:
    """Return True if port can be power-cycled (PoE is enabled)."""
    return isinstance(port.get("poe"), dict) and port["poe"].get("enabled") is True


def power_cyclable_ports(
    coordinator_data: Mapping[str, Any], site_id: str, device_id: str
) -> tuple[NetworkPortFacts, ...]:
    """
    Return the device's ports that a PoE power cycle can target.

    A port qualifies when its raw index is an integer (it feeds the button's
    unique ID, so it is never normalised) and PoE is enabled on it.
    """
    facts = device_facts(coordinator_data, site_id, device_id)
    return tuple(
        port
        for port in (facts.ports if facts is not None else ())
        if isinstance(port.raw_index, int) and port_can_be_power_cycled(port.record)
    )


def require_power_cyclable_port(
    coordinator_data: Any, site_id: str | None, device_id: str | None, port_idx: int
) -> Mapping[str, Any]:
    """
    Return the port a PoE power cycle targets, or raise a validation error.

    The shared eligibility check for every power-cycle caller: the device
    must exist in the site, the port must exist on it, and PoE must be
    enabled on the port.
    """
    devices = (
        coordinator_data.get("devices") if isinstance(coordinator_data, dict) else None
    )
    site_devices = devices.get(site_id) if isinstance(devices, dict) else None
    device_data = (
        site_devices.get(device_id) if isinstance(site_devices, dict) else None
    )
    if not isinstance(device_data, dict):
        msg = f"Device {device_id} was not found in site {site_id}"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="device_not_found",
            translation_placeholders={
                "device_id": str(device_id),
                "site_id": str(site_id),
            },
        )

    port = get_device_port(coordinator_data, site_id, device_id, port_idx)
    if port is None:
        msg = f"Port {port_idx} was not found on device {device_id}"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="port_not_found",
            translation_placeholders={
                "port_idx": str(port_idx),
                "device_id": str(device_id),
            },
        )

    if not port_can_be_power_cycled(port):
        msg = f"Port {port_idx} on device {device_id} is not PoE-enabled"
        raise ServiceValidationError(
            msg,
            translation_domain=DOMAIN,
            translation_key="port_not_poe",
            translation_placeholders={
                "port_idx": str(port_idx),
                "device_id": str(device_id),
            },
        )
    return port


def _fill_missing_stat(
    stats: dict[str, Any],
    target_key: str,
    existing_keys: tuple[str, ...],
    value: Any,
    caster: Callable[[Any], Any],
) -> None:
    """Set ``target_key`` from a legacy reading when v1 reported none."""
    if value is None or any(stats.get(key) is not None for key in existing_keys):
        return
    with suppress(TypeError, ValueError):
        stats[target_key] = caster(value)


def _legacy_system_stats(legacy_device: dict[str, Any]) -> tuple[Any, Any, Any]:
    """
    Return ``(cpu, memory, uptime)`` from a legacy controller device dict.

    Gateways keyed by MAC rather than a v1 UUID report CPU and memory
    percentages under ``system-stats``. The similarly named ``sys_stats``
    object holds raw counters (``loadavg_*``, ``mem_total``, ``mem_used``) and
    carries no ``cpu``/``mem`` keys, yet real consoles report both objects.
    Each key is therefore resolved across ``system-stats``, ``sys_stats`` and
    the device root in turn, rather than picking a single object up front.
    """
    sources = [
        source
        for source in (
            legacy_device.get("system-stats"),
            legacy_device.get("sys_stats"),
            legacy_device,
        )
        if isinstance(source, dict)
    ]

    def _pick(key: str) -> Any:
        for source in sources:
            value = source.get(key)
            if value is not None:
                return value
        return None

    return _pick("cpu"), _pick("mem"), _pick("uptime")


def _merge_legacy_system_stats(
    stats: dict[str, Any], legacy_device: dict[str, Any]
) -> None:
    """Fill CPU, memory and uptime in ``stats`` from a legacy device dict."""
    cpu, mem, uptime = _legacy_system_stats(legacy_device)
    _fill_missing_stat(stats, "cpuUtilizationPct", CPU_STAT_KEYS, cpu, float)
    _fill_missing_stat(stats, "memoryUtilizationPct", MEMORY_STAT_KEYS, mem, float)
    _fill_missing_stat(stats, "uptimeSec", UPTIME_STAT_KEYS, uptime, int)


def _seed_outlet_overrides(device: Mapping[str, Any]) -> list[dict[str, Any]]:
    """
    Seed a full ``outlet_overrides`` array from the sensed ``outlet_table``.

    Copies ``_seed_outlet_overrides`` from ``unifi-official-api`` 3.0.0; see
    ``_build_outlet_overrides``.
    """
    outlet_table = device.get("outlet_table")
    if not isinstance(outlet_table, list):
        return []

    overrides: list[dict[str, Any]] = []
    for outlet in outlet_table:
        if not isinstance(outlet, dict):
            continue

        index = outlet.get("index")
        if index is None:
            index = outlet.get("outlet_idx")
        try:
            index_int = int(index)  # type: ignore[arg-type]
        except TypeError, ValueError:
            continue

        override: dict[str, Any] = {
            "index": index_int,
            "relay_state": bool(outlet.get("relay_state", False)),
        }
        if "name" in outlet:
            override["name"] = outlet["name"]
        if "cycle_enabled" in outlet:
            override["cycle_enabled"] = bool(outlet["cycle_enabled"])
        overrides.append(override)

    return overrides


def _override_index(entry: Mapping[str, Any]) -> int | None:
    """Return an ``outlet_overrides`` entry's outlet index, as the library reads it."""
    idx = entry.get("index")
    if idx is None:
        idx = entry.get("outlet_idx")
    try:
        return int(idx)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None


def _is_table_outlet(outlet: Any, outlet_index: int) -> bool:
    """Return True if an ``outlet_table`` entry is the given outlet."""
    if not isinstance(outlet, dict):
        return False
    idx = outlet.get("index")
    if idx is None:
        idx = outlet.get("outlet_idx") or outlet.get("outletIdx")
    try:
        return idx is not None and int(idx) == outlet_index
    except TypeError, ValueError:
        return False


def _build_outlet_overrides(
    device: Mapping[str, Any],
    outlet_index: int,
    *,
    state: bool,
    cycle_enabled: bool | None,
) -> list[dict[str, Any]] | None:
    """
    Return the complete ``outlet_overrides`` array an outlet write sends.

    The controller treats ``outlet_overrides`` as the complete desired state,
    so the cache must hold exactly what was sent or the next write reverts
    earlier outlets (#358). This copies the read-modify-write rule of
    ``DevicesEndpoint.set_outlet_state`` in ``unifi-official-api`` 3.0.0 and
    returns ``None`` where that method raises ``UniFiValidationError``.
    Re-check it against the library whenever the version pin changes.
    """
    raw_overrides = device.get("outlet_overrides")
    outlet_overrides: list[dict[str, Any]] = []
    if isinstance(raw_overrides, list):
        outlet_overrides = [
            dict(item) for item in raw_overrides if isinstance(item, dict)
        ]

    if not outlet_overrides:
        outlet_overrides = _seed_outlet_overrides(device)

    if not outlet_overrides:
        return None

    found = False
    for override in outlet_overrides:
        if _override_index(override) == outlet_index:
            override["relay_state"] = state
            if cycle_enabled is not None:
                override["cycle_enabled"] = cycle_enabled
            found = True
            break

    if not found:
        new_override: dict[str, Any] = {
            "index": outlet_index,
            "relay_state": state,
        }
        if cycle_enabled is not None:
            new_override["cycle_enabled"] = cycle_enabled
        outlet_overrides.append(new_override)

    return outlet_overrides


# How long a successful outlet write is held over polls that still report the
# controller's older outlet values while the PDU applies it (#376). The lag
# seen on live hardware is 3 to 15 s. After this the poll wins, so a write the
# controller rejected or never applied does not stick.
OUTLET_WRITE_GUARD_SECONDS: Final = 60.0


class PendingOutletWrite(NamedTuple):
    """
    A successful outlet write the controller has not reported back (#376).

    This record holds only displayed values: the written fields and the sent
    entry. Confirmation and ``OUTLET_WRITE_GUARD_SECONDS`` release the fields
    here and nothing else. It never authorises a write: payload safety comes
    from ``OutletPayloadProvenance``, evaluated on raw poll data before this
    overlay runs.
    """

    # The written fields ("relay_state" or "cycle_enabled") and their values.
    fields: Mapping[str, bool]
    # The outlet's entry in the outlet_overrides array that was sent, if any.
    override: Mapping[str, Any] | None
    # time.monotonic() of the last write to the outlet.
    written_at: float


# A pending write's site ID, device ID and outlet index.
type PendingOutletKey = tuple[str, str, int]


def _hold_outlet_write(
    device: dict[str, Any], outlet_index: int, write: PendingOutletWrite
) -> tuple[dict[str, Any], dict[str, bool]]:
    """
    Return the device with the write's unconfirmed fields, and those fields.

    A field is confirmed when every polled array that has the outlet reports
    the written value. A poll with no report for the field at all (no usable
    array, or the outlet missing from both) confirms nothing: the field stays
    held, with nothing to patch. A non-empty ``outlet_overrides`` without the
    outlet does not confirm it either: the next write is built from that
    array, so the sent entry is appended. Patched arrays and entries are new
    copies; the device is returned as is when there is nothing to patch.
    """
    raw_overrides = device.get("outlet_overrides")
    overrides: list[Any] | None = (
        raw_overrides
        if isinstance(raw_overrides, list)
        and any(isinstance(entry, dict) for entry in raw_overrides)
        else None
    )
    override_pos = (
        next(
            (
                i
                for i, entry in enumerate(overrides)
                if isinstance(entry, dict) and _override_index(entry) == outlet_index
            ),
            None,
        )
        if overrides is not None
        else None
    )
    raw_table = device.get("outlet_table")
    table: list[Any] = raw_table if isinstance(raw_table, list) else []
    table_pos = next(
        (i for i, o in enumerate(table) if _is_table_outlet(o, outlet_index)),
        None,
    )

    held: dict[str, bool] = {}
    for field, value in write.fields.items():
        reported: list[Any] = []
        if overrides is not None:
            reported.append(
                overrides[override_pos].get(field) if override_pos is not None else None
            )
        if table_pos is not None and field in table[table_pos]:
            reported.append(table[table_pos][field])
        if not reported or any(polled != value for polled in reported):
            held[field] = value
    if not held or (overrides is None and table_pos is None):
        return device, held

    updated = dict(device)
    if overrides is not None:
        patched_overrides = list(overrides)
        if override_pos is not None:
            patched_overrides[override_pos] = {**overrides[override_pos], **held}
        else:
            patched_overrides.append(
                {**(write.override or {"index": outlet_index}), **held}
            )
        updated["outlet_overrides"] = patched_overrides
    if table_pos is not None:
        patched_table = list(table)
        patched_table[table_pos] = {**table[table_pos], **held}
        updated["outlet_table"] = patched_table
    return updated, held


def reconcile_pending_outlet_writes(
    site_id: str,
    devices: dict[str, Any],
    pending: Mapping[PendingOutletKey, PendingOutletWrite],
    now: float,
) -> tuple[dict[str, Any], dict[PendingOutletKey, PendingOutletWrite]]:
    """
    Keep a site's pending outlet writes over its polled devices (#376).

    For a few seconds after an outlet write the controller still reports the
    older ``outlet_overrides`` and ``outlet_table`` values while the PDU
    applies it. A poll in that window would replace the published, sent
    array, and the next outlet command, built from it, would switch the
    earlier outlet back. Until the controller reports a written field, the
    field is patched into copies of the polled entries; every other polled
    field is kept. A pending write is released once confirmed, once
    ``OUTLET_WRITE_GUARD_SECONDS`` have passed since the last write to the
    outlet, or when its device is gone from the poll.

    Release here affects display only: it never drops or restores payload
    integrity (``OutletPayloadProvenance``), which only a successful poll
    decides. A saved entry appended below is never a provenance input.

    Pure: the polled maps are never mutated. Returns the devices (the given
    map when nothing is patched) and the site's writes still pending.
    """
    result = devices
    kept: dict[PendingOutletKey, PendingOutletWrite] = {}
    for key, write in pending.items():
        write_site_id, device_id, outlet_index = key
        if write_site_id != site_id:
            continue
        device = result.get(device_id)
        if not isinstance(device, dict):
            continue
        updated, held = _hold_outlet_write(device, outlet_index, write)
        if not held:
            continue
        if now - write.written_at >= OUTLET_WRITE_GUARD_SECONDS:
            _LOGGER.debug(
                "Device coordinator: Outlet %s of device %s still reports old %s "
                "%.0f s after the write; using the polled values",
                outlet_index,
                device_id,
                sorted(held),
                OUTLET_WRITE_GUARD_SECONDS,
            )
            continue
        if updated is not device:
            if result is devices:
                result = dict(devices)
            result[device_id] = updated
        kept[key] = write._replace(fields=held)
    return result, kept


class OutletPayloadProvenance(NamedTuple):
    """
    Whether the latest successful poll gave a complete outlet baseline (#376).

    A separate record from ``PendingOutletWrite``, with a different lifetime:
    only a successful poll that passes the structural rule sets ``trusted``,
    and confirmation or timeout release of a pending field never touches it.
    The rule is a structural safeguard. It does not prove the physical outlet
    inventory or the controller's semantic state, and it never replays a
    historical sent array.
    """

    site_id: str
    device_id: str
    classic_id: str | None
    mac: str | None
    source: Literal["v1", "legacy"]
    # Every index a complete baseline must keep covering while the identity and
    # source stay unchanged. Only a trusted poll grows it.
    required_indexes: frozenset[int]
    # (index, field) pairs the raw table carried, so the transport payload must
    # keep them. Raw presence, never a normalizer's manufactured default.
    required_row_fields: frozenset[tuple[int, str]]
    trusted: bool


# A payload provenance record's site and device ID.
type OutletProvenanceKey = tuple[str, str]

_ABSENT: Final = object()

# The controller arrays the outlet payload rule judges. They are read before
# ``_merge_legacy_outlet_data`` normalizes ``outlet_table`` for display.
RAW_OUTLET_KEYS: Final = ("outlet_table", "outlet_overrides")


def _raw_outlet_arrays(device: Mapping[str, Any]) -> dict[str, Any]:
    """Return the outlet arrays a controller record carries, untouched."""
    return {key: device[key] for key in RAW_OUTLET_KEYS if key in device}


def _outlet_row_index(row: Mapping[str, Any]) -> int | None:
    """
    Return a row's outlet index for the payload-integrity rule.

    Stricter than the builder's ``_override_index``: a bool, a float, a
    non-integral type or a row that only carries the table-only
    ``outletIdx`` alias is malformed here. The rule accepts exactly what the
    read-modify-write path reads: ``int`` indexes through ``index`` or
    ``outlet_idx``, including integer strings that ``int()`` accepts.
    """
    index = row.get("index")
    if index is None:
        index = row.get("outlet_idx")
    if isinstance(index, bool):
        return None
    if isinstance(index, int):
        return index
    if isinstance(index, str):
        try:
            return int(index)
        except ValueError:
            return None
    return None


def _outlet_rows_by_index(rows: list[Any]) -> dict[int, dict[str, Any]] | None:
    """Return rows keyed by index, or None when a row or index is malformed."""
    by_index: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        index = _outlet_row_index(row)
        if index is None or index in by_index:
            return None
        by_index[index] = row
    return by_index


def _outlet_identity_mac(device: Mapping[str, Any]) -> str | None:
    """Return a device's normalized MAC for the provenance identity."""
    raw_mac = device.get("macAddress") or device.get("mac")
    if not isinstance(raw_mac, str) or not raw_mac:
        return None
    return raw_mac.strip().lower()


def evaluate_outlet_payload_provenance(
    previous: OutletPayloadProvenance | None,
    site_id: str,
    device_id: str,
    source: Literal["v1", "legacy"],
    *,
    device: Mapping[str, Any],
    raw_outlet_arrays: Mapping[str, Any],
) -> OutletPayloadProvenance:
    """
    Return the payload-integrity provenance of one raw polled device (#376).

    Run on the untouched raw controller arrays, before the display normalizer
    and before the pending-write overlay, so only controller data is judged.
    ``device`` supplies the identity fields only. A poll is trusted when
    ``outlet_table`` is a list, ``outlet_overrides`` is a list or absent, every
    row is a dictionary with a unique integer index, the effective baseline
    covers the required identity set, and every baseline row keeps its
    required fields:

    - ``relay_state`` must be a boolean.
    - ``cycle_enabled`` and ``name`` must be present when the matching
      ``outlet_table`` row carries that key.

    The effective baseline is a non-empty ``outlet_overrides``; otherwise it
    is the seed the builder makes from a structurally complete table. A
    non-empty override array that is sparse, or lacks a table-carried field,
    is untrusted. A poll that omits a previously required index is untrusted.
    An untrusted poll keeps the previous required set, so a malformed index
    cannot make a device permanently unwritable.

    A changed identity (site, device ID, classic ``_id``, normalized MAC or
    source) discards the previous record and evaluates a cold start. Source
    switches therefore need their own trusted poll.
    """
    mac = _outlet_identity_mac(device)
    raw_classic_id = device.get("_id")
    classic_id = raw_classic_id if isinstance(raw_classic_id, str) else None
    same_identity = (
        previous is not None
        and previous.site_id == site_id
        and previous.device_id == device_id
        and previous.classic_id == classic_id
        and previous.mac == mac
        and previous.source == source
    )
    required = (
        previous.required_indexes
        if same_identity and previous is not None
        else frozenset()
    )

    def record(
        indexes: frozenset[int],
        row_fields: frozenset[tuple[int, str]] = frozenset(),
        *,
        trusted: bool,
    ) -> OutletPayloadProvenance:
        return OutletPayloadProvenance(
            site_id=site_id,
            device_id=device_id,
            classic_id=classic_id,
            mac=mac,
            source=source,
            required_indexes=indexes,
            required_row_fields=row_fields,
            trusted=trusted,
        )

    table = raw_outlet_arrays.get("outlet_table", _ABSENT)
    if not isinstance(table, list):
        return record(required, trusted=False)
    raw_overrides = raw_outlet_arrays.get("outlet_overrides", _ABSENT)
    if raw_overrides is _ABSENT:
        overrides: list[Any] | None = None
    elif isinstance(raw_overrides, list):
        overrides = raw_overrides
    else:
        return record(required, trusted=False)

    table_rows = _outlet_rows_by_index(table)
    if table_rows is None:
        return record(required, trusted=False)
    override_rows = _outlet_rows_by_index(overrides) if overrides is not None else None
    if overrides is not None and override_rows is None:
        return record(required, trusted=False)

    poll_indexes = set(table_rows)
    if override_rows is not None:
        poll_indexes.update(override_rows)
    required_now = required | poll_indexes
    if override_rows:
        baseline: Mapping[int, Mapping[str, Any]] = override_rows
    else:
        # A complete table may seed the payload (#358); its relay states must
        # be real booleans, not the builder's False default for a missing key.
        if any(
            not isinstance(row.get("relay_state"), bool) for row in table_rows.values()
        ):
            return record(required, trusted=False)
        baseline = {
            row["index"]: row for row in _seed_outlet_overrides({"outlet_table": table})
        }

    # A baseline can only cover indexes this poll reported, so a poll that
    # omits an index a trusted poll established fails here. Keep the previous
    # set: a later complete poll can restore trust.
    if set(baseline) != required_now:
        return record(required, trusted=False)
    required_row_fields: set[tuple[int, str]] = set()
    for index, raw_table_row in table_rows.items():
        for field in ("cycle_enabled", "name"):
            if field in raw_table_row:
                required_row_fields.add((index, field))
    for index, row in baseline.items():
        if not isinstance(row.get("relay_state"), bool):
            return record(required, trusted=False)
        table_row = table_rows.get(index)
        if table_row is None:
            continue
        for field in ("cycle_enabled", "name"):
            if field in table_row and field not in row:
                return record(required, trusted=False)

    return record(frozenset(required_now), frozenset(required_row_fields), trusted=True)


def outlet_payload_is_safe(
    provenance: OutletPayloadProvenance | None,
    *,
    site_id: str,
    device_id: str,
    device: Mapping[str, Any],
    sent: list[dict[str, Any]] | None,
    outlet_index: int,
) -> bool:
    """
    Return whether ``sent`` is a complete, trusted outlet write payload (#376).

    Called under the refresh lock, before transport. A write needs a trusted
    provenance record, a cached identity that still matches it, a non-``None``
    payload with exactly one row for every required index (and no other row),
    the target index in that set, and every row's required fields. Otherwise
    the command fails closed with no PUT, no overlay, no pending record and no
    refresh. The saved entries in a pending write are never consulted.
    """
    if provenance is None or not provenance.trusted:
        return False
    if provenance.site_id != site_id or provenance.device_id != device_id:
        return False
    raw_classic_id = device.get("_id")
    classic_id = raw_classic_id if isinstance(raw_classic_id, str) else None
    if provenance.classic_id != classic_id or provenance.mac != _outlet_identity_mac(
        device
    ):
        return False
    if sent is None:
        return False

    sent_rows = _outlet_rows_by_index(sent)
    if sent_rows is None:
        return False
    if set(sent_rows) != set(provenance.required_indexes):
        return False
    if outlet_index not in sent_rows:
        return False
    required_fields = set(provenance.required_row_fields)
    for index, row in sent_rows.items():
        if not isinstance(row.get("relay_state"), bool):
            return False
        for field in ("cycle_enabled", "name"):
            if (index, field) in required_fields and field not in row:
                return False
    return True


# How many consecutive polls a device's last good statistics are reused for
# when its statistics call keeps failing, before its stats are dropped. Site
# VPN connection state is bounded the same way.
MAX_STATS_REUSE_POLLS = 3


class UnifiInsightsDeviceCoordinator(UnifiInsightsBaseCoordinator):
    """
    Coordinator for fast-changing device data (30 second updates).

    Handles:
    - Device online status
    - Device statistics (CPU, memory, uptime)
    - Client connections
    - Port statistics
    """

    def __init__(
        self,
        hass: HomeAssistant,
        network_client: UniFiNetworkClient,
        protect_client: UniFiProtectClient | None,
        entry: ConfigEntry,
        config_coordinator: UnifiInsightsConfigCoordinator,
    ) -> None:
        """Initialize the device coordinator."""
        super().__init__(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=entry,
            name="device",
            update_interval=SCAN_INTERVAL_DEVICE,
        )
        self.config_coordinator = config_coordinator
        # Track sites operating on legacy fallback to avoid spamming the log
        self._legacy_fallback_sites: set[str] = set()
        # Track per-site coordinator state committed at snapshot publication
        # (ADR-013, #391). Commits occur only when all sites succeed.
        # site_id -> source ("v1" | "legacy")
        self._site_sources: dict[str, str] = {}
        # site_id -> {normalized_mac: v1_device_id} (only from v1 polls)
        self._site_aliases: dict[str, dict[str, str]] = {}
        # site_id -> {device_id: normalized_mac}
        self._site_tracked_devices: dict[str, dict[str, str]] = {}
        # site_id -> {device_id: consecutive_missing_polls}
        self._site_absence_counts: dict[str, dict[str, int]] = {}
        # Track previous port byte counts for rate computation
        # device_id -> (monotonic sample time, per-port byte counters)
        self._prev_port_bytes: dict[str, tuple[float, dict[int, dict[str, int]]]] = {}
        # Consecutive polls each device's statistics call has failed for, and
        # the devices whose stats were reused (not refetched) this refresh.
        self._stats_failures: dict[str, int] = {}
        self._reused_stats: set[str] = set()
        # Consecutive polls each site's VPN connections call has failed for.
        self._vpn_connection_failures: dict[str, int] = {}
        # Consecutive polls each site's legacy device call has failed for.
        self._legacy_wan_failures: dict[str, int] = {}
        # Successful outlet writes the controller has not reported back yet
        # (#376), committed at snapshot publication.
        self._pending_outlet_writes: dict[PendingOutletKey, PendingOutletWrite] = {}
        # Payload integrity of each device's latest raw poll (#376), committed
        # with the pending map. A failed refresh changes neither.
        self._outlet_payload_provenance: dict[
            OutletProvenanceKey, OutletPayloadProvenance
        ] = {}
        self.data: dict[str, Any] = {
            "devices": {},
            "clients": {},
            "stats": {},
            "vpn_connections": {},
            # site_id -> SiteNetworkFacts, rebuilt (never mutated) per refresh
            NETWORK_FACTS_KEY: {},
            "last_update": None,
        }

    @staticmethod
    def _normalize_mac(value: Any) -> str | None:
        """
        Normalize a MAC address for dictionary lookups.

        Identity policy: exact legacy key matching. It only strips and
        lowercases, so a v1 device and its legacy record match on the spelling
        both endpoints share. It is deliberately not the canonical
        ``data_transforms.normalize_mac``, which validates and rewrites
        separators.
        """
        if not isinstance(value, str) or not value:
            return None
        return value.strip().lower()

    @classmethod
    def _legacy_device_for(
        cls,
        device: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Return the legacy controller dict matching a v1 device, if any."""
        mac = cls._normalize_mac(device.get("mac") or device.get("macAddress"))
        if mac is None:
            return None
        return legacy_devices_by_mac.get(mac)

    @staticmethod
    def _normalize_legacy_port(port: dict[str, Any]) -> dict[str, Any] | None:
        """
        Normalize a single legacy port_table entry into a v1-shaped port dict.

        Shared by ``_legacy_device_to_v1_dict()`` and
        ``_merge_legacy_port_data()`` so the legacy-primary fallback and the
        merge path produce identical port data, including network_name, SFP
        fields, PoE and byte stats.
        """
        port_idx = port.get("port_idx")
        if port_idx is None:
            return None

        normalized: dict[str, Any] = {
            "idx": port_idx,
            "port_idx": port_idx,
            "state": "UP" if port.get("up") else "DOWN",
            "enabled": port.get("enable", True),
            "speedMbps": port.get("speed"),
            "speed": port.get("speed"),
        }

        # Port type identification fields
        media = port.get("media")
        if media:
            normalized["media"] = media

        is_uplink = port.get("is_uplink")
        if is_uplink is not None:
            normalized["is_uplink"] = is_uplink

        port_name = port.get("name")
        if port_name:
            normalized["name"] = port_name

        ifname = port.get("ifname")
        if ifname:
            normalized["ifname"] = ifname

        network_name = port.get("network_name")
        if network_name:
            normalized["network_name"] = network_name

        # SFP module data
        sfp_found = port.get("sfp_found")
        if sfp_found is not None:
            normalized["sfp_found"] = sfp_found

        for sfp_key in (
            "sfp_part",
            "sfp_vendor",
            "sfp_serial",
            "sfp_compliance",
        ):
            sfp_val = port.get(sfp_key)
            if sfp_val is not None:
                normalized[sfp_key] = sfp_val

        # PoE data — only for ports with PoE hardware (port_poe flag)
        poe_capable = port.get("port_poe", False)
        if poe_capable:
            poe_enabled = port.get("poe_enable", False)
            poe_power = port.get("poe_power") or port.get("poePower")
            poe_good = port.get("poe_good", False)
            normalized["poe"] = {
                "enabled": bool(poe_enabled),
                "power": poe_power,
                "good": bool(poe_good),
            }

        # TX/RX bytes
        normalized["stats"] = {
            "txBytes": port.get("tx_bytes", 0),
            "rxBytes": port.get("rx_bytes", 0),
        }

        return normalized

    @classmethod
    def _legacy_device_to_v1_dict(cls, legacy: dict[str, Any]) -> dict[str, Any]:
        """
        Map a legacy ``/stat/device`` dict to a v1-shaped device dict.

        The v1 Device model uses ``id`` and camelCase aliases; the legacy
        endpoint returns ``_id`` and snake_case. Only the fields the
        downstream pipeline actually reads are mapped; everything else is
        preserved as-is. A legacy device without an ``_id`` follows the same
        fallback as the v1 model and is keyed on its MAC, so
        ``device_id_is_mac()`` correctly skips id-addressed endpoints for it.
        """
        mapped: dict[str, Any] = dict(legacy)

        # Ensure macAddress is populated from mac for the merge helpers
        mac = legacy.get("mac")
        if mac and "macAddress" not in mapped:
            mapped["macAddress"] = mac

        # _id → id (the Device model requires ``id``); fall back to MAC
        legacy_id = legacy.get("_id")
        if legacy_id and "id" not in mapped:
            mapped["id"] = legacy_id
        if "id" not in mapped and mac:
            mapped["id"] = mac

        # Map common snake_case legacy fields to camelCase v1 aliases
        field_map = {
            "fw_version": "firmwareVersion",
            "last_seen": "lastSeen",
            "site_id": "siteId",
            "tx_bytes": "txBytes",
            "rx_bytes": "rxBytes",
            "uplink_table": "uplinkTable",
        }
        for legacy_key, v1_key in field_map.items():
            if legacy_key in legacy and v1_key not in mapped:
                mapped[v1_key] = legacy[legacy_key]

        # Legacy "up" (bool) is what the online rule actually needs: it reads
        # ``state``/``status`` as a string and maps the spec states (only
        # ONLINE is online). The legacy numeric ``state`` (0/1) is not a
        # string, so it would read as unknown. Only fill the gap when no usable
        # string state exists.
        if "up" in legacy and not isinstance(mapped.get("state"), str):
            mapped["state"] = "ONLINE" if legacy["up"] else "OFFLINE"

        # Normalize port_table into ports for the sensor pipeline.
        # The sensor pipeline reads device_data["ports"] and falls back to
        # interfaces["ports"]; it does not consume port_table directly.
        port_table = legacy.get("port_table")
        if isinstance(port_table, list) and port_table and "ports" not in mapped:
            ports: list[dict[str, Any]] = []
            for port in port_table:
                if not isinstance(port, dict):
                    continue
                normalized = cls._normalize_legacy_port(port)
                if normalized is not None:
                    ports.append(normalized)
            if ports:
                mapped["ports"] = ports

        # Same parent-link block the merge path produces, so topology reads
        # one shape whichever endpoint served the device list.
        topology = cls._legacy_topology_block(legacy)
        if topology:
            mapped["topology"] = topology

        return mapped

    @staticmethod
    def _has_legacy_temperature_data(legacy_device: dict[str, Any]) -> bool:
        """Return True when legacy device data contains usable temperature info."""
        general_temperature = legacy_device.get("general_temperature")
        if general_temperature is None:
            general_temperature = legacy_device.get("generalTemperature")

        temperatures = legacy_device.get("temperatures")
        has_temperature = legacy_device.get("has_temperature")
        if has_temperature is None:
            has_temperature = legacy_device.get("hasTemperature")

        has_temperature_entries = isinstance(temperatures, list) and any(
            isinstance(item, dict) and item.get("value") is not None
            for item in temperatures
        )

        return bool(
            has_temperature
            or general_temperature is not None
            or has_temperature_entries
        )

    @classmethod
    def _merge_legacy_temperature_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Merge temperature-related legacy fields into device data."""
        mac_address = cls._normalize_mac(
            device_dict.get("macAddress") or device_dict.get("mac")
        )
        if mac_address is None:
            return

        legacy_device = legacy_devices_by_mac.get(mac_address)
        if legacy_device is None or not cls._has_legacy_temperature_data(legacy_device):
            return

        general_temperature = legacy_device.get("general_temperature")
        if general_temperature is None:
            general_temperature = legacy_device.get("generalTemperature")

        temperatures = legacy_device.get("temperatures")

        if general_temperature is not None:
            device_dict["generalTemperature"] = general_temperature

        if isinstance(temperatures, list):
            device_dict["temperatures"] = temperatures

        device_dict["hasTemperature"] = True

    @classmethod
    def _merge_legacy_port_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Merge port_table from legacy device data into device dict."""
        mac_address = cls._normalize_mac(
            device_dict.get("macAddress") or device_dict.get("mac")
        )
        if mac_address is None:
            return

        legacy_device = legacy_devices_by_mac.get(mac_address)
        if legacy_device is None:
            return

        port_table = legacy_device.get("port_table")
        if not isinstance(port_table, list) or not port_table:
            return

        # Normalize port_table entries into the format expected by sensor.py
        ports: list[dict[str, Any]] = []
        for port in port_table:
            if not isinstance(port, dict):
                continue
            # Reuse the shared normalizer so the merge path produces
            # identical port data to the legacy-primary fallback path.
            normalized = cls._normalize_legacy_port(port)
            if normalized is not None:
                ports.append(normalized)

        if ports:
            device_dict["ports"] = ports

    @classmethod
    def _merge_legacy_outlet_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Merge outlet_table and power totals from legacy data into the device."""
        mac_address = cls._normalize_mac(
            device_dict.get("macAddress") or device_dict.get("mac")
        )
        if mac_address is None:
            return

        legacy_device = legacy_devices_by_mac.get(mac_address)
        if legacy_device is None:
            return

        outlet_metrics = parse_outlet_metrics(legacy_device)
        if (
            not outlet_metrics.outlets
            and outlet_metrics.ac_power_consumption is None
            and outlet_metrics.ac_power_budget is None
        ):
            return

        if "_id" in legacy_device:
            device_dict["_id"] = legacy_device["_id"]

        if outlet_metrics.outlets:
            device_dict["outlet_table"] = [
                outlet.model_dump() for outlet in outlet_metrics.outlets
            ]
            if "outlet_overrides" in legacy_device:
                device_dict["outlet_overrides"] = legacy_device["outlet_overrides"]

        if outlet_metrics.ac_power_consumption is not None:
            device_dict["outlet_ac_power_consumption"] = (
                outlet_metrics.ac_power_consumption
            )
            device_dict["ac_power_consumption"] = outlet_metrics.ac_power_consumption

        if outlet_metrics.ac_power_budget is not None:
            device_dict["outlet_ac_power_budget"] = outlet_metrics.ac_power_budget
            device_dict["ac_power_budget"] = outlet_metrics.ac_power_budget

    @staticmethod
    def _strict_int(value: Any) -> int | None:
        """Return value when it is a real int (not a bool), else None."""
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    @classmethod
    def _legacy_topology_block(cls, legacy: dict[str, Any]) -> dict[str, Any]:
        """
        Extract the parent-link fields topology needs from a legacy device.

        The v1 API leaves ``uplink`` and ``type`` null on current firmware, so
        the classic ``/stat/device`` record is the only source of which device
        (and port) this one hangs off. ``last_uplink`` keeps the same keys when
        the device is offline and ``uplink`` has been emptied.
        """
        block: dict[str, Any] = {}

        legacy_type = legacy.get("type")
        if isinstance(legacy_type, str) and legacy_type:
            block["legacy_type"] = legacy_type.lower()

        uplink = legacy.get("uplink")
        link = uplink if isinstance(uplink, dict) else {}
        if cls._normalize_mac(link.get("uplink_mac")) is None:
            last_uplink = legacy.get("last_uplink")
            if isinstance(last_uplink, dict) and cls._normalize_mac(
                last_uplink.get("uplink_mac")
            ):
                link = {**link, **last_uplink}

        uplink_mac = cls._normalize_mac(link.get("uplink_mac"))
        if uplink_mac is not None:
            block["uplink_mac"] = uplink_mac
        for source_key, block_key in (
            ("uplink_remote_port", "uplink_remote_port"),
            ("port_idx", "uplink_port_idx"),
            ("speed", "uplink_speed"),
        ):
            value = cls._strict_int(link.get(source_key))
            if value is not None:
                block[block_key] = value
        uplink_type = link.get("type")
        if isinstance(uplink_type, str) and uplink_type:
            block["uplink_type"] = uplink_type.lower()

        return block

    @classmethod
    def _merge_legacy_uplink_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Attach the legacy parent link to a v1 device as ``topology``."""
        legacy_device = cls._legacy_device_for(device_dict, legacy_devices_by_mac)
        if legacy_device is None:
            return
        block = cls._legacy_topology_block(legacy_device)
        if block:
            device_dict["topology"] = block

    @classmethod
    def _merge_legacy_wan_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Merge per-WAN connection state from legacy gateway data."""
        mac_address = cls._normalize_mac(
            device_dict.get("macAddress") or device_dict.get("mac")
        )
        if mac_address is None:
            return
        legacy_device = legacy_devices_by_mac.get(mac_address)
        if legacy_device is None:
            return
        wans = normalize_legacy_wans(legacy_device)
        if wans:
            device_dict["wans"] = wans

    @classmethod
    def _merge_legacy_storage_data(
        cls,
        device_dict: dict[str, Any],
        legacy_devices_by_mac: dict[str, dict[str, Any]],
    ) -> None:
        """Merge the console's per-mount storage from legacy data (no extra request)."""
        mac_address = cls._normalize_mac(
            device_dict.get("macAddress") or device_dict.get("mac")
        )
        if mac_address is None:
            return
        legacy_device = legacy_devices_by_mac.get(mac_address)
        if legacy_device is None:
            return
        mounts = normalize_legacy_storage(legacy_device)
        if mounts:
            device_dict["storage_mounts"] = mounts

    def _vpn_connections_or_previous(
        self, site_id: str, connections: dict[str, dict[str, Any]] | None
    ) -> dict[str, dict[str, Any]] | None:
        """
        Return fresh VPN connections, or reuse the last good set for a few polls.

        A single failed call would otherwise flip every site-to-site VPN sensor
        on -> unknown -> on and fire automations. The reuse is bounded so a call
        that keeps failing cannot hold a stale "connected" forever; after that
        the site's entry is dropped and the sensors read unknown.
        """
        if connections is not None:
            self._vpn_connection_failures.pop(site_id, None)
            return connections
        failures = self._vpn_connection_failures.get(site_id, 0) + 1
        self._vpn_connection_failures[site_id] = failures
        previous = self.data["vpn_connections"].get(site_id)
        if failures <= MAX_STATS_REUSE_POLLS and isinstance(previous, dict):
            return previous
        return None

    def _reuse_previous_wans(self, site_id: str, devices: list[dict[str, Any]]) -> None:
        """
        Carry each device's last WAN and storage data over a failed legacy fetch.

        WAN links and storage mounts both only come from the legacy device call,
        so one failed call would otherwise drop them until the next poll.
        Like VPN connections, the reuse is bounded so a call that keeps
        failing cannot hold a stale state forever.
        """
        failures = self._legacy_wan_failures.get(site_id, 0) + 1
        self._legacy_wan_failures[site_id] = failures
        if failures > MAX_STATS_REUSE_POLLS:
            return
        previous_devices = self.data["devices"].get(site_id, {})
        for device in devices:
            previous = previous_devices.get(device.get("id", ""))
            if not isinstance(previous, dict):
                continue
            for field in ("wans", "storage_mounts"):
                value = previous.get(field)
                if isinstance(value, list):
                    device[field] = value

    async def _fetch_vpn_connections(
        self, site_id: str, legacy_site_name: str | None
    ) -> dict[str, dict[str, Any]] | None:
        """
        Return the site's live VPN connections keyed by network_id.

        Returns None when the state cannot be read (call failed, or no classic
        site name to ask with), so the caller can tell that apart from a site
        with no connected VPNs ({}). This only feeds optional entities, so a
        failure must never fail the device refresh - auth problems surface
        through _process_site.
        """
        if legacy_site_name is None:
            return None
        try:
            connections = await self.network_client.vpn_clients.list_vpn_connections(
                legacy_site_name
            )
        except Exception as err:
            _LOGGER.debug(
                "Device coordinator: VPN connections unavailable for site %s: %s",
                site_id,
                err,
            )
            return None
        return {connection["network_id"]: connection for connection in connections}

    async def _process_device(
        self,
        site_id: str,
        device_dict: dict[str, Any],
        clients: list[dict[str, Any]],
        legacy_site_name: str | None = None,
        legacy_device: dict[str, Any] | None = None,
        *,
        clients_by_uplink: dict[str, list[dict[str, Any]]] | None = None,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """
        Process a single device and its stats.

        If clients_by_uplink is provided, clients for this device are looked up
        directly; otherwise falls back to scanning the clients list.
        """
        device_id: str = device_dict.get("id", "")
        device_name = device_dict.get("name", device_id)

        try:
            # Get device statistics. A device keyed on its MAC (the API sent
            # no id) cannot be looked up by id, so only legacy metrics apply.
            stats: dict[str, Any] = {}
            if not device_id_is_mac(device_dict):
                stats_model = await self.network_client.devices.get_statistics(
                    site_id, device_id=device_id
                )
                stats = self._model_to_dict(stats_model) if stats_model else {}

            # Use the library client for legacy per-port PoE/byte metrics & system stats
            try:
                device_mac = (
                    device_dict.get("mac")
                    or device_dict.get("macAddress")
                    or device_dict.get("mac_address")
                )

                site_name = legacy_site_name or site_id
                if not legacy_site_name:
                    try:
                        site_obj = self.config_coordinator.get_site(site_id)
                        if isinstance(site_obj, dict):
                            site_name = (
                                site_obj.get("internalReference")
                                or site_obj.get("internal_reference")
                                or site_name
                            )
                    except Exception as err:
                        _LOGGER.debug(
                            "Device coordinator: Unable to resolve internal "
                            "site reference for %s: %s",
                            site_id,
                            err,
                        )

                if device_mac and site_name:
                    metrics = await self.network_client.devices.get_port_metrics(
                        site_name, device_mac
                    )

                    if metrics.port_bytes:
                        stats["port_bytes"] = {
                            idx: {"rx_bytes": pb.rx_bytes, "tx_bytes": pb.tx_bytes}
                            for idx, pb in metrics.port_bytes.items()
                        }

                    if metrics.poe_ports:
                        stats["poe_ports"] = dict(metrics.poe_ports)

                    if metrics.poe_total_w is not None:
                        stats["poe_total_w"] = metrics.poe_total_w

                    _fill_missing_stat(
                        stats,
                        "cpu_utilization_pct",
                        CPU_STAT_KEYS,
                        metrics.cpu_utilization_pct,
                        float,
                    )
                    _fill_missing_stat(
                        stats,
                        "memory_utilization_pct",
                        MEMORY_STAT_KEYS,
                        metrics.memory_utilization_pct,
                        float,
                    )
                    _fill_missing_stat(
                        stats,
                        "uptime_sec",
                        UPTIME_STAT_KEYS,
                        metrics.uptime_sec,
                        int,
                    )
            except Exception as err:
                _LOGGER.debug(
                    "Legacy PoE/metrics fetch failed: %s",
                    err,
                )

            if isinstance(legacy_device, dict):
                _merge_legacy_system_stats(stats, legacy_device)

            # Add client data to stats
            if stats:
                if clients_by_uplink is not None:
                    stats["clients"] = clients_by_uplink.get(device_id, [])
                else:
                    # Use camelCase uplinkDeviceId as returned with aliases
                    stats["clients"] = [
                        c
                        for c in clients
                        if (c.get("uplinkDeviceId") or c.get("uplink_device_id"))
                        == device_id
                    ]
                stats["id"] = device_id

            self._stats_failures.pop(device_id, None)
            return device_id, device_dict, stats

        except Exception as err:
            if self._is_unsupported_response(err):
                self._stats_failures.pop(device_id, None)
                _LOGGER.debug(
                    "Statistics not available for device %s (%s): %s",
                    device_name,
                    device_id,
                    err,
                )
                return device_id, device_dict, {}
            if isinstance(err, UniFiAuthenticationError):
                # Revoked credentials: fail the refresh so reauth starts.
                raise
            # One device's statistics timing out, erroring or being rate
            # limited should not fail every entity on every site, and blanking
            # them would make its sensors drop to unknown. Reuse its last good
            # statistics for a few polls; a persistent failure then drops them
            # so frozen values don't stay "available" indefinitely.
            failures = self._stats_failures.get(device_id, 0) + 1
            self._stats_failures[device_id] = failures
            previous = self.data["stats"].get(site_id, {}).get(device_id)
            if failures <= MAX_STATS_REUSE_POLLS and isinstance(previous, dict):
                _LOGGER.debug(
                    "Error getting stats for device %s (%s), keeping last known "
                    "stats: %s",
                    device_name,
                    device_id,
                    err,
                )
                self._reused_stats.add(device_id)
                return device_id, device_dict, previous
            log = (
                _LOGGER.warning
                if failures == MAX_STATS_REUSE_POLLS + 1
                else _LOGGER.debug
            )
            log(
                "Statistics for device %s (%s) failed %d times in a row: %s",
                device_name,
                device_id,
                failures,
                err,
            )
            return device_id, device_dict, {}

    async def _process_site(
        self, site_id: str, legacy_site_name: str | None = None
    ) -> SiteProcessResult:
        """
        Process a single site's devices and clients.

        Errors propagate: a site whose devices or clients cannot be fetched
        fails the whole refresh (see ``_async_update_data``).

        The v1 devices endpoint can return HTTP 500 on controllers with device
        models the API cannot serialize (e.g. USW Pro XG family). When that
        happens and the site's legacy (classic) name is known, fall back to
        the legacy ``/stat/device`` endpoint, which serves every device, and
        map its payload into v1-shaped dicts so downstream code is unchanged.
        """
        # Get devices and clients in parallel using new API. Results are
        # inspected individually so a clients.get_all() failure cannot trigger
        # the device fallback when devices.get_all() succeeded.
        devices_task = self.network_client.devices.get_all(site_id)
        clients_task = self.network_client.clients.get_all(site_id)

        devices_result, clients_result = await asyncio.gather(
            devices_task, clients_task, return_exceptions=True
        )

        v1_devices_error: UniFiResponseError | None = None
        devices_models: list[Any] = []
        clients_models: list[Any] = []

        if isinstance(devices_result, BaseException):
            if (
                isinstance(devices_result, UniFiResponseError)
                and devices_result.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
                and legacy_site_name is not None
            ):
                _LOGGER.debug(
                    "Device coordinator: v1 devices endpoint returned %s for "
                    "site %s; falling back to legacy /stat/device",
                    devices_result.status_code,
                    site_id,
                )
                v1_devices_error = devices_result
            else:
                raise devices_result
        else:
            devices_models = devices_result

        if isinstance(clients_result, BaseException):
            if v1_devices_error is None:
                raise clients_result
            # The clients endpoint is independent of the devices endpoint, so
            # its 5xx is likely transient: retry once. A failed retry
            # propagates and fails the whole refresh, keeping the previous
            # site data instead of writing empty client data.
            _LOGGER.debug(
                "Device coordinator: Clients fetch also failed for site %s "
                "after v1 devices failure; retrying clients",
                site_id,
            )
            clients_models = await self.network_client.clients.get_all(site_id)
        else:
            clients_models = clients_result

        legacy_devices: list[dict[str, Any]] = []
        legacy_as_primary = False
        legacy_failed = False
        if legacy_site_name is not None:
            try:
                legacy_devices = (
                    await self.network_client.devices.get_legacy_site_devices(
                        legacy_site_name
                    )
                )
            except Exception as err:
                _LOGGER.debug(
                    "Device coordinator: Failed to fetch legacy device data "
                    "for site %s (%s): %s",
                    site_id,
                    legacy_site_name,
                    err,
                )
                if v1_devices_error is not None:
                    # Both v1 and legacy failed. Re-raise the v1 error so the
                    # refresh fails and the previous device state is kept,
                    # rather than wiping the device registry with empty data.
                    raise v1_devices_error from err
                legacy_failed = True

        # When v1 devices failed with 5xx, use legacy devices as the primary
        # device list, mapped to v1-shaped dicts.
        if v1_devices_error is not None:
            if not legacy_devices:
                # The legacy endpoint served nothing useful. It cannot be
                # distinguished from an empty site, so fail the refresh and
                # keep the previous device state rather than wiping it.
                raise v1_devices_error
            devices = [
                self._legacy_device_to_v1_dict(legacy_device)
                for legacy_device in legacy_devices
                if isinstance(legacy_device, dict)
            ]
            site_aliases = self._site_aliases.get(site_id, {})
            for device in devices:
                mac = self._normalize_mac(device.get("macAddress") or device.get("mac"))
                if mac and mac in site_aliases:
                    device["id"] = site_aliases[mac]

            legacy_as_primary = True
            source: Literal["v1", "legacy"] = "legacy"
            log = (
                _LOGGER.debug
                if site_id in self._legacy_fallback_sites
                else _LOGGER.info
            )
            self._legacy_fallback_sites.add(site_id)
            log(
                "Device coordinator: Using %d legacy devices as primary "
                "source for site %s (v1 endpoint unavailable)",
                len(devices),
                site_id,
            )
        else:
            # Convert model objects to dictionaries
            devices = [self._model_to_dict(d) for d in devices_models]
            source = "v1"
            if site_id in self._legacy_fallback_sites:
                self._legacy_fallback_sites.remove(site_id)
                _LOGGER.info(
                    "Device coordinator: v1 devices endpoint recovered for site %s",
                    site_id,
                )

        clients = [self._model_to_dict(c) for c in clients_models]

        device_macs: dict[str, str] = {}
        for device in devices:
            dev_id = device.get("id")
            mac = self._normalize_mac(device.get("macAddress") or device.get("mac"))
            if dev_id and mac:
                device_macs[str(dev_id)] = mac

        legacy_devices_by_mac = {
            normalized_mac: legacy_device
            for legacy_device in legacy_devices
            if isinstance(legacy_device, dict)
            and (
                normalized_mac := self._normalize_mac(
                    legacy_device.get("mac") or legacy_device.get("macAddress")
                )
            )
            is not None
        }

        # Capture the controller's untouched outlet arrays before
        # ``_merge_legacy_outlet_data`` replaces ``outlet_table`` with the
        # normalizer's model dump. The display record keeps that model; the
        # payload rule judges only these raw values.
        raw_outlet_arrays: dict[str, dict[str, Any]] = {}
        for device in devices:
            raw_device_id = device.get("id")
            if raw_device_id is None:
                continue
            raw_source = self._legacy_device_for(device, legacy_devices_by_mac)
            raw_outlet_arrays[str(raw_device_id)] = _raw_outlet_arrays(
                raw_source if raw_source is not None else device
            )

        if legacy_devices_by_mac:
            for device in devices:
                if not legacy_as_primary:
                    self._merge_legacy_temperature_data(device, legacy_devices_by_mac)
                    self._merge_legacy_port_data(device, legacy_devices_by_mac)
                    self._merge_legacy_outlet_data(device, legacy_devices_by_mac)
                    # When legacy is primary, _legacy_device_to_v1_dict()
                    # already embeds the topology block directly.
                    self._merge_legacy_uplink_data(device, legacy_devices_by_mac)
                # The legacy-to-v1 mapping does not carry WAN links and storage, so they
                # are merged whichever source is primary.
                self._merge_legacy_wan_data(device, legacy_devices_by_mac)
                self._merge_legacy_storage_data(device, legacy_devices_by_mac)

        if legacy_failed:
            self._reuse_previous_wans(site_id, devices)
        else:
            self._legacy_wan_failures.pop(site_id, None)

        legacy_macs: LegacyMacs = None
        if legacy_site_name is not None:
            # An empty legacy answer next to a non-empty v1 one is not
            # evidence that a device is gone, just as it is not a usable
            # fallback list above.
            legacy_macs = (
                frozenset(legacy_devices_by_mac)
                if legacy_devices_by_mac
                else LEGACY_UNVERIFIED
            )

        _LOGGER.debug(
            "Device coordinator: Site %s - Found %d devices and %d clients",
            site_id,
            len(devices),
            len(clients),
        )

        # Log sample device keys for debugging data format issues
        if devices:
            sample_device = devices[0]
            _LOGGER.debug(
                "Device coordinator: Sample device keys for site %s: %s",
                site_id,
                list(sample_device.keys()),
            )

        # Group clients by uplink device in a single pass
        clients_by_uplink: dict[str, list[dict[str, Any]]] = {}
        for client in clients:
            uplink_id = client.get("uplinkDeviceId") or client.get("uplink_device_id")
            if isinstance(uplink_id, str) and uplink_id:
                clients_by_uplink.setdefault(uplink_id, []).append(client)

        # Process devices in parallel (get stats)
        tasks = [
            self._process_device(
                site_id,
                device,
                clients,
                legacy_site_name=legacy_site_name,
                legacy_device=self._legacy_device_for(device, legacy_devices_by_mac),
                clients_by_uplink=clients_by_uplink,
            )
            for device in devices
        ]
        results = await asyncio.gather(*tasks)

        # Organize results
        devices_dict = {}
        stats_dict = {}
        for device_id, device, stats in results:
            if device_id:
                devices_dict[device_id] = device
                stats_dict[device_id] = stats

        clients_dict: dict[str, dict[str, Any]] = {
            str(client.get("id")): client for client in clients if client.get("id")
        }

        return SiteProcessResult(
            devices=devices_dict,
            stats=stats_dict,
            clients=clients_dict,
            source=source,
            device_macs=device_macs,
            raw_outlet_arrays=raw_outlet_arrays,
            legacy_macs=legacy_macs,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch device data from API."""
        try:
            # Get site IDs from config coordinator
            site_ids = self.config_coordinator.get_site_ids()

            # Drop data for sites no longer polled so their entities stop
            # reporting last-known values. Site-level fetch failures below
            # still keep a polled site's previous data.
            # The facts map is replaced like the others, so an empty site list
            # also leaves it empty. The maps are new and stay local until the
            # single publish below: the facade passes the published maps on
            # by reference, so writing into them would change snapshots it
            # has already handed out.
            sections: dict[str, dict[str, Any]] = {
                key: {
                    site_id: value
                    for site_id, value in self.data[key].items()
                    if site_id in site_ids
                }
                for key in (
                    "devices",
                    "stats",
                    "clients",
                    "vpn_connections",
                    NETWORK_FACTS_KEY,
                )
            }
            self._vpn_connection_failures = {
                site_id: count
                for site_id, count in self._vpn_connection_failures.items()
                if site_id in site_ids
            }
            self._legacy_wan_failures = {
                site_id: count
                for site_id, count in self._legacy_wan_failures.items()
                if site_id in site_ids
            }

            if not site_ids:
                # Deliberately no stale-device cleanup here: an empty site
                # list is also what a transient Network API failure looks
                # like, and purging the registry on it would lose devices.
                _LOGGER.debug(
                    "Device coordinator: No sites available from config coordinator"
                )
                self._available = True
                self._pending_outlet_writes = {}
                self._outlet_payload_provenance = {}
                self.data = {
                    **self.data,
                    **sections,
                    "last_update": datetime.now(tz=UTC),
                }
                return self.data

            _LOGGER.debug(
                "Device coordinator: Processing %d sites",
                len(site_ids),
            )

            self._reused_stats = set()
            # The config coordinator's published mapping (ADR-010), not a
            # classic site lookup of our own: last-good during a lookup
            # outage, pruned to the selected sites.
            legacy_site_names = self.config_coordinator.legacy_site_names

            # Process all sites in parallel
            tasks = [
                self._process_site(site_id, legacy_site_names.get(site_id))
                for site_id in site_ids
            ]
            vpn_tasks = [
                self._fetch_vpn_connections(site_id, legacy_site_names.get(site_id))
                for site_id in site_ids
            ]
            results, vpn_results = await asyncio.gather(
                asyncio.gather(*tasks, return_exceptions=True),
                asyncio.gather(*vpn_tasks),
            )

            # A site that could not be refreshed fails the whole update, and
            # nothing is written until every site has succeeded. Reporting
            # success here used to leave entities "available" on data that
            # was silently going stale, and swallowed a revoked API key so
            # reauth never started. DataUpdateCoordinator keeps the previous
            # self.data when the update raises.
            failures = [
                (site_id, result)
                for site_id, result in zip(site_ids, results, strict=True)
                if isinstance(result, BaseException)
            ]
            if failures:
                for failed_site_id, site_error in failures:
                    _LOGGER.debug(
                        "Device coordinator: Refresh failed for site %s: %r",
                        failed_site_id,
                        site_error,
                    )
                # Revoked credentials (401) on any site start reauth. A 403
                # is not sent to reauth: the flow re-validates against the
                # sites endpoint, which the same key passes, so it would loop.
                auth_failure = next(
                    (
                        site_error
                        for _, site_error in failures
                        if isinstance(site_error, UniFiAuthenticationError)
                        and site_error.status_code != HTTPStatus.FORBIDDEN
                    ),
                    None,
                )
                if auth_failure is not None:
                    raise auth_failure
                failed_site_id, first_error = failures[0]
                if isinstance(first_error, UniFiAuthenticationError):
                    self._available = False
                    msg = f"Access forbidden for site {failed_site_id}: {first_error}"
                    raise UpdateFailed(msg) from first_error
                raise first_error

            # Every site succeeded; the filter only narrows the type.
            site_results = [
                result for result in results if not isinstance(result, BaseException)
            ]

            site_sources: dict[str, str] = {}
            site_device_macs: dict[str, dict[str, str]] = {}
            site_legacy_macs: dict[str, LegacyMacs] = {}
            # Pending outlet writes of sites and devices no longer polled are
            # dropped here; a failed refresh never gets this far.
            pending_outlet_writes: dict[PendingOutletKey, PendingOutletWrite] = {}
            # Rebuilt from this poll only, so a device or site that is no
            # longer polled loses its provenance (a reappearing device starts
            # cold). A failed refresh never gets here.
            outlet_payload_provenance: dict[
                OutletProvenanceKey, OutletPayloadProvenance
            ] = {}
            now = time.monotonic()

            # Put the results into the local section maps
            for site_id, (
                polled_devices,
                stats_dict,
                clients_dict,
                source,
                dev_macs,
                raw_outlet_arrays,
                legacy_macs,
            ), vpn in zip(site_ids, site_results, vpn_results, strict=True):
                # Judge the raw controller record before the pending overlay
                # changes it: a saved entry must never establish provenance.
                for device_id, polled_device in polled_devices.items():
                    outlet_payload_provenance[(site_id, device_id)] = (
                        evaluate_outlet_payload_provenance(
                            self._outlet_payload_provenance.get((site_id, device_id)),
                            site_id,
                            device_id,
                            source,
                            device=polled_device,
                            raw_outlet_arrays=raw_outlet_arrays.get(device_id, {}),
                        )
                    )
                devices_dict, held_writes = reconcile_pending_outlet_writes(
                    site_id, polled_devices, self._pending_outlet_writes, now
                )
                pending_outlet_writes.update(held_writes)
                sections["devices"][site_id] = devices_dict
                sections["stats"][site_id] = stats_dict
                sections["clients"][site_id] = clients_dict
                site_sources[site_id] = source
                site_device_macs[site_id] = dev_macs
                site_legacy_macs[site_id] = legacy_macs
                connections = self._vpn_connections_or_previous(site_id, vpn)
                if connections is None:
                    sections["vpn_connections"].pop(site_id, None)
                else:
                    sections["vpn_connections"][site_id] = connections

                _LOGGER.debug(
                    "Device coordinator: Processed site %s - %d devices, %d clients",
                    site_id,
                    len(devices_dict),
                    len(clients_dict),
                )

            # Compute per-port byte rates from deltas
            sections["stats"] = self._compute_port_rates(sections["stats"])

            # Build the read model from the final, enriched maps. It is a
            # new map each refresh: WebSocket change detection compares by
            # identity. A failed refresh never gets here, so the last
            # committed facts stay with the last committed devices and stats.
            sections[NETWORK_FACTS_KEY] = {
                site_id: build_site_network_facts(
                    sections["devices"].get(site_id),
                    sections["stats"].get(site_id),
                )
                for site_id in site_ids
            }

            # Forget failure counts for devices that are no longer reported.
            current_device_ids = {
                device_id
                for site_stats in sections["stats"].values()
                for device_id in site_stats
            }
            self._stats_failures = {
                device_id: count
                for device_id, count in self._stats_failures.items()
                if device_id in current_device_ids
            }

            self._available = True
            self._pending_outlet_writes = pending_outlet_writes
            self._outlet_payload_provenance = outlet_payload_provenance
            # Publish once, after the last await, as a new root: a snapshot
            # taken during the refresh keeps the previous maps unchanged.
            self.data = {
                **self.data,
                **sections,
                "last_update": datetime.now(tz=UTC),
            }

            # Clean up stale devices (Gold requirement). The legacy
            # cross-check reuses the legacy device list each site fetched in
            # this refresh, so cleanup needs no request of its own and stays
            # after the publish without an await.
            self._cleanup_stale_devices(
                site_sources, site_device_macs, site_legacy_macs
            )

            _LOGGER.debug(
                "Device coordinator: Update complete - %d sites processed",
                len(site_ids),
            )

            return self.data

        except UniFiAuthenticationError as err:
            self._handle_auth_error(err)
        except UniFiConnectionError as err:
            self._handle_connection_error(err)
        except UniFiTimeoutError as err:
            self._handle_timeout_error(err)
        except UniFiResponseError as err:
            self._handle_response_error(err)
        except UpdateFailed:
            # Already classified above (a site 403); the generic handler would
            # log it as an unexpected error with a traceback on every poll.
            raise
        except Exception as err:
            self._handle_generic_error(err)

        # Should never reach here due to raises above
        return self.data  # pragma: no cover

    def _compute_port_rates(
        self, stats_by_site: Mapping[str, Mapping[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """
        Return ``stats_by_site`` with per-port byte rates from poll deltas.

        Each device keeps its own last sample time. A device whose statistics
        were reused this poll (see ``_process_device``) is skipped and its
        previous sample carried forward: recomputing against the same
        counters would report a rate of 0, and the next fresh poll would then
        divide two polls' worth of bytes by one poll's elapsed time. Its
        reused stats keep the port_rates from the last real sample.

        The result is new maps: a record that gains ``port_rates`` is
        replaced by a copy, and no input map or record is written to.
        """
        now = time.monotonic()
        previous = self._prev_port_bytes
        current: dict[str, tuple[float, dict[int, dict[str, int]]]] = {}
        result: dict[str, dict[str, Any]] = {}

        for site_id, stats_dict in stats_by_site.items():
            site_stats = result[site_id] = dict(stats_dict)
            for device_id, stats in stats_dict.items():
                if not isinstance(stats, dict):
                    continue

                if device_id in self._reused_stats:
                    if device_id in previous:
                        current[device_id] = previous[device_id]
                    continue

                curr_dev = stats.get("port_bytes")
                if not isinstance(curr_dev, dict):
                    continue
                current[device_id] = (now, curr_dev)

                if device_id not in previous:
                    continue
                prev_time, prev_dev = previous[device_id]
                elapsed = now - prev_time
                if elapsed <= 0:
                    continue

                port_rates: dict[int, dict[str, float]] = {}
                for port_idx, curr in curr_dev.items():
                    prev = prev_dev.get(port_idx)
                    if prev is None:
                        continue

                    tx_delta = curr.get("tx_bytes", 0) - prev.get("tx_bytes", 0)
                    rx_delta = curr.get("rx_bytes", 0) - prev.get("rx_bytes", 0)

                    # Skip negative deltas (counter reset)
                    if tx_delta < 0 or rx_delta < 0:
                        continue

                    port_rates[port_idx] = {
                        "tx_bytes_rate": round(tx_delta / elapsed, 1),
                        "rx_bytes_rate": round(rx_delta / elapsed, 1),
                    }

                if port_rates:
                    site_stats[device_id] = {**stats, "port_rates": port_rates}

        # Update stored state for next poll
        self._prev_port_bytes = current
        return result

    def _cleanup_stale_devices(
        self,
        site_sources: dict[str, str],
        site_device_macs: dict[str, dict[str, str]],
        site_legacy_macs: Mapping[str, LegacyMacs] | None = None,
    ) -> None:
        """
        Remove stale network devices from the device registry (Gold requirement).

        Network staleness policy:
        - Evaluated per site against that site's previous snapshot.
        - A site is eligible for cleanup only if:
          1. Its source ("v1" | "legacy") matches its previous committed source
             (prevents purges during transient v1/legacy flips or on first poll).
          2. It did not return zero devices after a non-empty previous snapshot
             (prevents purges during transient empty responses).
        - Ineligible sites keep previous tracking and aliases unchanged and do not
          advance counters.
        - MAC guard: an identifier missing by ID is retained without advancing absence
          counters if its normalized MAC is present in the current site snapshot under
          any ID (prevents purges when an identifier changes representation).
        - Legacy cross-check (v1 polls only): a device the v1 list omits but the
          same poll's legacy list still has (by normalized MAC) is held: its
          counter neither advances nor resets, as on an ineligible poll. When a
          counter would advance but the legacy list is unverified (the call
          failed or returned no devices), the whole site is ineligible for that
          poll. A site without a classic name has no legacy list to check, so
          the v1 result alone decides.
        - Truly missing devices must be absent for MAX_CONSECUTIVE_MISSING_POLLS
          consecutive eligible polls before being detached via _remove_stale_device.
        - Reappearance resets the device's absence counter.
        - Aliases are updated on every committed v1 poll (merging new MAC->v1-ID
          pairs), preserved across fallback polls and transient ineligible polls,
          and pruned only when a device is detached as stale or when a site is pruned.
        """
        current_devices_by_site: dict[str, dict[str, Any]] = self.data.get(
            "devices", {}
        )

        polled_site_ids = set(site_sources.keys())

        new_tracked_devices: dict[str, dict[str, str]] = {}
        new_absence_counts: dict[str, dict[str, int]] = {}
        new_site_sources: dict[str, str] = {}
        new_site_aliases: dict[str, dict[str, str]] = {}

        for site_id in polled_site_ids:
            current_source = site_sources[site_id]
            previous_source = self._site_sources.get(site_id)
            previous_tracked = self._site_tracked_devices.get(site_id, {})
            current_tracked = site_device_macs.get(site_id, {})
            current_site_devs = current_devices_by_site.get(site_id, {})

            # Aliases are updated on every committed v1 poll, even if the poll
            # is ineligible for cleanup due to a source transition.
            site_aliases = dict(self._site_aliases.get(site_id, {}))
            if current_source == "v1":
                site_aliases.update(
                    {mac: dev_id for dev_id, mac in current_tracked.items()}
                )

            current_site_macs = set(current_tracked.values())
            missing = {
                dev_id: dev_mac
                for dev_id, dev_mac in previous_tracked.items()
                if dev_id not in current_site_devs and dev_mac not in current_site_macs
            }
            # The legacy list only checks a v1 poll; on a fallback poll it is
            # the device list itself.
            legacy_macs = (
                (site_legacy_macs or {}).get(site_id)
                if current_source == "v1"
                else None
            )

            # 1. Eligibility check
            source_trustworthy = (
                previous_source is not None
                and previous_source == current_source
                and not (len(current_site_devs) == 0 and len(previous_tracked) > 0)
            )
            legacy_unverified = bool(missing) and legacy_macs == LEGACY_UNVERIFIED
            is_eligible = source_trustworthy and not legacy_unverified

            new_site_sources[site_id] = current_source

            if not is_eligible:
                _LOGGER.debug(
                    "Device coordinator: Site %s is not eligible for stale "
                    "device cleanup (source=%s, prev_source=%s, devices=%d, "
                    "prev_tracked=%d, legacy_verified=%s); retaining tracking",
                    site_id,
                    current_source,
                    previous_source,
                    len(current_site_devs),
                    len(previous_tracked),
                    legacy_macs != LEGACY_UNVERIFIED,
                )
                if previous_source is None:
                    new_tracked_devices[site_id] = dict(current_tracked)
                elif source_trustworthy:
                    # Only the legacy cross-check is unverifiable: the v1 poll
                    # is good, so a device it reports for the first time is
                    # tracked too; missing devices stay tracked until legacy
                    # can confirm them.
                    new_tracked_devices[site_id] = {
                        **previous_tracked,
                        **current_tracked,
                    }
                else:
                    new_tracked_devices[site_id] = previous_tracked
                kept_counts = dict(self._site_absence_counts.get(site_id, {}))
                if source_trustworthy:
                    # Only the legacy cross-check is unverifiable: the v1 poll
                    # itself is good, so a device it lists (by ID or MAC) is
                    # back and its count starts over. Missing devices keep
                    # theirs until the legacy list can confirm them.
                    for dev_id in list(kept_counts):
                        if dev_id not in missing:
                            kept_counts.pop(dev_id)
                new_absence_counts[site_id] = kept_counts
                new_site_aliases[site_id] = site_aliases
                continue

            # Eligible: perform stale device evaluation
            site_counters = dict(self._site_absence_counts.get(site_id, {}))
            held: set[str] = set()

            for dev_id, dev_mac in previous_tracked.items():
                if dev_id in current_site_devs:
                    site_counters.pop(dev_id, None)
                elif dev_id not in missing:
                    _LOGGER.debug(
                        "Device coordinator: Device %s absent by ID but MAC "
                        "present in site %s; retaining",
                        f"{site_id}_{dev_id}",
                        site_id,
                    )
                    site_counters.pop(dev_id, None)
                elif isinstance(legacy_macs, frozenset) and dev_mac in legacy_macs:
                    _LOGGER.debug(
                        "Device coordinator: Device %s absent from v1 but "
                        "present in the legacy device list; holding",
                        f"{site_id}_{dev_id}",
                    )
                    held.add(dev_id)
                else:
                    count = site_counters.get(dev_id, 0) + 1
                    site_counters[dev_id] = count
                    if count >= MAX_CONSECUTIVE_MISSING_POLLS:
                        self._remove_stale_device(f"{site_id}_{dev_id}")
                        site_counters.pop(dev_id, None)
                        site_aliases.pop(dev_mac, None)
                    else:
                        _LOGGER.debug(
                            "Device coordinator: Device %s absent for %d/%d polls; "
                            "not removing yet",
                            f"{site_id}_{dev_id}",
                            count,
                            MAX_CONSECUTIVE_MISSING_POLLS,
                        )

            tracked_for_site: dict[str, str] = {
                **current_tracked,
                **{
                    dev_id: dev_mac
                    for dev_id, dev_mac in previous_tracked.items()
                    if dev_id not in current_site_devs
                    and (dev_id in site_counters or dev_id in held)
                },
            }

            new_tracked_devices[site_id] = tracked_for_site
            new_absence_counts[site_id] = site_counters
            new_site_aliases[site_id] = site_aliases

        # Prune all per-site state to the sites polled this refresh
        self._site_sources = new_site_sources
        self._site_aliases = new_site_aliases
        self._site_tracked_devices = new_tracked_devices
        self._site_absence_counts = new_absence_counts

    def get_device(self, site_id: str, device_id: str) -> dict[str, Any] | None:
        """Get device data by site ID and device ID."""
        devices = self.data.get("devices", {}).get(site_id, {})
        result = devices.get(device_id)
        return result if isinstance(result, dict) else None

    def get_device_stats(self, site_id: str, device_id: str) -> dict[str, Any] | None:
        """Get device statistics by site ID and device ID."""
        stats = self.data.get("stats", {}).get(site_id, {})
        result = stats.get(device_id)
        return result if isinstance(result, dict) else None

    def get_clients(self, site_id: str) -> dict[str, Any]:
        """Get all clients for a site."""
        result: dict[str, Any] = self.data.get("clients", {}).get(site_id, {})
        return result

    async def async_restart_device(self, site_id: str, device_id: str) -> bool:
        """Restart a network device; a falsy result raises ``restart_rejected``."""
        return await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                translation_placeholders={"device_id": device_id},
                message=f"Unable to restart device {device_id}",
                rejected_translation_key="restart_rejected",
            ),
            self.network_client.devices.restart,
            site_id,
            device_id,
        )

    async def async_power_cycle_port(
        self, site_id: str, device_id: str, port_idx: int
    ) -> bool:
        """Power cycle a PoE port, after the shared eligibility check."""
        require_power_cyclable_port(self.data, site_id, device_id, port_idx)
        return await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to power-cycle port {port_idx} on device {device_id}",
            ),
            self.network_client.devices.execute_port_action,
            site_id,
            device_id,
            port_idx,
            "POWER_CYCLE",
        )

    async def async_set_outlet_state(
        self,
        site_id: str,
        device_id: str,
        outlet_index: int,
        *,
        state: bool,
        cycle_enabled: bool | None = None,
    ) -> bool:
        """
        Set outlet relay state and/or cycle_enabled on a PDU device.

        The classic call is addressed by the device's classic ``_id`` and
        carries its current ``outlet_overrides``. After success the written
        fields and the complete ``outlet_overrides`` array that was sent are
        published as a copy-on-write overlay and a deferred refresh is
        requested. The intended field (``cycle_enabled`` for a cycle write,
        whose ``state`` only repeats the cached relay state, otherwise
        ``relay_state``) is recorded as pending, so polls that still report
        the controller's older value keep the written one (#376).

        Before transport, inside the same lock, the built payload must pass
        the payload-integrity gate: a trusted provenance record from the raw
        poll, a matching device identity and exactly one row per required
        outlet with its required fields. Otherwise the command raises the
        translated ``command_failed`` error and sends no PUT, records nothing,
        publishes nothing and requests no refresh. Confirmation or the 60 s
        timeout of a pending field never restores write eligibility; only a
        later trusted poll does.

        The write holds the coordinator's refresh lock from reading the cached
        record to publishing (#371 review). A poll already in flight finishes
        first, so it cannot replace the published array with its older one,
        and two overlapping outlet commands run one after the other, each
        built from the array the previous one sent.
        """
        site_name = await self.config_coordinator.async_resolve_legacy_site_name(
            site_id, verified_only=False
        )
        async with self._debounced_refresh.async_lock():
            device_data = self.get_device(site_id, device_id) or {}
            sent = _build_outlet_overrides(
                device_data,
                outlet_index,
                state=state,
                cycle_enabled=cycle_enabled,
            )
            if not outlet_payload_is_safe(
                self._outlet_payload_provenance.get((site_id, device_id)),
                site_id=site_id,
                device_id=device_id,
                device=device_data,
                sent=sent,
                outlet_index=outlet_index,
            ):
                _LOGGER.debug(
                    "Device coordinator: Refusing to set outlet %s on device %s: "
                    "the controller data is not a trusted complete outlet baseline",
                    outlet_index,
                    device_id,
                )
                msg = f"Unable to set outlet {outlet_index} state on device {device_id}"
                raise HomeAssistantError(
                    msg,
                    translation_domain=DOMAIN,
                    translation_key="command_failed",
                )
            result = await async_execute_command(
                CommandFailure(
                    translation_key="command_failed",
                    message=(
                        f"Unable to set outlet {outlet_index} state "
                        f"on device {device_id}"
                    ),
                ),
                self.network_client.devices.set_outlet_state,
                site_name,
                device_data.get("_id") or device_id,
                outlet_index,
                state=state,
                cycle_enabled=cycle_enabled,
                current_device=device_data,
            )
            changes: dict[str, bool] = {"relay_state": state}
            if cycle_enabled is not None:
                changes["cycle_enabled"] = cycle_enabled
            self._publish_outlet(site_id, device_id, outlet_index, changes, sent)
            self._record_outlet_write(
                (site_id, device_id, outlet_index),
                {"relay_state": state}
                if cycle_enabled is None
                else {"cycle_enabled": cycle_enabled},
                sent,
            )
        await self.async_request_refresh()
        return result

    def _record_outlet_write(
        self,
        key: PendingOutletKey,
        written: dict[str, bool],
        sent: list[dict[str, Any]] | None,
    ) -> None:
        """Record a successful outlet write as pending, keeping earlier fields."""
        previous = self._pending_outlet_writes.get(key)
        outlet_index = key[2]
        self._pending_outlet_writes[key] = PendingOutletWrite(
            fields={**previous.fields, **written} if previous else written,
            override=next(
                (
                    dict(entry)
                    for entry in sent or ()
                    if _override_index(entry) == outlet_index
                ),
                None,
            ),
            written_at=time.monotonic(),
        )

    def _publish_outlet(
        self,
        site_id: str,
        device_id: str,
        outlet_index: int,
        changes: dict[str, bool],
        overrides: list[dict[str, Any]] | None,
    ) -> None:
        """
        Publish an outlet write as a copy-on-write overlay (ADR-010).

        The data root, ``devices``, the site map and the device record are
        replaced. The device's ``outlet_overrides`` becomes a new list of the
        sent entries, and when the written outlet is in ``outlet_table`` that
        list and the outlet are replaced too; the other outlets stay shared.
        Listeners are notified without ``async_set_updated_data()``. The
        caller holds the refresh lock, so no poll that started before the
        write can finish after this overlay. A poll that starts after it can
        still read the controller's older values for a few seconds while the
        PDU applies the change; ``reconcile_pending_outlet_writes`` keeps the
        written fields over those polls (#376). This overlay and the pending
        entry only decide displayed values: payload safety comes from
        ``OutletPayloadProvenance``, which a successful raw poll evaluates
        before this overlay. The checked payload is complete, and provenance
        stays trusted, so a second write before the next poll can proceed.
        """
        devices = self.data.get("devices", {})
        site_devices = devices.get(site_id)
        if not isinstance(site_devices, dict):
            return
        device = site_devices.get(device_id)
        if not isinstance(device, dict):
            return

        updated_device = dict(device)
        applied = False

        if overrides is not None:
            updated_device["outlet_overrides"] = overrides
            applied = True

        outlets = device.get("outlet_table")
        if isinstance(outlets, list):
            position = next(
                (i for i, o in enumerate(outlets) if _is_table_outlet(o, outlet_index)),
                None,
            )
            if position is not None:
                table = list(outlets)
                table[position] = {**outlets[position], **changes}
                updated_device["outlet_table"] = table
                applied = True

        if not applied:
            return

        self.data = {
            **self.data,
            "devices": {
                **devices,
                site_id: {**site_devices, device_id: updated_device},
            },
        }
        self.async_update_listeners()

    async def _async_client_target(
        self, site_id: str, client_id: str
    ) -> tuple[str, str]:
        """
        Resolve the classic site name and client MAC for a client action.

        The official Integration API has no block/unblock/reconnect/forget
        operations, so these are issued against the classic ``cmd/stamgr``
        endpoint, which is scoped by the classic site name and identifies the
        client by MAC address rather than the integration client UUID.
        """
        clients = self.data.get("clients", {}).get(site_id, {})
        client_data = clients.get(client_id, {}) if isinstance(clients, dict) else {}
        mac = (
            client_data.get("macAddress")
            or client_data.get("mac_address")
            or client_data.get("mac")
        )
        if not mac:
            msg = f"Unable to determine MAC address for client {client_id}"
            raise HomeAssistantError(
                msg,
                translation_domain=DOMAIN,
                translation_key="client_not_resolvable",
                translation_placeholders={"client_id": client_id},
            )
        site_name = await self.config_coordinator.async_resolve_legacy_site_name(
            site_id, verified_only=False
        )
        return site_name, mac

    async def _async_client_command(
        self,
        verb: str,
        site_id: str,
        client_id: str,
        action: Callable[[str, str], Awaitable[bool]],
    ) -> None:
        """Run one classic client command against a resolved site and MAC."""
        site_name, mac = await self._async_client_target(site_id, client_id)
        await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to {verb} client {client_id}",
            ),
            action,
            site_name,
            mac,
        )

    async def async_block_client(self, site_id: str, client_id: str) -> None:
        """Block a network client, then request a deferred refresh."""
        await self._async_client_command(
            "block", site_id, client_id, self.network_client.clients.block
        )
        await self.async_request_refresh()

    async def async_unblock_client(self, site_id: str, client_id: str) -> None:
        """Unblock a network client, then request a deferred refresh."""
        await self._async_client_command(
            "unblock", site_id, client_id, self.network_client.clients.unblock
        )
        await self.async_request_refresh()

    async def async_reconnect_client(self, site_id: str, client_id: str) -> None:
        """Reconnect a network client."""
        await self._async_client_command(
            "reconnect", site_id, client_id, self.network_client.clients.reconnect
        )

    async def async_forget_client(self, site_id: str, client_id: str) -> None:
        """Forget/remove a network client."""
        await self._async_client_command(
            "forget", site_id, client_id, self.network_client.clients.forget
        )

    async def async_forget_clients_batch(self, site_id: str, macs: list[str]) -> bool:
        """Forget/remove a list of client MACs for a site."""
        site_name = self.config_coordinator.resolve_legacy_site_name(
            site_id, verified_only=True
        )
        return await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                message=f"Unable to forget clients for site {site_id}",
            ),
            self.network_client.clients.forget_batch,
            site_name,
            macs,
        )

    async def _async_derive_wake_broadcast(self, mac: str) -> str | None:
        """
        Return the client's subnet directed broadcast address, or None if unknown.

        Never raises: any failure means "unknown", so the packet goes to the
        default 255.255.255.255. A connected client with an IP needs one request
        (the site's networks). Any other client also reads the classic history for
        its last network, which makes it two.
        """
        live = find_client_by_mac(self.data.get("clients"), mac)
        live_ip = (
            first_present(live[1], "ipAddress", "ip_address", "ip") if live else None
        )

        async def _query_site(site_id: str) -> str | None:
            try:
                site_name = self.config_coordinator.resolve_legacy_site_name(
                    site_id, verified_only=True
                )
                ip: str | None
                network_id: str | None
                if isinstance(live_ip, str) and live_ip:
                    networks = await self.network_client.networks.get_legacy_all(
                        site_name
                    )
                    ip, network_id = live_ip, None
                else:
                    networks, records = await asyncio.gather(
                        self.network_client.networks.get_legacy_all(site_name),
                        self.network_client.clients.get_historical_legacy(site_name),
                    )
                    ip, network_id = history_network_hint(records, mac)
                return derive_directed_broadcast(networks, ip=ip, network_id=network_id)
            except Exception:
                _LOGGER.debug("Wake-on-LAN: no broadcast address from site %s", site_id)
                return None

        if live and live[0]:
            return await _query_site(live[0])

        site_ids = self.config_coordinator.get_site_ids()
        results = await asyncio.gather(*(_query_site(s) for s in site_ids))
        return next((b for b in results if b is not None), None)

    async def async_wake_client(
        self, mac: str, *, client_name: str | None = None
    ) -> None:
        """
        Send a Wake-on-LAN magic packet to a client through Home Assistant core.

        UniFi has no wake action, so this calls ``wake_on_lan.send_magic_packet``
        (loaded through this integration's manifest dependencies). The packet goes
        to the client's subnet directed broadcast when UniFi data allows deriving
        it, otherwise to the default 255.255.255.255.
        """
        normalized = normalize_mac(mac)
        live = (
            find_client_by_mac(self.data.get("clients"), normalized)
            if normalized
            else None
        )
        display_name = (
            client_name
            or (first_non_blank_text(live[1], "name", "hostname") if live else None)
            or (f"Client {normalized}" if normalized else str(mac))
        )
        if normalized is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="wake_failed",
                translation_placeholders={
                    "client_name": display_name,
                    "error": "invalid MAC address",
                },
            )

        broadcast: str | None = None
        if self.config_coordinator.last_update_success and self.last_update_success:
            try:
                async with asyncio.timeout(WAKE_BROADCAST_TIMEOUT_SECONDS):
                    broadcast = await self._async_derive_wake_broadcast(normalized)
            except TimeoutError:
                _LOGGER.debug("Wake-on-LAN: broadcast address derivation timed out")
            except Exception:
                _LOGGER.debug("Wake-on-LAN: broadcast address derivation failed")

        service_data: dict[str, str] = {"mac": normalized}
        if broadcast is not None:
            service_data["broadcast_address"] = broadcast
        try:
            await self.hass.services.async_call(
                WAKE_ON_LAN_DOMAIN, WAKE_ON_LAN_SERVICE, service_data, blocking=True
            )
        except ServiceNotFound as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="wake_on_lan_unavailable",
            ) from err
        except HomeAssistantError:
            raise
        except (OSError, ValueError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="wake_failed",
                translation_placeholders={
                    "client_name": display_name,
                    "error": str(err),
                },
            ) from err
        _LOGGER.debug(
            "Sent Wake-on-LAN packet (directed broadcast: %s)", broadcast is not None
        )
