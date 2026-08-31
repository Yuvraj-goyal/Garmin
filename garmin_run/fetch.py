"""Login and read-only download of your own Garmin data.

Two rules are enforced here rather than merely intended:

1. READ-ONLY. Every call goes through `_readonly`, which refuses any method
   whose name is not on the allowlist below. Nothing is ever written back to
   your Garmin account.
2. Your password is never stored. It is read with getpass (not echoed), passed
   straight to the library, and dropped. It is never written to a file, never
   put in an environment variable, and never printed.
"""

from __future__ import annotations

import datetime as dt
import getpass
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from garminconnect import Garmin

# Only these methods may ever be called. Anything that creates, updates,
# uploads or deletes is absent by construction.
READ_ONLY_METHODS = {
    "get_activities_by_date",
    "get_activity",
    "get_activity_splits",
    "get_activity_details",
    "get_activity_weather",
    "get_activity_hr_in_timezones",
    "get_lactate_threshold",
    "get_race_predictions",
    "get_user_profile",
    "get_userprofile_settings",
    "get_max_metrics",
    "connectapi",
}

RUN_TYPES = {
    "running",
    "trail_running",
    "track_running",
    "treadmill_running",
    "road_running",
    "ultra_run",
    "virtual_run",
    "obstacle_run",
}

# Convenience groups for --include. Anything not listed here can still be named
# directly by its Garmin type key, which --list-types will show you.
ACTIVITY_GROUPS = {
    "running": RUN_TYPES,
    "cycling": {
        "cycling", "road_biking", "mountain_biking", "gravel_cycling",
        "indoor_cycling", "virtual_ride", "cyclocross", "e_bike_fitness",
    },
    "walking": {"walking", "casual_walking", "speed_walking", "hiking"},
    "swimming": {"lap_swimming", "open_water_swimming", "swimming"},
    "strength": {"strength_training", "indoor_cardio", "fitness_equipment"},
}


def activity_type_key(activity: dict[str, Any]) -> str:
    """The sport key, from whichever shape this activity arrived in."""
    for field_name in ("activityType", "activityTypeDTO"):
        holder = activity.get(field_name) or {}
        if isinstance(holder, dict) and holder.get("typeKey"):
            return str(holder["typeKey"])
    return ""


def resolve_include(spec: str) -> set[str] | None:
    """Turn an --include value into a set of type keys. None means everything."""
    spec = (spec or "running").strip().lower()
    if spec in ("all", "everything", "*"):
        return None

    wanted: set[str] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if part in ACTIVITY_GROUPS:
            wanted |= ACTIVITY_GROUPS[part]
        elif part in ("run", "runs"):
            wanted |= RUN_TYPES
        elif part in ("bike", "ride", "rides"):
            wanted |= ACTIVITY_GROUPS["cycling"]
        elif part in ("walk", "walks", "hike", "hikes"):
            wanted |= ACTIVITY_GROUPS["walking"]
        elif part in ("swim", "swims"):
            wanted |= ACTIVITY_GROUPS["swimming"]
        else:
            wanted.add(part)  # a raw Garmin type key
    return wanted


