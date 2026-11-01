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
import re
from pathlib import Path
from string import Formatter
from typing import Any, Final

import pytest
import yaml
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.unifi_insights.binary_sensor import (
    BINARY_SENSOR_TYPES,
    SECURITY_BINARY_SENSOR_TYPES,
    STORAGE_NEARLY_FULL_DESCRIPTION,
)
from custom_components.unifi_insights.const import (
    CAMERA_DIGITAL_INPUT_EVENT_TYPES,
    DIGITAL_INPUT_EVENT_TYPES_BY_STATE,
)
from custom_components.unifi_insights.coordinators.rule_audit import (
    RULE_CHANGE_TYPES,
)
from custom_components.unifi_insights.device_tracker import LOCATION_DESCRIPTION
from custom_components.unifi_insights.sensor import (
    RADIO_DETAILS_SENSOR_TYPES,
    RADIO_STATS_SENSOR_TYPES,
    ROUTER_SENSORS,
    SECURITY_SENSOR_TYPES,
    SITE_INTERNET_ACTIVITY_SENSOR_TYPES,
    STORAGE_SENSOR_TYPES,
    WORKSPACE_SENSORS,
)
from custom_components.unifi_insights.switch import (
    NETWORK_RULE_SWITCH_TYPES,
)

_INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "unifi_insights"
_EN_JSON = _INTEGRATION_DIR / "translations" / "en.json"
_STRINGS_JSON = _INTEGRATION_DIR / "strings.json"
_ICONS_JSON = _INTEGRATION_DIR / "icons.json"


