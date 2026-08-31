"""The per-second stream: column mapping, verification, and artifact removal.

Garmin does NOT return the detail stream as named fields. It returns:

    activityDetailMetrics : [ {"metrics": [v0, v1, v2, ...]}, ... ]
    metricDescriptors     : [ {"key": "directHeartRate", "metricsIndex": 7}, ... ]

The position of each metric inside those bare rows is not fixed and is not
documented. It varies by device, by firmware and by activity. Reading the
descriptors is the only correct way to know which column is which, so that is
what happens here -- and then the mapping is VERIFIED against the activity's
own summary before any number is drawn.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .util import mean, percent_difference, speed_to_pace_min_per_mile

# Descriptor keys we care about, in preference order. Garmin uses different
# keys on different devices, so each metric lists every spelling we accept.
WANTED = {
    "timestamp": ["directTimestamp"],
    "elapsed": ["sumElapsedDuration", "sumDuration"],
    "moving": ["sumMovingDuration"],
    "distance": ["sumDistance"],
    "heart_rate": ["directHeartRate"],
    "speed": ["directSpeed", "directWeightedMeanSpeed"],
    "elevation": ["directElevation", "directCorrectedElevation"],
    "cadence": ["directDoubleCadence", "directRunCadence", "directBikeCadence"],
    "power": ["directPower"],
}

# How far a single sample may jump from BOTH neighbours before we call it an
# optical-sensor artifact rather than a real heartbeat.
SPIKE_THRESHOLD_BPM = 15.0


class VerificationError(Exception):
    """Raised when the mapped columns disagree with the activity summary."""


@dataclass
class Check:
    name: str
    from_stream: float | None
    from_summary: float | None
    difference_percent: float | None
    tolerance_percent: float
    units: str
    passed: bool


@dataclass
class Stream:
    """One activity's per-second data, mapped and cleaned."""

    activity_id: int
    column_map: dict[str, int] = field(default_factory=dict)
    descriptor_keys: list[str] = field(default_factory=list)
    time: list[float] = field(default_factory=list)
    distance: list[float | None] = field(default_factory=list)
    heart_rate: list[float | None] = field(default_factory=list)
    heart_rate_raw: list[float | None] = field(default_factory=list)
    speed: list[float | None] = field(default_factory=list)
    elevation: list[float | None] = field(default_factory=list)
    elevation_smooth: list[float | None] = field(default_factory=list)
    cadence: list[float | None] = field(default_factory=list)
    spikes_removed: int = 0
    spike_samples: list[dict[str, float]] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    @property
    def verifiable_checks(self) -> list[Check]:
        """Checks that actually had a summary value to compare against."""
        return [c for c in self.checks if c.from_summary is not None]

    @property
    def is_verified(self) -> bool:
        """True only if at least one check ran AND every check that ran passed.

        An activity whose summary carried no comparable values is NOT verified.
        Treating 'nothing to check' as 'everything checks out' is how a broken
        column mapping slips through unnoticed.
        """
        ran = self.verifiable_checks
        return bool(ran) and all(c.passed for c in ran)

    @property
    def sample_count(self) -> int:
        return len(self.time)

    @property
    def duration_seconds(self) -> float:
        return (self.time[-1] - self.time[0]) if len(self.time) > 1 else 0.0

    @property
    def pace_min_per_mile(self) -> list[float | None]:
        return [speed_to_pace_min_per_mile(s) for s in self.speed]

    def sample_durations(self) -> list[float]:
        """Seconds each sample represents, used for all time-in-zone maths."""
        out: list[float] = []
        for i in range(len(self.time)):
            if i + 1 < len(self.time):
                dt = self.time[i + 1] - self.time[i]
            else:
                dt = out[-1] if out else 1.0
            out.append(dt if 0 < dt < 60 else 0.0)
        return out


def smooth(values: list[float | None], window: int = 15) -> list[float | None]:
    """Centred moving average, used to take sensor jitter out of elevation.

    A barometric altimeter wanders a metre or two every second. Summing the
    raw positive changes turns that wander into thousands of feet of climb
    that never happened, so the signal is smoothed before any gain is taken.
    """
    if window < 2:
        return list(values)
    n = len(values)
    out: list[float | None] = [None] * n
    half = window // 2
    for i in range(n):
        chunk = [
            v for v in values[max(0, i - half):min(n, i + half + 1)]
            if v is not None and math.isfinite(v)
        ]
        out[i] = sum(chunk) / len(chunk) if chunk else None
    return out


def elevation_gain_metres(
    elevations: list[float | None],
    minimum_step: float = 0.5,
) -> float:
    """Total climb, counted only from movement that clears the noise floor."""
    total = 0.0
    previous: float | None = None
    for value in elevations:
        if value is None:
            continue
        if previous is not None:
            change = value - previous
            if change > minimum_step:
                total += change
                previous = value
            elif change < -minimum_step:
                previous = value
        else:
            previous = value
    return total


def build_column_map(details: dict[str, Any]) -> tuple[dict[str, int], list[str]]:
    """Read metricDescriptors and return {our name -> column index}.

    This runs BEFORE any value is touched. Assuming the column order instead
    produces a chart that looks entirely convincing and is entirely wrong.
    """
    descriptors = details.get("metricDescriptors") or []
    if not descriptors:
        raise VerificationError(
            "This activity has no metricDescriptors, so the columns in "
            "activityDetailMetrics cannot be identified. Refusing to guess."
        )

    by_key: dict[str, int] = {}
    for descriptor in descriptors:
        key = descriptor.get("key")
        index = descriptor.get("metricsIndex")
        if key is not None and index is not None:
            by_key[key] = int(index)

    column_map: dict[str, int] = {}
    for our_name, candidates in WANTED.items():
        for candidate in candidates:
            if candidate in by_key:
                column_map[our_name] = by_key[candidate]
                break

    return column_map, sorted(by_key)


