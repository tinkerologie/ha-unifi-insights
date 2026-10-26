# Copyright 2026 UniFi Insights contributors
"""
Facade snapshot builders for tests (ADR-013).

The facade publishes a read-only root, so a test never writes into
``facade.data``: it builds a new snapshot and assigns it, as an owner update
would. ``facade_data`` builds a complete snapshot through the facade's own
``build_facade_data``; the ``with_*`` builders return a new snapshot with one
record added or replaced, copy-on-write. Every section and record they take is
deep-frozen (``frozen``), so code under test that writes into the snapshot
fails. ``with_sections`` replaces whole top-level sections, frozen too, and
``without_sections`` drops them.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NoReturn, cast

from homeassistant.util.read_only_dict import ReadOnlyDict

from custom_components.unifi_insights.coordinators.facade import (
    FacadeData,
    build_facade_data,
    capture_availability,
)
from custom_components.unifi_insights.data_transforms import (
    NETWORK_FACTS_KEY,
    build_innerspace_view,
    build_site_network_facts,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

# The time every built snapshot reports as its last update.
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

# Every top-level key a facade snapshot can hold.
FACADE_DATA_KEYS: frozenset[str] = (
    FacadeData.__required_keys__ | FacadeData.__optional_keys__
)


class FrozenList(list[Any]):
    """A list that raises on every write, and still is a ``list``."""

    def _readonly(self, *_args: Any, **_kwargs: Any) -> NoReturn:
        """Raise like ``ReadOnlyDict`` does."""
        msg = "Cannot modify FrozenList"
        raise RuntimeError(msg)

    __setitem__ = _readonly
    __delitem__ = _readonly
    __iadd__ = _readonly
    __imul__ = _readonly
    append = _readonly
    extend = _readonly
    insert = _readonly
    pop = _readonly
    remove = _readonly
    clear = _readonly
    reverse = _readonly

    def sort(
        self, *, key: Callable[[Any], Any] | None = None, reverse: bool = False
    ) -> NoReturn:
        """Reject sorting with the same callable signature as list.sort."""
        self._readonly(key=key, reverse=reverse)

    def __copy__(self) -> FrozenList:
        """Return a shallow copy."""
        return FrozenList(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> FrozenList:
        """Return a deep copy."""
        return FrozenList(copy.deepcopy(item, memo) for item in self)


def frozen(value: Any) -> Any:
    """
    Return a deep, test-only frozen copy of ``value``.

    Mappings become ``ReadOnlyDict``, lists ``FrozenList`` and sets
    ``frozenset``; tuples are frozen item by item. Both stay a ``dict`` and a
    ``list``, so the result still serializes to JSON and passes
    ``async_redact_data``. A value that is already frozen all the way down
    is returned as is, so frozen sections and records keep their identity.
    Anything else is returned as is.
    """
    if isinstance(value, Mapping):
        items = {key: frozen(item) for key, item in value.items()}
        if isinstance(value, ReadOnlyDict) and _same_items(
            value.values(), items.values()
        ):
            return value
        return ReadOnlyDict(items)
    if isinstance(value, list):
        listed = [frozen(item) for item in value]
        if isinstance(value, FrozenList) and _same_items(value, listed):
            return value
        return FrozenList(listed)
    if isinstance(value, frozenset):
        return value
    if isinstance(value, set):
        return frozenset(value)
    if isinstance(value, tuple):
        parts = tuple(frozen(item) for item in value)
        return value if _same_items(value, parts) else parts
    return value


def _same_items(old: Iterable[Any], new: Iterable[Any]) -> bool:
    """Return whether freezing kept every item, by identity."""
    return all(a is b for a, b in zip(old, new, strict=True))


def _snapshot(data: Mapping[str, Any]) -> FacadeData:
    """Return ``data`` as a read-only snapshot root."""
    return cast("FacadeData", ReadOnlyDict(data))


def facade_data(
    *,
    site_ids: Iterable[str] = (),
    failed_sections: Iterable[tuple[str, str]] = (),
    **sections: Any,
) -> FacadeData:
    """
    Build a complete, frozen snapshot with empty sections by default.

    ``sections`` replace top-level sections by key (``network_facts`` too;
    by default it is None, as before the device coordinator publishes it).
    The default availability is all available; ``site_ids`` and
    ``failed_sections`` capture per-site availability as the facade would.
    """
    unknown = set(sections) - FACADE_DATA_KEYS
    if unknown:
        msg = f"Not facade snapshot keys: {sorted(unknown)}"
        raise TypeError(msg)
    base = build_facade_data(
        {},
        {},
        None,
        build_innerspace_view(None, None, None),
        availability=capture_availability(
            device=True,
            config=True,
            protect=True,
            innerspace=True,
            network=True,
            failed_sections=frozenset(failed_sections),
            site_ids=site_ids,
        ),
        site_manager=None,
        site_manager_host_id=None,
        now=NOW,
    )
    return _snapshot(
        {
            **{key: frozen(value) for key, value in base.items()},
            **{key: frozen(value) for key, value in sections.items()},
        }
    )


def with_sections(data: Mapping[str, Any], **sections: Any) -> FacadeData:
    """Return a copy of ``data`` with the given top-level sections replaced, frozen."""
    return _snapshot(
        {**data, **{key: frozen(value) for key, value in sections.items()}}
    )


def without_sections(data: Mapping[str, Any], *keys: str) -> FacadeData:
    """Return a copy of ``data`` without the given top-level sections."""
    return _snapshot({key: value for key, value in data.items() if key not in keys})


def _with_record(
    data: Mapping[str, Any], section: str, group: str, key: str, record: Any
) -> dict[str, Any]:
    """Return ``data[section]`` with ``record`` at ``[group][key]``, copy-on-write."""
    by_group = data.get(section)
    by_group = by_group if isinstance(by_group, Mapping) else {}
    records = by_group.get(group)
    records = records if isinstance(records, Mapping) else {}
    return ReadOnlyDict(
        {**by_group, group: ReadOnlyDict({**records, key: frozen(record)})}
    )


def with_network_device(
    data: Mapping[str, Any],
    site_id: str,
    device_id: str,
    record: Mapping[str, Any],
    *,
    stats: Mapping[str, Any] | None = None,
) -> FacadeData:
    """
    Return ``data`` with a Network device (and its stats) added or replaced.

    Published network facts are republished from the new records.
    """
    sections: dict[str, Any] = {
        "devices": _with_record(data, "devices", site_id, device_id, record)
    }
    if stats is not None:
        sections["stats"] = _with_record(data, "stats", site_id, device_id, stats)
    return _with_network_sections(data, site_id, sections)


def with_device_stats(
    data: Mapping[str, Any], site_id: str, device_id: str, stats: Any
) -> FacadeData:
    """
    Return ``data`` with a Network device's stats record replaced.

    ``stats`` may be any value, so a test can publish malformed stats (None,
    a string) as well as a record. Published network facts are republished.
    """
    return _with_network_sections(
        data,
        site_id,
        {"stats": _with_record(data, "stats", site_id, device_id, stats)},
    )


def _record_at(data: Mapping[str, Any], section: str, group: str, key: str) -> Any:
    """Return ``data[section][group][key]``, or an empty mapping when absent."""
    by_group = data.get(section)
    records = by_group.get(group) if isinstance(by_group, Mapping) else None
    record = records.get(key) if isinstance(records, Mapping) else None
    return record if isinstance(record, Mapping) else {}


def with_device_fields(
    data: Mapping[str, Any], site_id: str, device_id: str, **fields: Any
) -> FacadeData:
    """Return ``data`` with fields of a Network device's record replaced."""
    record = _record_at(data, "devices", site_id, device_id)
    return with_network_device(data, site_id, device_id, {**record, **fields})