def test_video_mode_options_have_readable_native_translations() -> None:
    """Video Mode uses valid HA state keys with matching English labels."""
    expected = {
        "default": "Normal",
        "high_fps": "High frame rate",
        "sport": "Fast motion",
        "slow_shutter": "Low light",
        "lpr_reflex": "License plates (reflective)",
        "lpr_none_reflex": "License plates (non-reflective)",
    }
    for path in (_STRINGS_JSON, _EN_JSON):
        strings = json.loads(path.read_text())
        assert strings["entity"]["select"]["video_mode"]["state"] == expected
        assert all(
            re.fullmatch(r"(?!.+[_-]{2})(?![_-])[a-z0-9-_]+(?<![_-])", key)
            for key in strings["entity"]["select"]["video_mode"]["state"]
        )


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

    # ADR-007: every entity lives in its platform module.
    entity_modules = [f for f in _INTEGRATION_DIR.glob("*.py") if f.stem in _PLATFORMS]

    for py_file in entity_modules:
        default_platform = py_file.stem
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
                            default_platform == "switch"
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
    for fname in ("switch.py",):
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
        # Rule switches name their placeholders in the description.
        placeholders |= {
            value.value
            for node in ast.walk(tree)
            for name, value in (
                [(kw.arg, kw.value) for kw in node.keywords]
                if isinstance(node, ast.Call)
                else [(getattr(node.target, "id", None), node.value)]
                if isinstance(node, ast.AnnAssign)
                else []
            )
            if name in ("name_placeholder", "id_placeholder")
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
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


def test_radio_sensor_translations_and_enum_states() -> None:
    """Every radio sensor has a name, and both enums a translated state each."""
    en_sensor = json.loads(_EN_JSON.read_text())["entity"]["sensor"]
    strings_sensor = json.loads(_STRINGS_JSON.read_text())["entity"]["sensor"]
    radio_descriptions = (*RADIO_STATS_SENSOR_TYPES, *RADIO_DETAILS_SENSOR_TYPES)

    enum_descriptions = 0
    for description in radio_descriptions:
        key = description.translation_key
        assert key is not None
        assert key in strings_sensor, f"{key} missing from strings.json"
        assert en_sensor[key] == strings_sensor[key], (
            f"{key} differs between strings.json and translations/en.json"
        )
        assert "{band}" in strings_sensor[key]["name"]
        if description.device_class != SensorDeviceClass.ENUM:
            continue
        enum_descriptions += 1
        assert description.options
        for states in (strings_sensor[key]["state"], en_sensor[key]["state"]):
            assert set(states) == set(description.options), (
                f"{key} states do not match its options"
            )
    assert enum_descriptions == 2
    assert set(en_sensor["radio_wlan_standard"]["state"].values()) == {
        "802.11a",
        "802.11b",
        "802.11g",
        "802.11n",
        "802.11ac",
        "802.11ax",
        "802.11be",
        "802.11ah",
    }


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
            (desc.translation_key, f"{{{desc.name_placeholder}}}"),
            (
                desc.unnamed_translation_key,
                f"{{{desc.id_placeholder}}}" if desc.id_placeholder else "",
            ),
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


def test_camera_digital_input_event_translations_and_icon() -> None:
    """Digital-input events have matching translations for both states and an icon."""
    strings_event = json.loads(_STRINGS_JSON.read_text())["entity"]["event"][
        "camera_digital_input"
    ]
    en_event = json.loads(_EN_JSON.read_text())["entity"]["event"][
        "camera_digital_input"
    ]
    icons_event = json.loads(_ICONS_JSON.read_text())["entity"]["event"][
        "camera_digital_input"
    ]

    assert strings_event == en_event
    assert strings_event["name"] == "Digital input {channel}"
    assert set(strings_event["state_attributes"]["event_type"]["state"]) == set(
        CAMERA_DIGITAL_INPUT_EVENT_TYPES
    )
    assert tuple(DIGITAL_INPUT_EVENT_TYPES_BY_STATE.values()) == (
        CAMERA_DIGITAL_INPUT_EVENT_TYPES
    )
    assert icons_event["default"].startswith("mdi:")


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
    # A policy-based route that sends traffic over a VPN shows the VPN icon.
    ("switch.py", "UnifiInsightsNetworkRuleSwitch"),
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
            "class UnifiInsightsNetworkRuleSwitch:\n @property\n"
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
        "class UnifiInsightsNetworkRuleSwitch:\n @property\n def icon(self):\n"
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


# --- Translated service-action errors (#339) ---------------------------------
#
# Every HomeAssistantError and ServiceValidationError the service actions build
# must name a translation key that exists in strings.json and en.json, with
# exactly the placeholders it supplies. Every construction counts, raised or
# returned. A key is a literal or each branch of a conditional expression; a
# helper that forwards a key parameter is checked at each of its call sites.

_SERVICE_ERROR_MODULES: Final = (
    "services.py",
    "service_targets.py",
    "coordinators/carrier_fabric.py",
)
_TRANSLATED_ERRORS: Final = frozenset(
    {"HomeAssistantError", "ServiceValidationError", "InvalidSubscriberIdError"}
)
# Placeholder alternatives: one frozenset per way the construction can be called.
_Placeholders = tuple[frozenset[str], ...]


def _called_name(node: ast.expr) -> str | None:
    """Return the name a call or raise uses, for ``X``, ``X(...)`` and ``m.X``."""
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _literal_keys(node: ast.expr | None) -> set[str] | None:
    """Resolve a literal key or every branch of a conditional; else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        body, orelse = _literal_keys(node.body), _literal_keys(node.orelse)
        return None if body is None or orelse is None else body | orelse
    return None


def _dict_placeholders(node: ast.expr | None) -> _Placeholders | None:
    """Resolve a literal placeholder dict (or None, or a conditional of those)."""
    if node is None or (isinstance(node, ast.Constant) and node.value is None):
        return (frozenset(),)
    if isinstance(node, ast.Dict) and all(
        isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys
    ):
        keys: set[str] = set()
        for key in node.keys:
            assert isinstance(key, ast.Constant)
            assert isinstance(key.value, str)
            keys.add(key.value)
        return (frozenset(keys),)
    if isinstance(node, ast.IfExp):
        body, orelse = _dict_placeholders(node.body), _dict_placeholders(node.orelse)
        return None if body is None or orelse is None else body + orelse
    return None


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    """Return a call's keyword argument value, or None."""
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


def _param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """Return a function's positional and keyword parameter names, in order."""
    args = func.args
    return [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]


def _functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return every function in a module, nested ones included."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _enclosing(
    tree: ast.Module,
) -> dict[ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None]:
    """Map each node to its innermost enclosing function."""
    owner: dict[ast.AST, ast.FunctionDef | ast.AsyncFunctionDef | None] = {}

    def visit(node: ast.AST, func: ast.FunctionDef | ast.AsyncFunctionDef | None):
        for child in ast.iter_child_nodes(node):
            owner[child] = func
            inner = (
                child
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else func
            )
            visit(child, inner)

    visit(tree, None)
    return owner


