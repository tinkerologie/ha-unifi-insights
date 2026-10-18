# Copyright 2026 UniFi Insights contributors
"""Tests for translation key completeness.

`translations/en.json` is the file Home Assistant actually loads at
runtime for a custom integration; `strings.json` is the source of truth
maintainers edit. These two files had drifted apart for the Protect
binary_sensor entities specifically - see
`test_binary_sensor_camera_and_sensor_translation_keys_present_in_en_json`.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from string import Formatter
from typing import Final

import pytest
import yaml

from custom_components.unifi_insights.binary_sensor import BINARY_SENSOR_TYPES
from custom_components.unifi_insights.coordinators.rule_audit import (
    RULE_CHANGE_TYPES,
)
from custom_components.unifi_insights.mobility_entity import (
    LOCATION_DESCRIPTION,
    ROUTER_SENSORS,
    WORKSPACE_SENSORS,
)
from custom_components.unifi_insights.network_rule_switch import (
    NETWORK_RULE_SWITCH_TYPES,
)
from custom_components.unifi_insights.protect_security_entity import (
    SECURITY_BINARY_SENSOR_TYPES,
    SECURITY_SENSOR_TYPES,
)
from custom_components.unifi_insights.site_internet_activity_sensor import (
    SITE_INTERNET_ACTIVITY_SENSOR_TYPES,
)
from custom_components.unifi_insights.storage_entity import (
    STORAGE_NEARLY_FULL_DESCRIPTION,
    STORAGE_SENSOR_TYPES,
)

_INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "unifi_insights"
_EN_JSON = _INTEGRATION_DIR / "translations" / "en.json"
_STRINGS_JSON = _INTEGRATION_DIR / "strings.json"
_ICONS_JSON = _INTEGRATION_DIR / "icons.json"


def test_binary_sensor_camera_and_sensor_translation_keys_present_in_en_json() -> None:
    """Every camera_*/sensor_* binary_sensor translation_key must resolve
    in translations/en.json, matching strings.json.

    Regression test: translations/en.json's entity.binary_sensor section
    had only 6 keys (device_status, door, doorbell, is_dark, is_recording,
    motion) while strings.json correctly defined camera_motion,
    camera_person_detection, etc. HA falls back to the device_class name
    ("Motion") when a translation_key lookup fails, so all four per-camera
    detection sensors rendered as "Motion" and collided into _2/_3/_4
    entity_id suffixes.
    """
    en_data = json.loads(_EN_JSON.read_text())
    en_keys = set(en_data["entity"]["binary_sensor"].keys())

    camera_and_sensor_keys = {
        description.translation_key
        for description in BINARY_SENSOR_TYPES
        if description.translation_key
        and description.translation_key.startswith(("camera_", "sensor_"))
    }

    missing = camera_and_sensor_keys - en_keys
    assert missing == set()


def test_en_json_camera_and_sensor_names_match_strings_json() -> None:
    """The values added to en.json must mirror strings.json exactly, not
    just exist as keys - a mismatched display name would be its own bug.
    """
    en_data = json.loads(_EN_JSON.read_text())
    strings_data = json.loads(_STRINGS_JSON.read_text())
    en_binary_sensor = en_data["entity"]["binary_sensor"]
    strings_binary_sensor = strings_data["entity"]["binary_sensor"]

    for key, value in strings_binary_sensor.items():
        if key.startswith(("camera_", "sensor_")):
            assert key in en_binary_sensor, f"{key} missing from translations/en.json"
            assert en_binary_sensor[key] == value


_PLATFORMS: Final = {
    "alarm_control_panel",
    "binary_sensor",
    "button",
    "camera",
    "cover",
    "device_tracker",
    "event",
    "image",
    "light",
    "lock",
    "media_player",
    "number",
    "select",
    "sensor",
    "siren",
    "switch",
    "update",
}

_DESC_CLASS_TO_PLATFORM: Final = {
    "BinarySensorEntityDescription": "binary_sensor",
    "UnifiInsightsBinarySensorEntityDescription": "binary_sensor",
    "UnifiProtectSecurityBinarySensorEntityDescription": "binary_sensor",
    "SensorEntityDescription": "sensor",
    "UnifiInsightsSensorEntityDescription": "sensor",
    "UnifiProtectSensorEntityDescription": "sensor",
    "UnifiProtectSecuritySensorEntityDescription": "sensor",
    "UnifiSiteInternetActivitySensorEntityDescription": "sensor",
    "UnifiStorageSensorEntityDescription": "sensor",
    "UnifiVoucherSensorEntityDescription": "sensor",
    "CarrierFabricAggregateSensorDescription": "sensor",
    "UnifiMobilitySensorEntityDescription": "sensor",
    "UnifiMobilityWorkspaceSensorEntityDescription": "sensor",
    "ButtonEntityDescription": "button",
    "UnifiInsightsButtonEntityDescription": "button",
    "SwitchEntityDescription": "switch",
    "UnifiInsightsSwitchEntityDescription": "switch",
    "UnifiNetworkRuleSwitchEntityDescription": "switch",
    "SelectEntityDescription": "select",
    "NumberEntityDescription": "number",
    "UnifiInsightsNumberEntityDescription": "number",
    "EventEntityDescription": "event",
    "UpdateEntityDescription": "update",
    "CoverEntityDescription": "cover",
    "LockEntityDescription": "lock",
    "ImageEntityDescription": "image",
    "UnifiInsightsImageEntityDescription": "image",
}

_MODULE_DEFAULT_PLATFORM: Final = {
    "carrier_fabric_sensor": "sensor",
    "mobility_entity": "sensor",
    "network_rule_switch": "switch",
    "network_rule_audit_event": "event",
    "site_internet_activity_sensor": "sensor",
    "storage_entity": "sensor",
    "voucher_sensor": "sensor",
    "innerspace_entity": "sensor",
}

# Explicit allow-list for finite f-string keys.
# In switch.py (UnifiAccessEmergencySwitch.__init__):
#   (constructs access_emergency_lockdown and access_emergency_evacuation)
# where mode is either "lockdown" or "evacuation".
_ALLOW_LISTED_DYNAMIC_ENTITY_KEYS: Final = {
    ("switch", "access_emergency_lockdown"),
    ("switch", "access_emergency_evacuation"),
}


def _collect_live_entity_translation_keys() -> tuple[
    set[tuple[str, str]], list[tuple[str, int, str]]
]:
    """Integration-wide AST collector of (platform, translation_key)."""
    constants: dict[str, str] = {}
    for py_file in _INTEGRATION_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    ):
                        constants[target.id] = node.value.value

    inventory: set[tuple[str, str]] = set()
    dynamic_unresolved: list[tuple[str, int, str]] = []

    entity_modules = [
        f
        for f in _INTEGRATION_DIR.glob("*.py")
        if f.stem in _PLATFORMS
        or f.stem in _MODULE_DEFAULT_PLATFORM
        or f.stem == "protect_security_entity"
    ]

    for py_file in entity_modules:
        module_name = py_file.stem
        default_platform = (
            module_name
            if module_name in _PLATFORMS
            else _MODULE_DEFAULT_PLATFORM.get(module_name)
        )
        tree = ast.parse(py_file.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_func = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                if call_func in _DESC_CLASS_TO_PLATFORM or (
                    isinstance(call_func, str)
                    and call_func.endswith("EntityDescription")
                ):
                    call_platform = _DESC_CLASS_TO_PLATFORM.get(
                        call_func, default_platform
                    )
                    if (
                        call_func == "EntityDescription"
                        and module_name == "mobility_entity"
                    ):
                        call_platform = "device_tracker"
                    for kw in node.keywords:
                        if kw.arg in ("translation_key", "unnamed_translation_key"):
                            if (
                                isinstance(kw.value, ast.Constant)
                                and isinstance(kw.value.value, str)
                                and call_platform
                            ):
                                inventory.add((call_platform, kw.value.value))
                            elif (
                                isinstance(kw.value, ast.Name)
                                and kw.value.id in constants
                                and call_platform
                            ):
                                inventory.add((call_platform, constants[kw.value.id]))
                            elif not (
                                isinstance(kw.value, ast.Constant)
                                and kw.value.value is None
                            ):
                                dynamic_unresolved.append(
                                    (py_file.name, node.lineno, ast.unparse(kw.value))
                                )

            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    attr_name = None
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "_attr_translation_key"
                    ):
                        attr_name = target.id
                    elif (
                        isinstance(target, ast.Attribute)
                        and target.attr == "_attr_translation_key"
                    ):
                        attr_name = target.attr
                    if attr_name:
                        plat = default_platform
                        if isinstance(node.value, ast.Constant) and isinstance(
                            node.value.value, str
                        ):
                            if plat:
                                inventory.add((plat, node.value.value))
                        elif (
                            isinstance(node.value, ast.Name)
                            and node.value.id in constants
                        ):
                            if plat:
                                inventory.add((plat, constants[node.value.id]))
                        elif (
                            module_name == "switch"
                            and isinstance(node.value, ast.JoinedStr)
                            and ast.unparse(node.value) == "f'access_emergency_{mode}'"
                        ):
                            inventory |= _ALLOW_LISTED_DYNAMIC_ENTITY_KEYS
                        elif isinstance(
                            node.value, ast.Attribute
                        ) and node.value.attr in (
                            "translation_key",
                            "unnamed_translation_key",
                        ):
                            pass
                        else:
                            dynamic_unresolved.append(
                                (py_file.name, node.lineno, ast.unparse(node.value))
                            )

    return inventory, dynamic_unresolved


def _switch_translation_keys() -> set[str]:
    """Collect every live translation key for switch entities."""
    inventory, unresolved = _collect_live_entity_translation_keys()
    assert not unresolved, f"Unresolved dynamic translation keys: {unresolved}"
    return {key for plat, key in inventory if plat == "switch"}


def _switch_supplied_placeholders() -> set[str]:
    """Collect every key passed to placeholders in switch modules."""
    placeholders: set[str] = set()
    for fname in ("switch.py", "network_rule_switch.py"):
        tree = ast.parse((_INTEGRATION_DIR / fname).read_text(encoding="utf-8"))
        placeholders |= {
            key.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
            for target in node.targets
            if isinstance(target, ast.Attribute)
            and target.attr == "_attr_translation_placeholders"
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    return placeholders


def test_english_translation_entity_sections_match() -> None:
    """strings.json and translations/en.json entity sections must be identical."""
    en_entity = json.loads(_EN_JSON.read_text())["entity"]
    strings_entity = json.loads(_STRINGS_JSON.read_text())["entity"]
    assert en_entity == strings_entity


def test_translation_key_inventory_coverage_and_no_dead_keys() -> None:
    """Every live entity key must have a translation and no dead keys exist."""
    strings_entity = json.loads(_STRINGS_JSON.read_text())["entity"]
    strings_keys = {
        (plat, key) for plat, entries in strings_entity.items() for key in entries
    }

    live_inventory, unresolved = _collect_live_entity_translation_keys()
    assert not unresolved, f"Unresolved dynamic translation keys: {unresolved}"

    dead_keys = strings_keys - live_inventory
    assert not dead_keys, (
        f"Found dead translation keys in strings.json: {sorted(dead_keys)}"
    )

    untranslated_keys = live_inventory - strings_keys
    assert not untranslated_keys, (
        f"Missing translations for live keys: {sorted(untranslated_keys)}"
    )


def test_switch_translation_keys_resolve_in_both_files() -> None:
    """Every translation_key used in switch.py must resolve in both files.

    Regression guard for the naming change that moved the dynamically named
    switches (firewall rule, policy-based route, VPN client, client allow,
    WiFi) off `_attr_name` and onto translation keys. Those entities set
    `_attr_has_entity_name = True` and have no `entity_description`, so a
    missing key makes `Entity._name_internal` return `UNDEFINED` and the
    friendly name silently collapses to the *device* name - every switch on
    a gateway then renders identically and collides on entity_id, exactly
    the failure documented above for the Protect binary sensors.
    """
    en_switch = json.loads(_EN_JSON.read_text())["entity"]["switch"]
    strings_switch = json.loads(_STRINGS_JSON.read_text())["entity"]["switch"]

    for key in sorted(_switch_translation_keys()):
        assert key in strings_switch, f"{key} missing from strings.json"
        assert key in en_switch, f"{key} missing from translations/en.json"
        assert en_switch[key] == strings_switch[key], (
            f"{key} differs between strings.json and translations/en.json"
        )


def test_switch_translation_placeholders_are_supplied() -> None:
    """Placeholders required by a switch name must be supplied by the code.

    `Entity._substitute_name_placeholders` raises `HomeAssistantError` on a
    missing placeholder outside the stable release channel, so a typo here
    breaks the entity rather than degrading it.
    """
    strings_switch = json.loads(_STRINGS_JSON.read_text())["entity"]["switch"]
    supplied = _switch_supplied_placeholders()

    for key in sorted(_switch_translation_keys()):
        required = {
            field
            for _, field, _, _ in Formatter().parse(strings_switch[key]["name"])
            if field
        }
        missing = required - supplied
        assert missing == set(), (
            f"switch.{key} name needs placeholders {sorted(missing)} "
            "which switch.py never supplies"
        )


def test_site_internet_activity_sensor_translations_resolve_in_both_files() -> None:
    """Every site internet activity sensor must have matching translations."""
    en_sensor = json.loads(_EN_JSON.read_text())["entity"]["sensor"]
    strings_sensor = json.loads(_STRINGS_JSON.read_text())["entity"]["sensor"]

    for desc in SITE_INTERNET_ACTIVITY_SENSOR_TYPES:
        key = desc.translation_key
        assert key is not None
        assert key in strings_sensor, f"{key} missing from strings.json"
        assert key in en_sensor, f"{key} missing from translations/en.json"
        assert en_sensor[key] == strings_sensor[key], (
            f"{key} differs between strings.json and translations/en.json"
        )


def test_mobility_translations_and_icons_resolve_in_every_file() -> None:
    """Every Mobility entity has a name, enum state names and an icon."""
    files = {
        "strings.json": json.loads(_STRINGS_JSON.read_text())["entity"],
        "translations/en.json": json.loads(_EN_JSON.read_text())["entity"],
    }
    icons = json.loads(_ICONS_JSON.read_text())["entity"]
    descriptions = [
        ("sensor", desc) for desc in (*ROUTER_SENSORS, *WORKSPACE_SENSORS)
    ] + [("device_tracker", LOCATION_DESCRIPTION)]

    for platform, desc in descriptions:
        key = desc.translation_key
        assert key is not None, desc.key
        assert key.startswith("mobility_"), key
        for name, entity in files.items():
            translation = entity.get(platform, {}).get(key)
            assert translation, f"{platform}.{key} missing from {name}"
            assert translation["name"], f"{platform}.{key} has no name in {name}"
            for option in getattr(desc, "options", None) or []:
                assert translation.get("state", {}).get(option), (
                    f"{platform}.{key} state {option} missing from {name}"
                )
        assert (
            files["strings.json"][platform][key]
            == (files["translations/en.json"][platform][key])
        )
        assert icons.get(platform, {}).get(key, {}).get("default"), (
            f"{platform}.{key} has no icon"
        )


def test_protect_security_entity_translations_resolve_in_both_files() -> None:
    """Alarm hub/Thread/fob entities resolve, ENUM states included."""
    en = json.loads(_EN_JSON.read_text())["entity"]
    strings = json.loads(_STRINGS_JSON.read_text())["entity"]

    for platform, descriptions in (
        ("binary_sensor", SECURITY_BINARY_SENSOR_TYPES),
        ("sensor", SECURITY_SENSOR_TYPES),
    ):
        for desc in descriptions:
            key = desc.translation_key
            assert key is not None
            assert key in strings[platform], f"{key} missing from strings.json"
            assert key in en[platform], f"{key} missing from translations/en.json"
            assert en[platform][key] == strings[platform][key], (
                f"{key} differs between strings.json and translations/en.json"
            )
            assert strings[platform][key]["name"]
            options = getattr(desc, "options", None)
            if options:
                assert set(strings[platform][key]["state"]) == set(options), key


def test_protect_security_entity_icons_defined() -> None:
    """Every alarm hub/Thread/fob entity has an icon in icons.json."""
    icons = json.loads(_ICONS_JSON.read_text())["entity"]

    for platform, descriptions in (
        ("binary_sensor", SECURITY_BINARY_SENSOR_TYPES),
        ("sensor", SECURITY_SENSOR_TYPES),
    ):
        for desc in descriptions:
            entry = icons.get(platform, {}).get(desc.translation_key)
            assert entry is not None, f"{desc.translation_key} has no icon"
            assert entry["default"].startswith("mdi:")


def test_voucher_translation_and_icon_keys_present() -> None:
    """Hotspot voucher entity keys exist in strings.json, en.json, and icons.json."""
    strings_data = json.loads(_STRINGS_JSON.read_text())["entity"]
    en_data = json.loads(_EN_JSON.read_text())["entity"]
    icons_data = json.loads(_ICONS_JSON.read_text())["entity"]

    expected_keys = {
        "number": [
            "voucher_duration",
            "voucher_guest_limit",
            "voucher_download_limit",
            "voucher_upload_limit",
            "voucher_data_limit",
        ],
        "button": [
            "generate_voucher",
        ],
        "sensor": [
            "latest_voucher_code",
            "latest_voucher_expiration",
            "active_vouchers",
        ],
        "image": [
            "voucher_qr_code",
        ],
    }

    for platform, keys in expected_keys.items():
        for key in keys:
            assert key in strings_data[platform], (
                f"{platform}.{key} missing from strings.json"
            )
            assert key in en_data[platform], (
                f"{platform}.{key} missing from translations/en.json"
            )
            assert strings_data[platform][key] == en_data[platform][key], (
                f"{platform}.{key} mismatch between strings.json and en.json"
            )
            assert key in icons_data[platform], (
                f"{platform}.{key} missing from icons.json"
            )
            icon_entry = icons_data[platform][key]
            assert "default" in icon_entry, f"{platform}.{key} missing default icon"
            assert icon_entry["default"].startswith("mdi:"), (
                f"{platform}.{key} invalid icon"
            )


def test_network_rule_switch_translations_resolve_in_both_files() -> None:
    """Network rule switch translations match in both files with placeholders."""
    en_switch = json.loads(_EN_JSON.read_text())["entity"]["switch"]
    strings_switch = json.loads(_STRINGS_JSON.read_text())["entity"]["switch"]

    for desc in NETWORK_RULE_SWITCH_TYPES:
        for key, placeholder in (
            (desc.translation_key, "{rule_name}"),
            (desc.unnamed_translation_key, "{rule_id}"),
        ):
            assert key in strings_switch, f"{key} missing from strings.json"
            assert key in en_switch, f"{key} missing from translations/en.json"
            assert en_switch[key] == strings_switch[key], (
                f"{key} differs between strings.json and translations/en.json"
            )
            assert placeholder in strings_switch[key]["name"], (
                f"{key} name must contain {placeholder}"
            )


def test_network_rule_switch_icons_defined() -> None:
    """Network rule switch icons are defined with default and state.off."""
    icons = json.loads(_ICONS_JSON.read_text())["entity"]["switch"]

    for desc in NETWORK_RULE_SWITCH_TYPES:
        for key in (desc.translation_key, desc.unnamed_translation_key):
            entry = icons.get(key)
            assert entry is not None, f"{key} has no icon entry in icons.json"
            assert entry.get("default", "").startswith("mdi:"), (
                f"{key} default icon missing or not mdi:"
            )
            assert entry.get("state", {}).get("off", "").startswith("mdi:"), (
                f"{key} state.off icon missing or not mdi:"
            )


def test_wake_exception_translations_match_in_both_files() -> None:
    """wake_on_lan_unavailable and wake_failed match in both translations files."""
    en_exc = json.loads(_EN_JSON.read_text())["exceptions"]
    strings_exc = json.loads(_STRINGS_JSON.read_text())["exceptions"]

    for key in ("wake_on_lan_unavailable", "wake_failed"):
        assert key in strings_exc, f"{key} missing from strings.json"
        assert key in en_exc, f"{key} missing from translations/en.json"
        assert en_exc[key] == strings_exc[key], (
            f"{key} differs between strings.json and translations/en.json"
        )
    assert "{client_name}" in strings_exc["wake_failed"]["message"]
    assert "{error}" in strings_exc["wake_failed"]["message"]


def test_chime_playback_error_and_action_metadata_match_in_both_files() -> None:
    """The retained action reports its unsupported behavior consistently."""
    strings = json.loads(_STRINGS_JSON.read_text())
    english = json.loads(_EN_JSON.read_text())
    key = "chime_playback_unsupported"
    assert strings["exceptions"][key] == english["exceptions"][key]
    assert "You cannot play" in strings["exceptions"][key]["message"]
    action = "play_chime_ringtone"
    assert strings["services"][action] == english["services"][action]
    assert "always raises" in strings["services"][action]["description"]
    field = strings["services"][action]["fields"]["ringtone_id"]
    assert "with or without" in field["description"]
    assert "automations stay valid" in field["description"]


def test_wake_button_translations_and_icon_resolve_in_both_files() -> None:
    """Wake button translation and icon exist in all files and options match."""
    en_btn = json.loads(_EN_JSON.read_text())["entity"]["button"]
    strings_btn = json.loads(_STRINGS_JSON.read_text())["entity"]["button"]
    icons_btn = json.loads(_ICONS_JSON.read_text())["entity"]["button"]

    assert "client_wake" in strings_btn
    assert "client_wake" in en_btn
    assert strings_btn["client_wake"]["name"] == "Wake {client_name}"
    assert en_btn["client_wake"]["name"] == "Wake {client_name}"
    assert icons_btn.get("client_wake", {}).get("default") == "mdi:power"

    en_opt = json.loads(_EN_JSON.read_text())["options"]["step"]["init"][
        "data_description"
    ]["client_control"]
    strings_opt = json.loads(_STRINGS_JSON.read_text())["options"]["step"]["init"][
        "data_description"
    ]["client_control"]
    assert en_opt == strings_opt
    assert "Wake-on-LAN" in en_opt


def test_storage_translations_and_icons_resolve_in_every_file() -> None:
    """Storage entities have matching translations, valid placeholders, and icons."""
    strings = json.loads(_STRINGS_JSON.read_text())["entity"]
    en = json.loads(_EN_JSON.read_text())["entity"]
    icons = json.loads(_ICONS_JSON.read_text())["entity"]

    items = [("sensor", desc.translation_key) for desc in STORAGE_SENSOR_TYPES] + [
        ("binary_sensor", STORAGE_NEARLY_FULL_DESCRIPTION.translation_key)
    ]

    for platform, key in items:
        assert key is not None
        assert key in strings[platform], f"{key} missing from strings.json"
        assert key in en[platform], f"{key} missing from translations/en.json"
        assert en[platform][key] == strings[platform][key], (
            f"{key} differs between strings.json and translations/en.json"
        )
        name = strings[platform][key]["name"]
        assert name
        placeholders = {field for _, field, _, _ in Formatter().parse(name) if field}
        assert placeholders <= {"mount_name"}, f"Unexpected placeholder in {name}"
        icon = icons.get(platform, {}).get(key, {}).get("default")
        assert icon is not None, f"{key} has no icon"
        assert icon.startswith("mdi:"), f"{key} icon invalid: {icon}"


def test_network_rule_change_event_translations_and_icon() -> None:
    """Network rule change events have matching translations and a valid icon."""
    strings_event = json.loads(_STRINGS_JSON.read_text())["entity"]["event"][
        "network_rule_changes"
    ]
    en_event = json.loads(_EN_JSON.read_text())["entity"]["event"][
        "network_rule_changes"
    ]
    icons_event = json.loads(_ICONS_JSON.read_text())["entity"]["event"][
        "network_rule_changes"
    ]

    assert strings_event == en_event
    assert set(strings_event["state_attributes"]["event_type"]["state"].keys()) == set(
        RULE_CHANGE_TYPES
    )
    assert icons_event["default"].startswith("mdi:")


def test_export_network_rules_action_strings_and_icon() -> None:
    """The export action has matching strings, exceptions and a valid icon."""
    strings = json.loads(_STRINGS_JSON.read_text())
    en = json.loads(_EN_JSON.read_text())
    services_yaml = yaml.safe_load((_INTEGRATION_DIR / "services.yaml").read_text())

    action = strings["services"]["export_network_rules"]
    assert action == en["services"]["export_network_rules"]
    assert set(action["fields"]) == {"site_id", "console_id", "rule_types", "redact"}
    assert set(action["fields"]) == set(services_yaml["export_network_rules"]["fields"])

    for key in (
        "export_console_required",
        "export_site_not_on_console",
        "export_no_sites",
        "export_network_rules_failed",
    ):
        assert strings["exceptions"][key]["message"]
        assert strings["exceptions"][key] == en["exceptions"][key]

    icons = json.loads(_ICONS_JSON.read_text())
    assert icons["services"]["export_network_rules"].startswith("mdi:")


def test_media_player_speaker_translations_and_icon() -> None:
    """The media_player speaker entity has matching translations and icons."""
    strings_data = json.loads(_STRINGS_JSON.read_text())
    en_data = json.loads(_EN_JSON.read_text())
    icons = json.loads(_ICONS_JSON.read_text())["entity"]

    strings = strings_data["entity"]
    en = en_data["entity"]

    assert "media_player" in strings
    assert "speaker" in strings["media_player"]
    assert strings["media_player"]["speaker"]["name"] == "Speaker"
    assert strings["media_player"] == en["media_player"]
    assert "media_player" in icons
    assert icons["media_player"]["speaker"]["default"].startswith("mdi:")

    for exc_key in (
        "media_not_audio",
        "talkback_session_invalid",
        "talkback_unsupported_codec_or_scheme",
        "talkback_stream_failure",
        "protect_not_available_for_talkback",
    ):
        assert exc_key in strings_data["exceptions"]
        assert exc_key in en_data["exceptions"]
        assert strings_data["exceptions"][exc_key] == en_data["exceptions"][exc_key]
        assert strings_data["exceptions"][exc_key]["message"]


def test_all_icons_json_entity_keys_match_live_inventory() -> None:
    """Every entity key in icons.json must correspond to a live translation key."""
    icons_entity = json.loads(_ICONS_JSON.read_text())["entity"]
    icon_keys = {
        (plat, key) for plat, entries in icons_entity.items() for key in entries
    }
    live_inventory, unresolved = _collect_live_entity_translation_keys()
    assert not unresolved, f"Unresolved dynamic translation keys: {unresolved}"
    dead_icon_keys = icon_keys - live_inventory
    assert not dead_icon_keys, (
        f"Found dead icon keys in icons.json: {sorted(dead_icon_keys)}"
    )


# Four state-dependent icon choices: firewall, VPN route, ordinary route, VPN
# client. The two route choices share one property in the current implementation.
_DYNAMIC_ICON_PROPERTIES: Final = {
    ("switch.py", "UnifiFirewallRuleSwitch"),
    ("switch.py", "UnifiInsightsPolicyBasedRouteSwitch"),
    ("switch.py", "UnifiInsightsVpnClientSwitch"),
}


def _static_icon_violations(source: str, filename: str) -> list[str]:
    """Reject icon overrides, including nonliteral values, outside reviewed icons."""
    tree = ast.parse(source, filename=filename)
    allowed_nodes: set[ast.AST] = set()
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for method in cls.body:
            if (
                isinstance(method, ast.FunctionDef)
                and method.name == "icon"
                and (filename, cls.name) in _DYNAMIC_ICON_PROPERTIES
                and any(
                    isinstance(decorator, ast.Name) and decorator.id == "property"
                    for decorator in method.decorator_list
                )
                and any(
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                    and node.attr == "is_on"
                    for node in ast.walk(method)
                )
            ):
                for node in ast.walk(method):
                    if isinstance(node, ast.Return):
                        allowed_nodes.update(ast.walk(node))

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            violations.extend(
                f"{filename}:{node.lineno}: icon override"
                for keyword in node.keywords
                if keyword.arg == "icon"
            )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                (isinstance(target, ast.Name) and target.id == "_attr_icon")
                or (isinstance(target, ast.Attribute) and target.attr == "_attr_icon")
                for target in targets
            ):
                violations.append(f"{filename}:{node.lineno}: _attr_icon override")
        elif (
            node not in allowed_nodes
            and isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("mdi:")
        ):
            violations.append(f"{filename}:{node.lineno}: static {node.value}")
    return violations


def test_no_static_mdi_strings_in_python_code() -> None:
    """Static icons belong in icons.json; only reviewed state icons may remain."""
    violations = [
        violation
        for py_file in _INTEGRATION_DIR.glob("**/*.py")
        for violation in _static_icon_violations(
            py_file.read_text(encoding="utf-8"), py_file.name
        )
    ]
    assert not violations, f"Found static icon overrides in Python: {violations}"


@pytest.mark.parametrize(
    "source",
    [
        '_attr_icon = "mdi:power"',
        "self._attr_icon = ICON",
        '_attr_icon = f"mdi:{kind}"',
        "_attr_icon: str = ICON",
        "SensorEntityDescription(key='test', icon=ICON)",
        "SensorEntityDescription(key='test', icon=f'mdi:{kind}')",
        'class NewSwitch:\n @property\n def icon(self):\n  return "mdi:power"',
        (
            "class UnifiFirewallRuleSwitch:\n @property\n"
            ' def icon(self):\n  return "mdi:power"'
        ),
    ],
)
def test_static_icon_guard_rejects_overrides(source: str) -> None:
    """Aliases, computed overrides and unreviewed properties cannot bypass guard."""
    assert _static_icon_violations(source, "switch.py")


@pytest.mark.parametrize(
    "override", ["self._attr_icon = ICON", "SensorEntityDescription(icon=ICON)"]
)
def test_static_icon_guard_rejects_overrides_inside_reviewed_properties(
    override: str,
) -> None:
    """Only returned state icons are exempt; overrides cannot hide in properties."""
    source = (
        "class UnifiFirewallRuleSwitch:\n @property\n def icon(self):\n"
        f"  {override}\n"
        '  return "mdi:power" if self.is_on else "mdi:power-off"'
    )
    assert _static_icon_violations(source, "switch.py")


@pytest.mark.parametrize(("filename", "class_name"), sorted(_DYNAMIC_ICON_PROPERTIES))
def test_static_icon_guard_allows_reviewed_state_properties(
    filename: str, class_name: str
) -> None:
    """Keep the reviewed switches' state-dependent Python icons."""
    source = (
        f"class {class_name}:\n @property\n def icon(self):\n"
        '  return "mdi:power" if self.is_on else "mdi:power-off"'
    )
    assert not _static_icon_violations(source, filename)


