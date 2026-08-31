#!/usr/bin/env python3
"""Run analysis from your own Garmin watch data.

Everything happens on this machine. Nothing is uploaded. Nothing is written
back to your Garmin account. Your password is never stored.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from garmin_run import appmodel, fetch, report, streams, zones
from garmin_run.util import (
    fmt_duration,
    metres_to_miles,
    fmt_pace,
    metres_to_feet,
    speed_to_pace_min_per_mile,
    mean,
)

HERE = Path(__file__).resolve().parent
RULE = "-" * 68


def echo(text: str = "") -> None:
    print(text, flush=True)


def step(number: str, title: str) -> None:
    echo("")
    echo(RULE)
    echo(f"STEP {number}: {title}")
    echo(RULE)


def zone_for_pace(pace: float | None, pace_zone_list) -> str:
    if pace is None:
        return "Z2"
    for zone in pace_zone_list:
        if zone.low <= pace < zone.high:
            return zone.key
    return "Z1"


def mile_splits(stream, pace_zone_list) -> list[dict]:
    """Mile splits computed from the per-second stream."""
    elevations = stream.elevation_smooth or stream.elevation
    points = [
        (t, d, hr, e)
        for t, d, hr, e in zip(
            stream.time, stream.distance, stream.heart_rate, elevations
        )
        if d is not None
    ]
    if len(points) < 2:
        return []

    METRE_MILE = 1609.344
    out: list[dict] = []
    start = points[0]
    mile_index = 1
    heart_rates: list[float] = []
    segment_elevations: list[float | None] = []

    for t, d, hr, elevation in points:
        if hr:
            heart_rates.append(hr)
        segment_elevations.append(elevation)

        if d - start[1] >= METRE_MILE:
            elapsed = t - start[0]
            pace = elapsed / 60.0
            out.append({
                "label": f"{mile_index}",
                "pace": pace,
                "hr": mean(heart_rates),
                "elevation_gain": metres_to_feet(
                    streams.elevation_gain_metres(segment_elevations)
                ),
                "mph": 60.0 / pace if pace else None,
                "zone": zone_for_pace(pace, pace_zone_list),
            })
            start = (t, start[1] + METRE_MILE, hr, elevation)
            mile_index += 1
            heart_rates, segment_elevations = [], []

    # The final partial mile, labelled honestly.
    remaining = points[-1][1] - start[1]
    if remaining > 80:
        elapsed = points[-1][0] - start[0]
        miles = remaining / METRE_MILE
        pace = (elapsed / 60.0) / miles
        out.append({
            "label": f"{mile_index} ({miles:.2f} mi)",
            "pace": pace,
            "hr": mean(heart_rates),
            "elevation_gain": metres_to_feet(
                streams.elevation_gain_metres(segment_elevations)
            ),
            "mph": 60.0 / pace if pace else None,
            "zone": zone_for_pace(pace, pace_zone_list),
        })
    return out


def lap_chart_data(splits_payload, pace_zone_list, fallback) -> list[dict]:
    """Prefer the watch's own laps for the lap chart; fall back to mile splits."""
    laps = (splits_payload or {}).get("lapDTOs") or []
    if len(laps) < 2:
        return fallback

    out = []
    for i, lap in enumerate(laps, start=1):
        pace = speed_to_pace_min_per_mile(lap.get("averageSpeed"))
        out.append({
            "label": str(i),
            "pace": pace,
            "mph": (60.0 / pace) if pace else None,
            "hr": lap.get("averageHR"),
            "elevation_gain": metres_to_feet(lap.get("elevationGain")),
            "zone": zone_for_pace(pace, pace_zone_list),
        })
    return out


