"""The single self-contained HTML page.

Everything is inline: no CDN, no external stylesheet, no font download, no
network access of any kind. The file works with the wifi off and can be
emailed, moved or opened years from now.
"""

from __future__ import annotations

import datetime as dt
import html
from typing import Any

from .util import (
    fmt_clock,
    fmt_duration,
    fmt_pace,
    metres_to_feet,
    metres_to_miles,
    speed_to_pace_min_per_mile,
)

ZONE_COLOURS = {
    "Z1": "#9aa5b1",
    "Z2": "#2f80ed",
    "Z3": "#27ae60",
    "Z4": "#f2994a",
    "Z5": "#eb5757",
}


SPORT_LABELS = {
    "running": "Run", "trail_running": "Trail Run", "track_running": "Track",
    "treadmill_running": "Treadmill", "road_running": "Run",
    "virtual_run": "Virtual Run", "ultra_run": "Ultra",
    "cycling": "Ride", "road_biking": "Ride", "mountain_biking": "MTB",
    "gravel_cycling": "Gravel", "indoor_cycling": "Indoor Ride",
    "virtual_ride": "Virtual Ride", "e_bike_fitness": "E-Bike",
    "walking": "Walk", "casual_walking": "Walk", "speed_walking": "Walk",
    "hiking": "Hike", "lap_swimming": "Swim", "open_water_swimming": "Swim",
    "swimming": "Swim", "strength_training": "Strength",
    "indoor_cardio": "Cardio", "fitness_equipment": "Gym",
    "yoga": "Yoga", "rowing": "Row", "indoor_rowing": "Row",
}

SPORT_COLOURS = {
    "Run": "#2f80ed", "Trail Run": "#27ae60", "Track": "#2f80ed",
    "Treadmill": "#2f80ed", "Virtual Run": "#2f80ed", "Ultra": "#27ae60",
    "Ride": "#f2994a", "MTB": "#f2994a", "Gravel": "#f2994a",
    "Indoor Ride": "#f2994a", "Virtual Ride": "#f2994a", "E-Bike": "#f2994a",
    "Walk": "#9aa5b1", "Hike": "#27ae60", "Swim": "#56ccf2",
    "Strength": "#bb6bd9", "Cardio": "#bb6bd9", "Gym": "#bb6bd9",
    "Yoga": "#bb6bd9", "Row": "#56ccf2",
}


def sport_label(type_key: str) -> str:
    if type_key in SPORT_LABELS:
        return SPORT_LABELS[type_key]
    return (type_key or "Activity").replace("_", " ").title()


LIST_CSS = """
.tabs{display:flex;border-bottom:1px solid #eef1f5;position:sticky;top:0;
  background:#fff;z-index:5}
.tabs button{flex:1;border:0;background:none;padding:14px 4px 12px;font:inherit;
  font-size:11px;letter-spacing:.6px;text-transform:uppercase;font-weight:640;
  color:#8b96a3;cursor:pointer;border-bottom:2px solid transparent}
.tabs button.on{color:#16202b;border-bottom-color:#16202b}
.chips{display:flex;gap:6px;padding:14px 20px 4px;flex-wrap:wrap}
.chips button{border:1px solid #e2e7ed;background:#fff;border-radius:20px;
  padding:5px 11px;font:inherit;font-size:11px;font-weight:600;color:#5b6672;
  cursor:pointer}
.chips button.on{background:#16202b;border-color:#16202b;color:#fff}
.row{display:flex;align-items:center;gap:12px;padding:13px 20px;
  border-bottom:1px solid #f5f7f9;cursor:pointer;background:none;border-left:0;
  border-right:0;border-top:0;width:100%;text-align:left;font:inherit}
.row:hover{background:#fafbfc}
.dot{width:9px;height:9px;border-radius:50%;flex:none}
.row .mid{flex:1;min-width:0;display:block}
.row .nm{display:block;font-size:13.5px;font-weight:620;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.row .sub{display:block;font-size:11px;color:#8b96a3;margin-top:3px}
.row .rt{text-align:right;font-variant-numeric:tabular-nums;flex:none}
.row .rt b{display:block;font-size:14px;font-weight:660}
.row .rt span{font-size:11px;color:#8b96a3}
.back{display:flex;align-items:center;gap:8px;padding:14px 20px;border:0;
  background:none;font:inherit;font-size:12.5px;font-weight:620;color:#2f80ed;
  cursor:pointer;border-bottom:1px solid #eef1f5;width:100%;text-align:left}
.empty{padding:40px 20px;text-align:center;color:#8b96a3;font-size:12.5px}
.sumcard{padding:16px 20px;border-bottom:1px solid #eef1f5;display:flex;gap:10px}
.sumcard div{flex:1;background:#f7f9fb;border-radius:11px;padding:11px}
.sumcard .lab{font-size:9px;letter-spacing:.7px;text-transform:uppercase;
  color:#8b96a3}
.sumcard .val{font-size:17px;font-weight:660;margin-top:4px;
  font-variant-numeric:tabular-nums}
"""

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#eceff3;color:#16202b;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
  padding:24px 12px;-webkit-font-smoothing:antialiased}
