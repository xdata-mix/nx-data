#!/usr/bin/env python3
"""
refresh_stream4free.py â Scraper AUTO-DISCOVERY pour stream4free.tv
Aucune liste hardcodee. Le script scrape le site, decouvre TOUS les slugs,
fetch chaque page, extrait le m3u8, et genere data-stream4free.m3u.

2 groupes :
  - "Stream4Free - Emissions TV"  (trouves sur /tv-show-series)
  - "Stream4Free - TV en direct"  (trouves sur /tv-live-france)
  - Si un slug est sur les deux ou aucun -> "Stream4Free - Emissions TV"

Utilise cloudscraper pour contourner la protection Cloudflare.
Heberge dans xdata-mix/nx-data, execute par GitHub Actions (refresh_stream4free.yml).
"""

import os
import re
import sys

try:
    import cloudscraper
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'linux', 'desktop': True}
    )
except ImportError:
    scraper = None

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
BASE_URL = "https://www.stream4free.tv"

# Pages de navigation a NE PAS traiter comme des chaines
NAV_PAGES = {
    "login", "register", "register-account", "lost-password", "contact",
    "privacy-policy", "terms-of-service", "about", "dmca", "forum",
    "tv-live-france", "tv-replay", "tv-show-series",
    "sex-live-stream",
    "setup-vod-american-dad-jwplayer-and-nimble",
}

# Regex pour extraire le m3u8 du HTML
# Primaire : URLs data-stream.top (majorite des chaines)
M3U8_RE = re.compile(
    r'https://sv\d+\.data-stream\.top(?::\d+)?/[a-f0-9]+/hls/[\w._-]+\.m3u8'
    r'(?:\?[^"\'<\s]+)?'
)
# Fallback : n'importe quelle URL .m3u8 (euronews via rakuten, etc.)
M3U8_FALLBACK_RE = re.compile(r'https?://[^\s"\'<>]+\.m3u8(?:\?[^\s"\'<>]*)?')

TITLE_RE = re.compile(r'<title[^>]*>([^<]+)</title>', re.IGNORECASE)
OG_IMAGE_RE = re.compile(
    r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
    re.IGNORECASE
)
OG_IMAGE_RE2 = re.compile(
    r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
    re.IGNORECASE
)
LINK_RE = re.compile(r'href=["\']\/([a-z0-9][a-z0-9_-]*)\/?["\']', re.IGNORECASE)


def fetch(url):
    """Fetch une URL et retourne (html, None) ou (None, erreur)."""
    try:
        if scraper:
            resp = scraper.get(url, timeout=25)
            if resp.status_code == 200:
                return resp.text, None
            return None, f"HTTP {resp.status_code}"
        else:
            import urllib.request, ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                return r.read().decode("utf-8", errors="replace"), None
    except Exception as e:
        return None, str(e)


def discover_slugs():
    """Scrape les pages index du site pour decouvrir TOUS les slugs de chaines.
    Retourne (all_slugs: set, live_slugs: set, show_slugs: set)."""
    all_slugs = set()
    live_slugs = set()
    show_slugs = set()

    pages = [
        ("index",      f"{BASE_URL}/",               None),
        ("live",       f"{BASE_URL}/tv-live-france",  live_slugs),
        ("shows",      f"{BASE_URL}/tv-show-series",  show_slugs),
        ("replay",     f"{BASE_URL}/tv-replay",       None),
    ]

    for label, url, target_set in pages:
        html, err = fetch(url)
        if html is None:
            print(f"  WARN discover {label}: {err}", file=sys.stderr)
            continue

        found = set(LINK_RE.findall(html))
        found -= NAV_PAGES
        found = {s for s in found if len(s) >= 2 and not s.startswith("_")}
        all_slugs |= found
        if target_set is not None:
            target_set |= found
        print(f"  discover {label}: {len(found)} slugs", file=sys.stderr)

    return all_slugs, live_slugs, show_slugs


