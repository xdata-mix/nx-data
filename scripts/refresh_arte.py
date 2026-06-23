#!/usr/bin/env python3
"""refresh_arte.py — génère data-replay-arte.m3u.

HTML scraping de arte.tv/fr/videos/<cat>/ + /fr/p/<slug>/ (= Concert + thèmes).
Check disponibilité via api.arte.tv/api/player/v2/config/ pour filtrer les
programmes avec droits expirés (= ERROR_NO_RIGHTS).
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from utils import http_get, slug_to_title

MAX_ITEMS_PER_ARTE_CAT = 500

ARTE_LOGO = "https://i.imgur.com/ecXMjNl.png"
ARTE_HREF_RE = re.compile(r"href=\"/fr/videos/([^/\"]+)/([^\"#]+?)/\"")
ARTE_PID_VALID = re.compile(r"^(\d{6}-\d{3}-[A-Z]|RC-[A-Za-z0-9]+)$")
ARTE_API_CFG = "https://api.arte.tv/api/player/v2/config/fr/{}"

ARTE_CATEGORIES = [
    ("cinema",                       "Cinéma"),
    ("series-et-fictions",           "Séries et fictions"),
    ("documentaires-et-reportages",  "Documentaires et reportages"),
    ("sciences",                     "Sciences"),
    ("culture-et-pop",               "Culture et pop"),
    ("histoire",                     "Histoire"),
    ("info-et-societe",              "Info et société"),
    ("voyages-et-decouvertes",       "Voyages et découvertes"),
    ("emissions",                    "Émissions"),
]
ARTE_CONCERT_GENRES = [
    ("pop-rock",                "Pop & Rock"),
    ("classique",               "Classique"),
    ("musiques-electroniques",  "Électro"),
    ("jazz",                    "Jazz"),
    ("arts-de-la-scene",        "Arts de la scène"),
    ("hip-hop",                 "Hip-hop"),
    ("metal",                   "Metal"),
    ("opera",                   "Opéra"),
    ("world",                   "World"),
    ("musique-baroque",         "Baroque"),
]
ARTE_THEMED_PAGES = [
    ("a-voir-en-famille",       "À voir en famille"),
]
ARTE_SERIES_CATS = {"series-et-fictions"}


def arte_check_available(pid):
    """True si streams ≠ vide (= droits actifs)."""
    try:
        j = json.loads(http_get(ARTE_API_CFG.format(pid), headers={"Accept": "application/json"}))
        data = j.get("data") or {}
        attrs = data.get("attributes") or {}
        if not (attrs.get("streams") or []):
            return False
        err = attrs.get("error") or data.get("error")
        if err and isinstance(err, dict) and err.get("code"):
            return False
        return True
    except Exception:
        return True  # tolérant sur timeout/réseau


def _scrape_arte_html(url, max_items):
    """Helper commun pour /fr/videos/<cat>/ et /fr/p/<slug>/."""
    try:
        raw = http_get(url, headers={"Accept": "text/html"})
    except Exception as e:
        print(f"[!] Arte fetch error {url}: {e}", file=sys.stderr)
        return []
    seen = set()
    candidates = []
    for m in ARTE_HREF_RE.finditer(raw):
        pid = m.group(1)
        slug = m.group(2)
        if not ARTE_PID_VALID.match(pid) or pid in seen:
            continue
        seen.add(pid)
        title = slug_to_title(slug)[:140]
        if not title:
            continue
        candidates.append({"program_id": pid, "title": title})
        if len(candidates) >= max_items * 2:
            break
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        availability = list(ex.map(lambda c: arte_check_available(c["program_id"]), candidates))
    out = [c for c, ok in zip(candidates, availability) if ok][:max_items]
    return out


def arte_category_programs(category_slug, max_items=MAX_ITEMS_PER_ARTE_CAT):
    return _scrape_arte_html(f"https://www.arte.tv/fr/videos/{category_slug}/", max_items)


def arte_p_page_programs(slug, max_items=MAX_ITEMS_PER_ARTE_CAT):
    return _scrape_arte_html(f"https://www.arte.tv/fr/p/{slug}/", max_items)


def generate(output_path):
    lines = ["#EXTM3U"]
    total = 0

    print("\n=== Arte+7 (9 catégories principales) ===")
    for cat_slug, cat_label in ARTE_CATEGORIES:
        progs = arte_category_programs(cat_slug)
        print(f"  {cat_label}: {len(progs)} programmes")
        tvg_type = "series" if cat_slug in ARTE_SERIES_CATS else "movie"
        for p in progs:
            lines.append(
                f'#EXTINF:-1 tvg-id="arte-{p["program_id"]}" '
                f'tvg-logo="{ARTE_LOGO}" tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Arte {cat_label}",{p["title"]}'
            )
            lines.append(f'arte://{p["program_id"]}')
            total += 1
        time.sleep(0.5)

    print("\n=== Arte Concert (10 sous-genres musique) ===")
    arte_seen = set()
    for line in lines:
        if line.startswith('arte://'):
            arte_seen.add(line.replace('arte://', '').strip())
    for genre_slug, genre_label in ARTE_CONCERT_GENRES:
        progs = arte_p_page_programs(genre_slug)
        added = 0
        group = f"Arte Concert - {genre_label}"
        for p in progs:
            pid = p["program_id"]
            if pid in arte_seen:
                continue
            arte_seen.add(pid)
            lines.append(
                f'#EXTINF:-1 tvg-id="arte-{pid}" '
                f'tvg-logo="{ARTE_LOGO}" tvg-country="FR" tvg-type="movie" '
                f'group-title="{group}",{p["title"]}'
            )
            lines.append(f'arte://{pid}')
            total += 1
            added += 1
        print(f"  {genre_label}: {len(progs)} → {added} nouveaux")
        time.sleep(0.3)

    print("\n=== Arte Pages Thématiques ===")
    for page_slug, page_label in ARTE_THEMED_PAGES:
        progs = arte_p_page_programs(page_slug)
        added = 0
        group = f"Arte Thématique - {page_label}"
        for p in progs:
            pid = p["program_id"]
            if pid in arte_seen:
                continue
            arte_seen.add(pid)
            lines.append(
                f'#EXTINF:-1 tvg-id="arte-{pid}" '
                f'tvg-logo="{ARTE_LOGO}" tvg-country="FR" tvg-type="movie" '
                f'group-title="{group}",{p["title"]}'
            )
            lines.append(f'arte://{pid}')
            total += 1
            added += 1
        print(f"  {page_label}: {len(progs)} → {added} nouveaux")
        time.sleep(0.3)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"\n[OK] Wrote {total} Arte programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay-arte.m3u")
    n = generate(out)
    if n == 0:
        sys.exit(1)
