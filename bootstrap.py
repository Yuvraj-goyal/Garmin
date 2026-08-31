#!/usr/bin/env python3
"""One-command setup and launch. Run this; it does the rest.

Finds a suitable Python on this machine, creates an isolated virtual
environment beside this file, installs the open-source python-garminconnect
library into it, and starts the analysis. Nothing is installed system-wide and
nothing outside this folder is touched.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"

# Pinned to the version this project was written and tested against.
# It needs Python 3.10 or newer; every release before it is from 2023 and is
# missing the endpoints used to read Garmin's own zone basis.
REQUIREMENT = "garminconnect==0.3.2"
MINIMUM_PYTHON = (3, 10)


def _version_of(executable: str) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            [executable, "-c", "import sys;print('%d %d' % sys.version_info[:2])"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            major, minor = result.stdout.split()
            return int(major), int(minor)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def find_python() -> tuple[str, tuple[int, int]] | None:
    """Newest Python on this machine that meets the minimum.

    macOS in particular usually has several: the one Apple ships, one from
    python.org, and one from Homebrew. The `python3` on PATH is often the
    oldest of them, so we look properly rather than trusting it.
    """
    candidates: list[str] = [sys.executable]

    names = [f"python3.{minor}" for minor in range(20, MINIMUM_PYTHON[1] - 1, -1)]
    names.append("python3")
    for name in names:
        found = shutil.which(name)
        if found:
            candidates.append(found)

    search_dirs = [
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/opt/local/bin",
        str(Path.home() / ".pyenv" / "shims"),
    ]
    for directory in search_dirs:
        for name in names:
            path = os.path.join(directory, name)
            if os.path.exists(path):
                candidates.append(path)

    for pattern in (
        "/Library/Frameworks/Python.framework/Versions/*/bin/python3",
        "/opt/homebrew/opt/python@3.*/bin/python3*",
        str(Path.home() / ".pyenv/versions/*/bin/python3"),
    ):
        candidates.extend(glob.glob(pattern))

    best: tuple[str, tuple[int, int]] | None = None
    seen: set[str] = set()
    for candidate in candidates:
        try:
            real = os.path.realpath(candidate)
        except OSError:
            continue
        if real in seen:
            continue
        seen.add(real)
        version = _version_of(candidate)
        if version and version >= MINIMUM_PYTHON:
            if best is None or version > best[1]:
                best = (candidate, version)
    return best


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def explain_no_python() -> None:
    current = ".".join(str(part) for part in sys.version_info[:3])
    needed = ".".join(str(part) for part in MINIMUM_PYTHON)
    print()
    print(f"  This machine's Python is {current}, and this needs {needed} or newer.")
    print()
    print("  That is why the install failed. The Garmin library dropped support")
    print("  for older Pythons, so pip could only offer you a 2023 release that")
    print("  is missing the endpoints this tool reads.")
    print()
    print("  I looked for a newer Python already installed here and did not find")
    print("  one. Installing a current Python is a one-time, five-minute job:")
    print()
    if sys.platform == "darwin":
        print("    If you have Homebrew:")
        print("      brew install python@3.12")
        print()
        print("    If not, download the macOS installer and run it:")
        print("      https://www.python.org/downloads/macos/")
    elif os.name == "nt":
        print("      winget install Python.Python.3.12")
        print("      (or https://www.python.org/downloads/windows/)")
    else:
        print("      sudo apt install python3.12 python3.12-venv")
        print("      (or your distribution's equivalent)")
    print()
    print("  Then run this exact same command again. Nothing else changes, and")
    print("  nothing on this machine has been modified so far.")
    print()


def main() -> int:
    python = venv_python()

    # An environment built by a too-old Python has to be rebuilt, not reused.
    existing = _version_of(str(python)) if python.exists() else None
    if existing and existing < MINIMUM_PYTHON:
        print(f"  The existing environment uses Python {existing[0]}.{existing[1]}, "
              f"which is too old. Rebuilding it.")
        shutil.rmtree(VENV, ignore_errors=True)

    if not venv_python().exists():
        chosen = find_python()
        if chosen is None:
            explain_no_python()
            return 1

        executable, version = chosen
        if sys.version_info[:2] < MINIMUM_PYTHON:
            print(f"  The 'python3' you ran is "
                  f"{sys.version_info[0]}.{sys.version_info[1]}, which is older "
                  f"than the {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} this needs.")
            print(f"  Found Python {version[0]}.{version[1]} at {executable} "
                  f"and using that instead.")
        elif version != sys.version_info[:2]:
            print(f"  Using Python {version[0]}.{version[1]} from {executable}.")

        print("  Setting up an isolated environment (one time, ~30 seconds) ...")
        shutil.rmtree(VENV, ignore_errors=True)
        result = subprocess.run(
            [executable, "-m", "venv", str(VENV)], capture_output=True, text=True
        )
        if result.returncode != 0:
            print("  Could not create the environment:")
            print("   ", (result.stderr or result.stdout).strip()[:400])
            if sys.platform.startswith("linux"):
                print("  On Debian or Ubuntu you may need: sudo apt install python3-venv")
            return result.returncode

    python = venv_python()

    try:
        subprocess.run(
            [str(python), "-c", "import garminconnect"], check=True, capture_output=True
        )
    except subprocess.CalledProcessError:
        print("  Installing the Garmin library "
              "(cyberjunky/python-garminconnect) ...")
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
            capture_output=True,
        )
        result = subprocess.run([str(python), "-m", "pip", "install", "--quiet",
                                 REQUIREMENT])
        if result.returncode != 0:
            print()
            print("  The install failed. If you are behind a proxy or offline, that")
            print("  is the usual cause. Nothing else has been changed.")
            return result.returncode
        print("  Installed.")

    return subprocess.run([str(python), str(HERE / "run.py"), *sys.argv[1:]]).returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  Stopped. Nothing was changed.")
        sys.exit(130)
