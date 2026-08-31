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


def _lap_chart(splits: list[dict[str, Any]]) -> str:
    """Inline SVG bar chart of lap pace. Taller bar means a faster lap."""
    paces = [s["pace"] for s in splits if s.get("pace")]
    if not paces:
        return '<div class="note">No lap data for this activity.</div>'

    fastest, slowest = min(paces), max(paces)
    spread = max(slowest - fastest, 0.15)
    width, height = 374, 132
    gap = 5
    bar_width = max((width - gap * (len(splits) - 1)) / len(splits), 4)

    bars, labels = [], []
    for i, split in enumerate(splits):
        pace = split.get("pace")
        x = i * (bar_width + gap)
        if not pace:
            continue
        # Normalise so the fastest lap is tallest.
        fraction = 1.0 - (pace - fastest) / spread
        bar_height = 26 + fraction * (height - 46)
        y = height - bar_height
        colour = ZONE_COLOURS.get(split.get("zone", "Z2"), "#2f80ed")
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" '
            f'height="{bar_height:.1f}" rx="3" fill="{colour}"/>'
        )
        if len(splits) <= 14:
            labels.append(
                f'<text x="{x + bar_width / 2:.1f}" y="{y - 5:.1f}" font-size="8.5" '
                f'fill="#7c8896" text-anchor="middle" '
                f'font-family="inherit">{fmt_pace(pace)}</text>'
            )

    return (
        f'<svg viewBox="0 -14 {width} {height + 20}" width="100%" '
        f'style="display:block;overflow:visible">{"".join(bars)}{"".join(labels)}</svg>'
        f'<div class="note">Taller is faster. Bar colour is the pace zone that '
        f'lap fell in. Fastest {fmt_pace(fastest)}/mi, slowest {fmt_pace(slowest)}/mi.</div>'
    )


def _confidence_tag(confidence: str) -> str:
    css = {"high": "t-hi", "medium": "t-md"}.get(confidence, "t-lo")
    return f'<span class="tag {css}">{html.escape(confidence)} confidence</span>'


def build_html(context: dict[str, Any]) -> str:
    activity = context["activity"]
    stream = context["stream"]
    splits = context["splits"]
    laps = context.get("laps") or splits
    pace_zone_seconds = context["pace_zone_seconds"]
    hr_zone_seconds = context["hr_zone_seconds"]
    pace_zone_list = context["pace_zones"]
    hr_zone_list = context["hr_zones"]
    max_hr = context["max_hr"]
    threshold_hr = context["threshold_hr"]
    threshold_pace = context["threshold_pace"]
    findings = context["findings"]
    easy_gap = context["easy_gap_seconds"]
    app = context["app"]

    distance_miles = metres_to_miles(activity.get("distance"))
    moving_seconds = activity.get("movingDuration") or activity.get("duration") or 0
    average_pace = (moving_seconds / 60.0) / distance_miles if distance_miles else None

    started = activity.get("startTimeLocal", "")
    try:
        when = dt.datetime.fromisoformat(started).strftime("%A, %d %B %Y at %H:%M")
    except (ValueError, TypeError):
        when = started

    pace_total = sum(pace_zone_seconds) or 1.0
    hr_total = sum(hr_zone_seconds) or 1.0

    # --- splits table ---
    split_rows = []
    for split in splits:
        gain = split.get("elevation_gain")
        gain_text = f"{gain:+.0f} ft" if gain is not None else "--"
        hr = split.get("hr")
        split_rows.append(
            f"<tr><td>{html.escape(str(split['label']))}</td>"
            f"<td>{fmt_pace(split.get('pace'))}</td>"
            f"<td>{gain_text}</td>"
            f"<td>{f'{hr:.0f}' if hr else '--'}</td></tr>"
        )

    # --- pace zone bars ---
    pace_bars = "".join(
        _bar(f"{z.key} {z.name}", z.label, seconds, pace_total, ZONE_COLOURS[z.key])
        for z, seconds in zip(pace_zone_list, pace_zone_seconds)
    )
    hr_bars = "".join(
        _bar(f"{z.key} {z.name}", z.label, seconds, hr_total, ZONE_COLOURS[z.key])
        for z, seconds in zip(hr_zone_list, hr_zone_seconds)
    )

    # --- derived zone evidence ---
    threshold_note = (
        f'<div class="ev">Rule of thumb, not a measurement &mdash; {html.escape(threshold_hr.basis)}</div>'
        if threshold_hr.is_rule_of_thumb
        else f'<div class="ev">Measured from {html.escape(threshold_hr.source.name)} '
             f'on {html.escape(threshold_hr.source.date)}</div>'
    )

    checks_html = "".join(
        f'<div class="find {"warn" if c.flag else "agree"}"><b>{html.escape(c.name)}</b>'
        f'{html.escape(c.finding)}<div class="ev">{html.escape(c.conclusion)}</div></div>'
        for c in threshold_pace.checks
    )

    findings_html = "".join(
        f'<div class="find {f.kind}"><b>{html.escape(f.headline)}</b>'
        f'{html.escape(f.detail)}</div>'
        for f in findings
    )

    if easy_gap is None:
        gap_block = (
            '<div class="big">Not comparable<small>Garmin did not expose a '
            'threshold pace on this account, so there is no app ceiling to '
            'measure against.</small></div>'
        )
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

    basis = (
        "; ".join(html.escape(b) for b in app.basis_statements)
        if app.basis_statements
        else "Garmin did not expose a stated basis on this account."
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(activity.get('activityName', 'Run'))}</title>
<style>{CSS}</style></head><body><div class="phone">

