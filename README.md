# Strava Top Speed

Small CLI for computing your highest speed across Strava activities, grouped by sport, from a Strava account export.

It reads `activities.csv` from a Strava export directory, then tries to verify speed from the exported GPS track file (`.gpx` or `.tcx`) using sustained movement over time.
It also verifies `.fit` and `.fit.gz` activities from their record-level speed samples when `fitdecode` is installed.

## What it does

- Auto-detects the newest `export_*` directory in the current folder
- Reads `activities.csv` from the Strava export
- Verifies speed from exported `gpx/tcx` tracks when available
- Verifies speed from exported `fit/fit.gz` files when available
- Uses lazy verification for single-sport runs like `--sports Ride`: it verifies only as many top summary-speed candidates as needed to produce a correct top N
- Computes your overall fastest activity and the fastest activity per sport
- Applies a conservative default glitch filter using sport-specific max mph caps
- Prints excluded glitch candidates so you can inspect what got removed
- Optionally writes the full ranked result set to JSON

## Expected export layout

The export directory should contain:

```text
export_.../
  activities.csv
  activities/
  ...
```

Your existing `export_8068444_may_2024` directory already matches this structure.

## Usage

Install the FIT parser dependency once:

```bash
python3 -m pip install --target .vendor -r requirements.txt
```

Run against the newest local export directory:

```bash
python3 strava_top_speed.py
```

Point at a specific export directory:

```bash
python3 strava_top_speed.py --export-dir export_8068444_may_2024
```

Restrict to some sports:

```bash
python3 strava_top_speed.py --sports Ride,Run,Alpine Ski
```

Restrict the date range:

```bash
python3 strava_top_speed.py --after 2023-01-01 --before 2026-03-05
```

Write full output to JSON:

```bash
python3 strava_top_speed.py --json-out results/top-speeds.json
```

Change the sustained verification window:

```bash
python3 strava_top_speed.py --window-seconds 20
```

Exclude likely old Strava-app GPX recordings using an export heuristic:

```bash
python3 strava_top_speed.py --exclude-likely-strava-app
```

Disable the glitch filter:

```bash
python3 strava_top_speed.py --disable-glitch-filter
```

Override a sport cap:

```bash
python3 strava_top_speed.py --max-mph Hike=15 --max-mph Run=25
```

## Default glitch caps

- `Alpine Ski`: 90 mph
- `Backcountry Ski`: 35 mph
- `Hike`: 12 mph
- `Kayaking`: 20 mph
- `Ride`: 65 mph
- `Run`: 22 mph
- `Snowshoe`: 10 mph
- `Swim`: 8 mph
- `Walk`: 8 mph
- `Workout`: 0 mph

Sports not listed are left unfiltered.

## Notes

- This version does not use the Strava API at all.
- For `gpx/tcx` activities, it computes a sustained speed from the track instead of trusting Strava's summary `Max Speed`.
- For `fit/fit.gz` activities, it computes a sustained speed from record-level speed samples when `fitdecode` is available.
- For single-sport runs, it starts from `activities.csv` summary speeds and stops verifying once remaining candidates cannot break into the current top N.
- It excludes GPX/TCX activities entirely when the track has too many obvious spike segments.
- `--exclude-likely-strava-app` is heuristic only. In this export, it means `Filename` is GPX and `From Upload` is empty.
- The glitch filter is still a pragmatic safeguard on top of that verification step.
- When your 2026 export finishes downloading, the script should automatically pick it if it is the newest `export_*` directory here.
