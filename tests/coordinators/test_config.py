# Copyright 2026 UniFi Insights contributors
"""
Tests for the config coordinator's command foundations (ADR-010).

Covers the published legacy site-name mapping and its lifecycle, and the
single ``resolve_legacy_site_name`` rule, against seeded real owners over
autospecced ``unifi_official_api`` clients.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from homeassistant.const import CONF_API_KEY
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiRateLimitError,
    UniFiRuleNotEditableError,
    UniFiTimeoutError,
)
from unifi_official_api.exceptions import UniFiResponseError
from unifi_official_api.network.models import SiteReportBucket
from unifi_official_api.protect.models import (
    Camera,
    CameraState,
    Viewer,
)
from unifi_official_api.protect.models.viewer import ViewerState

from custom_components.unifi_insights.const import (
    CONF_SITE_IDS,
    DOMAIN,
    SCAN_INTERVAL_CONFIG,
)
from custom_components.unifi_insights.coordinators.config import (
    LEGACY_REMAP_COOLDOWN,
    RULE_REFRESH_RETRY_DELAY,
    WINDOW_1D_MS,
    WINDOW_1H_MS,
    WINDOW_1M_MS,
    WINDOW_1W_MS,
    UnifiInsightsConfigCoordinator,
    aggregate_internet_activity_windows,
)
from custom_components.unifi_insights.coordinators.config_sections import (
    async_fetch_site_routes,
    async_fetch_site_wifi_and_links,
    async_update_site_internet_activity,
    enrich_wifi,
    is_predefined_nat_rule,
    map_legacy_site_names,
    resolve_report_site_name,
    rule_display_name,
    verified_legacy_site_name,
    wifi_qr_payload,
)
from custom_components.unifi_insights.coordinators.facade import (
    UnifiInsightsFacadeCoordinator,
)
from tests.fixtures.command_owners import (
    SITE_ID,
    async_refresh_owners,
    async_shutdown_owners,
    build_seeded_owners,
)
from tests.fixtures.coordinator_clients import (
    RULE_SECTION_PARAMS,
    _create_mock_model,
    _create_mock_network_client,
    _create_mock_protect_client,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant

    from tests.fixtures.command_owners import SeededOwners

BRANCH_ID = "branch_site"


def _two_sites(owners: SeededOwners, *, verified: bool = True) -> None:
    """Seed a second integration site and its classic counterpart."""
    sites = owners.network_client.sites.get_all.return_value
    branch = {"id": BRANCH_ID, "name": "Branch", "desc": "Branch Office"}
    if verified:
        branch["internalReference"] = "abc123"
    sites.append(branch)
    owners.network_client.sites.get_legacy_all.return_value = [
        {"name": "default", "desc": "Default Site"},
        {"name": "abc123", "desc": "Branch Office"},
    ]


def _assert_legacy_not_found(err: HomeAssistantError, site_id: str) -> None:
    """Assert the translated legacy_site_not_found error for one site."""
    assert err.translation_domain == DOMAIN
    assert err.translation_key == "legacy_site_not_found"
    assert err.translation_placeholders == {"site_id": site_id}


class TestPublishedLegacyMapping:
    """The mapping lifecycle the config coordinator publishes in its data."""

    async def test_initial_data_has_an_empty_unflagged_mapping(
        self, hass: HomeAssistant
    ) -> None:
        """Before any refresh the keys exist, so readers never miss them."""
        owners = build_seeded_owners(hass)

        assert owners.config.data["legacy_site_names"] == {}
        assert owners.config.data["legacy_mapping_failed"] is False

    async def test_successful_lookup_publishes_the_mapping(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A successful lookup publishes the selected sites' names."""
        assert seeded_owners.config.data["legacy_site_names"] == {SITE_ID: "default"}
        assert seeded_owners.config.data["legacy_mapping_failed"] is False

    async def test_lookup_failure_keeps_the_last_good_mapping(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A failed lookup flags the mapping and keeps the last good names."""
        config = seeded_owners.config
        client = seeded_owners.network_client
        before = config.data["legacy_site_names"]
        client.sites.get_legacy_all.side_effect = UniFiConnectionError("down")
        client.vpn_clients.list_vpn_clients.reset_mock()

        await config.async_refresh()

        assert config.last_update_success is True
        assert config.data["legacy_mapping_failed"] is True
        assert config.data["legacy_site_names"] == {SITE_ID: "default"}
        assert config.data["legacy_site_names"] is not before
        # This cycle's section fetches still run without a classic site name.
        client.vpn_clients.list_vpn_clients.assert_not_awaited()

    async def test_first_lookup_failure_publishes_an_empty_mapping(
        self, hass: HomeAssistant
    ) -> None:
        """With no earlier mapping a failed lookup publishes nothing."""
        owners = build_seeded_owners(hass)
        owners.network_client.sites.get_legacy_all.side_effect = UniFiConnectionError(
            "down"
        )
        try:
            await owners.config.async_refresh()

            assert owners.config.data["legacy_site_names"] == {}
            assert owners.config.data["legacy_mapping_failed"] is True
        finally:
            await async_shutdown_owners(owners)

    async def test_recovery_replaces_the_mapping_and_clears_the_flag(
        self, seeded_owners: SeededOwners
    ) -> None:
        """The next successful lookup replaces the mapping."""
        config = seeded_owners.config
        lookup = seeded_owners.network_client.sites.get_legacy_all
        lookup.side_effect = UniFiConnectionError("down")
        await config.async_refresh()

        lookup.side_effect = None
        lookup.return_value = [{"name": "renamed", "desc": "Default Site"}]
        await config.async_refresh()

        assert config.data["legacy_site_names"] == {SITE_ID: "renamed"}
        assert config.data["legacy_mapping_failed"] is False

    async def test_no_selected_sites_publishes_an_empty_unflagged_mapping(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A console without sites has no mapping and no failure."""
        config = seeded_owners.config
        seeded_owners.network_client.sites.get_legacy_all.side_effect = (
            UniFiConnectionError("down")
        )
        await config.async_refresh()
        seeded_owners.network_client.sites.get_all.return_value = []

        await config.async_refresh()

        assert config.data["legacy_site_names"] == {}
        assert config.data["legacy_mapping_failed"] is False

    @pytest.mark.parametrize("lookup_fails", [False, True])
    async def test_site_selection_prunes_the_mapping(
        self, hass: HomeAssistant, *, lookup_fails: bool
    ) -> None:
        """Deselected sites drop out whether or not the lookup succeeds."""
        owners = build_seeded_owners(hass)
        _two_sites(owners)
        try:
            await async_refresh_owners(owners)
            assert owners.config.data["legacy_site_names"] == {
                SITE_ID: "default",
                BRANCH_ID: "abc123",
            }

            hass.config_entries.async_update_entry(
                owners.entry, options={CONF_SITE_IDS: [SITE_ID]}
            )
            if lookup_fails:
                owners.network_client.sites.get_legacy_all.side_effect = (
                    UniFiConnectionError("down")
                )
            await owners.config.async_refresh()

            assert owners.config.data["legacy_site_names"] == {SITE_ID: "default"}
            assert owners.config.data["legacy_mapping_failed"] is lookup_fails
        finally:
            await async_shutdown_owners(owners)

    @pytest.mark.usefixtures("enable_custom_integrations")
    async def test_whole_refresh_failure_leaves_the_mapping_unchanged(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A refresh that fails after the lookup does not publish its mapping."""
        config = seeded_owners.config
        client = seeded_owners.network_client
        before = config.data["legacy_site_names"]
        client.sites.get_legacy_all.return_value = [
            {"name": "renamed", "desc": "Default Site"}
        ]
        client.routes.list_routes.side_effect = UniFiAuthenticationError(
            "revoked", status_code=401
        )

        await config.async_refresh()

        assert config.last_update_success is False
        assert config.data["legacy_site_names"] is before
        assert config.data["legacy_mapping_failed"] is False


class TestResolveLegacySiteName:
    """The single legacy site-name rule."""

    @pytest.mark.parametrize("verified_only", [False, True])
    async def test_verified_internal_reference_comes_first(
        self, seeded_owners: SeededOwners, *, verified_only: bool
    ) -> None:
        """The verified internalReference wins over the published mapping."""
        config = seeded_owners.config
        config.data["legacy_site_names"] = {SITE_ID: "fuzzy-match"}

        assert (
            config.resolve_legacy_site_name(SITE_ID, verified_only=verified_only)
            == "default"
        )

    async def test_mapped_mode_falls_back_to_the_published_mapping(
        self, hass: HomeAssistant
    ) -> None:
        """Without internalReference the mapped mode uses the mapping."""
        owners = build_seeded_owners(hass)
        _two_sites(owners, verified=False)
        try:
            await async_refresh_owners(owners)

            assert (
                owners.config.resolve_legacy_site_name(BRANCH_ID, verified_only=False)
                == "abc123"
            )
            with pytest.raises(HomeAssistantError) as caught:
                owners.config.resolve_legacy_site_name(BRANCH_ID, verified_only=True)
            _assert_legacy_not_found(caught.value, BRANCH_ID)
        finally:
            await async_shutdown_owners(owners)

    async def test_a_real_default_mapping_is_returned(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A mapped classic name of "default" is a real answer, not a guess."""
        config = seeded_owners.config
        del config.data["sites"][SITE_ID]["internalReference"]

        assert config.resolve_legacy_site_name(SITE_ID, verified_only=False) == (
            "default"
        )

    @pytest.mark.parametrize("mapped", [None, "", "   "])
    @pytest.mark.parametrize("verified_only", [False, True])
    async def test_never_guesses_default(
        self,
        seeded_owners: SeededOwners,
        mapped: str | None,
        *,
        verified_only: bool,
    ) -> None:
        """No verified or mapped name raises instead of guessing "default"."""
        config = seeded_owners.config
        config.data["sites"][SITE_ID]["internalReference"] = " "
        config.data["legacy_site_names"] = {} if mapped is None else {SITE_ID: mapped}

        with pytest.raises(HomeAssistantError) as caught:
            config.resolve_legacy_site_name(SITE_ID, verified_only=verified_only)

        _assert_legacy_not_found(caught.value, SITE_ID)

    async def test_unknown_site_raises(self, seeded_owners: SeededOwners) -> None:
        """A site the console does not have cannot be resolved."""
        with pytest.raises(HomeAssistantError) as caught:
            seeded_owners.config.resolve_legacy_site_name(
                "missing", verified_only=False
            )

        _assert_legacy_not_found(caught.value, "missing")


def _seed_rules(owners: SeededOwners) -> None:
    """Seed one disabled rule in every rule family the config owner reads."""
    client = owners.network_client
    client.firewall.list_rules.return_value = [
        {"id": "rule1", "name": "Block IoT", "enabled": False}
    ]
    client.routes.list_routes.return_value = [
        {"_id": "rule1", "description": "Via VPN", "enabled": False}
    ]
    client.vpn_clients.list_vpn_clients.return_value = [
        {"_id": "rule1", "name": "Office VPN", "enabled": False}
    ]
    client.port_forwards.list_port_forwards.return_value = [
        {"_id": "rule1", "name": "Game", "enabled": False}
    ]
    client.traffic_rules.list_traffic_rules.return_value = [
        {"_id": "rule1", "description": "Kids", "enabled": False}
    ]
    client.nat_rules.list_nat_rules.return_value = [
        {"_id": "rule1", "description": "DNAT", "enabled": False}
    ]
    client.static_routes.list_static_routes.return_value = [
        {"_id": "rule1", "name": "Lab", "enabled": False}
    ]


# Command, published section, endpoint, endpoint method, classic site name
# expected by the endpoint (None: the integration site ID).
_TOGGLES = {
    "firewall": (
        "async_set_firewall_rule_enabled",
        "firewall_rules",
        "firewall",
        "update_rule",
        None,
    ),
    "route": (
        "async_set_policy_based_route_enabled",
        "policy_based_routes",
        "routes",
        "update_route",
        "default",
    ),
    "vpn": (
        "async_set_vpn_client_enabled",
        "vpn_clients",
        "vpn_clients",
        "update_vpn_client",
        "default",
    ),
    "port forward": (
        "async_set_port_forward_enabled",
        "port_forwards",
        "port_forwards",
        "update_port_forward",
        "default",
    ),
    "traffic rule": (
        "async_set_traffic_rule_enabled",
        "traffic_rules",
        "traffic_rules",
        "update_traffic_rule",
        "default",
    ),
    "NAT rule": (
        "async_set_nat_rule_enabled",
        "nat_rules",
        "nat_rules",
        "update_nat_rule",
        "default",
    ),
    "static route": (
        "async_set_static_route_enabled",
        "static_routes",
        "static_routes",
        "update_static_route",
        "default",
    ),
}
_LEGACY_RULES = ("port forward", "traffic rule", "NAT rule", "static route")


@pytest.fixture
async def rule_owners(hass: HomeAssistant) -> AsyncGenerator[SeededOwners]:
    """Seeded owners with one disabled rule in every family, refreshed."""
    owners = build_seeded_owners(hass)
    _seed_rules(owners)
    await async_refresh_owners(owners)
    yield owners
    await async_shutdown_owners(owners)


class TestRuleToggleCommands:
    """Rule toggles: one call, a copy-on-write overlay, then reconciliation."""

    @pytest.mark.parametrize("family", sorted(_TOGGLES))
    async def test_toggle_publishes_overlay_then_refreshes(
        self, rule_owners: SeededOwners, family: str
    ) -> None:
        """The write is overlaid on new containers and the owner refreshes."""
        command, section, endpoint, method, site_name = _TOGGLES[family]
        config = rule_owners.config
        update = getattr(getattr(rule_owners.network_client, endpoint), method)
        before = config.data
        before_section = before[section]
        before_rule = before_section[SITE_ID]["rule1"]
        # The refresh updates the data root in place, so capture what the
        # overlay replaced: the root identity and the section map.
        seen: list[tuple[dict[str, Any], dict[str, Any]]] = []
        config.async_add_listener(
            lambda: seen.append((config.data, config.data[section]))
        )
        facade_seen: list[Any] = []
        rule_owners.facade.async_add_listener(
            lambda: facade_seen.append(
                rule_owners.facade.data[section][SITE_ID]["rule1"]["enabled"]
            )
        )
        refreshes = rule_owners.network_client.sites.get_all.await_count

        await getattr(config, command)(SITE_ID, "rule1", enabled=True)

        update.assert_awaited_once_with(site_name or SITE_ID, "rule1", enabled=True)
        overlay_root, overlay = seen[0]
        assert overlay[SITE_ID]["rule1"]["enabled"] is True
        assert overlay_root is not before
        assert overlay is not before_section
        assert overlay[SITE_ID] is not before_section[SITE_ID]
        # The retained snapshot keeps its contents.
        assert before_rule["enabled"] is False
        assert facade_seen[0] is True
        assert rule_owners.network_client.sites.get_all.await_count == refreshes + 1
        assert config._rule_refresh_retries == {}

    async def test_unknown_rule_gets_no_overlay(
        self, rule_owners: SeededOwners
    ) -> None:
        """A rule missing from the data is written without an overlay."""
        config = rule_owners.config
        seen: list[dict[str, Any]] = []
        config.async_add_listener(lambda: seen.append(config.data["firewall_rules"]))

        await config.async_set_firewall_rule_enabled(SITE_ID, "other", enabled=True)

        rule_owners.network_client.firewall.update_rule.assert_awaited_once_with(
            SITE_ID, "other", enabled=True
        )
        assert all("other" not in rules[SITE_ID] for rules in seen)

    @pytest.mark.parametrize("family", ["firewall", "route", "vpn"])
    async def test_pass_through_failure_is_translated(
        self, rule_owners: SeededOwners, family: str
    ) -> None:
        """A failed firewall, route or VPN write keeps its message and cause."""
        command, section, endpoint, method, _ = _TOGGLES[family]
        update = getattr(getattr(rule_owners.network_client, endpoint), method)
        cause = UniFiConnectionError("down")
        update.side_effect = cause
        refreshes = rule_owners.network_client.sites.get_all.await_count

        with pytest.raises(HomeAssistantError, match=r"^Unable to update ") as caught:
            await getattr(rule_owners.config, command)(SITE_ID, "rule1", enabled=True)

        assert caught.value.translation_key == "command_failed"
        assert caught.value.__cause__ is cause
        assert rule_owners.config.data[section][SITE_ID]["rule1"]["enabled"] is False
        assert rule_owners.network_client.sites.get_all.await_count == refreshes

    @pytest.mark.parametrize("family", _LEGACY_RULES)
    @pytest.mark.parametrize(
        ("error", "translation_key", "refreshed"),
        [
            (UniFiNotFoundError("gone"), "network_rule_not_found", True),
            (
                UniFiRuleNotEditableError("managed"),
                "network_rule_console_managed",
                True,
            ),
            (RuntimeError("boom"), "network_rule_update_failed", False),
        ],
    )
    async def test_legacy_rule_failures_are_translated(
        self,
        rule_owners: SeededOwners,
        family: str,
        error: Exception,
        translation_key: str,
        *,
        refreshed: bool,
    ) -> None:
        """Rule failures are translated; gone or managed rules refresh first."""
        command, section, endpoint, method, _ = _TOGGLES[family]
        update = getattr(getattr(rule_owners.network_client, endpoint), method)
        update.side_effect = error
        refreshes = rule_owners.network_client.sites.get_all.await_count

        with pytest.raises(HomeAssistantError) as caught:
            await getattr(rule_owners.config, command)(SITE_ID, "rule1", enabled=True)

        assert caught.value.translation_domain == DOMAIN
        assert caught.value.translation_key == translation_key
        assert caught.value.translation_placeholders == {
            "rule_type": family,
            "rule_name": rule_owners.config.data[section][SITE_ID]["rule1"].get("name")
            or rule_owners.config.data[section][SITE_ID]["rule1"]["description"],
        }
        assert caught.value.__cause__ is error
        assert rule_owners.network_client.sites.get_all.await_count == (
            refreshes + refreshed
        )
        # Port forwards and traffic rules keep their English message.
        if family in ("port forward", "traffic rule"):
            assert str(caught.value).startswith(f"Unable to update {family} ")

    async def test_legacy_rule_passes_translated_errors_through(
        self, rule_owners: SeededOwners
    ) -> None:
        """An owner's translated error is not re-wrapped."""
        own = HomeAssistantError("own")
        rule_owners.network_client.nat_rules.update_nat_rule.side_effect = own

        with pytest.raises(HomeAssistantError) as caught:
            await rule_owners.config.async_set_nat_rule_enabled(
                SITE_ID, "rule1", enabled=True
            )

        assert caught.value is own

    @pytest.mark.parametrize("family", _LEGACY_RULES)
    async def test_legacy_rules_need_a_verified_site_name(
        self, rule_owners: SeededOwners, family: str
    ) -> None:
        """Rule families never write without a verified classic name."""
        command, _, endpoint, method, _ = _TOGGLES[family]
        config = rule_owners.config
        del config.data["sites"][SITE_ID]["internalReference"]
        update = getattr(getattr(rule_owners.network_client, endpoint), method)

        with pytest.raises(HomeAssistantError) as caught:
            await getattr(config, command)(SITE_ID, "rule1", enabled=True)

        _assert_legacy_not_found(caught.value, SITE_ID)
        update.assert_not_awaited()


class TestRuleReconciliation:
    """A failed refresh never fails a successful write."""

    async def test_failed_refresh_keeps_the_write_and_retries_once(
        self, hass: HomeAssistant, rule_owners: SeededOwners
    ) -> None:
        """A recorded refresh failure is logged; one retry follows later."""
        config = rule_owners.config
        sites = rule_owners.network_client.sites.get_all
        sites.side_effect = UniFiConnectionError("down")

        await config.async_set_port_forward_enabled(SITE_ID, "rule1", enabled=True)

        assert config.last_update_success is False
        assert set(config._rule_refresh_retries) == {("port_forwards", SITE_ID)}
        attempts = sites.await_count
        sites.side_effect = None

        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=RULE_REFRESH_RETRY_DELAY + 1)
        )
        await hass.async_block_till_done()

        assert sites.await_count == attempts + 1
        assert config.last_update_success is True
        assert config._rule_refresh_retries == {}

        # The retry does not reschedule itself.
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=3 * RULE_REFRESH_RETRY_DELAY)
        )
        await hass.async_block_till_done()
        assert sites.await_count == attempts + 1

    async def test_unavailable_family_schedules_one_retry(
        self, rule_owners: SeededOwners
    ) -> None:
        """A refresh that cannot read the family schedules a retry for it."""
        config = rule_owners.config
        lister = rule_owners.network_client.firewall.list_rules
        lister.side_effect = UniFiConnectionError("busy")

        await config.async_set_firewall_rule_enabled(SITE_ID, "rule1", enabled=True)

        assert config.last_update_success is True
        assert not config.firewall_available(SITE_ID)
        assert set(config._rule_refresh_retries) == {("firewall_rules", SITE_ID)}

    @pytest.mark.parametrize(
        ("command", "section", "endpoint", "lister"),
        [
            (
                "async_set_policy_based_route_enabled",
                "policy_based_routes",
                "routes",
                "list_routes",
            ),
            (
                "async_set_vpn_client_enabled",
                "vpn_clients",
                "vpn_clients",
                "list_vpn_clients",
            ),
        ],
    )
    async def test_missing_record_after_refresh_schedules_a_retry(
        self,
        rule_owners: SeededOwners,
        command: str,
        section: str,
        endpoint: str,
        lister: str,
    ) -> None:
        """A family that reports a failed read as empty still gets a retry."""
        config = rule_owners.config
        getattr(
            getattr(rule_owners.network_client, endpoint), lister
        ).side_effect = UniFiConnectionError("busy")

        await getattr(config, command)(SITE_ID, "rule1", enabled=True)

        assert config.last_update_success is True
        assert "rule1" not in config.data[section].get(SITE_ID, {})
        assert set(config._rule_refresh_retries) == {(section, SITE_ID)}

    async def test_present_record_after_refresh_schedules_no_retry(
        self, rule_owners: SeededOwners
    ) -> None:
        """A refresh that reads the written rule back needs no retry."""
        config = rule_owners.config

        await config.async_set_firewall_rule_enabled(SITE_ID, "rule1", enabled=True)

        assert config._rule_refresh_retries == {}

    async def test_a_new_retry_replaces_the_pending_one(
        self, rule_owners: SeededOwners
    ) -> None:
        """Two writes to the same family and site leave one pending retry."""
        config = rule_owners.config
        rule_owners.network_client.sites.get_all.side_effect = UniFiConnectionError(
            "down"
        )

        await config.async_set_nat_rule_enabled(SITE_ID, "rule1", enabled=True)
        first = config._rule_refresh_retries[("nat_rules", SITE_ID)]
        await config.async_set_nat_rule_enabled(SITE_ID, "rule1", enabled=False)

        assert config._rule_refresh_retries[("nat_rules", SITE_ID)] is not first
        assert len(config._rule_refresh_retries) == 1

    async def test_shutdown_cancels_pending_retries(
        self, hass: HomeAssistant, rule_owners: SeededOwners
    ) -> None:
        """Pending retries never run after the owner shuts down."""
        config = rule_owners.config
        sites = rule_owners.network_client.sites.get_all
        sites.side_effect = UniFiConnectionError("down")
        await config.async_set_static_route_enabled(SITE_ID, "rule1", enabled=True)
        attempts = sites.await_count

        await config.async_shutdown()
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=RULE_REFRESH_RETRY_DELAY + 1)
        )
        await hass.async_block_till_done()

        assert config._rule_refresh_retries == {}
        assert sites.await_count == attempts


