#!/usr/bin/env python3
import argparse
import csv
import gzip
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

VENDOR_DIR = Path(__file__).resolve().parent / ".vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))

try:
    import fitdecode  # type: ignore
except ImportError:
    fitdecode = None

DATE_FORMAT = "%b %d, %Y, %I:%M:%S %p"
STRAVA_ACTIVITY_URL = "https://www.strava.com/activities/{activity_id}"
DEFAULT_MAX_MPH_BY_SPORT = {
    "alpine ski": 90.0,
    "backcountry ski": 35.0,
    "e-bike ride": 40.0,
    "hike": 12.0,
    "kayaking": 20.0,
    "ride": 65.0,
    "run": 22.0,
    "snowshoe": 10.0,
    "swim": 8.0,
    "walk": 8.0,
    "workout": 0.0,
}
TRACK_SPIKE_DELTA_MPH = 10.0
TRACK_SPIKE_RATIO_LIMIT = 0.0008
TRACK_SPIKE_COUNT_LIMIT = 6
WINDOW_SPIKE_DELTA_MPH = 10.0
WINDOW_SPIKE_MIN_COUNT = 3
WINDOW_SPIKE_MAX_EXCESS_MPH = 20.0


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
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=15,
        help="Minimum sustained window for verified track speed. Default: 15 seconds.",
    )
    parser.add_argument(
        "--exclude-likely-strava-app",
        action="store_true",
        help="Exclude activities that look like old Strava-app recordings based on export heuristics: GPX file and empty From Upload.",
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


def parse_track_time(raw: str) -> datetime:
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw).astimezone(timezone.utc)


def meters_per_second_to_mph(value: float) -> float:
    return value * 2.2369362920544