class _Factory:
    """A helper that builds a translated error from its ``key`` parameter."""

    def __init__(
        self,
        func: ast.FunctionDef | ast.AsyncFunctionDef,
        key_param: str,
        placeholders: _Placeholders | None,
    ) -> None:
        self.params = _param_names(func)
        self.key_param = key_param
        # None: placeholders are the call site's own **kwargs (forwarded).
        self.placeholders = placeholders

    def key_arg(self, call: ast.Call) -> ast.expr | None:
        """Return the expression a call site passes for the key parameter."""
        index = self.params.index(self.key_param)
        if index < len(call.args):
            return call.args[index]
        return _keyword(call, self.key_param)


def _local_dict_keys(
    func: ast.FunctionDef | ast.AsyncFunctionDef | None, name: str
) -> frozenset[str] | None:
    """Return the keys of the one dict literal a function binds to ``name``.

    A name bound more than once, or changed through a subscript or a method
    such as ``update``, is unresolved: the guard cannot tell which dict
    reaches the error.
    """
    if func is None:
        return None
    stores = 0
    value: ast.expr | None = None
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and node.id == name:
            stores += not isinstance(node.ctx, ast.Load)
        elif (
            isinstance(node, (ast.Subscript, ast.Attribute))
            and isinstance(node.value, ast.Name)
            and node.value.id == name
            and (isinstance(node, ast.Attribute) or not isinstance(node.ctx, ast.Load))
        ):
            return None
        if isinstance(node, ast.Assign) and [
            getattr(t, "id", None) for t in node.targets
        ] == [name]:
            value = node.value
    found = _dict_placeholders(value) if stores == 1 and value is not None else None
    return found[0] if found is not None and len(found) == 1 else None


def _construction_placeholders(
    node: ast.expr | None, func: ast.FunctionDef | ast.AsyncFunctionDef | None
) -> _Placeholders | None:
    """Resolve a literal placeholder dict, or a local name bound to one."""
    if (found := _dict_placeholders(node)) is not None:
        return found
    if isinstance(node, ast.Name):
        local = _local_dict_keys(func, node.id)
        return None if local is None else (local,)
    return None


