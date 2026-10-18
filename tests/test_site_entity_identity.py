# Copyright 2026 UniFi Insights contributors
"""Real-registry regressions for console-scoped logical-site identities."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import attr
import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockEntityPlatform,
)

from custom_components.unifi_insights import (
    async_setup_entry,
)
from custom_components.unifi_insights import (
    binary_sensor as binary_sensor_platform,
)
from custom_components.unifi_insights import (
    button as button_platform,
)
from custom_components.unifi_insights import (
    event as event_platform,
)
from custom_components.unifi_insights import (
    image as image_platform,
)
from custom_components.unifi_insights import (
    number as number_platform,
)
from custom_components.unifi_insights import (
    sensor as sensor_platform,
)
from custom_components.unifi_insights.config_flow import UnifiInsightsConfigFlow
from custom_components.unifi_insights.console_identity import (
    build_legacy_site_unique_id,
    build_site_unique_id,
    normalize_console_namespace,
    resolve_console_namespace,
)
from custom_components.unifi_insights.const import (
    CONF_CLIENT_CONTROL,
    CONF_CONNECTION_TYPE,
    CONF_CONSOLE_ID,
    CONNECTION_TYPE_LOCAL,
    CONNECTION_TYPE_REMOTE,
    DOMAIN,
    SITE_ENTITY_SUFFIXES,
)
from custom_components.unifi_insights.helpers import (
    SiteEntityIdentityError,
    async_migrate_site_entity,
    async_rekey_site_entities,
    async_restore_site_entity_identities,
    owned_site_entity_unique_id,
)
from custom_components.unifi_insights.internet_activity_snapshot import (
    _resolve_entity_ids,
)
from custom_components.unifi_insights.number import VOUCHER_NUMBER_DESCRIPTIONS
from custom_components.unifi_insights.probe import ProbeResult, ProbeStatus
from custom_components.unifi_insights.sensor import (
    NVR_SENSOR_TYPES,
    PROTECT_SENSOR_TYPES,
    SITE_CLIENT_SENSOR_TYPES,
)
from custom_components.unifi_insights.site_internet_activity_sensor import (
    SITE_INTERNET_ACTIVITY_SENSOR_TYPES,
)
from custom_components.unifi_insights.voucher_sensor import VOUCHER_SENSOR_TYPES

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _entry(hass, namespace="console_a", entry_id="entry_a"):
    """Register a synthetic config entry with a canonical stored console identity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONSOLE_ID: namespace, CONF_API_KEY: "synthetic_credential"},
        entry_id=entry_id,
        unique_id=namespace,
    )
    entry.add_to_hass(hass)
    return entry


def _row(registry, entry, domain, unique_id, key, **kwargs):
    """Create a real registry row with explicit ownership and family metadata."""
    return registry.async_get_or_create(
        domain, DOMAIN, unique_id, config_entry=entry, translation_key=key, **kwargs
    )


@pytest.mark.parametrize(
    ("value", "credentials", "expected"),
    [
        ("  AA-BB-CC-DD-EE-FF  ", (), "aa:bb:cc:dd:ee:ff"),
        (" CLOUD_ID ", (), "cloud_id"),
        ("", (), None),
        (" \t ", (), None),
        (None, (), None),
        (123, (), None),
        ("bad id", (), None),
        ("bad\x00id", (), None),
        ("API-KEY", ("API-KEY",), None),
        ("api:key", (" API-KEY ",), None),
        ("API-KEY", ("api:key",), None),
        ("console", (None, "", 5), "console"),
    ],
)
def test_namespace_validation(value, credentials, expected):
    """Accept canonical console IDs while rejecting malformed and credential IDs."""
    assert normalize_console_namespace(value, credentials=credentials) == expected


def test_namespace_uses_only_stored_console(hass):
    """Require a stored console identity instead of an unsafe entry or host fallback."""
    entry = _entry(hass)
    assert resolve_console_namespace(entry) == "console_a"
    hass.config_entries.async_update_entry(
        entry, data={CONF_API_KEY: "key", "host": "host", "console_name": "Display"}
    )
    assert resolve_console_namespace(entry) is None
    with pytest.raises(ValueError, match="validated console namespace"):
        build_site_unique_id(None, "site_shared", "key")
    assert build_legacy_site_unique_id("site_shared", "key") == "site_shared_key"


@pytest.mark.parametrize(
    ("domain", "key"),
    [
        (domain, key)
        for domain, keys in SITE_ENTITY_SUFFIXES.items()
        for key in sorted(keys)
    ],
)
async def test_all_families_migrate_preserving_metadata(
    hass, entity_registry, device_registry, area_registry, domain, *, key
):
    """
    Migrate every logical family while retaining native identity and customizations.
    """
    entry = _entry(hass)
    area = area_registry.async_create("Synthetic area")
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "site_site_shared")}
    )
    legacy = build_legacy_site_unique_id("site_shared", key)
    row = _row(entity_registry, entry, domain, legacy, key, device_id=device.id)
    row = entity_registry.async_update_entity(
        row.entity_id,
        name="Custom name",
        area_id=area.id,
        disabled_by=er.RegistryEntryDisabler.USER,
        hidden_by=er.RegistryEntryHider.USER,
    )
    before = attr.asdict(row)
    target = build_site_unique_id("console_a", "site_shared", key)
    assert (
        async_migrate_site_entity(
            hass, entry, domain, "console_a", "site_shared", suffix=key
        )
        == target
    )
    after = attr.asdict(entity_registry.async_get(row.entity_id))
    assert {
        k: v
        for k, v in after.items()
        if k not in {"unique_id", "previous_unique_id", "modified_at"}
    } == {
        k: v
        for k, v in before.items()
        if k not in {"unique_id", "previous_unique_id", "modified_at"}
    }
    assert (
        async_migrate_site_entity(
            hass, entry, domain, "console_a", "site_shared", suffix=key
        )
        == target
    )
    assert attr.asdict(entity_registry.async_get(row.entity_id)) == after


