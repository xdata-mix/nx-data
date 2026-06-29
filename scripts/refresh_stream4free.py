#!/usr/bin/env python3
"""
refresh_stream4free.py  -  Scraper pour stream4free.tv
Decouvre AUTOMATIQUEMENT les chaines depuis les pages du site
(plus de listes hardcodees - suit les changements du site en temps reel).

Genere data-stream4free.m3u avec 2 groupes :
  - "Stream4Free - Television en direct"   (chaines francaises live)
  - "Stream4Free - Emission de television"  (24/7 loops series/emissions/docs)

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
    "6ter-france", "70-show", "family-guy-france", "friends-live",
    "futurama", "futurama-france", "game-of-thrones-hd",
    "greendale-college", "himym", "house-md", "j-irai-dormir-chez-vous",
    "king-of-the-hill", "sex-live-stream", "the-big-bang-theory",
    "the-cleveland-show", "the-office", "the-simpsons-france",
    "the-walking-dead", "tv5-hd", "un-gars-une-fille",
}

# -- Heuristique de classification --
# Mots-cles qui identifient une VRAIE chaine TV francaise (pas une emission)
CHANNEL_MARKERS = [
    "tf1", "france-2", "france-3", "france-4", "france-5",
    "france-24", "france-info", "m6", "arte", "bfm", "cnews",
    "cstar", "c8", "w9", "tmc", "tfx", "lci", "euronews",
    "eurosport", "rmc", "rtl9", "tv5", "nrj", "gulli", "6ter",
    "l-equipe", "lequipe", "public-senat", "histoire", "nat-geo",
    "national-geo", "t18", "cherie25", "planete", "paris-premiere",
    "novo19", "rmc-story", "rmc-decouverte", "rmc-life",
]

# Slugs qui contiennent un marker MAIS ne sont PAS des chaines TV
NOT_CHANNELS = {
    "family-guy-france", "futurama-france", "the-simpsons-france",
}


def is_likely_channel(slug):
    """Heuristique : le slug ressemble-t-il a une chaine TV francaise ?"""
    s = slug.lower()
    if s in NOT_CHANNELS:
        return False
    return any(m in s for m in CHANNEL_MARKERS)


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

    # -- Phase 2 : classification + fetch m3u8 --
    GROUP_LIVE = u"Stream4Free - TÃ©lÃ©vision en direct"
    GROUP_SHOW = u"Stream4Free - Ãmission de tÃ©lÃ©vision"

    entries = []
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

        group = GROUP_LIVE if is_likely_channel(slug) else GROUP_SHOW

        entries.append((group, title, m3u8_url, logo))
        short_grp = "TV" if group == GROUP_LIVE else "Emission"
        print("  OK %s -> %s [%s]" % (slug, title, short_grp), file=sys.stderr)

