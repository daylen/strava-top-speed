#!/usr/bin/env python3
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

VENDOR_DIR = Path(__file__).resolve().parent / '.vendor'
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont  # type: ignore

WIDTH = 1080
HEIGHT = 1920
PADDING = 64
OUTPUT_DIR = Path('results/story-cards-vertical')

TITLE_FONT = '/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf'
SANS_FONT = '/System/Library/Fonts/Helvetica.ttc'
SERIF_FONT = '/System/Library/Fonts/Supplemental/BigCaslon.ttf'
MONO_FONT = '/System/Library/Fonts/Supplemental/Andale Mono.ttf'

PALETTE = {
    'ink': (248, 244, 236),
    'shadow': (10, 13, 20),
    'orange': (246, 114, 67),
    'red': (200, 63, 40),
    'teal': (66, 167, 167),
    'gold': (236, 185, 76),
    'panel': (14, 19, 31, 188),
    'panel_soft': (20, 26, 40, 150),
}


def font(path: str, size: int):
    return ImageFont.truetype(path, size=size)


def load_stats(path: Path) -> Dict:
    return json.loads(path.read_text(encoding='utf-8'))


def read_activity_rows(export_dir: Path) -> Dict[str, Dict[str, str]]:
    rows: Dict[str, Dict[str, str]] = {}
    with (export_dir / 'activities.csv').open(encoding='utf-8-sig') as handle:
        for row in csv.DictReader(handle):
            rows[row['Activity ID']] = row
    return rows


def by_sport(results: Sequence[Dict]) -> List[Dict]:
    best: Dict[str, Dict] = {}
    for result in results:
        sport = result['sport_type']
        if sport not in best or result['top_speed_mph'] > best[sport]['top_speed_mph']:
            best[sport] = result
    return sorted(best.values(), key=lambda item: item['top_speed_mph'], reverse=True)


def first_for_sport(results: Sequence[Dict], sport: str) -> Dict:
    candidates = [r for r in results if r['sport_type'] == sport]
    return max(candidates, key=lambda item: item['top_speed_mph'])


def short_date(iso_text: str) -> str:
    return iso_text[:10]


def month_year(iso_text: str) -> str:
    year, month, _ = iso_text[:10].split('-')
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    return f"{months[int(month) - 1]} {year}"


def miles_text(distance_m: str) -> str:
    try:
        return f"{float(distance_m) / 1609.344:.1f} mi"
    except Exception:
        return ''


def feet_text(elev_m: str) -> str:
    try:
        return f"{float(elev_m) * 3.28084:,.0f} ft vert"
    except Exception:
        return ''


def pace_text(mph: float) -> str:
    if mph <= 0:
        return '--:-- /mi'
    total_minutes = 60.0 / mph
    minutes = int(total_minutes)
    seconds = int(round((total_minutes - minutes) * 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d} /mi"


def wrap(draw: ImageDraw.ImageDraw, text: str, text_font, width: int) -> List[str]:
    words = text.split()
    lines: List[str] = []
    current = ''
    for word in words:
        trial = word if not current else current + ' ' + word
        if draw.textbbox((0, 0), trial, font=text_font)[2] <= width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def resolve_media_path(export_dir: Path, activity_rows: Dict[str, Dict[str, str]], activity_id: str, index: int = 0) -> Optional[Path]:
    row = activity_rows.get(str(activity_id))
    if not row:
        return None
    media = [entry.strip() for entry in (row.get('Media') or '').split('|') if entry.strip()]
    if not media:
        return None
    if index >= len(media):
        index = 0
    path = export_dir / media[index]
    return path if path.exists() else None


def cover_image(path: Optional[Path]) -> Image.Image:
    if path and path.exists():
        image = Image.open(path).convert('RGB')
    else:
        image = Image.new('RGB', (WIDTH, HEIGHT), (30, 42, 58))
    scale = max(WIDTH / image.width, HEIGHT / image.height)
    resized = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - WIDTH) // 2
    top = (resized.height - HEIGHT) // 2
    cropped = resized.crop((left, top, left + WIDTH, top + HEIGHT))
    cropped = ImageEnhance.Contrast(cropped).enhance(1.05)
    cropped = ImageEnhance.Color(cropped).enhance(0.94)
    return cropped


def add_photo_treatment(image: Image.Image, accent: Tuple[int, int, int]) -> Image.Image:
    overlay = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for y in range(HEIGHT):
        alpha = int(220 * (y / HEIGHT) ** 1.6)
        d.line((0, y, WIDTH, y), fill=(8, 12, 18, alpha))
    d.rectangle((0, 0, WIDTH, 300), fill=(12, 16, 24, 70))
    d.ellipse((760, -70, 1210, 380), fill=accent + (110,))
    d.polygon([(0, HEIGHT), (0, 1460), (360, 1300), (WIDTH, 1620), (WIDTH, HEIGHT)], fill=(accent[0], accent[1], accent[2], 92))
    merged = Image.alpha_composite(image.convert('RGBA'), overlay)
    return merged.convert('RGB')


def add_story_frame(draw: ImageDraw.ImageDraw) -> None:
    draw.rounded_rectangle((22, 22, WIDTH - 22, HEIGHT - 22), radius=34, outline=(255, 255, 255, 80), width=2)


