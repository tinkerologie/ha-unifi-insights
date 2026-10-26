# Copyright 2026 UniFi Insights contributors
"""Regression coverage for interpreter consistency and warning failures."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fixtures.validation_scripts import validation_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("selected", ["active", "local", "venv", "home"])
def test_interpreter_precedence(tmp_path: Path, selected: str) -> None:
    project = validation_project(tmp_path)
    locations = {
        "active": project.root / "active environment",
        "local": project.root / ".local/ha-venv",
        "venv": project.root / ".venv",
        "home": project.user_home / ".local/ha-venv",
    }
    order = list(locations)
    for key in order[order.index(selected) :]:
        project.interpreter(locations[key])
    if selected == "active":
        project.env["VIRTUAL_ENV"] = str(locations[selected])
    result = project.run("type-check")
    assert result.returncode == 0, result.stderr
    call = project.trace()[-1]
    assert call["python"] == str(locations[selected] / "bin/python")
    assert call["args"] == [
        "-m",
        "pyright",
        "--project",
        str(project.root / "pyrightconfig.json"),
        "--pythonpath",
        call["python"],
        "--warnings",
    ]


def test_active_environment_is_not_silently_replaced(tmp_path: Path) -> None:
    project = validation_project(tmp_path)
    project.interpreter(".local/ha-venv")
    project.env["VIRTUAL_ENV"] = str(project.root / "broken active environment")
    result = project.run("type-check")
    assert result.returncode != 0
    assert "Active virtual environment" in result.stderr
    assert project.trace() == []


def test_missing_environment_has_actionable_error(tmp_path: Path) -> None:
    project = validation_project(tmp_path)
    result = project.run("type-check")
    assert result.returncode != 0
    assert "bootstrap" in result.stderr


def test_warnings_fail_the_canonical_command(tmp_path: Path) -> None:
    project = validation_project(tmp_path)
    project.interpreter(".venv")
    project.env["FAKE_WARNINGS"] = "1"
    result = project.run("type-check")
    assert result.returncode == 1
    assert "0 errors, 1 warning" in result.stdout


def test_mypy_uses_the_same_explicit_interpreter(tmp_path: Path) -> None:
    project = validation_project(tmp_path)
    active = project.interpreter("active environment")
    project.interpreter(".venv")
    project.env["VIRTUAL_ENV"] = str(active.parent.parent)
    result = project.run("mypy")
    assert result.returncode == 0, result.stderr
    assert project.trace() == [
        {
            "python": str(active),
            "args": ["-m", "mypy", "custom_components/unifi_insights"],
        }
    ]


def test_pyright_installation_targets_the_selected_interpreter(tmp_path: Path) -> None:
    project = validation_project(tmp_path)
    python = project.interpreter(".venv")
    project.env["FAKE_MISSING_PYRIGHT"] = "1"
    project.fake_uv()
    result = project.run("type-check")
    assert result.returncode == 0, result.stderr
    assert project.trace()[0] == {
        "uv_args": ["pip", "install", "--python", str(python), "pyright"]
    }


def test_old_python_stops_before_validation(tmp_path: Path) -> None:
    project = validation_project(tmp_path)
    project.interpreter(".venv")
    project.env["FAKE_OLD_PYTHON"] = "1"
    result = project.run("type-check")
    assert result.returncode != 0
    assert "AssertionError" in result.stderr
    assert project.trace() == []


def test_missing_pyright_without_uv_has_actionable_error(tmp_path: Path) -> None:
    project = validation_project(tmp_path)
    project.interpreter(".venv")
    project.without_uv()
    project.env["FAKE_MISSING_PYRIGHT"] = "1"
    result = project.run("type-check")
    assert result.returncode != 0
    assert "Install Pyright through ./script/setup/bootstrap" in result.stderr
    assert project.trace() == []