class ReadOnlyGarmin:
    """Thin guard around the Garmin client that can only read."""

    def __init__(self, client: Garmin) -> None:
        self._client = client
        self.calls = 0

    def _readonly(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if method not in READ_ONLY_METHODS:
            raise PermissionError(
                f"Refusing to call '{method}': not on the read-only allowlist. "
                "This tool never writes to your Garmin account."
            )
        self.calls += 1
        # Be a polite API citizen; Garmin rate-limits aggressively.
        time.sleep(0.6)
        return getattr(self._client, method)(*args, **kwargs)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            return self._readonly(name, *args, **kwargs)

        return call


def _token_dir() -> Path:
    path = Path.home() / ".garminconnect"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def login(echo: Callable[[str], None]) -> ReadOnlyGarmin:
    """Log in, reusing a saved session token when one exists.

    The token lives in ~/.garminconnect on this machine with owner-only
    permissions. It is a session token, not your password; your password is
    never persisted anywhere.
    """
    tokenstore = str(_token_dir())

    # Try the saved session first so you are not asked to log in twice.
    try:
        client = Garmin()
        client.login(tokenstore)
        name = (client.get_full_name() or "").strip()
        echo(f"  Reused the saved session on this machine{' for ' + name if name else ''}.")
        return ReadOnlyGarmin(client)
    except Exception:
        pass

    echo("")
    echo("  Garmin needs you to sign in. This prompt is running on YOUR")
    echo("  computer. What you type is sent straight to Garmin over HTTPS and")
    echo("  is never saved, never logged, and never shown on screen.")
    echo("")

    try:
        email = input("  Garmin email: ").strip()
        password = getpass.getpass("  Garmin password (hidden, nothing will appear): ")
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\n  Cancelled. Nothing was sent and nothing was saved.")
    if not email or not password:
        raise SystemExit("  No credentials entered. Stopping without contacting Garmin.")

    client = Garmin(email=email, password=password, return_on_mfa=True)
    result = client.login()
    # Drop the password reference the moment the library is done with it.
    password = ""
    del password

    needs_mfa = None
    client_state = None
    if isinstance(result, tuple) and len(result) == 2:
        needs_mfa, client_state = result

    if needs_mfa == "needs_mfa":
        echo("")
        echo("  Garmin sent you a 2FA code (check your email or authenticator app).")
        try:
            code = input("  6-digit code: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("\n  Cancelled at the 2FA step. Nothing was saved.")
        client.resume_login(client_state, code)

    client.client.dump(tokenstore)
    os.chmod(tokenstore, 0o700)
    echo(f"  Signed in. Session token saved to {tokenstore} (this machine only).")
    return ReadOnlyGarmin(client)


class Cache:
    """Everything downloaded is cached on disk so re-runs cost no API calls."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def get_or_fetch(self, key: str, producer: Callable[[], Any]) -> Any:
        path = self.root / f"{key}.json"
        if path.exists():
            try:
                self.hits += 1
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                path.unlink(missing_ok=True)
        self.misses += 1
        value = producer()
        path.write_text(json.dumps(value))
        return value


def is_run(activity: dict[str, Any]) -> bool:
    return activity_type_key(activity) in RUN_TYPES


def fetch_activities(
    api: ReadOnlyGarmin,
    cache: Cache,
    days: int,
    echo: Callable[[str], None],
    include: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The activity list for the last `days` days, filtered to `include`.

    Returns the selected activities and a census of every type found, so you
    can see what else is in there that you did not ask for.
    """
    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    echo(f"  Asking Garmin for activities from {start} to {end} ...")

    # The activity LIST is deliberately never served from cache. It is one
    # API call, and caching it under a key ending in today's date means a
    # re-run later the same day cannot see a workout synced an hour ago --
    # which is exactly when you re-run. The per-activity downloads below are
    # the expensive part and they stay cached.
    list_key = cache.root / f"activities_{start}_{end}.json"
    try:
        activities = api.get_activities_by_date(start.isoformat(), end.isoformat())
        list_key.write_text(json.dumps(activities))
    except Exception as exc:
        if list_key.exists():
            echo(f"  Could not reach Garmin ({str(exc)[:50]}); using the last")
            echo(f"  downloaded list instead.")
            activities = json.loads(list_key.read_text())
        else:
            raise

    census: dict[str, int] = {}
    for activity in activities:
        key = activity_type_key(activity) or "(unknown)"
        census[key] = census.get(key, 0) + 1

    if include is None:
        selected = list(activities)
    else:
        selected = [a for a in activities if activity_type_key(a) in include]

    echo(f"  {len(activities)} activities in the window, "
         f"{len(selected)} of them selected.")
    return selected, census


def fetch_activity_bundle(
    api: ReadOnlyGarmin,
    cache: Cache,
    activity_id: int | str,
) -> dict[str, Any]:
    """Summary, splits, per-second detail and weather for one activity."""

    def _safe(label: str, producer: Callable[[], Any]) -> Any:
        try:
            return cache.get_or_fetch(f"{label}_{activity_id}", producer)
        except Exception as exc:  # a single missing piece must not kill the run
            print(f"    (could not fetch {label} for {activity_id}: {exc})", file=sys.stderr)
            return None

    return {
        "summary": _safe("summary", lambda: api.get_activity(activity_id)),
        "splits": _safe("splits", lambda: api.get_activity_splits(activity_id)),
        "details": _safe(
            "details",
            lambda: api.get_activity_details(activity_id, maxchart=20000, maxpoly=0),
        ),
        "weather": _safe("weather", lambda: api.get_activity_weather(activity_id)),
    }


# Garmin returns the same activity in two different shapes. The list endpoint
# gives flat keys; the single-activity endpoint nests the same values under
# summaryDTO. Reading the wrong one yields None rather than an error, which is
# how a page ends up reporting 0.00 miles for a run that clearly happened.
SUMMARY_FIELDS = (
    "distance", "duration", "movingDuration", "elapsedDuration",
    "averageHR", "maxHR", "averageSpeed", "maxSpeed", "averageRunCadence",
    "elevationGain", "elevationLoss", "calories",
    "startTimeLocal", "startTimeGMT",
)


def normalize_summary(
    detail: dict[str, Any] | None,
    list_entry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flatten either shape into one dict with predictable keys.

    Values are taken from the nested summaryDTO first, then the top level of
    the detailed activity, then the activity-list entry, so whichever endpoint
    actually carried the number is the one that wins.
    """
    detail = detail or {}
    list_entry = list_entry or {}
    nested = detail.get("summaryDTO") or {}

    merged: dict[str, Any] = dict(list_entry)
    merged.update({k: v for k, v in detail.items() if k != "summaryDTO"})
    for field_name in SUMMARY_FIELDS:
        for source in (nested, detail, list_entry):
            value = source.get(field_name)
            if value is not None:
                merged[field_name] = value
                break

    if not merged.get("activityName"):
        merged["activityName"] = list_entry.get("activityName") or "Run"

    type_dto = detail.get("activityTypeDTO") or detail.get("activityType") or {}
    if type_dto.get("typeKey"):
        merged["activityType"] = {"typeKey": type_dto["typeKey"]}
    elif list_entry.get("activityType"):
        merged["activityType"] = list_entry["activityType"]

    return merged
