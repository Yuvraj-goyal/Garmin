"""Deriving YOUR zones from efforts you actually ran.

Nothing here uses 220-minus-age and nothing here trusts a number an app
assumed on your behalf. Every value carries the activity and date it came
from, plus the judgment applied to it, so you can argue with it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .util import (
    best_window_mean,
    scan_windows,
    fastest_time_for_distance,
    fmt_pace,
    mean,
    speed_to_pace_min_per_mile,
    celsius_to_fahrenheit,
)

# --- Models used, stated openly so they can be challenged ------------------

# Riegel endurance exponent. Converts an effort of any duration to the pace
# you could hold for 60 minutes, which is the working definition of threshold.
RIEGEL_EXPONENT = 1.06
PACE_EXPONENT = (RIEGEL_EXPONENT - 1) / RIEGEL_EXPONENT  # ~0.0566

# A maximal effort should finish near your ceiling. Below this fraction of max
# heart rate, it was not maximal and must not be treated as one.
MAXIMAL_EFFORT_HR_FRACTION = 0.92

# Heat cost, percent slower than the same effort run in cool conditions.
# Piecewise on air temperature; dew point is added on top when reported.
HEAT_PENALTY_TABLE = [
    (60.0, 0.0), (65.0, 1.0), (70.0, 2.0), (75.0, 3.0),
    (80.0, 4.5), (85.0, 6.0), (90.0, 8.0),
]

MIN_THRESHOLD_EFFORT_SECONDS = 1200.0  # 20 minutes


@dataclass
class EffortSource:
    activity_id: int
    name: str
    date: str
    value: float
    detail: str = ""


@dataclass
class MaxHeartRate:
    value: float
    instantaneous: float
    window_5s: float | None
    window_10s: float | None
    window_30s: float | None
    source: EffortSource
    spikes_removed_total: int
    basis: str = "highest clean 10-second sustained value after artifact filtering"


@dataclass
class ThresholdHeartRate:
    value: float
    is_rule_of_thumb: bool
    basis: str
    source: EffortSource | None
    percent_of_max: float


@dataclass
class JudgmentCheck:
    name: str
    finding: str
    conclusion: str
    adjustment_seconds_per_mile: float
    flag: bool


@dataclass
class ThresholdPace:
    value_min_per_mile: float
    raw_value_min_per_mile: float
    confidence: str
    source: EffortSource | None
    checks: list[JudgmentCheck] = field(default_factory=list)
    what_would_sharpen: str = ""
    is_rule_of_thumb: bool = False


# --- Max heart rate --------------------------------------------------------

def derive_max_heart_rate(activities: list[dict[str, Any]]) -> MaxHeartRate | None:
    """Highest CLEAN sustained heart rate across everything downloaded.

    Reported at several window lengths. The headline number is the best
    10-second sustained value: long enough that no artifact can survive it,
    short enough to still be a true ceiling rather than an average.
    """
    best: MaxHeartRate | None = None
    spikes_total = 0

    for item in activities:
        stream = item["stream"]
        spikes_total += stream.spikes_removed
        readings = [v for v in stream.heart_rate if v]
        if not readings:
            continue

        instantaneous = max(readings)
        w5, _ = best_window_mean(stream.time, stream.heart_rate, 5)
        w10, _ = best_window_mean(stream.time, stream.heart_rate, 10)
        w30, _ = best_window_mean(stream.time, stream.heart_rate, 30)
        headline = w10 or w5 or instantaneous

        if best is None or headline > best.value:
            best = MaxHeartRate(
                value=headline,
                instantaneous=instantaneous,
                window_5s=w5,
                window_10s=w10,
                window_30s=w30,
                source=EffortSource(
                    activity_id=item["id"],
                    name=item["name"],
                    date=item["date"],
                    value=headline,
                ),
                spikes_removed_total=0,
            )

    if best:
        best.spikes_removed_total = spikes_total
    return best


# --- Threshold heart rate --------------------------------------------------

# Threshold is a steady-state intensity. A window only counts as evidence if
# the pace inside it was actually held rather than surged.
STEADY_PACE_CV = 0.10

# Physiological guard. Lactate threshold sits well below maximum. A value
# above this fraction of max HR did not come from a threshold effort, it came
# from a short maximal one, and using it inflates every zone above Z2.
MAX_PLAUSIBLE_LTHR_FRACTION = 0.92
MIN_PLAUSIBLE_LTHR_FRACTION = 0.75


def derive_threshold_heart_rate(
    activities: list[dict[str, Any]],
    max_hr: MaxHeartRate,
) -> ThresholdHeartRate:
    """Threshold HR from your hardest sustained STEADY effort.

    Two things matter and only one of them is obvious. The effort has to be
    hard, but it also has to be steady: the highest 20 minutes of heart rate
    in your history usually sits inside a short race, and a short race is run
    ABOVE threshold. Taking it uncorrected inflates the number and every zone
    built on top of it. So we require the pace inside the window to have been
    genuinely held, and then sanity-check the answer against your max.
    """
    best_value: float | None = None
    best_source: EffortSource | None = None
    best_cv: float | None = None

    for item in activities:
        stream = item["stream"]
        if stream.duration_seconds < MIN_THRESHOLD_EFFORT_SECONDS:
            continue
        windows = scan_windows(
            stream.time,
            {"hr": stream.heart_rate, "speed": stream.speed},
            MIN_THRESHOLD_EFFORT_SECONDS,
            step_seconds=20.0,
        )
        for window in windows:
            if "hr_mean" not in window or "speed_cv" not in window:
                continue
            if window["speed_cv"] > STEADY_PACE_CV:
                continue  # surged, so not a steady-state effort
            if best_value is None or window["hr_mean"] > best_value:
                best_value = window["hr_mean"]
                best_cv = window["speed_cv"]
                best_source = EffortSource(
                    item["id"], item["name"], item["date"], window["hr_mean"],
                    detail=(
                        f"best steady 20-minute block "
                        f"(pace held to within {window['speed_cv'] * 100:.1f}%)"
                    ),
                )

    if best_value is None:
        fallback = max_hr.value * 0.88
        return ThresholdHeartRate(
            value=fallback,
            is_rule_of_thumb=True,
            basis=(
                "RULE OF THUMB (88% of max) -- no effort in this window held a "
                "steady hard pace for 20 minutes"
            ),
            source=None,
            percent_of_max=88.0,
        )

    fraction = best_value / max_hr.value

    if fraction > MAX_PLAUSIBLE_LTHR_FRACTION:
        capped = max_hr.value * MAX_PLAUSIBLE_LTHR_FRACTION
        return ThresholdHeartRate(
            value=capped,
            is_rule_of_thumb=True,
            basis=(
                f"CAPPED -- the best steady effort averaged {best_value:.0f} bpm, "
                f"which is {fraction * 100:.0f}% of your max. Lactate threshold "
                f"does not sit that close to maximum, so that effort was run "
                f"ABOVE threshold. Capped at {MAX_PLAUSIBLE_LTHR_FRACTION * 100:.0f}% "
                f"of max instead"
            ),
            source=best_source,
            percent_of_max=MAX_PLAUSIBLE_LTHR_FRACTION * 100.0,
        )

    if fraction < MIN_PLAUSIBLE_LTHR_FRACTION:
        fallback = max_hr.value * 0.88
        return ThresholdHeartRate(
            value=fallback,
            is_rule_of_thumb=True,
            basis=(
                f"RULE OF THUMB (88% of max) -- your hardest steady 20 minutes "
                f"only reached {fraction * 100:.0f}% of max, which means nothing "
                f"in this window was run hard enough to measure threshold from"
            ),
            source=best_source,
            percent_of_max=88.0,
        )

    return ThresholdHeartRate(
        value=best_value,
        is_rule_of_thumb=False,
        basis=(
            f"measured: hardest STEADY 20-minute block in your history, at "
            f"{fraction * 100:.0f}% of max heart rate"
        ),
        source=best_source,
        percent_of_max=fraction * 100.0,
    )


# --- Threshold pace, with judgment -----------------------------------------

MIN_DIRECT_EFFORT_SECONDS = 600.0   # 10 minutes held at threshold HR
LTHR_MATCH_BPM = 4.0

# Corrections are judgment, not measurement, so they are bounded.
MAX_TOTAL_ADJUSTMENT_FRACTION = 0.04
NEGATIVE_SPLIT_CREDIT_WEIGHT = 0.25
MAX_NEGATIVE_SPLIT_FRACTION = 0.02


def _weather_in_fahrenheit(
    weather: dict[str, Any],
    account_unit: str | None,
) -> tuple[float | None, float | None, str]:
    """Resolve Garmin's temperature into Fahrenheit, and say which unit it used.

    Garmin does not label the unit on the weather response; it follows the
    account's measurement system. We use that setting when we have it, fall
    back to a range test only when we do not, and always report which unit was
    assumed so a wrong guess is visible instead of silently skewing the heat
    correction.
    """
    temp = weather.get("temp")
    dew = weather.get("dewPoint")
    if temp is None:
        return None, None, "unknown"

    if account_unit in ("metric", "C"):
        unit = "Celsius"
    elif account_unit in ("statute_us", "statute_uk", "F"):
        unit = "Fahrenheit"
    else:
        # No account setting: only values impossible as Fahrenheit running
        # weather are treated as Celsius. The 20-45 range is ambiguous, so it
        # is left as Fahrenheit rather than guessed at.
        unit = "Celsius" if temp < 20 else "Fahrenheit"

    if unit == "Celsius":
        return celsius_to_fahrenheit(temp), celsius_to_fahrenheit(dew), unit
    return float(temp), (float(dew) if dew is not None else None), unit


def _heat_penalty_percent(temp_f: float | None, dew_point_f: float | None) -> float:
    if temp_f is None:
        return 0.0
    penalty = 0.0
    for threshold, value in HEAT_PENALTY_TABLE:
        if temp_f >= threshold:
            penalty = value
    # Humidity compounds heat; dew point above 60F is the usual trigger.
    if dew_point_f is not None and dew_point_f > 60:
        penalty += min((dew_point_f - 60) * 0.15, 3.0)
    return penalty


def _equivalent_60_minute_pace(pace: float, duration_seconds: float) -> float:
    """Riegel-normalise an effort of any length to a 60-minute pace."""
    if duration_seconds <= 0:
        return pace
    return pace * (3600.0 / duration_seconds) ** PACE_EXPONENT


def _collect_pace_efforts(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best sustained hard efforts across whatever distances exist."""
    candidates: list[dict[str, Any]] = []
    windows = [(1200.0, "20 minutes"), (1800.0, "30 minutes"), (2400.0, "40 minutes")]
    distances = [(5000.0, "5K"), (10000.0, "10K"), (16093.4, "10 miles"),
                 (21097.5, "half marathon")]

    for item in activities:
        stream = item["stream"]

        for seconds, label in windows:
            if stream.duration_seconds < seconds:
                continue
            speed, start = best_window_mean(stream.time, stream.speed, seconds)
            pace = speed_to_pace_min_per_mile(speed) if speed else None
            if pace:
                candidates.append({
                    "item": item, "pace": pace, "duration": seconds,
                    "label": f"best {label}", "start_index": start,
                })

        for metres, label in distances:
            elapsed, start = fastest_time_for_distance(
                stream.time, stream.distance, metres
            )
            if elapsed and elapsed > 300:
                miles = metres / 1609.344
                pace = (elapsed / 60.0) / miles
                candidates.append({
                    "item": item, "pace": pace, "duration": elapsed,
                    "label": f"fastest {label}", "start_index": start,
                })

    for candidate in candidates:
        candidate["equivalent"] = _equivalent_60_minute_pace(
            candidate["pace"], candidate["duration"]
        )
    return candidates


