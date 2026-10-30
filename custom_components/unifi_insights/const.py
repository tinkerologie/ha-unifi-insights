# Copyright 2026 UniFi Insights contributors
"""Constants for the UniFi Insights integration."""

from datetime import timedelta
from typing import Final

DOMAIN = "unifi_insights"

# Exact logical-site families. Registry translation keys must also match these
# keys before a console identity change can re-key an existing entity.
SITE_ENTITY_SUFFIXES: dict[str, frozenset[str]] = {
    "sensor": frozenset(
        {
            "site_total_clients",
            "site_wired_clients",
            "site_wireless_clients",
            "internet_download_1h",
            "internet_upload_1h",
            "internet_download_1d",
            "internet_upload_1d",
            "internet_download_1w",
            "internet_upload_1w",
            "internet_download_1m",
            "internet_upload_1m",
            "latest_voucher_code",
            "latest_voucher_expiration",
            "active_vouchers",
        }
    ),
    "number": frozenset(
        {
            "voucher_duration",
            "voucher_guest_limit",
            "voucher_download_limit",
            "voucher_upload_limit",
            "voucher_data_limit",
        }
    ),
    "button": frozenset({"generate_voucher"}),
    "event": frozenset({"network_rule_changes"}),
    "image": frozenset({"voucher_qr_code"}),
}

MANUFACTURER: Final = "Ubiquiti Inc."

# Connection types
CONF_CONNECTION_TYPE: Final = "connection_type"
CONF_CONSOLE_ID: Final = "console_id"
CONF_CONSOLE_NAME: Final = "console_name"
# Substrings that identify the console itself (gateway or Cloud Key) among the
# Network devices, so its MAC can be used as the console's stable identity.
# Matched against both `type` and `model`: this API leaves `type` unset on
# every device, so `model` ("UniFi Dream Machine PRO SE") is what actually
# identifies the hardware.
CONSOLE_DEVICE_TOKENS: Final = (
    "udm",
    "ucg",
    "uck",
    "udr",
    "udw",
    "ugw",
    "uxg",
    "cloud key",
    "dream machine",
    "dream router",
    "dream wall",
    "gateway",
)

# Model prefixes that identify gateway/router devices
GATEWAY_MODEL_PREFIXES: Final = (
    "UDM",
    "USG",
    "UXG",
    "UCG",
    "UDR",
    "UDW",
    "GATEWAY",
)
CONNECTION_TYPE_LOCAL: Final = "local"
CONNECTION_TYPE_REMOTE: Final = "remote"
CONNECTION_TYPE_CARRIER_FABRIC: Final = "carrier_fabric"
CONF_CARRIER_ORG_ID: Final = "carrier_org_id"

# Options
CONF_TRACK_CLIENTS: Final = "track_clients"  # Deprecated, kept for migration
CONF_TRACK_WIFI_CLIENTS: Final = "track_wifi_clients"
CONF_TRACK_WIRED_CLIENTS: Final = "track_wired_clients"
CONF_CLIENT_CONTROL: Final = "client_control"
CONF_SITE_IDS: Final = "site_ids"  # Empty/absent means every site
CONF_TRACK_SUBSCRIBERS: Final = "track_subscribers"
CONF_CARRIER_ACTIONS: Final = "carrier_actions"
DEFAULT_TRACK_CLIENTS: Final = False
DEFAULT_TRACK_WIFI_CLIENTS: Final = False
DEFAULT_TRACK_WIRED_CLIENTS: Final = False
DEFAULT_CLIENT_CONTROL: Final = True
DEFAULT_TRACK_SUBSCRIBERS: Final = False
DEFAULT_CARRIER_ACTIONS: Final = False

DEFAULT_API_HOST = "https://192.168.10.1"