@pytest.mark.parametrize("target_owner", ["same", "foreign"])
async def test_owned_legacy_priority_over_occupied_target(
    hass, entity_registry, caplog, target_owner
):
    """Keep the owned legacy row when a same-owner or foreign target is occupied."""
    entry = _entry(hass)
    other = _entry(hass, "console_b", "entry_b")
    key = "internet_download_1h"
    legacy = _row(entity_registry, entry, "sensor", "site_shared_" + key, key)
    target = _row(
        entity_registry,
        entry if target_owner == "same" else other,
        "sensor",
        "console_a_site_shared_" + key,
        key,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    assert (
        async_migrate_site_entity(
            hass, entry, "sensor", "console_a", "site_shared", suffix=key
        )
        == legacy.unique_id
    )
    assert _resolve_entity_ids(
        entity_registry,
        entry_id=entry.entry_id,
        namespace="console_a",
        site_id="site_shared",
    ) == {"download_1h": legacy.entity_id}
    assert (
        entity_registry.async_get(target.entity_id).config_entry_id
        == target.config_entry_id
    )
    assert "target identity is occupied" in caplog.text


async def test_foreign_rows_missing_namespace_and_occupied_target(
    hass, entity_registry
):
    """Never claim foreign rows or create unscoped entities without a namespace."""
    entry = _entry(hass)
    other = _entry(hass, "console_b", "entry_b")
    key = "site_total_clients"
    legacy = _row(entity_registry, other, "sensor", "site_shared_" + key, key)
    assert (
        async_migrate_site_entity(
            hass, entry, "sensor", None, "site_shared", suffix=key
        )
        is None
    )
    assert (
        async_migrate_site_entity(
            hass, entry, "sensor", "console_a", "site_shared", suffix=key
        )
        == "console_a_site_shared_" + key
    )
    target = _row(
        entity_registry,
        other,
        "sensor",
        "console_a_site_shared_" + key,
        key,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    assert (
        async_migrate_site_entity(
            hass, entry, "sensor", "console_a", "site_shared", suffix=key
        )
        is None
    )
    assert entity_registry.async_get(legacy.entity_id).config_entry_id == other.entry_id
    assert entity_registry.async_get(target.entity_id).config_entry_id == other.entry_id
    assert (
        _resolve_entity_ids(
            entity_registry,
            entry_id=entry.entry_id,
            namespace="console_a",
            site_id="site_shared",
        )
        == {}
    )
    own = _row(entity_registry, entry, "sensor", "site_other_" + key, key)
    assert (
        async_migrate_site_entity(hass, entry, "sensor", None, "site_other", suffix=key)
        == own.unique_id
    )
    assert (
        async_migrate_site_entity(hass, entry, "sensor", "site", "other", suffix=key)
        == own.unique_id
    )


async def test_migration_race_keeps_owned_legacy(hass, entity_registry, caplog):
    """Retain the actual owned legacy identity when native migration conflicts."""
    entry = _entry(hass)
    row = _row(
        entity_registry,
        entry,
        "sensor",
        "site_shared_active_vouchers",
        "active_vouchers",
    )
    with patch.object(
        entity_registry, "async_update_entity", side_effect=ValueError("conflict")
    ):
        assert (
            async_migrate_site_entity(
                hass,
                entry,
                "sensor",
                "console_a",
                "site_shared",
                suffix="active_vouchers",
            )
            == row.unique_id
        )
    assert "registry update failed" in caplog.text
    assert entity_registry.async_get(row.entity_id).unique_id == row.unique_id


@pytest.mark.parametrize("metadata_changes", [False, True])
async def test_migration_value_error_after_native_update_uses_actual_owned_identity(
    hass, entity_registry, caplog, metadata_changes
):
    """Use the owned target after a native update mutates before raising ValueError."""
    entry = _entry(hass)
    key = "active_vouchers"
    row = _row(entity_registry, entry, "sensor", "site_shared_" + key, key)
    native = entity_registry.async_update_entity

    def update(entity_id, **kwargs):
        """
        Raise ValueError after the real native registry update has mutated the row.
        """
        native(
            entity_id,
            **kwargs,
            **({"translation_key": "physical_key"} if metadata_changes else {}),
        )
        err_msg = "Synthetic post-mutation ValueError with an opaque marker"
        raise ValueError(err_msg)

    with patch.object(entity_registry, "async_update_entity", side_effect=update):
        effective = async_migrate_site_entity(
            hass, entry, "sensor", "console_a", "site_shared", suffix=key
        )
    assert effective == (None if metadata_changes else "console_a_site_shared_" + key)
    assert (
        entity_registry.async_get(row.entity_id).unique_id
        == "console_a_site_shared_" + key
    )
    assert entity_registry.async_get_entity_id("sensor", DOMAIN, row.unique_id) is None
    assert len(entity_registry.entities) == 1
    assert "opaque marker" not in caplog.text


async def test_unexpected_migration_failure_propagates_and_later_discovery_is_safe(
    hass, entity_registry
):
    """Propagate unexpected mutation failures without recreating a stale identity."""
    entry = _entry(hass)
    row = _row(
        entity_registry,
        entry,
        "sensor",
        "site_shared_active_vouchers",
        "active_vouchers",
    )
    native = entity_registry.async_update_entity

    def update(*args, **kwargs):
        """Raise an unexpected exception after the native identity mutation succeeds."""
        native(*args, **kwargs)
        err_msg = "Synthetic unexpected post-mutation failure"
        raise RuntimeError(err_msg)

    with (
        patch.object(entity_registry, "async_update_entity", side_effect=update),
        pytest.raises(RuntimeError, match="unexpected"),
    ):
        async_migrate_site_entity(
            hass, entry, "sensor", "console_a", "site_shared", suffix="active_vouchers"
        )
    assert (
        async_migrate_site_entity(
            hass, entry, "sensor", "console_a", "site_shared", suffix="active_vouchers"
        )
        == "console_a_site_shared_active_vouchers"
    )
    assert (
        entity_registry.async_get(row.entity_id).unique_id
        == "console_a_site_shared_active_vouchers"
    )


async def test_missing_lookup_record_is_not_claimed():
    """Fail closed when an indexed identity has no retrievable registry row."""
    registry = MagicMock()
    registry.async_get_entity_id.side_effect = [None, "sensor.gone"]
    registry.async_get.return_value = None
    assert (
        owned_site_entity_unique_id(
            registry, "entry", "sensor", "legacy", "target", translation_key="key"
        )
        is None
    )
    registry.async_get_entity_id.side_effect = ["sensor.gone", None]
    assert (
        owned_site_entity_unique_id(
            registry, "entry", "sensor", "legacy", "target", translation_key="key"
        )
        == "target"
    )
    registry.async_get_entity_id.side_effect = ["sensor.legacy", None, None]
    registry.async_get.return_value = SimpleNamespace(
        config_entry_id="entry", translation_key="key"
    )
    with patch(
        "custom_components.unifi_insights.helpers.er.async_get", return_value=registry
    ):
        assert (
            async_migrate_site_entity(
                MagicMock(),
                SimpleNamespace(entry_id="entry"),
                "sensor",
                "console",
                "site",
                suffix="key",
            )
            is None
        )


async def test_rekey_exact_families_preserves_physical_resource_and_foreign_rows(
    hass, entity_registry
):
    """
    Rekey complete logical families while leaving physical and foreign rows intact.
    """
    entry = _entry(hass)
    other = _entry(hass, "console_b", "entry_b")
    expected = []
    for domain, keys in SITE_ENTITY_SUFFIXES.items():
        for key in keys:
            row = _row(
                entity_registry,
                entry,
                domain,
                build_site_unique_id("console_a", "site_shared", key),
                key,
            )
            expected.append(
                (row.entity_id, build_site_unique_id("new_console", "site_shared", key))
            )
    untouched = [
        _row(
            entity_registry,
            entry,
            "sensor",
            "console_a_site_shared_router_site_wired_clients",
            "wired_clients",
        ),
        _row(
            entity_registry,
            entry,
            "sensor",
            "console_a_site_shared_wifi_resource_clients",
            "wifi_clients",
        ),
        _row(
            entity_registry,
            entry,
            "sensor",
            "site_shared_active_vouchers",
            "active_vouchers",
        ),
        _row(
            entity_registry,
            other,
            "sensor",
            "console_a_site_foreign_active_vouchers",
            "active_vouchers",
        ),
        entity_registry.async_get_or_create(
            "sensor",
            "other_platform",
            "console_a_site_shared_active_vouchers",
            config_entry=entry,
        ),
    ]
    async_rekey_site_entities(hass, entry, " NEW_CONSOLE ", pending_api_key="new_key")
    for entity_id, unique_id in expected:
        assert entity_registry.async_get(entity_id).unique_id == unique_id
    for row in untouched:
        assert entity_registry.async_get(row.entity_id).unique_id == row.unique_id
    assert entry.data[CONF_CONSOLE_ID] == "console_a"


@pytest.mark.parametrize("owner", ["same", "foreign"])
async def test_rekey_conflict_preflight_is_atomic(hass, entity_registry, owner):
    """Reject occupied targets before mutating any eligible logical registry row."""
    entry = _entry(hass)
    other = _entry(hass, "console_b", "entry_b")
    rows = [
        _row(entity_registry, entry, "sensor", "console_a_site_" + key, key)
        for key in ("active_vouchers", "latest_voucher_code")
    ]
    _row(
        entity_registry,
        entry if owner == "same" else other,
        "sensor",
        "new_site_latest_voucher_code",
        "latest_voucher_code",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    with patch.object(
        entity_registry,
        "async_update_entity",
        wraps=entity_registry.async_update_entity,
    ) as update:
        with pytest.raises(SiteEntityIdentityError, match="occupied"):
            async_rekey_site_entities(hass, entry, "new")
        update.assert_not_called()
    assert [entity_registry.async_get(row.entity_id).unique_id for row in rows] == [
        row.unique_id for row in rows
    ]


async def test_rekey_unexpected_update_failure_rolls_back(hass, entity_registry):
    """Restore earlier native registry mutations when a later update fails."""
    entry = _entry(hass)
    rows = [
        _row(entity_registry, entry, "sensor", "console_a_site_" + key, key)
        for key in ("active_vouchers", "latest_voucher_code")
    ]
    native = entity_registry.async_update_entity
    count = 0

    def update(*args, **kwargs):
        """
        Fail the second update before mutation to exercise compensation of earlier rows.
        """
        nonlocal count
        count += 1
        if count == 2:
            err_msg = "synthetic update failure"
            raise RuntimeError(err_msg)
        return native(*args, **kwargs)

    with (
        patch.object(entity_registry, "async_update_entity", side_effect=update),
        pytest.raises(RuntimeError, match="synthetic"),
    ):
        async_rekey_site_entities(hass, entry, "new")
    assert count == 3
    assert [entity_registry.async_get(row.entity_id).unique_id for row in rows] == [
        row.unique_id for row in rows
    ]


@pytest.mark.parametrize(
    ("unique_id", "key"),
    [
        ("console_a_site_active_vouchers", None),
        ("console_a_active_vouchers", "active_vouchers"),
        ("console_a_site_wrong_key", "active_vouchers"),
    ],
)
async def test_rekey_rejects_ambiguous_metadata(hass, entity_registry, unique_id, key):
    """Reject incomplete logical family metadata before changing registry identities."""
    entry = _entry(hass)
    _row(entity_registry, entry, "sensor", unique_id, key)
    with pytest.raises(SiteEntityIdentityError, match="metadata"):
        async_rekey_site_entities(hass, entry, "new")


async def test_rekey_namespace_noops_and_credentials(hass, entity_registry):
    """Keep matching namespaces unchanged and reject current or pending credentials."""
    entry = _entry(hass)
    async_rekey_site_entities(hass, entry, "CONSOLE_A")
    for value in ("", 5, "synthetic_credential", "pending:key"):
        with pytest.raises(SiteEntityIdentityError):
            async_rekey_site_entities(hass, entry, value, pending_api_key="PENDING-KEY")
    with pytest.raises(SiteEntityIdentityError, match="pending credential"):
        async_rekey_site_entities(hass, entry, "new", pending_api_key="CONSOLE_A")
    entry2 = _entry(hass, None, "entry_b")
    async_rekey_site_entities(hass, entry2, "new")
    async_rekey_site_entities(hass, entry2, None)


def test_all_description_keys_are_in_exact_family_mapping():
    """Require the guarded family mapping to cover every logical description key."""
    assert {
        d.key
        for d in (
            *SITE_CLIENT_SENSOR_TYPES,
            *SITE_INTERNET_ACTIVITY_SENSOR_TYPES,
            *VOUCHER_SENSOR_TYPES,
        )
    } == SITE_ENTITY_SUFFIXES["sensor"]
    assert {d.key for d in VOUCHER_NUMBER_DESCRIPTIONS} == SITE_ENTITY_SUFFIXES[
        "number"
    ]


def test_identity_conflict_message_has_english_parity_and_truthful_recovery():
    """Keep the English conflict message consistent with preservation behavior."""
    root = Path(__file__).parents[1] / "custom_components" / DOMAIN
    messages = [
        json.loads((root / path).read_text())["config"]["error"][
            "site_entity_identity_conflict"
        ]
        for path in ("strings.json", "translations/en.json")
    ]
    assert messages[0] == messages[1]
    assert "site entity identities" in messages[0]
    assert "saved connection settings remain unchanged" in messages[0]
    assert "Check the logs" in messages[0]


@pytest.mark.parametrize(
    "connection_type", [CONNECTION_TYPE_LOCAL, CONNECTION_TYPE_REMOTE]
)
async def test_reauth_identity_failure_reports_preservation_error_without_mutation(
    hass, entity_registry, caplog, connection_type
):
    """
    Report unsafe reauth identity changes without persisting settings or registry
    changes.
    """
    entry = _entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CONNECTION_TYPE: connection_type,
            CONF_HOST: "https://console.example",
        },
    )
    before_data = dict(entry.data)
    row = _row(
        entity_registry,
        entry,
        "sensor",
        "console_a_site_shared_active_vouchers",
        "active_vouchers",
    )
    before = attr.asdict(row)
    with (
        patch.object(
            UnifiInsightsConfigFlow,
            "_async_validate_local_connection",
            AsyncMock(return_value=(True, None, {})),
        ),
        patch.object(
            UnifiInsightsConfigFlow,
            "_async_discover_remote_consoles",
            AsyncMock(return_value=[{"id": "console_a"}]),
        ),
        patch.object(
            UnifiInsightsConfigFlow,
            "_normalize_remote_console_id",
            return_value="console_a",
        ),
        patch.object(
            UnifiInsightsConfigFlow,
            "_async_validate_remote_console",
            AsyncMock(return_value=True),
        ),
        patch.object(
            entity_registry,
            "async_update_entity",
            wraps=entity_registry.async_update_entity,
        ) as update,
    ):
        flow = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], user_input={CONF_API_KEY: "CONSOLE_A"}
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "site_entity_identity_conflict"
    assert dict(entry.data) == before_data
    assert attr.asdict(entity_registry.async_get(row.entity_id)) == before
    update.assert_not_called()
    assert "Cannot change console identity safely" in caplog.text