def quality_score(stream, threshold_pace: float) -> float:
    """Seconds spent at or faster than marathon pace. Higher means harder."""
    hard_cutoff = threshold_pace * 1.10
    total = 0.0
    for pace, duration in zip(stream.pace_min_per_mile, stream.sample_durations()):
        if pace is not None and pace <= hard_cutoff:
            total += duration
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=180,
                        help="how many days of activities to pull (default 180)")
    parser.add_argument("--out", default=str(HERE / "out"),
                        help="where to write the HTML page")
    parser.add_argument("--no-open", action="store_true",
                        help="do not open a browser at the end")
    parser.add_argument(
        "--include", default="running",
        help="which activities to analyse: 'running' (default), 'all', or a "
             "comma list of sports or raw Garmin type keys, e.g. "
             "'running,cycling' or 'running,strength_training'")
    parser.add_argument(
        "--list-types", action="store_true",
        help="list every activity type in your history with counts, then stop")
    parser.add_argument(
        "--feature", default=None,
        help="activity id to put on the page, instead of picking automatically")
    args = parser.parse_args()

    include = fetch.resolve_include(args.include)

    echo("")
    echo("  RUN ANALYSIS FROM YOUR OWN WATCH DATA")
    echo("  Read-only. Nothing is uploaded. Nothing is written to Garmin.")

    cache = fetch.Cache(HERE / "cache")

    # ---------------------------------------------------------------- login
    step("2", "Signing in to Garmin (read-only)")
    api = fetch.login(echo)

    # ------------------------------------------------------------ activities
    step("2b", f"Downloading {args.days} days of activities")
    runs, census = fetch.fetch_activities(api, cache, args.days, echo, include)

    if args.list_types or not runs:
        echo("")
        echo("  Everything in your history for this window:")
        for key, count in sorted(census.items(), key=lambda kv: -kv[1]):
            mark = ""
            if include is None or key in include:
                mark = "  <- included"
            echo(f"    {count:>4}  {key}{mark}")
        echo("")
        echo("  To include more, re-run with --include. For example:")
        echo("    python3 bootstrap.py --include all")
        echo("    python3 bootstrap.py --include running,cycling")
        echo("    python3 bootstrap.py --include running,strength_training")
    if args.list_types:
        return 0
    if not runs:
        echo("  Nothing matched what you asked to include, so there is nothing")
        echo("  to analyse. Pick a type from the list above, or use a longer")
        echo("  --days window.")
        return 1

    measurement_system = None
    try:
        settings = api.get_userprofile_settings()
        measurement_system = (settings or {}).get("measurementSystem")
    except Exception:
        pass

    echo(f"  Pulling the lap breakdown and per-second detail for each run.")
    echo(f"  This is {len(runs)} runs, so it takes a couple of minutes the first")
    echo(f"  time. Everything is cached, so a second run is instant.")

    activities: list[dict] = []
    failures: list[tuple[str, str]] = []

    for index, run in enumerate(runs, start=1):
        activity_id = run.get("activityId")
        name = run.get("activityName") or "Run"
        date = (run.get("startTimeLocal") or "")[:10]
        print(f"\r  [{index}/{len(runs)}] {date} {name[:34]:<34}", end="", flush=True)

        bundle = fetch.fetch_activity_bundle(api, cache, activity_id)
        summary = fetch.normalize_summary(bundle["summary"], run)
        details = bundle["details"]
        if not details:
            failures.append((f"{date} {name}", "no detail stream returned"))
            continue

        try:
            stream = streams.load_stream(activity_id, details, summary)
        except streams.VerificationError as exc:
            failures.append((f"{date} {name}", str(exc)))
            continue

        activities.append({
            "id": activity_id, "name": name, "date": date,
            "summary": summary, "raw": run, "details": details,
            "splits": bundle["splits"], "weather": bundle["weather"],
            "stream": stream,
        })

    print("\r" + " " * 72 + "\r", end="")
    echo(f"  Downloaded and mapped {len(activities)} runs.")
    if failures:
        echo(f"  {len(failures)} could not be used:")
        for label, reason in failures[:5]:
            echo(f"    - {label}: {reason[:60]}")

    if not activities:
        echo("  Nothing usable came back. Stopping rather than drawing a chart.")
        return 1

    # ------------------------------------------------- step 3: verification
    step("3", "Verifying the column mapping before trusting any number")
    echo("  The per-second stream arrives as bare arrays of numbers. The column")
    echo("  order is not fixed and not documented, so metricDescriptors was read")
    echo("  first and the mapping built from it. Now we prove it is right.")
    echo("")

    sample = activities[0]
    echo(f"  Descriptor keys this watch reports:")
    echo(f"    {', '.join(sample['stream'].descriptor_keys)}")
    echo("")
    echo(f"  Mapping resolved for {sample['date']} {sample['name']}:")
    for key, index in sorted(sample["stream"].column_map.items(), key=lambda kv: kv[1]):
        echo(f"    column {index:>2}  ->  {key}")
    echo("")

    passed_all = 0
    unverifiable = 0
    failed_checks: list[str] = []
    not_recorded: dict[str, int] = {}
    for item in activities:
        stream = item["stream"]
        for check in stream.checks:
            if check.status == "not recorded in the stream":
                not_recorded[check.name] = not_recorded.get(check.name, 0) + 1
        if stream.is_verified:
            passed_all += 1
        elif not stream.confirmations:
            unverifiable += 1
        for check in stream.mismatches:
            failed_checks.append(
                f"{item['date']} {item['name']}: {check.name} "
                f"{check.describe()}"
            )

    echo(f"  Verification on {sample['date']} {sample['name']}:")
    for check in sample["stream"].checks:
        echo(f"    {check.name:<20} {check.describe()}")
    echo("")
    echo(f"  {passed_all} of {len(activities)} activities verified against their")
    echo(f"  own summary. {len(failed_checks)} genuine mismatch"
         f"{'' if len(failed_checks) == 1 else 'es'} found.")
    if unverifiable:
        echo(f"  {unverifiable} could NOT be verified at all: no metric in them had")
        echo(f"  both a stream value and a summary value to compare. Those are")
        echo(f"  not counted as passing.")
    if not_recorded:
        echo("")
        echo("  Metrics the summary reports but the stream never recorded:")
        for name, count in sorted(not_recorded.items(), key=lambda kv: -kv[1]):
            echo(f"    {name:<20} missing in {count} activit"
                 f"{'y' if count == 1 else 'ies'}")
        echo("  That is normal for a ride with no heart-rate strap. It is not")
        echo("  evidence the mapping is wrong, so it does not fail an activity.")
    if passed_all == 0:
        echo("")
        echo("  STOPPING. Not one activity could be verified, so there is no")
        echo("  evidence the column mapping is correct. Drawing a chart on top")
        echo("  of that would be exactly the failure this step exists to catch.")
        return 2

    if failed_checks and passed_all < len(activities) * 0.5:
        echo("")
        echo("  STOPPING. More than half the runs disagree with their own summary,")
        echo("  which means the column mapping cannot be trusted. Rather than draw")
        echo("  a chart that lies, here is what disagreed:")
        for line in failed_checks[:10]:
            echo(f"    - {line}")
        return 2
    if failed_checks:
        echo(f"  {len(failed_checks)} individual checks failed on otherwise usable")
        echo(f"  runs; those runs are still included but flagged. Examples:")
        for line in failed_checks[:3]:
            echo(f"    - {line}")

    # ------------------------------------------------ step 4: spike filter
    step("4", "Removing optical heart-rate artifacts")
    total_spikes = sum(item["stream"].spikes_removed for item in activities)
    total_samples = sum(item["stream"].sample_count for item in activities)
    echo(f"  A wrist optical sensor intermittently locks onto cadence and throws")
    echo(f"  a lone sample far above anything real, usually early in a run.")
    echo("")
    echo(f"  Samples examined              : {total_samples:,}")
    echo(f"  Single-sample artifacts thrown: {total_spikes:,}")
    echo(f"  Rule applied: a sample must jump more than "
         f"{streams.SPIKE_THRESHOLD_BPM:.0f} bpm from BOTH")
    echo(f"  neighbours while those neighbours agree with each other. A genuine")
    echo(f"  surge carries its neighbours with it and is left alone.")

    worst = sorted(activities, key=lambda a: -a["stream"].spikes_removed)[:3]
    if worst and worst[0]["stream"].spikes_removed:
        echo("")
        echo("  Runs with the most artifacts:")
        for item in worst:
            if item["stream"].spikes_removed:
                peak = max((s["value"] for s in item["stream"].spike_samples), default=0)
                echo(f"    {item['date']} {item['name'][:30]:<30} "
                     f"{item['stream'].spikes_removed:>3} removed, "
                     f"worst read {peak:.0f} bpm")

    # -------------------------------------------------- step 5: real zones
    step("5", "Working out YOUR zones, from efforts you actually ran")

    # Max heart rate is a whole-body ceiling, so every sport is evidence for
    # it. Threshold PACE is not: a bike ride has no minutes per mile worth the
    # name, so pace is only ever derived from running.
    foot_activities = [a for a in activities if fetch.is_run(a["raw"])]
    other_count = len(activities) - len(foot_activities)

    max_hr = zones.derive_max_heart_rate(activities)
    if not max_hr:
        echo("  No usable heart rate anywhere in this window. Stopping.")
        return 1
    if other_count:
        echo(f"  Max heart rate is drawn from all {len(activities)} activities")
        echo(f"  ({other_count} of them not running), because a maximum is a")
        echo(f"  whole-body ceiling and every sport is evidence for it.")
        echo(f"  Threshold pace uses only the {len(foot_activities)} runs, because")
        echo(f"  minutes per mile means nothing on a bike.")
        echo("")
    if not foot_activities:
        echo("  No runs in the included set, so threshold PACE cannot be derived.")
        echo("  Re-run with --include running (or add it to your list).")
        return 1

    echo("  MAX HEART RATE")
    echo(f"    Derived            : {max_hr.value:.0f} bpm")
    echo(f"    From               : {max_hr.source.name} on {max_hr.source.date}")
    echo(f"    Basis              : {max_hr.basis}")
    echo(f"    Unfiltered peak    : {max_hr.instantaneous:.0f} bpm  "
         f"<- what you get if you skip step 4")
    echo(f"    Best 5 seconds     : {max_hr.window_5s:.0f} bpm"
         if max_hr.window_5s else "")
    echo(f"    Best 30 seconds    : {max_hr.window_30s:.0f} bpm"
         if max_hr.window_30s else "")
    echo(f"    NOT 220-minus-age. This is the highest clean value you produced.")

    threshold_hr = zones.derive_threshold_heart_rate(foot_activities, max_hr)
    echo("")
    echo("  THRESHOLD HEART RATE")
    echo(f"    Derived            : {threshold_hr.value:.0f} bpm "
         f"({threshold_hr.percent_of_max:.0f}% of max)")
    echo(f"    Basis              : {threshold_hr.basis}")
    if threshold_hr.source:
        echo(f"    From               : {threshold_hr.source.name} "
             f"on {threshold_hr.source.date}")
    if threshold_hr.is_rule_of_thumb:
        echo(f"    ^ NOT a direct measurement. The basis line above says exactly")
        echo(f"      why, and the number is labelled that way everywhere it appears.")

    threshold_pace = zones.derive_threshold_pace(
        foot_activities, max_hr, threshold_hr, measurement_system
    )
    echo("")
    echo("  THRESHOLD PACE")
    if threshold_pace.source:
        echo(f"    Best effort found  : {threshold_pace.source.detail}")
        echo(f"    From               : {threshold_pace.source.name} "
             f"on {threshold_pace.source.date}")
    echo(f"    Raw (unadjusted)   : {fmt_pace(threshold_pace.raw_value_min_per_mile)}/mi")
    echo("")
    echo("    Judgment applied before committing to a number:")
    for check in threshold_pace.checks:
        echo("")
        echo(f"      {check.name}")
        echo(f"        Found      : {check.finding}")
        echo(f"        Conclusion : {check.conclusion}")
    echo("")
    echo(f"    COMMITTED NUMBER   : {fmt_pace(threshold_pace.value_min_per_mile)}/mi "
         f"[{threshold_pace.confidence.upper()} CONFIDENCE]")
    echo(f"    What would sharpen it: {threshold_pace.what_would_sharpen}")

    pace_zone_list = zones.pace_zones(threshold_pace.value_min_per_mile)
    hr_zone_list = zones.heart_rate_zones(threshold_hr.value)

    echo("")
    echo("  YOUR ZONE TABLE")
    echo(f"    {'':<4}{'':<11}{'Pace /mi':<20}{'Heart rate':<16}Evidence")
    for pace_zone, hr_zone in zip(pace_zone_list, hr_zone_list):
        evidence = (
            f"from {fmt_pace(threshold_pace.value_min_per_mile)} T pace / "
            f"{threshold_hr.value:.0f} LTHR"
        )
        echo(f"    {pace_zone.key:<4}{pace_zone.name:<11}"
             f"{pace_zone.label.replace(' /mi', ''):<20}"
             f"{hr_zone.label.replace(' bpm', ''):<16}{evidence}")
    echo("")
    echo(f"    Max HR {max_hr.value:.0f} bpm  <- {max_hr.source.date}, measured")
    echo(f"    LTHR   {threshold_hr.value:.0f} bpm  <- "
         f"{'measured' if not threshold_hr.is_rule_of_thumb else 'RULE OF THUMB'}")
    echo(f"    T pace {fmt_pace(threshold_pace.value_min_per_mile)}/mi <- "
         f"{threshold_pace.confidence} confidence")

    # ------------------------------------- step 6: pick the run and build it
    step("6", "Building the page")

    recent = sorted(foot_activities, key=lambda a: a["date"], reverse=True)
    if args.feature:
        chosen = [a for a in activities if str(a["id"]) == str(args.feature)]
        if not chosen:
            echo(f"  No activity with id {args.feature} in the downloaded set.")
            return 1
        recent = chosen
    scored = [
        (item, quality_score(item["stream"], threshold_pace.value_min_per_mile))
        for item in recent
    ]
    target = recent[0] if args.feature else None
    if target:
        echo(f"  Using the activity you asked for: {target['date']} {target['name']}")
        if not fetch.is_run(target["raw"]):
            echo("  Note: this is not a run. The heart rate zones below still")
            echo("  apply, but the pace zones are derived from running and mean")
            echo("  little for this sport.")
    for item, score in (scored if not args.feature else []):
        if score >= 300 and item["stream"].duration_seconds >= 900:
            target = item
            echo(f"  Chose {item['date']} {item['name']}")
            echo(f"  ({fmt_duration(score)} spent at marathon pace or faster, so this")
            echo(f"  was a real effort rather than an easy day.)")
            break
    if target is None:
        target = max(recent[:10], key=lambda a: a["stream"].duration_seconds)
        echo(f"  No clearly hard run in the window, so using the longest recent one:")
        echo(f"  {target['date']} {target['name']}")

    # ---- a panel for EVERY activity, not just the featured one ----------
    echo("")
    echo(f"  Building panels for all {len(activities)} activities ...")

    panels: list[dict] = []
    for item in sorted(activities, key=lambda a: a["date"], reverse=True):
        item_stream = item["stream"]
        if not item.get("splits"):
            item["splits"] = fetch.fetch_activity_bundle(
                api, cache, item["id"]
            )["splits"]

        item_splits = mile_splits(item_stream, pace_zone_list)
        item_laps = lap_chart_data(item["splits"], pace_zone_list, item_splits)
        on_foot = fetch.is_run(item["raw"])

        summary = item["summary"]
        miles = metres_to_miles(summary.get("distance"))
        moving = summary.get("movingDuration") or summary.get("duration") or 0
        avg_pace = (moving / 60.0) / miles if miles and moving else None

        started = summary.get("startTimeLocal", "")
        try:
            stamp = dt.datetime.fromisoformat(str(started))
            when = stamp.strftime("%A, %d %B %Y at %H:%M")
            short_date = stamp.strftime("%a %d %b")
        except (ValueError, TypeError):
            when = short_date = str(started)[:16]

        panels.append({
            "id": item["id"], "name": item["name"], "when": when,
            "short_date": short_date,
            "sport": report.sport_label(fetch.activity_type_key(item["raw"])),
            "mode": "pace" if on_foot else "speed",
            "miles": miles, "moving": moving, "avg_pace": avg_pace,
            "avg_mph": (60.0 / avg_pace) if avg_pace else 0.0,
            "avg_hr": summary.get("averageHR"),
            "splits": item_splits, "laps": item_laps,
            "pace_zone_seconds": zones.time_in_pace_zones(
                item_stream, pace_zone_list),
            "hr_zone_seconds": zones.time_in_heart_rate_zones(
                item_stream, hr_zone_list),
            "spikes": item_stream.spikes_removed,
            "samples": item_stream.sample_count,
        })

    featured = next(
        (n for n, panel in enumerate(panels) if panel["id"] == target["id"]), 0
    )
    echo(f"  {len(panels)} panels built. The activity list is the landing")
    echo(f"  screen; tap any activity to open it. The chips filter by sport and")
    echo(f"  the tabs hold your zones and the Garmin comparison.")
    echo(f"  Time in zone computed from the per-second stream, not from splits.")

    # ------------------------------------------- step 7: the honest section
    step("7", "What Garmin Connect is actually grading you against")
    app = appmodel.read_app_model(api, cache, echo)
    echo(f"  Read from Garmin: {', '.join(app.sources_found) or 'nothing available'}")
    if app.sources_missing:
        echo(f"  Not available on this account: {', '.join(app.sources_missing)}")
    echo("")
    if app.basis_statements:
        echo("  Garmin states its own basis as:")
        for statement in app.basis_statements:
            echo(f"    - {statement}")
    else:
        echo("  Garmin did not expose a stated basis on this account.")

    # Runs only. A 5 km covered on a bike takes about eight minutes and would
    # masquerade as a 5K personal best, making Garmin's race prediction look
    # absurdly slow and inverting the entire comparison below.
    best_5k = None
    for item in foot_activities:
        elapsed, _ = zones.fastest_time_for_distance(
            item["stream"].time, item["stream"].distance, 5000.0
        )
        if elapsed and (best_5k is None or elapsed < best_5k):
            best_5k = elapsed

    findings, easy_gap = appmodel.compare(
        app, max_hr, threshold_hr, threshold_pace, best_5k
    )
    echo("")
    for finding in findings:
        marker = {"agree": "AGREES", "differ": "DIFFERS", "warn": "WARNING"}[finding.kind]
        echo(f"  [{marker}] {finding.headline}")
        echo(f"           {finding.detail}")
        echo("")

    our_easy = zones.easy_pace_ceiling(threshold_pace.value_min_per_mile)
    app_easy = (
        zones.easy_pace_ceiling(app.threshold_pace) if app.threshold_pace else None
    )

    context = {
        "activities": panels, "featured": featured,
        "pace_zones": pace_zone_list, "hr_zones": hr_zone_list,
        "max_hr": max_hr, "threshold_hr": threshold_hr,
        "threshold_pace": threshold_pace, "findings": findings, "app": app,
        "easy_gap_seconds": easy_gap, "our_easy_ceiling": our_easy,
        "app_easy_ceiling": app_easy, "days": args.days,
    }
    html_text = report.build_html(context)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "training.html"
    out_file.write_text(html_text, encoding="utf-8")

    # -------------------------------------------------------- step 8: open
    step("8", "The one number that matters")
    if easy_gap is None:
        echo("  Garmin did not expose a threshold pace on this account, so there")
        echo("  is no app ceiling to measure the gap against.")
        echo(f"  Your evidence-based easy ceiling is {fmt_pace(our_easy)}/mi.")
    else:
        direction = "FASTER" if easy_gap > 0 else "SLOWER"
        echo(f"  Garmin's easy pace ceiling      : {fmt_pace(app_easy)}/mi")
        echo(f"  Your evidence-based easy ceiling: {fmt_pace(our_easy)}/mi")
        echo("")
        echo(f"      {abs(easy_gap):.0f} SECONDS PER MILE APART")
        echo("")
        echo(f"  Garmin has been grading your easy runs against a ceiling")
        echo(f"  {abs(easy_gap):.0f} s/mi {direction} than your own watch data supports.")

    echo("")
    echo(f"  Page written to: {out_file}")
    echo(f"  Read-only API calls made this session: {api.calls}")
    echo(f"  Cache hits: {cache.hits} (a re-run costs no API calls at all)")

    if not args.no_open:
        webbrowser.open(out_file.as_uri())
        echo("  Opening it in your browser now.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        echo("\n  Stopped. Nothing was uploaded and nothing was written to Garmin.")
        sys.exit(130)
