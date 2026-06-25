#!/usr/bin/env python3
"""
refresh_fast.py — Fetch FAST channel playlists (Samsung TV+, Pluto TV, Plex TV,
LG Channels, Rakuten TV, Sony One) and write data-fast.m3u (STANDALONE).

NE TOUCHE PAS data.m3u. Fichier autonome comme refresh_arte.py, refresh_bfm.py, etc.
Uses stdlib only (no pip install needed). Triggered via workflow_dispatch by cron-job.org.
"""

import json, re, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen

OUTPUT = "data-fast.m3u"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"

# -- Source URLs --
SAMSUNG_URL = "https://textup.fr/902510Ao?filetype=txt"
PLUTO_URL   = "https://textup.fr/902266T0?filetype=txt"
PLEX_URL    = "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plex_fr.m3u"
LG_URL      = "https://www.apsattv.com/frlg.m3u"
RAKUTEN_URL = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/fr_rakuten.m3u"

# Sony One FR — 6 chaines CloudFront stables
SONY_ONE = [
    ("Sony One Hits Action",     "https://d1tvu4639eiqii.cloudfront.net/playlist.m3u8",  ""),
    ("Sony One Hits Comedie",    "https://d3nvxupr08boay.cloudfront.net/playlist.m3u8",  ""),
    ("Sony One Series Comedie",  "https://d1d95b9dbrm3cl.cloudfront.net/playlist.m3u8",  ""),
    ("Sony One Favoris",         "https://d21fkiqbljd83t.cloudfront.net/playlist.m3u8",  ""),
    ("Sony One Series Thriller", "https://d1573iiyr4aa7c.cloudfront.net/playlist.m3u8",  ""),
    ("Sony One Blacklist",       "https://d1x87j1jmcypab.cloudfront.net/playlist.m3u8",  ""),
]


def fetch(url, timeout=20):
    try:
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  WARN: fetch {url}: {e}", file=sys.stderr)
        return None


def _attr(line, name):
    m = re.search(rf'{name}="([^"]*?)"', line)
    return m.group(1) if m else ""


# ---------- Samsung TV+ (JSON) ----------
def parse_samsung(body):
    entries = []
    try:
        data = json.loads(body)
        for grp in data.get("groups", []):
            gn = grp.get("name", "Divers")
            for s in grp.get("stations", []):
                n, u, l = s.get("name", "").strip(), s.get("url", "").strip(), s.get("image", "")
                if n and u and "c9v3.s.gy" not in u:
                    entries.append((n, u, f"Samsung TV+ - {gn}", l))
        for s in data.get("stations", []):
            n, u, l = s.get("name", "").strip(), s.get("url", "").strip(), s.get("image", "")
            fold = s.get("infold", "Samsung TV+")
            if n and u and "c9v3.s.gy" not in u:
                entries.append((n, u, fold or "Samsung TV+", l))
    except Exception as e:
        print(f"  WARN: parse_samsung: {e}", file=sys.stderr)
    return entries


# ---------- Generic M3U parser ----------
def parse_m3u(body, service):
    entries, lines, i = [], body.splitlines(), 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("#EXTINF:") and i + 1 < len(lines):
            ul = lines[i + 1].strip()
            if ul and ul.startswith("http") and not ul.startswith("#"):
                logo = _attr(ln, "tvg-logo")
                gt = _attr(ln, "group-title")
                nm = ln[ln.rfind(",") + 1:].strip() if "," in ln else ""
                if nm:
                    g = f"{service} - {gt}" if gt else service
                    entries.append((nm, ul, g, logo))
            i += 2
            continue
        i += 1
    return entries


