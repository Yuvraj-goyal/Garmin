#!/usr/bin/env python3
"""One-command setup and launch. Run this; it does the rest.

Creates an isolated virtual environment beside this file, installs the
open-source python-garminconnect library into it, and starts the analysis.
Nothing is installed system-wide and nothing outside this folder is touched.
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
REQUIREMENTS = ["garminconnect>=0.2.25"]


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def main() -> int:
    if sys.version_info < (3, 9):
        print(f"Python 3.9 or newer is needed; this is {sys.version.split()[0]}.")
        print("Install a current Python from https://www.python.org/downloads/")
        return 1

    python = venv_python()

    if not python.exists():
        print("  Setting up an isolated environment (one time, ~30 seconds) ...")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)

    try:
        subprocess.run(
            [str(python), "-c", "import garminconnect"],
            check=True, capture_output=True,
        )
    except subprocess.CalledProcessError:
        print("  Installing the Garmin library (cyberjunky/python-garminconnect) ...")
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet",
             "--upgrade", "pip", *REQUIREMENTS],
        )
        if result.returncode != 0:
            print("\n  The install failed. If you are behind a proxy or offline,")
            print("  that is the usual cause. Nothing else has been changed.")
            return result.returncode
        print("  Installed.")

    return subprocess.run([str(python), str(HERE / "run.py"), *sys.argv[1:]]).returncode


if __name__ == "__main__":
    sys.exit(main())
