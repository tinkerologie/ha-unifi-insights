# Copyright 2026 UniFi Insights contributors
"""Shared UniFi InnerSpace test fixtures and client doubles."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from unifi_official_api import (
    UniFiAuthenticationError,
    UniFiResponseError,
    UniFiTimeoutError,
)
from unifi_official_api.innerspace import (
    InnerSpaceProject,
    InnerSpaceProjectIdentity,
)

SAMPLE_PROJECT_ID = "project-1"
SAMPLE_PROJECT_NAME = "Main Office"


def build_innerspace_project(
    project_id: str = SAMPLE_PROJECT_ID,
    name: str = SAMPLE_PROJECT_NAME,
) -> InnerSpaceProject:
    """Return a valid InnerSpace project with a project identity."""
    return InnerSpaceProject(
        project=InnerSpaceProjectIdentity(id=project_id, name=name),
        plans=[],
        products=[],
    )


def innerspace_error(
    kind: str,
) -> Exception:
    """Return an error corresponding to the requested scenario."""
    if kind == "401":
        return UniFiAuthenticationError("Invalid API key", status_code=401)
    if kind == "403":
        return UniFiAuthenticationError("Forbidden", status_code=403)
    if kind == "timeout":
        return UniFiTimeoutError("Timed out")
    if kind == "502":
        return UniFiResponseError("Bad Gateway", status_code=502)
    if kind == "malformed":
        return UniFiResponseError(
            "Project response failed validation (id)",
            status_code=200,
            response_body=None,
        )

    class UnknownErrorKindError(ValueError):
        """Raised when an unknown error kind is requested."""

    raise UnknownErrorKindError(kind)


def make_innerspace_client_context(
    *,
    project: InnerSpaceProject | None = None,
    project_side_effect: Exception | None = None,
    floor_plans: list[Any] | None = None,
) -> MagicMock:
    """Create an async context manager mock for UniFiInnerSpaceClient."""
    client = MagicMock()
    client.base_url = "https://192.168.1.1"
    if project_side_effect is not None:
        client.get_project = AsyncMock(side_effect=project_side_effect)
    elif project is not None:
        client.get_project = AsyncMock(return_value=project)
    else:
        client.get_project = AsyncMock(return_value=InnerSpaceProject())
    client.list_floor_plans = AsyncMock(return_value=floor_plans or [])
    client.list_access_points = AsyncMock(return_value=[])
    client.list_switches = AsyncMock(return_value=[])
    client.list_inventory = AsyncMock(return_value=[])
    client.validate_connection = AsyncMock(return_value=True)
    client.close = AsyncMock()

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=None)
    for attr in dir(client):
        if not attr.startswith("_"):
            setattr(cm, attr, getattr(client, attr))
    return cm