@pytest.mark.parametrize(
    ("module", "expression", "expected"),
    [
        ("switch", 'f"access_emergency_{mode}"', True),
        ("sensor", 'f"access_emergency_{mode}"', False),
        ("switch", 'f"unreviewed_{mode}"', False),
        ("sensor", 'f"sensor_{kind}"', False),
        ("event", "RULE_CHANGE_EVENT_KEY", True),
    ],
)
def test_translation_inventory_rejects_unreviewed_dynamic_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: str,
    expression: str,
    *,
    expected: bool,
) -> None:
    """Resolve reviewed emergency keys and constants; reject other dynamic keys."""
    (tmp_path / f"{module}.py").write_text(
        'RULE_CHANGE_EVENT_KEY = "network_rule_changes"\n'
        f"class Example:\n _attr_translation_key = {expression}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(__name__ + "._INTEGRATION_DIR", tmp_path)
    inventory, unresolved = _collect_live_entity_translation_keys()
    assert bool(unresolved) is not expected
    if expected:
        assert inventory == (
            _ALLOW_LISTED_DYNAMIC_ENTITY_KEYS
            if module == "switch"
            else {("event", "network_rule_changes")}
        )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ('"device_status"', True),
        ("RULE_CHANGE_EVENT_KEY", True),
        ("None", True),
        ('f"port_{idx}"', False),
        ("make_key()", False),
    ],
)
def test_translation_inventory_rejects_unresolved_description_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expression: str,
    *,
    expected: bool,
) -> None:
    """Description translation keys must resolve to a literal or constant."""
    (tmp_path / "sensor.py").write_text(
        'RULE_CHANGE_EVENT_KEY = "network_rule_changes"\n'
        f'DESC = SensorEntityDescription(key="k", translation_key={expression})\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(__name__ + "._INTEGRATION_DIR", tmp_path)
    _, unresolved = _collect_live_entity_translation_keys()
    assert bool(unresolved) is not expected


def test_outlet_icon_resource() -> None:
    """Outlet switches retain their effective icon through icon translations."""
    assert json.loads(_ICONS_JSON.read_text())["entity"]["switch"]["outlet"] == {
        "default": "mdi:power-socket-us"
    }