# ---------- LG Channels (categorisation) ----------
_LG = [
    (["pluto tv"], "Pluto TV"), (["rakuten"], "Rakuten TV"), (["sony one"], "Sony One"),
    (["sport", "football", "nba", "top gear", "motorvision", "wwe", "eurosport"], "Sport"),
    (["action", "western", "thriller", "horror", "sci-fi", "polar", "asylum"], "Action & Thriller"),
    (["comedie", "comedy", "rire"], "Comedie"),
    (["drame", "drama", "romance", "romantique", "novela"], "Drame & Romance"),
    (["film", "cinema", "movie", "lionsgate", "nanar", "grjngo", "moviesphere",
      "allocine"], "Cinema"),
    (["serie", "series", "binge"], "Series"),
    (["kid", "enfant", "toon", "cartoon", "pokemon", "sonic", "miraculous", "teen"], "Jeunesse"),
    (["crime", "ncis", "blacklist", "jag", "walker"], "Crime"),
    (["musique", "music", "mtv", "trace", "vevo", "live nation"], "Musique"),
    (["nature", "animaux", "animal", "planet", "pets"], "Nature & Animaux"),
    (["docu", "histoire", "history", "science", "discovery", "insight", "real stories"], "Documentaire"),
    (["cuisine", "food", "maison", "jardin", "lifestyle", "fashion", "beauty"], "Lifestyle"),
    (["info", "news", "euronews", "france 24", "afrique"], "Info"),
    (["rmc", "bfm"], "BFM / RMC"),
]


def _clf(name):
    lo = name.lower()
    for keys, cat in _LG:
        if any(k in lo for k in keys):
            return cat
    return "Autres"


def parse_lg(body):
    entries, lines, i = [], body.splitlines(), 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("#EXTINF:") and i + 1 < len(lines):
            ul = lines[i + 1].strip()
            if ul and ul.startswith("http") and not ul.startswith("#"):
                logo = _attr(ln, "tvg-logo")
                raw = ln[ln.rfind(",") + 1:].strip() if "," in ln else ""
                if raw:
                    nm = re.sub(r"^\\d+\\s+", "", raw)
                    entries.append((nm or raw, ul, f"LG Channels - {_clf(nm)}", logo))
            i += 2
            continue
        i += 1
    return entries


# ---------- Rakuten TV (M3U from iptv-org) ----------
def parse_rakuten(body):
    return parse_m3u(body, "Rakuten TV")


# ---------- Main ----------
def main():
    print("refresh_fast.py: fetching FAST playlists...")
    print("  Output: data-fast.m3u (STANDALONE, ne touche PAS data.m3u)")

    bodies = {}
    tasks = {
        "samsung": SAMSUNG_URL,
        "pluto": PLUTO_URL,
        "plex": PLEX_URL,
        "lg": LG_URL,
        "rakuten": RAKUTEN_URL,
    }
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(fetch, url): svc for svc, url in tasks.items()}
        for f in as_completed(futs):
            bodies[futs[f]] = f.result()

    all_e = []
    for svc, parser, body in [
        ("Samsung TV+", parse_samsung, bodies.get("samsung")),
        ("Pluto TV", lambda b: parse_m3u(b, "Pluto TV"), bodies.get("pluto")),
        ("Plex TV", lambda b: parse_m3u(b, "Plex TV"), bodies.get("plex")),
        ("LG Channels", parse_lg, bodies.get("lg")),
        ("Rakuten TV", parse_rakuten, bodies.get("rakuten")),
    ]:
        if body:
            e = parser(body)
            print(f"  {svc}: {len(e)} channels")
            all_e.extend(e)
        else:
            print(f"  {svc}: FAILED")

    # Sony One (hardcoded)
    for name, url, logo in SONY_ONE:
        all_e.append((name, url, "Sony One", logo))
    print(f"  Sony One: {len(SONY_ONE)} channels")

    print(f"  Total: {len(all_e)} FAST channels")
    if not all_e:
        print("  ERROR: 0 channels -- aborting")
        sys.exit(1)

    # Write standalone data-fast.m3u
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for name, url, group, logo in all_e:
            l = f' tvg-logo="{logo}"' if logo else ""
            f.write(f'#EXTINF:-1{l} group-title="{group}",{name}\n')
            f.write(f'{url}\n')

    with open(OUTPUT) as f:
        n = sum(1 for _ in f)
    print(f"  Written {OUTPUT}: {n} lines")


if __name__ == "__main__":
    main()
