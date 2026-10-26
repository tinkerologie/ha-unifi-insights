# Copyright 2026 UniFi Insights contributors
"""Regression coverage for real sparse Core checkouts and validator root paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fixtures.validation_scripts import validation_project

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("cache", ["fresh", "same-tag", "old-tag", "update"])
def test_full_hassfest_uses_core_requirements(tmp_path: Path, cache: str) -> None:
    project = validation_project(tmp_path)
    interpreter = project.interpreter(".local/ha-venv")
    if cache != "fresh":
        core = project.cached_core("2026.10.1" if cache == "old-tag" else "2026.10.2")
        assert not (core / "requirements.txt").exists()
    args = ["--skip-plugins", ""]
    if cache == "update":
        args.append("--update")
    result = project.run("hassfest", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    call = project.trace()[-1]
    assert call["python"] == str(interpreter)
    arguments = call["args"]
    assert isinstance(arguments, list)
    assert "--skip-plugins" not in arguments
    assert arguments[arguments.index("--core-path") + 1] == str(
        project.root / ".local/ha-core"
    )
    assert arguments[arguments.index("--integration-path") + 1] == str(
        project.root / "custom_components/unifi_insights"
    )
    assert (
        "core-package" in (project.root / ".local/ha-core/requirements.txt").read_text()
    )
    assert "unifi-official-api" in (project.root / "requirements.txt").read_text()
    if cache in ("old-tag", "update"):
        assert "Updating to version" in result.stdout


@pytest.mark.parametrize(
    "missing",
    [
        "requirements.txt",
        "requirements_all.txt",
        "homeassistant/package_constraints.txt",
    ],
)
def test_missing_core_files_cannot_produce_an_incomplete_pass(
    tmp_path: Path, missing: str
) -> None:
    project = validation_project(tmp_path, missing=missing)
    project.interpreter(".venv")
    result = project.run("hassfest", "--skip-plugins", "")
    assert result.returncode != 0
    assert f"missing {missing}" in result.stderr
    assert project.trace() == []


def test_failed_fetch_stops_before_validation(tmp_path: Path) -> None:
    project = validation_project(tmp_path)
    project.interpreter(".venv")
    script = project.root / "script/hassfest"
    script.write_text(
        script.read_text().replace(
            str(project.origin), str(project.root / "missing-origin")
        )
    )
    result = project.run("hassfest", "--skip-plugins", "")
    assert result.returncode != 0
    assert project.trace() == []


def test_validator_failure_is_propagated(tmp_path: Path) -> None:
    project = validation_project(tmp_path)
    project.interpreter(".venv")
    project.env["FAKE_VALIDATOR_STATUS"] = "7"
    result = project.run("hassfest", "--skip-plugins", "")
    assert result.returncode != 0
    assert "Hassfest validation failed" in result.stderr


def test_arguments_and_explicit_active_interpreter_are_preserved(
    tmp_path: Path,
) -> None:
    project = validation_project(tmp_path)
    active = project.interpreter("active environment")
    project.interpreter(".local/ha-venv")
    project.env["VIRTUAL_ENV"] = str(active.parent.parent)
    result = project.run(
        "hassfest", "--skip-plugins", "quality_scale", "--requirements"
    )
    assert result.returncode == 0, result.stderr
    assert project.trace()[-1]["python"] == str(active)
    arguments = project.trace()[-1]["args"]
    assert isinstance(arguments, list)
    assert arguments[arguments.index("--skip-plugins") + 1] == "quality_scale"
    assert arguments[-1] == "--requirements"


def test_dependency_installation_targets_the_selected_interpreter(
    tmp_path: Path,
) -> None:
    project = validation_project(tmp_path)
    python = project.interpreter(".venv")
    project.env["FAKE_MISSING_TQDM"] = "1"
    project.fake_uv()
    result = project.run("hassfest", "--skip-plugins", "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert project.trace()[0] == {
        "uv_args": ["pip", "install", "--python", str(python), "tqdm"]
    }


def test_dependency_installation_without_uv_uses_selected_python(
    tmp_path: Path,
) -> None:
    project = validation_project(tmp_path)
    python = project.interpreter(".venv")
    project.without_uv()
    project.env["FAKE_MISSING_TQDM"] = "1"
    result = project.run("hassfest", "--skip-plugins", "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert project.trace()[0] == {
        "python": str(python),
        "args": ["-m", "pip", "install", "tqdm"],
    }


def test_missing_home_assistant_dependencies_stop_before_validation(
    tmp_path: Path,
) -> None:
    project = validation_project(tmp_path)
    project.interpreter(".venv")
    project.env["FAKE_MISSING_HA"] = "1"
    result = project.run("hassfest", "--skip-plugins", "")
    assert result.returncode != 0
    assert "Missing required dependencies" in result.stderr
    assert project.trace() == []


@pytest.mark.parametrize("cache", ["fresh", "tag", "dev"])
def test_dev_core_fetch_paths(tmp_path: Path, cache: str) -> None:
    project = validation_project(tmp_path)
    python = project.interpreter(".venv")
    project.env["HA_VERSION"] = "dev"
    if cache != "fresh":
        project.cached_core("dev" if cache == "dev" else "2026.10.2")
    args = ["--skip-plugins", ""]
    if cache == "dev":
        args.append("--update")
    result = project.run("hassfest", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert project.trace()[-1]["python"] == str(python)
    assert "Target version: dev" in result.stdout
