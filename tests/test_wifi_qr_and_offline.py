# Copyright 2026 UniFi Insights contributors
"""Unit tests for WiFi QR payloads and per-SSID counts.

Covers the pure-logic helpers behind issues #49 and #52, none of which
require a running Home Assistant instance.
"""

from __future__ import annotations

from unifi_official_api.protect.models import Camera, CameraState
from unifi_official_api.protect.models.viewer import (
    Viewer,
    ViewerState,
)

from custom_components.unifi_insights.coordinators.config import UnifiConfigCoordinator


class TestWifiQrPayload:
    """Tests for the WiFi QR connect-string builder (#49)."""

    def test_wpa_payload(self) -> None:
        payload = UnifiConfigCoordinator._wifi_qr_payload(
            "Home", "secret", "wpapsk", hidden=False
        )
        assert payload == "WIFI:T:WPA;S:Home;P:secret;;"

    def test_open_network_has_nopass(self) -> None:
        payload = UnifiConfigCoordinator._wifi_qr_payload(
            "Guest", None, "open", hidden=False
        )
        assert payload == "WIFI:T:nopass;S:Guest;;"

    def test_hidden_flag_and_escaping(self) -> None:
        payload = UnifiConfigCoordinator._wifi_qr_payload(
            "My;Net", "pa:ss", "wpapsk", hidden=True
        )
        # ';' and ':' inside values must be backslash-escaped, and H:true added.
        assert payload == r"WIFI:T:WPA;S:My\;Net;P:pa\:ss;H:true;;"

    def test_wep_payload(self) -> None:
        payload = UnifiConfigCoordinator._wifi_qr_payload(
            "Old", "k", "wep", hidden=False
        )
        assert payload.startswith("WIFI:T:WEP;")


class TestEnrichWifi:
    """Tests for WiFi enrichment with secrets + per-SSID counts (#49)."""

    def test_enrich_adds_counts_and_qr(self) -> None:
        wifi_dict = {"w1": {"id": "w1", "name": "Home"}}
        legacy = [{"name": "Home", "x_passphrase": "secret", "security": "wpapsk"}]
        active = [
            {"essid": "Home", "is_wired": False},
            {"essid": "Home", "is_wired": False},
            {"essid": "Home", "is_wired": True},  # wired excluded
            {"essid": "Other", "is_wired": False},
        ]

        UnifiConfigCoordinator._enrich_wifi(wifi_dict, legacy, active)

        wifi = wifi_dict["w1"]
        assert wifi["num_connected_clients"] == 2
        assert wifi["passphrase"] == legacy[0]["x_passphrase"]
        assert wifi["qr_code"] == "WIFI:T:WPA;S:Home;P:secret;;"

    def test_enrich_without_matching_config(self) -> None:
        wifi_dict = {"w1": {"id": "w1", "name": "Home"}}
        UnifiConfigCoordinator._enrich_wifi(wifi_dict, [], [])
        # No secrets available, but the count key is still populated.
        assert wifi_dict["w1"]["num_connected_clients"] == 0
        assert "qr_code" not in wifi_dict["w1"]


class TestMapLegacySiteNames:
    """Tests for classic site-name resolution (#44/#49)."""

    def test_single_site_falls_back_to_only_legacy(self) -> None:
        mapping = UnifiConfigCoordinator._map_legacy_site_names(
            {"uuid-1": {"name": "My Home"}},
            [{"name": "default", "desc": "Default"}],
        )
        assert mapping == {"uuid-1": "default"}

    def test_match_by_description(self) -> None:
        mapping = UnifiConfigCoordinator._map_legacy_site_names(
            {"uuid-1": {"name": "HQ"}, "uuid-2": {"name": "Branch"}},
            [
                {"name": "default", "desc": "HQ"},
                {"name": "site2", "desc": "Branch"},
            ],
        )
        assert mapping == {"uuid-1": "default", "uuid-2": "site2"}


class TestTolerantProtectEnums:
    """Tests for resilient Protect enum parsing (#52)."""

    def test_camera_unknown_state_does_not_raise(self) -> None:
        camera = Camera.model_validate(
            {"id": "c1", "mac": "AA", "state": "SOME_NEW_71_STATE"}
        )
        assert camera.state is CameraState.UNKNOWN

    def test_camera_known_state(self) -> None:
        camera = Camera.model_validate({"id": "c1", "mac": "AA", "state": "CONNECTED"})
        assert camera.state is CameraState.CONNECTED

    def test_viewer_unknown_state_does_not_raise(self) -> None:
        viewer = Viewer.model_validate({"id": "v1", "mac": "BB", "state": "WEIRD"})
        assert viewer.state is ViewerState.UNKNOWN

    def test_viewer_known_state(self) -> None:
        viewer = Viewer.model_validate({"id": "v1", "mac": "BB", "state": "CONNECTED"})
        assert viewer.state is ViewerState.CONNECTED