@pytest.mark.parametrize("namespace", [None, "", " ", "bad namespace", "CONSOLE_A"])
def test_builder_requires_a_valid_canonical_namespace(namespace):
    """Reject missing namespaces instead of manufacturing unscoped new identities."""
    with pytest.raises(ValueError, match="validated console namespace"):
        build_site_unique_id(namespace, "site_shared", "site_total_clients")


async def test_known_physical_legacy_is_never_migrated_or_claimed(
    hass, entity_registry
):
    """Preserve physical rows even when their IDs resemble a logical legacy family."""
    entry = _entry(hass)
    physical = _row(
        entity_registry,
        entry,
        "sensor",
        "site_shared_site_wired_clients",
        "wired_clients",
    )
    original = attr.asdict(physical)
    target = "console_a_site_shared_site_wired_clients"
    assert (
        async_migrate_site_entity(
            hass,
            entry,
            "sensor",
            "console_a",
            "site_shared",
            suffix="site_wired_clients",
        )
        == target
    )
    assert attr.asdict(entity_registry.async_get(physical.entity_id)) == original
    assert (
        async_migrate_site_entity(
            hass, entry, "sensor", None, "site_shared", suffix="site_wired_clients"
        )
        is None
    )
    logical = _row(entity_registry, entry, "sensor", target, "site_wired_clients")
    assert logical.entity_id != physical.entity_id
    assert (
        async_migrate_site_entity(
            hass,
            entry,
            "sensor",
            "console_a",
            "site_shared",
            suffix="site_wired_clients",
        )
        == logical.unique_id
    )
    # Also preserve the exact physical counterexample during namespace re-key.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CONSOLE_ID: "site"}
    )
    exact_physical = _row(
        entity_registry,
        entry,
        "sensor",
        "site_shared_router_site_wired_clients",
        "wired_clients",
    )
    async_rekey_site_entities(hass, entry, "new")
    assert (
        entity_registry.async_get(exact_physical.entity_id).unique_id
        == exact_physical.unique_id
    )


@pytest.mark.parametrize("with_legacy", [False, True])
async def test_owned_incompatible_target_is_not_reused(
    hass, entity_registry, with_legacy
):
    """Reject same-owner targets whose metadata identifies a different entity family."""
    entry = _entry(hass)
    key = "internet_download_1h"
    target = _row(
        entity_registry,
        entry,
        "sensor",
        "console_a_site_shared_" + key,
        "physical_download",
    )
    if with_legacy:
        legacy = _row(entity_registry, entry, "sensor", "site_shared_" + key, key)
    effective = async_migrate_site_entity(
        hass, entry, "sensor", "console_a", "site_shared", suffix=key
    )
    assert effective == (legacy.unique_id if with_legacy else None)
    snapshot = _resolve_entity_ids(
        entity_registry,
        entry_id=entry.entry_id,
        namespace="console_a",
        site_id="site_shared",
    )
    assert snapshot == ({"download_1h": legacy.entity_id} if with_legacy else {})
    assert (
        entity_registry.async_get(target.entity_id).translation_key
        == "physical_download"
    )


@pytest.mark.parametrize(
    ("candidate", "metadata"),
    [
        ("legacy", None),
        ("legacy", ""),
        ("target", None),
        ("target", ""),
    ],
)
async def test_ambiguous_owned_candidate_skips_and_is_retried(
    hass, entity_registry, mock_coordinator, caplog, candidate, *, metadata
):
    """Leave ambiguous candidates undiscovered so later valid metadata can retry."""
    entry = _entry(hass)
    key = "internet_download_1h"
    unique_id = (
        "site_shared_" if candidate == "legacy" else "console_a_site_shared_"
    ) + key
    row = _row(entity_registry, entry, "sensor", unique_id, metadata)
    mock_coordinator.protect_client = None
    mock_coordinator.data = {
        "sites": {"site_shared": {"id": "site_shared"}},
        "internet_activity": {"site_shared": {"1h": {"rx_bytes": 1}}},
    }
    entry.runtime_data = SimpleNamespace(
        coordinator=mock_coordinator, mobility_coordinator=None
    )
    added = []
    callbacks = []
    mock_coordinator.async_add_listener.side_effect = lambda listener: (
        callbacks.append(listener) or (lambda: None)
    )
    await sensor_platform.async_setup_entry(hass, entry, added.extend)
    assert added == []
    assert entity_registry.async_get(row.entity_id).unique_id == unique_id
    assert (
        _resolve_entity_ids(
            entity_registry,
            entry_id=entry.entry_id,
            namespace="console_a",
            site_id="site_shared",
        )
        == {}
    )
    assert "registry metadata" in caplog.text
    entity_registry.async_update_entity(row.entity_id, translation_key=key)
    for callback in callbacks:
        callback()
    assert [entity.unique_id for entity in added] == ["console_a_site_shared_" + key]
    assert entity_registry.async_get(row.entity_id).unique_id == added[0].unique_id


