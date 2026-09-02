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


def run_now(args: argparse.Namespace) -> int:
    """Refresh immediately, in this terminal, with output you can see.

    Deliberately not routed through the launchd wrapper. This one keeps your
    terminal attached, so if Garmin has ended the session you can actually
    answer the login and 2FA prompt -- which is the whole reason a scheduled
    run fails and the one thing a background job can never do.
    """
    extra = [a for a in (args.run_args or []) if a != "--"] or ["--include", "all"]
    command = [sys.executable, str(HERE / "bootstrap.py")] + extra
    print(f"  Running: {' '.join(command[1:])}")
    print("")
    return subprocess.run(command).returncode


def restart(_: argparse.Namespace) -> int:
    """Stop and start the scheduled job. Only useful if it seems stuck."""
    if sys.platform != "darwin":
        print("  There is no scheduled job to restart on this platform.")
        return 1
    if not PLIST.exists():
        print("  Nothing is scheduled, so there is nothing to restart.")
        print("  Install it with:  python3 schedule.py install")
        return 1

    target = f"gui/{os.getuid()}/{LABEL}"
    result = launchctl("kickstart", "-k", target)
    if result.returncode != 0:
        launchctl("bootout", target)
        result = launchctl("bootstrap", f"gui/{os.getuid()}", str(PLIST))
    if result.returncode != 0:
        print("  Could not restart it:")
        print("   ", (result.stderr or result.stdout).strip()[:300])
        print("  Reinstalling usually clears this:")
        print("    python3 schedule.py install")
        return result.returncode

    print("  Restarted. It is refreshing now; give it a minute, then:")
    print("    python3 schedule.py status")
    return 0


ICLOUD = (Path.home() / "Library" / "Mobile Documents" /
          "com~apple~CloudDocs" / "Training")


