#!/usr/bin/env python3
"""
refresh_stream4free.py - Scraper pour stream4free.tv
Decouvre AUTOMATIQUEMENT les chaines depuis les pages du site
(plus de listes hardcodees - suit les changements du site en temps reel).

Genere data-stream4free.m3u — TOUTES les chaines en un seul groupe
"Stream4Free - Television en direct" (live TV + emissions 24/7 = tout est du live).

IMPORTANT : le m3u stocke des URLs stream4free://<slug> (stables).
L'app ONYX resout le vrai flux m3u8 a la lecture via Stream4FreeResolver.

Heberge dans xdata-mix/nx-data, execute par GitHub Actions (refresh_stream4free.yml).
"""

import os
import re
import sys
import ssl
import time

# -- Bypass SSL (certains CDN ont des certs flaky) --
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

BASE_URL = "https://www.stream4free.tv"

# -- Pages de decouverte --
DISCOVER_PAGES = [
    BASE_URL + "/tv-live-france",
    BASE_URL + "/tv-show-series",
]

# -- Slugs a ignorer (pages de navigation, pas du contenu) --
SKIP_SLUGS = {
    "go-to-profile", "change-avatar", "linkvideo", "edit-profile",
    "tv-live-france", "tv-show-series", "tv-live-usa", "tv-live-uk",
    "live-sport", "tv-shows", "privacy-policy", "terms-of-service",
    "contact", "about", "register", "login", "dmca", "faq",
    "tv-live", "pack-emissions",
}

# -- Slugs connus (seeds) --
# Seeds = slugs charges par JavaScript, invisibles dans le HTML brut
# (le cloudscraper ne les voit pas car le site les injecte cote client).
# Mis a jour depuis le DOM navigateur le 2026-06-28.
SEED_SLUGS = {
    # Chaines TV live
    "6ter-france", "arte", "bfm-tv", "cnews", "cstar",
    "euronews-france", "eurosport", "france-3-live", "france-4",
    "france-5-live", "france-24", "france-info-tv",
    "histoire", "l-equipe-21", "lci-chaine-info-direct",
    "m6-live-streaming", "national-geographic", "novo19",
    "public-senat", "rmc-decouverte", "rmc-life", "rmc-story",
    "rtl9", "sex-live-stream", "t18-live", "tf1-live-streaming",
    "tf1-series-films", "tfx", "tmc", "tv5-hd", "w9-france",
    # Emissions / Series en boucle (24/7 live)
    "70-show", "american-dad-hd", "aqua-teen-hunger-force", "archer",
    "bobs-burgers", "breaking-bad", "camera-cafe-stream", "ddc",
    "divers-docs", "dragonball-dbz", "enquete-exclusive",
    "family-guy-hd", "friends-live", "futurama",
    "game-of-thrones-hd", "greendale-college", "h-integrale",
    "himym", "house-md", "kaamelott-hd", "king-of-the-hill",
    "l-univers-et-ses-mysteres", "poker-stream",
    "rick-and-morty", "scrubs", "seinfeld", "simpsons-vf",
    "sons-of-anarchy", "south-park-fr", "south-park-us",
    "special-investigation", "stargate-sg1-sga",
    "the-big-bang-theory", "the-cleveland-show", "the-office",
    "the-simpsons", "the-walking-dead", "triptank",
    "tv-sciences", "workaholics",
    "always-sunny-in-philadelphia",
}

GROUP_LIVE = "Stream4Free - Television en direct"

