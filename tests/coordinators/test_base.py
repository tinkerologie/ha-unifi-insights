# Copyright 2026 UniFi Insights contributors
"""Tests for the shared command executor in coordinators/base.py (ADR-010)."""

from __future__ import annotations

import ast
import json
import logging
import string
import traceback
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pydantic import ValidationError
from unifi_official_api import UniFiConnectionError
from unifi_official_api.network.models.voucher import Voucher

from custom_components.unifi_insights.const import DOMAIN
from custom_components.unifi_insights.coordinators.base import (
    CommandFailure,
    async_execute_command,
)

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


def _invalid_voucher() -> ValidationError:
    """Return a pydantic error whose text would echo a voucher code."""
    with pytest.raises(ValidationError) as invalid:
        Voucher.model_validate("1234567890")
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
async def test_validation_error_is_sanitised_and_unchained(
    caplog: pytest.LogCaptureFixture, message: str | None
) -> None:
    """Pydantic errors log field paths only and suppress the raw chain."""
    action = AsyncMock(side_effect=_invalid_voucher())

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
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True
    assert "1234567890" not in caplog.text
    assert "1234567890" not in "".join(traceback.format_exception(caught.value))
    assert "Invalid API response data (fields: root)" in caplog.text
    if message is not None:
        assert str(caught.value).startswith(f"{message}: Invalid API response data")


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
