# Copyright 2026 UniFi Insights contributors
"""Invariants for shared assertion/collector fixtures and immutable metadata."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, NonCallableMagicMock

import pytest
from homeassistant.helpers.entity import Entity

from custom_components.unifi_insights.binary_sensor import (
    UnifiInsightsBinarySensorEntityDescription,
    UnifiProtectSecurityBinarySensorEntityDescription,
)
from custom_components.unifi_insights.button import UnifiInsightsButtonEntityDescription
from custom_components.unifi_insights.sensor import (
    UnifiInsightsSensorEntityDescription,
    UnifiProtectSecuritySensorEntityDescription,
    UnifiProtectSensorEntityDescription,
    UnifiSiteInternetActivitySensorEntityDescription,
)
from tests.fixtures.assertions import require_instance, require_mock, require_not_none
from tests.fixtures.entities import EntityCallback, EntityCollector


def test_collector_accepts_the_full_platform_signature() -> None:
    target: list[Entity] = []
    collect = EntityCollector(target)
    first, second = Entity(), Entity()
    collect(iter([first]), True)  # noqa: FBT003 - verify HA's positional callback API
    collect([second], update_before_add=False)
    assert target == [first, second]
    assert collect.calls == [([first], True), ([second], False)]


def test_custom_observer_keeps_batches_and_order() -> None:
    batches: list[list[Entity]] = []
    callback = EntityCallback(batches.append)
    first, second = Entity(), Entity()
    callback(iter([first, second]), update_before_add=True)
    assert batches == [[first, second]]


def test_presence_and_type_guards_do_not_hide_invalid_fixtures() -> None:
    value = object()
    assert require_not_none(value) is value
    assert require_instance(value, object) is value
    with pytest.raises(AssertionError):
        require_not_none(None)
    with pytest.raises(AssertionError):
        require_instance(value, Entity)
    with pytest.raises(AssertionError):
        require_mock(value)
    for mock in (MagicMock(), NonCallableMagicMock()):
        assert require_mock(mock) is mock


@pytest.mark.parametrize(
    "description_type",
    [
        UnifiInsightsBinarySensorEntityDescription,
        UnifiProtectSecurityBinarySensorEntityDescription,
        UnifiInsightsButtonEntityDescription,
        UnifiInsightsSensorEntityDescription,
        UnifiProtectSensorEntityDescription,
        UnifiProtectSecuritySensorEntityDescription,
        UnifiSiteInternetActivitySensorEntityDescription,
    ],
)
def test_static_descriptions_are_immutable_and_keyword_only(description_type) -> None:
    parameters = inspect.signature(description_type).parameters
    fields = {
        "key": "fixture",
        "device_types": (),
        "window": "1h",
        "value_fn": lambda *_args: None,
    }
    description = description_type(
        **{key: value for key, value in fields.items() if key in parameters}
    )
    assert description.key == "fixture"
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in parameters.values()
    )
    with pytest.raises(FrozenInstanceError):
        description.key = "changed"