def extract_info(html, slug):
    """Extrait m3u8 URL, titre, logo d'un HTML de page channel."""
    m3u8_match = M3U8_RE.search(html)
    if not m3u8_match:
        m3u8_match = M3U8_FALLBACK_RE.search(html)
    m3u8_url = m3u8_match.group(0) if m3u8_match else None

    title_match = TITLE_RE.search(html)
    title = title_match.group(1).strip() if title_match else slug.replace("-", " ").title()
    for sep in [" - Stream4Free", " | Stream4Free", " en streaming gratuit",
                " en direct gratuit", " en streaming", " en direct", " Live Streaming",
                " Stream", " HD", " Live"]:
        if title.endswith(sep):
            title = title[:-len(sep)].strip()
    if title.lower().startswith("regarder "):
        title = title[9:].strip()
    if title:
        title = title[0].upper() + title[1:]

    logo_match = OG_IMAGE_RE.search(html) or OG_IMAGE_RE2.search(html)
    logo = logo_match.group(1).strip() if logo_match else ""
    if logo and not logo.startswith("http"):
        logo = f"{BASE_URL}{logo}" if logo.startswith("/") else f"{BASE_URL}/{logo}"

    return m3u8_url, title, logo


def main():
    mode = "cloudscraper" if scraper else "urllib (pas de bypass CF)"
    print(f"Stream4Free AUTO-DISCOVERY scraper [{mode}]", file=sys.stderr)

    # Phase 1 : decouvrir TOUS les slugs
    all_slugs, live_slugs, show_slugs = discover_slugs()
    print(f"\nTotal slugs decouverts : {len(all_slugs)}", file=sys.stderr)
    if not all_slugs:
        print("ERREUR : aucun slug decouvert (site bloque ?)", file=sys.stderr)
        if os.path.exists("data-stream4free.m3u"):
            print("On GARDE l'ancien fichier intact.", file=sys.stderr)
            sys.exit(0)
        sys.exit(1)

    # Phase 2 : fetch chaque slug, extraire le m3u8
    entries = []  # (group, title, m3u8_url, logo)
    failed = []

    for slug in sorted(all_slugs):
        html, err = fetch(f"{BASE_URL}/{slug}")
        if html is None:
            print(f"  WARN {slug}: {err}", file=sys.stderr)
            failed.append(slug)
            continue

        m3u8_url, title, logo = extract_info(html, slug)
        if not m3u8_url:
            print(f"  WARN {slug}: no m3u8", file=sys.stderr)
            failed.append(slug)
            continue

        # Categoriser : si trouve UNIQUEMENT sur /tv-live-france -> live
        # Sinon -> emission (defaut)
        if slug in live_slugs and slug not in show_slugs:
            group = "Stream4Free - TV en direct"
        else:
            group = "Stream4Free - Emissions TV"

        entries.append((group, title, m3u8_url, logo))
        print(f"  OK {slug} -> {title} [{group.split(' - ')[1]}]", file=sys.stderr)

    # Phase 3 : generer le M3U
    entries.sort(key=lambda e: (0 if "Emissions" in e[0] else 1, e[1].lower()))

    lines = ["#EXTM3U"]
    for group, title, url, logo in entries:
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        lines.append(f'#EXTINF:-1 group-title="{group}"{logo_attr},{title}')
        lines.append(url)

    m3u_content = "\n".join(lines) + "\n"
    out_path = "data-stream4free.m3u"
    total = len(entries)

    # Garde-fou keep-old-on-failure
    if total == 0:
        if os.path.exists(out_path):
            print(f"\n0 chaines avec m3u8 -> on GARDE l'ancien {out_path}.",
                  file=sys.stderr)
            sys.exit(0)
        print("ERREUR : 0 chaines et pas de fichier existant !", file=sys.stderr)
        sys.exit(1)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print(f"\nResultat : {total} chaines ecrites dans {out_path}", file=sys.stderr)
    if failed:
        print(f"  {len(failed)} echecs : {', '.join(failed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