def _effort_average_hr(stream, start_index: int | None, duration: float) -> float | None:
    if start_index is None:
        return None
    end_time = stream.time[start_index] + duration
    values = [
        hr for t, hr in zip(stream.time[start_index:], stream.heart_rate[start_index:])
        if hr and t <= end_time
    ]
    return mean(values)


def _negative_split_percent(stream, start_index: int | None, duration: float) -> float | None:
    """Positive result means the second half was faster."""
    if start_index is None:
        return None
    start_time = stream.time[start_index]
    midpoint = start_time + duration / 2.0
    end_time = start_time + duration

    first, second = [], []
    for t, speed in zip(stream.time[start_index:], stream.speed[start_index:]):
        if speed is None or t > end_time:
            continue
        (first if t <= midpoint else second).append(speed)

    first_mean, second_mean = mean(first), mean(second)
    if not first_mean or not second_mean:
        return None
    return (second_mean - first_mean) / first_mean * 100.0


def _direct_threshold_pace(
    activities: list[dict[str, Any]],
    threshold_hr: float,
) -> tuple[float | None, EffortSource | None]:
    """Threshold pace measured directly: the pace you held AT threshold HR.

    This is independent of any race-equivalence model. If you held a steady
    ten minutes or more with your heart rate sitting on your threshold, the
    pace you ran is your threshold pace, by definition, with no maths in
    between. It is the strongest single piece of evidence available, and it is
    the one a race-time estimate most often contradicts.
    """
    best_pace: float | None = None
    best_source: EffortSource | None = None

    for item in activities:
        stream = item["stream"]
        if stream.duration_seconds < MIN_DIRECT_EFFORT_SECONDS:
            continue
        windows = scan_windows(
            stream.time,
            {"hr": stream.heart_rate, "speed": stream.speed},
            MIN_DIRECT_EFFORT_SECONDS,
            step_seconds=20.0,
        )
        for window in windows:
            if "hr_mean" not in window or "speed_mean" not in window:
                continue
            if window["speed_cv"] > STEADY_PACE_CV:
                continue
            if abs(window["hr_mean"] - threshold_hr) > LTHR_MATCH_BPM:
                continue
            pace = speed_to_pace_min_per_mile(window["speed_mean"])
            if pace and (best_pace is None or pace < best_pace):
                best_pace = pace
                best_source = EffortSource(
                    item["id"], item["name"], item["date"], pace,
                    detail=(
                        f"held {fmt_pace(pace)}/mi for 10+ steady minutes at "
                        f"{window['hr_mean']:.0f} bpm, your threshold heart rate"
                    ),
                )
    return best_pace, best_source