@pytest.mark.parametrize(
    "failure", ["after_native_update", "schedule_save", "rollback_save"]
)
async def test_native_post_mutation_failure_restores_all_attempted_ids(
    hass, entity_registry, caplog, failure
):
    """Restore every attempted native update after mutation or save scheduling fails."""
    entry = _entry(hass)
    rows = [
        _row(entity_registry, entry, "sensor", "console_a_site_" + key, key)
        for key in ("active_vouchers", "latest_voucher_code")
    ]
    before = [attr.asdict(row) for row in rows]
    native_update = entity_registry.async_update_entity
    native_save = entity_registry.async_schedule_save
    count = 0

    def update(*args, **kwargs):
        """
        Raise after the second real native mutation to retain the attempted-row
        boundary.
        """
        nonlocal count
        count += 1
        result = native_update(*args, **kwargs)
        if count == 2:
            err_msg = "Synthetic exception after native mutation"
            raise RuntimeError(err_msg)
        return result

    def save():
        """
        Inject initial or rollback save-scheduling failure after native row mutation.
        """
        nonlocal count
        count += 1
        if count == 2 or (failure == "rollback_save" and count == 3):
            err_msg = "Synthetic schedule-save failure"
            raise RuntimeError(err_msg)
        return native_save()

    method = (
        "async_update_entity"
        if failure == "after_native_update"
        else "async_schedule_save"
    )
    side_effect = update if failure == "after_native_update" else save
    with (
        patch.object(entity_registry, method, side_effect=side_effect),
        pytest.raises(RuntimeError, match="Synthetic"),
    ):
        async_rekey_site_entities(hass, entry, "new")
    assert count == 4
    assert [entity_registry.async_get(row.entity_id).unique_id for row in rows] == [
        row.unique_id for row in rows
    ]
    for row, original in zip(rows, before, strict=True):
        after = attr.asdict(entity_registry.async_get(row.entity_id))
        assert {
            k: v
            for k, v in after.items()
            if k not in {"unique_id", "previous_unique_id", "modified_at"}
        } == {
            k: v
            for k, v in original.items()
            if k not in {"unique_id", "previous_unique_id", "modified_at"}
        }
    assert entry.data[CONF_CONSOLE_ID] == "console_a"
    assert ("Unable to fully roll back" in caplog.text) == (failure == "rollback_save")


async def _discover_all(hass, entry, coordinator):
    """Run every logical-site platform against the same native registry."""
    entry.runtime_data = SimpleNamespace(
        coordinator=coordinator, mobility_coordinator=None
    )
    coordinator.hass = hass
    entities = []
    for platform in (
        sensor_platform,
        number_platform,
        button_platform,
        image_platform,
        event_platform,
    ):
        await platform.async_setup_entry(hass, entry, entities.extend)
    return entities


async def test_two_consoles_all_families_real_registry(
    hass, entity_registry, mock_coordinator
):
    """Actual platform discovery yields independent rows for every affected key."""
    mock_coordinator.protect_client = None
    mock_coordinator.data = {
        "sites": {"site_shared": {"id": "site_shared", "name": "Synthetic site"}},
        "clients": {"site_shared": {}},
        "vouchers": {"site_shared": {}},
        "internet_activity": {
            "site_shared": {
                window: {"rx_bytes": 1, "tx_bytes": 2}
                for window in ("1h", "1d", "1w", "1m")
            }
        },
    }
    for namespace, entry_id in (("console_a", "entry_a"), ("console_b", "entry_b")):
        entry = _entry(hass, namespace, entry_id)
        entities = await _discover_all(hass, entry, mock_coordinator)
        assert len(entities) == sum(len(keys) for keys in SITE_ENTITY_SUFFIXES.values())
        assert {entity._site_id for entity in entities} == {"site_shared"}
        for entity in entities:
            domain = next(
                domain
                for domain, keys in SITE_ENTITY_SUFFIXES.items()
                if entity.translation_key in keys
            )
            key = entity.translation_key
            assert entity.unique_id == build_site_unique_id(
                namespace, "site_shared", key
            )
            row = _row(entity_registry, entry, domain, entity.unique_id, key)
            assert row.config_entry_id == entry.entry_id
            assert (DOMAIN, "site_site_shared") in entity.device_info["identifiers"]
        activity_ids = _resolve_entity_ids(
            entity_registry,
            entry_id=entry.entry_id,
            namespace=namespace,
            site_id="site_shared",
        )
        assert len(activity_ids) == 8
        assert all(
            entity_registry.async_get(entity_id).config_entry_id == entry.entry_id
            for entity_id in activity_ids.values()
        )
    assert len(entity_registry.entities) == 2 * sum(
        len(keys) for keys in SITE_ENTITY_SUFFIXES.values()
    )
    assert mock_coordinator.data["sites"] == {
        "site_shared": {"id": "site_shared", "name": "Synthetic site"}
    }


async def test_missing_namespace_skips_until_later_discovery_and_reload(
    hass, entity_registry, mock_coordinator
):
    """
    Retry logical discovery after a safe namespace becomes available and on reload.
    """
    entry = _entry(hass, None)
    mock_coordinator.protect_client = None
    mock_coordinator.data = {
        "sites": {"site_shared": {"id": "site_shared"}},
        "clients": {"site_shared": {}},
        "vouchers": {"site_shared": {}},
        "internet_activity": {"site_shared": {"1h": {"rx_bytes": 1, "tx_bytes": 2}}},
    }
    owned_legacy = _row(
        entity_registry,
        entry,
        "sensor",
        "site_shared_site_total_clients",
        "site_total_clients",
    )
    added = []
    callbacks = []
    mock_coordinator.async_add_listener.side_effect = lambda listener: (
        callbacks.append(listener) or (lambda: None)
    )
    entry.runtime_data = SimpleNamespace(
        coordinator=mock_coordinator, mobility_coordinator=None
    )
    for platform in (
        sensor_platform,
        number_platform,
        button_platform,
        image_platform,
        event_platform,
    ):
        await platform.async_setup_entry(hass, entry, added.extend)
    assert [e.unique_id for e in added] == [owned_legacy.unique_id]
    assert (
        _resolve_entity_ids(
            entity_registry,
            entry_id=entry.entry_id,
            namespace=None,
            site_id="site_shared",
        )
        == {}
    )
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_CONSOLE_ID: "console_a"}
    )
    for callback in callbacks:
        callback()
    expected_count = 3 + 2 + 3 + 5 + 1 + 1 + 1
    assert len(added) == expected_count
    for entity in added:
        domain = next(
            domain
            for domain, keys in SITE_ENTITY_SUFFIXES.items()
            if entity.translation_key in keys
        )
        row = _row(
            entity_registry, entry, domain, entity.unique_id, entity.translation_key
        )
        assert row.config_entry_id == entry.entry_id
    # The retained legacy entity stays known until reload; its owned row then migrates.
    reloaded = await _discover_all(hass, entry, mock_coordinator)
    assert len(reloaded) == expected_count
    assert (
        entity_registry.async_get(owned_legacy.entity_id).unique_id
        == "console_a_site_shared_site_total_clients"
    )
    assert not any(entity.unique_id == owned_legacy.unique_id for entity in reloaded)
    assert len(entity_registry.entities) == expected_count


async def test_platform_discovery_never_claims_disabled_foreign_targets(
    hass, entity_registry, mock_coordinator
):
    """Protect disabled and unloaded foreign ownership during logical discovery."""
    entry = _entry(hass)
    other = _entry(hass, "console_b", "entry_b")
    mock_coordinator.protect_client = None
    mock_coordinator.data = {
        "sites": {"site_shared": {"id": "site_shared"}},
        "clients": {"site_shared": {}},
        "vouchers": {"site_shared": {}},
        "internet_activity": {"site_shared": {"1h": {"rx_bytes": 1, "tx_bytes": 2}}},
    }
    foreign = []
    for domain, keys in SITE_ENTITY_SUFFIXES.items():
        foreign.extend(
            _row(
                entity_registry,
                other,
                domain,
                build_site_unique_id("console_a", "site_shared", key),
                key,
                disabled_by=er.RegistryEntryDisabler.USER,
            )
            for key in keys
        )
    legacy = _row(
        entity_registry,
        entry,
        "sensor",
        "site_shared_site_total_clients",
        "site_total_clients",
    )
    entities = await _discover_all(hass, entry, mock_coordinator)
    assert [entity.unique_id for entity in entities] == [legacy.unique_id]
    assert entities[0].native_value == 0
    mock_coordinator.data["clients"]["site_shared"]["synthetic_client"] = {
        "type": "WIRED"
    }
    assert entities[0].native_value == 1
    assert entity_registry.async_get(legacy.entity_id).unique_id == legacy.unique_id
    assert all(
        entity_registry.async_get(row.entity_id).config_entry_id == other.entry_id
        for row in foreign
    )


