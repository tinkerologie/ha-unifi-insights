# Copyright 2026 UniFi Insights contributors
"""Tests for the shared command executor in coordinators/base.py (ADR-010)."""

from __future__ import annotations

import ast
import json
import logging
import string
import traceback
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import UpdateFailed
from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiConnectionError,
    UniFiNotFoundError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.network.models.voucher import Voucher
from unifi_official_api.validation import parse_record

from custom_components.unifi_insights.const import (
    DOMAIN,
)
from custom_components.unifi_insights.coordinators.base import (
    CommandFailure,
    UnifiInsightsBaseCoordinator,
    async_execute_command,
)
from tests.fixtures.coordinator_clients import (
    _create_mock_network_client,
    _create_mock_protect_client,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

_INTEGRATION_DIR = Path(__file__).parents[2] / "custom_components" / "unifi_insights"

# Exception keys added by #338's shared foundations, with their placeholders.
_COMMAND_KEYS: dict[str, set[str]] = {
    "command_failed": set(),
    "invalid_api_response": set(),
    "refresh_failed": {"targets"},
    "client_not_resolvable": {"client_id"},
    "restart_rejected": {"device_id"},
    "voucher_not_created": {"site_id"},
    "ringtone_not_found": {"ringtone_id", "chime_id"},
    "liveview_not_found": {"liveview"},
}


def _invalid_voucher() -> UniFiResponseError:
    """Return the library's error for a voucher record that failed to parse."""
    code = "1234567890"
    with pytest.raises(UniFiResponseError) as invalid:
        parse_record(Voucher, code, "Voucher")
    return invalid.value


async def test_returns_the_call_result_and_forwards_arguments() -> None:
    """A successful call returns its result with its arguments unchanged."""
    action = AsyncMock(return_value=True)

    result = await async_execute_command(
        CommandFailure(translation_key="command_failed"),
        action,
        "site1",
        "dev1",
        state=True,
    )

    assert result is True
    action.assert_awaited_once_with("site1", "dev1", state=True)


@pytest.mark.parametrize(
    "error",
    [
        HomeAssistantError("already translated"),
        ServiceValidationError(
            translation_domain=DOMAIN, translation_key="port_not_poe"
        ),
    ],
)
async def test_home_assistant_errors_pass_through_unchanged(
    error: HomeAssistantError,
) -> None:
    """An owner's own translated error is never re-wrapped."""
    action = AsyncMock(side_effect=error)

    with pytest.raises(HomeAssistantError) as caught:
        await async_execute_command(
            CommandFailure(translation_key="command_failed"), action
        )

    assert caught.value is error


async def test_api_error_is_translated_and_keeps_its_cause(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Other errors become the translated failure raised from the API error."""
    cause = UniFiConnectionError("console unreachable")
    action = AsyncMock(side_effect=cause)

    with pytest.raises(HomeAssistantError) as caught:
        await async_execute_command(
            CommandFailure(
                translation_key="restart_rejected",
                translation_placeholders={"device_id": "dev1"},
            ),
            action,
        )

    assert type(caught.value) is HomeAssistantError
    assert caught.value.translation_domain == DOMAIN
    assert caught.value.translation_key == "restart_rejected"
    assert caught.value.translation_placeholders == {"device_id": "dev1"}
    assert caught.value.__cause__ is cause
    assert "restart_rejected" in caplog.text


async def test_fixed_message_is_kept_for_existing_callers() -> None:
    """A caller's established English message stays the error text."""
    action = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(HomeAssistantError, match=r"^Unable to restart dev1$") as caught:
        await async_execute_command(
            CommandFailure(
                translation_key="command_failed", message="Unable to restart dev1"
            ),
            action,
        )

    assert caught.value.translation_key == "command_failed"


@pytest.mark.parametrize("message", [None, "Unable to generate voucher"])
async def test_malformed_response_is_invalid_api_response(
    caplog: pytest.LogCaptureFixture, message: str | None
) -> None:
    """A record that failed to parse raises invalid_api_response, fields only."""
    error = _invalid_voucher()
    action = AsyncMock(side_effect=error)

    with (
        caplog.at_level(logging.DEBUG),
        pytest.raises(HomeAssistantError) as caught,
    ):
        await async_execute_command(
            CommandFailure(translation_key="command_failed", message=message),
            action,
        )

    assert caught.value.translation_key == "invalid_api_response"
    assert caught.value.translation_placeholders == {}
    assert caught.value.__cause__ is error
    assert "1234567890" not in caplog.text
    assert "1234567890" not in "".join(traceback.format_exception(caught.value))
    assert (
        "Invalid API response data (Voucher response failed validation (root))"
        in caplog.text
    )
    if message is not None:
        assert str(caught.value).startswith(f"{message}: Invalid API response data")


@pytest.mark.parametrize(
    "error",
    [
        UniFiNotFoundError("Voucher v1 not found", status_code=200),
        UniFiResponseError(
            "API returned non-JSON response (status 200)",
            status_code=200,
            response_body="<html></html>",
        ),
        UniFiResponseError("Bad request", status_code=400),
    ],
    ids=["200-no-record", "200-non-json", "400"],
)
async def test_other_response_errors_raise_the_command_key(error: Exception) -> None:
    """Only a malformed record is invalid_api_response; others keep the key."""
    action = AsyncMock(side_effect=error)

    with pytest.raises(HomeAssistantError) as caught:
        await async_execute_command(
            CommandFailure(translation_key="command_failed"), action
        )

    assert caught.value.translation_key == "command_failed"
    assert caught.value.__cause__ is error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            UniFiResponseError(
                "API returned non-JSON response (status 200)",
                status_code=200,
                response_body="<html></html>",
            ),
            True,
        ),
        (UniFiResponseError("Voucher response failed validation", 200), False),
        (UniFiNotFoundError("Voucher v1 not found", status_code=200), False),
        (UniFiNotFoundError("Not found", status_code=404), True),
        (UniFiResponseError("Server error", status_code=500), False),
    ],
    ids=["200-non-json", "200-malformed", "200-no-record", "404", "500"],
)
def test_unsupported_response_needs_a_body_on_2xx(
    error: Exception, *, expected: bool
) -> None:
    """A 2xx counts as "not offered" only for a non-JSON page, not a bad record."""
    assert UnifiInsightsBaseCoordinator._is_unsupported_response(error) is expected


