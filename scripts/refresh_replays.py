#!/usr/bin/env python3
"""
refresh_replays.py — génère data-replay.m3u (catalogue replay FR du jour)

Pipeline France.tv (reverse-engineered via Catchup TV & More) :
  1. api-mobile.yatta.francetv.fr/apps/channels/<chan> → programmes par chaîne
  2. URL `francetv://<si_id>` posée dans le M3U
  3. L'app résout à la lecture (k7.ftven.fr + hdfauth.ftven.fr)
     (= obligatoire car JWT signé sur IP cliente FR)
"""
import os, sys, json, time, urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Linux; Android 14; AndroidTV) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 15
MAX_ITEMS_PER_CHAN = 12
CHANNELS = [
    ("france-2",   "France 2",       "https://i.imgur.com/sJZBuY4.png"),
    ("france-3",   "France 3",       "https://i.imgur.com/PWbIICf.png"),
    ("france-4",   "France 4",       "https://i.imgur.com/wEsxQLP.png"),
    ("france-5",   "France 5",       "https://i.imgur.com/X4Y5jKR.png"),
    ("franceinfo", "Franceinfo",     "https://i.imgur.com/eITXz6A.png"),
]

def http_get(url, headers=None):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8")

def francetv_channel_programs(channel_path):
    url = (f"https://api-mobile.yatta.francetv.fr/apps/channels/{channel_path}"
           f"?platform=apps")
    try:
        raw = http_get(url)
    except Exception as e:
        print(f"[!] Fetch error {channel_path}: {e}", file=sys.stderr)
        return []
    data = json.loads(raw)
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
            logo = ""
            for k in ("image_url", "background_url"):
                v = item.get(k)
                if v and isinstance(v, str):
                    logo = v
                    break
            if not logo:
                imgs = item.get("media_image") or {}
                if isinstance(imgs, dict):
                    logo = imgs.get("url", "")
            out.append({
                "si_id": si,
                "title": title[:140],
                "logo": logo,
                "channel_path": channel_path,
            })
            if len(out) >= MAX_ITEMS_PER_CHAN:
                break
        if len(out) >= MAX_ITEMS_PER_CHAN:
            break
    return out

def generate_m3u(output_path):
    lines = ["#EXTM3U"]
    total = 0
    for channel_path, channel_label, channel_logo in CHANNELS:
        progs = francetv_channel_programs(channel_path)
        print(f"  {channel_label}: {len(progs)} programmes")
        for p in progs:
            logo = p["logo"] or channel_logo
            extinf = (
                f'#EXTINF:-1 tvg-id="francetv-{p["si_id"]}" '
                f'tvg-logo="{logo}" '
                f'tvg-country="FR" '
                f'group-title="Replay {channel_label}",{p["title"]}'
            )
            lines.append(extinf)
            lines.append(f'francetv://{p["si_id"]}')
            total += 1
        time.sleep(0.5)
    lines.append("")
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[OK] Wrote {total} replay programs to {output_path}")
    return total

if __name__ == "__main__":
    out = os.environ.get("OUTPUT", "data-replay.m3u")
    n = generate_m3u(out)
    if n == 0:
        print("[!] No replays found, exiting with error", file=sys.stderr)
        sys.exit(1)
