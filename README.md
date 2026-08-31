# Run analysis from your own watch data

Rebuilds the run-analysis screen from a paid tracking app using the activity
data your own Garmin watch already recorded — and then does the part the app
will not do, which is tell you whether the zones it grades you against are
actually yours.

## Run it

```
python3 bootstrap.py
```

**Needs Python 3.10 or newer.** The Garmin library dropped support for older
versions; on anything earlier, pip can only offer a 2023 release that is
missing the endpoints this reads. `bootstrap.py` searches your machine for a
suitable Python (macOS usually has several, and the `python3` on your PATH is
often the oldest), uses the newest one it finds, and tells you exactly what to
install if there isn't one.

The page has three tabs. **Activities** lists everything you downloaded, filterable by sport; tap any one to open its lap chart, splits and time in zone. **Your Zones** shows what was derived and the evidence behind it. **vs Garmin** puts those numbers against the ones Garmin grades you with.

That is the whole thing. It sets up an isolated environment, installs the
open-source [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect)
library by cyberjunky, asks you to sign in to Garmin once, downloads 180 days
of activities, works out your real zones, writes a self-contained HTML page and
opens it.

### Options

| Flag | What it does |
|---|---|
| `--list-types` | List every activity type in your history with counts, then stop. Start here if you want more than runs. |
| `--include running` | Default. Runs only. |
| `--include all` | Every activity: rides, walks, strength, swims, everything. |
| `--include running,cycling` | A comma list. Accepts groups (`running`, `cycling`, `walking`, `swimming`, `strength`) or raw Garmin type keys such as `strength_training`. |
| `--feature 12345678` | Put a specific activity on the page instead of the auto-picked one. Ids are shown while downloading. |
| `--days 365` | A longer window. |
| `--no-open` | Skip opening the browser. |

**What including other sports does and does not change.** Max heart rate is a
whole-body ceiling, so every sport you include counts as evidence for it.
Threshold *pace* is only ever derived from running — minutes per mile means
nothing on a bike — and threshold heart rate likewise, because it differs by
sport. The tool says which pool each number came from.

## Keeping it up to date automatically

The page is a snapshot, not a live view. Refreshing the browser will not
change it; re-running the tool regenerates it. To have that happen on its own:

```
python3 schedule.py install              # every 4 hours
python3 schedule.py install --at 06:00   # or a daily time
python3 schedule.py status               # installed? did the last run work?
python3 schedule.py uninstall            # remove it entirely
```

On macOS this installs a `launchd` agent in `~/Library/LaunchAgents`. It runs
once immediately, then on your chosen schedule, and catches up after your Mac
wakes if a scheduled time was missed while it slept. On Linux it prints the
`crontab` line to add instead.

Put a link to the page on your Desktop so you never have to find the path:

```
python3 schedule.py shortcut
```

That is a symlink, not a copy. Every refresh overwrites the same file in place,
so the link always opens the current page; a copy would freeze whatever was
there the day you made it. The line under the title says how old the page is
in plain words, so a stale page cannot pass for a fresh one.

**The one way it fails.** Garmin eventually ends the saved session and asks for
a fresh login. A background job cannot answer a 2FA prompt, so it will start
failing quietly and your page will simply stop getting newer. That is what
`schedule.py status` is for: it reports the last run's exit code and says so
plainly. Running it by hand once restores the session and the schedule resumes.

Everything it runs is your local script. It sends nothing anywhere, and
removing it leaves your data, cache and pages untouched.

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