@pytest.mark.parametrize("result", [False, None, [], {}])
async def test_falsy_result_raises_the_rejection_key(result: object) -> None:
    """A rejection key turns a falsy result into a translated error."""
    action = AsyncMock(return_value=result)

    with pytest.raises(HomeAssistantError) as caught:
        await async_execute_command(
            CommandFailure(
                translation_key="command_failed",
                translation_placeholders={"device_id": "dev1"},
                rejected_translation_key="restart_rejected",
            ),
            action,
        )

    assert caught.value.translation_domain == DOMAIN
    assert caught.value.translation_key == "restart_rejected"
    assert caught.value.translation_placeholders == {"device_id": "dev1"}


@pytest.mark.parametrize("result", [False, None, []])
async def test_falsy_result_is_returned_without_a_rejection_key(
    result: object,
) -> None:
    """Without a rejection key the result is returned as the API gave it."""
    action = AsyncMock(return_value=result)

    assert (
        await async_execute_command(
            CommandFailure(translation_key="command_failed"), action
        )
        == result
    )


def _placeholders(message: str) -> set[str]:
    """Return the format placeholders of a translation message."""
    return {
        name for _, name, _, _ in string.Formatter().parse(message) if name is not None
    }


@pytest.mark.parametrize("key", sorted(_COMMAND_KEYS))
def test_command_exception_keys_match_in_both_files(key: str) -> None:
    """Each new key exists in strings.json and en.json with its placeholders."""
    strings = json.loads((_INTEGRATION_DIR / "strings.json").read_text())
    english = json.loads((_INTEGRATION_DIR / "translations" / "en.json").read_text())

    message = strings["exceptions"][key]["message"]
    assert english["exceptions"][key] == strings["exceptions"][key]
    assert _placeholders(message) == _COMMAND_KEYS[key]


# Modules that run write commands or call their owners (ADR-010).
_TRANSLATED_ERRORS = frozenset({"HomeAssistantError", "ServiceValidationError"})

