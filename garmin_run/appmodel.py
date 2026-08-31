"""What zone model Garmin Connect is actually grading you against.

Garmin does print its own basis, but never on the screen where it grades you.
It lives in three places, all read-only:

    latestLactateThreshold  -> the threshold HR and threshold SPEED it assumes
    race predictor          -> the 5K/10K time it thinks you can run
    user settings           -> the max heart rate on file
    biometric heartRateZones-> the zone table it actually applies

We read all four, then put its numbers next to the derived ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .util import (
    fmt_duration,
    fmt_pace,
    speed_to_pace_min_per_mile,
    METRES_PER_MILE,
)
from .zones import EASY_CEILING_MULTIPLIER, easy_pace_ceiling


@dataclass
class AppModel:
    max_hr: float | None = None
    threshold_hr: float | None = None
    threshold_speed: float | None = None
    threshold_pace: float | None = None
    resting_hr: float | None = None
    vo2max: float | None = None
    predicted_5k_seconds: float | None = None
    predicted_10k_seconds: float | None = None
    zone_table: list[dict[str, Any]] = field(default_factory=list)
    basis_statements: list[str] = field(default_factory=list)
    sources_found: list[str] = field(default_factory=list)
    sources_missing: list[str] = field(default_factory=list)


def _first_number(payload: Any, *keys: str) -> float | None:
    """Pull the first present numeric key out of a dict or list-of-dicts."""
    candidates = payload if isinstance(payload, list) else [payload]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in keys:
            value = candidate.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
    return None


def read_app_model(api, cache, echo) -> AppModel:
    """Read Garmin's own zone basis. Every call here is a GET."""
    model = AppModel()

    def probe(label: str, producer):
        try:
            value = cache.get_or_fetch(f"appmodel_{label}", producer)
            if value:
                model.sources_found.append(label)
                return value
            model.sources_missing.append(label)
        except Exception as exc:
            echo(f"    (Garmin did not return {label}: {str(exc)[:60]})")
            model.sources_missing.append(label)
        return None

    lactate = probe("lactate_threshold", lambda: api.get_lactate_threshold(latest=True))
    if lactate:
        model.threshold_hr = _first_number(
            lactate, "heartRate", "lactateThresholdHeartRate", "value"
        )
        model.threshold_speed = _first_number(
            lactate, "speed", "lactateThresholdSpeed"
        )
        if model.threshold_speed:
            model.threshold_pace = speed_to_pace_min_per_mile(model.threshold_speed)

    settings = probe("user_settings", lambda: api.get_userprofile_settings())
    if settings:
        user_data = settings.get("userData", settings) if isinstance(settings, dict) else {}
        model.max_hr = _first_number(user_data, "maxHeartRate", "maxHr")
        model.resting_hr = _first_number(user_data, "restingHeartRate", "restingHr")
        model.vo2max = _first_number(user_data, "vo2MaxRunning", "vo2MaxCycling")
        if model.threshold_hr is None:
            model.threshold_hr = _first_number(
                user_data, "lactateThresholdHeartRate", "lactateThresholdHr"
            )

    predictions = probe("race_predictions", lambda: api.get_race_predictions())
    if predictions:
        model.predicted_5k_seconds = _first_number(predictions, "time5K")
        model.predicted_10k_seconds = _first_number(predictions, "time10K")

    zones = probe(
        "heart_rate_zones",
        lambda: api.connectapi("/biometric-service/heartRateZones"),
    )
    if zones:
        model.zone_table = zones if isinstance(zones, list) else [zones]

    # Assemble the basis in Garmin's own language.
    if model.max_hr:
        model.basis_statements.append(
            f"based on your max heart rate of {model.max_hr:.0f} bpm"
        )
    if model.threshold_hr:
        model.basis_statements.append(
            f"based on a lactate threshold heart rate of {model.threshold_hr:.0f} bpm"
        )
    if model.threshold_pace:
        model.basis_statements.append(
            f"based on a threshold pace of {fmt_pace(model.threshold_pace)}/mi"
        )
    if model.predicted_5k_seconds:
        model.basis_statements.append(
            f"based on a 5K race time of {fmt_duration(model.predicted_5k_seconds)}"
        )
    return model


@dataclass
class Finding:
    kind: str          # "agree" | "differ" | "warn"
    headline: str
    detail: str


