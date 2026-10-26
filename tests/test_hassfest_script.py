# Copyright 2026 UniFi Insights contributors
"""Pinning, source validation and environment isolation for the canonical wrapper."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
import yaml

from tests.fixtures.validation_scripts import REPO, git, validation_project

if TYPE_CHECKING:
    from pathlib import Path

    from tests.fixtures.validation_scripts import ValidationProject


def project_with_uv(
    tmp_path: Path,
    *,
    missing: str | None = None,
    contents: dict[str, str] | None = None,
) -> ValidationProject:
    project = validation_project(tmp_path, missing=missing, contents=contents)
    project.fake_uv()
    return project


def pinned_revision(project: ValidationProject) -> str:
    source = (project.root / "script/hassfest").read_text()
    match = re.search(r'^HASSFEST_CORE_REF="([a-f0-9]{40})"$', source, re.MULTILINE)
    assert match is not None
    return match[1]


@pytest.mark.parametrize("cache", ["fresh", "same-tag", "old-tag", "update"])
def test_full_hassfest_uses_core_requirements(tmp_path: Path, cache: str) -> None:
    project = project_with_uv(tmp_path)
    project.env["HA_VERSION"] = "2026.10.2"
    if cache != "fresh":
        core = project.cached_core("2026.10.1" if cache == "old-tag" else "2026.10.2")
        assert not (core / "requirements.txt").exists()
    args = ["--skip-plugins", ""]
    if cache == "update":
        args.append("--update")
    result = project.run("hassfest", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    call = project.trace()[-1]
    assert call["python"] == str(project.root / ".uv-cache/hassfest/bin/python")
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


@pytest.mark.parametrize(
    "missing",
    [
        "requirements.txt",
        "requirements_all.txt",
        "requirements_test.txt",
        "requirements_test_pre_commit.txt",
        "homeassistant/package_constraints.txt",
        "script/hassfest/__main__.py",
    ],
)
def test_missing_core_files_stop_before_validation(
    tmp_path: Path, missing: str
) -> None:
    project = project_with_uv(tmp_path, missing=missing)
    result = project.run("hassfest", "--skip-plugins", "")
    assert result.returncode != 0
    assert f"missing {missing}" in result.stderr
    assert project.trace() == []


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("requirements_test.txt", "tqdm==2.0\n"),
        ("requirements_test.txt", "pipdeptree==1.0\npipdeptree==1.1\ntqdm==2.0\n"),
        ("requirements_test.txt", "pipdeptree==1.0\n"),
        ("requirements_test_pre_commit.txt", "ruff>=3.0\n"),
    ],
)
def test_missing_or_ambiguous_tool_pins_fail_closed(
    tmp_path: Path, filename: str, content: str
) -> None:
    project = project_with_uv(tmp_path, contents={filename: content})
    result = project.run("hassfest")
    assert result.returncode != 0
    assert "Missing or ambiguous Core validator tooling pin" in result.stderr
    assert project.trace() == []


def test_failed_fetch_stops_before_validation(tmp_path: Path) -> None:
    project = project_with_uv(tmp_path)
    script = project.root / "script/hassfest"
    script.write_text(
        script.read_text().replace(str(project.origin), str(project.root / "absent"))
    )
    result = project.run("hassfest")
    assert result.returncode != 0
    assert project.trace() == []


@pytest.mark.parametrize("failure", ["FAKE_VALIDATOR_STATUS", "FAKE_UV_STATUS"])
def test_execution_failure_is_propagated(tmp_path: Path, failure: str) -> None:
    project = project_with_uv(tmp_path)
    project.env[failure] = "7"
    result = project.run("hassfest")
    assert result.returncode != 0
    assert "Hassfest validation failed" in result.stderr
    assert "Hassfest validation passed" not in result.stdout


def test_run_is_isolated_and_project_environment_is_untouched(tmp_path: Path) -> None:
    project = project_with_uv(tmp_path)
    active = project.interpreter("active environment")
    sentinel = active.parent / "project-package-sentinel"
    sentinel.write_text("preserve installed project packages\n")
    before = {p: p.read_bytes() for p in active.parent.iterdir()}
    project.env.update(
        VIRTUAL_ENV=str(active.parent.parent),
        PYTHONPATH=str(project.root / "unrelated modules"),
        PYTHONHOME=str(project.root / "invalid Python home"),
    )
    result = project.run(
        "hassfest", "--skip-plugins", "quality_scale", "--requirements"
    )
    assert result.returncode == 0, result.stderr
    assert {p: p.read_bytes() for p in active.parent.iterdir()} == before
    uv_call, validator_call = project.trace()
    assert uv_call["environment"] == dict.fromkeys(
        ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")
    )
    args = uv_call["uv_args"]
    assert isinstance(args, list)
    assert args[:6] == [
        "run",
        "--isolated",
        "--no-project",
        "--python",
        "3.14.2",
        "--with-requirements",
    ]
    assert args[6] == str(project.root / ".local/ha-core/requirements.txt")
    assert args[7:13] == [
        "--with",
        "pipdeptree==1.0",
        "--with",
        "tqdm==2.0",
        "--with",
        "ruff==3.0",
    ]
    assert validator_call["python"] != str(active)
    arguments = validator_call["args"]
    assert isinstance(arguments, list)
    assert arguments[-3:] == ["--skip-plugins", "quality_scale", "--requirements"]


def test_validator_needs_no_project_environment(tmp_path: Path) -> None:
    project = project_with_uv(tmp_path)
    assert not (project.root / ".venv").exists()
    assert not (project.root / ".local/ha-venv").exists()
    result = project.run("hassfest")
    assert result.returncode == 0, result.stderr


def test_missing_uv_has_actionable_error(tmp_path: Path) -> None:
    project = project_with_uv(tmp_path)
    project.without_uv()
    result = project.run("hassfest")
    assert result.returncode != 0
    assert "uv is required" in result.stderr
    assert "bootstrap" in result.stdout
    assert project.trace() == []
    assert not (project.root / ".local/ha-core").exists()


@pytest.mark.parametrize("cache", ["fresh", "tag", "pinned", "update"])
def test_advancing_upstream_never_changes_default_pin(
    tmp_path: Path, cache: str
) -> None:
    project = project_with_uv(tmp_path)
    pinned = pinned_revision(project)
    if cache != "fresh":
        project.cached_core("2026.10.2" if cache == "tag" else pinned)
    (project.origin / "requirements.txt").write_text("core-package==2.0\n")
    git(project.origin, "add", "requirements.txt")
    git(project.origin, "commit", "-m", "Advance development source")
    args = ["--update"] if cache == "update" else []
    result = project.run("hassfest", *args)
    assert result.returncode == 0, result.stdout + result.stderr
    core = project.root / ".local/ha-core"
    assert git(core, "rev-parse", "HEAD").stdout.strip() == pinned
    assert git(core, "branch", "--show-current").stdout.strip() == ""
    assert (core / "requirements.txt").read_text() == "core-package==1.0\n"
    assert f"Validator commit: {pinned}" in result.stdout


def test_explicit_development_override_can_refresh(tmp_path: Path) -> None:
    project = project_with_uv(tmp_path)
    project.cached_core("dev")
    (project.origin / "requirements.txt").write_text("core-package==2.0\n")
    git(project.origin, "add", "requirements.txt")
    git(project.origin, "commit", "-m", "Advance explicitly requested dev")
    project.env["HA_VERSION"] = "dev"
    result = project.run("hassfest", "--update")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        git(project.root / ".local/ha-core", "rev-parse", "HEAD").stdout
        == git(project.origin, "rev-parse", "HEAD").stdout
    )


def test_cached_pin_can_validate_offline(tmp_path: Path) -> None:
    project = project_with_uv(tmp_path)
    project.cached_core(pinned_revision(project))
    project.origin.rename(tmp_path / "offline origin")
    result = project.run("hassfest")
    assert result.returncode == 0, result.stdout + result.stderr


def test_invalid_cache_never_checks_out_parent_project(tmp_path: Path) -> None:
    project = project_with_uv(tmp_path)
    git(project.root, "init")
    (project.root / ".local/ha-core").mkdir(parents=True)
    result = project.run("hassfest")
    assert result.returncode != 0
    assert "Invalid Home Assistant Core cache" in result.stderr
    assert project.trace() == []


@pytest.mark.parametrize("filename", ["script/hassfest/__main__.py", "untracked.py"])
def test_modified_cache_cannot_masquerade_as_reviewed_source(
    tmp_path: Path, filename: str
) -> None:
    project = project_with_uv(tmp_path)
    core = project.cached_core(pinned_revision(project))
    (core / filename).write_text("# Local changes outside the reviewed commit\n")
    result = project.run("hassfest")
    assert result.returncode != 0
    assert "cache contains local changes" in result.stderr
    assert project.trace() == []
    assert (core / filename).exists()


def test_sparse_index_ignores_generated_bytecode_on_repeat_runs(tmp_path: Path) -> None:
    project = project_with_uv(tmp_path)
    core = project.cached_core(pinned_revision(project))
    assert not (core / ".gitignore").exists()
    bytecode = core / "script/hassfest/__pycache__/generated.cpython-314.pyc"
    bytecode.parent.mkdir()
    bytecode.write_bytes(b"Generated validator bytecode")
    git(core, "check-ignore", str(bytecode))
    for _ in range(2):
        result = project.run("hassfest")
        assert result.returncode == 0, result.stdout + result.stderr
    assert bytecode.exists()
    assert not (core / ".gitignore").exists()


def test_missing_skip_argument_is_reported(tmp_path: Path) -> None:
    project = project_with_uv(tmp_path)
    result = project.run("hassfest", "--skip-plugins")
    assert result.returncode != 0
    assert "requires a list" in result.stderr
    assert project.trace() == []


def test_ci_uses_canonical_full_validator_command() -> None:
    workflow = yaml.safe_load((REPO / ".github/workflows/validate.yml").read_text())
    steps = workflow["jobs"]["validate"]["steps"]
    assert any(
        step.get("run") == './script/hassfest --skip-plugins ""' for step in steps
    )
    assert all(
        step.get("with", {}).get("repository") != "home-assistant/core"
        for step in steps
    )
