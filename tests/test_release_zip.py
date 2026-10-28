# Copyright 2026 UniFi Insights contributors
"""Tests for script/build_release_zip.py, the HACS/Marketplace release asset."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "script" / "build_release_zip.py"
PACKAGE_DIR = REPO_ROOT / "custom_components" / "unifi_insights"


@pytest.fixture(scope="module")
def release_zip() -> Any:
    """Load the build script as a module."""
    spec = importlib.util.spec_from_file_location("build_release_zip", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _expected_files(package_dir: Path) -> list[str]:
    """List the package files independently of the script's own walker."""
    names = []
    for path in package_dir.rglob("*"):
        relative = path.relative_to(package_dir)
        if not path.is_file() or "__pycache__" in relative.parts:
            continue
        if relative.suffix in {".pyc", ".pyo"} or any(
            part.startswith(".") for part in relative.parts
        ):
            continue
        names.append(relative.as_posix())
    return sorted(names)


def _fake_package(root: Path, version: str = "2026.10.4") -> Path:
    package = root / "unifi_insights"
    (package / "translations").mkdir(parents=True)
    (package / "__pycache__").mkdir()
    (package / "manifest.json").write_text(json.dumps({"version": version}))
    (package / "__init__.py").write_text('"""Init."""\n')
    (package / "translations" / "en.json").write_text("{}")
    (package / "__pycache__" / "__init__.cpython-314.pyc").write_bytes(b"\0")
    (package / "stale.pyc").write_bytes(b"\0")
    (package / ".DS_Store").write_bytes(b"\0")
    return package


def test_real_package_zip_layout(release_zip: Any, tmp_path: Path) -> None:
    """The zip holds the package contents at its root, minus build leftovers."""
    zip_path = release_zip.build_release(tmp_path, environ={})
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        infos = archive.infolist()
        assert archive.testzip() is None

    assert "manifest.json" in names
    assert names == sorted(names)
    assert names == _expected_files(PACKAGE_DIR)
    for required in (
        "__init__.py",
        "services.yaml",
        "strings.json",
        "icons.json",
        "translations/en.json",
        "brand/icon.png",
        "frontend/topology-card.js",
    ):
        assert required in names
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]
    assert not [n for n in names if n.startswith("custom_components/")]
    for info in infos:
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert info.compress_type == zipfile.ZIP_DEFLATED
        assert stat.S_IMODE(info.external_attr >> 16) == 0o644


def test_builds_are_byte_identical(release_zip: Any, tmp_path: Path) -> None:
    """Two builds with the same SOURCE_DATE_EPOCH produce the same bytes."""
    environ = {"SOURCE_DATE_EPOCH": "1761700000"}
    first = release_zip.build_release(tmp_path / "a", environ=environ)
    second = release_zip.build_release(tmp_path / "b", environ=environ)
    assert first.read_bytes() == second.read_bytes()
    checksums = (tmp_path / "a" / "SHA256SUMS").read_text()
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    assert checksums == f"{digest}  unifi_insights.zip\n"
    assert checksums == (tmp_path / "b" / "SHA256SUMS").read_text()
    with zipfile.ZipFile(first) as archive:
        assert archive.infolist()[0].date_time == (2025, 10, 29, 1, 6, 40)


def test_exclusions(release_zip: Any, tmp_path: Path) -> None:
    """Bytecode, caches and hidden files never reach the zip."""
    package = _fake_package(tmp_path)
    zip_path = release_zip.build_release(
        tmp_path / "out", package_dir=package, version="v2026.10.4", environ={}
    )
    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist() == [
            "__init__.py",
            "manifest.json",
            "translations/en.json",
        ]


def test_version_mismatch_fails(release_zip: Any, tmp_path: Path) -> None:
    """A release tag that differs from manifest.json stops the build."""
    package = _fake_package(tmp_path, version="2026.10.4")
    with pytest.raises(release_zip.ReleaseZipError, match="does not match"):
        release_zip.build_release(tmp_path / "out", package_dir=package, version="v1")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    ("manifest", "message"),
    [(None, "not found"), ({}, "no version")],
)
def test_bad_manifest(
    release_zip: Any, tmp_path: Path, manifest: dict[str, str] | None, message: str
) -> None:
    """A missing manifest or version is reported."""
    package = tmp_path / "pkg"
    package.mkdir()
    if manifest is not None:
        (package / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(release_zip.ReleaseZipError, match=message):
        release_zip.check_version(package, "1")


def test_zip_without_manifest_fails(release_zip: Any, tmp_path: Path) -> None:
    """The zip must carry manifest.json at its root."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    with pytest.raises(release_zip.ReleaseZipError, match="missing"):
        release_zip.build_release(tmp_path / "out", package_dir=package, environ={})


def test_symlink_rejected(release_zip: Any, tmp_path: Path) -> None:
    """Symbolic links would make the zip depend on the build machine."""
    package = _fake_package(tmp_path)
    (package / "link.py").symlink_to(package / "__init__.py")
    with pytest.raises(release_zip.ReleaseZipError, match="Symbolic links"):
        release_zip.build_release(tmp_path / "out", package_dir=package, environ={})


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", 315532800), ("0", 315532800), ("1761700000", 1761700000)],
)
def test_source_date_epoch(release_zip: Any, value: str, expected: int) -> None:
    """Timestamps come from SOURCE_DATE_EPOCH, clamped to the zip epoch."""
    assert release_zip.source_date_epoch({"SOURCE_DATE_EPOCH": value}) == expected


def test_invalid_source_date_epoch(release_zip: Any) -> None:
    """A malformed SOURCE_DATE_EPOCH is an error, not a silent default."""
    with pytest.raises(release_zip.ReleaseZipError, match="SOURCE_DATE_EPOCH"):
        release_zip.source_date_epoch({"SOURCE_DATE_EPOCH": "yesterday"})


def test_cli(
    release_zip: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command line builds the zip and rejects a mismatched version."""
    manifest = json.loads((PACKAGE_DIR / "manifest.json").read_text())
    version = f"v{manifest['version']}"
    assert release_zip.main(["--output-dir", str(tmp_path), "--version", version]) == 0
    assert "unifi_insights.zip (" in capsys.readouterr().out
    assert (tmp_path / "SHA256SUMS").is_file()

    out_dir = str(tmp_path / "x")
    assert release_zip.main(["--output-dir", out_dir, "--version", "0.0.0"]) == 1
    assert "does not match" in capsys.readouterr().err
