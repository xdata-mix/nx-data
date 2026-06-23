#!/usr/bin/env python3
"""refresh_arte.py — génère data-replay-arte.m3u (Arte replay catalog).

2026-06-23 v3 : SCRAPE via les VRAIES URLs catégorie du site Arte :
  /fr/videos/films/, /fr/videos/series/, /fr/videos/documentaires-et-reportages/,
  /fr/videos/info-et-societe/, /fr/videos/sciences/, etc.

Pipeline :
  1. Fetch HTML /fr/videos/<slug>/ → extract zone UUIDs (= rails de la page)
  2. Pour chaque zone, fetch /api/emac/v4/fr/web/zones/{uuid}/content/?page=N
     → liste de programmes avec posters
  3. Génère m3u avec group-title="Arte <Label>" + jaquette
"""
import json, os, sys, time, re
sys.path.insert(0, os.path.dirname(__file__))
from utils import http_get

ARTE_API_BASE = "https://api.arte.tv/api/emac/v4/fr/web"
ARTE_LOGO_FALLBACK = "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/france/arte-fr.png"
MAX_PAGES_PER_ZONE = 5

ARTE_CATEGORIES = [
    ("films",                       "Cinéma",                       "movie"),
    ("series",                      "Séries et fictions",           "series"),
    ("documentaires-et-reportages", "Documentaires et reportages",  "movie"),
    ("info-et-societe",             "Info et société",              "movie"),
    ("culture-et-pop",              "Culture et pop",               "movie"),
    ("sciences",                    "Sciences",                     "movie"),
    ("voyages-et-decouvertes",      "Voyages et découvertes",       "movie"),
    ("histoire",                    "Histoire",                     "movie"),
    ("emissions",                   "Émissions",                    "series"),
    ("arts",                        "Arts",                         "movie"),
    ("fictions",                    "Fictions",                     "series"),
    ("famille",                     "À voir en famille",            "movie"),
]

ARTE_CONCERT_GENRES = [
    ("pop-rock",      "Pop & Rock"),
    ("electro",       "Électro"),
    ("jazz",          "Jazz"),
    ("classique",     "Classique"),
    ("hip-hop",       "Hip-hop"),
    ("world",         "World"),
    ("metal",         "Metal"),
    ("baroque",       "Baroque"),
    ("opera",         "Opéra"),
    ("arts-de-la-scene", "Arts de la scène"),
]

ZONE_UUID_RE = re.compile(r"/zones/([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/content")
ARTE_VIDEO_PID_RE = re.compile(r"/videos/([0-9]{6}-[0-9]{3}-[A-Z]|RC-[A-Za-z0-9]+)/")


def arte_html_scrape_zones(url):
    """Fetch URL HTML + extract zone UUIDs uniques."""
    try:
        html = http_get(url, headers={"Accept": "text/html"})
    except Exception as e:
        print(f"[!] Arte fetch error {url}: {e}", file=sys.stderr)
        return []
    return list(dict.fromkeys(ZONE_UUID_RE.findall(html)))


def arte_zone_fetch_content(zone_id, max_pages=MAX_PAGES_PER_ZONE):
    """Fetch /zones/{uuid}/content/?page=N avec pagination."""
    items = []
    for page in range(1, max_pages + 1):
        url = f"{ARTE_API_BASE}/zones/{zone_id}/content/?page={page}"
        try:
            d = json.loads(http_get(url, headers={"Accept": "application/json"}))
        except Exception:
            break
        data = d.get("data") or []
        if not isinstance(data, list) or not data:
            break
        items.extend(data)
        pagination = d.get("pagination") or {}
        if pagination.get("page", 0) >= pagination.get("pages", 1):
            break
    return items


def arte_item_to_program(item):
    """Extrait (program_id, title, poster, kind) depuis un item zone."""
    kind_obj = item.get("kind") or {}
    kind_code = (kind_obj.get("code") or "").upper() if isinstance(kind_obj, dict) else str(kind_obj).upper()
    if kind_code not in ("SHOW", "COLLECTION", "EVENT", "PROGRAM"):
        return None
    title = (item.get("title") or "").strip()
    subtitle = (item.get("subtitle") or "").strip()
    if title and subtitle and subtitle.lower() not in title.lower():
        title = f"{title} — {subtitle}"
    if not title:
        return None
    url = item.get("url") or ""
    pid_match = ARTE_VIDEO_PID_RE.search(url)
    if not pid_match:
        return None
    pid = pid_match.group(1)
    poster = ""
    main_img = item.get("mainImage") or {}
    if isinstance(main_img, dict):
        img_url = main_img.get("url") or ""
        if img_url:
            poster = img_url.replace("__SIZE__", "400x225")
    return {"pid": pid, "title": title[:140], "poster": poster, "kind": kind_code}


def generate(output_path):
    lines = ["#EXTM3U"]
    total = 0
    seen_pids = set()

    print("\n=== Arte catégories via URL slugs ===")
    for slug, label, tvg_type_default in ARTE_CATEGORIES:
        url = f"https://www.arte.tv/fr/videos/{slug}/"
        zones = arte_html_scrape_zones(url)
        added = 0
        for zone_uuid in zones:
            items = arte_zone_fetch_content(zone_uuid, max_pages=2)
            for item in items:
                prog = arte_item_to_program(item)
                if not prog or prog["pid"] in seen_pids:
                    continue
                seen_pids.add(prog["pid"])
                logo = prog["poster"] or ARTE_LOGO_FALLBACK
                lines.append(
                    f'#EXTINF:-1 tvg-id="arte-{prog["pid"]}" '
                    f'tvg-logo="{logo}" tvg-country="FR" tvg-type="{tvg_type_default}" '
                    f'group-title="Arte {label}",{prog["title"]}'
                )
                lines.append(f'arte://{prog["pid"]}')
                total += 1
                added += 1
        print(f"  {label} ({slug}): {len(zones)} zones, {added} programmes")
        time.sleep(0.4)

    print("\n=== Arte Concert (10 genres) ===")
    for genre_slug, genre_label in ARTE_CONCERT_GENRES:
        url = f"https://www.arte.tv/fr/p/{genre_slug}/"
        zones = arte_html_scrape_zones(url)
        added = 0
        for zone_uuid in zones[:5]:
            items = arte_zone_fetch_content(zone_uuid, max_pages=2)
            for item in items:
                prog = arte_item_to_program(item)
                if not prog or prog["pid"] in seen_pids:
                    continue
                seen_pids.add(prog["pid"])
                logo = prog["poster"] or ARTE_LOGO_FALLBACK
                lines.append(
                    f'#EXTINF:-1 tvg-id="arte-{prog["pid"]}" '
                    f'tvg-logo="{logo}" tvg-country="FR" tvg-type="movie" '
                    f'group-title="Arte Concert - {genre_label}",{prog["title"]}'
                )
                lines.append(f'arte://{prog["pid"]}')
                total += 1
                added += 1
        print(f"  Concert {genre_label}: {added} programmes")
        time.sleep(0.3)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[OK] Wrote {total} Arte programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay-arte.m3u")
    n = generate(out)
    if n == 0:
        sys.exit(1)