_COMMAND_MODULES = (
    "coordinators/base.py",
    "coordinators/carrier_fabric.py",
    "coordinators/config.py",
    "coordinators/device.py",
    "coordinators/facade.py",
    "coordinators/protect.py",
    "alarm_control_panel.py",
    "button.py",
    "camera.py",
    "cover.py",
    "light.py",
    "lock.py",
    "media_player.py",
    "number.py",
    "select.py",
    "siren.py",
    "switch.py",
)


def _raised_error_name(exc: ast.expr | None) -> str | None:
    """Return the class name a ``raise`` uses, for ``X``, ``X(...)``, ``m.X(...)``."""
    target = exc.func if isinstance(exc, ast.Call) else exc
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


@pytest.mark.parametrize("module", _COMMAND_MODULES)
def test_command_modules_raise_only_translated_errors(module: str) -> None:
    """Every HomeAssistantError or ServiceValidationError raised names a key."""
    tree = ast.parse((_INTEGRATION_DIR / module).read_text())
    untranslated = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Raise)
        and _raised_error_name(node.exc) in _TRANSLATED_ERRORS
        and not (
            isinstance(node.exc, ast.Call)
            and "translation_key" in {kw.arg for kw in node.exc.keywords}
        )
    ]

    assert untranslated == []


@pytest.mark.parametrize(
    "source",
    [
        "raise HomeAssistantError",
        "raise HomeAssistantError('x')",
        "raise exceptions.HomeAssistantError('x')",
        "raise ServiceValidationError(translation_domain='d')",
    ],
)
def test_translation_guard_catches_untranslated_raise_forms(source: str) -> None:
    """The guard flags bare, called and attribute-qualified raises."""
    node = ast.parse(source).body[0]
    assert isinstance(node, ast.Raise)
    assert _raised_error_name(node.exc) in _TRANSLATED_ERRORS
    assert not (
        isinstance(node.exc, ast.Call)
        and "translation_key" in {kw.arg for kw in node.exc.keywords}
    )


# --- Base coordinator --------------------------------------------------------


