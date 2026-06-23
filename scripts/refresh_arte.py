#!/usr/bin/env python3
"""refresh_arte.py — génère data-replay-arte.m3u (Arte replay catalog).

2026-06-23 : MIGRATION COMPLÈTE vers l'API JSON Arte EMAC v4.
  Avant : HTML scraping limité (~5 films Cinéma, ~10 séries — total ~250 items)
  Maintenant : API JSON officielle → toutes les zones/sections/programmes
  exhaustifs des pages CIN/SER/DEC/SCI/HIS/FAM/etc. avec pagination complète.

Pipeline :
  1. Fetch /api/emac/v4/fr/web/pages/{CODE} → liste des zones (= rails) de la page
  2. Pour chaque zone, fetch /zones/{id}/content/?page=N (pagination jusqu'à 5p)
  3. Pour chaque item (kind=SHOW ou COLLECTION ou EVENT) → entry m3u avec poster
"""
import json, os, sys, time, re
sys.path.insert(0, os.path.dirname(__file__))
from utils import http_get

ARTE_API_BASE = "https://api.arte.tv/api/emac/v4/fr/web"
ARTE_LOGO_FALLBACK = "https://raw.githubusercontent.com/tv-logo/tv-logos/main/countries/france/arte-fr.png"
MAX_PAGES_PER_ZONE = 5

# Pages Arte (= catégories top-level). Code → label affiché.
ARTE_PAGES = [
    ("CIN", "Cinéma"),
    ("SER", "Séries et fictions"),
    ("FIC", "Fictions"),
    ("DEC", "Voyages et découvertes"),
    ("SCI", "Sciences"),
    ("HIS", "Histoire"),
    ("FAM", "À voir en famille"),
    ("ACT", "Actualité et société"),
    ("ART", "Arts"),
    ("POP", "Culture et pop"),
]

# Concert Arte : pages séparées (= musique). Folder distinct dans l'app.
ARTE_CONCERT_PAGES = [
    ("CMU_RPO", "Pop & Rock"),
    ("CMU_ELE", "Électro"),
    ("CMU_JAZ", "Jazz"),
    ("CMU_CLA", "Classique"),
    ("CMU_HIP", "Hip-hop"),
    ("CMU_WLD", "World"),
    ("CMU_MET", "Metal"),
    ("CMU_BAR", "Baroque"),
    ("CMU_OPE", "Opéra"),
    ("CMU_SCN", "Arts de la scène"),
]


def arte_fetch_page(page_code):
    """Récupère /pages/{code} → liste des zones."""
    url = f"{ARTE_API_BASE}/pages/{page_code}"
    try:
        d = json.loads(http_get(url, headers={"Accept": "application/json"}))
        return d.get("zones") or []
    except Exception as e:
        print(f"[!] Arte page {page_code}: {e}", file=sys.stderr)
        return []


def arte_fetch_zone(zone, max_pages=MAX_PAGES_PER_ZONE):
    """Récupère TOUS les items d'une zone :
    1. Items inline déjà dans zone.content.data (= 1ère page)
    2. Pagination via API si pages > 1 (utilise UUID dédoublé)."""
    items = []
    inline_content = zone.get("content") or {}
    inline_data = inline_content.get("data") or []
    if isinstance(inline_data, list):
        items.extend(inline_data)
    pagination = inline_content.get("pagination") or {}
    total_pages = pagination.get("pages", 1) or 1
    if total_pages <= 1:
        return items
    zone_id = zone.get("id") or ""
    parts = zone_id.split("_")
    api_id = parts[0]
    for page in range(2, min(total_pages + 1, max_pages + 1)):
        url = f"{ARTE_API_BASE}/zones/{api_id}/content/?page={page}"
        try:
            d = json.loads(http_get(url, headers={"Accept": "application/json"}))
        except Exception:
            break
        data = d.get("data") or []
        if not isinstance(data, list) or not data:
            break
        items.extend(data)
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
    pid_match = re.search(r"/videos/([0-9]{6}-[0-9]{3}-[A-Z]|RC-[A-Za-z0-9]+)/", url)
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

    print("\n=== Arte pages thématiques via API EMAC v4 ===")
    for page_code, page_label in ARTE_PAGES:
        zones = arte_fetch_page(page_code)
        page_added = 0
        zone_count = 0
        for zone in zones:
            zone_title = (zone.get("title") or "").strip()
            zone_id = zone.get("id") or ""
            if not zone_id or not zone_title:
                continue
            if zone_title.lower() in {"ma liste", "reprendre la lecture", "vos favoris",
                                       "à ne pas manquer", "en ce moment", "incontournables",
                                       "les incontournables", "parcourir les genres"}:
                continue
            items = arte_fetch_zone(zone)
            if not items:
                continue
            zone_count += 1
            tvg_type = "series" if page_code in ("SER", "FIC") else "movie"
            for item in items:
                prog = arte_item_to_program(item)
                if not prog or prog["pid"] in seen_pids:
                    continue
                seen_pids.add(prog["pid"])
                logo = prog["poster"] or ARTE_LOGO_FALLBACK
                lines.append(
                    f'#EXTINF:-1 tvg-id="arte-{prog["pid"]}" '
                    f'tvg-logo="{logo}" tvg-country="FR" tvg-type="{tvg_type}" '
                    f'group-title="Arte {page_label} - {zone_title}",{prog["title"]}'
                )
                lines.append(f'arte://{prog["pid"]}')
                total += 1
                page_added += 1
        print(f"  {page_label} ({page_code}): {zone_count} zones, {page_added} programmes")
        time.sleep(0.4)

    print("\n=== Arte Concert (10 genres musicaux) ===")
    for page_code, genre_label in ARTE_CONCERT_PAGES:
        zones = arte_fetch_page(page_code)
        genre_added = 0
        for zone in zones:
            zone_id = zone.get("id") or ""
            if not zone_id:
                continue
            items = arte_fetch_zone(zone, max_pages=3)
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
                genre_added += 1
        print(f"  Concert {genre_label}: {genre_added} programmes")
        time.sleep(0.4)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n[OK] Wrote {total} Arte programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay-arte.m3u")
    n = generate(out)
    if n == 0:
        sys.exit(1)
