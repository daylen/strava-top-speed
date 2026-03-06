#!/usr/bin/env python3
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

DATE_FORMAT = "%b %d, %Y, %I:%M:%S %p"
DEFAULT_MAX_MPH_BY_SPORT = {
    "alpine ski": 90.0,
    "backcountry ski": 35.0,
    "hike": 12.0,
    "kayaking": 20.0,
    "ride": 65.0,
    "run": 22.0,
    "snowshoe": 10.0,
    "swim": 8.0,
    "walk": 8.0,
    "workout": 0.0,
}


class ExportError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute your top Strava speeds by sport from a Strava export directory."
    )
    parser.add_argument(
        "--export-dir",
        help="Path to a Strava export directory containing activities.csv. Defaults to the newest export_* directory in the current folder.",
    )
    parser.add_argument("--after", help="Only include activities on or after YYYY-MM-DD.")
    parser.add_argument("--before", help="Only include activities on or before YYYY-MM-DD.")
    parser.add_argument(
        "--sports",
        help="Comma-separated Activity Type filters, for example Ride,Run,Alpine Ski.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="How many activities to show in the ranking output. Default: 10.",
    )
    parser.add_argument(
        "--json-out",
        help="Write the full computed results to a JSON file.",
    )
    parser.add_argument(
        "--disable-glitch-filter",
        action="store_true",
        help="Do not exclude implausible speeds using sport-specific max mph caps.",
    )
    parser.add_argument(
        "--show-excluded",
        type=int,
        default=10,
        help="How many excluded glitch candidates to print. Default: 10.",
    )
    parser.add_argument(
        "--max-mph",
        action="append",
        default=[],
        metavar="SPORT=MPH",
        help="Override a sport cap, for example --max-mph Hike=15 --max-mph Run=25.",
    )
    return parser.parse_args()