# UniFi Access: optional application on a local entry, with its own bearer
# token. The official API PDF documents the developer API on port 12445.
CONF_ACCESS_TOKEN: Final = "access_token"  # noqa: S105 - config key name, not a secret
CONF_ACCESS_PORT: Final = "access_port"
CONF_ACCESS_WEBHOOK: Final = "access_webhook"
CONF_CLEAR_ACCESS_TOKEN: Final = "clear_access_token"  # noqa: S105 - config key name, not a secret
DEFAULT_ACCESS_PORT: Final = 12445
DEFAULT_ACCESS_WEBHOOK: Final = False
# Webhook state lives in entry.data: the Home Assistant receiver ID, the Access
# endpoint ID and the endpoint's signing secret returned by the console.
CONF_ACCESS_WEBHOOK_ID: Final = "access_webhook_id"
CONF_ACCESS_WEBHOOK_ENDPOINT_ID: Final = "access_webhook_endpoint_id"
CONF_ACCESS_WEBHOOK_SECRET: Final = "access_webhook_secret"  # noqa: S105 - config key name, not a secret
ACCESS_WEBHOOK_EVENT: Final = "access.door.unlock"
ACCESS_CONFIG_KEYS: Final = (
    CONF_ACCESS_TOKEN,
    CONF_ACCESS_PORT,
    CONF_ACCESS_WEBHOOK_ID,
    CONF_ACCESS_WEBHOOK_ENDPOINT_ID,
    CONF_ACCESS_WEBHOOK_SECRET,
)
# Access has no version endpoint in the official API, so these minimums are
# checked against the Access application version the user reports, and each
# feature is also detected by its own endpoint response.
ACCESS_MIN_VERSION_NOTIFICATIONS: Final = (1, 20, 11)
ACCESS_MIN_VERSION_EMERGENCY_LOCK_RULES: Final = (1, 24, 6)
ACCESS_MIN_VERSION_WEBHOOKS: Final = (2, 2, 10)
# Access state is polled and refreshed after each command. Door lock state
# changes outside Home Assistant, so the poll is the authoritative fallback.
SCAN_INTERVAL_ACCESS = timedelta(seconds=30)

# Scan intervals for multi-coordinator architecture (Platinum compliance)
# Device coordinator - fast updates for status, stats (30 seconds)
SCAN_INTERVAL_DEVICE = timedelta(seconds=30)
# Access point radio settings (a per-device details call) change rarely, so
# they are fetched far less often than the statistics they accompany. A failed
# attempt waits the same interval before it retries, so rate limiting or an
# offline console is not hammered once per statistics poll (#454).
SCAN_INTERVAL_DEVICE_DETAILS = timedelta(minutes=5)
# Config coordinator - slow updates for sites, WiFi config (5 minutes)
SCAN_INTERVAL_CONFIG = timedelta(minutes=5)
SCAN_INTERVAL_INNERSPACE = timedelta(minutes=5)
SCAN_INTERVAL_SITE_MANAGER = timedelta(minutes=10)
CARRIER_FABRIC_SCAN_INTERVAL = timedelta(minutes=5)
CARRIER_FABRIC_REQUEST_TIMEOUT: Final = 30  # seconds
# Mobility routers report usage and location slowly; keys without Mobility
# access, or accounts without routers, are only re-checked hourly.
SCAN_INTERVAL_MOBILITY = timedelta(minutes=5)
SCAN_INTERVAL_MOBILITY_IDLE = timedelta(hours=1)
# Device identifiers: routers are `mobility_{uuid}`, workspaces
# `mobility_workspace_{uuid}`. Router ids are UUIDs, so they never collide.
MOBILITY_DEVICE_PREFIX: Final = "mobility_"
MOBILITY_WORKSPACE_PREFIX: Final = "mobility_workspace_"
# Published Mobility API enums, shared by entities and diagnostics.
MOBILITY_WORKSPACE_STATUSES: Final = ("ACTIVE", "PENDING", "INACTIVE", "DECLINED")
MOBILITY_MODELS: Final = ("UMR", "UMR Industrial", "UMR Ultra")
MOBILITY_ROUTER_STATES: Final = (
    "CONNECTED",
    "DISCONNECTED",
    "ADOPTING",
    "ADOPTING_TIMEOUT",
    "DOWNLOADING",
    "UPGRADING",
    "RESTARTING",
    "FACTORY_RESET",
    "GETTING_READY",
    "RESTORING",
    "NULL",
    "DELETING",
)
MOBILITY_ROUTER_CONNECTED: Final = "CONNECTED"
# Site Manager response sections shared by the coordinator and diagnostics.
SITE_MANAGER_COLLECTIONS: Final = (
    "hosts",
    "sites",
    "devices",
    "isp_metrics",
    "sd_wan_configs",
)
ISP_WAN_NUMBERS: Final = (
    "avgLatency",
    "download_kbps",
    "downtime",
    "maxLatency",
    "packetLoss",
    "upload_kbps",
    "uptime",
)
# Protect coordinator - moderate updates for cameras, sensors (30 seconds)
# Real-time updates via WebSocket when available
SCAN_INTERVAL_PROTECT = timedelta(seconds=30)

