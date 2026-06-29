#!/usr/bin/env python3
"""
refresh_stream4free.py - Scraper pour stream4free.tv
Decouvre AUTOMATIQUEMENT les chaines depuis les pages du site
(plus de listes hardcodees - suit les changements du site en temps reel).

Genere data-stream4free.m3u — TOUTES les chaines en un seul groupe
"Stream4Free - Television en direct" (live TV + emissions 24/7 = tout est du live).

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
    "edit-details", "privacy", "preferences", "editpage", "inbox",
    "friends", "search", "advanced-search", "invite-friends",
    "pending-my-approval", "sent", "group", "photos", "videos",
    "events", "tv-live-france", "tv-show-series", "communaute",
    "forum", "setup-vod-american-dad-jwplayer-and-nimble",
    "tv-replay",
    # Pages de navigation detectees en run (404/403/no content)
    "change-profile-picture", "change-profile-video", "customize-my-page",
    "favicons", "modules", "templates", "lost-password", "media",
    "pending-approval", "privacy-policy", "profile", "register-account",
    "request-sent", "community", "groups", "gmqklfym6",
}

# -- Seeds : slugs charges par JavaScript, invisibles dans le HTML brut --
# cloudscraper ne les voit pas car le site les injecte cote client.
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
    "family-guy-france", "family-guy-hd", "finance-et-mondialisme",
    "friends-live", "futurama", "futurama-france",
    "game-of-thrones-hd", "greendale-college", "h-integrale",
    "himym", "house-md", "j-irai-dormir-chez-vous", "kaamelott-hd",
    "king-of-the-hill", "l-univers-et-ses-mysteres", "nature-hd",
    "scrubs", "seinfeld", "simpsons-vf", "soda", "sons-of-anarchy",
    "south-park-fr", "south-park-us", "special-investigation",
    "stargate-sg1-sga", "the-big-bang-theory", "the-cleveland-show",
    "the-office", "the-simpsons", "the-simpsons-france",
    "the-walking-dead", "tv-sciences", "un-gars-une-fille",
    "workaholics",
}

# -- HTTP helper --
# Essaie cloudscraper (bypass Cloudflare), fallback urllib
try:
    import cloudscraper
    _scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    def _fetch(url):
        resp = _scraper.get(url, timeout=20)
        resp.raise_for_status()
        return resp.text
    print("  [info] cloudscraper disponible", file=sys.stderr)
except ImportError:
    import urllib.request

    def _fetch(url):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            return resp.read().decode("utf-8", errors="replace")
    print("  [info] cloudscraper absent, fallback urllib", file=sys.stderr)

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
    r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_IMAGE_RE2 = re.compile(
    r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
    re.IGNORECASE,
)

def discover_slugs():
    """Scrape les pages listing pour decouvrir tous les slugs de contenu."""
    all_slugs = set()
    for page_url in DISCOVER_PAGES:
        html, err = fetch(page_url)
        if html is None:
            print("  WARN decouverte %s: %s" % (page_url, err), file=sys.stderr)
            continue
        for m in HREF_RE.finditer(html):
            slug = m.group(1).lower().rstrip("/")
            if slug not in SKIP_SLUGS and len(slug) > 2 and "/" not in slug:
                all_slugs.add(slug)
        print("  Decouverte %s: %d slugs cumules" % (page_url, len(all_slugs)),
              file=sys.stderr)
    return all_slugs

def extract_info(html, slug):
    """Extrait m3u8 URL, titre, logo d'un HTML de page channel."""
    m3u8_match = M3U8_RE.search(html)
    m3u8_url = m3u8_match.group(0) if m3u8_match else None

    title_match = TITLE_RE.search(html)
    title = title_match.group(1).strip() if title_match else slug.replace("-", " ").title()
    # Nettoyer les suffixes
    for sep in [
        " - Stream4Free", " | Stream4Free", " en streaming gratuit",
        " en direct gratuit", " en streaming", " en direct",
        " Live Streaming", " Stream", " Live",
    ]:
        if title.endswith(sep):
            title = title[: -len(sep)].strip()
    # Nettoyer les prefixes
    for prefix in [
        "Stream4free Live - ", "Stream4Free Live - ",
        "Regarder ", "regarder ",
    ]:
        if title.startswith(prefix):
            title = title[len(prefix):].strip()
    # Capitaliser la premiere lettre
    if title:
        title = title[0].upper() + title[1:]

    og = OG_IMAGE_RE.search(html) or OG_IMAGE_RE2.search(html)
    logo = ""
    if og:
        logo = og.group(1).strip()
        if logo and not logo.startswith("http"):
            logo = (BASE_URL + logo) if logo.startswith("/") else (BASE_URL + "/" + logo)

    return m3u8_url, title, logo

def main():
    print("Stream4Free scraper - auto-decouverte depuis %d pages" % len(DISCOVER_PAGES),
          file=sys.stderr)

    # -- Phase 1 : decouverte + merge seeds --
    slugs = discover_slugs()
    # Merge avec les seeds (slugs JS-loaded invisibles dans le HTML brut)
    slugs |= SEED_SLUGS
    # Retirer les skips (au cas ou un seed serait aussi dans SKIP)
    slugs -= SKIP_SLUGS

    if not slugs:
        print("ERREUR : 0 slugs apres merge (site bloque ?)", file=sys.stderr)
        out_path = "data-stream4free.m3u"
        if os.path.exists(out_path):
            print("  On GARDE l'ancien %s intact." % out_path, file=sys.stderr)
            sys.exit(0)
        sys.exit(1)

    print("  Total slugs (decouverts + seeds) : %d" % len(slugs), file=sys.stderr)

    # -- Phase 2 : fetch m3u8 pour chaque slug --
    # TOUT est "Television en direct" (live TV + emissions 24/7 = tout est du live)
    GROUP_LIVE = u"Stream4Free - T\u00e9l\u00e9vision en direct"

    entries = []
    seen_urls = set()   # dedup par URL m3u8
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

        # Dedup par URL m3u8 (ex: simpsons-vf et the-simpsons-france = meme flux)
        if m3u8_url in seen_urls:
            print("  SKIP %s: doublon m3u8 %s" % (slug, m3u8_url.split("/")[-1]),
                  file=sys.stderr)
            continue
        seen_urls.add(m3u8_url)

        entries.append((GROUP_LIVE, title, m3u8_url, logo))
        print("  OK %s -> %s" % (slug, title), file=sys.stderr)

        # Politesse : petit delai entre les requetes
        time.sleep(0.3)

    # -- Phase 3 : generer le M3U --
    entries.sort(key=lambda e: e[1].lower())

    lines = ["#EXTM3U"]
    for group, title, url, logo in entries:
        logo_attr = ' tvg-logo="%s"' % logo if logo else ""
        lines.append('#EXTINF:-1 group-title="%s"%s,%s' % (group, logo_attr, title))
        lines.append(url)

    m3u_content = "\n".join(lines) + "\n"
    total = len(entries)

    out_path = "data-stream4free.m3u"

    if total == 0:
        if os.path.exists(out_path):
            print("\nATTENTION : 0 chaines recuperees, on GARDE l'ancien %s." % out_path,
                  file=sys.stderr)
            sys.exit(0)
        else:
            print("ERREUR : aucune chaine recuperee et pas de fichier existant !",
                  file=sys.stderr)
            sys.exit(1)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print("\nResultat : %d chaines (tout en Television en direct)" % total,
          file=sys.stderr)
    if failed:
        print("  %d echecs : %s" % (len(failed), ", ".join(failed)), file=sys.stderr)

if __name__ == "__main__":
    main()