def find_export_dir(explicit: Optional[str]) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser()
        if not (candidate / "activities.csv").exists():
            raise ExportError(f"No activities.csv found in export directory: {candidate}")
        return candidate

    candidates = [
        path for path in Path.cwd().iterdir()
        if path.is_dir() and path.name.startswith("export_") and (path / "activities.csv").exists()
    ]
    if not candidates:
        raise ExportError("No Strava export directory found. Expected an export_* folder with activities.csv.")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def parse_filter_date(value: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.replace(tzinfo=timezone.utc)


def parse_activity_date(raw: str) -> datetime:
    return datetime.strptime(raw, DATE_FORMAT).replace(tzinfo=timezone.utc)


def meters_per_second_to_mph(value: float) -> float:
    return value * 2.2369362920544


def meters_per_second_to_kmh(value: float) -> float:
    return value * 3.6


def mph_to_meters_per_second(value: float) -> float:
    return value / 2.2369362920544


def format_speed(value_mps: float) -> str:
    return f"{meters_per_second_to_mph(value_mps):6.2f} mph | {meters_per_second_to_kmh(value_mps):6.2f} km/h | {value_mps:6.2f} m/s"


def normalize_sport_filters(raw: Optional[str]) -> Optional[Set[str]]:
    if not raw:
        return None
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def parse_optional_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return float(value)


def parse_max_mph_overrides(values: List[str]) -> Dict[str, float]:
    overrides: Dict[str, float] = {}
    for value in values:
        if "=" not in value:
            raise ExportError(f"Invalid --max-mph value: {value}. Expected SPORT=MPH.")
        sport, raw_mph = value.split("=", 1)
        sport = sport.strip().lower()
        if not sport:
            raise ExportError(f"Invalid --max-mph value: {value}. Sport name is empty.")
        overrides[sport] = float(raw_mph)
    return overrides


def build_glitch_caps(disabled: bool, overrides: Dict[str, float]) -> Dict[str, float]:
    if disabled:
        return {}
    caps = dict(DEFAULT_MAX_MPH_BY_SPORT)
    caps.update(overrides)
    return caps


def is_glitch_candidate(result: Dict[str, Any], caps_mph: Dict[str, float]) -> Tuple[bool, Optional[str]]:
    sport = result["sport_type"].lower()
    cap_mph = caps_mph.get(sport)
    if cap_mph is None:
        return False, None
    speed_mph = result["top_speed_mph"]
    if speed_mph <= cap_mph:
        return False, None
    return True, f"{speed_mph:.2f} mph exceeds {sport} cap of {cap_mph:.2f} mph"


def load_results(
    export_dir: Path,
    after: Optional[datetime],
    before: Optional[datetime],
    sport_filters: Optional[Set[str]],
    caps_mph: Dict[str, float],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    activities_csv = export_dir / "activities.csv"
    results: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []

    with activities_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_date = row.get("Activity Date")
            raw_speed = row.get("Max Speed")
            sport_type = (row.get("Activity Type") or "Unknown").strip()
            if not raw_date or not raw_speed:
                continue

            activity_date = parse_activity_date(raw_date)
            if after and activity_date < after:
                continue
            if before and activity_date > before:
                continue
            if sport_filters and sport_type.lower() not in sport_filters:
                continue

            top_speed_mps = float(raw_speed)
            activity_id = row.get("Activity ID") or ""
            result = {
                "id": int(activity_id) if activity_id.isdigit() else activity_id,
                "name": row.get("Activity Name") or "Untitled Activity",
                "sport_type": sport_type,
                "start_date": activity_date.isoformat().replace("+00:00", "Z"),
                "distance_m": parse_optional_float(row.get("Distance")),
                "moving_time_s": parse_optional_float(row.get("Moving Time")),
                "elapsed_time_s": parse_optional_float(row.get("Elapsed Time")),
                "top_speed_mps": top_speed_mps,
                "top_speed_mph": meters_per_second_to_mph(top_speed_mps),
                "top_speed_kmh": meters_per_second_to_kmh(top_speed_mps),
                "source": "activities.csv:max_speed",
                "file": row.get("Filename") or "",
            }

            is_glitch, reason = is_glitch_candidate(result, caps_mph)
            if is_glitch:
                result["excluded_reason"] = reason
                excluded.append(result)
                continue

            results.append(result)

    return results, excluded


def summarize(results: List[Dict[str, Any]], top_n: int) -> str:
    if not results:
        return "No matching activities found."

    lines: List[str] = []
    overall = max(results, key=lambda item: item["top_speed_mps"])
    lines.append("Overall top speed")
    lines.append(f"  {format_speed(overall['top_speed_mps'])}")
    lines.append(
        f"  {overall['sport_type']} | {overall['name']} | {overall['start_date']} | activity {overall['id']} | source={overall['source']}"
    )
    lines.append("")
    lines.append("Top speed by sport")

    by_sport: Dict[str, Dict[str, Any]] = {}
    for result in results:
        sport = result["sport_type"]
        if sport not in by_sport or result["top_speed_mps"] > by_sport[sport]["top_speed_mps"]:
            by_sport[sport] = result

    for sport in sorted(by_sport):
        best = by_sport[sport]
        lines.append(
            f"  {sport:20} {meters_per_second_to_mph(best['top_speed_mps']):6.2f} mph | {best['name']} | {best['start_date']}"
        )

    lines.append("")
    lines.append(f"Top {min(top_n, len(results))} activities")
    ranked = sorted(results, key=lambda item: item["top_speed_mps"], reverse=True)[:top_n]
    for index, result in enumerate(ranked, start=1):
        lines.append(
            f"  {index:>2}. {result['sport_type']:20} {meters_per_second_to_mph(result['top_speed_mps']):6.2f} mph | {result['name']} | {result['start_date']} | source={result['source']}"
        )
    return "\n".join(lines)


def summarize_excluded(excluded: List[Dict[str, Any]], limit: int) -> str:
    if not excluded or limit <= 0:
        return ""
    lines = ["Excluded glitch candidates"]
    ranked = sorted(excluded, key=lambda item: item["top_speed_mps"], reverse=True)[:limit]
    for index, result in enumerate(ranked, start=1):
        lines.append(
            f"  {index:>2}. {result['sport_type']:20} {result['top_speed_mph']:6.2f} mph | {result['name']} | {result['start_date']} | {result['excluded_reason']}"
        )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()

    try:
        export_dir = find_export_dir(args.export_dir)
        after = parse_filter_date(args.after)
        before = parse_filter_date(args.before, end_of_day=True)
        sport_filters = normalize_sport_filters(args.sports)
        max_mph_overrides = parse_max_mph_overrides(args.max_mph)
        glitch_caps = build_glitch_caps(args.disable_glitch_filter, max_mph_overrides)
        results, excluded = load_results(export_dir, after, before, sport_filters, glitch_caps)
    except (ExportError, FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Using export: {export_dir}")
    print(f"Matched {len(results)} activities.")
    if glitch_caps:
        print(f"Excluded {len(excluded)} glitch candidates using sport-specific max mph caps.")
    print()
    print(summarize(results, args.top))

    excluded_summary = summarize_excluded(excluded, args.show_excluded)
    if excluded_summary:
        print()
        print(excluded_summary)

    if args.json_out:
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "export_dir": str(export_dir),
            "matched_activities": len(results),
            "excluded_activities": len(excluded),
            "glitch_caps_mph": glitch_caps,
            "results": sorted(results, key=lambda item: item["top_speed_mps"], reverse=True),
            "excluded": sorted(excluded, key=lambda item: item["top_speed_mps"], reverse=True),
        }
        output_path = Path(args.json_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        print()
        print(f"Wrote JSON results to {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
