#!/usr/bin/env python3
"""refresh_m6.py — génère data-replay-m6.m3u (M6+ replay catalog).

Endpoint pc.middleware.6play.fr (= équiv web du middleware). Pas besoin d'auth
Gigya pour le catalogue. L'auth M6 (Widevine DRM) est utilisée par l'app Onyx
au moment du PLAYBACK. Filtre subscriptions/is_subscription (M6+MAX premium).
"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from utils import http_get, slug_to_title

M6_BASE = "https://pc.middleware.6play.fr/6play/v2/platforms/m6group_web/services"
M6_PAGE_SIZE = 100

M6_CHANNELS = [
    ("m6replay",              "M6",              "https://i.imgur.com/4lhxLPB.png"),
    ("w9replay",              "W9",              "https://i.imgur.com/oFGn1On.png"),
    ("6terreplay",            "6ter",            "https://i.imgur.com/M7vGd6Y.png"),
    ("gulli",                 "Gulli",           "https://i.imgur.com/tFNzQQM.png"),
    ("tevareplay",            "Téva",            "https://i.imgur.com/HuLNVjC.png"),
    ("parispremierereplay",   "Paris Première",  "https://i.imgur.com/oCBzd0e.png"),
]
# 2026-06-22 : 9 catégories transverses M6+ via /folders/{fid}/programs.
# Les folders sont CROSS-CHAÎNE — 1 seul appel suffit par folder.
M6_FOLDERS = [
    (10,   "Divertissement"),
    (232,  "Séries réalité"),
    (8,    "Séries"),
    (58,   "Sport"),
    (12,   "Infos & Société"),
    (907,  "Cinéma"),
    (70,   "Téléfilms"),
    (52,   "Jeunesse"),
    (2996, "Podcasts"),
]
M6_LOGO_GENERIC = "https://i.imgur.com/4lhxLPB.png"


def m6_channel_programs(service_id, max_items=2000):
    """Pagination /services/{svc}/programs?limit=100&offset=N."""
    out = []
    offset = 0
    while offset < max_items:
        url = f"{M6_BASE}/{service_id}/programs?limit={M6_PAGE_SIZE}&offset={offset}&csa=6"
        try:
            arr = json.loads(http_get(url, headers={"Accept": "application/json"}))
        except Exception as e:
            print(f"[!] M6 chan {service_id} offset={offset}: {e}", file=sys.stderr)
            break
        if not isinstance(arr, list) or len(arr) == 0:
            break
        for p in arr:
            pid = p.get("id")
            if not pid:
                continue
            if p.get("subscriptions") or p.get("is_subscription"):
                continue  # M6+MAX premium
            code = (p.get("code") or "").strip()
            name = (p.get("name") or "").strip()
            title = name if name else slug_to_title(code)
            if not title:
                continue
            img = ""
            imgs = p.get("images") or []
            if imgs and isinstance(imgs, list):
                img = imgs[0].get("external_key", "") if isinstance(imgs[0], dict) else ""
            ptype = (p.get("program_type_wording") or {}).get("code", "")
            is_series = ptype in {"episode", "emission", "magazine", "journal", "dessin-anime"}
            tvg_type = "series" if is_series else "movie"
            out.append({"program_id": pid, "title": title[:140], "image": img,
                        "service": service_id, "tvg_type": tvg_type})
        if len(arr) < M6_PAGE_SIZE:
            break
        offset += M6_PAGE_SIZE
    return out


def m6_folder_programs(folder_id, folder_label, max_items=2000):
    """Catégorie transverse cross-chaîne (1 seul appel suffit)."""
    out = []
    offset = 0
    while offset < max_items:
        url = (f"{M6_BASE}/m6replay/folders/{folder_id}/programs"
               f"?limit={M6_PAGE_SIZE}&offset={offset}&csa=6")
        try:
            arr = json.loads(http_get(url, headers={"Accept": "application/json"}))
        except Exception as e:
            print(f"[!] M6 folder {folder_id}: {e}", file=sys.stderr)
            break
        if not isinstance(arr, list) or len(arr) == 0:
            break
        for p in arr:
            pid = p.get("id")
            if not pid:
                continue
            if p.get("subscriptions") or p.get("is_subscription"):
                continue
            code = (p.get("code") or "").strip()
            name = (p.get("name") or "").strip()
            title = name if name else slug_to_title(code)
            if not title:
                continue
            img = ""
            imgs = p.get("images") or []
            if imgs and isinstance(imgs, list):
                img = imgs[0].get("external_key", "") if isinstance(imgs[0], dict) else ""
            ptype = (p.get("program_type_wording") or {}).get("code", "")
            is_series = ptype in {"episode", "emission", "magazine", "journal", "dessin-anime"}
            tvg_type = "series" if is_series else "movie"
            out.append({"program_id": pid, "title": title[:140], "image": img,
                        "service": "m6replay", "tvg_type": tvg_type,
                        "folder_label": folder_label})
        if len(arr) < M6_PAGE_SIZE:
            break
        offset += M6_PAGE_SIZE
    return out


def generate(output_path):
    lines = ["#EXTM3U"]
    total = 0

    print("\n=== M6+ Replay (6 chaînes) ===")
    for service_id, chan_label, chan_logo in M6_CHANNELS:
        progs = m6_channel_programs(service_id)
        print(f"  {chan_label}: {len(progs)} programmes")
        for p in progs:
            ilogo = p.get("image") or chan_logo
            if ilogo and not ilogo.startswith("http"):
                ilogo = f"https://images.6play.fr/v1/images/{ilogo}/raw"
            lines.append(
                f'#EXTINF:-1 tvg-id="m6plus-{p["program_id"]}" '
                f'tvg-logo="{ilogo}" tvg-country="FR" '
                f'tvg-type="{p.get("tvg_type", "series")}" '
                f'group-title="Replay {chan_label}",{p["title"]}'
            )
            lines.append(f'm6play://{service_id}/{p["program_id"]}')
            total += 1
        time.sleep(0.4)

    print("\n=== M6+ Thématiques transverses (9 catégories) ===")
    m6_themes_seen = set()
    for line in lines:
        if line.startswith('m6play://'):
            try:
                m6_themes_seen.add(line.split('/')[-1])
            except Exception:
                pass
    for folder_id, folder_label in M6_FOLDERS:
        progs = m6_folder_programs(folder_id, folder_label)
        added = 0
        group = f"Thématique M6+ - {folder_label}"
        for p in progs:
            pid = p["program_id"]
            if pid in m6_themes_seen:
                continue
            m6_themes_seen.add(pid)
            ilogo = p.get("image") or M6_LOGO_GENERIC
            if ilogo and not ilogo.startswith("http"):
                ilogo = f"https://images.6play.fr/v1/images/{ilogo}/raw"
            lines.append(
                f'#EXTINF:-1 tvg-id="m6plus-{pid}" '
                f'tvg-logo="{ilogo}" tvg-country="FR" '
                f'tvg-type="{p.get("tvg_type", "series")}" '
                f'group-title="{group}",{p["title"]}'
            )
            lines.append(f'm6play://m6replay/{pid}')
            total += 1
            added += 1
        print(f"  Théma {folder_label}: {len(progs)} → {added} nouveaux (dedup)")
        time.sleep(0.3)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(lines))
    print(f"\n[OK] Wrote {total} M6+ programs to {output_path}")
    return total


if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay-m6.m3u")
    n = generate(out)
    if n == 0:
        sys.exit(1)