def meters_per_second_to_kmh(value: float) -> float:
    return value * 3.6


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


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * radius * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def open_text_maybe_gzip(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8-sig")
    return path.open("r", encoding="utf-8-sig")


def iter_gpx_points(path: Path) -> Iterable[Tuple[datetime, float, float]]:
    with open_text_maybe_gzip(path) as handle:
        root = ET.parse(handle).getroot()
    namespace = {"gpx": root.tag[root.tag.find("{") + 1: root.tag.find("}")]} if root.tag.startswith("{") else {}
    point_path = ".//gpx:trkpt" if namespace else ".//trkpt"
    time_path = "gpx:time" if namespace else "time"
    for trkpt in root.findall(point_path, namespace):
        lat_raw = trkpt.attrib.get("lat")
        lon_raw = trkpt.attrib.get("lon")
        time_node = trkpt.find(time_path, namespace)
        if not lat_raw or not lon_raw or time_node is None or not time_node.text:
            continue
        yield parse_track_time(time_node.text.strip()), float(lat_raw), float(lon_raw)


def iter_tcx_points(path: Path) -> Iterable[Tuple[datetime, float, float]]:
    with open_text_maybe_gzip(path) as handle:
        root = ET.parse(handle).getroot()
    namespace = {"tcx": root.tag[root.tag.find("{") + 1: root.tag.find("}")]} if root.tag.startswith("{") else {}
    trackpoint_path = ".//tcx:Trackpoint" if namespace else ".//Trackpoint"
    time_path = "tcx:Time" if namespace else "Time"
    position_path = "tcx:Position" if namespace else "Position"
    lat_path = "tcx:LatitudeDegrees" if namespace else "LatitudeDegrees"
    lon_path = "tcx:LongitudeDegrees" if namespace else "LongitudeDegrees"
    for trackpoint in root.findall(trackpoint_path, namespace):
        time_node = trackpoint.find(time_path, namespace)
        position = trackpoint.find(position_path, namespace)
        if time_node is None or position is None or not time_node.text:
            continue
        lat_node = position.find(lat_path, namespace)
        lon_node = position.find(lon_path, namespace)
        if lat_node is None or lon_node is None or not lat_node.text or not lon_node.text:
            continue
        yield parse_track_time(time_node.text.strip()), float(lat_node.text), float(lon_node.text)


def open_binary_maybe_gzip(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rb")
    return path.open("rb")


def iter_fit_speed_points(path: Path) -> Iterable[Tuple[datetime, float]]:
    if fitdecode is None:
        return
    with open_binary_maybe_gzip(path) as raw:
        with fitdecode.FitReader(raw) as fit:
            for frame in fit:
                if not isinstance(frame, fitdecode.FitDataMessage) or frame.name != "record":
                    continue
                values = {field.name: field.value for field in frame.fields}
                timestamp = values.get("timestamp")
                speed = values.get("enhanced_speed")
                if speed is None:
                    speed = values.get("speed")
                if timestamp is None or speed is None:
                    continue
                yield timestamp, float(speed)


def build_track_segments(points: Iterable[Tuple[datetime, float, float]]) -> List[Tuple[datetime, float, float]]:
    samples = list(points)
    if len(samples) < 2:
        return []

    segments: List[Tuple[datetime, float, float]] = []
    for index in range(1, len(samples)):
        prev_time, prev_lat, prev_lon = samples[index - 1]
        curr_time, curr_lat, curr_lon = samples[index]
        dt = (curr_time - prev_time).total_seconds()
        if dt <= 0:
            continue
        segment_distance = haversine_meters(prev_lat, prev_lon, curr_lat, curr_lon)
        segments.append((curr_time, segment_distance, segment_distance / dt))
    return segments


def compute_sustained_speed_from_segments(segments: List[Tuple[datetime, float, float]], window_seconds: int) -> Optional[float]:
    if len(segments) < 2:
        return None

    cumulative_distances = [0.0]
    times = [segments[0][0]]
    for time_value, distance_value, _ in segments[1:]:
        cumulative_distances.append(cumulative_distances[-1] + distance_value)
        times.append(time_value)

    best_speed = 0.0
    start_index = 0
    for end_index in range(1, len(times)):
        while start_index < end_index and (times[end_index] - times[start_index]).total_seconds() > window_seconds:
            start_index += 1
        candidate_start = start_index
        if candidate_start >= end_index:
            continue
        window_duration = (times[end_index] - times[candidate_start]).total_seconds()
        if window_duration <= 0:
            continue
        if window_duration < window_seconds:
            if candidate_start == 0:
                continue
            candidate_start -= 1
            window_duration = (times[end_index] - times[candidate_start]).total_seconds()
            if window_duration <= 0 or window_duration < window_seconds:
                continue
        window_distance = cumulative_distances[end_index] - cumulative_distances[candidate_start]
        best_speed = max(best_speed, window_distance / window_duration)
    return best_speed if best_speed > 0.0 else None


def find_best_window(segments: List[Tuple[datetime, float, float]], window_seconds: int) -> Optional[Tuple[float, int, int]]:
    if len(segments) < 2:
        return None

    cumulative_distances = [0.0]
    times = [segments[0][0]]
    for time_value, distance_value, _ in segments[1:]:
        cumulative_distances.append(cumulative_distances[-1] + distance_value)
        times.append(time_value)

    best_speed = 0.0
    best_start = None
    best_end = None
    start_index = 0
    for end_index in range(1, len(times)):
        while start_index < end_index and (times[end_index] - times[start_index]).total_seconds() > window_seconds:
            start_index += 1
        candidate_start = start_index
        if candidate_start >= end_index:
            continue
        window_duration = (times[end_index] - times[candidate_start]).total_seconds()
        if window_duration <= 0:
            continue
        if window_duration < window_seconds:
            if candidate_start == 0:
                continue
            candidate_start -= 1
            window_duration = (times[end_index] - times[candidate_start]).total_seconds()
            if window_duration <= 0 or window_duration < window_seconds:
                continue
        window_distance = cumulative_distances[end_index] - cumulative_distances[candidate_start]
        speed = window_distance / window_duration
        if speed > best_speed:
            best_speed = speed
            best_start = candidate_start
            best_end = end_index
    if best_start is None or best_end is None or best_speed <= 0.0:
        return None
    return best_speed, best_start, best_end


def compute_sustained_speed_from_points(points: Iterable[Tuple[datetime, float, float]], window_seconds: int) -> Optional[float]:
    segments = build_track_segments(points)
    return compute_sustained_speed_from_segments(segments, window_seconds)


def compute_sustained_speed_from_speed_points(points: Iterable[Tuple[datetime, float]], window_seconds: int) -> Optional[float]:
    samples = list(points)
    if len(samples) < 2:
        return None

    best_speed = 0.0
    start_index = 0
    area = 0.0

    for end_index in range(1, len(samples)):
        dt = (samples[end_index][0] - samples[end_index - 1][0]).total_seconds()
        if dt <= 0:
            continue
        area += samples[end_index - 1][1] * dt

        while start_index < end_index and (samples[end_index][0] - samples[start_index][0]).total_seconds() > window_seconds:
            remove_dt = (samples[start_index + 1][0] - samples[start_index][0]).total_seconds()
            if remove_dt > 0:
                area -= samples[start_index][1] * remove_dt
            start_index += 1

        duration = (samples[end_index][0] - samples[start_index][0]).total_seconds()
        if duration <= 0:
            continue
        if duration < window_seconds:
            continue
        best_speed = max(best_speed, area / duration)

    return best_speed if best_speed > 0.0 else None


def is_track_too_noisy(segments: List[Tuple[datetime, float, float]], summary_speed_mps: float) -> bool:
    if not segments:
        return False
    threshold_mph = meters_per_second_to_mph(summary_speed_mps) + TRACK_SPIKE_DELTA_MPH
    spike_count = 0
    for _, _, segment_speed_mps in segments:
        if meters_per_second_to_mph(segment_speed_mps) > threshold_mph:
            spike_count += 1
    return spike_count >= TRACK_SPIKE_COUNT_LIMIT or (spike_count / len(segments)) >= TRACK_SPIKE_RATIO_LIMIT


def is_best_window_too_noisy(segments: List[Tuple[datetime, float, float]], window_seconds: int) -> bool:
    best = find_best_window(segments, window_seconds)
    if best is None:
        return False
    best_speed_mps, start_index, end_index = best
    best_speed_mph = meters_per_second_to_mph(best_speed_mps)
    spike_threshold_mph = best_speed_mph + WINDOW_SPIKE_DELTA_MPH
    spike_count = 0
    max_excess_mph = 0.0
    for _, _, segment_speed_mps in segments[start_index:end_index + 1]:
        segment_speed_mph = meters_per_second_to_mph(segment_speed_mps)
        if segment_speed_mph > spike_threshold_mph:
            spike_count += 1
            max_excess_mph = max(max_excess_mph, segment_speed_mph - best_speed_mph)
    return spike_count >= WINDOW_SPIKE_MIN_COUNT and max_excess_mph >= WINDOW_SPIKE_MAX_EXCESS_MPH


def verify_speed(export_dir: Path, relative_file: str, summary_speed_mps: float, window_seconds: int) -> Tuple[float, str]:
    if not relative_file:
        return summary_speed_mps, "activities.csv:max_speed"
    activity_file = export_dir / relative_file
    if not activity_file.exists():
        return summary_speed_mps, "activities.csv:max_speed:file_missing"

    suffixes = activity_file.suffixes
    try:
        if ".gpx" in suffixes:
            segments = build_track_segments(iter_gpx_points(activity_file))
            if is_track_too_noisy(segments, summary_speed_mps) or is_best_window_too_noisy(segments, window_seconds):
                return summary_speed_mps, "excluded:gpx_track_noisy"
            verified = compute_sustained_speed_from_segments(segments, window_seconds)
            if verified is not None:
                return min(summary_speed_mps, verified), f"gpx:sustained_{window_seconds}s:capped_by_summary"
            return summary_speed_mps, "activities.csv:max_speed:gpx_unusable"
        if ".tcx" in suffixes:
            segments = build_track_segments(iter_tcx_points(activity_file))
            if is_track_too_noisy(segments, summary_speed_mps) or is_best_window_too_noisy(segments, window_seconds):
                return summary_speed_mps, "excluded:tcx_track_noisy"
            verified = compute_sustained_speed_from_segments(segments, window_seconds)
            if verified is not None:
                return min(summary_speed_mps, verified), f"tcx:sustained_{window_seconds}s:capped_by_summary"
            return summary_speed_mps, "activities.csv:max_speed:tcx_unusable"
        if ".fit" in suffixes:
            if fitdecode is None:
                return summary_speed_mps, "activities.csv:max_speed:fit_parser_missing"
            verified = compute_sustained_speed_from_speed_points(iter_fit_speed_points(activity_file), window_seconds)
            if verified is not None:
                return min(summary_speed_mps, verified), f"fit:sustained_{window_seconds}s:capped_by_summary"
            return summary_speed_mps, "activities.csv:max_speed:fit_unusable"
    except (ET.ParseError, UnicodeDecodeError, OSError, ValueError):
        return summary_speed_mps, "activities.csv:max_speed:track_parse_error"
    return summary_speed_mps, "activities.csv:max_speed:fit_unverified"


def is_glitch_candidate(result: Dict[str, Any], caps_mph: Dict[str, float]) -> Tuple[bool, Optional[str]]:
    sport = result["sport_type"].lower()
    cap_mph = caps_mph.get(sport)
    if cap_mph is None:
        return False, None
    speed_mph = result["top_speed_mph"]
    if speed_mph <= cap_mph:
        return False, None
    return True, f"{speed_mph:.2f} mph exceeds {sport} cap of {cap_mph:.2f} mph"


def is_likely_strava_app_activity(row: Dict[str, str]) -> bool:
    filename = (row.get("Filename") or "").lower()
    from_upload = (row.get("From Upload") or "").strip()
    return ".gpx" in filename and from_upload == ""


def build_result(row: Dict[str, str], activity_date: datetime, top_speed_mps: float, summary_speed_mps: float, source: str) -> Dict[str, Any]:
    activity_id = row.get("Activity ID") or ""
    return {
        "id": int(activity_id) if activity_id.isdigit() else activity_id,
        "name": row.get("Activity Name") or "Untitled Activity",
        "sport_type": (row.get("Activity Type") or "Unknown").strip(),
        "start_date": activity_date.isoformat().replace("+00:00", "Z"),
        "distance_m": parse_optional_float(row.get("Distance")),
        "moving_time_s": parse_optional_float(row.get("Moving Time")),
        "elapsed_time_s": parse_optional_float(row.get("Elapsed Time")),
        "top_speed_mps": top_speed_mps,
        "top_speed_mph": meters_per_second_to_mph(top_speed_mps),
        "top_speed_kmh": meters_per_second_to_kmh(top_speed_mps),
        "summary_speed_mps": summary_speed_mps,
        "summary_speed_mph": meters_per_second_to_mph(summary_speed_mps),
        "source": source,
        "file": row.get("Filename") or "",
        "strava_url": STRAVA_ACTIVITY_URL.format(activity_id=activity_id) if activity_id else "",
    }


def iter_candidate_rows(
    export_dir: Path,
    after: Optional[datetime],
    before: Optional[datetime],
    sport_filters: Optional[Set[str]],
    exclude_likely_strava_app: bool,
) -> Iterable[Dict[str, str]]:
    activities_csv = export_dir / "activities.csv"
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
            if exclude_likely_strava_app and is_likely_strava_app_activity(row):
                continue

            yield row


def load_results(
    export_dir: Path,
    after: Optional[datetime],
    before: Optional[datetime],
    sport_filters: Optional[Set[str]],
    caps_mph: Dict[str, float],
    window_seconds: int,
    exclude_likely_strava_app: bool,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    results: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []

    for row in iter_candidate_rows(export_dir, after, before, sport_filters, exclude_likely_strava_app):
        summary_speed_mps = float(row["Max Speed"])
        activity_date = parse_activity_date(row["Activity Date"])
        relative_file = row.get("Filename") or ""
        top_speed_mps, source = verify_speed(export_dir, relative_file, summary_speed_mps, window_seconds)
        result = build_result(row, activity_date, top_speed_mps, summary_speed_mps, source)

        if source.startswith("excluded:"):
            result["excluded_reason"] = source.split(":", 1)[1]
            excluded.append(result)
            continue

        is_glitch, reason = is_glitch_candidate(result, caps_mph)
        if is_glitch:
            result["excluded_reason"] = reason
            excluded.append(result)
            continue

        results.append(result)

    return results, excluded


def load_results_lazy_single_sport(
    export_dir: Path,
    after: Optional[datetime],
    before: Optional[datetime],
    sport_filters: Set[str],
    caps_mph: Dict[str, float],
    window_seconds: int,
    exclude_likely_strava_app: bool,
    top_n: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, int]:
    candidates = list(iter_candidate_rows(export_dir, after, before, sport_filters, exclude_likely_strava_app))
    candidates.sort(key=lambda row: float(row["Max Speed"]), reverse=True)

    results: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    scanned = 0

    for row in candidates:
        scanned += 1
        summary_speed_mps = float(row["Max Speed"])
        activity_date = parse_activity_date(row["Activity Date"])
        relative_file = row.get("Filename") or ""
        top_speed_mps, source = verify_speed(export_dir, relative_file, summary_speed_mps, window_seconds)
        result = build_result(row, activity_date, top_speed_mps, summary_speed_mps, source)

        if source.startswith("excluded:"):
            result["excluded_reason"] = source.split(":", 1)[1]
            excluded.append(result)
        else:
            is_glitch, reason = is_glitch_candidate(result, caps_mph)
            if is_glitch:
                result["excluded_reason"] = reason
                excluded.append(result)
            else:
                results.append(result)
                results.sort(key=lambda item: item["top_speed_mps"], reverse=True)

        if len(results) < top_n:
            continue
        threshold = results[top_n - 1]["top_speed_mps"]
        next_summary_mps = float(candidates[scanned]["Max Speed"]) if scanned < len(candidates) else None
        if next_summary_mps is None or next_summary_mps <= threshold:
            return results[:top_n], excluded, scanned, len(candidates)

    return results[:top_n], excluded, scanned, len(candidates)


def summarize(results: List[Dict[str, Any]], top_n: int) -> str:
    if not results:
        return "No matching activities found."

    lines: List[str] = []
    overall = max(results, key=lambda item: item["top_speed_mps"])
    lines.append("Overall top speed")
    lines.append(f"  {format_speed(overall['top_speed_mps'])}")
    lines.append(
        f"  {overall['sport_type']} | {overall['name']} | {overall['start_date']} | activity {overall['id']} | {overall['strava_url']} | source={overall['source']}"
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
            f"  {sport:20} {meters_per_second_to_mph(best['top_speed_mps']):6.2f} mph | {best['name']} | {best['start_date']} | {best['strava_url']}"
        )

    lines.append("")
    lines.append(f"Top {min(top_n, len(results))} activities")
    ranked = sorted(results, key=lambda item: item["top_speed_mps"], reverse=True)[:top_n]
    for index, result in enumerate(ranked, start=1):
        lines.append(
            f"  {index:>2}. {result['sport_type']:20} {result['top_speed_mph']:6.2f} mph | {result['name']} | {result['start_date']} | {result['strava_url']} | source={result['source']}"
        )
    return "\n".join(lines)


def summarize_excluded(excluded: List[Dict[str, Any]], limit: int) -> str:
    if not excluded or limit <= 0:
        return ""
    lines = ["Excluded glitch candidates"]
    ranked = sorted(excluded, key=lambda item: item["top_speed_mps"], reverse=True)[:limit]
    for index, result in enumerate(ranked, start=1):
        lines.append(
            f"  {index:>2}. {result['sport_type']:20} {result['top_speed_mph']:6.2f} mph | {result['name']} | {result['start_date']} | {result['strava_url']} | {result['excluded_reason']}"
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
        used_lazy_verification = bool(sport_filters and len(sport_filters) == 1)
        scanned_candidates = None
        total_candidates = None
        if used_lazy_verification:
            results, excluded, scanned_candidates, total_candidates = load_results_lazy_single_sport(
                export_dir,
                after,
                before,
                sport_filters,
                glitch_caps,
                args.window_seconds,
                args.exclude_likely_strava_app,
                args.top,
            )
        else:
            results, excluded = load_results(
                export_dir,
                after,
                before,
                sport_filters,
                glitch_caps,
                args.window_seconds,
                args.exclude_likely_strava_app,
            )
    except (ExportError, FileNotFoundError, ValueError, ET.ParseError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Using export: {export_dir}")
    print(f"Matched {len(results)} activities.")
    if used_lazy_verification:
        print(f"Scanned {scanned_candidates} of {total_candidates} candidates using lazy verification.")
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
            "window_seconds": args.window_seconds,
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
