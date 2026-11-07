# Copyright 2026 UniFi Insights contributors
"""Network inventory policy exercised without a coordinator or registry."""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from custom_components.unifi_insights.coordinators import device_inventory
from custom_components.unifi_insights.coordinators.device_inventory import (
    LEGACY_UNVERIFIED,
    NetworkInventoryEvidence,
    NetworkInventoryState,
    RemoveNetworkDevice,
    reconcile_inventory,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.unifi_insights.coordinators.device_inventory import (
        InventorySource,
        LegacyMacs,
        NetworkInventoryStep,
    )

OLD_MAC = "aa:bb:cc:dd:ee:01"
STAY_MAC = "aa:bb:cc:dd:ee:02"
NEW_MAC = "aa:bb:cc:dd:ee:03"


def _poll(
    records: Mapping[str, str],
    *,
    source: InventorySource = "v1",
    legacy_macs: LegacyMacs = None,
    published_ids: frozenset[str] | None = None,
) -> NetworkInventoryEvidence:
    return NetworkInventoryEvidence(
        source=source,
        published_ids=frozenset(records) if published_ids is None else published_ids,
        device_macs=records,
        legacy_macs=legacy_macs,
    )


def _run(
    state: NetworkInventoryState, **sites: NetworkInventoryEvidence
) -> NetworkInventoryStep:
    before = deepcopy((state, sites))
    result = reconcile_inventory(state, sites)
    assert (state, sites) == before
    return result


def _seed(*, source: InventorySource = "v1") -> NetworkInventoryState:
    return _run(
        NetworkInventoryState(),
        site=_poll({"old": OLD_MAC, "stay": STAY_MAC}, source=source),
    ).state


def test_inventory_requires_three_eligible_absences() -> None:
    state = _seed()
    for count in (1, 2):
        step = _run(state, site=_poll({"stay": STAY_MAC}))
        assert step.removals == ()
        assert step.state.absence["site"] == {"old": count}
        state = step.state

    step = _run(state, site=_poll({"stay": STAY_MAC}))
    assert step.removals == (RemoveNetworkDevice("site", "old"),)
    assert step.state.tracked["site"] == {"stay": STAY_MAC}
    assert step.state.aliases["site"] == {STAY_MAC: "stay"}
    assert step.state.absence["site"] == {}


@pytest.mark.parametrize(
    ("before", "after"),
    [("v1", "legacy"), ("legacy", "v1")],
)
def test_source_change_holds_counts_and_retains_identity(
    before: InventorySource, after: InventorySource
) -> None:
    state = _run(
        _seed(source=before), site=_poll({"stay": STAY_MAC}, source=before)
    ).state
    step = _run(state, site=_poll({"new": NEW_MAC}, source=after))
    assert step.removals == ()
    assert step.state.sources == {"site": after}
    assert step.state.tracked == state.tracked
    assert step.state.absence == state.absence
    assert step.state.aliases == (
        {"site": {NEW_MAC: "new"}} if after == "v1" else state.aliases
    )


def test_empty_poll_holds_evidence() -> None:
    state = _run(_seed(), site=_poll({"stay": STAY_MAC})).state
    step = _run(state, site=_poll({}))
    assert step.state == state
    assert step.removals == ()


@pytest.mark.parametrize("return_id", ["old", "new_id"])
def test_presence_by_id_or_mac_resets_absence(return_id: str) -> None:
    state = _run(_seed(), site=_poll({"stay": STAY_MAC})).state
    step = _run(state, site=_poll({return_id: OLD_MAC, "stay": STAY_MAC}))
    assert step.state.absence["site"] == {}
    assert step.state.aliases["site"][OLD_MAC] == return_id
    assert step.removals == ()


def test_published_id_still_counts_as_presence_without_mac_evidence() -> None:
    state = _run(_seed(), site=_poll({"stay": STAY_MAC})).state
    step = _run(state, site=_poll({}, published_ids=frozenset({"old"})))
    assert step.state.absence["site"] == {"stay": 1}
    assert step.removals == ()


@pytest.mark.parametrize("legacy_macs", [LEGACY_UNVERIFIED, frozenset({OLD_MAC})])
def test_legacy_evidence_holds_missing_device(legacy_macs: LegacyMacs) -> None:
    state = _run(_seed(), site=_poll({"stay": STAY_MAC})).state
    step = _run(
        state, site=_poll({"stay": STAY_MAC, "new": NEW_MAC}, legacy_macs=legacy_macs)
    )
    assert step.removals == ()
    assert step.state.absence["site"] == {"old": 1}
    assert step.state.tracked["site"] == {
        "old": OLD_MAC,
        "stay": STAY_MAC,
        "new": NEW_MAC,
    }


def test_retirement_order_and_disappeared_site_pruning() -> None:
    seed = _poll({"old": OLD_MAC, "stay": STAY_MAC})
    state = _run(NetworkInventoryState(), a=seed, b=seed, gone=seed).state
    miss = _poll({"stay": STAY_MAC})
    for _ in range(2):
        state = _run(state, a=miss, b=miss, gone=miss).state
    step = _run(state, b=miss, a=miss)
    assert step.removals == (
        RemoveNetworkDevice("b", "old"),
        RemoveNetworkDevice("a", "old"),
    )
    for field in (
        step.state.sources,
        step.state.aliases,
        step.state.tracked,
        step.state.absence,
    ):
        assert set(field) == {"a", "b"}


def test_inventory_module_has_no_registry_transport_or_clock_imports() -> None:
    source = Path(device_inventory.__file__).read_text()
    tree = ast.parse(source)
    imports = [
        name
        for node in ast.walk(tree)
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
    ]
    assert not any(
        name.split(".", 1)[0]
        in {"homeassistant", "unifi_official_api", "time", "datetime"}
        for name in imports
    )
