# Strava Top Speed

Small CLI for computing your highest speed across Strava activities, grouped by sport, from a Strava account export.

It reads `activities.csv` from a Strava export directory and uses Strava's exported `Max Speed` field for each activity.

## What it does

- Auto-detects the newest `export_*` directory in the current folder
- Reads `activities.csv` from the Strava export
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
- It relies on the `Max Speed` values already exported by Strava in `activities.csv`.
- The glitch filter is a pragmatic safeguard, not a physics-based reconstruction of your actual top speed.
- When your 2026 export finishes downloading, the script should automatically pick it if it is the newest `export_*` directory here.
