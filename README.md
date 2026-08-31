# Run analysis from your own watch data

Rebuilds the run-analysis screen from a paid tracking app using the activity
data your own Garmin watch already recorded — and then does the part the app
will not do, which is tell you whether the zones it grades you against are
actually yours.

## Run it

```
python3 bootstrap.py
```

That is the whole thing. It sets up an isolated environment, installs the
open-source [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect)
library by cyberjunky, asks you to sign in to Garmin once, downloads 180 days
of activities, works out your real zones, writes a self-contained HTML page and
opens it.

Options: `--days 365` for a longer window, `--no-open` to skip the browser.

## Privacy

- **Everything stays on this computer.** Nothing is uploaded anywhere.
- **Read-only.** Every Garmin call goes through an allowlist in
  `garmin_run/fetch.py`; anything that writes, uploads or deletes raises an
  error rather than running. Your Garmin account is never modified.
- **Your password is never stored.** It is read with `getpass` (not echoed),
  passed straight to Garmin over HTTPS, and dropped. It is never written to a
  file, never put in an environment variable, and never printed.
- A session token is saved to `~/.garminconnect` with owner-only permissions so
  you are not asked to log in twice. Delete that folder to sign out.
- Downloaded data is cached in `cache/` so re-runs cost no API calls. Delete
  that folder to remove it.

## What it actually does

**Reads the descriptors before the numbers.** Garmin's per-second stream comes
back as bare arrays with a separate `metricDescriptors` list saying which
column is which. The order is not fixed and not documented. This reads the
descriptors first and builds the mapping from them.

**Verifies the mapping instead of trusting it.** The mapped heart-rate column
is averaged and compared against the `averageHR` the activity reports in its
own summary; same for speed against `averageSpeed` and for total distance. If
they disagree, it stops and says so rather than drawing a convincing chart that
is wrong.

**Filters optical artifacts before taking any maximum.** A wrist sensor locks
onto cadence and throws lone samples far above anything real. A sample is
discarded only when it jumps more than 15 bpm from *both* neighbours while
those neighbours agree with each other, so a genuine surge is left alone. The
count thrown out is reported. An unfiltered max is the single most common way
this whole thing goes wrong, because every zone is built on top of it.

**Computes time-in-zone from the per-second stream, never from splits.** A mile
split during an interval session blends the reps with the recoveries, which
makes any zone percentage built on splits meaningless.

**Derives your zones from efforts you actually ran** — not 220-minus-age, and
not a number the app assumed. Max HR is the highest clean sustained value, with
the activity and date it came from. Threshold HR is the hardest *steady*
20-minute block, sanity-checked against your max, because the highest 20
minutes in your history usually sits inside a short race and a short race is
run above threshold. Threshold pace is derived from your best sustained
efforts, then checked out loud for heat, for negative splits, and for whether
your heart rate actually got near max — and cross-checked against a second,
independent estimate: the pace you actually held at threshold heart rate.

**Puts the app's numbers next to yours** and says plainly where they agree,
where the easy band sits under each, and whether the app assumes you are faster
or slower than the evidence supports.

## Layout

| File | What it does |
|---|---|
| `bootstrap.py` | Sets everything up and launches. The only file you run. |
| `run.py` | Walks through the steps and prints the reasoning. |
| `garmin_run/fetch.py` | Login, the read-only allowlist, and the disk cache. |
| `garmin_run/streams.py` | Descriptor mapping, verification, artifact filtering. |
| `garmin_run/zones.py` | Max HR, threshold HR, threshold pace, time-in-zone. |
| `garmin_run/appmodel.py` | Reads Garmin's own stated basis and compares. |
| `garmin_run/report.py` | Builds the self-contained HTML page. |
| `garmin_run/util.py` | Units and window maths. Every conversion lives here. |

## Models used

Stated openly so they can be argued with, which is the entire point:

- Pace from speed: `26.8224 / speed_in_m_per_s` = minutes per mile.
- Efforts are normalised to a 60-minute equivalent with the Riegel exponent
  1.06. Threshold pace is the pace you could hold for an hour.
- Heart-rate zones are Friel-derived percentages of threshold HR; pace zones
  are Daniels-style multiples of threshold pace. The easy ceiling is 1.18x
  threshold pace.
- Heat correction is a published piecewise table on temperature, with dew point
  added on top. All corrections together are capped at 4% so a stack of
  estimates cannot outweigh the measurement underneath them.