@pytest.mark.parametrize(
    "outcome", ["success", "conflict", "failure", "post_mutation_failure"]
)
async def test_local_flow_rekeys_before_persisting_and_blocks_partial_change(
    hass, entity_registry, outcome
):
    """
    Persist local settings only after successful native logical identity migration.
    """
    entry = _entry(hass, "site_fallback")
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://console.example",
        },
    )
    before_data = dict(entry.data)
    rows = [
        _row(
            entity_registry,
            entry,
            "sensor",
            build_site_unique_id("site_fallback", "site_shared", key),
            key,
        )
        for key in ("site_total_clients", "site_wired_clients")
    ]
    new_namespace = "aa:bb:cc:dd:ee:ff"
    if outcome == "conflict":
        other = _entry(hass, "console_b", "entry_b")
        _row(
            entity_registry,
            other,
            "sensor",
            build_site_unique_id(new_namespace, "site_shared", "site_wired_clients"),
            "site_wired_clients",
            disabled_by=er.RegistryEntryDisabler.USER,
        )
    native = entity_registry.async_update_entity
    count = 0

    def update(*args, **kwargs):
        """
        Inject pre-mutation or post-mutation failure in the second native registry
        update.
        """
        nonlocal count
        count += 1
        if outcome == "failure" and count == 2:
            err_msg = "Synthetic registry failure"
            raise RuntimeError(err_msg)
        result = native(*args, **kwargs)
        if outcome == "post_mutation_failure" and count == 2:
            err_msg = "Synthetic registry failure after mutation"
            raise RuntimeError(err_msg)
        return result

    with (
        patch.object(
            UnifiInsightsConfigFlow,
            "_async_validate_local_connection",
            AsyncMock(return_value=(True, None, {"mac": new_namespace})),
        ),
        patch.object(entity_registry, "async_update_entity", side_effect=update),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
        patch(
            "custom_components.unifi_insights.async_migrate_entry", return_value=True
        ),
    ):
        flow = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            user_input={
                CONF_HOST: "https://new-console.example",
                CONF_API_KEY: "pending_credential",
                CONF_VERIFY_SSL: False,
            },
        )
        await hass.async_block_till_done()
    if outcome == "success":
        assert result["type"] == FlowResultType.ABORT
        assert entry.data[CONF_CONSOLE_ID] == new_namespace
        assert entry.unique_id == new_namespace
        assert [entity_registry.async_get(row.entity_id).unique_id for row in rows] == [
            build_site_unique_id(new_namespace, "site_shared", key)
            for key in ("site_total_clients", "site_wired_clients")
        ]
    else:
        assert result["type"] == FlowResultType.FORM
        assert result["errors"]["base"] == (
            "site_entity_identity_conflict" if outcome == "conflict" else "unknown"
        )
        assert dict(entry.data) == before_data
        assert entry.data[CONF_CONSOLE_ID] == "site_fallback"
        assert entry.unique_id == "site_fallback"
        assert [entity_registry.async_get(row.entity_id).unique_id for row in rows] == [
            row.unique_id for row in rows
        ]
        assert count == (
            0
            if outcome == "conflict"
            else 4
            if outcome == "post_mutation_failure"
            else 3
        )


@pytest.mark.parametrize("source", ["reconfigure", "reauth"])
async def test_remote_flow_rekeys_or_reuses_normalized_namespace(
    hass, entity_registry, source
):
    """
    Use the canonical namespace through native remote reconfigure and reauth flows.
    """
    old = "console_a" if source == "reconfigure" else "CONSOLE_A"
    entry = _entry(hass, old)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, CONF_CONNECTION_TYPE: CONNECTION_TYPE_REMOTE},
        unique_id="synthetic_credential" if source == "reconfigure" else old,
    )
    row = _row(
        entity_registry,
        entry,
        "sensor",
        "console_a_site_shared_active_vouchers",
        "active_vouchers",
    )
    new = "console_b" if source == "reconfigure" else "console_a"
    with (
        patch.object(
            UnifiInsightsConfigFlow,
            "_async_discover_remote_consoles",
            AsyncMock(return_value=[{"id": new}]),
        ),
        patch.object(
            UnifiInsightsConfigFlow, "_normalize_remote_console_id", return_value=new
        ),
        patch.object(
            UnifiInsightsConfigFlow,
            "_async_validate_remote_console",
            AsyncMock(return_value=True),
        ),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
        patch(
            "custom_components.unifi_insights.async_migrate_entry", return_value=True
        ),
    ):
        if source == "reconfigure":
            flow = await entry.start_reconfigure_flow(hass)
        else:
            flow = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "reauth", "entry_id": entry.entry_id},
                data=entry.data,
            )
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            user_input={
                CONF_API_KEY: "pending_credential",
                **({CONF_CONSOLE_ID: new} if source == "reconfigure" else {}),
            },
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert entry.data[CONF_CONSOLE_ID] == new
    assert (
        entity_registry.async_get(row.entity_id).unique_id
        == new + "_site_shared_active_vouchers"
    )


async def test_rekey_disappearing_row_does_not_prevent_other_restoration(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep a missing row missing while restoring every surviving attempted row."""
    entry = _entry(hass)
    rows = [
        _row(entity_registry, entry, "sensor", "console_a_site_" + key, key)
        for key in ("active_vouchers", "latest_voucher_code")
    ]
    native = entity_registry.async_update_entity
    count = 0

    def update(*args: Any, **kwargs: Any) -> er.RegistryEntry:
        """Remove the second row after its native mutation, then fail the update."""
        nonlocal count
        count += 1
        result = native(*args, **kwargs)
        if count == 2:
            entity_registry.async_remove(result.entity_id)
            err_msg = "Synthetic disappearing registry row"
            raise RuntimeError(err_msg)
        return result

    with (
        patch.object(entity_registry, "async_update_entity", side_effect=update),
        pytest.raises(RuntimeError, match="disappearing"),
    ):
        async_rekey_site_entities(hass, entry, "new")
    assert entity_registry.async_get(rows[0].entity_id).unique_id == rows[0].unique_id
    assert entity_registry.async_get(rows[1].entity_id) is None
    assert count == 3
    assert "Unable to fully roll back" in caplog.text


@pytest.mark.parametrize(
    "interference", ["foreign_owner", "replacement", "changed_uid", "occupied_old"]
)
async def test_compensation_never_takes_over_interfering_registry_rows(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
    interference: str,
) -> None:
    """Restore unaffected rows without overwriting ownership or identity changes."""
    entry = _entry(hass)
    other = _entry(hass, "console_b", "entry_b")
    rows = [
        _row(entity_registry, entry, "sensor", "console_a_site_" + key, key)
        for key in ("active_vouchers", "latest_voucher_code")
    ]
    updates = async_rekey_site_entities(hass, entry, "new")
    last = rows[-1]
    if interference == "foreign_owner":
        entity_registry.async_update_entity(
            last.entity_id, config_entry_id=other.entry_id
        )
    elif interference == "replacement":
        entity_registry.async_remove(last.entity_id)
        replacement = _row(
            entity_registry,
            other,
            "sensor",
            "foreign_replacement",
            last.translation_key,
        )
        entity_registry.async_update_entity(
            replacement.entity_id, new_entity_id=last.entity_id
        )
    elif interference == "changed_uid":
        entity_registry.async_update_entity(
            last.entity_id, new_unique_id="intervening_identity"
        )
    else:
        _row(entity_registry, other, "sensor", last.unique_id, last.translation_key)
    protected = attr.asdict(entity_registry.async_get(last.entity_id))
    assert not async_restore_site_entity_identities(hass, entry, updates)
    assert entity_registry.async_get(rows[0].entity_id).unique_id == rows[0].unique_id
    assert attr.asdict(entity_registry.async_get(last.entity_id)) == protected
    assert "Unable to fully roll back" in caplog.text


async def test_restoration_already_completed_is_a_native_noop(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Accept an already restored exact row without updates, saves or audit events."""
    entry = _entry(hass)
    other = _entry(hass, "console_b", "entry_b")
    previous = _row(
        entity_registry,
        entry,
        "sensor",
        build_site_unique_id("console_a", "site_shared", "active_vouchers"),
        "active_vouchers",
    )
    previous = entity_registry.async_update_entity(
        previous.entity_id,
        name="Custom voucher sensor",
        disabled_by=er.RegistryEntryDisabler.USER,
        hidden_by=er.RegistryEntryHider.USER,
    )
    foreign = _row(
        entity_registry,
        other,
        "sensor",
        build_site_unique_id("console_b", "site_shared", "active_vouchers"),
        "active_vouchers",
    )
    updates = async_rekey_site_entities(hass, entry, "console_new")
    assert updates == (
        (
            previous,
            build_site_unique_id("console_new", "site_shared", "active_vouchers"),
        ),
    )
    current = entity_registry.async_update_entity(
        previous.entity_id, new_unique_id=previous.unique_id
    )
    await hass.async_block_till_done()
    before = attr.asdict(current)
    foreign_before = attr.asdict(foreign)
    events = []
    unsubscribe = hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, events.append)
    try:
        with (
            patch.object(
                entity_registry,
                "async_update_entity",
                wraps=entity_registry.async_update_entity,
            ) as update,
            patch.object(
                entity_registry,
                "async_schedule_save",
                wraps=entity_registry.async_schedule_save,
            ) as save,
        ):
            assert async_restore_site_entity_identities(hass, entry, updates)
            assert async_restore_site_entity_identities(hass, entry, updates)
            await hass.async_block_till_done()
        update.assert_not_called()
        save.assert_not_called()
    finally:
        unsubscribe()
    assert not events
    assert attr.asdict(entity_registry.async_get(previous.entity_id)) == before
    assert attr.asdict(entity_registry.async_get(foreign.entity_id)) == foreign_before
    assert "Unable to fully roll back" not in caplog.text


