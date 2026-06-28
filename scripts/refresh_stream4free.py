#!/usr/bin/env python3
"""
refresh_stream4free.py — Scraper pour stream4free.tv
Génère data-stream4free.m3u avec 2 groupes :
  - "Stream4Free - Émissions TV"  (24/7 loops de séries/émissions)
  - "Stream4Free - TV en direct"  (chaînes françaises live)

Utilise cloudscraper pour contourner la protection Cloudflare.
Hébergé dans xdata-mix/nx-data, exécuté par GitHub Actions (refresh_stream4free.yml).
"""

import os
import re
import sys
import json
from collections import OrderedDict

try:
    import cloudscraper
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'linux', 'desktop': True}
    )
except ImportError:
    # Fallback si cloudscraper pas installé (dev local)
    scraper = None

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# ── Inventaire complet des chaînes ──
# Slugs = path après stream4free.tv/
# Le m3u8 slug peut différer du slug URL (ex: tf1-live-streaming → tf3-HD)
# On fetch chaque page pour extraire le VRAI m3u8 slug.

EMISSIONS_TV = [
    "south-park-us",
    "family-guy-hd",
    "futurama",
    "american-dad-hd",
    "the-simpsons",
    "archer",
    "bobs-burgers",
    "king-of-the-hill",
    "the-cleveland-show",
    "aqua-teen-hunger-force",
    "workaholics",
    "house-md",
    "friends-live",
    "always-sunny-in-philadelphia",
    "scrubs",
    "seinfeld",
    "the-big-bang-theory",
    "the-office",
    "himym",
    "greendale-college",
    "rick-and-morty",
    "triptank",
    "game-of-thrones-hd",
    "sons-of-anarchy",
    "the-walking-dead",
    "breaking-bad",
    "poker-stream",
    # Extra from mega-menu (24/7 show loops)
    "stargate-sg1-sga",
    "kaamelott-hd",
    "simpsons-vf",
    "camera-cafe-stream",
    "h-integrale",
    "south-park-fr",
    "national-geographic",
    "l-univers-et-ses-mysteres",
    "special-investigation",
    "histoire",
    "tv-sciences",
]

TV_EN_DIRECT = [
    "tf1-live-streaming",
    "france-2-direct",
    "france-3-live",
    "france-5-live",
    "m6-live-streaming",
    "arte",
    "w9-france",
    "tmc",
    "tfx",
    "france-4",
    "bfm-tv",
    "cnews",
    "cstar",
    "tf1-series-films",
    "novo19",
    "l-equipe-21",
    "6ter-france",
    "rmc-story",
    "rmc-decouverte",
    "euronews",
    "eurosport",
    "france-info-tv",
    "lci-chaine-info-direct",
    "t18-live",
    "france-24",
    "rtl9",
    "tv5-hd",
    "public-senat",
]

