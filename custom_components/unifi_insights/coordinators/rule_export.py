# Copyright 2026 UniFi Insights contributors
"""Fresh, versioned export of the UniFi network rule families."""

from __future__ import annotations

import json
import logging
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
    verified_legacy_site_name,
)
from .rule_audit import RULE_FAMILIES

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from unifi_official_api.network import UniFiNetworkClient

_LOGGER = logging.getLogger(__name__)

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
