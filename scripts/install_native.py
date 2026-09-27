#!/usr/bin/env python3
"""Create the native virtual environment and prepare local configuration."""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from typing import NoReturn
from pathlib import Path


SUPPORTED_PYTHON = {(3, 13), (3, 14)}


def fail(message: str) -> NoReturn:
    print(f"AudibleCalendar installation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def virtualenv_python(path: Path) -> Path:
    candidates = (
        path / "Scripts" / "python.exe",
        path / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if os.name == "nt" else candidates[1]


def check_python(version: tuple[int, int], label: str) -> None:
    if version not in SUPPORTED_PYTHON:
        supported = ", ".join(f"{major}.{minor}" for major, minor in sorted(SUPPORTED_PYTHON))
        fail(f"{label} is Python {version[0]}.{version[1]}; Python {supported} is required")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--requirements",
        default="requirements.lock",
        help="requirements file to install (Windows uses requirements.windows.lock)",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    check_python((sys.version_info.major, sys.version_info.minor), "The selected interpreter")

    venv = root / ".venv"
    python = virtualenv_python(venv)
    if not python.exists():
        print(f"Creating virtual environment at {venv}")
        subprocess.check_call([sys.executable, "-m", "venv", str(venv)])
        python = virtualenv_python(venv)
    if not python.exists():
        fail(f"the virtual environment did not create {python}")

    version = subprocess.check_output(
        [str(python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
        text=True,
    ).strip()
    major, minor = (int(value) for value in version.split(".", 1))
    check_python((major, minor), "The existing virtual environment")

    requirements = root / args.requirements
    if not requirements.is_file():
        fail(f"requirements file not found: {requirements}")
    print(f"Installing dependencies from {requirements.relative_to(root)}")
    subprocess.check_call([str(python), "-m", "pip", "install", "-r", str(requirements)])

    env_file = root / ".env"
    if not env_file.exists():
        shutil.copyfile(root / ".env.example", env_file)
        if os.name != "nt":
            env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        print("Created .env from .env.example")
    else:
        print("Keeping existing .env")

    data_directory = root / ".data"
    data_directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        data_directory.chmod(stat.S_IRWXU)
    print("Native installation is ready.")
    print("Start it with the platform run script in scripts/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
