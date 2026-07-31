#!/usr/bin/env python3
"""refresh_tf1_sitemap.py — COMPLETE data-replay-tf1.m3u via les SITEMAPS TF1+.

Tourne APRES refresh_tf1.py (qui produit data-replay-tf1.m3u avec de belles
jaquettes JSON-LD pour ~1874 programmes). Ce script lit ce fichier, puis via
les sitemaps video officiels de TF1 (maj quotidienne) AJOUTE uniquement les
programmes que l'ancien a rates -> complement SANS DOUBLON (dedup par l'URL
`tf1plus://<chan>/<slug>`).

Methode fine : ~4 requetes XML (sitemaps France), aucune requete par video.
Chaque video du sitemap porte <video:player_loc> = UUID resolvable via
mediainfo.tf1.fr/mediainfocombo/<uuid> (verifie : 200, DASH+DRM).

SECURITE : si les sitemaps ne renvoient rien / trop peu, on n'ajoute rien
(le fichier de l'ancien scraper reste intact).
"""
import re, sys, os, time, datetime, gzip, urllib.request

SITEMAP_INDEX = "https://www.tf1.fr/sitemaps/sitemap-v.xml"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
KEEP_CHANNELS = {"tf1", "tmc", "tfx", "tf1-series-films", "lci"}
OUT = os.path.join(os.path.dirname(__file__), "..", "data-replay-tf1.m3u")
MIN_PROGRAMS = 500  # garde-fou : sous ce seuil on considere le sitemap KO -> on n'ajoute rien

def http_get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://www.tf1.fr/",
                "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data.decode("utf-8", "replace")
        except Exception as e:
            if i == tries - 1:
                print(f"  [warn] GET {url[:70]} failed: {e}", file=sys.stderr)
                return ""
            time.sleep(1.5)
    return ""

def slug_to_title(slug):
    s = re.sub(r"-\d{3,}$", "", slug)
    return " ".join(w.capitalize() for w in s.split("-")) if s else slug

URL_BLOCK = re.compile(r"<url>(.*?)</url>", re.S)
LOC_RX    = re.compile(r"<loc>([^<]+)</loc>")
THUMB_RX  = re.compile(r"<video:thumbnail_loc>([^<]+)</video:thumbnail_loc>")
PATH_RX   = re.compile(r"tf1\.fr/([a-z0-9-]+)/([a-z0-9-]+)/videos?/")

def discover_france_sitemaps():
    idx = http_get(SITEMAP_INDEX)
    locs = LOC_RX.findall(idx)
    fr = [u for u in locs if not re.search(r"-fr-[a-z]{2}\d+\.xml$", u)]
    if not fr:
        fr = [f"https://www.tf1.fr/sitemaps/sitemap-v{n}.xml" for n in (1, 2, 3, 4)]
    return fr

def collect_sitemap_programs():
    programs = {}  # (chan,prog) -> poster
    for sm in discover_france_sitemaps():
        xml = http_get(sm)
        n = 0
        for block in URL_BLOCK.findall(xml):
            loc = LOC_RX.search(block)
            if not loc:
                continue
            m = PATH_RX.search(loc.group(1))
            if not m:
                continue
            chan, prog = m.group(1), m.group(2)
            if chan not in KEEP_CHANNELS:
                continue
            if (chan, prog) not in programs:
                thumb = THUMB_RX.search(block)
                programs[(chan, prog)] = thumb.group(1) if thumb else ""
            n += 1
        print(f"  {sm.split('/')[-1]}: {n} videos (chaines TF1)")
    return programs

def read_existing_urls(path):
    urls = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("tf1plus://"):
                    urls.add(line)
    return urls

def main():
    t0 = time.time()
    programs = collect_sitemap_programs()
    print("=" * 52)
    print(f"Programmes sitemap uniques : {len(programs)}")
    if len(programs) < MIN_PROGRAMS:
        print(f"[STOP] sitemap KO ({len(programs)} < {MIN_PROGRAMS}) -> aucun ajout, fichier intact.")
        return
    existing = read_existing_urls(OUT)
    print(f"Programmes deja presents   : {len(existing)}")
    added = []
    for (chan, prog), poster in sorted(programs.items()):
        url = f"tf1plus://{chan}/{prog}"
        if url in existing:
            continue
        title = slug_to_title(prog)
        group = f"Replay TF1+ Complement - {chan.upper()}"
        added.append(f'#EXTINF:-1 tvg-id="tf1plus-{chan}-{prog}-smx" '
                     f'tvg-logo="{poster}" tvg-country="FR" tvg-type="series" '
                     f'group-title="{group}",{title}')
        added.append(url)
    if not added:
        print("Rien a ajouter (tout deja present).")
    else:
        header = "" if os.path.exists(OUT) else "#EXTM3U\n"
        with open(OUT, "a", encoding="utf-8") as f:
            if header:
                f.write(header)
            f.write("\n" + "\n".join(added) + "\n")  # \n en tete : ne colle pas a la derniere ligne de l ancien
    print(f"Programmes AJOUTES (complement) : {len(added)//2}")
    print(f"Duree : {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()