# Consecutive missing polls threshold for stale device registry removal.
# For Network, a device is removed on the 3rd consecutive eligible poll in which
# it is absent. Protect keeps a missing device for 3 polls and removes it on the 4th.
# Removal cannot be undone (areas, customizations and automations keyed on the
# device go with it), while a single absence is ambiguous.
# Three polls is about 90 seconds.
MAX_CONSECUTIVE_MISSING_POLLS: Final = 3

# The longest window the event timeline card can show. The Protect event store
# keeps every event at least this long (`coordinators/protect_events.py`).
TIMELINE_MAX_HOURS: Final = 24


UNIFI_API_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


# Device types
DEVICE_TYPE_CAMERA: Final = "camera"
DEVICE_TYPE_LIGHT: Final = "light"
DEVICE_TYPE_SENSOR: Final = "sensor"
DEVICE_TYPE_NVR: Final = "nvr"
DEVICE_TYPE_VIEWER: Final = "viewer"
DEVICE_TYPE_CHIME: Final = "chime"
DEVICE_TYPE_DOORLOCK: Final = "doorlock"
DEVICE_TYPE_VIEWPORT: Final = "viewport"
# Protect 7.3.70 security devices. The coordinator keys their collections as
# f"{device_type}s" ("fobs", "link_stations", "alarm_hubs") because
# UnifiProtectEntity looks devices up that way.
DEVICE_TYPE_FOB: Final = "fob"
DEVICE_TYPE_LINK_STATION: Final = "link_station"
DEVICE_TYPE_ALARM_HUB: Final = "alarm_hub"
# Sirens are keyed "sirens" in the coordinator data. "siren" is also the
# WebSocket modelKey (spec sirenModelKey), so one constant serves both.
DEVICE_TYPE_SIREN: Final = "siren"

# Protect WebSocket modelKey values that differ from the device types above.
# Link stations and alarm hubs share one modelKey; there is no "alarmHub" key.
MODEL_KEY_LINKSTATION: Final = "linkstation"
MODEL_KEY_FOB: Final = "fob"

# Protect event types
EVENT_TYPE_ALARM_HUB_DEVICE_TAMPER: Final = "alarmHubDeviceTamper"
# Protect 7.2.105: "An ONVIF camera digital input has changed state (circuit
# opened or closed)". The camera schema has no field that says which cameras
# have inputs or how many, so the event entities are created from these frames.
EVENT_TYPE_CAMERA_DIGITAL_INPUT_CHANGED: Final = "cameraDigitalInputChanged"

