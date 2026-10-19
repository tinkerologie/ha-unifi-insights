# Copyright 2026 UniFi Insights contributors
"""
Tests for the config coordinator's command foundations (ADR-010).

Covers the published legacy site-name mapping and its lifecycle, and the
single ``resolve_legacy_site_name`` rule, against seeded real owners over
autospecced ``unifi_official_api`` clients.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiRuleNotEditableError,
)

from custom_components.unifi_insights.const import CONF_SITE_IDS, DOMAIN
from custom_components.unifi_insights.coordinators.config import (
    LEGACY_REMAP_COOLDOWN,
    RULE_REFRESH_RETRY_DELAY,
)
from tests.fixtures.command_owners import (
    SITE_ID,
    async_refresh_owners,
    async_shutdown_owners,
    build_seeded_owners,
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