@pytest.mark.parametrize(
    "failure",
    [
        "before_update",
        "after_update",
        "save_schedule",
        "restore_save_schedule",
        "reload_schedule",
        "after_reload_schedule",
    ],
)
async def test_native_entry_update_failure_compensates_both_state_owners(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    """Restore exact entry settings and owned rows across native update failures."""
    entry = _entry(hass, "site_fallback")
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://console.example",
        },
    )
    previous_data = dict(entry.data)
    previous_unique_id = entry.unique_id
    rows = [
        _row(
            entity_registry,
            entry,
            "sensor",
            build_site_unique_id("site_fallback", "site_shared", key),
            key,
        )
        for key in ("site_total_clients", "site_wired_clients")
    ]
    before = [attr.asdict(row) for row in rows]
    namespace = "aa:bb:cc:dd:ee:ff"
    manager = hass.config_entries
    native_update = manager.async_update_entry
    native_save = manager._async_schedule_save
    native_reload = manager.async_schedule_reload
    saves = 0
    updates = 0

    def update(entry: ConfigEntry, **kwargs: Any) -> bool:
        """Fail before or after the real native connection settings mutation."""
        nonlocal updates
        updates += 1
        if updates == 1:
            assert all(
                entity_registry.async_get(row.entity_id).unique_id.startswith(namespace)
                for row in rows
            )
            if failure == "before_update":
                err_msg = "Synthetic config update failure"
                raise RuntimeError(err_msg)
        result = native_update(entry, **kwargs)
        if updates == 1 and failure == "after_update":
            err_msg = "Synthetic config update failure"
            raise RuntimeError(err_msg)
        return result

    def save() -> None:
        """Raise after native entry mutation at initial or compensation scheduling."""
        nonlocal saves
        saves += 1
        if failure in {"save_schedule", "restore_save_schedule"} and (
            saves == 1 or failure == "restore_save_schedule"
        ):
            err_msg = "Synthetic config save scheduling failure"
            raise RuntimeError(err_msg)
        native_save()

    def reload(entry_id: str) -> None:
        """Fail either before or after the real native reload task is scheduled."""
        if failure == "after_reload_schedule":
            native_reload(entry_id)
        if failure in {"reload_schedule", "after_reload_schedule"}:
            err_msg = "Synthetic reload scheduling failure"
            raise RuntimeError(err_msg)
        native_reload(entry_id)

    with (
        patch.object(
            UnifiInsightsConfigFlow,
            "_async_validate_local_connection",
            AsyncMock(return_value=(True, None, {"mac": namespace})),
        ),
        patch.object(manager, "async_update_entry", side_effect=update),
        patch.object(manager, "_async_schedule_save", side_effect=save),
        patch.object(manager, "async_schedule_reload", side_effect=reload),
        patch(
            "custom_components.unifi_insights.async_setup_entry", return_value=True
        ) as setup,
        patch(
            "custom_components.unifi_insights.async_migrate_entry", return_value=True
        ),
    ):
        flow = await entry.start_reconfigure_flow(hass)
        result = await manager.flow.async_configure(
            flow["flow_id"],
            user_input={
                CONF_HOST: "https://new-console.example",
                CONF_API_KEY: "pending_credential",
                CONF_VERIFY_SSL: False,
            },
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "unknown"
    assert dict(entry.data) == previous_data
    assert entry.unique_id == previous_unique_id
    assert manager.async_entry_for_domain_unique_id(DOMAIN, namespace) is None
    for row, original in zip(rows, before, strict=True):
        current = attr.asdict(entity_registry.async_get(row.entity_id))
        assert {
            key: value
            for key, value in current.items()
            if key not in {"previous_unique_id", "modified_at"}
        } == {
            key: value
            for key, value in original.items()
            if key not in {"previous_unique_id", "modified_at"}
        }
    assert updates == (1 if failure == "before_update" else 2)
    assert ("Unable to fully restore saved connection settings" in caplog.text) == (
        failure == "restore_save_schedule"
    )
    assert setup.call_count == (1 if failure == "after_reload_schedule" else 0)


async def test_entry_compensation_does_not_claim_an_occupied_original_identity(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Refuse a foreign entry identity while still restoring eligible registry rows."""
    entry = _entry(hass)
    row = _row(
        entity_registry,
        entry,
        "sensor",
        "console_a_site_shared_active_vouchers",
        "active_vouchers",
    )
    flow = UnifiInsightsConfigFlow()
    flow.hass = hass
    native = hass.config_entries.async_update_entry
    foreign = None

    def update(entry: ConfigEntry, **kwargs: Any) -> bool:
        """Occupy the freed original identity after native entry mutation succeeds."""
        nonlocal foreign
        native(entry, **kwargs)
        foreign = _entry(hass, "console_a", "entry_b")
        err_msg = "Synthetic interference after native entry update"
        raise RuntimeError(err_msg)

    with (
        patch.object(hass.config_entries, "async_update_entry", side_effect=update),
        pytest.raises(RuntimeError, match="interference"),
    ):
        flow._async_update_site_entry_and_abort(
            entry,
            {**entry.data, CONF_CONSOLE_ID: "new"},
            "new",
            unique_id="new",
        )
    assert foreign is not None
    assert (
        hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, "console_a")
        is foreign
    )
    assert entry.unique_id == "new"
    assert entity_registry.async_get(row.entity_id).unique_id == row.unique_id
    assert "Cannot safely restore the saved connection identity" in caplog.text


async def test_successful_local_reauth_preserves_identity_and_updates_credential(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Exercise the local native reauth path without changing logical identities."""
    entry = _entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://console.example",
        },
    )
    row = _row(
        entity_registry,
        entry,
        "sensor",
        "console_a_site_shared_site_total_clients",
        "site_total_clients",
    )
    row = entity_registry.async_update_entity(
        row.entity_id, name="Custom logical sensor"
    )
    before = attr.asdict(row)
    with (
        patch.object(
            UnifiInsightsConfigFlow,
            "_async_validate_local_connection",
            AsyncMock(return_value=(True, None, {})),
        ),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
        patch(
            "custom_components.unifi_insights.async_migrate_entry", return_value=True
        ),
    ):
        flow = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], user_input={CONF_API_KEY: "pending_credential"}
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "pending_credential"
    assert entry.data[CONF_CONSOLE_ID] == entry.unique_id == "console_a"
    assert attr.asdict(entity_registry.async_get(row.entity_id)) == before


@pytest.mark.parametrize("legacy_id", ["stored:key", " STORED-KEY ", None, ""])
async def test_local_reauth_recovers_without_promoting_a_stored_credential(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, legacy_id: str | None
) -> None:
    """Rotate a legacy key while retaining native rows and awaiting safe backfill."""
    entry = _entry(hass, legacy_id)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://console.example.invalid",
            CONF_API_KEY: "stored:key",
        },
    )
    row = _row(
        entity_registry,
        entry,
        "sensor",
        "site_shared_site_total_clients",
        "site_total_clients",
    )
    row = entity_registry.async_update_entity(
        row.entity_id, name="Custom logical sensor"
    )
    before = attr.asdict(row)
    with (
        patch.object(
            UnifiInsightsConfigFlow,
            "_async_validate_local_connection",
            AsyncMock(return_value=(True, None, {})),
        ),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
        patch(
            "custom_components.unifi_insights.async_migrate_entry", return_value=True
        ),
    ):
        flow = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], user_input={CONF_API_KEY: "pending_credential"}
        )
        await hass.async_block_till_done()
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "pending_credential"
    assert CONF_CONSOLE_ID not in entry.data
    assert resolve_console_namespace(entry) is None
    assert attr.asdict(entity_registry.async_get(row.entity_id)) == before


async def test_local_reauth_keeps_pending_credential_namespace_conflict_guard(
    hass: HomeAssistant, entity_registry: er.EntityRegistry
) -> None:
    """Refuse to rotate a key into an existing safe console namespace."""
    entry = _entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://console.example.invalid",
        },
    )
    before = dict(entry.data)
    row = _row(
        entity_registry,
        entry,
        "sensor",
        "console_a_site_shared_site_total_clients",
        "site_total_clients",
    )
    with patch.object(
        UnifiInsightsConfigFlow,
        "_async_validate_local_connection",
        AsyncMock(return_value=(True, None, {})),
    ):
        flow = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], user_input={CONF_API_KEY: "CONSOLE_A"}
        )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "site_entity_identity_conflict"
    assert dict(entry.data) == before
    assert entity_registry.async_get(row.entity_id) == row