def derive_threshold_pace(
    activities: list[dict[str, Any]],
    max_hr: MaxHeartRate,
    threshold_hr: "ThresholdHeartRate | None" = None,
    temperature_unit: str | None = None,
) -> ThresholdPace:
    """Find the best sustained efforts, then apply judgment before committing."""
    candidates = _collect_pace_efforts(activities)

    if not candidates:
        return ThresholdPace(
            value_min_per_mile=0.0, raw_value_min_per_mile=0.0,
            confidence="none", source=None, is_rule_of_thumb=True,
            what_would_sharpen="No sustained effort of any length was found.",
        )

    best = min(candidates, key=lambda c: c["equivalent"])
    item = best["item"]
    stream = item["stream"]
    raw_equivalent = best["equivalent"]

    checks: list[JudgmentCheck] = []
    adjustment_total = 0.0

    # ---- Check 1: was it hot? --------------------------------------------
    weather = item.get("weather") or {}
    temp_f, dew_f, temp_unit = _weather_in_fahrenheit(weather, temperature_unit)
    heat_percent = _heat_penalty_percent(temp_f, dew_f)
    heat_adjustment = -(raw_equivalent * 60.0) * (heat_percent / 100.0)
    adjustment_total += heat_adjustment

    if temp_f is None:
        checks.append(JudgmentCheck(
            "Was it hot?", "No weather recorded for this effort.",
            "Cannot correct for heat. Treated as neutral.", 0.0, True))
    else:
        conditions = (f"{temp_f:.0f} F"
                      + (f", dew point {dew_f:.0f} F" if dew_f else "")
                      + f" (Garmin reported this in {temp_unit})")
        if heat_percent > 0:
            checks.append(JudgmentCheck(
                "Was it hot?", conditions,
                f"Heat costs about {heat_percent:.1f}% here, so the raw time "
                f"reads SLOWER than your actual fitness. Credited back "
                f"{abs(heat_adjustment):.0f} s/mi.",
                heat_adjustment, False))
        else:
            checks.append(JudgmentCheck(
                "Was it hot?", conditions,
                "Cool enough that heat cost nothing. No correction.", 0.0, False))

    # ---- Check 2: did you negative split it hard? ------------------------
    split = _negative_split_percent(stream, best.get("start_index"), best["duration"])
    if split is None:
        checks.append(JudgmentCheck(
            "Did you negative split it?", "Could not measure the two halves.",
            "No correction applied.", 0.0, True))
    elif split > 2.0:
        credit = -(raw_equivalent * 60.0) * min(
            split * NEGATIVE_SPLIT_CREDIT_WEIGHT / 100.0,
            MAX_NEGATIVE_SPLIT_FRACTION,
        )
        adjustment_total += credit
        checks.append(JudgmentCheck(
            "Did you negative split it?",
            f"Second half was {split:.1f}% faster than the first.",
            "A big negative split means you paced it conservatively and had "
            f"more left, so the raw time UNDERSTATES you. Credited back "
            f"{abs(credit):.0f} s/mi -- deliberately a fraction of the split, "
            f"because this is an inference about what you had left, not a "
            f"measurement of it.",
            credit, False))
    elif split < -4.0:
        checks.append(JudgmentCheck(
            "Did you negative split it?",
            f"Second half was {abs(split):.1f}% SLOWER than the first.",
            "You faded, which means the effort was genuinely maximal. The raw "
            "number is trustworthy here. No correction.", 0.0, False))
    else:
        checks.append(JudgmentCheck(
            "Did you negative split it?",
            f"Halves within {abs(split):.1f}% of each other.",
            "Evenly paced, which is the ideal case. No correction.", 0.0, False))

    # ---- Check 3: did your heart rate get near max? ----------------------
    effort_hr = _effort_average_hr(stream, best.get("start_index"), best["duration"])
    if effort_hr is None:
        checks.append(JudgmentCheck(
            "Did your heart rate get near max?",
            "No heart rate recorded for this effort.",
            "Cannot confirm this was maximal. Treat the result with caution.",
            0.0, True))
        hr_ok = False
    else:
        fraction = effort_hr / max_hr.value
        hr_ok = fraction >= MAXIMAL_EFFORT_HR_FRACTION
        if hr_ok:
            checks.append(JudgmentCheck(
                "Did your heart rate get near max?",
                f"Averaged {effort_hr:.0f} bpm, {fraction * 100:.0f}% of your "
                f"{max_hr.value:.0f} bpm max.",
                "That is a genuinely maximal effort. Safe to treat as one.",
                0.0, False))
        else:
            checks.append(JudgmentCheck(
                "Did your heart rate get near max?",
                f"Averaged {effort_hr:.0f} bpm, only {fraction * 100:.0f}% of "
                f"your {max_hr.value:.0f} bpm max.",
                "It finished well under your ceiling, so this was NOT a maximal "
                "effort and should not be treated as one. Your real threshold "
                "pace is very likely FASTER than this number.", 0.0, True))

    # ---- Bound the corrections -------------------------------------------
    cap = raw_equivalent * 60.0 * MAX_TOTAL_ADJUSTMENT_FRACTION
    if abs(adjustment_total) > cap:
        checks.append(JudgmentCheck(
            "Are the corrections getting out of hand?",
            f"The corrections above summed to {abs(adjustment_total):.0f} s/mi.",
            f"That is more than {MAX_TOTAL_ADJUSTMENT_FRACTION * 100:.0f}% of "
            f"the raw pace, which is more than judgment can honestly carry. "
            f"Capped at {cap:.0f} s/mi so a stack of estimates cannot outweigh "
            f"the measurement underneath them.",
            0.0, False))
        adjustment_total = cap if adjustment_total > 0 else -cap

    race_equivalent = raw_equivalent + adjustment_total / 60.0

    # ---- Check 4: does an independent method agree? ----------------------
    direct_pace: float | None = None
    direct_source: EffortSource | None = None
    # A capped threshold HR is still anchored to a real effort, so it can
    # still drive the cross-check. Only a pure fallback with no supporting
    # effort behind it cannot.
    if threshold_hr is not None and threshold_hr.source is not None:
        direct_pace, direct_source = _direct_threshold_pace(
            activities, threshold_hr.value
        )

    agreement_seconds: float | None = None
    if direct_pace:
        agreement_seconds = (direct_pace - race_equivalent) * 60.0
        agrees = abs(agreement_seconds) <= 10
        checks.append(JudgmentCheck(
            "Does a second, independent method agree?",
            f"Race-equivalence from your best effort says "
            f"{fmt_pace(race_equivalent)}/mi. Measuring the pace you actually "
            f"held at threshold heart rate says {fmt_pace(direct_pace)}/mi. "
            f"They differ by {abs(agreement_seconds):.0f} s/mi.",
            (
                "Two independent methods landing together is the strongest "
                "evidence available here."
                if agrees
                else "They disagree, so I am taking the SLOWER of the two. "
                     "Over-estimating threshold is the error that quietly turns "
                     "every easy run into a steady run, and that is the exact "
                     "failure this whole exercise exists to catch."
            ),
            0.0, abs(agreement_seconds) > 25,
        ))
        adjusted = max(race_equivalent, direct_pace)
    else:
        adjusted = race_equivalent
        if threshold_hr is not None and threshold_hr.source is not None:
            checks.append(JudgmentCheck(
                "Does a second, independent method agree?",
                "No steady 10-minute block at threshold heart rate was found "
                "anywhere in this window.",
                "Only one method had anything to work with, so there is nothing "
                "to cross-check against. That alone caps the confidence.",
                0.0, True))

    flags = sum(1 for c in checks if c.flag)
    long_enough = best["duration"] >= 900.0

    if (direct_pace and agreement_seconds is not None
            and abs(agreement_seconds) <= 10 and hr_ok and flags == 0):
        confidence = "high"
        sharpen = ("Two independent methods already agree. A 30-minute time "
                   "trial in cool conditions would only confirm it.")
    elif (direct_pace and abs(agreement_seconds or 0) <= 25) or (
            flags == 0 and hr_ok and long_enough):
        confidence = "medium"
        sharpen = ("One hard, evenly paced 20-30 minute effort in cool weather, "
                   "run alone rather than in a race, would move this to high "
                   "confidence.")
    else:
        confidence = "low"
        sharpen = ("The evidence here is thin or inconsistent. A 3200m time "
                   "trial or a 5K race -- run in the morning, not the afternoon "
                   "heat -- would replace this estimate with a measurement.")

    detail = (f"{best['label']} at {fmt_pace(best['pace'])}/mi over "
              f"{best['duration'] / 60:.0f} min")
    if direct_source:
        detail += f"; cross-checked against a block that {direct_source.detail}"

    return ThresholdPace(
        value_min_per_mile=adjusted,
        raw_value_min_per_mile=raw_equivalent,
        confidence=confidence,
        source=EffortSource(
            item["id"], item["name"], item["date"], adjusted, detail=detail,
        ),
        checks=checks,
        what_would_sharpen=sharpen,
        is_rule_of_thumb=(not hr_ok and direct_pace is None),
    )