.phone{max-width:414px;margin:0 auto;background:#fff;border-radius:28px;
  box-shadow:0 6px 34px rgba(16,32,48,.14);overflow:hidden}
.pad{padding:20px}
.hdr{padding:22px 20px 18px;border-bottom:1px solid #eef1f5}
.hdr h1{font-size:19px;font-weight:650;letter-spacing:-.2px;line-height:1.25}
.hdr .when{margin-top:5px;font-size:13px;color:#7c8896}
.hero{display:flex;border-bottom:1px solid #eef1f5}
.hero div{flex:1;padding:18px 8px;text-align:center;border-right:1px solid #eef1f5}
.hero div:last-child{border-right:0}
.hero .v{font-size:25px;font-weight:680;letter-spacing:-.8px;font-variant-numeric:tabular-nums}
.hero .k{margin-top:4px;font-size:10px;letter-spacing:.9px;color:#8b96a3;text-transform:uppercase}
h2{font-size:11px;letter-spacing:1.1px;text-transform:uppercase;color:#8b96a3;
  font-weight:640;margin-bottom:12px}
section{padding:20px;border-bottom:1px solid #eef1f5}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th{text-align:right;font-size:10px;letter-spacing:.5px;color:#8b96a3;font-weight:600;
  text-transform:uppercase;padding-bottom:9px;border-bottom:1px solid #eef1f5}
th:first-child{text-align:left}
td{padding:9px 0;text-align:right;border-bottom:1px solid #f5f7f9}
td:first-child{text-align:left;font-weight:600}
tr:last-child td{border-bottom:0}
.bar{margin-bottom:11px}
.bar .row{display:flex;justify-content:space-between;font-size:12px;margin-bottom:5px}
.bar .nm{font-weight:600}
.bar .nm span{color:#8b96a3;font-weight:400;margin-left:6px;font-size:11px}
.bar .pc{font-variant-numeric:tabular-nums;color:#5b6672}
.track{height:9px;background:#f0f3f6;border-radius:5px;overflow:hidden}
.fill{height:100%;border-radius:5px}
.note{font-size:11px;color:#8b96a3;margin-top:10px;line-height:1.5}
.cmp{background:#16202b;color:#fff}
.cmp h2{color:#7f8c9b}
.cmp .big{font-size:36px;font-weight:700;letter-spacing:-1.4px;line-height:1.05}
.cmp .big small{display:block;font-size:12px;font-weight:400;color:#8f9dab;
  letter-spacing:0;margin-top:8px;line-height:1.5}
.two{display:flex;gap:10px;margin:16px 0 4px}
.two div{flex:1;background:#22303f;border-radius:11px;padding:13px}
.two .lab{font-size:9.5px;letter-spacing:.8px;text-transform:uppercase;color:#8f9dab}
.two .val{font-size:20px;font-weight:670;margin-top:5px;font-variant-numeric:tabular-nums}
.find{border-left:3px solid;padding:11px 0 11px 12px;margin-bottom:11px;font-size:12.5px;
  line-height:1.5}
.find b{display:block;margin-bottom:4px;font-weight:640}
.agree{border-color:#27ae60}
.differ{border-color:#f2994a}
.warn{border-color:#eb5757}
.cmp .find{color:#c9d4de}
.cmp .find b{color:#fff}
.ev{font-size:11px;color:#8b96a3;margin-top:3px;line-height:1.45}
.tag{display:inline-block;font-size:9px;letter-spacing:.6px;text-transform:uppercase;
  padding:2px 7px;border-radius:20px;font-weight:650;vertical-align:middle}
.t-hi{background:#e3f6ea;color:#1c7a42}
.t-md{background:#fdf0e0;color:#95591b}
.t-lo{background:#fce8e8;color:#a32b2b}
.foot{padding:16px 20px 22px;font-size:10.5px;color:#98a3b0;line-height:1.6;text-align:center}
"""


def _bar(name: str, sub: str, seconds: float, total: float, colour: str) -> str:
    percent = (seconds / total * 100.0) if total else 0.0
    return f"""<div class="bar">
      <div class="row"><div class="nm">{html.escape(name)}<span>{html.escape(sub)}</span></div>
      <div class="pc">{percent:.0f}% &middot; {fmt_clock(seconds)}</div></div>
      <div class="track"><div class="fill" style="width:{max(percent, 0.6):.2f}%;
        background:{colour}"></div></div></div>"""


def _lap_chart(laps: list[dict[str, Any]], is_pace: bool = True,
               flat_colour: str | None = None) -> str:
    """Inline SVG bar chart of lap effort. A taller bar is always faster.

    Foot sports are charted in minutes per mile, where a SMALLER number is
    faster; wheels and water are charted in mph, where a LARGER number is.
    Both are normalised so height means the same thing either way.
    """
    key = "pace" if is_pace else "mph"
    values = [lap[key] for lap in laps if lap.get(key)]
    if not values:
        return '<div class="note">No lap data for this activity.</div>'

    low, high = min(values), max(values)
    # When every lap is effectively the same effort, scaling the difference up
    # to fill the chart invents drama that is not in the data. Draw them level.
    span = high - low
    uniform = span < (low * 0.02)
    spread = max(span, 0.15 if is_pace else 0.4)
    width, height = 374, 132
    gap = 5
    bar_width = max((width - gap * (len(laps) - 1)) / len(laps), 3)

    bars, labels = [], []
    for i, lap in enumerate(laps):
        value = lap.get(key)
        if not value:
            continue
        x = i * (bar_width + gap)
        # Fraction is 1.0 for the fastest lap in either convention.
        if uniform:
            fraction = 0.62
        else:
            fraction = ((high - value) / spread if is_pace
                        else (value - low) / spread)
        bar_height = 26 + fraction * (height - 46)
        y = height - bar_height
        # Pace zones are a running construct. Colouring a bike lap by them
        # paints every lap red, which says nothing true about the ride.
        colour = flat_colour or ZONE_COLOURS.get(lap.get("zone", "Z2"), "#2f80ed")
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
            f'height="{bar_height:.1f}" rx="3" fill="{colour}"/>'
        )
        if len(laps) <= 14:
            text = fmt_pace(value) if is_pace else f"{value:.1f}"
            labels.append(
                f'<text x="{x + bar_width / 2:.1f}" y="{y - 5:.1f}" '
                f'font-size="8.5" fill="#7c8896" text-anchor="middle" '
                f'font-family="inherit">{text}</text>'
            )

    if is_pace:
        caption = f"Fastest {fmt_pace(low)}/mi, slowest {fmt_pace(high)}/mi."
    else:
        caption = f"Fastest {high:.1f} mph, slowest {low:.1f} mph."

    if uniform:
        lead = "Every lap was effectively the same effort, so the bars are level."
    elif flat_colour:
        lead = "Taller is faster."
    else:
        lead = "Taller is faster. Bar colour is the pace zone that lap fell in."

    return (
        f'<svg viewBox="0 -14 {width} {height + 20}" width="100%" '
        f'style="display:block;overflow:visible">{"".join(bars)}{"".join(labels)}</svg>'
        f'<div class="note">{lead} {caption}</div>'
    )


def _confidence_tag(confidence: str) -> str:
    css = {"high": "t-hi", "medium": "t-md"}.get(confidence, "t-lo")
    return f'<span class="tag {css}">{html.escape(confidence)} confidence</span>'


def _activity_panel(activity: dict[str, Any], index: int,
                    pace_zone_list, hr_zone_list) -> str:
    """One activity's full detail: hero, lap chart, splits, zones."""
    is_pace = activity["mode"] == "pace"
    if not activity["avg_pace"]:
        headline, headline_label = "--", "No distance"
    elif is_pace:
        headline, headline_label = fmt_pace(activity["avg_pace"]), "Avg Pace /mi"
    else:
        headline, headline_label = f"{activity['avg_mph']:.1f}", "Avg Speed mph"

    split_rows = []
    for split in activity["splits"]:
        gain = split.get("elevation_gain")
        hr = split.get("hr")
        value = (fmt_pace(split.get("pace")) if is_pace
                 else (f"{split['mph']:.1f}" if split.get("mph") else "--"))
        split_rows.append(
            f"<tr><td>{html.escape(str(split['label']))}</td>"
            f"<td>{value}</td>"
            f"<td>{f'{gain:+.0f} ft' if gain is not None else '--'}</td>"
            f"<td>{f'{hr:.0f}' if hr else '--'}</td></tr>"
        )
    if not split_rows:
        split_rows.append('<tr><td colspan="4" style="color:#8b96a3">'
                          'No distance splits for this activity.</td></tr>')

    pace_total = sum(activity["pace_zone_seconds"]) or 0.0
    hr_total = sum(activity["hr_zone_seconds"]) or 0.0

    pace_section = ""
    if pace_total > 0 and is_pace:
        bars = "".join(
            _bar(f"{z.key} {z.name}", z.label, seconds, pace_total,
                 ZONE_COLOURS[z.key])
            for z, seconds in zip(pace_zone_list, activity["pace_zone_seconds"])
        )
        pace_section = f"""<section><h2>Time in pace zones</h2>{bars}
          <div class="note">Computed sample by sample from the per-second
          stream ({activity['samples']:,} samples), never from splits.</div>
        </section>"""

    hr_section = ""
    if hr_total > 0:
        bars = "".join(
            _bar(f"{z.key} {z.name}", z.label, seconds, hr_total,
                 ZONE_COLOURS[z.key])
            for z, seconds in zip(hr_zone_list, activity["hr_zone_seconds"])
        )
        note = (f"{activity['spikes']} single-sample optical artifact"
                f"{'s' if activity['spikes'] != 1 else ''} removed from this "
                f"activity before these numbers were computed.")
        if not is_pace:
            note += (" Pace zones are omitted here because they are derived "
                     "from running and mean little for this sport.")
        hr_section = f"""<section><h2>Time in heart rate zones</h2>{bars}
          <div class="note">{note}</div></section>"""
    else:
        hr_section = ('<section><div class="note">No heart rate was recorded '
                      'for this activity.</div></section>')

    distance = (f"{activity['miles']:.2f}" if activity["miles"] else "--")
    return f"""<div class="panel" data-panel="{index}" hidden>
      <button class="back" onclick="showList()">&larr; All activities</button>
      <div class="hdr">
        <h1>{html.escape(activity['name'])}</h1>
        <div class="when">{html.escape(activity['when'])} &middot;
          {html.escape(activity['sport'])}</div>
      </div>
      <div class="hero">
        <div><div class="v">{distance}</div><div class="k">Miles</div></div>
        <div><div class="v">{headline}</div><div class="k">{headline_label}</div></div>
        <div><div class="v">{fmt_duration(activity['moving'])}</div>
          <div class="k">Moving</div></div>
      </div>
      <section><h2>Lap chart</h2>{_lap_chart(activity['laps'], is_pace,
        None if is_pace else SPORT_COLOURS.get(activity['sport'], '#9aa5b1'))}</section>
      <section><h2>Splits</h2>
        <table><thead><tr><th>Mile</th>
          <th>{'Pace' if is_pace else 'mph'}</th><th>Elev</th><th>Avg HR</th>
        </tr></thead><tbody>{''.join(split_rows)}</tbody></table>
      </section>
      {pace_section}
      {hr_section}
    </div>"""


def build_html(context: dict[str, Any]) -> str:
    activities = context["activities"]
    pace_zone_list = context["pace_zones"]
    hr_zone_list = context["hr_zones"]
    max_hr = context["max_hr"]
    threshold_hr = context["threshold_hr"]
    threshold_pace = context["threshold_pace"]
    findings = context["findings"]
    easy_gap = context["easy_gap_seconds"]
    app = context["app"]

    # ---- the activity list ------------------------------------------------
    sports: dict[str, int] = {}
    for activity in activities:
        sports[activity["sport"]] = sports.get(activity["sport"], 0) + 1

    chips = ['<button class="on" data-sport="" onclick="filterBy(this,\'\')">'
             f'All {len(activities)}</button>']
    for sport, count in sorted(sports.items(), key=lambda kv: -kv[1]):
        chips.append(
            f'<button data-sport="{html.escape(sport)}" '
            f'onclick="filterBy(this,\'{html.escape(sport)}\')">'
            f'{html.escape(sport)} {count}</button>'
        )

    rows = []
    for index, activity in enumerate(activities):
        colour = SPORT_COLOURS.get(activity["sport"], "#9aa5b1")
        is_pace = activity["mode"] == "pace"
        if not activity["avg_pace"]:
            right = "--"
        elif is_pace:
            right = fmt_pace(activity["avg_pace"]) + "/mi"
        else:
            right = f"{activity['avg_mph']:.1f} mph"
        distance = f"{activity['miles']:.2f} mi" if activity["miles"] else "--"
        heart = f" &middot; {activity['avg_hr']:.0f} bpm" if activity["avg_hr"] else ""
        rows.append(
            f'<button class="row" data-sport="{html.escape(activity["sport"])}" '
            f'onclick="showPanel({index})">'
            f'<span class="dot" style="background:{colour}"></span>'
            f'<span class="mid"><span class="nm">{html.escape(activity["name"])}</span>'
            f'<span class="sub">{html.escape(activity["short_date"])} &middot; '
            f'{html.escape(activity["sport"])}{heart}</span></span>'
            f'<span class="rt"><b>{distance}</b>'
            f'<span>{fmt_duration(activity["moving"])} &middot; {right}</span></span>'
            f'</button>'
        )

    panels = "".join(
        _activity_panel(activity, index, pace_zone_list, hr_zone_list)
        for index, activity in enumerate(activities)
    )

    total_miles = sum(a["miles"] for a in activities)
    total_time = sum(a["moving"] or 0 for a in activities)

    # ---- zones tab --------------------------------------------------------
    zone_rows = "".join(
        f"<tr><td>{z.key} {z.name}</td><td>{z.label.replace(' /mi','')}</td>"
        f"<td>{h.label.replace(' bpm','')}</td></tr>"
        for z, h in zip(pace_zone_list, hr_zone_list)
    )
    threshold_note = (
        f'<div class="ev">Not a direct measurement &mdash; '
        f'{html.escape(threshold_hr.basis)}</div>'
        if threshold_hr.is_rule_of_thumb
        else f'<div class="ev">Measured from '
             f'{html.escape(threshold_hr.source.name)} on '
             f'{html.escape(threshold_hr.source.date)}</div>'
    )
    checks_html = "".join(
        f'<div class="find {"warn" if c.flag else "agree"}"><b>'
        f'{html.escape(c.name)}</b>{html.escape(c.finding)}'
        f'<div class="ev">{html.escape(c.conclusion)}</div></div>'
        for c in threshold_pace.checks
    )

    # ---- comparison tab ---------------------------------------------------
    findings_html = "".join(
        f'<div class="find {f.kind}"><b>{html.escape(f.headline)}</b>'
        f'{html.escape(f.detail)}</div>' for f in findings
    )
    basis = ("; ".join(html.escape(b) for b in app.basis_statements)
             if app.basis_statements
             else "Garmin did not expose a stated basis on this account.")

    if easy_gap is None:
        gap_block = ('<div class="big">Not comparable<small>Garmin did not '
                     'expose a threshold pace on this account, so there is no '
                     'app ceiling to measure against.</small></div>')
    else:
        direction = "faster" if easy_gap > 0 else "slower"
        gap_block = f"""<div class="big">{abs(easy_gap):.0f} s/mi<small>
          Garmin has been grading your easy runs against a ceiling
          {abs(easy_gap):.0f} seconds per mile {direction} than the evidence
          from your own watch supports.</small></div>
          <div class="two">
            <div><div class="lab">Garmin's easy ceiling</div>
              <div class="val">{fmt_pace(context['app_easy_ceiling'])}</div></div>
            <div><div class="lab">Evidence-based ceiling</div>
              <div class="val">{fmt_pace(context['our_easy_ceiling'])}</div></div>
          </div>"""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Training &mdash; {len(activities)} activities</title>
<style>{CSS}{LIST_CSS}</style></head><body><div class="phone">

<div class="tabs">
  <button class="on" data-tab="0" onclick="showTab(0)">Activities</button>
  <button data-tab="1" onclick="showTab(1)">Your Zones</button>
  <button data-tab="2" onclick="showTab(2)">vs Garmin</button>
</div>

<div class="tabview" data-tab="0">
  <div id="listview">
    <div class="hdr"><h1>Your training</h1>
      <div class="when">{len(activities)} activities over
      {context['days']} days</div></div>
    <div class="sumcard">
      <div><div class="lab">Distance</div>
        <div class="val">{total_miles:.0f} mi</div></div>
      <div><div class="lab">Moving time</div>
        <div class="val">{fmt_clock(total_time)}</div></div>
      <div><div class="lab">Activities</div>
        <div class="val">{len(activities)}</div></div>
    </div>
    <div class="chips">{''.join(chips)}</div>
    <div id="rows">{''.join(rows)}</div>
    <div class="empty" id="noneleft" hidden>Nothing of that type.</div>
  </div>
  {panels}
</div>

<div class="tabview" data-tab="1" hidden>
  <section><h2>Your zones, derived from your own runs</h2>
    <table><thead><tr><th>Zone</th><th>Pace /mi</th><th>Heart rate</th></tr>
      </thead><tbody>{zone_rows}</tbody></table>
  </section>
  <section><h2>Where those numbers came from</h2>
    <table><tbody>
      <tr><td>Max HR</td><td>{max_hr.value:.0f} bpm</td>
          <td>{html.escape(max_hr.source.date)}</td></tr>
      <tr><td>Threshold HR</td><td>{threshold_hr.value:.0f} bpm</td>
          <td>{html.escape(threshold_hr.source.date if threshold_hr.source else 'estimate')}</td></tr>
      <tr><td>Threshold pace</td>
          <td>{fmt_pace(threshold_pace.value_min_per_mile)}/mi</td>
          <td>{html.escape(threshold_pace.source.date if threshold_pace.source else 'estimate')}</td></tr>
    </tbody></table>
    <div class="ev" style="margin-top:10px">Max HR from
      <b>{html.escape(max_hr.source.name)}</b> &mdash; highest clean 10-second
      sustained value, after removing {max_hr.spikes_removed_total} artifact
      samples across everything downloaded. Unfiltered instantaneous peak was
      {max_hr.instantaneous:.0f} bpm.</div>
    {threshold_note}
    <div style="margin-top:14px">{_confidence_tag(threshold_pace.confidence)}</div>
    <div class="ev">Threshold pace from
      {html.escape(threshold_pace.source.detail if threshold_pace.source else 'no qualifying effort')}.
      What would sharpen it: {html.escape(threshold_pace.what_would_sharpen)}</div>
  </section>
  <section style="border-bottom:0"><h2>The judgment applied before committing</h2>
    <div class="ev" style="margin-bottom:14px">These checks are about the effort
    threshold pace was derived from
    ({html.escape(threshold_pace.source.name if threshold_pace.source else 'n/a')},
    {html.escape(threshold_pace.source.date if threshold_pace.source else '')}).</div>
    {checks_html}
  </section>
</div>

<div class="tabview" data-tab="2" hidden>
  <section class="cmp"><h2>What Garmin is grading you against</h2>
    <div class="ev" style="color:#8f9dab;margin-bottom:14px">Garmin's own
      stated basis: {basis}</div>
    {findings_html}
  </section>
  <section class="cmp" style="border-bottom:0"><h2>The number that matters</h2>
    {gap_block}
  </section>
</div>

<div class="foot">Built from {len(activities)} activities over
{context['days']} days, downloaded read-only from your own Garmin account.<br>
<b>Generated {html.escape(context['generated_at'])}.</b>
Refreshing this page will not change it &mdash; it is a snapshot. Re-run
<code>python3 bootstrap.py</code> to pick up new activities.<br>
This file is entirely self-contained. Nothing in it loads from the internet.</div>

</div>
<script>
function showTab(n){{
  document.querySelectorAll('.tabview').forEach(function(v){{
    v.hidden = (v.dataset.tab !== String(n));
  }});
  document.querySelectorAll('.tabs button').forEach(function(b){{
    b.classList.toggle('on', b.dataset.tab === String(n));
  }});
  if (n === 0) showList();
  window.scrollTo(0, 0);
}}
function showPanel(i){{
  document.getElementById('listview').hidden = true;
  document.querySelectorAll('.panel').forEach(function(p){{
    p.hidden = (p.dataset.panel !== String(i));
  }});
  window.scrollTo(0, 0);
}}
function showList(){{
  document.getElementById('listview').hidden = false;
  document.querySelectorAll('.panel').forEach(function(p){{ p.hidden = true; }});
  window.scrollTo(0, 0);
}}
function filterBy(button, sport){{
  document.querySelectorAll('.chips button').forEach(function(b){{
    b.classList.toggle('on', b === button);
  }});
  var shown = 0;
  document.querySelectorAll('#rows .row').forEach(function(r){{
    var match = (sport === '' || r.dataset.sport === sport);
    r.hidden = !match;
    if (match) shown++;
  }});
  document.getElementById('noneleft').hidden = (shown > 0);
}}
</script>
</body></html>"""
