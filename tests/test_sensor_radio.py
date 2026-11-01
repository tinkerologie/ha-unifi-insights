# Copyright 2026 UniFi Insights contributors
"""Tests for the access point radio sensors (#454)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfFrequency

from custom_components.unifi_insights.sensor import (
    RADIO_DETAILS_SENSOR_TYPES,
    RADIO_STATS_SENSOR_TYPES,
    UnifiRadioSensor,
    UnifiRadioSensorEntityDescription,
)
from tests.fixtures.assertions import require_not_none
from tests.fixtures.facade_data import facade_data, set_availability, with_sections

SITE_ID = "site1"
DEVICE_ID = "ap1"

AP_DEVICE = {
    "id": DEVICE_ID,
    "name": "Office AP",
    "model": "U7-Pro",
    "macAddress": "AA:BB:CC:00:00:02",
    "state": "ONLINE",
    "features": ["accessPoint"],
}
STATS = {
    "interfaces": {
        "radios": [
            {"frequencyGHz": 2.4, "txRetriesPct": 3.5},
            {"frequencyGHz": 5, "txRetriesPct": 0},
        ]
    }
}
DETAILS = {
    "radios": [
        {
            "frequencyGHz": 2.4,
            "channel": 6,
            "channelWidthMHz": 40,
            "txPowerMode": "AUTO",
            "wlanStandard": "802.11ax",
        },
        {
            "frequencyGHz": 5,
            "channel": 44,
            "channelWidthMHz": 80,
            "txPowerMode": "HIGH",
            "wlanStandard": "802.11ac",
        },
    ],
    "available": True,
}

DESCRIPTIONS = (*RADIO_STATS_SENSOR_TYPES, *RADIO_DETAILS_SENSOR_TYPES)


def _description(key: str) -> UnifiRadioSensorEntityDescription:
    """Return the radio description of ``key``."""
    return next(desc for desc in DESCRIPTIONS if desc.key == key)


@pytest.fixture
def coordinator() -> MagicMock:
    """Create a facade mock publishing one access point with two radios."""
    coordinator = MagicMock()
    coordinator.network_client.base_url = "https://192.168.1.1"
    coordinator.data = facade_data(
        devices={SITE_ID: {DEVICE_ID: AP_DEVICE}},
        stats={SITE_ID: {DEVICE_ID: STATS}},
        radio_details={SITE_ID: {DEVICE_ID: DETAILS}},
    )
    return coordinator


def _sensor(coordinator: MagicMock, key: str, band: str = "2_4ghz") -> UnifiRadioSensor:
    """Create the radio sensor ``key`` for ``band``."""
    return UnifiRadioSensor(
        coordinator, _description(key), SITE_ID, DEVICE_ID, band=band
    )


def test_tx_retries_values_keep_a_real_zero(coordinator: MagicMock) -> None:
    """A 0% retry rate is a measurement, not a missing value."""
    assert _sensor(coordinator, "radio_tx_retries").native_value == 3.5
    assert _sensor(coordinator, "radio_tx_retries", "5ghz").native_value == 0


def test_tx_retries_metadata(coordinator: MagicMock) -> None:
    """TX retries is an enabled diagnostic percentage measurement."""
    sensor = _sensor(coordinator, "radio_tx_retries")

    assert sensor.native_unit_of_measurement == PERCENTAGE
    assert sensor.state_class == SensorStateClass.MEASUREMENT
    assert sensor.device_class is None
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.entity_registry_enabled_default is True
    assert sensor.translation_placeholders == {"band": "2.4 GHz"}


def test_missing_retry_rate_is_unknown(coordinator: MagicMock) -> None:
    """A radio without txRetriesPct reads unknown, not zero."""
    coordinator.data = with_sections(
        coordinator.data,
        stats={
            SITE_ID: {DEVICE_ID: {"interfaces": {"radios": [{"frequencyGHz": 2.4}]}}}
        },
    )

    sensor = _sensor(coordinator, "radio_tx_retries")

    assert sensor.native_value is None
    assert sensor.available is True


def test_900_mhz_radio_uses_its_display_label(coordinator: MagicMock) -> None:
    """An 802.11ah radio is named with the 900 MHz label and a stable slug."""
    coordinator.data = with_sections(
        coordinator.data,
        stats={
            SITE_ID: {
                DEVICE_ID: {
                    "interfaces": {"radios": [{"frequencyGHz": 0.9, "txRetriesPct": 1}]}
                }
            }
        },
    )

    sensor = _sensor(coordinator, "radio_tx_retries", "900mhz")

    assert sensor.translation_placeholders == {"band": "900 MHz"}
    assert sensor.unique_id == f"{SITE_ID}_{DEVICE_ID}_radio_900mhz_radio_tx_retries"
    assert sensor.native_value == 1


@pytest.mark.parametrize("description", DESCRIPTIONS, ids=lambda d: d.key)
def test_unique_id_is_site_scoped_and_band_keyed(
    coordinator: MagicMock, description: UnifiRadioSensorEntityDescription
) -> None:
    """Unique IDs are site scoped, band keyed and never the DOMAIN prefix."""
    sensor = UnifiRadioSensor(coordinator, description, SITE_ID, DEVICE_ID, band="5ghz")

    unique_id = require_not_none(sensor.unique_id)
    assert unique_id == f"{SITE_ID}_{DEVICE_ID}_radio_5ghz_{description.key}"
    assert not unique_id.startswith("unifi_insights_")


@pytest.mark.parametrize("description", RADIO_DETAILS_SENSOR_TYPES, ids=lambda d: d.key)
def test_details_sensors_are_diagnostic_and_disabled_by_default(
    coordinator: MagicMock, description: UnifiRadioSensorEntityDescription
) -> None:
    """Radio settings rarely change, so their sensors start disabled."""
    sensor = UnifiRadioSensor(
        coordinator, description, SITE_ID, DEVICE_ID, band="2_4ghz"
    )

    assert sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert sensor.entity_registry_enabled_default is False


@pytest.mark.parametrize(
    ("key", "band", "expected"),
    [
        ("radio_tx_power_mode", "2_4ghz", "auto"),
        ("radio_tx_power_mode", "5ghz", "high"),
        ("radio_wlan_standard", "2_4ghz", "802_11ax"),
        ("radio_wlan_standard", "5ghz", "802_11ac"),
        ("radio_channel", "2_4ghz", 6),
        ("radio_channel", "5ghz", 44),
        ("radio_channel_width", "2_4ghz", 40),
        ("radio_channel_width", "5ghz", 80),
    ],
)
def test_details_values(
    coordinator: MagicMock, key: str, band: str, expected: object
) -> None:
    """Every details sensor reads its radio field."""
    assert _sensor(coordinator, key, band).native_value == expected


def test_details_enum_metadata(coordinator: MagicMock) -> None:
    """Both enum sensors offer only dot-free spec state keys."""
    power = _sensor(coordinator, "radio_tx_power_mode")
    standard = _sensor(coordinator, "radio_wlan_standard")
    width = _sensor(coordinator, "radio_channel_width")
    channel = _sensor(coordinator, "radio_channel")

    assert power.device_class == SensorDeviceClass.ENUM
    assert standard.device_class == SensorDeviceClass.ENUM
    assert power.options == [
        "auto",
        "low",
        "medium",
        "high",
        "custom",
        "disabled",
    ]
    assert standard.options == [
        "802_11a",
        "802_11b",
        "802_11g",
        "802_11n",
        "802_11ac",
        "802_11ax",
        "802_11be",
        "802_11ah",
    ]
    assert width.native_unit_of_measurement == UnitOfFrequency.MEGAHERTZ
    assert width.state_class is None
    assert channel.native_unit_of_measurement is None
    assert channel.state_class is None


def test_details_missing_fields_are_unknown(coordinator: MagicMock) -> None:
    """A radio that omits a setting reads unknown for that setting only."""
    coordinator.data = with_sections(
        coordinator.data,
        radio_details={
            SITE_ID: {
                DEVICE_ID: {
                    "radios": [{"frequencyGHz": 2.4, "channel": 6}],
                    "available": True,
                }
            }
        },
    )

    assert _sensor(coordinator, "radio_channel").native_value == 6
    assert _sensor(coordinator, "radio_channel_width").native_value is None
    assert _sensor(coordinator, "radio_tx_power_mode").native_value is None
    assert _sensor(coordinator, "radio_wlan_standard").native_value is None


@pytest.mark.parametrize("description", RADIO_DETAILS_SENSOR_TYPES, ids=lambda d: d.key)
def test_stale_details_make_only_step_two_unavailable(
    coordinator: MagicMock, description: UnifiRadioSensorEntityDescription
) -> None:
    """A failed details request leaves step-1 sensors working."""
    coordinator.data = with_sections(
        coordinator.data,
        radio_details={SITE_ID: {DEVICE_ID: {**DETAILS, "available": False}}},
    )

    sensor = UnifiRadioSensor(
        coordinator, description, SITE_ID, DEVICE_ID, band="2_4ghz"
    )

    assert sensor.available is False
    assert sensor.native_value is None
    assert _sensor(coordinator, "radio_tx_retries").available is True
    assert coordinator.data["radio_details"][SITE_ID][DEVICE_ID]["radios"]


def test_missing_details_record_makes_step_two_unavailable(
    coordinator: MagicMock,
) -> None:
    """A device without a details record has no step-2 reading."""
    coordinator.data = with_sections(coordinator.data, radio_details={})

    sensor = _sensor(coordinator, "radio_channel")

    assert sensor.available is False
    assert sensor.native_value is None
    assert _sensor(coordinator, "radio_tx_retries").available is True


def test_missing_radio_makes_only_that_band_unavailable(
    coordinator: MagicMock,
) -> None:
    """A band that is gone from both sections goes unavailable."""
    coordinator.data = with_sections(
        coordinator.data,
        stats={
            SITE_ID: {
                DEVICE_ID: {
                    "interfaces": {"radios": [{"frequencyGHz": 2.4, "txRetriesPct": 1}]}
                }
            }
        },
        radio_details={
            SITE_ID: {DEVICE_ID: {"radios": DETAILS["radios"][:1], "available": True}}
        },
    )

    assert _sensor(coordinator, "radio_tx_retries", "2_4ghz").available is True
    assert _sensor(coordinator, "radio_tx_retries", "5ghz").available is False
    assert _sensor(coordinator, "radio_tx_retries", "5ghz").native_value is None
    assert _sensor(coordinator, "radio_channel", "2_4ghz").available is True
    assert _sensor(coordinator, "radio_channel", "5ghz").available is False


def test_owner_unavailable_makes_every_radio_unavailable(
    coordinator: MagicMock,
) -> None:
    """Coordinator health gates step-1 and step-2 radios alike."""
    set_availability(coordinator, device=False)

    assert _sensor(coordinator, "radio_tx_retries").available is False
    assert _sensor(coordinator, "radio_channel").available is False
