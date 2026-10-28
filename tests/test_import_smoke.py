# Copyright 2026 UniFi Insights contributors
"""Tests for script/import_smoke.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from .fixtures.deprecations import HANDLER as DEPRECATION_REPORTS

if TYPE_CHECKING:
    from collections.abc import Generator

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "script" / "import_smoke.py"


@pytest.fixture(scope="module")
def smoke() -> Any:
    """Load the import_smoke script as a module."""
    spec = importlib.util.spec_from_file_location("import_smoke", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fake_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Return a scratch import root and forget the modules imported from it."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = set(sys.modules)
    yield tmp_path
    for name in set(sys.modules) - before:
        del sys.modules[name]


def _package(root: Path, files: dict[str, str]) -> Path:
    package_dir = root / "smoke_ns" / "fake_integration"
    for name, source in {"__init__.py": "", **files}.items():
        path = package_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    (root / "smoke_ns" / "__init__.py").write_text("")
    return package_dir


def test_module_names_lists_the_package_first(smoke: Any) -> None:
    """The real integration is listed package first, then by depth."""
    names = smoke.module_names()

    assert names[0] == "custom_components.unifi_insights"
    assert "custom_components.unifi_insights.sensor" in names
    assert "custom_components.unifi_insights.coordinators" in names
    depths = [name.count(".") for name in names]
    assert depths == sorted(depths)


def test_clean_package_passes(
    smoke: Any, fake_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A package without deprecated use exits 0."""
    package_dir = _package(fake_root, {"ok.py": "VALUE = 1\n"})

    assert smoke.run(package_dir) == 0
    assert "No deprecated use by unifi_insights found" in capsys.readouterr().out


def test_deprecations_reports_and_import_errors_fail(
    smoke: Any, fake_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Own warnings, HA reports and broken imports fail; others are listed."""
    (fake_root / "smoke_third_party.py").write_text(
        "import warnings\n"
        "warnings.warn('library internals', DeprecationWarning, stacklevel=1)\n"
    )
    package_dir = _package(
        fake_root,
        {
            "own.py": (
                "import warnings\n"
                "import smoke_third_party\n"
                "warnings.warn('own use', DeprecationWarning, stacklevel=1)\n"
                "warnings.warn('not a deprecation', UserWarning, stacklevel=1)\n"
            ),
            "reported.py": (
                "import logging\n"
                "logging.getLogger('homeassistant.helpers.frame').warning(\n"
                "    \"Detected that custom integration 'unifi_insights' uses X\"\n"
                ")\n"
            ),
            "broken.py": "from smoke_missing import thing\n",
        },
    )

    try:
        assert smoke.run(package_dir) == 1
    finally:
        DEPRECATION_REPORTS.pop_reports()

    out = capsys.readouterr().out
    assert "3 problem(s) in unifi_insights" in out
    assert "DeprecationWarning: own use" in out
    assert "Home Assistant report: homeassistant.helpers.frame" in out
    assert "import smoke_ns.fake_integration.broken failed" in out
    assert "ignored third-party DeprecationWarning: library internals" in out
    assert "not a deprecation" not in out
