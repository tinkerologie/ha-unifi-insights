# Copyright 2026 UniFi Insights contributors
"""Reconcile Network device inventory from committed state and poll evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

from custom_components.unifi_insights.const import MAX_CONSECUTIVE_MISSING_POLLS

if TYPE_CHECKING:
    from collections.abc import Mapping

LEGACY_UNVERIFIED: Final = "unverified"
type LegacyMacs = frozenset[str] | Literal["unverified"] | None
type InventorySource = Literal["v1", "legacy"]


@dataclass(frozen=True, slots=True, kw_only=True)
class NetworkInventoryState:
    """Committed source, aliases and disappearance evidence, grouped by site."""

    sources: Mapping[str, InventorySource] = field(default_factory=dict)
    aliases: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    tracked: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    absence: Mapping[str, Mapping[str, int]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class NetworkInventoryEvidence:
    """Device IDs and MAC evidence from one successful site poll."""

    source: InventorySource
    published_ids: frozenset[str]
    device_macs: Mapping[str, str]
    legacy_macs: LegacyMacs = None


@dataclass(frozen=True, slots=True)
class RemoveNetworkDevice:
    """A confirmed registry retirement for the coordinator to apply."""

    site_id: str
    device_id: str


@dataclass(frozen=True, slots=True)
class NetworkInventoryStep:
    """The next committed inventory and its ordered registry removals."""

    state: NetworkInventoryState
    removals: tuple[RemoveNetworkDevice, ...]


def reconcile_inventory(
    state: NetworkInventoryState,
    evidence_by_site: Mapping[str, NetworkInventoryEvidence],
) -> NetworkInventoryStep:
    """Advance disappearance evidence using only eligible, corroborated polls."""
    sources: dict[str, InventorySource] = {}
    aliases: dict[str, Mapping[str, str]] = {}
    tracked: dict[str, Mapping[str, str]] = {}
    absence: dict[str, Mapping[str, int]] = {}
    removals: list[RemoveNetworkDevice] = []

    for site_id, evidence in evidence_by_site.items():
        source = evidence.source
        previous_source = state.sources.get(site_id)
        previous_tracked = state.tracked.get(site_id, {})
        current_tracked = evidence.device_macs
        published = evidence.published_ids
        site_aliases = dict(state.aliases.get(site_id, {}))
        counts = dict(state.absence.get(site_id, {}))
        legacy_macs = evidence.legacy_macs if source == "v1" else None

        # A v1 poll can establish aliases even when it cannot authorize removal.
        if source == "v1":
            site_aliases.update(
                {mac: device_id for device_id, mac in current_tracked.items()}
            )
        sources[site_id] = source
        aliases[site_id] = site_aliases
        absence[site_id] = counts

        current_macs = set(current_tracked.values())
        missing = {
            device_id
            for device_id, mac in previous_tracked.items()
            if device_id not in published and mac not in current_macs
        }
        trustworthy = previous_source == source and not (
            not published and previous_tracked
        )
        if not trustworthy:
            # A first poll establishes tracking. Source flips and suspiciously
            # empty polls keep the last tracking and counters.
            tracked[site_id] = (
                dict(current_tracked) if previous_source is None else previous_tracked
            )
            continue

        if missing and legacy_macs == LEGACY_UNVERIFIED:
            # v1 presence is still useful even when absence cannot be confirmed.
            tracked[site_id] = {**previous_tracked, **current_tracked}
            for device_id in counts.keys() - missing:
                del counts[device_id]
            continue

        held: set[str] = set()
        for device_id, mac in previous_tracked.items():
            if device_id not in missing:
                counts.pop(device_id, None)
            elif isinstance(legacy_macs, frozenset) and mac in legacy_macs:
                held.add(device_id)
            else:
                count = counts.get(device_id, 0) + 1
                if count >= MAX_CONSECUTIVE_MISSING_POLLS:
                    removals.append(RemoveNetworkDevice(site_id, device_id))
                    counts.pop(device_id, None)
                    site_aliases.pop(mac, None)
                else:
                    counts[device_id] = count

        tracked[site_id] = {
            **current_tracked,
            **{
                device_id: mac
                for device_id, mac in previous_tracked.items()
                if device_id not in published
                and (device_id in counts or device_id in held)
            },
        }

    return NetworkInventoryStep(
        NetworkInventoryState(
            sources=sources, aliases=aliases, tracked=tracked, absence=absence
        ),
        tuple(removals),
    )
