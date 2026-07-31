#!/usr/bin/env python3
"""refresh_tf1_sitemap.py — NOUVEAU scraper TF1+ base sur les SITEMAPS video.

Objectif : catalogue TF1+ EXHAUSTIF (tous les programmes, toutes les chaines
TF1) via les sitemaps officiels de TF1 (maj quotidienne par TF1), au lieu du
scraping JSON-LD partiel des pages categorie (methode actuelle = ~1874 progs).

Chaque video du sitemap porte un <video:player_loc> = UUID resolvable via
mediainfo.tf1.fr/mediainfocombo/<uuid> (verifie : 200, DASH+DRM). On regroupe
par PROGRAMME (chaine/slug) et on sort les programmes au format
`tf1plus://<chan>/<slug>` (l'app va chercher les episodes elle-meme, exactement
comme avec le scraper actuel -> zero changement cote app).

SORTIE = data-replay-tf1-sitemap.m3u (fichier SEPARE = comparaison sure, ne
touche PAS la prod data-replay-tf1.m3u ni data-replay.m3u).

Methode fine, non-bourrin : seulement ~4 requetes XML (les sitemaps France),
aucune requete par video.
"""
import re, sys, os, time, datetime, gzip, urllib.request

SITEMAP_INDEX = "https://www.tf1.fr/sitemaps/sitemap-v.xml"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
# Chaines gardees = perimetre du scraper actuel (partenaires figaro/ina/lequipe/lcp exclus).
KEEP_CHANNELS = {"tf1", "tmc", "tfx", "tf1-series-films", "lci"}
OUT = os.path.join(os.path.dirname(__file__), "..", "data-replay-tf1-sitemap.m3u")

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
    s = re.sub(r"-\d{3,}$", "", slug)  # retire un id numerique final eventuel
    return " ".join(w.capitalize() for w in s.split("-")) if s else slug

URL_BLOCK = re.compile(r"<url>(.*?)</url>", re.S)
LOC_RX    = re.compile(r"<loc>([^<]+)</loc>")
PLAYER_RX = re.compile(r"<video:player_loc[^>]*>([^<]+)</video:player_loc>")
THUMB_RX  = re.compile(r"<video:thumbnail_loc>([^<]+)</video:thumbnail_loc>")
EXPIRE_RX = re.compile(r"<video:expiration_date>([^<]+)</video:expiration_date>")
PATH_RX   = re.compile(r"tf1\.fr/([a-z0-9-]+)/([a-z0-9-]+)/videos?/")

def discover_france_sitemaps():
    idx = http_get(SITEMAP_INDEX)
    locs = LOC_RX.findall(idx)
    fr = [u for u in locs if not re.search(r"-fr-[a-z]{2}\d+\.xml$", u)]  # France = pas de suffixe pays
    if not fr:
        fr = [f"https://www.tf1.fr/sitemaps/sitemap-v{n}.xml" for n in (1, 2, 3, 4)]
    return fr

def parse_sitemap(url):
    xml = http_get(url)
    out = []
    now = datetime.datetime.now(datetime.timezone.utc)
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
        uuid = PLAYER_RX.search(block)
        thumb = THUMB_RX.search(block)
        exp = EXPIRE_RX.search(block)
        expired = False
        if exp:
            try:
                if datetime.datetime.fromisoformat(exp.group(1)) < now:
                    expired = True
            except Exception:
                pass
        out.append((chan, prog,
                    uuid.group(1) if uuid else None,
                    thumb.group(1) if thumb else None,
                    expired))
    return out

def main():
    t0 = time.time()
    sitemaps = discover_france_sitemaps()
    print(f"Sitemaps France: {len(sitemaps)}")
    rows = []
    for sm in sitemaps:
        r = parse_sitemap(sm)
        print(f"  {sm.split('/')[-1]}: {len(r)} videos (chaines TF1 gardees)")
        rows.extend(r)
    total_videos = len(rows)
    programs = {}
    for chan, prog, uuid, thumb, expired in rows:
        p = programs.setdefault((chan, prog), {"poster": None, "videos": 0, "expired": 0})
        p["videos"] += 1
        if expired:
            p["expired"] += 1
        if not p["poster"] and thumb:
            p["poster"] = thumb
    lines = ["#EXTM3U"]
    per_chan = {}
    for (chan, prog), p in sorted(programs.items()):
        per_chan[chan] = per_chan.get(chan, 0) + 1
        title = slug_to_title(prog)
        group = f"Replay TF1+ Sitemap - {chan.upper()}"
        lines.append(f'#EXTINF:-1 tvg-id="tf1plus-{chan}-{prog}-sitemap" '
                     f'tvg-logo="{p["poster"] or ""}" tvg-country="FR" tvg-type="series" '
                     f'group-title="{group}",{title}')
        lines.append(f"tf1plus://{chan}/{prog}")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("=" * 52)
    print(f"TOTAL videos parcourues : {total_videos}")
    print(f"TOTAL programmes uniques: {len(programs)}")
    print(f"Par chaine              : {per_chan}")
    print(f"Fichier                 : {os.path.basename(OUT)} ({len(lines)} lignes)")
    print(f"Duree                   : {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()