def with_device_stats_fields(
    data: Mapping[str, Any], site_id: str, device_id: str, **fields: Any
) -> FacadeData:
    """Return ``data`` with fields of a Network device's stats record replaced."""
    stats = _record_at(data, "stats", site_id, device_id)
    return with_device_stats(data, site_id, device_id, {**stats, **fields})


def _with_network_sections(
    data: Mapping[str, Any], site_id: str, sections: dict[str, Any]
) -> FacadeData:
    """
    Return ``data`` with the given Network sections replaced.

    When the snapshot carries published network facts, the site's facts are
    republished from the new records, as the device coordinator would.
    """
    facts = data.get(NETWORK_FACTS_KEY)
    if isinstance(facts, Mapping):
        devices = sections.get("devices") or data.get("devices") or {}
        site_stats = (sections.get("stats") or data.get("stats") or {}).get(site_id)
        sections[NETWORK_FACTS_KEY] = ReadOnlyDict(
            {
                **facts,
                site_id: build_site_network_facts(devices.get(site_id, {}), site_stats),
            }
        )
    return with_sections(data, **sections)


def with_client(
    data: Mapping[str, Any], site_id: str, client_id: str, record: Mapping[str, Any]
) -> FacadeData:
    """Return ``data`` with a Network client added or replaced."""
    return with_sections(
        data, clients=_with_record(data, "clients", site_id, client_id, record)
    )