def _column(rows: list[list[Any]], index: int | None) -> list[float | None]:
    if index is None:
        return [None] * len(rows)
    out: list[float | None] = []
    for row in rows:
        if index < len(row):
            value = row[index]
            out.append(float(value) if isinstance(value, (int, float)) else None)
        else:
            out.append(None)
    return out


def filter_hr_spikes(
    heart_rate: list[float | None],
    times: list[float],
    threshold: float = SPIKE_THRESHOLD_BPM,
) -> tuple[list[float | None], int, list[dict[str, float]]]:
    """Remove single-sample optical-sensor artifacts.

    A wrist optical sensor intermittently locks onto cadence instead of pulse
    and throws a lone sample 20-40 bpm above anything real, most often in the
    first mile before the strap warms up and the signal settles.

    A sample is an artifact only when it jumps more than `threshold` from BOTH
    neighbours AND those neighbours agree with each other -- that is what
    "lands alone" means. A genuine surge carries its neighbours with it and is
    therefore left completely alone.
    """
    cleaned = list(heart_rate)
    removed: list[dict[str, float]] = []

    for i in range(1, len(cleaned) - 1):
        current = cleaned[i]
        previous = cleaned[i - 1]
        following = heart_rate[i + 1]
        if current is None or previous is None or following is None:
            continue

        jumps_from_both = (
            abs(current - previous) > threshold and abs(current - following) > threshold
        )
        neighbours_agree = abs(previous - following) <= threshold
        if jumps_from_both and neighbours_agree:
            removed.append(
                {
                    "index": float(i),
                    "time": times[i] if i < len(times) else 0.0,
                    "value": current,
                    "replaced_with": (previous + following) / 2.0,
                }
            )
            cleaned[i] = (previous + following) / 2.0

    return cleaned, len(removed), removed


def verify_against_summary(stream: Stream, summary: dict[str, Any]) -> list[Check]:
    """Prove the column mapping is right before trusting a single number.

    Take the heart-rate column we just mapped, average it, and compare it with
    the averageHR the activity reports in its own summary. Do the same for
    speed against averageSpeed, and for total distance. If the mapping were
    wrong -- if we had grabbed cadence and called it heart rate -- these would
    disagree loudly.
    """
    checks: list[Check] = []

    def add(name, from_stream, from_summary, tolerance, units):
        difference = percent_difference(from_stream, from_summary)
        passed = (
            from_stream is not None
            and from_summary is not None
            and difference is not None
            and abs(difference) <= tolerance
        )
        checks.append(
            Check(name, from_stream, from_summary, difference, tolerance, units, passed)
        )

    # Heart rate: mean of the RAW column, because averageHR in the summary is
    # itself computed from unfiltered data. Comparing filtered against
    # unfiltered would introduce a difference that is ours, not Garmin's.
    add(
        "Average heart rate",
        mean([v for v in stream.heart_rate_raw if v]),
        summary.get("averageHR"),
        6.0,
        "bpm",
    )

    # Speed: time-weighted so stops are not counted as though they were
    # equal-length samples.
    durations = stream.sample_durations()
    total_time = sum(
        d for d, s in zip(durations, stream.speed) if s is not None and d
    )
    weighted = sum(
        s * d for d, s in zip(durations, stream.speed) if s is not None and d
    )
    add(
        "Average speed",
        (weighted / total_time) if total_time else None,
        summary.get("averageSpeed"),
        10.0,
        "m/s",
    )

    # Distance: the single strongest check that the columns are what we think.
    distances = [d for d in stream.distance if d is not None]
    add(
        "Total distance",
        (max(distances) - min(distances)) if distances else None,
        summary.get("distance"),
        3.0,
        "m",
    )

    return checks


def load_stream(activity_id: int, details: dict[str, Any], summary: dict[str, Any]) -> Stream:
    """Full pipeline for one activity: map, extract, filter, verify."""
    column_map, descriptor_keys = build_column_map(details)

    raw_rows = details.get("activityDetailMetrics") or []
    rows = [row.get("metrics", []) for row in raw_rows]
    if not rows:
        raise VerificationError("This activity has no activityDetailMetrics rows.")

    stream = Stream(activity_id=activity_id, column_map=column_map,
                    descriptor_keys=descriptor_keys)

    # Prefer a real clock; fall back to elapsed seconds if the device omits it.
    if "timestamp" in column_map:
        stamps = _column(rows, column_map["timestamp"])
        base = next((s for s in stamps if s is not None), 0.0)
        stream.time = [((s - base) / 1000.0) if s is not None else 0.0 for s in stamps]
    elif "elapsed" in column_map:
        elapsed = _column(rows, column_map["elapsed"])
        stream.time = [e if e is not None else 0.0 for e in elapsed]
    else:
        stream.time = [float(i) for i in range(len(rows))]

    stream.distance = _column(rows, column_map.get("distance"))
    stream.speed = _column(rows, column_map.get("speed"))
    stream.elevation = _column(rows, column_map.get("elevation"))
    stream.elevation_smooth = smooth(stream.elevation)
    stream.cadence = _column(rows, column_map.get("cadence"))

    raw_hr = _column(rows, column_map.get("heart_rate"))
    # A zero heart rate is a dropout, not a reading.
    raw_hr = [v if (v and v > 20) else None for v in raw_hr]
    stream.heart_rate_raw = raw_hr
    stream.heart_rate, stream.spikes_removed, stream.spike_samples = filter_hr_spikes(
        raw_hr, stream.time
    )

    stream.checks = verify_against_summary(stream, summary)
    return stream
