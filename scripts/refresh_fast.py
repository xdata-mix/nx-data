#!/usr/bin/env python3
"""
refresh_fast.py — Fetch FAST channel playlists (Samsung TV+, Pluto TV, Plex TV, LG Channels)
and inject them into data.m3u.

Uses stdlib only (no pip install needed). Triggered via workflow_dispatch by cron-job.org.
"""

import json, os, re, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import Request, urlopen
from urllib.error import URLError

# -- Source URLs --
SAMSUNG_URL = "https://textup.fr/902510Ao?filetype=txt"
PLUTO_URL   = "https://textup.fr/902266T0?filetype=txt"
PLEX_URL    = "https://textup.fr/902513mk?filetype=txt"
LG_URL      = "https://www.apsattv.com/frlg.m3u"

# Group-title prefixes managed by this script
FAST_PREFIXES = ["Samsung TV+", "Pluto TV", "Plex TV", "LG Channels"]
DATA_M3U = "data.m3u"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"


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


def parse_samsung(body):
    entries = []
    try:
        data = json.loads(body)
        for grp in data.get("groups", []):
            gn = grp.get("name", "Divers")
            for s in grp.get("stations", []):
                n, u, l = s.get("name","").strip(), s.get("url","").strip(), s.get("image","")
                if n and u and "c9v3.s.gy" not in u:
                    entries.append(dict(name=n, logo=l, url=u, group=f"Samsung TV+ - {gn}"))
        for s in data.get("stations", []):
            n, u, l = s.get("name","").strip(), s.get("url","").strip(), s.get("image","")
            fold = s.get("infold", "Samsung TV+")
            if n and u and "c9v3.s.gy" not in u:
                entries.append(dict(name=n, logo=l, url=u, group=fold or "Samsung TV+"))
    except Exception as e:
        print(f"  WARN: parse_samsung: {e}", file=sys.stderr)
    return entries


def parse_m3u(body, svc):
    entries, lines, i = [], body.splitlines(), 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("#EXTINF:") and i+1 < len(lines):
            ul = lines[i+1].strip()
            if ul and ul.startswith("http") and not ul.startswith("#"):
                logo, gt = _attr(ln, "tvg-logo"), _attr(ln, "group-title")
                nm = ln[ln.rfind(",")+1:].strip() if "," in ln else ""
                if nm:
                    g = f"{svc} - {gt}" if gt else svc
                    entries.append(dict(name=nm, logo=logo, url=ul, group=g))
                i += 2; continue
        i += 1
    return entries


_LG = [
    (["pluto tv"], "Pluto TV"), (["rakuten"], "Rakuten TV"), (["sony one"], "Sony One"),
    (["sport","football","nba","top gear","motorvision","wwe","eurosport"], "Sport"),
    (["action","western","thriller","horror","sci-fi","polar","asylum"], "Action & Thriller"),
    (["comédie","comedie","comedy","rire"], "Comédie"),
    (["drame","drama","romance","romantique","novela"], "Drame & Romance"),
    (["film","cinéma","cinema","movie","lionsgate","nanar","grjngo","moviesphere",
      "allociné","allocine"], "Cinéma"),
    (["série","series","binge"], "Séries"),
    (["kid","enfant","toon","cartoon","pokemon","sonic","miraculous","yu-gi-oh","teen"], "Jeunesse"),
    (["crime","ncis","blacklist","jag","walker"], "Crime"),
    (["musique","music","mtv","trace","vevo","live nation"], "Musique"),
    (["nature","animaux","animal","planet","pets"], "Nature & Animaux"),
    (["docu","histoire","history","science","discovery","insight","real stories"], "Documentaire"),
    (["cuisine","food","maison","jardin","déco","lifestyle","fashion","beauty"], "Lifestyle"),
    (["info","news","euronews","france 24","afrique"], "Info"),
    (["rmc","bfm"], "BFM / RMC"),
    (["fréquence","soap","village","louis la brocante"], "Séries FR"),
]

def _clf(name):
    lo = name.lower()
    for keys, cat in _LG:
        if any(k in lo for k in keys): return cat
    return "Autres"

def parse_lg(body):
    entries, lines, i = [], body.splitlines(), 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("#EXTINF:") and i+1 < len(lines):
            ul = lines[i+1].strip()
            if ul and ul.startswith("http") and not ul.startswith("#"):
                logo = _attr(ln, "tvg-logo")
                raw = ln[ln.rfind(",")+1:].strip() if "," in ln else ""
                if raw:
                    nm = re.sub(r"^\d+\s+", "", raw)
                    entries.append(dict(name=nm or raw, logo=logo, url=ul,
                                        group=f"LG Channels - {_clf(nm)}"))
                i += 2; continue
        i += 1
    return entries


def strip_fast(lines):
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.strip().startswith("#EXTINF:"):
            gt = _attr(ln, "group-title")
            if any(gt.startswith(p) for p in FAST_PREFIXES):
                i += 1
                while i < len(lines):
                    if lines[i].strip().startswith("#EXT"): i += 1; continue
                    i += 1; break
                continue
        out.append(ln); i += 1
    return out

def to_m3u(entries):
    out = []
    for e in entries:
        l = f' tvg-logo="{e["logo"]}"' if e.get("logo") else ""
        out.append(f'#EXTINF:-1{l} group-title="{e["group"]}",{e["name"]}')
        out.append(e["url"])
    return out


def main():
    print("refresh_fast.py: fetching FAST playlists...")

    bodies = {}
    tasks = {"samsung": SAMSUNG_URL, "pluto": PLUTO_URL, "plex": PLEX_URL, "lg": LG_URL}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch, url): svc for svc, url in tasks.items()}
        for f in as_completed(futs):
            bodies[futs[f]] = f.result()

    all_e = []
    for svc, parser, body in [
        ("Samsung TV+", parse_samsung, bodies.get("samsung")),
        ("Pluto TV",    lambda b: parse_m3u(b, "Pluto TV"), bodies.get("pluto")),
        ("Plex TV",     lambda b: parse_m3u(b, "Plex TV"),  bodies.get("plex")),
        ("LG Channels", parse_lg, bodies.get("lg")),
    ]:
        if body:
            e = parser(body)
            print(f"  {svc}: {len(e)} channels")
            all_e.extend(e)
        else:
            print(f"  {svc}: FAILED")

    print(f"  Total: {len(all_e)} FAST channels")
    if not all_e:
        print("  ERROR: 0 channels -- aborting"); sys.exit(1)

    if os.path.exists(DATA_M3U):
        with open(DATA_M3U, encoding="utf-8") as f: existing = f.read().splitlines()
    else:
        existing = ["#EXTM3U"]

    cleaned = strip_fast(existing)
    print(f"  Stripped {len(existing)-len(cleaned)} old FAST lines")
    cleaned.extend(to_m3u(all_e))

    with open(DATA_M3U, "w", encoding="utf-8") as f:
        f.write("\n".join(cleaned) + "\n")
    print(f"  Written data.m3u: {len(cleaned)} lines")


if __name__ == "__main__":
    main()