def with_rule(
    data: Mapping[str, Any],
    kind: str,
    site_id: str,
    rule_id: str,
    record: object,
) -> FacadeData:
    """Return ``data`` with a rule of section ``kind`` added or replaced."""
    return with_sections(
        data, **{kind: _with_record(data, kind, site_id, rule_id, record)}
    )


def with_protect_device(
    data: Mapping[str, Any],
    collection: str,
    device_id: str,
    record: object,
) -> FacadeData:
    """Return ``data`` with a Protect device of ``collection`` added or replaced."""
    return with_sections(
        data, protect=_with_record(data, "protect", collection, device_id, record)
    )


def with_protect_fields(
    data: Mapping[str, Any], collection: str, device_id: str, **fields: Any
) -> FacadeData:
    """Return ``data`` with fields of a Protect device's record replaced."""
    record = _record_at(data, "protect", collection, device_id)
    return with_protect_device(data, collection, device_id, {**record, **fields})


def with_protect_collection(
    data: Mapping[str, Any], collection: str, records: Any
) -> FacadeData:
    """
    Return ``data`` with a whole Protect collection replaced.

    ``records`` may be any value, so a test can publish a malformed
    collection (None, a string) as well as a mapping of records.
    """
    protect = data.get("protect")
    protect = protect if isinstance(protect, Mapping) else {}
    return with_sections(
        data, protect=ReadOnlyDict({**protect, collection: frozen(records)})
    )


def with_camera(data: Mapping[str, Any], camera_id: str, record: object) -> FacadeData:
    """Return ``data`` with a Protect camera added or replaced."""
    return with_protect_device(data, "cameras", camera_id, record)


def with_latest_voucher(
    data: Mapping[str, Any], site_id: str, record: Mapping[str, Any] | None
) -> FacadeData:
    """Return ``data`` with a site's latest generated voucher set or cleared."""
    latest = data.get("latest_vouchers")
    latest = latest if isinstance(latest, Mapping) else {}
    return with_sections(
        data,
        latest_vouchers=ReadOnlyDict({**latest, site_id: frozen(record)}),
    )


def with_availability(
    data: Mapping[str, Any],
    *,
    device: bool = True,
    config: bool = True,
    protect: bool = True,
    innerspace: bool = True,
    network: bool = True,
    failed_sections: Iterable[tuple[str, str]] = (),
    site_ids: Iterable[str] | None = None,
) -> Any:
    """
    Return ``data`` with its availability captured anew, as the facade would.

    ``site_ids`` defaults to the sites ``data`` lists. A read-only snapshot
    stays read-only; a plain dict is copied as a plain dict.
    """
    if site_ids is None:
        sites = data.get("sites")
        site_ids = tuple(sites) if isinstance(sites, Mapping) else ()
    availability = capture_availability(
        device=device,
        config=config,
        protect=protect,
        innerspace=innerspace,
        network=network,
        failed_sections=frozenset(failed_sections),
        site_ids=site_ids,
    )
    if isinstance(data, ReadOnlyDict):
        return with_sections(data, availability=availability)
    return {**data, "availability": availability}


_OWNERS = ("device", "config", "protect", "innerspace", "network")


def set_availability(coordinator: Any, **availability: Any) -> None:
    """
    Flip a mocked facade's captured availability in one call.

    Takes the keyword arguments of ``with_availability`` and replaces
    ``coordinator.data`` with the result, as a facade rebuild would. An owner
    not given keeps the availability ``coordinator.data`` captured; failed
    sections are only the ones given.
    """
    data = coordinator.data
    current = data.get("availability")
    current = current if isinstance(current, Mapping) else {}
    owners = {owner: current.get(owner, True) for owner in _OWNERS}
    coordinator.data = with_availability(data, **{**owners, **availability})