def compare(
    app: AppModel,
    derived_max_hr,
    derived_threshold_hr,
    derived_threshold_pace,
    actual_best_5k_seconds: float | None,
) -> tuple[list[Finding], float | None]:
    """Plain-language comparison. Returns (findings, easy-ceiling gap in s/mi)."""
    findings: list[Finding] = []

    # --- Max heart rate ---------------------------------------------------
    if app.max_hr and derived_max_hr:
        difference = app.max_hr - derived_max_hr.value
        if abs(difference) <= 3:
            findings.append(Finding(
                "agree",
                f"Max heart rate agrees. Garmin has {app.max_hr:.0f}, the evidence "
                f"says {derived_max_hr.value:.0f}.",
                "This one is right, and it is worth saying so. Your max HR is the "
                "foundation of every heart-rate zone, and Garmin has it correct.",
            ))
        else:
            direction = "HIGHER" if difference > 0 else "LOWER"
            findings.append(Finding(
                "differ",
                f"Max heart rate is off by {abs(difference):.0f} bpm. Garmin has "
                f"{app.max_hr:.0f}, the evidence says {derived_max_hr.value:.0f}.",
                f"Garmin's number is {abs(difference):.0f} bpm {direction} than "
                f"anything you have actually produced. Every heart-rate zone "
                f"boundary is a percentage of this, so all five zones are shifted "
                f"by roughly {abs(difference) * 0.9:.0f} bpm in the same direction.",
            ))

    # --- Threshold heart rate --------------------------------------------
    if app.threshold_hr and derived_threshold_hr:
        difference = app.threshold_hr - derived_threshold_hr.value
        if abs(difference) <= 4:
            findings.append(Finding(
                "agree",
                f"Threshold heart rate agrees, within {abs(difference):.0f} bpm "
                f"({app.threshold_hr:.0f} vs {derived_threshold_hr.value:.0f}).",
                "Garmin's auto-detected threshold matches what your hardest "
                "sustained efforts actually show.",
            ))
        else:
            findings.append(Finding(
                "differ",
                f"Threshold heart rate differs by {abs(difference):.0f} bpm "
                f"({app.threshold_hr:.0f} vs {derived_threshold_hr.value:.0f}).",
                "Threshold HR sets the Z3/Z4 boundary. If Garmin's is high, runs "
                "you feel as tempo get logged as steady, and the hard days look "
                "easier on paper than they were in your legs.",
            ))

    # --- Threshold pace and the easy band ---------------------------------
    easy_gap_seconds: float | None = None
    if app.threshold_pace and derived_threshold_pace and derived_threshold_pace.value_min_per_mile:
        app_easy = easy_pace_ceiling(app.threshold_pace)
        our_easy = easy_pace_ceiling(derived_threshold_pace.value_min_per_mile)
        easy_gap_seconds = (our_easy - app_easy) * 60.0

        difference_seconds = (
            app.threshold_pace - derived_threshold_pace.value_min_per_mile
        ) * 60.0

        if abs(difference_seconds) <= 8:
            findings.append(Finding(
                "agree",
                f"Threshold pace agrees, within {abs(difference_seconds):.0f} s/mi "
                f"({fmt_pace(app.threshold_pace)} vs "
                f"{fmt_pace(derived_threshold_pace.value_min_per_mile)}).",
                "Your pace zones are built on solid ground.",
            ))
        else:
            assumes = "FASTER" if difference_seconds < 0 else "SLOWER"
            findings.append(Finding(
                "differ",
                f"Garmin assumes you are {assumes} than the evidence says, by "
                f"{abs(difference_seconds):.0f} s/mi at threshold "
                f"({fmt_pace(app.threshold_pace)} vs "
                f"{fmt_pace(derived_threshold_pace.value_min_per_mile)}).",
                (
                    "Every pace zone is a multiple of threshold pace, so this "
                    f"error propagates into all five of them. Because Garmin "
                    f"thinks you are {assumes.lower()}, it sets every boundary "
                    f"{assumes.lower()} too -- including the easy ceiling, which "
                    "is the one you run against most days of the week."
                ),
            ))

        findings.append(Finding(
            "differ" if abs(easy_gap_seconds) > 8 else "agree",
            f"The easy band: Garmin caps easy at {fmt_pace(app_easy)}/mi. "
            f"The evidence caps it at {fmt_pace(our_easy)}/mi.",
            (
                f"This is the band that changes how you train every single day. "
                f"Garmin's ceiling is {abs(easy_gap_seconds):.0f} s/mi "
                f"{'FASTER' if easy_gap_seconds > 0 else 'SLOWER'} than the "
                f"evidence supports. "
                + (
                    "Running easy days at Garmin's ceiling means your easy runs "
                    "are not easy -- they are steady, they carry fatigue into "
                    "your hard days, and the hard days get worse."
                    if easy_gap_seconds > 0
                    else "Garmin is holding your easy days back further than "
                    "needed, which costs aerobic volume rather than recovery."
                )
            ),
        ))

    # --- A race time you have never actually run --------------------------
    if app.predicted_5k_seconds:
        predicted = app.predicted_5k_seconds
        if actual_best_5k_seconds is None:
            findings.append(Finding(
                "warn",
                f"Garmin's basis includes a 5K race time of "
                f"{fmt_duration(predicted)} -- a race time you have never "
                f"actually run.",
                "There is no 5K in your downloaded history at all. That number "
                "is a model's projection from your training, not a result. Every "
                "zone that resolves from it inherits the projection's error.",
            ))
        else:
            gap = actual_best_5k_seconds - predicted
            if gap > 25:
                findings.append(Finding(
                    "warn",
                    f"Garmin's basis is a 5K race time of {fmt_duration(predicted)} "
                    f"-- a race time you have never actually run.",
                    f"Your fastest real 5K in this window is "
                    f"{fmt_duration(actual_best_5k_seconds)}. Garmin's basis is "
                    f"{fmt_duration(gap)} faster than anything you have actually "
                    f"run. It is grading you against a performance that exists "
                    f"only inside its own model.",
                ))
            else:
                findings.append(Finding(
                    "agree",
                    f"Garmin predicts a 5K of {fmt_duration(predicted)}, and you "
                    f"have actually run {fmt_duration(actual_best_5k_seconds)}.",
                    "Its prediction is backed by something you really did, which "
                    "is the case where trusting it is reasonable.",
                ))

    return findings, easy_gap_seconds
