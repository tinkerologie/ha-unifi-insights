# Copyright 2026 UniFi Insights contributors
"""Tests for the Carrier Fabric owner's translated validation (ADR-010)."""

from __future__ import annotations

import pytest

from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.coordinators.carrier_fabric import (
    InvalidSubscriberIdError,
    UnifiCarrierFabricCoordinator,
)


@pytest.mark.parametrize("subscriber_id", [123, "not-a-uuid"])
def test_invalid_subscriber_id_is_translated(subscriber_id: object) -> None:
    """The coordinator's own validation error carries its translation."""
    with pytest.raises(InvalidSubscriberIdError) as caught:
        UnifiCarrierFabricCoordinator._validate_subscriber_uuid(subscriber_id)

    assert isinstance(caught.value, ValueError)
    assert caught.value.translation_domain == DOMAIN
    assert caught.value.translation_key == "carrier_invalid_subscriber_id"
    assert caught.value.translation_placeholders == {"target": str(subscriber_id)}
    assert str(subscriber_id) in str(caught.value)