# --- Zone tables -----------------------------------------------------------

HR_ZONE_MODEL = [
    ("Z1", "Recovery",   0.00, 0.85),
    ("Z2", "Easy",       0.85, 0.91),
    ("Z3", "Steady",     0.91, 0.96),
    ("Z4", "Threshold",  0.96, 1.02),
    ("Z5", "VO2 max",    1.02, 9.99),
]

# Multipliers on threshold PACE. A bigger multiplier is a slower pace.
PACE_ZONE_MODEL = [
    ("Z1", "Recovery",   1.40, 9.99),
    ("Z2", "Easy",       1.18, 1.40),
    ("Z3", "Marathon",   1.05, 1.18),
    ("Z4", "Threshold",  0.97, 1.05),
    ("Z5", "Interval",   0.00, 0.97),
]

EASY_CEILING_MULTIPLIER = 1.18


@dataclass
class Zone:
    key: str
    name: str
    low: float
    high: float
    label: str


def heart_rate_zones(threshold_hr: float) -> list[Zone]:
    zones = []
    for key, name, low, high in HR_ZONE_MODEL:
        low_bpm = threshold_hr * low
        high_bpm = threshold_hr * high
        label = (
            f"under {high_bpm:.0f}" if low == 0
            else f"{low_bpm:.0f}+" if high > 9
            else f"{low_bpm:.0f}-{high_bpm:.0f}"
        )
        zones.append(Zone(key, name, low_bpm, high_bpm, f"{label} bpm"))
    return zones


