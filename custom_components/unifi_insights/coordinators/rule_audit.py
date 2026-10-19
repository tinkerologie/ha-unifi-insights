# Copyright 2026 UniFi Insights contributors
"""Rule change detection for the UniFi Insights config coordinator."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, Final

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from unifi_official_api.exceptions import UniFiResponseError

from custom_components.unifi_insights.const import DOMAIN

from .config_sections import (
    SECTION_OUTCOME_FAILED,
    SECTION_OUTCOME_OK,
    SECTION_OUTCOME_UNSUPPORTED,
    is_predefined_firewall_rule,
    is_predefined_nat_rule,
    verified_legacy_site_name,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from unifi_official_api.network import UniFiNetworkClient

_LOGGER = logging.getLogger(__name__)

RULE_CHANGE_ADDED: Final = "added"
RULE_CHANGE_MODIFIED: Final = "modified"
RULE_CHANGE_DELETED: Final = "deleted"
RULE_CHANGE_TYPES: Final[tuple[str, ...]] = (
    RULE_CHANGE_ADDED,
    RULE_CHANGE_MODIFIED,
    RULE_CHANGE_DELETED,
)


# Changes kept per site. A poll that produces more drops the oldest ones.
RULE_CHANGE_BUFFER_SIZE: Final = 100

# Keys that describe rule order or bookkeeping, not rule content. A reorder
# renumbers every following rule, so these never count as a modification.
VOLATILE_RULE_KEYS: Final[frozenset[str]] = frozenset(
    {"index", "rule_index", "metadata"}
)


@dataclass(frozen=True, kw_only=True)
class RuleFamily:
    """One audited rule family and where its records live in coordinator data."""

    rule_type: str
    data_key: str
    name_fields: tuple[str, ...]
    is_excluded: Callable[[dict[str, Any]], bool] | None = None


RULE_FAMILIES: Final[tuple[RuleFamily, ...]] = (
    RuleFamily(
        rule_type="firewall_rule",
        data_key="firewall_rules",
        name_fields=("name",),
        is_excluded=is_predefined_firewall_rule,
    ),
    RuleFamily(
        rule_type="policy_based_route",
        data_key="policy_based_routes",
        name_fields=("description", "name"),
    ),
    RuleFamily(
        rule_type="port_forward",
        data_key="port_forwards",
        name_fields=("name",),
    ),
    RuleFamily(
        rule_type="traffic_rule",
        data_key="traffic_rules",
        name_fields=("description",),
    ),
    RuleFamily(
        rule_type="nat_rule",
        data_key="nat_rules",
        name_fields=("description",),
        is_excluded=is_predefined_nat_rule,
    ),
    RuleFamily(
        rule_type="static_route",
        data_key="static_routes",
        name_fields=("name",),
    ),
)

RULE_TYPES: Final[tuple[str, ...]] = tuple(family.rule_type for family in RULE_FAMILIES)


@dataclass(frozen=True, kw_only=True)
class _RuleSnapshot:
    """Immutable copy of the parts of one rule the audit compares."""

    name: str | None
    enabled: bool | None
    fields: dict[str, str]


def _rule_name(family: RuleFamily, record: dict[str, Any]) -> str | None:
    """Return the first non-blank name of a rule, or None."""
    for field in family.name_fields:
        value = record.get(field)
        if isinstance(value, str) and (stripped := value.strip()):
            return stripped
    return None


def _snapshot(family: RuleFamily, record: dict[str, Any]) -> _RuleSnapshot:
    """
    Build the comparable copy of one rule record.

    Every value is stored as its canonical JSON text, so the snapshot shares
    no object with coordinator data: a switch that edits ``enabled`` in place
    for instant feedback cannot change the baseline it is compared with.
    """
    enabled = record.get("enabled")
    return _RuleSnapshot(
        name=_rule_name(family, record),
        enabled=enabled if isinstance(enabled, bool) else None,
        fields={
            str(key): json.dumps(value, sort_keys=True, default=str)
            for key, value in record.items()
            if key not in VOLATILE_RULE_KEYS
        },
    )


def _change(
    change_type: str,
    family: RuleFamily,
    site_id: str,
    rule_id: str,
    snapshot: _RuleSnapshot,
    *,
    changed_fields: list[str],
) -> dict[str, Any]:
    """Build one change record. It holds field names, never field values."""
    return {
        "change_type": change_type,
        "site_id": site_id,
        "rule_type": family.rule_type,
        "rule_id": rule_id,
        "rule_name": snapshot.name,
        "enabled": snapshot.enabled,
        "changed_fields": changed_fields,
    }


def _changed_fields(old: _RuleSnapshot, new: _RuleSnapshot) -> list[str]:
    """Return the sorted names of the fields that differ between two snapshots."""
    return sorted(
        key
        for key in old.fields.keys() | new.fields.keys()
        if old.fields.get(key) != new.fields.get(key)
    )


def _diff(
    family: RuleFamily,
    site_id: str,
    previous: dict[str, _RuleSnapshot],
    current: dict[str, _RuleSnapshot],
) -> list[dict[str, Any]]:
    """Return the changes between two snapshots of one family on one site."""
    added = [
        _change(
            RULE_CHANGE_ADDED,
            family,
            site_id,
            rule_id,
            current[rule_id],
            changed_fields=[],
        )
        for rule_id in sorted(current.keys() - previous.keys())
    ]
    modified = [
        _change(
            RULE_CHANGE_MODIFIED,
            family,
            site_id,
            rule_id,
            current[rule_id],
            changed_fields=names,
        )
        for rule_id in sorted(current.keys() & previous.keys())
        if (names := _changed_fields(previous[rule_id], current[rule_id]))
    ]
    deleted = [
        _change(
            RULE_CHANGE_DELETED,
            family,
            site_id,
            rule_id,
            previous[rule_id],
            changed_fields=[],
        )
        for rule_id in sorted(previous.keys() - current.keys())
    ]
    return [*added, *modified, *deleted]


class RuleAuditTracker:
    """
    Detect added, modified and deleted rules between config refreshes.

    A section is compared only when this refresh fetched it successfully
    (outcome ``ok``). The first successful fetch of a section is its baseline
    and reports nothing. An unsupported or failed fetch keeps the baseline, so
    an outage never looks like "every rule was deleted". A section that turns
    empty is reported only when the next successful fetch is empty too.
    """

    def __init__(self) -> None:
        """Initialize an empty tracker."""
        self._baselines: dict[tuple[str, str], dict[str, _RuleSnapshot]] = {}
        self._changes: dict[str, list[dict[str, Any]]] = {}
        self._pending_empty: set[tuple[str, str]] = set()
        self._failing_sites: set[str] = set()
        self._sequence = 0

    def process(
        self,
        data: Mapping[str, Any],
        outcomes: Mapping[tuple[str, str], str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Compare coordinator data with the baselines; return changes by site."""
        sites = data.get("sites")
        site_ids: set[str] = set(sites) if isinstance(sites, dict) else set()

        for key in [key for key in self._baselines if key[1] not in site_ids]:
            _LOGGER.debug(
                "Rule audit: site %s is gone; its %s baseline was dropped",
                key[1],
                key[0],
            )
            del self._baselines[key]
        self._pending_empty = {key for key in self._pending_empty if key[1] in site_ids}
        self._failing_sites &= site_ids
        for site_id in [
            site_id for site_id in self._changes if site_id not in site_ids
        ]:
            del self._changes[site_id]

        for site_id in sorted(site_ids):
            try:
                baselines, pending, settled, new_changes = self._diff_site(
                    data, outcomes, site_id
                )
            except Exception:
                # One site's comparison must never stop the others. Nothing was
                # committed for this site, so its changes are reported by the
                # next comparison that succeeds.
                if site_id not in self._failing_sites:
                    self._failing_sites.add(site_id)
                    _LOGGER.exception(
                        "Rule audit: comparing the rules of site %s failed; its "
                        "changes are reported once the comparison works again",
                        site_id,
                    )
                continue
            self._failing_sites.discard(site_id)
            self._baselines.update(baselines)
            self._pending_empty -= settled
            self._pending_empty |= pending
            if not new_changes:
                continue
            for change in new_changes:
                self._sequence += 1
                change["sequence"] = self._sequence
            buffered = [*self._changes.get(site_id, []), *new_changes]
            if len(new_changes) > RULE_CHANGE_BUFFER_SIZE:
                _LOGGER.warning(
                    "Rule audit: %d rule changes for site %s in one refresh; "
                    "only the newest %d are reported",
                    len(new_changes),
                    site_id,
                    RULE_CHANGE_BUFFER_SIZE,
                )
            self._changes[site_id] = buffered[-RULE_CHANGE_BUFFER_SIZE:]

        return {site_id: list(changes) for site_id, changes in self._changes.items()}

    def _diff_site(
        self,
        data: Mapping[str, Any],
        outcomes: Mapping[tuple[str, str], str],
        site_id: str,
    ) -> tuple[
        dict[tuple[str, str], dict[str, _RuleSnapshot]],
        set[tuple[str, str]],
        set[tuple[str, str]],
        list[dict[str, Any]],
    ]:
        """
        Compare every rule family of one site without changing the tracker.

        Returns the new baselines, the sections seen empty for the first time,
        the sections whose state is settled, and the changes found.
        """
        baselines: dict[tuple[str, str], dict[str, _RuleSnapshot]] = {}
        pending: set[tuple[str, str]] = set()
        settled: set[tuple[str, str]] = set()
        changes: list[dict[str, Any]] = []
        for family in RULE_FAMILIES:
            if outcomes.get((family.data_key, site_id)) != SECTION_OUTCOME_OK:
                continue
            current = self._current(family, data, site_id)
            if current is None:
                continue
            key = (family.data_key, site_id)
            previous = self._baselines.get(key)
            if previous and not current and key not in self._pending_empty:
                # "Every rule is gone" is believed only when the next successful
                # fetch says so too: a console that answers with an empty body
                # looks exactly like one whose rules were all deleted. The
                # baseline is kept, so nothing is lost if the rules come back.
                pending.add(key)
                continue
            settled.add(key)
            baselines[key] = current
            if previous is not None:
                changes.extend(_diff(family, site_id, previous, current))
        return baselines, pending, settled, changes

    @staticmethod
    def _current(
        family: RuleFamily, data: Mapping[str, Any], site_id: str
    ) -> dict[str, _RuleSnapshot] | None:
        """Snapshot one family on one site, or None if its data is malformed."""
        section = data.get(family.data_key)
        rules = section.get(site_id) if isinstance(section, dict) else None
        if not isinstance(rules, dict):
            return None
        return {
            rule_id: _snapshot(family, record)
            for rule_id, record in rules.items()
            if isinstance(rule_id, str)
            and isinstance(record, dict)
            and not (family.is_excluded is not None and family.is_excluded(record))
        }