# `cameraDigitalInputChanged` metadata.inputState -> the Home Assistant event
# type it fires. The names stay neutral (circuit open/closed) rather than
# opened/closed: with normally-closed wiring an open circuit can mean "idle",
# so the integration must not guess what the input is wired to.
DIGITAL_INPUT_EVENT_TYPES_BY_STATE: Final[dict[str, str]] = {
    "circuitOpen": "circuit_open",
    "circuitClosed": "circuit_closed",
}

# The event types a digital-input entity declares, in the order it lists them.
CAMERA_DIGITAL_INPUT_EVENT_TYPES: Final[tuple[str, ...]] = (
    "circuit_open",
    "circuit_closed",
)

# Camera attributes
ATTR_CAMERA_ID: Final = "camera_id"
ATTR_CAMERA_NAME: Final = "camera_name"
ATTR_CAMERA_STATE: Final = "camera_state"
ATTR_CAMERA_TYPE: Final = "camera_type"
ATTR_IS_PACKAGE_CAMERA: Final = "is_package_camera"
ATTR_PARENT_CAMERA_ID: Final = "parent_camera_id"
ATTR_MIC_ENABLED: Final = "mic_enabled"
ATTR_HDR_MODE: Final = "hdr_mode"
ATTR_VIDEO_MODE: Final = "video_mode"
ATTR_LAST_MOTION: Final = "last_motion"
ATTR_SMART_DETECT_TYPES: Final = "smart_detect_types"
ATTR_STATUS_LIGHT: Final = "status_light"
ATTR_HIGH_FPS_MODE: Final = "high_fps_mode"

# Light attributes
ATTR_LIGHT_ID: Final = "light_id"
ATTR_LIGHT_NAME: Final = "light_name"
ATTR_LIGHT_STATE: Final = "light_state"
ATTR_LIGHT_MODE: Final = "light_mode"
ATTR_LIGHT_LEVEL: Final = "light_level"
ATTR_LIGHT_MOTION: Final = "light_motion"
ATTR_LIGHT_DARK: Final = "light_dark"

# Services
SERVICE_SET_HDR_MODE: Final = "set_hdr_mode"
SERVICE_SET_VIDEO_MODE: Final = "set_video_mode"
SERVICE_SET_MIC_VOLUME: Final = "set_mic_volume"
SERVICE_SET_LIGHT_MODE: Final = "set_light_mode"
SERVICE_SET_LIGHT_LEVEL: Final = "set_light_level"
SERVICE_PTZ_MOVE: Final = "ptz_move"

SERVICE_PTZ_PATROL: Final = "ptz_patrol"

# Service descriptions
SERVICE_PTZ_MOVE_DESCRIPTION: Final = "Move a PTZ camera to a preset position."
SERVICE_PTZ_PATROL_DESCRIPTION: Final = "Start or stop a PTZ camera patrol."

# Camera states
CAMERA_STATE_CONNECTED: Final = "CONNECTED"

# Camera types
CAMERA_TYPE_REGULAR: Final = "regular"
CAMERA_TYPE_DOORBELL: Final = "doorbell"
CAMERA_TYPE_DOORBELL_WITH_PACKAGE_DETECTION: Final = "doorbell_with_package_detection"
CAMERA_TYPE_CAMERA_WITH_PACKAGE_DETECTION: Final = "camera_with_package_detection"
CAMERA_TYPE_DOORBELL_MAIN: Final = "doorbell_main"
CAMERA_TYPE_DOORBELL_PACKAGE: Final = "doorbell_package"
CAMERA_TYPE_CAMERA_MAIN: Final = "camera_main"
CAMERA_TYPE_CAMERA_PACKAGE: Final = "camera_package"

# HDR modes
HDR_MODE_AUTO: Final = "auto"
HDR_MODE_ON: Final = "on"
HDR_MODE_OFF: Final = "off"