async def test_no_mac_setup_uses_distinct_hosts_for_a_shared_site(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, mock_coordinator
) -> None:
    """Backfill two legacy local consoles and discover independently owned rows."""

    async def refresh_sites(coordinator):
        """Expose the same non-default site on both synthetic consoles."""
        coordinator.data = {"sites": {"site_shared": {"id": "site_shared"}}}

    async def refresh_devices(coordinator):
        """Expose no usable console MAC."""
        coordinator.data = {"devices": {}}

    entries = []
    with (
        patch(
            "custom_components.unifi_insights.async_probe_network",
            AsyncMock(
                return_value=ProbeResult(
                    ProbeStatus.AVAILABLE, sites=[MagicMock(id="site_shared")]
                )
            ),
        ),
        patch(
            "custom_components.unifi_insights.async_probe_protect",
            AsyncMock(return_value=ProbeResult(ProbeStatus.UNSUPPORTED)),
        ),
        patch(
            "custom_components.unifi_insights.UnifiConfigCoordinator."
            "async_config_entry_first_refresh",
            autospec=True,
            side_effect=refresh_sites,
        ),
        patch(
            "custom_components.unifi_insights.UnifiDeviceCoordinator."
            "async_config_entry_first_refresh",
            autospec=True,
            side_effect=refresh_devices,
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
    ):
        for index in ("a", "b"):
            credential = f"synthetic_key_{index}"
            entry = _entry(hass, credential, f"entry_{index}")
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
                    CONF_HOST: f"https://Console{index.upper()}.Example.Invalid",
                    CONF_API_KEY: credential,
                    CONF_VERIFY_SSL: False,
                },
            )
            entry.mock_state(hass, config_entries.ConfigEntryState.LOADED)
            assert await async_setup_entry(hass, entry)
            assert (
                entry.data[CONF_CONSOLE_ID]
                == entry.unique_id
                == (f"https://console{index}.example.invalid")
            )
            await hass.config_entries.async_unload(entry.entry_id)
            entries.append(entry)
    mock_coordinator.protect_client = None
    mock_coordinator.data = {
        "sites": {"site_shared": {"id": "site_shared"}},
        "clients": {"site_shared": {}},
        "vouchers": {"site_shared": {}},
        "internet_activity": {
            "site_shared": {
                window: {"rx_bytes": 1, "tx_bytes": 2}
                for window in ("1h", "1d", "1w", "1m")
            }
        },
    }
    identities = []
    for entry in entries:
        entities = await _discover_all(hass, entry, mock_coordinator)
        identities.append({entity.unique_id for entity in entities})
        for entity in entities:
            domain = next(
                domain
                for domain, keys in SITE_ENTITY_SUFFIXES.items()
                if entity.translation_key in keys
            )
            row = _row(
                entity_registry,
                entry,
                domain,
                entity.unique_id,
                entity.translation_key,
            )
            assert row.config_entry_id == entry.entry_id
    expected_count = sum(len(keys) for keys in SITE_ENTITY_SUFFIXES.values())
    assert len(identities[0]) == len(identities[1]) == expected_count
    assert identities[0].isdisjoint(identities[1])


@pytest.mark.parametrize("credential_match", ["stored", "pending"])
async def test_local_reconfigure_clears_credential_based_current_identity(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    credential_match: str,
) -> None:
    """Discard a credential-shaped current ID without weakening pending-key guards."""
    namespace = "STORED-KEY" if credential_match == "stored" else "PENDING-KEY"
    entry = _entry(hass, namespace)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://console.example",
            CONF_API_KEY: "stored:key",
        },
    )
    previous_data = dict(entry.data)
    previous_unique_id = entry.unique_id
    key = "site_total_clients"
    uid = (
        build_legacy_site_unique_id("site_shared", key)
        if credential_match == "stored"
        else build_site_unique_id("pending:key", "site_shared", key)
    )
    row = _row(entity_registry, entry, "sensor", uid, key)
    before = attr.asdict(row)
    discovered = "aa:bb:cc:dd:ee:ff"
    with (
        patch.object(
            UnifiInsightsConfigFlow,
            "_async_validate_local_connection",
            AsyncMock(return_value=(True, None, {"mac": discovered})),
        ),
        patch("custom_components.unifi_insights.async_setup_entry", return_value=True),
        patch(
            "custom_components.unifi_insights.async_migrate_entry", return_value=True
        ),
    ):
        flow = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            user_input={
                CONF_HOST: "https://new-console.example",
                CONF_API_KEY: "pending:key",
                CONF_VERIFY_SSL: False,
            },
        )
        await hass.async_block_till_done()
    if credential_match == "stored":
        assert result["type"] == FlowResultType.ABORT
        assert entry.data[CONF_CONSOLE_ID] == entry.unique_id == discovered
    else:
        assert result["type"] == FlowResultType.FORM
        assert result["errors"]["base"] == "site_entity_identity_conflict"
        assert dict(entry.data) == previous_data
        assert entry.unique_id == previous_unique_id
    assert attr.asdict(entity_registry.async_get(row.entity_id)) == before


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "account_conflict",
        "target_conflict",
        "registry_failure",
        "entry_failure",
    ],
)
async def test_local_reconfigure_promotes_host_namespace_with_native_preservation(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    device_registry,
    area_registry,
    outcome: str,
) -> None:
    """Promote a stored HTTPS identity while preserving native rows and compensation."""
    namespace = "https://console.example"
    discovered = "aa:bb:cc:dd:ee:ff"
    entry = _entry(hass, namespace)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: namespace,
            CONF_VERIFY_SSL: False,
        },
    )
    before_data = dict(entry.data)
    area = area_registry.async_create("Synthetic area")
    device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "physical_device")}
    )
    rows = []
    for key in ("site_total_clients", "site_wired_clients"):
        row = _row(
            entity_registry,
            entry,
            "sensor",
            build_site_unique_id(namespace, "site_shared", key),
            key,
            device_id=device.id,
        )
        rows.append(
            entity_registry.async_update_entity(
                row.entity_id,
                name=f"Custom {key}",
                area_id=area.id,
                icon="mdi:account",
                disabled_by=er.RegistryEntryDisabler.USER,
                hidden_by=er.RegistryEntryHider.USER,
            )
        )
    before_rows = [attr.asdict(row) for row in rows]
    other_namespace = discovered if outcome == "account_conflict" else "console_b"
    other = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONSOLE_ID: other_namespace, CONF_API_KEY: "other_credential"},
        entry_id="entry_b",
        unique_id=other_namespace,
        disabled_by=config_entries.ConfigEntryDisabler.USER,
    )
    other.add_to_hass(hass)
    foreign = _row(
        entity_registry,
        other,
        "sensor",
        build_site_unique_id(
            discovered if outcome == "target_conflict" else "console_b",
            "site_shared",
            "site_wired_clients",
        ),
        "site_wired_clients",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    before_foreign = attr.asdict(foreign)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.sites.get_all = AsyncMock(
        return_value=[SimpleNamespace(id="site_shared", name="Shared site")]
    )
    client.devices.get_all = AsyncMock(
        return_value=[SimpleNamespace(mac=discovered, is_gateway=True)]
    )
    native_registry_update = entity_registry.async_update_entity
    native_entry_update = hass.config_entries.async_update_entry
    registry_updates = 0
    entry_updates = 0

    def update_registry(*args: Any, **kwargs: Any) -> er.RegistryEntry:
        """Raise after the second real registry mutation to exercise restoration."""
        nonlocal registry_updates
        registry_updates += 1
        result = native_registry_update(*args, **kwargs)
        if outcome == "registry_failure" and registry_updates == 2:
            err_msg = "Synthetic registry mutation failure"
            raise RuntimeError(err_msg)
        return result

    def update_entry(entry: ConfigEntry, **kwargs: Any) -> bool:
        """Raise after a real config-entry mutation to compensate both state owners."""
        nonlocal entry_updates
        entry_updates += 1
        result = native_entry_update(entry, **kwargs)
        if outcome == "entry_failure" and entry_updates == 1:
            err_msg = "Synthetic config-entry mutation failure"
            raise RuntimeError(err_msg)
        return result

    with (
        patch(
            "custom_components.unifi_insights.config_flow.UniFiNetworkClient",
            return_value=client,
        ),
        patch.object(
            entity_registry, "async_update_entity", side_effect=update_registry
        ),
        patch.object(
            hass.config_entries, "async_update_entry", side_effect=update_entry
        ),
        patch(
            "custom_components.unifi_insights.async_setup_entry", return_value=True
        ) as setup,
        patch("custom_components.unifi_insights.async_unload_entry", return_value=True),
        patch(
            "custom_components.unifi_insights.async_migrate_entry", return_value=True
        ),
    ):
        flow = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            user_input={
                CONF_HOST: "https://new-console.example",
                CONF_API_KEY: "pending_credential",
                CONF_VERIFY_SSL: True,
            },
        )
        await hass.async_block_till_done()
        if entry.state is config_entries.ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(entry.entry_id)
    client.sites.get_all.assert_awaited_once_with(expected_unsupported=True)
    client.devices.get_all.assert_awaited_once_with(site_id="site_shared")
    assert attr.asdict(entity_registry.async_get(foreign.entity_id)) == before_foreign
    for row, before in zip(rows, before_rows, strict=True):
        current = attr.asdict(entity_registry.async_get(row.entity_id))
        expected = {
            **before,
            "unique_id": (
                build_site_unique_id(discovered, "site_shared", row.translation_key)
                if outcome == "success"
                else row.unique_id
            ),
        }
        assert {
            key: value
            for key, value in current.items()
            if key not in {"previous_unique_id", "modified_at"}
        } == {
            key: value
            for key, value in expected.items()
            if key not in {"previous_unique_id", "modified_at"}
        }
    if outcome == "success":
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_CONSOLE_ID] == entry.unique_id == discovered
        assert entry.data[CONF_API_KEY] == "pending_credential"
        assert setup.call_count == 1
        assert registry_updates == 2
    else:
        assert dict(entry.data) == before_data
        assert entry.unique_id == namespace
        setup.assert_not_called()
        if outcome == "account_conflict":
            assert result["type"] == FlowResultType.ABORT
            assert result["reason"] == "already_configured"
        else:
            assert result["type"] == FlowResultType.FORM
            assert result["errors"]["base"] == (
                "site_entity_identity_conflict"
                if outcome == "target_conflict"
                else "unknown"
            )
        assert registry_updates == (
            0 if outcome in {"account_conflict", "target_conflict"} else 4
        )