def pace_zones(threshold_pace: float) -> list[Zone]:
    """Zones in minutes per mile. `low` is the fast edge, `high` the slow edge."""
    zones = []
    for key, name, fast, slow in PACE_ZONE_MODEL:
        fast_pace = threshold_pace * fast
        slow_pace = threshold_pace * slow
        if fast == 0:
            label = f"faster than {fmt_pace(slow_pace)}"
        elif slow > 9:
            label = f"slower than {fmt_pace(fast_pace)}"
        else:
            label = f"{fmt_pace(fast_pace)}-{fmt_pace(slow_pace)}"
        zones.append(Zone(key, name, fast_pace, slow_pace, f"{label} /mi"))
    return zones


def easy_pace_ceiling(threshold_pace: float) -> float:
    """The fastest pace that still counts as easy. The number step 8 needs."""
    return threshold_pace * EASY_CEILING_MULTIPLIER


# --- Time in zone, from the per-second stream only -------------------------

def time_in_heart_rate_zones(stream, zones: list[Zone]) -> list[float]:
    """Seconds spent in each zone, computed sample by sample.

    Never computed from mile splits. A mile split during an interval session
    blends the reps with the recoveries, so any zone percentage built on
    splits is meaningless. This is the entire reason the detail stream exists.
    """
    totals = [0.0] * len(zones)
    for hr, dt in zip(stream.heart_rate, stream.sample_durations()):
        if hr is None or dt <= 0:
            continue
        for i, zone in enumerate(zones):
            if zone.low <= hr < zone.high:
                totals[i] += dt
                break
        else:
            totals[-1] += dt
    return totals


def time_in_pace_zones(stream, zones: list[Zone]) -> list[float]:
    """Seconds spent in each pace zone, sample by sample from the stream."""
    totals = [0.0] * len(zones)
    for pace, dt in zip(stream.pace_min_per_mile, stream.sample_durations()):
        if pace is None or dt <= 0 or pace > 30:
            continue
        for i, zone in enumerate(zones):
            if zone.low <= pace < zone.high:
                totals[i] += dt
                break
    return totals