# Video modes
VIDEO_MODE_DEFAULT: Final = "default"
VIDEO_MODE_HIGH_FPS: Final = "highFps"
VIDEO_MODE_SPORT: Final = "sport"
VIDEO_MODE_SLOW_SHUTTER: Final = "slowShutter"
VIDEO_MODE_LPR_REFLEX: Final = "lprReflex"
VIDEO_MODE_LPR_NONE_REFLEX: Final = "lprNoneReflex"
# Select keys are translatable HA slugs; API values remain unchanged.
VIDEO_MODE_OPTIONS: Final = {
    "default": VIDEO_MODE_DEFAULT,
    "high_fps": VIDEO_MODE_HIGH_FPS,
    "sport": VIDEO_MODE_SPORT,
    "slow_shutter": VIDEO_MODE_SLOW_SHUTTER,
    "lpr_reflex": VIDEO_MODE_LPR_REFLEX,
    "lpr_none_reflex": VIDEO_MODE_LPR_NONE_REFLEX,
}
VIDEO_MODE_API_TO_OPTION: Final = {
    value: option for option, value in VIDEO_MODE_OPTIONS.items()
}
VIDEO_MODES: Final = tuple(VIDEO_MODE_OPTIONS.values())

# Smart detect types
SMART_DETECT_PERSON: Final = "person"
SMART_DETECT_VEHICLE: Final = "vehicle"
SMART_DETECT_ANIMAL: Final = "animal"
SMART_DETECT_PACKAGE: Final = "package"

# Sensor attributes
ATTR_SENSOR_ID: Final = "sensor_id"
ATTR_SENSOR_NAME: Final = "sensor_name"
ATTR_SENSOR_BATTERY: Final = "battery_percentage"
ATTR_SENSOR_BATTERY_LOW: Final = "battery_low"
ATTR_SENSOR_MOTION_DETECTED: Final = "motion_detected"
ATTR_SENSOR_MOTION_DETECTED_AT: Final = "motion_detected_at"
ATTR_SENSOR_IS_OPENED: Final = "is_opened"
ATTR_SENSOR_OPEN_STATUS_CHANGED_AT: Final = "open_status_changed_at"
ATTR_SENSOR_LIGHT_VALUE: Final = "light_value"
ATTR_SENSOR_HUMIDITY_VALUE: Final = "humidity_value"
ATTR_SENSOR_TEMPERATURE_VALUE: Final = "temperature_value"
ATTR_SENSOR_TAMPER_DETECTED: Final = "tamper_detected"
ATTR_SENSOR_TAMPER_DETECTED_AT: Final = "tamper_detected_at"
ATTR_SENSOR_LEAK_DETECTED: Final = "leak_detected"
ATTR_SENSOR_LEAK_DETECTED_AT: Final = "leak_detected_at"
ATTR_SENSOR_EXTERNAL_LEAK_DETECTED: Final = "external_leak_detected"
ATTR_SENSOR_EXTERNAL_LEAK_DETECTED_AT: Final = "external_leak_detected_at"

# Link station / alarm hub / fob attributes
ATTR_THREAD_CHANNEL: Final = "channel"
ATTR_THREAD_NETWORK_NAME: Final = "network_name"
ATTR_THREAD_PAN_ID: Final = "pan_id"
ATTR_THREAD_EXTENDED_PAN_ID: Final = "extended_pan_id"
ATTR_THREAD_ERROR_REASON: Final = "error_reason"
ATTR_LAST_TAMPER_USER: Final = "last_tamper_user"
ATTR_LAST_TAMPER_AT: Final = "last_tamper_at"
ATTR_ARM_PROFILE_ID: Final = "arm_profile_id"
ATTR_NIGHT_PROFILE_ID: Final = "night_profile_id"

# NVR alarm control panel attributes (armMode)
ATTR_ARMED_AT: Final = "armed_at"
ATTR_WILL_BE_ARMED_AT: Final = "will_be_armed_at"
ATTR_BREACH_DETECTED_AT: Final = "breach_detected_at"
ATTR_BREACH_EVENT_COUNT: Final = "breach_event_count"

