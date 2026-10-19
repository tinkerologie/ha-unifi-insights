# Copyright 2026 UniFi Insights contributors
"""
Tests for the config coordinator's command foundations (ADR-010).

Covers the published legacy site-name mapping and its lifecycle, and the
single ``resolve_legacy_site_name`` rule, against seeded real owners over
autospecced ``unifi_official_api`` clients.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from homeassistant.exceptions import HomeAssistantError
from unifi_official_api import UniFiAuthenticationError, UniFiConnectionError

from custom_components.unifi_insights.const import CONF_SITE_IDS, DOMAIN
from tests.fixtures.command_owners import (
    SITE_ID,
    async_refresh_owners,
    async_shutdown_owners,
    build_seeded_owners,
)

if TYPE_CHECKING:
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