# --- Rule export -------------------------------------------------------------

# Fresh, versioned export of the UniFi network rule families.

RULE_EXPORT_FORMAT_VERSION: Final = 1


def _rule_fetchers(
    network_client: UniFiNetworkClient,
) -> dict[str, tuple[bool, Callable[..., Awaitable[list[Any]]]]]:
    """
    Map each rule type to ``(uses_classic_site_name, list method)``.

    Firewall policies are official API and take the site id. Every other
    family is a classic or v2 endpoint and takes the site's verified classic
    name (``internalReference``). Each list method accepts ``strict``, which
    the export always sets.
    """
    return {
        "firewall_rule": (False, network_client.firewall.list_rules),
        "policy_based_route": (True, network_client.routes.list_routes),
        "port_forward": (True, network_client.port_forwards.list_port_forwards),
        "traffic_rule": (True, network_client.traffic_rules.list_traffic_rules),
        "nat_rule": (True, network_client.nat_rules.list_nat_rules),
        "static_route": (True, network_client.static_routes.list_static_routes),
    }


def _export_record(model: Any) -> Any:
    """
    Return one rule as JSON-safe data, with the console's own key names.

    Only the fields the console sent are exported: a model default such as
    ``logging: False`` or ``enabled: True`` would otherwise read as a fact
    about the console. A key the console sent as ``null`` is kept.
    """
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json", by_alias=True, exclude_unset=True)
    return model


