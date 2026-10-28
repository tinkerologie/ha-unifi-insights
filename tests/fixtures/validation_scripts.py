# Copyright 2026 UniFi Insights contributors
"""Isolated script harness: real local Git checkouts, instrumented Python tools."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PYTHON_TOOL = """import json, os, pathlib, sys
args = sys.argv[1:]
if args[:1] == ['-c']:
    if args[1] == 'import pyright' and os.environ.get('FAKE_MISSING_PYRIGHT') == '1':
        sys.exit(1)
    if 'sys.version_info' in args[1]:
        if os.environ.get('FAKE_OLD_PYTHON') == '1':
            sys.version_info = (3, 13)
        exec(args[1])
    sys.exit(0)
with open(os.environ['VALIDATION_TRACE'], 'a') as trace:
    trace.write(json.dumps({'python': sys.argv[0], 'args': args}) + '\\n')
if args[:2] == ['-m', 'script.hassfest']:
    import runpy
    sys.path.insert(0, os.getcwd())
    runpy.run_module('script.hassfest', run_name='__main__')
    core = pathlib.Path(args[args.index('--core-path')+1])
    integration = pathlib.Path(args[args.index('--integration-path')+1])
    assert core.is_absolute() and integration.is_absolute()
    assert 'unifi-official-api' not in (core/'requirements.txt').read_text()
    project_requirements = integration.parents[1]/'requirements.txt'
    assert 'unifi-official-api' in project_requirements.read_text()
if args[:2] == ['-m', 'pyright'] and os.environ.get('FAKE_WARNINGS') == '1':
    print('0 errors, 1 warning')
    sys.exit(1 if '--warnings' in args else 0)
sys.exit(int(os.environ.get('FAKE_VALIDATOR_STATUS', '0')))
"""


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed Git executable and local fixture arguments
        ["git", "-c", "commit.gpgsign=false", *args],  # noqa: S607 - standard test prerequisite
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


@dataclass
class ValidationProject:
    root: Path
    origin: Path
    user_home: Path
    env: dict[str, str]

    def without_uv(self) -> None:
        """Expose only script prerequisites, isolating installer fallback tests."""
        folder = self.root / "bin"
        folder.mkdir(exist_ok=True)
        (folder / "uv").unlink(missing_ok=True)
        for name in ("bash", "dirname", "git", "mkdir"):
            executable = shutil.which(name)
            assert executable is not None
            (folder / name).symlink_to(executable)
        self.env["PATH"] = str(folder)

    def fake_uv(self) -> None:
        """Instrument installs and isolated runs without changing real environments."""
        validator_python = self.interpreter(".uv-cache/hassfest")
        command = self.root / "bin/uv"
        command.parent.mkdir(exist_ok=True)
        command.write_text(
            f"#!{sys.executable}\n"
            "import json, os, subprocess, sys\n"
            "args = sys.argv[1:]\n"
            "record = {'uv_args': args}\n"
            "if args[:1] == ['run']:\n"
            "    record['environment'] = {key: os.environ.get(key) for key in "
            "('VIRTUAL_ENV', 'PYTHONPATH', 'PYTHONHOME')}\n"
            "with open(os.environ['VALIDATION_TRACE'], 'a') as trace:\n"
            "    trace.write(json.dumps(record) + '\\n')\n"
            "if os.environ.get('FAKE_UV_STATUS'):\n"
            "    sys.exit(int(os.environ['FAKE_UV_STATUS']))\n"
            "if args[:1] == ['run']:\n"
            "    start = args.index('python') + 1\n"
            f"    sys.exit(subprocess.run([{str(validator_python)!r}, *args[start:]], "
            "check=False).returncode)\n"
        )
        command.chmod(0o755)
        self.env["PATH"] = str(command.parent) + os.pathsep + self.env["PATH"]

    def interpreter(self, location: Path | str) -> Path:
        folder = self.root / location
        python = folder / "bin/python"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text(f"#!{sys.executable}\n" + PYTHON_TOOL)
        python.chmod(0o755)
        return python

    def run(self, name: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - repository scripts copied into a temporary fixture
            ["bash", str(self.root / "script" / name), *args],  # noqa: S607
            cwd=self.root.parent,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
        )

    def trace(self) -> list[dict[str, object]]:
        path = Path(self.env["VALIDATION_TRACE"])
        return (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )

    def cached_core(self, version: str = "2026.10.2") -> Path:
        cache = self.root / ".local/ha-core"
        cache.mkdir(parents=True)
        git(cache, "init")
        git(cache, "remote", "add", "origin", str(self.origin))
        git(cache, "config", "core.sparseCheckout", "true")
        (cache / ".git/info/sparse-checkout").write_text("/script/\n/homeassistant/\n")
        if version == "dev" or re.fullmatch(r"[a-f0-9]{40}", version):
            git(cache, "fetch", "--depth=1", "origin", version)
        else:
            git(cache, "fetch", "--depth=1", "origin", "tag", version)
        git(cache, "checkout", version)
        return cache


def validation_project(
    tmp_path: Path,
    *,
    missing: str | None = None,
    contents: dict[str, str] | None = None,
) -> ValidationProject:
    root = tmp_path / "project with spaces"
    root.mkdir()
    shutil.copytree(REPO / "script", root / "script")
    (root / "pyrightconfig.json").write_text("{}\n")
    (root / "custom_components/unifi_insights").mkdir(parents=True)
    (root / "requirements.txt").write_text("unifi-official-api[talkback]==3.0.0\n")
    origin = tmp_path / "local core origin"
    origin.mkdir()
    core_files = {
        ".gitignore": "__pycache__/\n*.py[cod]\n",
        "requirements.txt": "core-package==1.0\n",
        "requirements_all.txt": "core-package==1.0\n",
        "requirements_test.txt": "pipdeptree==1.0\ntqdm==2.0\n",
        "requirements_test_pre_commit.txt": "ruff==3.0\n",
        "pyproject.toml": "[project]\nname='core-fixture'\n",
        "homeassistant/package_constraints.txt": "core-package==1.0\n",
        "script/hassfest/__main__.py": "# Synthetic validator package\n",
    }
    # ``contents`` replaces a default file or adds one the Core fixture lacks.
    for name, content in {**core_files, **(contents or {})}.items():
        if name == missing:
            continue
        path = origin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(origin, "init", "--initial-branch=dev")
    git(origin, "config", "user.name", "Validation fixture")
    git(origin, "config", "user.email", "fixture@example.invalid")
    git(origin, "add", ".")
    git(origin, "commit", "-m", "Fixture Core requirements")
    for version in ("2026.10.1", "2026.10.2"):
        git(origin, "tag", version)
    wrapper = root / "script/hassfest"
    wrapper.write_text(
        re.sub(
            r'^HASSFEST_CORE_REF="[a-f0-9]{40}"$',
            f'HASSFEST_CORE_REF="{git(origin, "rev-parse", "HEAD").stdout.strip()}"',
            wrapper.read_text(),
            flags=re.MULTILINE,
        ).replace(
            'HA_CORE_REPO="https://github.com/home-assistant/core.git"',
            "HA_CORE_REPO=" + shlex.quote(str(origin)),
        )
    )
    user_home = tmp_path / "fixture user home"
    user_home.mkdir()
    env = dict(os.environ)
    for name in ("VIRTUAL_ENV", "HA_VERSION", "PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    env["HOME"] = str(user_home)
    env["VALIDATION_TRACE"] = str(tmp_path / "tool-invocations.jsonl")
    return ValidationProject(root, origin, user_home, env)