def card_overall(stats: Dict, activity_rows: Dict[str, Dict[str, str]]) -> Image.Image:
    overall = stats['results'][0]
    export_dir = Path(stats['export_dir'])
    image = add_photo_treatment(cover_image(resolve_media_path(export_dir, activity_rows, str(overall['id']))), PALETTE['gold'])
    draw = ImageDraw.Draw(image, 'RGBA')
    add_story_frame(draw)

    draw.rounded_rectangle((PADDING, 126, PADDING + 168, 138), radius=6, fill=PALETTE['orange'])
    draw.text((PADDING, 120), 'FASTEST', font=font(TITLE_FONT, 134), fill=PALETTE['ink'])
    draw.text((PADDING, 238), 'THING I DID', font=font(TITLE_FONT, 120), fill=PALETTE['ink'])

    draw.text((PADDING, 1110), f"{overall['top_speed_mph']:.2f}", font=font(TITLE_FONT, 250), fill=PALETTE['ink'])
    draw.text((PADDING + 10, 1342), 'MPH', font=font(SANS_FONT, 42), fill=PALETTE['orange'])

    panel_top = 1390
    draw.rounded_rectangle((PADDING, panel_top, WIDTH - PADDING, 1760), radius=34, fill=PALETTE['panel'])
    draw.text((PADDING + 34, panel_top + 34), overall['sport_type'].upper(), font=font(SANS_FONT, 28), fill=PALETTE['gold'])
    draw.text((PADDING + 34, panel_top + 108), month_year(overall['start_date']), font=font(TITLE_FONT, 72), fill=PALETTE['ink'])
    return image


def card_ride(stats: Dict, activity_rows: Dict[str, Dict[str, str]]) -> Image.Image:
    ride = first_for_sport(stats['results'], 'Ride')
    export_dir = Path(stats['export_dir'])
    image = add_photo_treatment(cover_image(resolve_media_path(export_dir, activity_rows, str(ride['id']))), PALETTE['orange'])
    draw = ImageDraw.Draw(image, 'RGBA')
    add_story_frame(draw)

    draw.rounded_rectangle((PADDING, 126, PADDING + 154, 138), radius=6, fill=PALETTE['gold'])
    draw.rounded_rectangle((PADDING, 126, PADDING + 154, 138), radius=6, fill=PALETTE['gold'])
    draw.text((PADDING, 168), 'FASTEST', font=font(TITLE_FONT, 122), fill=PALETTE['ink'])
    draw.text((PADDING, 278), 'VERIFIED RIDE', font=font(TITLE_FONT, 112), fill=PALETTE['ink'])

    draw.rounded_rectangle((PADDING, 1000, WIDTH - PADDING, 1338), radius=34, fill=PALETTE['panel_soft'])
    draw.text((PADDING + 34, 1036), f"{ride['top_speed_mph']:.2f}", font=font(TITLE_FONT, 220), fill=PALETTE['ink'])
    draw.text((PADDING + 44, 1242), 'MPH', font=font(SANS_FONT, 40), fill=PALETTE['orange'])
    draw.text((PADDING + 310, 1240), short_date(ride['start_date']), font=font(SANS_FONT, 36), fill=(235, 238, 243))

    row = activity_rows[str(ride['id'])]
    ride_stats = ' / '.join(part for part in [miles_text(row.get('Distance', '')), feet_text(row.get('Elevation Gain', ''))] if part)
    draw.rounded_rectangle((PADDING, 1390, WIDTH - PADDING, 1468), radius=20, fill=(10, 12, 18, 120))
    draw.text((PADDING + 22, 1442), ride_stats, font=font(SANS_FONT, 34), fill=PALETTE['ink'], anchor='lm')

    title_lines = wrap(draw, ride['name'], font(SERIF_FONT, 52), WIDTH - 2 * PADDING)
    ty = 1536
    for line in title_lines[:3]:
        draw.text((PADDING, ty), line, font=font(SERIF_FONT, 50), fill=PALETTE['ink'])
        ty += 56
    draw.text((PADDING, HEIGHT - 70), ride['strava_url'].replace('https://', ''), font=font(MONO_FONT, 18), fill=(220, 224, 230, 190))
    return image


def card_mix(stats: Dict, activity_rows: Dict[str, Dict[str, str]]) -> Image.Image:
    sports = by_sport(stats['results'])
    run = first_for_sport(stats['results'], 'Run')
    export_dir = Path(stats['export_dir'])
    image = add_photo_treatment(cover_image(export_dir / 'media/DADB4C40-4D06-4006-A991-C56D3C89C602.jpg'), PALETTE['teal'])
    draw = ImageDraw.Draw(image, 'RGBA')
    add_story_frame(draw)

    card_y = 980
    box_h = 146
    giant_font = font(TITLE_FONT, 68)
    metric_font = font(TITLE_FONT, 50)
    for idx, item in enumerate(sports[:5]):
        top = card_y + idx * (box_h + 18)
        draw.rounded_rectangle((PADDING, top, WIDTH - PADDING, top + box_h), radius=28, fill=(12, 17, 28, 132))
        ghost = PALETTE['gold'] + (80,) if idx == 0 else (255, 255, 255, 54)
        draw.text((PADDING + 20, top + 34), item['sport_type'].upper(), font=giant_font, fill=ghost)
        metric = pace_text(item['top_speed_mph']) if item['sport_type'] == 'Run' else f"{item['top_speed_mph']:.2f} mph"
        draw.text((WIDTH - PADDING - 26, top + 44), metric, anchor='ra', font=metric_font, fill=PALETTE['ink'])

    return image


def main() -> int:
    stats_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/tmp/strava_stats.json')
    stats = load_stats(stats_path)
    activity_rows = read_activity_rows(Path(stats['export_dir']))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cards = {
        '01-overall-fastest-story.png': card_overall(stats, activity_rows),
        '02-fastest-ride-story.png': card_ride(stats, activity_rows),
        '03-sport-spread-story.png': card_mix(stats, activity_rows),
    }
    for name, image in cards.items():
        image.save(OUTPUT_DIR / name, quality=95)
        print(OUTPUT_DIR / name)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