@pytest.mark.parametrize(
    ("stored", "mismatch"),
    [
        ("aa:bb:cc:dd:ee:ff", False),
        ("AA:BB:CC:DD:EE:FF", False),
        ("AA-BB-CC-DD-EE-FF", False),
        ("aabbccddeeff", False),
        ("11:22:33:44:55:66", True),
    ],
)
async def test_local_reconfigure_compares_normalized_hardware_addresses(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    stored: str,
    *,
    mismatch: bool,
) -> None:
    """Accept equivalent hardware spellings and reject a genuinely different MAC."""
    entry = _entry(hass, stored)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CONNECTION_TYPE: CONNECTION_TYPE_LOCAL,
            CONF_HOST: "https://console.example",
        },
    )
    previous_data = dict(entry.data)
    row = _row(
        entity_registry,
        entry,
        "sensor",
        build_site_unique_id(
            normalize_console_namespace(stored), "site_shared", "site_total_clients"
        ),
        "site_total_clients",
    )
    before = attr.asdict(row)
    discovered = "aa:bb:cc:dd:ee:ff"
    with (
        patch.object(
            UnifiInsightsConfigFlow,
            "_async_validate_local_connection",
            AsyncMock(return_value=(True, None, {"mac": discovered})),
        ),
        patch(
            "custom_components.unifi_insights.async_setup_entry", return_value=True
        ) as setup,
        patch("custom_components.unifi_insights.async_unload_entry", return_value=True),
        patch(
            "custom_components.unifi_insights.async_migrate_entry", return_value=True
        ),
    ):
        flow = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            user_input={
                CONF_HOST: "https://new-console.example",
                CONF_API_KEY: "pending_credential",
                CONF_VERIFY_SSL: False,
            },
        )
        await hass.async_block_till_done()
        if entry.state is config_entries.ConfigEntryState.LOADED:
            assert await hass.config_entries.async_unload(entry.entry_id)
    assert result["type"] == FlowResultType.ABORT
    if mismatch:
        assert result["reason"] == "account_mismatch"
        assert dict(entry.data) == previous_data
        assert entry.unique_id == stored
        assert attr.asdict(entity_registry.async_get(row.entity_id)) == before
        setup.assert_not_called()
    else:
        assert result["reason"] == "reconfigure_successful"
        assert entry.data[CONF_CONSOLE_ID] == entry.unique_id == discovered
        assert entity_registry.async_get(
            row.entity_id
        ).unique_id == build_site_unique_id(
            discovered, "site_shared", "site_total_clients"
        )


@pytest.mark.parametrize(
    ("port_name", "port_label"), [("Port 25", "SFP+ 25"), ("Uplink", "Uplink")]
)
async def test_affected_entities_retain_registry_identities_after_setup(
    hass, entity_registry, mock_coordinator, port_name, port_label
):
    """Add every renamed family to HA and reuse its existing registry identity."""
    entry = _entry(hass, "console_a", "entry_a")
    hass.config_entries.async_update_entry(entry, options={CONF_CLIENT_CONTROL: True})
    mock_coordinator.hass = hass
    mock_coordinator.config_entry = entry
    mock_coordinator.async_add_listener.return_value = lambda: None
    mock_coordinator.async_get_wake_history = AsyncMock(return_value={})
    mock_coordinator.get_site.return_value = {"meta": {"name": "Default"}}
    mock_coordinator.data.update(
        {
            "sites": {"site_shared": {"id": "site_shared", "name": "Default"}},
            "devices": {
                "site_shared": {
                    "device1": {
                        "id": "device1",
                        "name": "Switch",
                        "state": "ONLINE",
                        "model": "USW-24",
                        "mac": "aa:bb:cc:dd:ee:ff",
                        "ports": [
                            {
                                "idx": 25,
                                "name": port_name,
                                "media": "SFP+",
                                "sfp_found": True,
                                "state": "UP",
                                "speedMbps": 10000,
                            }
                        ],
                    }
                }
            },
            "clients": {
                "site_shared": {
                    "client1": {
                        "id": "client1",
                        "name": "Laptop",
                        "type": "WIRELESS",
                        "uplinkDeviceId": "device1",
                    }
                }
            },
            "wifi": {
                "site_shared": {
                    "wifi1": {
                        "id": "wifi1",
                        "name": "Guests",
                        "qr_code": "WIFI:S:Guests;;",
                    }
                }
            },
            "vouchers": {},
            "internet_activity": {
                "site_shared": {
                    window: {"rx_bytes": 100, "tx_bytes": 200}
                    for window in ("1h", "1d", "1w", "1m")
                }
            },
        }
    )
    mock_coordinator.data["protect"]["sensors"] = {
        "sensor1": {
            "id": "sensor1",
            "name": "Environment",
            "state": "CONNECTED",
            "stats": {
                name: {"value": 20} for name in ("temperature", "humidity", "light")
            },
            "batteryStatus": {"percentage": 90},
        }
    }
    mock_coordinator.data["protect"]["nvrs"] = {
        "nvr1": {
            "id": "nvr1",
            "name": "NVR",
            "state": "CONNECTED",
            "storage_used_bytes": 1024**3,
            "storage_total_bytes": 2 * 1024**3,
        }
    }
    entry.runtime_data = SimpleNamespace(
        coordinator=mock_coordinator, mobility_coordinator=None, access_coordinator=None
    )

    # Use independent, pre-change unique IDs and deliberately customized entity
    # IDs. Reading the same registry rows is insufficient: every target must
    # become a live entity on its real HA EntityPlatform below.
    expected = [
        ("sensor", build_site_unique_id("console_a", "site_shared", desc.key), desc.key)
        for desc in (*SITE_CLIENT_SENSOR_TYPES, *SITE_INTERNET_ACTIVITY_SENSOR_TYPES)
    ]
    expected.extend(
        ("sensor", f"unifi_insights_sensor_sensor1_{desc.key}", desc.key)
        for desc in PROTECT_SENSOR_TYPES
    )
    expected.extend(
        ("sensor", f"unifi_insights_nvr_nvr1_{desc.key}", desc.key)
        for desc in NVR_SENSOR_TYPES
    )
    expected.extend(
        [
            ("button", "site_shared_device1_device_restart", "device_restart"),
            ("binary_sensor", "device1_port_sfp_present_25", "port_sfp_present"),
            ("sensor", "device1_port_speed_25", "port_speed"),
            ("sensor", "site_shared_wifi1_client_count", "wifi_client_count"),
            ("image", "site_shared_wifi1_qr_code", "wifi_qr_code"),
            ("button", "site_shared_client1_reconnect", "client_reconnect"),
        ]
    )
    rows = [
        entity_registry.async_get_or_create(
            domain,
            DOMAIN,
            unique_id,
            config_entry=entry,
            translation_key=key,
            suggested_object_id=f"custom_existing_{index}",
            disabled_by=None,
        )
        for index, (domain, unique_id, key) in enumerate(expected)
    ]
    platforms = {}
    try:
        for domain, module in (
            ("sensor", sensor_platform),
            ("button", button_platform),
            ("binary_sensor", binary_sensor_platform),
            ("image", image_platform),
        ):
            platform = MockEntityPlatform(hass, domain=domain, platform_name=DOMAIN)
            platform.config_entry = entry
            platforms[domain] = platform
            added = []
            await module.async_setup_entry(hass, entry, added.extend)
            await platform.async_add_entities(added)
        await hass.async_block_till_done()

        for row in rows:
            # Both assertions fail if discovery skips an entity or changes its
            # unique ID, even if the old registry row is still present.
            entity = platforms[row.domain].entities[row.entity_id]
            assert entity.entity_id == row.entity_id
            assert entity.unique_id == row.unique_id
            assert hass.states.get(row.entity_id) is not None
            current = entity_registry.async_get(row.entity_id)
            assert current.unique_id == row.unique_id
            assert current.entity_id == row.entity_id
            assert current.id == row.id

        sfp = platforms["binary_sensor"].entities[
            entity_registry.async_get_entity_id(
                "binary_sensor", DOMAIN, "device1_port_sfp_present_25"
            )
        ]
        speed = next(
            entity
            for entity in platforms["sensor"].entities.values()
            if entity.unique_id == "device1_port_speed_25"
        )
        assert (
            sfp.translation_placeholders
            == speed.translation_placeholders
            == {"port_label": port_label}
        )
    finally:
        for platform in platforms.values():
            await platform.async_reset()
