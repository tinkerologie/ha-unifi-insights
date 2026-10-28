#!/usr/bin/env python3
# Copyright 2026 UniFi Insights contributors
"""Build the reproducible release zip that HACS and the Marketplace install.

The zip holds the contents of ``custom_components/unifi_insights/`` at its root
(``manifest.json`` at the top level), which HACS and the Marketplace extract into
``config/custom_components/unifi_insights/``. ``SHA256SUMS`` is written next to it.

The same source tree always builds a byte-identical zip: entries are sorted, every
timestamp comes from ``SOURCE_DATE_EPOCH`` (the release commit's time in CI), and
permissions and compression settings are fixed.

Usage:
    python3 script/build_release_zip.py [--output-dir dist] [--version 2026.10.4]
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import zipfile

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "custom_components" / "unifi_insights"
ZIP_NAME = "unifi_insights.zip"
CHECKSUMS_NAME = "SHA256SUMS"

# 1980-01-01T00:00:00Z, the earliest timestamp a zip entry can store.
ZIP_EPOCH = 315532800
EXCLUDED_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache"})
EXCLUDED_SUFFIXES = (".pyc", ".pyo")
FILE_MODE = 0o100644  # regular file, rw-r--r--
COMPRESS_LEVEL = 9


class ReleaseZipError(Exception):
    """Raised when the release zip cannot be built."""


def is_excluded(relative: Path) -> bool:
    """Return True when a path under the package must stay out of the zip."""
    if any(part in EXCLUDED_DIRS or part.startswith(".") for part in relative.parts):
        return True
    return relative.name.endswith(EXCLUDED_SUFFIXES)


def iter_package_files(package_dir: Path) -> Iterator[Path]:
    """Yield the runtime files under ``package_dir`` as sorted relative paths."""
    files: list[Path] = []
    for path in package_dir.rglob("*"):
        relative = path.relative_to(package_dir)
        if is_excluded(relative):
            continue
        if path.is_symlink():
            raise ReleaseZipError(f"Symbolic links are not allowed: {relative}")
        if path.is_file():
            files.append(relative)
    yield from sorted(files, key=lambda p: p.as_posix())


def source_date_epoch(environ: dict[str, str] | None = None) -> int:
    """Return the entry timestamp from ``SOURCE_DATE_EPOCH`` (clamped to 1980)."""
    value = (os.environ if environ is None else environ).get("SOURCE_DATE_EPOCH")
    if not value:
        return ZIP_EPOCH
    try:
        epoch = int(value)
    except ValueError as err:
        raise ReleaseZipError(f"Invalid SOURCE_DATE_EPOCH: {value!r}") from err
    return max(epoch, ZIP_EPOCH)


def manifest_version(package_dir: Path) -> str:
    """Return the ``version`` from the package's ``manifest.json``."""
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ReleaseZipError(f"{manifest_path} not found")
    version = json.loads(manifest_path.read_text(encoding="utf-8")).get("version")
    if not isinstance(version, str) or not version:
        raise ReleaseZipError("manifest.json has no version")
    return version


def check_version(package_dir: Path, expected: str) -> None:
    """Fail when ``manifest.json`` does not match the release tag ``expected``."""
    expected = expected.removeprefix("v")
    actual = manifest_version(package_dir)
    if actual != expected:
        raise ReleaseZipError(
            f"manifest.json version {actual} does not match release {expected}"
        )


def build_zip(package_dir: Path, zip_path: Path, epoch: int) -> list[str]:
    """Write the reproducible zip and return its entry names in order."""
    date_time = time.gmtime(epoch)[:6]
    names: list[str] = []
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w") as archive:
        for relative in iter_package_files(package_dir):
            name = relative.as_posix()
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix, so external_attr holds the file mode
            info.external_attr = FILE_MODE << 16
            data = (package_dir / relative).read_bytes()
            archive.writestr(info, data, compresslevel=COMPRESS_LEVEL)
            names.append(name)
    if "manifest.json" not in names:
        raise ReleaseZipError("manifest.json is missing from the package")
    return names


def write_checksums(output_dir: Path, file_names: Sequence[str]) -> Path:
    """Write ``SHA256SUMS`` in the format ``sha256sum -c`` reads."""
    lines = []
    for file_name in file_names:
        digest = hashlib.sha256((output_dir / file_name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {file_name}\n")
    checksums_path = output_dir / CHECKSUMS_NAME
    checksums_path.write_text("".join(lines), encoding="utf-8")
    return checksums_path


def build_release(
    output_dir: Path,
    *,
    package_dir: Path = PACKAGE_DIR,
    version: str | None = None,
    environ: dict[str, str] | None = None,
) -> Path:
    """Build ``unifi_insights.zip`` and ``SHA256SUMS`` in ``output_dir``."""
    if version is not None:
        check_version(package_dir, version)
    zip_path = output_dir / ZIP_NAME
    build_zip(package_dir, zip_path, source_date_epoch(environ))
    write_checksums(output_dir, [ZIP_NAME])
    return zip_path


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = argparse.ArgumentParser(description="Build the release zip.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "dist",
        help="Directory for unifi_insights.zip and SHA256SUMS (default: dist)",
    )
    parser.add_argument(
        "--version",
        help="Release version or tag; fail if manifest.json differs",
    )
    args = parser.parse_args(argv)

    try:
        zip_path = build_release(args.output_dir, version=args.version)
    except ReleaseZipError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    with zipfile.ZipFile(zip_path) as archive:
        count = len(archive.namelist())
    print(f"Built {zip_path} ({count} files, sha256 {digest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