class TestWifiAndGuestCommands:
    """Wi-Fi requests a deferred refresh; guest authorisation does not."""

    async def test_wifi_update_requests_a_refresh(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A Wi-Fi toggle writes and then asks the owner to refresh."""
        client = seeded_owners.network_client
        refreshes = client.sites.get_all.await_count

        await seeded_owners.config.async_update_wifi_network(
            SITE_ID, "wifi1", enabled=False
        )

        client.wifi.update.assert_awaited_once_with(SITE_ID, "wifi1", enabled=False)
        assert client.sites.get_all.await_count == refreshes + 1

    async def test_wifi_failure_is_translated(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A failed Wi-Fi write raises command_failed without refreshing."""
        client = seeded_owners.network_client
        client.wifi.update.side_effect = UniFiConnectionError("down")
        refreshes = client.sites.get_all.await_count

        with pytest.raises(HomeAssistantError, match="Unable to update WiFi") as err:
            await seeded_owners.config.async_update_wifi_network(
                SITE_ID, "wifi1", enabled=True
            )

        assert err.value.translation_key == "command_failed"
        assert client.sites.get_all.await_count == refreshes

    @pytest.mark.parametrize("command", ["authorize_guest", "unauthorize_guest"])
    async def test_guest_commands_call_the_actions_endpoint(
        self, seeded_owners: SeededOwners, command: str
    ) -> None:
        """Guest authorisation uses integration IDs and never refreshes."""
        client = seeded_owners.network_client
        refreshes = client.sites.get_all.await_count

        await getattr(seeded_owners.config, f"async_{command}")(SITE_ID, "client1")

        getattr(client.clients, command).assert_awaited_once_with(SITE_ID, "client1")
        assert client.sites.get_all.await_count == refreshes

    async def test_guest_failure_is_translated(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A failed guest authorisation keeps the API error as its cause."""
        cause = UniFiConnectionError("down")
        seeded_owners.network_client.clients.authorize_guest.side_effect = cause

        with pytest.raises(HomeAssistantError) as caught:
            await seeded_owners.config.async_authorize_guest(SITE_ID, "client1")

        assert caught.value.translation_key == "command_failed"
        assert caught.value.__cause__ is cause


@pytest.fixture
async def unmapped_owners(hass: HomeAssistant) -> AsyncGenerator[SeededOwners]:
    """Owners whose site has no internalReference and whose first lookup failed."""
    owners = build_seeded_owners(hass)
    client = owners.network_client
    client.sites.get_all.return_value[0].pop("internalReference")
    client.sites.get_legacy_all.side_effect = UniFiConnectionError("down")
    await async_refresh_owners(owners)
    assert owners.config.data["legacy_mapping_failed"] is True
    assert owners.config.data["legacy_site_names"] == {}
    yield owners
    await async_shutdown_owners(owners)


class TestOnDemandLegacyRemap:
    """Mapped writes do not wait for the next poll after a failed lookup."""

    async def test_write_succeeds_after_on_demand_remap(
        self, unmapped_owners: SeededOwners
    ) -> None:
        """A failed first lookup is retried once when a mapped write needs it."""
        config = unmapped_owners.config
        lookup = unmapped_owners.network_client.sites.get_legacy_all
        lookup.side_effect = None
        lookup.return_value = [{"name": "default", "desc": "Default Site"}]

        await config.async_set_policy_based_route_enabled(
            SITE_ID, "route1", enabled=True
        )

        unmapped_owners.network_client.routes.update_route.assert_awaited_once_with(
            "default", "route1", enabled=True
        )
        assert lookup.await_count >= 2
        assert config.data["legacy_mapping_failed"] is False

    async def test_endpoint_still_down_raises_legacy_site_not_found(
        self, unmapped_owners: SeededOwners
    ) -> None:
        """When the remap fails too, the write raises instead of guessing."""
        lookup = unmapped_owners.network_client.sites.get_legacy_all
        attempts = lookup.await_count

        with pytest.raises(HomeAssistantError) as caught:
            await unmapped_owners.config.async_set_vpn_client_enabled(
                SITE_ID, "vpn1", enabled=True
            )

        _assert_legacy_not_found(caught.value, SITE_ID)
        assert lookup.await_count == attempts + 1
        unmapped_owners.network_client.vpn_clients.update_vpn_client.assert_not_awaited()

    async def test_concurrent_writes_share_one_remap(
        self, unmapped_owners: SeededOwners
    ) -> None:
        """Writes that need the mapping at the same time trigger one lookup."""
        lookup = unmapped_owners.network_client.sites.get_legacy_all
        attempts = lookup.await_count
        release = asyncio.Event()

        async def _slow_lookup() -> list[dict[str, str]]:
            await release.wait()
            return [{"name": "default", "desc": "Default Site"}]

        lookup.side_effect = _slow_lookup
        config = unmapped_owners.config
        writes = asyncio.gather(
            config.async_resolve_legacy_site_name(SITE_ID, verified_only=False),
            config.async_resolve_legacy_site_name(SITE_ID, verified_only=False),
            config.async_resolve_legacy_site_name(SITE_ID, verified_only=False),
        )
        await asyncio.sleep(0)
        release.set()

        assert await writes == ["default", "default", "default"]
        assert lookup.await_count == attempts + 1

    async def test_remap_is_rate_limited(self, unmapped_owners: SeededOwners) -> None:
        """A down classic API is asked at most once per cooldown."""
        config = unmapped_owners.config
        lookup = unmapped_owners.network_client.sites.get_legacy_all
        attempts = lookup.await_count

        for _ in range(3):
            with pytest.raises(HomeAssistantError):
                await config.async_resolve_legacy_site_name(
                    SITE_ID, verified_only=False
                )
        assert lookup.await_count == attempts + 1

        assert config._legacy_remap_at is not None
        config._legacy_remap_at -= LEGACY_REMAP_COOLDOWN
        with pytest.raises(HomeAssistantError):
            await config.async_resolve_legacy_site_name(SITE_ID, verified_only=False)
        assert lookup.await_count == attempts + 2

    async def test_verified_only_and_unknown_sites_never_remap(
        self, unmapped_owners: SeededOwners
    ) -> None:
        """Remapping cannot help verified-only writes or unknown sites."""
        config = unmapped_owners.config
        lookup = unmapped_owners.network_client.sites.get_legacy_all
        attempts = lookup.await_count

        for site_id, verified_only in ((SITE_ID, True), ("missing", False)):
            with pytest.raises(HomeAssistantError) as caught:
                await config.async_resolve_legacy_site_name(
                    site_id, verified_only=verified_only
                )
            _assert_legacy_not_found(caught.value, site_id)

        assert lookup.await_count == attempts

    async def test_a_published_mapping_needs_no_remap(
        self, seeded_owners: SeededOwners
    ) -> None:
        """A healthy mapping that lacks a usable name is not looked up again."""
        config = seeded_owners.config
        del config.data["sites"][SITE_ID]["internalReference"]
        config.data["legacy_site_names"] = {SITE_ID: " "}
        lookup = seeded_owners.network_client.sites.get_legacy_all
        attempts = lookup.await_count

        with pytest.raises(HomeAssistantError):
            await config.async_resolve_legacy_site_name(SITE_ID, verified_only=False)

        assert lookup.await_count == attempts


# --- Protect-only polling ----------------------------------------------------

# Tests for Protect-only console polling and warning suppression.


def _create_site_listing_client() -> MagicMock:
    """Create a network client whose site listing can be asserted on."""
    client = MagicMock()
    client.sites = MagicMock()
    client.sites.get_all = AsyncMock(return_value=[])
    return client


class TestProtectOnlyPollingBehavior:
    """Test Protect-only consoles avoid repeating Network site polling."""

    @pytest.mark.asyncio
    async def test_config_coordinator_skips_sites_when_network_unavailable(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Test config coordinator skips sites.get_all when network unavailable."""
        network_client = _create_site_listing_client()

        coordinator = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=MagicMock(),
            entry=mock_config_entry,
            network_available=False,
        )
        assert coordinator._network_available is False

        data = await coordinator._async_update_data()

        assert data["sites"] == {}
        network_client.sites.get_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_config_coordinator_marks_network_unavailable_on_non_json(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Test sites.get_all non-JSON error marks network unavailable."""
        network_client = _create_site_listing_client()
        network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError("Response is not JSON", status_code=200)
        )

        coordinator = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=MagicMock(),
            entry=mock_config_entry,
            network_available=True,
        )

        data = await coordinator._async_update_data()

        assert coordinator._network_available is False
        assert data["sites"] == {}


# --- WiFi QR payloads and legacy site names ----------------------------------

# Unit tests for WiFi QR payloads and per-SSID counts.
#
# Covers the pure-logic helpers behind issues #49 and #52, none of which
# require a running Home Assistant instance.


class TestWifiQrPayload:
    """Tests for the WiFi QR connect-string builder (#49)."""

    def test_wpa_payload(self) -> None:
        payload = UnifiInsightsConfigCoordinator._wifi_qr_payload(
            "Home", "secret", "wpapsk", hidden=False
        )
        assert payload == "WIFI:T:WPA;S:Home;P:secret;;"

    def test_open_network_has_nopass(self) -> None:
        payload = UnifiInsightsConfigCoordinator._wifi_qr_payload(
            "Guest", None, "open", hidden=False
        )
        assert payload == "WIFI:T:nopass;S:Guest;;"

    def test_hidden_flag_and_escaping(self) -> None:
        payload = UnifiInsightsConfigCoordinator._wifi_qr_payload(
            "My;Net", "pa:ss", "wpapsk", hidden=True
        )
        # ';' and ':' inside values must be backslash-escaped, and H:true added.
        assert payload == r"WIFI:T:WPA;S:My\;Net;P:pa\:ss;H:true;;"

    def test_wep_payload(self) -> None:
        payload = UnifiInsightsConfigCoordinator._wifi_qr_payload(
            "Old", "k", "wep", hidden=False
        )
        assert payload.startswith("WIFI:T:WEP;")


class TestEnrichWifi:
    """Tests for WiFi enrichment with secrets + per-SSID counts (#49)."""

    def test_enrich_adds_counts_and_qr(self) -> None:
        wifi_dict = {"w1": {"id": "w1", "name": "Home"}}
        legacy = [{"name": "Home", "x_passphrase": "secret", "security": "wpapsk"}]
        active = [
            {"essid": "Home", "is_wired": False},
            {"essid": "Home", "is_wired": False},
            {"essid": "Home", "is_wired": True},  # wired excluded
            {"essid": "Other", "is_wired": False},
        ]

        UnifiInsightsConfigCoordinator._enrich_wifi(wifi_dict, legacy, active)

        wifi = wifi_dict["w1"]
        assert wifi["num_connected_clients"] == 2
        assert wifi["passphrase"] == legacy[0]["x_passphrase"]
        assert wifi["qr_code"] == "WIFI:T:WPA;S:Home;P:secret;;"

    def test_enrich_without_matching_config(self) -> None:
        wifi_dict = {"w1": {"id": "w1", "name": "Home"}}
        UnifiInsightsConfigCoordinator._enrich_wifi(wifi_dict, [], [])
        # No secrets available, but the count key is still populated.
        assert wifi_dict["w1"]["num_connected_clients"] == 0
        assert "qr_code" not in wifi_dict["w1"]


class TestMapLegacySiteNames:
    """Tests for classic site-name resolution (#44/#49)."""

    def test_single_site_falls_back_to_only_legacy(self) -> None:
        mapping = UnifiInsightsConfigCoordinator._map_legacy_site_names(
            {"uuid-1": {"name": "My Home"}},
            [{"name": "default", "desc": "Default"}],
        )
        assert mapping == {"uuid-1": "default"}

    def test_match_by_description(self) -> None:
        mapping = UnifiInsightsConfigCoordinator._map_legacy_site_names(
            {"uuid-1": {"name": "HQ"}, "uuid-2": {"name": "Branch"}},
            [
                {"name": "default", "desc": "HQ"},
                {"name": "site2", "desc": "Branch"},
            ],
        )
        assert mapping == {"uuid-1": "default", "uuid-2": "site2"}


class TestTolerantProtectEnums:
    """Tests for resilient Protect enum parsing (#52)."""

    def test_camera_unknown_state_does_not_raise(self) -> None:
        camera = Camera.model_validate(
            {"id": "c1", "mac": "AA", "state": "SOME_NEW_71_STATE"}
        )
        assert camera.state is CameraState.UNKNOWN

    def test_camera_known_state(self) -> None:
        camera = Camera.model_validate({"id": "c1", "mac": "AA", "state": "CONNECTED"})
        assert camera.state is CameraState.CONNECTED

    def test_viewer_unknown_state_does_not_raise(self) -> None:
        viewer = Viewer.model_validate({"id": "v1", "mac": "BB", "state": "WEIRD"})
        assert viewer.state is ViewerState.UNKNOWN

    def test_viewer_known_state(self) -> None:
        viewer = Viewer.model_validate({"id": "v1", "mac": "BB", "state": "CONNECTED"})
        assert viewer.state is ViewerState.CONNECTED


# --- Config coordinator ------------------------------------------------------


class TestUnifiConfigCoordinator:
    """Tests for UnifiInsightsConfigCoordinator."""

    @pytest.fixture
    def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> UnifiInsightsConfigCoordinator:
        """Create a config coordinator for testing."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        return UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
        )

    def test_initialization(self, coordinator: UnifiInsightsConfigCoordinator):
        """Test coordinator initialization."""
        assert coordinator.name == f"{DOMAIN}_config"
        assert coordinator.update_interval == SCAN_INTERVAL_CONFIG
        assert "sites" in coordinator.data
        assert "wifi" in coordinator.data
        assert "firewall_rules" in coordinator.data
        assert "policy_based_routes" in coordinator.data
        assert "network_info" in coordinator.data

    @pytest.mark.asyncio
    async def test_async_update_data_success(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Test successful data fetch."""
        result = await coordinator._async_update_data()

        assert "sites" in result
        assert "default" in result["sites"]
        assert "site2" in result["sites"]
        assert "wifi" in result
        assert "firewall_rules" in result
        assert "rule1" in result["firewall_rules"]["default"]
        assert "policy_based_routes" in result
        assert "route1" in result["policy_based_routes"]["default"]
        assert coordinator._available is True
        assert coordinator.available_sites == {
            "default": "Default",
            "site2": "Site 2",
        }

    @pytest.mark.asyncio
    async def test_async_update_data_filters_selected_sites(self, hass: HomeAssistant):
        """Only the sites picked in options are polled (Issue #128)."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_API_KEY: "test_api_key"},
            options={CONF_SITE_IDS: ["site2"]},
        )
        network_client = _create_mock_network_client()
        coordinator = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=None,
            entry=entry,
        )

        result = await coordinator._async_update_data()

        assert list(result["sites"]) == ["site2"]
        assert coordinator.get_site_ids() == ["site2"]
        # Every site stays selectable in the options flow.
        assert coordinator.available_sites == {
            "default": "Default",
            "site2": "Site 2",
        }
        polled = [call.args[0] for call in network_client.wifi.get_all.await_args_list]
        assert polled == ["site2"]

    @pytest.mark.asyncio
    async def test_async_update_data_prunes_per_site_maps(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Per-site config data for a site no longer polled is dropped."""
        for key in ("wifi", "firewall_rules", "policy_based_routes", "vpn_clients"):
            coordinator.data[key]["gone"] = {"stale": {"id": "stale"}}

        result = await coordinator._async_update_data()

        for key in ("wifi", "firewall_rules", "policy_based_routes", "vpn_clients"):
            assert "gone" not in result[key]
            assert "default" in result[key]

    @pytest.mark.asyncio
    async def test_async_update_data_selected_sites_all_gone(
        self, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
    ):
        """A selection matching no current site polls nothing and says why."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_API_KEY: "test_api_key"},
            options={CONF_SITE_IDS: ["removed-site"]},
        )
        coordinator = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=_create_mock_network_client(),
            protect_client=None,
            entry=entry,
        )

        with caplog.at_level(logging.WARNING):
            result = await coordinator._async_update_data()
            await coordinator._async_update_data()

        assert result["sites"] == {}
        # Warned once, not on every 5-minute poll.
        assert caplog.text.count("none of the selected sites") == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", ["wifi.get_all", "firewall.list_rules"])
    @pytest.mark.parametrize(
        "error",
        [
            UniFiNotFoundError("Not found", status_code=404),
            UniFiResponseError("Bad request", status_code=400),
            UniFiAuthenticationError("Forbidden", status_code=403),
            UniFiResponseError("HTML instead of JSON", status_code=200),
        ],
        ids=["404", "400", "403", "200-non-json"],
    )
    async def test_async_update_data_optional_endpoint_unsupported(
        self,
        coordinator: UnifiInsightsConfigCoordinator,
        endpoint: str,
        error: Exception,
    ):
        """A 4xx from WiFi/firewall means the feature is absent, not a failure."""
        namespace, method = endpoint.split(".")
        setattr(
            getattr(coordinator.network_client, namespace),
            method,
            AsyncMock(side_effect=error),
        )

        result = await coordinator._async_update_data()

        section = "wifi" if namespace == "wifi" else "firewall_rules"
        assert result[section]["default"] == {}
        assert "default" in result["sites"]
        assert coordinator._available is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("endpoint", "section", "other_section"),
        [
            ("wifi.get_all", "wifi", "firewall_rules"),
            ("firewall.list_rules", "firewall_rules", "wifi"),
        ],
        ids=["wifi", "firewall"],
    )
    @pytest.mark.parametrize(
        "error",
        [
            UniFiResponseError("Server error", status_code=500),
            UniFiRateLimitError("Slow down", status_code=429),
            UniFiConnectionError("Connection refused"),
            UniFiTimeoutError("Timed out"),
            ValueError("Unparseable payload"),
        ],
        ids=["500", "429", "connection", "timeout", "unexpected"],
    )
    async def test_async_update_data_optional_section_failure(
        self,
        coordinator: UnifiInsightsConfigCoordinator,
        endpoint: str,
        section: str,
        other_section: str,
        error: Exception,
    ):
        """A failed WiFi/firewall fetch keeps that section's data and flags it."""
        await coordinator.async_refresh()
        assert coordinator.wifi_available("default") is True
        assert coordinator.firewall_available("default") is True
        previous = copy.deepcopy(coordinator.data[section])
        assert previous["default"]

        namespace, method = endpoint.split(".")
        working = getattr(getattr(coordinator.network_client, namespace), method)
        setattr(
            getattr(coordinator.network_client, namespace),
            method,
            AsyncMock(side_effect=error),
        )

        await coordinator.async_refresh()

        # The refresh itself succeeds so other sections and platforms keep
        # working, but the failed section is not reported as fresh.
        assert coordinator.last_update_success is True
        assert coordinator.data[section] == previous
        section_flag = {
            "wifi": coordinator.wifi_available,
            "firewall_rules": coordinator.firewall_available,
        }
        assert section_flag[section]("default") is False
        assert section_flag[other_section]("default") is True

        setattr(getattr(coordinator.network_client, namespace), method, working)
        await coordinator.async_refresh()
        assert section_flag[section]("default") is True

    @pytest.mark.asyncio
    async def test_optional_section_failure_is_per_site(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """A WiFi failure on one site leaves other sites' WiFi available."""
        working = coordinator.network_client.wifi.get_all

        async def get_all(site_id: str):
            if site_id == "site2":
                msg = "site2 WiFi down"
                raise UniFiConnectionError(msg)
            return await working(site_id)

        coordinator.network_client.wifi.get_all = AsyncMock(side_effect=get_all)
        await coordinator.async_refresh()

        assert coordinator.wifi_available("default") is True
        assert coordinator.wifi_available("site2") is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("endpoint", ["wifi.get_all", "firewall.list_rules"])
    async def test_async_update_data_optional_endpoint_auth_failure_reauths(
        self, coordinator: UnifiInsightsConfigCoordinator, endpoint: str
    ):
        """A 401 from WiFi/firewall means revoked credentials and starts reauth."""
        namespace, method = endpoint.split(".")
        setattr(
            getattr(coordinator.network_client, namespace),
            method,
            AsyncMock(side_effect=UniFiAuthenticationError("Revoked", status_code=401)),
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

        assert coordinator._available is False

    @pytest.mark.asyncio
    async def test_async_update_data_enriches_wifi_and_collects_sections(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Legacy WiFi enrichment, VPN clients and id-less rules on a good poll."""
        client = coordinator.network_client
        client.wifi.get_legacy_configs = AsyncMock(
            return_value=[
                {"name": "MainWiFi", "x_passphrase": "secret", "security": "wpapsk"}
            ]
        )
        client.clients.get_active_legacy = AsyncMock(
            return_value=[
                {"essid": "MainWiFi"},
                {"essid": "MainWiFi", "is_wired": True},
            ]
        )
        client.vpn_clients.list_vpn_clients = AsyncMock(
            return_value=[{"_id": "vpn1", "name": "Office"}, {"name": "no id"}]
        )
        client.firewall.list_rules = AsyncMock(
            return_value=[_create_mock_model({"name": "rule without id"})]
        )

        result = await coordinator._async_update_data()

        wifi = result["wifi"]["default"]["wifi1"]
        assert wifi["num_connected_clients"] == 1
        assert wifi["qr_code"].startswith("WIFI:T:WPA;S:MainWiFi;P:")
        assert list(result["vpn_clients"]["default"]) == ["vpn1"]
        assert result["firewall_rules"]["default"] == {}
        assert coordinator.firewall_available("default") is True

    @pytest.mark.asyncio
    async def test_async_update_data_drops_sites_that_disappeared(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Per-site sections are rebuilt, so a removed site's data is not kept."""
        coordinator.data["wifi"]["gone"] = {"wifi-x": {"id": "wifi-x"}}
        coordinator.data["firewall_rules"]["gone"] = {"rule-x": {"id": "rule-x"}}

        result = await coordinator._async_update_data()

        assert "gone" not in result["wifi"]
        assert "gone" not in result["firewall_rules"]

    @pytest.mark.asyncio
    async def test_async_update_data_routes_error(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test policy-based routes are optional when the endpoint is unavailable."""
        coordinator.network_client.routes.list_routes = AsyncMock(
            side_effect=Exception("Routes endpoint unavailable")
        )

        result = await coordinator._async_update_data()

        assert "sites" in result
        assert "default" in result["sites"]
        assert result["policy_based_routes"]["default"] == {}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_routes_auth_error(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test auth error during routes fetch triggers reauth."""
        coordinator.protect_client = None
        coordinator.network_client.routes.list_routes = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid API key")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_vpn_clients_error(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test VPN clients are optional when the endpoint is unavailable."""
        coordinator.network_client.vpn_clients.list_vpn_clients = AsyncMock(
            side_effect=Exception("VPN clients endpoint unavailable")
        )

        result = await coordinator._async_update_data()

        assert "sites" in result
        assert "default" in result["sites"]
        assert result["vpn_clients"]["default"] == {}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_stores_site_to_site_vpns(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Configured site-to-site tunnels are stored per site by id."""
        tunnel = {
            "id": "tun1",
            "name": "Office",
            "vpn_type": "ipsec-vpn",
            "enabled": True,
        }
        coordinator.network_client.vpn_clients.list_site_to_site_vpns = AsyncMock(
            return_value=[tunnel]
        )

        result = await coordinator._async_update_data()

        assert result["site_vpns"]["default"] == {"tun1": tunnel}

    @pytest.mark.asyncio
    async def test_async_update_data_site_to_site_vpns_error_keeps_last_known(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """A failed tunnel fetch keeps the last known tunnels, not an empty set."""
        tunnel = {
            "id": "tun1",
            "name": "Office",
            "vpn_type": "ipsec-vpn",
            "enabled": True,
        }
        coordinator.network_client.vpn_clients.list_site_to_site_vpns = AsyncMock(
            return_value=[tunnel]
        )
        await coordinator._async_update_data()

        coordinator.network_client.vpn_clients.list_site_to_site_vpns = AsyncMock(
            side_effect=Exception("networkconf unavailable")
        )
        result = await coordinator._async_update_data()

        assert result["site_vpns"]["default"] == {"tun1": tunnel}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_site_mapping_error_keeps_site_vpns(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """A failed legacy site mapping keeps the last known tunnels."""
        tunnel = {
            "id": "tun1",
            "name": "Office",
            "vpn_type": "ipsec-vpn",
            "enabled": True,
        }
        coordinator.network_client.vpn_clients.list_site_to_site_vpns = AsyncMock(
            return_value=[tunnel]
        )
        await coordinator._async_update_data()

        coordinator.network_client.sites.get_legacy_all = AsyncMock(
            side_effect=Exception("legacy sites unavailable")
        )
        result = await coordinator._async_update_data()

        assert result["site_vpns"]["default"] == {"tun1": tunnel}

    @pytest.mark.asyncio
    async def test_async_update_data_site_to_site_vpns_auth_error(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """An auth error on the tunnel fetch triggers reauth, not last-known."""
        coordinator.network_client.vpn_clients.list_site_to_site_vpns = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid API key")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_vpn_clients_auth_error(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test auth error during VPN clients fetch triggers reauth."""
        coordinator.protect_client = None
        coordinator.network_client.vpn_clients.list_vpn_clients = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid API key")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_auth_error(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Test data fetch with auth error on network-only setup."""
        coordinator.protect_client = None
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiAuthenticationError("Invalid API key")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_protect_only_console(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Test fetch succeeds on Protect-only console with empty sites (Issue 93)."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )

        result = await coordinator._async_update_data()
        assert result["sites"] == {}
        assert result["wifi"] == {}
        assert result["firewall_rules"] == {}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_protect_only_console_non_json_sites(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test fetch succeeds when the sites endpoint returns 200 and no JSON."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError(
                "API returned non-JSON response (status 200)", status_code=200
            )
        )

        result = await coordinator._async_update_data()
        assert result["sites"] == {}
        assert result["wifi"] == {}
        assert result["firewall_rules"] == {}
        assert coordinator._available is True

    @pytest.mark.asyncio
    async def test_async_update_data_non_json_sites_without_protect(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test a non-JSON sites response still fails when Protect is absent."""
        coordinator.protect_client = None
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError(
                "API returned non-JSON response (status 200)", status_code=200
            )
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_redirect_sites_still_fails(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test only a 200 is tolerated, so a 3xx is not swallowed."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError("Redirected", status_code=302)
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_server_error_sites_still_fails(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test a >=400 response is not swallowed by the non-JSON tolerance."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError("Server error", status_code=500)
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_connection_error(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Test data fetch with connection error."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiConnectionError("Connection refused")
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_timeout_error(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Test data fetch with timeout error."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiTimeoutError("Request timed out")
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_response_error(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Test data fetch with response error."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError("Bad response", status_code=400)
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_async_update_data_generic_error(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Test data fetch with generic error."""
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=Exception("Something broke")
        )

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    def test_get_site_existing(self, coordinator: UnifiInsightsConfigCoordinator):
        """Test getting existing site."""
        coordinator.data["sites"] = {"default": {"id": "default", "name": "Default"}}
        result = coordinator.get_site("default")
        assert result == {"id": "default", "name": "Default"}

    def test_get_site_missing(self, coordinator: UnifiInsightsConfigCoordinator):
        """Test getting missing site."""
        result = coordinator.get_site("nonexistent")
        assert result is None

    def test_get_site_non_dict_entry(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test getting site when site entry in data is not a dict."""
        coordinator.data["sites"] = {"site1": "not a dict"}
        assert coordinator.get_site("site1") is None

    def test_get_site_ids(self, coordinator: UnifiInsightsConfigCoordinator):
        """Test getting all site IDs."""
        coordinator.data["sites"] = {
            "default": {"id": "default"},
            "site2": {"id": "site2"},
        }
        result = coordinator.get_site_ids()
        assert set(result) == {"default", "site2"}

    def test_get_wifi_networks(self, coordinator: UnifiInsightsConfigCoordinator):
        """Test getting WiFi networks for a site."""
        coordinator.data["wifi"] = {
            "default": {"wifi1": {"id": "wifi1", "name": "MainWiFi"}}
        }
        result = coordinator.get_wifi_networks("default")
        assert "wifi1" in result

    def test_get_wifi_networks_missing_site(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Test getting WiFi networks for missing site."""
        result = coordinator.get_wifi_networks("nonexistent")
        assert result == {}

    def test_get_firewall_rules(self, coordinator: UnifiInsightsConfigCoordinator):
        """Test getting firewall rules for a site."""
        coordinator.data["firewall_rules"] = {
            "default": {"rule1": {"id": "rule1", "name": "Block Instagram"}}
        }
        result = coordinator.get_firewall_rules("default")
        assert "rule1" in result

    def test_get_firewall_rules_missing_site(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Test getting firewall rules for missing site."""
        result = coordinator.get_firewall_rules("nonexistent")
        assert result == {}

    def test_get_policy_based_routes(self, coordinator: UnifiInsightsConfigCoordinator):
        """Test getting policy-based routes for a site."""
        coordinator.data["policy_based_routes"] = {
            "default": {"route1": {"id": "route1", "description": "Route to VPN"}}
        }
        result = coordinator.get_policy_based_routes("default")
        assert "route1" in result

    def test_get_policy_based_routes_missing_site(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test getting policy-based routes for missing site."""
        result = coordinator.get_policy_based_routes("nonexistent")
        assert result == {}

    def test_get_vpn_clients(self, coordinator: UnifiInsightsConfigCoordinator) -> None:
        """Test getting VPN clients for a site."""
        coordinator.data["vpn_clients"] = {
            "default": {"client1": {"_id": "client1", "name": "Privado VPN"}}
        }
        result = coordinator.get_vpn_clients("default")
        assert "client1" in result

    def test_get_vpn_clients_missing_site(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test getting VPN clients for missing site."""
        result = coordinator.get_vpn_clients("nonexistent")
        assert result == {}

    @pytest.mark.asyncio
    async def test_async_update_data_skips_none_site_id(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Test that WiFi fetch skips None site IDs."""
        # Create a site model that returns None for id
        site_with_none_id = MagicMock()
        site_with_none_id.model_dump.return_value = {"id": None, "name": "BadSite"}

        site_valid = MagicMock()
        site_valid.model_dump.return_value = {"id": "valid_site", "name": "ValidSite"}

        coordinator.network_client.sites.get_all = AsyncMock(
            return_value=[site_with_none_id, site_valid]
        )

        result = await coordinator._async_update_data()

        # Should only have the valid site (None id is filtered out)
        assert "valid_site" in result["sites"]
        assert None not in result["sites"]

    @pytest.mark.asyncio
    async def test_async_update_data_skips_wifi_without_id(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Test that WiFi networks without an ID are skipped."""
        # Create WiFi models - one with ID, one without
        wifi_with_id = MagicMock()
        wifi_with_id.model_dump.return_value = {"id": "wifi1", "name": "ValidWiFi"}

        wifi_without_id = MagicMock()
        wifi_without_id.model_dump.return_value = {"id": None, "name": "BadWiFi"}

        coordinator.network_client.wifi.get_all = AsyncMock(
            return_value=[wifi_with_id, wifi_without_id]
        )

        result = await coordinator._async_update_data()

        # Should only have the WiFi with valid ID
        assert "wifi1" in result["wifi"].get("default", {})
        # WiFi without ID should not be in the dict
        assert None not in result["wifi"].get("default", {})

    def test_client_links_from_stat_sta(self):
        """Only link fields survive; MACs normalise; untagged has no vlan."""
        links = UnifiInsightsConfigCoordinator._client_links(
            [
                {
                    "mac": "8C:ED:E1:00:00:01",
                    "hostname": "cam",
                    "ip": "10.2.0.77",
                    "is_wired": True,
                    "sw_mac": "28:70:4E:00:00:01",
                    "sw_port": 14,
                    "vlan": 3,
                    "network": "Cameras",
                    "network_id": "legacy-net-1",
                },
                {
                    "mac": "12:00:00:00:00:02",
                    "is_wired": False,
                    "ap_mac": "02:00:00:00:00:08",
                    "network": "Default",
                },
                {
                    "mac": "aa:bb:cc:00:00:03",
                    "is_wired": True,
                    "sw_mac": "28:70:4e:00:00:01",
                    "sw_port": 5,
                    "vlan": 0,
                    "network": "Default",
                },
                {"mac": "not-a-mac"},
                "garbage",
            ]
        )

        assert links == {
            "8c:ed:e1:00:00:01": {
                "sw_mac": "28:70:4e:00:00:01",
                "sw_port": 14,
                "vlan": 3,
                "network_name": "Cameras",
            },
            "12:00:00:00:00:02": {
                "ap_mac": "02:00:00:00:00:08",
                "network_name": "Default",
            },
            "aa:bb:cc:00:00:03": {
                "sw_mac": "28:70:4e:00:00:01",
                "sw_port": 5,
                "network_name": "Default",
            },
        }

    @pytest.mark.asyncio
    async def test_async_update_data_collects_client_links_without_wifi(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """Links are built even when the Wi-Fi section is unavailable."""
        client = coordinator.network_client
        client.wifi.get_all = AsyncMock(side_effect=RuntimeError("wifi down"))
        client.clients.get_active_legacy = AsyncMock(
            return_value=[
                {
                    "mac": "8c:ed:e1:00:00:01",
                    "sw_mac": "28:70:4e:00:00:01",
                    "sw_port": 3,
                }
            ]
        )

        result = await coordinator._async_update_data()

        assert result["client_links"]["default"] == {
            "8c:ed:e1:00:00:01": {"sw_mac": "28:70:4e:00:00:01", "sw_port": 3}
        }
        # The fixture polls two sites, so this call also runs for "site2";
        # count the "default" calls specifically to pin the spec's "one
        # /stat/sta call per site" invariant without over-specifying the
        # total call count across sites, which is incidental here.
        assert (
            client.clients.get_active_legacy.await_args_list.count(call("default")) == 1
        )

    @pytest.mark.asyncio
    async def test_async_update_data_keeps_client_links_on_failure(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """A failed /stat/sta keeps the previous links and does not fail."""
        coordinator.data["client_links"] = {
            "default": {"aa:aa:aa:aa:aa:aa": {"vlan": 2}}
        }
        coordinator.network_client.clients.get_active_legacy = AsyncMock(
            side_effect=RuntimeError("sta down")
        )

        result = await coordinator._async_update_data()

        assert result["client_links"]["default"] == {"aa:aa:aa:aa:aa:aa": {"vlan": 2}}

    @pytest.mark.asyncio
    async def test_async_update_data_wifi_enrichment_unchanged(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """The single /stat/sta call still feeds per-SSID client counts."""
        client = coordinator.network_client
        client.wifi.get_legacy_configs = AsyncMock(
            return_value=[{"name": "MainWiFi", "security": "wpapsk"}]
        )
        client.clients.get_active_legacy = AsyncMock(
            return_value=[{"essid": "MainWiFi", "mac": "12:00:00:00:00:02"}]
        )

        result = await coordinator._async_update_data()

        assert result["wifi"]["default"]["wifi1"]["num_connected_clients"] == 1
        # The fixture polls two sites, so this call also runs for "site2";
        # count the "default" calls specifically to pin the spec's "one
        # /stat/sta call per site" invariant without over-specifying the
        # total call count across sites, which is incidental here.
        assert (
            client.clients.get_active_legacy.await_args_list.count(call("default")) == 1
        )

    @pytest.mark.asyncio
    async def test_async_update_data_clears_client_links_with_no_sites(
        self, coordinator: UnifiInsightsConfigCoordinator
    ):
        """The no-sites early return blanks client_links like its siblings."""
        coordinator.data["client_links"] = {
            "default": {"aa:aa:aa:aa:aa:aa": {"vlan": 2}}
        }
        coordinator.network_client.sites.get_all = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )

        result = await coordinator._async_update_data()

        assert result["client_links"] == {}

    def test_initialization_has_rule_sections(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test coordinator initialization includes rule section dictionaries."""
        assert "port_forwards" in coordinator.data
        assert "traffic_rules" in coordinator.data
        assert coordinator.data["port_forwards"] == {}
        assert coordinator.data["traffic_rules"] == {}

    def test_initialization_has_nat_and_static_route_sections(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Test coordinator initialization includes NAT and static route sections."""
        assert "nat_rules" in coordinator.data
        assert "static_routes" in coordinator.data
        assert coordinator.data["nat_rules"] == {}
        assert coordinator.data["static_routes"] == {}

    @pytest.mark.parametrize(
        ("site_data", "expected"),
        [
            ({"internalReference": " branch "}, "branch"),
            ({"internalReference": ""}, None),
            ({"internalReference": "   "}, None),
            ({"name": "default"}, None),
            ({"internalReference": 123}, None),
            ("not-a-dict", None),
            (None, None),
        ],
    )
    def test_verified_legacy_site_name(
        self, site_data: Any, expected: str | None
    ) -> None:
        """Test verified_legacy_site_name extracts valid non-blank internalReference."""
        assert verified_legacy_site_name(site_data) == expected

    @pytest.mark.parametrize(
        ("rule_data", "name_field", "expected"),
        [
            ({"name": "  Plex  "}, "name", "Plex"),
            ({"description": " Kids bedtime "}, "description", "Kids bedtime"),
            ({"name": "   "}, "name", "pf1"),
            ({"name": 42}, "name", "pf1"),
            ({"description": "Plex"}, "name", "pf1"),
            ("not-a-dict", "name", "pf1"),
        ],
    )
    def test_rule_display_name(
        self, rule_data: Any, name_field: str, expected: str
    ) -> None:
        """Test rule_display_name strips the name and falls back to the rule id."""
        assert rule_display_name(rule_data, "pf1", name_field) == expected

    @pytest.mark.parametrize(
        ("rule_data", "expected"),
        [
            ({"is_predefined": True}, True),
            ({"is_predefined": True, "setting_preference": "manual"}, True),
            ({"setting_preference": "auto"}, True),
            ({"setting_preference": None}, True),
            ({"is_predefined": False, "setting_preference": "manual"}, False),
            ({}, False),
            ({"is_predefined": "true"}, False),
            ("not a dict", False),
        ],
    )
    def test_is_predefined_nat_rule(self, rule_data: Any, *, expected: bool) -> None:
        """Test is_predefined_nat_rule identifies controller-managed NAT rules."""
        assert is_predefined_nat_rule(rule_data) is expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        RULE_SECTION_PARAMS,
    )
    async def test_rule_section_fetched_by_internal_reference(
        self,
        coordinator: UnifiInsightsConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Rule sections are fetched with internalReference and keyed by id."""
        site1 = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        site2 = _create_mock_model(
            {"id": "site2", "name": "Site 2", "internalReference": "branch"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(
            return_value=[site1, site2]
        )

        record1 = {"_id": "rule-1", "name": "Rule 1", "enabled": True}
        record2 = {"id": "rule-2", "name": "Rule 2", "enabled": False}
        endpoint = getattr(coordinator.network_client, endpoint_attr)

        def _fetch_side_effect(ref: str) -> list[Any]:
            return (
                [_create_mock_model(record1), _create_mock_model({"name": "No id"})]
                if ref == "default"
                else [_create_mock_model(record2)]
            )

        setattr(endpoint, list_method, AsyncMock(side_effect=_fetch_side_effect))

        result = await coordinator._async_update_data()
        mock_fn = getattr(endpoint, list_method)
        assert [c.args[0] for c in mock_fn.await_args_list] == ["default", "branch"]
        assert result[data_key]["default"] == {"rule-1": record1}
        assert result[data_key]["site2"] == {"rule-2": record2}
        avail_fn = getattr(coordinator, avail_method)
        assert avail_fn("default") is True
        assert avail_fn("site2") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        RULE_SECTION_PARAMS,
    )
    async def test_rule_section_ignores_fuzzy_legacy_mapping(
        self,
        coordinator: UnifiInsightsConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Rule sections ignore fuzzy legacy mapping from get_legacy_all."""
        site1 = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        site2 = _create_mock_model(
            {"id": "site2", "name": "Site 2", "internalReference": "branch"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(
            return_value=[site1, site2]
        )
        coordinator.network_client.sites.get_legacy_all = AsyncMock(
            return_value=[{"name": "default", "desc": "Site 2"}]
        )
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(endpoint, list_method, AsyncMock(return_value=[]))

        await coordinator._async_update_data()
        mock_fn = getattr(endpoint, list_method)
        assert [c.args[0] for c in mock_fn.await_args_list] == ["default", "branch"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        RULE_SECTION_PARAMS,
    )
    async def test_rule_section_without_internal_reference_is_empty_and_available(
        self,
        coordinator: UnifiInsightsConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Without internalReference, fetch is skipped and data is empty."""
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        mock_fn = getattr(endpoint, list_method)

        result = await coordinator._async_update_data()
        mock_fn.assert_not_awaited()
        assert result[data_key]["default"] == {}
        assert result[data_key]["site2"] == {}
        avail_fn = getattr(coordinator, avail_method)
        assert avail_fn("default") is True
        assert avail_fn("site2") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        RULE_SECTION_PARAMS,
    )
    @pytest.mark.parametrize(
        "error",
        [
            UniFiAuthenticationError("Forbidden", status_code=403),
            UniFiNotFoundError("Not found", status_code=404),
            UniFiResponseError("Non-JSON body", status_code=200),
        ],
    )
    async def test_rule_section_unsupported_response_stores_empty(
        self,
        coordinator: UnifiInsightsConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
        *,
        error: Exception,
    ) -> None:
        """Unsupported responses (403/404/200 non-JSON) store {} and keep available."""
        site = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(return_value=[site])
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(endpoint, list_method, AsyncMock(side_effect=error))

        result = await coordinator._async_update_data()
        assert result[data_key]["default"] == {}
        avail_fn = getattr(coordinator, avail_method)
        assert avail_fn("default") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        RULE_SECTION_PARAMS,
    )
    async def test_rule_section_transient_failure_keeps_previous_data(
        self,
        coordinator: UnifiInsightsConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Transient failure preserves data and toggles available until recovery."""
        site = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(return_value=[site])
        rec = {"_id": "r1", "name": "R1", "enabled": True}
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(
            endpoint, list_method, AsyncMock(return_value=[_create_mock_model(rec)])
        )

        res1 = await coordinator._async_update_data()
        assert res1[data_key]["default"] == {"r1": rec}
        avail_fn = getattr(coordinator, avail_method)
        assert avail_fn("default") is True

        setattr(
            endpoint,
            list_method,
            AsyncMock(side_effect=UniFiConnectionError("Connection lost")),
        )
        res2 = await coordinator._async_update_data()
        assert res2[data_key]["default"] == {"r1": rec}
        assert avail_fn("default") is False

        setattr(
            endpoint, list_method, AsyncMock(return_value=[_create_mock_model(rec)])
        )
        res3 = await coordinator._async_update_data()
        assert res3[data_key]["default"] == {"r1": rec}
        assert avail_fn("default") is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        RULE_SECTION_PARAMS,
    )
    async def test_rule_section_failure_is_per_site(
        self,
        coordinator: UnifiInsightsConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Failure on site A does not affect availability on site B."""
        site_a = _create_mock_model(
            {"id": "site_a", "name": "Site A", "internalReference": "ref_a"}
        )
        site_b = _create_mock_model(
            {"id": "site_b", "name": "Site B", "internalReference": "ref_b"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(
            return_value=[site_a, site_b]
        )

        rec_b = {"_id": "rb", "name": "RB", "enabled": True}

        def _side_effect(ref: str) -> list[Any]:
            if ref == "ref_a":
                msg = "Failure on A"
                raise UniFiConnectionError(msg)
            return [_create_mock_model(rec_b)]

        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(endpoint, list_method, AsyncMock(side_effect=_side_effect))

        result = await coordinator._async_update_data()
        avail_fn = getattr(coordinator, avail_method)
        assert avail_fn("site_a") is False
        assert avail_fn("site_b") is True
        assert result[data_key]["site_b"] == {"rb": rec_b}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        RULE_SECTION_PARAMS,
    )
    async def test_rule_section_401_triggers_reauth(
        self,
        coordinator: UnifiInsightsConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """401 error during rule fetch triggers reauthentication."""
        site = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(return_value=[site])
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(
            endpoint,
            list_method,
            AsyncMock(
                side_effect=UniFiAuthenticationError("Unauthorized", status_code=401)
            ),
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("data_key", "endpoint_attr", "list_method", "avail_method"),
        RULE_SECTION_PARAMS,
    )
    async def test_rule_sections_pruned_with_sites(
        self,
        coordinator: UnifiInsightsConfigCoordinator,
        data_key: str,
        endpoint_attr: str,
        list_method: str,
        avail_method: str,
    ) -> None:
        """Removed sites are pruned from rule sections, leaving empty dict."""
        site1 = _create_mock_model(
            {"id": "s1", "name": "S1", "internalReference": "r1"}
        )
        site2 = _create_mock_model(
            {"id": "s2", "name": "S2", "internalReference": "r2"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(
            return_value=[site1, site2]
        )
        endpoint = getattr(coordinator.network_client, endpoint_attr)
        setattr(endpoint, list_method, AsyncMock(return_value=[]))

        res1 = await coordinator._async_update_data()
        assert "s1" in res1[data_key]
        assert "s2" in res1[data_key]

        coordinator.network_client.sites.get_all = AsyncMock(return_value=[site1])
        res2 = await coordinator._async_update_data()
        assert "s1" in res2[data_key]
        assert "s2" not in res2[data_key]

        coordinator.network_client.sites.get_all = AsyncMock(return_value=[])
        res3 = await coordinator._async_update_data()
        assert res3[data_key] == {}

    @pytest.mark.asyncio
    async def test_rule_sections_fetched_concurrently(
        self, coordinator: UnifiInsightsConfigCoordinator
    ) -> None:
        """Port forwards and traffic rules are fetched concurrently with gather."""
        site = _create_mock_model(
            {"id": "default", "name": "Default", "internalReference": "default"}
        )
        coordinator.network_client.sites.get_all = AsyncMock(return_value=[site])

        pf_started = asyncio.Event()
        tr_started = asyncio.Event()

        async def slow_list_pf(site_name: str) -> list[Any]:
            pf_started.set()
            await tr_started.wait()
            return []

        async def slow_list_tr(site_name: str) -> list[Any]:
            tr_started.set()
            await pf_started.wait()
            return []

        coordinator.network_client.port_forwards.list_port_forwards = AsyncMock(
            side_effect=slow_list_pf
        )
        coordinator.network_client.traffic_rules.list_traffic_rules = AsyncMock(
            side_effect=slow_list_tr
        )

        result = await asyncio.wait_for(coordinator._async_update_data(), timeout=2)
        assert result["port_forwards"]["default"] == {}
        assert result["traffic_rules"]["default"] == {}
        assert pf_started.is_set()
        assert tr_started.is_set()

    @pytest.mark.asyncio
    async def test_config_coordinator_internet_activity_windows_and_resilience(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_protect_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """Test rolling window aggregation, hourly reuse, and transient recovery."""
        now_ms = 1_800_000_000_000
        windows = aggregate_internet_activity_windows(
            five_minute_buckets=[
                {
                    "time": now_ms - WINDOW_1H_MS - 1,
                    "wan-rx_bytes": 9999,
                    "wan-tx_bytes": 9999,
                },
                {
                    "time": now_ms - WINDOW_1H_MS,
                    "wan-rx_bytes": 1000,
                    "wan-tx_bytes": 500,
                    "wan2-rx_bytes": 250,
                    "wan2-tx_bytes": 100,
                },
            ],
            hourly_buckets=[
                {
                    "time": now_ms - WINDOW_1W_MS - 1,
                    "wan-rx_bytes": 8888,
                    "wan-tx_bytes": 8888,
                },
                {
                    "time": now_ms - WINDOW_1D_MS - 3_600_000,
                    "wan-rx_bytes": 2000,
                    "wan-tx_bytes": 800,
                },
                {
                    "time": now_ms - WINDOW_1D_MS,
                    "wan-rx_bytes": 3000,
                    "wan-tx_bytes": 1200,
                },
            ],
            daily_buckets=[
                {
                    "time": now_ms - WINDOW_1M_MS,
                    "wan-rx_bytes": 10000,
                    "wan-tx_bytes": 4000,
                }
            ],
            now_ms=now_ms,
        )
        assert windows == {
            "1h": {"rx_bytes": 1250, "tx_bytes": 600},
            "1d": {"rx_bytes": 3000, "tx_bytes": 1200},
            "1w": {"rx_bytes": 5000, "tx_bytes": 2000},
            "1m": {"rx_bytes": 10000, "tx_bytes": 4000},
        }
        assert aggregate_internet_activity_windows([], [], [], now_ms=now_ms) == {}

        mock_network_client.sites.get_all = AsyncMock(
            return_value=[
                {
                    "id": "site1",
                    "name": "Default",
                    "internalReference": "default",
                }
            ]
        )
        coord = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=mock_protect_client,
            entry=mock_config_entry,
        )

        # 1. Unsupported response stores no site data and keeps WiFi/firewall intact
        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=UniFiNotFoundError("Not found", status_code=404)
        )
        await coord._async_update_data()
        assert coord.data["internet_activity"] == {}
        assert coord.internet_activity_available("site1") is True
        assert coord.wifi_available("site1") is True
        assert coord.firewall_available("site1") is True

        # 2. Successful fetch populates windows
        async def _report_ok(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            return [{"time": end_ms, "wan-rx_bytes": 4096, "wan-tx_bytes": 2048}]

        mock_network_client.reports.get_site_report = AsyncMock(side_effect=_report_ok)
        await coord._async_update_data()
        assert coord.data["internet_activity"]["site1"]["1h"] == {
            "rx_bytes": 4096,
            "tx_bytes": 2048,
        }
        assert coord.internet_activity_available("site1") is True

        # 3. Transient failure retains prior data and marks section unavailable
        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=UniFiTimeoutError("Timeout")
        )
        await coord._async_update_data()
        coord.last_update_success = True
        assert coord.internet_activity_available("site1") is False
        assert "site1" in coord.data["internet_activity_unavailable"]
        assert coord.data["internet_activity"]["site1"]["1h"] == {
            "rx_bytes": 4096,
            "tx_bytes": 2048,
        }
        assert coord.wifi_available("site1") is True
        assert coord.firewall_available("site1") is True

        # 4. Later success replaces data and clears the unavailable marker
        async def _report_recovered(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            return [{"time": end_ms, "wan-rx_bytes": 8192, "wan-tx_bytes": 4096}]

        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=_report_recovered
        )
        await coord._async_update_data()
        assert coord.internet_activity_available("site1") is True
        assert "site1" not in coord.data["internet_activity_unavailable"]
        assert coord.data["internet_activity"]["site1"]["1h"] == {
            "rx_bytes": 8192,
            "tx_bytes": 4096,
        }

        # 5. Cover bucket variations (SiteReportBucket instance, invalid dict,
        # non-dict object, rx-only, tx-only), client fallback, hourly/daily
        # partial failure, HTTP 200 HTML response, empty windows, and facade
        mixed_buckets: list[Any] = [
            SiteReportBucket.model_validate({"time": now_ms, "wan-rx_bytes": 100}),
            {"time": now_ms, "wan-tx_bytes": 50},
            {"invalid": "no_time"},
            "not_a_dict",
        ]
        rx_only = aggregate_internet_activity_windows(
            [SiteReportBucket.model_validate({"time": now_ms, "wan-rx_bytes": 100})],
            [SiteReportBucket.model_validate({"time": now_ms, "wan-tx_bytes": 50})],
            mixed_buckets,
            now_ms=now_ms,
        )
        assert rx_only["1h"] == {"rx_bytes": 100}
        assert rx_only["1d"] == {"tx_bytes": 50}
        assert rx_only["1m"] == {"rx_bytes": 100, "tx_bytes": 50}

        # Fallback to network_client.get_site_report and empty fallback
        mock_network_client.reports = None
        mock_network_client.get_site_report = AsyncMock(return_value=[])
        assert (
            await coord._call_site_report("default", "hourly", start_ms=1, end_ms=2)
            == []
        )
        mock_network_client.get_site_report = None
        assert (
            await coord._call_site_report("default", "hourly", start_ms=1, end_ms=2)
            == []
        )

        # Hourly / daily transient failures and HTTP 200 HTML unsupported
        mock_network_client.reports = MagicMock()

        async def _fail_hourly(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            if interval == "hourly":
                msg = "Hourly timeout"
                raise UniFiTimeoutError(msg)
            return []

        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=_fail_hourly
        )
        assert (
            await coord._fetch_site_internet_activity(
                site_id="site1", site_name="default", now_ms=now_ms
            )
            is None
        )

        async def _fail_daily(
            site_name: str, interval: str, *, start_ms: int, end_ms: int
        ) -> list[dict[str, int]]:
            if interval == "daily":
                msg = "Daily timeout"
                raise UniFiTimeoutError(msg)
            return []

        mock_network_client.reports.get_site_report = AsyncMock(side_effect=_fail_daily)
        assert (
            await coord._fetch_site_internet_activity(
                site_id="site1", site_name="default", now_ms=now_ms
            )
            is None
        )

        mock_network_client.reports.get_site_report = AsyncMock(
            side_effect=UniFiResponseError("HTML", status_code=200)
        )
        await coord._async_update_data()
        assert "site1" not in coord.data["internet_activity"]

        facade_with_cfg = MagicMock()
        facade_with_cfg.config_available = True
        facade_with_cfg._config_coordinator = coord
        assert (
            UnifiInsightsFacadeCoordinator.internet_activity_available(
                facade_with_cfg, "site1"
            )
            is True
        )
        facade_with_cfg._config_coordinator = None
        assert (
            UnifiInsightsFacadeCoordinator.internet_activity_available(
                facade_with_cfg, "site1"
            )
            is True
        )

    @pytest.mark.asyncio
    async def test_config_coordinator_innerspace_only_suppresses_network_404(
        self,
        hass: HomeAssistant,
        mock_network_client: MagicMock,
        mock_config_entry: MockConfigEntry,
    ) -> None:
        """The config coordinator suppresses a Network 404 when InnerSpace is used."""
        mock_network_client.sites.get_all.side_effect = UniFiNotFoundError(
            "Not found", status_code=404
        )
        config_coord = UnifiInsightsConfigCoordinator(
            hass=hass,
            network_client=mock_network_client,
            protect_client=None,
            entry=mock_config_entry,
            network_available=True,
            innerspace_available=True,
        )
        data = await config_coord._async_update_data()
        assert data["sites"] == {}
        assert config_coord.available is True

    @pytest.mark.asyncio
    async def test_config_sections_helper_branches(
        self,
        mock_network_client: MagicMock,
    ) -> None:
        """Test edge branches in config_sections helper functions."""
        # map_legacy_site_names: skip empty name and fall back to single legacy site
        mappings = map_legacy_site_names(
            {"unmatched_site": {"name": "Office"}},
            [{"name": ""}, {"name": "default", "desc": "Main"}],
        )
        assert mappings == {"unmatched_site": "default"}

        # wifi_qr_payload: WEP and hidden SSID
        qr = wifi_qr_payload("My;Net", "secret", "wep", hidden=True)
        assert "T:WEP" in qr
        assert "H:true" in qr

        # enrich_wifi: client without essid, wifi without ssid/matching config
        wifi_dict: dict[str, dict[str, Any]] = {
            "w1": {"id": "w1"},
            "w2": {"id": "w2", "name": "UnmatchedSSID"},
        }
        enrich_wifi(
            wifi_dict,
            [{"name": "OtherSSID", "x_passphrase": "pw", "security": "wpapsk"}],
            [{"is_wired": False}],
        )
        assert wifi_dict["w2"]["num_connected_clients"] == 0

        # resolve_report_site_name: non-dict site_data and internalReference fallback
        assert resolve_report_site_name("site1", "not_a_dict", None) == "site1"
        assert (
            resolve_report_site_name("site1", {"internalReference": "custom_ref"}, None)
            == "custom_ref"
        )

        # async_fetch_site_routes: route without id is skipped
        mock_network_client.routes.list_routes = AsyncMock(
            return_value=[{"name": "no-id"}, {"id": "r1", "name": "with-id"}]
        )
        routes = await async_fetch_site_routes(
            mock_network_client, dict, "site1", "default"
        )
        assert list(routes.keys()) == ["r1"]

        # async_update_site_internet_activity: failed fetch with no prior_activity
        failed_set: set[tuple[str, str]] = set()
        activity_map: dict[str, dict[str, dict[str, int]]] = {}
        await async_update_site_internet_activity(
            AsyncMock(return_value=None),
            site_id="site1",
            site_data={},
            legacy_name="default",
            now_ms=1000,
            prior_activity=None,
            internet_activity_by_site=activity_map,
            failed_sections=failed_set,
        )
        assert ("internet_activity", "site1") in failed_set
        assert "site1" not in activity_map

        # async_fetch_site_wifi_and_links: get_legacy_configs raising logs debug
        dummy_coord = MagicMock()
        dummy_coord._fetch_optional_section = AsyncMock(
            return_value=[{"id": "w1", "name": "Net"}]
        )
        dummy_coord._model_to_dict = dict
        dummy_coord._client_links = lambda _: {}
        dummy_coord.data = {"client_links": {}, "wifi": {}}
        dummy_coord.network_client.clients.get_active_legacy = AsyncMock(
            return_value=[]
        )
        dummy_coord.network_client.wifi.get_legacy_configs = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        wifi_res, _ = await async_fetch_site_wifi_and_links(
            dummy_coord, "site1", "default", set()
        )
        assert "w1" in wifi_res
