#!/usr/bin/env python3
"""Install, inspect or remove a scheduled automatic refresh.

    python3 schedule.py install          # every 4 hours (default)
    python3 schedule.py install --every 6
    python3 schedule.py install --at 06:00
    python3 schedule.py status
    python3 schedule.py uninstall

Deliberately written for old Pythons too, so the system interpreter that
launchd hands us can always run it.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABEL = "com.garminrunanalysis.refresh"
PLIST = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
LOGS = HERE / "logs"
LOG_FILE = LOGS / "refresh.log"
LAST_RUN = LOGS / "last-run.txt"
WRAPPER = LOGS / "refresh.sh"


def write_wrapper(extra_args: list[str]) -> Path:
    """A tiny shell wrapper so the exit code survives for `status` to read.

    launchd records nothing useful about why a job failed, so the wrapper
    records it: the time it started, the time it finished, and the exit code.
    Without this a job that quietly stops working looks exactly like a job
    that has nothing to do.
    """
    LOGS.mkdir(parents=True, exist_ok=True)
    quoted = " ".join(f"'{a}'" for a in extra_args)
    WRAPPER.write_text(
        "#!/bin/sh\n"
        f"cd '{HERE}' || exit 1\n"
        f"echo \"--- started $(date) ---\" >> '{LOG_FILE}'\n"
        f"/usr/bin/env python3 '{HERE / 'bootstrap.py'}' {quoted} "
        f">> '{LOG_FILE}' 2>&1\n"
        "code=$?\n"
        f"echo \"finished $(date), exit $code\" >> '{LOG_FILE}'\n"
        f"printf '%s\\nexit=%s\\n' \"$(date)\" \"$code\" > '{LAST_RUN}'\n"
        "exit $code\n"
    )
    WRAPPER.chmod(0o755)
    return WRAPPER


def build_plist(every_hours: float | None, at_time: str | None) -> dict:
    job: dict = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", str(WRAPPER)],
        "WorkingDirectory": str(HERE),
        "StandardOutPath": str(LOG_FILE),
        "StandardErrorPath": str(LOG_FILE),
        # Catch up shortly after login if a scheduled run was missed.
        "RunAtLoad": True,
    }
    if at_time:
        hour, _, minute = at_time.partition(":")
        job["StartCalendarInterval"] = {
            "Hour": int(hour), "Minute": int(minute or 0)
        }
    else:
        job["StartInterval"] = int((every_hours or 4) * 3600)
    return job


def launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(args: argparse.Namespace) -> int:
    extra = [a for a in (args.run_args or []) if a != "--"]
    if not extra:
        extra = ["--include", "all", "--no-open"]
    elif "--no-open" not in extra:
        # A scheduled job must never try to open a browser.
        extra.append("--no-open")

    if sys.platform != "darwin":
        print("  Automatic scheduling here is macOS only (launchd).")
        print("  On Linux, add this line with `crontab -e` instead:")
        print(f"    0 */4 * * * /bin/sh {write_wrapper(extra)}")
        return 1

    write_wrapper(extra)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    with open(PLIST, "wb") as handle:
        plistlib.dump(build_plist(args.every, args.at), handle)

    # Remove any previous version before loading the new one.
    launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    launchctl("unload", str(PLIST))
    result = launchctl("bootstrap", f"gui/{os.getuid()}", str(PLIST))
    if result.returncode != 0:
        result = launchctl("load", "-w", str(PLIST))
    if result.returncode != 0:
        print("  Could not register the job with launchd:")
        print("   ", (result.stderr or result.stdout).strip()[:300])
        return result.returncode

    when = (f"every {args.every or 4:g} hours" if not args.at
            else f"daily at {args.at}")
    print(f"  Scheduled: refresh {when}.")
    print(f"  It runs:   python3 bootstrap.py {' '.join(extra)}")
    print(f"  Log:       {LOG_FILE}")
    print("")
    print("  It also runs once now, and catches up after your Mac wakes if a")
    print("  scheduled time was missed while it was asleep.")
    print("")
    print("  Check on it any time with:   python3 schedule.py status")
    print("  Remove it completely with:   python3 schedule.py uninstall")
    return 0


def status(_: argparse.Namespace) -> int:
    print(f"  Job:  {LABEL}")
    print(f"  Plist installed: {'yes' if PLIST.exists() else 'no'} ({PLIST})")

    if sys.platform == "darwin":
        listing = launchctl("list")
        registered = LABEL in (listing.stdout or "")
        print(f"  Registered with launchd: {'yes' if registered else 'no'}")

    if LAST_RUN.exists():
        print("")
        print("  Last run:")
        for line in LAST_RUN.read_text().strip().splitlines():
            print(f"    {line}")
        if "exit=0" not in LAST_RUN.read_text():
            print("")
            print("  THE LAST RUN FAILED. The usual cause is that Garmin ended")
            print("  the saved session and wants a fresh login, which a")
            print("  background job cannot answer because of the 2FA prompt.")
            print("  Fix it by running it once by hand:")
            print("    cd ~/garmin-run-analysis && python3 bootstrap.py --include all")
    else:
        print("  It has not run yet.")

    if LOG_FILE.exists():
        lines = LOG_FILE.read_text().strip().splitlines()
        print("")
        print(f"  Last few lines of {LOG_FILE.name}:")
        for line in lines[-12:]:
            print(f"    {line}")
    return 0


def uninstall(_: argparse.Namespace) -> int:
    if sys.platform == "darwin":
        launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
        launchctl("unload", str(PLIST))
    removed = False
    for path in (PLIST, WRAPPER):
        if path.exists():
            path.unlink()
            removed = True
    print("  Removed." if removed else "  Nothing was installed.")
    print("  Your data, cache and pages are untouched.")
    return 0


def shortcut(args: argparse.Namespace) -> int:
    """Put a link to the page on the Desktop.

    A symlink, not a copy: the tool overwrites the same file in place every
    refresh, so a link always opens the current page while a copy would
    freeze whatever was there the day you made it.
    """
    page = HERE / "out" / "training.html"
    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        print(f"  No Desktop folder found at {desktop}.")
        print(f"  Open the page directly instead: {page}")
        return 1

    link = desktop / (args.name or "Training.html")
    if link.exists() and not link.is_symlink():
        print(f"  {link} already exists and is a real file, not a link.")
        print("  Refusing to overwrite it. Pass a different --name.")
        return 1
    if link.is_symlink():
        link.unlink()
    link.symlink_to(page)

    print(f"  Added: {link}")
    print(f"  It points at {page}")
    print("")
    if not page.exists():
        print("  That page does not exist yet, so the link will not open until")
        print("  the tool has run once. That is expected if you have just")
        print("  installed it.")
    else:
        print("  Double-click it any time. Because it is a link rather than a")
        print("  copy, it always opens the latest refresh, and the line under")
        print("  the title tells you how old that refresh is.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("install", help="install the scheduled refresh")
    add.add_argument("--every", type=float, default=None,
                     help="hours between refreshes (default 4)")
    add.add_argument("--at", default=None,
                     help="a daily time instead, e.g. 06:00")
    add.add_argument("run_args", nargs=argparse.REMAINDER,
                     help="arguments passed to bootstrap.py")
    add.set_defaults(func=install)

    sub.add_parser("status", help="is it installed, and did it work?").set_defaults(
        func=status)
    sub.add_parser("uninstall", help="remove it entirely").set_defaults(
        func=uninstall)

    link = sub.add_parser("shortcut", help="put a link to the page on the Desktop")
    link.add_argument("--name", default=None,
                      help="filename for the link (default Training.html)")
    link.set_defaults(func=shortcut)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