# ── Regex pour extraire le m3u8 du HTML ──
M3U8_RE = re.compile(r'https://sv\d+\.data-stream\.top(?::\d+)?/[a-f0-9]+/hls/[\w._-]+\.m3u8(?:\?[^"\'<\s]+)?')
TITLE_RE = re.compile(r'<title[^>]*>([^<]+)</title>', re.IGNORECASE)
OG_IMAGE_RE = re.compile(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', re.IGNORECASE)
# Fallback : og:image peut aussi être content avant property
OG_IMAGE_RE2 = re.compile(r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']', re.IGNORECASE)

BASE_URL = "https://www.stream4free.tv"


def fetch_page(slug):
    """Fetch une page channel et retourne (html, None) ou (None, erreur)."""
    url = f"{BASE_URL}/{slug}"
    try:
        if scraper:
            # cloudscraper gère le challenge Cloudflare automatiquement
            resp = scraper.get(url, timeout=20)
            if resp.status_code == 200:
                return resp.text, None
            else:
                return None, f"HTTP {resp.status_code}"
        else:
            # Fallback urllib (sans bypass CF)
            import urllib.request
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                return resp.read().decode("utf-8", errors="replace"), None
    except Exception as e:
        return None, str(e)


def extract_info(html, slug):
    """Extrait m3u8 URL, titre, logo d'un HTML de page channel."""
    m3u8_match = M3U8_RE.search(html)
    m3u8_url = m3u8_match.group(0) if m3u8_match else None

    title_match = TITLE_RE.search(html)
    title = title_match.group(1).strip() if title_match else slug.replace("-", " ").title()
    # Nettoyer le titre (souvent "Regarder XXX en streaming gratuit - Stream4Free")
    for sep in [" - Stream4Free", " | Stream4Free", " en streaming gratuit",
                " en direct gratuit", " en streaming", " en direct", " Live Streaming",
                " Stream", " HD", " Live"]:
        if title.endswith(sep):
            title = title[:-len(sep)].strip()
    # Supprimer "Regarder " en début
    if title.lower().startswith("regarder "):
        title = title[9:].strip()
    # Capitaliser
    if title:
        title = title[0].upper() + title[1:]

    logo_match = OG_IMAGE_RE.search(html) or OG_IMAGE_RE2.search(html)
    logo = logo_match.group(1).strip() if logo_match else ""
    # Rendre le logo absolu si relatif
    if logo and not logo.startswith("http"):
        logo = f"{BASE_URL}{logo}" if logo.startswith("/") else f"{BASE_URL}/{logo}"

    return m3u8_url, title, logo


def main():
    entries = []  # list of (group, title, m3u8_url, logo)
    failed = []

    mode = "cloudscraper" if scraper else "urllib (pas de bypass CF)"
    print(f"Stream4Free scraper [{mode}] — {len(EMISSIONS_TV)} émissions + {len(TV_EN_DIRECT)} live", file=sys.stderr)

    for group_label, slugs in [
        ("Stream4Free - Émissions TV", EMISSIONS_TV),
        ("Stream4Free - TV en direct", TV_EN_DIRECT),
    ]:
        for slug in slugs:
            html, err = fetch_page(slug)
            if html is None:
                print(f"  WARN {slug}: fetch failed: {err}", file=sys.stderr)
                failed.append(slug)
                continue

            m3u8_url, title, logo = extract_info(html, slug)
            if not m3u8_url:
                print(f"  WARN {slug}: no m3u8 found in page", file=sys.stderr)
                failed.append(slug)
                continue

            entries.append((group_label, title, m3u8_url, logo))
            print(f"  OK {slug} → {title}", file=sys.stderr)

    # ── Générer le M3U ──
    lines = ["#EXTM3U"]
    for group, title, url, logo in entries:
        logo_attr = f' tvg-logo="{logo}"' if logo else ""
        lines.append(f'#EXTINF:-1 group-title="{group}"{logo_attr},{title}')
        lines.append(url)

    m3u_content = "\n".join(lines) + "\n"

    out_path = "data-stream4free.m3u"
    total = len(entries)

    # ── Garde-fou "keep-old-on-failure" ──
    # Si AUCUNE chaîne récupérée (ex: site bloque les IPs datacenter/GitHub),
    # on garde le fichier existant intact et on sort en succès (no-op).
    # Le fichier initial est commité manuellement ; le cron ne le casse pas.
    if total == 0:
        if os.path.exists(out_path):
            print(f"\nATTENTION : 0 chaînes récupérées (site bloqué ?), "
                  f"on GARDE l'ancien {out_path} intact.", file=sys.stderr)
            sys.exit(0)  # succès = no-op, pas d'écrasement
        else:
            print("ERREUR : aucune chaîne récupérée et pas de fichier existant !",
                  file=sys.stderr)
            sys.exit(1)

    # ── Écrire le fichier (au moins 1 chaîne OK) ──
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(m3u_content)

    print(f"\nRésultat : {total} chaînes écrites dans {out_path}", file=sys.stderr)
    if failed:
        print(f"  {len(failed)} échecs : {', '.join(failed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