def phone(args: argparse.Namespace) -> int:
    """Sync the page into iCloud Drive so it appears on your phone.

    A copy rather than a link: iCloud does not follow symlinks out of its
    own folder, so a link here would sync a broken pointer.
    """
    folder = Path(args.folder).expanduser() if args.folder else ICLOUD
    if not args.folder and not ICLOUD.parent.is_dir():
        print("  iCloud Drive does not appear to be set up on this Mac.")
        print(f"  Expected it at: {ICLOUD.parent}")
        print("")
        print("  Either turn on iCloud Drive in System Settings, or point this")
        print("  at a Dropbox or Google Drive folder instead:")
        print("    python3 schedule.py phone --folder ~/Dropbox/Training")
        print("")
        print("  Or serve it over your wifi instead, which needs no cloud at all:")
        print("    python3 schedule.py serve")
        return 1

    folder.mkdir(parents=True, exist_ok=True)
    extra = ["--include", "all", "--no-open", "--copy-to", str(folder)]

    if sys.platform == "darwin" and PLIST.exists():
        args.every, args.at, args.run_args = None, None, extra
        install(args)
        print("")
    else:
        write_wrapper(extra)
        print(f"  Set up to copy into: {folder}")
        print("  (No schedule is installed yet. Add one with: "
              "python3 schedule.py install)")
        print("")

    page = HERE / "out" / "training.html"
    if page.exists():
        (folder / "training.html").write_text(
            page.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  Copied the current page into {folder} now.")
    else:
        print("  No page exists yet. Run this first: python3 schedule.py run")

    print("")
    print("  ON YOUR PHONE")
    print("    1. Open the Files app")
    print(f"    2. iCloud Drive -> {folder.name} -> training.html"
          if folder == ICLOUD else f"    2. Open {folder.name} -> training.html")
    print("    3. Tap it. Press and hold, then Share -> add it to your Home")
    print("       Screen or Favourites so it is one tap from then on.")
    print("")
    print("  It updates by itself every time the scheduled refresh runs.")
    print("  The page works without JavaScript, so it reads correctly in the")
    print("  Files preview as one long scrolling page.")
    return 0


def _lan_addresses() -> list[str]:
    """Every IPv4 address this machine actually has, best candidate first.

    Guessing one address by probing a hard-coded router IP breaks the moment
    the network is not on that subnet, and can hand back a VPN or virtual
    interface instead. Listing them all lets you try the one that works
    rather than trusting a guess.
    """
    import re
    import socket

    found: list[str] = []

    def add(address: str) -> None:
        if (address and address not in found
                and not address.startswith("127.")
                and not address.startswith("169.254.")):
            found.append(address)

    # macOS names its wifi and ethernet interfaces predictably; ask directly.
    if sys.platform == "darwin":
        for interface in ("en0", "en1", "en2", "en3"):
            result = subprocess.run(["ipconfig", "getifaddr", interface],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                add(result.stdout.strip())

    # Then whatever the routing table picks for the outside world.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 9))   # no packet is sent
        add(probe.getsockname()[0])
    except OSError:
        pass
    finally:
        probe.close()

    # Finally anything else configured, so nothing is missed.
    for command in (["ifconfig"], ["ip", "-4", "addr"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True)
        except FileNotFoundError:
            continue
        if result.returncode == 0:
            for match in re.findall(r"inet\s+(\d+\.\d+\.\d+\.\d+)",
                                    result.stdout):
                add(match)
            break

    return found


def _firewall_warning() -> list[str]:
    """macOS blocks incoming connections when its firewall is on."""
    if sys.platform != "darwin":
        return []
    tool = "/usr/libexec/ApplicationFirewall/socketfilterfw"
    if not Path(tool).exists():
        return []
    result = subprocess.run([tool, "--getglobalstate"],
                            capture_output=True, text=True)
    text = (result.stdout or "").lower()
    if "enabled" not in text or "state = 0" in text:
        return []
    return [
        "  macOS's firewall is ON, which blocks incoming connections and is",
        "  the usual reason a phone cannot reach this. Either allow Python:",
        "",
        f"    sudo {tool} --add {sys.executable}",
        f"    sudo {tool} --unblockapp {sys.executable}",
        "",
        "  or skip the network entirely and use iCloud instead:",
        "",
        "    python3 schedule.py phone",
        "",
    ]


def serve(args: argparse.Namespace) -> int:
    """Serve the page to any device on your wifi, in a real browser."""
    import functools
    import http.server

    directory = HERE / "out"
    if not (directory / "training.html").exists():
        print("  No page to serve yet. Run this first:")
        print("    python3 schedule.py run")
        return 1

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            # Without this a phone caches the page and keeps showing the old
            # one after a refresh, which is the whole problem we are solving.
            self.send_header("Cache-Control", "no-store, must-revalidate")
            super().end_headers()

        def log_message(self, *a):  # keep the terminal readable
            pass

        def handle_one_request(self):
            # A phone that navigates away mid-transfer resets the connection.
            # That is normal and must not print a stack trace at someone.
            try:
                super().handle_one_request()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True

    class Server(http.server.ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass  # a dropped connection is not an error worth printing

    handler = functools.partial(Handler, directory=str(directory))
    try:
        server = Server(("0.0.0.0", args.port), handler)
    except OSError as exc:
        print(f"  Could not open port {args.port}: {exc}")
        print(f"  Something else is probably using it. Try another:")
        print(f"    python3 schedule.py serve --port {args.port + 1}")
        return 1

    # Prove the server answers before claiming it is reachable.
    import threading
    import urllib.request
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{args.port}/training.html", timeout=5) as reply:
            # Read the body fully; closing early resets the connection and
            # makes the server log an error about our own health check.
            ok = reply.status == 200 and len(reply.read()) > 0
    except Exception:
        ok = False

    addresses = _lan_addresses()
    print("")
    if not ok:
        print("  The server started but did not answer its own request, which")
        print("  means something is wrong locally rather than on your phone.")
        server.shutdown()
        return 1

    print(f"  Serving {directory} on port {args.port}. Verified working.")
    print("")
    if addresses:
        print("  OPEN ONE OF THESE ON YOUR PHONE (same wifi):")
        print("")
        for address in addresses:
            print(f"      http://{address}:{args.port}/training.html")
        if len(addresses) > 1:
            print("")
            print("  More than one address exists because this Mac has several")
            print("  network interfaces. The first is usually wifi; if it does")
            print("  not load, try the next.")
    else:
        print("  No network address was found, so this Mac may be offline.")

    for line in _firewall_warning():
        print(line)

    print("")
    print("  If the phone still cannot connect, check in this order:")
    print("    1. Phone on the SAME wifi, not cellular and not a guest network")
    print("    2. macOS firewall set to allow Python, and any VPN off")
    print("    3. Router 'client isolation' or 'AP isolation' turned off")
    print("")
    print("  If none of that works, iCloud needs no network at all:")
    print("    python3 schedule.py phone")
    print("")
    print("  Leave this running while you use it. Press Ctrl-C to stop.")
    try:
        while True:
            __import__("time").sleep(3600)
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        server.shutdown()
        server.server_close()
    return 0


class Parser(argparse.ArgumentParser):
    """Argparse, but an unknown command says the likely reason.

    A command that exists upstream and not here means this checkout is
    behind. The bare "invalid choice" leaves you comparing your list against
    instructions written for a newer version, which is not a puzzle worth
    handing anyone.
    """

    def error(self, message: str) -> "NoReturn":  # type: ignore[override]
        if "invalid choice" in message:
            sys.stderr.write(f"\n  {message}\n\n")
            sys.stderr.write(
                "  If you were told to run a command that is not in that list,\n"
                "  this copy is probably out of date. Update and retry in one go:\n\n"
                f"    cd '{HERE}' && git pull && python3 schedule.py --help\n\n"
            )
            raise SystemExit(2)
        super().error(message)


def main() -> int:
    parser = Parser(description=__doc__)
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

    now = sub.add_parser("run", help="refresh right now, in this terminal")
    now.add_argument("run_args", nargs=argparse.REMAINDER,
                     help="arguments passed to bootstrap.py")
    now.set_defaults(func=run_now)

    sub.add_parser("restart", help="restart the scheduled job if it seems stuck"
                   ).set_defaults(func=restart)

    ph = sub.add_parser("phone", help="sync the page to your phone via iCloud")
    ph.add_argument("--folder", default=None,
                    help="use this folder instead of iCloud Drive")
    ph.set_defaults(func=phone, every=None, at=None, run_args=None)

    sv = sub.add_parser("serve", help="serve the page to your phone over wifi")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(func=serve)

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