def _call_site_placeholders(
    factory: _Factory,
    call: ast.Call,
    func: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> _Placeholders | None:
    """Resolve the placeholders a factory call site supplies."""
    if factory.placeholders is not None:
        return factory.placeholders
    names = {kw.arg for kw in call.keywords if kw.arg and kw.arg not in factory.params}
    for kw in call.keywords:
        if kw.arg is None:
            if not isinstance(kw.value, ast.Name):
                return None
            local = _local_dict_keys(func, kw.value.id)
            if local is None:
                return None
            names |= local
    return (frozenset(names),)


def _message_placeholders(message: str) -> frozenset[str]:
    """Return the format placeholders a translated message uses."""
    return frozenset(
        name for _, name, _, _ in Formatter().parse(message) if name is not None
    )


def _check_key(
    where: str,
    keys: set[str],
    supplied: _Placeholders,
    translations: tuple[dict[str, Any], dict[str, Any]],
) -> list[str]:
    """Check each key exists in both files and its placeholders are supplied."""
    problems = []
    for key in sorted(keys):
        for label, exceptions in zip(
            ("strings.json", "en.json"), translations, strict=True
        ):
            if key not in exceptions:
                problems.append(f"{where}: key {key!r} missing in {label}")
                continue
            used = _message_placeholders(exceptions[key]["message"])
            if used not in supplied:
                problems.append(
                    f"{where}: {key!r} in {label} uses {sorted(used)}, "
                    f"supplied {[sorted(s) for s in supplied]}"
                )
    return problems


def _exception_translations() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the ``exceptions`` sections of strings.json and en.json."""
    return (
        json.loads(_STRINGS_JSON.read_text(encoding="utf-8"))["exceptions"],
        json.loads(_EN_JSON.read_text(encoding="utf-8"))["exceptions"],
    )


def _translated_error_violations(module: str) -> list[str]:
    """Return every untranslated or mistranslated error a module builds."""
    tree = ast.parse((_INTEGRATION_DIR / module).read_text(encoding="utf-8"))
    translations = _exception_translations()
    enclosing = _enclosing(tree)
    problems = [
        f"{module}:{node.lineno}: raises the bare error class"
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and node.exc is not None
        and not isinstance(node.exc, ast.Call)
        and _called_name(node.exc) in _TRANSLATED_ERRORS
    ]
    factories: dict[str, _Factory] = {}
    checked: set[ast.Call] = set()

    # Direct constructions; a key that is a parameter makes a factory.
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        if _called_name(call) not in _TRANSLATED_ERRORS:
            continue
        where = f"{module}:{call.lineno}"
        func = enclosing.get(call)
        domain = _keyword(call, "translation_domain")
        if not (isinstance(domain, ast.Name) and domain.id == "DOMAIN"):
            problems.append(f"{where}: translation_domain is not DOMAIN")
        key = _keyword(call, "translation_key")
        placeholders_arg = _keyword(call, "translation_placeholders")
        if key is None:
            problems.append(f"{where}: no translation_key")
            continue
        if (
            isinstance(key, ast.Name)
            and func is not None
            and key.id in _param_names(func)
        ):
            forwarded = (
                isinstance(placeholders_arg, ast.BoolOp)
                and isinstance(placeholders_arg.values[0], ast.Name)
                and placeholders_arg.values[0].id == getattr(func.args.kwarg, "arg", "")
            )
            supplied = None if forwarded else _dict_placeholders(placeholders_arg)
            if not forwarded and supplied is None:
                problems.append(f"{where}: placeholders are not a literal dict")
            factories[func.name] = _Factory(func, key.id, supplied)
            checked.add(call)
            continue
        keys = _literal_keys(key)
        supplied = _construction_placeholders(placeholders_arg, func)
        if keys is None:
            problems.append(f"{where}: translation_key is not a literal")
        elif supplied is None:
            problems.append(f"{where}: placeholders are not a literal dict")
        else:
            problems += _check_key(where, keys, supplied, translations)
        checked.add(call)

    if not checked:
        problems.append(f"{module}: builds no translated error")

    # Factory call sites, until no new forwarding factory appears.
    resolved: set[ast.Call] = set()
    while True:
        found_new = False
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
            factory = factories.get(_called_name(call) or "")
            if factory is None or call in resolved:
                continue
            resolved.add(call)
            where = f"{module}:{call.lineno}"
            func = enclosing.get(call)
            key = factory.key_arg(call)
            if (
                isinstance(key, ast.Name)
                and func is not None
                and key.id in _param_names(func)
                and func.name not in factories
            ):
                factories[func.name] = _Factory(func, key.id, factory.placeholders)
                found_new = True
                continue
            keys = _literal_keys(key)
            supplied = _call_site_placeholders(factory, call, func)
            if keys is None:
                problems.append(f"{where}: translation_key is not a literal")
            elif supplied is None:
                problems.append(f"{where}: placeholders cannot be resolved")
            else:
                problems += _check_key(where, keys, supplied, translations)
        if not found_new:
            return problems


@pytest.mark.parametrize("module", _SERVICE_ERROR_MODULES)
def test_service_errors_are_translated(module: str) -> None:
    """Every service-action error names a key both files define, with its fields."""
    assert _translated_error_violations(module) == []


def test_service_error_guard_resolves_factories_and_conditionals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Conditional keys, returned errors and key-forwarding helpers are all followed."""
    (tmp_path / "good.py").write_text(
        "def _invalid(message, key, **placeholders):\n"
        "    return ServiceValidationError(message, translation_domain=DOMAIN,\n"
        "        translation_key=key, translation_placeholders=placeholders or None)\n"
        "def _wrap(key):\n"
        "    return _invalid('m', key)\n"
        "def f(flag, site_id):\n"
        "    details = {'site_id': site_id}\n"
        "    if flag:\n"
        "        raise _invalid('m', 'refresh_site_not_found', **details)\n"
        "    raise _wrap('target_required' if flag else 'no_coordinator')\n"
        "def g(site_id):\n"
        "    placeholders = {'site_id': site_id}\n"
        "    raise InvalidSubscriberIdError('m', translation_domain=DOMAIN,\n"
        "        translation_key='refresh_site_not_found',\n"
        "        translation_placeholders=placeholders)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(__name__ + "._INTEGRATION_DIR", tmp_path)
    assert _translated_error_violations("good.py") == []


@pytest.mark.parametrize(
    ("source", "problem"),
    [
        (
            (
                "def _err(key):\n"
                "    return ServiceValidationError(translation_domain=DOMAIN,"
                " translation_key=key)\n"
                "def f():\n"
                "    raise _err('no_such_key')\n"
            ),
            "key 'no_such_key' missing in strings.json",
        ),
        (
            (
                "def _err(key):\n"
                "    return ServiceValidationError(translation_domain=DOMAIN,"
                " translation_key=key)\n"
                "def f(name):\n"
                "    raise _err(f'x_{name}')\n"
            ),
            "translation_key is not a literal",
        ),
        (
            (
                "def f(name):\n"
                "    raise HomeAssistantError(translation_domain=DOMAIN,"
                " translation_key=name.lower())\n"
            ),
            "translation_key is not a literal",
        ),
        (
            "raise HomeAssistantError(translation_key='no_coordinator')\n",
            "translation_domain is not DOMAIN",
        ),
        (
            (
                "raise ServiceValidationError(translation_domain=DOMAIN,"
                " translation_key='target_multiple',"
                " translation_placeholders={'wrong': 'x'})\n"
            ),
            "'target_multiple' in strings.json uses ['service'], supplied [['wrong']]",
        ),
        (
            (
                "def _invalid(message, key, **placeholders):\n"
                "    return ServiceValidationError(message, translation_domain=DOMAIN,"
                " translation_key=key, translation_placeholders=placeholders or None)\n"
                "def f(data):\n"
                "    raise _invalid('m', 'target_multiple', **data)\n"
            ),
            "placeholders cannot be resolved",
        ),
        (
            (
                "raise ServiceValidationError(translation_domain=DOMAIN,"
                " translation_key='no_coordinator', translation_placeholders=data)\n"
            ),
            "placeholders are not a literal dict",
        ),
        ("raise HomeAssistantError\n", "raises the bare error class"),
        ("raise HomeAssistantError('x')\n", "no translation_key"),
        ("raise exceptions.HomeAssistantError('x')\n", "no translation_key"),
        ("error = ServiceValidationError('x')\n", "no translation_key"),
        ("raise InvalidSubscriberIdError('x')\n", "no translation_key"),
        ("VALUE = 1\n", "builds no translated error"),
        (
            (
                "def f(site_id):\n"
                "    placeholders = {'site_id': site_id}\n"
                "    placeholders = {}\n"
                "    raise HomeAssistantError(translation_domain=DOMAIN,"
                " translation_key='refresh_site_not_found',"
                " translation_placeholders=placeholders)\n"
            ),
            "placeholders are not a literal dict",
        ),
        (
            (
                "def f(site_id):\n"
                "    placeholders = {'site_id': site_id}\n"
                "    del placeholders['site_id']\n"
                "    raise HomeAssistantError(translation_domain=DOMAIN,"
                " translation_key='refresh_site_not_found',"
                " translation_placeholders=placeholders)\n"
            ),
            "placeholders are not a literal dict",
        ),
        (
            (
                "def f(site_id):\n"
                "    placeholders = {'site_id': site_id}\n"
                "    placeholders.clear()\n"
                "    raise HomeAssistantError(translation_domain=DOMAIN,"
                " translation_key='refresh_site_not_found',"
                " translation_placeholders=placeholders)\n"
            ),
            "placeholders are not a literal dict",
        ),
    ],
    ids=[
        "helper-caller-undefined-key",
        "helper-caller-non-literal-key",
        "non-literal-key",
        "missing-domain",
        "placeholder-mismatch",
        "unresolved-forwarded-placeholders",
        "non-literal-placeholders",
        "bare-class",
        "called-without-key",
        "attribute-qualified",
        "returned-not-raised",
        "carrier-subclass",
        "no-constructions",
        "rebound-placeholders",
        "deleted-placeholder",
        "mutated-placeholders",
    ],
)
def test_service_error_guard_rejects_bad_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str, problem: str
) -> None:
    """Each way an error can escape translation fails the guard."""
    (tmp_path / "bad.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(__name__ + "._INTEGRATION_DIR", tmp_path)
    problems = _translated_error_violations("bad.py")
    assert any(problem in found for found in problems), problems


def test_service_error_guard_requires_the_key_in_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key that only strings.json defines fails; en.json is what HA loads."""
    english = json.loads(_EN_JSON.read_text(encoding="utf-8"))
    del english["exceptions"]["no_coordinator"]
    en_json = tmp_path / "en.json"
    en_json.write_text(json.dumps(english), encoding="utf-8")
    (tmp_path / "bad.py").write_text(
        "raise ServiceValidationError(translation_domain=DOMAIN,"
        " translation_key='no_coordinator')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(__name__ + "._INTEGRATION_DIR", tmp_path)
    monkeypatch.setattr(__name__ + "._EN_JSON", en_json)
    assert _translated_error_violations("bad.py") == [
        "bad.py:1: key 'no_coordinator' missing in en.json"
    ]
