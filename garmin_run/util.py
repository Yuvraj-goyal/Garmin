"""Units, formatting and time-weighted window maths.

Every unit conversion in this project lives here, so there is exactly one
place where metres-per-second becomes minutes-per-mile.
"""

from __future__ import annotations

import math
from typing import Any

METRES_PER_MILE = 1609.344

# Garmin reports speed in METRES PER SECOND. Pace in minutes per mile is
# (metres per mile) / (metres per second) / (seconds per minute)
#   = 1609.344 / 60 / speed = 26.8224 / speed
SECONDS_PER_MINUTE = 60.0
PACE_CONSTANT_MIN_PER_MILE = METRES_PER_MILE / SECONDS_PER_MINUTE  # 26.8224


def speed_to_pace_min_per_mile(speed_m_per_s: float | None) -> float | None:
    """Convert m/s to minutes per mile. Returns None for a stop (speed <= 0)."""
    if speed_m_per_s is None or speed_m_per_s <= 0.05:
        return None
    return PACE_CONSTANT_MIN_PER_MILE / speed_m_per_s


def pace_min_per_mile_to_speed(pace_min_per_mile: float | None) -> float | None:
    """Inverse of the above, so the round trip is provably symmetric."""
    if not pace_min_per_mile or pace_min_per_mile <= 0:
        return None
    return PACE_CONSTANT_MIN_PER_MILE / pace_min_per_mile


def metres_to_miles(metres: float | None) -> float:
    return 0.0 if metres is None else metres / METRES_PER_MILE


def metres_to_feet(metres: float | None) -> float:
    return 0.0 if metres is None else metres * 3.280839895


def celsius_to_fahrenheit(celsius: float | None) -> float | None:
    return None if celsius is None else celsius * 9.0 / 5.0 + 32.0


def fmt_pace(pace_min_per_mile: float | None) -> str:
    """Format a decimal minutes-per-mile value as m:ss."""
    if pace_min_per_mile is None or not math.isfinite(pace_min_per_mile):
        return "--:--"
    total_seconds = int(round(pace_min_per_mile * 60))
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def fmt_pace_delta(seconds: float | None) -> str:
    """Format a per-mile pace difference in signed seconds."""
    if seconds is None:
        return "--"
    sign = "+" if seconds >= 0 else "-"
    return f"{sign}{abs(seconds):.0f} s/mi"


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "--"
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def fmt_clock(seconds: float | None) -> str:
    """Duration for a zone bar: 1h 04m, or 7m 12s when under an hour."""
    if seconds is None or seconds <= 0:
        return "0m"
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    return sum(clean) / len(clean) if clean else None


def percent_difference(a: float | None, b: float | None) -> float | None:
    """Percentage difference of a from b, used for the verification step."""
    if a is None or b is None or b == 0:
        return None
    return (a - b) / b * 100.0


def best_window_mean(
    times: list[float],
    values: list[float | None],
    window_seconds: float,
) -> tuple[float | None, int | None]:
    """Highest time-weighted mean of `values` over any window of the given length.

    Returns (best_mean, start_index). Samples are irregular in a Garmin stream,
    so each sample is weighted by the time it actually represents rather than
    being treated as one equal tick.
    """
    n = len(times)
    if n < 2 or window_seconds <= 0:
        return None, None

    # Time-weighted prefix sums, skipping gaps where the value is missing.
    weighted = [0.0] * (n + 1)
    weights = [0.0] * (n + 1)
    for i in range(n):
        dt = (times[i + 1] - times[i]) if i + 1 < n else 0.0
        dt = dt if 0 < dt < 60 else 0.0  # ignore pauses longer than a minute
        value = values[i]
        if value is not None and math.isfinite(value):
            weighted[i + 1] = weighted[i] + value * dt
            weights[i + 1] = weights[i] + dt
        else:
            weighted[i + 1] = weighted[i]
            weights[i + 1] = weights[i]

    best: float | None = None
    best_start: int | None = None
    end = 0
    for start in range(n):
        if end < start:
            end = start
        while end < n - 1 and times[end] - times[start] < window_seconds:
            end += 1
        if times[end] - times[start] < window_seconds * 0.97:
            break  # ran out of activity before filling the window
        covered = weights[end + 1] - weights[start]
        if covered < window_seconds * 0.80:
            continue  # too much missing data inside this window to trust it
        window_mean = (weighted[end + 1] - weighted[start]) / covered
        if best is None or window_mean > best:
            best = window_mean
            best_start = start
    return best, best_start