def _rule_sort_key(record: Any) -> tuple[str, str]:
    """
    Return the order key of an exported rule: its id, then its canonical JSON.

    The console may list the same rules in another order from one call to the
    next. The order fields inside a record (``index``, ``rule_index``) are not
    touched, so a real reorder on the console still shows as changed values.
    """
    rule_id = (
        (record.get("_id") or record.get("id")) if isinstance(record, dict) else None
    )
    return str(rule_id), json.dumps(record, sort_keys=True, default=str)


async def _async_export_family(
    rule_type: str,
    site_id: str,
    site_ref: str | None,
    fetch: Callable[..., Awaitable[list[Any]]],
    is_unsupported: Callable[[Exception], bool],
) -> dict[str, Any]:
    """Fetch one rule family of one site and report how the fetch ended."""
    status = SECTION_OUTCOME_UNSUPPORTED
    rules: list[Any] = []
    if site_ref is not None:
        try:
            # Strict: a broken or partly parsed answer raises instead of being
            # exported as a shorter list of rules.
            models = await fetch(site_ref, strict=True)
            rules = [_export_record(model) for model in models]
            rules.sort(key=_rule_sort_key)
            status = SECTION_OUTCOME_OK
        except Exception as err:
            # A 2xx answer the transport could not parse (a login page, a cut
            # off body) is not the console refusing the list, so it is a failed
            # read; the shared classifier would call it unsupported.
            answered_ok = (
                isinstance(err, UniFiResponseError)
                and err.status_code < HTTPStatus.MULTIPLE_CHOICES
            )
            if answered_ok or not is_unsupported(err):
                status = SECTION_OUTCOME_FAILED
                # The error text can quote the request or the response body, so
                # only its type is logged and nothing of it is returned.
                _LOGGER.warning(
                    "Rule export: unable to fetch %s for site %s (%s)",
                    rule_type,
                    site_id,
                    type(err).__name__,
                )
    return {"status": status, "count": len(rules), "rules": rules}