class TestUnifiBaseCoordinator:
    """Tests for UnifiInsightsBaseCoordinator."""

    @pytest.fixture
    def coordinator(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> UnifiInsightsBaseCoordinator:
        """Create a base coordinator for testing."""
        network_client = _create_mock_network_client()
        protect_client = _create_mock_protect_client()
        return UnifiInsightsBaseCoordinator(
            hass=hass,
            network_client=network_client,
            protect_client=protect_client,
            entry=mock_config_entry,
            name="test",
            update_interval=timedelta(seconds=30),
        )

    def test_initialization(self, coordinator: UnifiInsightsBaseCoordinator):
        """Test coordinator initialization."""
        assert coordinator.name == f"{DOMAIN}_test"
        assert coordinator.update_interval == timedelta(seconds=30)
        assert coordinator._available is True
        assert coordinator.data == {}

    def test_available_property(self, coordinator: UnifiInsightsBaseCoordinator):
        """Test available property."""
        assert coordinator.available is True
        coordinator._available = False
        assert coordinator.available is False

    def test_model_to_dict_with_none(self, coordinator: UnifiInsightsBaseCoordinator):
        """Test model_to_dict with None input."""
        result = coordinator._model_to_dict(None)
        assert result == {}

    def test_model_to_dict_with_dict(self, coordinator: UnifiInsightsBaseCoordinator):
        """Test model_to_dict with dict input."""
        input_dict = {"key": "value"}
        result = coordinator._model_to_dict(input_dict)
        assert result == input_dict

    def test_model_to_dict_with_pydantic_model(
        self, coordinator: UnifiInsightsBaseCoordinator
    ):
        """Test model_to_dict with pydantic model."""
        mock_model = MagicMock()
        mock_model.model_dump = MagicMock(return_value={"id": "test", "name": "Test"})
        result = coordinator._model_to_dict(mock_model)
        assert result == {"id": "test", "name": "Test"}
        mock_model.model_dump.assert_called_once_with(by_alias=True, exclude_none=False)

    def test_model_to_dict_with_pydantic_model_type_error(
        self, coordinator: UnifiInsightsBaseCoordinator
    ):
        """Test model_to_dict fallback when by_alias raises TypeError."""
        mock_model = MagicMock()
        # First call raises TypeError, second call succeeds
        mock_model.model_dump = MagicMock(
            side_effect=[TypeError("by_alias not supported"), {"id": "fallback"}]
        )
        result = coordinator._model_to_dict(mock_model)
        assert result == {"id": "fallback"}

    @pytest.mark.parametrize(
        ("side_effects", "expected_calls"),
        [
            (["not_a_dict"], 1),
            ([TypeError("by_alias not supported"), "not_a_dict"], 2),
        ],
        ids=["direct_non_dict", "type_error_fallback_non_dict"],
    )
    def test_model_to_dict_non_dict_results(
        self,
        coordinator: UnifiInsightsBaseCoordinator,
        side_effects: list[Any],
        expected_calls: int,
    ) -> None:
        """Test model_to_dict returns {} when model_dump or
        TypeError fallback returns non-dict.
        """
        mock_model = MagicMock()
        mock_model.model_dump = MagicMock(side_effect=side_effects)
        result = coordinator._model_to_dict(mock_model)
        assert result == {}
        assert mock_model.model_dump.call_count == expected_calls

    def test_model_to_dict_with_object_dict(
        self, coordinator: UnifiInsightsBaseCoordinator
    ):
        """Test model_to_dict with object having __dict__."""

        class SimpleObject:
            def __init__(self):
                self.id = "test"
                self.name = "Test Object"
                self._private = "hidden"

        obj = SimpleObject()
        result = coordinator._model_to_dict(obj)
        assert result == {"id": "test", "name": "Test Object"}
        assert "_private" not in result

    def test_model_to_dict_with_primitive(
        self, coordinator: UnifiInsightsBaseCoordinator
    ):
        """Test model_to_dict with primitive value."""
        result = coordinator._model_to_dict("string")
        assert result == {}

    def test_handle_auth_error(self, coordinator: UnifiInsightsBaseCoordinator):
        """Test handling authentication error."""
        err = UniFiAuthenticationError("Invalid credentials")
        with pytest.raises(ConfigEntryAuthFailed, match="Authentication failed"):
            coordinator._handle_auth_error(err)
        assert coordinator._available is False

    def test_handle_connection_error(self, coordinator: UnifiInsightsBaseCoordinator):
        """Test handling connection error."""
        err = UniFiConnectionError("Connection refused")
        with pytest.raises(UpdateFailed, match="Error communicating with API"):
            coordinator._handle_connection_error(err)
        assert coordinator._available is False

    def test_handle_timeout_error(self, coordinator: UnifiInsightsBaseCoordinator):
        """Test handling timeout error."""
        err = UniFiTimeoutError("Request timed out")
        with pytest.raises(UpdateFailed, match="Timeout"):
            coordinator._handle_timeout_error(err)
        assert coordinator._available is False

    def test_handle_response_error(self, coordinator: UnifiInsightsBaseCoordinator):
        """Test handling API response error."""
        err = UniFiResponseError("Bad response", status_code=400)
        with pytest.raises(UpdateFailed, match="API error"):
            coordinator._handle_response_error(err)
        assert coordinator._available is False

    @pytest.mark.parametrize(
        ("handler", "err", "detail"),
        [
            ("_handle_timeout_error", UniFiTimeoutError("timed out"), "timed out"),
            (
                "_handle_response_error",
                UniFiResponseError("gateway", status_code=502),
                "502",
            ),
            (
                "_handle_response_error",
                UniFiResponseError("bad request", status_code=400),
                "400",
            ),
            (
                "_handle_generic_error",
                ValueError("unexpected"),
                "ValueError: unexpected",
            ),
        ],
    )
    def test_handlers_leave_outage_logging_to_coordinator(
        self,
        coordinator: UnifiInsightsBaseCoordinator,
        caplog: pytest.LogCaptureFixture,
        handler: str,
        err: Exception,
        detail: str,
    ) -> None:
        """Failures preserve diagnostics without emitting per-poll warnings."""
        caplog.set_level(logging.DEBUG)
        with pytest.raises(UpdateFailed, match=detail) as raised:
            getattr(coordinator, handler)(err)
        assert raised.value.__cause__ is err
        assert coordinator.available is False
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