<div class="hdr">
  <h1>{html.escape(activity.get('activityName', 'Run'))}</h1>
  <div class="when">{html.escape(when)}</div>
</div>

<div class="hero">
  <div><div class="v">{distance_miles:.2f}</div><div class="k">Miles</div></div>
  <div><div class="v">{fmt_pace(average_pace)}</div><div class="k">Avg Pace /mi</div></div>
  <div><div class="v">{fmt_duration(moving_seconds)}</div><div class="k">Moving</div></div>
</div>

<section><h2>Lap chart</h2>{_lap_chart(laps)}</section>

<section><h2>Splits</h2>
  <table><thead><tr><th>Mile</th><th>Pace</th><th>Elev</th><th>Avg HR</th></tr></thead>
  <tbody>{''.join(split_rows)}</tbody></table>
</section>

<section><h2>Time in pace zones</h2>{pace_bars}
  <div class="note">Computed sample by sample from the per-second stream
  ({stream.sample_count:,} samples), never from mile splits. A mile split during
  an interval session blends the reps with the recoveries, which makes any zone
  percentage built on splits meaningless.</div>
</section>

<section><h2>Time in heart rate zones</h2>{hr_bars}
  <div class="note">{stream.spikes_removed} single-sample optical artifact{'s' if stream.spikes_removed != 1 else ''}
  removed from this run before any of these numbers were computed.</div>
</section>

<section><h2>Your zones, derived from your own runs</h2>
  <table><thead><tr><th>Value</th><th>Derived</th><th>From</th></tr></thead><tbody>
    <tr><td>Max HR</td><td>{max_hr.value:.0f} bpm</td>
        <td>{html.escape(max_hr.source.date)}</td></tr>
    <tr><td>Threshold HR</td><td>{threshold_hr.value:.0f} bpm</td>
        <td>{html.escape(threshold_hr.source.date if threshold_hr.source else 'estimate')}</td></tr>
    <tr><td>Threshold pace</td><td>{fmt_pace(threshold_pace.value_min_per_mile)}/mi</td>
        <td>{html.escape(threshold_pace.source.date if threshold_pace.source else 'estimate')}</td></tr>
  </tbody></table>
  <div class="ev" style="margin-top:10px">Max HR from
    <b>{html.escape(max_hr.source.name)}</b> &mdash; highest clean 10-second
    sustained value, after removing {max_hr.spikes_removed_total} artifact samples
    across every run downloaded. Unfiltered instantaneous peak was
    {max_hr.instantaneous:.0f} bpm.</div>
  {threshold_note}
  <div style="margin-top:14px">{_confidence_tag(threshold_pace.confidence)}</div>
  <div class="ev">Threshold pace from
    {html.escape(threshold_pace.source.detail if threshold_pace.source else 'no qualifying effort')}.
    What would sharpen it: {html.escape(threshold_pace.what_would_sharpen)}</div>
</section>

<section><h2>The judgment applied before committing to that number</h2>
  <div class="ev" style="margin-bottom:14px">These checks are about the effort
  the threshold pace was derived from
  ({html.escape(threshold_pace.source.name if threshold_pace.source else 'n/a')},
  {html.escape(threshold_pace.source.date if threshold_pace.source else '')}), not
  about the run shown above.</div>
  {checks_html}
</section>

<section class="cmp"><h2>What Garmin is grading you against</h2>
  <div class="ev" style="color:#8f9dab;margin-bottom:14px">Garmin's own stated basis:
    {basis}</div>
  {findings_html}
</section>

<section class="cmp" style="border-bottom:0"><h2>The number that matters</h2>
  {gap_block}
</section>

<div class="foot">Built from {context['activity_count']} runs over
{context['days']} days, downloaded read-only from your own Garmin account.<br>
This file is entirely self-contained. Nothing in it loads from the internet.</div>

</div></body></html>"""