async def async_build_rule_export(
    network_client: UniFiNetworkClient,
    sites: Mapping[str, Any],
    *,
    console: dict[str, Any],
    site_ids: list[str] | None,
    rule_types: list[str] | None,
    is_unsupported: Callable[[Exception], bool],
) -> dict[str, Any]:
    """
    Fetch the requested rule families now and return the export document.

    Nothing is read from coordinator data: an export must show what the
    console holds at this moment, and say so per family when it could not be
    read. Raises ``HomeAssistantError`` only when every fetch failed.
    """
    fetchers = _rule_fetchers(network_client)
    families = [
        family
        for family in RULE_FAMILIES
        if rule_types is None or family.rule_type in rule_types
    ]
    exported_sites: dict[str, Any] = {}
    statuses: list[str] = []
    # Sorted, so two exports of an unchanged console list sites and rules alike.
    for site_id in sorted(sites):
        site = sites[site_id]
        if site_ids is not None and site_id not in site_ids:
            continue
        classic_name = verified_legacy_site_name(site)
        exported_types: dict[str, Any] = {}
        for family in families:
            uses_classic_name, fetch = fetchers[family.rule_type]
            exported_types[family.rule_type] = await _async_export_family(
                family.rule_type,
                site_id,
                classic_name if uses_classic_name else site_id,
                fetch,
                is_unsupported,
            )
            statuses.append(exported_types[family.rule_type]["status"])
        exported_sites[site_id] = {
            "name": site.get("name") if isinstance(site, dict) else None,
            "rule_types": exported_types,
        }

    if statuses and all(status == SECTION_OUTCOME_FAILED for status in statuses):
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="export_network_rules_failed",
        )

    return {
        "format_version": RULE_EXPORT_FORMAT_VERSION,
        "generated_at": dt_util.utcnow().isoformat(),
        "redacted": False,
        "console": console,
        "sites": exported_sites,
    }