# NVR attributes
ATTR_NVR_ID: Final = "nvr_id"
ATTR_NVR_NAME: Final = "nvr_name"
ATTR_NVR_STATE: Final = "nvr_state"
ATTR_NVR_VERSION: Final = "nvr_version"
ATTR_NVR_STORAGE_USED: Final = "storage_used"
ATTR_NVR_STORAGE_TOTAL: Final = "storage_total"
ATTR_NVR_STORAGE_AVAILABLE: Final = "storage_available"
ATTR_NVR_STORAGE_USED_PERCENT: Final = "storage_used_percent"

# Fill level, in percent of the 1-decimal value shown, at which a console's
# persistent storage counts as nearly full (Storage Nearly Full binary sensor).
STORAGE_NEARLY_FULL_PERCENT: Final = 90

# Chime attributes
ATTR_CHIME_ID: Final = "chime_id"
ATTR_CHIME_NAME: Final = "chime_name"
ATTR_CHIME_VOLUME: Final = "chime_volume"
ATTR_CHIME_RINGTONE_ID: Final = "chime_ringtone_id"
ATTR_CHIME_REPEAT_TIMES: Final = "chime_repeat_times"

# Chime services
SERVICE_SET_CHIME_VOLUME: Final = "set_chime_volume"
SERVICE_PLAY_CHIME_RINGTONE: Final = "play_chime_ringtone"
SERVICE_SET_CHIME_RINGTONE: Final = "set_chime_ringtone"
SERVICE_SET_CHIME_REPEAT_TIMES: Final = "set_chime_repeat_times"
SERVICE_SET_CHIME_PAIRED_DOORBELLS: Final = "set_chime_paired_doorbells"

# Light modes
LIGHT_MODE_ALWAYS: Final = "always"
LIGHT_MODE_MOTION: Final = "motion"
LIGHT_MODE_OFF: Final = "off"

# UniFi Network Services
SERVICE_POWER_CYCLE_PORT: Final = "power_cycle_port"
SERVICE_AUTHORIZE_GUEST: Final = "authorize_guest"
SERVICE_RECONNECT_CLIENT: Final = "reconnect_client"
SERVICE_REMOVE_CLIENTS: Final = "remove_clients"
SERVICE_GENERATE_VOUCHER: Final = "generate_voucher"
SERVICE_DELETE_VOUCHER: Final = "delete_voucher"
SERVICE_EXPORT_NETWORK_RULES: Final = "export_network_rules"
SERVICE_LIST_VOUCHERS: Final = "list_vouchers"
# Default voucher name required by UniFi Network API when note is omitted
DEFAULT_VOUCHER_NAME: Final = "Home Assistant"

# UniFi Protect Services
SERVICE_PTZ_GOTO_PRESET: Final = "ptz_goto_preset"
SERVICE_PTZ_START_PATROL: Final = "ptz_start_patrol"
SERVICE_PTZ_STOP_PATROL: Final = "ptz_stop_patrol"
SERVICE_TRIGGER_ALARM: Final = "trigger_alarm"
SERVICE_SET_LIVEVIEW: Final = "set_liveview"
SERVICE_CREATE_LIVEVIEW: Final = "create_liveview"

# UniFi Carrier Fabric Services
SERVICE_CARRIER_SUSPEND_SUBSCRIBER: Final = "carrier_suspend_subscriber"
SERVICE_CARRIER_RESUME_SUBSCRIBER: Final = "carrier_resume_subscriber"

# Port actions
PORT_ACTION_POWER_CYCLE: Final = "POWER_CYCLE"

# Client actions
CLIENT_ACTION_AUTHORIZE_GUEST: Final = "AUTHORIZE_GUEST_ACCESS"

# Viewer attributes
ATTR_VIEWER_ID: Final = "viewer_id"
ATTR_VIEWER_NAME: Final = "viewer_name"
