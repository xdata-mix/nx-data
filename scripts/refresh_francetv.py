#!/usr/bin/env python3
"""refresh_francetv.py — génère data-replay-francetv.m3u.

API publique api-mobile.yatta.francetv.fr/apps/. Pas de payant côté France.tv
(= 100% gratuit). URLs francetv://<si_id> résolues côté app par FrancetvResolver.
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from utils import http_get

MAX_ITEMS_PER_CHAN = 999

FRANCETV_CHANNELS = [
    ("france-2",   "France 2",        "https://i.imgur.com/sJZBuY4.png"),
    ("france-3",   "France 3",        "https://i.imgur.com/PWbIICf.png"),
    ("france-4",   "France 4",        "https://i.imgur.com/wEsxQLP.png"),
    ("france-5",   "France 5",        "https://i.imgur.com/X4Y5jKR.png"),
    ("franceinfo", "Franceinfo",      "https://i.imgur.com/eITXz6A.png"),
    ("slash",      "Slash",           "https://i.imgur.com/sJZBuY4.png"),
    ("sport",      "FranceTV Sport",  "https://i.imgur.com/sJZBuY4.png"),
    ("france-24",  "France 24",       "https://i.imgur.com/yAiTedt.png"),
    ("france-o",   "France Ô",        "https://i.imgur.com/sJZBuY4.png"),
]
FRANCETV_CATEGORIES = [
    ("series-et-fictions",      "Séries et fictions",  "https://i.imgur.com/sJZBuY4.png"),
    ("documentaires",           "Documentaires",       "https://i.imgur.com/sJZBuY4.png"),
    ("films",                   "Cinéma",              "https://i.imgur.com/sJZBuY4.png"),
    ("societe",                 "Société",             "https://i.imgur.com/sJZBuY4.png"),
    ("info",                    "Info",                "https://i.imgur.com/eITXz6A.png"),
    ("spectacles-et-culture",   "Arts et spectacles",  "https://i.imgur.com/sJZBuY4.png"),
    ("sport",                   "Sport",               "https://i.imgur.com/sJZBuY4.png"),
    ("jeux-et-divertissements", "Divertissement",      "https://i.imgur.com/sJZBuY4.png"),
    ("enfants",                 "Enfants",             "https://i.imgur.com/wEsxQLP.png"),
    ("podcasts",                "Podcasts",            "https://i.imgur.com/sJZBuY4.png"),
]


def _parse_yatta_response(data):
    """Helper commun pour /apps/channels/<path> et /apps/categories/<slug>."""
    seen = set()
    out = []
    for coll in data.get("collections", []):
        if coll.get("type") in ("live", "link"):
            continue
        for item in coll.get("items", []):
            si = item.get("si_id")
            if not si or si in seen:
                continue
            seen.add(si)
            title = (item.get("title") or "").strip()
            program = item.get("program") or {}
            program_title = program.get("label") or program.get("title") or ""
            if not title:
                title = program_title
            elif program_title and program_title.lower() not in title.lower():
                title = f"{program_title} — {title}"
            if not title:
                continue
            # 2026-06-23 : extract image from "images" array (= list of
            # {urls: {"w:265": ..., "w:400": ...}, type: "background_16x9"|"vignette"})
            # Prefer vignette (portrait) for posters, fallback background_16x9.
            logo = ""
            imgs = item.get("images") or []
            if isinstance(imgs, list):
                # Priority : vignette portrait (= les vrais posters de séries)
                portrait_img = next((i for i in imgs if i.get("type", "").startswith("vignette")), None)
                bg_img = next((i for i in imgs if i.get("type", "").startswith("background")), None)
                chosen = portrait_img or bg_img or (imgs[0] if imgs else None)
                if chosen:
                    urls = chosen.get("urls", {}) or {}
                    # Prefer ~400-800px for jaquettes (optimal taille app)
                    for size_key in ("w:400", "w:300", "w:800", "w:265", "w:1024", "w:2500"):
                        if urls.get(size_key):
                            logo = urls[size_key]
                            break
                    # Fallback : 1st URL trouvée
                    if not logo and urls:
                        logo = next(iter(urls.values()))
            out.append({"si_id": si, "title": title[:140], "logo": logo})
            if len(out) >= MAX_ITEMS_PER_CHAN:
                break
        if len(out) >= MAX_ITEMS_PER_CHAN:
            break
    return out


def francetv_channel_programs(channel_path):
    url = f"https://api-mobile.yatta.francetv.fr/apps/channels/{channel_path}?platform=apps"
    try:
        return _parse_yatta_response(json.loads(http_get(url)))
    except Exception as e:
        print(f"[!] FTV chan {channel_path}: {e}", file=sys.stderr)
        return []


def francetv_category_programs(category_slug):
    url = f"https://api-mobile.yatta.francetv.fr/apps/categories/{category_slug}?platform=apps"
    try:
        return _parse_yatta_response(json.loads(http_get(url)))
    except Exception as e:
        print(f"[!] FTV cat {category_slug}: {e}", file=sys.stderr)
        return []


def generate(output_path):
    lines = ["#EXTM3U"]
    total = 0

    print("\n=== France.tv (9 chaînes) ===")
    for channel_path, channel_label, channel_logo in FRANCETV_CHANNELS:
        progs = francetv_channel_programs(channel_path)
        print(f"  {channel_label}: {len(progs)} programmes")
        for p in progs:
            logo = p["logo"] or channel_logo
            lines.append(
                f'#EXTINF:-1 tvg-id="francetv-{p["si_id"]}" '
                f'tvg-logo="{logo}" tvg-country="FR" tvg-type="series" '
                f'group-title="Replay {channel_label}",{p["title"]}'
            )
            lines.append(f'francetv://{p["si_id"]}')
            total += 1
        time.sleep(0.4)

    print("\n=== France.tv Thématiques (10 catégories) ===")
    ftv_seen = set()
    for line in lines:
        if line.startswith('francetv://'):
            ftv_seen.add(line.replace('francetv://', '').strip())
    for cat_slug, cat_label, cat_logo in FRANCETV_CATEGORIES:
        progs = francetv_category_programs(cat_slug)
        added = 0
        group = f"Thématique France TV - {cat_label}"
        for p in progs:
            si = p["si_id"]
            if si in ftv_seen:
                continue
            ftv_seen.add(si)
            logo = p["logo"] or cat_logo
            lines.append(
                f'#EXTINF:-1 tvg-id="francetv-{si}" '
                f'tvg-logo="{logo}" tvg-country="FR" tvg-type="series" '
                f'group-title="{group}",{p["title"]}'
            )
            lines.append(f'francetv://{si}')
            total += 1
            added += 1
        print(f"  Théma {cat_label}: {len(progs)} → {added} nouveaux")
        time.sleep(0.3)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"\n[OK] Wrote {total} France.tv programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay-francetv.m3u")
    n = generate(out)
    if n == 0:
        sys.exit(1)