# ---- helpers reseau ----
try:
    import urllib.request
    def _fetch(url):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,*/*",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.5",
        })
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            return r.read().decode("utf-8", errors="replace")
except ImportError:
    print("ERREUR: urllib manquant", file=sys.stderr)
    sys.exit(1)

def fetch(url):
    """Fetch URL, retourne (html, None) ou (None, erreur)."""
    try:
        return _fetch(url), None
    except Exception as e:
        return None, str(e)

# -- Regex --
HREF_RE = re.compile(
    r'href=["\'](?:https?://(?:www\.)?stream4free\.tv)?/([a-z0-9][a-z0-9_-]+)["\'/]',
    re.IGNORECASE,
)
M3U8_RE = re.compile(r'https?://[a-z0-9]+\.data-stream\.top/[a-f0-9]+/hls/[\w._-]+\.m3u8')
TITLE_RE = re.compile(r'<title[^>]*>([^<]+)</title>', re.IGNORECASE)
OG_IMAGE_RE = re.compile(
    r'<meta\s+(?:property=["\']og:image["\']\s+content=["\']([^"\']+)["\']'
    r'|content=["\']([^"\']+)["\']\s+property=["\']og:image["\'])',
    re.IGNORECASE,
)


def extract_info(html, slug):
    """Extrait m3u8 URL, titre, logo d'un HTML de page channel."""
    m3u8 = M3U8_RE.search(html)
    m3u8_url = m3u8.group(0) if m3u8 else None

    tm = TITLE_RE.search(html)
    title = tm.group(1).strip() if tm else slug.replace("-", " ").title()
    for sep in [
        " - Stream4Free", " | Stream4Free", " en streaming gratuit",
        " en direct gratuit", " en streaming", " en direct",
        " Live Streaming", " Stream", " Live",
    ]:
        if title.endswith(sep):
            title = title[: -len(sep)].strip()
    for prefix in [
        "Stream4free Live - ", "Stream4Free Live - ",
        "Regarder ", "regarder ",
    ]:
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()
    if title:
        title = title[0].upper() + title[1:]

    og = OG_IMAGE_RE.search(html)
    logo = (og.group(1) or og.group(2) or "").strip() if og else ""
    if logo and not logo.startswith("http"):
        logo = (BASE_URL + logo) if logo.startswith("/") else (BASE_URL + "/" + logo)

    return m3u8_url, title, logo


def discover_slugs():
    """Decouvre des slugs depuis les pages d'index du site."""
    found = set()
    for page_url in DISCOVER_PAGES:
        html, err = fetch(page_url)
        if html is None:
            print("  WARN discover %s: %s" % (page_url, err), file=sys.stderr)
            continue
        for m in HREF_RE.finditer(html):
            s = m.group(1).lower().strip("/")
            if s and s not in SKIP_SLUGS and len(s) > 2:
                found.add(s)
        print("  Discover %s: %d liens" % (page_url.split("/")[-1], len(found)),
              file=sys.stderr)
    return found


def main():
    print("Stream4Free scraper (auto-discovery + stream4free:// URLs)", file=sys.stderr)

    # Phase 1 : decouvrir les slugs
    discovered = discover_slugs()
    slugs = SEED_SLUGS | discovered
    print("  Total slugs: %d (seeds %d + discovered %d)" % (
        len(slugs), len(SEED_SLUGS), len(discovered)), file=sys.stderr)

    # Phase 2 : fetcher chaque page
    entries = []
    seen_urls = set()
    failed = []

    for slug in sorted(slugs):
        html, err = fetch(BASE_URL + "/" + slug)
        if html is None:
            print("  WARN %s: fetch failed: %s" % (slug, err), file=sys.stderr)
            failed.append(slug)
            continue

        m3u8_url, title, logo = extract_info(html, slug)
        if not m3u8_url:
            print("  WARN %s: no m3u8 found" % slug, file=sys.stderr)
            failed.append(slug)
            continue

        if m3u8_url in seen_urls:
            print("  SKIP %s: doublon m3u8 %s" % (slug, m3u8_url.split("/")[-1]),
                  file=sys.stderr)
            continue
        seen_urls.add(m3u8_url)

        # CLE : stream4free://<slug> au lieu du m3u8 direct
        entries.append((GROUP_LIVE, title, "stream4free://" + slug, logo))
        print("  OK %s -> %s" % (slug, title), file=sys.stderr)

        time.sleep(0.3)

    # Phase 3 : generer le M3U
    entries.sort(key=lambda e: e[1].lower())

    lines = ["#EXTM3U"]
    for group, title, url, logo in entries:
        logo_attr = ' tvg-logo="%s"' % logo if logo else ""
        lines.append('#EXTINF:-1 group-title="%s"%s,%s' % (group, logo_attr, title))
        lines.append(url)

    m3u_content = "\n".join(lines) + "\n"

    out_path = "data-stream4free.m3u"
    total = len(entries)

    if total == 0:
        if os.path.exists(out_path):
            print("\nATTENTION : 0 chaines recuperees, on GARDE l'ancien %s intact." % out_path,
                  file=sys.stderr)
            sys.exit(0)
        else:
            print("ERREUR : aucune chaine et pas de fichier existant !", file=sys.stderr)
            sys.exit(1)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print("\nResultat : %d chaines ecrites dans %s" % (total, out_path), file=sys.stderr)
    if failed:
        print("  %d echecs : %s" % (len(failed), ", ".join(failed)), file=sys.stderr)


if __name__ == "__main__":
    main()