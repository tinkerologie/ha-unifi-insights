# Copyright 2026 UniFi Insights contributors
"""Tests for Protect-only console polling and warning suppression."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from unifi_official_api.exceptions import (
    UniFiResponseError,
)

from custom_components.unifi_insights.coordinators.config import (
    UnifiConfigCoordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


def _create_mock_network_client() -> MagicMock:
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
        network_client = _create_mock_network_client()

        coordinator = UnifiConfigCoordinator(
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
        network_client = _create_mock_network_client()
        network_client.sites.get_all = AsyncMock(
            side_effect=UniFiResponseError("Response is not JSON", status_code=200)
        )

        coordinator = UnifiConfigCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=MagicMock(),
            entry=mock_config_entry,
            network_available=True,
        )

        data = await coordinator._async_update_data()

        assert coordinator._network_available is False
        assert data["sites"] == {}
