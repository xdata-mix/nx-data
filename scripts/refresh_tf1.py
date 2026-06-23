#!/usr/bin/env python3
"""refresh_tf1.py — génère data-replay-tf1.m3u (TF1+ live + replay catalog).

Scraping JSON-LD ItemList sur tf1.fr/{chan}/replay + programmes-tv/{cat}.
SKIP /a-la-carte (= 100% payant : Sans pub 0,69€, Avant-premières, VOD Cinéma).
"""
import json, os, re, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from utils import http_get_tf1, slug_to_title

MAX_ITEMS_PER_CHAN = 999

TF1_CHANNELS = [
    ("tf1",              "TF1",              "https://i.imgur.com/qkOSt0o.png"),
    ("tmc",              "TMC",              "https://i.imgur.com/RY3iEMb.png"),
    ("tfx",              "TFX",              "https://i.imgur.com/JJVZJqL.png"),
    ("tf1-series-films", "TF1 Séries Films", "https://i.imgur.com/3OZdMb9.png"),
    ("lci",              "LCI",              "https://i.imgur.com/jVxzNHL.png"),
]
# 2026-06-22 : ajout "series" (= 295 progs uniques, la plus grosse catégorie).
# Skip "a-la-carte" qui est 100% payant.
TF1_CATEGORIES = [
    ("series",               "Séries"),
    ("sport",                "Sport"),
    ("reportages",           "Reportages"),
    ("telefilms",            "Téléfilms"),
    ("people-43944072",      "People"),
    ("podcasts-70045207",    "Podcasts"),
    ("impact",               "Impact"),
    ("jeunesse",             "Jeunesse"),
    ("divertissement",       "Divertissement"),
    ("films",                "Films"),
    ("info",                 "Info"),
]
TF1_REPLAY_URL = "https://www.tf1.fr/{slug}/replay"
TF1_CAT_HREF_RE = re.compile(r'href="/(tf1|tmc|tfx|tf1-series-films|lci)/([a-z0-9-]+)"')


def tf1plus_channel_programs(channel_slug, max_items=MAX_ITEMS_PER_CHAN):
    """Scrape JSON-LD ItemList sur la page replay TF1+."""
    try:
        raw = http_get_tf1(TF1_REPLAY_URL.format(slug=channel_slug))
    except Exception as e:
        print(f"[!] TF1+ fetch error {channel_slug}: {e}", file=sys.stderr)
        return []
    blocks = re.findall(r'<script type="application/ld\+json"[^>]*>([^<]+)</script>', raw, re.DOTALL)
    out = []
    seen = set()
    for b in blocks:
        try:
            data = json.loads(b)
        except Exception:
            continue
        if data.get("@type") != "ItemList":
            continue
        for elem in data.get("itemListElement", []):
            if len(out) >= max_items:
                return out
            if not isinstance(elem, dict):
                continue
            item = elem.get("item")
            if not isinstance(item, dict):
                continue
            prog_url = item.get("url") or ""
            name = item.get("name") or ""
            image = item.get("image") or ""
            if not prog_url or not name:
                continue
            path = prog_url.replace("https://www.tf1.fr/", "").strip("/")
            if "/" not in path or len(path.split("/")) > 2:
                continue
            if path in seen:
                continue
            seen.add(path)
            item_type = item.get("@type", "")
            out.append({"tf1_type": item_type, "si_id": path,
                        "title": name.strip()[:140], "logo": image})
    return out


def tf1_category_programs(category_slug, max_items=MAX_ITEMS_PER_CHAN):
    """Scrape /programmes-tv/<cat>. Format : hrefs /(chan)/(slug)."""
    try:
        raw = http_get_tf1(f"https://www.tf1.fr/programmes-tv/{category_slug}")
    except Exception as e:
        print(f"[!] TF1 cat fetch error {category_slug}: {e}", file=sys.stderr)
        return []
    out = []
    seen = set()
    excluded = {"replay", "direct", "news", "videos", "programmes-tv", "a-la-carte"}
    for m in TF1_CAT_HREF_RE.finditer(raw):
        chan = m.group(1)
        slug = m.group(2)
        if slug in excluded:
            continue
        si_id = f"{chan}/{slug}"
        if si_id in seen:
            continue
        seen.add(si_id)
        title = slug_to_title(slug)[:140]
        out.append({"si_id": si_id, "title": title, "logo": ""})
        if len(out) >= max_items:
            break
    return out


def generate(output_path):
    lines = ["#EXTM3U"]
    total = 0

    print("\n=== TF1+ Replay (5 chaînes JSON-LD) ===")
    for chan_slug, chan_label, chan_logo in TF1_CHANNELS:
        progs = tf1plus_channel_programs(chan_slug)
        print(f"  {chan_label}: {len(progs)} programmes")
        for p in progs:
            ilogo = p.get("logo") or chan_logo
            si_path = (p.get("si_id") or "").lower()
            tf1_type = (p.get("tf1_type") or "").lower()
            tvg_type = (
                "series" if tf1_type in ("tvseries", "tvseason") else
                "movie" if tf1_type == "movie" else
                ("movie" if (chan_slug == "tf1seriesfilms" or "film" in si_path or "/cinema/" in si_path) else "series")
            )
            lines.append(
                f'#EXTINF:-1 tvg-id="tf1plus-{p["si_id"]}" '
                f'tvg-logo="{ilogo}" tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(f'tf1plus://{p["si_id"]}')
            total += 1
        time.sleep(0.5)

    print("\n=== TF1+ /programmes-tv/<cat> (11 catégories) ===")
    tf1_cat_seen = set()
    for chan_slug, _, _ in TF1_CHANNELS:
        for p in tf1plus_channel_programs(chan_slug):
            tf1_cat_seen.add(p["si_id"])
    chan_meta = {c[0]: (c[1], c[2]) for c in TF1_CHANNELS}
    for cat_slug, cat_label in TF1_CATEGORIES:
        progs = tf1_category_programs(cat_slug)
        added = 0
        for p in progs:
            if p["si_id"] in tf1_cat_seen:
                continue
            tf1_cat_seen.add(p["si_id"])
            chan = p["si_id"].split("/")[0]
            meta = chan_meta.get(chan)
            if not meta:
                continue
            chan_label, chan_logo = meta
            si_path = (p.get("si_id") or "").lower()
            is_film = "film" in si_path or "/cinema/" in si_path
            tvg_type = "movie" if is_film else "series"
            lines.append(
                f'#EXTINF:-1 tvg-id="tf1plus-{p["si_id"].replace(chr(47), chr(45))}" '
                f'tvg-logo="{chan_logo}" tvg-country="FR" '
                f'tvg-type="{tvg_type}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(f'tf1plus://{p["si_id"]}')
            added += 1
            total += 1
        print(f"  {cat_label}: +{added} nouveaux")
        time.sleep(0.3)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"\n[OK] Wrote {total} TF1+ programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay-tf1.m3u")
    n = generate(out)
    if n == 0:
        sys.exit(1)