def fastest_time_for_distance(
    times: list[float],
    cumulative_distance: list[float | None],
    target_metres: float,
) -> tuple[float | None, int | None]:
    """Quickest elapsed time to cover `target_metres` anywhere in the activity.

    This is a rolling best effort, not a split, so it finds the real 5k inside
    a longer run rather than whatever happened to fall between mile markers.
    """
    points = [
        (t, d)
        for t, d in zip(times, cumulative_distance)
        if d is not None and math.isfinite(d)
    ]
    if len(points) < 2 or points[-1][1] - points[0][1] < target_metres:
        return None, None

    best: float | None = None
    best_start: int | None = None
    start = 0
    for end in range(len(points)):
        while points[end][1] - points[start][1] >= target_metres:
            elapsed = points[end][0] - points[start][0]
            if elapsed > 0 and (best is None or elapsed < best):
                best = elapsed
                best_start = start
            start += 1
    return best, best_start


def scan_windows(
    times: list[float],
    series: dict[str, list[float | None]],
    window_seconds: float,
    step_seconds: float = 15.0,
) -> list[dict[str, Any]]:
    """Mean and coefficient of variation for every window, in linear time.

    Threshold is a STEADY-STATE concept, so we need to know not just how hard
    a window was but how evenly it was run. Prefix sums of both the values and
    their squares give the mean and the spread in O(1) per window.
    """
    n = len(times)
    if n < 2:
        return []

    durations = []
    for i in range(n):
        dt = (times[i + 1] - times[i]) if i + 1 < n else 0.0
        durations.append(dt if 0 < dt < 60 else 0.0)

    prefixes: dict[str, tuple[list[float], list[float], list[float]]] = {}
    for name, values in series.items():
        total = [0.0] * (n + 1)
        squares = [0.0] * (n + 1)
        weight = [0.0] * (n + 1)
        for i in range(n):
            value, dt = values[i], durations[i]
            if value is not None and math.isfinite(value) and dt:
                total[i + 1] = total[i] + value * dt
                squares[i + 1] = squares[i] + value * value * dt
                weight[i + 1] = weight[i] + dt
            else:
                total[i + 1], squares[i + 1], weight[i + 1] = total[i], squares[i], weight[i]
        prefixes[name] = (total, squares, weight)

    results: list[dict[str, Any]] = []
    end = 0
    last_start_time = -1e9
    for start in range(n):
        if times[start] - last_start_time < step_seconds:
            continue
        if end < start:
            end = start
        while end < n - 1 and times[end] - times[start] < window_seconds:
            end += 1
        if times[end] - times[start] < window_seconds * 0.97:
            break
        last_start_time = times[start]

        entry: dict[str, Any] = {"start_index": start, "start_time": times[start]}
        usable = True
        for name, (total, squares, weight) in prefixes.items():
            covered = weight[end + 1] - weight[start]
            if covered < window_seconds * 0.80:
                usable = False
                break
            window_mean = (total[end + 1] - total[start]) / covered
            variance = max(
                (squares[end + 1] - squares[start]) / covered - window_mean ** 2, 0.0
            )
            entry[f"{name}_mean"] = window_mean
            entry[f"{name}_cv"] = (
                math.sqrt(variance) / window_mean if window_mean else 1.0
            )
        if usable:
            results.append(entry)
    return results
